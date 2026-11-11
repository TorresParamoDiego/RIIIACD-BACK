"""Importación del Excel de integrantes (brief fase 2, sección 3.3).

Flujo en dos pasos:

1. `preview()`: lee el archivo, normaliza cada fila, calcula las diferencias
   contra la base de datos y las guarda en `import_row_results` ANTES de
   aplicar nada. Devuelve el lote en estado `revision` con su informe.
2. `confirm()`: vuelve a calcular el plan con las filas guardadas (por si la
   base cambió desde la revisión) y lo aplica en una sola transacción.

Reglas:
- Coincidencia: CVU TecNM → CVU CONAHCyT (solo si la fila no trae CVU TecNM)
  → correo institucional → registro nuevo.
- Los campos con bloqueo en `field_locks` no se sobrescriben.
- Los duplicados se marcan para revisión; nunca se unen automáticamente.
- Quien falta en el archivo se desactiva; nunca se elimina.
- Idempotencia: un archivo con el mismo hash solo se aplica una vez.
- Una celda vacía no borra un dato existente (se conserva el último valor conocido).
"""

from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.models import (
    FieldLock,
    ImportBatch,
    ImportRowResult,
    Institution,
    Member,
    MemberHistory,
    MemberPublicProfile,
    ResearchLine,
)
from app.services import audit
from app.services.excel import read_members_sheet
from app.services.normalization import (
    IMPORTABLE_FIELDS,
    Catalog,
    InstitutionRef,
    LineRef,
    NormalizedRow,
    RowWarning,
    map_row,
    member_slug,
    name_key,
    unknown_columns,
)


class ImportError_(Exception):
    """Error de negocio de la importación. `codigo` se devuelve al cliente."""

    def __init__(self, codigo: str, mensaje: str, status: int = 409) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje
        self.status = status


# --------------------------------------------------------------------------- #
#  Plan
# --------------------------------------------------------------------------- #


@dataclass
class RowPlan:
    fila: int | None
    resultado: str
    datos: dict[str, Any]
    member: Member | None = None
    coincidencia: str | None = None
    diferencias: list[dict[str, Any]] = field(default_factory=list)
    advertencias: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Plan:
    filas: list[RowPlan]
    advertencias_generales: list[str]

    def resumen(self) -> dict[str, Any]:
        conteo = Counter(p.resultado for p in self.filas)
        codigos = Counter(a["codigo"] for p in self.filas for a in p.advertencias)
        return {
            "nuevos": conteo["nuevo"],
            "cambiados": conteo["cambiado"],
            "sinCambios": conteo["sin_cambios"],
            "duplicados": conteo["duplicado"],
            "desactivados": conteo["desactivado"],
            "omitidos": conteo["omitido"],
            "advertencias": sum(codigos.values()),
            "advertenciasPorCodigo": dict(codigos),
            "camposBloqueadosOmitidos": sum(1 for p in self.filas for d in p.diferencias if d.get("bloqueado")),
            "advertenciasGenerales": self.advertencias_generales,
        }


def load_catalog(db: Session) -> Catalog:
    institutions = [
        InstitutionRef(id=i.id, nombre=i.nombre, siglas=i.siglas, alias=tuple(i.alias or ()))
        for i in db.scalars(select(Institution).where(Institution.es_demostrativa.is_(False)))
    ]
    lines = [
        LineRef(id=ln.id, numero=ln.numero, nombre=ln.nombre, palabras_clave=tuple(ln.palabras_clave or ()))
        for ln in db.scalars(select(ResearchLine))
    ]
    return Catalog(institutions=institutions, lines=lines)


def _warning(codigo: str, mensaje: str, campo: str | None = None) -> dict[str, Any]:
    return RowWarning(codigo, mensaje, campo).as_dict()


