"""Lógica de negocio de tickets."""
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID
from sqlalchemy import or_, and_, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, status

from app.models import Ticket, Profile, Category, SystemConfig, Notification, ActivityLog
from app.schemas.schemas import TicketCreate, TicketUpdate
from app.core import permissions


def _as_uuid(value) -> Optional[UUID]:
    """Convierte str/UUID/None a UUID (o None) de forma segura."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def assert_ticket_access(ticket: Ticket, ctx) -> None:
    """
    Corta con 404 si el contexto NO puede acceder al ticket por tenant.
    Se usa 404 (no 403) para no revelar la existencia de tickets de otra org.
    """
    if ctx is not None and not ctx.can_access_org(getattr(ticket, "organization_id", None)):
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    if ctx is not None and not ctx.can_access_company(getattr(ticket, "company_id", None)):
        raise HTTPException(status_code=404, detail="Ticket no encontrado")


def assignee_in_tenant(db: Session, assignee_id, org_id, company_id=None) -> bool:
    """¿El usuario a asignar pertenece al MISMO tenant del ticket?
    Org debe coincidir siempre; la empresa solo se exige cuando ambas (ticket y
    usuario) tienen empresa definida (un técnico org-level sin empresa es válido)."""
    if assignee_id is None:
        return False
    p = db.query(Profile).filter(Profile.id == assignee_id).first()
    if not p:
        return False
    if org_id is not None and str(getattr(p, "organization_id", None)) != str(org_id):
        return False
    if company_id is not None and getattr(p, "company_id", None) is not None and str(p.company_id) != str(company_id):
        return False
    return True


def get_sla_hours(db: Session, priority: str) -> int:
    """Obtiene horas de SLA según prioridad desde system_config."""
    key = f"sla_{priority}_hours"
    config = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if config and config.value:
        try:
            return int(config.value)
        except ValueError:
            pass
    # Valores por defecto
    defaults = {"urgente": 2, "alta": 8, "media": 24, "baja": 72}
    return defaults.get(priority, 24)


def create_ticket(
    db: Session,
    data: TicketCreate,
    requester: dict,
    organization_id,
    company_id=None,
    ip_address: Optional[str] = None
) -> Ticket:
    """Crea un ticket nuevo con todos los datos PRO.

    El tenant (organization_id/company_id) llega YA resuelto y validado desde el
    contexto de backend (ver get_ticket_tenancy), nunca directo del navegador.
    """

    ticket = Ticket(
        title=data.title,
        description=data.description,
        category_id=data.category_id,
        priority=data.priority,
        location_id=data.location_id,
        location=data.location,
        assistance_type=data.assistance_type,

        # Tenancy (resuelto en backend)
        organization_id=_as_uuid(organization_id),
        company_id=_as_uuid(company_id),

        # Datos del solicitante
        requester_id=UUID(requester["id"]) if isinstance(requester["id"], str) else requester["id"],
        requester_name=requester["full_name"],
        requester_email=requester.get("email"),
        requester_phone=data.requester_phone or requester.get("phone"),
        requester_ci=data.requester_ci or requester.get("ci"),
        requester_department=requester.get("department"),
        
        # GPS
        gps_latitude=data.gps_latitude,
        gps_longitude=data.gps_longitude,
        gps_accuracy=data.gps_accuracy,
        
        # Dispositivo
        device_type=data.device_type,
        device_os=data.device_os,
        device_browser=data.device_browser,
        device_screen=data.device_screen,
        user_agent=data.user_agent,
        ip_address=ip_address,
        
        # Estado inicial
        status="pendiente",
    )
    
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    
    # Auto-asignar técnico responsable de la categoría — SOLO si pertenece al
    # mismo tenant del ticket (no se asigna a un usuario de otra org/empresa).
    if data.category_id:
        category = db.query(Category).filter(Category.id == data.category_id).first()
        if category and category.default_assignee and assignee_in_tenant(
            db, category.default_assignee, ticket.organization_id, ticket.company_id
        ):
            ticket.assigned_to = category.default_assignee
            db.commit()
            db.refresh(ticket)

            # Notificar técnico
            notif = Notification(
                user_id=category.default_assignee,
                ticket_id=ticket.id,
                title="Nuevo ticket asignado",
                message=f"Se te asignó el ticket #{ticket.ticket_number}: {ticket.title}",
                type="info"
            )
            db.add(notif)
            db.commit()
    
    return ticket


def update_ticket(
    db: Session,
    ticket_id: UUID,
    data: TicketUpdate,
    current_user: dict,
    ctx=None,
) -> Ticket:
    """Actualiza un ticket con validaciones de tenant y de permisos."""

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    # 1) Aislamiento por tenant ANTES de cualquier lógica funcional (404 si cross-tenant).
    assert_ticket_access(ticket, ctx)

    user_role = current_user.get("role", "usuario")
    user_id = UUID(current_user["id"]) if isinstance(current_user["id"], str) else current_user["id"]

    # 2) Permisos funcionales (jerarquía real de roles de CORE)
    is_owner = ticket.requester_id == user_id
    is_assignee = ticket.assigned_to == user_id
    is_staff = permissions.can_staff_tickets(user_role)

    if not (is_owner or is_assignee or is_staff):
        raise HTTPException(status_code=403, detail="No tenés permiso para editar este ticket")

    # Solo staff (técnico+) puede cambiar estado, asignación, prioridad, horas, costo
    update_data = data.model_dump(exclude_unset=True)

    restricted_fields = ["status", "assigned_to", "priority", "actual_hours", "cost"]
    if not is_staff:
        for field in restricted_fields:
            update_data.pop(field, None)

    # Solo supervisor+ puede asignar a otros; un técnico puede autoasignarse
    if "assigned_to" in update_data and not permissions.can_assign_tickets(user_role):
        if not (permissions.can_staff_tickets(user_role) and update_data["assigned_to"] == user_id):
            update_data.pop("assigned_to", None)

    # El técnico asignado NO puede ser de otra organización/empresa que el ticket.
    if update_data.get("assigned_to") is not None:
        if not assignee_in_tenant(db, update_data["assigned_to"], ticket.organization_id, ticket.company_id):
            raise HTTPException(status_code=403, detail="El técnico no pertenece a la organización/empresa del ticket.")

    for key, value in update_data.items():
        setattr(ticket, key, value)

    db.commit()
    db.refresh(ticket)
    return ticket


def close_ticket_with_signature(
    db: Session,
    ticket_id: UUID,
    signature_base64: str,
    resolution_notes: str,
    actual_hours: Optional[float],
    cost: Optional[float],
    technician: dict,
    ctx=None,
) -> Ticket:
    """Cierra un ticket con firma digital del técnico."""

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    # Aislamiento por tenant (404 si el ticket es de otra organización).
    assert_ticket_access(ticket, ctx)

    if ticket.status == "completada":
        raise HTTPException(status_code=400, detail="Ticket ya está cerrado")
    
    tech_id = UUID(technician["id"]) if isinstance(technician["id"], str) else technician["id"]
    
    ticket.status = "completada"
    ticket.completed_at = datetime.now(timezone.utc)
    ticket.technician_signature = signature_base64
    ticket.resolution_notes = resolution_notes
    ticket.closed_by = tech_id
    if actual_hours is not None:
        ticket.actual_hours = actual_hours
    if cost is not None:
        ticket.cost = cost
    
    db.commit()
    db.refresh(ticket)
    
    # Notificar al solicitante para que llene encuesta
    notif = Notification(
        user_id=ticket.requester_id,
        ticket_id=ticket.id,
        title="Tu ticket fue resuelto",
        message=f"El ticket #{ticket.ticket_number} fue completado. Calificá el servicio.",
        type="success"
    )
    db.add(notif)
    db.commit()
    
    return ticket


def list_user_tickets(
    db: Session,
    user_id: UUID,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    scope_org_id=None,
    scope_company_id=None,
) -> List[Ticket]:
    """Lista los tickets del usuario: los que creó Y los que tiene asignados (técnico).

    `scope_org_id` / `scope_company_id`: si no son None, restringen por org/empresa
    (defensa en profundidad; empresa solo cuando el contexto está acotado a una).
    """
    query = db.query(Ticket).filter(or_(
        Ticket.requester_id == user_id,
        Ticket.assigned_to == user_id
    ))

    if scope_org_id is not None:
        query = query.filter(Ticket.organization_id == _as_uuid(scope_org_id))
    if scope_company_id is not None:
        query = query.filter(Ticket.company_id == _as_uuid(scope_company_id))

    if search:
        query = query.filter(or_(
            Ticket.title.ilike(f"%{search}%"),
            Ticket.description.ilike(f"%{search}%")
        ))
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    if priority_filter:
        query = query.filter(Ticket.priority == priority_filter)
    
    return query.order_by(desc(Ticket.created_at)).offset(offset).limit(limit).all()


def list_all_tickets(
    db: Session,
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    assigned_to: Optional[UUID] = None,
    sla_filter: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    scope_org_id=None,
    scope_company_id=None,
) -> List[Ticket]:
    """Lista los tickets de la organización activa (para técnicos+).

    `scope_org_id`: None solo para overview de plataforma (Ommeganet sin impersonar);
    en cualquier otro caso es la org activa y FILTRA por ella.
    `scope_company_id`: filtra por empresa solo cuando el contexto está acotado a una.
    """
    query = db.query(Ticket)

    if scope_org_id is not None:
        query = query.filter(Ticket.organization_id == _as_uuid(scope_org_id))
    if scope_company_id is not None:
        query = query.filter(Ticket.company_id == _as_uuid(scope_company_id))

    if search:
        query = query.filter(or_(
            Ticket.title.ilike(f"%{search}%"),
            Ticket.description.ilike(f"%{search}%"),
            Ticket.requester_name.ilike(f"%{search}%")
        ))
    if status_filter:
        query = query.filter(Ticket.status == status_filter)
    if priority_filter:
        query = query.filter(Ticket.priority == priority_filter)
    if assigned_to:
        query = query.filter(Ticket.assigned_to == assigned_to)
    if sla_filter:
        query = query.filter(Ticket.sla_status == sla_filter)
    
    return query.order_by(desc(Ticket.created_at)).offset(offset).limit(limit).all()


def get_dashboard_stats(db: Session, user_id: Optional[UUID] = None, scope_org_id=None, scope_company_id=None) -> dict:
    """Calcula estadísticas del dashboard, acotadas por tenant.

    `scope_org_id`: None solo para overview de plataforma; si no, filtra por esa org.
    `scope_company_id`: filtra por empresa solo cuando el contexto está acotado a una.
    """
    from sqlalchemy import func

    query = db.query(Ticket)
    if user_id:
        query = query.filter(Ticket.requester_id == user_id)
    if scope_org_id is not None:
        query = query.filter(Ticket.organization_id == _as_uuid(scope_org_id))
    if scope_company_id is not None:
        query = query.filter(Ticket.company_id == _as_uuid(scope_company_id))

    tickets = query.all()
    
    stats = {
        "total": len(tickets),
        "pendientes": sum(1 for t in tickets if t.status == "pendiente"),
        "en_curso": sum(1 for t in tickets if t.status == "en_curso"),
        "completadas": sum(1 for t in tickets if t.status == "completada"),
        "canceladas": sum(1 for t in tickets if t.status == "cancelada"),
        "urgentes": sum(1 for t in tickets if t.priority == "urgente" and t.status != "completada"),
        "sla_overdue": sum(1 for t in tickets if t.sla_status == "overdue"),
        "sla_at_risk": sum(1 for t in tickets if t.sla_status == "at_risk"),
    }
    
    # SLA compliance
    met = sum(1 for t in tickets if t.sla_status == "met")
    breached = sum(1 for t in tickets if t.sla_status == "breached")
    if met + breached > 0:
        stats["sla_compliance_pct"] = round(100.0 * met / (met + breached), 1)
    else:
        stats["sla_compliance_pct"] = None
    
    # Tiempos promedio
    response_times = [t.response_time_minutes for t in tickets if t.response_time_minutes]
    resolution_times = [t.resolution_time_minutes for t in tickets if t.resolution_time_minutes]
    
    stats["avg_response_min"] = round(sum(response_times) / len(response_times), 1) if response_times else None
    stats["avg_resolution_min"] = round(sum(resolution_times) / len(resolution_times), 1) if resolution_times else None
    
    # Satisfacción promedio
    ratings = [t.satisfaction_rating for t in tickets if t.satisfaction_rating]
    stats["avg_satisfaction"] = round(sum(ratings) / len(ratings), 2) if ratings else None
    
    return stats
