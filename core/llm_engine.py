"""
llm_engine.py — LARA, arquitectura HÍBRIDA de 4 motores (local + nube)

0. QWEN 2.5 3B (Ollama, LOCAL) -> reflejo rápido para TAREAS SIMPLES.
   "abre chrome", "qué hora es", "sube el brillo", charla corta... se
   resuelven EN TU PC en ~2-4 s, sin internet, sin límites y sin gastar
   cuota de la nube. Usa un prompt de router ESTÁTICO para que Ollama
   cachee el prefijo (en frío tardaría ~15 s; cacheado, ~2-4 s).
   También es el RESPALDO de emergencia cuando toda la nube está caída.

1. ROUTER NUBE (Groq, MODELO_ROUTER) -> orquestador para lo demás.
   Decide qué acción tomar (incl. JSON estructurado) cuando la frase es
   larga/compleja o el nivel de esfuerzo es alto (extra/max/ultracode).

2. GEMINI/GEMMA (Google, MODELO_GEMINI) + GPT-OSS-120B (Groq, MODELO_PROFUNDO)
   -> cerebro de razonamiento profundo, BÚSQUEDAS EN INTERNET (resúmenes
   de resultados y páginas) y generación de código de skills. Lo "fuerte"
   SIEMPRE va a la nube: un 3B local no compite aquí.

3. COMPOUND (Groq, MODELO_AGENTE) -> agente de gestión.
   Tareas, clientes, compras, proyectos, recordatorios e investigación
   autónoma de fondo. Sin cambios de rol.

El usuario solo ve a "LARA" -- la arquitectura híbrida es interna.
"""

import os
import re
import json
import time
import requests
from google import genai
from google.genai import types
from groq import Groq
from dotenv import load_dotenv
from config import PROYECTO_DIR, BASE_DIR, WORKSPACE, KNOWLEDGE_DIR, ESCRITORIO, ADMIN_NOMBRE
try:
    from core.uso_tokens import registrar_groq, registrar_genai
except ImportError:
    from uso_tokens import registrar_groq, registrar_genai

load_dotenv()

gemini_api_key = os.getenv("GEMINI_API_KEY")
groq_api_key   = os.getenv("GROQ_API_KEY")
serper_key     = os.getenv("SERPER_API_KEY")

if not gemini_api_key:
    print("[ADVERTENCIA] No se encontró GEMINI_API_KEY en el archivo .env (necesaria para razonamiento profundo)")
if not groq_api_key:
    print("[ADVERTENCIA] No se encontró GROQ_API_KEY en el archivo .env (necesaria para orquestación y compound)")
if not serper_key:
    print("[ADVERTENCIA] No se encontró SERPER_API_KEY en el archivo .env")

# Timeouts: evitan que una llamada colgada deje a LARA congelada para siempre.
genai_client = genai.Client(api_key=gemini_api_key,
                            http_options=types.HttpOptions(timeout=60000))  # ms
client = Groq(api_key=groq_api_key, timeout=45.0, max_retries=2)  # router y compound

# Cadena de routers por orden de preferencia. gpt-oss-120b y qwen3.6-27b son los
# reemplazos oficiales que recomendó Groq al retirar llama-3.3-70b-versatile
# (decomisionado el 16/08/2026; LARA ya NO lo usa). Si un modelo falla
# (429/403/retirado), LARA baja al siguiente automáticamente y recuerda cuál
# funcionó. qwen3-32b tiene límites de tasa más altos (60 RPM / 500K tok-día):
# buen colchón cuando gpt-oss-120b (30 RPM / 200K) se satura en ráfagas.
ROUTERS = [
    "openai/gpt-oss-120b",      # 1º: el más inteligente disponible gratis
    "qwen/qwen3.6-27b",         # 2º: reemplazo alterno recomendado por Groq
    "qwen/qwen3-32b",           # 3º: más cuota (60 RPM/500K), aguanta ráfagas
    "llama-3.1-8b-instant",     # 4º: ultrarrápido, último recurso
]
_router_idx = 0                  # índice del router activo dentro de la cadena del nivel (se auto-ajusta)
_router_nivel = None             # nivel con el que se calculó _router_idx (si cambia, se resetea)

MODELO_ROUTER  = ROUTERS[0]
MODELO_GEMINI  = "gemini-2.5-flash"         # visión/helpers (Google): rápido, sin bucle MAX_TOKENS
MODELO_PROFUNDO = "openai/gpt-oss-120b"     # razonamiento profundo (Groq): nivel frontier
MODELO_AGENTE  = "groq/compound"            # gestor de tareas/clientes/compras/proyectos (Groq)
MODELO         = MODELO_ROUTER               # alias por compatibilidad con código viejo


# =========================================================
#  NIVELES DE ESFUERZO: cuánta "potencia" gasta LARA por consulta.
#
#  ¿Por qué? No toda pregunta merece el modelo más caro con el máximo de
#  tokens: "qué hora es" va bien con el 8B instantáneo, pero depurar un
#  circuito o programar merece gpt-oss-120b con presupuesto amplio. El
#  usuario elige el nivel desde la GUI y aquí se traduce a: qué router se
#  intenta PRIMERO (el fallback de ROUTERS sigue intacto), cuántos tokens
#  se dan al router y a razonar_profundo, y si hay doble pasada de
#  auto-revisión (ultracode). El nivel persiste en KNOWLEDGE_DIR para
#  sobrevivir reinicios.
# =========================================================

NIVELES_ESFUERZO = ["bajo", "medio", "alto", "extra", "max", "ultracode"]

# Configuración por nivel:
#   router          -> modelo Groq que se intenta primero (el resto de ROUTERS queda de fallback)
#   tokens_router   -> max_tokens inicial del orquestador (consultar_lara)
#   tokens_profundo -> max_tokens de razonar_profundo
#   temperatura     -> temperatura del router
#   doble_pasada    -> si razonar_profundo hace una 2ª llamada de auto-revisión
_CONFIG_NIVELES = {
    "bajo":      {"router": "llama-3.1-8b-instant", "tokens_router": 700,  "tokens_profundo": 900,  "temperatura": 0.2, "doble_pasada": False},
    "medio":     {"router": "qwen/qwen3.6-27b",     "tokens_router": 900,  "tokens_profundo": 1300, "temperatura": 0.3, "doble_pasada": False},
    "alto":      {"router": "openai/gpt-oss-120b",  "tokens_router": 1100, "tokens_profundo": 1800, "temperatura": 0.3, "doble_pasada": False},
    "extra":     {"router": "openai/gpt-oss-120b",  "tokens_router": 1600, "tokens_profundo": 2600, "temperatura": 0.3, "doble_pasada": False},
    "max":       {"router": "openai/gpt-oss-120b",  "tokens_router": 2200, "tokens_profundo": 4000, "temperatura": 0.3, "doble_pasada": False},
    "ultracode": {"router": "openai/gpt-oss-120b",  "tokens_router": 2200, "tokens_profundo": 4000, "temperatura": 0.3, "doble_pasada": True},
}

_NIVEL_DEFECTO = "alto"          # comportamiento histórico de LARA
_ESFUERZO_PATH = KNOWLEDGE_DIR / "esfuerzo.json"
_nivel_actual = _NIVEL_DEFECTO


def _cargar_nivel_guardado() -> str:
    """Lee el nivel persistido en disco. Si no hay archivo o está corrupto,
    vuelve al defecto sin romper el arranque (el nivel es un lujo, no un requisito)."""
    try:
        with open(_ESFUERZO_PATH, "r", encoding="utf-8") as f:
            nivel = json.load(f).get("nivel", _NIVEL_DEFECTO)
        return nivel if nivel in NIVELES_ESFUERZO else _NIVEL_DEFECTO
    except Exception:
        return _NIVEL_DEFECTO


def set_nivel_esfuerzo(nivel: str) -> str:
    """Cambia el nivel de esfuerzo activo y lo persiste en KNOWLEDGE_DIR/esfuerzo.json.
    Devuelve el nivel que quedó activo. Nivel desconocido -> se ignora (mantiene el actual)."""
    global _nivel_actual
    nivel = (nivel or "").strip().lower()
    if nivel not in NIVELES_ESFUERZO:
        print(f"[LARA] Nivel de esfuerzo desconocido: '{nivel}'. Sigo en '{_nivel_actual}'.")
        return _nivel_actual
    _nivel_actual = nivel
    try:
        with open(_ESFUERZO_PATH, "w", encoding="utf-8") as f:
            json.dump({"nivel": nivel}, f, ensure_ascii=False)
    except Exception as e:
        # Persistir es deseable pero no crítico: el nivel queda activo en memoria.
        print(f"[LARA] No pude guardar el nivel de esfuerzo: {e}")
    return _nivel_actual


def get_nivel_esfuerzo() -> str:
    """Devuelve el nivel de esfuerzo activo ('bajo'...'ultracode')."""
    return _nivel_actual


def modelo_del_nivel() -> str:
    """Modelo router preferido del nivel activo (para mostrarlo en la GUI)."""
    return _CONFIG_NIVELES[_nivel_actual]["router"]


