"""
turno_habla.py — Detección SEMÁNTICA de fin de turno (low-latency semantic
turn detection).

El VAD clásico de skills_audio.grabar_vad() corta la grabación por SILENCIO
FÍSICO: si callas 0.8 s, corta, sin importar si tu idea está completa o
solo hiciste una pausa cognitiva ("y entonces... a ver... fuimos al cine").
Este módulo añade una COMPUERTA que evalúa el texto parcial antes de dejar
cortar, para distinguir "silencio = terminé" de "silencio = estoy pensando".

Dos capas, de más rápida a más lenta (así casi nunca se paga la más lenta):
  1. HEURÍSTICA (gratis, <1 ms): si la transcripción parcial termina en una
     preposición/conjunción/artículo, la idea está CLARAMENTE incompleta.
     Si termina en puntuación terminal (. ? ! …), está CLARAMENTE completa.
     Cubre la gran mayoría de los casos sin gastar una sola llamada al modelo.
  2. EVALUADOR LOCAL (Ollama qwen2.5:3b, el mismo que ya usa LARA para el
     router — core/llm_local.py): solo para los casos AMBIGUOS que la
     heurística no resuelve. Prompt estático y salida de una palabra
     (COMPLETO/INCOMPLETO), así el coste en CPU es mínimo.

quien llama a evaluar_turno() decide qué hacer con el resultado; este
módulo no toca el micrófono ni el hilo de grabación (eso es
skills_audio._grabar_vad_impl, que usa esto como compuerta antes de cortar).

Se puede apagar por completo (vuelve al corte por silencio de siempre) con
la variable de entorno LARA_TURNO_SEMANTICO=0.
"""

import os
import re

ACTIVO = os.environ.get("LARA_TURNO_SEMANTICO", "1").strip().lower() not in ("0", "false", "no", "")

# Palabras que EXIGEN una continuación gramatical: si la frase transcrita
# termina en una de estas, el hablante casi seguro no terminó la idea.
_PALABRAS_INCOMPLETAS = {
    # preposiciones
    "de", "del", "a", "al", "ante", "bajo", "con", "contra", "desde", "en",
    "entre", "hacia", "hasta", "para", "por", "según", "segun", "sin",
    "sobre", "tras", "durante", "mediante",
    # conjunciones / nexos
    "y", "e", "o", "u", "pero", "sino", "aunque", "porque", "que", "si",
    "como", "cuando", "mientras", "ni", "pues",
    # artículos y determinantes que piden sustantivo
    "el", "la", "los", "las", "un", "una", "unos", "unas", "mi", "mis",
    "tu", "tus", "su", "sus", "este", "esta", "estos", "estas", "ese",
    "esa", "esos", "esas", "aquel", "aquella",
    # adverbios de grado que piden complemento
    "muy", "tan", "más", "mas", "menos",
}

_RE_TERMINAL = re.compile(r"[\.\?\!…]$")


def _ultima_palabra(texto: str) -> str:
    t = texto.strip().strip("¿¡").rstrip(".,;:!?…\"' ")
    partes = t.split()
    return partes[-1].lower() if partes else ""


def es_claramente_incompleto(texto: str) -> bool:
    """Heurística instantánea: ¿la frase termina en una palabra que exige más?"""
    if not texto or not texto.strip():
        return True
    return _ultima_palabra(texto) in _PALABRAS_INCOMPLETAS


def es_claramente_completo(texto: str) -> bool:
    """Heurística instantánea: ¿termina en puntuación terminal con cuerpo?"""
    t = texto.strip()
    return len(t) >= 4 and bool(_RE_TERMINAL.search(t)) and not es_claramente_incompleto(t)


# Prompt operativo del evaluador: ESTÁTICO (mismo texto siempre) para que
# Ollama pueda cachear el prefijo y responder rápido incluso en CPU.
_PROMPT_SISTEMA = (
    "Eres un analizador semántico de turnos de habla. Analiza la siguiente "
    "transcripción parcial de voz a texto y determina si el emisor ha "
    "finalizado su idea. Evalúa la completitud sintáctica y el contexto "
    "semántico. Si el texto termina en preposiciones, conjunciones, "
    "artículos o presenta una estructura fragmentada carente de resolución, "
    "el turno sigue activo. Ignora la presencia de muletillas. Devuelve "
    "ÚNICAMENTE la palabra \"COMPLETO\" o \"INCOMPLETO\". Cero explicaciones "
    "adicionales."
)


def evaluar_con_modelo(texto: str) -> str:
    """
    Consulta al evaluador local (Ollama) si el turno está COMPLETO o
    INCOMPLETO. Fail-open: si el modelo no está disponible, tarda demasiado
    o falla, devuelve "COMPLETO" (mejor cortar como el VAD clásico que dejar
    la grabación colgada para siempre esperando un evaluador que no llega).
    """
    try:
        from core import llm_local
        if not llm_local.disponible():
            return "COMPLETO"
        salida = llm_local.chat(
            [{"role": "system", "content": _PROMPT_SISTEMA},
             {"role": "user", "content": texto.strip()}],
            max_tokens=4, temperature=0.0, timeout=2.5,
        )
        if not salida:
            return "COMPLETO"
        return "INCOMPLETO" if "INCOMPLETO" in salida.upper() else "COMPLETO"
    except Exception:
        return "COMPLETO"


def evaluar_turno(texto: str) -> bool:
    """
    True si el turno de habla parece COMPLETO (se puede cortar y procesar).
    False si parece INCOMPLETO (hay que seguir grabando, el hablante sigue
    con la idea). Combina la heurística instantánea con el evaluador local,
    que solo se consulta cuando el caso es ambiguo.
    """
    if not ACTIVO:
        return True  # función apagada: comportarse como el VAD clásico
    if not texto or not texto.strip():
        return False  # nada reconocible todavía: no hay qué cortar, seguir
    if es_claramente_incompleto(texto):
        return False
    if es_claramente_completo(texto):
        return True
    return evaluar_con_modelo(texto) == "COMPLETO"
