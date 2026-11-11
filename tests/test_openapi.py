"""Criterio: OpenAPI generado y sincronizado. `openapi.json` se versiona y alimenta los tipos del frontend."""

import json
from pathlib import Path

from app.main import create_app

SNAPSHOT = Path(__file__).resolve().parent.parent / "openapi.json"


def test_openapi_json_esta_actualizado():
    current = create_app().openapi()
    assert SNAPSHOT.exists(), "Falta openapi.json: ejecuta `python -m app.cli exportar-openapi`."
    assert json.loads(SNAPSHOT.read_text(encoding="utf-8")) == current, (
        "openapi.json está desactualizado: ejecuta `python -m app.cli exportar-openapi` "
        "y después `npm run api:types` en el frontend."
    )
