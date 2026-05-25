# HelpDesk IT Pro - Backend (Python/FastAPI)

API REST profesional para el sistema de tickets IT.

## Stack
- **Python 3.12** + **FastAPI** (framework moderno y rápido)
- **SQLAlchemy** (ORM)
- **Pydantic v2** (validación de datos)
- **Supabase** (auth + storage + postgres)
- **JWT** (autenticación)

## Estructura
```
backend/
├── app/
│   ├── core/          # config, db, security, supabase
│   ├── models/        # SQLAlchemy models
│   ├── schemas/       # Pydantic schemas
│   ├── routers/       # Endpoints REST
│   └── services/      # Lógica de negocio
├── main.py            # Entry point FastAPI
├── requirements.txt   # Deps
├── Dockerfile
└── render.yaml        # Config Render.com
```

## Setup local

```bash
# 1. Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables
cp .env.example .env
# Editar .env con tus credenciales de Supabase

# 4. Ejecutar
uvicorn main:app --reload
```

API disponible en `http://localhost:8000`  
Docs interactivos en `http://localhost:8000/docs`

## Endpoints principales

### Auth
- `POST /auth/register` - Registro de usuario
- `POST /auth/login` - Login
- `GET /auth/me` - Perfil del usuario actual
- `PUT /auth/me` - Actualizar perfil

### Tickets
- `POST /tickets/` - Crear ticket
- `GET /tickets/my` - Mis tickets
- `GET /tickets/all` - Todos los tickets (tech+)
- `GET /tickets/{id}` - Ver ticket
- `PATCH /tickets/{id}` - Actualizar ticket
- `POST /tickets/{id}/close` - Cerrar con firma digital
- `POST /tickets/{id}/attachments` - Subir archivo
- `POST /tickets/{id}/comments` - Agregar comentario
- `POST /tickets/{id}/survey` - Encuesta de satisfacción
- `GET /tickets/stats/me` - Estadísticas personales
- `GET /tickets/stats/all` - Estadísticas globales

### Catálogos
- `GET /categories/` - Listar categorías
- `GET /locations/` - Listar sucursales
- `GET /users/technicians` - Listar técnicos
- `GET /config/` - Configuración del sistema
- `GET /notifications/` - Notificaciones del usuario

## Despliegue en Render.com

1. Sube `backend/` a un repo de GitHub
2. En Render.com: **New > Web Service**
3. Conectá tu repo
4. **Root Directory**: `backend`
5. **Build Command**: `pip install -r requirements.txt`
6. **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
7. **Plan**: Free
8. Agregá las variables de entorno (ver `.env.example`)

## Despliegue con Docker

```bash
docker build -t helpdesk-backend .
docker run -p 8000:8000 --env-file .env helpdesk-backend
```
