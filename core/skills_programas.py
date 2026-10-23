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
    # Sin respaldo vía 'start' con shell=True (A-02): si startfile no puede,
    # cmd.exe tampoco aportaría nada salvo riesgo de inyección.
    try:
        os.startfile(uri)
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


# ──────────────────────────────────────────────
#  Cierre INTELIGENTE de programas (contraparte de abrir_programa_inteligente)
# ──────────────────────────────────────────────
# Antes NO existía ninguna skill dedicada a cerrar programas: el router caía a
# "cmd" y el modelo tenía que ADIVINAR el nombre exacto del .exe (ej.
# "WhatsApp.exe"), que muchas veces no coincide con el proceso real -> taskkill
# fallaba y LARA decía "no pude" o inventaba una excusa. Esto busca en los
# procesos REALMENTE EN EJECUCIÓN (no en el índice de apps instalables) y hace
# coincidencia difusa igual que abrir_programa_inteligente, así no depende de
# que el modelo acierte el nombre interno del ejecutable.

_RELLENO_CERRAR = {"cierra", "ciérrame", "cierrame", "cerrar", "termina", "terminar",
                    "finaliza", "finalizar", "mata", "matar", "sal", "salir",
                    "el", "la", "los", "las", "mi", "mis", "un", "una", "de",
                    "proceso", "procesos", "programa", "app", "aplicacion",
                    "aplicación", "por", "favor", "porfavor", "porfa"}

# Procesos que NUNCA se cierran por coincidencia difusa, aunque el usuario lo
# pida: son del sistema operativo y matarlos puede colgar o cerrar la sesión
# de Windows (explorer.exe incluido -- para eso ya existe modo_nocturno, que
# lo hace a propósito y no por una coincidencia aproximada de voz).
_PROCESOS_PROTEGIDOS = {
    "system", "system idle process", "registry", "csrss", "wininit", "winlogon",
    "services", "lsass", "smss", "dwm", "svchost", "explorer", "audiodg",
}


