"""
Audit log automático cuando un usuario de Ommeganet (platform_owner / platform_admin)
accede a datos de otra organización.

Esto cumple con la ley 6534/20 de Paraguay (protección de datos) y es
estándar profesional para apps SaaS multi-tenant.

Uso desde un endpoint:

    from app.core.audit import log_cross_org_access

    @router.get("/tickets")
    async def list_tickets(ctx: TenancyContext = Depends(get_tenancy_context), request: Request):
        # Tu lógica de listar tickets...

        # Si Ommeganet está accediendo a otra org, registrarlo:
        if ctx.is_impersonating:
            log_cross_org_access(
                ctx=ctx,
                request=request,
                resource="tickets",
                action="view_list",
            )

        return tickets
"""
import logging
from typing import Optional, Dict, Any
from fastapi import Request
from app.core.supabase_client import supabase_admin

logger = logging.getLogger(__name__)


def log_cross_org_access(
    ctx,
    request: Optional[Request] = None,
    resource: str = "",
    action: str = "view_list",
    resource_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
):
    """
    Registra un acceso de Ommeganet a datos de un cliente.

    Solo loguea si:
      - El usuario es de plataforma (Ommeganet)
      - Está accediendo a una org distinta a la suya

    Si falla el log, NO interrumpe el flujo del request (best-effort).
    """
    try:
        # Solo loguear si está impersonando otra org
        if not getattr(ctx, "is_impersonating", False):
            return

        ip_address = None
        user_agent = None
        method = None
        path = None
        target_org_name = "(desconocido)"

        if request is not None:
            ip_address = request.client.host if request.client else None
            user_agent = request.headers.get("user-agent")
            method = request.method
            path = request.url.path
            target_org_name = getattr(request.state, "target_org_name", "(desconocido)")

        payload = {
            "actor_id": ctx.user_id,
            "actor_email": ctx.user.get("email", ""),
            "actor_organization_id": ctx.user_org_id,
            "actor_role": ctx.role,
            "target_organization_id": ctx.active_org_id,
            "target_organization_name": target_org_name,
            "target_company_id": ctx.active_company_id,
            "target_resource": resource,
            "target_resource_id": resource_id,
            "action": action,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "request_method": method,
            "request_path": path,
            "metadata": metadata,
        }

        supabase_admin.table("cross_org_audit_log").insert(payload).execute()

    except Exception as e:
        # NUNCA interrumpir un request por fallar el audit log
        logger.error(f"Falló audit log cross-org: {type(e).__name__}: {e}")


def query_audit_log(
    organization_id: Optional[str] = None,
    actor_id: Optional[str] = None,
    resource: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Lee el audit log con filtros opcionales."""
    q = supabase_admin.table("cross_org_audit_log").select("*")

    if organization_id:
        q = q.eq("target_organization_id", organization_id)
    if actor_id:
        q = q.eq("actor_id", actor_id)
    if resource:
        q = q.eq("target_resource", resource)

    q = q.order("created_at", desc=True).range(offset, offset + limit - 1)
    return q.execute().data or []
