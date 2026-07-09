"""
skills_memoria.py
Memoria de continuidad entre sesiones de LARA. Dos capas:

1. HISTORIAL EN DISCO: cada turno (usuario/LARA) se guarda en un JSON. Al
   reiniciar el programa, se cargan los últimos turnos, así LARA no empieza
   de cero cada vez que abres el programa.

2. COMPACTACIÓN: dentro de una sesión muy larga, en vez de simplemente
   "olvidar" los turnos viejos cuando se acumulan demasiados (como pasaba
   antes con historial[-10:]), se resumen en 2-4 hechos concretos ANTES de
   descartarlos, y esos hechos se guardan permanentemente en la base de
   conocimiento (skills_conocimiento.py) para no perder la información real,
   solo su forma "conversación palabra por palabra".
"""

import json
from pathlib import Path
from datetime import datetime
from config import KNOWLEDGE_DIR

HISTORIAL_DISCO = KNOWLEDGE_DIR / "historial_persistente.json"
MAX_TURNOS_DISCO = 300  # tope de turnos guardados en disco (no crece sin límite)


def _cargar() -> list:
    if HISTORIAL_DISCO.exists():
        try:
            return json.loads(HISTORIAL_DISCO.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _guardar(data: list) -> None:
    HISTORIAL_DISCO.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def guardar_turno(role: str, content: str) -> None:
    """Persiste un turno de la conversación en disco, con tope de tamaño."""
    turnos = _cargar()
    turnos.append({"role": role, "content": content, "fecha": datetime.now().isoformat(timespec="minutes")})
    if len(turnos) > MAX_TURNOS_DISCO:
        turnos = turnos[-MAX_TURNOS_DISCO:]
    _guardar(turnos)
    # Memoria semántica de largo plazo (Mejora 4): además del historial lineal,
    # indexamos el turno por SIGNIFICADO para poder recuperarlo luego aunque se
    # pregunte con otras palabras. Va aislado en try: si falla, no afecta al
    # guardado normal del historial.
    try:
        from core.memoria_vectorial import indexar
        indexar(content, role)
    except Exception:
        pass


def cargar_historial_reciente(n: int = 10) -> list:
    """
    Devuelve los últimos n turnos en el formato que usa main.py/llm_engine.py:
    [{"role": "user"/"assistant", "content": "..."}]
    Se usa al arrancar el programa para no empezar en blanco cada sesión.
    """
    turnos = _cargar()
    recientes = turnos[-n:] if turnos else []
    return [{"role": t["role"], "content": t["content"]} for t in recientes]


def total_turnos_guardados() -> int:
    return len(_cargar())