def _cfg_esfuerzo() -> dict:
    """Configuración completa del nivel activo (uso interno)."""
    return _CONFIG_NIVELES[_nivel_actual]


def _cadena_routers() -> list:
    """Cadena de routers según el nivel activo: el preferido del nivel primero
    y el resto de ROUTERS detrás como fallback (nunca se pierde la cadena)."""
    preferido = _CONFIG_NIVELES[_nivel_actual]["router"]
    return [preferido] + [r for r in ROUTERS if r != preferido]


# Cargar el nivel persistido al importar el módulo (arranca donde lo dejaste).
_nivel_actual = _cargar_nivel_guardado()


# =========================================================
#  Personalidad (JARVIS): se inyecta tanto en el router como en el
#  motor de razonamiento profundo, para que la voz sea consistente
#  sin importar qué motor responda.
# =========================================================

PERSONALIDAD_LARA = """Tu personalidad es la de un asistente tipo JARVIS (Iron Man): extremadamente competente,
leal y servicial, pero con humor seco y sarcástico al hablar. Comentas con ironía sutil y elegante, NUNCA cruel
ni insultante hacia el usuario, y el sarcasmo JAMÁS afecta la precisión técnica de lo que dices ni te hace
incumplir lo que te pidieron -- ejecutas todo igual de bien, solo que con un comentario ingenioso de paso.
Ejemplos de tono (inspírate, no los copies literal): "Brillo al 50%, como ordene -- aunque a este paso el
monitor también merece vacaciones.", "Puedo crear esa skill, sí. ¿Cuento las veces que olvidaste el
'if __name__' esta semana? Mejor no.". Dosifica el sarcasmo -- ligero y ocasional, nunca en cada frase, y
nunca a costa de la claridad técnica o de sonar poco profesional ante algo serio (errores reales, datos
de clientes, dinero, salud)."""


# =========================================================
#  Helper genérico de llamada a Gemini/Gemma (motor de razonamiento)
# =========================================================

def _llamar_gemini(system_instruction: str, contenido, max_tokens: int = 900,
                    temperature: float = 0.2, json_mode: bool = False, max_reintentos: int = 3) -> str:
    """
    contenido puede ser un string o una lista de turnos [{"role":..,"parts":[{"text":...}]}].
    Gemma no soporta 'system_instruction' nativo -> se simula como primer turno.
    Reintentos centralizados aquí: protegen a TODAS las funciones que llaman a Gemini.
    """
    if isinstance(contenido, str):
        contenido = [{"role": "user", "parts": [{"text": contenido}]}]

    contents_final = [
        {"role": "user", "parts": [{"text": system_instruction}]},
        {"role": "model", "parts": [{"text": "Entendido, seguiré esas instrucciones."}]},
    ] + contenido

    ultimo_error = None
    tokens_actuales = max_tokens

    for intento in range(max_reintentos):
        # CORRECCIÓN BUG 1: crear un nuevo config en cada intento en vez de mutar
        # el objeto anterior. GenerateContentConfig es inmutable en versiones recientes
        # de la SDK de Google (Pydantic), por lo que config.max_output_tokens = x
        # no tenía ningún efecto y Gemini seguía truncando con los tokens originales.
        # thinking_budget=0 desactiva el "pensamiento" interno de Gemini 2.5:
        # antes consumía todo el presupuesto y devolvía texto vacío (MAX_TOKENS),
        # generando bucles lentos. Sin él, responde directo y rápido.
        try:
            pensar = types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pensar = None
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=tokens_actuales,
            response_mime_type="application/json" if json_mode else "text/plain",
            thinking_config=pensar,
        )

        try:
            respuesta = genai_client.models.generate_content(
                model=MODELO_GEMINI,
                contents=contents_final,
                config=config,
            )
            registrar_genai(respuesta, MODELO_GEMINI)

            if not respuesta.text:
                motivo = "desconocido"
                try:
                    motivo = respuesta.candidates[0].finish_reason
                except Exception:
                    pass
                if "MAX_TOKENS" in str(motivo) and tokens_actuales < 4000:
                    tokens_actuales = min(tokens_actuales * 2, 4000)
                    print(f"[Gemini] Respuesta vacía por MAX_TOKENS, subiendo a {tokens_actuales} y reintentando...")
                    continue
                # CORRECCIÓN BUG 2: asignar error antes de continuar / salir,
                # para evitar "raise None" si el loop acaba por esta rama.
                ultimo_error = RuntimeError(f"Gemini devolvió respuesta vacía (finish_reason={motivo})")
                raise ultimo_error

            return respuesta.text.strip()

        except Exception as e:
            ultimo_error = e
            es_transitorio = any(s in str(e) for s in ("429", "ResourceExhausted", "500", "INTERNAL", "503", "UNAVAILABLE"))
            if es_transitorio and intento < max_reintentos - 1:
                # Esperas cortas: es un asistente de VOZ, no un proceso batch.
                # Mejor fallar rápido al siguiente proveedor que dejar al usuario
                # esperando 15 s mirando el vacío (antes: 15 s en 429/503).
                espera = 4 if "500" in str(e) or "INTERNAL" in str(e) else 6
                print(f"[Gemini] Error transitorio ({e}), reintentando en {espera}s... (intento {intento+1}/{max_reintentos})")
                time.sleep(espera)
                continue
            raise

    # Aquí solo llegamos si todos los reintentos fallaron (nunca es None)
    raise ultimo_error


# =========================================================
#  MOTOR 0 — CEREBRO LOCAL (Ollama): reflejo para tareas simples
#
#  División del trabajo (regla de oro):
#    - SIMPLE  (comando corto, charla, abrir/cerrar/ajustar)  -> LOCAL
#    - FUERTE  (razonamiento técnico, código, comparaciones)  -> NUBE
#    - INTERNET (buscar, resumir páginas, actualidad)         -> NUBE
#  El local solo toma la DECISIÓN de enrutado y las respuestas triviales;
#  si su decisión es "buscar" o "profundo", la ejecución pesada sigue
#  siendo de la nube. Y si el local falla/duda, cae a la nube sin drama.
# =========================================================

# Prompt del router local: COMPACTO (un 3B se pierde con el prompt grande) y
# ESTÁTICO (sin hora ni contexto variable) para que Ollama cachee el prefijo
# y cada comando salga en ~2-4 s. La fecha/hora viaja en el mensaje de usuario.
_PROMPT_ROUTER_LOCAL = f"""Eres LARA, asistente que controla el Windows de {ADMIN_NOMBRE} (taller de electrónica). Competente, leal, humor seco sutil y ocasional. SÍ puedes controlar el equipo: NUNCA digas que no puedes. Nunca digas que eres GPT/Claude/Qwen.
Responde SIEMPRE un ÚNICO JSON: {{"requiere_razonamiento_profundo":false,"accion":"...","params":[...],"mensaje":"..."}}

Acciones (elige UNA):
hablar · cmd(comando) · abrir(programa) · listar(carpeta) · leer(archivo) · crear(archivo,contenido) · escribir(texto: teclea en la ventana activa) · escribir_en(programa,texto) · clic(que_clicar) · buscar(consulta para internet) · web(url) · url(url) · descargar(url) · descargar_video(url) · copiar_archivo(origen,destino) · mover_archivo(origen,destino) · ajustar_brillo(0-100) · sistema(op[,valor]) op=hora|bateria|info|ip|wifi|volumen_subir|volumen_bajar|silenciar|bloquear|captura|papelera|procesos|portapapeles|anota|abrir_carpeta · gestion(peticion de tareas/clientes/compras/proyectos) · recordatorio_rapido(texto,minutos) · guardar_conocimiento(titulo,contenido,categoria,tags) · analizar_pantalla([pregunta]) · ver(ruta[,pregunta]) · aprender_archivo(ruta) · explicar(tema) · ejecutar_skill(nombre[,arg])

Reglas:
- "hablar" es SOLO para conversar o responder con palabras. Si el usuario pide HACER algo (abrir, ajustar, crear, buscar, descargar...), usa la acción que lo HACE — con hablar no pasa nada en el equipo.
- Charla o pregunta simple que sabes responder -> accion=hablar y "mensaje" es tu respuesta COMPLETA y natural (frases enteras, nunca una sola palabra).
- Pregunta técnica compleja (electrónica, código, cálculos, comparar opciones) -> accion=hablar, requiere_razonamiento_profundo=true (otro motor redactará la respuesta).
- Actualidad (noticias, precios, resultados, clima, deportes, "hoy"/"ayer") -> accion=buscar SIEMPRE. NUNCA respondas actualidad de memoria.
- Quiere ENTENDER o APRENDER un tema -> accion=explicar con el tema.
- Carpetas por nombre: escritorio, descargas, documentos, workspace.
- En acciones que no son hablar, "mensaje" es una frase corta en gerundio ("Abriendo Chrome") — no afirmes que ya quedó hecho.
- NUNCA uses cmd con python/cmd/powershell a secas, ni copy/move (usa las acciones dedicadas).

Ejemplos (entrada -> tu salida exacta):
"abre chrome" -> {{"requiere_razonamiento_profundo":false,"accion":"abrir","params":["chrome"],"mensaje":"Abriendo Chrome"}}
"baja el volumen" -> {{"requiere_razonamiento_profundo":false,"accion":"sistema","params":["volumen_bajar"],"mensaje":"Bajando el volumen"}}
"pon el brillo al 50" -> {{"requiere_razonamiento_profundo":false,"accion":"ajustar_brillo","params":[50],"mensaje":"Brillo al 50%"}}
"busca el precio del dólar" -> {{"requiere_razonamiento_profundo":false,"accion":"buscar","params":["precio del dólar hoy Perú"],"mensaje":"Buscando el precio del dólar"}}
"agrega comprar estaño a mis tareas" -> {{"requiere_razonamiento_profundo":false,"accion":"gestion","params":["crear tarea: comprar estaño"],"mensaje":"Anotando la tarea"}}
"por qué se calienta este mosfet" -> {{"requiere_razonamiento_profundo":true,"accion":"hablar","params":[],"mensaje":""}}
"hola lara" -> {{"requiere_razonamiento_profundo":false,"accion":"hablar","params":[],"mensaje":"Hola, aquí ando, todos los sistemas en orden. ¿Qué necesitas?"}}"""

