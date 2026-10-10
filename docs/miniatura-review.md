# Diff propuesto: Miniatura y guardas de schedule

Base: main, merge PR #1 (285c26f). Rama local: review/miniatura-schedule-guards.

## Cambio de portada

Miniatura externa pública HTTPS sirve como portada de REEL/TRIAL_REEL de Instagram.
No requiere ctw-cover-agent ni Dropbox OAuth. Dropbox share pasa a dl=1/raw=1.
Drive preview/carpetas y URLs firmadas con X-Amz-Expires no se aceptan como portada:
usar una imagen pública durable en Miniatura. No se agrega la imagen al array media.
El bridge de bytes existente queda como fallback opcional.
REQUIRE_COVER_FOR_SCHEDULE sigue false. Si se activa en el futuro, valida la URL
resuelta, no solo la existencia de bytes.

Fuente oficial revisada el 9 de octubre de 2026:
https://app.metricool.com/resources/apidocs/index.html
https://app.metricool.com/api/swagger.json
ScheduledPost declara videoThumbnailUrl en raíz. ScheduledPostInstagramData no
declara coverUrl. Se sustituye el campo no documentado por videoThumbnailUrl.
Esta es validación de esquema, no confirmación de publicación en Instagram.
Hace falta un piloto autorizado de un solo Reel y verificar la portada en planner.
No se añade soporte de portada YT/TikTok ni nuevos campos de política por red.

## Guardas

- Newsletter y LinkedIn Majo siempre manuales. Se suman exclusiones env y CLI.
- IG Nico excluido en pruebas y workflow.
- YT se reconoce; desconocidos o múltiples redes se omiten, sin fallback Instagram.
- Multi-select Canal con varias entradas no elige silenciosamente la primera.
- Consulta Metricool antes del POST: texto normalizado exacto + red + fecha ±1 min.
  Un candidato existente se omite para revisión, sin marcar Notion automáticamente.
- SQLite reserva marca + página ANTES del POST, con clave única atómica.
  Timeout o fallo posterior de Notion deja reserva: no auto-reintento.
  Reconciliar manualmente con Metricool antes de liberar una reserva.
- El guard debe estar en almacenamiento durable y compartido entre todos los
  procesos live. No borrar .data ni cambiar su ubicación sin migrar el guard.
  No proporciona exactly-once distribuido ni protege contra operadores externos.
  El chequeo remoto es una segunda guarda, no sustituto de estado durable.

confirm_published, sync_dates y media/* no se modifican.

## GitHub Actions: solo preparación segura

Workflow manual, tres jobs secuenciales en dry-run, Python 3.11 y ffmpeg.
No push/PR trigger con secretos. Permiso contents: read. Concurrencia única.
Sin cron activo, sin ruta live habilitada. Schedule usa --enable solo junto con
--dry-run para ejercitar el flujo; no crea posts ni modifica estados Notion.
Las lecturas Notion/Metricool requieren credenciales reales; no se probaron aquí.
No subir logs ni artefactos con captions, URLs privadas, guard o credenciales.

Cron candidato comentado para lunes-viernes 08-20 Bogotá cada dos horas:
13,15,17,19,21,23 UTC lunes-viernes; 01 UTC martes-sábado para 20:00 previo.
Festivos no se filtran. Confirmado por Juan: lunes-viernes, festivos incluidos; no fines de semana.
La fuente oficial de GitHub advierte retrasos o descartes en carga alta,
especialmente al inicio de cada hora; cron no garantiza minuto exacto.
https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows

ANTES de activar live/cron:
1. Obtener OK de un piloto único.
2. Resolver persistencia durable del guard. Runner Actions es efímero.
   Cache/artifacts no se tratan como almacenamiento transaccional: no habilitar
   live solo añadiendo un cache. Preferir registro durable externo, o persistir
   id/estado por página en Notion con un protocolo de reconciliación revisado.
3. Verificar el post piloto y su portada, exclusiones y lectura de credenciales.
4. Revisar permisos y protección de ramas. En repo público, quien pueda modificar
   un workflow autorizado puede provocar disclosure de secrets en un run.

## Configuración en GitHub

Settings > Secrets and variables > Actions > New repository secret:
- NOTION_TOKEN (integración propia compartida con parrilla, lectura/actualización).
- NOTION_DATABASE_ID (data source ID).
- METRICOOL_USER_TOKEN (X-Mc-Auth).
- METRICOOL_USER_ID.
- GDRIVE_SA_JSON (JSON íntegro; opcional en dry-run, necesario para Drive live).
  Compartir fuentes Drive con email de la service account. Se escribe solo en
  runner.temp, modo 0600, y se elimina con always(). Nunca commitear archivo.

blogId=5822365 y zonas America/Bogota se fijan en el workflow.
No pedir DROPBOX_ACCESS_TOKEN. No .env ni gdrive-sa.json en Git.
El media y la portada se suben a Metricool (`/v2/media/s3/upload-transactions`)
y el post guarda `https://static.metricool.com`. No se usan litterbox, uguu,
transfer.sh ni un enlace de carpeta Dropbox. Un link que ya quedó en Metricool
y no responde 200 se detecta en `confirm_published` (`media_expired`) antes de
la hora. La portada del reel es un JPEG en `videoThumbnailUrl`.
No Slack configurado: no introduce destinatarios de alertas.

Guía oficial de secrets:
https://docs.github.com/en/actions/security-for-github-actions/security-guides/using-secrets-in-github-actions
