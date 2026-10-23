# Changelog de LARA

## 2026-10-14 — Utilidades locales, avisos duraderos y recursos

- Recordatorios SQLite, recuperación de vencidos al arrancar, cancelación por
  ID, temporizadores y sesión de concentración de 25 minutos con aviso hablado
  si la voz de alertas está conectada.
- Comandos locales de tareas y catálogo `ayuda`, accesos en la GUI y consulta
  `mis motores`. Las escrituras pasan por la autorización existente.
- Perfil por RAM y número de hilos; respeta overrides de entorno. El lanzador
  deja de sobrescribir la elección de modelo. Sin nuevas dependencias.
- Clientes de nube opcionales cuando faltan claves; router con reintentos
  acotados y cadena configurable. Investigación de fondo optativa.
- Verificación: 38 pruebas unitarias/de integración correctas, incluyendo
  persistencia, reentrega tras fallo, autorización y arranque sin claves.
  Construcción Tk aislada correcta. No se validó audio real ni inferencia GPU;
  Ollama no respondió durante la revisión.

Formato: cada versión lista los cambios relevantes. Las novedades históricas
(v9.x, v10.x) están detalladas en `LEEME.md`.

## [No publicado] — Fase 1, segunda pasada (auditoría A-01/02/04/06/07/08)

### Seguridad
- Cerradas cuatro rutas que ejecutaban sin autorización: reintento de cierre
  "como administrador", "deshaz lo último", rutinas (cada paso con efecto se
  autoriza) y el alias `aprender_de_archivo`.
- Nueva categoría `ACCIONES_EFECTO` (`abrir`, `url`, `rutina`): directas en
  manual/auto, bloqueadas en plan/lectura. El dictado se detiene si el modo
  cambia a plan/lectura.
- Corregida una inyección de comandos: abrir una URL o un programa pasaba el
  texto del modelo a `cmd.exe` con `shell=True`. Ahora se usa `os.startfile`
  o listas de argumentos, con validación de URL (solo http/https).
- `ejecutar_cmd`, `ejecutar_cmd_admin` y la ejecución de código se niegan en
  la propia función sin `LARA_ENABLE_EXPERIMENTAL_TOOLS=1`.
- Open Code: sin UI de confirmación deniega; los comandos se confirman también
  en la pestaña "auto"; respeta el modo global plan/lectura y audita todo.

### Instalación y operación
- `requirements.lock` con versiones exactas (incl. transitivas),
  `INSTALAR_LARA.bat` (crea `.venv`, instala, prueba), `INICIAR_LARA.bat` ya
  no instala paquetes, `pyproject.toml` alineado (faltaba Kokoro),
  `requirements-dev.txt` y `tools/generar_lock.py`.
- `LARA_DATA_DIR` / `LARA_DATA_LOCAL` para elegir dónde viven los datos.
- Logging con rotación e ID de turno; `logs/eventos.jsonl` registra turnos,
  duración, autorizaciones y proveedor (local/nube).

### Pruebas
- De 6 a 32 pruebas, aisladas en un directorio temporal. Incluye la prueba de
  aceptación de la Fase 1 (`tests/test_aceptacion_modos.py`).

## [No publicado] — Estabilización Fase 1 y experiencia visual

### Seguridad y operación
- Se centralizó la autorización de acciones en `LaraCerebro._autorizar_accion`.
  Los atajos de notas, dictado, sistema y skills pasan por la misma política que
  las decisiones del modelo.
- Una acción de riesgo sin interfaz de confirmación se deniega; ya no se
  autoriza por defecto. El modo `auto` solicita confirmación para operaciones
  riesgosas durante la estabilización.
- Se pausaron por defecto el modo agente, ejecución de comandos/código,
  automatización de teclado/ratón y descubrimiento o ejecución dinámica de
  skills. `LARA_ENABLE_EXPERIMENTAL_TOOLS=1` queda reservado para desarrollo
  aislado, nunca para uso cotidiano.
