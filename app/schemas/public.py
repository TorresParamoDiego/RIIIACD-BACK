"""Contratos públicos. `PublicMember` replica `src/types/member.ts` del frontend.

`PublicMember` usa `extra="forbid"`: si alguien intenta añadir un campo que no
está en la lista blanca, la validación falla en lugar de publicarlo.
"""

from typing import Literal

from pydantic import ConfigDict, EmailStr, Field

from app.schemas.common import CamelModel

ResearchLineId = Literal["linea-1", "linea-2", "linea-3", "linea-4", "linea-5"]
InstitutionType = Literal["tecnologico", "centro-investigacion", "universidad"]
AcademicTitle = Literal["Dr.", "Dra.", "Mtro.", "Mtra.", "Ing.", "Lic."]


class PublicMember(CamelModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tratamiento: AcademicTitle | None = None
    nombres: str
    apellidos: str
    nombre_completo: str
    institucion_id: str | None
    rol: str
    linea_investigacion_id: ResearchLineId | None
    correo_publico: str | None = Field(None, description="Solo con una autorización aprobada y vigente.")
    biografia: str | None = Field(None, max_length=1000)
    perfil_profesional: str | None = None
    lineas_propuestas: list[ResearchLineId] = []
    foto_url: str | None = None
    perfil_academico_url: str | None = None
    es_demostrativo: bool = False


PUBLIC_MEMBER_FIELDS = frozenset(PublicMember.model_fields)


class MemberPage(CamelModel):
    items: list[PublicMember]
    total: int
    pagina: int
    por_pagina: int
    total_paginas: int


class InstitutionOut(CamelModel):
    id: str
    nombre: str
    siglas: str | None = None
    tipo: InstitutionType
    logo_url: str | None = None
    es_demostrativo: bool = Field(False, validation_alias="es_demostrativa")


class ResearchLineOut(CamelModel):
    id: ResearchLineId
    numero: int
    slug: str
    nombre: str
    nombre_corto: str
    descripcion: str
    ejes: list[str]
    responsable_id: str | None
    palabras_clave: list[str]


class InstitutionsByType(CamelModel):
    tecnologico: int
    centro_investigacion: int = Field(
        serialization_alias="centro-investigacion", validation_alias="centro-investigacion"
    )
    universidad: int


class StatisticsOut(CamelModel):
    integrantes: int
    instituciones: int
    instituciones_por_tipo: InstitutionsByType
    fecha_corte: str | None = None


class ContactIn(CamelModel):
    nombre: str = Field(min_length=3, max_length=200)
    correo: EmailStr
    asunto: str = Field(min_length=4, max_length=200)
    mensaje: str = Field(min_length=20, max_length=2000)
    sitio_web: str | None = Field(
        None, max_length=200, description="Campo trampa anti-spam: debe llegar vacío (oculto en el formulario)."
    )


class Accepted(CamelModel):
    recibido: bool = True
