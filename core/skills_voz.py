"""
skills_voz.py — Voz de LARA (entrada y salida de audio).

SALIDA (TTS): tubería de DOS hilos — uno SINTETIZA (texto -> archivo de audio)
y otro REPRODUCE en orden, uno a la vez (nunca se solapa). Mientras suena una
frase, la siguiente ya se está generando: se eliminó el silencio entre frases
que hacía la voz poco fluida. Motor preferido configurable (get/set_motor_tts,
persiste en audio_config.json, o variable LARA_TTS):
  - "auto"/"edge": edge-tts (nube, voz Camila) primero; Kokoro de respaldo.
  - "kokoro": Kokoro-82M LOCAL primero (offline, sin latencia de red).
Último recurso siempre: pyttsx3 (offline pero robótico, para nunca quedarse
muda). Por voz: "usa la voz kokoro" / "usa la voz de internet".

ENTRADA (STT): graba con detección de voz (VAD) — espera a que hables y corta
cuando haces una pausa, en vez de un tiempo fijo de 5 s — y transcribe con el
reconocedor gratuito de Google.

on_done: hablar() acepta un callback que se llama al terminar de reproducir esa
frase. Lo usa la animación de explicación para avanzar de slide sincronizada.
"""

import os
import re
import time
import queue
import tempfile
import threading

from config import PROYECTO_DIR

# Voz por defecto (peruana, femenina, neuronal). Alternativas:
# es-MX-DaliaNeural, es-ES-ElviraNeural, es-PE-AlexNeural (masculina).
VOZ_TTS = "es-PE-CamilaNeural"
IDIOMA_STT = "es-PE"

# Velocidad del habla (edge-tts). Formato "+0%", "+15%", "-10%"... Se ajusta con
# set_velocidad() o por voz ("habla más rápido/despacio").
VELOCIDAD_TTS = "+0%"
_velocidad_pct = 0  # entero interno; VELOCIDAD_TTS se deriva de este

VOZ_ACTIVA = True
_dir_tmp = os.path.join(tempfile.gettempdir(), "lara_voz")
os.makedirs(_dir_tmp, exist_ok=True)

# ── Ajustes de conversación (tunea aquí la fluidez) ───────────────────────────
# Estos valores son el EQUILIBRIO entre "que no me corte" y "que responda rápido".
#   VAD_FIN_SILENCIO: segundos de silencio tras hablar antes de EVALUAR si
#     cortar. Ya no es el corte final a secas: core/turno_habla.py decide si
#     tu idea está completa; si no, skills_audio extiende la grabación en vez
#     de cortarte a media frase (ver LARA_TURNO_EXTENSION más abajo). Por eso
#     este valor puede ser más bajo que antes sin que te corte por pausas para
#     pensar — solo afecta cuánto tarda en REACCIONAR cuando sí terminaste.
#   VAD_MAX_FRASE: tope de segundos de una sola intervención (para frases largas
#     no se corta a los 15 s como antes).
# 0.6 s (antes 0.8): con la compuerta semántica activa, una pausa real para
# pensar ya no corta la frase (se extiende), así que se puede reaccionar más
# rápido en cuanto sí terminaste de hablar. Se puede forzar con la variable
# LARA_FIN_SILENCIO. Margen de extensión: LARA_TURNO_EXTENSION (por defecto
# 1.8 s). Apagar la compuerta semántica entera: LARA_TURNO_SEMANTICO=0.
VAD_FIN_SILENCIO = float(os.environ.get("LARA_FIN_SILENCIO", "0.6"))
VAD_MAX_FRASE = 22.0

# Tubería de voz en DOS etapas (síntesis || reproducción):
#   _cola (texto)  ->  hilo de SÍNTESIS  ->  _cola_audio (archivos)  ->  hilo de
#   REPRODUCCIÓN. Mientras suena una frase, la siguiente ya se está generando
#   (antes se generaba y reproducía en serie: entre frase y frase había un
#   hueco igual al tiempo de síntesis — ese silencio era lo que se sentía
#   "poco fluido"). _cola_audio va acotada (2) para no sintetizar de más si
#   luego llega un detener_voz().
_cola = queue.Queue()
_cola_audio = queue.Queue(maxsize=2)
_worker = None
_worker_play = None
_worker_lock = threading.Lock()
# Generación de voz: detener_voz() la incrementa; un audio sintetizado ANTES
# del stop (p.ej. el que estaba bloqueado entrando a la cola justo entonces)
# ya no se reproduce después ("para" no deja colas fantasma).
_gen_voz = 0
_hablando = False        # True mientras LARA está reproduciendo voz
_ultimo_fin_habla = 0.0  # time.time() en que terminó la última frase reproducida
_frases_dichas = []      # [(time.time(), texto normalizado)] de lo último que dijo LARA
# Ventana anti-eco: antes 20 s era demasiado — durante 20 s cualquier cosa que
# dijeras parecida a lo que LARA dijo se descartaba, y a veces tragaba tu voz
# real (por eso "tenías que hablar dos veces"). 8 s cubre el eco sin comerse
# tus respuestas.
_ECO_VENTANA = 8.0


