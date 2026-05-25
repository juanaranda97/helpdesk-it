"""Routers de catálogos: categorías, ubicaciones, usuarios, config, notificaciones."""
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_admin, require_supervisor
from app.core.supabase_client import supabase_admin
from app.models import Category, Location, SystemConfig, Profile, Notification
from app.schemas.schemas import (
    CategoryCreate, CategoryUpdate, CategoryResponse,
    LocationCreate, LocationResponse,
    ProfileResponse, ProfileRoleUpdate,
    ConfigUpdate, ConfigResponse,
    NotificationResponse,
)


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


@users_router.get("/", response_model=List[ProfileResponse])
async def list_users(
    db: Session = Depends(get_db),
    _: dict = Depends(require_supervisor)
):
    return db.query(Profile).order_by(Profile.full_name).all()


@users_router.get("/technicians", response_model=List[ProfileResponse])
async def list_technicians(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Lista técnicos disponibles para asignar."""
    return db.query(Profile).filter(
        Profile.role.in_(["tecnico", "supervisor", "admin"]),
        Profile.is_active == True
    ).order_by(Profile.full_name).all()


@users_router.patch("/{user_id}/role", response_model=ProfileResponse)
async def change_user_role(
    user_id: UUID,
    data: ProfileRoleUpdate,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin)
):
    user = db.query(Profile).filter(Profile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user.role = data.role
    db.commit()
    db.refresh(user)
    return user


@users_router.patch("/{user_id}/toggle-active", response_model=ProfileResponse)
async def toggle_user_active(
    user_id: UUID,
    db: Session = Depends(get_db),
    _: dict = Depends(require_admin)
):
    user = db.query(Profile).filter(Profile.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return user


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
