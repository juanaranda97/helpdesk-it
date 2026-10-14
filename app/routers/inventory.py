"""Router del módulo de Inventario (ITAM).

Todas las operaciones:
- Resuelven el tenant en backend (get_scoped_tenancy). Nunca confían en
  organization_id/company_id del navegador (solo X-Org-Id de plataforma, validado).
- Exigen rol de gestión de activos (permissions.can_manage_assets): org_admin+,
  company_admin, tecnico y roles de plataforma.
"""
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenancy import get_scoped_tenancy, TenancyContext
from app.core import permissions
from app.schemas.inventory import (
    EquipmentCreate, EquipmentUpdate, EquipmentResponse, EquipmentListItem,
    AssignmentCreate, AssignmentReturn, AssignmentResponse,
    InventoryStats, InventoryMeta, AssignableUser, TenantOption,
)
from app.services import inventory as inv

router = APIRouter(prefix="/inventory", tags=["Inventario"])


def require_asset_ctx(ctx: TenancyContext = Depends(get_scoped_tenancy)) -> TenancyContext:
    """Contexto de tenancy + exige rol de gestión de activos.

    Además aplica FAIL-CLOSED de empresa de forma CENTRALIZADA: un rol sub-organización
    sin empresa asignada no puede operar sobre Inventario (403). Al estar acá, TODOS los
    endpoints actuales y futuros del módulo quedan protegidos (incluido GET /inventory/meta).
    Las verificaciones de los servicios se conservan como defensa en profundidad.
    org_admin/org_owner y plataforma conservan su alcance multiempresa/global.
    """
    if not permissions.can_manage_assets(ctx.role):
        raise HTTPException(status_code=403, detail="Requiere permisos de gestión de activos")
    ctx.assert_company_scope()
    return ctx


# ============================================================
# DASHBOARD / META
# ============================================================
@router.get("/stats", response_model=InventoryStats)
async def inventory_stats(
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    return InventoryStats(**inv.get_stats(db, ctx))


@router.get("/meta", response_model=InventoryMeta)
async def inventory_meta(
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    return InventoryMeta(**inv.get_meta(db, ctx))


@router.get("/assignable-users", response_model=List[AssignableUser])
async def assignable_users(
    company_id: Optional[UUID] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    """Usuarios elegibles para asignar un activo — de la empresa seleccionada
    (validada) o del scope; activos, no dados de baja/suspendidos; con búsqueda."""
    return inv.list_assignable_users(db, ctx, company_id=company_id, search=search)


# ----- Selectores dependientes de pertenencia (todo validado/scopeado) -----
@router.get("/organizations", response_model=List[TenantOption])
async def inv_organizations(db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_asset_ctx)):
    return inv.list_org_options(db, ctx)


@router.get("/companies", response_model=List[TenantOption])
async def inv_companies(organization_id: UUID, db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_asset_ctx)):
    return inv.list_company_options(db, ctx, organization_id)


@router.get("/departments", response_model=List[TenantOption])
async def inv_departments(company_id: UUID, db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_asset_ctx)):
    return inv.list_department_options(db, ctx, company_id)


@router.get("/branches", response_model=List[TenantOption])
async def inv_branches(organization_id: UUID, company_id: Optional[UUID] = None,
                       db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_asset_ctx)):
    return inv.list_branch_options(db, ctx, organization_id, company_id)


@router.get("/locations", response_model=List[TenantOption])
async def inv_locations(branch_id: UUID, db: Session = Depends(get_db), ctx: TenancyContext = Depends(require_asset_ctx)):
    return inv.list_location_options(db, ctx, branch_id)


# ============================================================
# CRUD
# ============================================================
@router.get("/", response_model=List[EquipmentListItem])
async def list_equipment(
    search: Optional[str] = None,
    organization_id: Optional[UUID] = None,
    company_id: Optional[UUID] = None,
    branch_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    marca: Optional[str] = None,
    serial: Optional[str] = None,
    assigned_user_id: Optional[UUID] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    return inv.list_equipment(
        db, ctx,
        organization_id=organization_id, company_id=company_id, branch_id=branch_id,
        department_id=department_id, tipo=tipo, estado=estado,
        marca=marca, serial=serial, assigned_user_id=assigned_user_id,
        search=search, limit=limit, offset=offset,
    )


@router.post("/", response_model=EquipmentResponse, status_code=201)
async def create_equipment(
    data: EquipmentCreate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    eq = inv.create_equipment(db, ctx, data, ctx.user)
    return inv.get_equipment(db, ctx, eq.id)


@router.get("/{equipment_id}", response_model=EquipmentResponse)
async def get_equipment(
    equipment_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    return inv.get_equipment(db, ctx, equipment_id)


@router.patch("/{equipment_id}", response_model=EquipmentResponse)
async def update_equipment(
    equipment_id: UUID,
    data: EquipmentUpdate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    inv.update_equipment(db, ctx, equipment_id, data, ctx.user)
    return inv.get_equipment(db, ctx, equipment_id)


# ============================================================
# ASIGNACIONES
# ============================================================
@router.post("/{equipment_id}/assign", response_model=AssignmentResponse, status_code=201)
async def assign_equipment(
    equipment_id: UUID,
    data: AssignmentCreate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    a = inv.assign_equipment(db, ctx, equipment_id, data, ctx.user)
    rows = inv.list_assignments(db, ctx, equipment_id)
    return next((r for r in rows if str(r["id"]) == str(a.id)), rows[0])


@router.post("/{equipment_id}/return", response_model=AssignmentResponse)
async def return_equipment(
    equipment_id: UUID,
    data: AssignmentReturn,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    a = inv.return_equipment(db, ctx, equipment_id, data, ctx.user)
    rows = inv.list_assignments(db, ctx, equipment_id)
    return next((r for r in rows if str(r["id"]) == str(a.id)), rows[0])


@router.get("/{equipment_id}/assignments", response_model=List[AssignmentResponse])
async def list_assignments(
    equipment_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    return inv.list_assignments(db, ctx, equipment_id)
