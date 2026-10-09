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
- `SCHEDULE_EXCLUDE_CHANNELS=Newsletter,IG Nico`. Al empezar, el schedule lee `GET /admin/simpleProfiles` del blog `5822365` y solo publica en redes conectadas. Si esa lectura falla, usa Instagram, TikTok y YouTube. `Canal Ig` es el feed de Instagram (`colombiatechoficial`), mapeado por el nombre completo. LinkedIn se ignora aunque `linkedinCompany` esté conectado. `fbBusinessId` es el id de negocio de Instagram, no una página de Facebook. Luma, WhatsApp, Comunidades, Terceros, Mailing, Content hub- web, GovTech web, Newsletter y cualquier LinkedIn se quitan de la fila y se registran; no bloquean el resto. Si no queda ninguna red conectada, la fila se omite con `no_connected_network`. IG Nico sigue omitiendo la fila entera. `Youtube Shorts` sale como YouTube short. No usar los blogs `7255578` ni `7272512`.
- `SCHEDULE_MAX_PER_RUN` cuenta posts creados. Una fila pasada, duplicada, sin media, de tipo Miniatura, o de un canal excluido no consume cupo.
- Fechas de publicación ya pasadas no se mueven a «ahora + 5 min»: se omiten y, si hay Slack, se avisan. Una fecha de Notion sin hora (solo `YYYY-MM-DD`) no se programa a medianoche: se omite con `missing_time`.
- YouTube no acepta imágenes. Si la fila incluye YouTube y el media resuelto son solo imágenes, se omite con `no_video_for_network` y no se hace POST. TikTok sí acepta fotos: el swagger `ScheduledPostTikTokData` tiene `photoCoverIndex`, así que un post solo de TikTok (o TikTok + Instagram) con imágenes sale como foto (`photoCoverIndex` 0). Un post que también lleva YouTube no se parte.
- Un 4xx/5xx de `create_scheduled_post` o `update` registra el cuerpo de Metricool (recortado a 2 KB) y la forma del request sin secretos (redes, tipo de `instagramData`/`tiktokData`/`youtubeData`, cantidad y extensiones de media, `publicationDate`). Ese mensaje entra en el aviso de Slack. `httpx` y `httpcore` quedan en WARNING para no imprimir la URL del webhook.
- Si Metricool ya tiene la pieza, no se crea otra. El match es caption/título muy parecidos (Jaccard o ratio ≥ 0.6 tras quitar stopwords, hashtags, emojis y URLs; o un prefijo/substring largo) dentro de ±2 días, y casi el mismo texto si las fechas están a más de 2 días. La misma red y la misma hora (±15 min) cuenta como la misma pieza solo si el Jaccard de las palabras no es claramente bajo (≥ ~0.3) o hay tokens distintivos en común. Un CTA repetido (comenta, enviamos, información) no identifica la pieza. El parecido de caracteres entre captions largos no basta. Si ese horario ya lo ocupa otra pieza, se omite con `slot_conflict` y no se crea. Un ocupante con texto vacío o muy corto (una Historia de Instagram no lleva caption) también es `slot_conflict`, igual que una fila Historia en un horario ocupado, salvo que el media de ambos lados sea un URL directo claramente distinto. Una carpeta de Drive frente a un jpeg de Metricool no cuenta como distinto: no se crea un segundo post en un horario cuyo texto está vacío. Cada fila candidata deja una línea `Slot Notion … reason=none occupants=0` cuando no hay nadie en esos ±15 min, con la hora de Notion y el post más cercano (`nearest`, `nearest_thin`, `delta_min`). Un story al día siguiente no es el mismo horario. Un post ya ligado a otra fila de Notion no se reutiliza. Palabras sueltas en común no alcanzan. Varios candidatos: se omite y se reporta, sin crear. Notion pasa a **Programado** si sigue pendiente, o a **Publicado** si ya se publicó. Un provider en `ERROR` no cambia Notion. Un caption con placeholder (`[X]`, `{...}`, `TODO`, `TBD`, `XXX`, `lorem ipsum`) no se programa.
- Tipos en español: `Piezas estática` / `estático` / `estatica` salen como post estático, no como Reel. Si el tipo incluye `Historias` (aunque también diga pieza estática), Instagram sale como `STORY`. `Miniaturas` no se programa como post. Sin Archivo Final no se programa.
- `--dry-run` no escribe: no crea ni actualiza Metricool, no cambia Notion, no sube media pública y no llama a Slack.
- `GET /v2/scheduler/posts` usa `start` y `end` (swagger `getCalendarReport`). `from`/`to` se ignoran y la API devuelve solo el día de hoy.
- `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` para carpetas Drive **y** descarga autenticada de archivos (API `alt=media`; no usar `uc?export=download` HTML). Compartir carpetas/archivos con el email de la SA.
- URL pública Metricool: preferir `S3_*` si existe; `TRANSFER_SH` es **opcional** (a menudo timeout). Sin S3 el job usa litterbox → uguu.se automáticamente. Dropbox `dl=1` / YouTube pasan directo a normalize sin re-host.
- ffmpeg: sistema `ffmpeg`/`FFMPEG_BIN`, o `pip install "metricool-sync-posts[ffmpeg]"` (imageio-ffmpeg), o Windows `winget install --id Gyan.FFmpeg -e`. Sin ffmpeg, `.mov` se intenta sin remux (log de aviso).
- Al arrancar, el schedule loguea `metricool_sync_posts build: <sha|version>` — si no aparece, el zip no se aplicó.
- El título público de YouTube (`youtubeData.title`, shorts y videos largos) es `Titulo` (`NOTION_PROP_COVER_TEXT`), recortado a 100 caracteres. No usa `Titulo de la publicación` ni el caption. Si la fila incluye YouTube y `Titulo` está vacío, no se programa (`missing_hook`).
- Portadas de **Instagram REEL y TRIAL_REEL** (también si el Canal trae más redes): el texto es la propiedad rich text `Titulo` (`NOTION_PROP_COVER_TEXT`), no `Titulo de la publicación`. Si `Titulo` está vacío la fila no se programa (`missing_hook`). `CTW_COVER_AGENT_PATH` llama a `prepare_cover_before_metricool`; si no queda lista (`missing_frame`, `unreadable_folder`, `unsupported_link`, `render_failed`, `error`) tampoco se programa. `REQUIRE_COVER_FOR_SCHEDULE=true` por defecto; `false` permite el reel sin portada. Los bytes salen a un URL público de al menos 72 h (S3, si no litterbox 72h) y van en `videoThumbnailUrl`. Un `cover_url` de Dropbox directo se usa si esa subida no está. `--dry-run` no sube a hosts públicos, Dropbox ni Drive: si el agente acepta un modo sin escritura se llama así; si no, se registra `would prepare cover (titulo=...)`. Cada reel deja una línea INFO con status, source (`dropbox` / `drive` / `existing`) y reason.
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
