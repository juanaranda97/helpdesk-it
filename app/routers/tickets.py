"""Router de tickets — CRUD completo con aislamiento multi-tenant.

Todos los endpoints resuelven el tenant en backend (get_scoped_tenancy) y
validan el acceso por organización antes de cualquier lógica funcional.
"""
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.tenancy import get_scoped_tenancy, TenancyContext
from app.core import permissions
from app.models import Ticket, TicketAttachment, TicketComment, SatisfactionSurvey
from app.schemas.schemas import (
    TicketCreate, TicketUpdate, TicketClose, TicketResponse, TicketListItem,
    CommentCreate, CommentResponse, AttachmentResponse,
    SurveyCreate, SurveyResponse, DashboardStats
)
from app.services import tickets as ticket_service
from app.services.tickets import assert_ticket_access
from app.services.storage import upload_file_to_storage

router = APIRouter(prefix="/tickets", tags=["Tickets"])


def require_ticket_staff(ctx: TenancyContext = Depends(get_scoped_tenancy)) -> TenancyContext:
    """Contexto + exige rol de staff de tickets (técnico y superiores).

    FAIL-CLOSED: un rol de staff acotado a empresa SIN empresa asignada NO obtiene
    visibilidad de staff (listado/stats de toda la org); se corta con 403. Los
    usuarios finales acceden a SUS tickets por otras rutas (no por staff)."""
    if not permissions.can_staff_tickets(ctx.role):
        raise HTTPException(status_code=403, detail="Requiere permisos de técnico o superior")
    ctx.assert_company_scope()
    return ctx


def _get_ticket_or_404(db: Session, ticket_id: UUID, ctx: TenancyContext) -> Ticket:
    """Trae el ticket y corta 404 si no existe o es de otra organización."""
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    assert_ticket_access(ticket, ctx)   # 404 si cross-tenant
    return ticket


def _require_ticket_access(db: Session, ticket_id: UUID, ctx: TenancyContext) -> Ticket:
    """Tenant (404) + permiso funcional (403): solicitante, técnico asignado o staff.
    Misma regla que GET /tickets/{id}, reutilizada por comentarios y adjuntos.

    El solicitante y el técnico asignado siempre acceden a SU ticket. El acceso por
    PRIVILEGIO de staff es FAIL-CLOSED: un rol de staff acotado sin empresa no obtiene
    acceso de staff a tickets de toda la organización (403)."""
    ticket = _get_ticket_or_404(db, ticket_id, ctx)
    uid = UUID(ctx.user_id)
    if ticket.requester_id == uid or ticket.assigned_to == uid:
        return ticket
    if not permissions.can_staff_tickets(ctx.role):
        raise HTTPException(status_code=403, detail="Sin permiso para este ticket")
    ctx.assert_company_scope()   # staff sin empresa: fail-closed
    return ticket


# ============================================================
# DASHBOARD
# ============================================================
@router.get("/stats/me", response_model=DashboardStats)
async def my_stats(
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    stats = ticket_service.get_dashboard_stats(
        db, user_id=UUID(ctx.user_id),
        scope_org_id=ctx.scope_org_id, scope_company_id=ctx.scope_company_id,
    )
    return DashboardStats(**stats)


@router.get("/stats/all", response_model=DashboardStats)
async def all_stats(
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_ticket_staff),
):
    stats = ticket_service.get_dashboard_stats(
        db, scope_org_id=ctx.scope_org_id, scope_company_id=ctx.scope_company_id
    )
    return DashboardStats(**stats)


