"""
llm_local.py — Cerebro LOCAL de LARA (Ollama + qwen2.5:3b-instruct).

Motivación: las cuentas gratuitas de Groq/Gemini se saturan (errores 503/429) y
tienen límites de tokens. La mayoría de las frases que le dices a LARA son
DECISIONES LIGERAS ("abre chrome", "qué hora es", "modo noche"): eso lo resuelve
de sobra un modelo pequeño (3B) corriendo EN TU PC, al instante, sin límites y
sin internet. Los casos difíciles (razonamiento técnico, búsquedas en internet,
generación de código) siguen yendo a la nube — la división la decide
llm_engine.py (cerebro híbrido).

Este módulo es el CLIENTE de Ollama (https://ollama.com) usando su API nativa
(/api/chat), que permite tres cosas que la API compatible-OpenAI no garantiza:
  - format="json"      -> el modelo NO PUEDE devolver texto fuera del JSON.
  - keep_alive="30m"   -> el modelo queda cargado en RAM entre turnos (sin
                          re-cargar ~2 GB en cada frase).
  - options.num_predict -> límite de generación real del runtime.

ENCENDIDO POR DEFECTO: si Ollama no está instalado o no corre, disponible()
devuelve False y LARA usa la nube EXACTAMENTE como antes — cero cambio de
comportamiento. Para apagarlo a propósito:  set LARA_LLM_LOCAL=0

Rendimiento medido en esta PC (CPU, Q4_K_M):
  - Prompt NUEVO: ~80 tokens/s de lectura (un prompt de 1000 tokens = ~12 s).
  - Prompt CACHEADO (mismo prefijo que el turno anterior): ~0.3 s + generación
    a ~15 tokens/s. Por eso llm_engine usa un prompt de router ESTÁTICO para
    el modelo local: el prefijo se cachea y cada comando sale en ~2-4 s.

Variables de entorno (todas opcionales):
  LARA_LLM_LOCAL=1|0          (defecto 1: auto — solo actúa si Ollama responde)
  LARA_OLLAMA_MODEL=qwen2.5:3b-instruct
  LARA_OLLAMA_EMBED=embeddinggemma   (embeddings de la memoria semántica)
  LARA_OLLAMA_URL=http://127.0.0.1:11434
  LARA_OLLAMA_TIMEOUT=25      (segundos por llamada; si excede -> nube)
  LARA_OLLAMA_KEEP_ALIVE=3m   (cuánto queda el modelo cargado en RAM; ver NOTA)
  LARA_OLLAMA_NUM_CTX=2048    (tamaño de contexto; ver nota de RAM abajo)

NOTA DE RAM: sin num_ctx, Ollama usa el contexto por defecto del Modelfile del
modelo (para qwen2.5-coder eso es grande). En una PC con poca RAM libre, el
runner de llama.cpp puede fallar al reservar el buffer de KV-cache (error 500
"failed to allocate buffer for kv cache") y la llamada real fallará SIEMPRE
aunque disponible() diga True. Por eso se fija un num_ctx moderado por
defecto: los prompts de router de LARA son cortos y no lo necesitan grande.

NOTA DE KEEP_ALIVE: mientras el modelo queda cargado, el PROCESO ollama.exe
retiene ~600 MB de RAM del sistema (medido en esta PC), aparte de lo que use
la GPU. En equipos con poca RAM libre eso compite con Whisper local
(stt_local.py) y puede degradar la captura de voz. Por eso el default bajó de
30m a 3m: sigue habiendo caché de prompt dentro de una conversación seguida,
pero se libera la RAM más rápido en los silencios. Si tu PC tiene RAM de
sobra y priorizas latencia sobre la voz, puedes subirlo con la variable de
entorno.
"""

import os
import json
import time
import threading

