"""Comandos de mantenimiento.

python -m app.cli sembrar               # catálogos y responsables documentados (idempotente)
python -m app.cli crear-admin --correo coordinacion@... --nombre "..." --rol coordinacion
python -m app.cli exportar-openapi [ruta]
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import ROLE_PERMISSIONS
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import AdminUser, Consent, Institution, Member, MemberPublicProfile, ResearchLine
from app.services.normalization import name_key

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "catalogo.json"


def seed(db: Session, path: Path = SEED_FILE) -> dict[str, int]:
    """Carga líneas oficiales, instituciones confirmadas y responsables documentados.

    Origen: datos NO demostrativos del frontend (src/data). Los integrantes de
    la semilla no tienen CVU ni correo: la primera importación los detectará
    como «posible duplicado por nombre» para que la coordinación los vincule.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    counts = {"instituciones": 0, "lineas": 0, "integrantes": 0}

    for inst in data["institutions"]:
        if db.get(Institution, inst["id"]) is None:
            db.add(
                Institution(
                    id=inst["id"], nombre=inst["nombre"], siglas=inst.get("siglas"), tipo=inst["tipo"], alias=[]
                )
            )
            counts["instituciones"] += 1

    for line in data["researchLines"]:
        if db.get(ResearchLine, line["id"]) is None:
            db.add(
                ResearchLine(
                    id=line["id"], numero=line["numero"], slug=line["slug"], nombre=line["nombre"],
                    nombre_corto=line["nombreCorto"], descripcion=line["descripcion"], ejes=line["ejes"],
                    palabras_clave=line["palabrasClave"],
                )
            )  # fmt: skip
            counts["lineas"] += 1
    db.flush()

    for m in data["members"]:
        if db.scalar(select(Member).where(Member.slug == m["id"])):
            continue
        member = Member(
            slug=m["id"],
            tratamiento=m.get("tratamiento"),
            nombres=m["nombres"],
            apellidos=m["apellidos"],
            nombre_completo=m["nombreCompleto"],
            clave_nombre=name_key(m["nombres"], m["apellidos"]),
            institucion_id=m.get("institucionId"),
            rol=m.get("rol"),
            linea_investigacion_id=m.get("lineaInvestigacionId"),
            origen="semilla",
        )
        member.perfil = MemberPublicProfile(version=1, lineas_propuestas=[])
        db.add(member)
        db.flush()
        if m.get("correoPublico"):
            # Correo de contacto institucional ya publicado en la documentación de la red.
            member.correo_institucional = m["correoPublico"]
            db.add(
                Consent(
                    member_id=member.id,
                    version_texto="documentacion-fase-1",
                    texto="Correo de contacto institucional publicado en la documentación oficial de la red.",
                    aprobado_en=member.creado_en,
                    nota="Registro de semilla. Confirmar con la persona al enviarle su primer enlace.",
                )
            )
        counts["integrantes"] += 1
    db.flush()

    for line in data["researchLines"]:
        row = db.get(ResearchLine, line["id"])
        responsable = db.scalar(select(Member.id).where(Member.slug == line.get("responsableId")))
        if row and responsable and row.responsable_id is None:
            row.responsable_id = responsable
    db.commit()
    return counts


def create_admin(db: Session, correo: str, nombre: str, rol: str, password: str) -> AdminUser:
    if rol not in ROLE_PERMISSIONS:
        raise SystemExit(f"Rol desconocido: {rol}. Opciones: {', '.join(ROLE_PERMISSIONS)}")
    if len(password) < 12:
        raise SystemExit("La contraseña debe tener al menos 12 caracteres.")
    user = AdminUser(correo=correo.lower(), nombre=nombre, rol=rol, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    return user


def export_openapi(path: Path) -> None:
    from app.main import create_app

    path.write_text(json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sembrar")
    admin = sub.add_parser("crear-admin")
    admin.add_argument("--correo", required=True)
    admin.add_argument("--nombre", required=True)
    admin.add_argument("--rol", default="coordinacion", choices=sorted(ROLE_PERMISSIONS))
    openapi = sub.add_parser("exportar-openapi")
    openapi.add_argument("ruta", nargs="?", default="openapi.json")
    args = parser.parse_args(argv)

    if args.cmd == "exportar-openapi":
        export_openapi(Path(args.ruta))
        print(f"OpenAPI escrito en {args.ruta}")
        return
    with SessionLocal() as db:
        if args.cmd == "sembrar":
            print(seed(db))
        elif args.cmd == "crear-admin":
            password = os.environ.get("RIIIACD_ADMIN_PASSWORD") or getpass.getpass("Contraseña (mín. 12 caracteres): ")
            user = create_admin(db, args.correo, args.nombre, args.rol, password)
            print(f"Usuario creado: {user.correo} ({user.rol})")


if __name__ == "__main__":
    main(sys.argv[1:])
