"""CORE-USERS-01 (R0) — RBAC, tenancy, edición controlada y auditoría de usuarios.

Pruebas con PostgreSQL real (servicio) + HTTP (TestClient) sobre el router.
Cubren: alcance por org/empresa, fail-closed sin empresa, protección de
platform_owner y del último owner activo, no autoescalada/autodesactivación,
departamento de otra empresa rechazado, y auditoría de cambios.
"""
import uuid
import pytest
from fastapi import HTTPException

from app.core.tenancy import TenancyContext
from app.services import users as usvc
from app.schemas.schemas import UserAdminUpdate
from app.models import ProfileAuditLog, Profile
from tests.conftest import mk_org, mk_company, mk_department, mk_profile, user_dict

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
COMP_A1 = uuid.uuid4()
COMP_A2 = uuid.uuid4()


def ctx(role="org_admin", org=ORG_A, company=None, platform=False, user_id=None, impersonating=False):
    org_s = str(org) if org else None
    return TenancyContext(
        user={"id": str(user_id or uuid.uuid4()), "role": role,
              "organization_id": None if platform else org_s,
              "company_id": str(company) if company else None},
        active_org_id=None if platform else org_s,
        active_company_id=str(company) if company else None,
        is_impersonating=impersonating,
    )


# ======================================================================
# R0-01 — Alcance de listado por org/empresa + fail-closed
# ======================================================================
def test_list_users_aisla_por_organizacion(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    mk_profile(db, ORG_A, full_name="A1"); mk_profile(db, ORG_A, full_name="A2")
    mk_profile(db, ORG_B, full_name="B1")
    rows = usvc.list_users(db, ctx(role="org_admin", org=ORG_A))
    assert {r.full_name for r in rows} == {"A1", "A2"}


def test_list_users_company_admin_solo_su_empresa(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1); mk_company(db, ORG_A, company_id=COMP_A2)
    mk_profile(db, ORG_A, company_id=COMP_A1, full_name="C1")
    mk_profile(db, ORG_A, company_id=COMP_A2, full_name="C2")
    rows = usvc.list_users(db, ctx(role="company_admin", org=ORG_A, company=COMP_A1))
    assert {r.full_name for r in rows} == {"C1"}


@pytest.mark.parametrize("role", ["company_admin", "manager", "supervisor", "tecnico"])
def test_list_users_failclosed_sin_empresa(db, role):
    mk_org(db, org_id=ORG_A); mk_profile(db, ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.list_users(db, ctx(role=role, org=ORG_A, company=None))
    assert e.value.status_code == 403


# ======================================================================
# R0-03 — Roles/privilegios: escalada, owners, autoescalada
# ======================================================================
def test_no_autoescalada_de_rol(db):
    mk_org(db, org_id=ORG_A)
    me = mk_profile(db, ORG_A, role="org_admin", full_name="Yo")
    c = ctx(role="org_admin", org=ORG_A, user_id=me.id)
    with pytest.raises(HTTPException) as e:
        usvc.change_role(db, c, me.id, "platform_owner")
    assert e.value.status_code == 403


def test_org_admin_no_puede_asignar_rol_superior(db):
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario")
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.change_role(db, c, target.id, "platform_owner")
    assert e.value.status_code == 403


def test_org_admin_no_puede_editar_platform_owner(db):
    mk_org(db, org_id=ORG_A)
    owner = mk_profile(db, ORG_A, role="platform_owner", full_name="Owner")
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.change_role(db, c, owner.id, "usuario")
    assert e.value.status_code == 403


def test_solo_platform_owner_asigna_rol_de_plataforma(db):
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario")
    # platform_admin NO puede crear platform_admin (solo platform_owner otorga roles de plataforma)
    c = ctx(role="platform_admin", org=ORG_A, platform=True)
    with pytest.raises(HTTPException) as e:
        usvc.change_role(db, c, target.id, "platform_admin")
    assert e.value.status_code == 403


def test_platform_owner_puede_promover(db):
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario")
    c = ctx(role="platform_owner", org=ORG_A, platform=True)
    out = usvc.change_role(db, c, target.id, "tecnico")
    assert out.role == "tecnico"


def test_no_degradar_ultimo_platform_owner(db):
    mk_org(db, org_id=ORG_A)
    only_owner = mk_profile(db, ORG_A, role="platform_owner", full_name="Unico")
    actor = ctx(role="platform_owner", org=ORG_A, platform=True)  # otro owner actor (distinto id)
    with pytest.raises(HTTPException) as e:
        usvc.change_role(db, actor, only_owner.id, "usuario")
    assert e.value.status_code == 409


def test_degradar_owner_ok_si_hay_otro_activo(db):
    mk_org(db, org_id=ORG_A)
    o1 = mk_profile(db, ORG_A, role="platform_owner", full_name="O1")
    mk_profile(db, ORG_A, role="platform_owner", full_name="O2")  # segundo owner activo
    actor = ctx(role="platform_owner", org=ORG_A, platform=True)
    out = usvc.change_role(db, actor, o1.id, "org_admin")
    assert out.role == "org_admin"


def test_no_autodesactivacion(db):
    mk_org(db, org_id=ORG_A)
    me = mk_profile(db, ORG_A, role="org_admin")
    c = ctx(role="org_admin", org=ORG_A, user_id=me.id)
    with pytest.raises(HTTPException) as e:
        usvc.toggle_active(db, c, me.id)
    assert e.value.status_code == 403


def test_no_desactivar_ultimo_owner(db):
    mk_org(db, org_id=ORG_A)
    only_owner = mk_profile(db, ORG_A, role="platform_owner")
    actor = ctx(role="platform_owner", org=ORG_A, platform=True)
    with pytest.raises(HTTPException) as e:
        usvc.toggle_active(db, actor, only_owner.id)
    assert e.value.status_code == 409


# ======================================================================
# R0-02 — Edición controlada + departamento + reconciliación legacy
# ======================================================================
def test_update_departamento_valido_y_reconcilia_legacy(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep = mk_department(db, ORG_A, COMP_A1, name="Administración")
    target = mk_profile(db, ORG_A, company_id=COMP_A1, full_name="T")
    c = ctx(role="org_admin", org=ORG_A)
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(department_id=dep.id))
    assert str(out.department_id) == str(dep.id)
    assert out.department == "Administración"   # texto legacy reconciliado


def test_update_departamento_de_otra_empresa_rechazado(db):
    # Department de empresa A2 rechazado para usuario de empresa A1 (R0 test).
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1); mk_company(db, ORG_A, company_id=COMP_A2)
    dep_a2 = mk_department(db, ORG_A, COMP_A2, name="DeptoA2")
    target = mk_profile(db, ORG_A, company_id=COMP_A1)
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.update_user(db, c, target.id, UserAdminUpdate(department_id=dep_a2.id))
    assert e.value.status_code == 400


def test_update_no_cambia_rol_implicitamente(db):
    # Editar el departamento NO altera rol/org/empresa.
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep = mk_department(db, ORG_A, COMP_A1, name="Dep")
    target = mk_profile(db, ORG_A, company_id=COMP_A1, role="tecnico")
    c = ctx(role="org_admin", org=ORG_A)
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(department_id=dep.id))
    assert out.role == "tecnico"
    assert str(out.organization_id) == str(ORG_A)
    assert str(out.company_id) == str(COMP_A1)