def compute_plan(db: Session, rows: list[NormalizedRow]) -> Plan:
    members = list(db.scalars(select(Member)))
    by_cvu = {m.cvu_tecnm: m for m in members if m.cvu_tecnm}
    by_conahcyt: dict[str, list[Member]] = defaultdict(list)
    by_email = {m.correo_institucional: m for m in members if m.correo_institucional}
    by_name: dict[str, list[Member]] = defaultdict(list)
    for m in members:
        if m.cvu_conahcyt:
            by_conahcyt[m.cvu_conahcyt].append(m)
        by_name[m.clave_nombre].append(m)
    locks = {(lock.member_id, lock.campo) for lock in db.scalars(select(FieldLock))}

    valid = [r for r in rows if r.datos is not None]
    cvu_count = Counter(r.datos["cvu_tecnm"] for r in valid if r.datos and r.datos["cvu_tecnm"])
    email_count = Counter(r.datos["correo_institucional"] for r in valid if r.datos and r.datos["correo_institucional"])
    name_count = Counter(name_key(r.datos["nombres"], r.datos["apellidos"]) for r in valid if r.datos)

    plans: list[RowPlan] = []
    claimed: dict[uuid.UUID, int] = {}
    """Integrante → fila que lo reclamó. Una segunda fila hacia el mismo integrante es duplicado."""
    seen: set[uuid.UUID] = set()
    """Integrantes presentes en el archivo (aplicados o en revisión): no se desactivan."""

    for row in rows:
        advertencias = [w.as_dict() for w in row.advertencias]
        if row.datos is None:
            plans.append(RowPlan(row.fila, "omitido", {}, advertencias=advertencias))
            continue
        d = row.datos
        key = name_key(d["nombres"], d["apellidos"])

        # 1. Duplicados dentro del propio archivo.
        if d["cvu_tecnm"] and cvu_count[d["cvu_tecnm"]] > 1:
            advertencias.append(
                _warning("duplicado-en-archivo", "El CVU TecNM aparece en más de una fila.", "cvu_tecnm")
            )
        if d["correo_institucional"] and email_count[d["correo_institucional"]] > 1:
            advertencias.append(
                _warning(
                    "duplicado-en-archivo",
                    "El correo institucional aparece en más de una fila.",
                    "correo_institucional",
                )
            )
        if name_count[key] > 1:
            advertencias.append(_warning("posible-duplicado", "Hay otra fila con el mismo nombre.", "nombres"))
        in_file_dup = any(a["codigo"] == "duplicado-en-archivo" for a in advertencias)

        # 2. Búsqueda: CVU TecNM → CVU CONAHCyT (externos) → correo institucional.
        by_cvu_match = by_cvu.get(d["cvu_tecnm"]) if d["cvu_tecnm"] else None
        conahcyt_matches = by_conahcyt.get(d["cvu_conahcyt"], []) if d["cvu_conahcyt"] and not d["cvu_tecnm"] else []
        by_email_match = by_email.get(d["correo_institucional"]) if d["correo_institucional"] else None

        candidates = {m.id: m for m in [by_cvu_match, by_email_match, *conahcyt_matches] if m is not None}
        member: Member | None = None
        coincidencia: str | None = None
        if by_cvu_match:
            member, coincidencia = by_cvu_match, "cvu_tecnm"
        elif len(conahcyt_matches) == 1:
            member, coincidencia = conahcyt_matches[0], "cvu_conahcyt"
        elif by_email_match:
            member, coincidencia = by_email_match, "correo_institucional"

        if len(candidates) > 1:
            advertencias.append(
                _warning("identificadores-en-conflicto", "El CVU y el correo corresponden a integrantes distintos.")
            )
        if len(conahcyt_matches) > 1:
            advertencias.append(_warning("identificadores-en-conflicto", "El CVU CONAHCyT está en varios integrantes."))
        if member is None and not candidates:
            same_name = by_name.get(key, [])
            if same_name:
                candidates = {m.id: m for m in same_name}
                advertencias.append(
                    _warning(
                        "nombre-coincide-con-registro",
                        "No coincide por CVU ni correo, pero ya existe un integrante con el mismo nombre.",
                        "nombres",
                    )
                )
        if member is not None and member.id in claimed:
            advertencias.append(
                _warning("duplicado-en-archivo", f"Este integrante ya corresponde a la fila {claimed[member.id]}.")
            )
            in_file_dup = True

        seen.update(candidates)

        conflict = in_file_dup or len(candidates) > 1 or (member is None and candidates)
        if conflict:
            plans.append(
                RowPlan(row.fila, "duplicado", d, member=member, coincidencia=coincidencia, advertencias=advertencias)
            )
            continue

        if member is None:
            plans.append(RowPlan(row.fila, "nuevo", d, advertencias=advertencias))
            continue

        claimed[member.id] = row.fila
        diferencias: list[dict[str, Any]] = []
        for campo in IMPORTABLE_FIELDS:
            nuevo = d.get(campo)
            anterior = getattr(member, campo)
            if nuevo is None or nuevo == anterior:
                continue
            diferencias.append(
                {"campo": campo, "anterior": anterior, "nuevo": nuevo, "bloqueado": (member.id, campo) in locks}
            )
        if not member.activo:
            diferencias.append({"campo": "activo", "anterior": False, "nuevo": True, "bloqueado": False})
        aplicables = [x for x in diferencias if not x["bloqueado"]]
        plans.append(
            RowPlan(
                row.fila,
                "cambiado" if aplicables else "sin_cambios",
                d,
                member=member,
                coincidencia=coincidencia,
                diferencias=diferencias,
                advertencias=advertencias,
            )
        )

    # 3. Desactivar a quien falta en el archivo.
    for m in members:
        if m.activo and m.id not in seen:
            plans.append(
                RowPlan(
                    None,
                    "desactivado",
                    {},
                    member=m,
                    diferencias=[{"campo": "activo", "anterior": True, "nuevo": False, "bloqueado": False}],
                )
            )

    generales: list[str] = []
    activos = sum(1 for m in members if m.activo)
    desactivar = sum(1 for p in plans if p.resultado == "desactivado")
    if activos and desactivar / activos > 0.3:
        generales.append(
            f"Se desactivarían {desactivar} de {activos} integrantes activos. Revisa que el archivo esté completo."
        )
    return Plan(filas=plans, advertencias_generales=generales)