- Se añadieron seis regresiones en `tests/test_fase1_policy.py`, un
  `.env.example` sin secretos y el documento `AUDITORIA.md` como fuente de
  verdad de riesgos, pendientes y criterios de aceptación.

### Interfaz gráfica
- La interfaz pasó de un HUD claro y rígido a un espacio oscuro de capas suaves:
  cabecera de producto, indicador de escucha, panel lateral de sesiones,
  superficie principal de conversación y entrada destacada.
- Se creó una paleta única y accesible en `ui_tema.py` (azul noche, turquesa,
  naranja de actividad y texto de alto contraste), junto con tipografía Segoe
  UI para mejorar legibilidad en Windows.
- Los mensajes de LARA y del usuario ahora tienen jerarquía visual mediante
  burbujas diferenciadas; las pestañas, controles y selector de modo comparten
  estados activos/inactivos coherentes.

## [No publicado] — LARA como agente de trabajo (nivel administrador)

### Añadido — Capacidades tipo Claude Code (`core/skills_archivos.py`)
Hasta ahora LARA solo podía CREAR archivos (sobrescribir enteros) y LEER
truncado a 1500 caracteres — no podía trabajar sobre código/textos existentes.
Ahora tiene las herramientas de un agente de verdad, todas por voz o texto:
- **editar_archivo(archivo, buscar, reemplazar)**: reemplazo EXACTO de un
  fragmento (Edit quirúrgico, no reescribe el archivo). Verifica unicidad
  (si el fragmento aparece varias veces, pide desambiguar; "reemplázalas
  todas" para todas). Deja copia `.bak` y registra "deshacer".
- **insertar_en_archivo(archivo, texto[, tras_linea])**: añade al final o
  tras una línea ancla.
- **leer_rango(archivo[, desde, hasta])**: lee el archivo ENTERO o un rango
  de líneas, con números de línea, SIN el tope de 1500 caracteres.
- **buscar_en_archivos(patron[, carpeta, glob])**: grep por contenido
  (regex), devuelve `archivo:línea: texto`. Salta binarios y `.git`,
  `__pycache__`, `node_modules`...
- **buscar_archivos(patron[, carpeta])**: glob por nombre (`*.py`,
  `factura*.pdf`), recursivo, ordenado por fecha.
Integradas en el router (nube y local), en el gate de modos (editar/insertar
piden confirmación en 'manual' y se bloquean en 'lectura'; leer/buscar son
inocuas) y con "deshacer" (restaura el `.bak`). Verificadas end-to-end.

### Cambiado — Ejecución con permisos de administrador que SÍ devuelve salida
- **cmd_admin ahora captura el resultado** (`core/skills_os.ejecutar_cmd_admin`):
  antes solo abría una ventana elevada y no podía leer nada; ahora redirige
  stdout/stderr del proceso elevado a un temporal, espera a que termine (con
  timeout, contando el tiempo del clic de UAC) y DEVUELVE la salida, para que
  LARA razone sobre ella como con un comando normal. `esperar=False` mantiene
  el modo lanzar-y-olvidar para instaladores interactivos. La salida se
  muestra en pantalla y se resume por voz.

## [No publicado] — Voz más rápida y fluida

### Añadido
- **Motor de voz elegible — Kokoro como voz principal**: di "usa la voz
  kokoro" para que LARA hable con Kokoro-82M LOCAL (sin internet, sin
  latencia de red) como primera opción, o "usa la voz de internet" para
  volver a edge-tts (Camila). La preferencia persiste en `audio_config.json`
  (o variable `LARA_TTS=kokoro|auto`). Cuando Kokoro es el motor elegido, se
  PRECARGA al arrancar para que la primera frase no pague la carga del modelo
  (~8 s medidos). Antes Kokoro solo era respaldo: aunque tuvieras los pesos
  descargados, nunca sonaba mientras hubiera internet.

