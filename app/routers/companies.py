"""Router de empresas (companies) dentro de organizaciones."""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query

from app.core.supabase_client import supabase_admin
from app.core.tenancy import (
    get_tenancy_context,
    require_org_admin,
    TenancyContext,
)
from app.core.audit import log_cross_org_access
from app.schemas.tenancy import (
    CompanyOut,
    CompanyCreate,
    CompanyUpdate,
    CompanySummary,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/companies", tags=["Empresas"])


# ============================================================
# LISTAR EMPRESAS
# ============================================================
@router.get("/", response_model=List[CompanySummary])
async def list_companies(
    organization_id: Optional[str] = Query(None),
    include_inactive: bool = False,
    ctx: TenancyContext = Depends(get_tenancy_context),
):
    """
    Lista empresas.
    - Si pasas organization_id, lista las de esa org (con validación de permisos).
    - Si no pasas nada, lista las de tu org activa.
    """
    target_org_id = organization_id or ctx.active_org_id

    # Validar permisos
    if not ctx.is_platform_user and target_org_id != ctx.user_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No podés listar empresas de otra organización",
        )

    q = supabase_admin.table("v_companies_summary").select("*")

    if target_org_id:
        # Filtrar por org via subquery (la view no tiene organization_id directo, usar nombre)
        # Mejor: traer todas y filtrar — o agregar campo
        # Solución simple: traer de companies con join
        q = supabase_admin.table("companies").select(
            "id, name, industry, is_active, organization_id"
        ).eq("organization_id", target_org_id)

    if not include_inactive:
        q = q.eq("is_active", True)

    result = q.execute()
    rows = result.data or []

    # Enriquecer con nombre de org y conteos
    enriched = []
    for row in rows:
        # Conteo de usuarios
        users_count = supabase_admin.table("profiles").select(
            "id", count="exact"
        ).eq("company_id", row["id"]).execute()

        # Nombre de org
        org_name = "(sin org)"
        if row.get("organization_id"):
            org = supabase_admin.table("organizations").select("name").eq(
                "id", row["organization_id"]
            ).single().execute()
            if org.data:
                org_name = org.data["name"]

        enriched.append({
            "id": row["id"],
            "name": row["name"],
            "industry": row.get("industry"),
            "is_active": row["is_active"],
            "organization_name": org_name,
            "user_count": users_count.count or 0,
            "equipment_count": 0,  # se llenará cuando esté el módulo de inventario
            "ticket_count": 0,
        })

    return enriched


# ============================================================
# OBTENER UNA EMPRESA
# ============================================================
@router.get("/{company_id}", response_model=CompanyOut)
async def get_company(
    company_id: str,
    request: Request,
    ctx: TenancyContext = Depends(get_tenancy_context),
):
    """Detalle de una empresa."""
    result = supabase_admin.table("companies").select("*").eq("id", company_id).single().execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    company = result.data

    # Validar permisos
    if not ctx.is_platform_user and company["organization_id"] != ctx.user_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permisos para ver esa empresa",
        )

    # Audit log
    if ctx.is_platform_user and company["organization_id"] != ctx.user_org_id:
        log_cross_org_access(
            ctx=ctx, request=request, resource="company",
            action="view_detail", resource_id=company_id,
        )

    return company


# ============================================================
# CREAR EMPRESA
# ============================================================
@router.post("/", response_model=CompanyOut, status_code=201)
async def create_company(
    data: CompanyCreate,
    ctx: TenancyContext = Depends(require_org_admin),
):
    """Crea una empresa dentro de una organización. Solo org_admins."""
    # Validar permisos sobre la org destino
    if not ctx.is_platform_user and data.organization_id != ctx.user_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No podés crear empresas en otra organización",
        )

    payload = data.model_dump()
    payload["created_by"] = ctx.user_id

    try:
        result = supabase_admin.table("companies").insert(payload).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="No se pudo crear la empresa")
        return result.data[0]
    except Exception as e:
        logger.error(f"Create company error: {e}")
        if "duplicate" in str(e).lower():
            raise HTTPException(
                status_code=409,
                detail="Ya existe una empresa con ese nombre en esa organización",
            )
        raise HTTPException(status_code=400, detail=f"Error al crear: {str(e)}")


# ============================================================
# ACTUALIZAR EMPRESA
# ============================================================
@router.patch("/{company_id}", response_model=CompanyOut)
async def update_company(
    company_id: str,
    data: CompanyUpdate,
    request: Request,
    ctx: TenancyContext = Depends(require_org_admin),
):
    """Actualiza una empresa."""
    # Verificar que la empresa existe
    existing = supabase_admin.table("companies").select("organization_id").eq(
        "id", company_id
    ).single().execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    # Validar permisos
    if not ctx.is_platform_user and existing.data["organization_id"] != ctx.user_org_id:
        raise HTTPException(status_code=403, detail="No podés editar esa empresa")

    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar")

    result = supabase_admin.table("companies").update(update_data).eq("id", company_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    # Audit log si cross-org
    if ctx.is_platform_user and existing.data["organization_id"] != ctx.user_org_id:
        log_cross_org_access(
            ctx=ctx, request=request, resource="company",
            action="update", resource_id=company_id,
        )

    return result.data[0]


# ============================================================
# DESACTIVAR EMPRESA (soft delete)
# ============================================================
@router.delete("/{company_id}")
async def deactivate_company(
    company_id: str,
    request: Request,
    ctx: TenancyContext = Depends(require_org_admin),
):
    """Desactiva (soft-delete) una empresa. No borra datos."""
    existing = supabase_admin.table("companies").select("organization_id, name").eq(
        "id", company_id
    ).single().execute()

    if not existing.data:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")

    if not ctx.is_platform_user and existing.data["organization_id"] != ctx.user_org_id:
        raise HTTPException(status_code=403, detail="No tenés permisos")

    supabase_admin.table("companies").update({"is_active": False}).eq(
        "id", company_id
    ).execute()

    if ctx.is_platform_user and existing.data["organization_id"] != ctx.user_org_id:
        log_cross_org_access(
            ctx=ctx, request=request, resource="company",
            action="delete", resource_id=company_id,
        )

    return {"message": f"Empresa '{existing.data['name']}' desactivada"}
