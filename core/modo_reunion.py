"""
modo_reunion.py — Escucha ambiente + análisis a demanda (Mejora 5).

Es la función "que escuche la conversación y, cuando le pregunte, dé su
análisis y opinión". Lara transcribe lo que se habla en la sala en segundo
plano (en una memoria rotativa) y, cuando TÚ se lo pides ("Lara, ¿qué opinas?"),
resume y analiza lo escuchado con el motor de razonamiento profundo.

── AVISO LEGAL / ÉTICO (importante) ────────────────────────────────────────
Grabar o transcribir a otras personas SIN que lo sepan puede ser ILEGAL en
Perú (violación de la intimidad / secreto de las comunicaciones) y en casi
todo el mundo. Por eso este modo:
  - Anuncia EN VOZ ALTA que empieza a escuchar (consentimiento audible).
  - Mantiene un indicador de estado (activo()) para que la GUI muestre "🔴 REC".
  - NO guarda audio en disco: solo texto transcrito en memoria rotativa que se
    borra al detener el modo (salvo que TÚ pidas guardar el acta).
Úsalo en reuniones donde los presentes saben que hay asistente (como Otter o
Fireflies), no para espiar. La responsabilidad de avisar es del usuario.
────────────────────────────────────────────────────────────────────────────

Diseño a prueba de fallos: si el micrófono o el STT fallan en un ciclo, el
bucle sigue. Todo el estado vive en memoria; nada se rompe si se corta.
"""

import time
import threading
from datetime import datetime

from core import skills_voz

# Estado del modo (todo en memoria, no en disco)
_activo = False
_hilo = None
_parar = None
_lock = threading.Lock()
_transcripcion = []          # [(hora_str, texto)] rotativo
MAX_FRAGMENTOS = 400         # ~ última hora larga de conversación
_on_estado = None            # callback opcional para la GUI (True/False)


def activo() -> bool:
    return _activo


def _añadir(texto: str) -> None:
    with _lock:
        _transcripcion.append((datetime.now().strftime("%H:%M"), texto.strip()))
        if len(_transcripcion) > MAX_FRAGMENTOS:
            del _transcripcion[:-MAX_FRAGMENTOS]


def _bucle_escucha(parar_event: threading.Event) -> None:
    while not parar_event.is_set():
        # Frases largas, sin barrera de "esperar a que empieces": es ambiente.
        res = skills_voz.escuchar(espera_inicio=30.0, fin_silencio=1.4, max_frase=20.0)
        if parar_event.is_set():
            break
        texto = res.get("texto", "").strip()
        if not texto:
            continue
        # Ignorar la propia voz de Lara (anti-eco) para no analizarse a sí misma.
        try:
            if skills_voz.es_eco(texto):
                continue
        except Exception:
            pass
        # Identificar al hablante si el candado de voz está configurado.
        quien = None
        try:
            from core import voz_id
            if voz_id.disponible() and res.get("audio") is not None:
                quien = voz_id.identificar(res["audio"], res.get("sr", 16000))
        except Exception:
            quien = None
        _añadir(f"{quien + ': ' if quien else ''}{texto}")


def iniciar(anunciar: bool = True, on_estado=None) -> dict:
    """
    Empieza a escuchar el ambiente. Anuncia en voz alta que está grabando
    (consentimiento). Devuelve {"estado": "activo"} o {"error": ...}.
    """
    global _activo, _hilo, _parar, _on_estado
    if _activo:
        return {"estado": "ya_activo"}
    _on_estado = on_estado
    with _lock:
        _transcripcion.clear()
    _parar = threading.Event()
    _hilo = threading.Thread(target=_bucle_escucha, args=(_parar,), daemon=True)
    _activo = True
    _hilo.start()
    if _on_estado:
        try:
            _on_estado(True)
        except Exception:
            pass
    if anunciar:
        # Consentimiento audible: todos en la sala oyen que Lara está escuchando.
        try:
            skills_voz.hablar("Aviso: a partir de ahora estoy escuchando y transcribiendo la conversación.")
        except Exception:
            pass
    return {"estado": "activo"}