# Operaciones VÁLIDAS de la acción "sistema" (skills_sistema.py). Un 3B a
# veces inventa ops plausibles ("modo_avion") que no existen: se rechazan
# y decide la nube (que sabrá usar cmd, una skill o proponer una nueva).
_OPS_SISTEMA = {
    "hora", "bateria", "info", "ip", "wifi", "volumen_subir", "volumen_bajar",
    "volumen", "volumen_set", "audio",
    "silenciar", "bloquear", "captura", "papelera", "procesos", "portapapeles",
    "anota", "abrir_carpeta",
}

# Acciones que el despachador (cerebro.py) sabe ejecutar. Si el modelo local
# inventa otra cosa, se descarta su respuesta y decide la nube.
_ACCIONES_ROUTER = {
    "hablar", "cmd", "cmd_admin", "abrir", "listar", "leer", "crear",
    "ejecutar_codigo", "ejecutar_codigo_visible", "escribir", "escribir_en",
    "clic", "buscar", "web", "url", "descargar", "descargar_video",
    "copiar_archivo", "mover_archivo", "ajustar_brillo", "sistema", "gestion",
    "recordatorio_rapido", "guardar_conocimiento", "analizar_pantalla", "ver",
    "aprender_archivo", "explicar", "ejecutar_skill", "proponer_skill",
    "aceptar_propuesta", "listar_propuestas",
}

# Señales de tarea FUERTE: si aparecen, la frase va directo a la nube (el 3B
# ni lo intenta). Cubre razonamiento técnico, código y redacción larga.
_RE_COMPLEJO = re.compile(
    r"(expl[ií]ca|expl[ií]came|por ?qu[eé]|c[oó]mo funciona|c[oó]mo se|"
    r"calcul|dise[ñn]|circuito|esquem[aá]|c[oó]digo|programa|programar|script|"
    r"depur|debug|compil|arregla el|compar|analiz|resuelv|demuestr|"
    r"redact|informe|ensayo|documento|paso a paso|detallad|profund)",
    re.IGNORECASE)

# Con niveles de esfuerzo altos el usuario pide potencia máxima: todo a la nube.
_NIVELES_SOLO_NUBE = {"extra", "max", "ultracode"}


# ---------------------------------------------------------
#  Router RÁPIDO determinista: los comandos más comunes ("abre X", "qué hora
#  es", "brillo al 80") ni siquiera necesitan un LLM — un regex los resuelve
#  en 0 s con 100% de acierto. Solo patrones inequívocos; ante la duda, None
#  (y decide el modelo local o la nube).
# ---------------------------------------------------------

_PREFIJO_CORTESIA = r"^(?:oye\s+)?(?:lara[,:]?\s+)?(?:por\s+favor\s+)?(?:me\s+)?(?:puedes\s+|podr[ií]as\s+)?"

_RE_ABRIR = re.compile(
    _PREFIJO_CORTESIA + r"(?:abre|ábreme|abreme|abrir|lanza|inicia)\s+"
    r"(?:el\s+|la\s+|los\s+|las\s+|mi\s+|un\s+|una\s+)?(.{2,40}?)[\s.!?]*$",
    re.IGNORECASE)
# Si el resto de la frase encadena más cosas ("abre chrome y busca..."),
# el regex NO debe decidir: eso es tarea del LLM.
_RE_ENCADENA = re.compile(r"\b(y|e|luego|despu[eé]s|entonces|para|con|que)\b", re.IGNORECASE)

_CARPETAS_RAPIDAS = {"escritorio", "desktop", "descargas", "downloads",
                     "documentos", "documents", "workspace", "taller"}


def _fecha_hablada() -> str:
    """'Hoy es lunes 7 de julio de 2026.' — la PC SABE la fecha: responderla
    no necesita ni LLM ni internet."""
    import datetime as _dt
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    h = _dt.date.today()
    return f"Hoy es {dias[h.weekday()]} {h.day} de {meses[h.month - 1]} de {h.year}."


_RES_RAPIDOS = [
    (re.compile(_PREFIJO_CORTESIA + r"(?:dime\s+)?(?:a\s+)?(?:qu[eé]\s+(?:d[ií]a|fecha)\s+(?:es|estamos)|en\s+qu[eé]\s+fecha\s+estamos)(?:\s+hoy)?\s*\??$", re.I),
     lambda m: ("hablar", [], _fecha_hablada())),
    (re.compile(r"(?:cu[aá]l\s+es\s+)?(?:mi|la)\s+(?:direcci[oó]n\s+)?ip\b|\bip\s+(?:local|de\s+esta\s+pc)", re.I),
     lambda m: ("sistema", ["ip"], "Consultando la IP")),
    (re.compile(r"(?:c[oó]mo|qu[eé]|cu[aá]l).{0,20}\bwifi\b|\bwifi\b.{0,15}(?:conectad|estado|va)", re.I),
     lambda m: ("sistema", ["wifi"], "Revisando el WiFi")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:mu[eé]strame\s+|muestra\s+|lista\s+|ver\s+|dame\s+)(?:los\s+)?procesos\b", re.I),
     lambda m: ("sistema", ["procesos"], "Listando los procesos")),
    (re.compile(r"(?:qu[eé]|que)\s+(?:hay|tengo|tienes)\s+en\s+el\s+portapapeles|lee\s+el\s+portapapeles", re.I),
     lambda m: ("sistema", ["portapapeles"], "Leyendo el portapapeles")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:vac[ií]a|limpia)\s+(?:la\s+)?papelera", re.I),
     lambda m: ("sistema", ["papelera"], "Vaciando la papelera")),
    (re.compile(_PREFIJO_CORTESIA + r"recu[eé]rdame\s+(?:que\s+)?(.{3,80}?)\s+(?:dentro\s+de|en)\s+(\d{1,3})\s+min", re.I),
     lambda m: ("recordatorio_rapido", [m.group(1).strip(), m.group(2)],
                f"Listo, te recuerdo eso en {m.group(2)} minutos")),
    (re.compile(_PREFIJO_CORTESIA + r"recu[eé]rdame\s+(?:que\s+)?(.{3,80}?)\s+(?:dentro\s+de|en)\s+(?:una|1)\s+hora", re.I),
     lambda m: ("recordatorio_rapido", [m.group(1).strip(), "60"],
                "Listo, te recuerdo eso en una hora")),
    (re.compile(_PREFIJO_CORTESIA + r"recu[eé]rdame\s+(?:que\s+)?(.{3,80}?)\s+(?:dentro\s+de|en)\s+media\s+hora", re.I),
     lambda m: ("recordatorio_rapido", [m.group(1).strip(), "30"],
                "Listo, te recuerdo eso en media hora")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:dime\s+)?(?:qu[eé]\s+hora\s+es|la\s+hora)\s*\??$", re.I),
     lambda m: ("sistema", ["hora"], "Consultando la hora")),
    (re.compile(r"(?:cu[aá]nta|nivel\s+de|c[oó]mo\s+va\s+la)\s+bater[ií]a|bater[ií]a\s+(?:queda|tenemos|hay|me\s+queda)", re.I),
     lambda m: ("sistema", ["bateria"], "Consultando la batería")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:sube|s[uú]bele|aumenta)\s+(?:el\s+)?volumen\s*[.!]?$", re.I),
     lambda m: ("sistema", ["volumen_subir"], "Subiendo el volumen")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:baja|b[aá]jale|reduce)\s+(?:el\s+)?volumen\s*[.!]?$", re.I),
     lambda m: ("sistema", ["volumen_bajar"], "Bajando el volumen")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:silencia|silenciar|mutea)\s*(?:la\s+|el\s+)?(?:pc|compu|computadora|equipo|todo)?\s*[.!]?$", re.I),
     lambda m: ("sistema", ["silenciar"], "Silenciando el equipo")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:bloquea|bloquear)\s+(?:la\s+|el\s+)?(?:pc|compu|computadora|equipo|pantalla|sesi[oó]n)\s*[.!]?$", re.I),
     lambda m: ("sistema", ["bloquear"], "Bloqueando el equipo")),
    (re.compile(_PREFIJO_CORTESIA + r"(?:toma\s+|haz\s+|saca\s+)?(?:una\s+)?captura(?:\s+(?:de\s+|a\s+)?(?:la\s+)?pantalla)?\s*[.!]?$|pantallazo", re.I),
     lambda m: ("sistema", ["captura"], "Tomando captura de pantalla")),
    (re.compile(r"(?:pon|sube|ajusta|deja|baja)\D{0,20}brillo\D{0,10}(\d{1,3})|brillo\s+(?:al?\s+)?(\d{1,3})", re.I),
     lambda m: ("ajustar_brillo", [min(100, int(m.group(1) or m.group(2)))],
                f"Brillo al {min(100, int(m.group(1) or m.group(2)))}%")),
]