# --------------------------------------------------------------------------- #
#  Paso 1: revisión
# --------------------------------------------------------------------------- #


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def preview(db: Session, *, content: bytes, filename: str, user_id: uuid.UUID | None) -> ImportBatch:
    digest = file_hash(content)
    if db.scalar(select(ImportBatch.id).where(ImportBatch.archivo_hash == digest, ImportBatch.estado == "aplicado")):
        raise ImportError_("archivo-ya-aplicado", "Este archivo ya se importó antes; no se aplicará de nuevo.")

    sheet = read_members_sheet(content)
    catalog = load_catalog(db)
    rows = [map_row(raw, fila, catalog) for fila, raw in sheet.rows]
    if not any(r.datos for r in rows):
        raise ImportError_("archivo-sin-integrantes", "El archivo no contiene filas con nombre y apellidos.", 422)

    extra = unknown_columns(sheet.headers)
    if extra and rows:
        rows[0].advertencias.extend(
            RowWarning("columna-desconocida", f'Columna no reconocida: "{c}".', c) for c in extra
        )

    plan = compute_plan(db, rows)

    # Solo puede haber un lote en revisión: confirmar uno viejo después de uno
    # nuevo revertiría datos.
    db.execute(update(ImportBatch).where(ImportBatch.estado == "revision").values(estado="descartado"))

    batch = ImportBatch(archivo_hash=digest, nombre_archivo=filename[:300], subido_por=user_id, estado="revision")
    _store_plan(batch, plan)
    db.add(batch)
    db.flush()
    audit.record(
        db,
        user_id,
        "importacion.revision",
        "import_batches",
        batch.id,
        {"archivo": filename, "hash": digest, **{k: v for k, v in batch.resumen.items() if isinstance(v, int)}},
    )
    db.commit()
    return batch


def _store_plan(batch: ImportBatch, plan: Plan) -> None:
    batch.filas = [
        ImportRowResult(
            orden=i,
            fila=p.fila,
            resultado=p.resultado,
            member_id=p.member.id if p.member else None,
            coincidencia=p.coincidencia,
            diferencias=p.diferencias,
            advertencias=p.advertencias,
            datos=p.datos,
        )
        for i, p in enumerate(plan.filas)
    ]
    batch.resumen = plan.resumen()


# --------------------------------------------------------------------------- #
#  Paso 2: confirmación
# --------------------------------------------------------------------------- #


