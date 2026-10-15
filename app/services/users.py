"""Servicio de Usuarios (CORE-USERS-01).

Reglas de negocio con aislamiento multi-tenant confiable y auditoría:
- Alcance por organización y empresa; FAIL-CLOSED para roles acotados sin empresa.
- Nunca se autoriza usando IDs del cliente: todo se valida contra la DB y el ctx.
- Jerarquía de roles explícita; se bloquea escalada de privilegios, edición de
  owners por actores de menor rango, autoescalada/autodesactivación y la
  eliminación/desactivación del ÚLTIMO platform_owner activo.
- Toda mutación de perfil/rol/estado queda auditada (actor, tenant, campo,
  valor previo/nuevo, resultado) sin exponer secretos.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core import permissions
from app.models import Profile, Organization, Company, Department, ProfileAuditLog

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_uuid(v) -> Optional[UUID]:
    if v is None:
        return None
    return v if isinstance(v, UUID) else UUID(str(v))


def _opt_uuid_eq(a, b) -> bool:
    """Igualdad de UUID opcionales. None==None True; None vs concreto False
    (un NULL no coincide con un tenant concreto)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return str(_as_uuid(a)) == str(_as_uuid(b))


def _audit_row(ctx, target: Profile, action: str, field: str, old, new, result: str) -> ProfileAuditLog:
    return ProfileAuditLog(
        actor_id=_as_uuid(ctx.user_id),
        actor_role=ctx.role,
        actor_organization_id=_as_uuid(ctx.active_org_id),
        actor_company_id=_as_uuid(ctx.active_company_id),
        target_user_id=target.id,
        target_organization_id=target.organization_id,
        target_company_id=target.company_id,
        action=action,
        field=field,
        old_value=None if old is None else str(old),
        new_value=None if new is None else str(new),
        result=result,
    )


def _audit(db: Session, ctx, target: Profile, action: str, field: str,
           old, new, result: str = "ok") -> None:
    """Auditoría de un cambio APLICADO ('ok'): va en la MISMA transacción del cambio
    (se commitea junto). Para operaciones DENEGADAS usar _audit_denied (durable)."""
    db.add(_audit_row(ctx, target, action, field, old, new, result))


def _audit_denied(db: Session, ctx, target: Profile, action: str, field: str, old, new) -> None:
    """Auditoría DURABLE de una operación DENEGADA.

    Se escribe en una transacción INDEPENDIENTE (conexión propia) que se commitea
    de inmediato, de modo que el registro 'denied' persiste aunque la transacción
    principal haga rollback al levantar la HTTPException. No guarda secretos."""
    bind = db.get_bind()
    engine = getattr(bind, "engine", bind)   # Session.get_bind() -> Engine (o Connection)
    s2 = Session(bind=engine)
    try:
        s2.add(_audit_row(ctx, target, action, field, old, new, result="denied"))
        s2.commit()
    except Exception as e:
        s2.rollback()
        # No se silencia: se registra para monitoreo/alerta. Si el log de auditoría
        # falla sistemáticamente, debe detectarse (estrategia explícita de registro).
        logger.error(
            "AUDIT_FAILURE: no se pudo persistir auditoría 'denied' "
            "action=%s field=%s target=%s actor=%s err=%s",
            action, field, getattr(target, "id", None), getattr(ctx, "user_id", None),
            type(e).__name__,
        )
    finally:
        s2.close()


# ---------------------------------------------------------------------------
# Alcance / acceso
# ---------------------------------------------------------------------------
def _scoped_query(db: Session, ctx):
    """Query de Profile acotada al tenant del actor. FAIL-CLOSED."""
    ctx.assert_company_scope()
    q = db.query(Profile)
    scope_org = ctx.scope_org_id
    if scope_org is not None:
        q = q.filter(Profile.organization_id == _as_uuid(scope_org))
    scope_company = ctx.scope_company_id
    if scope_company is not None:
        q = q.filter(Profile.company_id == _as_uuid(scope_company))
    return q