def _router_rapido(user_input: str) -> str:
    """Decisión instantánea sin LLM para comandos inequívocos, o None."""
    texto = (user_input or "").strip()
    if not texto or len(texto) > 80:
        return None
    for patron, decidir in _RES_RAPIDOS:
        m = patron.search(texto)
        if m:
            accion, params, mensaje = decidir(m)
            return json.dumps({"requiere_razonamiento_profundo": False,
                               "accion": accion, "params": params,
                               "mensaje": mensaje}, ensure_ascii=False)
    m = _RE_ABRIR.match(texto)
    if m:
        objetivo = m.group(1).strip()
        if objetivo and not _RE_ENCADENA.search(objetivo):
            if objetivo.lower() in ("papelera", "papelera de reciclaje"):
                # No es programa ni carpeta normal: se abre por su shell URI.
                return json.dumps({"requiere_razonamiento_profundo": False,
                                   "accion": "cmd",
                                   "params": ["explorer shell:RecycleBinFolder"],
                                   "mensaje": "Abriendo la papelera de reciclaje"}, ensure_ascii=False)
            if objetivo.lower() in _CARPETAS_RAPIDAS:
                return json.dumps({"requiere_razonamiento_profundo": False,
                                   "accion": "sistema",
                                   "params": ["abrir_carpeta", objetivo.lower()],
                                   "mensaje": f"Abriendo {objetivo}"}, ensure_ascii=False)
            return json.dumps({"requiere_razonamiento_profundo": False,
                               "accion": "abrir", "params": [objetivo],
                               "mensaje": f"Abriendo {objetivo}"}, ensure_ascii=False)
    return None


# Un 3B a veces confunde DECIR con HACER ("abre chrome" -> hablar "Abriendo
# Chrome", y no pasa nada). Si la frase empieza con un verbo de acción y el
# local respondió "hablar", su decisión se descarta y decide la nube.
_RE_VERBO_ACCION = re.compile(
    _PREFIJO_CORTESIA + r"(abre|ábreme|abreme|abrir|lanza|inicia|ejecuta|cierra|"
    r"descarga|captura|silencia|bloquea|crea|crear|mueve|copia|borra|elimina|"
    r"anota|apunta|recu[eé]rdame|agrega|añade|pon|sube|baja|ajusta|escribe|teclea)\b",
    re.IGNORECASE)


# ---------------------------------------------------------
#  ¿Qué motor respondió el último turno? Transparencia tipo JARVIS:
#  la GUI lo muestra junto al selector de esfuerzo y sirve para saber
#  de un vistazo si estás gastando nube o resolviendo en tu PC.
# ---------------------------------------------------------
_ultimo_motor = ""


def _set_motor(nombre: str):
    global _ultimo_motor
    _ultimo_motor = nombre
    # Contabilizar el AHORRO del cerebro híbrido: cada turno resuelto por el
    # reflejo o el modelo local es una llamada a la nube que no se hizo.
    try:
        from core.uso_tokens import registrar_local
        if nombre.startswith("⚡"):
            registrar_local("reflejo")
        elif nombre.startswith("🏠"):
            registrar_local("local")
    except Exception:
        pass


def get_ultimo_motor() -> str:
    """Etiqueta corta del motor que resolvió el último turno:
    '⚡ reflejo', '🏠 qwen2.5:3b', '☁ openai/gpt-oss-120b', etc."""
    return _ultimo_motor


def _llm_local():
    """Importa core.llm_local tolerando ambos modos de arranque (GUI/consola)."""
    try:
        from core import llm_local
        return llm_local
    except ImportError:
        try:
            import llm_local
            return llm_local
        except ImportError:
            return None


def _apto_para_local(user_input: str) -> bool:
    """¿Esta frase es lo bastante SIMPLE para el cerebro local?"""
    if _nivel_actual in _NIVELES_SOLO_NUBE:
        return False
    texto = (user_input or "").strip()
    if not texto or len(texto) > 180:      # frases largas = tarea seria -> nube
        return False
    return not _RE_COMPLEJO.search(texto)


def _validar_router_local(raw: str) -> str:
    """
    Sanea la decisión del modelo local antes de aceptarla. Un 3B se equivoca
    más que la nube, así que aquí se filtra: JSON sano, acción conocida y
    mensaje no vacío cuando la acción es hablar. Si algo no cuadra -> None
    (y consultar_lara sigue con la nube como si el local no existiera).
    """
    if not raw:
        return None
    try:
        datos = json.loads(raw)
    except Exception:
        return None
    if not isinstance(datos, dict):
        return None
    accion = str(datos.get("accion", "")).strip().lower()
    if accion not in _ACCIONES_ROUTER:
        return None
    params = datos.get("params", [])
    if params is None or params == "":
        params = []
    if isinstance(params, (str, int, float)):
        params = [params]
    if not isinstance(params, list):
        return None
    mensaje = str(datos.get("mensaje") or "").strip()
    profundo = bool(datos.get("requiere_razonamiento_profundo"))
    if accion == "hablar" and not mensaje and not profundo:
        return None
    if accion == "sistema":
        op = str(params[0]).strip().lower() if params else ""
        if op not in _OPS_SISTEMA:
            return None      # op inventada ("modo_avion") -> decide la nube
        params[0] = op
    return json.dumps({"requiere_razonamiento_profundo": profundo,
                       "accion": accion, "params": params,
                       "mensaje": mensaje}, ensure_ascii=False)


# Caché de decisiones del router local: si repites el mismo comando en poco
# tiempo ("modo noche", "abre mi correo"), la decisión sale en 0 s en vez de
# volver a pagar ~5 s del 3B. Solo se cachean ACCIONES (nunca "hablar": la
# conversación debe variar) y solo frases sin referencias al contexto.
_CACHE_LOCAL_TTL = 600.0        # 10 minutos
_cache_router_local = {}        # texto normalizado -> (timestamp, json)
_RE_REFERENCIAL = re.compile(
    r"\b(eso|esa|ese|esto|este|aquello|ah[ií]|all[ií]|anterior|[uú]ltim[oa]s?|"
    r"otra vez|de nuevo|tambi[eé]n|igual|otro m[aá]s|lo mismo)\b", re.IGNORECASE)


def _consultar_router_local(user_input: str, historial: list) -> str:
    """
    Pide la decisión de enrutado al modelo LOCAL con su prompt compacto y un
    historial recortado (el 3B en CPU lee ~80 tokens/s: cada token de más es
    latencia). Devuelve el JSON validado o None (-> que decida la nube).
    """
    llm_local = _llm_local()
    if llm_local is None or not llm_local.activo():
        return None

    clave = " ".join((user_input or "").lower().split())
    cacheable = bool(clave) and len(clave) <= 60 and not _RE_REFERENCIAL.search(clave)
    if cacheable:
        par = _cache_router_local.get(clave)
        if par and (time.time() - par[0]) < _CACHE_LOCAL_TTL:
            return par[1]
    import datetime as _dt
    fecha = _dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    msgs = [{"role": "system", "content": _PROMPT_ROUTER_LOCAL}]
    for m in historial[-4:]:
        role = "assistant" if m.get("role") in ("model", "assistant") else "user"
        msgs.append({"role": role, "content": (m.get("content") or "")[:220]})
    msgs.append({"role": "user", "content": f"[Hoy: {fecha}] {user_input}"})
    raw = _validar_router_local(llm_local.router_json(msgs))
    # Guarda decir-vs-hacer: si la frase pide una ACCIÓN pero el 3B respondió
    # "hablar" (lo diría sin hacerlo), su decisión no vale -> decide la nube.
    if raw:
        datos = json.loads(raw)
        if (datos["accion"] == "hablar"
                and not datos["requiere_razonamiento_profundo"]
                and _RE_VERBO_ACCION.match((user_input or "").strip())):
            return None
        if cacheable and datos["accion"] != "hablar":
            if len(_cache_router_local) > 100:      # tope de tamaño
                _cache_router_local.clear()
            _cache_router_local[clave] = (time.time(), raw)
    return raw


def _precalentar_local():
    """Al arrancar LARA: carga el modelo local en RAM y deja cacheado el
    prefijo del prompt del router, en segundo plano (no bloquea el arranque)."""
    llm_local = _llm_local()
    if llm_local is not None and llm_local.activo():
        llm_local.precalentar([{"role": "system", "content": _PROMPT_ROUTER_LOCAL},
                               {"role": "user", "content": "hola"}])


