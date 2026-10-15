"""Tests de los controles de seguridad de cierre pre-deploy (ITAM-01).

1) Permisos por ticket en comentarios/adjuntos (tenant + funcional + internos).
2) company_admin sin company_id -> 403.
3) get_meta filtra sucursales por empresa.
4) Asignación de tickets (assigned_to / default_assignee) no cruza organización/empresa.
"""
import uuid
import pytest
from fastapi import HTTPException

from app.models import Category, Ticket
from app.schemas.schemas import TicketCreate
from app.services import tickets as ticket_service
from app.services import inventory as inv
from app.core.tenancy import TenancyContext
from tests.conftest import mk_profile, mk_equipment, mk_branch, user_dict

ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()
COMP_1 = uuid.uuid4()
COMP_2 = uuid.uuid4()


def mk_category(db, name=None, default_assignee=None):
    c = Category(name=name or f"Cat-{uuid.uuid4().hex[:6]}", default_assignee=default_assignee)
    db.add(c); db.commit()
    return c


def ctx_for(role="tecnico", org=ORG_A, company=None):
    return TenancyContext(
        user={"id": str(uuid.uuid4()), "role": role,
              "organization_id": str(org) if org else None,
              "company_id": str(company) if company else None},
        active_org_id=str(org) if org else None,
        active_company_id=str(company) if company else None,
        is_impersonating=False,
    )


# ======================================================================
# Control 1: permisos por ticket en comentarios/adjuntos
# ======================================================================
def test_usuario_no_ve_comentarios_de_ticket_ajeno(api_client, db):
    cat = mk_category(db)
    u1 = user_dict(mk_profile(db, ORG_A, role="usuario"))
    u2 = user_dict(mk_profile(db, ORG_A, role="usuario"))   # misma org/empresa, otro ticket
    api_client.login(u1)
    t1 = api_client.post("/tickets/", json={"title": "Ticket de U1", "category_id": cat.id, "priority": "media"}).json()

    api_client.login(u2)
    assert api_client.get(f"/tickets/{t1['id']}/comments").status_code == 403
    assert api_client.get(f"/tickets/{t1['id']}/attachments").status_code == 403
    assert api_client.get(f"/tickets/{t1['id']}").status_code == 403


def test_usuario_normal_no_crea_comentario_interno(api_client, db):
    cat = mk_category(db)
    u1 = user_dict(mk_profile(db, ORG_A, role="usuario"))
    api_client.login(u1)
    t1 = api_client.post("/tickets/", json={"title": "Ticket U1", "category_id": cat.id, "priority": "media"}).json()
    # comentario normal propio: OK
    assert api_client.post(f"/tickets/{t1['id']}/comments", json={"comment": "hola", "is_internal": False}).status_code == 201
    # comentario interno por usuario normal: 403
    r = api_client.post(f"/tickets/{t1['id']}/comments", json={"comment": "secreto", "is_internal": True})
    assert r.status_code == 403


def test_staff_puede_comentar_interno_y_ver_ticket(api_client, db):
    cat = mk_category(db)
    u1 = user_dict(mk_profile(db, ORG_A, role="usuario"))
    # Staff org-level sin empresa sobre un ticket sin empresa: org_admin (multiempresa).
    tech = user_dict(mk_profile(db, ORG_A, role="org_admin"))
    api_client.login(u1)
    t1 = api_client.post("/tickets/", json={"title": "Ticket U1", "category_id": cat.id, "priority": "media"}).json()
    api_client.login(tech)
    assert api_client.get(f"/tickets/{t1['id']}/comments").status_code == 200
    assert api_client.post(f"/tickets/{t1['id']}/comments", json={"comment": "interno", "is_internal": True}).status_code == 201


# ======================================================================
# Control 2: company_admin sin empresa -> 403
# ======================================================================
def test_company_admin_sin_empresa_403(api_client, db):
    u = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=None))
    api_client.login(u)
    assert api_client.get("/inventory/").status_code == 403
    assert api_client.get("/tickets/my").status_code == 403


