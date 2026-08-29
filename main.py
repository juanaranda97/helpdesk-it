"""FastAPI - HelpDesk IT Pro Backend (Multi-tenant)."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.core.config import settings

# Routers existentes
from app.routers.auth import router as auth_router
from app.routers.tickets import router as tickets_router
from app.routers.catalogs import (
    categories_router,
    locations_router,
    users_router,
    config_router,
    notifications_router,
)

# Routers nuevos (multi-tenant)
from app.routers.organizations import router as organizations_router
from app.routers.companies import router as companies_router


# Logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} - {settings.ENVIRONMENT}")
    logger.info(f"📡 CORS allowed origins: {settings.allowed_origins_list}")
    logger.info(f"🏢 Multi-tenant mode: ENABLED")
    yield
    logger.info("👋 Shutting down...")


# Crear app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="API REST para sistema de tickets IT multi-tenant",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*", "X-Org-Id", "X-Company-Id"],
    expose_headers=["*"],
)


# Exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Maneja errores de validación con formato amigable."""
    errors = []
    for err in exc.errors():
        loc = " → ".join(str(x) for x in err.get("loc", []))
        errors.append({
            "field": loc,
            "message": err.get("msg", "Error de validación"),
        })
    return JSONResponse(
        status_code=422,
        content={"detail": "Datos inválidos", "errors": errors},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Captura errores no esperados."""
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Error interno del servidor"},
    )


# Health check
@app.get("/", tags=["Health"])
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "ok",
        "environment": settings.ENVIRONMENT,
        "multi_tenant": True,
        "docs": "/docs",
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "healthy"}


# Registrar routers
# --- Nuevos multi-tenant ---
app.include_router(organizations_router)
app.include_router(companies_router)

# --- Existentes ---
app.include_router(auth_router)
app.include_router(tickets_router)
app.include_router(categories_router)
app.include_router(locations_router)
app.include_router(users_router)
app.include_router(config_router)
app.include_router(notifications_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=(settings.ENVIRONMENT == "development"),
        log_level=settings.LOG_LEVEL.lower(),
    )
