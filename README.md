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
| **schedule** | `notion-aprobado-metricool` | Aprobado → Metricool → Notion **Programado** (semana calendario Bogotá, máx. 5). **Apagado por defecto** (`ENABLE_SCHEDULE=false`). |
| **confirm_published** | `notion-publicado-metricool` | **Programado** ±7d → si Metricool publicó → **Publicado**. |
| **sync_dates** | `notion-sync-fechas-metricool` | Alinea fecha Notion vs Metricool (±1 min); `updateScheduledPost` con cuerpo completo. |

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
- `SCHEDULE_EXCLUDE_CHANNELS=Newsletter` (mantener; Canales/LinkedIn Majo a mano)
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
- Confirmar parámetros exactos de `GET /v2/scheduler/posts` (nombres `from`/`to` vs documentación MCP)