def esta_hablando() -> bool:
    return _hablando or not _cola.empty() or not _cola_audio.empty()


def hablo_desde(ts: float) -> bool:
    """True si LARA está hablando ahora o habló en algún momento desde 'ts'.
    Sirve para detectar que una grabación del micrófono se SOLAPÓ con su voz."""
    return esta_hablando() or _ultimo_fin_habla >= ts


def ultimo_fin_habla() -> float:
    """time.time() en que LARA terminó de reproducir su última frase (0.0 si
    nunca habló). Para saber si una grabación empezó DURANTE su voz."""
    return _ultimo_fin_habla


def recortar_voz_de_lara(crudo: bytes, sr: int, t_voz: float):
    """
    Caso: la grabación EMPEZÓ mientras LARA hablaba por los parlantes (el VAD
    se disparó con su voz) y TÚ contestaste apenas terminó ella, antes de la
    pausa de corte — tu frase quedó PEGADA en la misma toma que la de ella y
    el texto mixto se descartaba entero ("le hablo al instante y no me oye").

    Si aplica, corta la parte de LARA y devuelve (crudo_tuyo, t_voz_nuevo,
    voz_dur_estimada, crudo_previo) — 'crudo_previo' es el tramo ANTERIOR al
    corte, para que el llamador verifique con voz_id que de verdad era la voz
    de LARA antes de aplicar el recorte (si eras TÚ hablando encima de ella,
    recortar destrozaría el inicio de tu frase). Devuelve None si no aplica
    (ella no hablaba, sigue hablando, o no quedó nada útil detrás).
    """
    fin = _ultimo_fin_habla
    if esta_hablando() or not fin:
        return None
    dur_total = len(crudo) / (2.0 * sr)
    # ¿La toma empezó claramente DURANTE su voz y siguió claramente DESPUÉS?
    if not (t_voz < fin - 0.2 and t_voz + dur_total > fin + 0.45):
        return None
    corte_s = (fin - t_voz) + 0.1   # +0.1: colita de reverbero de sus parlantes
    corte_b = 2 * int(corte_s * sr)
    resto = crudo[corte_b:]
    if len(resto) < 2 * int(0.35 * sr):
        return None  # no quedó nada con sustancia detrás de su voz
    try:
        # voz_dur real del recorte (mismo criterio de energía que el VAD)
        import numpy as np
        x = np.frombuffer(resto, dtype=np.int16).astype(np.float32)
        paso = int(0.03 * sr)
        niveles = [float(np.sqrt(np.mean(x[i:i+paso] ** 2) + 1e-9))
                   for i in range(0, x.size - paso, paso)]
        voz_dur = 0.03 * sum(1 for n in niveles if n > 140.0)
    except Exception:
        voz_dur = max(0.0, dur_total - corte_s - 0.4)
    return resto, fin + 0.1, voz_dur, crudo[:corte_b]


def sembrar_voz_lara() -> None:
    """
    Enseña a voz_id la voz de la PROPIA LARA sintetizando unas frases con el
    motor TTS actual (SIN reproducirlas). Es la semilla del anti-eco ACÚSTICO:
    con su huella enrolada, una captura del micrófono que se identifique como
    "LARA" es su eco por los parlantes (se ignora) y una que se identifique
    como una persona es una interrupción real (se atiende, sin palabra clave).
    El perfil luego se refina solo con los ecos reales confirmados por texto.
    No hace nada si el perfil ya está sembrado. Pensada para un hilo de fondo.
    """
    try:
        from core import voz_id
        if voz_id.voz_lara_lista():
            return
        import miniaudio
        frases = (
            "Hola, soy Lara, tu asistente del taller de electrónica.",
            "El murciélago añejo comía feliz cardillo y kiwi junto al río.",
            "¿Puedes encender la luz azul del laboratorio, por favor?",
            "Treinta y tres tazas trituradas ruedan por el taller de trabajo.",
        )
        añadidas = 0
        for f in frases:
            ruta = _sintetizar(f)
            if not ruta:
                continue
            try:
                dec = miniaudio.decode_file(
                    ruta, output_format=miniaudio.SampleFormat.SIGNED16,
                    nchannels=1, sample_rate=16000)
                crudo = dec.samples.tobytes() if hasattr(dec.samples, "tobytes") else bytes(dec.samples)
                r = voz_id.aprender_voz_lara(crudo, 16000)
                if "error" not in r:
                    añadidas += 1
            finally:
                try:
                    os.remove(ruta)
                except OSError:
                    pass
        if añadidas:
            print(f"[LARA] Huella de mi propia voz sembrada ({añadidas} muestras). "
                  "Anti-eco acústico activo.")
    except Exception:
        pass


