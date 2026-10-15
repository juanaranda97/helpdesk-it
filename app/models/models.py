"""Modelos SQLAlchemy del sistema de tickets."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Date, Text,
    ForeignKey, Numeric, JSON, CheckConstraint, text
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
    estado_laboral = Column(String)   # activo | baja | suspendido (para elegibilidad)
    # Tenancy (columnas YA existentes en la DB; se mapean para validar que un
    # usuario pertenezca a una org/empresa — p.ej. al asignar un activo).
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))
    # Pertenencia estructurada (CORE-USERS-01): department_id reconcilia el texto
    # legacy `department`. branch_id ya existe en la DB; se mapea para lectura/validación.
    # `department_id` requiere la migración aditiva 0002 (ADD COLUMN IF NOT EXISTS).
    branch_id = Column(UUID(as_uuid=True))
    department_id = Column(UUID(as_uuid=True))
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
    ticket_number = Column(Integer, unique=True, server_default=text("nextval('tickets_ticket_number_seq')"))
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    
    # Tenancy (columnas YA existentes en PostgreSQL; la FK vive en la DB,
    # no se declara aquí para no requerir modelos ORM de organizations/companies
    # ni emitir DDL. NO se crean columnas nuevas ni migraciones.)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))

    # Relaciones
    requester_id = Column(UUID(as_uuid=True), ForeignKey("profiles.id"), nullable=False)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("profiles.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))
    
    # Snapshot del solicitante
    requester_name = Column(String, nullable=False)
    requester_email = Column(String)
    requester_phone = Column(String)
    requester_ci = Column(String)
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
    # Tenancy (columnas YA existentes en la DB; se heredan del ticket padre)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))
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
    # Tenancy (columnas YA existentes en la DB; se heredan del ticket padre)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))
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


# ============================================================
# INVENTARIO (ITAM) — tablas YA existentes en Supabase
# Se mapean las columnas reales. Las FK a organizations/companies/branches/
# physical_locations viven en la DB; aquí se declaran como UUID planos para no
# requerir modelos ORM de esas tablas (no se emite DDL). NO se crean columnas.
# ============================================================
class Equipment(Base):
    """Activo de inventario (equipment)."""
    __tablename__ = "equipment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    codigo_interno = Column(Text, unique=True)      # asset tag interno (ÚNICO en DB)
    tipo = Column(Text, nullable=False)
    marca = Column(Text)
    modelo = Column(Text)
    serial = Column(Text)
    nro_producto = Column(Text)

    # Fechas
    fecha_fabricacion = Column(Date)
    fecha_compra = Column(Date)
    fecha_garantia_hasta = Column(Date)

    # Especificaciones técnicas
    procesador = Column(Text)
    ram = Column(Text)
    tipo_ram = Column(Text)
    drive = Column(Text)
    tipo_drive = Column(Text)
    marca_drive = Column(Text)
    sistema_operativo = Column(Text)
    version_so = Column(Text)
    so_instalado_el = Column(Date)
    antivirus = Column(Text)
    antivirus_version = Column(Text)
    antivirus_fecha = Column(Date)
    onedrive_estado = Column(Text)
    monitor_marca = Column(Text)
    monitor_tamano = Column(Text)
    mouse_modelo = Column(Text)
    tiene_auricular = Column(Boolean, default=False)
    anydesk_id = Column(Text)

    # Estado y ubicación
    estado = Column(Text, nullable=False, default="disponible")
    ubicacion = Column(Text)
    notas = Column(Text)

    # Metadata / tenancy
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(UUID(as_uuid=True))
    branch_id = Column(UUID(as_uuid=True))
    physical_location_id = Column(UUID(as_uuid=True))
    department_id = Column(UUID(as_uuid=True))   # FK a departments.id (requiere migración)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))

    assignments = relationship(
        "EquipmentAssignment",
        back_populates="equipment",
    )


class EquipmentAssignment(Base):
    """Asignación/devolución de un activo a un usuario (equipment_assignments)."""
    __tablename__ = "equipment_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    equipment_id = Column(UUID(as_uuid=True), ForeignKey("equipment.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), nullable=False)   # FK a profiles.id en la DB
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())
    returned_at = Column(DateTime(timezone=True))          # NULL = asignación activa
    assigned_by = Column(UUID(as_uuid=True))
    returned_reason = Column(Text)
    notas = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))

    equipment = relationship("Equipment", back_populates="assignments")


class Branch(Base):
    """Sucursal (branches). Modelo mínimo de SOLO LECTURA para validar tenant.
    Las columnas reales son más; aquí solo las necesarias para aislamiento."""
    __tablename__ = "branches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text)
    is_active = Column(Boolean, default=True)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))


class PhysicalLocation(Base):
    """Ubicación física (physical_locations). Modelo mínimo SOLO LECTURA."""
    __tablename__ = "physical_locations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    branch_id = Column(UUID(as_uuid=True))
    name = Column(Text)
    is_active = Column(Boolean, default=True)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))


class Organization(Base):
    """Organización (organizations). Modelo mínimo de SOLO LECTURA."""
    __tablename__ = "organizations"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text)
    is_active = Column(Boolean, default=True)


class Company(Base):
    """Empresa (companies). Modelo mínimo de SOLO LECTURA."""
    __tablename__ = "companies"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(UUID(as_uuid=True))
    name = Column(Text)
    is_active = Column(Boolean, default=True)


class Department(Base):
    """Departamento/área (departments). Modelo mínimo de SOLO LECTURA."""
    __tablename__ = "departments"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text)
    is_active = Column(Boolean, default=True)
    organization_id = Column(UUID(as_uuid=True))
    company_id = Column(UUID(as_uuid=True))


class ProfileAuditLog(Base):
    """Auditoría de cambios de perfil/rol/estado (CORE-USERS-01, R0-06).

    Registra QUIÉN cambió QUÉ, en qué tenant, valor previo y nuevo, y el resultado.
    NO se guardan secretos ni datos sensibles (ni contraseñas ni tokens): solo el
    nombre del campo y sus valores de identidad/rol/estado. Tabla propia aditiva
    (migración 0002), independiente del `cross_org_audit_log` de plataforma.
    """
    __tablename__ = "profile_audit_log"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    actor_id = Column(UUID(as_uuid=True))
    actor_role = Column(String)
    actor_organization_id = Column(UUID(as_uuid=True))
    actor_company_id = Column(UUID(as_uuid=True))
    target_user_id = Column(UUID(as_uuid=True))
    target_organization_id = Column(UUID(as_uuid=True))
    target_company_id = Column(UUID(as_uuid=True))
    action = Column(String, nullable=False)          # update_profile | change_role | toggle_active
    field = Column(String, nullable=False)           # full_name | role | is_active | department_id | ...
    old_value = Column(Text)
    new_value = Column(Text)
    result = Column(String, nullable=False, default="ok")   # ok | denied
    created_at = Column(DateTime(timezone=True), server_default=func.now())
