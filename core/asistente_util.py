"""Comandos cotidianos deterministas: sin tokens ni ejecución de código."""
import re
from datetime import datetime

AYUDA = """Estas son algunas cosas que puedes pedirme:
• mis tareas / nueva tarea revisar el amplificador / completar tarea 3
• mis recordatorios / cancelar recordatorio 2
• temporizador 10 minutos / pomodoro (25 minutos)
• recuérdame revisar el horno en 15 minutos
• anota comprar resistencias
• qué hora es / batería / info del sistema
• abre calculadora / abre descargas
• diagnóstico / estado de documentos
• mis motores (configuración local y disponibilidad de claves)
• aprendizaje / activar aprendizaje / respuesta correcta / corrige respuesta: texto
• estado del gateway (comprueba conexión local sin mostrar claves)
• sé breve / respuestas largas / repite / silencio
Las tareas, temporizadores y recordatorios de esta lista funcionan sin IA.
Los recordatorios vencidos se recuperan al abrir LARA; el programa debe estar abierto para avisar a tiempo.
Visión, investigación y conversación avanzada dependen de los motores configurados."""


def resolver(texto, autorizar):
    """None si no reconoce la frase. Cada escritura consulta la política."""
    from core import recordatorios as rec, skills_tasks as tareas
    t = texto.strip().rstrip(".!?¿¡").lower()
    if t in ("ayuda", "qué puedes hacer", "que puedes hacer", "trucos", "habilidades"):
        return AYUDA
    if t in ("mis motores", "perfil de rendimiento"):
        import os
        from core.gateway_llm import activo as gateway_activo
        from config import PERFIL_RECURSOS
        return (f"Perfil {PERFIL_RECURSOS['perfil']}: {PERFIL_RECURSOS['ram_gb']} GB RAM, "
                f"{PERFIL_RECURSOS['cpu_hilos']} hilos CPU.\n"
                f"Whisper: {os.getenv('LARA_WHISPER_MODEL')}. "
                f"Ollama: {os.getenv('LARA_OLLAMA_MODEL', 'qwen2.5:3b-instruct')}, "
                f"contexto {os.getenv('LARA_OLLAMA_NUM_CTX')}.\n"
                f"Clave Groq: {'configurada' if os.getenv('GROQ_API_KEY') else 'ausente'}. "
                f"Clave Gemini: {'configurada' if os.getenv('GEMINI_API_KEY') else 'ausente'}.\n"
                f"Gateway opcional: {'activado' if gateway_activo() else 'apagado'}.\n"
                "Claves configuradas no garantizan conexión ni cuota. Usa diagnóstico para comprobar motores.")
    if t == 'estado del gateway':
        from core.gateway_llm import diagnostico
        return str(diagnostico())
    if t in ("mis tareas", "tareas pendientes"):
        filas = tareas.tarea_listar("pendiente")["tareas"]
        return "\n".join(f"#{f['id']} · {f['titulo']}" for f in filas) or "No tienes tareas pendientes."
    if t in ("mis recordatorios", "recordatorios pendientes"):
        return "\n".join(f"#{f['id']} · {datetime.fromtimestamp(f['vence']):%d/%m %H:%M} · {f['texto']}" for f in rec.listar()) or "No tienes recordatorios pendientes."
    nueva = re.fullmatch(r"nueva tarea\s+(.+)", texto.strip(), re.I)
    completa = re.fullmatch(r"completar tarea\s+(\d+)", t)
    cancela = re.fullmatch(r"cancelar recordatorio\s+(\d+)", t)
    timer = re.fullmatch(r"temporizador\s+(\d+(?:[.,]\d+)?)\s*(segundos?|minutos?|horas?)", t)
    if not any((nueva, completa, cancela, timer, t == "pomodoro")):
        return None
    accion = "gestion" if nueva or completa else "recordatorio_rapido"
    permitido, motivo = autorizar(accion, [texto])
    if not permitido:
        return motivo
    if nueva:
        r = tareas.tarea_crear(nueva[1].strip())
        return f"Tarea #{r['tarea']['id']} guardada: {r['tarea']['titulo']}"
    if completa:
        r = tareas.tarea_completar(int(completa[1]))
        return r.get("error") or f"Tarea #{completa[1]} completada."
    if cancela:
        return "Recordatorio cancelado." if rec.cancelar(int(cancela[1])) else "No encontré ese recordatorio pendiente."
    minutos = 25.0 if t == "pomodoro" else float(timer[1].replace(",", ".")) * (1 / 60 if timer[2].startswith("segundo") else 60 if timer[2].startswith("hora") else 1)
    from core.agente_fondo import recordatorio_rapido
    r = recordatorio_rapido("Terminó tu sesión de concentración. Haz una pausa." if t == "pomodoro" else "Terminó tu temporizador.", minutos)
    return r.get("error") or f"Programado: #{r['id']}, en {minutos:g} minutos. Puedes consultarlo con 'mis recordatorios'."
