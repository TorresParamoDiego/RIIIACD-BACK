"""Roles y permisos del panel administrativo.

PROVISIONAL — PENDIENTE (brief 4.6): cuántos roles y cuáles. Esta tabla es
el único lugar que hay que cambiar cuando la coordinación lo defina.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    MEMBERS_READ = "integrantes.consultar"
    """Ver datos administrativos (CVU, teléfono, correo personal). Cada consulta se audita."""
    MEMBERS_WRITE = "integrantes.editar"
    IMPORTS = "importaciones.gestionar"
    REVIEW = "solicitudes.revisar"
    INVITATIONS = "invitaciones.generar"
    AUDIT_READ = "auditoria.consultar"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "coordinacion": frozenset(Permission),
    "administracion_tecnica": frozenset({Permission.MEMBERS_READ, Permission.IMPORTS, Permission.AUDIT_READ}),
    "consulta": frozenset({Permission.MEMBERS_READ}),
}


def has_permission(rol: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(rol, frozenset())
