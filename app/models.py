"""Modelo de datos (brief fase 2, sección 3.2).

Convenciones:
- Fechas en UTC con zona horaria.
- Los estados se guardan como texto con restricción CHECK, para que la base
  de datos rechace valores inválidos aunque falle la validación de la API.
- Los datos administrativos (CVU, teléfono, correo personal, archivos) viven
  solo en `members`. El sitio público lee únicamente lo que expone
  `app.services.public` mediante una lista blanca.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _check_in(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class Timestamped:
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --------------------------------------------------------------------------- #
#  Catálogos
# --------------------------------------------------------------------------- #

INSTITUTION_TYPES = ("tecnologico", "centro-investigacion", "universidad")


class Institution(Timestamped, Base):
    __tablename__ = "institutions"
    __table_args__ = (CheckConstraint(_check_in("tipo", INSTITUTION_TYPES), name="tipo_valido"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(300))
    siglas: Mapped[str | None] = mapped_column(String(40))
    tipo: Mapped[str] = mapped_column(String(30))
    alias: Mapped[list[str]] = mapped_column(JsonType, default=list)
    """Claves normalizadas alternativas (ver normalization.institution_key)."""
    logo_url: Mapped[str | None] = mapped_column(String(500))
    es_demostrativa: Mapped[bool] = mapped_column(Boolean, default=False)


class ResearchLine(Timestamped, Base):
    __tablename__ = "research_lines"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    numero: Mapped[int] = mapped_column(Integer, unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    nombre: Mapped[str] = mapped_column(String(300))
    nombre_corto: Mapped[str] = mapped_column(String(120))
    descripcion: Mapped[str] = mapped_column(Text)
    ejes: Mapped[list[str]] = mapped_column(JsonType, default=list)
    palabras_clave: Mapped[list[str]] = mapped_column(JsonType, default=list)
    responsable_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL", use_alter=True)
    )


# --------------------------------------------------------------------------- #
#  Integrantes
# --------------------------------------------------------------------------- #


class Member(Timestamped, Base):
    """Registro administrativo completo. Nunca se serializa directamente al sitio público."""

    __tablename__ = "members"
    __table_args__ = (
        # Identificador principal y secundario: únicos cuando existen.
        Index("uq_members_cvu_tecnm", "cvu_tecnm", unique=True, postgresql_where=text("cvu_tecnm IS NOT NULL")),
        Index(
            "uq_members_correo_institucional",
            "correo_institucional",
            unique=True,
            postgresql_where=text("correo_institucional IS NOT NULL"),
        ),
        Index("ix_members_cvu_conahcyt", "cvu_conahcyt"),
        Index("ix_members_clave_nombre", "clave_nombre"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    slug: Mapped[str] = mapped_column(String(160), unique=True)
    """Identificador público estable (p. ej. m-ana-perez-lopez). Es el `id` de PublicMember."""
    numero: Mapped[int | None] = mapped_column(Integer)
    tratamiento: Mapped[str | None] = mapped_column(String(10))
    nombres: Mapped[str] = mapped_column(String(200))
    apellidos: Mapped[str] = mapped_column(String(200))
    nombre_completo: Mapped[str] = mapped_column(String(400))
    clave_nombre: Mapped[str] = mapped_column(String(400))
    """Nombre normalizado para detectar duplicados."""

    # Identificadores (administrativos)
    cvu_tecnm: Mapped[str | None] = mapped_column(String(40))
    cvu_conahcyt: Mapped[str | None] = mapped_column(String(40))
    correo_institucional: Mapped[str | None] = mapped_column(String(254))

    # Datos personales (administrativos)
    telefono_particular: Mapped[str | None] = mapped_column(String(40))
    correo_personal: Mapped[str | None] = mapped_column(String(254))
    archivo_cvu_tecnm: Mapped[str | None] = mapped_column(String(300))
    archivo_cvu_conahcyt: Mapped[str | None] = mapped_column(String(300))
    archivo_carta_compromiso: Mapped[str | None] = mapped_column(String(300))
    scopus_author_id: Mapped[str | None] = mapped_column(String(40))
    infraestructura: Mapped[str | None] = mapped_column(Text)
    programas_academicos: Mapped[str | None] = mapped_column(Text)

    # Datos de directorio
    institucion_texto: Mapped[str | None] = mapped_column(String(300))
    institucion_id: Mapped[str | None] = mapped_column(ForeignKey("institutions.id", ondelete="SET NULL"))
    rol: Mapped[str | None] = mapped_column(String(120))
    linea_investigacion_texto: Mapped[str | None] = mapped_column(Text)
    linea_investigacion_id: Mapped[str | None] = mapped_column(ForeignKey("research_lines.id", ondelete="SET NULL"))

    # Estado
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    """Las personas que desaparecen del Excel se desactivan; nunca se eliminan."""
    desactivado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    es_publico: Mapped[bool] = mapped_column(Boolean, default=True)
    """La coordinación puede ocultar una ficha sin desactivar a la persona."""
    origen: Mapped[str] = mapped_column(String(20), default="importacion")
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    perfil: Mapped[MemberPublicProfile | None] = relationship(back_populates="member", uselist=False, lazy="joined")
    institucion: Mapped[Institution | None] = relationship(lazy="joined")


class MemberPublicProfile(Base):
    """Campos públicos editables por el integrante, ya aprobados por la coordinación."""

    __tablename__ = "member_public_profile"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    biografia: Mapped[str | None] = mapped_column(String(1000))
    perfil_profesional: Mapped[str | None] = mapped_column(String(500))
    lineas_propuestas: Mapped[list[str]] = mapped_column(JsonType, default=list)
    foto_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("photos.id", ondelete="SET NULL", use_alter=True))
    actualizado_en: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    member: Mapped[Member] = relationship(back_populates="perfil")


CHANGE_FIELDS = ("biografia", "perfil_profesional", "lineas_propuestas", "correo_publico", "foto")
CHANGE_STATES = ("pendiente", "aprobada", "rechazada")


class MemberChangeRequest(Timestamped, Base):
    __tablename__ = "member_change_requests"
    __table_args__ = (
        CheckConstraint(_check_in("campo", CHANGE_FIELDS), name="campo_valido"),
        CheckConstraint(_check_in("estado", CHANGE_STATES), name="estado_valido"),
        Index("ix_member_change_requests_estado", "estado"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"))
    campo: Mapped[str] = mapped_column(String(40))
    valor_anterior: Mapped[Any] = mapped_column(JsonType, nullable=True)
    valor_propuesto: Mapped[Any] = mapped_column(JsonType, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    motivo_rechazo: Mapped[str | None] = mapped_column(Text)
    revisado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    revisado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    member: Mapped[Member] = relationship(lazy="joined")


class FieldLock(Base):
    """Campo protegido: una importación posterior no lo sobrescribe."""

    __tablename__ = "field_locks"

    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    campo: Mapped[str] = mapped_column(String(60), primary_key=True)
    origen: Mapped[str] = mapped_column(String(30))
    """'aprobacion' (cambio del integrante aprobado) o 'edicion_admin' (edición directa)."""
    bloqueado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    bloqueado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("member_change_requests.id", ondelete="SET NULL")
    )


class MemberHistory(Timestamped, Base):
    """Bitácora por campo de todo cambio a un integrante."""

    __tablename__ = "member_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    campo: Mapped[str] = mapped_column(String(60))
    valor_anterior: Mapped[Any] = mapped_column(JsonType, nullable=True)
    valor_nuevo: Mapped[Any] = mapped_column(JsonType, nullable=True)
    origen: Mapped[str] = mapped_column(String(30))
    """'alta', 'importacion', 'aprobacion', 'edicion_admin', 'desactivacion', 'reactivacion'."""
    referencia: Mapped[str | None] = mapped_column(String(80))
    """Id del lote de importación o de la solicitud de cambio."""
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))


# --------------------------------------------------------------------------- #
#  Importación del Excel
# --------------------------------------------------------------------------- #

BATCH_STATES = ("revision", "aplicado", "descartado")
ROW_RESULTS = ("nuevo", "cambiado", "sin_cambios", "duplicado", "desactivado", "omitido")


class ImportBatch(Timestamped, Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(_check_in("estado", BATCH_STATES), name="estado_valido"),
        # Idempotencia: el mismo archivo (mismo hash) solo se aplica una vez.
        Index(
            "uq_import_batches_hash_aplicado",
            "archivo_hash",
            unique=True,
            postgresql_where=text("estado = 'aplicado'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    archivo_hash: Mapped[str] = mapped_column(String(64), index=True)
    """SHA-256 del archivo. El archivo en sí no se guarda (PENDIENTE brief 4.5: retención)."""
    nombre_archivo: Mapped[str] = mapped_column(String(300))
    subido_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    estado: Mapped[str] = mapped_column(String(20), default="revision")
    resumen: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    aplicado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aplicado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))

    filas: Mapped[list[ImportRowResult]] = relationship(
        back_populates="batch", cascade="all, delete-orphan", order_by="ImportRowResult.orden"
    )


class ImportRowResult(Base):
    __tablename__ = "import_row_results"
    __table_args__ = (CheckConstraint(_check_in("resultado", ROW_RESULTS), name="resultado_valido"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    orden: Mapped[int] = mapped_column(Integer)
    fila: Mapped[int | None] = mapped_column(Integer)
    """Fila del Excel (encabezado = 1). Nula en las desactivaciones."""
    resultado: Mapped[str] = mapped_column(String(20))
    member_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("members.id", ondelete="SET NULL"))
    coincidencia: Mapped[str | None] = mapped_column(String(30))
    """'cvu_tecnm', 'correo_institucional' o nulo."""
    diferencias: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    advertencias: Mapped[list[dict[str, Any]]] = mapped_column(JsonType, default=list)
    datos: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    """Fila ya normalizada; permite confirmar el lote sin conservar el archivo."""

    batch: Mapped[ImportBatch] = relationship(back_populates="filas")


# --------------------------------------------------------------------------- #
#  Invitaciones, fotografías y autorizaciones
# --------------------------------------------------------------------------- #


class Invitation(Timestamped, Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    """SHA-256 del token. El token en claro nunca se guarda."""
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    usado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canal: Mapped[str] = mapped_column(String(20))
    """'smtp' o 'manual'."""
    creado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))

    member: Mapped[Member] = relationship(lazy="joined")


PHOTO_STATES = ("pendiente", "aprobada", "reemplazada", "rechazada")


class Photo(Timestamped, Base):
    __tablename__ = "photos"
    __table_args__ = (CheckConstraint(_check_in("estado", PHOTO_STATES), name="estado_valido"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    clave_almacenamiento: Mapped[str] = mapped_column(String(200), unique=True)
    estado: Mapped[str] = mapped_column(String(20), default="pendiente")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_type: Mapped[str] = mapped_column(String(50))
    ancho: Mapped[int] = mapped_column(Integer)
    alto: Mapped[int] = mapped_column(Integer)
    bytes: Mapped[int] = mapped_column(Integer)
    invitation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"))
    aprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aprobado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))


class Consent(Timestamped, Base):
    """Autorización para publicar el correo institucional. Sin una aprobada y vigente, no se publica."""

    __tablename__ = "consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    member_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    version_texto: Mapped[str] = mapped_column(String(40))
    texto: Mapped[str] = mapped_column(Text)
    """Copia literal del texto aceptado, por si cambia la versión vigente."""
    change_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("member_change_requests.id", ondelete="SET NULL")
    )
    aprobado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aprobado_por: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    nota: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
#  Contacto, administración y auditoría
# --------------------------------------------------------------------------- #


class ContactMessage(Timestamped, Base):
    __tablename__ = "contact_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    nombre: Mapped[str] = mapped_column(String(200))
    correo: Mapped[str] = mapped_column(String(254))
    asunto: Mapped[str] = mapped_column(String(200))
    mensaje: Mapped[str] = mapped_column(Text)
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    """Hash con sal de la IP: sirve para detectar abuso sin guardar la dirección."""
    atendido: Mapped[bool] = mapped_column(Boolean, default=False)


ADMIN_ROLES = ("coordinacion", "administracion_tecnica", "consulta")


class AdminUser(Timestamped, Base):
    __tablename__ = "admin_users"
    __table_args__ = (CheckConstraint(_check_in("rol", ADMIN_ROLES), name="rol_valido"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    correo: Mapped[str] = mapped_column(String(254), unique=True)
    nombre: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    rol: Mapped[str] = mapped_column(String(30))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class AdminSession(Timestamped, Base):
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expira_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revocado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[AdminUser] = relationship(lazy="joined")


class AuditLog(Timestamped, Base):
    """Acciones administrativas y toda consulta o cambio sobre datos personales."""

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entidad", "entidad", "entidad_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    usuario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    accion: Mapped[str] = mapped_column(String(80))
    entidad: Mapped[str] = mapped_column(String(60))
    entidad_id: Mapped[str | None] = mapped_column(String(80))
    detalle: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    ip_hash: Mapped[str | None] = mapped_column(String(64))


__all__ = [
    "AdminSession",
    "AdminUser",
    "AuditLog",
    "Consent",
    "ContactMessage",
    "FieldLock",
    "ImportBatch",
    "ImportRowResult",
    "Institution",
    "Invitation",
    "Member",
    "MemberChangeRequest",
    "MemberHistory",
    "MemberPublicProfile",
    "Photo",
    "ResearchLine",
]
