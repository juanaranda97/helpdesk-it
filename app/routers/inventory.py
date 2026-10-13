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
    InventoryStats, InventoryMeta, AssignableUser,
)
from app.services import inventory as inv

router = APIRouter(prefix="/inventory", tags=["Inventario"])


def require_asset_ctx(ctx: TenancyContext = Depends(get_scoped_tenancy)) -> TenancyContext:
    """Contexto de tenancy + exige rol de gestión de activos."""
    if not permissions.can_manage_assets(ctx.role):
        raise HTTPException(status_code=403, detail="Requiere permisos de gestión de activos")
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
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_asset_ctx),
):
    """Usuarios elegibles para asignar un activo — SOLO de la org/empresa activa.
    (Se registra antes de /{equipment_id} para no ser capturado por el path param.)"""
    return inv.list_assignable_users(db, ctx)


# ============================================================
# CRUD
# ============================================================
@router.get("/", response_model=List[EquipmentListItem])
async def list_equipment(
    search: Optional[str] = None,
    company_id: Optional[UUID] = None,
    branch_id: Optional[UUID] = None,
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
        company_id=company_id, branch_id=branch_id, tipo=tipo, estado=estado,
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

