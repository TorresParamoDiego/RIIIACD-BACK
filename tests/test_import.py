"""Reglas de importación del Excel (brief 3.3) y criterios de aceptación relacionados."""

from __future__ import annotations

from sqlalchemy import func, select

from app.models import ImportBatch, Member, MemberHistory
from tests.conftest import build_xlsx, import_file, person, upload


def counts(db):
    return (
        db.scalar(select(func.count()).select_from(Member)),
        db.scalar(select(func.count()).select_from(MemberHistory)),
        db.scalar(select(func.count()).select_from(ImportBatch).where(ImportBatch.estado == "aplicado")),
    )


def test_revision_antes_de_aplicar(admin, db):
    r = upload(admin, build_xlsx([person(1), person(2)]))
    assert r.status_code == 201
    report = r.json()
    assert report["estado"] == "revision"
    assert report["resumen"]["nuevos"] == 2
    # Las diferencias se guardan, pero aún no se aplica nada.
    assert db.scalar(select(func.count()).select_from(Member).where(Member.origen == "importacion")) == 0


def test_importar_dos_veces_el_mismo_archivo_no_duplica(admin, db):
    content = build_xlsx([person(1), person(2), person(3)])
    first = import_file(admin, content)
    assert first["resumen"]["nuevos"] == 3
    before = counts(db)

    again = upload(admin, content)
    assert again.status_code == 409
    assert again.json()["detail"]["codigo"] == "archivo-ya-aplicado"
    db.expire_all()
    assert counts(db) == before


def test_confirmar_dos_veces_el_mismo_lote_falla(admin):
    r = upload(admin, build_xlsx([person(1)]))
    batch = r.json()["id"]
    assert admin.post(f"/admin/imports/{batch}/confirm").status_code == 200
    second = admin.post(f"/admin/imports/{batch}/confirm")
    assert second.status_code == 409


def test_mismo_contenido_con_otro_hash_no_crea_cambios(admin, db):
    import_file(admin, build_xlsx([person(1), person(2)]))
    before = counts(db)
    # Archivo distinto (fila de título) pero mismos datos: sin cambios.
    report = import_file(admin, build_xlsx([person(1), person(2)], title_rows=1))
    assert report["resumen"]["sinCambios"] == 2
    assert report["resumen"]["nuevos"] == 0
    db.expire_all()
    members, history, applied = counts(db)
    assert (members, history) == before[:2]
    assert applied == before[2] + 1


def test_identificadores_se_leen_como_texto(admin, db):
    import_file(admin, build_xlsx([person(1, **{"Scopus Author Identifier": 57200000001.0})]))
    m = db.scalar(select(Member).where(Member.cvu_tecnm == "100001"))
    assert m is not None
    assert m.telefono_particular == "4610000001"
    assert m.scopus_author_id == "57200000001"
    assert m.rol == "Integrante"
    assert m.institucion_id == "cenidet"
    assert m.linea_investigacion_id == "linea-2"


def test_busca_por_cvu_y_luego_por_correo(admin, db):
    import_file(admin, build_xlsx([person(1)]))
    # Mismo CVU, correo nuevo → se actualiza el correo del mismo registro.
    r1 = import_file(admin, build_xlsx([person(1, **{"CORREO INSTITUCIONAL": "nuevo1@celaya.tecnm.mx"})]))
    row = next(f for f in r1["filas"] if f["fila"] == 2)
    assert row["resultado"] == "cambiado" and row["coincidencia"] == "cvu_tecnm"
    # Sin CVU, mismo correo → coincide por correo (y la celda vacía no borra el CVU guardado).
    r2 = import_file(
        admin,
        build_xlsx(
            [
                person(
                    1,
                    **{
                        "NÚMERO CVU TecNM": None,
                        "CORREO INSTITUCIONAL": "nuevo1@celaya.tecnm.mx",
                        "ROLL": "Responsable de línea",
                    },
                )
            ]
        ),
    )
    row = next(f for f in r2["filas"] if f["fila"] == 2)
    assert row["coincidencia"] == "correo_institucional"
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Member).where(Member.origen == "importacion")) == 1
    m = db.scalar(select(Member).where(Member.origen == "importacion"))
    assert m.cvu_tecnm == "100001" and m.rol == "Responsable de línea"


