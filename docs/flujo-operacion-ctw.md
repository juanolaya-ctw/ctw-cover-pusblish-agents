# Flujo operación CTW — Notion ↔ Metricool

## Marca Metricool

- **Colombia Tech** (`METRICOOL_BLOG_ID=5822365`)
- Redes conectadas: Instagram, LinkedIn, TikTok, YouTube
- Otras marcas en la cuenta (Nicolás, Maria José) no las usa este script salvo que cambies `METRICOOL_BLOG_ID`

## Estados Notion (Parrilla)

| Estado | Job |
|--------|-----|
| Aprobado - Edición Final | `schedule` (→ Programado) |
| Programado | `confirm_published`, `sync_dates` |
| Publicado | Solo lectura en confirm |

## Jobs

1. **schedule** — Semana calendario Bogotá. `SCHEDULE_MAX_PER_RUN` (default 5) cuenta creaciones; una fila omitida o duplicada no gasta cupo. Siempre excluye **Newsletter**, **IG Nico** y cualquier Canal que sea solo LinkedIn. Varios valores de Canal arman un solo post con todas las redes mapeadas (`Youtube Shorts` → YouTube short); LinkedIn y Newsletter se quitan, y si aparece IG Nico no se programa nada. No reprograma fechas pasadas. Si la pieza ya está en Metricool (caption muy parecido, más estricto si la fecha difiere en más de 2 días, o misma red y misma hora ±15 min cuando el copy no es claramente distinto), no crea otra; palabras sueltas compartidas no cuentan. Si el horario ya tiene otra pieza, omite con `slot_conflict`. Un caption con placeholder no se programa. Si hay más de un candidato, omite y reporta. Un match único alinea Notion a Programado o Publicado. Sin media, o tipo Miniatura, se omite. Filtros CLI: `--only-date`, `--exclude-channel`, `--only-page-id`.
2. **confirm_published** — Programado ±7 días → Publicado solo si todos los `providers[].status` son `PUBLISHED`, o si el post ya no está en el scheduler y la hora pasó (margen 30 min). `ERROR` se reporta y no cambia el estado. `PENDING` se queda.
3. **sync_dates** — Notion manda la fecha. Empareja por id/uuid de Metricool si la fila lo tiene, si no por caption/título dentro de ±7 días, aunque la hora no coincida.

No usar `METRICOOL_BLOG_ID` `7255578` ni `7272512`. `--dry-run` no escribe en Metricool, Notion, media pública ni Slack.

## Archivo Final

- **Carpeta Drive:** requiere `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` + carpetas compartidas con esa cuenta. Listado + descarga vía API (`alt=media`).
- **Archivo Drive:** `/file/d/…` — preferir la misma SA (API). Sin SA, fallback `uc?export=download` (suele devolver HTML si no es público).
- **Dropbox share/public:** `dl=1` / `raw=1` (sin `DROPBOX_ACCESS_TOKEN` ni API).
- **URL pública Metricool:** Dropbox `dl=1` / YouTube → pass-through. Drive → SA download luego `S3_*` o litterbox/uguu (transfer.sh solo si `TRANSFER_SH=true`, no es obligatorio).
- **YouTube URL:** programación sobre video existente (equipo debe pasarlo a público antes de la hora).
- **Carrusel:** todos los images de la carpeta (máx. 10).

## Portadas de Reel

Instagram `REEL` y `TRIAL_REEL` (también en un post de varias redes) necesitan portada antes de programarse. El texto de la portada es `Titulo` (`NOTION_PROP_COVER_TEXT`), no el título de la tarea. Vacío → `missing_hook`. `CTW_COVER_AGENT_PATH` prepara el PNG; si no está listo se omite con el motivo del agente. `REQUIRE_COVER_FOR_SCHEDULE=true` por defecto. El PNG se publica en un host de al menos 72 h (S3 o litterbox) y Metricool lo recibe en `videoThumbnailUrl`. `--dry-run` no sube archivos.
