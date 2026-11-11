"""Normalización de las filas del Excel de integrantes.

Traslado directo de `src/utils/memberMapper.ts` (frontend, fase 1): las
reglas de limpieza, nombres propios, correos, adscripción, línea de
investigación y rol (columna `ROLL` → `rol`) son las mismas. Si se cambia una
regla aquí, conviene reflejarla allá o retirar la copia del frontend.

Los CVU, el teléfono y los identificadores Scopus se leen como texto, porque
Excel los convierte en números.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

EXCEL_COLUMNS: tuple[str, ...] = (
    "No.",
    "NOMBRES",
    "APELLIDOS",
    "NÚMERO CVU TecNM",
    "Nombre del archivo del CVU de TecNM enviado",
    "NÚMERO CVU de CONAHCyT PARA Profesores externos",
    "Nombre del archivo del CVU de CONAHCYT enviado",
    "CORREO INSTITUCIONAL",
    "TELÉFONO PARTICULAR",
    "CORREO PERSONAL",
    "INSTITUCIÓN DE ADSCRIPCIÓN",
    "ROLL",
    "Scopus Author Identifier",
    "NOMBRE DEL ARCHIVO DE CARTA COMPROMISO ENVIADO",
    "LÍNEA DE INVESTIGACIÓN PROPUESTA PARA TRABAJAR EN LA RED",
    "Infraestructura que aporta su IT a la Red",
    "Programas académicos de licenciatura y de posgrado que asociará a la red",
)

ROLES = {
    "lider_red": "Líder de la red",
    "lider_linea": "Responsable de línea",
    "integrante": "Integrante",
}

# --------------------------------------------------------------------------- #
#  Texto
# --------------------------------------------------------------------------- #


def strip_diacritics(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value) if not unicodedata.combining(c))


def collapse_whitespace(value: str) -> str:
    return re.sub(r"[\s ]+", " ", value).strip()


def to_search_key(value: str) -> str:
    """Minúsculas, sin acentos, sin signos y con espacios simples."""
    key = strip_diacritics(value).lower()
    key = re.sub(r"[^a-z0-9@.\s-]", " ", key)
    return re.sub(r"\s+", " ", key).strip()


def slugify(value: str) -> str:
    slug = re.sub(r"[\s.@]+", "-", to_search_key(value))
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def normalize_header(header: str) -> str:
    """'  Número CVU  TecNM ' → 'numero cvu tecnm'"""
    return re.sub(r"[^a-z0-9]+", " ", strip_diacritics(str(header)).lower()).strip()


HEADER_LOOKUP = {normalize_header(col): col for col in EXCEL_COLUMNS}

# --------------------------------------------------------------------------- #
#  Celdas
# --------------------------------------------------------------------------- #

EMPTY_MARKERS = {
    "", "-", "--", ".", "na", "n/a", "n.a.", "no aplica", "ninguno", "ninguna",
    "s/n", "sin dato", "null", "undefined", "none",
}  # fmt: skip


def clean_cell(value: Any) -> str | None:
    """Texto limpio, o None para celdas vacías y marcadores como 'N/A', '-' o 'No aplica'."""
    if value is None:
        return None
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = collapse_whitespace(str(value))
    return None if text.lower() in EMPTY_MARKERS else text


def clean_identifier(value: Any) -> str | None:
    """CVU, teléfono, Scopus: se conservan como texto sin decimales ni espacios."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value == value and value not in (float("inf"), float("-inf")) else None
    text = clean_cell(value)
    if text is None:
        return None
    # "1.2345E+5" o "123456.0" escritos como texto
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".")[0]
    elif re.fullmatch(r"\d+(\.\d+)?[eE]\+?\d+", text):
        text = str(int(float(text)))
    return re.sub(r"\s+", "", text)


NAME_PARTICLES = {"de", "del", "la", "las", "los", "y", "e", "van", "von", "da", "di"}


def _capitalize(word: str) -> str:
    return word[:1].upper() + word[1:]


def normalize_person_name(value: str | None) -> str:
    """'  MARÍA  yasmín ' → 'María Yasmín'; 'DE LA CRUZ NICOLÁS' → 'De la Cruz Nicolás'."""
    if not value:
        return ""
    words = collapse_whitespace(value).lower().split(" ")
    out = []
    for index, word in enumerate(words):
        if index > 0 and word in NAME_PARTICLES:
            out.append(word)
            continue
        parts = re.split(r"([-'’])", word)
        out.append("".join(_capitalize(p) if p and not re.fullmatch(r"[-'’]", p) else p for p in parts))
    return " ".join(out)


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


@dataclass
class EmailResult:
    email: str | None
    invalid: bool


def normalize_email(value: Any) -> EmailResult:
    text = clean_cell(value)
    if not text:
        return EmailResult(None, False)
    # Algunas celdas contienen varios correos; se toma el primero válido.
    candidates = [re.sub(r"^mailto:", "", c) for c in re.split(r"[\s,;/]+", text.lower()) if c]
    email = next((c for c in candidates if EMAIL_PATTERN.match(c)), None)
    return EmailResult(email, email is None)


