"""
skills_sistema.py — Utilidades de sistema de LARA (mejoras tipo JARVIS).

Reúne muchas capacidades pequeñas y útiles bajo una sola acción 'sistema' para no
inflar el prompt del router (economía de tokens). Cada operación devuelve un dict
con un campo 'mensaje' listo para que LARA lo diga.

Operaciones: hora, bateria, info, ip, wifi, volumen_subir, volumen_bajar,
silenciar, bloquear, captura, papelera, procesos, portapapeles, anota, abrir_carpeta.
Las acciones que afectan al equipo (bloquear, papelera) se ejecutan directamente
porque el usuario las pidió explícitamente.
"""

import os
import subprocess
from datetime import datetime
from pathlib import Path

from config import WORKSPACE, DESCARGAS, ESCRITORIO, DOCUMENTOS


def _ps(comando: str, timeout: int = 12) -> str:
    """Ejecuta un comando PowerShell y devuelve stdout limpio."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", comando],
                           capture_output=True, text=True, encoding="utf-8",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        return (r.stdout or "").strip()
    except Exception as e:
        return f"__error__:{e}"


def _hora():
    ahora = datetime.now()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return {"mensaje": f"Son las {ahora.strftime('%H:%M')} del {dias[ahora.weekday()]} {ahora.strftime('%d/%m/%Y')}."}


def _bateria():
    out = _ps("(Get-CimInstance Win32_Battery).EstimatedChargeRemaining")
    estado = _ps("(Get-CimInstance Win32_Battery).BatteryStatus")
    if not out or out.startswith("__error__") or not out.strip().isdigit():
        return {"mensaje": "No detecto batería (¿es un equipo de escritorio?)."}
    cargando = " y cargando" if estado.strip() == "2" else ""
    return {"mensaje": f"Batería al {out.strip()}%{cargando}."}


def _info():
    cpu = _ps("(Get-CimInstance Win32_Processor).Name")
    ram = _ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)")
    libre = _ps("[math]::Round((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB,1)")
    disco = _ps("[math]::Round((Get-PSDrive C).Free/1GB,1)")
    return {"mensaje": f"Equipo: {cpu.strip()}. RAM total {ram}GB (libre ~{libre}GB). Disco C: libre {disco}GB."}


def _ip():
    local = _ps("(Test-Connection -ComputerName localhost -Count 1).IPV4Address.IPAddressToString")
    try:
        import requests
        publica = requests.get("https://api.ipify.org", timeout=6).text
    except Exception:
        publica = "no disponible"
    return {"mensaje": f"IP local: {local or 'n/d'}. IP pública: {publica}."}


def _wifi():
    out = _ps("(netsh wlan show interfaces) | Select-String 'SSID' | Select-Object -First 1")
    return {"mensaje": f"Wi-Fi: {out or 'no conectado o sin adaptador'}."}


def _volumen(direccion):
    try:
        import pyautogui
        tecla = {"subir": "volumeup", "bajar": "volumedown", "silenciar": "volumemute"}[direccion]
        veces = 1 if direccion == "silenciar" else 5
        for _ in range(veces):
            pyautogui.press(tecla)
        txt = {"subir": "Subiendo el volumen.", "bajar": "Bajando el volumen.", "silenciar": "Volumen silenciado."}
        return {"mensaje": txt[direccion]}
    except Exception as e:
        return {"error": str(e), "mensaje": "No pude cambiar el volumen."}


def _volumen_set(valor):
    """Fija el volumen a un nivel ABSOLUTO (0-100). Sin dependencias extra:
    lleva el volumen a 0 (50 pasos de bajar, cada tecla = 2%) y luego sube al
    nivel pedido. Fiable en cualquier Windows."""
    try:
        n = int(round(float(str(valor).strip().rstrip("%").replace(",", "."))))
    except (ValueError, TypeError):
        return {"mensaje": "Dime a qué nivel pongo el volumen, del 0 al 100."}
    n = max(0, min(100, n))
    try:
        import pyautogui
        for _ in range(50):                 # a 0% (cada paso baja 2%)
            pyautogui.press("volumedown")
        for _ in range(round(n / 2)):       # subir hasta el nivel pedido
            pyautogui.press("volumeup")
        return {"mensaje": f"Volumen al {n}%."}
    except Exception as e:
        return {"error": str(e), "mensaje": "No pude ajustar el volumen."}


def _bloquear():
    try:
        subprocess.Popen("rundll32.exe user32.dll,LockWorkStation", shell=True)
        return {"mensaje": "Bloqueando el equipo. Hasta pronto."}
    except Exception as e:
        return {"error": str(e), "mensaje": "No pude bloquear el equipo."}


def _captura():
    try:
        from PIL import ImageGrab
        carpeta = Path(DESCARGAS) if Path(DESCARGAS).exists() else WORKSPACE
        ruta = carpeta / f"captura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        ImageGrab.grab().save(ruta)
        return {"mensaje": f"Captura guardada en {ruta}.", "ruta": str(ruta)}
    except Exception as e:
        return {"error": str(e), "mensaje": "No pude tomar la captura."}


def _papelera():
    out = _ps("Clear-RecycleBin -Force -ErrorAction SilentlyContinue; 'ok'")
    return {"mensaje": "Papelera de reciclaje vaciada." if "ok" in out else "No pude vaciar la papelera."}


def _procesos():
    out = _ps("Get-Process | Sort-Object WS -Descending | Select-Object -First 5 Name,@{N='MB';E={[math]::Round($_.WS/1MB)}} | Format-Table -HideTableHeaders | Out-String")
    return {"mensaje": "Procesos que más memoria usan:\n" + (out.strip() or "n/d")}


def _portapapeles():
    out = _ps("Get-Clipboard")
    if out.startswith("__error__") or not out:
        return {"mensaje": "El portapapeles está vacío."}
    return {"mensaje": f"En el portapapeles: {out[:500]}", "contenido": out}


def _anota(texto):
    if not texto:
        return {"mensaje": "¿Qué quieres que anote?"}
    archivo = Path(DOCUMENTOS) / "notas_lara.txt" if Path(DOCUMENTOS).exists() else WORKSPACE / "notas_lara.txt"
    with open(archivo, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M')}] {texto}\n")
    return {"mensaje": f"Anotado en {archivo.name}."}


def _abrir_carpeta(nombre):
    carpetas = {"descargas": DESCARGAS, "escritorio": ESCRITORIO, "documentos": DOCUMENTOS, "workspace": WORKSPACE}
    destino = carpetas.get((nombre or "").lower().strip(), WORKSPACE)
    try:
        os.startfile(str(destino))
        return {"mensaje": f"Abriendo {destino}."}
    except Exception as e:
        return {"error": str(e), "mensaje": "No pude abrir la carpeta."}


_OPS = {
    "hora": lambda v: _hora(),
    "bateria": lambda v: _bateria(),
    "info": lambda v: _info(),
    "ip": lambda v: _ip(),
    "wifi": lambda v: _wifi(),
    "volumen_subir": lambda v: _volumen("subir"),
    "volumen_bajar": lambda v: _volumen("bajar"),
    "volumen": lambda v: _volumen_set(v),        # nivel absoluto 0-100
    "volumen_set": lambda v: _volumen_set(v),
    "audio": lambda v: _volumen_set(v),
    "silenciar": lambda v: _volumen("silenciar"),
    "bloquear": lambda v: _bloquear(),
    "captura": lambda v: _captura(),
    "papelera": lambda v: _papelera(),
    "procesos": lambda v: _procesos(),
    "portapapeles": lambda v: _portapapeles(),
    "anota": lambda v: _anota(v),
    "abrir_carpeta": lambda v: _abrir_carpeta(v),
}


def ejecutar(operacion: str, valor: str = "") -> dict:
    op = (operacion or "").lower().strip()
    fn = _OPS.get(op)
    if not fn:
        # 'desconocida' avisa al cerebro para que BUSQUE OTRA VÍA (cmd/PowerShell
        # o proponer una skill) en vez de rendirse con "no conozco esa operación".
        return {"desconocida": True, "operacion": op,
                "mensaje": f"No tengo una operación de sistema '{operacion}'.",
                "operaciones": list(_OPS.keys())}
    return fn(valor)
