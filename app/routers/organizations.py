"""Router de organizaciones (tenants)."""
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request

from app.core.supabase_client import supabase_admin
from app.core.tenancy import (
    get_tenancy_context,
    require_platform_role,
    TenancyContext,
)
from app.core.audit import log_cross_org_access
from app.schemas.tenancy import (
    OrganizationOut,
    OrganizationCreate,
    OrganizationUpdate,
    OrganizationSummary,
    ImpersonateRequest,
    ImpersonateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/organizations", tags=["Organizaciones"])


# ============================================================
# LISTAR ORGANIZACIONES
# ============================================================
@router.get("/", response_model=List[OrganizationSummary])
async def list_organizations(
    include_inactive: bool = False,
    ctx: TenancyContext = Depends(get_tenancy_context),
):
    """
    Lista organizaciones.
    - Platform users (Ommeganet): ven TODAS las orgs.
    - Otros usuarios: ven SOLO la suya.
    """
    q = supabase_admin.table("v_organizations_summary").select("*")

    if not ctx.is_platform_user:
        # Usuarios normales solo ven su propia org
        q = q.eq("id", ctx.user_org_id)

    if not include_inactive:
        q = q.eq("is_active", True)

    result = q.execute()
    return result.data or []


# ============================================================
# OBTENER UNA ORG
# ============================================================
@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(
    org_id: str,
    request: Request,
    ctx: TenancyContext = Depends(get_tenancy_context),
):
    """Detalle de una organización."""
    # Validar permisos: solo platform users pueden ver cualquier org, los demás solo la suya
    if not ctx.is_platform_user and org_id != ctx.user_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenés permisos para ver esa organización",
        )

    result = supabase_admin.table("organizations").select("*").eq("id", org_id).single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organización no encontrada",
        )

    # Audit log si platform está accediendo a org externa
    if ctx.is_platform_user and org_id != ctx.user_org_id:
        log_cross_org_access(
            ctx=ctx,
            request=request,
            resource="organization",
            action="view_detail",
            resource_id=org_id,
        )

    return result.data


# ============================================================
# CREAR ORG (solo platform_owner)
# ============================================================
@router.post("/", response_model=OrganizationOut, status_code=201)
async def create_organization(
    data: OrganizationCreate,
    ctx: TenancyContext = Depends(require_platform_role),
):
    """Crea una nueva organización. Solo Ommeganet (platform_owner)."""
    payload = data.model_dump()
    payload["created_by"] = ctx.user_id

    try:
        result = supabase_admin.table("organizations").insert(payload).execute()
        if not result.data:
            raise HTTPException(status_code=400, detail="No se pudo crear la organización")
        return result.data[0]
    except Exception as e:
        logger.error(f"Create org error: {e}")
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Ya existe una org con ese nombre o slug")
        raise HTTPException(status_code=400, detail=f"Error al crear: {str(e)}")


# ============================================================
# ACTUALIZAR ORG
# ============================================================
@router.patch("/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: str,
    data: OrganizationUpdate,
    request: Request,
    ctx: TenancyContext = Depends(get_tenancy_context),
):
    """Actualiza una org. Platform users pueden cualquiera, org_owners solo la suya."""
    from app.core import permissions

    if not ctx.is_platform_user:
        if org_id != ctx.user_org_id:
            raise HTTPException(status_code=403, detail="No podés editar esa organización")
        if not permissions.can_manage_org(ctx.role):
            raise HTTPException(status_code=403, detail="No tenés permisos para editar la org")

    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No hay cambios para guardar")

    result = supabase_admin.table("organizations").update(update_data).eq("id", org_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Organización no encontrada")

    if ctx.is_platform_user and org_id != ctx.user_org_id:
        log_cross_org_access(
            ctx=ctx, request=request, resource="organization",
            action="update", resource_id=org_id,
        )

    return result.data[0]


# ============================================================
# IMPERSONATE (Ommeganet entra a ver datos de otra org)
# ============================================================
@router.post("/{org_id}/impersonate", response_model=ImpersonateResponse)
async def impersonate_organization(
    org_id: str,
    request: Request,
    body: ImpersonateRequest,
    ctx: TenancyContext = Depends(require_platform_role),
):
    """
    Inicia un acceso a otra organización (Ommeganet → cliente).
    Esto registra automáticamente en el audit log.

    El frontend debería usar el X-Org-Id header en las siguientes peticiones
    para ver datos de esa org. Este endpoint principalmente sirve para:
    1. Validar que la org existe y está activa
    2. Registrar el inicio del acceso en audit log
    """
    if org_id != body.organization_id:
        raise HTTPException(status_code=400, detail="org_id en URL y body no coinciden")

    # Validar org
    org = supabase_admin.table("organizations").select("id, name, is_active").eq(
        "id", org_id
    ).single().execute()

    if not org.data:
        raise HTTPException(status_code=404, detail="Organización no encontrada")

    if not org.data["is_active"]:
        raise HTTPException(status_code=403, detail="Esta organización está suspendida")

    # Validar empresa si se pasó
    if body.company_id:
        company = supabase_admin.table("companies").select("id, name").eq(
            "id", body.company_id
        ).eq("organization_id", org_id).single().execute()
        if not company.data:
            raise HTTPException(
                status_code=404,
                detail="La empresa no existe o no pertenece a esa organización",
            )

    # Audit log: inicio de acceso
    # Truco: forzamos is_impersonating=True para que se loguee
    ctx.is_impersonating = True
    ctx.active_org_id = org_id
    ctx.active_company_id = body.company_id

    log_cross_org_access(
        ctx=ctx,
        request=request,
        resource="organization",
        action="impersonate_start",
        resource_id=org_id,
        metadata={"reason": body.reason} if body.reason else None,
    )

    return ImpersonateResponse(
        success=True,
        organization_id=org_id,
        organization_name=org.data["name"],
        company_id=body.company_id,
        message=f"Accediste a {org.data['name']}. Este acceso queda registrado.",
    )
