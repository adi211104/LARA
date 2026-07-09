"""
rag_documentos.py — RAG sobre TUS documentos del taller.

Indexa el CONTENIDO de tus archivos (PDF, Word, texto, Markdown) en una base
vectorial local y, cuando preguntas algo, recupera los fragmentos más
relevantes CITANDO el archivo de origen. Así LARA responde desde TODO tu
material, no solo desde lo que subes a mano en el chat.

Ejemplo:
    Tú: ¿cuánto cobré por el ampli Marshall?
    LARA: Según tu nota 'clientes_2025.docx', el cliente paga los viernes y el
          presupuesto fue S/ 350.

Diseño:
  - Embeddings LOCALES (Ollama embeddinggemma), reutilizando el mismo motor que
    la memoria (`core.memoria_vectorial`): rápido, gratis y offline. Si no hay
    modelo local, cae a búsqueda por palabras (como la memoria).
  - Almacenamiento en KNOWLEDGE_DIR/rag_documentos.sqlite3 (fragmentos + vectores).
  - Indexado INCREMENTAL: solo re-procesa archivos NUEVOS o MODIFICADOS (mtime);
    si borras un archivo, sus fragmentos se limpian en la siguiente pasada.
  - Carpetas por defecto: DOCUMENTOS_RAG y WORKSPACE. Puedes indexar otras a mano.
"""

import json
import sqlite3
import threading
from pathlib import Path

from config import KNOWLEDGE_DIR, DOCUMENTOS_RAG, WORKSPACE, CARPETAS_CONOCIDAS
from core.logger import log_error
# Reutilizamos el motor de embeddings ya probado de la memoria (local -> Gemini
# -> palabras) para no duplicar la lógica ni los umbrales.
from core.memoria_vectorial import _embedding, _embed_local, _coseno, _similitud_palabras

RUTA_DB = KNOWLEDGE_DIR / "rag_documentos.sqlite3"
EXTS = {".txt", ".md", ".pdf", ".docx"}
MAX_BYTES = 20 * 1024 * 1024      # ignora archivos enormes (>20 MB)
TAM_CHUNK = 700                    # caracteres por fragmento
SOLAPE = 120                       # solape entre fragmentos (para no cortar ideas)
_lock = threading.Lock()
_db_lista = False
_indexando = False                 # evita dos indexados simultáneos


# ── SQLite ────────────────────────────────────────────────────────────────────
def _conectar() -> sqlite3.Connection:
    global _db_lista
    RUTA_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(RUTA_DB), timeout=10)
    if not _db_lista:
        con.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ruta       TEXT NOT NULL,
                archivo    TEXT,
                idx        INTEGER,
                texto      TEXT,
                vec        TEXT,
                vec_modelo TEXT
            )""")
        con.execute("""
            CREATE TABLE IF NOT EXISTS archivos (
                ruta   TEXT PRIMARY KEY,
                mtime  REAL,
                chunks INTEGER
            )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_ruta ON chunks(ruta)")
        con.commit()
        _db_lista = True
    return con


# ── Extracción de texto ───────────────────────────────────────────────────────
def _extraer_texto(ruta: Path) -> str:
    ext = ruta.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return ruta.read_text(encoding="utf-8", errors="ignore")
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(ruta))
            return "\n".join((p.extract_text() or "") for p in reader.pages)
        if ext == ".docx":
            import docx
            d = docx.Document(str(ruta))
            return "\n".join(p.text for p in d.paragraphs)
    except Exception as e:
        log_error("rag_extraer", f"{ruta.name}: {e}")
    return ""


