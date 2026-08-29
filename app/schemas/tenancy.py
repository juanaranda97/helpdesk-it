"""Schemas Pydantic para el módulo de tenancy."""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr


# ============================================================
# ORGANIZATIONS
# ============================================================
class OrganizationBase(BaseModel):
    name: str
    slug: str
    short_name: Optional[str] = None
    legal_name: Optional[str] = None
    org_type: str = "cliente"
    is_group: bool = False
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None
    country: str = "Paraguay"
    logo_url: Optional[str] = None
    primary_color: Optional[str] = "#8b5cf6"
    plan: str = "free"
    is_active: bool = True
    notes: Optional[str] = None


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationUpdate(BaseModel):
    name: Optional[str] = None
    short_name: Optional[str] = None
    legal_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    tax_id: Optional[str] = None
    address: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None
    plan: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class OrganizationOut(OrganizationBase):
    id: str
    created_at: datetime


class OrganizationSummary(BaseModel):
    """Vista enriquecida con conteos."""
    id: str
    name: str
    slug: str
    short_name: Optional[str] = None
    org_type: str
    is_group: bool
    is_active: bool
    plan: str
    company_count: int = 0
    user_count: int = 0
    active_user_count: int = 0


# ============================================================
# COMPANIES
# ============================================================
class CompanyBase(BaseModel):
    name: str
    legal_name: Optional[str] = None
    tax_id: Optional[str] = None
    industry: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    country: str = "Paraguay"
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    website: Optional[str] = None
    is_active: bool = True
    notes: Optional[str] = None


class CompanyCreate(CompanyBase):
    organization_id: str


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    legal_name: Optional[str] = None
    tax_id: Optional[str] = None
    industry: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    website: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None


class CompanyOut(CompanyBase):
    id: str
    organization_id: str
    created_at: datetime


class CompanySummary(BaseModel):
    id: str
    name: str
    industry: Optional[str] = None
    is_active: bool
    organization_name: str
    user_count: int = 0
    equipment_count: int = 0
    ticket_count: int = 0


# ============================================================
# CONTEXT (lo que devuelve /auth/me/context)
# ============================================================
class UserContext(BaseModel):
    user_id: str
    email: str
    full_name: str
    role: str

    # Tenancy
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None
    organization_slug: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None

    # Permisos calculados
    is_platform_user: bool = False
    can_access_all_orgs: bool = False
    can_manage_org: bool = False
    can_manage_assets: bool = False
    can_baja_user: bool = False
    can_access_vault: bool = False

    # Estado
    estado_laboral: str = "activo"


# ============================================================
# IMPERSONATE
# ============================================================
class ImpersonateRequest(BaseModel):
    organization_id: str
    company_id: Optional[str] = None
    reason: Optional[str] = Field(None, description="Motivo del acceso (para audit log)")


class ImpersonateResponse(BaseModel):
    success: bool
    organization_id: str
    organization_name: str
    company_id: Optional[str] = None
    message: str


# ============================================================
# AUDIT LOG
# ============================================================
class AuditLogEntry(BaseModel):
    id: str
    actor_id: str
    actor_email: str
    actor_role: Optional[str] = None
    target_organization_id: str
    target_organization_name: str
    target_company_id: Optional[str] = None
    target_resource: str
    target_resource_id: Optional[str] = None
    action: str
    ip_address: Optional[str] = None
    request_path: Optional[str] = None
    created_at: datetime
