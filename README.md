# Metricool Sync Posts

Automatización **sin costo de LLM** para la Parrilla de Contenido (Notion → Metricool → redes). Tres jobs independientes invocables por cron o manualmente.

Marca de referencia: **Colombia Tech** (`METRICOOL_BLOG_ID=5822365`, zona `America/Bogota`).

## Requisitos

- Python 3.11+
- `ffmpeg` / `ffprobe` en PATH (procesamiento de video/reels)
- Tokens: Notion integration, Metricool API (`X-Mc-Auth`), opcional Slack webhook

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# editar .env
```

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
- `ENABLE_SCHEDULE=false` (hasta reactivar producto)
- `TRANSFER_SH_ENABLED` / `S3_*` para URLs públicas temporales tras remux ffmpeg
- `SLACK_WEBHOOK_URL` (opcional, dedupe 6 h en `.data/slack-dedupe.json`)

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

## Pendiente de piloto con credenciales reales

- Validar nombres exactos de propiedades Notion (Estado vs select)
- Capturar cuerpo JSON real por tipo de post (reel, carrusel, TRIAL_REEL) vía inspector del planner
- Probar normalize media con URLs Dropbox `.mov` y upload público
- Confirmar parámetros exactos de `GET /v2/scheduler/posts` (nombres `from`/`to` vs documentación MCP)
