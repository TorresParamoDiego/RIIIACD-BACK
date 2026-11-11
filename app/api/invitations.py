"""Endpoints del integrante con enlace único (brief 3.6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import client_ip, rate_limit
from app.config import get_settings
from app.db import get_db
from app.schemas.common import ErrorResponse
from app.schemas.invitations import (
    ChangeSubmissionResult,
    ConsentText,
    EditableProfile,
    InvitationMember,
    MemberChangeProposal,
    MemberInvitation,
    PhotoRules,
    PhotoUploadResult,
)
from app.services import invitations, profiles
from app.services.photos import ALLOWED_FORMATS
from app.services.public import photo_url

router = APIRouter(
    prefix="/invitations",
    tags=["integrante"],
    dependencies=[Depends(rate_limit("invitacion", "invitation_rate_limit", "invitation_rate_window_seconds"))],
    responses={404: {"model": ErrorResponse}, 410: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[0]}•••@{domain}"


@router.get("/{token}", response_model=MemberInvitation)
def get_invitation(token: str, db: Session = Depends(get_db)) -> MemberInvitation:
    """Valida el enlace SIN consumirlo."""
    invitation = invitations.get_valid(db, token)
    member = invitation.member
    actuales = profiles.current_values(db, member)
    settings = get_settings()
    return MemberInvitation(
        integrante=InvitationMember(
            id=member.slug,
            nombre_completo=member.nombre_completo,
            correo_institucional_enmascarado=mask_email(member.correo_institucional),
        ),
        expira_en=invitation.expira_en.isoformat(),
        perfil=EditableProfile(
            biografia=actuales["biografia"],
            perfil_profesional=actuales["perfil_profesional"],
            linea_investigacion_id=member.linea_investigacion_id,  # type: ignore[arg-type]
            lineas_propuestas=actuales["lineas_propuestas"],
            correo_publico_autorizado=actuales["correo_publico"],
            foto_url=photo_url(actuales["foto"]) if actuales["foto"] else None,
        ),
        texto_consentimiento=ConsentText(version=settings.consent_text_version, texto=settings.consent_text),
        foto=PhotoRules(formatos=list(ALLOWED_FORMATS.values()), tamano_maximo_bytes=settings.photo_max_bytes),
    )


@router.post("/{token}/changes", response_model=ChangeSubmissionResult, status_code=201)
def submit_changes(
    token: str, payload: MemberChangeProposal, request: Request, db: Session = Depends(get_db)
) -> ChangeSubmissionResult:
    """Envía los cambios a aprobación y CONSUME el enlace."""
    data = payload.model_dump(exclude_unset=True)
    propuesta = {
        ("correo_publico" if k == "solicita_correo_publico" else k): (v.strip() or None if isinstance(v, str) else v)
        for k, v in data.items()
    }
    if propuesta.get("version_texto_consentimiento") is None:
        propuesta.pop("version_texto_consentimiento", None)
    if "lineas_propuestas" in propuesta and propuesta["lineas_propuestas"] is None:
        propuesta["lineas_propuestas"] = []
    if "correo_publico" in propuesta and propuesta["correo_publico"] is None:
        propuesta.pop("correo_publico")
    creadas = profiles.submit_changes(db, token=token, propuesta=propuesta, ip=client_ip(request))
    return ChangeSubmissionResult(solicitudes_creadas=creadas)


@router.put(
    "/{token}/photo", response_model=PhotoUploadResult, status_code=201, responses={413: {"model": ErrorResponse}}
)
async def upload_photo(
    token: str, request: Request, foto: UploadFile = File(...), db: Session = Depends(get_db)
) -> PhotoUploadResult:
    """Sube la fotografía a aprobación. No consume el enlace: hay que enviar después los cambios."""
    limit = get_settings().photo_max_bytes
    raw = await foto.read(limit + 1)
    photo = profiles.submit_photo(db, token=token, raw=raw, ip=client_ip(request))
    return PhotoUploadResult(foto_id=str(photo.id), estado=photo.estado)
