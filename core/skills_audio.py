"""
skills_audio.py — Gestión de dispositivos de audio de LARA.

Centraliza entrada (micrófono) y salida (parlantes/auriculares) para que se
puedan ELEGIR desde la propia LARA y la selección persista entre sesiones.

- Entrada: graba con sounddevice usando el micrófono elegido. Soluciona el error
  intermitente "Error querying device -1" reinicializando PortAudio y reintentando
  (antes obligaba a reiniciar el programa).
- Salida: decodifica el mp3 de la voz con miniaudio y lo reproduce con sounddevice
  en el dispositivo de salida elegido. Si algo falla, cae a MCI (salida por defecto
  de Windows) para no quedarse muda nunca.

La configuración se guarda en KNOWLEDGE_DIR/audio_config.json (sincronizado por
OneDrive como el resto), pero los índices de dispositivo se validan en cada PC.
"""

import os
import json
import time
import ctypes
import threading
from pathlib import Path

from config import KNOWLEDGE_DIR

CONFIG_PATH = KNOWLEDGE_DIR / "audio_config.json"
_lock = threading.Lock()
_mic_lock = threading.Lock()  # un solo uso del micrófono a la vez


# ──────────────────────────────────────────────
#  Configuración persistente
# ──────────────────────────────────────────────

def _cargar_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _guardar_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_input_device():
    return _cargar_config().get("input_device")


def get_output_device():
    return _cargar_config().get("output_device")


def set_input_device(idx) -> None:
    cfg = _cargar_config()
    cfg["input_device"] = idx
    _guardar_config(cfg)


def set_output_device(idx) -> None:
    cfg = _cargar_config()
    cfg["output_device"] = idx
    _guardar_config(cfg)


# ──────────────────────────────────────────────
#  Listado de dispositivos
# ──────────────────────────────────────────────

def _reiniciar_portaudio():
    """Reinicia el motor PortAudio: cura el 'Error querying device -1' sin reiniciar LARA."""
    try:
        import sounddevice as sd
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def listar_dispositivos() -> dict:
    """Devuelve {'entrada': [(idx, nombre)], 'salida': [(idx, nombre)]} sin duplicar nombres."""
    try:
        import sounddevice as sd
    except Exception as e:
        return {"error": f"sounddevice no disponible: {e}", "entrada": [], "salida": []}

    entrada, salida = [], []
    vistos_in, vistos_out = set(), set()
    try:
        for i, d in enumerate(sd.query_devices()):
            nombre = d["name"].strip()
            if d["max_input_channels"] > 0 and nombre.lower() not in vistos_in:
                entrada.append((i, nombre))
                vistos_in.add(nombre.lower())
            if d["max_output_channels"] > 0 and nombre.lower() not in vistos_out:
                salida.append((i, nombre))
                vistos_out.add(nombre.lower())
    except Exception as e:
        return {"error": str(e), "entrada": entrada, "salida": salida}
    return {"entrada": entrada, "salida": salida}


def nombre_dispositivo(idx) -> str:
    if idx is None:
        return "predeterminado del sistema"
    try:
        import sounddevice as sd
        return sd.query_devices(idx)["name"].strip()
    except Exception:
        return f"dispositivo {idx}"


# ──────────────────────────────────────────────
#  Entrada — grabación robusta
# ──────────────────────────────────────────────

def grabar(segundos: float, sample_rate: int = 16000):
    """Graba del micrófono elegido. Reintenta reinicializando PortAudio si falla."""
    import sounddevice as sd
    import numpy as np

    device = get_input_device()
    intentos = 0
    while True:
        try:
            grabacion = sd.rec(int(segundos * sample_rate), samplerate=sample_rate,
                               channels=1, dtype="int16", device=device)
            sd.wait()
            return grabacion.astype(np.int16).tobytes(), sample_rate
        except Exception as e:
            intentos += 1
            if intentos == 1:
                # Causa típica del "device -1": PortAudio con estado viejo
                _reiniciar_portaudio()
                continue
            if intentos == 2 and device is not None:
                # El dispositivo guardado ya no existe en esta PC -> probar el por defecto
                device = None
                _reiniciar_portaudio()
                continue
            raise e


