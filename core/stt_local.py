"""
stt_local.py — Reconocimiento de voz LOCAL (offline) con faster-whisper.

Es la Mejora 1 del salto "tipo JARVIS": Lara ya transcribía con Whisper, pero
en la NUBE (Groq). Eso depende de internet, tiene límites de uso y añade la
latencia del viaje de red. Este módulo corre Whisper EN TU PC:

  - Funciona SIN internet.
  - Sin límites de peticiones ni costo.
  - Menos latencia una vez cargado el modelo (no sube el audio a ningún lado).
  - Más privado: el audio nunca sale del equipo.

Se integra en skills_voz.escuchar() como PRIMER intento. Si faster-whisper no
está instalado o el modelo no carga, devuelve None y skills_voz cae solo a
Groq y luego a Google, exactamente como antes. Nada se rompe si falta la
dependencia.

Instalar:  pip install faster-whisper

Modelo por defecto: "small" (buen equilibrio precisión/velocidad en CPU).
Para acentos difíciles o mucho ruido sube a "medium"; si tienes GPU NVIDIA,
"medium"/"large-v3" vuelan. Se puede forzar con la variable de entorno
LARA_WHISPER_MODEL (ej: medium) y el dispositivo con LARA_WHISPER_DEVICE
(cpu | cuda).
"""

import os
# Silencia el aviso de symlinks de HuggingFace en Windows (no afecta al uso).
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
import threading

_modelo = None
_estado = None          # None=por probar, True=disponible, False=no disponible
_lock = threading.Lock()

_TAM_MODELO = os.environ.get("LARA_WHISPER_MODEL", "small")
_DISPOSITIVO = os.environ.get("LARA_WHISPER_DEVICE", "auto")
_IDIOMA = "es"


def disponible() -> bool:
    """True si faster-whisper se pudo cargar en esta sesión."""
    return _estado is True


def _cargar_modelo():
    """Carga (una sola vez) el modelo faster-whisper. None si no se puede."""
    global _modelo, _estado
    if _estado is False:
        return None
    if _modelo is not None:
        return _modelo
    with _lock:
        if _modelo is not None:
            return _modelo
        try:
            from faster_whisper import WhisperModel
        except Exception:
            # Caso real visto en esta PC: Smart App Control de Windows bloquea
            # los .pyd SIN FIRMA de PyAV ("no podemos confirmar quién publicó
            # output.pyd") y el import de faster_whisper muere entero — y LARA
            # se quedaba sin oído local (todo el STT se iba a la nube, más
            # lento). Pero faster-whisper solo usa 'av' para decodificar
            # ARCHIVOS de audio (decode_audio) y LARA siempre le pasa arrays
            # de numpy ya decodificados: un módulo hueco en su lugar basta
            # para que el resto funcione igual.
            try:
                import sys as _sys
                import types as _types
                if "av" not in _sys.modules:
                    _stub = _types.ModuleType("av")
                    _stub.__version__ = "0.0-stub-lara"
                    _sys.modules["av"] = _stub
                from faster_whisper import WhisperModel
                print("[LARA] PyAV bloqueado/no disponible: Whisper local sigue "
                      "activo (solo se pierde decodificar archivos de audio).")
            except Exception:
                _estado = False
                print("[LARA] faster-whisper no se pudo cargar. STT local "
                      "desactivado esta sesión; uso Whisper en la nube.")
                return None
        try:
            # int8 = rápido y ligero en CPU; float16 si hay GPU. compute_type
            # "auto" deja que la librería elija lo mejor para el dispositivo.
            dispositivo = _DISPOSITIVO
            if dispositivo == "auto":
                try:
                    import ctranslate2
                    dispositivo = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    dispositivo = "cpu"
            compute = "float16" if dispositivo == "cuda" else "int8"
            _modelo = WhisperModel(_TAM_MODELO, device=dispositivo, compute_type=compute)
            _estado = True
            print(f"[LARA] Whisper LOCAL cargado ({_TAM_MODELO}, {dispositivo}). STT sin internet activo.")
            return _modelo
        except Exception as e:
            print(f"[LARA] No pude cargar Whisper local ({e}). Uso Whisper en la nube.")
            _estado = False
            return None


def precargar() -> bool:
    """Carga el modelo por adelantado (ej: al arrancar) para que la primera
    transcripción no tenga que esperar la carga. Devuelve True si quedó listo."""
    return _cargar_modelo() is not None


def transcribir(crudo: bytes, sr_rate: int = 16000):
    """
    Transcribe audio PCM int16 mono. Devuelve el texto (str), "" si no se
    entendió nada, o None si el motor local no está disponible (para que el
    llamador use su respaldo en la nube).

    Aplica el VAD interno de Whisper y filtra segmentos de baja confianza,
    igual que hace skills_voz con la versión de Groq (anti-alucinación).
    """
    modelo = _cargar_modelo()
    if modelo is None:
        return None
    try:
        import numpy as np
        audio = np.frombuffer(crudo, dtype=np.int16).astype(np.float32) / 32768.0
        if sr_rate != 16000:
            # Remuestreo lineal simple a 16 kHz (lo que espera Whisper).
            n = int(round(audio.size * 16000 / sr_rate))
            if n > 0:
                audio = np.interp(
                    np.linspace(0, audio.size, n, endpoint=False),
                    np.arange(audio.size), audio).astype(np.float32)

        # beam_size=1 (greedy) transcribe ~2-3x más rápido que beam_size=5, con
        # pérdida de precisión mínima en frases cortas de comando/charla — que es
        # el 99% del uso por voz. Ajustable con LARA_WHISPER_BEAM si algún día
        # quieres priorizar precisión sobre velocidad.
        beam = int(os.environ.get("LARA_WHISPER_BEAM", "1"))
        # initial_prompt: ancla el estilo/vocabulario esperado. Ayuda con el
        # acento y con nombres que Whisper "small" transcribe raro. Se puede
        # cambiar o vaciar con LARA_WHISPER_PROMPT.
        # El prompt CEBA el vocabulario típico: sin él, el modelo "small"
        # transcribía "blog de notas" en vez de "bloc de notas", etc.
        prompt = os.environ.get(
            "LARA_WHISPER_PROMPT",
            "Conversación en español con Lara, la asistente del taller de "
            "electrónica: Lara, abre el bloc de notas. ¿Qué hora es? Sube el "
            "volumen. Anota esto en mis tareas.") or None
        segmentos, _info = modelo.transcribe(
            audio, language=_IDIOMA, vad_filter=True,
            beam_size=beam, temperature=0.0,
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        partes, dudosos = [], 0
        for s in segmentos:
            # avg_logprob bajo o no_speech_prob alto => segmento dudoso (ruido)
            if getattr(s, "no_speech_prob", 0.0) > 0.6:
                continue
            if getattr(s, "avg_logprob", 0.0) < -1.0:
                dudosos += 1   # HABÍA voz, pero el modelo pequeño no está seguro
                continue
            partes.append(s.text.strip())
        texto = " ".join(p for p in partes if p).strip()
        if not texto and dudosos:
            # El "small" oyó algo pero no lo entendió con confianza. Antes esto
            # devolvía "" y LARA te decía "no entendí" (tenías que REPETIR).
            # Devolver None deja que el llamador pruebe Whisper large en la
            # NUBE, que entiende mucho mejor los casos difíciles.
            return None
        return texto
    except Exception as e:
        print(f"[LARA] Error en Whisper local: {e}")
        return None
