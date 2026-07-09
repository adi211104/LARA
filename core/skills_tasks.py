"""
skills_tasks.py
Gestión persistente de tareas, clientes, compras y proyectos para LARA.
Guarda todo como JSON dentro de KNOWLEDGE_DIR (definido en config.py).
Colócalo junto a tus otros módulos (mismo paquete 'core' que skills_os.py).
"""

import json
from pathlib import Path
from datetime import datetime
from config import KNOWLEDGE_DIR

DB_TAREAS    = KNOWLEDGE_DIR / "tareas.json"
DB_CLIENTES  = KNOWLEDGE_DIR / "clientes.json"
DB_COMPRAS   = KNOWLEDGE_DIR / "compras.json"
DB_PROYECTOS = KNOWLEDGE_DIR / "proyectos.json"


def _cargar(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _guardar(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- TAREAS ----------------

def tarea_crear(titulo: str, fecha_limite: str = "", prioridad: str = "media", proyecto: str = "") -> dict:
    """fecha_limite en formato YYYY-MM-DD"""
    tareas = _cargar(DB_TAREAS)
    nueva = {
        "id": (max((t["id"] for t in tareas), default=0) + 1),
        "titulo": titulo,
        "fecha_limite": fecha_limite,
        "prioridad": prioridad,
        "proyecto": proyecto,
        "estado": "pendiente",
        "creada": datetime.now().isoformat(timespec="minutes"),
    }
    tareas.append(nueva)
    _guardar(DB_TAREAS, tareas)
    return {"estado": "ok", "tarea": nueva}


def tarea_listar(filtro_estado: str = "") -> dict:
    tareas = _cargar(DB_TAREAS)
    if filtro_estado:
        tareas = [t for t in tareas if t["estado"] == filtro_estado]
    return {"tareas": tareas}


def tarea_completar(id_tarea: int) -> dict:
    tareas = _cargar(DB_TAREAS)
    for t in tareas:
        if t["id"] == id_tarea:
            t["estado"] = "completada"
            _guardar(DB_TAREAS, tareas)
            return {"estado": "ok", "tarea": t}
    return {"error": "Tarea no encontrada"}


def tarea_eliminar(id_tarea: int) -> dict:
    tareas = _cargar(DB_TAREAS)
    nuevas = [t for t in tareas if t["id"] != id_tarea]
    if len(nuevas) == len(tareas):
        return {"error": "Tarea no encontrada"}
    _guardar(DB_TAREAS, nuevas)
    return {"estado": "ok"}


def tareas_pendientes_urgentes() -> list:
    """Usado por el agente de fondo: tareas vencidas o que vencen hoy/mañana."""
    tareas = _cargar(DB_TAREAS)
    hoy = datetime.now().date()
    urgentes = []
    for t in tareas:
        if t["estado"] != "pendiente" or not t.get("fecha_limite"):
            continue
        try:
            fecha = datetime.fromisoformat(t["fecha_limite"]).date()
        except ValueError:
            continue
        dias = (fecha - hoy).days
        if dias <= 1:
            urgentes.append({**t, "dias_restantes": dias})
    return urgentes


# ---------------- CLIENTES ----------------

def cliente_crear(nombre: str, contacto: str = "", notas: str = "") -> dict:
    clientes = _cargar(DB_CLIENTES)
    nuevo = {"id": (max((c["id"] for c in clientes), default=0) + 1),
             "nombre": nombre, "contacto": contacto, "notas": notas}
    clientes.append(nuevo)
    _guardar(DB_CLIENTES, clientes)
    return {"estado": "ok", "cliente": nuevo}


def cliente_listar() -> dict:
    return {"clientes": _cargar(DB_CLIENTES)}


# ---------------- COMPRAS ----------------

def compra_crear(item: str, proveedor: str = "", precio: str = "", estado: str = "pendiente") -> dict:
    compras = _cargar(DB_COMPRAS)
    nueva = {"id": (max((c["id"] for c in compras), default=0) + 1),
             "item": item, "proveedor": proveedor, "precio": precio, "estado": estado}
    compras.append(nueva)
    _guardar(DB_COMPRAS, compras)
    return {"estado": "ok", "compra": nueva}


def compra_listar(filtro_estado: str = "") -> dict:
    compras = _cargar(DB_COMPRAS)
    if filtro_estado:
        compras = [c for c in compras if c["estado"] == filtro_estado]
    return {"compras": compras}


def compra_actualizar_estado(id_compra: int, nuevo_estado: str) -> dict:
    compras = _cargar(DB_COMPRAS)
    for c in compras:
        if c["id"] == id_compra:
            c["estado"] = nuevo_estado
            _guardar(DB_COMPRAS, compras)
            return {"estado": "ok", "compra": c}
    return {"error": "Compra no encontrada"}


# ---------------- PROYECTOS ----------------

def proyecto_crear(nombre: str, cliente: str = "", descripcion: str = "") -> dict:
    proyectos = _cargar(DB_PROYECTOS)
    nuevo = {"id": (max((p["id"] for p in proyectos), default=0) + 1),
             "nombre": nombre, "cliente": cliente, "descripcion": descripcion, "estado": "activo"}
    proyectos.append(nuevo)
    _guardar(DB_PROYECTOS, proyectos)
    return {"estado": "ok", "proyecto": nuevo}


def proyecto_listar(filtro_estado: str = "") -> dict:
    proyectos = _cargar(DB_PROYECTOS)
    if filtro_estado:
        proyectos = [p for p in proyectos if p["estado"] == filtro_estado]
    return {"proyectos": proyectos}