# --------------------------------------------------------------------------- #
#  Instituciones
# --------------------------------------------------------------------------- #

INSTITUTION_NOISE = (
    re.compile(r"^tecnologico nacional de mexico\s*(campus)?\s*"),
    re.compile(r"^tecnm\s*(campus)?\s*"),
    re.compile(r"^tec nm\s*(campus)?\s*"),
)


def institution_key(value: str) -> str:
    """'TecNM Campus Celaya' → 'celaya'."""
    key = re.sub(r"\s+", " ", re.sub(r"[.-]", " ", to_search_key(value))).strip()
    for pattern in INSTITUTION_NOISE:
        key = pattern.sub("", key)
    key = re.sub(r"^instituto tecnologico (superior )?de ", "it ", key)
    key = re.sub(r"^instituto tecnologico (superior )?", "it ", key)
    key = re.sub(r"^i t ", "it ", key)
    return key.strip()


@dataclass(frozen=True)
class InstitutionRef:
    id: str
    nombre: str
    siglas: str | None = None
    alias: tuple[str, ...] = ()


@dataclass(frozen=True)
class LineRef:
    id: str
    numero: int
    nombre: str
    palabras_clave: tuple[str, ...] = ()


@dataclass
class Catalog:
    institutions: list[InstitutionRef] = field(default_factory=list)
    lines: list[LineRef] = field(default_factory=list)


def normalize_institution(value: Any, catalog: Catalog) -> tuple[str | None, str | None]:
    """Devuelve (texto limpio, id del catálogo o None)."""
    nombre = clean_cell(value)
    if not nombre:
        return None, None
    key = institution_key(nombre)
    for inst in catalog.institutions:
        if key in inst.alias:
            return nombre, inst.id
    for inst in catalog.institutions:
        if institution_key(inst.nombre) == key:
            return nombre, inst.id
        if inst.siglas:
            siglas = institution_key(inst.siglas)
            # "CENIDET" dentro de "TecNM / CENIDET"
            if siglas == key or siglas in key.split(" "):
                return nombre, inst.id
    return nombre, None


# --------------------------------------------------------------------------- #
#  Líneas de investigación
# --------------------------------------------------------------------------- #


@dataclass
class LineResolution:
    linea_id: str | None
    ambigua: bool


def resolve_research_line(value: Any, catalog: Catalog) -> LineResolution:
    """1) número explícito ('Línea 3', 'L3', '3.-…'); 2) nombre oficial; 3) palabras clave."""
    text = clean_cell(value)
    if not text:
        return LineResolution(None, False)
    key = to_search_key(text)

    numbered = re.match(r"^(?:linea\s*|l\s*)?([1-5])(?:\s|\.|-|\)|$)", key)
    if numbered:
        line = next((ln for ln in catalog.lines if ln.numero == int(numbered.group(1))), None)
        if line:
            return LineResolution(line.id, False)

    exact = next((ln for ln in catalog.lines if to_search_key(ln.nombre) in key), None)
    if exact:
        return LineResolution(exact.id, False)

    scores = sorted(
        ((sum(1 for kw in ln.palabras_clave if to_search_key(kw) in key), ln.id) for ln in catalog.lines),
        reverse=True,
    )
    scores = [s for s in scores if s[0] > 0]
    if not scores:
        return LineResolution(None, False)
    if len(scores) > 1 and scores[0][0] == scores[1][0]:
        return LineResolution(None, True)
    return LineResolution(scores[0][1], False)


# --------------------------------------------------------------------------- #
#  Rol (columna ROLL)
# --------------------------------------------------------------------------- #

ROLE_ALIASES = {
    "lider de la red": ROLES["lider_red"],
    "lider red": ROLES["lider_red"],
    "coordinador de la red": ROLES["lider_red"],
    "coordinadora de la red": ROLES["lider_red"],
    "lider de linea": ROLES["lider_linea"],
    "responsable de linea": ROLES["lider_linea"],
    "integrante": ROLES["integrante"],
    "miembro": ROLES["integrante"],
    "colaborador": ROLES["integrante"],
    "colaboradora": ROLES["integrante"],
}


def normalize_role(value: Any) -> str | None:
    text = clean_cell(value)
    if not text:
        return None
    return ROLE_ALIASES.get(to_search_key(text)) or _capitalize(text.lower())


# --------------------------------------------------------------------------- #
#  Fila completa
# --------------------------------------------------------------------------- #

IMPORTABLE_FIELDS: tuple[str, ...] = (
    "numero",
    "nombres",
    "apellidos",
    "cvu_tecnm",
    "archivo_cvu_tecnm",
    "cvu_conahcyt",
    "archivo_cvu_conahcyt",
    "correo_institucional",
    "telefono_particular",
    "correo_personal",
    "institucion_texto",
    "institucion_id",
    "rol",
    "scopus_author_id",
    "archivo_carta_compromiso",
    "linea_investigacion_texto",
    "linea_investigacion_id",
    "infraestructura",
    "programas_academicos",
)
"""Campos de `members` que alimenta el Excel. Son los únicos que la importación puede cambiar."""


