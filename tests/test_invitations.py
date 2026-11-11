"""Criterio: un enlace caduca a las 24 horas y no puede usarse dos veces."""

from __future__ import annotations

from app.services import mailer
from tests.conftest import build_xlsx, import_file, person


def _slug(admin):
    import_file(admin, build_xlsx([person(1)]))
    return admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]


def _token(admin, slug, envio="manual"):
    r = admin.post("/admin/invitations", json={"integranteId": slug, "envio": envio})
    assert r.status_code == 201, r.text
    return r.json()


def test_validar_no_consume_y_enviar_si(admin, client):
    token = _token(admin, _slug(admin))["enlace"].rsplit("/", 1)[1]
    for _ in range(2):
        r = client.get(f"/invitations/{token}")
        assert r.status_code == 200
    data = r.json()
    assert data["integrante"]["correoInstitucionalEnmascarado"] == "p•••@celaya.tecnm.mx"
    assert data["foto"]["tamanoMaximoBytes"] == 2 * 1024 * 1024
    assert "cvu" not in r.text.lower()

    first = client.post(f"/invitations/{token}/changes", json={"biografia": "Investigadora."})
    assert first.status_code == 201 and first.json()["solicitudesCreadas"] == 1
    second = client.post(f"/invitations/{token}/changes", json={"biografia": "Otra vez."})
    assert second.status_code == 410 and second.json()["detail"]["codigo"] == "invitacion-usada"
    assert client.get(f"/invitations/{token}").json()["detail"]["codigo"] == "invitacion-usada"
    assert client.put(f"/invitations/{token}/photo", files={"foto": ("a.jpg", b"x", "image/jpeg")}).status_code == 410


def test_caduca_a_las_24_horas(admin, client, fake_clock):
    token = _token(admin, _slug(admin))["enlace"].rsplit("/", 1)[1]
    fake_clock.advance(hours=23, minutes=59)
    assert client.get(f"/invitations/{token}").status_code == 200
    fake_clock.advance(minutes=1)
    r = client.get(f"/invitations/{token}")
    assert r.status_code == 410 and r.json()["detail"]["codigo"] == "invitacion-expirada"
    assert client.post(f"/invitations/{token}/changes", json={"biografia": "x"}).status_code == 410


def test_un_enlace_nuevo_revoca_el_anterior(admin, client):
    slug = _slug(admin)
    old = _token(admin, slug)["enlace"].rsplit("/", 1)[1]
    new = _token(admin, slug)["enlace"].rsplit("/", 1)[1]
    assert client.get(f"/invitations/{old}").status_code == 404
    assert client.get(f"/invitations/{new}").status_code == 200


def test_token_inexistente(client):
    r = client.get("/invitations/token-que-no-existe")
    assert r.status_code == 404 and r.json()["detail"]["codigo"] == "invitacion-invalida"


def test_sin_smtp_se_devuelve_el_enlace_para_compartirlo(admin):
    data = _token(admin, _slug(admin), envio="smtp")
    assert data["enviado"] is False
    assert data["enlace"].startswith("http://localhost:5173/actualizar/")
    assert "SMTP" in data["motivoNoEnviado"]


def test_con_smtp_se_envia_y_no_se_devuelve_el_enlace(admin, monkeypatch):
    sent = {}
    monkeypatch.setattr(mailer, "is_configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body: sent.update(to=to, body=body) or True)
    data = _token(admin, _slug(admin), envio="smtp")
    assert data["enviado"] is True and data["enlace"] is None
    assert sent["to"] == "persona1@celaya.tecnm.mx"
    assert "/actualizar/" in sent["body"] and "24 horas" in sent["body"]


def test_el_token_no_se_guarda_en_claro(admin, db):
    from sqlalchemy import select

    from app.models import Invitation

    token = _token(admin, _slug(admin))["enlace"].rsplit("/", 1)[1]
    inv = db.scalars(select(Invitation)).one()
    assert inv.token_hash != token and len(inv.token_hash) == 64
