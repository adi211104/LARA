"""
modos.py — Modos de operación de LARA (estilo Claude Code).

Controlan CUÁNTA autonomía tiene LARA para ejecutar acciones que tocan tu
equipo. Igual que en Claude Code alternas entre auto / manual / plan, aquí:

  - auto     : ejecuta TODO sin preguntar (máxima autonomía, como hasta ahora).
  - manual   : pide CONFIRMACIÓN antes de acciones de RIESGO (comandos, código,
               mover/borrar archivos, descargas, escribir/clic, instalar skills).
               Lo inocuo (charla, consultas, hora, abrir programas, buscar) va directo.
  - plan     : NO ejecuta nada; DESCRIBE lo que haría y espera tu aprobación.
  - lectura  : solo responde / consulta / mira; BLOQUEA toda acción que modifique
               el equipo (útil para preguntar sin miedo a que "haga" algo).

El modo persiste en KNOWLEDGE_DIR/modo.json y se cambia por voz/texto
("modo auto", "modo manual", "modo plan", "modo lectura") o desde la GUI.

Además centraliza:
  - la clasificación de acciones en riesgo (para el gate de confirmación),
  - la AUDITORÍA: toda acción de riesgo se registra en logs/auditoria.log.
"""

import json
from datetime import datetime

from config import KNOWLEDGE_DIR, BASE_DIR

NIVELES = ["auto", "manual", "plan", "lectura"]
_DEFECTO = "manual"          # arranque seguro: pide confirmación de lo riesgoso
_MODO_PATH = KNOWLEDGE_DIR / "modo.json"
_AUDIT_PATH = BASE_DIR / "logs" / "auditoria.log"

_modo_actual = None          # se carga perezosamente en la 1ª consulta

# Descripción legible por modo (para mensajes/GUI).
DESCRIPCION = {
    "auto":    "Autónomo — ejecuto todo sin preguntar.",
    "manual":  "Manual — te pido confirmación antes de acciones de riesgo.",
    "plan":    "Plan — describo lo que haría y espero tu aprobación; no ejecuto nada.",
    "lectura": "Solo lectura — respondo y consulto, pero no modifico tu equipo.",
}

# ── Clasificación de acciones ────────────────────────────────────────────────
# Acciones del router que MODIFICAN el equipo o EJECUTAN cosas (las "de riesgo").
# Todo lo que no esté aquí (hablar, buscar, web, url, listar, leer, analizar_*,
# explicar, guardar_conocimiento, recordatorio, gestion, sistema-lectura) es inocuo.
ACCIONES_RIESGO = {
    "cmd", "cmd_admin", "ejecutar_codigo", "ejecutar_codigo_visible",
    "mover_archivo", "copiar_archivo", "crear",
    "escribir", "escribir_en", "abrir_y_escribir", "clic", "presionar", "teclas",
    "descargar", "descargar_video",
    "proponer_skill", "aceptar_propuesta", "ejecutar_skill",
}

# 'sistema' agrupa muchas operaciones; solo unas pocas son destructivas.
# El resto (hora, batería, volumen, brillo, captura...) es inocuo.
SISTEMA_OPS_RIESGO = {
    "papelera", "vaciar_papelera", "apagar", "reiniciar", "cerrar_sesion", "hibernar",
}


def _es_riesgo(accion: str, params) -> bool:
    accion = (accion or "").strip()
    if accion == "sistema":
        op = (params[0] if params else "").strip().lower()
        return op in SISTEMA_OPS_RIESGO
    return accion in ACCIONES_RIESGO


# ── Persistencia del modo ────────────────────────────────────────────────────
def _cargar_modo() -> str:
    try:
        with open(_MODO_PATH, "r", encoding="utf-8") as f:
            m = json.load(f).get("modo", _DEFECTO)
        return m if m in NIVELES else _DEFECTO
    except Exception:
        return _DEFECTO


