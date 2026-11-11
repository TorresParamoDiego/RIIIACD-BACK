"""Sesiones del panel administrativo: cookie HttpOnly + token CSRF."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AdminContext, client_ip, current_admin, rate_limit
from app.api.errors import ApiError
from app.config import get_settings
from app.core import clock
from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import DUMMY_HASH, new_token, token_hash, verify_password
from app.db import get_db
from app.models import AdminSession, AdminUser
from app.schemas.admin import AdminUserOut, LoginIn, LoginOut
from app.schemas.common import ErrorResponse
from app.services import audit

router = APIRouter(prefix="/admin/auth", tags=["administración: sesión"])


def user_out(user: AdminUser) -> AdminUserOut:
    return AdminUserOut(
        id=str(user.id),
        correo=user.correo,
        nombre=user.nombre,
        rol=user.rol,
        permisos=sorted(p.value for p in ROLE_PERMISSIONS.get(user.rol, frozenset())),
    )


@router.post(
    "/login",
    response_model=LoginOut,
    dependencies=[Depends(rate_limit("login", "login_rate_limit", "login_rate_window_seconds"))],
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def login(payload: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> LoginOut:
    settings = get_settings()
    user = db.scalar(select(AdminUser).where(AdminUser.correo == str(payload.correo).lower()))
    valid = verify_password(user.password_hash if user else DUMMY_HASH, payload.contrasena)
    if user is None or not valid or not user.activo:
        audit.record(
            db, None, "sesion.fallida", "admin_users", None, {"correo": str(payload.correo).lower()}, client_ip(request)
        )
        db.commit()
        raise ApiError(401, "credenciales-invalidas", "Correo o contraseña incorrectos.")

    token, csrf = new_token(), new_token()
    session = AdminSession(
        user_id=user.id,
        token_hash=token_hash(token),
        csrf_hash=token_hash(csrf),
        expira_en=clock.now() + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(session)
    audit.record(db, user.id, "sesion.iniciar", "admin_users", user.id, {}, client_ip(request))
    db.commit()

    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        settings.session_cookie_name, token, max_age=max_age, httponly=True,
        secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite, path="/admin",
    )  # fmt: skip
    # Cookie legible por el panel (no HttpOnly) para el patrón de doble envío.
    response.set_cookie(
        settings.csrf_cookie_name, csrf, max_age=max_age, httponly=False,
        secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite, path="/",
    )  # fmt: skip
    return LoginOut(usuario=user_out(user), csrf_token=csrf)


@router.post("/logout", status_code=204, response_class=Response)
def logout(response: Response, ctx: AdminContext = Depends(current_admin), db: Session = Depends(get_db)) -> Response:
    session = db.get(AdminSession, ctx.session.id)
    if session:
        session.revocado_en = clock.now()
    audit.record(db, ctx.user.id, "sesion.cerrar", "admin_users", ctx.user.id, {}, ctx.ip)
    db.commit()
    settings = get_settings()
    response.delete_cookie(settings.session_cookie_name, path="/admin")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=AdminUserOut)
def me(ctx: AdminContext = Depends(current_admin)) -> AdminUserOut:
    return user_out(ctx.user)
