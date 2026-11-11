"""Reglas de fotografías (brief 3.4)."""

from __future__ import annotations

import io

from PIL import Image
from sqlalchemy import select

from app.models import Photo
from app.services.storage import get_storage
from tests.conftest import build_xlsx, import_file, jpeg_with_exif, person


def _setup(admin):
    import_file(admin, build_xlsx([person(1)]))
    slug = admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]
    return slug


def _token(admin, slug):
    return admin.post("/admin/invitations", json={"integranteId": slug}).json()["enlace"].rsplit("/", 1)[1]


def _upload(client, token, data, name="foto.jpg", ctype="image/jpeg"):
    return client.put(f"/invitations/{token}/photo", files={"foto": (name, data, ctype)})


def test_reencodifica_y_elimina_exif(admin, client, db):
    original = jpeg_with_exif()
    assert Image.open(io.BytesIO(original)).getexif()  # el original sí trae EXIF
    slug = _setup(admin)
    token = _token(admin, slug)
    r = _upload(client, token, original)
    assert r.status_code == 201 and r.json()["estado"] == "pendiente"

    photo = db.scalars(select(Photo)).one()
    assert photo.clave_almacenamiento.startswith("photos/") and photo.clave_almacenamiento.endswith(".webp")
    assert "foto" not in photo.clave_almacenamiento.split("/")[1]  # nombre aleatorio, no el original
    stored = get_storage().get(photo.clave_almacenamiento)
    img = Image.open(io.BytesIO(stored))
    assert img.format == "WEBP"
    assert not img.getexif()
    assert b"CamaraDePrueba" not in stored and "exif" not in img.info


def test_solo_se_publica_tras_aprobacion_y_la_anterior_queda_reemplazada(admin, client, db):
    slug = _setup(admin)
    token = _token(admin, slug)
    photo_id = _upload(client, token, jpeg_with_exif()).json()["fotoId"]
    client.post(f"/invitations/{token}/changes", json={})  # solo foto
    assert client.get(f"/media/photos/{photo_id}").status_code == 404
    assert client.get(f"/members/{slug}").json()["fotoUrl"] is None

    req = admin.get("/admin/change-requests").json()[0]
    assert req["campo"] == "foto" and req["fotoUrlAdmin"]
    assert admin.get(req["fotoUrlAdmin"]).status_code == 200  # la coordinación sí la ve
    admin.post(f"/admin/change-requests/{req['id']}/approve")
    public_url = client.get(f"/members/{slug}").json()["fotoUrl"]
    assert public_url.endswith(photo_id)
    r = client.get(f"/media/photos/{photo_id}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"

    token2 = _token(admin, slug)
    new_id = _upload(client, token2, jpeg_with_exif((80, 80))).json()["fotoId"]
    client.post(f"/invitations/{token2}/changes", json={})
    admin.post(f"/admin/change-requests/{admin.get('/admin/change-requests').json()[0]['id']}/approve")
    db.expire_all()
    estados = {str(p.id): p.estado for p in db.scalars(select(Photo))}
    assert estados == {photo_id: "reemplazada", new_id: "aprobada"}
    assert client.get(f"/media/photos/{photo_id}").status_code == 404
    assert client.get(f"/media/photos/{new_id}").status_code == 200


def test_formatos_y_tamano(admin, client):
    token = _token(admin, _setup(admin))
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    r = _upload(client, token, gif.getvalue(), "a.gif", "image/gif")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "formato-no-permitido"
    r = _upload(client, token, b"<?php echo 1; ?>", "falsa.jpg", "image/jpeg")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "imagen-invalida"
    r = _upload(client, token, b"\xff" * (2 * 1024 * 1024 + 1))
    assert r.status_code == 413
    png = io.BytesIO()
    Image.new("RGBA", (10, 10)).save(png, format="PNG")
    assert _upload(client, token, png.getvalue(), "a.png", "image/png").status_code == 201


def test_rechazar_foto_borra_el_archivo(admin, client, db):
    slug = _setup(admin)
    token = _token(admin, slug)
    _upload(client, token, jpeg_with_exif())
    client.post(f"/invitations/{token}/changes", json={})
    req = admin.get("/admin/change-requests").json()[0]
    admin.post(f"/admin/change-requests/{req['id']}/reject", json={"motivo": "No es una foto de rostro"})
    db.expire_all()
    photo = db.scalars(select(Photo)).one()
    assert photo.estado == "rechazada"
    assert not get_storage().exists(photo.clave_almacenamiento)
