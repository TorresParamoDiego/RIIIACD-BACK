"""Contratos administrativos. Contienen datos personales: solo para el panel, con sesión y rol."""

from datetime import datetime
from typing import Any, Literal

from pydantic import EmailStr, Field

from app.schemas.common import CamelModel
from app.schemas.public import AcademicTitle, ResearchLineId


class LoginIn(CamelModel):
    correo: EmailStr
    contrasena: str = Field(min_length=1, max_length=200)


class AdminUserOut(CamelModel):
    id: str
    correo: str
    nombre: str
    rol: str
    permisos: list[str]


class LoginOut(CamelModel):
    usuario: AdminUserOut
    csrf_token: str = Field(description="Enviar en el encabezado X-CSRF-Token en cada POST/PUT/PATCH/DELETE.")


class ImportDiff(CamelModel):
    campo: str
    anterior: Any = None
    nuevo: Any = None
    bloqueado: bool = False


class ImportWarning(CamelModel):
    codigo: str
    mensaje: str
    campo: str | None = None


class ImportRowOut(CamelModel):
    fila: int | None
    resultado: Literal["nuevo", "cambiado", "sin_cambios", "duplicado", "desactivado", "omitido"]
    integrante_id: str | None = None
    nombre: str | None = None
    coincidencia: str | None = None
    diferencias: list[ImportDiff] = []
    advertencias: list[ImportWarning] = []


class ImportReport(CamelModel):
    id: str
    estado: Literal["revision", "aplicado", "descartado"]
    nombre_archivo: str
    archivo_hash: str
    creado_en: datetime
    aplicado_en: datetime | None = None
    resumen: dict[str, Any]
    filas: list[ImportRowOut]


class ChangeRequestOut(CamelModel):
    id: str
    integrante_id: str
    integrante_nombre: str
    campo: str
    valor_anterior: Any = None
    valor_propuesto: Any = None
    estado: str
    motivo_rechazo: str | None = None
    creado_en: datetime
    revisado_en: datetime | None = None
    foto_url_admin: str | None = None


class RejectIn(CamelModel):
    motivo: str = Field(min_length=3, max_length=1000)


class InvitationIn(CamelModel):
    integrante_id: str = Field(description="Slug público del integrante.")
    envio: Literal["smtp", "manual"] = "manual"


class InvitationOut(CamelModel):
    invitacion_id: str
    expira_en: datetime
    enviado: bool
    enlace: str | None = Field(None, description="Se devuelve cuando el envío es manual o el correo no salió.")
    motivo_no_enviado: str | None = None


class MemberAdminOut(CamelModel):
    id: str
    slug: str
    numero: int | None
    tratamiento: str | None
    nombres: str
    apellidos: str
    cvu_tecnm: str | None
    cvu_conahcyt: str | None
    correo_institucional: str | None
    telefono_particular: str | None
    correo_personal: str | None
    institucion_texto: str | None
    institucion_id: str | None
    rol: str | None
    linea_investigacion_texto: str | None
    linea_investigacion_id: str | None
    scopus_author_id: str | None
    archivo_cvu_tecnm: str | None
    archivo_cvu_conahcyt: str | None
    archivo_carta_compromiso: str | None
    activo: bool
    es_publico: bool
    biografia: str | None = None
    perfil_profesional: str | None = None
    lineas_propuestas: list[str] = []
    campos_bloqueados: list[str] = []


class MemberPatch(CamelModel):
    """Edición directa. Cada campo enviado queda en el historial y se bloquea frente a importaciones."""

    tratamiento: AcademicTitle | None = None
    nombres: str | None = Field(None, min_length=1, max_length=200)
    apellidos: str | None = Field(None, min_length=1, max_length=200)
    cvu_tecnm: str | None = Field(None, max_length=40, pattern=r"^\d+$")
    cvu_conahcyt: str | None = Field(None, max_length=40, pattern=r"^\d+$")
    correo_institucional: EmailStr | None = None
    telefono_particular: str | None = Field(None, max_length=40)
    correo_personal: EmailStr | None = None
    institucion_id: str | None = None
    rol: str | None = Field(None, max_length=120)
    linea_investigacion_id: ResearchLineId | None = None
    scopus_author_id: str | None = Field(None, max_length=40)
    activo: bool | None = None
    es_publico: bool | None = None
    biografia: str | None = Field(None, max_length=1000)
    perfil_profesional: str | None = Field(None, max_length=500)
    lineas_propuestas: list[ResearchLineId] | None = None


class MemberAdminList(CamelModel):
    items: list[MemberAdminOut]
    total: int