def _normalizar_eco(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


def _registrar_frase_dicha(texto: str) -> None:
    _frases_dichas.append((time.time(), _normalizar_eco(texto)))
    del _frases_dichas[:-12]


def es_eco(texto: str) -> bool:
    """True si 'texto' es (parte de) algo que LARA dijo hace poco: el micrófono
    captó su propia voz por los parlantes y NO hay que tratarlo como pregunta."""
    import difflib
    ahora = time.time()
    t = _normalizar_eco(texto)
    if not t:
        return False
    recientes = [f for ts, f in _frases_dichas if ahora - ts <= _ECO_VENTANA and f]
    if not recientes:
        return False
    for f in recientes:
        if difflib.SequenceMatcher(None, t, f).ratio() >= 0.72:
            return True
        if len(t) >= 12 and (t in f or f in t):
            return True
    # Captura que abarca varias frases seguidas: ¿casi todas sus palabras
    # están en lo que LARA acaba de decir?
    palabras = t.split()
    if len(palabras) >= 3:
        dichas = set(" ".join(recientes).split())
        if sum(1 for p in palabras if p in dichas) / len(palabras) >= 0.8:
            return True
    return False


# ──────────────────────────────────────────────
#  SALIDA — Texto a voz (serializada)
# ──────────────────────────────────────────────

def _reproducir_archivo(ruta: str) -> bool:
    """Reproduce un audio en el dispositivo de salida elegido (vía skills_audio)."""
    try:
        from core.skills_audio import reproducir_audio
        return reproducir_audio(ruta)
    except Exception:
        return False


def _gen_edge(texto: str) -> str:
    """Sintetiza con edge-tts y devuelve la ruta del mp3, o None si falló.
    NO reproduce: la reproducción es del hilo de playback (tubería)."""
    try:
        import asyncio
        import edge_tts
        salida = os.path.join(_dir_tmp, f"tts_{int(time.time()*1000)}.mp3")

        async def _gen():
            await edge_tts.Communicate(texto, VOZ_TTS, rate=VELOCIDAD_TTS).save(salida)

        asyncio.run(_gen())
        return salida if os.path.getsize(salida) > 0 else None
    except Exception:
        return None


# ── Kokoro-82M — respaldo offline REAL (voz neuronal, sin internet) ──────────
# edge-tts necesita internet; pyttsx3 funciona sin internet pero suena
# robótico. Kokoro-82M (Apache 2.0, github.com/thewh1teagle/kokoro-onnx) es
# un modelo de 82M de parámetros pensado para correr rápido en CPU: llena el
# hueco de "offline pero que suene bien". Los pesos NO vienen en el paquete
# pip (son ~350 MB): hay que descargarlos aparte (ver LEEME.txt en
# modelos_voz/kokoro/). Si no están, esta función se auto-desactiva en
# silencio y la cadena de respaldo sigue con pyttsx3 exactamente como antes.
#
# Kokoro está entrenado sobre todo en inglés: el español usa el fallback de
# fonemización de espeak-ng (paquete espeakng-loader, sin instalar nada a
# nivel de sistema) y tiene pocas voces "es" (ef_dora, em_alex, em_santa) con
# calidad más limitada que la voz neuronal nativa de edge-tts. Por eso en el
# modo "auto" va DESPUÉS de edge-tts; con "usa la voz kokoro" (o LARA_TTS=
# kokoro) pasa a ser la voz PRINCIPAL: menos calidad, pero cero latencia de
# red y funciona igual sin internet.
_KOKORO_DIR = PROYECTO_DIR / "modelos_voz" / "kokoro"
_kokoro_motor = None
_kokoro_estado = None   # None=por probar, True=disponible, False=no disponible
_kokoro_lock = threading.Lock()


def _cargar_kokoro():
    global _kokoro_motor, _kokoro_estado
    if _kokoro_estado is False:
        return None
    if _kokoro_motor is not None:
        return _kokoro_motor
    with _kokoro_lock:
        if _kokoro_motor is not None:
            return _kokoro_motor
        try:
            from kokoro_onnx import Kokoro
        except Exception:
            _kokoro_estado = False
            return None
        modelo_path = os.environ.get("LARA_KOKORO_MODEL", str(_KOKORO_DIR / "kokoro-v1.0.onnx"))
        voces_path = os.environ.get("LARA_KOKORO_VOICES", str(_KOKORO_DIR / "voices-v1.0.bin"))
        if not (os.path.isfile(modelo_path) and os.path.isfile(voces_path)):
            print(f"[LARA] Kokoro TTS no encontrado en {_KOKORO_DIR} (respaldo offline "
                  "opcional). Descarga kokoro-v1.0.onnx y voices-v1.0.bin desde "
                  "github.com/thewh1teagle/kokoro-onnx/releases y colócalos ahí "
                  "-- ver modelos_voz/kokoro/LEEME.txt. Sin ellos, LARA sigue "
                  "funcionando igual (edge-tts + pyttsx3).")
            _kokoro_estado = False
            return None
        try:
            # Español necesita espeak-ng para fonemizar (Kokoro es ante todo
            # un modelo inglés). espeakng_loader trae el binario sin pedirte
            # instalarlo en el sistema. Si falta, Kokoro puede fallar solo con
            # voces no-inglesas -> se captura abajo y se cae a pyttsx3.
            try:
                import espeakng_loader
                from phonemizer.backend.espeak.wrapper import EspeakWrapper
                EspeakWrapper.set_library(espeakng_loader.get_library_path())
            except Exception:
                pass
            _kokoro_motor = Kokoro(modelo_path, voces_path)
            _kokoro_estado = True
            print("[LARA] Kokoro TTS local cargado. Respaldo de voz offline real activo.")
            return _kokoro_motor
        except Exception as e:
            print(f"[LARA] No pude cargar Kokoro TTS ({e}).")
            _kokoro_estado = False
            return None


def _gen_kokoro(texto: str) -> str:
    """Sintetiza con Kokoro local y devuelve la ruta del wav, o None si falló."""
    motor = _cargar_kokoro()
    if motor is None:
        return None
    try:
        voz = os.environ.get("LARA_KOKORO_VOZ", "ef_dora")
        idioma = os.environ.get("LARA_KOKORO_LANG", "es")
        muestras, sr = motor.create(texto, voice=voz, speed=1.0, lang=idioma)

        import numpy as np
        import wave
        pcm = np.clip(np.asarray(muestras, dtype=np.float32), -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype(np.int16)
        salida = os.path.join(_dir_tmp, f"kokoro_{int(time.time()*1000)}.wav")
        with wave.open(salida, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes(pcm16.tobytes())
        return salida
    except Exception as e:
        print(f"[LARA] Error en Kokoro TTS: {e}")
        return None


# ── Motor de voz PREFERIDO ────────────────────────────────────────────────────
# "auto"/"edge": edge-tts primero (nube, voz Camila) y Kokoro de respaldo.
# "kokoro":     Kokoro LOCAL primero (offline, sin latencia de red) y edge-tts
#               de respaldo. Se cambia por voz ("usa la voz kokoro" / "usa la
#               voz de internet"), persiste en audio_config.json y puede
#               forzarse con la variable de entorno LARA_TTS.
_MOTORES_VALIDOS = ("auto", "edge", "kokoro")
_motor_tts = None  # None = aún no leído de la config


def get_motor_tts() -> str:
    global _motor_tts
    if _motor_tts is None:
        m = os.environ.get("LARA_TTS", "").strip().lower()
        if m not in _MOTORES_VALIDOS:
            try:
                from core import skills_audio
                m = str(skills_audio._cargar_config().get("motor_tts", "auto")).lower()
            except Exception:
                m = "auto"
        _motor_tts = m if m in _MOTORES_VALIDOS else "auto"
    return _motor_tts


def set_motor_tts(nombre: str) -> str:
    """Fija el motor de voz preferido ('auto'|'edge'|'kokoro') y lo persiste.
    Si eliges kokoro, precarga el modelo en segundo plano para que la primera
    frase no pague la carga (~varios segundos)."""
    global _motor_tts
    nombre = (nombre or "").strip().lower()
    if nombre not in _MOTORES_VALIDOS:
        return get_motor_tts()
    _motor_tts = nombre
    try:
        from core import skills_audio
        cfg = skills_audio._cargar_config()
        cfg["motor_tts"] = nombre
        skills_audio._guardar_config(cfg)
    except Exception:
        pass
    if nombre == "kokoro":
        precargar_tts()
    return _motor_tts


def precargar_tts() -> None:
    """Carga Kokoro en RAM en segundo plano si es el motor preferido (o quieres
    tenerlo listo como respaldo sin pagar la carga en la primera frase)."""
    if get_motor_tts() != "kokoro":
        return
    threading.Thread(target=_cargar_kokoro, daemon=True,
                     name="lara-precargar-kokoro").start()


def _sintetizar(texto: str) -> str:
    """Genera el audio de 'texto' con la cadena del motor preferido. Devuelve la
    ruta del archivo, o None (-> el playback caerá a pyttsx3 como siempre)."""
    if get_motor_tts() == "kokoro":
        cadena = (_gen_kokoro, _gen_edge)
    else:
        cadena = (_gen_edge, _gen_kokoro)
    for gen in cadena:
        ruta = gen(texto)
        if ruta:
            return ruta
    return None


def _tts_offline(texto: str) -> bool:
    try:
        import pyttsx3
        motor = pyttsx3.init()
        motor.setProperty("rate", 180)
        for v in motor.getProperty("voices"):
            if any(s in v.name.lower() for s in ("spanish", "español", "helena", "sabina")):
                motor.setProperty("voice", v.id)
                break
        motor.say(texto)
        motor.runAndWait()
        return True
    except Exception:
        return False


def _limpiar_para_voz(texto: str) -> str:
    texto = re.sub(r"https?://\S+", "el enlace", texto)
    texto = re.sub(r"[*_`#>]+", "", texto)
    texto = re.sub(r"[\U0001F000-\U0001FAFF☀-➿◢◣◤◥▸◆▪●]", "", texto)
    return texto.strip()


def _bucle_sintesis():
    """Etapa 1 de la tubería: convierte texto en archivos de audio y los pasa a
    la cola de reproducción. Corre en paralelo con el playback: mientras suena
    la frase N, aquí ya se genera la N+1 (cero silencio entre frases)."""
    while True:
        texto, on_done, on_start = _cola.get()
        gen = _gen_voz
        try:
            limpio = _limpiar_para_voz(texto) if (VOZ_ACTIVA and texto) else ""
            ruta = _sintetizar(limpio) if limpio else None
            if limpio or on_done or on_start:
                # ruta None con texto = ambos motores fallaron -> el playback
                # usará pyttsx3 (nunca quedarse muda). put() bloquea si ya hay
                # 2 audios en espera (backpressure: no sintetizar de más).
                _cola_audio.put((gen, limpio, ruta, on_done, on_start))
        except Exception:
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
        finally:
            _cola.task_done()


def _bucle_playback():
    """Etapa 2: reproduce los audios EN ORDEN, uno a la vez (nunca se solapan).
    on_start se dispara aquí, justo antes de que el audio suene de verdad."""
    global _hablando, _ultimo_fin_habla
    while True:
        gen, limpio, ruta, on_done, on_start = _cola_audio.get()
        if gen != _gen_voz:
            limpio = ""  # sintetizado antes de un detener_voz(): no reproducir
        if on_start:
            try:
                on_start()
            except Exception:
                pass
        try:
            if limpio and VOZ_ACTIVA:
                _hablando = True
                _registrar_frase_dicha(limpio)  # memoria anti-eco
                if ruta:
                    _reproducir_archivo(ruta)
                else:
                    _tts_offline(limpio)  # último recurso: robótico pero habla
        except Exception:
            pass
        finally:
            if ruta:
                try:
                    os.remove(ruta)
                except OSError:
                    pass
            _hablando = False
            _ultimo_fin_habla = time.time()
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
            _cola_audio.task_done()


def _asegurar_worker():
    global _worker, _worker_play
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_bucle_sintesis, daemon=True)
            _worker.start()
        if _worker_play is None or not _worker_play.is_alive():
            _worker_play = threading.Thread(target=_bucle_playback, daemon=True)
            _worker_play.start()


def _trocear(texto: str):
    """Divide un texto largo en frases para reproducir más fluido (arranca antes)."""
    import re
    partes = re.split(r"(?<=[\.\?\!\:\;\n])\s+", texto.strip())
    chunks, actual = [], ""
    for p in partes:
        if len(actual) + len(p) < 180:
            actual = (actual + " " + p).strip()
        else:
            if actual:
                chunks.append(actual)
            actual = p
    if actual:
        chunks.append(actual)
    return chunks or [texto]


def hablar(texto: str, on_done=None, on_start=None, bloquear: bool = False) -> None:
    """
    Encola texto para que LARA lo diga. Se trocea por frases y se reproduce de
    forma secuencial (nunca se solapa). 'on_done' se invoca al terminar TODO.
    'on_start' se invoca justo cuando el PRIMER trozo empieza a sonar de
    verdad (tras sintetizarlo) — úsalo para sincronizar algo visual con la voz.
    """
    if not VOZ_ACTIVA or not texto or not texto.strip():
        if on_start:
            on_start()
        if on_done:
            on_done()
        return
    _asegurar_worker()
    chunks = _trocear(texto)

    if bloquear:
        evento = threading.Event()

        def _fin():
            if on_done:
                on_done()
            evento.set()
        for i, ch in enumerate(chunks):
            _cola.put((ch,
                       _fin if i == len(chunks) - 1 else None,
                       on_start if i == 0 else None))
        evento.wait(timeout=120)
    else:
        for i, ch in enumerate(chunks):
            _cola.put((ch,
                       on_done if i == len(chunks) - 1 else None,
                       on_start if i == 0 else None))


def hablar_stream(iterador_texto, on_chunk=None) -> str:
    """
    Habla MIENTRAS el texto se genera (Mejora de latencia tipo JARVIS).

    'iterador_texto' es un generador que va soltando trozos de texto (tokens o
    fragmentos) según los produce el modelo. En cuanto se acumula una frase
    completa, se encola para hablar — así Lara empieza a responder a los ~1-2 s
    en vez de esperar a que el modelo termine TODA la respuesta.

    'on_chunk(texto_frase)' (opcional) se llama con cada frase lista, para que la
    GUI la vaya mostrando en pantalla a la vez que suena.

    Devuelve el texto completo dicho (para guardarlo en el historial).
    """
    _asegurar_worker()
    completo = []
    buffer = ""
    _fin_frase = re.compile(r"(?<=[\.\?\!\:\;\n])\s+")

    def _emitir(frase: str):
        frase = frase.strip()
        if not frase:
            return
        completo.append(frase)
        if on_chunk:
            try:
                on_chunk(frase)
            except Exception:
                pass
        if VOZ_ACTIVA:
            _cola.put((frase, None, None))

    try:
        for trozo in iterador_texto:
            if not trozo:
                continue
            buffer += trozo
            # Emitir todas las frases completas que ya haya en el buffer.
            while True:
                m = _fin_frase.search(buffer)
                # Solo cortamos si la frase tiene cuerpo suficiente (evita trocear
                # "1." o "Dr." como si fueran fin de frase).
                if m and m.start() >= 12:
                    _emitir(buffer[:m.start() + 1])
                    buffer = buffer[m.end():]
                else:
                    break
    except Exception:
        pass
    if buffer.strip():
        _emitir(buffer)
    return " ".join(completo).strip()


def detener_voz() -> None:
    """Vacía las colas (texto y audio ya sintetizado) y corta la reproducción."""
    global _gen_voz
    _gen_voz += 1  # invalida lo que la síntesis tenga a medio encolar
    try:
        while True:
            _cola.get_nowait()
            _cola.task_done()
    except queue.Empty:
        pass
    try:
        while True:
            _, _, ruta, _, _ = _cola_audio.get_nowait()
            if ruta:
                try:
                    os.remove(ruta)
                except OSError:
                    pass
            _cola_audio.task_done()
    except queue.Empty:
        pass
    try:
        from core.skills_audio import detener_salida
        detener_salida()
    except Exception:
        pass


# ──────────────────────────────────────────────
#  ENTRADA — Voz a texto (con VAD)
# ──────────────────────────────────────────────

_whisper_estado = None  # None=por probar, True=disponible, False=bloqueado

# Frases que Whisper "alucina" cuando recibe silencio o ruido (típicas del
# entrenamiento con subtítulos). Se descartan SIEMPRE.
_ALUCINACIONES = (
    "subtítulos realizados por", "subtitulos realizados por", "amara.org",
    "gracias por ver el video", "gracias por ver el vídeo", "suscríbete",
    "suscribete", "hasta la próxima", "www.", "cc por", "subtítulos por",
)
# Muletillas de 1-2 sílabas que Whisper inventa sobre ruido corto. Solo se
# descartan cuando la voz real detectada fue MUY breve (no bloquea un "sí" real).
_MULETILLAS = {"y", "eh", "mm", "ah", "uh", "qué", "que", "¿qué?", "sí", "si", "no", "ya",
               "a", "o", "e", "gracias", "gracias por ver", "adiós", "adios", "chau", "ok"}


def _transcribir_whisper(crudo: bytes, sr_rate: int):
    """Transcribe con Whisper de Groq filtrando segmentos de baja confianza
    (anti-alucinación). None si el modelo no está disponible."""
    global _whisper_estado
    if _whisper_estado is False:
        return None
    try:
        import io, wave
        from core.llm_engine import client as groq_client
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr_rate); w.writeframes(crudo)
        buf.seek(0)
        tr = groq_client.audio.transcriptions.create(
            file=("a.wav", buf.read()), model="whisper-large-v3-turbo",
            language="es", response_format="verbose_json", temperature=0.0)
        _whisper_estado = True

        # Filtrar segmentos donde el propio Whisper duda (silencio/ruido)
        segmentos = getattr(tr, "segments", None) or []
        if segmentos:
            partes = []
            for s in segmentos:
                g = (lambda k, d: s.get(k, d)) if isinstance(s, dict) else (lambda k, d: getattr(s, k, d))
                if g("no_speech_prob", 0.0) > 0.5:   # Whisper cree que NO hay voz
                    continue
                if g("avg_logprob", 0.0) < -0.75:    # transcripción muy insegura
                    continue
                partes.append(str(g("text", "")).strip())
            return " ".join(p for p in partes if p).strip()
        return (getattr(tr, "text", "") or "").strip()
    except Exception as e:
        s = str(e).lower()
        if "permission" in s or "403" in s or "blocked" in s or "decommission" in s:
            _whisper_estado = False  # no volver a intentar esta sesión
        return None


def _parece_voz_humana(crudo: bytes, sr_rate: int = 16000) -> bool:
    """
    Análisis físico de la señal: ¿esto es voz o estática/zumbido/ruido?
    Un micrófono desconectado o un canal muerto produce ruido que puede pasar
    el VAD por energía, pero NO tiene la firma de la voz humana:
      1. MODULACIÓN: la voz sube y baja sílaba a sílaba (RMS por tramos varía);
         la estática es plana.
      2. ESPECTRO: la energía de la voz se concentra en 85–4000 Hz; el ruido
         blanco se reparte hasta 8 kHz y el zumbido eléctrico vive bajo 85 Hz.
    """
    try:
        import numpy as np
        x = np.frombuffer(crudo, dtype=np.int16).astype(np.float32)
        if x.size < sr_rate // 4:
            return False

        # 1) Modulación de amplitud (tramos de 30 ms)
        paso = int(0.03 * sr_rate)
        nivel = np.array([np.sqrt(np.mean(x[i:i+paso] ** 2) + 1e-9)
                          for i in range(0, x.size - paso, paso)])
        cv = float(nivel.std() / (nivel.mean() + 1e-9))
        if cv < 0.25:          # señal plana -> estática/zumbido, no voz
            return False

        # 2) Concentración espectral en banda de voz
        esp = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
        freqs = np.fft.rfftfreq(x.size, 1.0 / sr_rate)
        total = float(esp.sum()) + 1e-9
        banda_voz = float(esp[(freqs >= 85) & (freqs <= 4000)].sum())
        if banda_voz / total < 0.55:   # energía dispersa -> ruido
            return False

        return True
    except Exception:
        return True  # ante la duda, no bloquear (el resto de filtros siguen)


def _es_alucinacion(texto: str, voz_dur: float) -> bool:
    """True si el texto es una alucinación típica de STT sobre ruido/silencio."""
    t = texto.lower().strip()
    if not t:
        return True
    if any(a in t for a in _ALUCINACIONES):
        return True
    # Texto corto con casi nada de voz real detectada -> ruido interpretado.
    # 0.22 s (antes 0.45): un "sí"/"no"/"ya" REAL dicho a ritmo normal suma
    # ~0.25-0.4 s de bloques con energía y se estaba descartando — obligaba a
    # repetirlo alargando la palabra. Contra las alucinaciones sobre ruido ya
    # hay tres defensas antes que esta: voz sostenida en el VAD (3 bloques),
    # firma física de voz humana y el filtro de confianza del propio Whisper;
    # esta ventana queda solo para chasquidos que se cuelen entre 0.20 y 0.22.
    if voz_dur < 0.22 and (t.strip("¿?¡!., ") in _MULETILLAS or len(t) <= 3):
        return True
    return False


def escuchar(espera_inicio: float = 8.0, fin_silencio: float = None,
             max_frase: float = None) -> dict:
    """
    Escucha una frase completa: espera a que hables (capta hasta susurros) y
    corta tras tu pausa. Valida que hubo voz real (ignora golpes/ruido) y
    filtra alucinaciones del reconocedor.
    Devuelve {"texto","audio","sr","t_voz"} o {"error": "..."}. 't_voz' es el
    instante (time.time()) en que empezó la voz grabada: sirve para el anti-eco.
    Si fin_silencio/max_frase son None usa los ajustes globales VAD_* (así se
    pueden tunear en un solo sitio y a mitad de sesión).
    """
    if fin_silencio is None:
        fin_silencio = VAD_FIN_SILENCIO
    if max_frase is None:
        max_frase = VAD_MAX_FRASE
    try:
        from core.skills_audio import grabar_vad
        crudo, sr_rate, voz_dur, t_voz, texto_previo = grabar_vad(
            espera_inicio, fin_silencio, max_frase)
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"No pude usar el micrófono: {e}"}

    return transcribir_crudo(crudo, sr_rate, voz_dur, t_voz, texto_previo)