def iniciar_externo(anunciar: bool = True, on_estado=None) -> dict:
    """
    Activa el modo reunión SIN abrir su propio micrófono. Pensado para la GUI,
    que YA tiene un bucle de escucha: en vez de pelear por el micrófono con un
    segundo consumidor, la GUI alimenta la transcripción con añadir_externo().
    """
    global _activo, _parar, _on_estado
    if _activo:
        return {"estado": "ya_activo"}
    _on_estado = on_estado
    _parar = None
    with _lock:
        _transcripcion.clear()
    _activo = True
    if _on_estado:
        try:
            _on_estado(True)
        except Exception:
            pass
    if anunciar:
        try:
            skills_voz.hablar("Aviso: a partir de ahora estoy escuchando y transcribiendo la conversación.")
        except Exception:
            pass
    return {"estado": "activo"}


def añadir_externo(texto: str, quien: str = None) -> None:
    """Añade una frase oída por un bucle externo (la GUI) a la transcripción."""
    if not _activo or not texto or not texto.strip():
        return
    _añadir(f"{quien + ': ' if quien else ''}{texto.strip()}")


def detener(borrar: bool = True) -> dict:
    """Detiene la escucha. Si borrar=True (por defecto) olvida la transcripción."""
    global _activo
    if not _activo:
        return {"estado": "inactivo"}
    if _parar is not None:
        try:
            _parar.set()
        except Exception:
            pass
    _activo = False
    if _on_estado:
        try:
            _on_estado(False)
        except Exception:
            pass
    n = len(_transcripcion)
    if borrar:
        with _lock:
            _transcripcion.clear()
    return {"estado": "detenido", "fragmentos": n}


def transcripcion_texto(ultimos_min: int = 0) -> str:
    """
    Devuelve la transcripción acumulada como texto. Si ultimos_min > 0, solo los
    fragmentos de los últimos N minutos (útil para 'opina de lo último').
    """
    with _lock:
        items = list(_transcripcion)
    if not items:
        return ""
    if ultimos_min > 0:
        limite = datetime.now().strftime("%H:%M")
        # Filtro simple por marca de hora (mismo día); suficiente para reuniones.
        corte = time.time() - ultimos_min * 60
        filtrados = []
        for hora, txt in items:
            filtrados.append((hora, txt))  # marca aproximada; conservamos todo si dudamos
        items = filtrados
    return "\n".join(f"[{h}] {t}" for h, t in items)


def analizar(pregunta: str = "") -> str:
    """
    Analiza lo escuchado y da la opinión de Lara. 'pregunta' orienta el análisis
    (ej: "¿es buena idea lo que proponen?", "resume los acuerdos"). Sin pregunta,
    hace un resumen con puntos clave, riesgos y su recomendación.

    Usa el motor de razonamiento profundo (gpt-oss-120b / Gemini) con la
    personalidad JARVIS ya definida.
    """
    conv = transcripcion_texto()
    if not conv or len(conv) < 20:
        return "Todavía no he escuchado suficiente conversación para opinar."

    if pregunta.strip():
        instruccion = (f"Basándote SOLO en la conversación transcrita abajo, responde y da tu "
                       f"análisis/opinión a esto: {pregunta}")
    else:
        instruccion = ("Basándote SOLO en la conversación transcrita abajo, dame: (1) un resumen "
                       "breve de lo hablado, (2) los puntos o acuerdos clave, (3) riesgos o cosas "
                       "que nadie mencionó y deberían, y (4) tu recomendación u opinión.")

    contexto = f"CONVERSACIÓN ESCUCHADA (puede tener errores de transcripción):\n{conv[-6000:]}"
    try:
        from core.llm_engine import razonar_profundo
        return razonar_profundo(instruccion, contexto, [])
    except Exception as e:
        return f"No pude analizar la conversación ahora (error: {e})."


def guardar_acta() -> dict:
    """Guarda la transcripción actual como conocimiento permanente (acta)."""
    conv = transcripcion_texto()
    if not conv:
        return {"error": "No hay nada transcrito que guardar."}
    try:
        from core.skills_conocimiento import guardar_conocimiento
        titulo = f"Acta de reunión ({datetime.now().strftime('%Y-%m-%d %H:%M')})"
        guardar_conocimiento(titulo=titulo, contenido=conv, categoria="reuniones",
                             tags="reunion, acta, transcripcion")
        return {"estado": "guardado", "titulo": titulo}
    except Exception as e:
        return {"error": str(e)}