def grabar_vad(espera_inicio: float = 8.0, fin_silencio: float = 1.0,
               max_frase: float = 15.0, sample_rate: int = 16000):
    """
    Graba una frase completa con detección de voz por energía (VAD):
      1. Espera hasta 'espera_inicio' s a que empieces a hablar.
      2. Graba mientras hablas y para tras 'fin_silencio' s de silencio,
         o al llegar a 'max_frase' s.
    Antes de cortar por silencio, una COMPUERTA SEMÁNTICA (core/turno_habla.py)
    evalúa si tu idea ya está completa; si no, extiende la grabación en vez de
    cortarte a media frase por una pausa para pensar (ver _grabar_vad_impl).
    Devuelve (bytes_int16, sample_rate, voz_dur, t_inicio_voz, texto_parcial).
    't_inicio_voz' es el time.time() en que empezó la voz: sirve para saber si
    LARA estaba hablando en ese momento (anti-eco). 'texto_parcial' es la
    transcripción que la compuerta semántica YA HIZO durante la pausa final (o
    None): si es válida, el llamador se ahorra transcribir el audio OTRA VEZ
    (antes cada frase pagaba Whisper dos veces: una para evaluar el turno y
    otra en transcribir_crudo). Lanza RuntimeError si no detecta voz.
    """
    with _mic_lock:
        return _grabar_vad_impl(espera_inicio, fin_silencio, max_frase, sample_rate)


# Calibración de ruido de fondo cacheada entre llamadas. En escucha continua
# grabar_vad() se llama una vez por frase; recalibrar CADA vez deja ~0.3 s
# "ciegos" en los que, si ya estabas hablando, tu voz se mide como ruido y
# sube el umbral -> la frase se pierde y tienes que repetir. Reutilizar la
# última calibración (mismo dispositivo, reciente) evita esa ventana.
_calibracion = {"base": None, "device": "__sin_calibrar__", "ts": 0.0}
_CALIBRACION_VIGENCIA = 45.0  # s


