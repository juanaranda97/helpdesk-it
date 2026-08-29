"""Router de autenticación con soporte multi-tenant."""
import logging
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict

from app.core.config import settings
from app.core.security import get_current_user
from app.core.supabase_client import supabase_admin, supabase_anon
from app.core import permissions
from app.schemas.schemas import (
    LoginRequest, RegisterRequest, TokenResponse, ProfileResponse, ProfileUpdate
)
from app.schemas.tenancy import UserContext

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["Autenticación"])


# ============================================================
# REGISTRO
# ============================================================
@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest):
    """Registra un nuevo usuario."""
    try:
        # Crear en Supabase Auth
        auth_response = supabase_admin.auth.admin.create_user({
            "email": data.email,
            "password": data.password,
            "email_confirm": True,
            "user_metadata": {
                "full_name": data.full_name,
                "role": "usuario",
            },
        })

        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Error al crear usuario")

        user_id = auth_response.user.id

        # Actualizar perfil con datos adicionales
        supabase_admin.table("profiles").update({
            "phone": data.phone,
            "whatsapp": data.whatsapp,
            "department": data.department,
            "full_name": data.full_name,
        }).eq("id", user_id).execute()

        # Hacer login para obtener token (vía httpx directo, más confiable)
        token_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/token?grant_type=password"

        async with httpx.AsyncClient(timeout=10.0) as client:
            login_resp = await client.post(
                token_url,
                headers={
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Content-Type": "application/json",
                },
                json={"email": data.email, "password": data.password},
            )

        if login_resp.status_code != 200:
            raise HTTPException(status_code=400, detail="Usuario creado pero error al loguearse")

        body = login_resp.json()

        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            user={
                "id": user_id,
                "email": data.email,
                "full_name": data.full_name,
                "role": "usuario",
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Register error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=400, detail=f"Error al registrar: {str(e)}")


# ============================================================
# LOGIN
# ============================================================
@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest):
    """Login con email y contraseña — llamada HTTP directa a Supabase Auth."""
    auth_url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/token?grant_type=password"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                auth_url,
                headers={
                    "apikey": settings.SUPABASE_ANON_KEY,
                    "Content-Type": "application/json",
                },
                json={"email": data.email, "password": data.password},
            )

        if resp.status_code != 200:
            logger.warning(f"Supabase auth failed: {resp.status_code} {resp.text[:200]}")
            raise HTTPException(status_code=401, detail="Credenciales inválidas")

        body = resp.json()
        user_id = body["user"]["id"]

        # Obtener perfil
        profile_resp = supabase_admin.table("profiles").select("*").eq(
            "id", user_id
        ).single().execute()

        if not profile_resp.data:
            profile = {
                "id": user_id,
                "email": data.email,
                "full_name": body["user"].get("user_metadata", {}).get(
                    "full_name", data.email.split("@")[0]
                ),
                "role": "usuario",
            }
        else:
            profile = profile_resp.data

            # Bloquear usuarios dados de baja
            if profile.get("estado_laboral") == "baja":
                raise HTTPException(
                    status_code=403,
                    detail="Tu cuenta está dada de baja. Contactá al administrador.",
                )

        return TokenResponse(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
            user=profile,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=401, detail=f"Error: {type(e).__name__}: {str(e)}")


# ============================================================
# LOGOUT
# ============================================================
@router.post("/logout")
async def logout(current_user: Dict = Depends(get_current_user)):
    """Cerrar sesión."""
    try:
        supabase_anon.auth.sign_out()
    except Exception:
        pass
    return {"message": "Sesión cerrada"}


# ============================================================
# ME (perfil)
# ============================================================
@router.get("/me", response_model=ProfileResponse)
async def get_me(current_user: Dict = Depends(get_current_user)):
    """Obtener perfil del usuario actual."""
    return current_user


@router.put("/me", response_model=ProfileResponse)
async def update_me(data: ProfileUpdate, current_user: Dict = Depends(get_current_user)):
    """Actualizar perfil propio."""
    update_data = data.model_dump(exclude_unset=True, exclude_none=True)

    if update_data:
        result = supabase_admin.table("profiles").update(update_data).eq(
            "id", current_user["id"]
        ).execute()
        if result.data:
            return result.data[0]

    return current_user


# ============================================================
# CONTEXT (lo más importante: devuelve todo el contexto de tenancy + permisos)
# ============================================================
@router.get("/me/context", response_model=UserContext)
async def get_my_context(current_user: Dict = Depends(get_current_user)):
    """
    Devuelve el contexto completo del usuario logueado:
    organización, empresa, rol, permisos calculados.

    El frontend usa esto al iniciar sesión para saber qué mostrar.
    """
    role = current_user.get("role", "usuario")
    org_id = current_user.get("organization_id")
    company_id = current_user.get("company_id")

    # Buscar nombre de la org
    org_name = None
    org_slug = None
    if org_id:
        try:
            org = supabase_admin.table("organizations").select("name, slug").eq(
                "id", org_id
            ).single().execute()
            if org.data:
                org_name = org.data["name"]
                org_slug = org.data["slug"]
        except Exception:
            pass

    # Buscar nombre de la empresa
    company_name = None
    if company_id:
        try:
            company = supabase_admin.table("companies").select("name").eq(
                "id", company_id
            ).single().execute()
            if company.data:
                company_name = company.data["name"]
        except Exception:
            pass

    return UserContext(
        user_id=current_user["id"],
        email=current_user.get("email", ""),
        full_name=current_user.get("full_name", ""),
        role=role,
        organization_id=org_id,
        organization_name=org_name,
        organization_slug=org_slug,
        company_id=company_id,
        company_name=company_name,
        is_platform_user=permissions.is_platform_role(role),
        can_access_all_orgs=permissions.can_access_all_orgs(role),
        can_manage_org=permissions.can_manage_org(role),
        can_manage_assets=permissions.can_manage_assets(role),
        can_baja_user=permissions.can_baja_user(role),
        can_access_vault=permissions.can_access_vault(role),
        estado_laboral=current_user.get("estado_laboral", "activo"),
    )
