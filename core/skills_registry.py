"""
skills_registry.py — Registro permanente de skills instaladas en LARA.

DISEÑO MULTI-PC (OneDrive / carpetas sincronizadas):
  Las rutas se guardan RELATIVAS a CORE_DIR (ej. "modo_lectura.py") para que
  el registro funcione en cualquier máquina aunque el nombre de usuario cambie.
  En tiempo de ejecución se resuelven contra el CORE_DIR real de esa máquina.

  Compatibilidad hacia atrás: si una entrada antigua tiene ruta absoluta y el
  archivo ya no existe, se intenta auto-reparar buscando el .py en CORE_DIR.

Flujo típico:
  1. auto_registrar_skills_core() al arrancar → escanea core/, registra skills
  2. generar_prompt_sistema()  → llama listar_skills_resumen() → inyecta en prompt
  3. Router / pre-despacho    → llama ejecutar_skill_registrada("modo_nocturno", "activar")
"""

import json
import ast as _ast
import re as _re
import subprocess
from pathlib import Path
from config import KNOWLEDGE_DIR, CORE_DIR

REGISTRO_PATH = KNOWLEDGE_DIR / "skills_registry.json"

# Módulos internos de LARA que nunca son skills ejecutables
_MODULOS_INTERNOS = {
    "main.py", "config.py", "llm_engine.py", "logger.py",
    "skills_os.py", "skills_web.py", "skills_download.py",
    "skills_conocimiento.py", "skills_memoria.py", "skills_novedades.py",
    "skills_propuestas.py", "skills_registry.py", "skills_tasks.py",
    "agente_fondo.py", "__init__.py",
}


# ──────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────