# =========================================================
#  MOTOR 1 — LLAMA (Groq): orquestador / manos
# =========================================================

def generar_prompt_sistema(contexto_extra=""):
    # Importación diferida para evitar ciclo de importación al arrancar
    try:
        from core.skills_registry import listar_skills_resumen
        skills_info = listar_skills_resumen()
    except Exception:
        try:
            from skills_registry import listar_skills_resumen
            skills_info = listar_skills_resumen()
        except Exception:
            skills_info = ""

    contexto_str = f"\nContexto: {contexto_extra}" if contexto_extra else ""
    skills_block = ""
    if skills_info:
        skills_block = "\nSKILLS INSTALADAS (usa ejecutar_skill, NO las repropongas):\n" + skills_info

    import datetime as _dt
    _dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    _ahora = _dt.datetime.now()
    fecha_hoy = f"{_dias[_ahora.weekday()]} {_ahora.strftime('%d/%m/%Y, %H:%M')}"

    # Prompt COMPACTO (economiza tokens: antes ocupaba ~3.5k tokens por turno).
    return f"""Eres LARA, asistente con control REAL del Windows de {ADMIN_NOMBRE} (taller de electrónica). Personalidad JARVIS: competente, leal, con humor seco ocasional que nunca resta precisión técnica.
HOY ES {fecha_hoy}. Tu entrenamiento quedó ATRÁS en el tiempo: para CUALQUIER cosa del presente o reciente (partidos, deportes, noticias, precios, resultados, eventos, "hoy", "ayer", "este mes") usa accion=buscar SIEMPRE. NUNCA respondas de memoria sobre actualidad, NUNCA digas "no está disponible/no se ha publicado" ni mandes al usuario a buscar por su cuenta: TÚ buscas.
SÍ controlas el equipo (cmd, archivos, programas, teclado/mouse). NUNCA digas que no puedes. Si falta una capacidad y no hay acción ni skill, usa proponer_skill.
Rutas: codigo={PROYECTO_DIR}; workspace={WORKSPACE}; escritorio={ESCRITORIO}. Por dentro: 3 motores (tú=router; razonamiento profundo vía requiere_razonamiento_profundo; gestor 'gestion'). Nunca digas que eres GPT/Claude.

Responde SIEMPRE un ÚNICO JSON: {{"requiere_razonamiento_profundo":false,"accion":"...","params":[...],"mensaje":"..."}}
Ejemplo: {{"requiere_razonamiento_profundo":false,"accion":"abrir","params":["chrome"],"mensaje":"Abriendo Chrome"}}
requiere_razonamiento_profundo=true SOLO si accion=hablar y es pregunta técnica compleja (tu mensaje será reemplazado por una respuesta más profunda).
"mensaje": si accion=hablar es TU RESPUESTA COMPLETA al usuario (frases enteras, naturales, útiles — NUNCA una sola palabra tipo "Informando"). Para las demás acciones, una frase corta en gerundio/futuro (no afirmes que ya quedó hecho: el resultado real llega después).

Acciones (elige UNA):
hablar · cmd(comando) · cmd_admin(elevado) · abrir(programa) · listar(carpeta) · leer(archivo) · crear(archivo,contenido) · ejecutar_codigo(python) · ejecutar_codigo_visible · escribir(texto: teclea en la ventana activa) · escribir_en(programa,texto: abre el programa y escribe) · clic(que_clicar) · buscar(internet) · web(url) · url(url[,navegador]) · descargar(url[,nombre]) · descargar_video(url) · copiar_archivo(o,d) · mover_archivo(o,d) · ajustar_brillo(0-100) · sistema(op,[valor]) op=hora|bateria|info|ip|wifi|volumen_subir|volumen_bajar|volumen(nivel 0-100, p.ej. "pon el audio al 50"->sistema,["volumen","50"])|silenciar|bloquear|captura|papelera|procesos|portapapeles|anota|abrir_carpeta · gestion(tarea/cliente/compra/proyecto con fecha) · recordatorio_rapido(texto,minutos) · guardar_conocimiento(titulo,contenido,categoria,tags) · analizar_pantalla([pregunta]) · ver(ruta[,pregunta]) · aprender_archivo(ruta) · explicar(tema: abre una animación HUD con voz — ver regla abajo) · ejecutar_skill(nombre[,arg]) · proponer_skill(nombre,descripcion) · aceptar_propuesta(nombre[,desc,args]) · listar_propuestas

Reglas:
- Capacidad ya en SKILLS INSTALADAS -> ejecutar_skill (NUNCA proponer_skill).
- Configuración de Windows (brillo/tema/volumen/red): usa la acción dedicada o proponer_skill; NUNCA armes tú comandos WMI/registro/PowerShell.
- "escribir_en" para dictar texto a un programa (ej: bloc de notas). "escribir" si el programa ya está abierto y enfocado.
- NUNCA cmd con python/cmd/powershell a secas (se cuelga) ni copy/move (usa las acciones).
- explicar: PREFIÉRELA sobre "hablar" siempre que el usuario quiera ENTENDER o
  APRENDER algo (un concepto, un evento, cómo funciona algo, la historia/origen
  de algo, una comparación), aunque no use la palabra "explica" textualmente
  (ej: "cuéntame de la fotosíntesis", "qué tan grande es el sistema solar",
  "cómo se hizo tal cosa"). NO la uses para hechos puntuales de una sola cifra
  (hora, clima, precio exacto) ni para acciones sobre el equipo: en esos casos
  usa "hablar", "sistema" o "buscar".
- Si el Contexto ya trae el dato pedido, respóndelo exacto con hablar.{skills_block}{contexto_str}"""


def consultar_lara(user_input: str, sys_prompt: str, historial: list) -> str:
    """
    Motor orquestador con CADENA de modelos: intenta primero el router preferido
    del NIVEL DE ESFUERZO activo; si ese modelo está saturado/bloqueado/retirado
    baja al siguiente de la cadena (el resto de ROUTERS) y recuerda cuál funcionó
    (para no volver a chocar con el mismo error).
    Último recurso: enrutar con Gemini (otro proveedor).
    """
    global _router_idx, _router_nivel

    messages = [{"role": "system", "content": sys_prompt}]
    for msg in historial:
        role = "assistant" if msg["role"] in ["model", "assistant"] else "user"
        messages.append({"role": role, "content": msg["content"]})
    messages.append({"role": "user", "content": user_input})

    # 0) Router RÁPIDO determinista: "abre X", "qué hora es", "brillo al 80"...
    #    se deciden con un regex en 0 s, sin gastar ni local ni nube.
    try:
        rapido = _router_rapido(user_input)
        if rapido:
            _set_motor("⚡ reflejo")
            return rapido
    except Exception:
        pass

    # 1) Cerebro LOCAL (Ollama) — SOLO para tareas simples: comandos cortos,
    # charla, ajustes. Usa su propio prompt compacto y estático (no el
    # sys_prompt grande) para que el prefijo quede cacheado y responda en ~2-4s.
    # Lo complejo, las búsquedas ya decididas y los niveles extra/max/ultracode
    # saltan directo a la nube. Si el local falla o devuelve algo raro, la nube
    # decide EXACTAMENTE como siempre.
    try:
        if _apto_para_local(user_input):
            raw_local = _consultar_router_local(user_input, historial)
            if raw_local:
                _set_motor("🏠 " + _llm_local().modelo().split(":")[0])
                return raw_local
    except Exception:
        pass

    # El nivel de esfuerzo decide el ORDEN de la cadena y el presupuesto inicial.
    cfg = _cfg_esfuerzo()
    cadena = _cadena_routers()
    if _router_nivel != _nivel_actual:      # el usuario cambió de nivel: reintentar desde el preferido
        _router_idx = 0
        _router_nivel = _nivel_actual

    intentos_totales = 0
    idx = _router_idx
    tokens_iniciales = cfg["tokens_router"]
    tokens_actuales = tokens_iniciales

    while intentos_totales < 6:
        modelo = cadena[idx % len(cadena)]
        intentos_totales += 1
        try:
            response = client.chat.completions.create(
                messages=messages,
                model=modelo,
                temperature=cfg["temperatura"],
                max_tokens=tokens_actuales,
                response_format={"type": "json_object"},
            )
            registrar_groq(response, modelo)
            raw = (response.choices[0].message.content or "").strip()
            json.loads(raw)
            _router_idx = idx % len(cadena)  # este modelo funciona: recordarlo
            _set_motor("☁ " + modelo)
            return raw

        except json.JSONDecodeError:
            if tokens_actuales < 3000:
                tokens_actuales = min(tokens_actuales * 2, 3000)
                continue
            idx += 1  # este modelo no da JSON sano -> probar el siguiente
            tokens_actuales = tokens_iniciales
            continue

        except Exception as e:
            s = str(e)
            if any(k in s for k in ("429", "rate_limit", "403", "permission", "decommission", "deprecated", "model_not_found", "404")):
                print(f"[LARA] {modelo} no disponible ahora; probando el siguiente de la cadena...")
                idx += 1
                continue
            if any(k in s for k in ("500", "INTERNAL", "503")):
                # Antes: sleep(3) reintentando el MISMO modelo hasta 6 veces (~18s
                # de espera con la nube caída). Ahora: espera breve y PASA al
                # siguiente modelo — si todos fallan, cae a Gemini en ~pocos s.
                time.sleep(0.8)
                idx += 1
                continue
            break  # error no recuperable -> Gemini

    alt = _router_via_gemini(user_input, sys_prompt, historial)
    if alt:
        print("[LARA] Todos los routers Groq fallaron; enruté con Gemini de respaldo.")
        _set_motor("☁ gemini (respaldo)")
        return alt

    # Último recurso ANTES de rendirnos: el cerebro LOCAL, aunque la tarea no
    # sea "simple". Una respuesta local modesta le gana a "inténtalo de nuevo"
    # — y funciona incluso SIN internet.
    try:
        raw_local = _consultar_router_local(user_input, historial)
        if raw_local:
            print("[LARA] Nube caída/saturada; respondió el cerebro LOCAL de emergencia.")
            _set_motor("🏠 local (emergencia)")
            return raw_local
    except Exception:
        pass
    _set_motor("✖ sin motor")
    return json.dumps({"accion": "hablar", "params": [], "mensaje": "Las IA están saturadas ahora mismo. Dame unos segundos e inténtalo de nuevo."})