### Cambiado (latencia y fluidez de la conversación)
- **Ya no se transcribe cada frase DOS veces**: la compuerta semántica de
  turno ya transcribía tu frase con Whisper para decidir si habías terminado,
  y luego `transcribir_crudo` la transcribía OTRA VEZ desde cero. Ahora
  `grabar_vad` devuelve esa transcripción y se reutiliza si no hablaste más
  después de la pausa (el caso normal) — se ahorra una pasada completa de
  Whisper (~0.5–2 s) en casi todas las frases. Era la causa principal del
  "se demora en entender lo que digo".
- **La evaluación de fin de turno arranca ANTES del corte**: antes se lanzaba
  recién al cumplirse los 0.6 s de silencio, así que al silencio se le sumaba
  el tiempo de Whisper + evaluador DESPUÉS. Ahora se lanza a ~0.33 s de pausa
  y corre en paralelo mientras se termina de esperar: al llegar al umbral de
  corte, el veredicto (y la transcripción) suelen estar ya listos y el corte
  es inmediato.
- **Voz en tubería (síntesis ∥ reproducción)**: un hilo sintetiza y otro
  reproduce. Mientras suena la frase N, la N+1 ya se está generando — se
  eliminó el silencio entre frases (igual al tiempo de síntesis: ~1–4 s con
  edge-tts medidos en esta red) que hacía la voz entrecortada en respuestas
  largas. La reproducción sigue estrictamente en orden y sin solaparse;
  `detener_voz` limpia también los audios ya sintetizados en cola.
- **La GUI precarga Whisper local al arrancar**: `main.py` (consola) ya lo
  hacía, pero la GUI —la vía que usas siempre— no: la PRIMERA frase que
  decías pagaba la carga del modelo (varios segundos) y parecía que LARA "no
  escuchaba". También precarga Kokoro si es el motor elegido.

### Cambiado (comprensión: "tengo que repetir varias veces")
- **Los monosílabos reales ya no se descartan como ruido**: un "sí"/"no"/"ok"
  dicho a ritmo normal suma ~0.25 s de voz y caía en DOS filtros (mínimo de
  voz 0.30 s y ventana anti-muletilla 0.45 s) — había que repetirlo alargado.
  Umbrales ahora: 0.20 s y 0.22 s; contra alucinaciones siguen la voz
  sostenida del VAD, la firma física de voz humana y el filtro de confianza
  de Whisper. Verificado con audio real: "sí" y "no" ahora se entienden.
- **Si el Whisper local DUDA, escala a la nube en vez de rendirse**: cuando
  el modelo "small" oía algo pero sus segmentos salían con confianza baja,
  devolvía vacío y LARA decía "no entendí" (a repetir). Ahora devuelve None
  y `transcribir_crudo` prueba Whisper large en la nube (Groq), que entiende
  mucho mejor los casos difíciles; la transcripción vacía de la compuerta de
  turno tampoco se reutiliza, para no cortarle el paso a esa escalada.
- **Vocabulario cebado en Whisper local** (`initial_prompt`): el "small"
  transcribía "blog de notas" por "bloc de notas"; con el prompt de contexto
  del taller sale bien (ajustable con `LARA_WHISPER_PROMPT`).
- **Umbral de energía del VAD con piso 140 (antes 200)**: hablando suave o
  con micrófonos de poca ganancia, bloques de voz real quedaban bajo 200 y la
  frase medía tan poca "voz" que se descartaba entera. Ajustable con
  `LARA_VAD_PISO` si tu ambiente es muy ruidoso.

### Añadido — Dúplex acústico estilo Gemini Live (sin palabra clave)
- **LARA ahora reconoce SU PROPIA voz** (`voz_id.NOMBRE_LARA`, `es_voz_de_lara`,
  `_puntajes` con umbrales propios de eco): la decisión de "¿eso lo dije yo o
  me están hablando?" deja de depender de tiempos y del texto y pasa a ser
  ACÚSTICA, como en los asistentes live. El perfil se SIEMBRA al arrancar con
  su TTS (`skills_voz.sembrar_voz_lara`, 4 muestras sin reproducir) y se
  REFINA solo: cada eco confirmado por texto aporta una muestra real de cómo
  suena por TUS parlantes.
