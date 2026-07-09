"""
skills_voz.py — Voz de LARA (entrada y salida de audio).

SALIDA (TTS): edge-tts genera voz neuronal en español (gratis, alta calidad).
La reproducción está SERIALIZADA en un único hilo trabajador con cola: cada
frase se genera y se reproduce COMPLETA antes de la siguiente, así nunca se
corta ni se solapa (bug anterior: el audio se cortaba tras una palabra porque
varios hilos competían y se pisaban). Si edge-tts falla (sin internet), cae a
pyttsx3 offline.

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
#   VAD_FIN_SILENCIO: segundos de silencio tras hablar antes de que corte y
#     procese. Súbelo si te corta a mitad de frase (haces pausas); bájalo si
#     sientes que tarda en arrancar tras terminar de hablar.
#   VAD_MAX_FRASE: tope de segundos de una sola intervención (para frases largas
#     no se corta a los 15 s como antes).
VAD_FIN_SILENCIO = 1.5
VAD_MAX_FRASE = 22.0

# Cola y trabajador único de reproducción
_cola = queue.Queue()
_worker = None
_worker_lock = threading.Lock()
_hablando = False        # True mientras LARA está reproduciendo voz
_ultimo_fin_habla = 0.0  # time.time() en que terminó la última frase reproducida
_frases_dichas = []      # [(time.time(), texto normalizado)] de lo último que dijo LARA
# Ventana anti-eco: antes 20 s era demasiado — durante 20 s cualquier cosa que
# dijeras parecida a lo que LARA dijo se descartaba, y a veces tragaba tu voz
# real (por eso "tenías que hablar dos veces"). 8 s cubre el eco sin comerse
# tus respuestas.
_ECO_VENTANA = 8.0


def esta_hablando() -> bool:
    return _hablando or not _cola.empty()


def hablo_desde(ts: float) -> bool:
    """True si LARA está hablando ahora o habló en algún momento desde 'ts'.
    Sirve para detectar que una grabación del micrófono se SOLAPÓ con su voz."""
    return esta_hablando() or _ultimo_fin_habla >= ts


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


def _tts_edge(texto: str) -> bool:
    try:
        import asyncio
        import edge_tts
        salida = os.path.join(_dir_tmp, f"tts_{int(time.time()*1000)}.mp3")

        async def _gen():
            await edge_tts.Communicate(texto, VOZ_TTS, rate=VELOCIDAD_TTS).save(salida)

        asyncio.run(_gen())
        ok = _reproducir_archivo(salida)
        try:
            os.remove(salida)
        except OSError:
            pass
        return ok
    except Exception:
        return False


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


def _bucle_worker():
    global _hablando, _ultimo_fin_habla
    while True:
        texto, on_done, on_start = _cola.get()
        # on_start se dispara AQUÍ, justo antes de sintetizar/reproducir: es el
        # momento real en que la voz "empieza" (hablar() solo la encoló, y
        # edge-tts tarda en sintetizar; quien sincroniza una animación con la
        # voz necesita saber cuándo el audio va a sonar de verdad, no cuándo
        # se pidió). Se dispara siempre, exactamente una vez por llamada.
        if on_start:
            try:
                on_start()
            except Exception:
                pass
        try:
            if VOZ_ACTIVA and texto:
                limpio = _limpiar_para_voz(texto)
                if limpio:
                    _hablando = True
                    _registrar_frase_dicha(limpio)  # memoria anti-eco
                    if not _tts_edge(limpio):
                        _tts_offline(limpio)
        except Exception:
            pass
        finally:
            _hablando = False
            _ultimo_fin_habla = time.time()
            if on_done:
                try:
                    on_done()
                except Exception:
                    pass
            _cola.task_done()


def _asegurar_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_bucle_worker, daemon=True)
            _worker.start()


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
    """Vacía la cola y corta cualquier reproducción en curso."""
    try:
        while True:
            _cola.get_nowait()
            _cola.task_done()
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
    # Texto corto con casi nada de voz real detectada -> ruido interpretado
    if voz_dur < 0.45 and (t.strip("¿?¡!., ") in _MULETILLAS or len(t) <= 3):
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
        crudo, sr_rate, voz_dur, t_voz = grabar_vad(espera_inicio, fin_silencio, max_frase)
    except RuntimeError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"No pude usar el micrófono: {e}"}

    # Golpe/clic/ruido: hubo disparo pero casi nada de voz sostenida -> ignorar
    if voz_dur < 0.30:
        return {"error": "Solo detecté un ruido, no voz."}

    # Firma física de voz humana (bloquea estática de micrófonos desconectados,
    # zumbidos y ruido constante ANTES de gastar una transcripción)
    if not _parece_voz_humana(crudo, sr_rate):
        return {"error": "Eso no suena a voz (¿micrófono desconectado o ruido?)."}

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
