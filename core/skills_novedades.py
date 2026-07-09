"""
skills_novedades.py
- Cola rotativa de temas que el agente de fondo investiga de a poco (no todo de golpe).
- Registro de "novedades" encontradas (modelos, librerías, skills, herramientas).
- Registro de "limitaciones" detectadas en uso real (ej: brillo no funciona en monitor
  externo) para que el agente investigue solución y proponga código.
"""

import json
from pathlib import Path
from datetime import datetime
from config import KNOWLEDGE_DIR

NOVEDADES = KNOWLEDGE_DIR / "novedades.json"
LIMITACIONES = KNOWLEDGE_DIR / "limitaciones.json"
ESTADO_INVESTIGACION = KNOWLEDGE_DIR / "estado_investigacion.json"

# Cola de temas. El agente avanza por aquí de a poco en cada ciclo y vuelve
# al inicio al llegar al final (rotación infinita). Agrega/quita libremente.
TEMAS_INVESTIGACION = [
    "nuevos modelos LLM open source ligeros 2026",
    "modelos disponibles en Groq recientes",
    "librerias Python automatizacion Windows",
    "control brillo monitor externo Windows DDC CI",
    "APIs gratuitas reconocimiento de voz",
    "herramientas diagnostico electronico con software",
    "busqueda automatica de datasheets componentes",
    "alternativas a yt-dlp para descargas",
    "control de volumen Windows desde Python",
    "control de bateria laptop desde PowerShell",
    "frameworks de agentes IA con herramientas",
    "bases de datos vectoriales ligeras en Python",
    "OCR gratuito en Python para datasheets",
    "texto a voz gratuito en español",
    "monitoreo de procesos Windows con Python",
    "software de inventario open source",
    "generacion automatica de reportes PDF en Python",
    "notificaciones push gratuitas para Python",
    "scraping de precios de componentes electronicos",
    "comparadores de precios con API",
    "automatizacion de correo electronico en Python",
    "deteccion de fallas en circuitos con software",
    "simuladores de circuitos open source",
    "calculadoras electronicas open source",
    "software de gestion de citas para clientes",
    "facturacion electronica Peru API",
    "mejoras recientes a modelos Llama de Meta",
    "nuevas herramientas de Groq compound",
    "seguridad en agentes de IA que ejecutan comandos",
]


def _cargar(path: Path) -> list:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _guardar(path: Path, data: list) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- NOVEDADES ----------------

def guardar_novedad(tema: str, hallazgo: str) -> dict:
    novedades = _cargar(NOVEDADES)
    nueva = {
        "id": max((n["id"] for n in novedades), default=0) + 1,
        "tema": tema,
        "hallazgo": hallazgo,
        "fecha": datetime.now().isoformat(timespec="minutes"),
        "leida": False,
    }
    novedades.append(nueva)
    _guardar(NOVEDADES, novedades)
    return nueva


def listar_novedades(solo_no_leidas: bool = False) -> list:
    novedades = _cargar(NOVEDADES)
    if solo_no_leidas:
        novedades = [n for n in novedades if not n["leida"]]
    return novedades


def marcar_novedades_leidas() -> None:
    novedades = _cargar(NOVEDADES)
    for n in novedades:
        n["leida"] = True
    _guardar(NOVEDADES, novedades)


# ---------------- COLA ROTATIVA DE TEMAS ----------------

def proximos_temas(cantidad: int = 3) -> list:
    estado = _cargar(ESTADO_INVESTIGACION)
    inicio = estado[0]["indice"] if estado else 0
    total = len(TEMAS_INVESTIGACION)

    indices = [(inicio + i) % total for i in range(cantidad)]
    nuevo_indice = (inicio + cantidad) % total
    _guardar(ESTADO_INVESTIGACION, [{"indice": nuevo_indice}])

    return [TEMAS_INVESTIGACION[i] for i in indices]


# ---------------- LIMITACIONES DETECTADAS ----------------

def registrar_limitacion(skill: str, descripcion: str) -> dict:
    limitaciones = _cargar(LIMITACIONES)
    for l in limitaciones:
        if l["skill"] == skill and l["estado"] == "pendiente":
            return l  # ya está en cola, no duplicar

    nueva = {
        "id": max((l["id"] for l in limitaciones), default=0) + 1,
        "skill": skill,
        "descripcion": descripcion,
        "estado": "pendiente",
        "fecha": datetime.now().isoformat(timespec="minutes"),
    }
    limitaciones.append(nueva)
    _guardar(LIMITACIONES, limitaciones)
    return nueva


def listar_limitaciones_pendientes() -> list:
    return [l for l in _cargar(LIMITACIONES) if l["estado"] == "pendiente"]


def marcar_limitacion_resuelta(id_limitacion: int) -> None:
    limitaciones = _cargar(LIMITACIONES)
    for l in limitaciones:
        if l["id"] == id_limitacion:
            l["estado"] = "propuesta_generada"
    _guardar(LIMITACIONES, limitaciones)