- **Niveles al hablar encima de su voz** (gui): persona con perfil de voz
  reconocida -> se atiende AL INSTANTE, sin decir "Lara"; voz identificada
  como la propia LARA -> se ignora (eco), salvo que traiga "Lara" explícito;
  voz dudosa -> se pide "Lara" (red de seguridad)... hasta que el perfil de
  eco MADURA (≥8 muestras, `voz_lara_madura`): desde ahí, "no es su voz" es
  señal fiable y también se atiende sin palabra clave. Decir "Lara, aprende
  mi voz" una vez mete tu voz en el carril rápido desde el primer día.
- **El recorte post-voz verifica la huella antes de cortar**: si el tramo
  inicial de una toma mixta NO era la voz de LARA (eras tú hablándole encima),
  ya no se recorta (antes podía comerse el inicio de tu frase).
- Verificado con 4 voces TTS reales: frases nuevas de Camila se detectan como
  eco; voces masculina y femenina distintas se detectan como personas.

### Corregido
- **Hablarle JUSTO después de que LARA termina ya no cae al vacío** (dos
  causas, ambas del modo sin auriculares):
  1. El filtro de barge-in estricto ("sin la palabra Lara se ignora") se
     aplicaba a todo lo que empezara hasta 0.7 s DESPUÉS de su última
     palabra, no solo a lo hablado encima de su voz. Ahora hay dos ventanas:
     el eco por texto se sigue comprobando con margen amplio (-0.7 s), pero
     el filtro estricto solo aplica si ella SEGUÍA hablando cuando tu voz
     empezó (+0.15 s). Contestarle al vuelo ya se procesa normal.
  2. Si contestabas antes de la pausa de corte (0.6 s), tu frase quedaba
     PEGADA en la misma grabación que la voz de ella (el VAD se había
     disparado con sus parlantes) y el texto mixto se descartaba entero.
     Nuevo `skills_voz.recortar_voz_de_lara`: detecta ese caso, corta la
     parte de ella y procesa SOLO la tuya (verificado con audio real: de una
     toma mixta de 8 s rescató exactamente la frase del usuario).
