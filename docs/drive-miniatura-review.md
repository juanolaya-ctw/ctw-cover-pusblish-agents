# Portada desde Drive público: ajuste mínimo

Base main 9ccb8b1, rama propuesta review/drive-direct-miniatura.
Permite SOLO https://drive.google.com/uc?export=download&id=FILE como Miniatura.
Rechaza Drive folder, /view, IDs vacíos/duplicados, queries adicionales y fragmentos.
No cambia media pipeline, schedule, jobs de confirm/sync ni workflow.
No hace pública una imagen privada: la imagen debe ser descargable públicamente.
Miniatura no genera portada ni crea permisos. REQUIRE_COVER_FOR_SCHEDULE=false.

Para cover-final.png verificado:
https://drive.google.com/uc?export=download&id=130ls965KuP_cNA00ixuwwTGekOYIJkzW
Descarga anónima comprobada HTTP 200 image/png, 2,413,589 bytes.
Imagen inspeccionada: vertical 804x1429, texto visible, sin fecha del CTA antiguo.
No se probó normalize de Metricool ni aceptación real de portada por Instagram.
Un piloto autorizado debe comprobarlos; si normalize falla, cover sigue opcional.

Pruebas: 86 offline verdes (73 previas + 13 nuevas), ruff limpio.
Incluye allow exacto, dry-run sin normalize, URL normalizada, fallo opcional y
rechazo de variantes Drive que no sean descarga directa. Sin POST ni changes remotos.

Propuesta de draft PR:
Título: Allow verified public Drive direct-download Miniatura URL
Descripción: Allow only exact public Drive uc export=download links in the cover
resolver; keep folder/view links rejected. No OAuth or sharing changes. 86 tests
pass, ruff clean. Metricool normalization and planner cover still require pilot.

Sharing detectado: anyone:writer en carpeta y cover. No se modificó.
El dueño debería revisar y decidir si anyone debe ser reader antes de publicar.
