"""Configuración de tests.

- Variables de entorno dummy ANTES de importar la app.
- Fixtures con una base PostgreSQL REAL y aislada (coretest local), para que los
  filtros SQL se apliquen de verdad (no mocks). Incluye los CHECK reales de
  `equipment` y un TestClient con overrides de get_db / get_current_user.
"""
import os
import uuid

_DUMMY_JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl"
os.environ.setdefault("SUPABASE_URL", "https://dummy.supabase.co")
os.environ.setdefault("SUPABASE_KEY", _DUMMY_JWT)
os.environ.setdefault("SUPABASE_ANON_KEY", _DUMMY_JWT)
os.environ.setdefault("JWT_SECRET", "x_very_long_dummy_secret_for_tests_1234567890")
os.environ.setdefault("ENVIRONMENT", "test")

# Base de pruebas REAL (cluster local levantado para la sesión).
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/coretest")
# La app crea su engine desde DATABASE_URL al importar: apuntamos a la misma base.
os.environ.setdefault("DATABASE_URL", TEST_DB_URL)

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

EQUIPMENT_TABLES = [
    "equipment_assignments", "equipment", "tickets", "ticket_comments",
    "ticket_attachments", "satisfaction_surveys", "notifications", "activity_log",
    "profiles", "categories", "locations", "system_config",
    "branches", "physical_locations", "organizations", "companies", "departments",
    "profile_audit_log",
]

# Hosts locales autorizados para correr la suite (NUNCA una base remota/productiva).
_ALLOWED_TEST_HOSTS = {"127.0.0.1", "localhost", "::1", ""}
# Ref del proyecto Supabase productivo: se bloquea explícitamente por las dudas.
_FORBIDDEN_SUBSTRINGS = ("supabase", "ctazrasxhkgckcbenhvb", "amazonaws", "render.com")


def _assert_safe_test_db(url: str):
    """Rechaza CUALQUIER base cuyo host/nombre no corresponda explícitamente al
    entorno local de pruebas autorizado. Falla ANTES de ejecutar cualquier DROP
    si existe la menor incertidumbre. Nunca permite tocar Supabase productivo."""
    u = make_url(url)
    host = (u.host or "").lower()
    db = (u.database or "").lower()
    blob = f"{host} {db} {url}".lower()

    for bad in _FORBIDDEN_SUBSTRINGS:
        if bad in blob:
            raise RuntimeError(
                f"ABORT tests: la base apunta a un entorno NO local/productivo "
                f"(coincide con '{bad}'): host={host!r} db={db!r}. "
                f"La suite NO se ejecutará para proteger datos productivos."
            )
    if host not in _ALLOWED_TEST_HOSTS:
        raise RuntimeError(f"ABORT tests: host no autorizado para pruebas: {host!r} (solo local).")
    # El nombre de la base debe ser explícitamente de pruebas.
    if not (db == "coretest" or db.startswith("test") or db.endswith("_test") or "test" in db):
        raise RuntimeError(f"ABORT tests: nombre de base no autorizado para pruebas: {db!r}.")
    return u


