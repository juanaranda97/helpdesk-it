"""Pruebas REALES de Inventario sobre PostgreSQL (filtros SQL efectivos + CHECKs).

Cubren: aislamiento por organización y empresa (listado, stats, usuarios asignables),
restricciones CHECK de tipo/estado, flujo de asignación/devolución, no-doble-asignación,
y regresión de pérdida de datos al editar.
"""
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError, DataError

from app.core.tenancy import TenancyContext
from app.services import inventory as inv
from app.schemas.inventory import EquipmentUpdate, AssignmentCreate, AssignmentReturn
from tests.conftest import mk_profile, mk_equipment, user_dict

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
COMP_A1 = uuid.uuid4()
COMP_A2 = uuid.uuid4()


def ctx_for(role="tecnico", org=ORG_A, company=None, active=None, impersonating=False):
    org_s = str(org) if org else None
    active_s = str(active) if active else org_s
    return TenancyContext(
        user={"id": str(uuid.uuid4()), "role": role, "organization_id": org_s,
              "company_id": str(company) if company else None},
        active_org_id=active_s,
        active_company_id=str(company) if company else None,
        is_impersonating=impersonating,
    )


# ======================================================================
# Aislamiento REAL de listado y stats (filtros SQL aplicados de verdad)
# ======================================================================
def test_list_isolation_por_organizacion(db):
    mk_equipment(db, ORG_A, codigo_interno="A-1", serial="SA1")
    mk_equipment(db, ORG_A, codigo_interno="A-2", serial="SA2")
    mk_equipment(db, ORG_B, codigo_interno="B-1", serial="SB1")

    rows_a = inv.list_equipment(db, ctx_for(org=ORG_A))
    rows_b = inv.list_equipment(db, ctx_for(org=ORG_B))
    assert {r["codigo_interno"] for r in rows_a} == {"A-1", "A-2"}
    assert {r["codigo_interno"] for r in rows_b} == {"B-1"}


def test_stats_no_mezclan_organizaciones(db):
    mk_equipment(db, ORG_A, estado="disponible")
    mk_equipment(db, ORG_A, estado="reparacion")
    mk_equipment(db, ORG_B, estado="disponible")
    s = inv.get_stats(db, ctx_for(org=ORG_A))
    assert s["total"] == 2
    assert s["por_estado"].get("disponible") == 1
    assert s["por_estado"].get("reparacion") == 1


def test_platform_overview_ve_todas(db):
    mk_equipment(db, ORG_A, codigo_interno="A-1")
    mk_equipment(db, ORG_B, codigo_interno="B-1")
    rows = inv.list_equipment(db, ctx_for(role="platform_owner", org=None))
    assert {r["codigo_interno"] for r in rows} == {"A-1", "B-1"}


def test_list_filtra_por_empresa_cuando_el_contexto_esta_acotado(db):
    mk_equipment(db, ORG_A, company_id=COMP_A1, codigo_interno="A1-1")
    mk_equipment(db, ORG_A, company_id=COMP_A2, codigo_interno="A2-1")
    # company_admin acotado a COMP_A1
    ctx = ctx_for(role="company_admin", org=ORG_A, company=COMP_A1)
    rows = inv.list_equipment(db, ctx)
    assert {r["codigo_interno"] for r in rows} == {"A1-1"}


# ======================================================================
# CHECK constraints reales de PostgreSQL
# ======================================================================
def test_check_estado_invalido_rechazado_por_postgres(db):
    from app.models import Equipment
    bad = Equipment(id=uuid.uuid4(), tipo="NB", estado="en_reparacion", organization_id=ORG_A)
    db.add(bad)
    with pytest.raises((IntegrityError, DataError)):
        db.commit()
    db.rollback()


def test_check_tipo_invalido_rechazado_por_postgres(db):
    from app.models import Equipment
    bad = Equipment(id=uuid.uuid4(), tipo="notebook", estado="disponible", organization_id=ORG_A)
    db.add(bad)
    with pytest.raises((IntegrityError, DataError)):
        db.commit()
    db.rollback()


def test_valores_validos_se_insertan(db):
    e = mk_equipment(db, ORG_A, tipo="Servidor", estado="baja", codigo_interno="SRV-1")
    assert e.id is not None


