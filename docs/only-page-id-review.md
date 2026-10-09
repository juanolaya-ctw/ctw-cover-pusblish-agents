# Selector de piloto: --only-page-id

Base main a9ae093 (merge PR #2). Rama propuesta review/only-page-id.

Cambio aditivo: schedule acepta --only-page-id UUID (con o sin guiones).
Valida UUID antes de abrir clientes. Consulta TODAS las filas aprobadas de la
semana, antes del límite normal, y selecciona exactamente una página por UUID.
Cero coincidencias, duplicadas o exclusión por Canal/fecha producen error y
salida no cero antes de caption, portada, media o POST. No hay fallback a otras
filas. Los canales desconocidos se siguen omitiendo por las guardas existentes.
Sin flag, la consulta/límite/filtros normales siguen iguales.
No modifica media, confirm_published, sync_dates ni workflow.

Ejemplo dry-run (reemplazar UUID por el de la fila elegida, NO por el nombre):
python -m metricool_sync_posts.cli.schedule --enable --dry-run --only-page-id UUID --exclude-channel "IG Nico"

No ejecutar live solo porque dry-run pasa. Antes del piloto real:
- Verificar fila actual, estado aprobado, semana actual y hora futura Bogotá.
- Revisar marca Colombia Tech 5822365, IG, caption, vídeo y Miniatura durable.
- Revisar planner para duplicados y pedir OK del usuario sobre ese único post.
- Mantener --only-page-id y exclusión IG Nico, sin cron.
- Asegurar que guard durable se conserve y reconciliar timeout antes de retry.
- Verificar después un único post y portada visible; selector no valida portada.

No se añade dispatch live al workflow Actions. Sigue manual dry-run.
Persistencia durable en runners efímeros continúa pendiente.

Pruebas offline: 73 pasan, ruff limpio. Ocho pruebas nuevas: target después de
primeros cinco, UUID sin guiones, cero coincidencias, duplicadas, fila manual,
fecha no coincidente, UUID inválido, y modo sin flag con límite anterior.
No se hicieron lecturas Notion/Metricool reales ni POST ni cambios remotos.
