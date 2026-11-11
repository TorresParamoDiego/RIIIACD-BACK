"""Autenticación, CSRF, roles, auditoría de consultas y formulario de contacto."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import create_app
from app.models import AuditLog, ContactMessage
from tests.conftest import ADMIN_PASSWORD, build_xlsx, import_file, person


def test_sin_sesion_401(client):
    assert client.get("/admin/members").status_code == 401
    assert client.post("/admin/invitations", json={"integranteId": "x"}).status_code == 401


def test_credenciales_invalidas(client, make_admin):
    make_admin("coordinacion")
    r = client.post("/admin/auth/login", json={"correo": "coordinacion@celaya.tecnm.mx", "contrasena": "mala"})
    assert r.status_code == 401 and r.json()["detail"]["codigo"] == "credenciales-invalidas"


def test_cookie_httponly_y_csrf(make_admin):
    admin = make_admin("coordinacion")
    me = admin.get("/admin/auth/me")
    assert me.status_code == 200 and me.json()["rol"] == "coordinacion"

    fresh = TestClient(create_app())
    r = fresh.post("/admin/auth/login", json={"correo": "coordinacion@celaya.tecnm.mx", "contrasena": ADMIN_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "path=/admin" in cookie and "samesite=lax" in cookie
    # Sin encabezado CSRF, una escritura se rechaza aunque la cookie sea válida.
    assert fresh.post("/admin/invitations", json={"integranteId": "x"}).status_code == 403
    fresh.headers["X-CSRF-Token"] = "token-falso"
    assert fresh.post("/admin/invitations", json={"integranteId": "x"}).status_code == 403
    fresh.headers["X-CSRF-Token"] = r.json()["csrfToken"]
    fresh.headers["Origin"] = "https://sitio-malicioso.example"
    assert fresh.post("/admin/invitations", json={"integranteId": "x"}).status_code == 403


def test_logout_revoca_la_sesion(make_admin):
    admin = make_admin("coordinacion")
    assert admin.post("/admin/auth/logout").status_code == 204
    assert admin.get("/admin/auth/me").status_code == 401


def test_consultar_datos_personales_queda_auditado(admin, db):
    import_file(admin, build_xlsx([person(1)]))
    slug = admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]
    data = admin.get(f"/admin/members/{slug}").json()
    assert data["cvuTecnm"] == "100001"  # el panel sí ve los datos administrativos
    acciones = [a.accion for a in db.scalars(select(AuditLog))]
    assert "integrantes.consultar" in acciones and "integrante.consultar" in acciones


def test_contacto_guarda_y_campo_trampa(client, db):
    ok = {
        "nombre": "Ana Pérez",
        "correo": "ana@example.com",
        "asunto": "Colaboración",
        "mensaje": "Quisiera sumar a mi institución.",
    }
    assert client.post("/contact", json=ok).status_code == 202
    assert client.post("/contact", json={**ok, "sitioWeb": "http://spam.example"}).status_code == 202
    msgs = db.scalars(select(ContactMessage)).all()
    assert len(msgs) == 1 and msgs[0].ip_hash and msgs[0].ip_hash != "testclient"


def test_contacto_limite_de_solicitudes(client):
    ok = {
        "nombre": "Ana Pérez",
        "correo": "ana@example.com",
        "asunto": "Colaboración",
        "mensaje": "Quisiera sumar a mi institución.",
    }
    statuses = [client.post("/contact", json=ok).status_code for _ in range(6)]
    assert statuses[:5] == [202] * 5 and statuses[5] == 429


def test_cabeceras_de_seguridad(client):
    r = client.get("/members")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/invitations/x").headers["cache-control"] == "no-store"
