"""
rutinas.py — Presencia proactiva de LARA (Mejora 6, estilo JARVIS).

JARVIS no espera a que le pregunten: al aparecer te saluda por tu nombre según
la hora y te pone al día ("tienes 2 tareas urgentes, una vence hoy"). Este
módulo arma ese "briefing" a partir de datos que Lara YA tiene (tareas urgentes,
compras pendientes, novedades del agente de fondo) y lo devuelve como texto para
mostrarlo y/o decirlo en voz alta.

Todo es a prueba de fallos: si una fuente falla, se omite y el resto del
briefing se arma igual. Nunca lanza excepción hacia el llamador.
"""

import json
from datetime import datetime

from config import ADMIN_NOMBRE, KNOWLEDGE_DIR


def _saludo_por_hora() -> str:
    h = datetime.now().hour
    if 5 <= h < 12:
        return f"Buenos días, {ADMIN_NOMBRE}"
    if 12 <= h < 19:
        return f"Buenas tardes, {ADMIN_NOMBRE}"
    return f"Buenas noches, {ADMIN_NOMBRE}"


def _linea_tareas() -> str:
    try:
        from core.skills_tasks import tareas_pendientes_urgentes
        urgentes = tareas_pendientes_urgentes() or []
    except Exception:
        return ""
    if not urgentes:
        return ""
    n = len(urgentes)
    hoy = [t for t in urgentes if t.get("dias_restantes", 9) <= 0]
    if hoy:
        detalle = hoy[0].get("titulo", "una tarea")
        extra = f", una vence hoy: {detalle}" if len(hoy) == 1 else f", {len(hoy)} vencen hoy"
    else:
        prox = min(urgentes, key=lambda t: t.get("dias_restantes", 9))
        extra = f"; la más próxima, {prox.get('titulo','')}, en {prox.get('dias_restantes','?')} días"
    palabra = "tarea urgente" if n == 1 else "tareas urgentes"
    return f"Tienes {n} {palabra}{extra}."


def _linea_compras() -> str:
    try:
        from core.skills_tasks import compra_listar
        pend = compra_listar(filtro_estado="pendiente").get("compras", [])
    except Exception:
        return ""
    if not pend:
        return ""
    n = len(pend)
    return f"Hay {n} compra{'s' if n != 1 else ''} pendiente{'s' if n != 1 else ''} por gestionar."


def _linea_novedades() -> str:
    try:
        from core.skills_novedades import listar_novedades
        nov = listar_novedades(solo_no_leidas=True) or []
    except Exception:
        return ""
    if not nov:
        return ""
    n = len(nov)
    return f"Encontré {n} novedad{'es' if n != 1 else ''} mientras no estabas; dime \"novedades\" para verlas."


def briefing(incluir_saludo: bool = True) -> str:
    """
    Devuelve el resumen de arranque como una sola cadena hablable. Si no hay
    nada pendiente, devuelve un saludo corto (JARVIS elegante, no silencio).
    """
    partes = []
    if incluir_saludo:
        partes.append(_saludo_por_hora() + ".")
    cuerpo = [l for l in (_linea_tareas(), _linea_compras(), _linea_novedades()) if l]
    if cuerpo:
        partes.extend(cuerpo)
        partes.append("¿Por dónde empezamos?")
    else:
        partes.append("Todo en orden por ahora. Te escucho.")
    return " ".join(partes).strip()


def saludar(on_speak=None, imprimir=None) -> str:
    """
    Genera el briefing y lo entrega: lo habla (si on_speak) y/o lo muestra (si
    imprimir). Devuelve el texto. Pensado para llamarse al arrancar LARA.
    """
    texto = briefing()
    if imprimir:
        try:
            imprimir(texto)
        except Exception:
            pass
    if on_speak:
        try:
            on_speak(texto)
        except Exception:
            pass
    return texto


# =========================================================
#  RUTINAS PROGRAMABLES (Mejora c): "modo taller", "modo noche"...
# =========================================================
#
# Una rutina es una lista de PASOS que LARA ejecuta en orden con una sola frase
# ("modo noche"). Cada paso es un dict con "tipo" y sus datos:
#   {"tipo":"hablar","texto":"..."}       -> lo dice/anota
#   {"tipo":"brillo","nivel":30}          -> ajusta el brillo (0-100)
#   {"tipo":"sistema","op":"silenciar"}   -> operación de sistema (silenciar, etc.)
#   {"tipo":"abrir","programa":"code"}    -> abre un programa
#   {"tipo":"tareas"}                     -> resume tus tareas pendientes
#   {"tipo":"briefing"}                   -> inserta el briefing del día
#
# Hay rutinas de fábrica (abajo) y el usuario puede añadir/editar las suyas en
# KNOWLEDGE_DIR/rutinas.json (se combinan; las del usuario ganan si coinciden).