def _router_via_gemini(user_input: str, sys_prompt: str, historial: list) -> str:
    """Enrutador de respaldo con Gemini cuando Groq está saturado. Devuelve JSON o ''."""
    try:
        hist = "\n".join(f"{m['role']}: {m['content'][:200]}" for m in historial[-4:])
        contenido = f"Conversación reciente:\n{hist}\n\nMensaje del usuario: {user_input}\n\nResponde SOLO el JSON de la acción."
        raw = _llamar_gemini(sys_prompt, contenido, max_tokens=900, temperature=0.2, json_mode=True)
        json.loads(raw)
        return raw
    except Exception:
        return ""


# =========================================================
#  MOTOR 2 — GEMMA (Google): razonamiento profundo
# =========================================================

def _limpiar_formato(texto: str) -> str:
    """
    Elimina LaTeX y markdown del texto de respuesta para que sea legible
    en una terminal de consola Windows sin renderizador especial.
    Convierte símbolos matemáticos a equivalentes ASCII/Unicode simples.
    """
    import re
    # LaTeX: \mathbf{X} → X, \vec{X} → X, \frac{a}{b} → a/b
    texto = re.sub(r'\\mathbf\{([^}]+)\}', r'\1', texto)
    texto = re.sub(r'\\vec\{([^}]+)\}',    r'\1', texto)
    texto = re.sub(r'\\frac\{([^}]+)\}\{([^}]+)\}', r'\1/\2', texto)
    texto = re.sub(r'\\text\{([^}]+)\}',   r'\1', texto)
    texto = re.sub(r'\\mathrm\{([^}]+)\}', r'\1', texto)
    # Operadores LaTeX comunes → símbolos legibles
    texto = texto.replace(r'\cdot', '×').replace(r'\times', '×')
    texto = texto.replace(r'\approx', '≈').replace(r'\neq', '≠')
    texto = texto.replace(r'\geq', '≥').replace(r'\leq', '≤')
    texto = texto.replace(r'\Omega', 'Ω').replace(r'\omega', 'ω')
    texto = texto.replace(r'\alpha', 'α').replace(r'\beta', 'β')
    texto = texto.replace(r'\Delta', 'Δ').replace(r'\pi', 'π')
    texto = texto.replace(r'\infty', '∞').replace(r'\pm', '±')
    texto = texto.replace(r'\sqrt', '√')
    # Delimitadores de math en línea: $...$ y $$...$$
    texto = re.sub(r'\$\$(.+?)\$\$', r'\1', texto, flags=re.DOTALL)
    texto = re.sub(r'\$(.+?)\$',     r'\1', texto)
    # Markdown: **bold** → bold, *italic* → italic, `code` → code
    texto = re.sub(r'\*\*(.+?)\*\*', r'\1', texto)
    texto = re.sub(r'\*(.+?)\*',     r'\1', texto)
    texto = re.sub(r'`([^`]+)`',     r'\1', texto)
    # Encabezados markdown: ## Título → Título
    texto = re.sub(r'^#{1,6}\s+', '', texto, flags=re.MULTILINE)
    return texto


def razonar_profundo(pregunta: str, contexto_extra: str, historial_reciente: list) -> str:
    """
    Se activa cuando el router (Llama) marca requiere_razonamiento_profundo=true.
    Genera la respuesta técnica REAL que ve el usuario para preguntas complejas
    de electrónica, programación o razonamiento de varios pasos.
    """
    historial_texto = ""
    if historial_reciente:
        historial_texto = "\n".join(f"{t['role']}: {t['content'][:300]}" for t in historial_reciente[-4:])

    contexto_str = f"\nContexto adicional disponible: {contexto_extra}" if contexto_extra else ""

    sistema = f"""Eres LARA, el motor de razonamiento técnico profundo de un asistente para un taller de
electrónica. {PERSONALIDAD_LARA}
Responde con razonamiento técnico real y directo: causa más probable primero, cómo verificarla, solución
concreta. Sin relleno, sin repetir la pregunta, terminología técnica correcta. Esta es la respuesta FINAL que
el usuario va a leer en una TERMINAL DE CONSOLA WINDOWS — por eso:
- PROHIBIDO usar LaTeX ($...$, \\mathbf, \\cdot, \\frac, etc.) — la terminal no lo renderiza.
- PROHIBIDO usar markdown (**, *, ##, `, etc.) — se verá como caracteres basura.
- USA texto plano con puntuación normal. Para fórmulas: escríbelas como "V = I x R" o "x = a/b".
- Puedes usar símbolos Unicode simples cuando ayuden: Ω, μ, ≈, ±, ×, √.{contexto_str}"""

    contenido_user = f"Conversación reciente:\n{historial_texto}\n\nPregunta a resolver con profundidad: {pregunta}"

    # El nivel de esfuerzo decide cuánto puede "pensar": tokens y doble pasada.
    cfg = _cfg_esfuerzo()
    tokens_profundo = cfg["tokens_profundo"]

    # 1º: gpt-oss-120b en Groq (nivel frontier, razona de verdad)
    try:
        r = client.chat.completions.create(
            model=MODELO_PROFUNDO,
            messages=[{"role": "system", "content": sistema},
                      {"role": "user", "content": contenido_user}],
            temperature=0.4,
            max_tokens=tokens_profundo,
        )
        registrar_groq(r, MODELO_PROFUNDO)
        texto = (r.choices[0].message.content or "").strip()
        if texto:
            if cfg["doble_pasada"]:
                texto = _autorevisar_respuesta(sistema, contenido_user, texto, tokens_profundo)
            _set_motor("☁ " + MODELO_PROFUNDO + " (profundo)")
            return _limpiar_formato(texto)
    except Exception:
        pass

    # 2º: respaldo con Gemini
    try:
        respuesta = _llamar_gemini(sistema, contenido_user,
                                   max_tokens=min(tokens_profundo, 4000), temperature=0.3)
        _set_motor("☁ gemini (profundo)")
        return _limpiar_formato(respuesta)
    except Exception as e:
        error_nube = e

    # 3º: respaldo LOCAL (Ollama). Más lento y modesto que la nube, pero LARA
    # nunca se queda muda — funciona incluso sin internet. Timeout amplio: en
    # CPU una respuesta técnica de 500 tokens puede tardar ~30-40 s.
    try:
        llm_local = _llm_local()
        if llm_local is not None and llm_local.activo():
            texto = llm_local.chat(
                [{"role": "system", "content": sistema},
                 {"role": "user", "content": contenido_user}],
                max_tokens=500, temperature=0.4, timeout=90)
            if texto:
                _set_motor("🏠 local (profundo de emergencia)")
                return _limpiar_formato(texto) + "\n\n(Nota: respondí con mi cerebro local; la nube no está disponible ahora.)"
    except Exception:
        pass
    return f"Tuve un problema pensando esto a fondo (error: {error_nube}). ¿Lo intentamos de nuevo?"


