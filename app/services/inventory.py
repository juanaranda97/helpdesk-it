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

from app.models import Equipment, EquipmentAssignment, Profile, Branch, PhysicalLocation


def _as_uuid(value) -> Optional[UUID]:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def assert_equipment_access(eq: Equipment, ctx) -> None:
    """404 si el contexto NO puede acceder al activo por tenant (organización Y empresa).
    Control de acceso CENTRALIZADO: lo usan get/update/assign/return/list_assignments."""
    if ctx is None:
        return
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
        if company_id is not None and b.company_id is not None and str(b.company_id) != str(company_id):
            raise HTTPException(status_code=400, detail="La sucursal no pertenece a la empresa del activo.")
    if physical_location_id:
        pl = db.query(PhysicalLocation).filter(PhysicalLocation.id == _as_uuid(physical_location_id)).first()
        if not pl or str(pl.organization_id) != str(org_id):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la organización del activo.")
        if company_id is not None and pl.company_id is not None and str(pl.company_id) != str(company_id):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la empresa del activo.")
        if branch_id and pl.branch_id is not None and str(pl.branch_id) != str(_as_uuid(branch_id)):
            raise HTTPException(status_code=400, detail="La ubicación no pertenece a la sucursal indicada.")


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
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """Lista/busca activos del tenant activo con filtros. Enriquece con el
    usuario asignado actual (de la asignación activa)."""
    scope = ctx.scope_org_id if ctx is not None else None
    scope_company = ctx.scope_company_id if ctx is not None else None

    q = db.query(Equipment)
    if scope is not None:
        q = q.filter(Equipment.organization_id == _as_uuid(scope))
    if scope_company is not None:
        # El contexto está acotado a una empresa: filtro forzado por backend.
        q = q.filter(Equipment.company_id == _as_uuid(scope_company))
    if company_id:
        q = q.filter(Equipment.company_id == _as_uuid(company_id))
    if branch_id:
        q = q.filter(Equipment.branch_id == _as_uuid(branch_id))
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
            "assigned_user_id": a.user_id if a else None,
            "assigned_user_name": names.get(str(a.user_id)) if a else None,
            "updated_at": r.updated_at,
            "data_source": "manual",
        })
    return out


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
    return data


# ============================================================
# CREAR / MODIFICAR
# ============================================================
def create_equipment(db: Session, ctx, data, current_user: dict) -> Equipment:
    org_id, company_id = ctx.tenant_for_create()

    payload = data.model_dump(exclude_unset=True)
    # El tenant NO viene del cliente: se fija desde el contexto validado.
    payload.pop("organization_id", None)
    payload.pop("company_id", None)

    # Sucursal/ubicación deben pertenecer al tenant del activo.
    _validate_branch_location(db, org_id, company_id, payload.get("branch_id"), payload.get("physical_location_id"))

    eq = Equipment(
        **payload,
        organization_id=_as_uuid(org_id),
        company_id=_as_uuid(company_id),
        created_by=_as_uuid(current_user.get("id")),
    )
    if not eq.estado:
        eq.estado = "disponible"

    db.add(eq)
    try:
        db.commit()
    except Exception as e:
        db.rollback()
        msg = str(e).lower()
        if "unique" in msg or "duplicate" in msg or "codigo_interno" in msg:
            raise HTTPException(status_code=409, detail="Ya existe un activo con ese código interno.")
        raise HTTPException(status_code=400, detail="No se pudo crear el activo.")
    db.refresh(eq)
    return eq


def update_equipment(db: Session, ctx, equipment_id: UUID, data, current_user: dict) -> Equipment:
    eq = db.query(Equipment).filter(Equipment.id == equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="Activo no encontrado")
    assert_equipment_access(eq, ctx)

    update_data = data.model_dump(exclude_unset=True)
    # El tenant nunca se reasigna desde el cliente.
    update_data.pop("organization_id", None)
    update_data.pop("company_id", None)

    # Si se cambian sucursal/ubicación, validar contra el tenant del activo.
    if "branch_id" in update_data or "physical_location_id" in update_data:
        new_branch = update_data.get("branch_id", eq.branch_id)
        new_pl = update_data.get("physical_location_id", eq.physical_location_id)
        _validate_branch_location(db, eq.organization_id, eq.company_id, new_branch, new_pl)

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

    if eq.organization_id is None:
        raise HTTPException(status_code=409, detail="El activo no tiene organización asignada; no se puede asignar de forma segura.")

    # El usuario destino debe existir y pertenecer a la MISMA organización del activo.
    target = db.query(Profile).filter(Profile.id == _as_uuid(data.user_id)).first()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if str(getattr(target, "organization_id", None)) != str(eq.organization_id):
        raise HTTPException(status_code=403, detail="El usuario no pertenece a la organización del activo.")
    # Si el contexto está acotado a una empresa, el usuario debe ser de esa empresa.
    if ctx is not None and not ctx.can_access_company(getattr(target, "company_id", None)):
        raise HTTPException(status_code=403, detail="El usuario no pertenece a la empresa activa.")

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


def list_assignable_users(db: Session, ctx) -> List[dict]:
    """Usuarios elegibles para asignación: SOLO de la org (y empresa) activa,
    activos, con datos mínimos. Aísla por tenant en backend."""
    scope = ctx.scope_org_id if ctx is not None else None
    if scope is None:
        # Overview de plataforma: no hay una org concreta -> no se listan usuarios.
        return []
    q = db.query(Profile.id, Profile.full_name).filter(
        Profile.organization_id == _as_uuid(scope),
        Profile.is_active.is_(True),
    )
    sc = ctx.scope_company_id if ctx is not None else None
    if sc is not None:
        q = q.filter(Profile.company_id == _as_uuid(sc))
    rows = q.order_by(Profile.full_name).all()
    return [{"id": r[0], "full_name": r[1]} for r in rows]


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

