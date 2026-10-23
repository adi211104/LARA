import os
from pathlib import Path

# Ubicación real del código de LARA (verdad de hechos, no algo que el modelo deba adivinar)
PROYECTO_DIR = Path(__file__).resolve().parent

# El .env se carga aquí (y no solo en llm_engine) para que las variables de
# rutas y perfil estén disponibles antes de calcular BASE_DIR.
try:
    from dotenv import load_dotenv
    load_dotenv(PROYECTO_DIR / ".env")
except ImportError:
    pass

from core.recursos import aplicar as _aplicar_recursos
PERFIL_RECURSOS = _aplicar_recursos()

# --- Carpeta base de datos ---
# Windows define automáticamente la variable de entorno "OneDriveCommercial" cuando
# el equipo está vinculado a una cuenta institucional/empresarial (ej. universidad),
# y "OneDrive" para cuentas personales.
#
# A-06: LARA_DATA_DIR permite elegir el directorio de datos explícitamente
# (p. ej. una carpeta local que NO se sincroniza). LARA_DATA_LOCAL=1 fuerza la
# carpeta de usuario aunque OneDrive exista. Sin ninguna de las dos se mantiene
# el comportamiento histórico (OneDrive si existe) para no "perder" datos ya
# guardados allí.
_ONEDRIVE = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive")
_DATA_DIR = os.environ.get("LARA_DATA_DIR", "").strip()
_DATA_LOCAL = os.environ.get("LARA_DATA_LOCAL", "0").strip() == "1"

if _DATA_DIR:
    BASE_DIR = Path(_DATA_DIR).expanduser()
elif _DATA_LOCAL:
    BASE_DIR = Path.home() / "Taller_IA"
elif _ONEDRIVE:
    BASE_DIR = Path(_ONEDRIVE) / "Taller_IA"
else:
    print("[ADVERTENCIA] No se detectó OneDrive vinculado en este equipo. "
          "Usando carpeta local de respaldo -- tus datos NO se sincronizarán entre PCs.")
    BASE_DIR = Path.home() / "Taller_IA"

WORKSPACE = BASE_DIR / "workspace"
KNOWLEDGE_DIR = BASE_DIR / "conocimiento"
SKILLS_PENDIENTES = BASE_DIR / "skills_pendientes"
# Carpeta que LARA indexa para el RAG (responder desde TUS documentos: PDF, Word,
# notas). Deja aquí lo que quieras que "sepa". También indexa WORKSPACE.
DOCUMENTOS_RAG = BASE_DIR / "documentos_rag"
ESCRITORIO = Path.home() / "Desktop"
DESCARGAS = Path.home() / "Downloads"
DOCUMENTOS = Path.home() / "Documents"

CORE_DIR = PROYECTO_DIR / "core"

# Atajos de carpetas por nombre, para que LARA resuelva "escritorio", "descargas", "core", etc.
# sin que el modelo tenga que adivinar la ruta completa de Windows NI "recordarla" por chat
# (el historial de conversación no persiste entre reinicios del programa -- esto sí).
CARPETAS_CONOCIDAS = {
    "escritorio": ESCRITORIO,
    "desktop": ESCRITORIO,
    "descargas": DESCARGAS,
    "downloads": DESCARGAS,
    "documentos": DOCUMENTOS,
    "documents": DOCUMENTOS,
    "workspace": WORKSPACE,
    "taller": WORKSPACE,
    "core": CORE_DIR,
    "skills": CORE_DIR,
    "skills_pendientes": SKILLS_PENDIENTES,  # permite resolver "skills_pendientes/archivo.py" en mover_archivo
    "pendientes": SKILLS_PENDIENTES,
}

# Aseguramos que las carpetas base existan
for carpeta in [WORKSPACE, KNOWLEDGE_DIR, SKILLS_PENDIENTES, DOCUMENTOS_RAG]:
    carpeta.mkdir(parents=True, exist_ok=True)

# Identidad
ADMIN_NOMBRE = "Adi"
AGENTE_NOMBRE = "LARA"

# Herramientas de Taller (Windows nativo)
HERRAMIENTAS = {
    "files":      ["explorer.exe"],
    "terminal":   ["cmd.exe"],
    "powershell": ["powershell.exe"],
    "browser":    ["cmd.exe", "/c", "start", "msedge"],
    "chrome":     ["cmd.exe", "/c", "start", "chrome"],
    "code":       ["code"],             # VS Code
    "editor":     ["notepad.exe"],
    "calc":       ["calc.exe"],
    "taskmgr":    ["taskmgr.exe"],
    "inventario": ["python", str(BASE_DIR / "inventario.py")],
}
