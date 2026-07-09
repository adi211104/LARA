"""
wake_word.py — Activación por voz "Oye, Lara" (Mejora 2, estilo JARVIS).

En lugar de pulsar un botón para hablarle, Lara escucha de fondo y despierta
cuando dices su nombre. Reutiliza TODA la tubería de audio que ya existe
(grabar_vad + Whisper local/nube + anti-eco + firma de voz humana), así que
hereda su robustez sin duplicar nada.

Cómo funciona cada ciclo:
  1. Espera una frase con VAD y la transcribe (skills_voz.escuchar).
  2. Si la frase EMPIEZA por la palabra de activación ("lara", "oye lara",
     "hey lara", "ok lara"):
        - BARGE-IN: si Lara estaba hablando, corta su voz al instante.
        - Si dijiste solo el nombre, escucha la orden a continuación.
        - Si dijiste nombre + orden en la misma frase, usa el resto como orden.
        - Identifica quién habló (voz_id) y lo pasa como 'hablante'.
  3. Llama al_activar(orden, hablante).

Todo es a prueba de fallos: si el micrófono o el STT fallan en un ciclo, el
bucle sigue vivo. Se detiene con parar_event.set().

NOTA: solo un consumidor del micrófono debe correr a la vez. No uses el wake
word y el modo reunión al mismo tiempo (ambos usan el mismo micrófono).
"""

import re
import time
import unicodedata

from core import skills_voz

# Palabras/prefijos que despiertan a Lara. Se comparan sin acentos ni signos.
PALABRAS_ACTIVACION = ("lara", "oye lara", "hey lara", "ok lara", "escucha lara",
                       "oye, lara", "hola lara")


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


# El STT a menudo destroza el nombre "Lara" (plara, clara, laura, lala...). Para
# no ignorar al usuario por un error de transcripción, aceptamos variantes muy
# parecidas. Se EXCLUYEN a propósito palabras comunes/nombres reales como
# "para" y "sara" para no auto-activarse con cualquier frase.
import difflib as _difflib
_LARA_VARIANTES = {"lara", "plara", "clara", "laura", "larra", "lala", "jara",
                   "tara", "dara", "laira", "lira", "llara", "larah", "laca"}


def _es_token_lara(tok: str) -> bool:
    if not tok:
        return False
    if tok in _LARA_VARIANTES:
        return True
    if tok in ("para", "sara", "cara", "mara", "vara", "hora", "ahora"):
        return False  # comunes: no confundir con el nombre
    return _difflib.SequenceMatcher(None, tok, "lara").ratio() >= 0.8


def menciona_lara(texto: str) -> bool:
    """True si el texto contiene el nombre 'Lara' o una variante que el STT
    suele producir. Úsalo en vez de ('lara' in texto), que falla con 'plara'."""
    return any(_es_token_lara(t) for t in _norm(texto).split())


def _extraer_orden(texto: str):
    """
    Si 'texto' empieza por la palabra de activación, devuelve (True, orden) donde
    'orden' es lo que sigue al nombre (puede ser "" si solo dijo "Lara").
    Si no contiene la activación, devuelve (False, "").
    """
    n = _norm(texto)
    if not n:
        return False, ""
    # ¿Empieza por alguna forma de activación?
    for p in sorted(PALABRAS_ACTIVACION, key=len, reverse=True):
        pn = _norm(p)
        if n == pn:
            return True, ""
        if n.startswith(pn + " "):
            return True, texto[len(p):].lstrip(" ,.:;").strip()
    # Activación en medio de la frase (ej: "eh, lara, apaga la luz")
    m = re.search(r"\blara\b", n)
    if m:
        resto = n[m.end():].lstrip(" ,.:;")
        # Reconstruimos aprox. la orden desde el texto original tras "lara"
        mo = re.search(r"lara", texto, re.IGNORECASE)
        orden = texto[mo.end():].lstrip(" ,.:;").strip() if mo else resto
        return True, orden

    # Difusa: un token PARECIDO a "lara" (plara, clara...) por error del STT.
    toks = n.split()
    for i, t in enumerate(toks):
        if _es_token_lara(t):
            return True, " ".join(toks[i + 1:]).strip()
    return False, ""