@pytest.fixture(scope="session")
def engine():
    # Guarda de seguridad: valida la URL ANTES de crear el engine o hacer DROP.
    _assert_safe_test_db(TEST_DB_URL)
    eng = create_engine(TEST_DB_URL)
    from app.models.models import Base
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        c.execute(text("CREATE SEQUENCE IF NOT EXISTS tickets_ticket_number_seq;"))
    Base.metadata.create_all(eng)
    # Reproducir los CHECK reales de equipment (no existen en el modelo ORM).
    with eng.begin() as c:
        c.execute(text(
            "ALTER TABLE equipment ADD CONSTRAINT equipment_tipo_check CHECK "
            "(tipo = ANY (ARRAY['NB','PC','Monitor','Mouse','Teclado','Auricular',"
            "'Impresora','Router','Switch','Servidor','Otro']))"
        ))
        c.execute(text(
            "ALTER TABLE equipment ADD CONSTRAINT equipment_estado_check CHECK "
            "(estado = ANY (ARRAY['disponible','asignado','reparacion','baja','extraviado']))"
        ))
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean(engine):
    """Limpia las tablas antes de cada test (aislamiento entre tests)."""
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(EQUIPMENT_TABLES) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(Session):
    s = Session()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def api_client(engine, Session):
    """TestClient con get_db y get_current_user overrideados.
    Usar client.login(user_dict) para fijar el usuario autenticado."""
    from fastapi.testclient import TestClient
    import main
    from app.core import database, security

    holder = {"user": None}

    def override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    def override_user():
        from fastapi import HTTPException
        if holder["user"] is None:
            raise HTTPException(status_code=401, detail="no user")
        return holder["user"]

    main.app.dependency_overrides[database.get_db] = override_db
    main.app.dependency_overrides[security.get_current_user] = override_user

    client = TestClient(main.app)
    client.login = lambda u: holder.__setitem__("user", u)
    yield client
    main.app.dependency_overrides.clear()


# ----------------------------------------------------------------------
# Helpers de datos
# ----------------------------------------------------------------------
def mk_profile(db, org_id, *, role="usuario", company_id=None, full_name="Usuario",
               is_active=True, estado_laboral=None, email=None):
    from app.models import Profile
    p = Profile(id=uuid.uuid4(), email=email or f"{uuid.uuid4().hex[:8]}@t.com", full_name=full_name,
                role=role, is_active=is_active, estado_laboral=estado_laboral,
                organization_id=org_id, company_id=company_id)
    db.add(p); db.commit()
    return p


def mk_equipment(db, org_id, *, tipo="NB", estado="disponible", company_id=None,
                 codigo_interno=None, created_by=None, **extra):
    from app.models import Equipment
    e = Equipment(id=uuid.uuid4(), tipo=tipo, estado=estado, organization_id=org_id,
                  company_id=company_id, codigo_interno=codigo_interno, created_by=created_by, **extra)
    db.add(e); db.commit()
    return e


def mk_org(db, *, name="Org", is_active=True, org_id=None):
    import uuid as _u
    from app.models import Organization
    o = Organization(id=org_id or _u.uuid4(), name=name, is_active=is_active)
    db.add(o); db.commit()
    return o


def mk_company(db, org_id, *, name="Empresa", is_active=True, company_id=None):
    import uuid as _u
    from app.models import Company
    c = Company(id=company_id or _u.uuid4(), organization_id=org_id, name=name, is_active=is_active)
    db.add(c); db.commit()
    return c


def mk_department(db, org_id, company_id, *, name="Depto", is_active=True):
    import uuid as _u
    from app.models import Department
    d = Department(id=_u.uuid4(), name=name, organization_id=org_id, company_id=company_id, is_active=is_active)
    db.add(d); db.commit()
    return d


def mk_branch(db, org_id, *, company_id=None, name="Sucursal", is_active=True):
    from app.models import Branch
    b = Branch(id=uuid.uuid4(), name=name, is_active=is_active, organization_id=org_id, company_id=company_id)
    db.add(b); db.commit()
    return b


def mk_location(db, org_id, branch_id, *, company_id=None, name="Ubicación", is_active=True):
    from app.models import PhysicalLocation
    p = PhysicalLocation(id=uuid.uuid4(), name=name, branch_id=branch_id, is_active=is_active,
                         organization_id=org_id, company_id=company_id)
    db.add(p); db.commit()
    return p


def user_dict(profile):
    return {
        "id": str(profile.id), "email": profile.email, "full_name": profile.full_name,
        "role": profile.role,
        "organization_id": str(profile.organization_id) if profile.organization_id else None,
        "company_id": str(profile.company_id) if profile.company_id else None,
        "estado_laboral": "activo",
    }
