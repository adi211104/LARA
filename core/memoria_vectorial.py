"""
memoria_vectorial.py — Memoria SEMÁNTICA de largo plazo (Mejora 4).

Antes Lara solo recordaba los últimos N turnos (memoria "de corto plazo").
Este módulo le da memoria de largo plazo tipo JARVIS: guarda cada cosa
importante que se dice con su EMBEDDING (una huella numérica del significado)
y, cuando le preguntas algo, recupera lo más RELACIONADO por significado —
aunque uses otras palabras que la vez que lo dijiste.

Ejemplo: hace semanas dijiste "el cliente del amplificador Marshall paga los
viernes". Hoy preguntas "¿cuándo cobro lo del ampli?" — la búsqueda por
palabras no lo encontraría, pero la semántica sí.

Diseño a prueba de fallos (v10.2: embeddings LOCALES primero):
  - Embeddings con Ollama (embeddinggemma, multilingüe) EN TU PC: recordar()
    corre en CADA turno, y hacerlo local quita una llamada a Gemini por turno
    (latencia + cuota) y funciona sin internet.
  - Si el modelo local no está, cae a Gemini (text-embedding-004) como antes.
  - Si no hay ninguno, cae a búsqueda por solapamiento de palabras.
  - IMPORTANTE: vectores de modelos distintos NO son comparables entre sí
    (espacios diferentes). Cada entrada guarda con qué modelo se calculó
    ("vec_modelo") y solo se compara contra la consulta embebida con ESE
    mismo modelo. Las entradas viejas (Gemini) se MIGRAN al modelo local en
    segundo plano la primera vez que se detectan, sin bloquear nada.
  - Todo se guarda en KNOWLEDGE_DIR/memoria_vectorial.json (sincronizado por
    OneDrive como el resto de la memoria de Lara).
"""

import json
import math
import threading
import unicodedata
from datetime import datetime

from config import KNOWLEDGE_DIR

RUTA = KNOWLEDGE_DIR / "memoria_vectorial.json"
MODELO_EMBED_GEMINI = "text-embedding-004"
MAX_ENTRADAS = 4000        # tope: la memoria no crece infinita
_lock = threading.Lock()
_embed_estado_gemini = None    # None=por probar, True=ok, False=no disponible
_migracion_lanzada = False     # migrar gemini->local solo una vez por sesión

# Frases sin valor para recordar (ruido conversacional). No se indexan.
_TRIVIAL = {"ok", "vale", "gracias", "hola", "adios", "adiós", "chau", "si", "sí",
            "no", "ya", "listo", "bien", "dale", "aja", "ajá", "mmm", "eh"}

# Respuestas-error o de relleno del propio asistente: recordarlas solo mete
# ruido en las búsquedas ("No tengo ese dato exacto." aparecía como recuerdo).
_RUIDO_INICIO = ("no tengo ese dato", "no tengo el dato", "error", "tuve un problema",
                 "las ia están saturadas", "las ia estan saturadas", "no pude",
                 "lo siento", "no encontré resultados", "no encontre resultados")