def _calibrar_ambiente(stream, bloque, rms, device):
    ahora = time.time()
    if (_calibracion["base"] is not None and _calibracion["device"] == device
            and ahora - _calibracion["ts"] < _CALIBRACION_VIGENCIA):
        return _calibracion["base"]
    ambiente = []
    for _ in range(10):
        data, _ = stream.read(bloque)
        ambiente.append(rms(bytes(data)))
    base = sorted(ambiente)[len(ambiente) // 2]  # mediana
    _calibracion["base"] = base
    _calibracion["device"] = device
    _calibracion["ts"] = ahora
    return base


# Margen extra de silencio que se concede cuando la compuerta semántica dice
# INCOMPLETO (o todavía no contestó): tope duro para que, si el evaluador
# falla o el hablante se calla del todo, la grabación no se quede colgada.
_EXTENSION_MAX = float(os.environ.get("LARA_TURNO_EXTENSION", "1.8"))


def _grabar_vad_impl(espera_inicio, fin_silencio, max_frase, sample_rate):
    import sounddevice as sd
    import numpy as np
    import threading
    from core import stt_local, turno_habla

    device = get_input_device()
    bloque = int(0.03 * sample_rate)  # 30 ms por bloque

    def _abrir_stream():
        return sd.InputStream(samplerate=sample_rate, channels=1, dtype="int16",
                              blocksize=bloque, device=device)

    try:
        stream = _abrir_stream()
        stream.start()
    except Exception:
        _reiniciar_portaudio()
        stream = _abrir_stream()
        stream.start()

    def rms(frame):
        x = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
        return float(np.sqrt(np.mean(x * x)) + 1e-6) if x.size else 0.0

    try:
        base = _calibrar_ambiente(stream, bloque, rms, device)
        # Umbral SENSIBLE (capta susurros: 1.6x el ruido, mínimo 140).
        # La protección contra golpes/ruido no está en el umbral sino en exigir
        # VOZ SOSTENIDA: 3 bloques seguidos (~90 ms) para considerar que hablas.
        # Un golpe o clic es un pico de 1-2 bloques y no dispara la grabación.
        # Piso 140 (antes 200): con micrófonos de poca ganancia o hablando
        # suave, muchos bloques de voz REAL quedaban bajo 200 — la grabación
        # tardaba en dispararse y 'voz_dur' salía tan corta que la frase se
        # descartaba como ruido ("tengo que repetir varias veces"). Ajustable
        # con LARA_VAD_PISO si tu ambiente es muy ruidoso.
        umbral = max(base * 1.6, float(os.environ.get("LARA_VAD_PISO", "140")))

        frames = []
        pre_roll = []          # último medio segundo antes de detectar voz (no corta el arranque)
        hablando = False
        t_inicio_voz = None    # instante en que empezó la voz (para anti-eco)
        racha_voz = 0
        silencio_acum = 0.0
        espera_acum = 0.0
        dur = 0.0
        bloques_con_voz = 0    # cuánta VOZ real hubo (para validar después)

        # ── Compuerta semántica de turno (streaming STT + evaluador) ──────
        # Al llegar al silencio base NO se corta directo: se transcribe lo
        # grabado hasta ahora y turno_habla.evaluar_turno() decide si la idea
        # está completa. Corre en un hilo aparte para no bloquear la captura
        # del micrófono mientras se evalúa.
        gate = turno_habla.ACTIVO
        evaluando = {"activo": False, "resultado": None, "gen": 0, "texto": None}
        # La evaluación arranca ANTES de llegar a fin_silencio (a ~la mitad de
        # la pausa): así, cuando el silencio sí alcanza el umbral de corte, el
        # veredicto (y la transcripción) suelen estar YA listos y el corte es
        # inmediato, en vez de sumar el tiempo de Whisper DESPUÉS de la pausa.
        umbral_eval = min(0.33, fin_silencio * 0.55)

        def _evaluar_en_fondo(snapshot: bytes, gen: int):
            try:
                texto = stt_local.transcribir(snapshot, sample_rate)
            except Exception:
                texto = None
            # texto None = Whisper local no disponible: sin señal semántica,
            # comportarse como el VAD clásico (cortar).
            veredicto = True if texto is None else turno_habla.evaluar_turno(texto)
            if evaluando["gen"] == gen:   # descarta si el hablante ya retomó
                evaluando["resultado"] = veredicto
                evaluando["texto"] = texto  # reutilizable: evita transcribir 2 veces
            evaluando["activo"] = False

        while True:
            data, _ = stream.read(bloque)
            data = bytes(data)
            nivel = rms(data)
            if not hablando:
                espera_acum += 0.03
                pre_roll.append(data)
                if len(pre_roll) > 16:   # ~0.5 s de contexto previo
                    pre_roll.pop(0)
                racha_voz = racha_voz + 1 if nivel > umbral else 0
                if racha_voz >= 3:       # voz sostenida (no un golpe)
                    hablando = True
                    t_inicio_voz = time.time() - 0.09  # la voz empezó ~3 bloques atrás
                    frames.extend(pre_roll)
                    bloques_con_voz += racha_voz
                elif espera_acum >= espera_inicio:
                    raise RuntimeError("No detecté que hablaras.")
                continue

            frames.append(data)
            dur += 0.03
            if nivel > umbral:
                bloques_con_voz += 1
                silencio_acum = 0.0
                if evaluando["resultado"] is not None or evaluando["activo"]:
                    # Retomó la voz: invalida cualquier veredicto pendiente de
                    # la pausa anterior (era sobre un texto ya viejo).
                    evaluando["resultado"] = None
                    evaluando["texto"] = None
                    evaluando["gen"] += 1
            else:
                silencio_acum += 0.03

            if dur >= max_frase:
                break
            # Lanzar la evaluación semántica en cuanto la pausa "pinta" a final
            # (umbral_eval < fin_silencio): corre en paralelo mientras se sigue
            # esperando, y su transcripción se reutiliza al salir.
            if (gate and silencio_acum >= umbral_eval
                    and not evaluando["activo"] and evaluando["resultado"] is None):
                evaluando["activo"] = True
                gen = evaluando["gen"]
                snap = b"".join(frames)
                threading.Thread(target=_evaluar_en_fondo, args=(snap, gen),
                                 daemon=True).start()
            if silencio_acum < fin_silencio:
                continue
            if not gate:
                break
            if evaluando["resultado"] is True:
                break
            if silencio_acum >= fin_silencio + _EXTENSION_MAX:
                break  # tope duro: el evaluador no llegó a tiempo o dijo que sigues

        voz_dur = bloques_con_voz * 0.03  # segundos de voz real dentro de la grabación
        # Transcripción reutilizable: solo si pertenece a ESTA toma (no hubo voz
        # nueva después del snapshot que evaluó la compuerta) Y trajo texto. Si
        # vino vacía o dudosa (None), NO se reutiliza: el llamador transcribe
        # completo y puede escalar a Whisper en la nube, que entiende más.
        texto_parcial = (evaluando["texto"]
                         if evaluando["resultado"] is not None and evaluando["texto"]
                         else None)
        return (b"".join(frames), sample_rate, voz_dur,
                (t_inicio_voz or time.time()), texto_parcial)
    finally:
        try:
            stream.stop(); stream.close()
        except Exception:
            pass


# ──────────────────────────────────────────────
#  Salida — reproducción en dispositivo elegido
# ──────────────────────────────────────────────

def _reproducir_mci(ruta: str) -> bool:
    """Respaldo: reproduce por la salida por defecto de Windows (sin elegir dispositivo)."""
    try:
        alias = f"laravoz{int(time.time()*1000)}"
        mci = ctypes.windll.winmm.mciSendStringW
        if mci(f'open "{ruta}" type mpegvideo alias {alias}', None, 0, 0) != 0:
            return False
        mci(f"play {alias} wait", None, 0, 0)
        mci(f"close {alias}", None, 0, 0)
        return True
    except Exception:
        return False


def reproducir_audio(ruta: str) -> bool:
    """
    Reproduce un archivo de audio (mp3/wav) COMPLETO en el dispositivo de salida
    elegido (o el por defecto). Decodifica con miniaudio y reproduce con sounddevice
    esperando a que termine (sd.wait) -> nunca corta a media palabra. Si esa vía
    falla, cae a MCI como último respaldo.
    """
    with _lock:
        device = get_output_device()  # None = dispositivo por defecto del sistema
        try:
            import miniaudio
            import sounddevice as sd
            import numpy as np
            # Latencia 'high': búfer más grande -> evita los micro-cortes en textos largos.
            try:
                sd.default.latency = ("high", "high")
            except Exception:
                pass
            dec = miniaudio.decode_file(ruta, output_format=miniaudio.SampleFormat.FLOAT32)
            muestras = np.frombuffer(dec.samples, dtype=np.float32)
            if dec.nchannels > 1:
                muestras = muestras.reshape(-1, dec.nchannels)
            sd.play(muestras, dec.sample_rate, device=device)
            sd.wait()
            return True
        except Exception:
            return _reproducir_mci(ruta)


def reproducir_tono(frecuencias=(660, 880), dur: float = 0.12, volumen: float = 0.25) -> bool:
    """
    Sintetiza y reproduce un 'chime' corto (sin archivos de audio) en el
    dispositivo de salida elegido. Se usa como señal de presencia cuando LARA se
    activa por wake word — un toque JARVIS que confirma "te escucho" sin hablar.
    'frecuencias' es una secuencia de tonos en Hz que suenan encadenados.
    Devuelve False silenciosamente si el audio no está disponible.
    """
    try:
        import numpy as np
        import sounddevice as sd
    except Exception:
        return False
    with _lock:
        device = get_output_device()
        sr = 44100
        try:
            ondas = []
            for f in frecuencias:
                t = np.linspace(0, dur, int(sr * dur), endpoint=False)
                onda = np.sin(2 * np.pi * f * t).astype(np.float32)
                # Envolvente suave (fade in/out) para que no "chasquee".
                n_fade = max(1, int(sr * 0.01))
                env = np.ones_like(onda)
                env[:n_fade] = np.linspace(0, 1, n_fade)
                env[-n_fade:] = np.linspace(1, 0, n_fade)
                ondas.append(onda * env * volumen)
            señal = np.concatenate(ondas)
            sd.play(señal, sr, device=device)
            sd.wait()
            return True
        except Exception:
            return False


def detener_salida() -> None:
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:
        pass
    try:
        ctypes.windll.winmm.mciSendStringW("close all", None, 0, 0)
    except Exception:
        pass