def _get_target_or_404(db: Session, ctx, user_id) -> Profile:
    """Trae el perfil destino y corta 404 si es de otra org/empresa (no revela
    existencia cross-tenant). FAIL-CLOSED para roles acotados sin empresa."""
    ctx.assert_company_scope()
    p = db.query(Profile).filter(Profile.id == _as_uuid(user_id)).first()
    if not p:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not ctx.can_access_org(p.organization_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if not ctx.can_access_company(p.company_id):
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return p


def _assert_outranks(db: Session, ctx, target: Profile) -> None:
    """El actor debe tener rango >= al rol ACTUAL del destino. Protege a los owners
    y a cualquier usuario de mayor jerarquía frente a actores de menor rango.
    El rechazo se AUDITA de forma durable antes de levantar 403."""
    if not permissions.can_assign_role(ctx.role, target.role or "usuario"):
        _audit_denied(db, ctx, target, "deny_outrank", "role", target.role, ctx.role)
        raise HTTPException(status_code=403, detail="No podés administrar a un usuario de mayor jerarquía.")


_OWNER_GUARD_LOCK_KEY = 865142039   # clave constante para pg_advisory_xact_lock


def _lock_owner_guard(db: Session) -> None:
    """Serializa TODAS las operaciones que puedan afectar la cantidad de owners
    activos, usando un advisory lock transaccional de PostgreSQL. Dos requests
    concurrentes que intenten degradar/desactivar owners distintos se serializan:
    la segunda espera al commit de la primera y recién entonces cuenta -> no pueden
    dejar la plataforma sin owner activo. El lock se libera al commit/rollback.

    FAIL-CLOSED: si el bloqueo NO puede adquirirse, la operación se corta (503). Nunca
    se continúa con una degradación/desactivación de owners sin la protección de
    concurrencia (no se degrada silenciosamente a un conteo sin serializar)."""
    try:
        from sqlalchemy import text
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _OWNER_GUARD_LOCK_KEY})
    except Exception as e:
        logger.error("OWNER_GUARD_LOCK_FAILURE: %s", type(e).__name__)
        raise HTTPException(
            status_code=503,
            detail="No se pudo asegurar la protección de concurrencia de owners; reintentá.",
        )


def _is_owner_operative(p: Profile) -> bool:
    """Un owner cuenta como ACTIVO y OPERATIVO si está is_active y su estado laboral
    no es baja/suspendido (coherencia is_active + estado_laboral)."""
    return bool(p.is_active) and (getattr(p, "estado_laboral", None) not in ("baja", "suspendido"))


def _count_active_platform_owners(db: Session, exclude_id=None) -> int:
    from sqlalchemy import or_
    q = db.query(Profile).filter(
        Profile.role.in_(list(permissions.PLATFORM_OWNER_ROLES)),
        Profile.is_active.is_(True),
        or_(Profile.estado_laboral.is_(None),
            Profile.estado_laboral.notin_(["baja", "suspendido"])),
    )
    if exclude_id is not None:
        q = q.filter(Profile.id != _as_uuid(exclude_id))
    return q.count()


# ---------------------------------------------------------------------------
# Listados
# ---------------------------------------------------------------------------
def list_users(db: Session, ctx, search: Optional[str] = None,
               limit: int = 200, offset: int = 0) -> List[Profile]:
    q = _scoped_query(db, ctx)
    if search:
        like = f"%{search}%"
        from sqlalchemy import or_
        q = q.filter(or_(Profile.full_name.ilike(like), Profile.email.ilike(like)))
    return q.order_by(Profile.full_name).offset(offset).limit(limit).all()


def list_technicians(db: Session, ctx) -> List[Profile]:
    """Técnicos+ asignables del tenant del actor (activos, no baja/suspendido)."""
    from sqlalchemy import or_
    q = _scoped_query(db, ctx).filter(
        Profile.role.in_(["tecnico", "supervisor", "manager", "company_admin",
                          "admin", "org_admin"]),
        Profile.is_active.is_(True),
        or_(Profile.estado_laboral.is_(None),
            Profile.estado_laboral.notin_(["baja", "suspendido"])),
    )
    return q.order_by(Profile.full_name).all()


# ---------------------------------------------------------------------------
# Validación de pertenencia (department/company/org) para edición
# ---------------------------------------------------------------------------
def _validate_company_in_org(db: Session, company_id, org_id) -> None:
    c = db.query(Company).filter(Company.id == _as_uuid(company_id)).first()
    if not c or not _opt_uuid_eq(c.organization_id, org_id):
        raise HTTPException(status_code=400, detail="La empresa no pertenece a la organización indicada.")


