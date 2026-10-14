"""Lógica de negocio del módulo de Inventario (ITAM).

Reglas de seguridad (idénticas al patrón seguro de Tickets):
- El tenant (organization_id/company_id) de toda operación se resuelve y valida
  en backend desde el contexto autenticado (TenancyContext). NUNCA se confía en
  organization_id/company_id enviados por el navegador.
- Los listados/stats se filtran por la org en scope; un recurso de otra org
  responde 404 (no revela existencia). Un recurso con organization_id NULL no
  es accesible bajo scope (no genera bypass).
"""
from datetime import datetime, timezone
from typing import Optional, List, Dict
from uuid import UUID
from sqlalchemy import or_, desc
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models import (
    Equipment, EquipmentAssignment, Profile, Branch, PhysicalLocation,
    Organization, Company, Department,
)


def _as_uuid(value) -> Optional[UUID]:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _opt_uuid_eq(a, b) -> bool:
    """Igualdad de UUIDs OPCIONALES con semántica estricta de tenant.

    None == None -> True (ambos sin empresa). None vs un valor concreto -> False:
    un `company_id` NULL NO coincide con una empresa concreta (no se admite NULL
    como "pertenece a cualquier empresa"). Evita que un catálogo sin empresa se
    cuele silenciosamente en el tenant de un activo con empresa, y viceversa.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return str(_as_uuid(a)) == str(_as_uuid(b))


def assert_equipment_access(eq: Equipment, ctx) -> None:
    """404 si el contexto NO puede acceder al activo por tenant (organización Y empresa).
    Control de acceso CENTRALIZADO: lo usan get/update/assign/return/list_assignments."""
    if ctx is None:
        return
    # FAIL-CLOSED: rol que exige empresa sin empresa asignada -> 403 (no acceso amplio).
    ctx.assert_company_scope()
    if not ctx.can_access_org(getattr(eq, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    if not ctx.can_access_company(getattr(eq, "company_id", None)):
        raise HTTPException(status_code=404, detail="Activo no encontrado")


def _validate_branch_location(db: Session, org_id, company_id, branch_id, physical_location_id) -> None:
    """Valida que la sucursal / ubicación física usadas pertenezcan al MISMO tenant
    (organización, y empresa si está definida) del activo. 400 si no corresponden."""
    org_id = _as_uuid(org_id)
    company_id = _as_uuid(company_id)
    b = None
    if branch_id:
        b = db.query(Branch).filter(Branch.id == _as_uuid(branch_id)).first()
        if not b or str(b.organization_id) != str(org_id):
            raise HTTPException(status_code=400, detail="La sucursal no pertenece a la organización del activo.")
        # Empresa: coincidencia EXACTA (NULL no coincide con una empresa concreta).
        if not _opt_uuid_eq(b.company_id, company_id):
            raise HTTPException(status_code=400, detail="La sucursal no pertenece a la empresa del activo.")
    if physical_location_id:
        pl = db.query(PhysicalLocation).filter(PhysicalLocation.id == _as_uuid(physical_location_id)).first()
        if not pl or str(pl.organization_id) != str(org_id):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la organización del activo.")
        if not _opt_uuid_eq(pl.company_id, company_id):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la empresa del activo.")
        if branch_id and pl.branch_id is not None and str(pl.branch_id) != str(_as_uuid(branch_id)):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la sucursal indicada.")


def _validate_department(db: Session, org_id, company_id, department_id) -> None:
    """El departamento debe pertenecer al MISMO tenant (org, y empresa si está definida)."""
    if not department_id:
        return
    d = db.query(Department).filter(Department.id == _as_uuid(department_id)).first()
    if not d or str(d.organization_id) != str(_as_uuid(org_id)):
        raise HTTPException(status_code=400, detail="El departamento no pertenece a la organización del activo.")
    # Empresa: coincidencia EXACTA. Un departamento con company_id NULL NO se admite
    # para un activo con empresa concreta (ni al revés): nada de "pertenece a cualquiera".
    if not _opt_uuid_eq(d.company_id, company_id):
        raise HTTPException(status_code=400, detail="El departamento no pertenece a la empresa del activo.")


# ---------------------------------------------------------------------------
# Resolución SEGURA del tenant de creación (contrato plataforma / cliente)
# ---------------------------------------------------------------------------
def _assert_org_usable(db: Session, org_id) -> None:
    o = db.query(Organization).filter(Organization.id == _as_uuid(org_id)).first()
    if not o:
        raise HTTPException(status_code=404, detail="Organización no encontrada")
    if o.is_active is False:
        raise HTTPException(status_code=403, detail="La organización está suspendida")


def _assert_company_in_org(db: Session, company_id, org_id) -> None:
    c = db.query(Company).filter(Company.id == _as_uuid(company_id)).first()
    if not c or str(c.organization_id) != str(_as_uuid(org_id)):
        raise HTTPException(status_code=400, detail="La empresa no pertenece a la organización indicada.")
    if c.is_active is False:
        raise HTTPException(status_code=403, detail="La empresa está inactiva")


def resolve_create_tenant(db: Session, ctx, req_org, req_company):
    """Devuelve (organization_id, company_id) validados para crear un activo.

    - Plataforma SIN impersonar: elige org + empresa EXPLÍCITAMENTE (ambas obligatorias).
    - Plataforma IMPERSONANDO una org: org fija a la org impersonada (sin discrepancias
      con el formulario); elige empresa dentro de ella.
    - org_admin/org_owner: org fija (la suya); elige CUALQUIER empresa de su org
      (conservan alcance multiempresa aunque tengan una empresa de referencia en su perfil).
    - Roles acotados a empresa (company_admin, tecnico, ...): org y empresa FIJAS.
    Nunca se confía en valores arbitrarios del cliente: todo se valida contra la DB.
    """
    # FAIL-CLOSED: un rol sub-organización SIN empresa asignada no puede crear activos
    # (de lo contrario podría elegir cualquier empresa de su org vía el formulario).
    ctx.assert_company_scope()
    if ctx.is_platform_user:
        if ctx.is_impersonating and ctx.active_org_id:
            # Está "dentro" de una org concreta: no se permite registrar en otra.
            org = ctx.active_org_id
            if req_org and str(req_org) != str(org):
                raise HTTPException(status_code=403, detail="Estás operando dentro de otra organización (impersonation).")
        else:
            org = req_org or ctx.active_org_id
            if not org:
                raise HTTPException(status_code=400, detail="Seleccioná una organización para registrar el activo.")
        _assert_org_usable(db, org)
        # Si la plataforma eligió empresa en el contexto, se respeta; si no, la del form.
        company = ctx.scope_company_id or req_company
        if not company:
            raise HTTPException(status_code=400, detail="Seleccioná una empresa para registrar el activo.")
        if ctx.scope_company_id and req_company and str(req_company) != str(ctx.scope_company_id):
            raise HTTPException(status_code=403, detail="No podés registrar activos en otra empresa.")
        _assert_company_in_org(db, company, org)
        return str(org), str(company)

    # No-plataforma: la organización es la suya (no se puede cambiar).
    org = ctx.active_org_id
    if not org:
        raise HTTPException(status_code=403, detail="Tu usuario no tiene organización asignada.")
    if req_org and str(req_org) != str(org):
        raise HTTPException(status_code=403, detail="No podés registrar activos en otra organización.")

    # Empresa FIJA solo para roles acotados a empresa (scope_company_id). org_admin
    # NO se fuerza por la empresa de su perfil: conserva alcance multiempresa.
    forced_company = ctx.scope_company_id
    if forced_company:
        if req_company and str(req_company) != str(forced_company):
            raise HTTPException(status_code=403, detail="No podés registrar activos en otra empresa.")
        company = forced_company
    else:
        if not req_company:
            raise HTTPException(status_code=400, detail="Seleccioná una empresa para registrar el activo.")
        company = req_company
    _assert_company_in_org(db, company, org)
    return str(org), str(company)


def _validate_responsible(db: Session, user_id, org_id, company_id) -> Profile:
    """Valida que el responsable sea asignable: existe, ACTIVO, estado laboral
    admisible, de la MISMA organización y de la MISMA empresa (exacta; NULL no
    coincide). Devuelve el Profile o levanta HTTPException. Único punto de verdad
    para alta inicial y asignaciones posteriores."""
    p = db.query(Profile).filter(Profile.id == _as_uuid(user_id)).first()
    if not p:
        raise HTTPException(status_code=404, detail="Usuario responsable no encontrado")
    if p.is_active is False:
        raise HTTPException(status_code=403, detail="El usuario está inactivo.")
    if getattr(p, "estado_laboral", None) in ("baja", "suspendido"):
        raise HTTPException(status_code=403, detail="El usuario está dado de baja o suspendido.")
    if str(getattr(p, "organization_id", None)) != str(_as_uuid(org_id)):
        raise HTTPException(status_code=403, detail="El responsable no pertenece a la organización del activo.")
    # Coincidencia EXACTA de empresa: NULL no coincide.
    if str(getattr(p, "company_id", None)) != str(_as_uuid(company_id)):
        raise HTTPException(status_code=403, detail="El responsable no pertenece a la empresa del activo.")
    return p


# ---------------------------------------------------------------------------
# Catálogos para selectores dependientes (todo validado/scopeado en backend)
# ---------------------------------------------------------------------------
def _can_use_org(ctx, org_id) -> bool:
    if ctx.is_platform_user and not ctx.is_impersonating:
        return True
    return str(org_id) == str(ctx.active_org_id)


def list_org_options(db: Session, ctx) -> List[dict]:
    """Organizaciones donde el usuario puede registrar activos."""
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    q = db.query(Organization.id, Organization.name).filter(Organization.is_active.is_(True))
    if not (ctx.is_platform_user and not ctx.is_impersonating):
        q = q.filter(Organization.id == _as_uuid(ctx.active_org_id))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Organization.name).all()]


def list_company_options(db: Session, ctx, organization_id) -> List[dict]:
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    if not organization_id or not _can_use_org(ctx, organization_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa organización")
    q = db.query(Company.id, Company.name).filter(
        Company.organization_id == _as_uuid(organization_id),
        Company.is_active.is_(True),
    )
    # Si el contexto está acotado a una empresa, solo esa.
    if ctx.scope_company_id:
        q = q.filter(Company.id == _as_uuid(ctx.scope_company_id))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Company.name).all()]


def _assert_company_access(db: Session, ctx, company_id) -> None:
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    c = db.query(Company.organization_id).filter(Company.id == _as_uuid(company_id)).first()
    if not c:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if not _can_use_org(ctx, c[0]):
        raise HTTPException(status_code=403, detail="No autorizado para esa empresa")
    if ctx.scope_company_id and str(ctx.scope_company_id) != str(company_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa empresa")


def list_department_options(db: Session, ctx, company_id) -> List[dict]:
    _assert_company_access(db, ctx, company_id)
    q = db.query(Department.id, Department.name).filter(
        Department.company_id == _as_uuid(company_id),
        Department.is_active.is_(True),
    )
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Department.name).all()]


def list_branch_options(db: Session, ctx, organization_id, company_id=None) -> List[dict]:
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    if not organization_id or not _can_use_org(ctx, organization_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa organización")
    q = db.query(Branch.id, Branch.name).filter(
        Branch.organization_id == _as_uuid(organization_id),
        Branch.is_active.is_(True),
    )
    eff_company = company_id or ctx.scope_company_id
    if eff_company:
        _assert_company_access(db, ctx, eff_company)
        q = q.filter(Branch.company_id == _as_uuid(eff_company))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Branch.name).all()]


def list_location_options(db: Session, ctx, branch_id) -> List[dict]:
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    b = db.query(Branch).filter(Branch.id == _as_uuid(branch_id)).first()
    if not b or not _can_use_org(ctx, b.organization_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa sucursal")
    # Si el usuario está acotado a una empresa concreta, la sucursal debe ser EXACTAMENTE
    # de esa empresa: una sucursal con company_id NULL tampoco se admite (no es "de todas").
    if ctx.scope_company_id and not _opt_uuid_eq(b.company_id, ctx.scope_company_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa sucursal")
    # Filtrar ubicaciones por ORG y EMPRESA autorizadas, no solo por branch_id:
    # una ubicación mal cargada (otra org/empresa bajo el mismo branch_id) no debe
    # filtrarse al tenant equivocado.
    q = db.query(PhysicalLocation.id, PhysicalLocation.name).filter(
        PhysicalLocation.branch_id == _as_uuid(branch_id),
        PhysicalLocation.organization_id == _as_uuid(b.organization_id),
        PhysicalLocation.is_active.is_(True),
    )
    # Empresa efectiva: el scope del usuario, o la empresa de la sucursal.
    eff_company = ctx.scope_company_id or b.company_id
    if eff_company is not None:
        q = q.filter(PhysicalLocation.company_id == _as_uuid(eff_company))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(PhysicalLocation.name).all()]


def _names_map(db: Session, user_ids) -> Dict[str, str]:
    """Mapa {str(profile_id): full_name} para enriquecer asignaciones."""
    ids = {str(u) for u in user_ids if u}
    if not ids:
        return {}
    rows = db.query(Profile.id, Profile.full_name).filter(Profile.id.in_(list(ids))).all()
    return {str(r[0]): r[1] for r in rows}


def _active_assignments_for(db: Session, equipment_ids) -> Dict[str, EquipmentAssignment]:
    """Mapa {str(equipment_id): asignación activa} (returned_at IS NULL)."""
    ids = [e for e in equipment_ids if e]
    if not ids:
        return {}
    rows = (
        db.query(EquipmentAssignment)
        .filter(
            EquipmentAssignment.equipment_id.in_(ids),
            EquipmentAssignment.returned_at.is_(None),
        )
        .all()
    )
    return {str(r.equipment_id): r for r in rows}


# ============================================================
# LISTAR / BUSCAR
# ============================================================
def list_equipment(
    db: Session,
    ctx,
    company_id=None,
    branch_id=None,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    marca: Optional[str] = None,
    serial: Optional[str] = None,
    assigned_user_id=None,
    organization_id=None,
    department_id=None,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """Lista/busca activos del tenant activo con filtros. Enriquece con el
    usuario asignado actual y los nombres de pertenencia (org/empresa/depto/sucursal)."""
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    scope = ctx.scope_org_id if ctx is not None else None
    scope_company = ctx.scope_company_id if ctx is not None else None

    q = db.query(Equipment)
    if scope is not None:
        q = q.filter(Equipment.organization_id == _as_uuid(scope))
    elif organization_id:
        # Overview de plataforma con filtro explícito de organización.
        q = q.filter(Equipment.organization_id == _as_uuid(organization_id))
    if scope_company is not None:
        # El contexto está acotado a una empresa: filtro forzado por backend.
        q = q.filter(Equipment.company_id == _as_uuid(scope_company))
    if company_id:
        q = q.filter(Equipment.company_id == _as_uuid(company_id))
    if branch_id:
        q = q.filter(Equipment.branch_id == _as_uuid(branch_id))
    if department_id:
        q = q.filter(Equipment.department_id == _as_uuid(department_id))
    if tipo:
        q = q.filter(Equipment.tipo == tipo)
    if estado:
        q = q.filter(Equipment.estado == estado)
    if marca:
        q = q.filter(Equipment.marca.ilike(f"%{marca}%"))
    if serial:
        q = q.filter(Equipment.serial.ilike(f"%{serial}%"))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Equipment.codigo_interno.ilike(like),
            Equipment.serial.ilike(like),
            Equipment.modelo.ilike(like),
            Equipment.marca.ilike(like),
        ))

    # Filtro por usuario asignado: activos con asignación activa a ese usuario
    if assigned_user_id:
        sub = (
            db.query(EquipmentAssignment.equipment_id)
            .filter(
                EquipmentAssignment.user_id == _as_uuid(assigned_user_id),
                EquipmentAssignment.returned_at.is_(None),
            )
        )
        q = q.filter(Equipment.id.in_(sub))

    rows = q.order_by(desc(Equipment.updated_at)).offset(offset).limit(limit).all()

    active = _active_assignments_for(db, [r.id for r in rows])
    names = _names_map(db, [a.user_id for a in active.values()])
    pert = _pertenencia_names(
        db,
        [r.organization_id for r in rows],
        [r.company_id for r in rows],
        [r.department_id for r in rows],
        [r.branch_id for r in rows],
    )

    out = []
    for r in rows:
        a = active.get(str(r.id))
        out.append({
            "id": r.id,
            "codigo_interno": r.codigo_interno,
            "tipo": r.tipo,
            "marca": r.marca,
            "modelo": r.modelo,
            "serial": r.serial,
            "estado": r.estado,
            "ubicacion": r.ubicacion,
            "branch_id": r.branch_id,
            "company_id": r.company_id,
            "organization_id": r.organization_id,
            "department_id": r.department_id,
            "organization_name": pert["org"].get(str(r.organization_id)),
            "company_name": pert["company"].get(str(r.company_id)),
            "department_name": pert["dept"].get(str(r.department_id)),
            "branch_name": pert["branch"].get(str(r.branch_id)),
            "assigned_user_id": a.user_id if a else None,
            "assigned_user_name": names.get(str(a.user_id)) if a else None,
            "updated_at": r.updated_at,
            "data_source": "manual",
        })
    return out


def _pertenencia_names(db: Session, org_ids, company_ids, dept_ids, branch_ids) -> dict:
    """Resuelve nombres de organización/empresa/departamento/sucursal en lote."""
    def _m(model, ids):
        s = {str(i) for i in ids if i}
        if not s:
            return {}
        rows = db.query(model.id, model.name).filter(model.id.in_(list(s))).all()
        return {str(r[0]): r[1] for r in rows}
    return {
        "org": _m(Organization, org_ids),
        "company": _m(Company, company_ids),
        "dept": _m(Department, dept_ids),
        "branch": _m(Branch, branch_ids),
    }


# ============================================================
# FICHA / DETALLE
# ============================================================
def get_equipment(db: Session, ctx, equipment_id: UUID) -> dict:
    eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)

    active = _active_assignments_for(db, [eq.id]).get(str(eq.id))
    current = None
    if active:
        names = _names_map(db, [active.user_id])
        current = {
            "assignment_id": active.id,
            "user_id": active.user_id,
            "user_name": names.get(str(active.user_id)),
            "assigned_at": active.assigned_at,
            "assigned_by": active.assigned_by,
        }

    data = {c.name: getattr(eq, c.name) for c in eq.__table__.columns}
    data["current_assignment"] = current
    data["data_source"] = "manual"
    pert = _pertenencia_names(db, [eq.organization_id], [eq.company_id], [eq.department_id], [eq.branch_id])
    data["organization_name"] = pert["org"].get(str(eq.organization_id))
    data["company_name"] = pert["company"].get(str(eq.company_id))
    data["department_name"] = pert["dept"].get(str(eq.department_id))
    data["branch_name"] = pert["branch"].get(str(eq.branch_id))
    return data


# ============================================================
# CREAR / MODIFICAR
# ============================================================
def create_equipment(db: Session, ctx, data, current_user: dict) -> Equipment:
    payload = data.model_dump(exclude_unset=True)
    # Tenant EXPLÍCITO y validado en backend (plataforma elige; cliente fijo).
    req_org = payload.pop("organization_id", None)
    req_company = payload.pop("company_id", None)
    org_id, company_id = resolve_create_tenant(db, ctx, req_org, req_company)

    # Responsable inicial (opcional) — se procesa como asignación, no como texto.
    assigned_to = payload.pop("assigned_to", None)

    # Pertenencia: sucursal/ubicación/departamento deben pertenecer al tenant.
    _validate_branch_location(db, org_id, company_id, payload.get("branch_id"), payload.get("physical_location_id"))
    _validate_department(db, org_id, company_id, payload.get("department_id"))

    # Validar el responsable ANTES de crear nada (consistencia transaccional):
    # empresa EXACTA, activo y estado laboral admisible.
    if assigned_to:
        _validate_responsible(db, assigned_to, org_id, company_id)

    eq = Equipment(
        **payload,
        organization_id=_as_uuid(org_id),
        company_id=_as_uuid(company_id),
        created_by=_as_uuid(current_user.get("id")),
    )
    # Estado coherente con la asignación inicial. "asignado" SOLO es válido si hay
    # responsable inicial; nunca se acepta un "asignado" del cliente sin asignación.
    if assigned_to:
        eq.estado = "asignado"
    elif not eq.estado or eq.estado == "asignado":
        eq.estado = "disponible"

    # UNA sola transacción: activo + (opcional) asignación inicial. Si algo falla,
    # no queda un activo a medias.
    try:
        db.add(eq)
        db.flush()   # obtiene eq.id sin cerrar la transacción
        if assigned_to:
            db.add(EquipmentAssignment(
                equipment_id=eq.id,
                user_id=_as_uuid(assigned_to),
                assigned_by=_as_uuid(current_user.get("id")),
                organization_id=eq.organization_id,
                company_id=eq.company_id,
            ))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "codigo_interno" in msg:
            raise HTTPException(status_code=409, detail="Ya existe un activo con ese código interno.")
        raise HTTPException(status_code=400, detail="No se pudo crear el activo.")
    db.refresh(eq)
    return eq


def update_equipment(db: Session, ctx, equipment_id: UUID, data, current_user: dict) -> Equipment:
    update_data = data.model_dump(exclude_unset=True)
    update_data.pop("organization_id", None)   # el tenant nunca se reasigna desde el cliente
    update_data.pop("company_id", None)

    # Si el PATCH toca el estado, se bloquea la FILA del activo (FOR UPDATE) para
    # SERIALIZAR con assign/return (que usan el mismo lock): así la comprobación de
    # asignación activa y el commit son atómicos y no puede colarse una asignación
    # concurrente entre el chequeo y el guardado (estado inconsistente).
    changes_estado = "estado" in update_data
    if changes_estado:
        eq = _lock_equipment(db, equipment_id)
    else:
        eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)

    # Si se cambian sucursal/ubicación, validar contra el tenant del activo.
    if "branch_id" in update_data or "physical_location_id" in update_data:
        new_branch = update_data.get("branch_id", eq.branch_id)
        new_pl = update_data.get("physical_location_id", eq.physical_location_id)
        _validate_branch_location(db, eq.organization_id, eq.company_id, new_branch, new_pl)
    # Si se cambia el departamento, validarlo contra el tenant del activo.
    if "department_id" in update_data:
        _validate_department(db, eq.organization_id, eq.company_id, update_data.get("department_id"))

    # COHERENCIA DE ESTADOS: el PATCH normal NO puede alterar el estado de asignación
    # sin registrar el movimiento. "asignado" <=> existe una asignación activa.
    if "estado" in update_data:
        new_estado = update_data.get("estado")
        active = _active_assignments_for(db, [eq.id]).get(str(eq.id))
        if new_estado == "asignado" and active is None:
            raise HTTPException(
                status_code=409,
                detail="No se puede marcar 'asignado' sin una asignación activa. Usá el flujo de asignación.",
            )
        if active is not None and new_estado != "asignado":
            raise HTTPException(
                status_code=409,
                detail="El activo tiene una asignación activa. Registrá la devolución antes de cambiar el estado.",
            )

    for k, v in update_data.items():
        setattr(eq, k, v)

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "codigo_interno" in msg:
            raise HTTPException(status_code=409, detail="Ya existe un activo con ese código interno.")
        raise HTTPException(status_code=400, detail="No se pudo actualizar el activo.")
    db.refresh(eq)
    return eq


# ============================================================
# ASIGNAR / DEVOLVER
# ============================================================
def _lock_equipment(db: Session, equipment_id: UUID):
    """Trae el activo con FOR UPDATE para serializar asignaciones concurrentes.

    SIN fallback a SELECT sin bloqueo: si el motor no puede dar el lock, la
    excepción se propaga y la operación falla de forma segura (no se degrada
    silenciosamente a una lectura sin bloqueo que permitiría doble asignación).
    """
    return (
        db.query(Equipment)
        .filter(Equipment.id == equipment_id)
        .with_for_update()
        .first()
    )


def assign_equipment(db: Session, ctx, equipment_id: UUID, data, current_user: dict) -> EquipmentAssignment:
    # Bloqueo de fila del activo: dos asignaciones concurrentes se serializan;
    # la segunda espera al commit de la primera y entonces ve la activa -> 409.
    eq = _lock_equipment(db, equipment_id)
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)   # org + empresa (centralizado)

    if eq.organization_id is None or eq.company_id is None:
        raise HTTPException(status_code=409, detail="El activo no tiene organización/empresa asignada; no se puede asignar de forma segura.")

    # Responsable válido contra la ORG y EMPRESA REALES del activo (empresa exacta,
    # activo, estado laboral admisible). Se valida contra eq.company_id, no solo
    # contra el contexto.
    _validate_responsible(db, data.user_id, eq.organization_id, eq.company_id)

    # No permitir doble asignación activa (dentro del lock).
    existing = (
        db.query(EquipmentAssignment)
        .filter(
            EquipmentAssignment.equipment_id == eq.id,
            EquipmentAssignment.returned_at.is_(None),
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="El activo ya está asignado. Registrá la devolución antes de reasignar.")

    assignment = EquipmentAssignment(
        equipment_id=eq.id,
        user_id=_as_uuid(data.user_id),
        assigned_by=_as_uuid(current_user.get("id")),
        notas=getattr(data, "notas", None),
        organization_id=eq.organization_id,
        company_id=eq.company_id,
    )
    db.add(assignment)
    eq.estado = "asignado"
    db.commit()   # libera el lock
    db.refresh(assignment)
    return assignment


def return_equipment(db: Session, ctx, equipment_id: UUID, data, current_user: dict) -> EquipmentAssignment:
    eq = _lock_equipment(db, equipment_id)
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)   # org + empresa (centralizado)

    active = (
        db.query(EquipmentAssignment)
        .filter(
            EquipmentAssignment.equipment_id == eq.id,
            EquipmentAssignment.returned_at.is_(None),
        )
        .first()
    )
    if not active:
        raise HTTPException(status_code=409, detail="El activo no tiene una asignación activa.")

    active.returned_at = datetime.now(timezone.utc)
    active.returned_reason = getattr(data, "returned_reason", None)
    if getattr(data, "notas", None):
        active.notas = (active.notas + " | " if active.notas else "") + data.notas
    eq.estado = "disponible"
    db.commit()
    db.refresh(active)
    return active


def list_assignable_users(db: Session, ctx, company_id=None, search=None) -> List[dict]:
    """Usuarios elegibles para asignación: activos, NO dados de baja/suspendidos,
    de la EMPRESA seleccionada (validada) o del scope. Búsqueda por nombre/email.
    Aísla por tenant en backend; nunca expone usuarios de otra empresa."""
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    if company_id:
        _assert_company_access(db, ctx, company_id)
        c = db.query(Company.organization_id).filter(Company.id == _as_uuid(company_id)).first()
        eff_org = str(c[0]) if c else None
        eff_company = str(company_id)
    else:
        eff_org = ctx.scope_org_id if ctx is not None else None
        eff_company = ctx.scope_company_id if ctx is not None else None
    if eff_org is None:
        # Plataforma sin empresa concreta -> no se listan usuarios (hay que elegir empresa).
        return []

    q = db.query(Profile.id, Profile.full_name, Profile.email).filter(
        Profile.organization_id == _as_uuid(eff_org),
        Profile.is_active.is_(True),
        or_(Profile.estado_laboral.is_(None),
            Profile.estado_laboral.notin_(["baja", "suspendido"])),
    )
    if eff_company:
        q = q.filter(Profile.company_id == _as_uuid(eff_company))
    if search:
        like = f"%{search}%"
        q = q.filter(or_(Profile.full_name.ilike(like), Profile.email.ilike(like)))
    rows = q.order_by(Profile.full_name).limit(50).all()
    return [{"id": r[0], "full_name": r[1], "email": r[2]} for r in rows]


def list_assignments(db: Session, ctx, equipment_id: UUID) -> List[dict]:
    eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)

    rows = (
        db.query(EquipmentAssignment)
        .filter(EquipmentAssignment.equipment_id == eq.id)
        .order_by(desc(EquipmentAssignment.assigned_at))
        .all()
    )
    names = _names_map(db, [r.user_id for r in rows] + [r.assigned_by for r in rows])
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "equipment_id": r.equipment_id,
            "user_id": r.user_id,
            "user_name": names.get(str(r.user_id)),
            "assigned_at": r.assigned_at,
            "returned_at": r.returned_at,
            "assigned_by": r.assigned_by,
            "assigned_by_name": names.get(str(r.assigned_by)) if r.assigned_by else None,
            "returned_reason": r.returned_reason,
            "notas": r.notas,
        })
    return out


# ============================================================
# STATS / META
# ============================================================
def get_stats(db: Session, ctx) -> dict:
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed
    scope = ctx.scope_org_id if ctx is not None else None
    scope_company = ctx.scope_company_id if ctx is not None else None
    q = db.query(Equipment.id, Equipment.estado, Equipment.tipo)
    if scope is not None:
        q = q.filter(Equipment.organization_id == _as_uuid(scope))
    if scope_company is not None:
        q = q.filter(Equipment.company_id == _as_uuid(scope_company))
    rows = q.all()

    por_estado: Dict[str, int] = {}
    por_tipo: Dict[str, int] = {}
    for _id, estado, tipo in rows:
        por_estado[estado or "sin_estado"] = por_estado.get(estado or "sin_estado", 0) + 1
        por_tipo[tipo or "sin_tipo"] = por_tipo.get(tipo or "sin_tipo", 0) + 1

    # asignados reales = activos con asignación activa en el scope
    aq = db.query(EquipmentAssignment.equipment_id).filter(EquipmentAssignment.returned_at.is_(None))
    if scope is not None:
        aq = aq.filter(EquipmentAssignment.organization_id == _as_uuid(scope))
    if scope_company is not None:
        aq = aq.filter(EquipmentAssignment.company_id == _as_uuid(scope_company))
    asignados = aq.distinct().count()

    total = len(rows)
    # Los nombres del contrato de API se mantienen (los usa el frontend), pero los
    # VALORES se calculan desde los códigos válidos de PostgreSQL: 'reparacion', 'baja'.
    return {
        "total": total,
        "asignados": asignados,
        "disponibles": por_estado.get("disponible", 0),
        "en_reparacion": por_estado.get("reparacion", 0),
        "de_baja": por_estado.get("baja", 0),
        "por_estado": por_estado,
        "por_tipo": por_tipo,
    }


def get_meta(db: Session, ctx) -> dict:
    """Catálogos para los selects: marcas distintas del tenant + sucursales.
    Filtra por organización y, cuando el contexto está acotado a una empresa,
    también por esa empresa."""
    if ctx is not None:
        ctx.assert_company_scope()   # fail-closed (defensa en profundidad; el router ya lo exige)
    scope = ctx.scope_org_id if ctx is not None else None
    scope_company = ctx.scope_company_id if ctx is not None else None

    mq = db.query(Equipment.marca).filter(Equipment.marca.isnot(None))
    if scope is not None:
        mq = mq.filter(Equipment.organization_id == _as_uuid(scope))
    if scope_company is not None:
        mq = mq.filter(Equipment.company_id == _as_uuid(scope_company))
    marcas = sorted({m[0] for m in mq.distinct().all() if m[0]})

    # Sucursales activas del tenant (vía ORM, misma DB). Filtra por org y empresa.
    bq = db.query(Branch.id, Branch.name).filter(Branch.is_active.is_(True))
    if scope is not None:
        bq = bq.filter(Branch.organization_id == _as_uuid(scope))
    if scope_company is not None:
        bq = bq.filter(Branch.company_id == _as_uuid(scope_company))
    branches = [{"id": b[0], "name": b[1] or "(sin nombre)"} for b in bq.order_by(Branch.name).all()]

    return {"marcas": marcas, "branches": branches}
