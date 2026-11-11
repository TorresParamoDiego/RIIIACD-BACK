"""Cola de aprobación e historial. Criterio: toda aprobación, rechazo y edición queda en el historial."""

from __future__ import annotations

from sqlalchemy import select

from app.models import AuditLog, FieldLock, MemberHistory
from tests.conftest import build_xlsx, import_file, person


def _member_and_token(admin):
    import_file(admin, build_xlsx([person(1)]))
    slug = admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]
    token = admin.post("/admin/invitations", json={"integranteId": slug}).json()["enlace"].rsplit("/", 1)[1]
    return slug, token


def test_nada_se_publica_sin_aprobacion(admin, client):
    slug, token = _member_and_token(admin)
    r = client.post(
        f"/invitations/{token}/changes",
        json={
            "biografia": "Doctora en IA.",
            "perfilProfesional": "Profesora investigadora",
            "lineasPropuestas": ["linea-1", "linea-2"],
        },
    )
    assert r.json()["solicitudesCreadas"] == 3
    public = client.get(f"/members/{slug}").json()
    assert public["biografia"] is None and public["lineasPropuestas"] == []

    queue = admin.get("/admin/change-requests").json()
    assert {q["campo"] for q in queue} == {"biografia", "perfil_profesional", "lineas_propuestas"}
    # La línea principal (linea-2) no se repite entre las propuestas.
    assert next(q for q in queue if q["campo"] == "lineas_propuestas")["valorPropuesto"] == ["linea-1"]
    for q in queue:
        assert admin.post(f"/admin/change-requests/{q['id']}/approve").status_code == 200
    public = client.get(f"/members/{slug}").json()
    assert public["biografia"] == "Doctora en IA."
    assert public["perfilProfesional"] == "Profesora investigadora"
    assert public["lineasPropuestas"] == ["linea-1"]


def test_aprobacion_y_rechazo_quedan_en_historial_y_auditoria(admin, client, db):
    slug, token = _member_and_token(admin)
    client.post(f"/invitations/{token}/changes", json={"biografia": "Bio", "perfilProfesional": "Perfil"})
    queue = {q["campo"]: q for q in admin.get("/admin/change-requests").json()}

    assert admin.post(f"/admin/change-requests/{queue['biografia']['id']}/approve").status_code == 200
    r = admin.post(
        f"/admin/change-requests/{queue['perfil_profesional']['id']}/reject", json={"motivo": "Texto incompleto"}
    )
    assert r.status_code == 200 and r.json()["motivoRechazo"] == "Texto incompleto"
    assert admin.post(f"/admin/change-requests/{queue['biografia']['id']}/approve").status_code == 409

    history = admin.get(f"/admin/members/{slug}/history").json()
    origins = [(h["campo"], h["origen"]) for h in history]
    assert ("biografia", "aprobacion") in origins
    assert ("perfil_profesional", "rechazo") in origins
    admin.patch(f"/admin/members/{slug}", json={"perfilProfesional": "Editado por la coordinación"})
    history = admin.get(f"/admin/members/{slug}/history").json()
    assert ("perfil_profesional", "edicion_admin") in [(h["campo"], h["origen"]) for h in history]

    acciones = set(db.scalars(select(AuditLog.accion)))
    assert {"solicitud.aprobar", "solicitud.rechazar", "integrante.editar", "integrante.consultar"} <= acciones or {
        "solicitud.aprobar", "solicitud.rechazar", "integrante.editar", "integrante.historial"
    } <= acciones  # fmt: skip
    locks = {lock.campo: lock.origen for lock in db.scalars(select(FieldLock))}
    assert locks["biografia"] == "aprobacion"
    assert locks["perfil_profesional"] == "edicion_admin"
    assert db.scalar(select(MemberHistory).where(MemberHistory.origen == "rechazo")) is not None


def test_rechazo_requiere_motivo(admin, client):
    _, token = _member_and_token(admin)
    client.post(f"/invitations/{token}/changes", json={"biografia": "Bio"})
    req = admin.get("/admin/change-requests").json()[0]
    assert admin.post(f"/admin/change-requests/{req['id']}/reject", json={"motivo": ""}).status_code == 422


def test_biografia_maximo_1000(admin, client):
    _, token = _member_and_token(admin)
    assert client.post(f"/invitations/{token}/changes", json={"biografia": "a" * 1001}).status_code == 422
    assert client.post(f"/invitations/{token}/changes", json={"biografia": "a" * 1000}).status_code == 201


def test_consentimiento_con_texto_desactualizado(admin, client):
    _, token = _member_and_token(admin)
    r = client.post(
        f"/invitations/{token}/changes", json={"solicitaCorreoPublico": True, "versionTextoConsentimiento": "v-vieja"}
    )
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "consentimiento-desactualizado"


def test_sin_cambios_no_consume_el_enlace(admin, client):
    _, token = _member_and_token(admin)
    r = client.post(f"/invitations/{token}/changes", json={})
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "sin-cambios"
    assert client.get(f"/invitations/{token}").status_code == 200


def test_retirar_autorizacion_de_correo(admin, client):
    slug, token = _member_and_token(admin)
    client.post(
        f"/invitations/{token}/changes",
        json={"solicitaCorreoPublico": True, "versionTextoConsentimiento": "borrador-0"},
    )
    admin.post(f"/admin/change-requests/{admin.get('/admin/change-requests').json()[0]['id']}/approve")
    assert client.get(f"/members/{slug}").json()["correoPublico"]

    token2 = admin.post("/admin/invitations", json={"integranteId": slug}).json()["enlace"].rsplit("/", 1)[1]
    assert client.get(f"/invitations/{token2}").json()["perfil"]["correoPublicoAutorizado"] is True
    client.post(f"/invitations/{token2}/changes", json={"solicitaCorreoPublico": False})
    admin.post(f"/admin/change-requests/{admin.get('/admin/change-requests').json()[0]['id']}/approve")
    assert client.get(f"/members/{slug}").json()["correoPublico"] is None


def test_permiso_de_revision(make_admin, admin, client):
    _, token = _member_and_token(admin)
    client.post(f"/invitations/{token}/changes", json={"biografia": "Bio"})
    consulta = make_admin("consulta")
    assert consulta.get("/admin/change-requests").status_code == 403