def _identificar(audio, sr):
    """Nombre del hablante vía voz_id, o None si no está disponible/activo."""
    try:
        from core import voz_id
        if not voz_id.disponible():
            return None
        return voz_id.identificar(audio, sr)
    except Exception:
        return None


def _chime() -> None:
    """Toque de presencia al activarse (si el audio está disponible)."""
    try:
        from core.skills_audio import reproducir_tono
        reproducir_tono()
    except Exception:
        pass


# Frases que cierran una conversación de seguimiento sin decir de nuevo "Lara".
_CIERRE = ("gracias", "eso es todo", "nada mas", "nada más", "listo", "ya esta",
           "ya está", "olvidalo", "olvídalo", "para", "detente", "silencio")


def escuchar_activacion(al_activar, parar_event,
                        verificar_hablante: bool = True,
                        solo_autorizado: bool = False,
                        espera_orden: float = 6.0,
                        seguimiento: bool = True,
                        ventana_seguimiento: float = 5.0) -> None:
    """
    Bucle de wake word. Por cada activación válida llama:
        al_activar(orden: str, hablante: str | None)

    - verificar_hablante: identifica quién habló y lo pasa a al_activar.
    - solo_autorizado: si True y el candado de voz está activo, ignora a quien
      no esté autorizado (voz_id.autorizado). Útil para que no la active
      cualquiera.
    - espera_orden: segundos que espera la orden cuando solo dijiste "Lara".
    - seguimiento: tras responder, sigue escuchando unos segundos para permitir
      una repregunta natural SIN repetir "Lara" (conversación continua tipo
      JARVIS). Se corta al quedarte en silencio o decir "gracias/eso es todo".
    - ventana_seguimiento: cuántos segundos espera esa repregunta.
    """
    while not parar_event.is_set():
        res = skills_voz.escuchar(espera_inicio=6.0, fin_silencio=1.0, max_frase=12.0)
        if parar_event.is_set():
            break
        texto = res.get("texto", "")
        if not texto:
            time.sleep(0.15)
            continue

        activo, orden = _extraer_orden(texto)
        if not activo:
            continue  # no dijo "Lara": seguir escuchando de fondo

        # BARGE-IN: si Lara estaba hablando, la cortamos para atender.
        if skills_voz.esta_hablando():
            skills_voz.detener_voz()
        _chime()  # toque de presencia: "te escucho"

        # Filtro opcional por candado de voz (solo el dueño la activa).
        if solo_autorizado and res.get("audio") is not None:
            try:
                from core import voz_id
                cfg = voz_id.get_config()
                if cfg.get("candado_activo") and not voz_id.autorizado(res["audio"], res.get("sr", 16000)):
                    continue
            except Exception:
                pass

        hablante = None
        if verificar_hablante and res.get("audio") is not None:
            hablante = _identificar(res["audio"], res.get("sr", 16000))

        # Si solo dijo el nombre, pedimos la orden a continuación.
        if not orden:
            try:
                skills_voz.hablar("¿Sí?")
            except Exception:
                pass
            res2 = skills_voz.escuchar(espera_inicio=espera_orden, fin_silencio=1.1, max_frase=15.0)
            if parar_event.is_set():
                break
            orden = res2.get("texto", "").strip()
            if verificar_hablante and hablante is None and res2.get("audio") is not None:
                hablante = _identificar(res2["audio"], res2.get("sr", 16000))
            if not orden:
                continue

        try:
            al_activar(orden, hablante)
        except Exception:
            pass

        if seguimiento:
            _seguimiento(al_activar, parar_event, hablante,
                         verificar_hablante, ventana_seguimiento)