# ============================================================
# CRUD TICKETS
# ============================================================
@router.post("/", response_model=TicketResponse, status_code=201)
async def create_ticket(
    data: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    """Crea un ticket. El tenant se resuelve y valida en backend."""
    ip = request.client.host if request.client else None
    org_id, company_id = ctx.tenant_for_create()
    ticket = ticket_service.create_ticket(
        db, data, ctx.user,
        organization_id=org_id, company_id=company_id, ip_address=ip,
    )
    return ticket


@router.get("/my", response_model=List[TicketListItem])
async def my_tickets(
    search: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    return ticket_service.list_user_tickets(
        db, UUID(ctx.user_id), search, status, priority, limit, offset,
        scope_org_id=ctx.scope_org_id, scope_company_id=ctx.scope_company_id,
    )


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
    ctx: TenancyContext = Depends(require_ticket_staff),
):
    return ticket_service.list_all_tickets(
        db, search, status, priority, assigned_to, sla_status, limit, offset,
        scope_org_id=ctx.scope_org_id, scope_company_id=ctx.scope_company_id,
    )


@router.get("/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    return _require_ticket_access(db, ticket_id, ctx)


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: UUID,
    data: TicketUpdate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    # El service valida tenant (404) + permisos funcionales.
    return ticket_service.update_ticket(db, ticket_id, data, ctx.user, ctx=ctx)


@router.post("/{ticket_id}/close", response_model=TicketResponse)
async def close_ticket(
    ticket_id: UUID,
    data: TicketClose,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(require_ticket_staff),
):
    return ticket_service.close_ticket_with_signature(
        db, ticket_id,
        signature_base64=data.technician_signature,
        resolution_notes=data.resolution_notes,
        actual_hours=float(data.actual_hours) if data.actual_hours else None,
        cost=float(data.cost) if data.cost else None,
        technician=ctx.user,
        ctx=ctx,
    )


# ============================================================
# ATTACHMENTS
# ============================================================
@router.post("/{ticket_id}/attachments", response_model=AttachmentResponse, status_code=201)
async def upload_attachment(
    ticket_id: UUID,
    file: UploadFile = File(...),
    duration_seconds: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    ticket = _require_ticket_access(db, ticket_id, ctx)
    metadata = await upload_file_to_storage(file, str(ticket_id), ctx.user_id)
    attachment = TicketAttachment(
        ticket_id=ticket_id,
        organization_id=ticket.organization_id,
        company_id=ticket.company_id,
        uploaded_by=UUID(ctx.user_id),
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
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    _require_ticket_access(db, ticket_id, ctx)
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
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    ticket = _require_ticket_access(db, ticket_id, ctx)
    # Comentario INTERNO = operación privilegiada de staff: requiere rol de staff Y
    # alcance de empresa. Un staff acotado sin empresa (aunque sea solicitante/asignado)
    # NO puede crear comentarios internos.
    staff_privilege = permissions.can_staff_tickets(ctx.role) and not ctx.missing_required_company
    if data.is_internal and not staff_privilege:
        raise HTTPException(status_code=403, detail="No tenés permiso para crear comentarios internos")
    comment = TicketComment(
        ticket_id=ticket_id,
        organization_id=ticket.organization_id,
        company_id=ticket.company_id,
        user_id=UUID(ctx.user_id),
        user_name=ctx.user["full_name"],
        user_role=ctx.role,
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
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    _require_ticket_access(db, ticket_id, ctx)
    query = db.query(TicketComment).filter(TicketComment.ticket_id == ticket_id)
    # Los comentarios internos son visibles solo con PRIVILEGIO de staff (rol de staff
    # + alcance de empresa). Un staff acotado sin empresa no los ve.
    staff_privilege = permissions.can_staff_tickets(ctx.role) and not ctx.missing_required_company
    if not staff_privilege:
        query = query.filter(TicketComment.is_internal == False)  # noqa: E712
    return query.order_by(TicketComment.created_at).all()


# ============================================================
# SURVEY
# ============================================================
@router.post("/{ticket_id}/survey", response_model=SurveyResponse, status_code=201)
async def submit_survey(
    ticket_id: UUID,
    data: SurveyCreate,
    db: Session = Depends(get_db),
    ctx: TenancyContext = Depends(get_scoped_tenancy),
):
    ticket = _get_ticket_or_404(db, ticket_id, ctx)

    if ticket.requester_id != UUID(ctx.user_id):
        raise HTTPException(status_code=403, detail="Solo el solicitante puede calificar")
    if ticket.status != "completada":
        raise HTTPException(status_code=400, detail="Solo se puede calificar tickets completados")

    existing = db.query(SatisfactionSurvey).filter(
        SatisfactionSurvey.ticket_id == ticket_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ya enviaste una encuesta para este ticket")

    survey = SatisfactionSurvey(
        ticket_id=ticket_id,
        user_id=UUID(ctx.user_id),
        rating=data.rating,
        speed_rating=data.speed_rating,
        quality_rating=data.quality_rating,
        technician_rating=data.technician_rating,
        comment=data.comment,
        would_recommend=data.would_recommend,
    )
    db.add(survey)
    ticket.satisfaction_rating = data.rating
    ticket.satisfaction_comment = data.comment
    ticket.satisfaction_submitted_at = survey.created_at
    db.commit()
    db.refresh(survey)
    return survey
