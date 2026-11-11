"""Endpoints administrativos (autenticados, por rol). Contienen datos personales: todo se audita."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AdminContext, require
from app.api.errors import ApiError
from app.core.permissions import Permission
from app.db import get_db
from app.models import FieldLock, ImportBatch, Member, MemberChangeRequest, MemberHistory, Photo
from app.schemas.admin import (
    ChangeRequestOut,
    ImportReport,
    ImportRowOut,
    InvitationIn,
    InvitationOut,
    MemberAdminList,
    MemberAdminOut,
    MemberPatch,
    RejectIn,
)
from app.schemas.common import ErrorResponse
from app.services import audit, importer, invitations, profiles
from app.services.normalization import to_search_key
from app.services.storage import StorageError, get_storage

router = APIRouter(
    prefix="/admin",
    tags=["administración"],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)

MAX_EXCEL_BYTES = 10 * 1024 * 1024


# --------------------------------------------------------------------------- #
#  Importaciones
# --------------------------------------------------------------------------- #


def import_report(db: Session, batch: ImportBatch) -> ImportReport:
    ids = {r.member_id for r in batch.filas if r.member_id}
    members = {m.id: m for m in db.scalars(select(Member).where(Member.id.in_(ids)))} if ids else {}
    filas = []
    for r in batch.filas:
        m = members.get(r.member_id) if r.member_id else None
        nombre = (
            m.nombre_completo if m else (f"{r.datos.get('nombres', '')} {r.datos.get('apellidos', '')}".strip() or None)
        )
        filas.append(
            ImportRowOut(
                fila=r.fila,
                resultado=r.resultado,  # type: ignore[arg-type]
                integrante_id=m.slug if m else None,
                nombre=nombre,
                coincidencia=r.coincidencia,
                diferencias=r.diferencias,  # type: ignore[arg-type]
                advertencias=r.advertencias,  # type: ignore[arg-type]
            )
        )
    return ImportReport(
        id=str(batch.id),
        estado=batch.estado,  # type: ignore[arg-type]
        nombre_archivo=batch.nombre_archivo,
        archivo_hash=batch.archivo_hash,
        creado_en=batch.creado_en,
        aplicado_en=batch.aplicado_en,
        resumen=batch.resumen,
        filas=filas,
    )


@router.post("/imports", response_model=ImportReport, status_code=201, responses={409: {"model": ErrorResponse}})
async def upload_import(
    archivo: UploadFile = File(..., description="Excel (.xlsx) de integrantes"),
    ctx: AdminContext = Depends(require(Permission.IMPORTS)),
    db: Session = Depends(get_db),
) -> ImportReport:
    """Sube el Excel y devuelve el informe de revisión. No aplica nada todavía."""
    content = await archivo.read(MAX_EXCEL_BYTES + 1)
    if len(content) > MAX_EXCEL_BYTES:
        raise ApiError(413, "archivo-demasiado-grande", "El archivo supera 10 MB.")
    batch = importer.preview(db, content=content, filename=archivo.filename or "integrantes.xlsx", user_id=ctx.user.id)
    db.refresh(batch)
    return import_report(db, batch)


@router.get("/imports/{batch_id}", response_model=ImportReport)
def get_import(
    batch_id: uuid.UUID, ctx: AdminContext = Depends(require(Permission.IMPORTS)), db: Session = Depends(get_db)
) -> ImportReport:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise ApiError(404, "lote-no-encontrado", "El lote de importación no existe.")
    audit.record(db, ctx.user.id, "importacion.consultar", "import_batches", batch.id, {}, ctx.ip)
    db.commit()
    return import_report(db, batch)


@router.post("/imports/{batch_id}/confirm", response_model=ImportReport, responses={409: {"model": ErrorResponse}})
def confirm_import(
    batch_id: uuid.UUID, ctx: AdminContext = Depends(require(Permission.IMPORTS)), db: Session = Depends(get_db)
) -> ImportReport:
    """Aplica el lote. Recalcula las diferencias con el estado actual antes de aplicarlas."""
    batch = importer.confirm(db, batch_id=batch_id, user_id=ctx.user.id)
    db.refresh(batch)
    return import_report(db, batch)


# --------------------------------------------------------------------------- #
#  Cola de aprobación
# --------------------------------------------------------------------------- #


def change_request_out(r: MemberChangeRequest) -> ChangeRequestOut:
    return ChangeRequestOut(
        id=str(r.id),
        integrante_id=r.member.slug,
        integrante_nombre=r.member.nombre_completo,
        campo=r.campo,
        valor_anterior=r.valor_anterior,
        valor_propuesto=r.valor_propuesto,
        estado=r.estado,
        motivo_rechazo=r.motivo_rechazo,
        creado_en=r.creado_en,
        revisado_en=r.revisado_en,
        foto_url_admin=f"/admin/photos/{r.valor_propuesto}" if r.campo == "foto" and r.valor_propuesto else None,
    )


@router.get("/change-requests", response_model=list[ChangeRequestOut])
def list_change_requests(
    estado: Literal["pendiente", "aprobada", "rechazada"] = "pendiente",
    ctx: AdminContext = Depends(require(Permission.REVIEW)),
    db: Session = Depends(get_db),
) -> list[ChangeRequestOut]:
    requests = db.scalars(
        select(MemberChangeRequest).where(MemberChangeRequest.estado == estado).order_by(MemberChangeRequest.creado_en)
    ).all()
    return [change_request_out(r) for r in requests]


@router.post(
    "/change-requests/{request_id}/approve", response_model=ChangeRequestOut, responses={409: {"model": ErrorResponse}}
)
def approve_change(
    request_id: uuid.UUID, ctx: AdminContext = Depends(require(Permission.REVIEW)), db: Session = Depends(get_db)
) -> ChangeRequestOut:
    """Aplica el cambio, lo registra en el historial y bloquea el campo."""
    return change_request_out(profiles.approve(db, request_id=request_id, user_id=ctx.user.id, ip=ctx.ip))


@router.post(
    "/change-requests/{request_id}/reject", response_model=ChangeRequestOut, responses={409: {"model": ErrorResponse}}
)
def reject_change(
    request_id: uuid.UUID,
    payload: RejectIn,
    ctx: AdminContext = Depends(require(Permission.REVIEW)),
    db: Session = Depends(get_db),
) -> ChangeRequestOut:
    return change_request_out(
        profiles.reject(db, request_id=request_id, user_id=ctx.user.id, motivo=payload.motivo.strip(), ip=ctx.ip)
    )


@router.get("/photos/{photo_id}", response_class=Response)
def admin_photo(
    photo_id: uuid.UUID, ctx: AdminContext = Depends(require(Permission.REVIEW)), db: Session = Depends(get_db)
) -> Response:
    """Vista previa de cualquier fotografía (también pendientes) para revisarla."""
    photo = db.get(Photo, photo_id)
    if photo is None:
        raise ApiError(404, "foto-no-encontrada", "La fotografía no existe.")
    try:
        data = get_storage().get(photo.clave_almacenamiento)
    except StorageError as exc:
        raise ApiError(404, "foto-no-encontrada", "El archivo de la fotografía ya no existe.") from exc
    audit.record(db, ctx.user.id, "foto.consultar", "photos", photo.id, {"estado": photo.estado}, ctx.ip)
    db.commit()
    return Response(content=data, media_type=photo.content_type, headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- #
#  Invitaciones
# --------------------------------------------------------------------------- #


def _member_by_slug(db: Session, slug: str) -> Member:
    member = db.scalar(select(Member).where(Member.slug == slug))
    if member is None:
        raise ApiError(404, "integrante-no-encontrado", "No existe un integrante con ese identificador.")
    return member


@router.post("/invitations", response_model=InvitationOut, status_code=201)
def create_invitation(
    payload: InvitationIn, ctx: AdminContext = Depends(require(Permission.INVITATIONS)), db: Session = Depends(get_db)
) -> InvitationOut:
    """Genera un enlace de 24 horas. Con envio=smtp intenta enviarlo; si no sale, se devuelve para compartirlo."""
    member = _member_by_slug(db, payload.integrante_id)
    if not member.activo:
        raise ApiError(409, "integrante-inactivo", "El integrante está desactivado.")
    created = invitations.create(db, member=member, envio=payload.envio, user_id=ctx.user.id, ip=ctx.ip)
    return InvitationOut(
        invitacion_id=str(created.invitation.id),
        expira_en=created.invitation.expira_en,
        enviado=created.enviado,
        enlace=None if created.enviado else created.enlace,
        motivo_no_enviado=created.motivo_no_enviado,
    )


# --------------------------------------------------------------------------- #
#  Integrantes (datos administrativos)
# --------------------------------------------------------------------------- #


def member_admin_out(db: Session, m: Member) -> MemberAdminOut:
    perfil = profiles.ensure_profile(m)
    locks = sorted(db.scalars(select(FieldLock.campo).where(FieldLock.member_id == m.id)))
    return MemberAdminOut(
        id=str(m.id),
        **{f: getattr(m, f) for f in MemberAdminOut.model_fields if hasattr(m, f) and f not in {"id"}},
        biografia=perfil.biografia,
        perfil_profesional=perfil.perfil_profesional,
        lineas_propuestas=list(perfil.lineas_propuestas or []),
        campos_bloqueados=locks,
    )


@router.get("/members", response_model=MemberAdminList)
def list_admin_members(
    q: str | None = Query(None, max_length=100),
    activo: bool | None = None,
    pagina: int = Query(1, ge=1),
    por_pagina: int = Query(50, alias="porPagina", ge=1, le=200),
    ctx: AdminContext = Depends(require(Permission.MEMBERS_READ)),
    db: Session = Depends(get_db),
) -> MemberAdminList:
    query = select(Member)
    if q:
        key = to_search_key(q)
        query = query.where(
            or_(
                Member.clave_nombre.contains(key, autoescape=True),
                Member.correo_institucional.contains(q.lower(), autoescape=True),
                Member.cvu_tecnm == q.strip(),
            )
        )
    if activo is not None:
        query = query.where(Member.activo.is_(activo))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    members = (
        db.scalars(query.order_by(Member.apellidos, Member.nombres).offset((pagina - 1) * por_pagina).limit(por_pagina))
        .unique()
        .all()
    )
    audit.record(
        db, ctx.user.id, "integrantes.consultar", "members", None, {"q": q, "resultados": len(members)}, ctx.ip
    )
    db.commit()
    return MemberAdminList(items=[member_admin_out(db, m) for m in members], total=total)


@router.get("/members/{slug}", response_model=MemberAdminOut)
def get_admin_member(
    slug: str, ctx: AdminContext = Depends(require(Permission.MEMBERS_READ)), db: Session = Depends(get_db)
) -> MemberAdminOut:
    member = _member_by_slug(db, slug)
    audit.record(db, ctx.user.id, "integrante.consultar", "members", member.id, {}, ctx.ip)
    db.commit()
    return member_admin_out(db, member)


@router.get("/members/{slug}/history")
def member_history(
    slug: str, ctx: AdminContext = Depends(require(Permission.MEMBERS_READ)), db: Session = Depends(get_db)
) -> list[dict]:
    member = _member_by_slug(db, slug)
    rows = db.scalars(
        select(MemberHistory).where(MemberHistory.member_id == member.id).order_by(MemberHistory.creado_en)
    ).all()
    audit.record(db, ctx.user.id, "integrante.historial", "members", member.id, {}, ctx.ip)
    db.commit()
    return [
        {
            "campo": h.campo,
            "valorAnterior": h.valor_anterior,
            "valorNuevo": h.valor_nuevo,
            "origen": h.origen,
            "referencia": h.referencia,
            "usuarioId": str(h.usuario_id) if h.usuario_id else None,
            "creadoEn": h.creado_en.isoformat(),
        }
        for h in rows
    ]


@router.patch("/members/{slug}", response_model=MemberAdminOut, responses={409: {"model": ErrorResponse}})
def patch_member(
    slug: str,
    payload: MemberPatch,
    ctx: AdminContext = Depends(require(Permission.MEMBERS_WRITE)),
    db: Session = Depends(get_db),
) -> MemberAdminOut:
    """Edición directa con historial. Los campos editados quedan bloqueados frente a importaciones."""
    member = _member_by_slug(db, slug)
    cambios = payload.model_dump(exclude_unset=True)
    for key in ("correo_institucional", "correo_personal"):
        if cambios.get(key):
            cambios[key] = str(cambios[key]).lower()
    try:
        profiles.admin_patch(db, member=member, cambios=cambios, user_id=ctx.user.id, ip=ctx.ip)
    except IntegrityError as exc:
        db.rollback()
        raise ApiError(
            409, "identificador-duplicado", "El CVU o el correo institucional ya pertenecen a otro integrante."
        ) from exc
    db.refresh(member)
    return member_admin_out(db, member)
