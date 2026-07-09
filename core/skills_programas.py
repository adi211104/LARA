"""
skills_programas.py — Apertura INTELIGENTE de programas.

El viejo abrir_programa solo lanzaba el texto literal ("camara") y fallaba si la
app real se llamaba distinto ("Cámara", "Camera", "WinCamera"...). Esto indexa
TODO lo que el usuario puede abrir y hace coincidencia difusa:

Fuentes del índice:
  1. Get-StartApps  -> todas las apps del menú inicio (UWP + escritorio) con su AppID.
  2. Accesos directos (.lnk) del menú inicio (sistema y usuario).
  3. Ejecutables/.lnk del Escritorio y Descargas (programas portables que no se instalan).

Búsqueda: normaliza (quita tildes y palabras como "abre/mi/programa"), aplica
sinónimos español↔inglés, y combina coincidencia exacta + subcadena + difusa
(difflib). Devuelve el mejor candidato y alternativas.

El índice se cachea en KNOWLEDGE_DIR/programas_index.json y se reconstruye solo
si tiene más de 1 día o si se fuerza.
"""

import os
import json
import time
import subprocess
import unicodedata
from pathlib import Path
from difflib import SequenceMatcher

from config import KNOWLEDGE_DIR, ESCRITORIO, DESCARGAS

INDICE_PATH = KNOWLEDGE_DIR / "programas_index.json"
CADUCIDAD_SEG = 24 * 3600

# Sinónimos / alias frecuentes (consulta normalizada -> términos a buscar)
SINONIMOS = {
    "camara": ["camera", "windows camera", "camara"],
    "navegador": ["edge", "chrome", "firefox", "browser"],
    "explorador": ["explorer", "file explorer", "explorador de archivos"],
    "calculadora": ["calc", "calculator", "calculadora"],
    "bloc de notas": ["notepad", "bloc de notas"],
    "notas": ["notepad", "sticky notes", "notas rapidas"],
    "terminal": ["terminal", "cmd", "powershell", "windows terminal"],
    "musica": ["spotify", "groove", "media player", "musica"],
    "fotos": ["photos", "fotos"],
    "configuracion": ["settings", "configuracion"],
    "ajustes": ["settings", "configuracion"],
    "word": ["word", "winword"],
    "excel": ["excel"],
    "codigo": ["visual studio code", "code"],
    "vscode": ["visual studio code", "code"],
    "paint": ["paint", "paint 3d"],
    "tienda": ["microsoft store", "store", "tienda"],
    "correo": ["mail", "outlook", "correo"],
    "reloj": ["clock", "alarms", "reloj"],
}

# Palabras de relleno a quitar de la consulta del usuario
_RELLENO = {"abre", "abrir", "abreme", "ábreme", "inicia", "ejecuta", "lanza", "arranca",
            "el", "la", "los", "las", "mi", "mis", "un", "una", "programa", "app",
            "aplicacion", "aplicación", "por", "favor", "porfavor", "porfa"}

# Apps del sistema que se abren por su URI de PROTOCOLO. Es mucho más fiable que
# buscarlas en el índice del menú inicio: la Cámara, por ejemplo, a veces no sale
# en Get-StartApps o su nombre cambia con el idioma ("Cámara"/"Camera"), y
# entonces la búsqueda fallaba. Con el URI se abren siempre. Cada clave se compara
# contra la consulta ya limpia (sin "abre/mi/..."), por palabra o igualdad.
_APPS_URI = {
    "camara": "microsoft.windows.camera:",
    "camera": "microsoft.windows.camera:",
    "webcam": "microsoft.windows.camera:",
    "configuracion": "ms-settings:",
    "ajustes": "ms-settings:",
    "settings": "ms-settings:",
    "fotos": "ms-photos:",
    "tienda": "ms-windows-store:",
    "store": "ms-windows-store:",
    "correo": "outlookmail:",
    "calendario": "outlookcal:",
    "mapas": "bingmaps:",
    "reloj": "ms-clock:",
    "alarma": "ms-clock:",
    "alarmas": "ms-clock:",
}


def _lanzar_uri(uri: str) -> bool:
    """Abre un URI de protocolo de Windows (ej. microsoft.windows.camera:)."""
    try:
        os.startfile(uri)
        return True
    except Exception:
        try:
            subprocess.Popen(f'start "" "{uri}"', shell=True)
            return True
        except Exception:
            return False