def _validate_department(db: Session, department_id, org_id, company_id) -> Department:
    d = db.query(Department).filter(Department.id == _as_uuid(department_id)).first()
    if not d or not _opt_uuid_eq(d.organization_id, org_id):
        raise HTTPException(status_code=400, detail="El departamento no pertenece a la organización del usuario.")
    # Empresa EXACTA: NULL no coincide con empresa concreta.
    if not _opt_uuid_eq(d.company_id, company_id):
        raise HTTPException(status_code=400, detail="El departamento no pertenece a la empresa del usuario.")
    return d


# ---------------------------------------------------------------------------
# Mutaciones
# ---------------------------------------------------------------------------
def _apply_role_change(db: Session, ctx, target: Profile, new_role: str) -> bool:
    """Valida y aplica un cambio de rol. Devuelve True si cambió. Audita."""
    if new_role == target.role:
        return False
    if not permissions.is_valid_role(new_role):
        _audit_denied(db, ctx, target, "change_role", "role", target.role, new_role)
        raise HTTPException(status_code=400, detail="Rol inválido.")
    # No autoescalada / no cambiar el propio rol.
    if str(target.id) == str(ctx.user_id):
        _audit_denied(db, ctx, target, "change_role", "role", target.role, new_role)
        raise HTTPException(status_code=403, detail="No podés cambiar tu propio rol.")
    # El actor debe superar el rol ACTUAL del destino...
    _assert_outranks(db, ctx, target)
    # ...y poder OTORGAR el nuevo rol (no asignar un rol mayor al propio).
    if not permissions.can_assign_role(ctx.role, new_role):
        _audit_denied(db, ctx, target, "change_role", "role", target.role, new_role)
        raise HTTPException(status_code=403, detail="No podés asignar un rol de mayor jerarquía que el tuyo.")
    # Solo un platform_owner puede OTORGAR roles de plataforma.
    if new_role in permissions.PLATFORM_ROLES and not permissions.is_platform_owner(ctx.role):
        _audit_denied(db, ctx, target, "change_role", "role", target.role, new_role)
        raise HTTPException(status_code=403, detail="Solo un platform_owner puede asignar roles de plataforma.")
    # Degradar a un platform_owner: no dejar la plataforma sin owner activo (serializado).
    if permissions.is_platform_owner(target.role) and not permissions.is_platform_owner(new_role):
        _lock_owner_guard(db)
        if _is_owner_operative(target) and _count_active_platform_owners(db, exclude_id=target.id) == 0:
            _audit_denied(db, ctx, target, "change_role", "role", target.role, new_role)
            raise HTTPException(status_code=409, detail="No se puede degradar al último platform_owner activo.")
    old = target.role
    target.role = new_role
    _audit(db, ctx, target, "change_role", "role", old, new_role)
    return True


def _apply_active_change(db: Session, ctx, target: Profile, new_active: bool) -> bool:
    if bool(target.is_active) == bool(new_active):
        return False
    if str(target.id) == str(ctx.user_id):
        _audit_denied(db, ctx, target, "toggle_active", "is_active", target.is_active, new_active)
        raise HTTPException(status_code=403, detail="No podés desactivar tu propia cuenta.")
    _assert_outranks(db, ctx, target)
    # No desactivar al último platform_owner activo (serializado contra concurrencia).
    if not new_active and permissions.is_platform_owner(target.role):
        _lock_owner_guard(db)
        if _count_active_platform_owners(db, exclude_id=target.id) == 0:
            _audit_denied(db, ctx, target, "toggle_active", "is_active", target.is_active, new_active)
            raise HTTPException(status_code=409, detail="No se puede desactivar al último platform_owner activo.")
    old = target.is_active
    target.is_active = new_active
    _audit(db, ctx, target, "toggle_active", "is_active", old, new_active)
    return True


def change_role(db: Session, ctx, user_id, new_role: str) -> Profile:
    target = _get_target_or_404(db, ctx, user_id)
    _apply_role_change(db, ctx, target, new_role)
    db.commit(); db.refresh(target)
    return target


def toggle_active(db: Session, ctx, user_id) -> Profile:
    target = _get_target_or_404(db, ctx, user_id)
    _apply_active_change(db, ctx, target, not bool(target.is_active))
    db.commit(); db.refresh(target)
    return target


