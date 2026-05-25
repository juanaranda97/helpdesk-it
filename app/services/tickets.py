"""Lógica de negocio de tickets."""
from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID
from sqlalchemy import or_, and_, desc
from sqlalchemy.orm import Session, joinedload
from fastapi import HTTPException, status

from app.models import Ticket, Profile, Category, SystemConfig, Notification, ActivityLog
from app.schemas.schemas import TicketCreate, TicketUpdate


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
    ip_address: Optional[str] = None
) -> Ticket:
    """Crea un ticket nuevo con todos los datos PRO."""
    
    ticket = Ticket(
        title=data.title,
        description=data.description,
        category_id=data.category_id,
        priority=data.priority,
        location_id=data.location_id,
        location=data.location,
        assistance_type=data.assistance_type,
        
        # Datos del solicitante
        requester_id=UUID(requester["id"]) if isinstance(requester["id"], str) else requester["id"],
        requester_name=requester["full_name"],
        requester_email=requester.get("email"),
        requester_phone=data.requester_phone or requester.get("phone"),
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
    
    # Auto-asignar técnico responsable de la categoría
    if data.category_id:
        category = db.query(Category).filter(Category.id == data.category_id).first()
        if category and category.default_assignee:
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
    current_user: dict
) -> Ticket:
    """Actualiza un ticket con validaciones de permisos."""
    
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    user_role = current_user.get("role", "usuario")
    user_id = UUID(current_user["id"]) if isinstance(current_user["id"], str) else current_user["id"]
    
    # Validar permisos
    is_owner = ticket.requester_id == user_id
    is_assignee = ticket.assigned_to == user_id
    is_tech_plus = user_role in ("tecnico", "supervisor", "admin")
    
    if not (is_owner or is_assignee or is_tech_plus):
        raise HTTPException(status_code=403, detail="No tenés permiso para editar este ticket")
    
    # Solo tech+ puede cambiar estado, asignación, prioridad
    update_data = data.model_dump(exclude_unset=True)
    
    restricted_fields = ["status", "assigned_to", "priority", "actual_hours", "cost"]
    if not is_tech_plus:
        for field in restricted_fields:
            update_data.pop(field, None)
    
    # Solo supervisor+ puede asignar
    if "assigned_to" in update_data and user_role not in ("supervisor", "admin"):
        if not (user_role == "tecnico" and update_data["assigned_to"] == user_id):
            update_data.pop("assigned_to", None)
    
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
    technician: dict
) -> Ticket:
    """Cierra un ticket con firma digital del técnico."""
    
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
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
    offset: int = 0
) -> List[Ticket]:
    """Lista los tickets de un usuario."""
    query = db.query(Ticket).filter(Ticket.requester_id == user_id)
    
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
    offset: int = 0
) -> List[Ticket]:
    """Lista todos los tickets (para técnicos+)."""
    query = db.query(Ticket)
    
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


def get_dashboard_stats(db: Session, user_id: Optional[UUID] = None) -> dict:
    """Calcula estadísticas del dashboard."""
    from sqlalchemy import func
    
    query = db.query(Ticket)
    if user_id:
        query = query.filter(Ticket.requester_id == user_id)
    
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