@dataclass
class RowWarning:
    codigo: str
    mensaje: str
    campo: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"codigo": self.codigo, "mensaje": self.mensaje, "campo": self.campo}


@dataclass
class NormalizedRow:
    fila: int
    datos: dict[str, Any] | None
    """None si la fila se omite (sin nombre ni apellidos)."""
    advertencias: list[RowWarning]


def map_row(raw: dict[str, Any], fila: int, catalog: Catalog) -> NormalizedRow:
    row = {HEADER_LOOKUP[h]: v for h, v in ((normalize_header(k), v) for k, v in raw.items()) if h in HEADER_LOOKUP}
    warnings: list[RowWarning] = []

    nombres = normalize_person_name(clean_cell(row.get("NOMBRES")))
    apellidos = normalize_person_name(clean_cell(row.get("APELLIDOS")))
    if not nombres and not apellidos:
        if any(clean_cell(v) for v in row.values()):
            warnings.append(RowWarning("fila-sin-nombre", "La fila tiene datos pero no tiene nombre ni apellidos."))
        return NormalizedRow(fila, None, warnings)

    numero_texto = clean_identifier(row.get("No."))
    numero = int(numero_texto) if numero_texto and numero_texto.isdigit() else None
    if numero_texto and numero is None:
        warnings.append(RowWarning("numero-invalido", f'El número "{numero_texto}" no es válido.', "numero"))

    correo_inst = normalize_email(row.get("CORREO INSTITUCIONAL"))
    if correo_inst.invalid:
        warnings.append(
            RowWarning("correo-invalido", "El correo institucional no tiene un formato válido.", "correo_institucional")
        )
    correo_pers = normalize_email(row.get("CORREO PERSONAL"))
    if correo_pers.invalid:
        warnings.append(
            RowWarning("correo-invalido", "El correo personal no tiene un formato válido.", "correo_personal")
        )

    institucion_texto, institucion_id = normalize_institution(row.get("INSTITUCIÓN DE ADSCRIPCIÓN"), catalog)
    if institucion_texto and not institucion_id:
        warnings.append(
            RowWarning(
                "institucion-no-reconocida",
                f'La institución "{institucion_texto}" no está en el catálogo.',
                "institucion_id",
            )
        )

    linea_texto = clean_cell(row.get("LÍNEA DE INVESTIGACIÓN PROPUESTA PARA TRABAJAR EN LA RED"))
    linea = resolve_research_line(linea_texto, catalog)
    if linea_texto and not linea.linea_id:
        warnings.append(
            RowWarning(
                "linea-ambigua" if linea.ambigua else "linea-no-reconocida",
                f'La línea "{linea_texto}" coincide con varias líneas oficiales; requiere revisión.'
                if linea.ambigua
                else f'La línea "{linea_texto}" no corresponde a ninguna línea oficial.',
                "linea_investigacion_id",
            )
        )

    datos = {
        "numero": numero,
        "nombres": nombres,
        "apellidos": apellidos,
        "cvu_tecnm": clean_identifier(row.get("NÚMERO CVU TecNM")),
        "archivo_cvu_tecnm": clean_cell(row.get("Nombre del archivo del CVU de TecNM enviado")),
        "cvu_conahcyt": clean_identifier(row.get("NÚMERO CVU de CONAHCyT PARA Profesores externos")),
        "archivo_cvu_conahcyt": clean_cell(row.get("Nombre del archivo del CVU de CONAHCYT enviado")),
        "correo_institucional": correo_inst.email,
        "telefono_particular": clean_identifier(row.get("TELÉFONO PARTICULAR")),
        "correo_personal": correo_pers.email,
        "institucion_texto": institucion_texto,
        "institucion_id": institucion_id,
        "rol": normalize_role(row.get("ROLL")),
        "scopus_author_id": clean_identifier(row.get("Scopus Author Identifier")),
        "archivo_carta_compromiso": clean_cell(row.get("NOMBRE DEL ARCHIVO DE CARTA COMPROMISO ENVIADO")),
        "linea_investigacion_texto": linea_texto,
        "linea_investigacion_id": linea.linea_id,
        "infraestructura": clean_cell(row.get("Infraestructura que aporta su IT a la Red")),
        "programas_academicos": clean_cell(
            row.get("Programas académicos de licenciatura y de posgrado que asociará a la red")
        ),
    }
    return NormalizedRow(fila, datos, warnings)


def unknown_columns(headers: list[str]) -> list[str]:
    return [h for h in headers if h and normalize_header(h) not in HEADER_LOOKUP]


def name_key(nombres: str, apellidos: str) -> str:
    return to_search_key(f"{nombres} {apellidos}")


def member_slug(nombres: str, apellidos: str) -> str:
    base = slugify(f"{nombres} {apellidos}")
    return f"m-{base}" if base else "m-sin-nombre"
