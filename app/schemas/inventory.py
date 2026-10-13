"""Schemas Pydantic del módulo de Inventario (ITAM)."""
from datetime import datetime, date
from typing import Optional, List, Dict, Literal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict


# ============================================================
# Vocabulario REAL (coincide con los CHECK de public.equipment).
# NO modificar sin cambiar el CHECK en Postgres (pendiente de aprobación).
#   equipment_tipo_check   -> TIPOS
#   equipment_estado_check -> ESTADOS
# La UI muestra nombres amigables (TIPO_LABELS en el frontend) pero envía
# estos códigos a la API.
# ============================================================
TIPOS = ["NB", "PC", "Monitor", "Mouse", "Teclado", "Auricular", "Impresora", "Router", "Switch", "Servidor", "Otro"]
ESTADOS = ["disponible", "asignado", "reparacion", "baja", "extraviado"]

TipoType = Literal["NB", "PC", "Monitor", "Mouse", "Teclado", "Auricular", "Impresora", "Router", "Switch", "Servidor", "Otro"]
EstadoType = Literal["disponible", "asignado", "reparacion", "baja", "extraviado"]


# ============================================================
# EQUIPMENT
# ============================================================
class EquipmentBase(BaseModel):
    """Campos editables manualmente de un activo."""
    codigo_interno: Optional[str] = Field(default=None, max_length=100)
    tipo: TipoType                     # validado contra el CHECK real (422 si inválido)
    marca: Optional[str] = None
    modelo: Optional[str] = None
    serial: Optional[str] = None
    nro_producto: Optional[str] = None

    fecha_fabricacion: Optional[date] = None
    fecha_compra: Optional[date] = None
    fecha_garantia_hasta: Optional[date] = None

    procesador: Optional[str] = None
    ram: Optional[str] = None
    tipo_ram: Optional[str] = None
    drive: Optional[str] = None
    tipo_drive: Optional[str] = None
    marca_drive: Optional[str] = None
    sistema_operativo: Optional[str] = None
    version_so: Optional[str] = None
    so_instalado_el: Optional[date] = None
    antivirus: Optional[str] = None
    antivirus_version: Optional[str] = None
    antivirus_fecha: Optional[date] = None
    onedrive_estado: Optional[str] = None
    monitor_marca: Optional[str] = None
    monitor_tamano: Optional[str] = None
    mouse_modelo: Optional[str] = None
    tiene_auricular: Optional[bool] = None
    anydesk_id: Optional[str] = None

    estado: Optional[EstadoType] = None     # default 'disponible' en la DB
    ubicacion: Optional[str] = None
    notas: Optional[str] = None

    # Ubicación estructurada (FK en la DB). El tenant NO se acepta por acá:
    # organization_id / company_id se resuelven en backend desde el contexto.
    branch_id: Optional[UUID] = None
    physical_location_id: Optional[UUID] = None


class EquipmentCreate(EquipmentBase):
    pass


class EquipmentUpdate(BaseModel):
    """Todos opcionales. El tenant nunca se actualiza desde el cliente."""
    codigo_interno: Optional[str] = None
    tipo: Optional[TipoType] = None
    marca: Optional[str] = None
    modelo: Optional[str] = None
    serial: Optional[str] = None
    nro_producto: Optional[str] = None
    fecha_fabricacion: Optional[date] = None
    fecha_compra: Optional[date] = None
    fecha_garantia_hasta: Optional[date] = None
    procesador: Optional[str] = None
    ram: Optional[str] = None
    tipo_ram: Optional[str] = None
    drive: Optional[str] = None
    tipo_drive: Optional[str] = None
    marca_drive: Optional[str] = None
    sistema_operativo: Optional[str] = None
    version_so: Optional[str] = None
    so_instalado_el: Optional[date] = None
    antivirus: Optional[str] = None
    antivirus_version: Optional[str] = None
    antivirus_fecha: Optional[date] = None
    onedrive_estado: Optional[str] = None
    monitor_marca: Optional[str] = None
    monitor_tamano: Optional[str] = None
    mouse_modelo: Optional[str] = None
    tiene_auricular: Optional[bool] = None
    anydesk_id: Optional[str] = None
    estado: Optional[EstadoType] = None
    ubicacion: Optional[str] = None
    notas: Optional[str] = None
    branch_id: Optional[UUID] = None
    physical_location_id: Optional[UUID] = None


class AssignableUser(BaseModel):
    """Datos mínimos de un usuario elegible para asignación (sin exponer de más)."""
    id: UUID
    full_name: Optional[str] = None


class CurrentAssignment(BaseModel):
    """Asignación activa (si la hay) de un activo."""
    assignment_id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    assigned_at: datetime
    assigned_by: Optional[UUID] = None


class EquipmentListItem(BaseModel):
    """Fila compacta para la tabla del inventario."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    codigo_interno: Optional[str] = None
    tipo: str
    marca: Optional[str] = None
    modelo: Optional[str] = None
    serial: Optional[str] = None
    estado: str
    ubicacion: Optional[str] = None
    branch_id: Optional[UUID] = None
    company_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    assigned_user_id: Optional[UUID] = None
    assigned_user_name: Optional[str] = None
    updated_at: Optional[datetime] = None
    # Procedencia del registro. Hoy SIEMPRE "manual": no existe agente/discovery.
    data_source: str = "manual"


class EquipmentResponse(EquipmentBase):
    """Ficha completa de un activo."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: Optional[UUID] = None
    company_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    current_assignment: Optional[CurrentAssignment] = None
    # Hoy todo dato es manual; cuando exista discovery, por campo/registro.
    data_source: str = "manual"


# ============================================================
# ASSIGNMENTS
# ============================================================
class AssignmentCreate(BaseModel):
    user_id: UUID
    notas: Optional[str] = None


class AssignmentReturn(BaseModel):
    returned_reason: Optional[str] = None
    notas: Optional[str] = None


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    equipment_id: UUID
    user_id: UUID
    user_name: Optional[str] = None
    assigned_at: datetime
    returned_at: Optional[datetime] = None
    assigned_by: Optional[UUID] = None
    assigned_by_name: Optional[str] = None
    returned_reason: Optional[str] = None
    notas: Optional[str] = None


# ============================================================
# STATS / META
# ============================================================
class InventoryStats(BaseModel):
    total: int = 0
    asignados: int = 0
    disponibles: int = 0
    en_reparacion: int = 0
    de_baja: int = 0
    por_estado: Dict[str, int] = {}
    por_tipo: Dict[str, int] = {}


class BranchLite(BaseModel):
    id: UUID
    name: str


class InventoryMeta(BaseModel):
    estados: List[str] = ESTADOS
    tipos: List[str] = TIPOS
    marcas: List[str] = []           # distinct desde los datos del tenant
    branches: List[BranchLite] = []  # sucursales de la org activa

