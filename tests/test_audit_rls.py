"""R0 correcciones — Seguridad de profile_audit_log (RLS + permisos mínimos).

Verifica que, tras aplicar la parte de seguridad de la migración 0002, los roles
expuestos por PostgREST (anon / authenticated) NO pueden consultar ni modificar la
auditoría directamente, mientras que el backend (rol privilegiado/superusuario,
que omite RLS) sí conserva acceso.
"""
import uuid
import pytest
from sqlalchemy import text


# Mismas sentencias de seguridad que la migración 0002 (sin el guard DO, porque
# acá los roles se crean antes). Se ejecutan como bloque multi-statement.
SECURITY_SQL = """
ALTER TABLE public.profile_audit_log ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.profile_audit_log FROM PUBLIC;
REVOKE ALL ON public.profile_audit_log FROM anon;
REVOKE ALL ON public.profile_audit_log FROM authenticated;
"""


def _ensure_roles_and_security(engine):
    with engine.begin() as c:
        c.exec_driver_sql("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
        END $$;
        """)
        # Dar USAGE de schema (como en Supabase) para que la ÚNICA barrera sea la tabla.
        c.exec_driver_sql("GRANT USAGE ON SCHEMA public TO anon, authenticated")
        c.exec_driver_sql(SECURITY_SQL)


def test_anon_authenticated_no_acceden_a_auditoria(db):
    engine = db.get_bind()
    engine = getattr(engine, "engine", engine)
    _ensure_roles_and_security(engine)

    # Insertar una fila como superusuario (backend) -> OK.
    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO public.profile_audit_log (id, action, field, result) "
            "VALUES (:i,'t','role','ok')"
        ), {"i": str(uuid.uuid4())})

    for role in ("anon", "authenticated"):
        # SELECT directo debe fallar (sin privilegio de tabla).
        with engine.connect() as c:
            c.execute(text(f"SET ROLE {role}"))
            with pytest.raises(Exception):
                c.execute(text("SELECT count(*) FROM public.profile_audit_log")).fetchone()
        # INSERT directo debe fallar.
        with engine.connect() as c:
            c.execute(text(f"SET ROLE {role}"))
            with pytest.raises(Exception):
                c.execute(text("INSERT INTO public.profile_audit_log (action,field,result) VALUES ('x','role','ok')"))

    # El backend (superusuario) sí ve la fila.
    with engine.connect() as c:
        n = c.execute(text("SELECT count(*) FROM public.profile_audit_log")).scalar()
        assert n >= 1
