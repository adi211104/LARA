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

Almacenamiento (v11: SQLite en vez de un JSON monolítico):
  - Antes TODO vivía en un único memoria_vectorial.json que se REESCRÍBIA ENTERO
    en cada guardado (a miles de recuerdos, escribir 4000 entradas por cada
    turno es lento y arriesga corromper el archivo si se corta a medias).
  - Ahora cada recuerdo es una fila en KNOWLEDGE_DIR/memoria_vectorial.sqlite3:
    insertar es una sola fila, buscar es un SELECT, y el archivo no se reescribe
    completo nunca. El JSON antiguo se MIGRA automáticamente la primera vez.
  - Dedup: no se guardan textos idénticos ya presentes.

Diseño de embeddings (sin cambios respecto a v10.2):
  - Embeddings con Ollama (embeddinggemma, multilingüe) EN TU PC primero.
  - Si el modelo local no está, cae a Gemini (text-embedding-004).
  - Si no hay ninguno, cae a búsqueda por solapamiento de palabras.
  - Vectores de modelos distintos NO son comparables (espacios diferentes):
    cada fila guarda con qué modelo se calculó ("vec_modelo") y solo se compara
    contra la consulta embebida con ESE mismo modelo. Las entradas viejas
    (Gemini) se migran al modelo local en segundo plano.
"""

import json
import math
import sqlite3
import threading
import unicodedata
from datetime import datetime

from config import KNOWLEDGE_DIR

RUTA_DB = KNOWLEDGE_DIR / "memoria_vectorial.sqlite3"
RUTA_JSON_LEGADO = KNOWLEDGE_DIR / "memoria_vectorial.json"   # se migra si existe
MODELO_EMBED_GEMINI = "text-embedding-004"
MAX_ENTRADAS = 4000        # tope: la memoria no crece infinita
_lock = threading.Lock()
_embed_estado_gemini = None    # None=por probar, True=ok, False=no disponible
_migracion_lanzada = False     # migrar gemini->local solo una vez por sesión
_db_lista = False              # esquema creado + JSON legado importado

# Frases sin valor para recordar (ruido conversacional). No se indexan.
_TRIVIAL = {"ok", "vale", "gracias", "hola", "adios", "adiós", "chau", "si", "sí",
            "no", "ya", "listo", "bien", "dale", "aja", "ajá", "mmm", "eh"}

# Respuestas-error o de relleno del propio asistente: recordarlas solo mete
# ruido en las búsquedas ("No tengo ese dato exacto." aparecía como recuerdo).
_RUIDO_INICIO = ("no tengo ese dato", "no tengo el dato", "error", "tuve un problema",
                 "las ia están saturadas", "las ia estan saturadas", "no pude",
                 "lo siento", "no encontré resultados", "no encontre resultados")


# ── Almacenamiento SQLite ─────────────────────────────────────────────────────
def _conectar() -> sqlite3.Connection:
    """Abre una conexión nueva (thread-safe: una por operación). Crea el esquema
    y migra el JSON antiguo la primera vez."""
    global _db_lista
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(RUTA_DB), timeout=10)
    if not _db_lista:
        con.execute("""
            CREATE TABLE IF NOT EXISTS recuerdos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                texto      TEXT NOT NULL,
                role       TEXT,
                fecha      TEXT,
                vec        TEXT,          -- vector como JSON, o NULL
                vec_modelo TEXT
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_texto ON recuerdos(texto)")
        con.commit()
        _importar_json_legado(con)
        _db_lista = True
    return con


