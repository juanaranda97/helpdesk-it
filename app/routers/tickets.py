"""Router de tickets - CRUD completo."""
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_tech
from app.core.supabase_client import supabase_admin
from app.models import Ticket, TicketAttachment, TicketComment, SatisfactionSurvey
from app.schemas.schemas import (
    TicketCreate, TicketUpdate, TicketClose, TicketResponse, TicketListItem,
    CommentCreate, CommentResponse, AttachmentResponse,
    SurveyCreate, SurveyResponse, DashboardStats
)
from app.services import tickets as ticket_service
from app.services.storage import upload_file_to_storage

router = APIRouter(prefix="/tickets", tags=["Tickets"])


# ============================================================
# DASHBOARD
# ============================================================
@router.get("/stats/me", response_model=DashboardStats)
async def my_stats(
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Estadísticas del usuario actual."""
    stats = ticket_service.get_dashboard_stats(db, user_id=UUID(current_user["id"]))
    return DashboardStats(**stats)


@router.get("/stats/all", response_model=DashboardStats)
async def all_stats(
    db: Session = Depends(get_db),
    _: dict = Depends(require_tech)
):
    """Estadísticas globales (tech+)."""
    stats = ticket_service.get_dashboard_stats(db)
    return DashboardStats(**stats)


# ============================================================
# CRUD TICKETS
# ============================================================
@router.post("/", response_model=TicketResponse, status_code=201)
async def create_ticket(
    data: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Crea un ticket nuevo."""
    ip = request.client.host if request.client else None
    ticket = ticket_service.create_ticket(db, data, current_user, ip)
    return ticket


@router.get("/my", response_model=List[TicketListItem])
async def my_tickets(
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Lista los tickets del usuario actual."""
    user_id = UUID(current_user["id"])
    return ticket_service.list_user_tickets(db, user_id, search, status, priority, limit, offset)


@router.get("/all", response_model=List[TicketListItem])
async def all_tickets(
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[UUID] = None,
    sla_status: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: dict = Depends(require_tech)
):
    """Lista todos los tickets (tech+)."""
    return ticket_service.list_all_tickets(
        db, search, status, priority, assigned_to, sla_status, limit, offset
    )


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Obtiene un ticket específico."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    # Validar permisos
    user_id = UUID(current_user["id"])
    user_role = current_user.get("role", "usuario")
    
    if ticket.requester_id != user_id and ticket.assigned_to != user_id and \
       user_role not in ("admin", "supervisor", "tecnico"):
        raise HTTPException(status_code=403, detail="Sin permiso para ver este ticket")
    
    return ticket


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: UUID,
    data: TicketUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Actualiza un ticket."""
    return ticket_service.update_ticket(db, ticket_id, data, current_user)


@router.post("/{ticket_id}/close", response_model=TicketResponse)
async def close_ticket(
    ticket_id: UUID,
    data: TicketClose,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_tech)
):
    """Cierra un ticket con firma digital del técnico."""
    return ticket_service.close_ticket_with_signature(
        db, ticket_id,
        signature_base64=data.technician_signature,
        resolution_notes=data.resolution_notes,
        actual_hours=float(data.actual_hours) if data.actual_hours else None,
        cost=float(data.cost) if data.cost else None,
        technician=current_user
    )


# ============================================================
# ATTACHMENTS (fotos, videos, audios)
# ============================================================
@router.post("/{ticket_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    ticket_id: UUID,
    file: UploadFile = File(...),
    duration_seconds: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Sube un archivo (foto/video/audio) al ticket."""
    
    # Validar acceso al ticket
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    # Subir a storage
    metadata = await upload_file_to_storage(file, str(ticket_id), current_user["id"])
    
    # Guardar en DB
    attachment = TicketAttachment(
        ticket_id=ticket_id,
        uploaded_by=UUID(current_user["id"]),
        file_name=metadata["file_name"],
        file_url=metadata["file_url"],
        file_size=metadata["file_size"],
        file_type=metadata["file_type"],
        attachment_type=metadata["attachment_type"],
        duration_seconds=duration_seconds,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)
    
    return attachment


@router.get("/{ticket_id}/attachments", response_model=List[AttachmentResponse])
async def list_attachments(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Lista archivos adjuntos de un ticket."""
    return db.query(TicketAttachment).filter(
        TicketAttachment.ticket_id == ticket_id
    ).order_by(TicketAttachment.created_at.desc()).all()


# ============================================================
# COMMENTS
# ============================================================
@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=201)
async def add_comment(
    ticket_id: UUID,
    data: CommentCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Agrega un comentario al ticket."""
    comment = TicketComment(
        ticket_id=ticket_id,
        user_id=UUID(current_user["id"]),
        user_name=current_user["full_name"],
        user_role=current_user.get("role", "usuario"),
        comment=data.comment,
        is_internal=data.is_internal,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


@router.get("/{ticket_id}/comments", response_model=List[CommentResponse])
async def list_comments(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Lista comentarios de un ticket."""
    user_role = current_user.get("role", "usuario")
    query = db.query(TicketComment).filter(TicketComment.ticket_id == ticket_id)
    
    # Usuarios normales no ven comentarios internos
    if user_role == "usuario":
        query = query.filter(TicketComment.is_internal == False)
    
    return query.order_by(TicketComment.created_at).all()


# ============================================================
# SURVEY
# ============================================================
@router.post("/{ticket_id}/survey", response_model=SurveyResponse, status_code=201)
async def submit_survey(
    ticket_id: UUID,
    data: SurveyCreate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(get_current_user)
):
    """Solicitante envía encuesta de satisfacción."""
    
    # Verificar que el ticket existe y es del usuario
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    if ticket.requester_id != UUID(current_user["id"]):
        raise HTTPException(status_code=403, detail="Solo el solicitante puede calificar")
    
    if ticket.status != "completada":
        raise HTTPException(status_code=400, detail="Solo se puede calificar tickets completados")
    
    # Verificar si ya hay encuesta
    existing = db.query(SatisfactionSurvey).filter(
        SatisfactionSurvey.ticket_id == ticket_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ya enviaste una encuesta para este ticket")
    
    survey = SatisfactionSurvey(
        ticket_id=ticket_id,
        user_id=UUID(current_user["id"]),
        rating=data.rating,
        speed_rating=data.speed_rating,
        quality_rating=data.quality_rating,
        technician_rating=data.technician_rating,
        comment=data.comment,
        would_recommend=data.would_recommend,
    )
    db.add(survey)
    
    # Actualizar el ticket también
    ticket.satisfaction_rating = data.rating
    ticket.satisfaction_comment = data.comment
    ticket.satisfaction_submitted_at = survey.created_at
    
    db.commit()
    db.refresh(survey)
    return survey