def confirm(db: Session, *, batch_id: uuid.UUID, user_id: uuid.UUID | None) -> ImportBatch:
    batch = db.scalar(select(ImportBatch).where(ImportBatch.id == batch_id).with_for_update(of=ImportBatch))
    if batch is None:
        raise ImportError_("lote-no-encontrado", "El lote de importación no existe.", 404)
    if batch.estado != "revision":
        raise ImportError_("lote-no-confirmable", f"El lote está en estado «{batch.estado}» y no se puede aplicar.")
    if db.scalar(
        select(ImportBatch.id).where(ImportBatch.archivo_hash == batch.archivo_hash, ImportBatch.estado == "aplicado")
    ):
        raise ImportError_("archivo-ya-aplicado", "Este archivo ya se importó antes; no se aplicará de nuevo.")

    stored = [r for r in batch.filas if r.resultado != "desactivado"]
    rows = [
        NormalizedRow(r.fila or 0, r.datos or None, [RowWarning(**a) for a in r.advertencias if _is_source_warning(a)])
        for r in stored
    ]
    plan = compute_plan(db, rows)
    referencia = str(batch.id)

    for p in plan.filas:
        if p.resultado == "nuevo":
            p.member = _create_member(db, p.datos, referencia, user_id)
        elif p.resultado == "cambiado" and p.member is not None:
            for diff in p.diferencias:
                if diff["bloqueado"]:
                    continue
                if diff["campo"] == "activo":
                    p.member.activo = True
                    p.member.desactivado_en = None
                    origen = "reactivacion"
                else:
                    setattr(p.member, diff["campo"], diff["nuevo"])
                    origen = "importacion"
                db.add(
                    MemberHistory(
                        member_id=p.member.id,
                        campo=diff["campo"],
                        valor_anterior=diff["anterior"],
                        valor_nuevo=diff["nuevo"],
                        origen=origen,
                        referencia=referencia,
                        usuario_id=user_id,
                    )
                )
            _refresh_names(p.member)
        elif p.resultado == "desactivado" and p.member is not None:
            p.member.activo = False
            p.member.desactivado_en = clock.now()
            db.add(
                MemberHistory(
                    member_id=p.member.id,
                    campo="activo",
                    valor_anterior=True,
                    valor_nuevo=False,
                    origen="desactivacion",
                    referencia=referencia,
                    usuario_id=user_id,
                )
            )

    batch.filas.clear()
    db.flush()
    _store_plan(batch, plan)
    batch.estado = "aplicado"
    batch.aplicado_en = clock.now()
    batch.aplicado_por = user_id
    audit.record(
        db,
        user_id,
        "importacion.confirmacion",
        "import_batches",
        batch.id,
        {k: v for k, v in batch.resumen.items() if isinstance(v, int)},
    )
    try:
        db.commit()
    except IntegrityError as exc:  # carrera: otro proceso aplicó el mismo archivo
        db.rollback()
        raise ImportError_("archivo-ya-aplicado", "Este archivo ya se importó antes; no se aplicará de nuevo.") from exc
    return batch


SOURCE_WARNING_CODES = {
    "fila-sin-nombre",
    "numero-invalido",
    "correo-invalido",
    "institucion-no-reconocida",
    "linea-ambigua",
    "linea-no-reconocida",
    "columna-desconocida",
}


def _is_source_warning(warning: dict[str, Any]) -> bool:
    """Advertencias que vienen del archivo (las de coincidencia se recalculan)."""
    return warning.get("codigo") in SOURCE_WARNING_CODES


def _refresh_names(member: Member) -> None:
    member.nombre_completo = f"{member.nombres} {member.apellidos}".strip()
    member.clave_nombre = name_key(member.nombres, member.apellidos)


def unique_slug(db: Session, nombres: str, apellidos: str) -> str:
    base = member_slug(nombres, apellidos)
    existing = set(db.scalars(select(Member.slug).where(Member.slug.like(f"{base}%"))))
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


def _create_member(db: Session, datos: dict[str, Any], referencia: str, user_id: uuid.UUID | None) -> Member:
    member = Member(
        slug=unique_slug(db, datos["nombres"], datos["apellidos"]),
        **{k: datos.get(k) for k in IMPORTABLE_FIELDS},
        nombre_completo="",
        clave_nombre="",
        origen="importacion",
    )
    _refresh_names(member)
    member.perfil = MemberPublicProfile(version=1, lineas_propuestas=[])
    db.add(member)
    db.flush()
    db.add(
        MemberHistory(
            member_id=member.id,
            campo="*",
            valor_anterior=None,
            valor_nuevo={k: datos.get(k) for k in IMPORTABLE_FIELDS},
            origen="alta",
            referencia=referencia,
            usuario_id=user_id,
        )
    )
    return member
