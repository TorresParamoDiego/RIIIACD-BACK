"""Procesamiento de fotografías (brief 3.4).

- Formatos aceptados: JPEG, PNG y WEBP, detectados por el CONTENIDO del
  archivo (no por la extensión ni por el Content-Type que envía el navegador).
- Tamaño máximo: PHOTO_MAX_BYTES (2 MB sugerido; PENDIENTE brief 4.9).
- La imagen se decodifica y se vuelve a codificar como WEBP: esto descarta
  los metadatos EXIF (incluida la ubicación GPS) y cualquier contenido
  incrustado. Se respeta la orientación EXIF antes de descartarla.
- Nombre de archivo aleatorio.
"""

from __future__ import annotations

import io
import secrets
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import get_settings

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
OUTPUT_CONTENT_TYPE = "image/webp"
MAX_PIXELS = 40_000_000
"""Protección contra 'bombas de descompresión'."""


class PhotoError(ValueError):
    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


@dataclass
class ProcessedPhoto:
    data: bytes
    content_type: str
    ancho: int
    alto: int
    clave: str


def random_key() -> str:
    return f"photos/{secrets.token_hex(16)}.webp"


def process_photo(raw: bytes) -> ProcessedPhoto:
    settings = get_settings()
    if not raw:
        raise PhotoError("foto-vacia", "No se recibió ninguna imagen.")
    if len(raw) > settings.photo_max_bytes:
        mb = settings.photo_max_bytes / (1024 * 1024)
        raise PhotoError("foto-demasiado-grande", f"La fotografía no puede pesar más de {mb:g} MB.")

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ALLOWED_FORMATS:
            raise PhotoError("formato-no-permitido", "La fotografía debe ser JPEG, PNG o WEBP.")
        with Image.open(io.BytesIO(raw)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img.thumbnail((settings.photo_max_dimension, settings.photo_max_dimension))
            out = io.BytesIO()
            # Sin `exif=` ni `icc_profile=`: la imagen nueva no lleva metadatos.
            img.save(out, format="WEBP", quality=85, method=4)
            ancho, alto = img.size
    except PhotoError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PhotoError("imagen-invalida", "El archivo no es una imagen válida.") from exc

    return ProcessedPhoto(out.getvalue(), OUTPUT_CONTENT_TYPE, ancho, alto, random_key())
