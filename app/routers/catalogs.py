"""Routers de catálogos: categorías, ubicaciones, usuarios, config, notificaciones."""
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_admin, require_supervisor
from app.core.supabase_client import supabase_admin
from app.core.tenancy import get_scoped_tenancy, TenancyContext
from app.core import permissions
from app.models import Category, Location, SystemConfig, Profile, Notification
from app.schemas.schemas import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
    LocationCreate, LocationResponse,
    ProfileResponse, ProfileRoleUpdate, UserAdminUpdate,
    ConfigUpdate, ConfigResponse,
    NotificationResponse,
)
from app.services import users as users_service


# ============================================================
# CATEGORIES
# ============================================================
categories_router = APIRouter(prefix="/categories", tags=["Categorías"])


@categories_router.get("/", response_model=List[CategoryResponse])
async def list_categories(db: Session = Depends(get_db)):
    return db.query(Category).order_by(Category.name).all()


@categories_router.post("/", response_model=CategoryResponse, status_code=201)
async def create_category(
    data: CategoryCreate,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin)
):
    category = Category(**data.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@categories_router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    data: CategoryUpdate,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin)
):
    category = db.query(Category).filter(Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Categoría no encontrada")
    
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(category, key, value)
    
    db.commit()
    db.refresh(category)
    return category


# ============================================================
# LOCATIONS
# ============================================================
locations_router = APIRouter(prefix="/locations", tags=["Sucursales"])


@locations_router.get("/", response_model=List[LocationResponse])
async def list_locations(db: Session = Depends(get_db)):
    return db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()


@locations_router.post("/", response_model=LocationResponse, status_code=201)
async def create_location(
    data: LocationCreate,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin)
):
    location = Location(**data.model_dump())
    db.add(location)
    db.commit()
    db.refresh(location)
    return location


# ============================================================
# USERS / PROFILES
# ============================================================
users_router = APIRouter(prefix="/users", tags=["Usuarios"])


def require_user_manager(ctx: TenancyContext = Depends(get_scoped_tenancy)) -> TenancyContext:
    """Contexto de tenancy + exige permiso de gestión de usuarios, y FAIL-CLOSED de
    empresa (un rol sub-organización sin empresa no puede gestionar usuarios)."""
    if not permissions.can_manage_users(ctx.role):
        raise HTTPException(status_code=403, detail="Requiere permisos de gestión de usuarios")
    ctx.assert_company_scope()
    return ctx


@users_router.get("/", response_model=List[ProfileResponse])
async def list_users(
    search: str | None = None,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_user_manager),
):
    """Lista usuarios del tenant del actor (org + empresa). Sin fugas cross-tenant."""
    return users_service.list_users(db, ctx, search=search)


@users_router.get("/technicians", response_model=List[ProfileResponse])
async def list_technicians(
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    """Técnicos+ asignables del tenant del actor (activos). Scopeado en backend."""
    return users_service.list_technicians(db, ctx)


@users_router.get("/assignable-roles")
async def assignable_roles(ctx: TenancyContext = Depends(require_user_manager)):
    """Roles que ESTE actor puede otorgar (autorizados por backend; sin defaults
    engañosos en el frontend)."""
    base = ["usuario", "tecnico", "rrhh", "supervisor", "manager", "company_admin",
            "org_admin", "org_owner", "platform_admin", "platform_owner"]
    out = []
    for r in base:
        if not permissions.can_assign_role(ctx.role, r):
            continue
        if r in permissions.PLATFORM_ROLES and not permissions.is_platform_owner(ctx.role):
            continue
        out.append(r)
    return {"roles": out}


@users_router.get("/org-options")
async def user_org_options(db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_user_manager)):
    return users_service.list_org_options(db, ctx)


@users_router.get("/company-options")
async def user_company_options(organization_id: UUID, db: Session = Depends(get_db),
                               ctx: TenancyContext = Depends(require_user_manager)):
    return users_service.list_company_options(db, ctx, organization_id)


@users_router.get("/department-options")
async def user_department_options(company_id: UUID, db: Session = Depends(get_db),
                                  ctx: TenancyContext = Depends(require_user_manager)):
    return users_service.list_department_options(db, ctx, company_id)


@users_router.patch("/{user_id}", response_model=ProfileResponse)
async def update_user(
    user_id: UUID,
    data: UserAdminUpdate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_user_manager),
):
    """Edición controlada y parcial del perfil (nombre, org, empresa, depto, rol,
    estado). Autorización por campo + auditoría en el servicio."""
    return users_service.update_user(db, ctx, user_id, data)


@users_router.patch("/{user_id}/role", response_model=ProfileResponse)
async def change_user_role(
    user_id: UUID,
    data: ProfileRoleUpdate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_user_manager),
):
    return users_service.change_role(db, ctx, user_id, data.role)


@users_router.patch("/{user_id}/toggle-active", response_model=ProfileResponse)
async def toggle_user_active(
    user_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_user_manager),
):
    return users_service.toggle_active(db, ctx, user_id)


# ============================================================
# CONFIG
# ============================================================
config_router = APIRouter(prefix="/config", tags=["Configuración"])


@config_router.get("/", response_model=List[ConfigResponse])
async def list_config(db: Session = Depends(get_db)):
    """Cualquier usuario logueado puede leer la config (necesaria para frontend)."""
    configs = db.query(SystemConfig).all()
    return [ConfigResponse(key=c.key, value=c.value, description=c.description) for c in configs]


@config_router.patch("/{key}", response_model=ConfigResponse)
async def update_config(
    key: str,
    data: ConfigUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if not config:
        # Crear si no existe
        config = SystemConfig(key=key, value=data.value, updated_by=UUID(current_user["id"]))
        db.add(config)
    else:
        config.value = data.value
        config.updated_by = UUID(current_user["id"])
    
    db.commit()
    db.refresh(config)
    return ConfigResponse(key=config.key, value=config.value, description=config.description)


@config_router.post("/bulk")
async def bulk_update_config(
    updates: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin)
):
    """Actualizar varios configs a la vez."""
    user_id = UUID(current_user["id"])
    for key, value in updates.items():
        config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if config:
            config.value = str(value)
            config.updated_by = user_id
        else:
            db.add(SystemConfig(key=key, value=str(value), updated_by=user_id))
    db.commit()
    return {"message": "Configuración actualizada", "updated": len(updates)}


# ============================================================
# NOTIFICATIONS
# ============================================================
notifications_router = APIRouter(prefix="/notifications", tags=["Notificaciones"])


@notifications_router.get("/", response_model=List[NotificationResponse])
async def my_notifications(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    return db.query(Notification).filter(
        Notification.user_id == UUID(current_user["id"])
    ).order_by(Notification.created_at.desc()).limit(50).all()


@notifications_router.patch("/{notif_id}/read")
async def mark_read(
    notif_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.user_id == UUID(current_user["id"])
    ).first()
    if notif:
        notif.is_read = True
        db.commit()
    return {"message": "ok"}


@notifications_router.patch("/read-all")
async def mark_all_read(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    db.query(Notification).filter(
        Notification.user_id == UUID(current_user["id"]),
        Notification.is_read == False
    ).update({"is_read": True})
    db.commit()
    return {"message": "ok"}
