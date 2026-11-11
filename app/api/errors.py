"""Formato único de error: {"detail": {"codigo": "...", "mensaje": "..."}}."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.excel import ExcelFormatError
from app.services.importer import ImportError_
from app.services.invitations import InvitationError
from app.services.photos import PhotoError
from app.services.profiles import ProfileError


class ApiError(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str, headers: dict[str, str] | None = None) -> None:
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje
        self.headers = headers


def _response(status: int, codigo: str, mensaje: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"codigo": codigo, "mensaje": mensaje}}, headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError) -> JSONResponse:
        return _response(exc.status, exc.codigo, exc.mensaje, exc.headers)

    @app.exception_handler(InvitationError)
    async def _inv(_: Request, exc: InvitationError) -> JSONResponse:
        return _response(exc.status, exc.codigo, exc.mensaje)

    @app.exception_handler(ImportError_)
    async def _imp(_: Request, exc: ImportError_) -> JSONResponse:
        return _response(exc.status, exc.codigo, exc.mensaje)

    @app.exception_handler(ProfileError)
    async def _prof(_: Request, exc: ProfileError) -> JSONResponse:
        return _response(exc.status, exc.codigo, exc.mensaje)

    @app.exception_handler(PhotoError)
    async def _photo(_: Request, exc: PhotoError) -> JSONResponse:
        return _response(413 if exc.codigo == "foto-demasiado-grande" else 422, exc.codigo, exc.mensaje)

    @app.exception_handler(ExcelFormatError)
    async def _excel(_: Request, exc: ExcelFormatError) -> JSONResponse:
        return _response(422, "excel-invalido", str(exc))
