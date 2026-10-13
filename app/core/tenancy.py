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

    # ------------------------------------------------------------------
    # Scope de tenant (usado por el módulo de Tickets, vía SQLAlchemy)
    # ------------------------------------------------------------------
    @property
    def scope_org_id(self) -> Optional[str]:
        """
        Organización por la que se deben FILTRAR los recursos.

        - Usuario de plataforma SIN impersonar  -> None  (overview: ve todo lo que
          le permite su rol; no se aplica filtro de org). Es un permiso explícito
          de Ommeganet (can_access_all_orgs), no un bypass cross-tenant entre clientes.
        - Cualquier otro caso (usuario normal, o plataforma impersonando) -> active_org_id.
          El filtro se ata a la org activa resuelta y validada en backend.
        """
        if self.is_platform_user and not self.is_impersonating:
            return None
        return self.active_org_id

    def can_access_org(self, resource_org_id) -> bool:
        """
        ¿El contexto puede acceder a un recurso que pertenece a `resource_org_id`?

        Regla de seguridad:
        - overview de plataforma (scope None) -> True.
        - scope fijado a una org -> solo si coincide EXACTAMENTE.
        - Un recurso con organization_id NULL NUNCA es accesible bajo scope
          (evita que el ticket histórico sin tenant filtre a un cliente). Sin
          excepción general.
        """
        scope = self.scope_org_id
        if scope is None:
            return True
        if resource_org_id is None:
            return False
        return str(resource_org_id) == str(scope)

    @property
    def scope_company_id(self) -> Optional[str]:
        """
        Empresa por la que se debe FILTRAR (narrowing adicional dentro de la org),
        o None si no corresponde acotar por empresa.

        Aplica cuando el contexto está LIMITADO a una compañía:
        - Usuario de plataforma que eligió empresa (X-Company-Id validado).
        - Rol company_admin (acotado a su empresa).
        En overview de plataforma (sin org) -> None.
        """
        if self.scope_org_id is None:
            return None
        if self.is_platform_user and self.is_impersonating and self.active_company_id:
            return self.active_company_id
        if self.is_platform_user and self.active_company_id:
            return self.active_company_id
        if self.role == "company_admin" and self.active_company_id:
            return self.active_company_id
        return None

    def can_access_company(self, resource_company_id) -> bool:
        """Si el contexto está acotado a una empresa, el recurso debe ser de esa empresa."""
        sc = self.scope_company_id
        if sc is None:
            return True
        if resource_company_id is None:
            return False
        return str(resource_company_id) == str(sc)

    def tenant_for_create(self):
        """
        Devuelve (organization_id, company_id) a asignar a un recurso nuevo.
        Si no se puede determinar la organización de forma segura -> 400.
        """
        if not self.active_org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "No se pudo determinar la organización del ticket. "
                    "Si sos usuario de plataforma, seleccioná una organización."
                ),
            )
        return self.active_org_id, self.active_company_id


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


def _validate_company_in_org(company_id: str, org_id: str) -> None:
    """Valida (SOLO LECTURA) que la empresa pertenezca a la organización activa."""
    try:
        comp = (
            supabase_admin.table("companies")
            .select("id, organization_id")
            .eq("id", company_id)
            .single()
            .execute()
        )
    except Exception:
        comp = None
    data = getattr(comp, "data", None) if comp else None
    if not data or str(data.get("organization_id")) != str(org_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La empresa seleccionada no pertenece a la organización activa.",
        )


def get_scoped_tenancy(
    request: Request,
    current_user: Dict = Depends(get_current_user),
    x_org_id: Optional[str] = Header(None, alias="X-Org-Id"),
    x_company_id: Optional[str] = Header(None, alias="X-Company-Id"),
) -> TenancyContext:
    """
    Resuelve el contexto de tenancy para módulos de datos con scope por org
    (Tickets, Inventario, etc.).

    Diferencias con get_tenancy_context:
    - Un usuario de plataforma SIN organización propia NO recibe 403: queda en
      modo overview (scope_org_id == None). Evita romper el dashboard de Ommeganet.
    - Un usuario NO-plataforma SIGUE obligado a tener organización.
    - X-Org-Id solo lo puede usar un rol de plataforma (impersonation) y se valida
      en backend; nunca se confía en el header del navegador por sí solo.
    - Al impersonar, se valida que la org exista/activa y que la empresa (si viene)
      pertenezca a esa org. Todo con lecturas; sin INSERT/UPDATE/DELETE.
    """
    role = current_user.get("role", "usuario")
    user_org_id = current_user.get("organization_id")
    user_company_id = current_user.get("company_id")

    is_platform = permissions.is_platform_role(role)
    is_impersonating = False
    active_org_id = user_org_id
    active_company_id = user_company_id
    company_from_header = False   # ¿la empresa activa vino de un header (no confiable)?

    if x_org_id and x_org_id != user_org_id:
        if not is_platform:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tenés permisos para acceder a esa organización",
            )
        active_org_id = x_org_id
        is_impersonating = True
        active_company_id = x_company_id if x_company_id else None
        company_from_header = bool(x_company_id)
    elif x_company_id and is_platform:
        # Plataforma filtrando por empresa SIN cambiar de org (misma org activa).
        active_company_id = x_company_id
        company_from_header = True

    # Los no-plataforma DEBEN tener organización; los de plataforma pueden quedar unscoped.
    if not is_platform and not active_org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu usuario no tiene organización asignada. Contactá al administrador.",
        )

    # Un company_admin SIN empresa asignada NO puede obtener acceso amplio a la org:
    # falla con 403 en vez de quedar con scope_company_id=None.
    if role == "company_admin" and not active_company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu usuario (company_admin) no tiene empresa asignada. Contactá al administrador.",
        )

    # Validar la org impersonada (existe/activa) — caso sensible.
    if is_impersonating:
        org_check = (
            supabase_admin.table("organizations")
            .select("id, name, is_active")
            .eq("id", active_org_id)
            .single()
            .execute()
        )
        if not org_check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organización no encontrada")
        if not org_check.data.get("is_active", True):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Esta organización está suspendida")
        request.state.target_org_name = org_check.data["name"]

    # Validar que la empresa del HEADER pertenezca a la org activa — SIEMPRE que
    # la empresa provino de un header (con o sin cambio de org). Nunca se confía
    # en X-Company-Id del navegador sin verificarlo contra la org.
    if company_from_header and active_company_id and active_org_id:
        _validate_company_in_org(active_company_id, active_org_id)

    ctx = TenancyContext(
        user=current_user,
        active_org_id=active_org_id,
        active_company_id=active_company_id,
        is_impersonating=is_impersonating,
    )
    request.state.tenancy = ctx
    return ctx


# Alias de compatibilidad para el módulo de Tickets (misma lógica, nombre genérico).
get_ticket_tenancy = get_scoped_tenancy


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