def test_company_admin_con_empresa_ok(api_client, db):
    u = user_dict(mk_profile(db, ORG_A, role="company_admin", company_id=COMP_1))
    api_client.login(u)
    assert api_client.get("/inventory/").status_code == 200


# ======================================================================
# Control 3: get_meta filtra sucursales por empresa
# ======================================================================
def test_get_meta_filtra_sucursales_por_empresa(db):
    mk_branch(db, ORG_A, company_id=COMP_1, name="Suc C1")
    mk_branch(db, ORG_A, company_id=COMP_2, name="Suc C2")
    meta = inv.get_meta(db, ctx_for(role="company_admin", org=ORG_A, company=COMP_1))
    names = {b["name"] for b in meta["branches"]}
    assert names == {"Suc C1"}


def test_get_meta_org_admin_ve_todas_las_sucursales(db):
    mk_branch(db, ORG_A, company_id=COMP_1, name="Suc C1")
    mk_branch(db, ORG_A, company_id=COMP_2, name="Suc C2")
    meta = inv.get_meta(db, ctx_for(role="org_admin", org=ORG_A))
    assert {b["name"] for b in meta["branches"]} == {"Suc C1", "Suc C2"}


# ======================================================================
# Control 4: asignación de tickets no cruza organización/empresa
# ======================================================================
def test_autoasignacion_cross_org_se_omite(db):
    assignee_b = mk_profile(db, ORG_B, role="tecnico")       # técnico de OTRA org
    cat = mk_category(db, default_assignee=assignee_b.id)
    requester = user_dict(mk_profile(db, ORG_A, role="usuario"))
    t = ticket_service.create_ticket(
        db, TicketCreate(title="auto cross", category_id=cat.id, priority="media"),
        requester, organization_id=ORG_A, company_id=None,
    )
    assert t.assigned_to is None   # NO se asignó al técnico de otra org


def test_autoasignacion_misma_org_ok(db):
    assignee_a = mk_profile(db, ORG_A, role="tecnico")
    cat = mk_category(db, default_assignee=assignee_a.id)
    requester = user_dict(mk_profile(db, ORG_A, role="usuario"))
    t = ticket_service.create_ticket(
        db, TicketCreate(title="auto ok", category_id=cat.id, priority="media"),
        requester, organization_id=ORG_A, company_id=None,
    )
    assert str(t.assigned_to) == str(assignee_a.id)


def test_update_assigned_to_cross_org_403(api_client, db):
    cat = mk_category(db)
    u1 = user_dict(mk_profile(db, ORG_A, role="usuario"))
    tech_a = user_dict(mk_profile(db, ORG_A, role="supervisor"))
    other_b = mk_profile(db, ORG_B, role="tecnico")
    api_client.login(u1)
    t1 = api_client.post("/tickets/", json={"title": "para asignar", "category_id": cat.id, "priority": "media"}).json()
    api_client.login(tech_a)
    r = api_client.patch(f"/tickets/{t1['id']}", json={"assigned_to": str(other_b.id)})
    assert r.status_code == 403


def test_update_assigned_to_misma_org_ok(api_client, db):
    cat = mk_category(db)
    u1 = user_dict(mk_profile(db, ORG_A, role="usuario"))
    # Asignador staff org-level sin empresa: org_admin (puede asignar, multiempresa).
    sup_a = user_dict(mk_profile(db, ORG_A, role="org_admin"))
    tech_a2 = mk_profile(db, ORG_A, role="tecnico")
    api_client.login(u1)
    t1 = api_client.post("/tickets/", json={"title": "asignar ok", "category_id": cat.id, "priority": "media"}).json()
    api_client.login(sup_a)
    r = api_client.patch(f"/tickets/{t1['id']}", json={"assigned_to": str(tech_a2.id)})
    assert r.status_code == 200
    assert str(r.json()["assigned_to"]) == str(tech_a2.id)