_RUTINAS_FABRICA = {
    "modo taller": {
        "disparadores": ["modo taller", "a trabajar", "modo trabajo", "activa el taller"],
        "pasos": [
            {"tipo": "hablar", "texto": f"Activando modo taller, {ADMIN_NOMBRE}. Preparando todo."},
            {"tipo": "brillo", "nivel": 85},
            {"tipo": "abrir", "programa": "code"},
            {"tipo": "tareas"},
            {"tipo": "hablar", "texto": "Listo. A trabajar."},
        ],
    },
    "modo noche": {
        "disparadores": ["modo noche", "buenas noches lara", "me voy a dormir", "hora de dormir"],
        "pasos": [
            {"tipo": "hablar", "texto": "Activando modo noche. Bajo el brillo y silencio el equipo."},
            {"tipo": "brillo", "nivel": 25},
            {"tipo": "sistema", "op": "silenciar"},
            {"tipo": "tareas"},
            {"tipo": "hablar", "texto": f"Eso es todo por hoy. Descansa, {ADMIN_NOMBRE}."},
        ],
    },
    "modo concentracion": {
        "disparadores": ["modo concentracion", "modo concentración", "modo enfoque", "no me molestes"],
        "pasos": [
            {"tipo": "sistema", "op": "silenciar"},
            {"tipo": "brillo", "nivel": 70},
            {"tipo": "hablar", "texto": "Modo concentración activado. Silencio puesto. Tú a lo tuyo."},
        ],
    },
    "buenos dias": {
        "disparadores": ["buenos dias", "buenos días", "buen dia", "modo mañana"],
        "pasos": [
            {"tipo": "brillo", "nivel": 90},
            {"tipo": "briefing"},
        ],
    },
}

_RUTINAS_USUARIO = KNOWLEDGE_DIR / "rutinas.json"


def _cargar_rutinas() -> dict:
    """Combina rutinas de fábrica con las del usuario (KNOWLEDGE_DIR/rutinas.json)."""
    rutinas = {k: dict(v) for k, v in _RUTINAS_FABRICA.items()}
    if _RUTINAS_USUARIO.exists():
        try:
            extra = json.loads(_RUTINAS_USUARIO.read_text(encoding="utf-8"))
            if isinstance(extra, dict):
                rutinas.update(extra)
        except Exception:
            pass
    return rutinas


def _norm(t: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", (t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return " ".join(t.replace(",", " ").split())


def detectar_rutina(prompt: str):
    """Devuelve el nombre de la rutina si el prompt la dispara, o None."""
    n = _norm(prompt)
    if not n:
        return None
    for nombre, r in _cargar_rutinas().items():
        for disp in r.get("disparadores", [nombre]):
            dn = _norm(disp)
            if n == dn or n.startswith(dn) or (len(dn) >= 6 and dn in n):
                return nombre
    return None


def _accion_de_paso(paso: dict):
    """Traduce un paso con efecto a (accion, params) del gate de modos, o None
    si el paso solo habla o consulta."""
    tipo = paso.get("tipo", "")
    if tipo == "brillo":
        return "ajustar_brillo", [str(paso.get("nivel", 50))]
    if tipo == "sistema":
        return "sistema", [paso.get("op", ""), paso.get("valor", "")]
    if tipo == "abrir":
        return "abrir", [paso.get("programa", "")]
    return None


def _ejecutar_paso(paso: dict, autorizar=None) -> str:
    """Ejecuta un paso y devuelve la frase que LARA debería decir (o "").

    Los pasos con efecto solo se ejecutan si 'autorizar(accion, params)'
    devuelve True. Sin callback se deniegan: una rutina nunca se salta la
    política de modos (A-01)."""
    tipo = paso.get("tipo", "")
    accion = _accion_de_paso(paso)
    if accion and not (autorizar and autorizar(*accion)):
        return ""
    try:
        if tipo == "hablar":
            return paso.get("texto", "")
        if tipo == "brillo":
            from core.skills_os import ajustar_brillo
            ajustar_brillo(int(paso.get("nivel", 50)))
            return ""
        if tipo == "sistema":
            from core.skills_sistema import ejecutar as sistema_ejecutar
            r = sistema_ejecutar(paso.get("op", ""), paso.get("valor", ""))
            return "" if isinstance(r, dict) and r.get("error") else ""
        if tipo == "abrir":
            from core.skills_programas import abrir_programa_inteligente
            abrir_programa_inteligente(paso.get("programa", ""))
            return ""
        if tipo == "tareas":
            from core.skills_tasks import tarea_listar
            data = tarea_listar("")
            tareas = data.get("tareas", []) if isinstance(data, dict) else []
            pend = [t for t in tareas if str(t.get("estado", "")).lower() != "completada"]
            if not pend:
                return "No tienes tareas pendientes."
            top = pend[:3]
            return "Tienes " + str(len(pend)) + " tareas pendientes. Las próximas: " + \
                   "; ".join(t.get("titulo", "") for t in top) + "."
        if tipo == "briefing":
            return briefing(incluir_saludo=True)
    except Exception:
        return ""
    return ""


def ejecutar_rutina(nombre: str, on_speak=None, imprimir=None, autorizar=None) -> str:
    """
    Ejecuta la rutina 'nombre' paso a paso. Habla cada frase (on_speak) y/o la
    muestra (imprimir). Devuelve el texto completo (para el historial/GUI).
    """
    rutinas = _cargar_rutinas()
    r = rutinas.get(nombre)
    if not r:
        return ""
    dichas = []
    for paso in r.get("pasos", []):
        frase = _ejecutar_paso(paso, autorizar)
        if frase and frase.strip():
            dichas.append(frase.strip())
            if imprimir:
                try:
                    imprimir(frase.strip())
                except Exception:
                    pass
            if on_speak:
                try:
                    on_speak(frase.strip())
                except Exception:
                    pass
    return "\n".join(dichas).strip()


def listar_rutinas() -> list:
    """Nombres de rutinas disponibles (fábrica + usuario)."""
    return list(_cargar_rutinas().keys())