# 127.0.0.1 (NO "localhost"): en Windows "localhost" intenta primero IPv6 y el
# fallback a IPv4 costaba ~2 s en CADA llamada. Medido: 2.5 s -> 0.3 s.
_URL = os.environ.get("LARA_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
_MODELO = os.environ.get("LARA_OLLAMA_MODEL", "qwen2.5:3b-instruct")
_ACTIVO = os.environ.get("LARA_LLM_LOCAL", "1").strip().lower() not in ("0", "false", "no", "")
_TIMEOUT = float(os.environ.get("LARA_OLLAMA_TIMEOUT", "25"))
_NUM_CTX = int(os.environ.get("LARA_OLLAMA_NUM_CTX", "2048"))
_THREADS = int(os.environ.get("LARA_OLLAMA_THREADS", "3"))
_KEEP_ALIVE = os.environ.get("LARA_OLLAMA_KEEP_ALIVE", "3m")


def _opciones_thinking():
    """Qwen3 rápido: evitar consumir el presupuesto en pensamiento invisible.

    No enviar la opción a modelos antiguos que podrían rechazarla.
    El usuario puede activarla explícitamente, pero el local solo atiende lo simple.
    """
    if _MODELO.lower().startswith('qwen3'):
        return {'think': os.environ.get('LARA_OLLAMA_THINK', '0').lower() in ('1', 'true', 'yes')}
    return {}
# Modelo de EMBEDDINGS local (memoria semántica sin gastar nube). Multilingüe:
# la memoria de LARA está en español. Instalar: ollama pull embeddinggemma
_MODELO_EMBED = os.environ.get("LARA_OLLAMA_EMBED", "embeddinggemma")

# Disponibilidad con reintento: si Ollama no respondía al arrancar pero lo
# abres después (o se cayó y volvió), LARA lo re-detecta sola cada 90 s en vez
# de quedarse para siempre con la primera respuesta (el bug del cache eterno).
_RECHEQUEO_SEG = 90.0
_estado_ok = None      # None = nunca probado; True/False = último resultado
_estado_ts = 0.0       # cuándo se probó por última vez
_ya_avisado = False    # para no repetir el mensaje de "cerebro local activo"
_precalentado = False

_sesion = None         # sesión HTTP persistente (singleton perezoso)


def _http():
    """
    Sesión HTTP compartida hacia Ollama. Dos optimizaciones MEDIDAS en esta PC:
      - trust_env=False: sin ella, requests consulta el proxy de Windows en
        cada petición (~2 s extra por llamada... ¡a localhost!).
      - conexión persistente (keep-alive TCP): evita reconectar en cada turno.
    Con ambas, un embed pasó de 2.5 s a 0.3 s.
    """
    global _sesion
    if _sesion is None:
        import requests
        s = requests.Session()
        s.trust_env = False
        _sesion = s
    return _sesion

# Estadísticas de la sesión: cuántos turnos resolvió el local (= llamadas a la
# nube que te ahorraste) y cuántas veces falló y hubo que ir a la nube.
_stats = {"respuestas": 0, "fallos": 0}


def activo() -> bool:
    """True si el usuario tiene habilitado el cerebro local (defecto: sí)."""
    return _ACTIVO


def modelo() -> str:
    return _MODELO


def stats() -> dict:
    """{'respuestas': n_resueltas_en_local, 'fallos': n_caidas_a_nube}."""
    return dict(_stats)


def _marcar(ok: bool):
    global _estado_ok, _estado_ts
    _estado_ok = ok
    _estado_ts = time.time()


def disponible() -> bool:
    """
    True si Ollama responde y tiene el modelo pedido. El resultado se cachea:
    si es True se confía hasta que una llamada real falle; si es False se
    reintenta cada 90 s (por si abres Ollama con LARA ya corriendo).
    """
    global _ya_avisado
    if not _ACTIVO:
        return False
    if _estado_ok is True:
        return True
    if _estado_ok is False and (time.time() - _estado_ts) < _RECHEQUEO_SEG:
        return False
    try:
        r = _http().get(f"{_URL}/api/tags", timeout=2.0)
        if r.status_code != 200:
            _marcar(False)
            return False
        nombres = [m.get("name", "") for m in r.json().get("models", [])]
        # Coincidencia EXACTA del tag completo: comparar solo por prefijo
        # (ej. "qwen2.5") producía falsos positivos con modelos distintos que
        # comparten familia (p. ej. "qwen2.5-coder:3b" vs "qwen2.5:3b-instruct"),
        # haciendo creer a LARA que el modelo estaba listo cuando en realidad
        # Ollama no tenía ese tag y cada llamada real fallaba en silencio.
        ok = _MODELO in nombres
        _marcar(ok)
        if ok and not _ya_avisado:
            _ya_avisado = True
            print(f"[LARA] Cerebro LOCAL activo (Ollama: {_MODELO}). "
                  f"Lo simple se resuelve en tu PC; lo difícil va a la nube.")
        elif not ok:
            print(f"[LARA] Ollama corre pero no encontré el modelo '{_MODELO}'. "
                  f"Haz: ollama pull {_MODELO}")
        return ok
    except Exception:
        _marcar(False)
        return False


def precalentar(messages: list = None):
    """
    Carga el modelo en RAM en segundo plano al arrancar LARA (y, si se pasan
    'messages', también deja CACHEADO el prefijo del prompt del router).
    Así el PRIMER comando del usuario ya sale rápido (~2-4 s) en vez de pagar
    la carga del modelo + lectura completa del prompt (~15-20 s en frío).
    No bloquea el arranque: corre en un hilo daemon y falla en silencio.
    """
    global _precalentado
    if not _ACTIVO or _precalentado:
        return
    _precalentado = True

    def _trabajo():
        if not disponible():
            return
        try:
            if messages:
                _http().post(f"{_URL}/api/chat", json={
                    "model": _MODELO, "messages": messages, "stream": False,
                    **_opciones_thinking(),
                    "keep_alive": _KEEP_ALIVE,
                    "options": {"num_predict": 1, "num_ctx": _NUM_CTX},
                }, timeout=120)
            else:
                # Sin prompt: solo carga los pesos y los mantiene (keep_alive).
                _http().post(f"{_URL}/api/generate", json={
                    "model": _MODELO, "keep_alive": _KEEP_ALIVE,
                    "options": {"num_ctx": _NUM_CTX}}, timeout=120)
        except Exception:
            pass

    threading.Thread(target=_trabajo, daemon=True, name="lara-precalentar-llm").start()


def chat(messages: list, json_mode: bool = False, max_tokens: int = 700,
         temperature: float = 0.3, timeout: float = None) -> str:
    """
    Llama al modelo local (API nativa /api/chat de Ollama) y devuelve el texto
    de la respuesta, o None si falla (para que el llamador use la nube).
    'messages' es la lista estándar [{"role","content"}].
    """
    if not disponible():
        return None
    try:
        payload = {
            "model": _MODELO,
            "messages": messages,
            "stream": False,
            **_opciones_thinking(),
            "keep_alive": _KEEP_ALIVE,
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": _NUM_CTX, "num_thread": _THREADS},
        }
        if json_mode:
            payload["format"] = "json"   # Ollama fuerza JSON válido a nivel de sampler
        r = _http().post(f"{_URL}/api/chat", json=payload,
                          timeout=timeout or _TIMEOUT)
        if r.status_code != 200:
            _stats["fallos"] += 1
            _marcar(False)
            return None
        texto = (r.json().get("message", {}).get("content") or "").strip()
        if texto:
            _stats["respuestas"] += 1
            return texto
        _stats["fallos"] += 1
        return None
    except Exception:
        # Timeout o conexión caída: marcar no-disponible para no volver a
        # esperar 25 s en cada frase; se reintenta solo a los 90 s.
        _stats["fallos"] += 1
        _marcar(False)
        return None


def chat_stream(messages: list, max_tokens: int = 500, temperature: float = 0.3):
    """
    Versión STREAMING de chat(): GENERADOR que va soltando trozos de texto
    conforme el modelo los produce. Clave para el modo offline: a ~15 tokens/s
    en CPU una respuesta completa tarda ~30 s, pero con streaming LARA empieza
    a HABLAR a los ~2-3 s (la voz lee frase por frase mientras se genera).
    Si algo falla a mitad, el generador simplemente termina (el llamador decide
    si lo que alcanzó a salir es suficiente).
    """
    if not disponible():
        return
    try:
        payload = {
            "model": _MODELO,
            "messages": messages,
            "stream": True,
            **_opciones_thinking(),
            "keep_alive": _KEEP_ALIVE,
            "options": {"temperature": temperature, "num_predict": max_tokens,
                        "num_ctx": _NUM_CTX, "num_thread": _THREADS},
        }
        hubo_texto = False
        with _http().post(f"{_URL}/api/chat", json=payload, stream=True,
                           timeout=_TIMEOUT) as r:
            if r.status_code != 200:
                _stats["fallos"] += 1
                _marcar(False)
                return
            for linea in r.iter_lines():
                if not linea:
                    continue
                try:
                    trozo = json.loads(linea)
                except Exception:
                    continue
                delta = trozo.get("message", {}).get("content") or ""
                if delta:
                    hubo_texto = True
                    yield delta
                if trozo.get("done"):
                    break
        if hubo_texto:
            _stats["respuestas"] += 1
        else:
            _stats["fallos"] += 1
    except Exception:
        _stats["fallos"] += 1
        _marcar(False)


# ---------------------------------------------------------
#  Embeddings LOCALES para la memoria semántica de largo plazo.
#  Cada turno de LARA busca recuerdos por significado; hacerlo local quita
#  una llamada a Gemini por turno (latencia + cuota) y funciona sin internet.
# ---------------------------------------------------------

_embed_ok = None       # None=por probar; True/False con re-chequeo como el chat
_embed_ts = 0.0


def modelo_embed() -> str:
    return _MODELO_EMBED


def embed_disponible() -> bool:
    """True si Ollama corre y tiene el modelo de embeddings instalado."""
    global _embed_ok, _embed_ts
    if not _ACTIVO:
        return False
    if _embed_ok is True:
        return True
    if _embed_ok is False and (time.time() - _embed_ts) < _RECHEQUEO_SEG:
        return False
    try:
        r = _http().get(f"{_URL}/api/tags", timeout=2.0)
        nombres = [m.get("name", "") for m in r.json().get("models", [])] if r.status_code == 200 else []
        _embed_ok = _MODELO_EMBED in nombres
    except Exception:
        _embed_ok = False
    _embed_ts = time.time()
    return _embed_ok


def embed(texto: str) -> list:
    """
    Devuelve el vector de embedding del texto (lista de floats), o None si el
    modelo local de embeddings no está (→ el llamador decide su respaldo).
    """
    global _embed_ok, _embed_ts
    if not texto or not embed_disponible():
        return None
    try:
        r = _http().post(f"{_URL}/api/embed", json={
            "model": _MODELO_EMBED,
            "input": texto[:2000],
            "keep_alive": _KEEP_ALIVE,
        }, timeout=15)
        if r.status_code != 200:
            return None
        vecs = r.json().get("embeddings") or []
        if vecs and vecs[0]:
            return [float(x) for x in vecs[0]]
        return None
    except Exception:
        _embed_ok = False
        _embed_ts = time.time()
        return None


def router_json(messages: list, max_tokens: int = 350) -> str:
    """
    Pide al modelo local la decisión del router en JSON. Devuelve el JSON (str)
    validado, o None si el local no está o no produjo JSON sano (→ usar nube).
    """
    raw = chat(messages, json_mode=True, max_tokens=max_tokens, temperature=0.2)
    if not raw:
        return None
    try:
        json.loads(raw)
        return raw
    except Exception:
        # Con format="json" no debería pasar, pero por si acaso: extraer el JSON.
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                json.loads(m.group(0))
                return m.group(0)
            except Exception:
                return None
    return None
