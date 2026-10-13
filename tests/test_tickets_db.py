"""Pruebas REALES de Tickets sobre PostgreSQL (router + tenancy + SQL).

Verifican que la Fase 1 quedó integrada: create setea el tenant correcto (y NO
interpreta la IP como organization_id), los listados/stats/ficha aíslan por org,
y las operaciones cross-tenant responden 404.
"""
import uuid
from app.models import Ticket, Category
from tests.conftest import mk_profile, user_dict

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


def mk_category(db, name=None):
    c = Category(name=name or f"Cat-{uuid.uuid4().hex[:6]}")
    db.add(c); db.commit()
    return c


def test_create_fija_org_del_contexto_no_la_ip(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario", full_name="Ana"))
    api_client.login(ua)
    r = api_client.post("/tickets/", json={"title": "No anda el wifi", "category_id": cat.id, "priority": "media"})
    assert r.status_code == 201, r.text
    body = r.json()
    # El ticket quedó con la org del contexto (no un string de IP ni NULL).
    t = db.query(Ticket).filter(Ticket.id == uuid.UUID(body["id"])).first()
    assert str(t.organization_id) == str(ORG_A)
    assert t.requester_name == "Ana"


def test_my_tickets_aisla_por_org(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario"))
    ub = user_dict(mk_profile(db, ORG_B, role="usuario"))

    api_client.login(ua)
    api_client.post("/tickets/", json={"title": "Ticket A", "category_id": cat.id, "priority": "media"})

    r = api_client.get("/tickets/my")
    assert [x["title"] for x in r.json()] == ["Ticket A"]

    api_client.login(ub)
    r = api_client.get("/tickets/my")
    assert r.json() == []


def test_all_tickets_staff_aisla_por_org(api_client, db):
    cat = mk_category(db)
    ua_user = user_dict(mk_profile(db, ORG_A, role="usuario"))
    tech_b = user_dict(mk_profile(db, ORG_B, role="tecnico"))

    api_client.login(ua_user)
    api_client.post("/tickets/", json={"title": "Ticket de A", "category_id": cat.id, "priority": "alta"})

    # Un técnico de B NO ve los tickets de A en la cola.
    api_client.login(tech_b)
    r = api_client.get("/tickets/all")
    assert r.status_code == 200
    assert r.json() == []


def test_get_cross_tenant_404(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario"))
    tech_b = user_dict(mk_profile(db, ORG_B, role="tecnico"))

    api_client.login(ua)
    body = api_client.post("/tickets/", json={"title": "Secreto A", "category_id": cat.id, "priority": "media"}).json()

    api_client.login(tech_b)
    r = api_client.get(f"/tickets/{body['id']}")
    assert r.status_code == 404   # no revela existencia


def test_update_cross_tenant_404(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario"))
    tech_b = user_dict(mk_profile(db, ORG_B, role="tecnico"))

    api_client.login(ua)
    body = api_client.post("/tickets/", json={"title": "A edit", "category_id": cat.id, "priority": "media"}).json()

    api_client.login(tech_b)
    r = api_client.patch(f"/tickets/{body['id']}", json={"status": "en_curso"})
    assert r.status_code == 404


def test_comment_hereda_tenant_del_ticket(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario", full_name="Ana"))
    api_client.login(ua)
    body = api_client.post("/tickets/", json={"title": "con comentario", "category_id": cat.id, "priority": "media"}).json()
    r = api_client.post(f"/tickets/{body['id']}/comments", json={"comment": "hola", "is_internal": False})
    assert r.status_code == 201, r.text
    from app.models import TicketComment
    c = db.query(TicketComment).filter(TicketComment.ticket_id == uuid.UUID(body["id"])).first()
    assert str(c.organization_id) == str(ORG_A)


def test_stats_all_scoped(api_client, db):
    cat = mk_category(db)
    ua = user_dict(mk_profile(db, ORG_A, role="usuario"))
    tech_b = user_dict(mk_profile(db, ORG_B, role="tecnico"))
    api_client.login(ua)
    api_client.post("/tickets/", json={"title": "t1", "category_id": cat.id, "priority": "media"})
    api_client.post("/tickets/", json={"title": "t2", "category_id": cat.id, "priority": "media"})

    api_client.login(tech_b)
    r = api_client.get("/tickets/stats/all")
    assert r.status_code == 200
    assert r.json()["total"] == 0   # B no ve tickets de A


# ======================================================================
# Item 2: aislamiento por EMPRESA en Tickets
# ======================================================================
COMP_1 = uuid.uuid4()
COMP_2 = uuid.uuid4()


def test_company_admin_no_ve_ticket_de_otra_empresa_404(api_client, db):
    cat = mk_category(db)
    cadmin1 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_1))
    cadmin2 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_2))

    api_client.login(cadmin2)
    body = api_client.post("/tickets/", json={"title": "de empresa 2", "category_id": cat.id, "priority": "media"}).json()
    # quedó atado a COMP_2
    t = db.query(Ticket).filter(Ticket.id == uuid.UUID(body["id"])).first()
    assert str(t.company_id) == str(COMP_2)

    # company_admin de COMP_1 NO puede verlo (misma org, otra empresa)
    api_client.login(cadmin1)
    r = api_client.get(f"/tickets/{body['id']}")
    assert r.status_code == 404


def test_cola_tickets_filtra_por_empresa_para_company_admin(api_client, db):
    cat = mk_category(db)
    cadmin1 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_1))
    cadmin2 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_2))

    api_client.login(cadmin1)
    api_client.post("/tickets/", json={"title": "T de C1", "category_id": cat.id, "priority": "media"})
    api_client.login(cadmin2)
    api_client.post("/tickets/", json={"title": "T de C2", "category_id": cat.id, "priority": "media"})

    # cada company_admin ve SOLO su empresa
    api_client.login(cadmin1)
    titles1 = [t["title"] for t in api_client.get("/tickets/all").json()]
    assert titles1 == ["T de C1"]


def test_org_admin_ve_todas_las_empresas_de_su_org(api_client, db):
    cat = mk_category(db)
    cadmin1 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_1))
    cadmin2 = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_2))
    orgadmin = user_dict(mk_profile(db, ORG_A, role="org_admin", company_id=None))

    api_client.login(cadmin1)
    api_client.post("/tickets/", json={"title": "Tkt C1", "category_id": cat.id, "priority": "media"})
    api_client.login(cadmin2)
    api_client.post("/tickets/", json={"title": "Tkt C2", "category_id": cat.id, "priority": "media"})

    # org_admin (sin empresa acotada) ve ambas empresas de su org
    api_client.login(orgadmin)
    r = api_client.get("/tickets/all")
    assert {t["title"] for t in r.json()} == {"Tkt C1", "Tkt C2"}
    assert api_client.get("/tickets/stats/all").json()["total"] == 2

