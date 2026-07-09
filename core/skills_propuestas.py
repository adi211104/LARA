"""
skills_propuestas.py — Gestión del ciclo de vida de skills en LARA.

Flujo completo:
  1. proponer_skill()   → guarda el código en SKILLS_PENDIENTES (sin duplicar)
  2. listar_propuestas_pendientes() → devuelve la lista para que el router la informe al usuario
  3. aceptar_propuesta() → mueve el archivo a CORE_DIR y registra la skill en skills_registry.json
"""

from pathlib import Path
from config import SKILLS_PENDIENTES, CORE_DIR


# ──────────────────────────────────────────────
# Guardar propuesta
# ──────────────────────────────────────────────

def proponer_skill(nombre: str, codigo: str, sobrescribir: bool = False) -> dict:
    """
    Guarda una propuesta de skill en SKILLS_PENDIENTES/<nombre>.py.

    CORRECCIÓN: ya no crea duplicados nombre_1.py, nombre_2.py, etc.
    Si ya existe un archivo con el mismo nombre:
      - sobrescribir=True  → sobreescribe directamente (útil cuando el usuario pide "mejora X")
      - sobrescribir=False → devuelve un dict con 'requiere_confirmacion' para que main.py
                             pregunte al usuario antes de actuar.

    La cabecera identificativa usa comentarios (#), no docstrings, para no
    desplazar el docstring real del script generado.
    """
    SKILLS_PENDIENTES.mkdir(parents=True, exist_ok=True)
    destino = SKILLS_PENDIENTES / f"{nombre}.py"

    # Detectar conflicto sin sobrescribir ciegamente
    if destino.exists() and not sobrescribir:
        return {
            "requiere_confirmacion": True,
            "nombre": nombre,
            "ruta_existente": str(destino),
            "mensaje": (
                f"Ya existe una propuesta '{nombre}.py' en skills_pendientes. "
                f"¿Deseas sobrescribirla? Responde 'sí' para reemplazarla."
            ),
        }

    # Cabecera como comentarios # (no docstring) para no "robar" el __doc__ del script
    cabecera = (
        "# ─────────────────────────────────────────────────────────\n"
        f"# Propuesta de LARA — skill: {nombre}\n"
        "# Estado: PENDIENTE de revisión. Mueve este archivo a core/\n"
        "# cuando lo hayas revisado, o usa 'aceptar_propuesta'.\n"
        "# ─────────────────────────────────────────────────────────\n\n"
    )

    destino.write_text(cabecera + codigo, encoding="utf-8")
    return {
        "estado": "ok",
        "nombre": nombre,
        "ruta": str(destino),
        "sobrescrito": destino.exists(),
        "mensaje": f"Propuesta guardada en '{destino}'. Revísala y usa 'aceptar_propuesta' para instalarla.",
    }


# ──────────────────────────────────────────────
# Listar propuestas pendientes
# ──────────────────────────────────────────────

def listar_propuestas_pendientes() -> dict:
    """
    Devuelve la lista de archivos .py en SKILLS_PENDIENTES.
    Expuesto como acción 'listar_propuestas' en main.py para que el router
    pueda informar al usuario de qué skills están esperando revisión.
    """
    if not SKILLS_PENDIENTES.exists():
        return {"pendientes": [], "mensaje": "No hay ninguna skill pendiente de revisión."}

    archivos = sorted(SKILLS_PENDIENTES.glob("*.py"))
    if not archivos:
        return {"pendientes": [], "mensaje": "No hay ninguna skill pendiente de revisión."}

    return {
        "pendientes": [f.name for f in archivos],
        "mensaje": f"Hay {len(archivos)} skill(s) pendiente(s): {', '.join(f.name for f in archivos)}",
    }


# ──────────────────────────────────────────────
# Aceptar propuesta → mover + registrar
# ──────────────────────────────────────────────

def aceptar_propuesta(
    nombre: str,
    descripcion: str = "",
    argumentos: list[str] | None = None,
) -> dict:
    """
    Mueve SKILLS_PENDIENTES/<nombre>.py a CORE_DIR/<nombre>.py
    y registra la skill en skills_registry.json.

    Args:
        nombre:      Nombre del archivo (sin .py), ej. "modo_nocturno"
        descripcion: Descripción breve para el prompt del router.
                     Si está vacía, se extrae del docstring del archivo.
        argumentos:  Argumentos válidos, ej. ["activar", "desactivar"].
                     Si es None, se intenta detectar del código ("sys.argv").
    """
    # Import aquí para evitar importación circular al arrancar LARA.
    # Soporta ejecución como paquete (core.*) y como módulos sueltos.
    try:
        from core.skills_registry import registrar_skill
    except ImportError:
        from skills_registry import registrar_skill

    origen = SKILLS_PENDIENTES / f"{nombre}.py"
    if not origen.exists():
        return {"error": f"No se encontró la propuesta '{nombre}.py' en skills_pendientes."}

    destino = CORE_DIR / f"{nombre}.py"
    CORE_DIR.mkdir(parents=True, exist_ok=True)

    # Mover el archivo
    import shutil
    shutil.move(str(origen), str(destino))

    # Extraer descripción del docstring si no se proporcionó
    if not descripcion:
        try:
            codigo = destino.read_text(encoding="utf-8")
            # Buscar el primer string entre comillas triples que no sea la cabecera de LARA
            import ast
            tree = ast.parse(codigo)
            for node in ast.walk(tree):
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    val = str(node.value.value).strip()
                    if val and "Propuesta de LARA" not in val:
                        descripcion = val.split("\n")[0][:120]
                        break
        except Exception:
            pass
        if not descripcion:
            descripcion = f"Skill {nombre} instalada por LARA."

    # Detectar argumentos del código si no se proporcionaron
    if argumentos is None:
        try:
            codigo = destino.read_text(encoding="utf-8")
            # Heurística: buscar diccionario "valores" con claves de argumentos (patrón común)
            import re
            match = re.search(r'valores\s*=\s*\{([^}]+)\}', codigo)
            if match:
                claves = re.findall(r'"(\w+)"\s*:', match.group(1))
                argumentos = claves if claves else []
            else:
                argumentos = []
        except Exception:
            argumentos = []

    # Registrar en skills_registry.json
    registrar_skill(
        nombre=nombre,
        ruta=str(destino),
        descripcion=descripcion,
        argumentos=argumentos,
    )

    return {
        "estado": "ok",
        "nombre": nombre,
        "ruta_final": str(destino),
        "descripcion": descripcion,
        "argumentos": argumentos,
        "mensaje": (
            f"Skill '{nombre}' instalada en core/ y registrada. "
            f"Ya puedes invocarla con: ejecutar_skill {nombre} {argumentos[0] if argumentos else ''}"
        ),
    }
