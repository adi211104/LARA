"""
skills_archivos.py — Capacidades de LARA a nivel de un AGENTE de trabajo
(estilo Claude Code / Cursor): editar archivos con precisión, buscar dentro
de ellos y localizarlos por patrón. Antes LARA solo podía CREAR (sobrescribir
entero) y LEER (truncado a 1500 caracteres), lo que le impedía trabajar de
verdad sobre código o textos existentes.

Lo que añade:
  - editar_archivo: reemplazo EXACTO de un fragmento por otro (como un "Edit"
    quirúrgico), con verificación de unicidad para no editar el sitio erróneo.
    Deja copia .bak para poder deshacer.
  - insertar_en_archivo: añade texto al final o tras una línea ancla.
  - leer_archivo_rango: lee un archivo COMPLETO o un rango de líneas, con
    números de línea (para razonar sobre qué editar). Sin el tope de 1500.
  - buscar_en_archivos: 'grep' — busca un patrón (texto o regex) por el
    contenido de los archivos de una carpeta y devuelve archivo:línea:texto.
  - buscar_archivos: 'glob' — localiza archivos por patrón de nombre
    (ej. "*.py", "factura*.pdf") de forma recursiva.

Todas resuelven rutas con el mismo _resolver_ruta de skills_os (entiende
'escritorio', 'core', 'workspace'... y rutas absolutas), así el usuario habla
igual que con el resto de LARA. Las que MODIFICAN pasan por el gate de modos
en cerebro (en 'manual' te piden confirmación; en 'lectura' se bloquean).
"""

import os
import re
from pathlib import Path

from core.skills_os import _resolver_ruta

# Carpetas que NUNCA se recorren en búsquedas (ruido y volumen inútil).
_IGNORAR_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv",
                 ".idea", ".vscode", "dist", "build", ".mypy_cache"}
# Extensiones que se tratan como texto para 'grep'. El resto (binarios) se
# saltan para no volcar basura ni gastar tiempo.
_EXT_TEXTO = {".py", ".txt", ".md", ".json", ".csv", ".html", ".htm", ".css",
              ".js", ".ts", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".bat",
              ".ps1", ".sh", ".c", ".cpp", ".h", ".java", ".kt", ".rs", ".go",
              ".sql", ".log", ".env", ".toml", ".gradle", ".properties", ""}

_MAX_RESULTADOS = 60          # tope de coincidencias devueltas (grep/glob)
_MAX_BYTES_LEER = 400_000     # ~400 KB: techo para leer un archivo entero


def _es_texto(p: Path) -> bool:
    return p.suffix.lower() in _EXT_TEXTO


def leer_archivo_rango(ruta: str, desde: int = 1, hasta: int = 0) -> dict:
    """
    Lee un archivo de texto con números de línea. Por defecto lo lee ENTERO
    (hasta el techo de tamaño); con 'desde'/'hasta' devuelve solo ese rango
    (1-indexado, inclusivo). Pensada para que LARA vea el contenido real antes
    de editarlo, sin el truncado a 1500 del leer_archivo clásico.
    """
    target = _resolver_ruta(ruta)
    try:
        if target.is_dir():
            return {"error": f"'{ruta}' es una carpeta, no un archivo."}
        if not target.exists():
            if target.suffix == "" and target.with_suffix(".py").exists():
                target = target.with_suffix(".py")
            else:
                return {"error": f"Archivo no encontrado: {target}"}
        if target.stat().st_size > _MAX_BYTES_LEER:
            return {"error": f"El archivo es muy grande ({target.stat().st_size//1024} KB). "
                             f"Pide un rango con 'desde' y 'hasta'."}
        lineas = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        total = len(lineas)
        desde = max(1, int(desde or 1))
        hasta = int(hasta) if hasta else total
        hasta = min(hasta, total)
        trozo = lineas[desde - 1:hasta]
        numeradas = "\n".join(f"{desde + i:>5}\t{l}" for i, l in enumerate(trozo))
        return {"estado": "ok", "archivo": target.name, "ruta": str(target),
                "total_lineas": total, "desde": desde, "hasta": hasta,
                "contenido": numeradas}
    except Exception as e:
        return {"error": str(e)}


def _guardar_bak(target: Path) -> None:
    """Copia .bak del archivo antes de modificarlo (para 'deshaz lo último')."""
    try:
        bak = target.with_suffix(target.suffix + ".bak")
        bak.write_text(target.read_text(encoding="utf-8", errors="ignore"),
                       encoding="utf-8")
    except Exception:
        pass


def editar_archivo(ruta: str, buscar: str, reemplazar: str,
                   todas: bool = False) -> dict:
    """
    Reemplaza el fragmento EXACTO 'buscar' por 'reemplazar' dentro del archivo.
    Como un Edit quirúrgico: no reescribe el archivo entero, solo el fragmento.

    - Si 'buscar' no aparece: error (no adivina).
    - Si aparece MÁS de una vez y todas=False: error pidiendo desambiguar
      (evita editar el sitio equivocado). Con todas=True reemplaza todas.
    Deja una copia .bak. Devuelve cuántas sustituciones hizo.
    """
    target = _resolver_ruta(ruta)
    try:
        if not target.exists() or target.is_dir():
            return {"error": f"Archivo no encontrado: {target}"}
        original = target.read_text(encoding="utf-8", errors="ignore")
        n = original.count(buscar)
        if n == 0:
            return {"error": "No encontré ese fragmento EXACTO en el archivo. "
                             "Revisa espacios/tildes o pídeme leerlo primero."}
        if n > 1 and not todas:
            return {"error": f"El fragmento aparece {n} veces; sería ambiguo. "
                             f"Dame más contexto alrededor para que sea único, "
                             f"o dime 'reemplázalas todas'."}
        _guardar_bak(target)
        nuevo = original.replace(buscar, reemplazar) if todas \
            else original.replace(buscar, reemplazar, 1)
        target.write_text(nuevo, encoding="utf-8")
        hechas = n if todas else 1
        return {"estado": "ok", "ruta": str(target), "sustituciones": hechas,
                "bak": str(target.with_suffix(target.suffix + ".bak"))}
    except Exception as e:
        return {"error": str(e)}