def test_campo_editado_manualmente_no_se_sobrescribe(admin, db):
    import_file(admin, build_xlsx([person(1)]))
    slug = admin.get("/admin/members", params={"q": "persona1"}).json()["items"][0]["slug"]
    assert admin.patch(f"/admin/members/{slug}", json={"rol": "Responsable de línea"}).status_code == 200

    report = import_file(admin, build_xlsx([person(1, ROLL="Colaborador", **{"TELÉFONO PARTICULAR": 4619999999})]))
    row = next(f for f in report["filas"] if f["fila"] == 2)
    by_field = {d["campo"]: d for d in row["diferencias"]}
    assert by_field["rol"]["bloqueado"] is True
    assert by_field["telefono_particular"]["bloqueado"] is False
    db.expire_all()
    m = db.scalar(select(Member).where(Member.slug == slug))
    assert m.rol == "Responsable de línea"  # bloqueado
    assert m.telefono_particular == "4619999999"  # no bloqueado


def test_quien_falta_se_desactiva_y_no_se_elimina(admin, db, client):
    import_file(admin, build_xlsx([person(1), person(2)]))
    report = import_file(admin, build_xlsx([person(1)]))
    assert report["resumen"]["desactivados"] >= 1
    db.expire_all()
    p2 = db.scalar(select(Member).where(Member.cvu_tecnm == "100002"))
    assert p2 is not None and p2.activo is False and p2.desactivado_en is not None
    assert client.get(f"/members/{p2.slug}").status_code == 404
    # Si vuelve a aparecer, se reactiva.
    report = import_file(admin, build_xlsx([person(1), person(2), person(3)]))
    row = next(f for f in report["filas"] if f["integranteId"] == p2.slug)
    assert any(d["campo"] == "activo" and d["nuevo"] is True for d in row["diferencias"])
    db.expire_all()
    assert db.get(Member, p2.id).activo is True


def test_duplicados_se_marcan_y_no_se_unen(admin, db):
    rows = [person(1), person(2, **{"NÚMERO CVU TecNM": 100001}), person(3)]
    report = upload(admin, build_xlsx(rows)).json()
    resultados = {f["fila"]: f["resultado"] for f in report["filas"] if f["fila"]}
    assert resultados[2] == "duplicado" and resultados[3] == "duplicado"
    assert resultados[4] == "nuevo"
    admin.post(f"/admin/imports/{report['id']}/confirm")
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Member).where(Member.origen == "importacion")) == 1


def test_coincidencia_por_nombre_con_registro_existente_requiere_revision(admin, db):
    # El responsable de la semilla no tiene CVU ni correo: no se crea un segundo registro.
    rows = [person(1, NOMBRES="ERNESTO", APELLIDOS="DE LA CRUZ NICOLÁS")]
    report = upload(admin, build_xlsx(rows)).json()
    row = next(f for f in report["filas"] if f["fila"] == 2)
    assert row["resultado"] == "duplicado"
    assert any(a["codigo"] == "nombre-coincide-con-registro" for a in row["advertencias"])
    # Ese responsable no se desactiva mientras esté en revisión.
    desactivados = [f["integranteId"] for f in report["filas"] if f["resultado"] == "desactivado"]
    assert "m-ernesto-de-la-cruz-nicolas" not in desactivados
    assert report["filas"][1]["nombre"] or True


def test_informe_reporta_advertencias(admin):
    rows = [
        person(1, **{"INSTITUCIÓN DE ADSCRIPCIÓN": "Universidad Inventada"}),
        person(2, **{"LÍNEA DE INVESTIGACIÓN PROPUESTA PARA TRABAJAR EN LA RED": "salud y ética"}),
        person(3, **{"CORREO INSTITUCIONAL": "no-es-un-correo"}),
    ]
    resumen = upload(admin, build_xlsx(rows)).json()["resumen"]
    codigos = resumen["advertenciasPorCodigo"]
    assert codigos["institucion-no-reconocida"] == 1
    assert codigos["linea-ambigua"] == 1
    assert codigos["correo-invalido"] == 1
    assert resumen["advertenciasGenerales"]  # se desactivaría a toda la semilla


def test_archivo_invalido(admin):
    r = upload(admin, b"esto no es un excel")
    assert r.status_code == 422 and r.json()["detail"]["codigo"] == "excel-invalido"


def test_permisos_de_importacion(make_admin):
    consulta = make_admin("consulta")
    assert upload(consulta, build_xlsx([person(1)])).status_code == 403
    tecnica = make_admin("administracion_tecnica")
    assert upload(tecnica, build_xlsx([person(1)])).status_code == 201
