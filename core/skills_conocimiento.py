"""
skills_conocimiento.py
Base de conocimiento técnico de LARA (reparaciones, datasheets, notas).

Clave del diseño: la búsqueda NO lee todos los documentos guardados.
Primero compara la consulta contra un ÍNDICE liviano (titulo/categoria/tags)
que cabe siempre en memoria y no gasta tokens de LLM. Solo si hay coincidencia
real, carga el contenido de esos 1-2 archivos puntuales para inyectarlo al
contexto. Así LARA "sabe dónde buscar" sin leer su biblioteca completa cada vez.
"""

import json
from pathlib import Path
from datetime import datetime
from config import KNOWLEDGE_DIR

BASE_DIR = KNOWLEDGE_DIR / "base"
BASE_DIR.mkdir(parents=True, exist_ok=True)
INDICE = KNOWLEDGE_DIR / "indice_conocimiento.json"


def _cargar_indice() -> list:
    if INDICE.exists():
        try:
            return json.loads(INDICE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _guardar_indice(data: list) -> None:
    INDICE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def guardar_conocimiento(titulo: str, contenido: str, categoria: str = "general", tags: str = "") -> dict:
    """
    tags: string separado por comas, ej: "tv lg, driver, pantalla parpadea"
    """
    indice = _cargar_indice()
    nuevo_id = max((e["id"] for e in indice), default=0) + 1
    nombre_archivo = f"{nuevo_id}_{titulo[:40].replace(' ', '_').replace('/', '_')}.txt"
    ruta = BASE_DIR / nombre_archivo
    ruta.write_text(contenido, encoding="utf-8")

    entrada = {
        "id": nuevo_id,
        "titulo": titulo,
        "categoria": categoria,
        "tags": [t.strip().lower() for t in tags.split(",") if t.strip()],
        "archivo": nombre_archivo,
        "creado": datetime.now().isoformat(timespec="minutes"),
    }
    indice.append(entrada)
    _guardar_indice(indice)
    return {"estado": "ok", "id": nuevo_id, "archivo": str(ruta)}


def _puntuar(entrada: dict, palabras_query: list) -> int:
    texto_indexable = (entrada["titulo"] + " " + entrada["categoria"] + " " + " ".join(entrada["tags"])).lower()
    return sum(1 for p in palabras_query if p in texto_indexable)


def buscar_conocimiento(query: str, max_resultados: int = 2, min_score: int = 1) -> list:
    """
    Paso 1 (gratis): puntúa cada entrada del índice contra palabras de la query.
    Paso 2 (solo si hay match): carga el contenido SOLO de los mejores resultados.
    """
    indice = _cargar_indice()
    if not indice:
        return []

    palabras_query = [p.lower() for p in query.split() if len(p) > 3]
    if not palabras_query:
        return []

    candidatos = []
    for entrada in indice:
        score = _puntuar(entrada, palabras_query)
        if score >= min_score:
            candidatos.append((score, entrada))

    candidatos.sort(key=lambda x: x[0], reverse=True)

    resultados = []
    for score, entrada in candidatos[:max_resultados]:
        ruta = BASE_DIR / entrada["archivo"]
        contenido = ruta.read_text(encoding="utf-8") if ruta.exists() else ""
        if len(contenido) > 1200:
            contenido = contenido[:1200] + "\n... [TRUNCADO]"
        resultados.append({"titulo": entrada["titulo"], "categoria": entrada["categoria"], "contenido": contenido})
    return resultados


def listar_conocimiento(categoria: str = "") -> dict:
    indice = _cargar_indice()
    if categoria:
        indice = [e for e in indice if e["categoria"] == categoria]
    return {"entradas": [{"id": e["id"], "titulo": e["titulo"], "categoria": e["categoria"], "tags": e["tags"]} for e in indice]}
