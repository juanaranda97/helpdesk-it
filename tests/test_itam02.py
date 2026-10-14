"""ITAM-02 — pertenencia multiempresa, departamento, responsable y selectores.

Pruebas con PostgreSQL real: contrato seguro de tenant (plataforma elige / cliente fijo),
validación de departamento, selectores dependientes scopeados, usuarios asignables
(empresa + búsqueda + exclusión de bajas), y asignación inicial transaccional.
"""
import uuid
import pytest
from fastapi import HTTPException

from app.core.tenancy import TenancyContext
from app.services import inventory as inv
from app.schemas.inventory import EquipmentCreate
from tests.conftest import (
    mk_org, mk_company, mk_department, mk_branch, mk_location, mk_profile, user_dict,
)

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


def ctx(role="org_admin", org=ORG_A, company=None, platform=False):
    org_s = str(org) if org else None
    return TenancyContext(
        user={"id": str(uuid.uuid4()), "role": role,
              "organization_id": None if platform else org_s,
              "company_id": str(company) if company else None},
        active_org_id=None if platform else org_s,
        active_company_id=str(company) if company else None,
        is_impersonating=False,
    )


# ======================================================================
# Contrato seguro de tenant de creación
# ======================================================================
def test_platform_elige_org_y_empresa(db):
    mk_org(db, org_id=ORG_A)
    comp = mk_company(db, ORG_A)
    c = ctx(role="platform_owner", platform=True)
    eq = inv.create_equipment(db, c, EquipmentCreate(tipo="NB", organization_id=ORG_A, company_id=comp.id), {"id": str(uuid.uuid4())})
    assert str(eq.organization_id) == str(ORG_A)
    assert str(eq.company_id) == str(comp.id)


def test_platform_sin_org_o_empresa_da_400(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A)
    c = ctx(role="platform_owner", platform=True)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", organization_id=ORG_A), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 400   # falta empresa


