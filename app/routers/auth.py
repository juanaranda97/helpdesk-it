"""Router de autenticación."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.supabase_client import supabase_admin, supabase_anon
from app.schemas.schemas import (
    LoginRequest, RegisterRequest, TokenResponse, ProfileResponse, ProfileUpdate
)

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest, db: Session = Depends(get_db)):
    """Registra un nuevo usuario."""
    try:
        # Crear usuario en Supabase Auth
        auth_response = supabase_admin.auth.admin.create_user({
            "email": data.email,
            "password": data.password,
            "email_confirm": True,  # autoconfirma email
            "user_metadata": {
                "full_name": data.full_name,
                "role": "usuario"
            }
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
        
        # Hacer login para obtener token
        login_response = supabase_anon.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })
        
        return TokenResponse(
            access_token=login_response.session.access_token,
            refresh_token=login_response.session.refresh_token,
            user={
                "id": user_id,
                "email": data.email,
                "full_name": data.full_name,
                "role": "usuario"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error al registrar: {str(e)}")


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest):
    """Login con email y contraseña."""
    try:
        response = supabase_anon.auth.sign_in_with_password({
            "email": data.email,
            "password": data.password
        })
        
        if not response.session:
            raise HTTPException(status_code=401, detail="Credenciales inválidas")
        
        # Obtener perfil
        profile_response = supabase_admin.table("profiles").select("*").eq(
            "id", response.user.id
        ).single().execute()
        
        profile = profile_response.data if profile_response.data else {
            "id": response.user.id,
            "email": data.email,
            "full_name": response.user.user_metadata.get("full_name", data.email.split("@")[0]),
            "role": "usuario"
        }
        
        return TokenResponse(
            access_token=response.session.access_token,
            refresh_token=response.session.refresh_token,
            user=profile
        )
        
    except HTTPException:
        raise

    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Login error: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=401, detail=f"Error: {type(e).__name__}: {str(e)}")


@router.post("/logout")
async def logout(current_user: dict = Depends(get_current_user)):
    """Cerrar sesión."""
    try:
        supabase_anon.auth.sign_out()
    except Exception:
        pass
    return {"message": "Sesión cerrada"}


@router.get("/me", response_model=ProfileResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Obtener perfil del usuario actual."""
    return current_user


@router.put("/me", response_model=ProfileResponse)
async def update_me(
    data: ProfileUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Actualizar perfil propio."""
    update_data = data.model_dump(exclude_unset=True, exclude_none=True)
    
    if update_data:
        result = supabase_admin.table("profiles").update(update_data).eq(
            "id", current_user["id"]
        ).execute()
        
        if result.data:
            return result.data[0]
    
    return current_user