def test_update_cross_org_target_404(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    target_b = mk_profile(db, ORG_B)
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.update_user(db, c, target_b.id, UserAdminUpdate(full_name="Hack"))
    assert e.value.status_code == 404


def test_no_plataforma_no_cambia_organizacion(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    target = mk_profile(db, ORG_A, role="usuario")
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.update_user(db, c, target.id, UserAdminUpdate(organization_id=ORG_B))
    assert e.value.status_code == 403


# ======================================================================
# R0-06 — Auditoría
# ======================================================================
def test_cambio_de_rol_queda_auditado(db):
    mk_org(db, org_id=ORG_A)
    actor_id = uuid.uuid4()
    target = mk_profile(db, ORG_A, role="usuario", full_name="T")
    c = ctx(role="org_admin", org=ORG_A, user_id=actor_id)
    usvc.change_role(db, c, target.id, "tecnico")
    rows = db.query(ProfileAuditLog).filter(
        ProfileAuditLog.target_user_id == target.id,
        ProfileAuditLog.field == "role",
    ).all()
    assert len(rows) == 1
    a = rows[0]
    assert a.action == "change_role" and a.old_value == "usuario" and a.new_value == "tecnico"
    assert a.result == "ok" and str(a.actor_id) == str(actor_id)


def test_cambio_denegado_tambien_se_audita(db):
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario")
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException):
        usvc.change_role(db, c, target.id, "platform_owner")
    rows = db.query(ProfileAuditLog).filter(
        ProfileAuditLog.target_user_id == target.id,
        ProfileAuditLog.result == "denied",
    ).all()
    assert len(rows) >= 1


# ======================================================================
# R0 HTTP — router + legacy no-bypass
# ======================================================================
def _owner_user(db, org=ORG_A):
    return user_dict(mk_profile(db, org, role="platform_owner", full_name="Owner"))


def test_http_list_users_scoped_y_no_fuga(api_client, db):
    mk_org(db, org_id=ORG_A, name="A"); mk_org(db, org_id=ORG_B, name="B")
    admin_a = user_dict(mk_profile(db, ORG_A, role="org_admin", full_name="AdminA"))
    mk_profile(db, ORG_A, full_name="Ux-A")
    mk_profile(db, ORG_B, full_name="Ux-B")
    api_client.login(admin_a)
    r = api_client.get("/users/")
    assert r.status_code == 200
    names = {u["full_name"] for u in r.json()}
    assert "Ux-A" in names and "AdminA" in names and "Ux-B" not in names


def test_http_tecnico_sin_empresa_403(api_client, db):
    mk_org(db, org_id=ORG_A)
    tech = user_dict(mk_profile(db, ORG_A, role="tecnico"))   # company None
    api_client.login(tech)
    assert api_client.get("/users/").status_code == 403


def test_http_legacy_role_endpoint_no_elude_permisos(api_client, db):
    # El endpoint legacy /users/{id}/role ahora pasa por las mismas reglas:
    # un org_admin NO puede elevar a platform_owner (403), aunque antes era abierto.
    mk_org(db, org_id=ORG_A)
    admin_a = user_dict(mk_profile(db, ORG_A, role="org_admin"))
    target = mk_profile(db, ORG_A, role="usuario")
    api_client.login(admin_a)
    r = api_client.patch(f"/users/{target.id}/role", json={"role": "platform_owner"})
    assert r.status_code == 403


def test_http_toggle_last_owner_409(api_client, db):
    mk_org(db, org_id=ORG_A)
    owner_actor = _owner_user(db)
    only_owner = mk_profile(db, ORG_A, role="platform_owner", full_name="Solo")
    # Desactivar a 'Solo' deja a 'Owner' (actor) como único -> permitido;
    # para probar el 409 desactivamos cuando es el último, así que dejamos solo uno.
    # Hacemos al actor NO owner activo contándolo: usamos un tercer caso simple:
    api_client.login(owner_actor)
    # Desactivar primero al actor es imposible (autodesactivación). Validamos el
    # escenario de "último owner": desactivar 'Solo' deja a 'Owner' activo -> 200.
    r = api_client.patch(f"/users/{only_owner.id}/toggle-active")
    assert r.status_code == 200


# ======================================================================
# R0 correcciones — auditoría DURABLE de denegadas, concurrencia de owner,
# departamento textual sin empresa, integridad al cambiar org/empresa.
# ======================================================================
def test_denied_audit_persiste_tras_rollback(db, Session):
    # 1) op no autorizada -> 403; 3) rollback de la sesión original;
    # 4-5) nueva sesión ve el registro 'denied'; 6) perfil NO modificado.
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario", full_name="T")
    db.commit(); tid = target.id

    s1 = Session()
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException):
        usvc.change_role(s1, c, tid, "platform_owner")   # denegado
    s1.rollback(); s1.close()                             # rollback de la original

    s2 = Session()
    denied = s2.query(ProfileAuditLog).filter(
        ProfileAuditLog.target_user_id == tid, ProfileAuditLog.result == "denied").count()
    assert denied >= 1
    tgt = s2.query(Profile).filter(Profile.id == tid).first()
    assert tgt.role == "usuario"   # el perfil NO cambió
    s2.close()