def test_org_admin_no_puede_otra_org(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    comp_b = mk_company(db, ORG_B)
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", organization_id=ORG_B, company_id=comp_b.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 403


def test_company_admin_empresa_fija(db):
    mk_org(db, org_id=ORG_A)
    comp1 = mk_company(db, ORG_A); comp2 = mk_company(db, ORG_A)
    c = ctx(role="company_admin", org=ORG_A, company=comp1.id)
    # intenta registrar en comp2 -> 403 (su empresa es comp1)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", company_id=comp2.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 403
    # en su empresa -> ok
    eq = inv.create_equipment(db, c, EquipmentCreate(tipo="NB", company_id=comp1.id, codigo_interno="CA-1"), {"id": str(uuid.uuid4())})
    assert str(eq.company_id) == str(comp1.id)


# ======================================================================
# Departamento
# ======================================================================
def test_departamento_de_otra_empresa_da_400(db):
    mk_org(db, org_id=ORG_A)
    comp1 = mk_company(db, ORG_A); comp2 = mk_company(db, ORG_A)
    dep2 = mk_department(db, ORG_A, comp2.id)
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", company_id=comp1.id, department_id=dep2.id), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 400


def test_departamento_valido_ok(db):
    mk_org(db, org_id=ORG_A)
    comp = mk_company(db, ORG_A)
    dep = mk_department(db, ORG_A, comp.id, name="Administración")
    c = ctx(role="org_admin", org=ORG_A)
    eq = inv.create_equipment(db, c, EquipmentCreate(tipo="Impresora", company_id=comp.id, department_id=dep.id, codigo_interno="IMP-1"), {"id": str(uuid.uuid4())})
    assert str(eq.department_id) == str(dep.id)


# ======================================================================
# Asignación inicial (responsable) — transaccional
# ======================================================================
def test_crear_con_responsable_valido(db):
    mk_org(db, org_id=ORG_A)
    comp = mk_company(db, ORG_A)
    resp = mk_profile(db, ORG_A, company_id=comp.id, full_name="Resp OK")
    c = ctx(role="org_admin", org=ORG_A)
    eq = inv.create_equipment(db, c, EquipmentCreate(tipo="NB", company_id=comp.id, assigned_to=resp.id, codigo_interno="R-OK"), {"id": str(uuid.uuid4())})
    assert eq.estado == "asignado"
    hist = inv.list_assignments(db, c, eq.id)
    assert len(hist) == 1 and str(hist[0]["user_id"]) == str(resp.id)


def test_crear_con_responsable_de_otra_empresa_falla_y_no_crea(db):
    from app.models import Equipment
    mk_org(db, org_id=ORG_A)
    comp1 = mk_company(db, ORG_A); comp2 = mk_company(db, ORG_A)
    resp_otra = mk_profile(db, ORG_A, company_id=comp2.id)
    c = ctx(role="org_admin", org=ORG_A)
    before = db.query(Equipment).count()
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", company_id=comp1.id, assigned_to=resp_otra.id, codigo_interno="R-BAD"), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 403
    # transacción: NO quedó activo a medias
    assert db.query(Equipment).count() == before


# ======================================================================
# Selectores dependientes (scopeados / validados)
# ======================================================================
def test_companies_de_otra_org_403(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        inv.list_company_options(db, c, ORG_B)
    assert e.value.status_code == 403


def test_departments_filtra_por_empresa(db):
    mk_org(db, org_id=ORG_A)
    comp = mk_company(db, ORG_A)
    mk_department(db, ORG_A, comp.id, name="Admin")
    mk_department(db, ORG_A, comp.id, name="Soporte")
    c = ctx(role="org_admin", org=ORG_A)
    deps = inv.list_department_options(db, c, comp.id)
    assert {d["name"] for d in deps} == {"Admin", "Soporte"}


def test_platform_lista_todas_las_orgs(db):
    mk_org(db, org_id=ORG_A, name="A"); mk_org(db, org_id=ORG_B, name="B")
    c = ctx(role="platform_owner", platform=True)
    orgs = inv.list_org_options(db, c)
    assert {o["name"] for o in orgs} == {"A", "B"}


def test_org_admin_solo_ve_su_org(db):
    mk_org(db, org_id=ORG_A, name="A"); mk_org(db, org_id=ORG_B, name="B")
    c = ctx(role="org_admin", org=ORG_A)
    orgs = inv.list_org_options(db, c)
    assert {o["name"] for o in orgs} == {"A"}


# ======================================================================
# Usuarios asignables: empresa + búsqueda + exclusión de bajas
# ======================================================================
def test_assignable_users_empresa_busqueda_y_bajas(db):
    mk_org(db, org_id=ORG_A)
    comp = mk_company(db, ORG_A)
    mk_profile(db, ORG_A, company_id=comp.id, full_name="Ana Perez", email="ana@x.com")
    mk_profile(db, ORG_A, company_id=comp.id, full_name="Beto Diaz", email="beto@x.com")
    mk_profile(db, ORG_A, company_id=comp.id, full_name="Baja User", estado_laboral="baja")
    c = ctx(role="platform_owner", platform=True)
    # sin búsqueda: 2 activos (excluye la baja)
    users = inv.list_assignable_users(db, c, company_id=comp.id)
    assert {u["full_name"] for u in users} == {"Ana Perez", "Beto Diaz"}
    # búsqueda por nombre
    r = inv.list_assignable_users(db, c, company_id=comp.id, search="ana")
    assert [u["full_name"] for u in r] == ["Ana Perez"]
    # búsqueda por email
    r = inv.list_assignable_users(db, c, company_id=comp.id, search="beto@")
    assert [u["full_name"] for u in r] == ["Beto Diaz"]


# ======================================================================
# Listado enriquecido (nombres de pertenencia) + filtro por departamento
# ======================================================================
def test_listado_enriquece_nombres_y_filtra_departamento(db):
    from tests.conftest import mk_equipment
    mk_org(db, org_id=ORG_A, name="Grupo X")
    comp = mk_company(db, ORG_A, name="Fertimax")
    dep = mk_department(db, ORG_A, comp.id, name="Administración")
    mk_equipment(db, ORG_A, company_id=comp.id, department_id=dep.id, codigo_interno="E-1")
    mk_equipment(db, ORG_A, company_id=comp.id, codigo_interno="E-2")
    c = ctx(role="org_admin", org=ORG_A)
    rows = inv.list_equipment(db, c)
    by_code = {r["codigo_interno"]: r for r in rows}
    assert by_code["E-1"]["organization_name"] == "Grupo X"
    assert by_code["E-1"]["company_name"] == "Fertimax"
    assert by_code["E-1"]["department_name"] == "Administración"
    # filtro por departamento
    only = inv.list_equipment(db, c, department_id=dep.id)
    assert [r["codigo_interno"] for r in only] == ["E-1"]


# ======================================================================
# REGRESIÓN CROSS-EMPRESA (ITAM-02 revisión, punto 1 y 7)
# Un rol sub-organización (tecnico/supervisor/manager) acotado a la EMPRESA A
# de una organización NO puede ver ni tocar datos de la EMPRESA B de la MISMA
# organización: listado, stats, ficha, historial, sucursales y usuarios.
# ======================================================================
from tests.conftest import mk_equipment, mk_branch  # noqa: E402

COMP_A1 = uuid.uuid4()
COMP_A2 = uuid.uuid4()
SUBORG_ROLES = ["tecnico", "supervisor", "manager"]


def _seed_dos_empresas(db):
    """ORG_A con dos empresas (A1, A2), cada una con activo, sucursal, depto y user."""
    mk_org(db, org_id=ORG_A, name="Grupo")
    mk_company(db, ORG_A, name="Empresa A1", company_id=COMP_A1)
    mk_company(db, ORG_A, name="Empresa A2", company_id=COMP_A2)
    eq1 = mk_equipment(db, ORG_A, company_id=COMP_A1, codigo_interno="A1-EQ", estado="disponible")
    eq2 = mk_equipment(db, ORG_A, company_id=COMP_A2, codigo_interno="A2-EQ", estado="disponible")
    mk_branch(db, ORG_A, company_id=COMP_A1, name="Suc A1")
    mk_branch(db, ORG_A, company_id=COMP_A2, name="Suc A2")
    mk_department(db, ORG_A, COMP_A1, name="Depto A1")
    mk_department(db, ORG_A, COMP_A2, name="Depto A2")
    mk_profile(db, ORG_A, company_id=COMP_A1, full_name="User A1", email="a1@x.com")
    mk_profile(db, ORG_A, company_id=COMP_A2, full_name="User A2", email="a2@x.com")
    return eq1, eq2


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_listado_solo_ve_su_empresa(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    rows = inv.list_equipment(db, c)
    assert {r["codigo_interno"] for r in rows} == {"A1-EQ"}


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_stats_no_cuenta_otra_empresa(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    s = inv.get_stats(db, c)
    assert s["total"] == 1   # solo el activo de A1


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_ficha_de_otra_empresa_da_404(db, role):
    _eq1, eq2 = _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.get_equipment(db, c, eq2.id)
    assert e.value.status_code == 404


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_historial_de_otra_empresa_da_404(db, role):
    _eq1, eq2 = _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.list_assignments(db, c, eq2.id)
    assert e.value.status_code == 404


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_sucursales_solo_su_empresa(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    branches = inv.list_branch_options(db, c, ORG_A)
    assert {b["name"] for b in branches} == {"Suc A1"}


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_departamentos_de_otra_empresa_403(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.list_department_options(db, c, COMP_A2)
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_companies_solo_su_empresa(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    comps = inv.list_company_options(db, c, ORG_A)
    assert {x["name"] for x in comps} == {"Empresa A1"}


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_usuarios_asignables_solo_su_empresa(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    # Sin pedir empresa: usa el scope. No debe exponer usuarios de A2.
    users = inv.list_assignable_users(db, c)
    assert {u["full_name"] for u in users} == {"User A1"}


@pytest.mark.parametrize("role", SUBORG_ROLES)
def test_cross_empresa_usuarios_de_otra_empresa_explicita_403(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=COMP_A1)
    with pytest.raises(HTTPException) as e:
        inv.list_assignable_users(db, c, company_id=COMP_A2)
    assert e.value.status_code == 403


# ======================================================================
# FAIL-CLOSED: rol sub-organización SIN empresa asignada (revisión, punto 1)
# No debe obtener alcance organizacional: 403 en listados, stats, fichas y
# asignaciones. No se usa None como autorización de acceso amplio.
# ======================================================================
NOCOMPANY_ROLES = ["tecnico", "supervisor", "manager", "company_admin"]


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_lista(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=None)   # rol acotado SIN empresa
    with pytest.raises(HTTPException) as e:
        inv.list_equipment(db, c)
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_stats(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=None)
    with pytest.raises(HTTPException) as e:
        inv.get_stats(db, c)
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_ve_ficha(db, role):
    _eq1, _eq2 = _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=None)
    with pytest.raises(HTTPException) as e:
        inv.get_equipment(db, c, _eq1.id)
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_ve_historial(db, role):
    _eq1, _eq2 = _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=None)
    with pytest.raises(HTTPException) as e:
        inv.list_assignments(db, c, _eq1.id)
    assert e.value.status_code == 403


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_lista_usuarios_ni_selectores(db, role):
    _seed_dos_empresas(db)
    c = ctx(role=role, org=ORG_A, company=None)
    for call in (
        lambda: inv.list_assignable_users(db, c),
        lambda: inv.list_company_options(db, c, ORG_A),
        lambda: inv.list_branch_options(db, c, ORG_A),
        lambda: inv.list_org_options(db, c),
    ):
        with pytest.raises(HTTPException) as e:
            call()
        assert e.value.status_code == 403


@pytest.mark.parametrize("role", NOCOMPANY_ROLES)
def test_failclosed_sin_empresa_no_crea(db, role):
    mk_org(db, org_id=ORG_A)
    mk_company(db, ORG_A, company_id=COMP_A1)
    c = ctx(role=role, org=ORG_A, company=None)
    with pytest.raises(HTTPException) as e:
        inv.create_equipment(db, c, EquipmentCreate(tipo="NB", organization_id=ORG_A, company_id=COMP_A1),
                             {"id": str(uuid.uuid4())})
    assert e.value.status_code == 403


def test_org_admin_sin_empresa_mantiene_alcance(db):
    # Contraprueba: org_admin SIN empresa conserva alcance multiempresa (no fail-closed).
    _seed_dos_empresas(db)
    c = ctx(role="org_admin", org=ORG_A, company=None)
    rows = inv.list_equipment(db, c)
    assert {r["codigo_interno"] for r in rows} == {"A1-EQ", "A2-EQ"}
    assert inv.get_stats(db, c)["total"] == 2


# ======================================================================
# COHERENCIA DE ESTADOS (revisión, punto 3)
# "asignado" <=> existe una asignación activa. El PATCH normal no puede alterar
# el estado de asignación sin registrar el movimiento.
# ======================================================================
from app.schemas.inventory import EquipmentUpdate, AssignmentCreate  # noqa: E402


def test_create_estado_asignado_sin_responsable_se_coacciona(db):
    # Cliente envía estado="asignado" pero sin responsable -> no debe quedar asignado.
    mk_org(db, org_id=ORG_A); comp = mk_company(db, ORG_A, company_id=COMP_A1)
    c = ctx(role="org_admin", org=ORG_A)
    eq = inv.create_equipment(
        db, c, EquipmentCreate(tipo="NB", organization_id=ORG_A, company_id=COMP_A1, estado="asignado"),
        {"id": str(uuid.uuid4())})
    assert eq.estado == "disponible"
    # y no hay asignación activa
    assert inv.list_assignments(db, c, eq.id) == []


def test_patch_no_marca_asignado_sin_asignacion(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    c = ctx(role="org_admin", org=ORG_A)
    from tests.conftest import mk_equipment
    eq = mk_equipment(db, ORG_A, company_id=COMP_A1, estado="disponible")
    with pytest.raises(HTTPException) as e:
        inv.update_equipment(db, c, eq.id, EquipmentUpdate(estado="asignado"), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 409


def test_patch_no_cambia_estado_con_asignacion_activa(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    resp = mk_profile(db, ORG_A, company_id=COMP_A1, full_name="Resp")
    c = ctx(role="org_admin", org=ORG_A)
    from tests.conftest import mk_equipment
    eq = mk_equipment(db, ORG_A, company_id=COMP_A1, estado="disponible")
    inv.assign_equipment(db, c, eq.id, AssignmentCreate(user_id=resp.id), {"id": str(uuid.uuid4())})
    # Intentar pasar a 'reparacion' sin registrar devolución -> 409
    with pytest.raises(HTTPException) as e:
        inv.update_equipment(db, c, eq.id, EquipmentUpdate(estado="reparacion"), {"id": str(uuid.uuid4())})
    assert e.value.status_code == 409
    db.refresh(eq)
    assert eq.estado == "asignado"   # no cambió


def test_patch_estado_valido_sin_asignacion_ok(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    c = ctx(role="org_admin", org=ORG_A)
    from tests.conftest import mk_equipment
    eq = mk_equipment(db, ORG_A, company_id=COMP_A1, estado="disponible")
    out = inv.update_equipment(db, c, eq.id, EquipmentUpdate(estado="reparacion"), {"id": str(uuid.uuid4())})
    assert out.estado == "reparacion"


def test_estado_vuelve_a_disponible_tras_devolucion(db):
    from app.schemas.inventory import AssignmentReturn
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    resp = mk_profile(db, ORG_A, company_id=COMP_A1, full_name="Resp")
    c = ctx(role="org_admin", org=ORG_A)
    from tests.conftest import mk_equipment
    eq = mk_equipment(db, ORG_A, company_id=COMP_A1, estado="disponible")
    inv.assign_equipment(db, c, eq.id, AssignmentCreate(user_id=resp.id), {"id": str(uuid.uuid4())})
    db.refresh(eq); assert eq.estado == "asignado"
    inv.return_equipment(db, c, eq.id, AssignmentReturn(returned_reason="fin"), {"id": str(uuid.uuid4())})
    db.refresh(eq); assert eq.estado == "disponible"
    # Tras devolver, ya se puede cambiar el estado por PATCH.
    inv.update_equipment(db, c, eq.id, EquipmentUpdate(estado="reparacion"), {"id": str(uuid.uuid4())})
    db.refresh(eq); assert eq.estado == "reparacion"


# ======================================================================
# VALIDACIÓN DEFENSIVA DE CATÁLOGOS (revisión, punto 4)
# Departamento/sucursal/ubicación deben coincidir EXACTO con el tenant del activo.
# Un company_id NULL NO se admite como "pertenece a cualquier empresa".
# ======================================================================
def test_departamento_con_empresa_null_no_se_admite_para_activo_con_empresa(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep_null = mk_department(db, ORG_A, None, name="Depto sin empresa")
    with pytest.raises(HTTPException) as e:
        inv._validate_department(db, ORG_A, COMP_A1, dep_null.id)
    assert e.value.status_code == 400


def test_sucursal_con_empresa_null_no_se_admite_para_activo_con_empresa(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    br_null = mk_branch(db, ORG_A, company_id=None, name="Suc sin empresa")
    with pytest.raises(HTTPException) as e:
        inv._validate_branch_location(db, ORG_A, COMP_A1, br_null.id, None)
    assert e.value.status_code == 400


def test_ubicacion_de_otra_empresa_no_se_admite(db):
    mk_org(db, org_id=ORG_A)
    mk_company(db, ORG_A, company_id=COMP_A1); mk_company(db, ORG_A, company_id=COMP_A2)
    br = mk_branch(db, ORG_A, company_id=COMP_A1, name="Suc A1")
    loc_otra = mk_location(db, ORG_A, br.id, company_id=COMP_A2, name="Loc A2")
    with pytest.raises(HTTPException) as e:
        inv._validate_branch_location(db, ORG_A, COMP_A1, br.id, loc_otra.id)
    assert e.value.status_code == 400


def test_catalogo_exacto_ok(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep = mk_department(db, ORG_A, COMP_A1, name="Admin")
    br = mk_branch(db, ORG_A, company_id=COMP_A1, name="Suc A1")
    loc = mk_location(db, ORG_A, br.id, company_id=COMP_A1, name="Oficina")
    # No deben levantar excepción (coincidencia exacta de org + empresa).
    inv._validate_department(db, ORG_A, COMP_A1, dep.id)
    inv._validate_branch_location(db, ORG_A, COMP_A1, br.id, loc.id)


def test_list_location_options_filtra_por_empresa_no_solo_branch(db):
    # Dos ubicaciones bajo el MISMO branch_id pero de empresas distintas (dato mal
    # cargado): el listado del contexto de COMP_A1 no debe exponer la de COMP_A2.
    mk_org(db, org_id=ORG_A)
    mk_company(db, ORG_A, company_id=COMP_A1); mk_company(db, ORG_A, company_id=COMP_A2)
    br = mk_branch(db, ORG_A, company_id=COMP_A1, name="Suc A1")
    mk_location(db, ORG_A, br.id, company_id=COMP_A1, name="Loc OK")
    mk_location(db, ORG_A, br.id, company_id=COMP_A2, name="Loc Intrusa")
    c = ctx(role="company_admin", org=ORG_A, company=COMP_A1)
    locs = inv.list_location_options(db, c, br.id)
    assert {x["name"] for x in locs} == {"Loc OK"}


# ======================================================================
# CIERRE DE REVISIÓN TÉCNICA (3 correcciones localizadas)
# ======================================================================

# -- Punto 2: list_location_options rechaza sucursal SIN empresa si el usuario
#    está acotado a una empresa concreta (NULL no es "de todas").
def test_location_options_rechaza_sucursal_sin_empresa_para_usuario_acotado(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    br_sin_emp = mk_branch(db, ORG_A, company_id=None, name="Suc sin empresa")
    mk_location(db, ORG_A, br_sin_emp.id, company_id=None, name="Loc")
    c = ctx(role="company_admin", org=ORG_A, company=COMP_A1)   # Empresa A concreta
    with pytest.raises(HTTPException) as e:
        inv.list_location_options(db, c, br_sin_emp.id)
    assert e.value.status_code == 403


def test_location_options_sucursal_de_su_empresa_ok(db):
    # Contraprueba: la sucursal de SU empresa sí lista sus ubicaciones.
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    br = mk_branch(db, ORG_A, company_id=COMP_A1, name="Suc A1")
    mk_location(db, ORG_A, br.id, company_id=COMP_A1, name="Oficina")
    c = ctx(role="company_admin", org=ORG_A, company=COMP_A1)
    locs = inv.list_location_options(db, c, br.id)
    assert {x["name"] for x in locs} == {"Oficina"}


# -- Punto 3: PATCH de estado serializado con asignación concurrente (FOR UPDATE).
#    Con dos sesiones PostgreSQL reales: una asignación en vuelo retiene el lock de
#    fila; el PATCH de estado espera y, al continuar, ve la asignación -> 409. Nunca
#    queda un activo en reparacion/baja con una asignación activa sin devolver.
def test_patch_estado_serializa_con_asignacion_concurrente(db, Session):
    import threading, time
    from app.models import Equipment, EquipmentAssignment

    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    resp = mk_profile(db, ORG_A, company_id=COMP_A1, full_name="Resp")
    from tests.conftest import mk_equipment
    eq = mk_equipment(db, ORG_A, company_id=COMP_A1, estado="disponible")
    db.commit()
    eq_id, resp_id = eq.id, resp.id

    # Sesión 1: toma el lock de fila (asignación "en vuelo"), aún sin commitear.
    s1 = Session()
    locked = s1.query(Equipment).filter(Equipment.id == eq_id).with_for_update().first()

    result = {}
    def patch_worker():
        s2 = Session()
        c = ctx(role="org_admin", org=ORG_A)
        try:
            inv.update_equipment(s2, c, eq_id, EquipmentUpdate(estado="reparacion"),
                                 {"id": str(uuid.uuid4())})
            result["status"] = 200
        except HTTPException as e:
            result["status"] = e.status_code
        except Exception as e:                      # pragma: no cover
            result["status"] = repr(e)
        finally:
            s2.close()

    t = threading.Thread(target=patch_worker)
    t.start()
    time.sleep(0.6)   # el worker queda BLOQUEADO esperando el lock de fila

    # S1 crea la asignación y marca 'asignado', luego commitea (libera el lock).
    s1.add(EquipmentAssignment(equipment_id=eq_id, user_id=resp_id, assigned_by=resp_id,
                               organization_id=ORG_A, company_id=COMP_A1))
    locked.estado = "asignado"
    s1.commit(); s1.close()

    t.join(timeout=15)

    # El PATCH se serializó DESPUÉS de la asignación -> 409 (no pisó el estado).
    assert result.get("status") == 409, f"resultado inesperado: {result}"

    # Estado final coherente: 'asignado' con exactamente 1 asignación activa.
    db.expire_all()
    fresh = db.query(Equipment).filter(Equipment.id == eq_id).first()
    assert fresh.estado == "asignado"
    activas = (db.query(EquipmentAssignment)
               .filter(EquipmentAssignment.equipment_id == eq_id,
                       EquipmentAssignment.returned_at.is_(None)).count())
    assert activas == 1
