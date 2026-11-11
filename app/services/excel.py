"""Lectura del archivo .xlsx de integrantes."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from app.services.normalization import normalize_header


class ExcelFormatError(ValueError):
    """El archivo no es un .xlsx legible o no tiene la hoja de integrantes."""


@dataclass
class SheetData:
    headers: list[str]
    rows: list[tuple[int, dict[str, Any]]]
    """(número de fila en Excel, {encabezado original: valor de celda})."""


HEADER_MARKERS = {"nombres", "apellidos"}
MAX_HEADER_SEARCH = 15
MAX_ROWS = 5000


def read_members_sheet(content: bytes) -> SheetData:
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
        raise ExcelFormatError("El archivo no es un libro de Excel (.xlsx) válido.") from exc

    try:
        for sheet in workbook.worksheets:
            data = _read_sheet(sheet)
            if data is not None:
                return data
    finally:
        workbook.close()
    raise ExcelFormatError('No se encontró una hoja con las columnas "NOMBRES" y "APELLIDOS".')


def _read_sheet(sheet: Any) -> SheetData | None:
    rows_iter = sheet.iter_rows(values_only=True)
    headers: list[str] | None = None
    header_row = 0
    for index, values in enumerate(rows_iter, start=1):
        normalized = {normalize_header(str(v)) for v in values if v is not None}
        if normalized >= HEADER_MARKERS:
            headers = ["" if v is None else str(v).strip() for v in values]
            header_row = index
            break
        if index >= MAX_HEADER_SEARCH:
            return None
    if headers is None:
        return None

    rows: list[tuple[int, dict[str, Any]]] = []
    for offset, values in enumerate(rows_iter, start=1):
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        if len(rows) >= MAX_ROWS:
            raise ExcelFormatError(f"El archivo supera el máximo de {MAX_ROWS} filas.")
        record = {headers[i]: values[i] for i in range(min(len(headers), len(values))) if headers[i]}
        rows.append((header_row + offset, record))
    return SheetData(headers=[h for h in headers if h], rows=rows)
