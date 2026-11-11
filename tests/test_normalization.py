"""Paridad con src/utils/memberMapper.ts del frontend."""

from app.services.normalization import (
    Catalog,
    InstitutionRef,
    LineRef,
    clean_cell,
    clean_identifier,
    institution_key,
    normalize_email,
    normalize_institution,
    normalize_person_name,
    normalize_role,
    resolve_research_line,
)

CATALOG = Catalog(
    institutions=[InstitutionRef("cenidet", "Centro Nacional de Investigación y Desarrollo Tecnológico", "CENIDET"),
                  InstitutionRef("it-celaya", "Instituto Tecnológico de Celaya", None, ("celaya",))],
    lines=[LineRef("linea-3", 3, "Inteligencia geoespacial", ("geoespacial", "territorio")),
           LineRef("linea-4", 4, "Ética y gobernanza de la IA", ("etica", "gobernanza")),
           LineRef("linea-5", 5, "IA en salud, educación y agricultura", ("salud", "educacion"))],
)  # fmt: skip


def test_nombres_propios():
    assert normalize_person_name("  MARÍA  yasmín ") == "María Yasmín"
    assert normalize_person_name("DE LA CRUZ NICOLÁS") == "De la Cruz Nicolás"
    assert normalize_person_name("PÉREZ-GARCÍA") == "Pérez-García"


def test_celdas_e_identificadores():
    assert clean_cell(" N/A ") is None and clean_cell("  a   b ") == "a b"
    assert clean_identifier(123456.0) == "123456"
    assert clean_identifier("1.23456E+5") == "123456"
    assert clean_identifier(" 12 34 ") == "1234"


def test_correos():
    assert normalize_email(" Ana@Celaya.TecNM.mx ; otro@x.mx").email == "ana@celaya.tecnm.mx"
    assert normalize_email("sin arroba").invalid


def test_instituciones():
    assert institution_key("TecNM Campus Celaya") == "celaya"
    assert normalize_institution("TecNM / CENIDET", CATALOG) == ("TecNM / CENIDET", "cenidet")
    assert normalize_institution("Tecnológico Nacional de México campus Celaya", CATALOG)[1] == "it-celaya"
    assert normalize_institution("Otra", CATALOG)[1] is None


def test_lineas_y_roles():
    assert resolve_research_line("L3 - algo", CATALOG).linea_id == "linea-3"
    assert resolve_research_line("Proyecto de inteligencia geoespacial aplicada", CATALOG).linea_id == "linea-3"
    amb = resolve_research_line("salud y ética", CATALOG)
    assert amb.linea_id is None and amb.ambigua
    assert normalize_role("RESPONSABLE DE LÍNEA") == "Responsable de línea"
    assert normalize_role("asesor") == "Asesor"