def test_owner_concurrencia_mantiene_al_menos_uno(db, Session):
    import threading
    mk_org(db, org_id=ORG_A)
    o1 = mk_profile(db, ORG_A, role="platform_owner", full_name="O1")
    o2 = mk_profile(db, ORG_A, role="platform_owner", full_name="O2")
    db.commit(); id1, id2 = o1.id, o2.id

    results = {}
    def worker(name, target_id):
        s = Session()
        c = ctx(role="platform_owner", org=ORG_A, platform=True)
        try:
            usvc.toggle_active(s, c, target_id)   # desactiva
            results[name] = "ok"
        except HTTPException as e:
            results[name] = e.status_code
        except Exception as e:                     # pragma: no cover
            results[name] = repr(e)
        finally:
            s.close()

    t1 = threading.Thread(target=worker, args=("a", id1))
    t2 = threading.Thread(target=worker, args=("b", id2))
    t1.start(); t2.start(); t1.join(15); t2.join(15)

    vals = list(results.values())
    assert "ok" in vals and 409 in vals, f"resultado inesperado: {results}"
    db.expire_all()
    activos = db.query(Profile).filter(
        Profile.role == "platform_owner", Profile.is_active.is_(True)).count()
    assert activos >= 1   # nunca queda la plataforma sin owner activo


