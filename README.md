# RIIIACD — API (`riiiacd-back`)

API del portal de la **Red de Investigación e Innovación en Inteligencia Artificial y Ciencia de Datos (RIIIACD)** del TecNM. Fase 2 del proyecto: sirve el directorio público, la actualización de perfiles por enlace único, la importación del padrón en Excel y la cola de aprobación de la coordinación.

FastAPI · Pydantic v2 · SQLAlchemy 2 · Alembic · PostgreSQL 16 · pytest.

El frontend vive en otro repositorio (`riiiacd`) y el panel administrativo será una aplicación separada (pendiente 4.4).

---

## Puesta en marcha

Requisitos: Python 3.11 o superior y PostgreSQL 14 o superior.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # ajusta DATABASE_URL y SECRET_KEY
alembic upgrade head              # crea el esquema desde cero
python -m app.cli sembrar         # líneas, CENIDET y responsables documentados (idempotente)
python -m app.cli crear-admin --correo coordinacion@celaya.tecnm.mx --nombre "Coordinación" --rol coordinacion
uvicorn app.main:app --reload     # http://localhost:8000/docs
```

Con Docker (opcional): `docker compose up --build`.

Comandos de calidad:

```bash
pytest                            # 51 pruebas contra PostgreSQL (ver «Pruebas»)
ruff check . && ruff format --check .
python -m app.cli exportar-openapi    # regenera openapi.json (lo verifica una prueba)
```

---

## Estructura

```
app/
  main.py            Aplicación, CORS y cabeceras de seguridad
  config.py          Variables de entorno (.env.example)
  models.py          Las 14 entidades del brief + usuarios y sesiones administrativas
  core/              Reloj, contraseñas y tokens, roles y permisos
  services/
    normalization.py Traslado de src/utils/memberMapper.ts (mismas reglas)
    excel.py         Lectura del .xlsx
    importer.py      Revisión y confirmación de lotes (reglas 3.3)
    invitations.py   Enlaces de 24 horas y un solo uso
    profiles.py      Propuestas, aprobación, rechazo y edición administrativa
    photos.py        Validación y reencodificación de fotografías
    storage.py       Interfaz de almacenamiento: local y S3-compatible
    mailer.py        SMTP institucional (opcional)
    public.py        Consultas públicas con lista blanca
    audit.py         Bitácora
  api/               Rutas públicas, de integrante, de sesión y administrativas
