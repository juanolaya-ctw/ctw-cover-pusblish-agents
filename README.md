# Metricool Sync Posts

Repositorio: [juanolaya-ctw/ctw-cover-pusblish-agents](https://github.com/juanolaya-ctw/ctw-cover-pusblish-agents)

Automatización **sin costo de LLM** para la Parrilla de Contenido (Notion → Metricool → redes). Tres jobs independientes invocables por cron o manualmente.

Marca de referencia: **Colombia Tech** (`METRICOOL_BLOG_ID=5822365`, zona `America/Bogota`).

## Requisitos

- Python 3.11+
- `ffmpeg` / `ffprobe` en PATH (procesamiento de video/reels)
- Tokens: Notion integration, Metricool API (`X-Mc-Auth`), opcional Slack webhook
- **No hace falta** `DROPBOX_ACCESS_TOKEN` para programar: Archivo Final usa enlaces públicos/share de Dropbox (`dl=1`) o Google Drive (archivo o carpeta + service account)

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# editar .env
python -m metricool_sync_posts.cli.validate_env
```

Guía paso a paso en Windows: Project store `docs/windows-local-setup.md`.

### Validar credenciales

```bash
python -m metricool_sync_posts.cli.validate_env
python -m metricool_sync_posts.cli.validate_env --skip-network   # solo placeholders
```

Sale con código **0** si Notion (`users/me` + data source) y Metricool (GET scheduler en ventana corta) responden bien.

### Descubrir `NOTION_DATABASE_ID`

```bash
metricool-discover --query "Parrilla"
# o
python -m metricool_sync_posts.cli.discover --query "Parrilla"
```

Copia el `id` de la base **Parrilla de Contenido - CTW** a `NOTION_DATABASE_ID`.

## Jobs

| Job | Comando | Descripción |
|-----|---------|-------------|
| **schedule** | `notion-aprobado-metricool` | Aprobado → Metricool → Notion **Programado** (semana calendario Bogotá). Máx. `SCHEDULE_MAX_PER_RUN` creaciones; omisiones y duplicados no gastan cupo. **Apagado por defecto** (`ENABLE_SCHEDULE=false`). |
| **confirm_published** | `notion-publicado-metricool` | **Programado** ±7d → **Publicado** solo si cada provider de Metricool está `PUBLISHED`, o si el post ya no está en el scheduler y la hora pasó. `ERROR` no cambia el estado (bloqueo). `PENDING` se queda. |
| **sync_dates** | `notion-sync-fechas-metricool` | Notion es la fecha fuente. Empareja por id/uuid guardado o por caption/título dentro de ±7 días (aunque la hora haya cambiado) y actualiza Metricool. |

Cada job acepta `--dry-run`.

```bash
# Ejemplos
notion-aprobado-metricool --dry-run
notion-aprobado-metricool --enable          # ignora ENABLE_SCHEDULE=false una vez
notion-aprobado-metricool --enable --only-date 2026-10-06 --exclude-channel "IG Nico"
notion-publicado-metricool --dry-run
notion-sync-fechas-metricool --dry-run
```

Equivalente con módulos:

```bash
python -m metricool_sync_posts.cli.schedule --dry-run
python -m metricool_sync_posts.cli.confirm_published
python -m metricool_sync_posts.cli.sync_dates
```

## Cron sugerido (UTC)

Ajusta a tu infraestructura; intervalos configurables en la doc de operación del Project store.

| Minuto (UTC) | Job |
|--------------|-----|
| `:00` | schedule (solo cuando `ENABLE_SCHEDULE=true`) |
| `:10` | confirm_published |
| `:30` | sync_dates |

Ejemplo crontab (cada hora):

```cron
0 * * * * cd /path/to/repo && .venv/bin/notion-aprobado-metricool >> .data/cron-schedule.log 2>&1
10 * * * * cd /path/to/repo && .venv/bin/notion-publicado-metricool >> .data/cron-published.log 2>&1
30 * * * * cd /path/to/repo && .venv/bin/notion-sync-fechas-metricool >> .data/cron-sync.log 2>&1
```

Por defecto en documentación interna se sugiere cada **6 h** si no necesitas granularidad horaria.

## Variables de entorno

Ver [.env.example](.env.example). Principales:

- `NOTION_TOKEN`, `NOTION_DATABASE_ID`
- `METRICOOL_USER_TOKEN`, `METRICOOL_USER_ID`, `METRICOOL_BLOG_ID`
- `ENABLE_SCHEDULE=false` (usa `--enable` para un run; o `true` en cron)
- `SCHEDULE_EXCLUDE_CHANNELS=Newsletter,IG Nico`. Esas dos también están fijas en código. Cualquier Canal que sea solo LinkedIn se excluye; en una fila multi-red se quita LinkedIn y se programa la otra red. No usar los blogs `7255578` ni `7272512`.
- `SCHEDULE_MAX_PER_RUN` cuenta posts creados. Una fila pasada, duplicada, sin media, de tipo Miniatura, o de un canal excluido no consume cupo.
- Fechas de publicación ya pasadas no se mueven a «ahora + 5 min»: se omiten y, si hay Slack, se avisan.
- Si Metricool ya tiene la pieza (caption, título o media, en una ventana de ±7 días), no se crea otra. Notion pasa a **Programado** si sigue pendiente, o a **Publicado** si ya se publicó. Un provider en `ERROR` no cambia Notion.
- Tipos en español: `Piezas estática` / `estático` / `estatica` salen como post estático, no como Reel. `Miniaturas` no se programa como post. Sin Archivo Final no se programa.
- `--dry-run` no escribe: no crea ni actualiza Metricool, no cambia Notion, no sube media pública y no llama a Slack.
- `GET /v2/scheduler/posts` usa `start` y `end` (swagger `getCalendarReport`). `from`/`to` se ignoran y la API devuelve solo el día de hoy.
- `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` para carpetas Drive **y** descarga autenticada de archivos (API `alt=media`; no usar `uc?export=download` HTML). Compartir carpetas/archivos con el email de la SA.
- URL pública Metricool: preferir `S3_*` si existe; `TRANSFER_SH` es **opcional** (a menudo timeout). Sin S3 el job usa litterbox → uguu.se automáticamente. Dropbox `dl=1` / YouTube pasan directo a normalize sin re-host.
- ffmpeg: sistema `ffmpeg`/`FFMPEG_BIN`, o `pip install "metricool-sync-posts[ffmpeg]"` (imageio-ffmpeg), o Windows `winget install --id Gyan.FFmpeg -e`. Sin ffmpeg, `.mov` se intenta sin remux (log de aviso).
- Al arrancar, el schedule loguea `metricool_sync_posts build: <sha|version>` — si no aparece, el zip no se aplicó.
- Portadas IG / Dropbox API = **Fase 2 opcional**: dejar `CTW_COVER_AGENT_PATH` vacío; `REQUIRE_COVER_FOR_SCHEDULE=false`. `DROPBOX_ACCESS_TOKEN` solo si activas cover-agent (token ~4h). Media de schedule **no** usa OAuth Dropbox.
- `SLACK_WEBHOOK_URL` (opcional, dedupe 6 h en `.data/slack-dedupe.json`)

Go-live hoy (pasos Juan Windows): Project store `docs/go-live-today.md`.

## Estructura

```
src/metricool_sync_posts/
  config.py
  notion/          # consultas y propiedades Notion
  metricool/       # cliente REST y matching
  media/           # Dropbox/Drive, ffmpeg, upload público
  jobs/            # schedule, confirm_published, sync_dates
  slack/           # notificaciones + dedupe
  cli/             # entrypoints
tests/
docs/              # en Project store (Cursor Context)
```

## API Metricool

Resumen en `docs/metricool-api.md` (Project store). Swagger oficial: https://app.metricool.com/resources/apidocs/index.html

## Pruebas

```bash
pytest -q
ruff check src tests
```

CI en GitHub Actions (opcional): requiere un PAT con scope `workflow`. Plantilla: `docs/ci-lint-workflow.yml` → copiar a `.github/workflows/lint.yml`.

## Pendiente de piloto con credenciales reales

- Validar nombres exactos de propiedades Notion (Estado vs select)
- Capturar cuerpo JSON real por tipo de post (reel, carrusel, TRIAL_REEL) vía inspector del planner
- Probar normalize media con URLs Dropbox `.mov` y upload público
- `GET /v2/scheduler/posts` filtra con `start`/`end` (confirmado en swagger y en un probe de 2026-10-09: `from`/`to` devolvió 11 posts del día; `start`/`end` devolvió el rango)
