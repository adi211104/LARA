# Changelog de LARA

Formato: cada versión lista los cambios relevantes. Las novedades históricas
(v9.x, v10.x) están detalladas en `LEEME.md`.

## [No publicado] — Endurecimiento y control

### Añadido
- **Planner multipaso**: con el modo agente activo, LARA descompone una petición
  compleja en pasos, te MUESTRA el plan y los ejecuta en orden encadenando
  resultados (respetando el modo: en 'plan' solo muestra, en 'manual' confirma
  cada paso de riesgo). `core/llm_engine.generar_plan` + `cerebro.ejecutar_plan`.
- **Volumen a un nivel exacto**: "pon el audio al 50%" ahora funciona (op de
  sistema `volumen` 0-100, sin dependencias nuevas).
- **Auto-recuperación ("halla la manera")**: si LARA no tiene una operación
  directa para algo, ya no dice "no puedo": re-enruta para lograrlo por otra vía
  (comando de Windows o proponiendo una skill), con el gate de modos de por medio.
- **RAG sobre tus documentos** (`core/rag_documentos.py`): LARA indexa el
  contenido de tus PDF, Word, TXT y Markdown (carpeta `Taller_IA/documentos_rag`
  y `workspace`) y responde CITANDO el archivo de origen. Usa los embeddings
  LOCALES de Ollama (rápido, gratis, offline), guarda en SQLite e indexa de
  forma INCREMENTAL (solo lo nuevo/modificado). Comandos: "indexa mis documentos",
  "indexa la carpeta X", "estado de documentos". Se indexa solo al arrancar.
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
- **Voz más rápida**: el silencio final que dispara la transcripción baja de
  1.5s a 0.8s y Whisper pasa a decodificación greedy (~2-3x más rápido); se
  silencia el aviso de symlinks de HuggingFace.
- **Nube más ágil ante caídas**: ante 500/503 ya no se duerme ~18s reintentando
  el mismo modelo; pasa al siguiente y, si todos caen, a Gemini en pocos segundos.