alembic/             Migraciones (0001: esquema inicial)
seed/catalogo.json   Datos NO demostrativos exportados del frontend
tests/               Pruebas por criterio de aceptación
openapi.json         Contrato versionado; el frontend genera sus tipos a partir de él
```

---

## Endpoints

**Públicos**

| Ruta | Notas |
| --- | --- |
| `GET /members` | `q`, `institucion`, `linea`, `rol` (`todas`/`sin-asignar`), `orden` (`apellidos-asc`, `apellidos-desc`, `nombres-asc`), `pagina`, `porPagina` (máx. 100). Devuelve `{items, total, pagina, porPagina, totalPaginas}` |
| `GET /members/{id}` | `id` = slug público. 404 si no existe, está inactivo u oculto |
| `GET /institutions`, `GET /research-lines`, `GET /statistics` | `statistics` se calcula con los integrantes activos y públicos |
| `POST /contact` | 5 por hora por IP; campo trampa `sitioWeb` |
| `GET /media/photos/{id}` | Solo fotografías aprobadas |

**Integrante (con enlace)**: `GET /invitations/{token}` (valida sin consumir), `PUT /invitations/{token}/photo` (multipart `foto`; no consume), `POST /invitations/{token}/changes` (consume).

**Administración** (cookie de sesión + `X-CSRF-Token`): `POST /admin/auth/login|logout`, `GET /admin/auth/me`, `POST /admin/imports`, `GET /admin/imports/{id}`, `POST /admin/imports/{id}/confirm`, `GET /admin/change-requests`, `POST /admin/change-requests/{id}/approve|reject`, `GET /admin/photos/{id}`, `POST /admin/invitations`, `GET /admin/members`, `GET|PATCH /admin/members/{slug}`, `GET /admin/members/{slug}/history`.

Errores: siempre `{"detail": {"codigo": "...", "mensaje": "..."}}`. El frontend usa `codigo` (por ejemplo `invitacion-expirada`).

---

## Decisiones de implementación

- **Importación en dos pasos.** `POST /admin/imports` calcula y guarda las diferencias por fila sin aplicar nada. `confirm` las vuelve a calcular con el estado actual y aplica todo en una transacción. Solo puede haber un lote en revisión a la vez: subir otro descarta el anterior, para que confirmar uno viejo no revierta datos.
- **Coincidencia:** CVU TecNM → CVU CONAHCyT (si la fila no trae CVU TecNM) → correo institucional → registro nuevo. Si el CVU y el correo apuntan a personas distintas, o la fila no coincide por identificadores pero sí por nombre con alguien existente, se marca como **duplicado** y no se aplica.
- **Una celda vacía no borra datos.** Se conserva el último valor conocido; para borrar, se edita desde el panel.
- **Idempotencia:** índice único parcial sobre el hash del archivo en lotes `aplicado`. Además, reimportar el mismo contenido con otro hash produce cero cambios.
- **Bloqueos:** aprobar un cambio o editar un campo desde el panel crea un registro en `field_locks`; la importación muestra la diferencia como `bloqueado: true` y no la aplica.
- **Correo público:** la solicitud del integrante crea un `consent` sin aprobar. Solo al aprobarse se publica. Retirar la autorización también pasa por aprobación.
- **Fotografías:** el formato se detecta por el contenido, se respeta la orientación, se reencodifica a WEBP (sin EXIF ni GPS), se redimensiona a 1200 px como máximo y se guarda con un nombre aleatorio. La foto anterior pasa a `reemplazada`; una rechazada se borra del almacenamiento.
- **Enlaces:** solo se guarda el SHA-256 del token. Generar un enlace nuevo revoca los vigentes de esa persona.
- **Sesiones:** cookie `HttpOnly` limitada a `/admin`, `Secure` y `SameSite=Lax`; token CSRF sincronizado (encabezado `X-CSRF-Token`) y verificación de `Origin`. Contraseñas con Argon2.
- **Auditoría:** cada consulta de datos administrativos, edición, aprobación, rechazo, importación, invitación e inicio de sesión queda en `audit_log` con la IP en hash con sal.
- **Límite de solicitudes:** en memoria del proceso. Con varios procesos conviene moverlo a Redis o al proxy inverso.
- **Semilla:** líneas oficiales, CENIDET y los cinco responsables documentados. El correo del líder (contacto oficial de la red) se siembra con una autorización marcada como «registro de semilla» para confirmarla con él.

---

## Pruebas y criterios de aceptación

`pytest` borra el esquema de `TEST_DATABASE_URL` (por defecto `riiiacd_test`), aplica las migraciones **desde cero** y corre las 51 pruebas.

| Criterio (brief §5) | Prueba |
| --- | --- |
| Ningún endpoint público devuelve CVU, teléfono, correo personal ni archivos | `test_privacy.py`: recorre todas las rutas GET públicas del esquema OpenAPI con un integrante que tiene todos los datos, foto y correo aprobados; además, verifica que el esquema `PublicMember` sea exactamente la lista blanca |
| Importar dos veces el mismo archivo no duplica | `test_import.py::test_importar_dos_veces_el_mismo_archivo_no_duplica` y `test_mismo_contenido_con_otro_hash_no_crea_cambios` |
| Un campo aprobado manualmente no se sobrescribe | `test_import.py::test_campo_editado_manualmente_no_se_sobrescribe` |
| El enlace caduca a las 24 horas y no se usa dos veces | `test_invitations.py::test_caduca_a_las_24_horas` y `test_validar_no_consume_y_enviar_si` |
| Aprobaciones, rechazos y ediciones en el historial | `test_change_requests.py::test_aprobacion_y_rechazo_quedan_en_historial_y_auditoria` |
| Migraciones reproducibles desde cero | Fixture `migrated_db` en cada corrida, y `alembic downgrade base && alembic upgrade head && alembic check` |
| OpenAPI sincronizado | `test_openapi.py` compara `openapi.json` con la aplicación; el frontend genera `src/types/api.generated.ts` con `npm run api:types` |

---

## Pendientes (brief §4, sin respuesta asumida)

| # | Tema | Estado en el código |
| --- | --- | --- |
| 4.1 | Servidor institucional, Docker y dominio `riiiacd.celaya.tecnm.mx` | `Dockerfile` y `docker-compose.yml` opcionales; nada depende de Docker |
| 4.2 | SMTP institucional | `SMTP_*` opcionales; sin ellos, `POST /admin/invitations` devuelve el enlace para compartirlo a mano |
| 4.3 | Logotipos oficiales | Campo `institutions.logo_url` listo |
| 4.4 | Repositorio del panel administrativo | `ADMIN_ORIGINS` configurable; la API ya expone todo lo que el panel necesita |
| 4.5 | Retención del Excel original | **No se guarda el archivo.** Solo el hash y las filas normalizadas en `import_row_results` |
| 4.6 | Roles administrativos | Tabla PROVISIONAL en `app/core/permissions.py` (coordinación, administración técnica, consulta) |
| 4.7 | Campos que aprueba la coordinación | `CHANGE_FIELDS` en `app/models.py`; perfil profesional con 500 caracteres **propuestos** |
| 4.8 | Aviso de privacidad y texto de autorización | `CONSENT_TEXT` y `CONSENT_TEXT_VERSION` con un borrador marcado |
| 4.9 | Tamaño máximo de fotografía | `PHOTO_MAX_BYTES` = 2 MB, configurable |
| 4.10 | Pantone 294 C | No afecta a la API |

Otros límites conocidos: `S3Storage` está implementado pero no se ha probado contra un servicio real; el límite de solicitudes es por proceso.
