"""Cambios al perfil: propuestas del integrante, aprobación de la coordinación
y edición administrativa directa (brief 1.3, 3.2 y 3.6).

Todo cambio deja rastro en `member_history` y en `audit_log`. Al aprobar un
cambio o editar un campo a mano, se registra un bloqueo en `field_locks` para
que una importación posterior no lo sobrescriba.

PENDIENTE (brief 4.7): lista exacta de campos que aprueba la coordinación.
Hoy son los de `CHANGE_FIELDS` en app/models.py.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.models import (
    Consent,
    FieldLock,
    Institution,
    Invitation,
    Member,
    MemberChangeRequest,
    MemberHistory,
    MemberPublicProfile,
    Photo,
    ResearchLine,
)
from app.services import audit, invitations
from app.services.importer import _refresh_names
from app.services.photos import process_photo
from app.services.storage import get_storage

SUPERSEDED = "Sustituida por una propuesta más reciente del integrante."


class ProfileError(Exception):
    def __init__(self, codigo: str, mensaje: str, status: int = 422) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.status = status


# --------------------------------------------------------------------------- #
#  Lectura
# --------------------------------------------------------------------------- #


def ensure_profile(member: Member) -> MemberPublicProfile:
    if member.perfil is None:
        member.perfil = MemberPublicProfile(version=1, lineas_propuestas=[])
    return member.perfil


def active_consent(db: Session, member_id: uuid.UUID) -> Consent | None:
    return db.scalar(
        select(Consent)
        .where(Consent.member_id == member_id, Consent.aprobado_en.is_not(None), Consent.revocado_en.is_(None))
        .order_by(Consent.aprobado_en.desc())
    )


def current_values(db: Session, member: Member) -> dict[str, Any]:
    perfil = ensure_profile(member)
    return {
        "biografia": perfil.biografia,
        "perfil_profesional": perfil.perfil_profesional,
        "lineas_propuestas": list(perfil.lineas_propuestas or []),
        "correo_publico": active_consent(db, member.id) is not None,
        "foto": str(perfil.foto_id) if perfil.foto_id else None,
    }


def valid_line_ids(db: Session) -> set[str]:
    return set(db.scalars(select(ResearchLine.id)))


# --------------------------------------------------------------------------- #
#  Propuestas del integrante (con enlace válido)
# --------------------------------------------------------------------------- #


def _supersede_pending(db: Session, member_id: uuid.UUID, campo: str) -> None:
    pending = db.scalars(
        select(MemberChangeRequest).where(
            MemberChangeRequest.member_id == member_id,
            MemberChangeRequest.campo == campo,
            MemberChangeRequest.estado == "pendiente",
        )
    ).all()
    for request in pending:
        request.estado = "rechazada"
        request.motivo_rechazo = SUPERSEDED
        request.revisado_en = clock.now()
        if campo == "foto":
            _discard_photo(db, request)
        if campo == "correo_publico":
            db.execute(
                update(Consent)
                .where(Consent.change_request_id == request.id, Consent.aprobado_en.is_(None))
                .values(revocado_en=clock.now(), nota=SUPERSEDED)
            )


def submit_changes(db: Session, *, token: str, propuesta: dict[str, Any], ip: str | None = None) -> int:
    """Crea una solicitud por campo modificado y consume el enlace. Devuelve cuántas se crearon."""
    invitation = invitations.get_valid(db, token, for_update=True)
    member = invitation.member
    actuales = current_values(db, member)
    settings = get_settings()

    if "lineas_propuestas" in propuesta and propuesta["lineas_propuestas"] is not None:
        lineas = sorted(set(propuesta["lineas_propuestas"]))
        desconocidas = set(lineas) - valid_line_ids(db)
        if desconocidas:
            raise ProfileError("linea-desconocida", f"Líneas no válidas: {', '.join(sorted(desconocidas))}.")
        propuesta["lineas_propuestas"] = [x for x in lineas if x != member.linea_investigacion_id]

    version_texto = propuesta.pop("version_texto_consentimiento", None)
    if propuesta.get("correo_publico") is True and version_texto != settings.consent_text_version:
        raise ProfileError(
            "consentimiento-desactualizado",
            "El texto de autorización cambió. Vuelve a cargar la página y revisa el texto vigente.",
        )

    creadas = 0
    for campo, valor in propuesta.items():
        if valor == actuales[campo]:
            continue
        _supersede_pending(db, member.id, campo)
        request = MemberChangeRequest(
            member_id=member.id,
            invitation_id=invitation.id,
            campo=campo,
            valor_anterior=actuales[campo],
            valor_propuesto=valor,
        )
        db.add(request)
        db.flush()
        if campo == "correo_publico" and valor is True:
            db.add(
                Consent(
                    member_id=member.id,
                    version_texto=settings.consent_text_version,
                    texto=settings.consent_text,
                    change_request_id=request.id,
                )
            )
        creadas += 1

    pending_photo = db.scalar(
        select(MemberChangeRequest.id).where(
            MemberChangeRequest.invitation_id == invitation.id,
            MemberChangeRequest.campo == "foto",
            MemberChangeRequest.estado == "pendiente",
        )
    )
    if creadas == 0 and pending_photo is None:
        raise ProfileError("sin-cambios", "No hay cambios respecto de tu perfil publicado.")

    invitations.consume(invitation)
    audit.record(db, None, "invitacion.enviar-cambios", "members", member.id, {"solicitudes": creadas}, ip)
    db.commit()
    return creadas


def submit_photo(db: Session, *, token: str, raw: bytes, ip: str | None = None) -> Photo:
    invitation = invitations.get_valid(db, token, for_update=True)
    member = invitation.member
    processed = process_photo(raw)

    _supersede_pending(db, member.id, "foto")
    version = (
        db.scalar(select(Photo.version).where(Photo.member_id == member.id).order_by(Photo.version.desc())) or 0
    ) + 1
    get_storage().put(processed.clave, processed.data, processed.content_type)
    photo = Photo(
        member_id=member.id,
        clave_almacenamiento=processed.clave,
        estado="pendiente",
        version=version,
        content_type=processed.content_type,
        ancho=processed.ancho,
        alto=processed.alto,
        bytes=len(processed.data),
        invitation_id=invitation.id,
    )
    db.add(photo)
    db.flush()
    db.add(
        MemberChangeRequest(
            member_id=member.id,
            invitation_id=invitation.id,
            campo="foto",
            valor_anterior=current_values(db, member)["foto"],
            valor_propuesto=str(photo.id),
        )
    )
    audit.record(db, None, "invitacion.subir-foto", "photos", photo.id, {"integrante": member.slug}, ip)
    db.commit()
    return photo


def _discard_photo(db: Session, request: MemberChangeRequest) -> None:
    photo = db.get(Photo, uuid.UUID(str(request.valor_propuesto))) if request.valor_propuesto else None
    if photo and photo.estado == "pendiente":
        photo.estado = "rechazada"
        get_storage().delete(photo.clave_almacenamiento)


# --------------------------------------------------------------------------- #
#  Revisión de la coordinación
# --------------------------------------------------------------------------- #


def _lock(db: Session, member_id: uuid.UUID, campo: str, origen: str, user_id: uuid.UUID | None,
          request_id: uuid.UUID | None = None) -> None:  # fmt: skip
    stmt = pg_insert(FieldLock).values(
        member_id=member_id,
        campo=campo,
        origen=origen,
        bloqueado_por=user_id,
        bloqueado_en=clock.now(),
        change_request_id=request_id,
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[FieldLock.member_id, FieldLock.campo],
            set_={
                "origen": origen,
                "bloqueado_por": user_id,
                "bloqueado_en": clock.now(),
                "change_request_id": request_id,
            },
        )
    )


def _get_pending(db: Session, request_id: uuid.UUID) -> MemberChangeRequest:
    request = db.scalar(
        select(MemberChangeRequest).where(MemberChangeRequest.id == request_id).with_for_update(of=MemberChangeRequest)
    )
    if request is None:
        raise ProfileError("solicitud-no-encontrada", "La solicitud no existe.", 404)
    if request.estado != "pendiente":
        raise ProfileError("solicitud-ya-revisada", f"La solicitud ya fue {request.estado}.", 409)
    return request


def approve(db: Session, *, request_id: uuid.UUID, user_id: uuid.UUID, ip: str | None = None) -> MemberChangeRequest:
    request = _get_pending(db, request_id)
    member = request.member
    perfil = ensure_profile(member)
    anterior = current_values(db, member)[request.campo]
    now = clock.now()

    if request.campo in ("biografia", "perfil_profesional", "lineas_propuestas"):
        setattr(perfil, request.campo, request.valor_propuesto)
        perfil.version += 1
    elif request.campo == "correo_publico":
        db.execute(
            update(Consent)
            .where(Consent.member_id == member.id, Consent.aprobado_en.is_not(None), Consent.revocado_en.is_(None))
            .values(revocado_en=now)
        )
        if request.valor_propuesto is True:
            consent = db.scalar(select(Consent).where(Consent.change_request_id == request.id))
            if consent is None:  # pragma: no cover - defensa
                raise ProfileError("consentimiento-faltante", "No se encontró la autorización del integrante.", 409)
            consent.aprobado_en = now
            consent.aprobado_por = user_id
            consent.revocado_en = None
    elif request.campo == "foto":
        photo = db.get(Photo, uuid.UUID(str(request.valor_propuesto)))
        if photo is None or photo.estado != "pendiente":
            raise ProfileError("foto-no-disponible", "La fotografía ya no está disponible.", 409)
        if perfil.foto_id:
            previous = db.get(Photo, perfil.foto_id)
            if previous and previous.estado == "aprobada":
                previous.estado = "reemplazada"
        photo.estado = "aprobada"
        photo.aprobado_en = now
        photo.aprobado_por = user_id
        perfil.foto_id = photo.id
        perfil.version += 1

    request.estado = "aprobada"
    request.revisado_por = user_id
    request.revisado_en = now
    db.add(
        MemberHistory(
            member_id=member.id,
            campo=request.campo,
            valor_anterior=anterior,
            valor_nuevo=request.valor_propuesto,
            origen="aprobacion",
            referencia=str(request.id),
            usuario_id=user_id,
        )
    )
    _lock(db, member.id, request.campo, "aprobacion", user_id, request.id)
    audit.record(db, user_id, "solicitud.aprobar", "member_change_requests", request.id, {"campo": request.campo}, ip)
    db.commit()
    return request


def reject(
    db: Session, *, request_id: uuid.UUID, user_id: uuid.UUID, motivo: str, ip: str | None = None
) -> MemberChangeRequest:
    request = _get_pending(db, request_id)
    now = clock.now()
    if request.campo == "foto":
        _discard_photo(db, request)
    if request.campo == "correo_publico":
        db.execute(
            update(Consent)
            .where(Consent.change_request_id == request.id, Consent.aprobado_en.is_(None))
            .values(revocado_en=now, nota=f"Rechazada: {motivo}")
        )
    request.estado = "rechazada"
    request.motivo_rechazo = motivo
    request.revisado_por = user_id
    request.revisado_en = now
    actual = current_values(db, request.member)[request.campo]
    db.add(
        MemberHistory(
            member_id=request.member_id,
            campo=request.campo,
            valor_anterior=actual,
            valor_nuevo=actual,
            origen="rechazo",
            referencia=str(request.id),
            usuario_id=user_id,
        )
    )
    audit.record(
        db,
        user_id,
        "solicitud.rechazar",
        "member_change_requests",
        request.id,
        {"campo": request.campo, "motivo": motivo},
        ip,
    )
    db.commit()
    return request


# --------------------------------------------------------------------------- #
#  Edición administrativa directa
# --------------------------------------------------------------------------- #

ADMIN_MEMBER_FIELDS = {
    "tratamiento", "nombres", "apellidos", "cvu_tecnm", "cvu_conahcyt", "correo_institucional",
    "telefono_particular", "correo_personal", "institucion_id", "rol", "linea_investigacion_id",
    "scopus_author_id", "activo", "es_publico",
}  # fmt: skip
ADMIN_PROFILE_FIELDS = {"biografia", "perfil_profesional", "lineas_propuestas"}


def admin_patch(
    db: Session, *, member: Member, cambios: dict[str, Any], user_id: uuid.UUID, ip: str | None = None
) -> list[str]:
    """Aplica los cambios, los registra en el historial y bloquea los campos. Devuelve los campos modificados."""
    if "institucion_id" in cambios and cambios["institucion_id"] and not db.get(Institution, cambios["institucion_id"]):
        raise ProfileError("institucion-desconocida", "La institución no existe en el catálogo.")
    lines = valid_line_ids(db)
    if cambios.get("linea_investigacion_id") and cambios["linea_investigacion_id"] not in lines:
        raise ProfileError("linea-desconocida", "La línea de investigación no existe.")
    if cambios.get("lineas_propuestas") and set(cambios["lineas_propuestas"]) - lines:
        raise ProfileError("linea-desconocida", "Alguna de las líneas propuestas no existe.")

    perfil = ensure_profile(member)
    modificados: list[str] = []
    for campo, nuevo in cambios.items():
        target: Any = perfil if campo in ADMIN_PROFILE_FIELDS else member
        if campo not in ADMIN_MEMBER_FIELDS | ADMIN_PROFILE_FIELDS:
            continue
        anterior = getattr(target, campo)
        if campo == "lineas_propuestas":
            nuevo = sorted(set(nuevo or []))
        if anterior == nuevo:
            continue
        setattr(target, campo, nuevo)
        if campo == "activo":
            member.desactivado_en = None if nuevo else clock.now()
        if target is perfil:
            perfil.version += 1
        db.add(
            MemberHistory(
                member_id=member.id,
                campo=campo,
                valor_anterior=anterior,
                valor_nuevo=nuevo,
                origen="edicion_admin",
                usuario_id=user_id,
            )
        )
        _lock(db, member.id, campo, "edicion_admin", user_id)
        modificados.append(campo)

    if {"nombres", "apellidos"} & set(modificados):
        _refresh_names(member)
    if modificados:
        audit.record(db, user_id, "integrante.editar", "members", member.id, {"campos": modificados}, ip)
    db.commit()
    return modificados


def pending_invitation_photo(db: Session, invitation: Invitation) -> Photo | None:
    return db.scalar(
        select(Photo)
        .where(Photo.invitation_id == invitation.id, Photo.estado == "pendiente")
        .order_by(Photo.version.desc())
    )
