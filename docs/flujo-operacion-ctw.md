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

1. **schedule** — Semana calendario Bogotá, máx. `SCHEDULE_MAX_PER_RUN` (default 5). Por defecto excluye canal **Newsletter** (`SCHEDULE_EXCLUDE_CHANNELS`). Filtros CLI: `--only-date`, `--exclude-channel` (se suman al env).
2. **confirm_published** — Programado ±7 días → Publicado si Metricool publicó.
3. **sync_dates** — Alinea `Publicación` Notion vs Metricool (±1 min).

## Archivo Final

- **Carpeta Drive:** requiere `GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE` + carpetas compartidas con esa cuenta.
- **Archivo Drive:** enlace `/file/d/…` → descarga directa (sin service account).
- **Dropbox share/public:** `dl=1` / `raw=1` (sin `DROPBOX_ACCESS_TOKEN` ni API).
- **YouTube URL:** programación sobre video existente (equipo debe pasarlo a público antes de la hora).
- **Carrusel:** todos los images de la carpeta (máx. 10).

## Portadas (Fase 2 — opcional)

Solo **Instagram** vía `CTW_COVER_AGENT_PATH` + opcional `DROPBOX_ACCESS_TOKEN` (API, expira ~4h).  
Go-live: dejar path vacío y `REQUIRE_COVER_FOR_SCHEDULE=false` — **no bloquea** el schedule.