def transcribir_crudo(crudo, sr_rate, voz_dur, t_voz, texto_previo: str = None) -> dict:
    """Transcribe audio PCM ya grabado (Whisper local -> nube -> Google) y filtra
    ruido/alucinaciones. Devuelve {"texto","audio","sr","t_voz"} o {"error":...}.
    Se separó de escuchar() para poder usarla desde la CAPTURA CONTINUA (grabar
    y transcribir en hilos distintos, sin perder voz mientras se transcribe).
    'texto_previo': transcripción que la compuerta semántica de grabar_vad ya
    hizo durante la pausa final. Si viene, se usa DIRECTO (cero latencia aquí)
    en vez de volver a pasar el audio por Whisper."""
    # Golpe/clic/ruido: hubo disparo pero casi nada de voz sostenida -> ignorar.
    # 0.20 s (antes 0.30): con el disparo del VAD ya se exigen 3 bloques de voz
    # sostenida; este segundo umbral se comía los monosílabos reales ("sí",
    # "no", "ok") dichos rápido y obligaba a repetirlos alargándolos.
    if voz_dur < 0.20:
        return {"error": "Solo detecté un ruido, no voz."}

    # Firma física de voz humana (bloquea estática de micrófonos desconectados,
    # zumbidos y ruido constante ANTES de gastar una transcripción)
    if not _parece_voz_humana(crudo, sr_rate):
        return {"error": "Eso no suena a voz (¿micrófono desconectado o ruido?)."}

    # -1) Transcripción YA HECHA por la compuerta de turno: reutilizarla.
    if texto_previo is not None:
        texto = texto_previo.strip()
        if not texto or _es_alucinacion(texto, voz_dur):
            return {"error": "No entendí lo que dijiste (ruido o muy corto)."}
        return {"texto": texto, "audio": crudo, "sr": sr_rate, "t_voz": t_voz}

    # 0) Whisper LOCAL (offline, sin internet, menos latencia). Si no está
    #    instalado/cargado devuelve None y seguimos con Whisper en la nube.
    try:
        from core import stt_local
        texto = stt_local.transcribir(crudo, sr_rate)
    except Exception:
        texto = None
    if texto is not None:
        if not texto or _es_alucinacion(texto, voz_dur):
            return {"error": "No entendí lo que dijiste (ruido o muy corto)."}
        return {"texto": texto, "audio": crudo, "sr": sr_rate, "t_voz": t_voz}

    # 1) Whisper en la nube (Groq) con filtro de confianza
    texto = _transcribir_whisper(crudo, sr_rate)
    if texto is not None:
        if not texto or _es_alucinacion(texto, voz_dur):
            return {"error": "No entendí lo que dijiste (ruido o muy corto)."}
        return {"texto": texto, "audio": crudo, "sr": sr_rate, "t_voz": t_voz}

    # 2) Respaldo: reconocedor gratuito de Google
    try:
        import speech_recognition as sr
        recon = sr.Recognizer()
        audio = sr.AudioData(crudo, sr_rate, 2)
        texto = recon.recognize_google(audio, language=IDIOMA_STT)
        texto = (texto or "").strip()
        if not texto or _es_alucinacion(texto, voz_dur):
            return {"error": "No entendí lo que dijiste (ruido o muy corto)."}
        return {"texto": texto, "audio": crudo, "sr": sr_rate, "t_voz": t_voz}
    except sr.UnknownValueError:
        return {"error": "No entendí lo que dijiste (silencio o ruido)."}
    except sr.RequestError as e:
        return {"error": f"Sin conexión con el servicio de reconocimiento: {e}"}
    except Exception as e:
        return {"error": str(e)}