def _seguimiento(al_activar, parar_event, hablante,
                 verificar_hablante: bool, ventana_seguimiento: float) -> None:
    """
    Conversación continua: tras responder, escucha repreguntas SIN repetir
    "Lara". Se corta al quedarte en silencio o decir "gracias/eso es todo".
    Compartido por el wake word por transcripción y el wake word local.
    """
    while not parar_event.is_set():
        # Esperar a que Lara termine de hablar antes de volver a escuchar
        # (si no, grabaría su propia voz).
        espera = 0.0
        while skills_voz.esta_hablando() and espera < 30 and not parar_event.is_set():
            time.sleep(0.2)
            espera += 0.2
        seg = skills_voz.escuchar(espera_inicio=ventana_seguimiento,
                                  fin_silencio=1.1, max_frase=15.0)
        if parar_event.is_set():
            break
        texto_seg = seg.get("texto", "").strip()
        if not texto_seg:
            break  # silencio: fin de la conversación, vuelve a modo wake word
        try:
            if skills_voz.es_eco(texto_seg):
                continue  # era su propia voz
        except Exception:
            pass
        n_seg = _norm(texto_seg)
        if any(n_seg == c or n_seg.startswith(c) for c in _CIERRE):
            break
        # Permitir que una repregunta empiece igualmente por "Lara".
        _act2, orden2 = _extraer_orden(texto_seg)
        orden2 = orden2 if _act2 and orden2 else texto_seg
        quien2 = hablante
        if verificar_hablante and seg.get("audio") is not None:
            quien2 = _identificar(seg["audio"], seg.get("sr", 16000)) or hablante
        try:
            al_activar(orden2, quien2)
        except Exception:
            pass


# ──────────────────────────────────────────────
#  Wake word LOCAL instantáneo (openWakeWord)
# ──────────────────────────────────────────────
#
# El detector por transcripción (arriba) transcribe CADA frase con Whisper para
# ver si dijiste "Lara": preciso, pero costoso para tener siempre encendido.
# openWakeWord es un modelo neuronal minúsculo que detecta la palabra clave en
# tiempo real con muy poca CPU, sin transcribir nada. En cuanto dispara, usamos
# la tubería normal (chime + escuchar la orden con el buen STT).
#
# Palabra clave: por defecto "hey jarvis" (modelo preentrenado que encaja con el
# tema). openWakeWord NO trae un modelo "Lara" de fábrica; si entrenas uno,
# apunta a su archivo con la variable de entorno LARA_WAKE_MODEL (ruta .onnx/
# .tflite) o pon el nombre de otro modelo preentrenado (alexa, hey_mycroft...).
import os as _os

_oww_model = None
_oww_estado = None          # None=por probar, True=ok, False=no disponible
_WAKE_MODELO = _os.environ.get("LARA_WAKE_MODEL", "hey_jarvis")
_WAKE_UMBRAL = float(_os.environ.get("LARA_WAKE_UMBRAL", "0.5"))


def local_disponible() -> bool:
    """True si openWakeWord se pudo cargar (wake word local instantáneo)."""
    return _cargar_oww() is not None


def _cargar_oww():
    global _oww_model, _oww_estado
    if _oww_estado is False:
        return None
    if _oww_model is not None:
        return _oww_model
    try:
        from openwakeword.model import Model
    except Exception:
        _oww_estado = False
        return None
    try:
        # Descarga los modelos preentrenados la primera vez (si hace falta).
        try:
            import openwakeword
            openwakeword.utils.download_models()
        except Exception:
            pass
        if _os.path.exists(_WAKE_MODELO):
            _oww_model = Model(wakeword_models=[_WAKE_MODELO])
        else:
            _oww_model = Model(wakeword_models=[_WAKE_MODELO])
        _oww_estado = True
        print(f"[LARA] Wake word LOCAL activo (openWakeWord: {_WAKE_MODELO}).")
        return _oww_model
    except Exception as e:
        print(f"[LARA] No pude cargar el wake word local ({e}). Uso el de transcripción.")
        _oww_estado = False
        return None


