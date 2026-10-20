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

1. **schedule** — Semana calendario Bogotá. `SCHEDULE_MAX_PER_RUN` (default 5) cuenta creaciones; una fila omitida o duplicada no gasta cupo. Lee las redes conectadas en `simpleProfiles` (si falla: Instagram, TikTok, YouTube). Un Canal que no está conectado se quita y se registra; la fila solo se omite con `no_connected_network` cuando no queda ninguna. IG Nico omite la fila entera. Varios valores de Canal arman un solo post (`Youtube Shorts` → YouTube short). No reprograma fechas pasadas. Si la pieza ya está en Metricool (caption muy parecido, más estricto si la fecha difiere en más de 2 días, o misma red y misma hora ±15 min cuando el copy no es claramente distinto), no crea otra; palabras sueltas compartidas no cuentan. El choque de horario se evalúa por red y grupo de formato (Historias solo con Historias; feed de Instagram — POST/carrusel, REEL, TRIAL_REEL — solo con feed; TikTok solo con TikTok; YouTube solo con YouTube; ±15 min, también contra posts de Metricool según su red y tipo, y contra otras filas de la corrida). Si una red choca, solo esa red se omite y se avisa con `slot_conflict`; las otras redes de la misma pieza sí se programan. Si todas chocan, la fila no se crea. Otra red u otro formato a la misma hora no es conflicto (un feed de Instagram no bloquea un TikTok). Un caption con placeholder no se programa. Si hay más de un candidato, omite y reporta. Un match único alinea Notion a Programado o Publicado, salvo un TikTok en Published sin id ni enlace: eso queda en Programado y se avisa. Todo post de TikTok (video o imagen) sale con `autoPublish: true` y `tiktokData.privacyOption` `PUBLIC_TO_EVERYONE`. Sin media, o tipo Miniatura, se omite. Filtros CLI: `--only-date`, `--exclude-channel`, `--only-page-id`.
2. **confirm_published** — Programado ±7 días → Publicado solo si todos los `providers[].status` son `PUBLISHED` de verdad. Un TikTok en Published sin id ni enlace del post (publicación manual: Metricool avisó, TikTok no publicó) no pasa a Publicado y Slack avisa «TikTok quedó en publicación manual / no salió». `ERROR` se reporta y no cambia el estado. `PENDING` se queda.
3. **sync_dates** — Notion manda la fecha. Empareja por id/uuid de Metricool si la fila lo tiene, si no por caption/título dentro de ±7 días, aunque la hora no coincida.

No usar `METRICOOL_BLOG_ID` `7255578` ni `7272512`. `--dry-run` no escribe en Metricool, Notion, media pública ni Slack.

## Archivo Final

- **Carpeta Drive:** requiere `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` + carpetas compartidas con esa cuenta. Listado + descarga vía API (`alt=media`).
- **Archivo Drive:** `/file/d/…` — preferir la misma SA (API). Sin SA, fallback `uc?export=download` (suele devolver HTML si no es público).
- **Dropbox:** un archivo (`/scl/fi/`, `/s/`) se descarga por el enlace público y se sube a Metricool. Una carpeta (`/scl/fo/`, `/sh/`) con `dl=1` es un ZIP: se lista con `files/list_folder` y `shared_link` (nunca la raíz de nuestra cuenta). Un archivo de otra cuenta se baja con `sharing/get_shared_link_file` y la ruta relativa dentro de esa carpeta. Un archivo nuestro (`path_lower`) se sigue bajando por id. La portada del Reel se busca en esa carpeta. Sin el permiso `sharing.read` el aviso lo dice; no se reporta como portada o archivo faltante. Carrusel: imágenes. Reel: el video.
- **URL en el post:** `PUT` + `PATCH /v2/media/s3/upload-transactions` (`planner`). Se guarda `convertedFileUrl` en `https://static.metricool.com`. No se usan litterbox, uguu, transfer.sh, S3 propio ni URLs de Drive o Dropbox. La extensión sale de magic bytes y mimeType (nunca `.bin`). Si la subida falla: `media_host_failed`. Un video de Instagram por encima de 25 Mbps o 300 MB se re-codifica a ~14 Mbps; TikTok o YouTube solos no.
- **YouTube URL:** programación sobre video existente (equipo debe pasarlo a público antes de la hora).
- **Carrusel:** todos los images de la carpeta (máx. 10).

## Portadas de Reel

Instagram `REEL` y `TRIAL_REEL` (también en un post de varias redes) necesitan portada antes de programarse. El texto de la portada es `Titulo` (`NOTION_PROP_COVER_TEXT`), no el título de la tarea. Vacío → `missing_hook`. `CTW_COVER_AGENT_PATH` prepara `cover-final.png`; se convierte a JPEG y Metricool lo recibe en `videoThumbnailUrl` (no hay campo de portada en `instagramData`). Si esa subida falla, la fila se omite con `media_host_failed`. `--dry-run` no sube archivos. `confirm_published` avisa `media_expired` si un post PENDING de las próximas 24 h tiene un media que no responde 200. Un `update` de Metricool devuelve un id nuevo y el mismo uuid: el match usa el uuid.