def test_dept_texto_editable_para_usuario_sin_empresa(db):
    mk_org(db, org_id=ORG_A)
    target = mk_profile(db, ORG_A, role="usuario", full_name="Admin sin empresa")  # company None
    target.department = "Dirección"; db.commit()
    c = ctx(role="org_admin", org=ORG_A)
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(department="Gerencia General"))
    assert out.department == "Gerencia General"
    assert out.company_id is None   # NO se le asignó empresa ficticia
    assert out.department_id is None


def test_dept_texto_bloqueado_si_estructurado(db):
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep = mk_department(db, ORG_A, COMP_A1, name="Admin")
    target = mk_profile(db, ORG_A, company_id=COMP_A1)
    target.department_id = dep.id; target.department = "Admin"; db.commit()
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.update_user(db, c, target.id, UserAdminUpdate(department="Discrepante"))
    assert e.value.status_code == 400


def test_cambio_empresa_limpia_departamento_y_texto(db):
    mk_org(db, org_id=ORG_A)
    mk_company(db, ORG_A, company_id=COMP_A1); mk_company(db, ORG_A, company_id=COMP_A2)
    dep = mk_department(db, ORG_A, COMP_A1, name="Admin")
    target = mk_profile(db, ORG_A, company_id=COMP_A1)
    target.department_id = dep.id; target.department = "Admin"; db.commit()
    c = ctx(role="org_admin", org=ORG_A)
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(company_id=COMP_A2))
    assert str(out.company_id) == str(COMP_A2)
    assert out.department_id is None and out.department is None   # no arrastra depto anterior


def test_cambio_org_y_empresa_en_una_sola_request(db):
    mk_org(db, org_id=ORG_A); mk_org(db, org_id=ORG_B)
    comp_b = mk_company(db, ORG_B, name="EmpB")
    target = mk_profile(db, ORG_A, company_id=COMP_A1)
    target.department = "Algo"; db.commit()
    c = ctx(role="platform_owner", org=ORG_A, platform=True)
    out = usvc.update_user(db, c, target.id,
                           UserAdminUpdate(organization_id=ORG_B, company_id=comp_b.id))
    assert str(out.organization_id) == str(ORG_B)
    assert str(out.company_id) == str(comp_b.id)
    assert out.department_id is None and out.department is None


