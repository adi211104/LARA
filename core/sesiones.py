"""
sesiones.py — Chats múltiples con historial navegable.

Antes LARA guardaba TODO en un único archivo plano (historial_persistente.json).
Ahora cada conversación es una "sesión" independiente, con su propio archivo,
título y fechas. Al abrir LARA se crea una sesión nueva, y en la barra lateral
se puede volver a cualquier sesión anterior para recuperar su contexto.

Hay dos "espacios" separados:
  - "chat" : conversaciones normales con LARA.
  - "code" : sesiones del modo Open Code (programación autónoma).

Estructura en disco (dentro de KNOWLEDGE_DIR, sincronizado por OneDrive):
  sesiones/<espacio>/<id>.json  ->  {id, titulo, creado, actualizado, turnos:[...]}

La primera vez migra el historial plano antiguo a una sesión "Sesiones anteriores".
"""

import json
import uuid
import threading
from pathlib import Path
from datetime import datetime

from config import KNOWLEDGE_DIR

RAIZ = KNOWLEDGE_DIR / "sesiones"
_lock = threading.RLock()


def guardar_estado_codigo(sid, carpeta, pendiente, artefactos=None):
    with _lock:
        data = cargar_sesion('code', sid) or {'id': sid, 'titulo': 'Code: ' + Path(carpeta).name,
                                            'creado': _ahora(), 'turnos': []}
        data['carpeta'] = str(carpeta)
        data['pendiente'] = pendiente
        if artefactos is not None:
            data['artefactos'] = artefactos
        data['actualizado'] = _ahora()
        _guardar_atomico(_ruta('code', sid), data)


def _guardar_atomico(ruta, data):
    temporal = ruta.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    try:
        temporal.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporal.replace(ruta)
    finally:
        temporal.unlink(missing_ok=True)


def _dir_espacio(espacio: str) -> Path:
    d = RAIZ / espacio
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ruta(espacio: str, sid: str) -> Path:
    return _dir_espacio(espacio) / f"{sid}.json"


def _ahora() -> str:
    return datetime.now().isoformat(timespec="minutes")


# ──────────────────────────────────────────────
#  Crear / cargar / guardar
# ──────────────────────────────────────────────

# Títulos iniciales pendientes (sesión creada pero aún sin archivo en disco)
_titulos_pendientes = {}


def crear_sesion(espacio: str = "chat", titulo: str = "") -> str:
    """
    Crea una sesión NUEVA pero NO escribe el archivo hasta que tenga el primer
    turno. Así no se acumulan conversaciones vacías en la lista de historial.
    """
    _migrar_si_hace_falta()
    sid = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
    if titulo:
        _titulos_pendientes[sid] = titulo
    return sid


def cargar_sesion(espacio: str, sid: str) -> dict:
    p = _ruta(espacio, sid)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def turnos_como_historial(espacio: str, sid: str, n: int = 12) -> list:
    """Devuelve los últimos n turnos en formato [{'role','content'}] para el cerebro."""
    data = cargar_sesion(espacio, sid)
    turnos = data.get("turnos", [])[-n:]
    return [{"role": t["role"], "content": t["content"]} for t in turnos]


def guardar_turno(espacio: str, sid: str, role: str, content: str) -> None:
    p = _ruta(espacio, sid)
    data = cargar_sesion(espacio, sid)
    if not data:
        data = {"id": sid, "titulo": _titulos_pendientes.pop(sid, "Nueva conversación"),
                "creado": _ahora(), "turnos": []}
    data["turnos"].append({"role": role, "content": content, "fecha": _ahora()})
    data["actualizado"] = _ahora()
    # Auto-título con el primer mensaje del usuario
    if data.get("titulo") in ("", "Nueva conversación") and role == "user":
        data["titulo"] = content.strip().replace("\n", " ")[:48] or "Conversación"
    _guardar_atomico(p, data)

    # Memoria semántica de largo plazo (Mejora 4): indexamos las conversaciones
    # normales por SIGNIFICADO para poder recuperarlas luego aunque se pregunte
    # con otras palabras. El espacio "code" (Open Code) no se indexa. Aislado en
    # try: si falla, el guardado de la sesión no se ve afectado.
    if espacio == "chat":
        try:
            from core.memoria_vectorial import indexar
            # Guardar el chat nunca espera redes ni inferencia. La recuperación
            # léxica sigue disponible; reindexar_embeddings enriquece después.
            indexar(content, role, generar_embedding=False)
        except Exception:
            pass


def renombrar_sesion(espacio: str, sid: str, titulo: str) -> None:
    data = cargar_sesion(espacio, sid)
    if data:
        data["titulo"] = titulo[:60]
        _ruta(espacio, sid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def eliminar_sesion(espacio: str, sid: str) -> bool:
    p = _ruta(espacio, sid)
    if p.exists():
        try:
            p.unlink()
            return True
        except Exception:
            return False
    return False


def listar_sesiones(espacio: str = "chat") -> list:
    """Lista sesiones del espacio, más reciente primero."""
    _migrar_si_hace_falta()
    out = []
    for p in _dir_espacio(espacio).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id", p.stem),
                "titulo": d.get("titulo", "Conversación"),
                "actualizado": d.get("actualizado", d.get("creado", "")),
                "n_turnos": len(d.get("turnos", [])),
            })
        except Exception:
            continue
    out.sort(key=lambda s: s["actualizado"], reverse=True)
    return out


# ──────────────────────────────────────────────
#  Migración del historial plano antiguo
# ──────────────────────────────────────────────

def _migrar_si_hace_falta() -> None:
    viejo = KNOWLEDGE_DIR / "historial_persistente.json"
    marca = RAIZ / ".migrado"
    if marca.exists() or not viejo.exists():
        return
    try:
        turnos = json.loads(viejo.read_text(encoding="utf-8"))
    except Exception:
        turnos = []
    RAIZ.mkdir(parents=True, exist_ok=True)
    if turnos:
        sid = "20000101_000000_old0"
        data = {
            "id": sid,
            "titulo": "Sesiones anteriores (archivo)",
            "creado": _ahora(),
            "actualizado": _ahora(),
            "turnos": [{"role": t.get("role", "user"), "content": t.get("content", ""),
                        "fecha": t.get("fecha", "")} for t in turnos],
        }
        (RAIZ / "chat").mkdir(parents=True, exist_ok=True)
        _ruta("chat", sid).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    marca.write_text("ok", encoding="utf-8")