def get_modo() -> str:
    global _modo_actual
    if _modo_actual is None:
        _modo_actual = _cargar_modo()
    return _modo_actual


def set_modo(modo: str) -> str:
    """Cambia el modo activo y lo persiste. Modo desconocido -> se ignora."""
    global _modo_actual
    modo = (modo or "").strip().lower()
    if modo not in NIVELES:
        return get_modo()
    _modo_actual = modo
    try:
        _MODO_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_MODO_PATH, "w", encoding="utf-8") as f:
            json.dump({"modo": modo}, f, ensure_ascii=False)
    except Exception:
        pass  # persistir es deseable, no crítico: queda activo en memoria
    return _modo_actual


# Frases que cambian de modo (para detectarlas en el cerebro).
_FRASES_MODO = {
    "auto":    ("modo auto", "modo automatico", "modo automático", "modo autonomo",
                "modo autónomo", "autonomia total", "autonomía total", "haz todo sin preguntar"),
    "manual":  ("modo manual", "modo confirmacion", "modo confirmación", "preguntame antes",
                "pregúntame antes", "pide confirmacion", "pide confirmación"),
    "plan":    ("modo plan", "modo planificacion", "modo planificación", "solo planifica",
                "no ejecutes", "solo dime que harias", "solo dime qué harías"),
    "lectura": ("modo lectura", "solo lectura", "modo consulta", "no toques nada",
                "no modifiques nada"),
}


def detectar_cambio_modo(texto: str):
    """Si 'texto' pide cambiar de modo, devuelve el modo destino; si no, None."""
    t = (texto or "").lower().strip().strip(".!?¡¿")
    for modo, frases in _FRASES_MODO.items():
        if any(f == t or t.startswith(f) for f in frases):
            return modo
    return None


# ── Decisión del gate ────────────────────────────────────────────────────────
# Resultado de evaluar una acción contra el modo activo:
EJECUTAR = "ejecutar"        # adelante, sin preguntar
CONFIRMAR = "confirmar"      # pide confirmación al usuario antes
BLOQUEAR = "bloquear"        # no ejecutar (plan/lectura); solo describir


def evaluar(accion: str, params=None) -> tuple:
    """
    Decide qué hacer con una acción según el modo activo.
    Devuelve (decision, motivo):
      - EJECUTAR  : ejecutarla directamente.
      - CONFIRMAR : preguntar al usuario primero.
      - BLOQUEAR  : no ejecutarla; 'motivo' explica por qué (para decírselo).
    """
    params = params or []
    modo = get_modo()
    riesgo = _es_riesgo(accion, params)

    if not riesgo:
        # Lo inocuo se ejecuta en cualquier modo salvo que quieras un "plan" puro.
        # En 'plan' aun así dejamos pasar consultas/lectura (no modifican nada).
        return EJECUTAR, ""

    if modo == "auto":
        return EJECUTAR, ""
    if modo == "manual":
        return CONFIRMAR, ""
    if modo == "plan":
        return BLOQUEAR, ("En modo plan no ejecuto acciones. Esto es lo que haría. "
                          "Di 'modo auto' o 'modo manual' para que lo lleve a cabo.")
    if modo == "lectura":
        return BLOQUEAR, ("Estoy en modo solo lectura: no modifico tu equipo. "
                          "Cambia a 'modo manual' o 'modo auto' si quieres que lo haga.")
    return EJECUTAR, ""


# ── Auditoría ────────────────────────────────────────────────────────────────
def auditar(accion: str, params=None, resultado: str = "") -> None:
    """Registra una acción de riesgo en logs/auditoria.log (append, best-effort)."""
    try:
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p = json.dumps(params or [], ensure_ascii=False)
        linea = f"{ts} [{get_modo()}] {accion} params={p}"
        if resultado:
            linea += f" -> {resultado[:200]}"
        with open(_AUDIT_PATH, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass  # la auditoría nunca debe tumbar una acción
