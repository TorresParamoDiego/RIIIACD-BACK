"""Contratos del flujo por enlace. Replican `src/types/memberUpdate.ts` del frontend."""

from pydantic import Field

from app.schemas.common import CamelModel
from app.schemas.public import ResearchLineId


class InvitationMember(CamelModel):
    id: str
    nombre_completo: str
    correo_institucional_enmascarado: str | None


class EditableProfile(CamelModel):
    biografia: str | None
    perfil_profesional: str | None
    linea_investigacion_id: ResearchLineId | None
    lineas_propuestas: list[ResearchLineId]
    correo_publico_autorizado: bool
    foto_url: str | None


class ConsentText(CamelModel):
    version: str
    texto: str


class PhotoRules(CamelModel):
    formatos: list[str]
    tamano_maximo_bytes: int


class MemberInvitation(CamelModel):
    integrante: InvitationMember
    expira_en: str
    perfil: EditableProfile
    texto_consentimiento: ConsentText
    foto: PhotoRules


class MemberChangeProposal(CamelModel):
    """Solo se envían los campos que cambiaron. `null` vacía el campo."""

    biografia: str | None = Field(None, max_length=1000)
    perfil_profesional: str | None = Field(None, max_length=500)
    lineas_propuestas: list[ResearchLineId] | None = Field(None, max_length=5)
    solicita_correo_publico: bool | None = None
    version_texto_consentimiento: str | None = Field(None, max_length=40)


class ChangeSubmissionResult(CamelModel):
    solicitudes_creadas: int


class PhotoUploadResult(CamelModel):
    foto_id: str
    estado: str
