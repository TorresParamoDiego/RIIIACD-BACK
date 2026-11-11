"""Endpoints públicos (sin autenticación)."""

from __future__ import annotations

import math
import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import client_ip, rate_limit
from app.api.errors import ApiError
from app.config import get_settings
from app.db import get_db
from app.models import ContactMessage, Photo
from app.schemas.common import ErrorResponse
from app.schemas.public import (
    Accepted,
    ContactIn,
    InstitutionOut,
    MemberPage,
    PublicMember,
    ResearchLineOut,
    StatisticsOut,
)
from app.services import audit, public
from app.services.storage import StorageError, get_storage

router = APIRouter(tags=["público"])


@router.get("/members", response_model=MemberPage)
def list_members(
    db: Session = Depends(get_db),
    q: str | None = Query(None, max_length=100, description="Busca en nombres y apellidos (sin acentos)."),
    institucion: str = Query(public.ALL, max_length=80, description="Id de institución, 'todas' o 'sin-asignar'."),
    linea: str = Query(public.ALL, max_length=20, description="Id de línea, 'todas' o 'sin-asignar'."),
    rol: str = Query(public.ALL, max_length=120),
    orden: str = Query("apellidos-asc", pattern="^(apellidos-asc|apellidos-desc|nombres-asc)$"),
    pagina: int = Query(1, ge=1, le=10_000),
    por_pagina: int = Query(12, alias="porPagina", ge=1),
) -> MemberPage:
    por_pagina = min(por_pagina, get_settings().members_max_page_size)
    filters = public.MemberFilters(q, institucion, linea, rol, orden, pagina, por_pagina)
    items, total = public.list_members(db, filters)
    return MemberPage(
        items=[PublicMember.model_validate(i) for i in items],
        total=total,
        pagina=pagina,
        por_pagina=por_pagina,
        total_paginas=max(1, math.ceil(total / por_pagina)),
    )


@router.get("/members/{member_id}", response_model=PublicMember, responses={404: {"model": ErrorResponse}})
def get_member(member_id: str, db: Session = Depends(get_db)) -> PublicMember:
    data = public.get_member(db, member_id)
    if data is None:
        raise ApiError(404, "integrante-no-encontrado", "No existe un integrante público con ese identificador.")
    return PublicMember.model_validate(data)


@router.get("/institutions", response_model=list[InstitutionOut])
def list_institutions(db: Session = Depends(get_db)) -> list[InstitutionOut]:
    return [InstitutionOut.model_validate(i) for i in public.list_institutions(db)]


@router.get("/research-lines", response_model=list[ResearchLineOut])
def list_research_lines(db: Session = Depends(get_db)) -> list[ResearchLineOut]:
    return [ResearchLineOut.model_validate(x) for x in public.list_research_lines(db)]


@router.get("/statistics", response_model=StatisticsOut)
def get_statistics(db: Session = Depends(get_db)) -> StatisticsOut:
    return StatisticsOut.model_validate(public.statistics(db))


@router.post(
    "/contact",
    response_model=Accepted,
    status_code=202,
    dependencies=[Depends(rate_limit("contacto", "contact_rate_limit", "contact_rate_window_seconds"))],
    responses={429: {"model": ErrorResponse}},
)
def contact(payload: ContactIn, request: Request, db: Session = Depends(get_db)) -> Accepted:
    # Campo trampa: los robots lo rellenan. Se responde igual para no darles pistas.
    if payload.sitio_web:
        return Accepted()
    db.add(
        ContactMessage(
            nombre=payload.nombre.strip(),
            correo=str(payload.correo).lower(),
            asunto=payload.asunto.strip(),
            mensaje=payload.mensaje.strip(),
            ip_hash=audit.hash_ip(client_ip(request)),
        )
    )
    db.commit()
    return Accepted()


@router.get("/media/photos/{photo_id}", responses={404: {"model": ErrorResponse}}, response_class=Response)
def public_photo(photo_id: uuid.UUID, db: Session = Depends(get_db)) -> Response:
    """Solo sirve fotografías aprobadas. Las pendientes, rechazadas o reemplazadas responden 404."""
    photo = db.get(Photo, photo_id)
    if photo is None or photo.estado != "aprobada":
        raise ApiError(404, "foto-no-encontrada", "La fotografía no existe.")
    try:
        data = get_storage().get(photo.clave_almacenamiento)
    except StorageError as exc:
        raise ApiError(404, "foto-no-encontrada", "La fotografía no existe.") from exc
    return Response(
        content=data,
        media_type=photo.content_type,
        headers={"Cache-Control": "public, max-age=86400", "Content-Disposition": "inline"},
    )
