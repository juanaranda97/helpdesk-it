"""Configuración central de la aplicación."""
from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings cargados desde variables de entorno (.env)."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # ---- App ----
    APP_NAME: str = "HelpDesk IT Pro API"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "info"
    
    # ---- Supabase ----
    SUPABASE_URL: str
    SUPABASE_KEY: str  # service_role para operaciones admin
    SUPABASE_ANON_KEY: str  # para verificar tokens del frontend
    
    # ---- Database ----
    DATABASE_URL: str
    
    # ---- JWT ----
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 10080  # 7 días
    
    # ---- CORS ----
    ALLOWED_ORIGINS: str = "http://localhost:5500"
    
    # ---- Storage ----
    STORAGE_BUCKET: str = "ticket-files"
    MAX_FILE_SIZE_MB: int = 25
    
    @property
    def allowed_origins_list(self) -> List[str]:
        """Convierte la cadena de orígenes en lista."""
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]
    
    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_FILE_SIZE_MB * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Singleton de settings con cache."""
    return Settings()


settings = get_settings()
