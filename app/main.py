"""Aplicación FastAPI del portal RIIIACD."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __doc__ as app_doc
from app.api import admin, auth, errors, invitations, public
from app.config import get_settings

PRIVATE_PREFIXES = ("/admin", "/invitations")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="RIIIACD API",
        version="0.1.0",
        description=(
            f"{app_doc} Directorio público, actualización de perfiles por enlace único, "
            "importación del padrón y cola de aprobación."
        ),
        docs_url=None if settings.entorno == "produccion" else "/docs",
        redoc_url=None,
    )
    errors.install(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[*settings.cors_origins, *settings.admin_origins],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith(PRIVATE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(public.router)
    app.include_router(invitations.router)
    app.include_router(auth.router)
    app.include_router(admin.router)

    @app.get("/health", tags=["sistema"], include_in_schema=False)
    def health() -> dict[str, str]:
        return {"estado": "ok"}

    return app


app = create_app()