def _importar_json_legado(con: sqlite3.Connection) -> None:
    """Si existe el memoria_vectorial.json viejo y la tabla está vacía, lo
    importa una vez y renombra el JSON a .migrado para no reimportar."""
    try:
        if not RUTA_JSON_LEGADO.exists():
            return
        (n,) = con.execute("SELECT COUNT(*) FROM recuerdos").fetchone()
        if n > 0:
            return
        datos = json.loads(RUTA_JSON_LEGADO.read_text(encoding="utf-8"))
        filas = []
        for d in datos:
            vec = d.get("vec")
            filas.append((
                d.get("texto", ""), d.get("role", ""), d.get("fecha", ""),
                json.dumps(vec) if vec else None, d.get("vec_modelo") or "",
            ))
        con.executemany(
            "INSERT INTO recuerdos(texto, role, fecha, vec, vec_modelo) VALUES (?,?,?,?,?)",
            filas)
        con.commit()
        RUTA_JSON_LEGADO.rename(RUTA_JSON_LEGADO.with_suffix(".json.migrado"))
        print(f"[LARA] Memoria migrada a SQLite: {len(filas)} recuerdos importados.")
    except Exception as e:
        print(f"[LARA] No pude migrar la memoria antigua: {e}")


def _fila_a_dict(row) -> dict:
    idv, texto, role, fecha, vec, vec_modelo = row
    return {
        "id": idv, "texto": texto, "role": role or "", "fecha": fecha or "",
        "vec": json.loads(vec) if vec else None, "vec_modelo": vec_modelo or "",
    }


def _cargar() -> list:
    """Todos los recuerdos como lista de dicts (misma forma que la versión JSON)."""
    try:
        con = _conectar()
        try:
            rows = con.execute(
                "SELECT id, texto, role, fecha, vec, vec_modelo FROM recuerdos ORDER BY id"
            ).fetchall()
            return [_fila_a_dict(r) for r in rows]
        finally:
            con.close()
    except Exception:
        return []


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


def indexar(texto: str, role: str = "user", *, generar_embedding: bool = True) -> None:
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
    vec, modelo = _embedding(limpio) if generar_embedding else (None, None)
    with _lock:
        try:
            con = _conectar()
        except Exception:
            return
        try:
            # Dedup: si ese texto EXACTO ya está, no lo repetimos.
            existe = con.execute(
                "SELECT 1 FROM recuerdos WHERE texto = ? LIMIT 1", (limpio,)).fetchone()
            if existe:
                return
            con.execute(
                "INSERT INTO recuerdos(texto, role, fecha, vec, vec_modelo) VALUES (?,?,?,?,?)",
                (limpio, role, datetime.now().isoformat(timespec="minutes"),
                 json.dumps(vec) if vec else None, modelo or ""))
            # Enforzar el tope: borrar los más viejos si pasamos de MAX_ENTRADAS.
            (n,) = con.execute("SELECT COUNT(*) FROM recuerdos").fetchone()
            if n > MAX_ENTRADAS:
                con.execute(
                    "DELETE FROM recuerdos WHERE id IN "
                    "(SELECT id FROM recuerdos ORDER BY id LIMIT ?)", (n - MAX_ENTRADAS,))
            con.commit()
        finally:
            con.close()


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
    try:
        con = _conectar()
        try:
            (n,) = con.execute("SELECT COUNT(*) FROM recuerdos").fetchone()
            return n
        finally:
            con.close()
    except Exception:
        return 0


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
    aplican por fila (UPDATE por id). Devuelve cuántas entradas actualizó.
    """
    datos = _cargar()
    ll = _llm_local()
    etiqueta_local = ""
    try:
        if ll is not None and ll.embed_disponible():
            etiqueta_local = "local:" + ll.modelo_embed()
    except Exception:
        pass

    nuevos = {}          # id -> (vec, modelo)
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
            nuevos[d["id"]] = (vec, modelo)
            n += 1

    if not nuevos:
        return 0
    with _lock:
        try:
            con = _conectar()
        except Exception:
            return 0
        try:
            for idv, (vec, modelo) in nuevos.items():
                con.execute("UPDATE recuerdos SET vec = ?, vec_modelo = ? WHERE id = ?",
                            (json.dumps(vec), modelo, idv))
            con.commit()
            return len(nuevos)
        finally:
            con.close()
