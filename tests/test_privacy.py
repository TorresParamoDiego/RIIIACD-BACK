"""Criterio: ningún endpoint público devuelve CVU, teléfono, correo personal ni archivos."""

from __future__ import annotations

import json
import re

from app.main import create_app
from app.schemas.public import PUBLIC_MEMBER_FIELDS
from tests.conftest import build_xlsx, import_file, jpeg_with_exif, person

SENSITIVE_VALUES = ["100001", "4610000001", "persona1@correo-personal.com", "cvu_1.pdf", "carta_1.pdf", "987654"]
SENSITIVE_KEYS = re.compile(
    r'"(cvu\w*|telefono\w*|correoPersonal|correo_personal|archivo\w*|cartaCompromiso\w*|scopus\w*|infraestructura|'
    r'programasAcademicos|correoInstitucional|solicitaCorreoPublico)"',
    re.IGNORECASE,
)
PUBLIC_PREFIXES_EXCLUDED = ("/admin", "/invitations", "/docs", "/openapi", "/health")


def _setup_member_with_everything(admin, client):
    import_file(admin, build_xlsx([person(1, **{"NÚMERO CVU de CONAHCyT PARA Profesores externos": 987654,
                                               "Scopus Author Identifier": 57200000001})]))  # fmt: skip
    slug = admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]
    # Foto aprobada, biografía aprobada y correo autorizado: el máximo de datos públicos posible.
    link = admin.post("/admin/invitations", json={"integranteId": slug, "envio": "manual"}).json()["enlace"]
    token = link.rsplit("/", 1)[1]
    client.put(f"/invitations/{token}/photo", files={"foto": ("f.jpg", jpeg_with_exif(), "image/jpeg")})
    client.post(f"/invitations/{token}/changes", json={"biografia": "Bio", "solicitaCorreoPublico": True,
                                                       "versionTextoConsentimiento": "borrador-0"})  # fmt: skip
    for req in admin.get("/admin/change-requests").json():
        assert admin.post(f"/admin/change-requests/{req['id']}/approve").status_code == 200
    return slug


def test_ningun_endpoint_publico_expone_datos_administrativos(admin, client):
    slug = _setup_member_with_everything(admin, client)
    member = client.get(f"/members/{slug}").json()
    photo_id = member["fotoUrl"].rsplit("/", 1)[1]
    assert member["correoPublico"] == "persona1@celaya.tecnm.mx"  # autorizado y aprobado

    # Se recorren TODAS las rutas GET documentadas: una ruta pública nueva queda cubierta sin tocar la prueba.
    paths = [
        p
        for p, ops in create_app().openapi()["paths"].items()
        if "get" in ops and not p.startswith(PUBLIC_PREFIXES_EXCLUDED)
    ]
    assert {"/members", "/members/{member_id}", "/institutions", "/research-lines", "/statistics"} <= set(paths)
    checked = 0
    for route_path in paths:
        path = route_path.replace("{member_id}", slug).replace("{photo_id}", photo_id)
        for query in ("", "?porPagina=100", "?q=persona1"):
            r = client.get(path + query)
            assert r.status_code == 200, (path, r.status_code)
            if r.headers["content-type"].startswith("image/"):
                continue
            body = r.text
            for value in SENSITIVE_VALUES:
                assert value not in body, f"{path} expone {value}"
            assert not SENSITIVE_KEYS.search(body), f"{path} expone un campo administrativo"
            checked += 1
    assert checked >= 6


def test_el_esquema_openapi_de_publicmember_es_la_lista_blanca():
    schema = create_app().openapi()["components"]["schemas"]["PublicMember"]
    props = set(schema["properties"])
    allowed_aliases = {
        "id", "tratamiento", "nombres", "apellidos", "nombreCompleto", "institucionId", "rol",
        "lineaInvestigacionId", "correoPublico", "biografia", "perfilProfesional", "lineasPropuestas",
        "fotoUrl", "perfilAcademicoUrl", "esDemostrativo",
    }  # fmt: skip
    assert props == allowed_aliases
    assert len(PUBLIC_MEMBER_FIELDS) == len(allowed_aliases)
    assert schema.get("additionalProperties") is False
    text = json.dumps(create_app().openapi()["paths"]["/members"])
    assert not SENSITIVE_KEYS.search(text)


def test_el_correo_solo_se_publica_con_autorizacion_aprobada(admin, client):
    import_file(admin, build_xlsx([person(1)]))
    slug = client.get("/members", params={"q": "persona1"}).json()["items"][0]["id"]
    assert client.get(f"/members/{slug}").json()["correoPublico"] is None

    token = admin.post("/admin/invitations", json={"integranteId": slug}).json()["enlace"].rsplit("/", 1)[1]
    client.post(
        f"/invitations/{token}/changes",
        json={"solicitaCorreoPublico": True, "versionTextoConsentimiento": "borrador-0"},
    )
    # Solicitud pendiente: todavía no se publica.
    assert client.get(f"/members/{slug}").json()["correoPublico"] is None
    req = admin.get("/admin/change-requests").json()[0]
    admin.post(f"/admin/change-requests/{req['id']}/approve")
    assert client.get(f"/members/{slug}").json()["correoPublico"] == "persona1@celaya.tecnm.mx"


def test_integrante_inactivo_u_oculto_responde_404(admin, client):
    import_file(admin, build_xlsx([person(1), person(2)]))
    slugs = {m["nombres"]: m["id"] for m in client.get("/members", params={"porPagina": 100}).json()["items"]}
    admin.patch(f"/admin/members/{slugs['Persona1']}", json={"esPublico": False})
    admin.patch(f"/admin/members/{slugs['Persona2']}", json={"activo": False})
    assert client.get(f"/members/{slugs['Persona1']}").status_code == 404
    assert client.get(f"/members/{slugs['Persona2']}").status_code == 404
    assert client.get("/members/no-existe").status_code == 404
    names = {m["nombres"] for m in client.get("/members", params={"porPagina": 100}).json()["items"]}
    assert not {"Persona1", "Persona2"} & names


def test_filtros_y_paginacion_en_servidor(admin, client):
    import_file(
        admin,
        build_xlsx(
            [
                person(i, **({"LÍNEA DE INVESTIGACIÓN PROPUESTA PARA TRABAJAR EN LA RED": "Línea 3"} if i % 2 else {}))
                for i in range(1, 8)
            ]
        ),
    )
    # Personas 2, 4 y 6 en línea 2. Los integrantes de la semilla no vienen en el archivo: quedan desactivados.
    page = client.get("/members", params={"porPagina": 2, "pagina": 2, "linea": "linea-2"}).json()
    assert page["total"] == 3
    assert page["porPagina"] == 2 and page["pagina"] == 2 and page["totalPaginas"] == 2
    assert len(page["items"]) == 1
    assert client.get("/members", params={"q": "PERSONA3"}).json()["total"] == 1
    assert client.get("/members", params={"porPagina": 1000}).json()["porPagina"] == 100
    ordered = client.get("/members", params={"orden": "apellidos-desc", "porPagina": 100}).json()["items"]
    assert [m["apellidos"] for m in ordered] == sorted([m["apellidos"] for m in ordered], reverse=True)
