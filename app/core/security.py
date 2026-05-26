"""Seguridad: validación de tokens JWT de Supabase."""
import httpx
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import jwt, JWTError
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.core.config import settings
from app.core.supabase_client import supabase_admin


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
            headers={"WWW-Authenticate": "Bearer"}
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Valida el token de Supabase llamando directo al endpoint /auth/v1/user.
    Evita usar el SDK para esta validación porque tiene bugs con service_role.
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
            import logging
            logging.getLogger(__name__).warning(
                f"Auth validation failed: {resp.status_code} {resp.text[:200]}"
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido o expirado",
                headers={"WWW-Authenticate": "Bearer"}
            )

        user = resp.json()
        user_id = user.get("id")

        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Respuesta de auth inválida"
            )

        # Obtener perfil completo desde la tabla profiles (usa service_role para saltar RLS)
        profile_response = supabase_admin.table("profiles").select("*").eq(
            "id", user_id
        ).single().execute()

        if not profile_response.data:
            # Si no hay perfil, devolvemos uno mínimo (fallback)
            return {
                "id": user_id,
                "email": user.get("email"),
                "full_name": user.get("user_metadata", {}).get("full_name", ""),
                "role": "usuario",
            }

        return profile_response.data

    except HTTPException:
        raise
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Auth error: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Error de autenticación: {str(e)}"
        )


def require_role(*allowed_roles: str):
    """Decorator que valida que el usuario tenga un rol específico."""
    async def role_checker(current_user: Dict = Depends(get_current_user)) -> Dict:
        user_role = current_user.get("role", "usuario")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Acceso denegado. Roles permitidos: {', '.join(allowed_roles)}"
            )
        return current_user
    return role_checker


# Shortcuts comunes
require_admin = require_role("admin")
require_supervisor = require_role("admin", "supervisor")
require_tech = require_role("admin", "supervisor", "tecnico")
