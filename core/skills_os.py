import subprocess
import os
import shutil
from pathlib import Path
from config import HERRAMIENTAS, WORKSPACE, CARPETAS_CONOCIDAS

_INTERACTIVOS_PELIGROSOS = {"python", "python3", "py", "cmd", "powershell", "node", "irb", "sqlite3"}


def _resolver_ruta(ruta: str) -> Path:
    """
    Convierte algo como 'escritorio', 'temp_exec.py' o una ruta absoluta en un
    Path real. Si es solo un nombre de carpeta conocida (ej. 'escritorio'),
    devuelve esa carpeta. Si es 'escritorio/archivo.py', lo separa bien.
    Si no es absoluta y no matchea una carpeta conocida, asume WORKSPACE.
    """
    ruta = ruta.strip().strip('"').strip("'")
    partes = ruta.replace("\\", "/").split("/")
    primera = partes[0].lower()

    if primera in CARPETAS_CONOCIDAS:
        base = CARPETAS_CONOCIDAS[primera]
        resto = partes[1:]
        return base.joinpath(*resto) if resto else base

    p = Path(ruta)
    if p.is_absolute():
        return p
    return WORKSPACE / ruta


def copiar_archivo(origen: str, destino: str) -> dict:
    """
    Copia un archivo. 'origen' y 'destino' pueden ser nombres de carpeta
    conocidos ('escritorio', 'descargas', 'workspace') o rutas completas.
    Si 'destino' es solo una carpeta, conserva el nombre original del archivo.
    """
    try:
        ruta_origen = _resolver_ruta(origen)
        if not ruta_origen.exists():
            return {"error": f"El archivo de origen no existe: {ruta_origen}"}

        ruta_destino = _resolver_ruta(destino)
        if ruta_destino.exists() and ruta_destino.is_dir():
            ruta_destino = ruta_destino / ruta_origen.name
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(ruta_origen, ruta_destino)
        return {"estado": "ok", "origen": str(ruta_origen), "destino": str(ruta_destino)}
    except Exception as e:
        return {"error": str(e)}


def mover_archivo(origen: str, destino: str) -> dict:
    """Igual que copiar_archivo, pero mueve (corta) el archivo en vez de copiarlo."""
    try:
        ruta_origen = _resolver_ruta(origen)
        if not ruta_origen.exists():
            return {"error": f"El archivo de origen no existe: {ruta_origen}"}

        ruta_destino = _resolver_ruta(destino)
        if ruta_destino.exists() and ruta_destino.is_dir():
            ruta_destino = ruta_destino / ruta_origen.name
        ruta_destino.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(ruta_origen), str(ruta_destino))
        return {"estado": "ok", "origen": str(ruta_origen), "destino": str(ruta_destino)}
    except Exception as e:
        return {"error": str(e)}


