"""
Sistema de permisos basado en roles.

Jerarquía de roles (de más privilegio a menos):

  platform_owner    → Vos + tu papá. Acceso TOTAL a todas las orgs.
  platform_admin    → Empleados de Ommeganet. Casi todo, menos vault personal de owners.
  org_owner         → Dueño/CEO de la org cliente.
  org_admin         → Admin IT del cliente.
  company_admin     → Admin de una empresa dentro de un grupo.
  manager           → Jefe de sector.
  supervisor        → Supervisor de tickets.
  tecnico           → Técnico IT.
  rrhh              → Recursos Humanos.
  usuario           → Empleado normal.

Roles legacy (compatibilidad con datos viejos):
  superadmin        → equivale a platform_owner
  admin             → equivale a org_admin
"""
from enum import Enum
from typing import Set


class Role(str, Enum):
    PLATFORM_OWNER = "platform_owner"
    PLATFORM_ADMIN = "platform_admin"
    ORG_OWNER = "org_owner"
    ORG_ADMIN = "org_admin"
    COMPANY_ADMIN = "company_admin"
    MANAGER = "manager"
    SUPERVISOR = "supervisor"
    TECNICO = "tecnico"
    RRHH = "rrhh"
    USUARIO = "usuario"
    # Legacy
    SUPERADMIN = "superadmin"
    ADMIN = "admin"


# Roles que tienen acceso de plataforma (Ommeganet)
PLATFORM_ROLES: Set[str] = {
    Role.PLATFORM_OWNER.value,
    Role.PLATFORM_ADMIN.value,
    Role.SUPERADMIN.value,  # legacy
}

# Roles que son owners de su org
ORG_OWNER_ROLES: Set[str] = {
    Role.PLATFORM_OWNER.value,
    Role.PLATFORM_ADMIN.value,
    Role.ORG_OWNER.value,
    Role.SUPERADMIN.value,
}

# Roles con permisos administrativos en su org
ORG_ADMIN_ROLES: Set[str] = ORG_OWNER_ROLES | {
    Role.ORG_ADMIN.value,
    Role.ADMIN.value,  # legacy
}

# Roles que pueden gestionar el inventario / licencias / proveedores
ASSET_MANAGEMENT_ROLES: Set[str] = ORG_ADMIN_ROLES | {
    Role.COMPANY_ADMIN.value,
    Role.TECNICO.value,
}

# Roles que pueden dar de baja usuarios
USER_LIFECYCLE_ROLES: Set[str] = ORG_ADMIN_ROLES | {
    Role.RRHH.value,
}

# Roles con acceso al vault (zero-knowledge)
VAULT_ROLES: Set[str] = {
    Role.PLATFORM_OWNER.value,
    Role.SUPERADMIN.value,  # legacy
}


def is_platform_role(role: str) -> bool:
    """¿Es un rol de plataforma (Ommeganet)?"""
    return role in PLATFORM_ROLES


def can_access_all_orgs(role: str) -> bool:
    """¿Puede ver TODAS las organizaciones?"""
    return role in PLATFORM_ROLES


def can_manage_org(role: str) -> bool:
    """¿Puede administrar su organización?"""
    return role in ORG_ADMIN_ROLES


def can_manage_assets(role: str) -> bool:
    """¿Puede gestionar inventario/licencias/proveedores?"""
    return role in ASSET_MANAGEMENT_ROLES


def can_baja_user(role: str) -> bool:
    """¿Puede dar de baja usuarios?"""
    return role in USER_LIFECYCLE_ROLES


def can_access_vault(role: str) -> bool:
    """¿Tiene acceso al vault?"""
    return role in VAULT_ROLES


def role_priority(role: str) -> int:
    """Devuelve la jerarquía del rol (más alto = más privilegio)."""
    priorities = {
        Role.PLATFORM_OWNER.value: 100,
        Role.SUPERADMIN.value: 100,  # legacy
        Role.PLATFORM_ADMIN.value: 90,
        Role.ORG_OWNER.value: 80,
        Role.ORG_ADMIN.value: 70,
        Role.ADMIN.value: 70,  # legacy
        Role.COMPANY_ADMIN.value: 60,
        Role.MANAGER.value: 50,
        Role.SUPERVISOR.value: 40,
        Role.TECNICO.value: 30,
        Role.RRHH.value: 30,
        Role.USUARIO.value: 10,
    }
    return priorities.get(role, 0)


def can_assign_role(actor_role: str, target_role: str) -> bool:
    """Un usuario solo puede asignar roles de menor o igual jerarquía que el suyo."""
    return role_priority(actor_role) >= role_priority(target_role)
