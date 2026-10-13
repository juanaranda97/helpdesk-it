"""Tests del módulo de Inventario (ITAM): aislamiento multi-tenant, CRUD y asignaciones.

No requieren Postgres: usan sesiones falsas (FakeSession) y objetos ORM en memoria,
más tests puros de TenancyContext. Cubren los casos de aislamiento exigidos.
"""
import uuid
import pytest
from fastapi import HTTPException

from app.core.tenancy import TenancyContext
from app.models import Equipment, EquipmentAssignment, Profile
from app.schemas.inventory import EquipmentCreate, AssignmentCreate, AssignmentReturn
from app.services import inventory as inv


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
ORG_A = str(uuid.uuid4())
ORG_B = str(uuid.uuid4())
USER = str(uuid.uuid4())
TARGET = str(uuid.uuid4())
EQ_ID = uuid.uuid4()


def ctx_for(role="tecnico", org=ORG_A, active=None, impersonating=False, company=None):
    active = active if active is not None else org
    return TenancyContext(
        user={"id": USER, "role": role, "organization_id": org, "company_id": company},
        active_org_id=active,
        active_company_id=company,
        is_impersonating=impersonating,
    )


class FakeQuery:
    def __init__(self, result):
        self._result = result
    def filter(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def offset(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def distinct(self, *a, **k): return self
    def with_for_update(self, *a, **k): return self
    def first(self): return self._result
    def all(self): return self._result if isinstance(self._result, list) else []
    def count(self): return len(self._result) if isinstance(self._result, list) else 0


class FakeSession:
    """Devuelve un resultado por clase de modelo consultada."""
    def __init__(self, results):
        self.results = results
        self.added = []
        self.committed = False
    def query(self, *entities):
        model = entities[0]
        return FakeQuery(self.results.get(model))
    def add(self, obj): self.added.append(obj)
    def commit(self): self.committed = True
    def refresh(self, obj): pass
    def rollback(self): pass


# ======================================================================
# 1) TenancyContext — decisiones puras de aislamiento
# ======================================================================
def test_scope_usuario_normal_es_su_org():
    c = ctx_for(role="tecnico", org=ORG_A)
    assert c.scope_org_id == ORG_A

def test_can_access_propia_org():
    c = ctx_for(org=ORG_A)
    assert c.can_access_org(ORG_A) is True

def test_no_puede_acceder_otra_org():
    c = ctx_for(org=ORG_A)
    assert c.can_access_org(ORG_B) is False

def test_null_tenant_no_es_accesible_bajo_scope():
    # Punto crítico: un activo con organization_id NULL no filtra a un cliente.
    c = ctx_for(org=ORG_A)
    assert c.can_access_org(None) is False

def test_platform_overview_sin_impersonar_ve_todo():
    c = ctx_for(role="platform_owner", org=None, active=None, impersonating=False)
    assert c.scope_org_id is None
    assert c.can_access_org(ORG_A) is True
    assert c.can_access_org(ORG_B) is True

def test_platform_impersonando_queda_acotado():
    c = ctx_for(role="platform_owner", org="OMM", active=ORG_B, impersonating=True)
    assert c.scope_org_id == ORG_B
    assert c.can_access_org(ORG_B) is True
    assert c.can_access_org(ORG_A) is False

def test_tenant_for_create_sin_org_falla():
    c = ctx_for(role="platform_owner", org=None, active=None, impersonating=False)
    with pytest.raises(HTTPException) as e:
        c.tenant_for_create()
    assert e.value.status_code == 400


# ======================================================================
# 2) create_equipment — el tenant se fija en backend, no desde el cliente
# ======================================================================
def test_create_fija_tenant_desde_contexto():
    c = ctx_for(org=ORG_A, company=None)
    data = EquipmentCreate(tipo="NB", codigo_interno="EQ-1", marca="Dell")
    eq = inv.create_equipment(FakeSession({}), c, data, {"id": USER})
    assert str(eq.organization_id) == ORG_A          # viene del contexto
    assert str(eq.created_by) == USER
    assert eq.estado == "disponible"                  # default
    assert eq.tipo == "NB"

def test_create_ignora_org_enviada_por_cliente():
    # EquipmentCreate no tiene campo organization_id → aunque el cliente lo mande,
    # no llega al modelo; el backend fija el del contexto.
    assert "organization_id" not in EquipmentCreate.model_fields
    assert "company_id" not in EquipmentCreate.model_fields


# ======================================================================
# 3) Asignaciones — tenant, usuario mismo-org, no doble asignación
# ======================================================================
def _equipment(org=ORG_A, estado="disponible"):
    return Equipment(id=EQ_ID, tipo="NB", organization_id=uuid.UUID(org),
                     company_id=None, estado=estado)

def test_assign_ok_mismo_tenant():
    c = ctx_for(org=ORG_A)
    eq = _equipment(ORG_A)
    target = Profile(id=uuid.UUID(TARGET), full_name="Juan", organization_id=uuid.UUID(ORG_A))
    sess = FakeSession({Equipment: eq, Profile: target, EquipmentAssignment: None})
    a = inv.assign_equipment(sess, c, EQ_ID, AssignmentCreate(user_id=uuid.UUID(TARGET)), {"id": USER})
    assert str(a.user_id) == TARGET
    assert a.organization_id == eq.organization_id
    assert eq.estado == "asignado"

def test_assign_rechaza_usuario_de_otra_org():
    c = ctx_for(org=ORG_A)
    eq = _equipment(ORG_A)
    target_b = Profile(id=uuid.UUID(TARGET), full_name="Otro", organization_id=uuid.UUID(ORG_B))
    sess = FakeSession({Equipment: eq, Profile: target_b, EquipmentAssignment: None})
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(sess, c, EQ_ID, AssignmentCreate(user_id=uuid.UUID(TARGET)), {"id": USER})
    assert e.value.status_code == 403

def test_assign_rechaza_doble_asignacion():
    c = ctx_for(org=ORG_A)
    eq = _equipment(ORG_A)
    target = Profile(id=uuid.UUID(TARGET), full_name="Juan", organization_id=uuid.UUID(ORG_A))
    active = EquipmentAssignment(id=uuid.uuid4(), equipment_id=EQ_ID, user_id=uuid.UUID(TARGET), returned_at=None)
    sess = FakeSession({Equipment: eq, Profile: target, EquipmentAssignment: active})
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(sess, c, EQ_ID, AssignmentCreate(user_id=uuid.UUID(TARGET)), {"id": USER})
    assert e.value.status_code == 409

def test_assign_cross_tenant_da_404():
    # Usuario de A intenta asignar un activo de B → no debe revelar existencia.
    c = ctx_for(org=ORG_A)
    eq_b = _equipment(ORG_B)
    sess = FakeSession({Equipment: eq_b, Profile: None, EquipmentAssignment: None})
    with pytest.raises(HTTPException) as e:
        inv.assign_equipment(sess, c, EQ_ID, AssignmentCreate(user_id=uuid.UUID(TARGET)), {"id": USER})
    assert e.value.status_code == 404


# ======================================================================
# 4) Devoluciones
# ======================================================================
def test_return_cierra_asignacion_y_libera():
    c = ctx_for(org=ORG_A)
    eq = _equipment(ORG_A, estado="asignado")
    active = EquipmentAssignment(id=uuid.uuid4(), equipment_id=EQ_ID, user_id=uuid.UUID(TARGET), returned_at=None)
    sess = FakeSession({Equipment: eq, EquipmentAssignment: active})
    a = inv.return_equipment(sess, c, EQ_ID, AssignmentReturn(returned_reason="fin de uso"), {"id": USER})
    assert a.returned_at is not None
    assert eq.estado == "disponible"

def test_return_sin_asignacion_activa_da_409():
    c = ctx_for(org=ORG_A)
    eq = _equipment(ORG_A, estado="disponible")
    sess = FakeSession({Equipment: eq, EquipmentAssignment: None})
    with pytest.raises(HTTPException) as e:
        inv.return_equipment(sess, c, EQ_ID, AssignmentReturn(), {"id": USER})
    assert e.value.status_code == 409


# ======================================================================
# 5) Acceso directo a ficha cross-tenant y NULL-tenant
# ======================================================================
def test_get_equipment_cross_tenant_da_404():
    c = ctx_for(org=ORG_A)
    eq_b = _equipment(ORG_B)
    sess = FakeSession({Equipment: eq_b})
    with pytest.raises(HTTPException) as e:
        inv.get_equipment(sess, c, EQ_ID)
    assert e.value.status_code == 404

def test_get_equipment_null_tenant_da_404_para_usuario_scoped():
    c = ctx_for(org=ORG_A)
    eq_null = Equipment(id=EQ_ID, tipo="PC", organization_id=None, estado="disponible")
    sess = FakeSession({Equipment: eq_null})
    with pytest.raises(HTTPException) as e:
        inv.get_equipment(sess, c, EQ_ID)
    assert e.value.status_code == 404

