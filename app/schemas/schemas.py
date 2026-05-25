"""Schemas Pydantic para validación de entrada/salida."""
from datetime import datetime, date
from decimal import Decimal
from typing import Optional, List, Literal
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ============================================================
# AUTH
# ============================================================
class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    full_name: str = Field(min_length=2, max_length=100)
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    department: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    user: dict


# ============================================================
# PROFILES
# ============================================================
class ProfileBase(BaseModel):
    full_name: str
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None


class ProfileUpdate(ProfileBase):
    full_name: Optional[str] = None


class ProfileRoleUpdate(BaseModel):
    role: Literal["usuario", "tecnico", "supervisor", "admin"]


class ProfileResponse(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    email: str
    role: str
    avatar_url: Optional[str] = None
    is_active: bool
    created_at: datetime


# ============================================================
# CATEGORIES
# ============================================================
class CategoryBase(BaseModel):
    name: str
    description: Optional[str] = None
    color: str = "#3b82f6"
    icon: str = "tool"
    whatsapp_number: Optional[str] = None
    responsible_name: Optional[str] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    icon: Optional[str] = None
    whatsapp_number: Optional[str] = None
    responsible_name: Optional[str] = None


class CategoryResponse(CategoryBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    created_at: datetime


# ============================================================
# LOCATIONS
# ============================================================
class LocationBase(BaseModel):
    name: str
    address: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None


class LocationCreate(LocationBase):
    pass


class LocationResponse(LocationBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    is_active: bool
    created_at: datetime


# ============================================================
# TICKETS
# ============================================================
PriorityType = Literal["baja", "media", "alta", "urgente"]
StatusType = Literal["pendiente", "en_curso", "en_espera", "completada", "cancelada"]
AssistanceType = Literal["remoto", "presencial", "telefonico", "whatsapp"]


class TicketCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10, max_length=2000)
    category_id: int
    priority: PriorityType = "media"
    location_id: Optional[int] = None
    location: Optional[str] = None
    assistance_type: AssistanceType = "remoto"
    requester_phone: Optional[str] = None
    
    # Datos automáticos
    gps_latitude: Optional[float] = None
    gps_longitude: Optional[float] = None
    gps_accuracy: Optional[float] = None
    device_type: Optional[str] = None
    device_os: Optional[str] = None
    device_browser: Optional[str] = None
    device_screen: Optional[str] = None
    user_agent: Optional[str] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[StatusType] = None
    priority: Optional[PriorityType] = None
    assigned_to: Optional[UUID] = None
    category_id: Optional[int] = None
    actual_hours: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    resolution_notes: Optional[str] = None
    due_date: Optional[date] = None


class TicketClose(BaseModel):
    """Cerrar ticket con firma del técnico."""
    technician_signature: str  # base64 de la firma
    resolution_notes: str = Field(min_length=10)
    actual_hours: Optional[Decimal] = None
    cost: Optional[Decimal] = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_number: int
    title: str
    description: str
    
    requester_id: UUID
    assigned_to: Optional[UUID] = None
    category_id: Optional[int] = None
    location_id: Optional[int] = None
    
    requester_name: str
    requester_email: Optional[str] = None
    requester_phone: Optional[str] = None
    requester_department: Optional[str] = None
    location: Optional[str] = None
    
    status: str
    priority: str
    assistance_type: Optional[str] = None
    
    gps_latitude: Optional[Decimal] = None
    gps_longitude: Optional[Decimal] = None
    
    device_type: Optional[str] = None
    device_os: Optional[str] = None
    device_browser: Optional[str] = None
    
    sla_deadline: Optional[datetime] = None
    sla_status: Optional[str] = None
    first_response_at: Optional[datetime] = None
    response_time_minutes: Optional[int] = None
    resolution_time_minutes: Optional[int] = None
    
    actual_hours: Optional[Decimal] = None
    cost: Optional[Decimal] = None
    resolution_notes: Optional[str] = None
    
    technician_signature: Optional[str] = None
    satisfaction_rating: Optional[int] = None
    
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class TicketListItem(BaseModel):
    """Ticket reducido para listados."""
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_number: int
    title: str
    requester_name: str
    status: str
    priority: str
    sla_status: Optional[str] = None
    sla_deadline: Optional[datetime] = None
    category_id: Optional[int] = None
    assigned_to: Optional[UUID] = None
    created_at: datetime


# ============================================================
# ATTACHMENTS
# ============================================================
class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_id: UUID
    file_name: str
    file_url: str
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    attachment_type: str
    duration_seconds: Optional[int] = None
    thumbnail_url: Optional[str] = None
    created_at: datetime


# ============================================================
# COMMENTS
# ============================================================
class CommentCreate(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)
    is_internal: bool = False


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_id: UUID
    user_id: UUID
    user_name: str
    user_role: Optional[str] = None
    comment: str
    is_internal: bool
    created_at: datetime


# ============================================================
# SATISFACTION SURVEY
# ============================================================
class SurveyCreate(BaseModel):
    rating: int = Field(ge=1, le=5)
    speed_rating: Optional[int] = Field(default=None, ge=1, le=5)
    quality_rating: Optional[int] = Field(default=None, ge=1, le=5)
    technician_rating: Optional[int] = Field(default=None, ge=1, le=5)
    comment: Optional[str] = None
    would_recommend: bool = True


class SurveyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_id: UUID
    rating: int
    speed_rating: Optional[int] = None
    quality_rating: Optional[int] = None
    technician_rating: Optional[int] = None
    comment: Optional[str] = None
    would_recommend: bool
    created_at: datetime


# ============================================================
# CONFIG
# ============================================================
class ConfigUpdate(BaseModel):
    key: str
    value: str


class ConfigResponse(BaseModel):
    key: str
    value: Optional[str] = None
    description: Optional[str] = None


# ============================================================
# DASHBOARD STATS
# ============================================================
class DashboardStats(BaseModel):
    total: int = 0
    pendientes: int = 0
    en_curso: int = 0
    completadas: int = 0
    canceladas: int = 0
    urgentes: int = 0
    sla_compliance_pct: Optional[float] = None
    sla_overdue: int = 0
    sla_at_risk: int = 0
    avg_response_min: Optional[float] = None
    avg_resolution_min: Optional[float] = None
    avg_satisfaction: Optional[float] = None


# ============================================================
# NOTIFICATIONS
# ============================================================
class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: UUID
    ticket_id: Optional[UUID] = None
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime
