# LARA v10.2 — Asistente del Taller (estilo JARVIS)

## Novedades v10.2 (memoria local y 2 segundos menos en TODO lo local)
- **Memoria semántica 100% LOCAL:** los recuerdos de largo plazo ahora se
  buscan con `embeddinggemma` (Ollama, multilingüe) EN TU PC. Antes cada turno
  hacía una llamada de embeddings a Gemini (y llevaba tiempo fallando: tus 62
  recuerdos estaban guardados SIN vector, buscándose solo por palabras). Ya
  están todos migrados y la búsqueda por significado funciona de verdad:
  "cuándo cobro lo del ampli" encuentra "el cliente del amplificador paga los
  viernes" (medido 0.54-0.68 de similitud vs 0.23-0.34 el ruido; umbral
  calibrado a 0.45). Instalar en otra PC: `ollama pull embeddinggemma`.
- **~2 segundos menos en CADA llamada local (medido):** dos problemas de
  Windows corregidos en el cliente de Ollama — `requests` consultaba el proxy
  del sistema en cada petición y "localhost" intentaba IPv6 antes de IPv4.
  Ahora: sesión HTTP persistente sin proxy + `127.0.0.1`. Un embed pasó de
  2.5 s a 0.3 s; recordar() por turno de ~2.5 s a ~0.3-0.5 s.
- **Caché de decisiones del router local:** si repites un comando en menos de
  10 min ("modo noche"), la decisión sale al instante. Solo se cachean acciones
  (nunca charla, que debe variar) y frases sin referencias al contexto.
- **Validador más estricto:** el 3B inventaba operaciones plausibles pero
  inexistentes (`sistema/modo_avion`) — ahora se valida también la operación
  de `sistema` contra la lista real y ante la duda decide la nube. La memoria
  además ya no indexa respuestas-error ("No tengo ese dato exacto...").
- **Contador de AHORRO en "uso":** el resumen diario de tokens ahora muestra
  cuántos turnos se resolvieron en tu PC sin gastar nube (instantáneos + modelo
  local).
- **Banco de pruebas:** `python probar_cerebro.py` verifica en segundos el
  reflejo, el filtro simple/fuerte, el validador, el modelo local en vivo y la
  memoria semántica. Úsalo después de cualquier cambio en `core/`.

## Novedades v10.1 (reflejos instantáneos y más transparencia)
- **Router "reflejo" (0 segundos, sin ninguna IA):** los comandos inequívocos
  se resuelven con patrones deterministas al instante y con 100% de acierto:
  "abre X" (programas, carpetas y hasta la papelera), "qué hora es", "qué día
  es hoy" (¡la fecha la sabe la PC, antes la buscaba en internet!), "brillo al
  80", "sube/baja el volumen", "silencia", "captura la pantalla", "cuánta
  batería queda", "bloquea la pc", "mi ip", "wifi", "procesos", "portapapeles",
  "vacía la papelera" y "recuérdame X en N minutos / media hora / una hora".
- **Indicador de motor en la GUI:** junto al selector de esfuerzo ahora se ve
  QUIÉN respondió cada turno: ⚡ reflejo (instantáneo), 🏠 local (tu PC) o
  ☁ nube (y cuál modelo). Sabes de un vistazo si estás gastando cuota.
- **Sin internet, LARA sigue hablando EN STREAMING:** si toda la nube cae, el
  razonamiento profundo lo hace el modelo local por frases — empieza a hablar
  a los ~2-3 s en vez de esperar ~30 s el texto completo.
- **La compactación de memoria ya no congela el turno:** resumir conversaciones
  viejas (que usa un LLM y tardaba segundos) ahora corre en segundo plano.
- **Guarda decir-vs-hacer:** si pides una acción y el modelo local intenta solo
  "decirla" sin hacerla, su respuesta se descarta y decide la nube.

## Novedades v10.0 (cerebro HÍBRIDO local + nube)
- **Cerebro local encendido por defecto:** las tareas SIMPLES ("abre chrome",
  "qué hora es", "sube el brillo", charla corta) las resuelve `qwen2.5:3b-instruct`
  corriendo en TU PC vía Ollama — en ~2-4 s, sin internet, sin límites y sin
  gastar cuota de Groq/Gemini. Las tareas FUERTES (razonamiento técnico, código,
  comparaciones) y TODAS las búsquedas en internet siguen yendo a la nube, que
  para eso es mucho mejor. Si Ollama no está corriendo, todo va a la nube como
  antes (cero cambio). Apagar a propósito: `LARA_LLM_LOCAL=0`.
- **Optimizado de verdad para tu PC:** el router local usa un prompt compacto y
  ESTÁTICO para que Ollama cachee el prefijo (medido: de ~15 s en frío a ~2-4 s
  cacheado), precalienta el modelo al arrancar (`keep_alive=30m`, no recarga los
  2 GB en cada frase) y recorta el historial que le pasa (un 3B en CPU lee ~80
  tokens/s: cada token de más es latencia).
- **Filtro de seguridad sobre el 3B:** su decisión se valida (JSON sano, acción
  conocida, mensaje no vacío) y si algo no cuadra decide la nube. Frases largas,
  palabras de tarea compleja o nivel de esfuerzo extra/max/ultracode ni siquiera
  pasan por el local. Además, la red anti-desactualización de siempre sigue
  activa: si el local intenta responder actualidad de memoria, se relanza como
  búsqueda web.
- **LARA ya no se queda muda sin nube:** el cerebro local es el respaldo de
  EMERGENCIA en todo el motor — si Groq y Gemini están caídos/saturados, el
  local enruta y hasta responde razonamiento profundo (más modesto, pero
  responde), incluso sin internet.
- **Diagnóstico con estadísticas:** "diagnóstico" ahora muestra cuántas
  respuestas resolvió el cerebro local en la sesión (= llamadas a la nube que
  te ahorraste) y cuántas cayeron a la nube.
- Requisito en cada PC donde quieras el modo local: instalar Ollama y bajar el
  modelo con `ollama pull qwen2.5:3b-instruct` (el .bat ya deja las variables).

## Novedades v9.9 (robustez y conversación natural)
- **Auto-diagnóstico:** di/escribe "diagnóstico" o "revísate" y comprueba de un
  vistazo micrófono, STT local, voz, modelos, perfiles de voz, wake word y
  memoria. `core/diagnostico.py`.
- **Respuestas habladas breves:** cuando la voz está activa, Lara va al grano
  (1-3 frases) para que la charla sea fluida. Toggle por voz: "sé breve" /
  "respuestas largas".
- **Control de conversación por voz:** "para" (corta lo que dice), "repite",
  "habla más rápido/despacio/normal".
- **Nota:** en este equipo ya están instalados `faster-whisper` (STT offline) y
  `openwakeword` (wake word local, palabra "hey jarvis"). ¡Prueba diciendo
  "Hey Jarvis"!

## Novedades v9.8
- **Reconoce tu voz sin el botón de entrenar:** (1) di "Lara, aprende mi voz"
  (o "...como Azif") y aprende mientras hablas normal, sin frases guiadas;
  (2) cada vez que te reconoce con seguridad, refuerza tu perfil solo, así
  mejora con el uso. Sigue funcionando el candado de voz (apagado por defecto).
- **Abre apps del sistema por URI:** "abre mi cámara", "abre configuración",
  "abre la tienda", "fotos", "reloj"... ahora se lanzan por su protocolo de
  Windows (fiable), en vez de depender del nombre en el menú inicio.
- **Respuestas más resistentes a caídas:** si los servidores de IA se saturan
  (503/429), reintenta en 4-6 s (antes 15 s) y pasa antes al respaldo.


Asistente de IA personal para Windows. Tres motores en la nube (Llama orquestador,
Gemma razonamiento profundo, Compound gestor) + voz, visión y memoria persistente.

## Novedades v9.5 (salto "tipo JARVIS")
1. **Voz local (offline):** transcribe con `faster-whisper` EN tu PC — sin internet,
   sin límites y con menos latencia. Cae a Whisper en la nube si no está instalado.
   Instálalo con `pip install faster-whisper`. Variables opcionales:
   `LARA_WHISPER_MODEL` (small/medium/large-v3) y `LARA_WHISPER_DEVICE` (cpu/cuda).
2. **Wake word "Oye, Lara":** manos libres real. Consola: `lara escucha` (parar: `wake off`).
   Verifica quién habla con `voz_id` e interrumpe su propia voz si la llamas (barge-in).
3. **Respuesta en streaming:** en el razonamiento profundo, Lara empieza a hablar
   apenas tiene la primera frase (no espera a terminar toda la respuesta).
4. **Memoria semántica de largo plazo:** recuerda cosas de conversaciones pasadas
   por SIGNIFICADO (embeddings de Gemini), aunque preguntes con otras palabras.
   Respaldo por palabras clave si no hay conexión. Archivo: `conocimiento/memoria_vectorial.json`.
5. **Modo reunión + análisis:** escucha el ambiente y, cuando le preguntas, resume y
   opina. Consola: `reunion on` / `opina` / `acta` / `reunion off`. AVISA en voz alta
   que está grabando (consentimiento) y NO guarda audio, solo texto. ⚠️ Grabar a
   terceros sin avisar puede ser ilegal — úsalo con consentimiento de los presentes.