def _sin_tildes(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def _norm(t: str) -> str:
    return _sin_tildes(t).lower().strip()


# ──────────────────────────────────────────────
#  Construcción del índice
# ──────────────────────────────────────────────

def _start_apps() -> list:
    """Lista de (nombre, appid) desde Get-StartApps (UWP + escritorio)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=25,
        )
        data = json.loads(r.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        return [(d.get("Name", ""), d.get("AppID", "")) for d in data if d.get("Name")]
    except Exception:
        return []


def _accesos_directos() -> list:
    """(nombre, ruta) de los .lnk del menú inicio (sistema y usuario)."""
    bases = [
        Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("AppData", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    encontrados = []
    for base in bases:
        if base.exists():
            for lnk in base.rglob("*.lnk"):
                encontrados.append((lnk.stem, str(lnk)))
    return encontrados


def _portables() -> list:
    """(nombre, ruta) de .exe y .lnk en Escritorio y Descargas (apps portables)."""
    encontrados = []
    for carpeta in (ESCRITORIO, DESCARGAS):
        if carpeta and Path(carpeta).exists():
            for ext in ("*.exe", "*.lnk"):
                for f in Path(carpeta).glob(ext):
                    encontrados.append((f.stem, str(f)))
    return encontrados


def indexar_programas(forzar: bool = False) -> dict:
    """Construye (o lee de caché) el índice de programas abribles."""
    if not forzar and INDICE_PATH.exists():
        try:
            cache = json.loads(INDICE_PATH.read_text(encoding="utf-8"))
            if time.time() - cache.get("creado", 0) < CADUCIDAD_SEG:
                return cache["programas"]
        except Exception:
            pass

    programas = {}  # nombre_norm -> {"nombre","tipo","target"}

    def _add(nombre, tipo, target):
        if not nombre or not target:
            return
        clave = _norm(nombre)
        if clave and clave not in programas:
            programas[clave] = {"nombre": nombre, "tipo": tipo, "target": target}

    for nombre, appid in _start_apps():
        _add(nombre, "uwp", appid)
    for nombre, ruta in _accesos_directos():
        _add(nombre, "shortcut", ruta)
    for nombre, ruta in _portables():
        _add(nombre, "exe", ruta)

    try:
        INDICE_PATH.write_text(json.dumps({"creado": time.time(), "programas": programas},
                                          ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return programas


# ──────────────────────────────────────────────
#  Búsqueda difusa
# ──────────────────────────────────────────────

def _terminos_busqueda(consulta: str) -> list:
    base = _norm(consulta)
    palabras = [p for p in base.split() if p not in _RELLENO]
    limpio = " ".join(palabras) or base
    terminos = [limpio]
    # Expandir con sinónimos si la consulta coincide con una clave conocida
    for clave, alias in SINONIMOS.items():
        if clave in limpio or limpio in clave:
            terminos.extend(_norm(a) for a in alias)
    return list(dict.fromkeys(terminos))


def buscar_programa(consulta: str, max_resultados: int = 5) -> list:
    """Devuelve candidatos ordenados [{'nombre','tipo','target','score'}]."""
    programas = indexar_programas()
    terminos = _terminos_busqueda(consulta)

    puntuados = []
    for clave, info in programas.items():
        mejor = 0.0
        for t in terminos:
            if not t:
                continue
            if clave == t:
                mejor = max(mejor, 1.0)
            elif t in clave or clave in t:
                # subcadena: peso alto, mejor cuanto más parecidos de largo
                mejor = max(mejor, 0.85 + 0.1 * (len(t) / max(len(clave), 1)))
            else:
                # difusa por palabras y global
                ratio = SequenceMatcher(None, t, clave).ratio()
                tokens_t, tokens_c = set(t.split()), set(clave.split())
                solap = len(tokens_t & tokens_c) / max(len(tokens_t), 1)
                mejor = max(mejor, 0.6 * ratio + 0.4 * solap)
        if mejor >= 0.55:
            puntuados.append((mejor, info))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    return [{**info, "score": round(s, 2)} for s, info in puntuados[:max_resultados]]


# ──────────────────────────────────────────────
#  Lanzamiento
# ──────────────────────────────────────────────

def _lanzar(info: dict) -> dict:
    tipo, target, nombre = info["tipo"], info["target"], info["nombre"]
    try:
        if tipo == "uwp":
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
        elif tipo == "shortcut":
            os.startfile(target)
        else:  # exe / portable
            os.startfile(target)
        return {"estado": "ok", "nombre": nombre, "tipo": tipo}
    except Exception as e:
        return {"error": str(e), "nombre": nombre}


def abrir_programa_inteligente(consulta: str) -> dict:
    """
    Busca el programa que mejor coincide con lo que pidió el usuario y lo abre.
    Si no hay coincidencia clara, devuelve sugerencias para que LARA pregunte.
    """
    # 1) Apps del sistema con URI de protocolo (cámara, ajustes, fotos...): es la
    #    vía más fiable, así "abre mi cámara" funciona siempre.
    limpio = " ".join(p for p in _norm(consulta).split() if p not in _RELLENO) or _norm(consulta)
    palabras = set(limpio.split())
    for clave, uri in _APPS_URI.items():
        if clave == limpio or clave in palabras:
            if _lanzar_uri(uri):
                return {"estado": "ok", "nombre": clave, "tipo": "uri"}

    candidatos = buscar_programa(consulta)
    if not candidatos:
        # Último recurso: intentar el comando 'start' literal de Windows
        try:
            subprocess.Popen(f'start "" "{consulta}"', shell=True)
            return {"estado": "ok", "nombre": consulta, "tipo": "literal",
                    "nota": "No estaba en el índice; intenté abrirlo directamente."}
        except Exception:
            return {"error": f"No encontré ningún programa que coincida con '{consulta}'.",
                    "sugerencias": []}

    mejor = candidatos[0]
    resultado = _lanzar(mejor)
    alternativas = [c["nombre"] for c in candidatos[1:4]]
    if "error" not in resultado and alternativas:
        resultado["alternativas"] = alternativas
    return resultado