def ejecutar_cmd(comando: str) -> dict:
    """Ejecuta comandos en Windows (CMD/PowerShell). stdin cerrado para evitar
    que un comando interactivo cuelgue el proceso. Además, BLOQUEA a nivel de
    código (no solo de prompt) cualquier intento de abrir un intérprete sin
    argumentos, sin importar qué decida el modelo."""
    comando_normalizado = comando.strip().split()[0].lower() if comando.strip() else ""
    if comando_normalizado in _INTERACTIVOS_PELIGROSOS and len(comando.strip().split()) == 1:
        return {
            "error": (
                f"'{comando}' abriría un intérprete interactivo y se colgaría esperando input. "
                f"Usa 'ejecutar_codigo' o 'ejecutar_codigo_visible' para correr código Python, "
                f"o especifica el comando completo con su archivo/argumento."
            )
        }
    try:
        r = subprocess.run(
            comando,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        stdout_limpio = r.stdout.strip()
        if len(stdout_limpio) > 1000:
            stdout_limpio = stdout_limpio[:1000] + "\n... [SALIDA TRUNCADA POR SEGURIDAD DE TOKENS]"
        return {"stdout": stdout_limpio, "stderr": r.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "El comando tardó demasiado y fue cancelado (timeout 30s). Evita comandos interactivos o que esperen input."}
    except Exception as e:
        return {"error": str(e)}


def ejecutar_codigo_visible(codigo: str, nombre_archivo: str = "temp_exec.py") -> dict:
    """
    Igual que ejecutar_codigo_python, pero en vez de capturar la salida en
    segundo plano, abre una VENTANA CMD real visible donde corre el script.
    Usa subprocess.Popen (no .run) -> no bloquea el hilo principal de LARA,
    la ventana queda abierta con 'pause' para que puedas ver el resultado.
    """
    target = WORKSPACE / nombre_archivo
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(codigo, encoding="utf-8")

        comando = f'start cmd /k "python \"{target}\" & echo. & pause"'
        subprocess.Popen(comando, shell=True)
        return {"estado": "ok", "ruta": str(target), "nota": "Se abrió una ventana CMD ejecutando el script. La salida se ve ahí."}
    except Exception as e:
        return {"error": str(e)}


def ajustar_brillo(nivel) -> dict:
    """
    Ajusta el brillo de la pantalla integrada (laptop) vía WMI.
    LIMITACIÓN: solo funciona en pantallas que soportan WMI brightness
    (típicamente laptops). Monitores externos normalmente NO lo soportan
    porque usan DDC/CI, que Windows no expone igual.
    """
    try:
        nivel = max(0, min(100, int(nivel)))
    except Exception:
        return {"error": f"Nivel de brillo inválido: {nivel}"}

    comando = (
        'powershell -Command "(Get-WmiObject -Namespace root/WMI '
        f'-Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{nivel})"'
    )
    try:
        r = subprocess.run(
            comando, shell=True, capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL
        )
        if r.returncode != 0 or r.stderr.strip():
            return {"error": f"No se pudo ajustar el brillo (tu pantalla puede no soportar WMI, común en monitores externos): {r.stderr.strip()[:200]}"}
        return {"estado": "ok", "nivel": nivel}
    except subprocess.TimeoutExpired:
        return {"error": "El comando de brillo tardó demasiado."}
    except Exception as e:
        return {"error": str(e)}


def ejecutar_cmd_admin(comando: str) -> dict:
    """
    Ejecuta un comando con permisos de administrador (dispara el prompt de UAC
    de Windows). LIMITACIÓN: al elevarse en un proceso nuevo, no se puede
    capturar su stdout/stderr directamente -- el usuario debe ver el resultado
    en la ventana que se abre. Úsalo solo cuando el comando realmente requiera
    privilegios elevados (instalar software, modificar el sistema, etc).
    """
    try:
        cmd_ps = f'Start-Process cmd -ArgumentList "/c {comando} & pause" -Verb RunAs'
        subprocess.Popen(["powershell", "-Command", cmd_ps], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"estado": "ok", "nota": "Se abrió una ventana elevada (UAC). Revisa el resultado ahí; esta función no puede leer su salida."}
    except Exception as e:
        return {"error": str(e)}


def ejecutar_codigo_python(codigo: str, nombre_archivo: str = "temp_exec.py") -> dict:
    """
    Crea un archivo .py en WORKSPACE con el código dado y lo ejecuta de inmediato
    con 'python archivo.py' (NUNCA invoca el intérprete interactivo a secas).
    Resuelve en UN solo paso lo que antes requería 'crear' + 'cmd' por separado,
    evitando el bloqueo del shell.
    """
    target = WORKSPACE / nombre_archivo
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(codigo, encoding="utf-8")

        r = subprocess.run(
            ["python", str(target)],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        stdout_limpio = r.stdout.strip()
        if len(stdout_limpio) > 1000:
            stdout_limpio = stdout_limpio[:1000] + "\n... [SALIDA TRUNCADA]"
        return {"estado": "ok", "ruta": str(target), "stdout": stdout_limpio, "stderr": r.stderr.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "La ejecución tardó demasiado y fue cancelada (timeout 30s)."}
    except Exception as e:
        return {"error": str(e)}

def abrir_programa(nombre: str) -> dict:
    """Abre herramientas definidas en config o usa el comando start de Windows."""
    cmd = HERRAMIENTAS.get(nombre.lower())
    try:
        if cmd:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(f'start "" "{nombre}"', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"estado": "ok", "programa": nombre}
    except Exception as e:
        return {"error": str(e)}

def abrir_url_en_navegador(url: str, navegador: str = "edge") -> dict:
    """
    Abre una URL directamente en el navegador indicado.
    Navegadores soportados: edge, chrome, default (navegador por defecto del sistema).
    """
    try:
        url = url.strip()
        if not url.startswith("http"):
            url = "https://" + url

        if navegador.lower() == "chrome":
            cmd = f'start chrome "{url}"'
        elif navegador.lower() == "default":
            cmd = f'start "" "{url}"'
        else:
            # Edge es el default en Windows 10/11
            cmd = f'start msedge "{url}"'

        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"estado": "ok", "url": url, "navegador": navegador}
    except Exception as e:
        return {"error": str(e)}

def listar_directorio(ruta: str = "") -> dict:
    """Lista los archivos de una carpeta. Por defecto mira en el workspace.
    Entiende nombres de carpeta conocidos (core, escritorio, pendientes, etc.) vía _resolver_ruta."""
    target = _resolver_ruta(ruta) if ruta else WORKSPACE
    try:
        if not target.exists():
            return {"error": f"La ruta '{target}' no existe."}
        todos_archivos = list(target.iterdir())
        items = [{"nombre": f.name, "tipo": "dir" if f.is_dir() else "archivo"} for f in todos_archivos[:20]]
        resultado = {"ruta": str(target), "contenido": items}
        if len(todos_archivos) > 20:
            resultado["nota"] = f"Hay {len(todos_archivos)} elementos en total, truncado a 20."
        return resultado
    except Exception as e:
        return {"error": str(e)}

def leer_archivo(ruta: str) -> dict:
    """Lee el contenido de un archivo de texto o código de forma segura.
    Entiende nombres de carpeta conocidos (core, escritorio, etc.) vía _resolver_ruta."""
    target = _resolver_ruta(ruta)
    try:
        if target.is_dir():
            return {"error": f"'{ruta}' es una carpeta, no un archivo. Usa 'listar' para ver su contenido."}
        if not target.exists():
            # Intento extra: si no tiene extensión, probar con .py (caso típico: "lee modo_lectura")
            if target.suffix == "" and target.with_suffix(".py").exists():
                target = target.with_suffix(".py")
            else:
                return {"error": f"Archivo no encontrado: {target}"}
        contenido = target.read_text(encoding='utf-8', errors='ignore')
        if len(contenido) > 1500:
            contenido = contenido[:1500] + "\n... [CONTENIDO TRUNCADO PARA EVITAR EXCESO DE TOKENS]"
        return {"estado": "ok", "archivo": target.name, "ruta": str(target), "contenido": contenido}
    except Exception as e:
        return {"error": str(e)}

def crear_archivo(ruta: str, contenido: str) -> dict:
    """Crea o sobrescribe un archivo con código o texto en el sistema.
    Entiende nombres de carpeta conocidos (core, escritorio, etc.) vía _resolver_ruta."""
    target = _resolver_ruta(ruta)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contenido, encoding='utf-8')
        return {"estado": "ok", "ruta": str(target), "accion": "archivo guardado correctamente"}
    except Exception as e:
        return {"error": str(e)}