## Novedades v9.6 (presencia proactiva JARVIS)
6. **Briefing al arrancar:** te saluda por tu nombre según la hora y te pone al
   día (tareas urgentes, compras, novedades). `core/rutinas.py`.
7. **Alertas habladas:** el agente de fondo AVISA en voz alta lo crítico (tareas
   que vencen ya), no solo con notificación en pantalla.
8. **Chime de presencia:** un toque de sonido al activarse por wake word ("te
   escucho"), sintetizado sin archivos (`skills_audio.reproducir_tono`).
9. **GUI con streaming y reunión:** la ventana ahora habla en streaming y tiene
   botón "🎙️ Reunión" (transcribe la sala; responde solo si dices "Lara").
10. **Conversación continua:** tras responder por wake word, Lara sigue
    escuchando unos segundos para repreguntas SIN repetir "Lara". Se corta al
    quedarte en silencio o decir "gracias/eso es todo".

## Novedades v9.7 (autonomía JARVIS)
11. **Wake word LOCAL instantáneo (openWakeWord):** detecta la palabra clave en
    tiempo real con muy poca CPU, sin transcribir cada frase. `pip install openwakeword`.
    Palabra por defecto: "hey jarvis" (modelo preentrenado). openWakeWord no trae
    un modelo "Lara"; si entrenas uno, apúntalo con la variable `LARA_WAKE_MODEL`
    (ruta al .onnx/.tflite) o usa otro preentrenado (alexa, hey_mycroft). Si no
    está instalado, sigue funcionando el wake word por transcripción (que sí
    reconoce "Lara"). Umbral ajustable con `LARA_WAKE_UMBRAL` (0-1).
12. **Modo agente (multipaso):** encadena pasos sola — ejecuta una acción, mira el
    resultado y decide la siguiente hasta resolver la tarea (máx. 5 pasos). Se
    enciende diciendo/escribiendo "modo agente on" y se apaga con "modo agente off".
    Por defecto OFF (mantiene la respuesta ágil de un paso).
13. **Rutinas programables:** una frase dispara una secuencia de acciones.
    De fábrica: "modo taller" (abre VS Code, brillo alto, lista tareas),
    "modo noche" (baja brillo, silencia, resumen, despedida), "modo concentración"
    (silencia), "buenos días" (brillo + briefing). Puedes añadir/editar las tuyas
    en `conocimiento/rutinas.json`. `core/rutinas.py`.

## Cómo iniciar
- **Interfaz gráfica (recomendado):** doble clic en `INICIAR_LARA.bat` (instala lo que falte y abre la ventana).
- **Solo consola:** `python main.py`

## Qué puede hacer ahora
- **Voz:** te habla con voz neuronal en español (edge-tts) y te escucha por micrófono.
  - En la GUI: botón 🎤 (push-to-talk), 🔊 para silenciar, 👂 modo manos libres.
  - En consola: `voz on`, `voz off`, `escuchar`.
- **Visión / pantalla:** botón 📷 "Ver pantalla" — captura tu monitor y recomienda qué hacer.
- **Archivos:** botón 📎 "Subir archivo" analiza fotos, PDF, video, audio o documentos;
  🧠 "Aprender archivo" además guarda lo aprendido en su memoria permanente.
- **Sistema operativo:** ejecuta comandos, abre programas, crea/lee/mueve archivos, brillo, etc.
- **Internet:** busca, lee páginas, descarga archivos y videos.
- **Gestión del taller:** tareas, clientes, compras, proyectos, recordatorios.
- **Memoria entre sesiones:** recuerda conversaciones anteriores y aprende skills nuevas.

## Estructura
- `gui.py` — interfaz gráfica.
- `main.py` — modo consola.
- `core/cerebro.py` — núcleo: decide y ejecuta (compartido por GUI y consola).
- `core/skills_voz.py` — voz (TTS + STT).
- `core/skills_vision.py` — visión multimodal (imágenes, pantalla, archivos, video).
- `core/llm_engine.py` — los tres motores LLM.
- Resto de `core/` — skills de SO, web, memoria, conocimiento, gestión, etc.

## Multi-PC
Las API keys están en `.env` y los datos en OneDrive/Taller_IA, así que LARA funciona
igual en cualquier PC tuyo. En una máquina nueva, `INICIAR_LARA.bat` instala todo solo.

## Notas
- El micrófono usa `sounddevice` (PyAudio no compila en Python 3.14).
- Para mejor voz se usa internet (edge-tts); sin conexión cae a la voz offline de Windows.
