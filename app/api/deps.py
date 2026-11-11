from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.config import get_settings
from app.core import clock
from app.core.permissions import Permission, has_permission
from app.core.security import rate_limiter, token_hash
from app.db import get_db
from app.models import AdminSession, AdminUser

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconocida"


def rate_limit(scope: str, limit_attr: str, window_attr: str) -> Callable[[Request], None]:
    def dependency(request: Request) -> None:
        s = get_settings()
        if not rate_limiter.allow(f"{scope}:{client_ip(request)}", getattr(s, limit_attr), getattr(s, window_attr)):
            raise ApiError(
                429, "demasiadas-solicitudes", "Demasiadas solicitudes. Inténtalo más tarde.",
                {"Retry-After": str(getattr(s, window_attr))},
            )  # fmt: skip

    return dependency


@dataclass
class AdminContext:
    user: AdminUser
    session: AdminSession
    ip: str


def current_admin(request: Request, db: Session = Depends(get_db)) -> AdminContext:
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise ApiError(401, "no-autenticado", "Inicia sesión para continuar.")
    session = db.scalar(select(AdminSession).where(AdminSession.token_hash == token_hash(token)))
    if (
        session is None
        or session.revocado_en is not None
        or session.expira_en <= clock.now()
        or not session.user.activo
    ):
        raise ApiError(401, "sesion-invalida", "La sesión caducó. Inicia sesión de nuevo.")

    if request.method not in SAFE_METHODS:
        # CSRF: el encabezado debe coincidir con el token de la sesión (patrón de token sincronizado)
        # y, si el navegador envía Origin, debe ser el del panel.
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not hmac.compare_digest(token_hash(csrf), session.csrf_hash):
            raise ApiError(403, "csrf-invalido", "Falta o no coincide el token CSRF.")
        origin = request.headers.get("origin")
        if origin and origin not in settings.admin_origins:
            raise ApiError(403, "origen-no-permitido", "Origen no permitido.")
    return AdminContext(user=session.user, session=session, ip=client_ip(request))


def require(permission: Permission) -> Callable[..., AdminContext]:
    def dependency(ctx: AdminContext = Depends(current_admin)) -> AdminContext:
        if not has_permission(ctx.user.rol, permission):
            raise ApiError(403, "permiso-insuficiente", "Tu rol no permite esta acción.")
        return ctx

    return dependency