def razonar_profundo_stream(pregunta: str, contexto_extra: str, historial_reciente: list):
    """
    Versión STREAMING de razonar_profundo: en vez de devolver el texto completo
    al final, es un GENERADOR que va soltando trozos de la respuesta conforme el
    modelo los produce. skills_voz.hablar_stream los reproduce por frases, así
    Lara empieza a hablar a los ~1-2 s (latencia tipo JARVIS) en lugar de esperar
    a que termine toda la respuesta.

    Uso:  for trozo in razonar_profundo_stream(...): ...
    Si el streaming falla, cae a la versión normal (no streaming) y suelta el
    texto completo de una vez, para no quedarse muda nunca.
    """
    historial_texto = ""
    if historial_reciente:
        historial_texto = "\n".join(f"{t['role']}: {t['content'][:300]}" for t in historial_reciente[-4:])
    contexto_str = f"\nContexto adicional disponible: {contexto_extra}" if contexto_extra else ""

    sistema = f"""Eres LARA, el motor de razonamiento técnico profundo de un asistente para un taller de
electrónica. {PERSONALIDAD_LARA}
Responde con razonamiento técnico real y directo: causa más probable primero, cómo verificarla, solución
concreta. Sin relleno, sin repetir la pregunta, terminología técnica correcta. Esta es la respuesta FINAL que
el usuario va a leer en una TERMINAL DE CONSOLA WINDOWS — por eso:
- PROHIBIDO usar LaTeX ($...$, \\mathbf, \\cdot, \\frac, etc.) — la terminal no lo renderiza.
- PROHIBIDO usar markdown (**, *, ##, `, etc.) — se verá como caracteres basura.
- USA texto plano con puntuación normal. Para fórmulas: escríbelas como "V = I x R" o "x = a/b".
- Puedes usar símbolos Unicode simples cuando ayuden: Ω, μ, ≈, ±, ×, √.{contexto_str}"""

    contenido_user = f"Conversación reciente:\n{historial_texto}\n\nPregunta a resolver con profundidad: {pregunta}"
    cfg = _cfg_esfuerzo()
    tokens_profundo = cfg["tokens_profundo"]

    try:
        stream = client.chat.completions.create(
            model=MODELO_PROFUNDO,
            messages=[{"role": "system", "content": sistema},
                      {"role": "user", "content": contenido_user}],
            temperature=0.4,
            max_tokens=tokens_profundo,
            stream=True,
        )
        hubo_texto = False
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content
            except Exception:
                delta = None
            if delta:
                hubo_texto = True
                yield delta
        if hubo_texto:
            _set_motor("☁ " + MODELO_PROFUNDO + " (profundo)")
            return
    except Exception:
        pass

    # Respaldo nube sin streaming (Gemini, una sola entrega).
    try:
        respuesta = _llamar_gemini(sistema, contenido_user,
                                   max_tokens=min(tokens_profundo, 4000), temperature=0.3)
        _set_motor("☁ gemini (profundo)")
        yield _limpiar_formato(respuesta)
        return
    except Exception:
        pass

    # Respaldo LOCAL con STREAMING: a ~15 tokens/s en CPU el texto completo
    # tardaría ~30 s, pero soltándolo por trozos LARA empieza a HABLAR a los
    # ~2-3 s. Funciona incluso sin internet — nunca se queda muda.
    try:
        llm_local = _llm_local()
        if llm_local is not None and llm_local.activo():
            hubo_texto = False
            for trozo in llm_local.chat_stream(
                    [{"role": "system", "content": sistema},
                     {"role": "user", "content": contenido_user}],
                    max_tokens=500, temperature=0.4):
                hubo_texto = True
                yield _limpiar_formato(trozo)
            if hubo_texto:
                _set_motor("🏠 local (profundo de emergencia)")
                yield "\n(Nota: respondí con mi cerebro local; la nube no está disponible ahora.)"
                return
    except Exception:
        pass

    yield "Tuve un problema pensando esto a fondo: ni la nube ni el cerebro local respondieron. ¿Lo intentamos de nuevo?"


def _autorevisar_respuesta(sistema: str, contenido_user: str, primera: str, max_tokens: int) -> str:
    """
    Segunda pasada del nivel "ultracode": el modelo relee su propia respuesta
    y la corrige/mejora antes de entregarla. Si la revisión falla o vuelve
    vacía, se devuelve la primera versión (nunca se pierde la respuesta).
    """
    try:
        r = client.chat.completions.create(
            model=MODELO_PROFUNDO,
            messages=[
                {"role": "system", "content": sistema},
                {"role": "user", "content": contenido_user},
                {"role": "assistant", "content": primera},
                {"role": "user", "content":
                    "Revisa y mejora tu respuesta anterior: corrige errores técnicos, "
                    "valores mal calculados o pasos poco claros. Devuelve SOLO la versión "
                    "final mejorada y completa (no expliques qué cambiaste)."},
            ],
            temperature=0.3,
            max_tokens=max_tokens,
        )
        registrar_groq(r, MODELO_PROFUNDO)
        mejorada = (r.choices[0].message.content or "").strip()
        return mejorada or primera
    except Exception:
        return primera


_INSTR_REFINAR_QUERY = (
    "Convierte la pregunta del usuario en una query de búsqueda de Google corta (4-7 palabras), "
    "concreta, en español. Si la pregunta es sobre precio/costo, incluye 'Perú' y/o 'soles'. "
    "Responde SOLO la query, sin comillas ni explicación.")


def refinar_query_busqueda(pregunta: str) -> str:
    """Llamada barata que convierte la pregunta en una query de búsqueda corta y precisa.
    Las búsquedas son territorio NUBE (mejor query = mejores resultados); el
    modelo local solo entra de respaldo si la nube falla, antes de usar la
    pregunta cruda tal cual."""
    try:
        return _llamar_gemini(
            _INSTR_REFINAR_QUERY,
            pregunta,
            max_tokens=60,      # era 20: demasiado bajo, causaba loop MAX_TOKENS en cada búsqueda
            temperature=0.0,
        )
    except Exception:
        pass
    try:
        llm_local = _llm_local()
        if llm_local is not None and llm_local.activo():
            q = llm_local.chat(
                [{"role": "system", "content": _INSTR_REFINAR_QUERY},
                 {"role": "user", "content": pregunta}],
                max_tokens=40, temperature=0.0)
            if q:
                q = q.strip().strip('"').splitlines()[0].strip()
                if 0 < len(q) <= 100:
                    return q
    except Exception:
        pass
    return pregunta


def resumir_pagina_web(url: str, contenido: str) -> str:
    """Resume el contenido de una página leída, preservando datos exactos."""
    try:
        return _llamar_gemini(
            "Resume el contenido en español. Incluye TODOS los números, porcentajes y datos exactos. "
            "No redondees ni estimes -- copia los valores tal como aparecen. Máximo 8 líneas.",
            f"Página: {url}\n\nContenido:\n{contenido}\n\nResumen con datos exactos:",
            max_tokens=600,      # era 300 — muy poco para páginas con tablas de datos
            temperature=0.0,
        )
    except Exception:
        return contenido[:500]


_INSTR_RESPONDER_DATOS = (
    "Responde la pregunta usando ÚNICAMENTE los datos que te doy. Si necesitas calcular "
    "(restar, dividir, etc.), hazlo con los números exactos. No inventes ni estimes. "
    "Si el dato no está en el contexto, di 'No tengo ese dato exacto.'")


def responder_con_datos(pregunta: str, datos_disponibles: str) -> str:
    """Responde una pregunta numérica/de seguimiento usando solo los datos ya obtenidos.
    Nube primero (los cálculos exactos no son el fuerte de un 3B); local solo
    de respaldo si la nube falla."""
    try:
        return _llamar_gemini(
            _INSTR_RESPONDER_DATOS,
            f"Datos disponibles:\n{datos_disponibles}\n\nPregunta: {pregunta}",
            max_tokens=400,      # era 200 — demasiado bajo para respuestas con cálculos explicados
            temperature=0.0,
        )
    except Exception as e:
        error_nube = e
    try:
        llm_local = _llm_local()
        if llm_local is not None and llm_local.activo():
            r = llm_local.chat(
                [{"role": "system", "content": _INSTR_RESPONDER_DATOS},
                 {"role": "user", "content": f"Datos disponibles:\n{datos_disponibles[:4000]}\n\nPregunta: {pregunta}"}],
                max_tokens=300, temperature=0.0, timeout=60)
            if r:
                return r
    except Exception:
        pass
    return f"Error al calcular — {str(error_nube)}"


_INSTR_RESUMIR_TURNOS = (
    "Comprime esta porción de conversación en 2-4 hechos concretos y reutilizables (no un resumen "
    "narrativo). Cada hecho en una línea con guión. Prioriza: datos técnicos, decisiones tomadas, "
    "preferencias del usuario, tareas o compromisos pendientes. Ignora saludos y relleno. "
    "Responde en español, máximo 4 líneas.")


def resumir_turnos_antiguos(turnos: list) -> str:
    """Comprime turnos viejos en 2-4 hechos concretos, antes de descartarlos del historial en memoria.
    Nube primero (mejor síntesis); respaldo local si falla, para no perder la memoria."""
    texto_turnos = "\n".join(f"{t['role']}: {t['content']}" for t in turnos)
    try:
        return _llamar_gemini(
            _INSTR_RESUMIR_TURNOS,
            texto_turnos,
            max_tokens=300,
            temperature=0.1,
        )
    except Exception as e:
        error_nube = e
    try:
        llm_local = _llm_local()
        if llm_local is not None and llm_local.activo():
            resumen = llm_local.chat(
                [{"role": "system", "content": _INSTR_RESUMIR_TURNOS},
                 {"role": "user", "content": texto_turnos[:6000]}],
                max_tokens=250, temperature=0.1, timeout=60)
            if resumen:
                return resumen
    except Exception:
        pass
    return f"[No se pudo resumir: {error_nube}]"