def escuchar_activacion_local(al_activar, parar_event,
                              verificar_hablante: bool = True,
                              seguimiento: bool = True,
                              ventana_seguimiento: float = 5.0) -> None:
    """
    Bucle de wake word LOCAL: escucha el micrófono en tiempo real con
    openWakeWord (sin transcribir) y, al detectar la palabra clave, reproduce el
    chime y captura la orden con el STT normal. Si openWakeWord no está
    disponible, cae al detector por transcripción (escuchar_activacion).
    """
    modelo = _cargar_oww()
    if modelo is None:
        return escuchar_activacion(al_activar, parar_event,
                                   verificar_hablante=verificar_hablante,
                                   seguimiento=seguimiento,
                                   ventana_seguimiento=ventana_seguimiento)
    try:
        import numpy as np
        import sounddevice as sd
        from core.skills_audio import get_input_device
    except Exception:
        return escuchar_activacion(al_activar, parar_event,
                                   verificar_hablante=verificar_hablante,
                                   seguimiento=seguimiento,
                                   ventana_seguimiento=ventana_seguimiento)

    sr = 16000
    bloque = 1280  # 80 ms: tamaño de trama que espera openWakeWord

    while not parar_event.is_set():
        try:
            with sd.InputStream(samplerate=sr, channels=1, dtype="int16",
                                blocksize=bloque, device=get_input_device()) as stream:
                modelo.reset()
                while not parar_event.is_set():
                    # No escuchar la palabra clave mientras Lara habla (evita
                    # auto-activarse con su propia voz por los parlantes).
                    if skills_voz.esta_hablando():
                        time.sleep(0.05)
                        continue
                    data, _ = stream.read(bloque)
                    muestras = np.frombuffer(bytes(data), dtype=np.int16)
                    scores = modelo.predict(muestras)
                    disparo = any(v >= _WAKE_UMBRAL for v in scores.values())
                    if disparo:
                        break
        except Exception as e:
            print(f"[LARA] Wake word local: error de audio ({e}); reintento.")
            time.sleep(0.5)
            continue

        if parar_event.is_set():
            break

        # Detectado: cerramos el stream (salimos del with) y atendemos la orden.
        _chime()
        res = skills_voz.escuchar(espera_inicio=5.0, fin_silencio=1.1, max_frase=15.0)
        orden = res.get("texto", "").strip()
        # Quitar un "Lara/Jarvis" inicial si el STT lo capturó.
        _act, resto = _extraer_orden(orden)
        if _act and resto:
            orden = resto
        if not orden:
            continue
        hablante = None
        if verificar_hablante and res.get("audio") is not None:
            hablante = _identificar(res["audio"], res.get("sr", 16000))
        try:
            al_activar(orden, hablante)
        except Exception:
            pass
        if seguimiento:
            _seguimiento(al_activar, parar_event, hablante,
                         verificar_hablante, ventana_seguimiento)


def iniciar(al_activar, verificar_hablante: bool = True, solo_autorizado: bool = False,
            preferir_local: bool = True):
    """
    Arranca el wake word en un hilo de fondo. Devuelve (hilo, parar_event):
    llama parar_event.set() para detenerlo. Uso típico desde consola o GUI:

        hilo, parar = wake_word.iniciar(lambda orden, quien: cerebro.procesar(orden, quien))
        ...
        parar.set()
    """
    import threading
    parar = threading.Event()
    # Preferimos el wake word LOCAL instantáneo (openWakeWord) si está instalado;
    # si no, el detector por transcripción (que además reconoce "Lara").
    usar_local = preferir_local and local_disponible()
    if usar_local:
        objetivo = escuchar_activacion_local
        kwargs = {"verificar_hablante": verificar_hablante}
    else:
        objetivo = escuchar_activacion
        kwargs = {"verificar_hablante": verificar_hablante, "solo_autorizado": solo_autorizado}
    hilo = threading.Thread(target=objetivo, args=(al_activar, parar), kwargs=kwargs, daemon=True)
    hilo.start()
    return hilo, parar
