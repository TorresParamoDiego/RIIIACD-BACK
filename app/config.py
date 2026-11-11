"""Configuración por variables de entorno (ver .env.example)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    entorno: Literal["desarrollo", "pruebas", "produccion"] = "desarrollo"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/riiiacd"
    secret_key: str = Field(default="cambia-esta-clave-en-produccion", min_length=16)

    # URLs públicas
    public_site_url: str = "http://localhost:5173"
    """Sitio público (frontend). Los enlaces de invitación apuntan a {public_site_url}/actualizar/{token}."""
    api_public_url: str = "http://localhost:8000"
    """URL con la que el navegador alcanza esta API; se usa para construir las URL de las fotografías."""
    cors_origins: list[str] = ["http://localhost:5173"]
    admin_origins: list[str] = ["http://localhost:5174"]
    """Origen del panel administrativo (aplicación separada). Se permite con credenciales."""

    # Invitaciones (decisión cerrada: 24 horas, un solo uso)
    invitation_ttl_hours: int = 24

    # Sesiones administrativas
    session_ttl_hours: int = 8
    session_cookie_name: str = "riiiacd_session"
    csrf_cookie_name: str = "riiiacd_csrf"
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Fotografías. PENDIENTE (brief 4.9): confirmar el tamaño máximo de 2 MB.
    photo_max_bytes: int = 2 * 1024 * 1024
    photo_max_dimension: int = 1200
    storage_backend: Literal["local", "s3"] = "local"
    media_root: Path = Path("media")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None

    # SMTP institucional. PENDIENTE (brief 4.2): disponibilidad y credenciales.
    # Sin SMTP_HOST, los enlaces se comparten de forma manual.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True

    # Texto de autorización del correo público.
    # PENDIENTE (brief 4.8): lo redacta la institución junto con el aviso de privacidad.
    consent_text_version: str = "borrador-0"
    consent_text: str = (
        "Autorizo a la RIIIACD a publicar mi correo institucional en mi ficha del directorio "
        "público. Puedo retirar esta autorización en cualquier momento."
    )

    # Límites de solicitudes (por IP, en memoria del proceso)
    contact_rate_limit: int = 5
    contact_rate_window_seconds: int = 3600
    login_rate_limit: int = 10
    login_rate_window_seconds: int = 900
    invitation_rate_limit: int = 30
    invitation_rate_window_seconds: int = 3600
    trust_proxy_headers: bool = False
    """Usar X-Forwarded-For para la IP del cliente (solo detrás de un proxy de confianza)."""

    members_max_page_size: int = 100


@lru_cache
def get_settings() -> Settings:
    return Settings()