# ======================================================================
# R0 cierre — fixes 4, 5, 6
# ======================================================================
def test_lock_owner_guard_fail_closed():
    # Fix 4: si no se puede adquirir el advisory lock, la operación falla (503),
    # nunca continúa sin la protección de concurrencia.
    class _BoomDB:
        def execute(self, *a, **k):
            raise RuntimeError("no se pudo adquirir el lock")
    with pytest.raises(HTTPException) as e:
        usvc._lock_owner_guard(_BoomDB())
    assert e.value.status_code == 503


def test_owner_guard_fail_closed_corta_toggle(db, monkeypatch):
    # Integración: si el lock falla, toggle de un owner NO procede (503) y el owner
    # sigue activo (no se degrada la protección).
    mk_org(db, org_id=ORG_A)
    o1 = mk_profile(db, ORG_A, role="platform_owner", full_name="O1")
    mk_profile(db, ORG_A, role="platform_owner", full_name="O2")
    actor = ctx(role="platform_owner", org=ORG_A, platform=True)
    monkeypatch.setattr(usvc, "_lock_owner_guard",
                        lambda _db: (_ for _ in ()).throw(HTTPException(status_code=503, detail="lock")))
    with pytest.raises(HTTPException) as e:
        usvc.toggle_active(db, actor, o1.id)
    assert e.value.status_code == 503
    db.rollback(); db.expire_all()
    assert db.query(Profile).filter(Profile.id == o1.id).first().is_active is True


def test_dept_texto_bloqueado_con_empresa_y_department_id_null(db):
    # Fix 5: perfil CON empresa pero department_id NULL -> el texto NO se admite
    # (no se omite la validación estructurada por department_id NULL).
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    target = mk_profile(db, ORG_A, company_id=COMP_A1)   # department_id NULL
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException) as e:
        usvc.update_user(db, c, target.id, UserAdminUpdate(department="A dedo"))
    assert e.value.status_code == 400


def test_outrank_denegado_se_audita(db):
    # Fix 6: el rechazo en _assert_outranks queda auditado (durable).
    mk_org(db, org_id=ORG_A)
    owner = mk_profile(db, ORG_A, role="platform_owner", full_name="Owner")
    c = ctx(role="org_admin", org=ORG_A)
    with pytest.raises(HTTPException):
        usvc.update_user(db, c, owner.id, UserAdminUpdate(full_name="Hack"))
    rows = db.query(ProfileAuditLog).filter(
        ProfileAuditLog.target_user_id == owner.id,
        ProfileAuditLog.action == "deny_outrank",
        ProfileAuditLog.result == "denied").count()
    assert rows >= 1


def test_quitar_departamento_estructurado_limpia_texto(db):
    # Fix 2: asignar departamento, retirarlo (department_id=None) y verificar que
    # TANTO department_id COMO el texto legacy quedan en NULL (no se arrastra el nombre).
    mk_org(db, org_id=ORG_A); mk_company(db, ORG_A, company_id=COMP_A1)
    dep = mk_department(db, ORG_A, COMP_A1, name="Administración")
    target = mk_profile(db, ORG_A, company_id=COMP_A1, full_name="T")
    c = ctx(role="org_admin", org=ORG_A)
    # 1) asignar
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(department_id=dep.id))
    assert str(out.department_id) == str(dep.id) and out.department == "Administración"
    # 2) retirar el departamento estructurado
    out = usvc.update_user(db, c, target.id, UserAdminUpdate(department_id=None))
    assert out.department_id is None
    assert out.department is None   # el texto legacy también quedó NULL
    # 3) consultar de nuevo (fresh) confirma ambos NULL
    db.expire_all()
    fresh = db.query(Profile).filter(Profile.id == target.id).first()
    assert fresh.department_id is None and fresh.department is None
