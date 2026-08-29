"""Seguridad: validación de tokens JWT de Supabase + contexto de tenancy."""
import logging
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import jwt, JWTError
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.supabase_client import supabase_admin


logger = logging.getLogger(__name__)
security = HTTPBearer()


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Crea un JWT propio (para casos especiales)."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Dict[str, Any]:
    """Decodifica un JWT propio."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Dict[str, Any]:
    """
    Valida el token de Supabase llamando directo al endpoint /auth/v1/user.
    Retorna el perfil del usuario CON datos de tenancy (org, company, role).
    """
    token = credentials.credentials
    auth_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/user"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                auth_url,
                headers={
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {token}",
                },
            )

        if resp.status_code != 200:
            logger.warning(f"Auth validation failed: {resp.status_code} {resp.text[:200]}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado",
                headers={"WWW-Authenticate": "Bearer"},
            )

        user = resp.json()
        user_id = user.get("id")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Respuesta de auth inválida",
            )

        # Obtener perfil completo CON todos los campos de tenancy
        profile_resp = (
            supabase_admin.table("profiles")
            .select(
                "id, email, full_name, role, organization_id, company_id, "
                "branch_id, department_id, estado_laboral, ci, cargo, "
                "phone, whatsapp, department"
            )
            .eq("id", user_id)
            .single()
            .execute()
        )

        if not profile_resp.data:
            # Fallback mínimo si no hay perfil
            return {
                "id": user_id,
                "email": user.get("email"),
                "full_name": user.get("user_metadata", {}).get("full_name", ""),
                "role": "usuario",
                "organization_id": None,
                "company_id": None,
                "estado_laboral": "activo",
            }

        profile = profile_resp.data

        # Bloquear usuarios dados de baja
        if profile.get("estado_laboral") == "baja":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu cuenta está dada de baja. Contactá al administrador.",
            )
        if profile.get("estado_laboral") == "suspendido":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu cuenta está suspendida temporalmente.",
            )

        return profile

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Auth error: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Error de autenticación: {str(e)}",
        )


def require_role(*allowed_roles: str):
    """Decorator legacy - mantenido por compatibilidad. Preferir tenancy.require_*."""

    async def role_checker(current_user: Dict = Depends(get_current_user)) -> Dict:
        user_role = current_user.get("role", "usuario")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. Roles permitidos: {', '.join(allowed_roles)}",
            )
        return current_user

    return role_checker


# Shortcuts comunes (legacy)
require_admin = require_role("admin", "superadmin", "platform_owner", "org_admin")
require_supervisor = require_role(
    "admin", "supervisor", "superadmin", "platform_owner", "org_admin", "manager"
)
require_tech = require_role(
    "admin", "supervisor", "tecnico", "superadmin", "platform_owner", "org_admin", "manager"
)