def _cargar() -> list:
    if RUTA.exists():
        try:
            return json.loads(RUTA.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _guardar(data: list) -> None:
    try:
        RUTA.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _llm_local():
    try:
        from core import llm_local
        return llm_local
    except ImportError:
        try:
            import llm_local
            return llm_local
        except ImportError:
            return None


def _embed_local(texto: str):
    """(vector, etiqueta_modelo) con Ollama local, o (None, None)."""
    ll = _llm_local()
    if ll is None:
        return None, None
    try:
        vec = ll.embed(texto)
        if vec:
            return vec, "local:" + ll.modelo_embed()
    except Exception:
        pass
    return None, None


def _embed_gemini(texto: str):
    """(vector, etiqueta_modelo) con Gemini, o (None, None)."""
    global _embed_estado_gemini
    if _embed_estado_gemini is False:
        return None, None
    try:
        from core.llm_engine import genai_client
        r = genai_client.models.embed_content(model=MODELO_EMBED_GEMINI, contents=texto)
        # La SDK devuelve r.embeddings (lista) con .values; toleramos variantes.
        emb = getattr(r, "embeddings", None)
        if emb:
            vec = getattr(emb[0], "values", None) or (emb[0] if isinstance(emb[0], list) else None)
        else:
            vec = getattr(getattr(r, "embedding", None), "values", None)
        if vec:
            _embed_estado_gemini = True
            return [float(x) for x in vec], "gemini:" + MODELO_EMBED_GEMINI
    except Exception:
        _embed_estado_gemini = False
    return None, None


def _embedding(texto: str):
    """(vector, etiqueta_modelo) — local primero, Gemini de respaldo, o (None, None)."""
    vec, modelo = _embed_local(texto)
    if vec:
        return vec, modelo
    return _embed_gemini(texto)


def _modelo_de(entrada: dict) -> str:
    """Etiqueta de modelo de una entrada. Las viejas (pre-v10.2) eran de Gemini."""
    if not entrada.get("vec"):
        return ""
    return entrada.get("vec_modelo") or ("gemini:" + MODELO_EMBED_GEMINI)


def _coseno(a: list, b: list) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


def _similitud_palabras(q: str, texto: str) -> float:
    """Respaldo sin embeddings: solapamiento de palabras (Jaccard sencillo)."""
    a = set(_normalizar(q).split())
    b = set(_normalizar(texto).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def indexar(texto: str, role: str = "user") -> None:
    """Guarda un fragmento en la memoria de largo plazo con su embedding."""
    if not texto or not texto.strip():
        return
    limpio = texto.strip()
    if len(limpio) < 12:                       # demasiado corto para aportar
        return
    if _normalizar(limpio) in _TRIVIAL:
        return
    if limpio.startswith("["):                 # notas internas del sistema
        return
    if _normalizar(limpio).startswith(_RUIDO_INICIO):   # errores/relleno del asistente
        return
    # El embedding se calcula FUERA del lock (puede tardar; local ~0.1s, nube ~0.5s)
    vec, modelo = _embedding(limpio)
    with _lock:
        datos = _cargar()
        # Evitar duplicar lo último idéntico
        if datos and datos[-1].get("texto", "").strip() == limpio:
            return
        datos.append({
            "texto": limpio,
            "role": role,
            "fecha": datetime.now().isoformat(timespec="minutes"),
            "vec": vec,             # puede ser None si no hubo embeddings
            "vec_modelo": modelo,   # con qué modelo se calculó (no son mezclables)
        })
        if len(datos) > MAX_ENTRADAS:
            datos = datos[-MAX_ENTRADAS:]
        _guardar(datos)


def recordar(consulta: str, k: int = 4, umbral: float = 0.55) -> list:
    """
    Devuelve hasta k recuerdos más relacionados con 'consulta' por SIGNIFICADO.
    Cada recuerdo es {"texto","role","fecha","score"}. Lista vacía si no hay
    nada suficientemente relevante (para no meter ruido en el contexto).

    La consulta se embebe UNA vez por cada familia de modelo presente en la
    memoria (normalmente solo la local), y cada entrada se compara contra la
    versión de SU modelo. Entradas sin vector comparable -> similitud por palabras.
    """
    datos = _cargar()
    if not datos:
        return []

    # qvec por familia de modelo, calculado perezosamente (solo si hace falta)
    qvecs = {}          # etiqueta_modelo -> vector o None (None = no disponible)

    def _qvec_para(modelo: str):
        if modelo in qvecs:
            return qvecs[modelo]
        vec = None
        if modelo.startswith("local:"):
            vec, _ = _embed_local(consulta)
        elif modelo.startswith("gemini:"):
            vec, _ = _embed_gemini(consulta)
        qvecs[modelo] = vec
        return vec

    # Umbral por modelo, calibrado con pares reales en español:
    #   - embeddinggemma: relacionados ~0.54-0.68, ruido ~0.23-0.34 -> 0.45
    #   - Gemini (histórico): 0.55 (el parámetro 'umbral')
    #   - palabras (Jaccard): valores bajos por naturaleza -> 0.08
    puntuados = []
    for d in datos:
        modelo = _modelo_de(d)
        qv = _qvec_para(modelo) if modelo else None
        if qv and d.get("vec"):
            corte = 0.45 if modelo.startswith("local:") else umbral
            puntuados.append((_coseno(qv, d["vec"]), d, corte))
        else:
            puntuados.append((_similitud_palabras(consulta, d.get("texto", "")), d, 0.08))

    # Si hay entradas viejas de Gemini y ya existe el modelo local, migrarlas
    # en segundo plano (una vez por sesión) para dejar todo local y comparable.
    _migrar_en_fondo_si_hace_falta(datos)

    puntuados.sort(key=lambda x: x[0], reverse=True)
    salida = []
    for score, d, corte in puntuados[:k]:
        if score < corte:
            continue
        salida.append({"texto": d["texto"], "role": d.get("role", ""),
                       "fecha": d.get("fecha", ""), "score": round(score, 3)})
    return salida


def total() -> int:
    return len(_cargar())


def _migrar_en_fondo_si_hace_falta(datos: list) -> None:
    """Si hay entradas sin vector local y el modelo local está disponible,
    lanza UNA migración en hilo de fondo (no bloquea el turno)."""
    global _migracion_lanzada
    if _migracion_lanzada:
        return
    ll = _llm_local()
    if ll is None:
        return
    try:
        if not ll.embed_disponible():
            return
    except Exception:
        return
    etiqueta_local = "local:" + ll.modelo_embed()
    pendientes = any(_modelo_de(d) != etiqueta_local for d in datos)
    if not pendientes:
        return
    _migracion_lanzada = True
    threading.Thread(target=lambda: reindexar_embeddings(limite=MAX_ENTRADAS),
                     daemon=True, name="lara-migrar-memoria").start()


def reindexar_embeddings(limite: int = 200) -> int:
    """
    Recalcula embeddings de entradas que lo necesitan: las guardadas SIN vector
    (no había internet) y las de OTRO modelo (p. ej. Gemini) cuando el modelo
    local ya está disponible — así toda la memoria converge al modelo local.
    Los embeddings se calculan FUERA del lock (esto puede tardar minutos) y se
    aplican al final en una pasada corta, para no bloquear a indexar()/recordar().
    Devuelve cuántas entradas actualizó.
    """
    with _lock:
        datos = _cargar()
    ll = _llm_local()
    etiqueta_local = ""
    try:
        if ll is not None and ll.embed_disponible():
            etiqueta_local = "local:" + ll.modelo_embed()
    except Exception:
        pass

    nuevos = {}          # texto -> (vec, modelo)
    n = 0
    for d in datos:
        if n >= limite:
            break
        modelo_actual = _modelo_de(d)
        necesita = (not d.get("vec")) or (etiqueta_local and modelo_actual != etiqueta_local)
        if not necesita:
            continue
        vec, modelo = _embedding(d.get("texto", ""))
        if vec and modelo != modelo_actual:
            nuevos[d.get("texto", "")] = (vec, modelo)
            n += 1

    if not nuevos:
        return 0
    with _lock:
        datos = _cargar()      # releer: pudo cambiar mientras calculábamos
        aplicados = 0
        for d in datos:
            par = nuevos.get(d.get("texto", ""))
            if par:
                d["vec"], d["vec_modelo"] = par
                aplicados += 1
        if aplicados:
            _guardar(datos)
        return aplicados