- **Smart App Control bloqueaba PyAV y dejaba a LARA sin oído local**
  (`core/stt_local.py`): Windows 11 (Smart App Control) puede bloquear los
  `.pyd` SIN FIRMA del paquete `av` ("no podemos confirmar quién publicó
  output.pyd") y el import de faster-whisper moría entero → TODO el STT se
  iba a la nube sin avisar (una de las causas del "se demora en entender").
  faster-whisper solo usa `av` para decodificar ARCHIVOS y LARA le pasa
  arrays ya decodificados: si `av` no carga, ahora se inyecta un módulo hueco
  y Whisper local sigue funcionando igual (verificado simulando el bloqueo).
  Además ya no falla en silencio: imprime qué pasó y qué se pierde.

## [Anterior] — Endurecimiento y control

### Añadido
- **Reintentar cierre de programa con permisos de administrador**: si un
  proceso no se puede cerrar (ej. Task Manager, que en cuentas admin se
  auto-eleva SIN mostrar UAC y por eso taskkill normal le da "Acceso
  denegado"), LARA ahora lo dice explícitamente y, si le pides "usa permisos
  de administrador" a continuación (sin repetir el nombre del programa),
  reintenta con elevación real (dispara el UAC de Windows, requiere tu
  aprobación). Antes decía "puede necesitar permisos de administrador" como
  una excusa/adivinanza y la frase de seguimiento no hacía nada — repetía el
  mismo intento fallido.
- **Cerrar programas de verdad** (`core/skills_programas.cerrar_programa_inteligente`):
  antes NO existía ninguna acción dedicada a cerrar/terminar procesos — el
  router caía a "cmd" y el modelo tenía que ADIVINAR el nombre exacto del
  .exe (ej. "WhatsApp.exe"), fallaba casi siempre y LARA respondía con una
  excusa genérica ("no estaba activo"). Ahora busca por coincidencia difusa
  entre los procesos REALMENTE en ejecución (nombre y título de ventana,
  igual que abrir_programa_inteligente) y los cierra de verdad. Protegido
  contra cerrar procesos críticos del sistema (explorer, csrss, svchost...)
  y contra cerrarse a sí misma. Nueva acción del router: `cerrar(programa)`,
  con atajo instantáneo sin LLM para frases como "cierra X" / "termina el
  proceso de X".

### Corregido
- **LARA se escuchaba a sí misma y ejecutaba su propio eco como si fuera una
  orden tuya** (sin auriculares, laptop): el barge-in (interrumpirla
  hablando mientras habla) se activaba con CUALQUIER frase captada durante
  su voz que el filtro de eco por texto no reconociera — y sin auriculares
  el STT transcribe su propia voz saliendo por los parlantes distinto a
  como ella la dijo literalmente, así que el filtro fallaba seguido. Ahora
  el barge-in exige la palabra de activación ("Lara") como segunda red de
  seguridad; sin ella, lo que suena mientras habla se ignora en vez de
  ejecutarse. Coincide con lo que ya decía el docstring de `gui.py` pero el
  código no cumplía.
- **Candado de voz**: no es un bug que responda a cualquier voz — viene
  APAGADO por defecto hasta que lo activas (Config > candado de voz en la
  GUI, tras entrenar tu perfil). Documentado para que quede claro que es
  una función a encender, no un fallo.

- **Kokoro-82M como respaldo de voz offline REAL** (`core/skills_voz._tts_kokoro`):
  cadena de voz ahora es edge-tts (nube, calidad) -> Kokoro-82M (local, Apache
  2.0, CPU) -> pyttsx3 (último recurso, robótico). Antes, sin internet, LARA
  caía directo a pyttsx3; ahora hay un escalón intermedio que suena mucho
  mejor y es 100% local. Opcional: si el paquete `kokoro-onnx` o los pesos
  (~350 MB, no van en pip) no están, se auto-desactiva en silencio y LARA
  sigue exactamente igual que antes — ver `modelos_voz/kokoro/LEEME.txt`.
- **Compuerta semántica de turno de habla** (`core/turno_habla.py`): antes de
  cortar la grabación por silencio, evalúa si la IDEA está completa en vez de
  cortar por silencio físico a secas. Heurística instantánea (última palabra
  = preposición/conjunción/artículo -> sigues; puntuación terminal -> listo)
  y, solo en casos ambiguos, una consulta corta al modelo local (Ollama
  qwen2.5:3b) con salida COMPLETO/INCOMPLETO. Ya no corta a media frase por
  una pausa para pensar; tope duro (`LARA_TURNO_EXTENSION`, 1.8 s) para nunca
  colgar la grabación si el evaluador no contesta. Se apaga con
  `LARA_TURNO_SEMANTICO=0`. De paso, `skills_audio.grabar_vad` ya no
  recalibra el ruido de fondo en cada frase de la escucha continua (esa
  ventana de ~0.3 s "ciega" tragaba el arranque de tu voz y era otra causa de
  "tengo que hablar varias veces").
- **Escucha por voz continua (productor/consumidor)**: el micrófono ya no se
  cierra mientras LARA transcribe o piensa la frase anterior. Un hilo graba sin
  parar y encola las frases; otro las transcribe. Se acabó el "tengo que repetir
  varias veces" y el perder lo que dices justo después de que responde.
- **Barge-in real**: si hablas MIENTRAS LARA habla, corta su voz al instante y
  te atiende, sin necesidad de decir "Lara" (su propio eco se sigue filtrando;
  funciona mejor con auriculares).
- **Frases viejas descartadas**: si el proceso se atrasa, una frase de hace >8 s
  no se responde tarde (arregla las respuestas "de la nada" en modo reunión).
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