def escucha_continua(callback, parar_event: threading.Event,
                     espera_inicio: float = 6.0) -> None:
    """
    Bucle de escucha manos libres. Espera frases con VAD y por cada una llama
    callback(texto). Se detiene cuando parar_event.set() es invocado.
    """
    while not parar_event.is_set():
        res = escuchar(espera_inicio=espera_inicio, fin_silencio=1.0, max_frase=15.0)
        if parar_event.is_set():
            break
        if "texto" in res and res["texto"]:
            try:
                callback(res["texto"])
            except Exception:
                pass
        else:
            time.sleep(0.2)


def cambiar_voz(nombre_voz: str) -> None:
    global VOZ_TTS
    VOZ_TTS = nombre_voz


def set_voz_activa(activa: bool) -> None:
    global VOZ_ACTIVA
    VOZ_ACTIVA = bool(activa)
    if not activa:
        detener_voz()


def set_velocidad(delta_pct: int = None, absoluto: int = None) -> str:
    """
    Ajusta la velocidad del habla. 'delta_pct' suma/resta al valor actual
    (ej: +15 más rápido, -15 más lento); 'absoluto' la fija (0 = normal).
    Se limita a [-40, +60] % para que siga siendo inteligible. Devuelve el
    valor textual aplicado (ej: "+15%").
    """
    global _velocidad_pct, VELOCIDAD_TTS
    if absoluto is not None:
        _velocidad_pct = int(absoluto)
    elif delta_pct is not None:
        _velocidad_pct += int(delta_pct)
    _velocidad_pct = max(-40, min(60, _velocidad_pct))
    VELOCIDAD_TTS = f"{'+' if _velocidad_pct >= 0 else ''}{_velocidad_pct}%"
    return VELOCIDAD_TTS
