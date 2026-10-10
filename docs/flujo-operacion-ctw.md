# Flujo operación CTW — Notion ↔ Metricool

## Marca Metricool

- **Colombia Tech** (`METRICOOL_BLOG_ID=5822365`)
- Redes que se programan: Instagram (`Canal Ig` = colombiatechoficial), TikTok, YouTube. LinkedIn está en la marca y no se publica. `fbBusinessId` no es Facebook.
- Otras marcas en la cuenta (Nicolás, Maria José) no las usa este script salvo que cambies `METRICOOL_BLOG_ID`

## Estados Notion (Parrilla)

| Estado | Job |
|--------|-----|
| Aprobado - Edición Final | `schedule` (→ Programado) |
| Programado | `confirm_published`, `sync_dates` |
| Publicado | Solo lectura en confirm |

## Jobs

1. **schedule** — Semana calendario Bogotá. `SCHEDULE_MAX_PER_RUN` (default 5) cuenta creaciones; una fila omitida o duplicada no gasta cupo. Lee las redes conectadas en `simpleProfiles` (si falla: Instagram, TikTok, YouTube). Un Canal que no está conectado se quita y se registra; la fila solo se omite con `no_connected_network` cuando no queda ninguna. IG Nico omite la fila entera. Varios valores de Canal arman un solo post (`Youtube Shorts` → YouTube short). No reprograma fechas pasadas. Si la pieza ya está en Metricool (caption muy parecido, más estricto si la fecha difiere en más de 2 días, o misma red y misma hora ±15 min cuando el copy no es claramente distinto), no crea otra; palabras sueltas compartidas no cuentan. Si el horario ya tiene otra pieza, omite con `slot_conflict`. Un caption con placeholder no se programa. Si hay más de un candidato, omite y reporta. Un match único alinea Notion a Programado o Publicado. Sin media, o tipo Miniatura, se omite. Filtros CLI: `--only-date`, `--exclude-channel`, `--only-page-id`.
2. **confirm_published** — Programado ±7 días → Publicado solo si todos los `providers[].status` son `PUBLISHED`, o si el post ya no está en el scheduler y la hora pasó (margen 30 min). `ERROR` se reporta y no cambia el estado. `PENDING` se queda.
3. **sync_dates** — Notion manda la fecha. Empareja por id/uuid de Metricool si la fila lo tiene, si no por caption/título dentro de ±7 días, aunque la hora no coincida.

No usar `METRICOOL_BLOG_ID` `7255578` ni `7272512`. `--dry-run` no escribe en Metricool, Notion, media pública ni Slack.

## Archivo Final

- **Carpeta Drive:** requiere `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` + carpetas compartidas con esa cuenta. Listado + descarga vía API (`alt=media`).
- **Archivo Drive:** `/file/d/…` — preferir la misma SA (API). Sin SA, fallback `uc?export=download` (suele devolver HTML si no es público).
- **Dropbox:** un archivo (`/scl/fi/`, `/s/`) se descarga y se sube a Metricool. Una carpeta (`/scl/fo/`, `/sh/`) con `dl=1` es un ZIP: se lista con `files/list_folder` y cada archivo se baja por id (`id:...`), no por path. Carrusel: imágenes. Reel: el video.
- **URL en el post:** `PUT` + `PATCH /v2/media/s3/upload-transactions` (`planner`). Se guarda `convertedFileUrl` en `https://static.metricool.com`. No se usan litterbox, uguu, transfer.sh, S3 propio ni URLs de Drive o Dropbox. La extensión sale de magic bytes y mimeType (nunca `.bin`). Si la subida falla: `media_host_failed`. Un video de Instagram por encima de 25 Mbps o 300 MB se re-codifica a ~14 Mbps; TikTok o YouTube solos no.
- **YouTube URL:** programación sobre video existente (equipo debe pasarlo a público antes de la hora).
- **Carrusel:** todos los images de la carpeta (máx. 10).

## Portadas de Reel

Instagram `REEL` y `TRIAL_REEL` (también en un post de varias redes) necesitan portada antes de programarse. El texto de la portada es `Titulo` (`NOTION_PROP_COVER_TEXT`), no el título de la tarea. Vacío → `missing_hook`. `CTW_COVER_AGENT_PATH` prepara `cover-final.png`; se convierte a JPEG y Metricool lo recibe en `videoThumbnailUrl` (no hay campo de portada en `instagramData`). Si esa subida falla, la fila se omite con `media_host_failed`. `--dry-run` no sube archivos. `confirm_published` avisa `media_expired` si un post PENDING de las próximas 24 h tiene un media que no responde 200. Un `update` de Metricool devuelve un id nuevo y el mismo uuid: el match usa el uuid.