# ======================================================================
# Asignación / devolución
# ======================================================================
def test_assign_y_historial(db):
    target = mk_profile(db, ORG_A, full_name="María")
    eq = mk_equipment(db, ORG_A, codigo_interno="A-1")
    ctx = ctx_for(org=ORG_A)
    a = inv.assign_equipment(db, ctx, eq.id, AssignmentCreate(user_id=target.id), {"id": str(uuid.uuid4())})
    assert a.user_id == target.id
    db.refresh(eq)
    assert eq.estado == "asignado"
    hist = inv.list_assignments(db, ctx, eq.id)
    assert len(hist) == 1 and hist[0]["user_name"] == "María"


def test_assign_usuario_de_otra_org_403(db):
    target_b = mk_profile(db, ORG_B, full_name="Otro")
    eq = mk_equipment(db, ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(db, ctx_for(org=ORG_A), eq.id, AssignmentCreate(user_id=target_b.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 403


def test_no_doble_asignacion(db):
    t1 = mk_profile(db, ORG_A); t2 = mk_profile(db, ORG_A)
    eq = mk_equipment(db, ORG_A)
    ctx = ctx_for(org=ORG_A)
    inv.assign_equipment(db, ctx, eq.id, AssignmentCreate(user_id=t1.id), {"id": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(db, ctx, eq.id, AssignmentCreate(user_id=t2.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 409


def test_return_libera_y_permite_reasignar(db):
    t1 = mk_profile(db, ORG_A); t2 = mk_profile(db, ORG_A)
    eq = mk_equipment(db, ORG_A)
    ctx = ctx_for(org=ORG_A)
    inv.assign_equipment(db, ctx, eq.id, AssignmentCreate(user_id=t1.id), {"id": str(uuid.uuid4())})
    inv.return_equipment(db, ctx, eq.id, AssignmentReturn(returned_reason="fin"), {"id": str(uuid.uuid4())})
    db.refresh(eq); assert eq.estado == "disponible"
    # ahora sí se puede reasignar
    inv.assign_equipment(db, ctx, eq.id, AssignmentCreate(user_id=t2.id), {"id": str(uuid.uuid4())})
    hist = inv.list_assignments(db, ctx, eq.id)
    assert len(hist) == 2


def test_assign_cross_tenant_404(db):
    target = mk_profile(db, ORG_A)
    eq_b = mk_equipment(db, ORG_B)
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(db, ctx_for(org=ORG_A), eq_b.id, AssignmentCreate(user_id=target.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 404


# ======================================================================
# Regresión: editar solo la ubicación NO borra las specs (item 4)
# ======================================================================
def test_editar_ubicacion_preserva_specs(db):
    eq = mk_equipment(
        db, ORG_A, codigo_interno="A-1",
        procesador="i7-1165G7", ram="16GB", sistema_operativo="Windows 11", antivirus="Defender",
        ubicacion="Oficina 1",
    )
    ctx = ctx_for(org=ORG_A)
    inv.update_equipment(db, ctx, eq.id, EquipmentUpdate(ubicacion="Oficina 2"), {"id": str(uuid.uuid4())})
    db.refresh(eq)
    assert eq.ubicacion == "Oficina 2"
    assert eq.procesador == "i7-1165G7"
    assert eq.ram == "16GB"
    assert eq.sistema_operativo == "Windows 11"
    assert eq.antivirus == "Defender"


# ======================================================================
# Usuarios asignables: aislados por org (y empresa)
# ======================================================================
def test_assignable_users_solo_de_la_org(db):
    mk_profile(db, ORG_A, full_name="A-user1")
    mk_profile(db, ORG_A, full_name="A-user2")
    mk_profile(db, ORG_B, full_name="B-user")
    users = inv.list_assignable_users(db, ctx_for(org=ORG_A))
    names = {u["full_name"] for u in users}
    assert names == {"A-user1", "A-user2"}


def test_assignable_users_filtra_por_empresa(db):
    mk_profile(db, ORG_A, company_id=COMP_A1, full_name="C1-user")
    mk_profile(db, ORG_A, company_id=COMP_A2, full_name="C2-user")
    users = inv.list_assignable_users(db, ctx_for(role="company_admin", org=ORG_A, company=COMP_A1))
    assert {u["full_name"] for u in users} == {"C1-user"}


# ======================================================================
# API end-to-end (TestClient) — prueba router + service + SQL real
# ======================================================================
def test_api_crud_y_aislamiento(api_client, db):
    ua = user_dict(mk_profile(db, ORG_A, role="org_admin", full_name="AdminA"))
    ub = user_dict(mk_profile(db, ORG_B, role="org_admin", full_name="AdminB"))

    api_client.login(ua)
    r = api_client.post("/inventory/", json={"tipo": "NB", "codigo_interno": "API-A-1", "marca": "Dell"})
    assert r.status_code == 201, r.text
    assert str(r.json()["organization_id"]) == str(ORG_A)   # tenant fijado en backend

    # Org A ve su activo
    r = api_client.get("/inventory/")
    assert [x["codigo_interno"] for x in r.json()] == ["API-A-1"]

    # Org B NO lo ve
    api_client.login(ub)
    r = api_client.get("/inventory/")
    assert r.json() == []

    # tipo inválido -> 422 (Pydantic, antes de tocar la DB)
    api_client.login(ua)
    r = api_client.post("/inventory/", json={"tipo": "notebook"})
    assert r.status_code == 422


# ======================================================================
# Item 1: aislamiento por EMPRESA dentro de una misma organización
# ======================================================================
def test_company_admin_404_al_leer_activo_de_otra_empresa(db):
    eq_b = mk_equipment(db, ORG_A, company_id=COMP_A2, codigo_interno="A2-X")
    ctx = ctx_for(role="company_admin", org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.get_equipment(db, ctx, eq_b.id)
    assert e.value.status_code == 404


def test_company_admin_404_al_modificar_activo_de_otra_empresa(db):
    eq_b = mk_equipment(db, ORG_A, company_id=COMP_A2, codigo_interno="A2-Y")
    ctx = ctx_for(role="company_admin", org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.update_equipment(db, ctx, eq_b.id, EquipmentUpdate(ubicacion="X"), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 404


def test_company_admin_404_al_ver_asignaciones_de_otra_empresa(db):
    eq_b = mk_equipment(db, ORG_A, company_id=COMP_A2, codigo_interno="A2-Z")
    ctx = ctx_for(role="company_admin", org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.list_assignments(db, ctx, eq_b.id)
    assert e.value.status_code == 404


def test_company_admin_404_al_asignar_activo_de_otra_empresa(db):
    from tests.conftest import mk_profile
    eq_b = mk_equipment(db, ORG_A, company_id=COMP_A2)
    target = mk_profile(db, ORG_A, company_id=COMP_A1)
    ctx = ctx_for(role="company_admin", org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(db, ctx, eq_b.id, AssignmentCreate(user_id=target.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 404


# ======================================================================
# Item 1: sucursal / ubicación deben pertenecer al tenant del activo
# ======================================================================
def test_crear_con_sucursal_de_otra_org_da_400(db):
    from app.schemas.inventory import EquipmentCreate
    from tests.conftest import mk_branch
    branch_b = mk_branch(db, ORG_B)   # sucursal de OTRA org
    ctx = ctx_for(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, ctx, EquipmentCreate(tipo="NB", branch_id=branch_b.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 400


def test_crear_con_sucursal_valida_ok(db):
    from app.schemas.inventory import EquipmentCreate
    from tests.conftest import mk_branch
    branch_a = mk_branch(db, ORG_A)
    ctx = ctx_for(role="org_admin", org=ORG_A)
    eq = inv.create_equipment(db, ctx, EquipmentCreate(tipo="NB", branch_id=branch_a.id, codigo_interno="OK-1"), {"id": str(uuid.uuid4())})
    assert str(eq.branch_id) == str(branch_a.id)


def test_ubicacion_de_otra_sucursal_da_400(db):
    from app.schemas.inventory import EquipmentCreate
    from tests.conftest import mk_branch, mk_location
    b1 = mk_branch(db, ORG_A); b2 = mk_branch(db, ORG_A)
    loc_b2 = mk_location(db, ORG_A, b2.id)
    ctx = ctx_for(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, ctx, EquipmentCreate(tipo="NB", branch_id=b1.id, physical_location_id=loc_b2.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 400


# ======================================================================
# Item 4: KPI usa los códigos reales ('reparacion', 'baja')
# ======================================================================
def test_stats_cuenta_reparacion_y_baja_desde_codigos_reales(db):
    mk_equipment(db, ORG_A, estado="reparacion", codigo_interno="R-1")
    mk_equipment(db, ORG_A, estado="baja", codigo_interno="B-1")
    mk_equipment(db, ORG_A, estado="disponible", codigo_interno="D-1")
    s = inv.get_stats(db, ctx_for(org=ORG_A))
    assert s["en_reparacion"] == 1
    assert s["de_baja"] == 1
    assert s["disponibles"] == 1

