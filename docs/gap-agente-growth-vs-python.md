# Agente Growth (LLM) vs script Python

| Aspecto | Agente Cursor | Este repo |
|---------|---------------|-----------|
| Costo | Tokens LLM + polling | Solo cron + APIs |
| Carpetas Drive | Interpretación manual / UI | Drive API + reglas carrusel/historias |
| Errores | Reintento conversacional | Logs + Slack; fallo explícito |
| Payload Metricool | Inspector / MCP | Código en `content_types.py` |

El script **no sustituye** pegar una carpeta en la UI de Metricool sin **GOOGLE_DRIVE_SERVICE_ACCOUNT_FILE** configurado.