def insertar_en_archivo(ruta: str, texto: str, tras_linea: str = "") -> dict:
    """
    Inserta 'texto' en el archivo: al FINAL si 'tras_linea' está vacío, o justo
    DESPUÉS de la primera línea que contenga 'tras_linea'. Crea el archivo si
    no existe (inserción al final). Deja copia .bak si ya existía.
    """
    target = _resolver_ruta(ruta)
    try:
        existia = target.exists() and not target.is_dir()
        if existia:
            _guardar_bak(target)
            lineas = target.read_text(encoding="utf-8", errors="ignore").splitlines()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            lineas = []
        if tras_linea:
            idx = next((i for i, l in enumerate(lineas) if tras_linea in l), None)
            if idx is None:
                return {"error": f"No encontré una línea que contenga «{tras_linea}»."}
            lineas.insert(idx + 1, texto)
        else:
            lineas.append(texto)
        target.write_text("\n".join(lineas) + "\n", encoding="utf-8")
        return {"estado": "ok", "ruta": str(target),
                "donde": "final" if not tras_linea else f"tras «{tras_linea}»"}
    except Exception as e:
        return {"error": str(e)}


def buscar_en_archivos(patron: str, carpeta: str = "", glob: str = "*") -> dict:
    """
    'grep' de LARA: busca 'patron' (regex, sin distinción de mayúsculas) por el
    CONTENIDO de los archivos de texto bajo 'carpeta' (recursivo). Devuelve las
    líneas coincidentes como 'archivo:línea: texto'. 'glob' filtra por nombre
    (ej. "*.py"). Sáltate binarios y carpetas de ruido automáticamente.
    """
    base = _resolver_ruta(carpeta) if carpeta else _resolver_ruta("workspace")
    if not base.exists():
        return {"error": f"La carpeta '{base}' no existe."}
    try:
        rx = re.compile(patron, re.IGNORECASE)
    except re.error as e:
        return {"error": f"Patrón inválido: {e}"}
    resultados, archivos_vistos = [], 0
    try:
        raiz = base if base.is_dir() else base.parent
        for p in raiz.rglob(glob):
            if p.is_dir() or any(part in _IGNORAR_DIRS for part in p.parts):
                continue
            if not _es_texto(p):
                continue
            archivos_vistos += 1
            try:
                for i, linea in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(linea):
                        resultados.append(f"{p}:{i}: {linea.strip()[:200]}")
                        if len(resultados) >= _MAX_RESULTADOS:
                            return {"estado": "ok", "coincidencias": resultados,
                                    "nota": f"Truncado a {_MAX_RESULTADOS} coincidencias.",
                                    "archivos_revisados": archivos_vistos}
            except Exception:
                continue
        if not resultados:
            return {"estado": "ok", "coincidencias": [],
                    "nota": f"Sin coincidencias de «{patron}» en {archivos_vistos} archivos."}
        return {"estado": "ok", "coincidencias": resultados,
                "archivos_revisados": archivos_vistos}
    except Exception as e:
        return {"error": str(e)}


def buscar_archivos(patron: str, carpeta: str = "") -> dict:
    """
    'glob' de LARA: localiza archivos por patrón de NOMBRE (ej. "*.py",
    "factura*.pdf", "**/notas*.txt") bajo 'carpeta', recursivo. Si el patrón no
    trae comodín, se busca como "*patron*". Devuelve las rutas encontradas,
    ordenadas por fecha de modificación (lo más reciente primero).
    """
    base = _resolver_ruta(carpeta) if carpeta else _resolver_ruta("workspace")
    if not base.exists():
        return {"error": f"La carpeta '{base}' no existe."}
    if not any(c in patron for c in "*?["):
        patron = f"*{patron}*"
    if not patron.startswith("**"):
        patron = f"**/{patron}"
    try:
        encontrados = [p for p in base.glob(patron)
                       if p.is_file() and not any(part in _IGNORAR_DIRS for part in p.parts)]
        encontrados.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        rutas = [str(p) for p in encontrados[:_MAX_RESULTADOS]]
        res = {"estado": "ok", "archivos": rutas, "total": len(encontrados)}
        if len(encontrados) > _MAX_RESULTADOS:
            res["nota"] = f"Mostrando {_MAX_RESULTADOS} de {len(encontrados)}."
        if not rutas:
            res["nota"] = f"No hallé archivos que coincidan con «{patron}»."
        return res
    except Exception as e:
        return {"error": str(e)}


def restaurar_bak(ruta: str) -> dict:
    """Revierte un archivo a su copia .bak (deshacer de editar/insertar)."""
    target = _resolver_ruta(ruta)
    bak = target.with_suffix(target.suffix + ".bak")
    try:
        if not bak.exists():
            return {"error": "No hay copia de seguridad (.bak) de ese archivo."}
        target.write_text(bak.read_text(encoding="utf-8", errors="ignore"),
                          encoding="utf-8")
        return {"estado": "ok", "ruta": str(target)}
    except Exception as e:
        return {"error": str(e)}
