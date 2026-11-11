"""Almacenamiento de archivos detrás de una interfaz intercambiable (brief 1.3 y 3.4).

La lógica de negocio solo conoce `Storage`. Hoy se usa `LocalStorage`; para
migrar a un servicio S3-compatible basta con STORAGE_BACKEND=s3 y las
variables S3_*, sin tocar los servicios.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from app.config import Settings, get_settings

KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9/_-]*\.[a-z0-9]{2,5}$")


class StorageError(Exception):
    pass


def _check_key(key: str) -> str:
    if not KEY_PATTERN.match(key) or ".." in key:
        raise StorageError(f"Clave de almacenamiento no válida: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """Archivos en disco bajo MEDIA_ROOT. Las claves nunca salen de ese directorio."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / _check_key(key)).resolve()
        if self.root not in path.parents:
            raise StorageError("Ruta fuera del directorio de almacenamiento.")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"No existe: {key}") from exc

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3Storage:
    """Servicio S3-compatible (AWS S3, MinIO…). Requiere `pip install .[s3]`.

    Implementación preparada para la migración; aún no se ha probado contra un
    servicio real (no hay uno disponible en esta fase).
    """

    def __init__(self, settings: Settings) -> None:
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise StorageError("STORAGE_BACKEND=s3 requiere instalar boto3 (pip install .[s3]).") from exc
        if not settings.s3_bucket:
            raise StorageError("Falta S3_BUCKET.")
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise StorageError(f"No existe: {key}") from exc

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(settings)
    return LocalStorage(settings.media_root)
