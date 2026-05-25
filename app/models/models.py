"""Modelos SQLAlchemy del sistema de tickets."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Text,
    ForeignKey, Numeric, JSON, CheckConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Profile(Base):
    """Perfil de usuario con rol."""
    __tablename__ = "profiles"
    
    id = Column(UUID(as_uuid=True), primary_key=True)
    email = Column(String, unique=True, nullable=False)
    full_name = Column(String, nullable=False)
    phone = Column(String)
    whatsapp = Column(String)
    department = Column(String)
    location = Column(String)
    role = Column(String, nullable=False, default="usuario")
    avatar_url = Column(String)
    signature_url = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Category(Base):
    """Categorías de tickets."""
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)
    color = Column(String, default="#3b82f6")
    icon = Column(String, default="tool")
    whatsapp_number = Column(String)
    responsible_name = Column(String)
    default_assignee = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Location(Base):
    """Sucursales / ubicaciones."""
    __tablename__ = "locations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    address = Column(String)
    city = Column(String)
    latitude = Column(Numeric(10, 7))
    longitude = Column(Numeric(10, 7))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SystemConfig(Base):
    """Configuración del sistema."""
    __tablename__ = "system_config"
    
    key = Column(String, primary_key=True)
    value = Column(Text)
    description = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    updated_by = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))


class Ticket(Base):
    """Ticket de soporte con TODOS los campos PRO."""
    __tablename__ = "tickets"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_number = Column(Integer, unique=True, autoincrement=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    
    # Relaciones
    requester_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))
    
    # Snapshot del solicitante
    requester_name = Column(String, nullable=False)
    requester_email = Column(String)
    requester_phone = Column(String)
    requester_department = Column(String)
    location = Column(String)
    
    # Estado y prioridad
    status = Column(String, nullable=False, default="pendiente")
    priority = Column(String, nullable=False, default="media")
    assistance_type = Column(String, default="remoto")
    
    # GPS
    gps_latitude = Column(Numeric(10, 7))
    gps_longitude = Column(Numeric(10, 7))
    gps_accuracy = Column(Numeric(10, 2))
    
    # Datos del dispositivo
    device_type = Column(String)
    device_os = Column(String)
    device_browser = Column(String)
    device_screen = Column(String)
    user_agent = Column(Text)
    ip_address = Column(String)
    
    # Tiempos y SLA
    estimated_hours = Column(Numeric(10, 2))
    actual_hours = Column(Numeric(10, 2))
    due_date = Column(Date)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    sla_deadline = Column(DateTime(timezone=True))
    sla_status = Column(String, default="on_time")
    first_response_at = Column(DateTime(timezone=True))
    response_time_minutes = Column(Integer)
    resolution_time_minutes = Column(Integer)
    
    # Costos
    cost = Column(Numeric(15, 2), default=0)
    resolution_notes = Column(Text)
    
    # Firmas
    technician_signature = Column(Text)
    requester_signature = Column(Text)
    closed_by = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    
    # Satisfacción
    satisfaction_rating = Column(Integer)
    satisfaction_comment = Column(Text)
    satisfaction_submitted_at = Column(DateTime(timezone=True))
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relaciones
    requester = relationship("Profile", foreign_keys=[requester_id])
    assignee = relationship("Profile", foreign_keys=[assigned_to])
    category = relationship("Category")
    location_obj = relationship("Location")
    attachments = relationship("TicketAttachment", back_populates="ticket", cascade="all, delete-orphan")
    comments = relationship("TicketComment", back_populates="ticket", cascade="all, delete-orphan")


class TicketAttachment(Base):
    """Archivos adjuntos (imágenes, videos, audios)."""
    __tablename__ = "ticket_attachments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    file_name = Column(String, nullable=False)
    file_url = Column(Text, nullable=False)
    file_size = Column(Integer)
    file_type = Column(String)
    attachment_type = Column(String, default="file")  # file, image, video, audio, signature
    duration_seconds = Column(Integer)
    thumbnail_url = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    ticket = relationship("Ticket", back_populates="attachments")


class TicketComment(Base):
    """Comentarios en tickets."""
    __tablename__ = "ticket_comments"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    user_name = Column(String, nullable=False)
    user_role = Column(String)
    comment = Column(Text, nullable=False)
    is_internal = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    ticket = relationship("Ticket", back_populates="comments")


class ActivityLog(Base):
    """Log de auditoría."""
    __tablename__ = "activity_log"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"))
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    user_name = Column(String)
    user_role = Column(String)
    action = Column(String, nullable=False)
    description = Column(Text)
    activity_metadata = Column("metadata", JSON)
    ip_address = Column(String)
    user_agent = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """Notificaciones internas."""
    __tablename__ = "notifications"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"))
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String, default="info")
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SatisfactionSurvey(Base):
    """Encuestas de satisfacción."""
    __tablename__ = "satisfaction_surveys"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    speed_rating = Column(Integer)
    quality_rating = Column(Integer)
    technician_rating = Column(Integer)
    comment = Column(Text)
    would_recommend = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
