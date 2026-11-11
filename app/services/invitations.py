"""Enlaces únicos de actualización de perfil (brief 1.3).

- Válidos 24 horas (INVITATION_TTL_HOURS) y de un solo uso.
- Solo se guarda el SHA-256 del token.
- Generar un enlace nuevo revoca los anteriores que siguieran vigentes.
- El enlace se consume al enviar los cambios (POST .../changes), no al abrirlo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import clock
from app.core.security import new_token, token_hash
from app.models import Invitation, Member
from app.services import audit, mailer


class InvitationError(Exception):
    STATUS = {"invitacion-invalida": 404, "invitacion-expirada": 410, "invitacion-usada": 410}
    MESSAGES = {
        "invitacion-invalida": "El enlace no es válido.",
        "invitacion-expirada": "El enlace caducó. Solicita uno nuevo a la coordinación.",
        "invitacion-usada": "El enlace ya se utilizó. Solicita uno nuevo a la coordinación.",
    }

    def __init__(self, codigo: str) -> None:
        super().__init__(self.MESSAGES[codigo])
        self.codigo = codigo
        self.status = self.STATUS[codigo]
        self.mensaje = self.MESSAGES[codigo]


def site_link(token: str) -> str:
    return f"{get_settings().public_site_url.rstrip('/')}/actualizar/{token}"


@dataclass
class CreatedInvitation:
    invitation: Invitation
    token: str
    enlace: str
    enviado: bool
    motivo_no_enviado: str | None


def create(
    db: Session, *, member: Member, envio: str, user_id: uuid.UUID | None, ip: str | None = None
) -> CreatedInvitation:
    settings = get_settings()
    now = clock.now()
    db.execute(
        update(Invitation)
        .where(
            Invitation.member_id == member.id,
            Invitation.usado_en.is_(None),
            Invitation.revocado_en.is_(None),
            Invitation.expira_en > now,
        )
        .values(revocado_en=now)
    )
    token = new_token()
    invitation = Invitation(
        member_id=member.id,
        token_hash=token_hash(token),
        expira_en=now + timedelta(hours=settings.invitation_ttl_hours),
        canal="manual",
        creado_por=user_id,
    )
    db.add(invitation)
    db.flush()

    enlace = site_link(token)
    enviado = False
    motivo: str | None = None
    if envio == "smtp":
        if not member.correo_institucional:
            motivo = "El integrante no tiene correo institucional registrado."
        elif not mailer.is_configured():
            motivo = "El SMTP institucional no está configurado; comparte el enlace manualmente."
        else:
            subject, body = mailer.invitation_email(member.nombres, enlace, settings.invitation_ttl_hours)
            enviado = mailer.send(member.correo_institucional, subject, body)
            if enviado:
                invitation.canal = "smtp"
            else:
                motivo = "El servidor de correo rechazó el envío; comparte el enlace manualmente."

    audit.record(
        db,
        user_id,
        "invitacion.crear",
        "invitations",
        invitation.id,
        {"integrante": member.slug, "envio": envio, "enviado": enviado},
        ip,
    )
    db.commit()
    return CreatedInvitation(invitation, token, enlace, enviado, motivo)


def get_valid(db: Session, token: str, *, for_update: bool = False) -> Invitation:
    if not token or len(token) > 200:
        raise InvitationError("invitacion-invalida")
    query = select(Invitation).where(Invitation.token_hash == token_hash(token))
    if for_update:
        query = query.with_for_update(of=Invitation)
    invitation = db.scalar(query)
    if invitation is None or invitation.revocado_en is not None:
        raise InvitationError("invitacion-invalida")
    if invitation.usado_en is not None:
        raise InvitationError("invitacion-usada")
    if invitation.expira_en <= clock.now():
        raise InvitationError("invitacion-expirada")
    if not invitation.member.activo:
        raise InvitationError("invitacion-invalida")
    return invitation


def consume(invitation: Invitation) -> None:
    invitation.usado_en = clock.now()