def _fragmentar(texto: str) -> list:
    """Parte el texto en fragmentos con solape, respetando párrafos cuando puede."""
    texto = (texto or "").strip()
    if not texto:
        return []
    chunks = []
    i = 0
    n = len(texto)
    while i < n:
        fin = min(i + TAM_CHUNK, n)
        # Intenta cortar en un salto de párrafo o frase cercano al final.
        if fin < n:
            corte = texto.rfind("\n", i + TAM_CHUNK // 2, fin)
            if corte == -1:
                corte = texto.rfind(". ", i + TAM_CHUNK // 2, fin)
            if corte != -1:
                fin = corte + 1
        frag = texto[i:fin].strip()
        if len(frag) >= 30:
            chunks.append(frag)
        if fin >= n:
            break
        i = max(fin - SOLAPE, i + 1)
    return chunks


# ── Indexado ──────────────────────────────────────────────────────────────────
def indexar_archivo(ruta) -> int:
    """(Re)indexa un archivo. Devuelve cuántos fragmentos guardó (0 si nada)."""
    ruta = Path(ruta)
    if not ruta.exists() or not ruta.is_file():
        return 0
    if ruta.suffix.lower() not in EXTS:
        return 0
    try:
        if ruta.stat().st_size > MAX_BYTES:
            return 0
        mtime = ruta.stat().st_mtime
    except OSError:
        return 0

    con = _conectar()
    try:
        fila = con.execute("SELECT mtime FROM archivos WHERE ruta = ?", (str(ruta),)).fetchone()
        if fila and abs(fila[0] - mtime) < 1e-6:
            return 0  # sin cambios desde la última vez
    finally:
        con.close()

    texto = _extraer_texto(ruta)
    fragmentos = _fragmentar(texto)
    if not fragmentos:
        return 0

    # Embeddings FUERA del lock (puede tardar).
    embebidos = []
    for frag in fragmentos:
        vec, modelo = _embedding(frag)
        embebidos.append((frag, vec, modelo))

    with _lock:
        con = _conectar()
        try:
            con.execute("DELETE FROM chunks WHERE ruta = ?", (str(ruta),))
            con.executemany(
                "INSERT INTO chunks(ruta, archivo, idx, texto, vec, vec_modelo) VALUES (?,?,?,?,?,?)",
                [(str(ruta), ruta.name, i, frag,
                  json.dumps(vec) if vec else None, modelo or "")
                 for i, (frag, vec, modelo) in enumerate(embebidos)])
            con.execute(
                "INSERT INTO archivos(ruta, mtime, chunks) VALUES (?,?,?) "
                "ON CONFLICT(ruta) DO UPDATE SET mtime=excluded.mtime, chunks=excluded.chunks",
                (str(ruta), mtime, len(embebidos)))
            con.commit()
        finally:
            con.close()
    return len(embebidos)


def _limpiar_borrados(con, rutas_vistas: set) -> int:
    """Quita de la base los archivos que ya no existen en disco."""
    borrados = 0
    rutas_db = [r[0] for r in con.execute("SELECT ruta FROM archivos").fetchall()]
    for r in rutas_db:
        if r not in rutas_vistas and not Path(r).exists():
            con.execute("DELETE FROM chunks WHERE ruta = ?", (r,))
            con.execute("DELETE FROM archivos WHERE ruta = ?", (r,))
            borrados += 1
    if borrados:
        con.commit()
    return borrados


def indexar_carpeta(carpeta) -> dict:
    """Indexa (incremental) todos los documentos soportados de una carpeta."""
    carpeta = Path(carpeta)
    if not carpeta.exists():
        return {"error": f"No existe la carpeta {carpeta}"}
    nuevos_chunks = 0
    archivos_procesados = 0
    vistas = set()
    for ruta in carpeta.rglob("*"):
        if ruta.is_file() and ruta.suffix.lower() in EXTS:
            vistas.add(str(ruta))
            c = indexar_archivo(ruta)
            if c:
                archivos_procesados += 1
                nuevos_chunks += c
    con = _conectar()
    try:
        borrados = _limpiar_borrados(con, vistas)
    finally:
        con.close()
    return {"carpeta": str(carpeta), "archivos_actualizados": archivos_procesados,
            "fragmentos_nuevos": nuevos_chunks, "archivos_borrados": borrados}


def indexar_todo(carpetas=None) -> dict:
    """Indexa las carpetas por defecto (DOCUMENTOS_RAG + WORKSPACE) u otras dadas."""
    global _indexando
    if _indexando:
        return {"estado": "ya en curso"}
    _indexando = True
    try:
        carpetas = carpetas or [DOCUMENTOS_RAG, WORKSPACE]
        total = {"archivos_actualizados": 0, "fragmentos_nuevos": 0, "archivos_borrados": 0}
        for c in carpetas:
            r = indexar_carpeta(c)
            for k in total:
                total[k] += r.get(k, 0)
        return total
    finally:
        _indexando = False


def indexar_todo_async() -> None:
    """Lanza el indexado en segundo plano (para el arranque). No bloquea."""
    def _run():
        try:
            indexar_todo()
        except Exception as e:
            log_error("rag_indexar_async", str(e))
    threading.Thread(target=_run, daemon=True, name="lara-rag-index").start()


def resolver_carpeta(nombre: str) -> Path:
    """'documentos', 'escritorio', o una ruta -> Path (usa las carpetas conocidas)."""
    nombre = (nombre or "").strip().strip('"').strip("'")
    clave = nombre.lower()
    if clave in CARPETAS_CONOCIDAS:
        return CARPETAS_CONOCIDAS[clave]
    p = Path(nombre)
    return p if p.is_absolute() else (DOCUMENTOS_RAG / nombre)


# ── Búsqueda ──────────────────────────────────────────────────────────────────
def buscar(consulta: str, k: int = 5, umbral_local: float = 0.42) -> list:
    """Fragmentos más relevantes para 'consulta'. Cada uno: {texto, archivo, ruta, score}."""
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    con = _conectar()
    try:
        filas = con.execute(
            "SELECT texto, archivo, ruta, vec, vec_modelo FROM chunks").fetchall()
    except Exception:
        return []
    finally:
        con.close()
    if not filas:
        return []

    qvec, qmodelo = _embedding(consulta)
    puntuados = []
    for texto, archivo, ruta, vec, vec_modelo in filas:
        v = json.loads(vec) if vec else None
        if qvec and v and vec_modelo == qmodelo:
            score, corte = _coseno(qvec, v), umbral_local
        else:
            score, corte = _similitud_palabras(consulta, texto or ""), 0.08
        puntuados.append((score, corte, texto, archivo, ruta))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    salida = []
    for score, corte, texto, archivo, ruta in puntuados[:k]:
        if score < corte:
            continue
        salida.append({"texto": texto, "archivo": archivo, "ruta": ruta,
                       "score": round(score, 3)})
    return salida


def estado() -> dict:
    con = _conectar()
    try:
        (na,) = con.execute("SELECT COUNT(*) FROM archivos").fetchone()
        (nc,) = con.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return {"archivos": na, "fragmentos": nc}
    finally:
        con.close()
