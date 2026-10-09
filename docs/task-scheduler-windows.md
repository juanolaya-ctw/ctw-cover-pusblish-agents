# Task Scheduler (Windows)

Repo: `C:\ctw-automation-post\ctw-cover-pusblish-agents`

## Tareas sugeridas

| Tarea | Comando | Frecuencia |
|-------|---------|------------|
| Confirm published | `python -m metricool_sync_posts.cli.confirm_published` | Cada 1–2 h |
| Sync dates | `python -m metricool_sync_posts.cli.sync_dates` | Cada 30–60 min |
| Schedule | `python -m metricool_sync_posts.cli.schedule --enable` | Cada 6 h (solo cuando medios OK) |

## Acción

Programa una tarea con:

- **Program/script:** `C:\Users\metal\AppData\Local\Python\pythoncore-3.14-64\python.exe`
- **Argumentos:** `-m metricool_sync_posts.cli.confirm_published`
- **Iniciar en:** `C:\ctw-automation-post\ctw-cover-pusblish-agents`

Repite para `sync_dates` y `schedule --enable`.

Usa la misma cuenta de usuario que tiene el `.env` configurado.
