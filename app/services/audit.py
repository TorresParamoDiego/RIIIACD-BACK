"""Bitácora de acciones administrativas (`audit_log`).

Toda consulta o cambio sobre datos personales pasa por `record()`.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditLog


def hash_ip(ip: str | None) -> str | None:
    """Hash con sal de la IP: permite correlacionar abuso sin guardar la dirección."""
    if not ip:
        return None
    return hashlib.sha256(f"{get_settings().secret_key}:{ip}".encode()).hexdigest()


def record(
    db: Session,
    user_id: uuid.UUID | None,
    accion: str,
    entidad: str,
    entidad_id: uuid.UUID | str | None,
    detalle: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            usuario_id=user_id,
            accion=accion,
            entidad=entidad,
            entidad_id=str(entidad_id) if entidad_id is not None else None,
            detalle=detalle or {},
            ip_hash=hash_ip(ip),
        )
    )
