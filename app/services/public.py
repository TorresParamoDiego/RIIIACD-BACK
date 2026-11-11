"""Consultas del sitio público.

La proyección a `PublicMember` es una LISTA BLANCA: se construye campo por
campo y nunca a partir del modelo completo. El CVU, el teléfono, el correo
personal, los archivos y la carta compromiso no aparecen en ninguna de estas
funciones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Consent, ImportBatch, Institution, Member, MemberPublicProfile, Photo, ResearchLine
from app.services.normalization import ROLES, to_search_key

ALL = "todas"
UNASSIGNED = "sin-asignar"
SORT_ORDERS = ("apellidos-asc", "apellidos-desc", "nombres-asc")


def _consent_active():  # type: ignore[no-untyped-def]
    return exists().where(
        Consent.member_id == Member.id, Consent.aprobado_en.is_not(None), Consent.revocado_en.is_(None)
    )


def _public_base() -> Select[Any]:
    return (
        select(Member, _consent_active().label("correo_autorizado"), Photo.id.label("foto_id"))
        .outerjoin(MemberPublicProfile, MemberPublicProfile.member_id == Member.id)
        .outerjoin(Photo, and_(Photo.id == MemberPublicProfile.foto_id, Photo.estado == "aprobada"))
        .where(Member.activo.is_(True), Member.es_publico.is_(True))
    )


def photo_url(photo_id: Any) -> str:
    return f"{get_settings().api_public_url.rstrip('/')}/media/photos/{photo_id}"


def to_public_member(member: Member, *, correo_autorizado: bool, foto_id: Any | None) -> dict[str, Any]:
    """Lista blanca explícita. Coincide con PUBLIC_MEMBER_FIELDS del frontend."""
    perfil = member.perfil
    lineas = [x for x in (perfil.lineas_propuestas if perfil else []) or [] if x != member.linea_investigacion_id]
    data: dict[str, Any] = {
        "id": member.slug,
        "tratamiento": member.tratamiento,
        "nombres": member.nombres,
        "apellidos": member.apellidos,
        "nombre_completo": member.nombre_completo,
        "institucion_id": member.institucion_id,
        "rol": member.rol or ROLES["integrante"],
        "linea_investigacion_id": member.linea_investigacion_id,
        "correo_publico": member.correo_institucional if correo_autorizado else None,
        "biografia": perfil.biografia if perfil else None,
        "perfil_profesional": perfil.perfil_profesional if perfil else None,
        "lineas_propuestas": lineas,
        "foto_url": photo_url(foto_id) if foto_id else None,
        "perfil_academico_url": None,
        "es_demostrativo": False,
    }
    return data


@dataclass
class MemberFilters:
    q: str | None = None
    institucion: str = ALL
    linea: str = ALL
    rol: str = ALL
    orden: str = "apellidos-asc"
    pagina: int = 1
    por_pagina: int = 12


def list_members(db: Session, f: MemberFilters) -> tuple[list[dict[str, Any]], int]:
    query = _public_base()
    if f.q and (key := to_search_key(f.q)):
        for term in key.split(" "):
            query = query.where(Member.clave_nombre.contains(term, autoescape=True))
    if f.institucion == UNASSIGNED:
        query = query.where(Member.institucion_id.is_(None))
    elif f.institucion != ALL:
        query = query.where(Member.institucion_id == f.institucion)
    if f.linea == UNASSIGNED:
        query = query.where(Member.linea_investigacion_id.is_(None))
    elif f.linea != ALL:
        query = query.where(Member.linea_investigacion_id == f.linea)
    if f.rol != ALL:
        query = query.where(Member.rol == f.rol)

    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    if f.orden == "apellidos-desc":
        query = query.order_by(Member.apellidos.desc(), Member.nombres.desc())
    elif f.orden == "nombres-asc":
        query = query.order_by(Member.nombres, Member.apellidos)
    else:
        query = query.order_by(Member.apellidos, Member.nombres)
    query = query.offset((f.pagina - 1) * f.por_pagina).limit(f.por_pagina)

    items = [
        to_public_member(row.Member, correo_autorizado=row.correo_autorizado, foto_id=row.foto_id)
        for row in db.execute(query).unique()
    ]
    return items, total


def get_member(db: Session, slug: str) -> dict[str, Any] | None:
    row = db.execute(_public_base().where(Member.slug == slug)).unique().first()
    if row is None:
        return None
    return to_public_member(row.Member, correo_autorizado=row.correo_autorizado, foto_id=row.foto_id)


def list_institutions(db: Session) -> list[Institution]:
    return list(
        db.scalars(select(Institution).where(Institution.es_demostrativa.is_(False)).order_by(Institution.nombre))
    )


def list_research_lines(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ResearchLine, Member.slug)
        .outerjoin(Member, Member.id == ResearchLine.responsable_id)
        .order_by(ResearchLine.numero)
    )
    return [
        {
            "id": line.id,
            "numero": line.numero,
            "slug": line.slug,
            "nombre": line.nombre,
            "nombre_corto": line.nombre_corto,
            "descripcion": line.descripcion,
            "ejes": line.ejes,
            "responsable_id": slug,
            "palabras_clave": line.palabras_clave,
        }
        for line, slug in rows
    ]


def statistics(db: Session) -> dict[str, Any]:
    """Cifras calculadas a partir del padrón: solo integrantes activos y públicos."""
    visible = and_(Member.activo.is_(True), Member.es_publico.is_(True))
    integrantes = db.scalar(select(func.count()).select_from(Member).where(visible)) or 0
    por_tipo = dict(
        db.execute(
            select(Institution.tipo, func.count(func.distinct(Institution.id)))
            .join(Member, Member.institucion_id == Institution.id)
            .where(visible, Institution.es_demostrativa.is_(False))
            .group_by(Institution.tipo)
        ).all()
    )
    tipos = {t: int(por_tipo.get(t, 0)) for t in ("tecnologico", "centro-investigacion", "universidad")}
    fecha: datetime | None = db.scalar(select(func.max(ImportBatch.aplicado_en)))
    return {
        "integrantes": integrantes,
        "instituciones": sum(tipos.values()),
        "instituciones_por_tipo": tipos,
        "fecha_corte": fecha.isoformat() if fecha else None,
    }