def _procesos_en_ejecucion() -> list:
    """[{'pid','nombre','titulo'}] de los procesos activos (PowerShell Get-Process)."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "Get-Process | Select-Object Id,ProcessName,MainWindowTitle | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", timeout=15,
        )
        data = json.loads(r.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        return [{"pid": d.get("Id"), "nombre": d.get("ProcessName", ""),
                 "titulo": d.get("MainWindowTitle", "") or ""} for d in data if d.get("ProcessName")]
    except Exception:
        return []


def _terminos_busqueda_cierre(consulta: str) -> list:
    base = _norm(consulta)
    palabras = [p for p in base.split() if p not in _RELLENO_CERRAR]
    limpio = " ".join(palabras) or base
    return [limpio] if limpio else []


def buscar_proceso(consulta: str, max_resultados: int = 5) -> list:
    """Devuelve procesos en ejecución que coinciden con la consulta, ordenados
    por score: [{'nombre','pids','titulo','score'}]. Agrupa por nombre de
    proceso (una app con varias ventanas/subprocesos cuenta como una)."""
    terminos = _terminos_busqueda_cierre(consulta)
    if not terminos or not terminos[0]:
        return []

    grupos = {}  # nombre_norm -> {"nombre","pids":[],"titulo"}
    for p in _procesos_en_ejecucion():
        nombre = p["nombre"]
        clave = _norm(nombre)
        if clave in _PROCESOS_PROTEGIDOS or p["pid"] == os.getpid():
            continue
        g = grupos.setdefault(clave, {"nombre": nombre, "pids": [], "titulo": ""})
        g["pids"].append(p["pid"])
        if p["titulo"] and not g["titulo"]:
            g["titulo"] = p["titulo"]

    puntuados = []
    for clave, info in grupos.items():
        candidatos_texto = [clave]
        if info["titulo"]:
            candidatos_texto.append(_norm(info["titulo"]))
        mejor = 0.0
        for t in terminos:
            for c in candidatos_texto:
                if not t or not c:
                    continue
                if c == t:
                    mejor = max(mejor, 1.0)
                elif t in c or c in t:
                    mejor = max(mejor, 0.85 + 0.1 * (len(t) / max(len(c), 1)))
                else:
                    ratio = SequenceMatcher(None, t, c).ratio()
                    tokens_t, tokens_c = set(t.split()), set(c.split())
                    solap = len(tokens_t & tokens_c) / max(len(tokens_t), 1)
                    mejor = max(mejor, 0.6 * ratio + 0.4 * solap)
        if mejor >= 0.55:
            puntuados.append((mejor, info))

    puntuados.sort(key=lambda x: x[0], reverse=True)
    return [{**info, "score": round(s, 2)} for s, info in puntuados[:max_resultados]]


def _matar_pid(pid: int) -> str:
    """Intenta cerrar un PID sin elevar. Devuelve "" si funcionó, o el stderr
    real de taskkill (para no inventar un motivo genérico)."""
    try:
        r = subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return ""
        return (r.stderr or r.stdout or "taskkill falló sin detalle").strip()
    except Exception as e:
        return str(e)


def _matar_pid_admin(pid: int) -> bool:
    """
    Cierra un PID CON elevación (dispara el prompt de UAC de Windows -- el
    usuario debe aprobarlo). Usa Start-Process -Verb RunAs -Wait -PassThru
    para poder leer el código de salida real de taskkill (a diferencia de
    ejecutar_cmd_admin, que solo abre una ventana visible sin forma de saber
    si el comando de adentro tuvo éxito).
    """
    try:
        ps = (f"$p = Start-Process taskkill -ArgumentList '/PID {pid} /F' "
              f"-Verb RunAs -WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def cerrar_programa_inteligente(consulta: str, admin: bool = False) -> dict:
    """
    Busca entre los procesos REALMENTE en ejecución el que mejor coincide con
    lo que pidió el usuario y lo cierra (todos sus PID, por si tiene varias
    ventanas/subprocesos). Si no hay coincidencia clara, devuelve un error
    honesto en vez de una excusa genérica.

    'admin=True' cierra CON permisos elevados (dispara UAC): úsalo solo
    cuando el usuario lo pida explícitamente tras un primer intento fallido
    (un proceso corriendo elevado -- ej. Task Manager "como administrador" --
    no se puede cerrar sin elevarse también).
    """
    candidatos = buscar_proceso(consulta)
    if not candidatos:
        return {"error": f"No encontré ningún proceso en ejecución que coincida con '{consulta}'."}

    mejor = candidatos[0]
    cerrados = 0
    ultimo_motivo = ""
    for pid in mejor["pids"]:
        motivo = _matar_pid(pid)
        if not motivo:
            cerrados += 1
            continue
        if admin and _matar_pid_admin(pid):
            cerrados += 1
            continue
        ultimo_motivo = motivo

    if cerrados == 0:
        sugerencia = "" if admin else " Puedo reintentar con permisos de administrador si me lo pides."
        return {"error": f"Encontré '{mejor['nombre']}' pero no pude cerrarlo "
                          f"({ultimo_motivo}).{sugerencia}",
                "nombre": mejor["nombre"], "necesita_admin": not admin}

    resultado = {"estado": "ok", "nombre": mejor["nombre"], "cerrados": cerrados}
    alternativas = [c["nombre"] for c in candidatos[1:4] if c["nombre"] != mejor["nombre"]]
    if alternativas:
        resultado["alternativas"] = alternativas
    return resultado


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
        # Último recurso: abrir el nombre literal (App Paths / PATH de Windows)
        # con os.startfile, que no interpreta metacaracteres como '&' o '|'.
        literal = (consulta or "").strip()
        if not literal or any(c in '"^&|<>%`' or ord(c) < 32 for c in literal):
            return {"error": f"No encontré ningún programa que coincida con '{consulta}'.",
                    "sugerencias": []}
        try:
            os.startfile(literal)
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
