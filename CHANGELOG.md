# Changelog de LARA

Formato: cada versión lista los cambios relevantes. Las novedades históricas
(v9.x, v10.x) están detalladas en `LEEME.md`.

## [No publicado] — Endurecimiento y control

### Añadido
- **Control de versiones (git)**: repositorio inicializado, `.gitignore`
  (secretos y datos de runtime fuera del repo), `ESTRUCTURA.md` y este changelog.
- **Modos de operación configurables** (`core/modos.py`), estilo Claude Code:
  - `auto` — ejecuta todo sin preguntar.
  - `manual` — pide confirmación antes de acciones de riesgo (comandos, código,
    mover/borrar archivos, descargas).
  - `plan` — no ejecuta nada; describe lo que haría y espera aprobación.
  - `lectura` — solo responde/consulta; bloquea toda acción que modifique el equipo.
  - El modo se cambia por voz/texto ("modo auto", "modo manual"...) y desde la GUI,
    y persiste entre reinicios.
- **Auditoría de acciones**: toda acción de riesgo queda registrada en
  `Taller_IA/logs/auditoria.log`.
- **Deshacer (undo)**: `mover_archivo` registra su inverso; "deshaz lo último"
  revierte la última acción reversible.
- **Memoria vectorial sobre SQLite**: sustituye el JSON monolítico (que se
  reescribía entero en cada guardado). Migra automáticamente los datos antiguos.

### Cambiado
- **Router más robusto**: extracción de JSON con conteo de llaves balanceado y
  modo JSON nativo de Groq donde aplica (menos "el modelo devolvió JSON roto").
- **Despacho por tabla**: `_procesar_respuesta_llm` pasó de un `if/elif` gigante
  a un registro de manejadores (más fácil de extender y probar).
- **Concurrencia**: `procesar()` es reentrante-seguro (lock) para GUI + wake word
  + compactación de fondo a la vez.

### Corregido
- Errores antes silenciados (`except: pass`) ahora se registran en el log.
- Comentario obsoleto sobre `llama-3.3-70b` (ya no se usa en el código).
