"""Infraestructura de pruebas.

Las pruebas corren contra PostgreSQL real (TEST_DATABASE_URL). Al iniciar la
sesión se borra el esquema y se aplican las migraciones de Alembic DESDE CERO:
así cada corrida comprueba también que las migraciones son reproducibles.
"""

from __future__ import annotations

import io
import os
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://postgres@/riiiacd_test?host=/tmp&port=5433")
os.environ["DATABASE_URL"] = TEST_DB
os.environ["ENTORNO"] = "pruebas"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["MEDIA_ROOT"] = tempfile.mkdtemp(prefix="riiiacd-media-")
os.environ["SECRET_KEY"] = "clave-de-pruebas-0123456789"
os.environ.pop("SMTP_HOST", None)

from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.cli import create_admin, seed  # noqa: E402
from app.core import clock  # noqa: E402
from app.core.security import rate_limiter  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.normalization import EXCEL_COLUMNS  # noqa: E402

ADMIN_PASSWORD = "contrasena-segura-de-pruebas"


@pytest.fixture(scope="session", autouse=True)
def migrated_db() -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = TEST_DB
    command.upgrade(cfg, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_db() -> Iterator[None]:
    with engine.begin() as conn:
        tables = conn.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version'")
        ).scalars()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    with SessionLocal() as db:
        seed(db)
    rate_limiter.reset()
    yield


@pytest.fixture
def db() -> Iterator[Any]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: float) -> None:
        self.current += timedelta(**kwargs)


@pytest.fixture
def fake_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fc = FakeClock()
    monkeypatch.setattr(clock, "now", fc)
    return fc


def login(client: TestClient, correo: str) -> TestClient:
    r = client.post("/admin/auth/login", json={"correo": correo, "contrasena": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    client.headers["X-CSRF-Token"] = r.json()["csrfToken"]
    return client


@pytest.fixture
def make_admin() -> Any:
    def factory(rol: str = "coordinacion", correo: str | None = None) -> TestClient:
        correo = correo or f"{rol}@celaya.tecnm.mx"
        with SessionLocal() as session:
            create_admin(session, correo, f"Usuario {rol}", rol, ADMIN_PASSWORD)
        return login(TestClient(create_app()), correo)

    return factory


@pytest.fixture
def admin(make_admin: Any) -> TestClient:
    return make_admin("coordinacion")


def build_xlsx(rows: list[dict[str, Any]], *, title_rows: int = 0) -> bytes:
    """Excel con los encabezados oficiales. `rows` usa los nombres de columna del Excel."""
    wb = Workbook()
    ws = wb.active
    for _ in range(title_rows):
        ws.append(["Registro de integrantes RIIIACD"])
    ws.append(list(EXCEL_COLUMNS))
    for i, row in enumerate(rows, start=1):
        ws.append([row.get(col, i if col == "No." else None) for col in EXCEL_COLUMNS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def person(n: int, **overrides: Any) -> dict[str, Any]:
    base = {
        "NOMBRES": f"Persona{n}",
        "APELLIDOS": f"Apellido{n} Prueba",
        "NÚMERO CVU TecNM": 100000 + n,  # número, como lo entrega Excel
        "CORREO INSTITUCIONAL": f"persona{n}@celaya.tecnm.mx",
        "TELÉFONO PARTICULAR": 4610000000 + n,
        "CORREO PERSONAL": f"persona{n}@correo-personal.com",
        "INSTITUCIÓN DE ADSCRIPCIÓN": "CENIDET",
        "ROLL": "integrante",
        "Nombre del archivo del CVU de TecNM enviado": f"cvu_{n}.pdf",
        "NOMBRE DEL ARCHIVO DE CARTA COMPROMISO ENVIADO": f"carta_{n}.pdf",
        "LÍNEA DE INVESTIGACIÓN PROPUESTA PARA TRABAJAR EN LA RED": "Línea 2",
    }
    base.update(overrides)
    return base


def upload(admin: TestClient, content: bytes, name: str = "integrantes.xlsx") -> Any:
    return admin.post(
        "/admin/imports",
        files={"archivo": (name, content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


def import_file(admin: TestClient, content: bytes) -> dict[str, Any]:
    r = upload(admin, content)
    assert r.status_code == 201, r.text
    c = admin.post(f"/admin/imports/{r.json()['id']}/confirm")
    assert c.status_code == 200, c.text
    return c.json()


def jpeg_with_exif(size: tuple[int, int] = (64, 48)) -> bytes:
    from PIL import Image

    img = Image.new("RGB", size, (27, 57, 106))
    exif = Image.Exif()
    exif[0x010F] = "CamaraDePrueba"  # Make
    exif[0x0132] = "2026:11:10 10:00:00"
    exif[0x8825] = {1: "N", 2: (20.0, 31.0, 0.0), 3: "W", 4: (100.0, 48.0, 0.0)}  # GPS
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()
