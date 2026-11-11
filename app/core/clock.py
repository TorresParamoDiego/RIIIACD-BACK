"""Reloj único de la aplicación. Las pruebas lo sustituyen para simular el paso del tiempo."""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)