def _cargar() -> dict:
    if REGISTRO_PATH.exists():
        try:
            return json.loads(REGISTRO_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _guardar(registro: dict) -> None:
    REGISTRO_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO_PATH.write_text(
        json.dumps(registro, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolver_ruta(info: dict) -> Path | None:
    """
    Resuelve la ruta real del script de una skill, con tres niveles de fallback:
      1. ruta_relativa  → CORE_DIR / nombre_archivo  (multi-PC, la correcta)
      2. ruta           → ruta absoluta antigua       (compatibilidad hacia atrás)
      3. CORE_DIR / nombre.py                         (auto-reparación si todo falla)
    Devuelve un Path existente o None si no se encuentra en ningún lado.
    """
    # 1. Ruta relativa (nuevo formato)
    rel = info.get("ruta_relativa")
    if rel:
        candidato = CORE_DIR / rel
        if candidato.exists():
            return candidato

    # 2. Ruta absoluta (formato antiguo)
    abs_ruta = info.get("ruta")
    if abs_ruta:
        p = Path(abs_ruta)
        if p.exists():
            return p
        # 3. Auto-reparación: solo el nombre de archivo relativo a CORE_DIR actual
        solo_nombre = p.name
        candidato_core = CORE_DIR / solo_nombre
        if candidato_core.exists():
            return candidato_core

    return None


def _detectar_argumentos(codigo: str) -> list:
    """
    Extrae los argumentos válidos de una skill usando múltiples patrones.
    Soporta:
      - valores = {"activar": ..., "desactivar": ...}   ← skills antiguas
      - if accion == "activar": / elif accion == "desactivar":
      - if sys.argv[1] == "activar":
    """
    # Patrón 1: dict valores = {"arg": ...}
    m = _re.search(r'valores\s*=\s*\{([^}]+)\}', codigo)
    if m:
        args = _re.findall(r'"(\w+)"\s*:', m.group(1))
        if args:
            return args

    # Patrón 2: comparaciones con variable accion / sys.argv[1]
    args = _re.findall(r'(?:accion|argv\[1\])\s*(?:\.lower\(\))?\s*==\s*["\'](\w+)["\']', codigo)
    if args:
        return list(dict.fromkeys(args))  # deduplica manteniendo orden

    # Patrón 3: elif accion == ... (complemento del patrón 2 para elif)
    args = _re.findall(r'elif\s+\w+\s*(?:\.lower\(\))?\s*==\s*["\'](\w+)["\']', codigo)
    if args:
        return list(dict.fromkeys(args))

    return []


# ──────────────────────────────────────────────
# API pública
# ──────────────────────────────────────────────

def registrar_skill(
    nombre: str,
    ruta: str,
    descripcion: str,
    argumentos: list,
    tipo: str = "python_script",
) -> None:
    """
    Registra o actualiza una skill. Guarda SIEMPRE la ruta relativa a CORE_DIR
    (además de la absoluta para compatibilidad) para que funcione en multi-PC.
    """
    reg = _cargar()
    p = Path(ruta)
    try:
        ruta_relativa = str(p.relative_to(CORE_DIR))
    except ValueError:
        ruta_relativa = p.name  # si no es hija de CORE_DIR, solo el nombre

    reg[nombre] = {
        "ruta": str(ruta),           # absoluta (compatibilidad / referencia)
        "ruta_relativa": ruta_relativa,  # relativa a CORE_DIR (multi-PC)
        "descripcion": descripcion,
        "argumentos": argumentos,
        "tipo": tipo,
    }
    _guardar(reg)


def desregistrar_skill(nombre: str) -> bool:
    reg = _cargar()
    if nombre in reg:
        del reg[nombre]
        _guardar(reg)
        return True
    return False


def cargar_registro() -> dict:
    return _cargar()


def listar_skills_resumen() -> str:
    """
    Bloque de texto para inyectar en el system prompt del router.
    Solo muestra skills cuyo archivo existe en esta máquina.
    """
    reg = _cargar()
    if not reg:
        return ""
    lineas = []
    for nombre, info in reg.items():
        if _resolver_ruta(info) is None:
            continue  # ocultar skills rotas del prompt
        args = ", ".join(info.get("argumentos", []))
        lineas.append(f"  - {nombre}: {info['descripcion']}. Args: {args or 'ninguno'}")
    return "\n".join(lineas)


def migrar_rutas_absolutas() -> int:
    """
    Migración automática: convierte entradas con ruta absoluta (formato antiguo)
    a ruta_relativa (formato multi-PC). Llamar una vez al arrancar.
    Devuelve el número de entradas actualizadas.
    """
    reg = _cargar()
    actualizadas = 0
    for nombre, info in reg.items():
        if "ruta_relativa" not in info and "ruta" in info:
            p = Path(info["ruta"])
            try:
                info["ruta_relativa"] = str(p.relative_to(CORE_DIR))
            except ValueError:
                info["ruta_relativa"] = p.name
            actualizadas += 1
    if actualizadas:
        _guardar(reg)
    return actualizadas


def reparar_rutas_rotas() -> list:
    """
    Recorre el registro, detecta entradas cuya ruta absoluta ya no existe
    pero cuyo archivo sí está en CORE_DIR (caso multi-PC), y actualiza la ruta.
    Devuelve los nombres de skills reparadas.
    """
    reg = _cargar()
    reparadas = []
    for nombre, info in reg.items():
        ruta_abs = Path(info.get("ruta", ""))
        if not ruta_abs.exists():
            # Intentar localizar por nombre de archivo en CORE_DIR actual
            solo_nombre = ruta_abs.name or f"{nombre}.py"
            candidato = CORE_DIR / solo_nombre
            if candidato.exists():
                info["ruta"] = str(candidato)
                info["ruta_relativa"] = solo_nombre
                reparadas.append(nombre)
    if reparadas:
        _guardar(reg)
    return reparadas


def auto_registrar_skills_core() -> list:
    """
    Escanea CORE_DIR al arrancar y registra automáticamente scripts .py
    que usen sys.argv y no estén ya en el registro.
    Además: migra rutas antiguas a relativas y repara rutas rotas.
    """
    # Migración y reparación silenciosas antes de registrar nuevas
    migrar_rutas_absolutas()
    reparadas = reparar_rutas_rotas()
    if reparadas:
        print(f"[LARA] Rutas de skills auto-reparadas: {', '.join(reparadas)}")

    reg = _cargar()
    registradas = []

    # Reparación: skills registradas SIN argumentos (registro "al vuelo" antiguo)
    # -> re-analizar su código para detectarlos. Sin esto, "desactivar" nunca
    # llega a la skill y esta activa por defecto.
    args_reparados = False
    for nombre, info in reg.items():
        if not info.get("argumentos"):
            ruta = _resolver_ruta(info)
            if ruta:
                try:
                    args = _detectar_argumentos(ruta.read_text(encoding="utf-8", errors="ignore"))
                    if args:
                        info["argumentos"] = args
                        args_reparados = True
                except OSError:
                    pass
    if args_reparados:
        _guardar(reg)

    for py_file in sorted(CORE_DIR.glob("*.py")):
        nombre = py_file.stem
        if py_file.name in _MODULOS_INTERNOS or nombre in reg:
            continue
        try:
            codigo = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "sys.argv" not in codigo:
            continue

        # Descripción desde docstring
        descripcion = f"Skill {nombre}."
        try:
            tree = _ast.parse(codigo)
            doc = _ast.get_docstring(tree)
            if doc and "Propuesta de LARA" not in doc:
                descripcion = doc.strip().split("\n")[0][:120]
        except Exception:
            pass

        argumentos = _detectar_argumentos(codigo)

        try:
            ruta_relativa = str(py_file.relative_to(CORE_DIR))
        except ValueError:
            ruta_relativa = py_file.name

        reg[nombre] = {
            "ruta": str(py_file),
            "ruta_relativa": ruta_relativa,
            "descripcion": descripcion,
            "argumentos": argumentos,
            "tipo": "python_script",
        }
        registradas.append(nombre)

    if registradas:
        _guardar(reg)

    return registradas


def ejecutar_skill_registrada(nombre: str, argumento: str = "") -> dict:
    """
    Ejecuta una skill registrada. Resuelve la ruta con fallbacks multi-PC.
    Si la skill no está en el registro pero existe en CORE_DIR, la registra al vuelo.
    """
    reg = _cargar()

    if nombre not in reg:
        # Fallback: buscar en CORE_DIR por nombre
        candidato = CORE_DIR / f"{nombre}.py"
        if candidato.exists():
            registrar_skill(
                nombre=nombre,
                ruta=str(candidato),
                descripcion=f"Skill {nombre} (auto-detectada).",
                argumentos=[],
            )
            reg = _cargar()
        else:
            return {
                "error": (
                    f"Skill '{nombre}' no encontrada en el registro ni en core/. "
                    f"Skills disponibles: {list(reg.keys()) or 'ninguna'}"
                )
            }

    info = reg[nombre]
    ruta_real = _resolver_ruta(info)

    if ruta_real is None:
        return {
            "error": (
                f"No se encontró el archivo de '{nombre}' en esta máquina. "
                f"Ruta registrada: {info.get('ruta', '?')}. "
                f"Tampoco está en {CORE_DIR}. ¿Se borró o movió el script?"
            )
        }

    # Si la ruta resuelta difiere de la guardada, actualizar el registro (auto-reparación silenciosa)
    if str(ruta_real) != info.get("ruta"):
        info["ruta"] = str(ruta_real)
        try:
            info["ruta_relativa"] = str(ruta_real.relative_to(CORE_DIR))
        except ValueError:
            info["ruta_relativa"] = ruta_real.name
        reg[nombre] = info
        _guardar(reg)

    # Validar argumento
    args_validos = info.get("argumentos", [])
    if args_validos and argumento and argumento not in args_validos:
        return {
            "error": (
                f"Argumento '{argumento}' no válido para '{nombre}'. "
                f"Argumentos permitidos: {args_validos}"
            )
        }

    cmd = ["python", str(ruta_real)]
    if argumento:
        cmd.append(argumento)

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        stdout_limpio = r.stdout.strip()
        if len(stdout_limpio) > 800:
            stdout_limpio = stdout_limpio[:800] + "\n... [SALIDA TRUNCADA]"
        return {
            "estado": "ok" if r.returncode == 0 else "error_ejecucion",
            "skill": nombre,
            "argumento": argumento,
            "stdout": stdout_limpio,
            "stderr": r.stderr.strip(),
        }
    except subprocess.TimeoutExpired:
        return {"error": f"La skill '{nombre}' tardó demasiado (timeout 30s)."}
    except Exception as e:
        return {"error": str(e)}