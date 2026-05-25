"""Servicio de almacenamiento de archivos en Supabase Storage."""
import mimetypes
from uuid import uuid4
from typing import Optional, Tuple
from fastapi import UploadFile, HTTPException, status

from app.core.config import settings
from app.core.supabase_client import supabase_admin


# Tipos de archivo permitidos por categoría
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/heic"}
ALLOWED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-msvideo"}
ALLOWED_AUDIO_TYPES = {"audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/webm"}
ALLOWED_DOC_TYPES = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/plain",
    "text/csv",
}

ALL_ALLOWED = ALLOWED_IMAGE_TYPES | ALLOWED_VIDEO_TYPES | ALLOWED_AUDIO_TYPES | ALLOWED_DOC_TYPES


def detect_attachment_type(content_type: str) -> str:
    """Detecta el tipo de attachment según el content-type."""
    if content_type in ALLOWED_IMAGE_TYPES:
        return "image"
    if content_type in ALLOWED_VIDEO_TYPES:
        return "video"
    if content_type in ALLOWED_AUDIO_TYPES:
        return "audio"
    return "file"


def validate_file(file: UploadFile) -> None:
    """Valida tipo y tamaño del archivo."""
    if file.content_type not in ALL_ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tipo de archivo no permitido: {file.content_type}"
        )


async def upload_file_to_storage(
    file: UploadFile,
    ticket_id: str,
    user_id: str
) -> dict:
    """Sube un archivo a Supabase Storage y retorna metadata."""
    
    # Validar
    validate_file(file)
    
    # Leer contenido
    content = await file.read()
    file_size = len(content)
    
    if file_size > settings.max_file_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Archivo demasiado grande (máx. {settings.MAX_FILE_SIZE_MB}MB)"
        )
    
    # Generar path único
    ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
    unique_name = f"{uuid4()}.{ext}"
    storage_path = f"{ticket_id}/{unique_name}"
    
    # Subir a Supabase Storage
    try:
        response = supabase_admin.storage.from_(settings.STORAGE_BUCKET).upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": file.content_type or "application/octet-stream",
                "cache-control": "3600"
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al subir archivo: {str(e)}"
        )
    
    # Obtener URL pública
    public_url = supabase_admin.storage.from_(settings.STORAGE_BUCKET).get_public_url(storage_path)
    
    return {
        "file_name": file.filename,
        "file_url": public_url,
        "file_size": file_size,
        "file_type": file.content_type,
        "attachment_type": detect_attachment_type(file.content_type or ""),
        "storage_path": storage_path,
    }


def delete_file_from_storage(storage_path: str) -> bool:
    """Borra un archivo del storage."""
    try:
        supabase_admin.storage.from_(settings.STORAGE_BUCKET).remove([storage_path])
        return True
    except Exception:
        return False