def update_user(db: Session, ctx, user_id, data) -> Profile:
    """Edición CONTROLADA y parcial. Solo aplica los campos enviados; cada uno con
    su propia validación/autorización. Una sola transacción + auditoría por campo."""
    target = _get_target_or_404(db, ctx, user_id)
    payload = data.model_dump(exclude_unset=True)

    # El actor debe superar el rol actual del destino para editarlo.
    _assert_outranks(db, ctx, target)

    # Determinar org/empresa EFECTIVAS tras el update (para validar dept/empresa).
    eff_org = target.organization_id
    eff_company = target.company_id

    # --- organización: solo plataforma puede mover de org ---
    if "organization_id" in payload:
        new_org = _as_uuid(payload["organization_id"])
        if not _opt_uuid_eq(new_org, target.organization_id):
            if not ctx.is_platform_user:
                raise HTTPException(status_code=403, detail="No podés cambiar la organización de un usuario.")
            if new_org is not None:
                o = db.query(Organization).filter(Organization.id == new_org).first()
                if not o:
                    raise HTTPException(status_code=400, detail="Organización inexistente.")
            old = target.organization_id
            target.organization_id = new_org
            eff_org = new_org
            # Al cambiar de org, empresa y departamento (estructurado Y texto legacy)
            # previos dejan de ser válidos: se limpian para no arrastrar pertenencia vieja.
            if target.company_id is not None:
                _audit(db, ctx, target, "update_profile", "company_id", target.company_id, None)
                target.company_id = None
            eff_company = None
            if target.department_id is not None:
                _audit(db, ctx, target, "update_profile", "department_id", target.department_id, None)
                target.department_id = None
            if target.department is not None:
                _audit(db, ctx, target, "update_profile", "department", target.department, None)
                target.department = None
            _audit(db, ctx, target, "update_profile", "organization_id", old, new_org)

    # --- empresa ---
    if "company_id" in payload:
        new_company = _as_uuid(payload["company_id"])
        if not _opt_uuid_eq(new_company, target.company_id):
            # Un actor acotado a empresa no puede sacar/mover usuarios de su empresa.
            if ctx.scope_company_id is not None and not _opt_uuid_eq(new_company, ctx.scope_company_id):
                raise HTTPException(status_code=403, detail="No podés asignar a un usuario a otra empresa.")
            if new_company is not None:
                _validate_company_in_org(db, new_company, eff_org)
            old = target.company_id
            target.company_id = new_company
            eff_company = new_company
            # Cambió la empresa: el departamento previo (estructurado Y texto legacy)
            # puede no corresponder -> se limpia para no dejar depto de empresa anterior.
            if target.department_id is not None:
                _audit(db, ctx, target, "update_profile", "department_id", target.department_id, None)
                target.department_id = None
            if target.department is not None:
                _audit(db, ctx, target, "update_profile", "department", target.department, None)
                target.department = None
            _audit(db, ctx, target, "update_profile", "company_id", old, new_company)

    # --- departamento estructurado (reconcilia el texto legacy) ---
    if "department_id" in payload:
        new_dep = _as_uuid(payload["department_id"])
        if new_dep is not None:
            d = _validate_department(db, new_dep, eff_org, eff_company)
            old = target.department_id
            target.department_id = new_dep
            _audit(db, ctx, target, "update_profile", "department_id", old, new_dep)
            # Reconciliación: el texto legacy refleja el nombre del departamento.
            if target.department != d.name:
                _audit(db, ctx, target, "update_profile", "department", target.department, d.name)
                target.department = d.name
        else:
            # Quitar el departamento ESTRUCTURADO limpia TAMBIÉN el texto legacy,
            # para no dejar el nombre del departamento anterior colgado en la tabla.
            old = target.department_id
            if old is not None:
                target.department_id = None
                _audit(db, ctx, target, "update_profile", "department_id", old, None)
            if target.department is not None:
                _audit(db, ctx, target, "update_profile", "department", target.department, None)
                target.department = None

    # --- texto legacy explícito (si no vino department_id) ---
    # El departamento TEXTUAL solo se admite para perfiles administrativos SIN empresa
    # (company_id NULL), sin asignarles empresa ficticia. Un perfil CON empresa debe
    # usar el departamento ESTRUCTURADO (department_id), AUNQUE su department_id esté
    # NULL: no se puede omitir la validación estructurada por el solo hecho de que
    # department_id sea NULL. Si en este mismo request se limpió la empresa (org/empresa
    # cambiada), target.company_id ya quedó en None y el texto vuelve a ser editable.
    if "department" in payload and "department_id" not in payload:
        new_txt = payload["department"]
        if new_txt != target.department:
            if target.company_id is not None:
                raise HTTPException(
                    status_code=400,
                    detail="El usuario pertenece a una empresa; su departamento es estructurado (no texto).",
                )
            _audit(db, ctx, target, "update_profile", "department", target.department, new_txt)
            target.department = new_txt

    # --- full_name ---
    if "full_name" in payload and payload["full_name"] is not None:
        if payload["full_name"] != target.full_name:
            _audit(db, ctx, target, "update_profile", "full_name", target.full_name, payload["full_name"])
            target.full_name = payload["full_name"]

    # --- rol ---
    if "role" in payload and payload["role"] is not None:
        _apply_role_change(db, ctx, target, payload["role"])

    # --- estado activo ---
    if "is_active" in payload and payload["is_active"] is not None:
        _apply_active_change(db, ctx, target, bool(payload["is_active"]))

    # --- estado laboral ---
    if "estado_laboral" in payload and payload["estado_laboral"] is not None:
        new_el = payload["estado_laboral"]
        if new_el != target.estado_laboral:
            if str(target.id) == str(ctx.user_id):
                _audit_denied(db, ctx, target, "update_profile", "estado_laboral", target.estado_laboral, new_el)
                raise HTTPException(status_code=403, detail="No podés cambiar tu propio estado laboral.")
            # baja/suspendido de un owner: no dejar la plataforma sin owner activo
            # (serializado contra concurrencia; coherente con is_active).
            if new_el in ("baja", "suspendido") and permissions.is_platform_owner(target.role) and target.is_active:
                _lock_owner_guard(db)
                if _count_active_platform_owners(db, exclude_id=target.id) == 0:
                    _audit_denied(db, ctx, target, "update_profile", "estado_laboral", target.estado_laboral, new_el)
                    raise HTTPException(status_code=409, detail="No se puede dar de baja al último platform_owner activo.")
            _audit(db, ctx, target, "update_profile", "estado_laboral", target.estado_laboral, new_el)
            target.estado_laboral = new_el

    db.commit(); db.refresh(target)
    return target