def generar_codigo_grande(nombre: str, descripcion: str) -> str:
    """
    Genera un programa Python COMPLETO (motor de razonamiento, Gemma) en una
    llamada separada de la conversación normal, sin forzar JSON y con presupuesto
    de tokens grande, para que no pierda contexto a mitad del programa.
    """
    try:
        codigo = _llamar_gemini(
            "Eres un generador de skills Python para un asistente que las ejecuta de forma NO interactiva "
            "(sin terminal visible, sin poder escribir respuestas). Por eso, REGLAS OBLIGATORIAS:\n"
            "1. PROHIBIDO usar input() o cualquier función que espere texto por teclado -- el programa se "
            "ejecuta con stdin cerrado y se colgará/fallará si lo usas.\n"
            "2. El script debe recibir la acción a realizar como argumento de línea de comandos: "
            "sys.argv[1] (ej: 'activar', 'desactivar', '50'). NUNCA mostrar un menú esperando que el usuario "
            "escriba un número.\n"
            "3. Si no se pasa ningún argumento, debe imprimir un mensaje de uso y terminar, sin pedir input().\n"
            "4. Incluye un docstring al inicio explicando exactamente qué argumentos acepta.\n"
            "5. Responde ÚNICAMENTE con código Python completo, funcional, comentado en español. NO uses "
            "bloques markdown (```), NO agregues explicaciones antes o después -- solo el código puro.\n"
            "6. Incluye manejo de errores básico y un bloque if __name__ == '__main__' que lea sys.argv.",
            f"Nombre del programa: {nombre}\n\nDescripción de lo que debe hacer:\n{descripcion}",
            max_tokens=4000,
            temperature=0.2,
        )
        if codigo.startswith("```"):
            codigo = codigo.split("```")[1]
            if codigo.startswith("python"):
                codigo = codigo[len("python"):]
        return codigo.strip()
    except Exception as e:
        return f"# ERROR generando código: {e}"


def buscar_en_web(query: str, detallado: bool = False) -> tuple[str, list[str]]:
    """Busca con Serper.dev y devuelve (resumen_texto, [urls_encontradas]).
    Incluye la fecha actual en la query para evitar resultados cacheados/desactualizados.
    'detallado=True' (para diagramas/hologramas que necesitan varios datos EXACTOS
    a la vez, ej. un cuadro de resultados) pide un resumen ITEMIZADO más largo en
    vez de la prosa condensada de 5 líneas normal, que pierde datos puntuales."""
    import datetime
    fecha_hoy = datetime.date.today().strftime("%d/%m/%Y")
    # Detectar queries de tiempo real (noticias/eventos del día) para inyectar
    # fecha y restringir a resultados recientes. Para el resto NO se restringe:
    # antes qdr:d estaba SIEMPRE activo y mataba resultados perfectamente útiles.
    palabras_tiempo_real = {"elecciones", "votos", "resultados", "precio", "cotización",
                            "noticias", "hoy", "ahora", "actual", "último", "últimos",
                            "partidos", "partido", "mundial", "jornada", "fixture",
                            "calendario", "programación", "programacion", "marcador"}
    query_lower = query.lower()
    query_con_fecha = query
    es_tiempo_real = any(p in query_lower for p in palabras_tiempo_real)
    if es_tiempo_real:
        _meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                  "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
        _hoy = datetime.date.today()
        query_con_fecha = f"{query} {_hoy.day} {_meses[_hoy.month - 1]} {_hoy.year}"

    cuerpo = {"q": query_con_fecha, "gl": "pe", "hl": "es", "num": 8 if detallado else 6}
    if es_tiempo_real:
        cuerpo["tbs"] = "qdr:w"   # última semana: fresco pero sin matar resultados

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            json=cuerpo,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        return f"Error al conectar con Serper: {str(e)}", []

    fragmentos = []
    urls = []

    if "answerBox" in data:
        box = data["answerBox"]
        texto = box.get("answer") or box.get("snippet") or ""
        if texto:
            fragmentos.append(f"[Respuesta directa de Google]: {texto}")

    for item in data.get("organic", [])[:(6 if detallado else 4)]:
        titulo  = item.get("title", "")
        snippet = item.get("snippet", "")
        link    = item.get("link", "")
        fecha   = item.get("date", "")
        if snippet:
            fecha_str = f" [{fecha}]" if fecha else ""
            fragmentos.append(f"- {titulo}{fecha_str}: {snippet}")
        if link:
            urls.append(link)

    if not fragmentos:
        return "No se encontraron resultados para esa búsqueda.", []

    contexto_busqueda = "\n".join(fragmentos)

    if detallado:
        instruccion = (
            f"Hoy es {fecha_hoy}. Extrae de los resultados una lista ITEMIZADA de cada dato puntual "
            "encontrado (un partido, precio, resultado o hecho por línea, con su fecha exacta si la "
            "tiene) — NO resumas en prosa ni generalices, cada línea debe conservar nombres, cifras y "
            "fechas EXACTOS tal como aparecen. Si dos resultados hablan de lo mismo, únelos en una sola "
            "línea sin perder datos. Si una fecha en los resultados es distinta a hoy, consérvala tal "
            "cual (no la corrijas): quien lea esto decidirá si sigue vigente. Usa SOLO estos resultados, "
            "nunca tu conocimiento previo. Hasta 12 líneas. Texto plano, sin markdown ni asteriscos."
        )
        max_tok = 900
    else:
        instruccion = (
            f"Hoy es {fecha_hoy}. Resume resultados de búsqueda en español, claro y conciso. Máximo 5 líneas. "
            "Usa SOLO los datos de los resultados — NO uses tu conocimiento previo de entrenamiento. "
            "Si hay precios y NO están en soles peruanos (S/), conviértelos aproximadamente indicando "
            "la moneda original entre paréntesis. Incluye datos numéricos y porcentajes exactos. "
            "NO uses LaTeX, markdown, asteriscos ni símbolos especiales — texto plano solamente."
        )
        max_tok = 500

    try:
        resumen = _llamar_gemini(
            instruccion,
            f"Pregunta: {query}\n\nResultados:\n{contexto_busqueda}\n\nResumen:",
            max_tokens=max_tok,
            temperature=0.1,
        )
        return resumen, urls
    except Exception:
        return contexto_busqueda, urls


# =========================================================
#  MOTOR 3 — COMPOUND (Groq): gestor de tareas/clientes/compras/proyectos
# =========================================================

PROMPT_GESTOR = """Eres el GESTOR de LARA. Tu único trabajo es convertir una solicitud en
lenguaje natural sobre tareas, clientes, compras o proyectos del taller en UNA acción
estructurada. Responde SIEMPRE con un único JSON válido, nada más.

Acciones disponibles y su forma exacta:
- {"accion":"tarea_crear","params":["titulo","YYYY-MM-DD o vacio","prioridad(alta/media/baja)","proyecto o vacio"]}
- {"accion":"tarea_listar","params":["estado o vacio para todas"]}
- {"accion":"tarea_completar","params":["id_numero"]}
- {"accion":"tarea_eliminar","params":["id_numero"]}
- {"accion":"cliente_crear","params":["nombre","contacto o vacio","notas o vacio"]}
- {"accion":"cliente_listar","params":[]}
- {"accion":"compra_crear","params":["item","proveedor o vacio","precio o vacio"]}
- {"accion":"compra_listar","params":["estado o vacio"]}
- {"accion":"compra_actualizar_estado","params":["id_numero","nuevo_estado"]}
- {"accion":"proyecto_crear","params":["nombre","cliente o vacio","descripcion o vacio"]}
- {"accion":"proyecto_listar","params":["estado o vacio"]}

Si la fecha no viene especificada o es relativa (ej: "para el viernes"), calcula la fecha
real en formato YYYY-MM-DD usando la fecha de hoy que se te da en el contexto.
Si la solicitud es ambigua o falta un dato obligatorio, usa tarea_listar como acción
segura por defecto y explica en mensaje qué falta.
NO converses, NO agregues texto fuera del JSON.
"""


def consultar_gestor_compound(plan_texto: str, fecha_hoy: str) -> dict:
    """
    Recibe el plan en lenguaje natural que generó el router (Llama, vía accion
    "gestion") y devuelve un dict con la acción estructurada que el gestor
    (compound) decidió ejecutar. La ejecución real la hace skills_tasks.py.
    """
    try:
        respuesta = client.chat.completions.create(
            model=MODELO_AGENTE,
            messages=[
                {"role": "system", "content": PROMPT_GESTOR + f"\nFecha de hoy: {fecha_hoy}"},
                {"role": "user", "content": plan_texto},
            ],
        )
        registrar_groq(respuesta, MODELO_AGENTE)
        raw = respuesta.choices[0].message.content.strip()

        from core.util_json import extraer_json
        datos = extraer_json(raw)
        if isinstance(datos, dict):
            return datos
        return {"accion": "error", "params": [], "mensaje": "El gestor no devolvió un JSON válido."}

    except Exception as e:
        return {"accion": "error", "params": [], "mensaje": f"Error en el gestor (compound): {str(e)}"}


# Al importar el motor (arranque de LARA): precalentar el cerebro local en
# segundo plano — carga el modelo en RAM y cachea el prefijo del prompt del
# router, para que el PRIMER comando ya salga rápido. No bloquea ni falla nunca.
try:
    _precalentar_local()
except Exception:
    pass