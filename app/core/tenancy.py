"""
Middleware de tenancy multi-organización.

Cada request lleva contexto de:
  - El usuario autenticado
  - Su organización "natural" (a la que pertenece)
  - La organización "activa" (la que está consultando ahora mismo)

Para usuarios normales, ambas coinciden.
Para platform_owner/admin (Ommeganet), pueden "impersonar" otra org
usando el header X-Org-Id, lo cual queda registrado en audit log.
"""
from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends, Request, status, Header
from app.core.security import get_current_user
from app.core.supabase_client import supabase_admin
from app.core import permissions


class TenancyContext:
    """Contexto de tenancy de un request."""

    def __init__(
        self,
        user: Dict[str, Any],
        active_org_id: str,
        active_company_id: Optional[str] = None,
        is_impersonating: bool = False,
    ):
        self.user = user
        self.user_id: str = user["id"]
        self.role: str = user.get("role", "usuario")
        self.user_org_id: Optional[str] = user.get("organization_id")
        self.user_company_id: Optional[str] = user.get("company_id")
        self.active_org_id: str = active_org_id
        self.active_company_id: Optional[str] = active_company_id
        self.is_impersonating: bool = is_impersonating
        self.is_platform_user: bool = permissions.is_platform_role(self.role)

    def require_role(self, allowed_roles):
        """Levanta 403 si el rol no está permitido."""
        if isinstance(allowed_roles, str):
            allowed_roles = {allowed_roles}
        elif not isinstance(allowed_roles, set):
            allowed_roles = set(allowed_roles)

        if self.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado para rol '{self.role}'",
            )

    def filter_query(self, query):
        """
        Aplica filtro multi-tenant a una query de Supabase.
        Usar así:
            q = supabase_admin.table("tickets").select("*")
            q = ctx.filter_query(q)
        """
        return query.eq("organization_id", self.active_org_id)


def get_tenancy_context(
    request: Request,
    current_user: Dict = Depends(get_current_user),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_company_id: Optional[str] = Header(None, alias="X-Company-Id"),
) -> TenancyContext:
    """
    Resuelve el contexto de tenancy de un request.

    - Usuarios normales: usan SU organization_id, ignoran los headers.
    - Platform users (Ommeganet): pueden pasar X-Org-Id para "impersonar"
      otra organización. Esto se registra en audit log.
    """
    role = current_user.get("role", "usuario")
    user_org_id = current_user.get("organization_id")
    user_company_id = current_user.get("company_id")

    is_platform = permissions.is_platform_role(role)
    is_impersonating = False
    active_org_id: Optional[str] = user_org_id
    active_company_id: Optional[str] = user_company_id

    if x_org_id and x_org_id != user_org_id:
        # Quiere acceder a otra org
        if not is_platform:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés permisos para acceder a esa organización",
            )
        active_org_id = x_org_id
        is_impersonating = True
        # Si está cambiando de org, el company_id debe re-resolverse
        active_company_id = x_company_id if x_company_id else None

    elif x_company_id and is_platform:
        # Platform user filtrando por empresa específica
        active_company_id = x_company_id

    if not active_org_id:
        # Si el usuario no tiene org asignada y no es platform, error
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu usuario no tiene organización asignada. Contactá al administrador.",
        )

    # Validar que la org existe y está activa
    org_check = (
        supabase_admin.table("organizations")
        .select("id, name, is_active")
        .eq("id", active_org_id)
        .single()
        .execute()
    )
    if not org_check.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organización no encontrada",
        )
    if not org_check.data.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta organización está suspendida",
        )

    ctx = TenancyContext(
        user=current_user,
        active_org_id=active_org_id,
        active_company_id=active_company_id,
        is_impersonating=is_impersonating,
    )

    # Guardar contexto en request.state para acceso desde audit middleware
    request.state.tenancy = ctx
    request.state.target_org_name = org_check.data["name"]

    return ctx


def require_platform_role(ctx: TenancyContext = Depends(get_tenancy_context)) -> TenancyContext:
    """Solo permite roles de plataforma (Ommeganet)."""
    if not ctx.is_platform_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta acción requiere permisos de plataforma",
        )
    return ctx


def require_org_admin(ctx: TenancyContext = Depends(get_tenancy_context)) -> TenancyContext:
    """Requiere ser admin de la organización (o superior)."""
    if not permissions.can_manage_org(ctx.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requiere permisos de administrador de la organización",
        )
    return ctx


def require_asset_manager(ctx: TenancyContext = Depends(get_tenancy_context)) -> TenancyContext:
    """Para gestión de inventario, licencias, proveedores."""
    if not permissions.can_manage_assets(ctx.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requiere permisos de gestión de activos",
        )
    return ctx


def require_baja_permission(ctx: TenancyContext = Depends(get_tenancy_context)) -> TenancyContext:
    """Para dar de baja usuarios (admin o RRHH)."""
    if not permissions.can_baja_user(ctx.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requiere permisos de gestión de usuarios",
        )
    return ctx