# ---------------------------------------------------------------------------
# Selectores dependientes (para el modal de edición) — todos scopeados
# ---------------------------------------------------------------------------
def _can_use_org(ctx, org_id) -> bool:
    if ctx.is_platform_user and not ctx.is_impersonating:
        return True
    return _opt_uuid_eq(org_id, ctx.active_org_id)


def list_org_options(db: Session, ctx) -> List[dict]:
    ctx.assert_company_scope()
    q = db.query(Organization.id, Organization.name).filter(Organization.is_active.is_(True))
    if not (ctx.is_platform_user and not ctx.is_impersonating):
        q = q.filter(Organization.id == _as_uuid(ctx.active_org_id))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Organization.name).all()]


def list_company_options(db: Session, ctx, organization_id) -> List[dict]:
    ctx.assert_company_scope()
    if not organization_id or not _can_use_org(ctx, organization_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa organización")
    q = db.query(Company.id, Company.name).filter(
        Company.organization_id == _as_uuid(organization_id),
        Company.is_active.is_(True),
    )
    if ctx.scope_company_id:
        q = q.filter(Company.id == _as_uuid(ctx.scope_company_id))
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Company.name).all()]


def list_department_options(db: Session, ctx, company_id) -> List[dict]:
    ctx.assert_company_scope()
    c = db.query(Company).filter(Company.id == _as_uuid(company_id)).first()
    if not c or not _can_use_org(ctx, c.organization_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa empresa")
    if ctx.scope_company_id and not _opt_uuid_eq(ctx.scope_company_id, company_id):
        raise HTTPException(status_code=403, detail="No autorizado para esa empresa")
    q = db.query(Department.id, Department.name).filter(
        Department.company_id == _as_uuid(company_id),
        Department.is_active.is_(True),
    )
    return [{"id": r[0], "name": r[1]} for r in q.order_by(Department.name).all()]
