"""
cerebro.py — Núcleo reutilizable de LARA.

Antes toda la lógica de decisión/ejecución vivía dentro de main.py mezclada con
print(). Eso impedía reutilizarla desde una interfaz gráfica o desde la voz.

Aquí esa lógica se encapsula en la clase LaraCerebro, que:
  - procesa un turno del usuario y DEVUELVE el texto que diría LARA
    (en vez de imprimirlo), para que el llamador decida cómo mostrarlo
    (consola, GUI, voz...).
  - emite mensajes de estado ("buscando en internet...", "pensando...") por un
    callback opcional on_status, sin contaminar la respuesta final.
  - opcionalmente habla en voz alta cada respuesta vía on_speak.
  - mantiene la memoria entre sesiones igual que antes (skills_memoria).

main.py (consola) y gui.py (gráfica) son ahora capas finas sobre este cerebro.
"""

import json
import re
import threading
from datetime import datetime

from config import ADMIN_NOMBRE
from core.llm_engine import (
    consultar_lara, generar_prompt_sistema, buscar_en_web, consultar_gestor_compound,
    refinar_query_busqueda, generar_codigo_grande, resumir_pagina_web,
    responder_con_datos, razonar_profundo, razonar_profundo_stream,
    resumir_turnos_antiguos,
)
from core.skills_os import (
    ejecutar_cmd, ejecutar_cmd_admin, ejecutar_codigo_python, ejecutar_codigo_visible,
    copiar_archivo, mover_archivo, ajustar_brillo, abrir_programa,
    abrir_url_en_navegador, listar_directorio, leer_archivo, crear_archivo,
)
from core.skills_programas import abrir_programa_inteligente
from core.skills_control import escribir_texto, abrir_y_escribir, clic as _clic, presionar
from core.skills_web import leer_pagina_web
from core.skills_download import descargar_archivo, descargar_video
from core.skills_conocimiento import guardar_conocimiento, buscar_conocimiento
from core.logger import log_error
from core.skills_tasks import (
    tarea_crear, tarea_listar, tarea_completar, tarea_eliminar,
    cliente_crear, cliente_listar,
    compra_crear, compra_listar, compra_actualizar_estado,
    proyecto_crear, proyecto_listar,
)
from core.agente_fondo import recordatorio_rapido, _notificar
from core.skills_propuestas import proponer_skill, listar_propuestas_pendientes, aceptar_propuesta
from core.skills_registry import cargar_registro, ejecutar_skill_registrada
from core.skills_novedades import listar_novedades, marcar_novedades_leidas, registrar_limitacion
from core.skills_vision import analizar_pantalla, analizar_imagen, analizar_archivo, aprender_de_archivo
from core import sesiones, modos


# Tabla de despacho para el GESTOR (compound). Idéntica a la de main.py original.
ACCIONES_GESTOR = {
    "tarea_crear":             lambda p: tarea_crear(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else "media", p[3] if len(p) > 3 else ""),
    "tarea_listar":            lambda p: tarea_listar(p[0] if p else ""),
    "tarea_completar":         lambda p: tarea_completar(int(p[0])),
    "tarea_eliminar":          lambda p: tarea_eliminar(int(p[0])),
    "cliente_crear":           lambda p: cliente_crear(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else ""),
    "cliente_listar":          lambda p: cliente_listar(),
    "compra_crear":            lambda p: compra_crear(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else ""),
    "compra_listar":           lambda p: compra_listar(p[0] if p else ""),
    "compra_actualizar_estado": lambda p: compra_actualizar_estado(int(p[0]), p[1]),
    "proyecto_crear":          lambda p: proyecto_crear(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else ""),
    "proyecto_listar":         lambda p: proyecto_listar(p[0] if p else ""),
}

MAX_HISTORIAL_MEMORIA = 30

# Modo agente (Mejora b): acciones cuyo RESULTADO puede alimentar un paso
# siguiente (obtienen información o cambian el estado del equipo). Tras una de
# estas, si el modo agente está activo, LARA vuelve a consultar al modelo para
# decidir el próximo paso. Las acciones "hablar"/"" son terminales (fin del turno).
TOOL_ACCIONES_CADENA = {
    "cmd", "cmd_admin", "buscar", "web", "url", "listar", "leer",
    "ejecutar_codigo", "ejecutar_codigo_visible", "sistema", "ver",
    "analizar_pantalla", "descargar", "descargar_video", "crear",
}
MAX_PASOS_AGENTE = 5  # tope de pasos encadenados por turno (evita bucles)

# Extracción robusta de JSON del router (llaves balanceadas). Compartida con
# llm_engine para no duplicar el parser frágil de antes.
from core.util_json import extraer_json as _extraer_json


class LaraCerebro:
    def __init__(self, on_status=None, on_speak=None, on_holograma=None,
                 espacio="chat", session_id=None, on_speak_stream=None,
                 on_confirm=None):
        self.espacio = espacio
        self.session_id = session_id or sesiones.crear_sesion(espacio)
        self.historial = sesiones.turnos_como_historial(espacio, self.session_id, n=12)
        self.contexto_ejecucion = ""
        self.ultimas_urls = []
        self.ultimo_contenido = ""
        self._on_status = on_status or (lambda m: None)
        self._on_speak = on_speak or (lambda m: None)
        # Callback OPCIONAL de voz en streaming: recibe un generador de texto y
        # lo habla frase a frase mientras el modelo lo genera (latencia baja).
        # Si el llamador no lo provee, el razonamiento profundo usa la ruta
        # normal (_say) sin cambios — la GUI sigue funcionando igual que antes.
        self._on_speak_stream = on_speak_stream
        self._on_holograma = on_holograma or (lambda contenido: None)
        self._tiene_panel = on_holograma is not None  # GUI con panel animado
        self._buffer = []  # respuestas del turno actual
        self.documento_activo = None  # archivo cargado para preguntarle cosas
        self.modo_pantalla = False     # pantalla en vivo: cada pregunta mira la pantalla
        self.modo_dictado = False      # dictado: la GUI teclea lo que digas
        self.dictado_destino = ""      # programa donde dictar (ej. bloc de notas)
        self.ultima_skill = None       # última skill ejecutada (para "desactívala", "apágalo"...)
        self.hablante_actual = None    # quién habló en el último turno (voz_id)
        # Modo agente (Mejora b): cuando está activo, LARA ENCADENA pasos —
        # ejecuta una acción, mira el resultado y decide la siguiente sola, hasta
        # resolver la tarea o llegar al tope de pasos. Por defecto OFF para no
        # cambiar la respuesta ágil de un solo paso. Se enciende con
        # set_modo_agente(True) o diciendo "modo agente on".
        self.modo_agente = False
        self._ultima_accion = ""       # última acción despachada (para el bucle)
        self.voz_concisa = True        # respuestas HABLADAS breves (fluidez por voz)
        self._ultima_respuesta = ""    # última respuesta dada (para "repite")
        # Confirmación de acciones de riesgo (modos manual/plan/lectura). El
        # llamador (consola/GUI) provee cómo preguntar al usuario; si no hay
        # forma de preguntar, en modo manual se ejecuta igual (es TU equipo) pero
        # queda auditado. Devuelve True para autorizar, False para cancelar.
        self._on_confirm = on_confirm
        # Serializa procesar(): GUI, wake word y voz pueden llamar a la vez.
        self._lock = threading.RLock()
        # Pila de acciones REVERSIBLES de la sesión (para "deshaz lo último").
        self._undo = []

    # ── sesiones ──────────────────────────────────────────────
    def _persistir(self, role: str, content: str):
        sesiones.guardar_turno(self.espacio, self.session_id, role, content)

    def nueva_sesion(self):
        self.session_id = sesiones.crear_sesion(self.espacio)
        self.historial = []
        self.contexto_ejecucion = ""
        self.ultimas_urls = []
        self.ultimo_contenido = ""
        return self.session_id

    def cargar_sesion(self, sid: str):
        self.session_id = sid
        self.historial = sesiones.turnos_como_historial(self.espacio, sid, n=12)
        self.contexto_ejecucion = ""
        return self.historial

    def listar_sesiones(self):
        return sesiones.listar_sesiones(self.espacio)

    # ── salida ────────────────────────────────────────────────
    def _status(self, msg: str):
        try:
            self._on_status(msg)
        except Exception:
            pass

    def _say(self, msg: str, hablar: bool = True):
        if msg is None:
            return
        msg = str(msg).strip()
        if not msg:
            return
        self._buffer.append(msg)
        if hablar:
            try:
                self._on_speak(msg)
            except Exception:
                pass

    def set_modo_agente(self, activo: bool) -> None:
        """Enciende/apaga el modo agente (encadenar pasos automáticamente)."""
        self.modo_agente = bool(activo)

    # ── Autorización de acciones de riesgo (modos / sandbox) ──────────────────
    # Texto legible de cada acción, para el mensaje de confirmación o el plan.
    _VERBO_ACCION = {
        "cmd": "ejecutar el comando", "cmd_admin": "ejecutar como ADMINISTRADOR",
        "ejecutar_codigo": "ejecutar código Python", "ejecutar_codigo_visible": "ejecutar código Python (visible)",
        "mover_archivo": "mover el archivo", "copiar_archivo": "copiar el archivo",
        "crear": "crear el archivo", "escribir": "teclear el texto",
        "escribir_en": "abrir y escribir en", "abrir_y_escribir": "abrir y escribir en",
        "clic": "hacer clic en", "presionar": "presionar las teclas", "teclas": "presionar las teclas",
        "descargar": "descargar", "descargar_video": "descargar el video",
        "proponer_skill": "generar la skill", "aceptar_propuesta": "INSTALAR la skill",
        "ejecutar_skill": "ejecutar la skill", "sistema": "la operación de sistema",
    }

    def _describir_accion(self, accion: str, params, mensaje: str = "") -> str:
        verbo = self._VERBO_ACCION.get(accion, accion)
        detalle = ""
        if params:
            detalle = " ".join(str(p) for p in params[:2])
            if len(detalle) > 300:
                detalle = detalle[:300] + "…"
        return f"{verbo} {detalle}".strip()

    def _confirmar(self, accion: str, params, mensaje: str = "") -> bool:
        """Pregunta al usuario antes de una acción de riesgo (modo manual).

        Usa el callback on_confirm que provee la GUI/consola. Si no hay ninguno
        (nadie a quien preguntar), autoriza —es TU equipo— pero deja rastro en
        la auditoría; la seguridad real la dan los modos 'plan'/'lectura'."""
        desc = self._describir_accion(accion, params, mensaje)
        if self._on_confirm is None:
            modos.auditar(accion, params, "sin-UI-de-confirmacion:auto-autorizado")
            return True
        try:
            return bool(self._on_confirm(accion, desc))
        except Exception as e:
            log_error("on_confirm", str(e))
            return False

    # ── Deshacer (undo) ───────────────────────────────────────────────────────
    def _registrar_undo(self, descripcion: str, accion_inversa) -> None:
        """Apila una acción reversible. 'accion_inversa' es un callable sin
        argumentos que revierte lo hecho (se llama en 'deshaz lo último')."""
        self._undo.append((descripcion, accion_inversa))
        if len(self._undo) > 20:
            self._undo = self._undo[-20:]

    def _deshacer_ultimo(self) -> str:
        if not self._undo:
            return "No hay nada reciente que pueda deshacer."
        descripcion, inversa = self._undo.pop()
        try:
            inversa()
            modos.auditar("deshacer", [descripcion], "ok")
            return f"Listo, deshice: {descripcion}."
        except Exception as e:
            log_error("deshacer", str(e))
            return f"No pude deshacer '{descripcion}' — {e}"

    def _razonar_profundo_decir(self, pregunta_original: str) -> str:
        """
        Razonamiento profundo con LATENCIA BAJA. Si el llamador conectó voz en
        streaming (on_speak_stream), Lara empieza a hablar apenas llega la
        primera frase, mientras el modelo sigue generando el resto. Si no, usa
        la ruta normal (genera todo y luego lo dice) — comportamiento idéntico
        al anterior. En ambos casos devuelve el texto completo.
        """
        if self._on_speak_stream is not None:
            try:
                gen = razonar_profundo_stream(
                    pregunta_original, self.contexto_ejecucion, self.historial[-6:])
                texto = self._on_speak_stream(gen)
                if texto and texto.strip():
                    self._buffer.append(texto.strip())
                    return texto.strip()
            except Exception as e:
                log_error("razonar_profundo_stream", str(e))
        # Ruta normal (sin streaming)
        respuesta_profunda = razonar_profundo(
            pregunta_original, self.contexto_ejecucion, self.historial[-6:])
        self._say(respuesta_profunda)
        return respuesta_profunda

    # ── detección determinista de skills (igual que en main.py) ──
    def _detectar_skill_en_input(self, texto: str):
        reg = cargar_registro()
        if not reg:
            return None
        t = texto.lower()
        palabras_crear = {"crea", "genera", "propón", "propon", "mejora", "actualiza",
                          "reescribe", "modifica", "sobrescribe", "sobreescribe", "nueva skill"}
        if any(p in t for p in palabras_crear):
            return None
        indicadores_desactivar = {"desactiva", "quita", "apaga", "disable", "normal",
                                  "original", "vuelve", "restaura", "sin filtro", "claro", "light"}
        for nombre, info in reg.items():
            nombre_legible = nombre.replace("_", " ").lower()
            if nombre_legible not in t and nombre.lower() not in t:
                continue
            args = info.get("argumentos", [])
            argumento = ""
            for arg in args:
                if arg.lower() in t:
                    argumento = arg
                    break
            if not argumento:
                # Inferir del contexto aunque el registro no declare argumentos
                # (registros antiguos): la skill sabrá interpretar activar/desactivar.
                if any(p in t for p in indicadores_desactivar):
                    argumento = "desactivar"
                elif args or any(p in t for p in ("activa", "pon", "aplica", "enciende")):
                    argumento = "activar"
            return (nombre, argumento)
        return None

    # ── detección de preguntas explicativas (para el holograma) ──
    _GATILLOS_EXPLICAR = (
        "explicame", "explícame", "explica ", "explicar ",
        "me explicas", "puedes explicarme", "podrías explicarme", "podrias explicarme",
        "que es ", "qué es ", "que son ", "qué son ", "como funciona", "cómo funciona",
        "hablame de", "háblame de", "enseñame sobre", "enséñame sobre", "enseñame de",
        "en que consiste", "en qué consiste", "que significa", "qué significa",
        "como se hace", "cómo se hace", "para que sirve", "para qué sirve",
        "profundiza en", "profundiza sobre", "profundiza ", "amplia sobre", "amplía sobre",
        "ahonda en", "dame mas detalle", "dame más detalle", "mas sobre ", "más sobre ",
        "cuentame mas de", "cuéntame más de", "detalla ",
        "cuentame sobre", "cuéntame sobre", "cuentame acerca de", "cuéntame acerca de",
        "cuentame de", "cuéntame de", "que sabes de", "qué sabes de", "que sabes sobre",
        "qué sabes sobre", "quiero entender", "no entiendo bien", "no entiendo como",
        "no entiendo cómo", "no entiendo que", "no entiendo qué",
        "dame un resumen de", "hazme un resumen de", "resumeme", "resúmeme",
        "ilustrame", "ilústrame", "por que pasa", "por qué pasa", "por que ocurre",
        "por qué ocurre", "cual es el origen de", "cuál es el origen de",
        "cual es la historia de", "cuál es la historia de",
        "muestrame como", "muéstrame cómo", "muestrame un diagrama", "muéstrame un diagrama",
        "hazme un diagrama", "haz un diagrama", "quiero ver como", "quiero ver cómo",
        "instruyeme sobre", "instrúyeme sobre",
    )
    _NO_EXPLICAR = ("hora", "fecha", "cómo estás", "como estas", "quién eres", "quien eres",
                    "qué hora", "que hora", "cómo te", "como te")

    def _tema_explicativo(self, texto: str):
        """Si el usuario pide ENTENDER un tema, devuelve el tema; si no, None."""
        t = texto.lower().strip()
        if len(t) < 8 or any(n in t for n in self._NO_EXPLICAR):
            return None
        for g in self._GATILLOS_EXPLICAR:
            idx = t.find(g)
            if idx != -1:
                tema = texto[idx + len(g):].strip(" ?¿.!¡")
                # El tema debe tener sustancia (no "qué es?" suelto)
                if len(tema) >= 3:
                    return tema
                return texto.strip(" ?¿.!¡")
        return None

    # ── API multimodal directa (la usa la GUI con sus botones) ──
    def ver_pantalla(self, pregunta: str = "") -> str:
        self._status("Mirando tu pantalla...")
        res = analizar_pantalla(pregunta)
        salida = res.get("respuesta") if "error" not in res else f"No pude ver la pantalla — {res['error']}"
        self._buffer = []
        self._say(salida)
        self.contexto_ejecucion = f"[Análisis de pantalla]: {salida[:600]}"
        self.historial.append({"role": "user", "content": f"[LARA miró la pantalla]: {salida[:400]}"})
        self._persistir("assistant", salida)
        return salida

    def analizar_archivo_directo(self, ruta: str, pregunta: str = "", aprender: bool = False) -> str:
        self._status(f"Analizando {ruta}...")
        if aprender:
            res = aprender_de_archivo(ruta)
            salida = (f"Aprendido y guardado: {res.get('titulo','')}\n\n{res.get('resumen','')}"
                      if "error" not in res else f"No pude aprender del archivo — {res['error']}")
        else:
            res = analizar_archivo(ruta, pregunta)
            salida = res.get("respuesta") if "error" not in res else f"No pude analizar el archivo — {res['error']}"
        self._buffer = []
        self._say(salida)
        self.contexto_ejecucion = f"[Análisis de archivo {ruta}]: {salida[:600]}"
        self.historial.append({"role": "user", "content": f"[LARA analizó {ruta}]: {salida[:400]}"})
        self._persistir("assistant", salida)
        return salida

    # ── documento cargado para preguntarle (mantiene el flujo) ──
    def cargar_documento(self, ruta: str) -> None:
        self.documento_activo = ruta

    def limpiar_documento(self) -> None:
        self.documento_activo = None

    def consultar_documento(self, pregunta: str) -> str:
        """Responde una pregunta sobre el documento/imagen cargado actualmente."""
        import os
        ruta = self.documento_activo
        self._buffer = []
        self.historial.append({"role": "user", "content": pregunta})
        self._persistir("user", pregunta)
        self._status("Analizando el archivo...")
        res = analizar_archivo(ruta, pregunta)
        salida = res.get("respuesta") if "error" not in res else f"No pude analizar el archivo — {res['error']}"
        self._say(salida)
        self.contexto_ejecucion = f"[Archivo {os.path.basename(ruta)} — P:{pregunta[:60]}]: {salida[:400]}"
        self.historial.append({"role": "assistant", "content": salida})
        self._persistir("assistant", salida)
        return salida

    # Coletillas de FORMATO ("...en un diagrama de llaves", "...con un dibujo")
    # que describen CÓMO dibujar, no QUÉ buscar: si se dejan en la query de
    # búsqueda, Google devuelve páginas sobre "qué es un diagrama de llaves"
    # en vez de los resultados/estado actual del tema real.
    _RUIDO_BUSQUEDA = re.compile(
        r"\s+(?:en|con|de|como|a modo de)\s+(?:un|una|forma|modo)?\s*"
        r"(?:diagrama|gr[aá]fico|dibujo|animaci[oó]n|esquema|infograf[ií]a|"
        r"holograma|mapa mental|l[ií]nea de tiempo|l[ií]neas de tiempo|"
        r"tabla|cuadro|bracket|llaves)\b.*$",
        re.IGNORECASE,
    )

    @classmethod
    def _limpiar_tema_busqueda(cls, tema: str) -> str:
        """Quita instrucciones de FORMATO VISUAL del tema para que la búsqueda
        web apunte al contenido real (marcador, fecha, resultado), no al
        formato pedido. Si el recorte deja muy poco, se usa el tema completo."""
        limpio = cls._RUIDO_BUSQUEDA.sub("", tema).strip(" ,.")
        return limpio if len(limpio) >= 4 else tema

    def explicar_visual(self, tema: str) -> str:
        """Genera una explicación en slides, la anima en el panel y la narra por slide.

        Si el tema huele a ACTUALIDAD (mundial, resultados, precios...), primero
        busca en internet y pasa esos datos como contexto: así la explicación usa
        cifras reales y no un diagrama genérico de memoria. Además siempre añade
        un mini-resumen de los últimos turnos para que fluya con la conversación.
        """
        self._status(f"Preparando explicación de {tema}...")
        from core.skills_explicar import generar_explicacion_visual

        contexto = self.contexto_ejecucion or ""

        # Mini-resumen de los últimos 2 turnos, para hilar con lo que se hablaba
        ultimos = [h for h in self.historial[-2:] if h.get("content")]
        if ultimos:
            hilo = " | ".join(f"{h['role']}: {h['content'][:120]}" for h in ultimos)
            contexto += f"\nÚltimos turnos de la conversación (para dar continuidad, sin saludar): {hilo}"

        # Tema de actualidad -> buscar datos reales ANTES de generar
        if self._pide_datos_actuales(tema) or any(
                p in tema.lower() for p in self._ACTUALIDAD_FUERTE + self._ACTUALIDAD_DEBIL):
            consulta = self._limpiar_tema_busqueda(tema)
            self._status(f"Buscando datos actuales sobre {consulta}...")
            resumen_web, urls = "", []
            try:
                # detallado=True: lista itemizada (una línea por dato con su fecha
                # exacta) en vez de la prosa de 5 líneas normal, que para un
                # holograma con varias piezas (ej. un cuadro de resultados) perdía
                # demasiado detalle y llevaba a rellenar huecos inventando.
                resumen_web, urls = buscar_en_web(consulta, detallado=True)
            except Exception as e:
                log_error("explicar_visual_buscar", str(e))
            if resumen_web and urls:
                self.ultimas_urls = urls
                self.ultimo_contenido = resumen_web
                contexto += (
                    "\n[DATOS CONFIRMADOS POR BÚSQUEDA WEB — ÚNICA FUENTE VÁLIDA; cada línea trae su "
                    "propia fecha, compárala contra HOY antes de usarla]:\n"
                    + resumen_web[:2200] +
                    "\nUsa EXCLUSIVAMENTE estos datos para equipos, marcadores, fechas y rondas. "
                    "PROHIBIDO mezclar con ediciones/temporadas pasadas o con lo que recuerdes de tu "
                    "entrenamiento: si no aparece arriba, no lo dibujes ni lo digas. Si estos datos no "
                    "alcanzan para completar todo lo pedido, muestra SOLO lo confirmado y dilo así en "
                    "la narración (ej. \"esto es lo confirmado hasta ahora\").")
            else:
                # Búsqueda fallida o vacía: seguir, pero que la narración sea honesta
                contexto += (
                    "\n[SIN DATOS FRESCOS]: se intentó buscar información actual sobre el tema pero no "
                    "hubo resultados. PROHIBIDO inventar marcadores, fechas, equipos o resultados de tu "
                    "memoria como si fueran de hoy: dilo honestamente en la narración (\"no tengo el dato "
                    "de hoy\") y limita el diagrama a lo genérico/atemporal del tema (formato, reglas), "
                    "dejando claro que NO es el estado actual.")

        contenido = generar_explicacion_visual(tema, contexto)
        self._buffer = []
        if "error" in contenido:
            # Si falla la estructura, responde igual en texto plano hablado
            self._say(f"Te explico sobre {tema}. Dame un momento, no pude armar la animación esta vez.")
            return "\n".join(self._buffer)

        resumen_txt = self._texto_de_slides(contenido)

        if self._tiene_panel:
            # El panel de la GUI dibuja y NARRA cada slide -> aquí no hablamos (evita doble voz)
            self._say(resumen_txt, hablar=False)
            try:
                self._on_holograma(contenido)
            except Exception:
                pass
        else:
            # Consola: no hay panel, así que sí narramos el texto completo
            self._say(resumen_txt)

        resumen_ctx = f"[Explicación de '{tema}']: {contenido.get('narracion_intro','')[:300]}"
        self.contexto_ejecucion = resumen_ctx
        self.historial.append({"role": "user", "content": resumen_ctx})
        self._persistir("assistant", resumen_txt)
        return resumen_txt

    @staticmethod
    def _texto_de_slides(contenido: dict) -> str:
        lineas = [contenido.get("titulo", "").upper()]
        if contenido.get("narracion_intro"):
            lineas.append(contenido["narracion_intro"])
        for s in contenido.get("slides", []):
            lineas.append("")
            lineas.append(f"▸ {s.get('titulo','')}")
            for it in s.get("items", []):
                lineas.append(f"   • {it}")
        return "\n".join(lineas).strip()

    # ── procesamiento de un turno de texto ──────────────────────
    _LIMPIAR_DOC = ("olvida el archivo", "olvida la imagen", "olvida el documento",
                    "cierra el archivo", "ya no el archivo", "quita el archivo",
                    "salir del archivo", "olvida el adjunto")

    # ── intents deterministas (instantáneos, sin LLM, gratis y fiables) ──
    _SISTEMA_INTENTS = [
        (("que hora", "qué hora", "la hora", "dime la hora", "hora es"), "hora"),
        (("bateria", "batería", "cuanta carga", "cuánta carga", "queda de pila", "porcentaje de pila"), "bateria"),
        (("cuanta ram", "cuánta ram", "info del sistema", "informacion del sistema", "especificaciones",
          "espacio en disco", "cuanto disco", "cuánto disco", "memoria libre", "specs"), "info"),
        (("mi ip", "direccion ip", "dirección ip", "cual es mi ip", "cuál es mi ip"), "ip"),
        (("wifi", "wi-fi", "red wi"), "wifi"),
        (("sube el volumen", "sube volumen", "mas volumen", "más volumen", "aumenta el volumen", "sube el audio"), "volumen_subir"),
        (("baja el volumen", "baja volumen", "menos volumen", "reduce el volumen", "baja el audio"), "volumen_bajar"),
        (("silencia el", "mutea", "quita el sonido", "silencia el audio", "silencia el volumen"), "silenciar"),
        (("bloquea la pc", "bloquea el equipo", "bloquea la computadora", "bloquea mi pc", "bloquea la pantalla"), "bloquear"),
        (("toma una captura", "captura de pantalla", "haz una captura", "screenshot", "captura la pantalla"), "captura"),
        (("vacia la papelera", "vacía la papelera", "vaciar la papelera"), "papelera"),
        (("que consume memoria", "qué consume memoria", "procesos que mas", "procesos que más", "uso de memoria"), "procesos"),
        (("en el portapapeles", "lee el portapapeles", "que copie", "qué copié"), "portapapeles"),
    ]
    # "Silencio" escrito o dicho = callar la VOZ de LARA (no mutear Windows).
    # Se compara por igualdad/startswith sobre el texto limpio, nunca con 'in'
    # suelto, para no capturar frases que solo contienen la palabra
    # ("explícame el silencio sísmico" NO debe silenciar la voz).
    _VOZ_OFF = ("silencio", "cállate", "callate", "calla", "guarda silencio",
                "silencio por favor", "deja de hablar", "no hables")
    _VOZ_ON = ("activa la voz", "habla de nuevo", "reactiva la voz",
               "enciende la voz", "ya puedes hablar")

    # Palabras que delatan que el usuario pregunta por ACTUALIDAD: con ellas la
    # memoria del modelo no basta y hay que ir a internet. Las "fuertes" bastan
    # solas; las "débiles" ("hoy", "tabla"...) solo cuentan si además la frase
    # parece una petición de información (interrogativo o signo de pregunta).
    _ACTUALIDAD_FUERTE = ("mundial", "partido", "noticias", "noticia", "precio", "precios",
                          "resultados", "resultado", "elecciones", "clasificación", "clasificacion",
                          "jornada", "marcador", "ranking", "última hora", "ultima hora")
    _ACTUALIDAD_DEBIL = ("hoy", "actual", "actuales", "ahora mismo", "tabla", "2025", "2026")
    _INTERROGATIVOS = ("qué", "que", "cómo", "como", "cuál", "cual", "cuánto", "cuanto",
                       "cuándo", "cuando", "quién", "quien", "dónde", "donde",
                       "dime", "dame", "busca", "cuéntame", "cuentame")

    def _pide_datos_actuales(self, texto: str) -> bool:
        """True si 'texto' pregunta por algo de actualidad (requiere datos de internet)."""
        t = (texto or "").lower()
        if not t or t.startswith("["):
            return False
        # Smalltalk sobre el estado propio no es actualidad ("hoy estoy cansado")
        if any(x in t for x in ("cómo estás", "como estas", "estoy ", "me siento")):
            return False
        # La fecha y la hora las SABE la PC: no son "actualidad de internet"
        # ("qué día es hoy" caía aquí por la palabra débil "hoy" y buscaba en
        # la web algo que el reloj del sistema responde gratis y al instante).
        if re.search(r"(qu[eé]|a qu[eé]|en qu[eé])\s+(d[ií]a|fecha|hora)\s+(es|estamos)", t):
            return False
        if any(re.search(r"\b" + re.escape(p) + r"\b", t) for p in self._ACTUALIDAD_FUERTE):
            return True
        if any(re.search(r"\b" + re.escape(p) + r"\b", t) for p in self._ACTUALIDAD_DEBIL):
            palabras = re.findall(r"\w+", t)
            return "?" in texto or any(w in self._INTERROGATIVOS for w in palabras)
        return False

    _DICTADO_INI = ("toma dictado", "modo dictado", "dicta en", "escribe lo que te dicte",
                    "escribe lo que diga", "escribe lo que te diga", "voy a dictar", "te voy a dictar")
    _DICTADO_FIN = ("termina el dictado", "fin del dictado", "para de escribir", "deja de escribir",
                    "termina de escribir", "para el dictado")
    _PANTALLA_VIVO_INI = ("pantalla en vivo", "mira mi pantalla en vivo", "modo pantalla", "observa mi pantalla",
                          "mira lo que hago", "ve mi pantalla en vivo", "vigila mi pantalla")
    _PANTALLA_VIVO_FIN = ("deja de mirar", "sal del modo pantalla", "ya no mires", "desactiva pantalla",
                          "deja de observar")
    _PANTALLA_1VEZ = ("mira mi pantalla", "analiza mi pantalla", "que ves en mi pantalla", "qué ves en mi pantalla",
                      "revisa mi pantalla", "mira la pantalla")

    # Pronombres que se refieren a la ÚLTIMA skill ejecutada ("desactívala")
    _PRON_DESACT = ("desactivala", "desactívala", "desactivalo", "desactívalo",
                    "apagala", "apágala", "apagalo", "apágalo", "quitala", "quítala",
                    "quitalo", "quítalo", "desactiva eso", "apaga eso", "quita eso",
                    "desactivalo ya", "no está desactivado", "no esta desactivado",
                    "sigue activado", "desactiva", "desactivar")
    _PRON_ACT = ("activala", "actívala", "activalo", "actívalo", "activa eso",
                 "enciendela", "enciéndela", "enciendelo", "enciéndelo", "ponla de nuevo",
                 "ponlo de nuevo", "activalo ya")

    def _skill_por_pronombre(self, t: str):
        """'desactívala' / 'actívalo' -> ejecuta la última skill con ese argumento."""
        if not self.ultima_skill:
            return None
        limpio = t.strip(" .,!¡¿?")
        arg = None
        if limpio in self._PRON_DESACT or any(limpio.startswith(p) for p in ("desactiva", "apaga", "quita") if len(limpio) < 22):
            arg = "desactivar"
        elif limpio in self._PRON_ACT:
            arg = "activar"
        if not arg:
            return None
        reg = cargar_registro()
        info = reg.get(self.ultima_skill, {})
        args_reg = info.get("argumentos", [])
        # Si el registro declara argumentos, validar; si está vacío (registro
        # antiguo), pasar el argumento igual: la skill sabrá interpretarlo.
        if args_reg and arg not in args_reg:
            return None
        return (self.ultima_skill, arg)

    def _pre_despacho_rapido(self, prompt: str):
        """Resuelve comandos comunes sin gastar LLM (instantáneo). Devuelve respuesta o None."""
        t = prompt.lower().strip()

        def _resp(texto):
            self._buffer = []
            self._say(texto)
            self.historial.append({"role": "user", "content": prompt})
            self._persistir("user", prompt)
            self._persistir("assistant", texto)
            return texto

        # "Silencio" / "cállate" -> apagar SOLO la voz de LARA (no el volumen de
        # Windows). Igualdad o startswith casi exacto (tolera "cállate ya",
        # "silencio, por favor") sin capturar frases largas que contengan la palabra.
        limpio_voz = t.strip(" .,!¡¿?")

        def _casi_exacto(frases):
            return any(limpio_voz == f or
                       (limpio_voz.startswith(f) and len(limpio_voz) <= len(f) + 12)
                       for f in frases)

        if _casi_exacto(self._VOZ_OFF):
            from core import skills_voz
            skills_voz.set_voz_activa(False)  # ANTES de responder, para no narrar esto
            return _resp("Voz desactivada. Escribe o di 'activa la voz' cuando me quieras oír.")
        if _casi_exacto(self._VOZ_ON):
            from core import skills_voz
            skills_voz.set_voz_activa(True)
            return _resp("Voz activada. Te escucho y te hablo de nuevo.")

        # "Desactívala" / "actívalo" -> última skill ejecutada, con el argumento correcto
        pron = self._skill_por_pronombre(t)
        if pron:
            nombre_skill, arg = pron
            self._status(f"Ejecutando skill: {nombre_skill} {arg}")
            r = ejecutar_skill_registrada(nombre_skill, arg)
            salida = r.get("stdout") or r.get("error") or "Hecho."
            self.contexto_ejecucion = f"Skill '{nombre_skill}' {arg}: {salida[:200]}"
            return _resp(salida)

        # Modo pantalla en vivo: salir o responder mirando la pantalla
        if self.modo_pantalla and any(f in t for f in self._PANTALLA_VIVO_FIN):
            self.modo_pantalla = False
            return _resp("Listo, dejo de mirar tu pantalla.")
        if self.modo_pantalla:
            self._persistir("user", prompt)
            return self.ver_pantalla(prompt)

        if t.startswith("anota ") or t.startswith("apunta "):
            from core.skills_sistema import ejecutar as _sis
            return _resp(_sis("anota", prompt.split(" ", 1)[1]).get("mensaje", "Anotado."))

        if any(f in t for f in self._DICTADO_INI):
            destino = ""
            for prog in ("bloc de notas", "notepad", "documento", "word"):
                if prog in t:
                    destino = "word" if prog == "word" else "bloc de notas"
                    break
            self.dictado_destino = destino
            self.modo_dictado = True
            if destino:
                from core.skills_programas import abrir_programa_inteligente
                abrir_programa_inteligente(destino)
            return _resp("Te escucho. Dicta y voy escribiendo. Di 'termina el dictado' cuando acabes.")

        if any(f in t for f in self._PANTALLA_VIVO_INI):
            self.modo_pantalla = True
            return _resp("Modo pantalla en vivo activado. Pregúntame lo que quieras sobre lo que ves.")

        if any(f in t for f in self._PANTALLA_1VEZ):
            self._persistir("user", prompt)
            return self.ver_pantalla(prompt)

        for frases, op in self._SISTEMA_INTENTS:
            if any(f in t for f in frases):
                from core.skills_sistema import ejecutar as _sis
                r = _sis(op)
                self.contexto_ejecucion = f"[sistema {op}]: {r.get('mensaje','')[:200]}"
                return _resp(r.get("mensaje", "Listo."))

        return None

    def procesar(self, prompt: str, hablante: str | None = None) -> str:
        """Procesa un turno. Serializado con un lock para que GUI, wake word y
        voz (que pueden entrar a la vez) no se pisen el historial ni el buffer."""
        with self._lock:
            return self._procesar(prompt, hablante)

    def _procesar(self, prompt: str, hablante: str | None = None) -> str:
        """Procesa un mensaje del usuario y devuelve la respuesta de LARA (texto).

        'hablante' (opcional) es el nombre identificado por voz_id: si no es el
        admin, se avisa al modelo para que se dirija a esa persona por su nombre.
        Las llamadas antiguas con un solo argumento siguen funcionando igual.
        """
        self.hablante_actual = hablante

        # ── Cambio de modo por voz/texto ("modo auto", "modo plan"...) ──
        modo_destino = modos.detectar_cambio_modo(prompt)
        if modo_destino:
            modos.set_modo(modo_destino)
            self._buffer = []
            self._say(f"Cambiado. {modos.DESCRIPCION[modo_destino]}")
            return "\n".join(self._buffer)

        # ── Deshacer la última acción reversible ("deshaz lo último") ──
        _pu = prompt.lower().strip().strip(".!?¡¿")
        if _pu in ("deshaz", "deshazlo", "deshaz lo ultimo", "deshaz lo último",
                   "deshacer", "revierte", "revierte lo ultimo", "revierte lo último",
                   "undo", "cancela lo ultimo", "cancela lo último"):
            self._buffer = []
            self._say(self._deshacer_ultimo())
            return "\n".join(self._buffer)

        # Toggle del modo agente por voz/texto (Mejora b).
        _p = prompt.lower().strip().strip(".!?¡¿")
        if _p in ("modo agente on", "modo agente", "activa el modo agente", "encender modo agente",
                  "modo autonomo", "modo autónomo"):
            self.set_modo_agente(True)
            self._buffer = []
            self._say("Modo agente activado. Ahora encadeno pasos hasta terminar la tarea.")
            return "\n".join(self._buffer)
        if _p in ("modo agente off", "apaga el modo agente", "apagar modo agente", "detener modo agente"):
            self.set_modo_agente(False)
            self._buffer = []
            self._say("Modo agente desactivado. Vuelvo a responder paso a paso.")
            return "\n".join(self._buffer)

        # ── Controles de conversación por voz/texto (respuesta instantánea) ──
        # Cancelar / callar: corta la voz y cualquier cosa en curso.
        if _p in ("para", "parar", "detente", "cancela", "cancelar", "callate", "cállate",
                  "stop", "ya para", "basta"):
            try:
                from core import skills_voz as _sv
                _sv.detener_voz()
            except Exception as e:
                log_error("detener_voz", str(e))
            self._buffer = []
            return ""  # sin respuesta hablada: solo se detiene

        # Repetir la última respuesta.
        if _p in ("repite", "repítelo", "repitelo", "repite eso", "que dijiste",
                  "qué dijiste", "no te escuche", "no te escuché"):
            self._buffer = []
            self._say(self._ultima_respuesta or "No he dicho nada aún.")
            return "\n".join(self._buffer)

        # Velocidad del habla.
        if any(f in _p for f in ("habla mas rapido", "habla más rápido", "mas rapido",
                                 "más rápido", "acelera")):
            try:
                from core import skills_voz as _sv
                v = _sv.set_velocidad(delta_pct=15)
                self._buffer = []
                self._say(f"Listo, hablo más rápido ({v}).")
                return "\n".join(self._buffer)
            except Exception as e:
                log_error("set_velocidad(+)", str(e))
        if any(f in _p for f in ("habla mas despacio", "habla más despacio", "mas despacio",
                                 "más despacio", "mas lento", "más lento")):
            try:
                from core import skills_voz as _sv
                v = _sv.set_velocidad(delta_pct=-15)
                self._buffer = []
                self._say(f"De acuerdo, hablo más despacio ({v}).")
                return "\n".join(self._buffer)
            except Exception as e:
                log_error("set_velocidad(-)", str(e))
        if _p in ("habla normal", "velocidad normal", "velocidad por defecto"):
            try:
                from core import skills_voz as _sv
                _sv.set_velocidad(absoluto=0)
                self._buffer = []
                self._say("Velocidad de voz restablecida.")
                return "\n".join(self._buffer)
            except Exception:
                pass

        # Longitud de las respuestas habladas.
        if _p in ("se breve", "sé breve", "respuestas cortas", "responde corto", "mas corto", "más corto"):
            self.voz_concisa = True
            self._buffer = []
            self._say("Entendido, seré más breve.")
            return "\n".join(self._buffer)
        if _p in ("respuestas largas", "explayate", "explávate", "explaya", "mas detalle", "más detalle",
                  "responde largo", "se detallada", "sé detallada"):
            self.voz_concisa = False
            self._buffer = []
            self._say("De acuerdo, daré respuestas más completas.")
            return "\n".join(self._buffer)

        # Auto-diagnóstico de sistemas.
        if _p in ("diagnostico", "diagnóstico", "revisate", "revísate", "autodiagnostico",
                  "como estas de sistemas", "cómo estás de sistemas", "estado de sistemas"):
            self._buffer = []
            self._status("Ejecutando diagnóstico...")
            try:
                from core import diagnostico, skills_voz as _sv
                detalle = diagnostico.texto()
                self._say(detalle, hablar=False)      # el detalle completo va a pantalla
                if _sv.VOZ_ACTIVA:
                    _sv.hablar(diagnostico.resumen_hablado())  # en voz, solo el resumen
            except Exception as e:
                self._say(f"No pude completar el diagnóstico: {e}")
            return "\n".join(self._buffer)

        # RAG: (re)indexar tus documentos para poder responder desde ellos.
        if _p in ("indexa mis documentos", "indexar documentos", "reindexa documentos",
                  "reindexar documentos", "actualiza mis documentos", "aprende mis documentos",
                  "lee mis documentos", "actualiza el indice", "actualiza el índice"):
            self._buffer = []
            self._status("Indexando tus documentos...")
            try:
                from core import rag_documentos
                r = rag_documentos.indexar_todo()
                est = rag_documentos.estado()
                self._say(f"Documentos al día: {r.get('archivos_actualizados', 0)} archivos "
                          f"nuevos o modificados ({r.get('fragmentos_nuevos', 0)} fragmentos). "
                          f"Ya puedo consultar {est['fragmentos']} fragmentos de {est['archivos']} archivos.")
            except Exception as e:
                log_error("rag_indexar_cmd", str(e))
                self._say("No pude indexar los documentos ahora mismo.")
            return "\n".join(self._buffer)

        # RAG: "indexa/aprende/lee la carpeta <ruta o nombre conocido>".
        m_carp = re.match(r"(?:indexa|indexar|aprende|lee)\s+(?:la\s+)?carpeta\s+(.+)", _p)
        if m_carp:
            self._buffer = []
            try:
                from core import rag_documentos
                ruta = rag_documentos.resolver_carpeta(m_carp.group(1).strip())
                self._status(f"Indexando {ruta}...")
                r = rag_documentos.indexar_carpeta(ruta)
                if "error" in r:
                    self._say(r["error"])
                else:
                    self._say(f"Indexé {r['archivos_actualizados']} archivos de «{ruta.name}» "
                              f"({r['fragmentos_nuevos']} fragmentos nuevos).")
            except Exception as e:
                log_error("rag_carpeta_cmd", str(e))
                self._say("No pude indexar esa carpeta.")
            return "\n".join(self._buffer)

        # RAG: estado del índice de documentos.
        if _p in ("que documentos conoces", "qué documentos conoces", "estado de documentos",
                  "cuantos documentos tienes", "cuántos documentos tienes", "estado del indice",
                  "estado del índice"):
            self._buffer = []
            try:
                from core import rag_documentos
                est = rag_documentos.estado()
                self._say(f"Tengo indexados {est['archivos']} documentos "
                          f"({est['fragmentos']} fragmentos consultables). "
                          f"Di 'indexa mis documentos' para actualizar.")
            except Exception as e:
                log_error("rag_estado_cmd", str(e))
                self._say("No pude consultar el estado de los documentos.")
            return "\n".join(self._buffer)

        # Aprendizaje de voz SIN el botón de entrenar: "aprende mi voz [como X]".
        # Inicia una sesión de enrolamiento pasivo; el bucle de escucha de la GUI
        # va tomando las siguientes frases que hables hasta completar el perfil.
        if any(f in _p for f in ("aprende mi voz", "reconoce mi voz", "recuerda mi voz",
                                 "memoriza mi voz", "aprende mi voz como", "registra mi voz")):
            self._buffer = []
            nombre = ADMIN_NOMBRE
            m = re.search(r"como\s+([a-záéíóúñ]+)", _p)
            if m:
                nombre = m.group(1).capitalize()
            elif hablante:
                nombre = hablante
            try:
                from core import voz_id
                voz_id.iniciar_aprendizaje(nombre)
                self._say(f"Perfecto. Voy a aprender tu voz, {nombre}. Háblame con normalidad unos "
                          f"segundos —cuéntame lo que sea— y te aviso cuando ya te reconozca.")
            except Exception as e:
                log_error("iniciar_aprendizaje", str(e))
                self._say("No pude iniciar el aprendizaje de voz ahora mismo.")
            return "\n".join(self._buffer)

        # Rutinas programables (Mejora c): "modo taller", "modo noche"... Se
        # detectan antes que nada y ejecutan una secuencia de pasos de una vez.
        try:
            from core import rutinas
            nombre_rutina = rutinas.detectar_rutina(prompt)
            if nombre_rutina:
                self._buffer = []
                self._status(f"Ejecutando rutina: {nombre_rutina}")
                self.historial.append({"role": "user", "content": prompt})
                self._persistir("user", prompt)
                texto = rutinas.ejecutar_rutina(nombre_rutina, on_speak=self._on_speak)
                if texto:
                    self._buffer.append(texto)
                    self._persistir("assistant", texto)
                    self.historial.append({"role": "assistant", "content": texto})
                return "\n".join(self._buffer)
        except Exception as e:
            log_error("rutinas", str(e))

        # Si hay un documento cargado, las preguntas son SOBRE él (mantiene el flujo)
        if self.documento_activo:
            if any(f in prompt.lower() for f in self._LIMPIAR_DOC):
                self.limpiar_documento()
                self._buffer = []
                self._say("Listo, dejo el archivo de lado. ¿En qué seguimos?")
                return "\n".join(self._buffer)
            return self.consultar_documento(prompt)

        # Comandos comunes resueltos al instante (hora, batería, volumen, pantalla, dictado...)
        rapido = self._pre_despacho_rapido(prompt)
        if rapido is not None:
            return rapido

        self._buffer = []
        self.historial.append({"role": "user", "content": prompt})
        self._persistir("user", prompt)
        self._status("LARA pensando...")

        # Conocimiento guardado relevante
        try:
            relevantes = buscar_conocimiento(prompt)
            if relevantes:
                extra = "\n".join(f"[Conocimiento guardado — {r['titulo']}]: {r['contenido'][:500]}" for r in relevantes)
                self.contexto_ejecucion = (self.contexto_ejecucion + "\n" + extra) if self.contexto_ejecucion else extra
        except Exception as e:
            log_error("buscar_conocimiento", str(e))

        # Modo reunión activo (Mejora 5): si Lara está escuchando el ambiente y
        # le preguntas por tu opinión/análisis de lo hablado, le damos la
        # transcripción reciente como contexto para que responda con fundamento.
        try:
            from core import modo_reunion
            if modo_reunion.activo():
                _pl = prompt.lower()
                _quiere = any(w in _pl for w in (
                    "opina", "opinas", "opinión", "opinion", "piensas", "análisis",
                    "analisis", "analiza", "resume", "resumen", "conversa", "reunión",
                    "reunion", "hablaron", "dijeron", "esto", "eso", "acuerdo"))
                if _quiere:
                    conv = modo_reunion.transcripcion_texto()
                    if conv and len(conv) > 20:
                        extra = ("CONVERSACIÓN QUE LARA ESTÁ ESCUCHANDO (para tu análisis/opinión, "
                                 "puede tener errores de transcripción):\n" + conv[-4000:])
                        self.contexto_ejecucion = (self.contexto_ejecucion + "\n" + extra) if self.contexto_ejecucion else extra
        except Exception as e:
            log_error("modo_reunion.contexto", str(e))

        # Memoria semántica de largo plazo (Mejora 4): recuerdos de OTRAS
        # conversaciones relacionados por SIGNIFICADO con lo que se pregunta
        # ahora. Da continuidad tipo JARVIS ("como me dijiste hace semanas...").
        try:
            from core.memoria_vectorial import recordar
            recuerdos = recordar(prompt, k=3)
            # Excluimos lo idéntico al prompt actual (ya está en el historial).
            recuerdos = [r for r in recuerdos if r["texto"].strip() != prompt.strip()]
            if recuerdos:
                extra = "Recuerdos relevantes de conversaciones anteriores (úsalos si aplican, no los cites literalmente):\n" + \
                    "\n".join(f"- ({r['fecha']}) {r['texto'][:280]}" for r in recuerdos)
                self.contexto_ejecucion = (self.contexto_ejecucion + "\n" + extra) if self.contexto_ejecucion else extra
        except Exception as e:
            log_error("memoria_vectorial.recordar", str(e))

        # RAG de documentos: fragmentos de TUS archivos (PDF, Word, notas) que se
        # parecen a la pregunta. Así LARA responde desde tu material, citando el
        # archivo. Búsqueda LOCAL (mismos embeddings de la memoria); si no hay
        # nada suficientemente relevante, no añade ruido.
        try:
            from core import rag_documentos
            frags = rag_documentos.buscar(prompt, k=4)
            if frags:
                extra = ("Fragmentos de TUS documentos relacionados con la pregunta "
                         "(cita el archivo entre comillas si los usas; no inventes lo que no esté):\n" +
                         "\n".join(f"- [{f['archivo']}]: {f['texto'][:400]}" for f in frags))
                self.contexto_ejecucion = (self.contexto_ejecucion + "\n" + extra) if self.contexto_ejecucion else extra
        except Exception as e:
            log_error("rag_documentos.buscar", str(e))

        # Novedades del agente de fondo
        try:
            novedades = listar_novedades(solo_no_leidas=True)
            if novedades:
                resumen = "\n".join(f"- [{n['tema']}]: {n['hallazgo'][:150]}" for n in novedades[:5])
                extra = f"Novedades del agente de fondo (menciónalas si pregunta por novedades):\n{resumen}"
                self.contexto_ejecucion = (self.contexto_ejecucion + "\n" + extra) if self.contexto_ejecucion else extra
                marcar_novedades_leidas()
        except Exception as e:
            log_error("listar_novedades", str(e))

        # Si habla alguien que NO es el admin, avisar al modelo por el mismo
        # canal de contexto extra que ya llega a generar_prompt_sistema (variable
        # local: no ensucia self.contexto_ejecucion de los turnos siguientes).
        contexto_para_prompt = self.contexto_ejecucion
        if hablante and hablante != ADMIN_NOMBRE:
            aviso = (f"OJO: ahora está hablando {hablante}, no {ADMIN_NOMBRE}. "
                     f"Dirígete a {hablante} por su nombre.")
            contexto_para_prompt = (contexto_para_prompt + "\n" + aviso) if contexto_para_prompt else aviso

        # Respuestas HABLADAS breves: si la voz está activa, la respuesta se va a
        # escuchar, no a leer — un párrafo largo cansa y rompe la fluidez. Pedimos
        # al modelo ir al grano (salvo que el usuario haya pedido "respuestas largas").
        try:
            from core import skills_voz as _sv
            if _sv.VOZ_ACTIVA and self.voz_concisa:
                brev = ("RESPUESTA HABLADA: sé breve y directo (1-3 frases, ~40 palabras máx). "
                        "Da lo esencial; si hay más que decir, ofrece ampliarlo en vez de soltarlo todo.")
                contexto_para_prompt = (contexto_para_prompt + "\n" + brev) if contexto_para_prompt else brev
        except Exception:
            pass

        sys_prompt = generar_prompt_sistema(contexto_para_prompt)

        # Pre-despacho determinista de skills instaladas
        skill_match = self._detectar_skill_en_input(prompt)
        if skill_match:
            nombre_skill, arg_skill = skill_match
            self.ultima_skill = nombre_skill
            self._status(f"Ejecutando skill: {nombre_skill} {arg_skill}")
            resultado_skill = ejecutar_skill_registrada(nombre_skill, arg_skill)
            if "error" in resultado_skill:
                log_error("pre_despacho_skill", resultado_skill["error"])
                self._say(f"No pude ejecutar '{nombre_skill}' — {resultado_skill['error']}")
            else:
                salida = resultado_skill.get("stdout") or "Skill ejecutada."
                self._say(salida)
                self.contexto_ejecucion = f"Skill '{nombre_skill}' ejecutada: {salida[:200]}"
                self.historial.append({"role": "user", "content": f"[Skill ejecutada]: {nombre_skill} {arg_skill} → {salida[:200]}"})
                self._persistir("assistant", salida)
            return "\n".join(self._buffer)

        # Pre-despacho determinista: preguntas para ENTENDER un tema -> holograma animado
        tema = self._tema_explicativo(prompt)
        if tema:
            self._status(f"Preparando explicación de {tema}...")
            return self.explicar_visual(tema)

        respuesta_llm = consultar_lara(prompt, sys_prompt, self.historial[-6:])
        self.historial.append({"role": "assistant", "content": respuesta_llm})

        # Compactación de historial largo — EN SEGUNDO PLANO: resumir turnos
        # viejos usa un LLM (varios segundos) y antes congelaba justo este
        # turno. El recorte del historial en memoria sí es inmediato; solo el
        # resumen + guardado en conocimiento corre aparte (es archivístico,
        # nada de este turno depende de él).
        if len(self.historial) > MAX_HISTORIAL_MEMORIA:
            turnos_viejos = self.historial[:-20]
            self.historial = self.historial[-20:]

            def _compactar():
                try:
                    resumen = resumir_turnos_antiguos(turnos_viejos)
                    guardar_conocimiento(
                        titulo=f"Resumen de conversación ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
                        contenido=resumen, categoria="memoria_sesion",
                        tags="conversacion, resumen, memoria")
                except Exception as e:
                    log_error("resumir_turnos_antiguos", str(e))

            threading.Thread(target=_compactar, daemon=True,
                             name="lara-compactar-historial").start()

        self._procesar_respuesta_llm(respuesta_llm)

        # ── Modo agente (Mejora b): encadenar pasos ──────────────────
        # Si el modo agente está activo y la acción ejecutada NO fue terminal
        # (obtuvo información / cambió el equipo), volvemos a consultar al modelo
        # para que decida el siguiente paso usando el resultado (que ya quedó en
        # el historial). Se detiene al responder (accion=hablar) o al tope.
        pasos = 0
        while (self.modo_agente and self._ultima_accion in TOOL_ACCIONES_CADENA
               and pasos < MAX_PASOS_AGENTE):
            pasos += 1
            self._status(f"Modo agente: paso {pasos}...")
            sys_prompt_ag = generar_prompt_sistema(self.contexto_ejecucion)
            nudge = ("Usa el ÚLTIMO resultado del historial para avanzar hacia lo que se pidió. "
                     "Si ya tienes lo necesario para responder, usa accion=hablar con la respuesta "
                     "final y completa. Si falta un paso concreto, ejecútalo con la acción adecuada.")
            siguiente = consultar_lara(nudge, sys_prompt_ag, self.historial[-8:])
            self.historial.append({"role": "assistant", "content": siguiente})
            self._procesar_respuesta_llm(siguiente)

        # Persistir la respuesta LIMPIA (no el JSON crudo del router) para el historial
        limpio = "\n".join(self._buffer)
        if limpio.strip():
            self._persistir("assistant", limpio)
            self._ultima_respuesta = limpio  # para el comando "repite"
        return limpio

    # ── despacho de la decisión del router (puerto de main.py) ───
    def _procesar_respuesta_llm(self, respuesta: str):
        self.contexto_ejecucion = ""
        datos = _extraer_json(respuesta)
        if not isinstance(datos, dict):
            self._say(respuesta)
            return

        accion = datos.get("accion", "")
        params = datos.get("params", [])
        mensaje = datos.get("mensaje", "")
        requiere_profundo = datos.get("requiere_razonamiento_profundo", False)
        self._ultima_accion = accion  # para el bucle del modo agente

        # ── Gate de modos / sandbox ──────────────────────────────────────────
        # Un único punto de control cubre TODAS las acciones de riesgo. Según el
        # modo activo (auto/manual/plan/lectura) la acción se ejecuta, se pide
        # confirmación, o se bloquea describiéndola. Lo inocuo (hablar, buscar,
        # leer, consultas) pasa siempre sin fricción.
        decision, motivo = modos.evaluar(accion, params)
        if decision == modos.BLOQUEAR:
            self._say(f"{motivo}\n\n▸ Acción que pediste: {self._describir_accion(accion, params, mensaje)}")
            return
        if decision == modos.CONFIRMAR:
            if not self._confirmar(accion, params, mensaje):
                self._say("De acuerdo, no lo hago.")
                return
            modos.auditar(accion, params)

        # Anti-desactualización: si el modelo responde "no hay datos/no se ha
        # publicado/consulta tú mismo", es su entrenamiento viejo hablando.
        # LARA no se rinde: busca en internet ella misma y responde con datos.
        _EXCUSAS = ("no se ha publicado", "no está disponible", "no esta disponible",
                    "no tengo información", "no tengo informacion", "mi conocimiento",
                    "no dispongo de", "aún no se conoce", "aun no se conoce",
                    "podrás consultarlo", "podras consultarlo", "puedes consultar",
                    "te recomiendo consultar", "fuentes de noticias", "página oficial",
                    "pagina oficial", "no puedo saber", "no tengo acceso a",
                    "no tengo ese dato", "no tengo el dato", "no tengo ese",
                    "no tengo datos", "no cuento con", "no manejo esa informacion",
                    "no manejo esa información")
        if accion in ("hablar", "") and not requiere_profundo:
            pregunta_original = next((h["content"] for h in reversed(self.historial)
                                      if h["role"] == "user" and not h["content"].startswith("[")), mensaje)
            hay_excusa = any(x in mensaje.lower() for x in _EXCUSAS)
            # Si es un seguimiento sobre datos YA buscados (lo maneja más abajo
            # responder_con_datos con self.ultimo_contenido), no re-buscar.
            sigue_datos = self.ultimo_contenido and any(
                w in mensaje.lower() for w in ("diferencia", "cuántos", "cuantos",
                                               "porcentaje", "votos", "resultado"))
            # Búsqueda proactiva: si la pregunta es de ACTUALIDAD (mundial, hoy,
            # noticias, precio...) y el router respondió "hablar" (o sea, de
            # memoria: no hubo acción buscar en este turno), la memoria del
            # modelo no vale -> relanzar como búsqueda web. Al relanzar la
            # acción pasa a ser "buscar", así que no hay recursión posible.
            if hay_excusa or (self._pide_datos_actuales(pregunta_original) and not sigue_datos):
                self._status("Eso requiere datos actuales; buscando en internet...")
                self._procesar_respuesta_llm(json.dumps({
                    "accion": "buscar", "params": [pregunta_original], "mensaje": ""}))
                return

        if accion in ("hablar", ""):
            if self.ultimo_contenido and any(w in mensaje.lower() for w in ["diferencia", "cuántos", "cuantos", "porcentaje", "votos", "resultado"]):
                self._say(responder_con_datos(mensaje, self.ultimo_contenido))
            elif requiere_profundo:
                self._status("LARA pensando a fondo...")
                pregunta_original = self.historial[-2]["content"] if len(self.historial) >= 2 else mensaje
                respuesta_profunda = self._razonar_profundo_decir(pregunta_original)
                self.historial.append({"role": "assistant", "content": respuesta_profunda})
            else:
                self._say(mensaje)
            return

        if accion == "error":
            self._say(f"[error interno]: {mensaje}")
            return

        # Multimodal: ver pantalla
        if accion == "analizar_pantalla":
            self.ver_pantalla(params[0] if params else "")
            return

        # Multimodal: analizar imagen/archivo
        if accion in ("ver", "analizar_imagen", "analizar_archivo"):
            ruta = params[0] if params else ""
            pregunta = params[1] if len(params) > 1 else ""
            if not ruta:
                self._say("Indícame la ruta de la imagen o archivo a analizar.")
                return
            self.analizar_archivo_directo(ruta, pregunta)
            return

        if accion in ("aprender_archivo", "aprender_de_archivo"):
            ruta = params[0] if params else ""
            if not ruta:
                self._say("Indícame qué archivo quieres que aprenda.")
                return
            self.analizar_archivo_directo(ruta, aprender=True)
            return

        # Explicación visual (animación in-window)
        if accion == "explicar":
            tema = params[0] if params else mensaje
            self.explicar_visual(tema)
            return

        # Automatización: teclear / escribir en un programa / clic / teclas
        if accion == "escribir":
            texto = params[0] if params else ""
            self._say(mensaje or "Escribiendo...")
            r = escribir_texto(texto)
            if "error" in r:
                self._say(f"No pude escribir — {r['error']}")
            self.contexto_ejecucion = f"[escribir]: {json.dumps(r, ensure_ascii=False)}"
            return

        if accion in ("escribir_en", "abrir_y_escribir"):
            programa = params[0] if params else ""
            texto = params[1] if len(params) > 1 else mensaje
            self._say(mensaje or f"Escribiendo en {programa}...")
            r = abrir_y_escribir(programa, texto)
            if "error" in r:
                self._say(f"No pude hacerlo — {r['error']}")
            self.contexto_ejecucion = f"[escribir_en]: {json.dumps(r, ensure_ascii=False)}"
            return

        if accion == "clic":
            x = params[0] if params else None
            y = params[1] if len(params) > 1 else None
            try:
                x = int(x); y = int(y)
            except (TypeError, ValueError):
                x = y = None
            r = _clic(x, y)
            self._say(mensaje or "Hecho." if "error" not in r else f"No pude hacer clic — {r['error']}")
            return

        if accion in ("presionar", "teclas"):
            r = presionar(params[0] if params else "")
            self._say(mensaje or ("Listo." if "error" not in r else f"No pude — {r['error']}"))
            return

        # Utilidades de sistema (hora, batería, volumen, captura, portapapeles, etc.)
        if accion == "sistema":
            from core.skills_sistema import ejecutar as _sistema
            operacion = params[0] if params else ""
            valor = params[1] if len(params) > 1 else ""
            r = _sistema(operacion, valor)
            self._say(r.get("mensaje", "Listo."))
            self.contexto_ejecucion = f"[sistema {operacion}]: {r.get('mensaje','')[:300]}"
            return

        # Gestión (compound)
        if accion == "gestion":
            plan = params[0] if params else mensaje
            self._status(f"Gestor: {plan}")
            decision = consultar_gestor_compound(plan, datetime.now().strftime("%Y-%m-%d"))
            accion_g = decision.get("accion", "")
            params_g = decision.get("params", [])
            mensaje_g = decision.get("mensaje", "")
            if accion_g == "error" or accion_g not in ACCIONES_GESTOR:
                self._say(f"El gestor no pudo procesar la solicitud — {mensaje_g or 'acción no reconocida'}")
                return
            try:
                resultado = ACCIONES_GESTOR[accion_g](params_g)
            except Exception as e:
                self._say(f"Error ejecutando la acción del gestor — {e}")
                return
            self._say(mensaje_g or "Listo, gestionado.")
            self.contexto_ejecucion = f"Gestión '{accion_g}' ejecutada: {json.dumps(resultado, ensure_ascii=False)}"
            self.historial.append({"role": "user", "content": f"[Resultado de gestión '{accion_g}']: {json.dumps(resultado, ensure_ascii=False)}"})
            return

        # Búsqueda web
        if accion == "buscar":
            query_original = params[0] if params else mensaje
            query = refinar_query_busqueda(query_original)
            self._status(f"Buscando en internet: {query}")
            resumen, urls = buscar_en_web(query)
            self.ultimas_urls = urls
            self.ultimo_contenido = resumen
            self._say(resumen)
            urls_str = "\n".join(urls[:3]) if urls else "ninguna"
            self.contexto_ejecucion = (f"Búsqueda '{query}' completada.\nDATOS:\n{resumen}\nURLs:\n{urls_str}")
            self.historial.append({"role": "user", "content": f"[Resultado búsqueda '{query}']:\n{resumen}\nURLs: {urls_str}"})
            return

        # Leer página web
        if accion == "web":
            url = params[0] if params else ""
            if not url.startswith("http") and self.ultimas_urls:
                url = self.ultimas_urls[0]
            if not url.startswith("http"):
                self._say("No tengo una URL válida para leer. Intenta buscar primero.")
                return
            self._status(f"Leyendo página: {url}")
            resultado = leer_pagina_web(url)
            if "error" in resultado:
                self._say(f"No pude leer esa página — {resultado['error']}")
                self.contexto_ejecucion = f"Error al leer {url}: {resultado['error']}"
            else:
                contenido = resultado.get("contenido", "")
                self.ultimo_contenido = contenido
                resumen_texto = resumir_pagina_web(url, contenido)
                self._say(resumen_texto)
                self.contexto_ejecucion = f"Página leída: {url}\nDATOS:\n{resumen_texto}"
                self.historial.append({"role": "user", "content": f"[Datos exactos de {url}]:\n{resumen_texto}"})
            return

        # Abrir URL en navegador
        if accion == "url":
            url = params[0] if len(params) > 0 else ""
            navegador = params[1] if len(params) > 1 else "edge"
            if not url.startswith("http"):
                url = "https://" + url
            self._status(f"Abriendo {url} en {navegador}")
            resultado = abrir_url_en_navegador(url, navegador)
            if "error" in resultado:
                self._say(f"No pude abrir esa URL — {resultado['error']}")
            else:
                self._say(mensaje or f"Abriendo {url} en {navegador}")
            return

        # Proponer skill (no bloqueante: si ya existe, pide repetir con confirmación)
        if accion == "proponer_skill":
            nombre = params[0] if len(params) > 0 else "skill_sin_nombre"
            descripcion = params[1] if len(params) > 1 else mensaje
            self._status(f"Generando propuesta: {nombre}")
            codigo = generar_codigo_grande(nombre, descripcion)
            resultado = proponer_skill(nombre, codigo)
            if resultado.get("requiere_confirmacion"):
                self._say(f"{resultado['mensaje']} (Repite el pedido añadiendo 'sobrescribe' para reemplazarla.)")
                return
            if "error" in resultado:
                log_error("proponer_skill", resultado["error"])
                self._say(f"No pude generar la propuesta — {resultado['error']}")
                return
            self._say(f"Listo, dejé la propuesta en {resultado['ruta']}. Di 'acepta la propuesta {nombre}' para instalarla.")
            _notificar("LARA — Nueva propuesta de skill", f"{nombre}: revisa {resultado['ruta']}")
            self.contexto_ejecucion = f"Propuesta de skill '{nombre}' generada en {resultado['ruta']}"
            self.historial.append({"role": "user", "content": f"[Propuesta de skill generada]: nombre='{nombre}', archivo='{resultado['ruta']}'"})
            return

        # Ejecutar skill registrada
        if accion == "ejecutar_skill":
            nombre = params[0] if len(params) > 0 else ""
            argumento = params[1] if len(params) > 1 else ""
            if not nombre:
                self._say("Debes indicar el nombre de la skill a ejecutar.")
                return
            # Red de seguridad: si el router no pasó argumento pero el usuario
            # dijo "desactiva...", inferirlo (sin arg la skill activa por defecto).
            if not argumento:
                ult = next((h["content"].lower() for h in reversed(self.historial) if h["role"] == "user"), "")
                reg_info = cargar_registro().get(nombre, {})
                if any(p in ult for p in ("desactiv", "apaga", "quita")) and "desactivar" in reg_info.get("argumentos", []):
                    argumento = "desactivar"
                elif "activar" in reg_info.get("argumentos", []):
                    argumento = "activar"
            self.ultima_skill = nombre
            self._status(f"Ejecutando skill: {nombre} {argumento}")
            if mensaje:
                self._say(mensaje)
            resultado = ejecutar_skill_registrada(nombre, argumento)
            if "error" in resultado:
                log_error("ejecutar_skill", resultado["error"])
                self._say(f"Error al ejecutar '{nombre}' — {resultado['error']}")
            else:
                salida = resultado.get("stdout") or "Skill ejecutada sin salida visible."
                self._say(salida)
                self.contexto_ejecucion = f"Skill '{nombre}' ejecutada: {json.dumps(resultado, ensure_ascii=False)}"
                self.historial.append({"role": "user", "content": f"[Skill ejecutada]: nombre='{nombre}', argumento='{argumento}', resultado='{salida[:200]}'"})
            return

        # Aceptar propuesta
        if accion == "aceptar_propuesta":
            nombre = params[0] if len(params) > 0 else ""
            descripcion = params[1] if len(params) > 1 else ""
            args_str = params[2] if len(params) > 2 else ""
            argumentos = [a.strip() for a in args_str.split(",") if a.strip()] if args_str else None
            if not nombre:
                self._say("Debes indicar el nombre de la propuesta a aceptar.")
                return
            self._status(f"Aceptando propuesta: {nombre}")
            resultado = aceptar_propuesta(nombre, descripcion, argumentos)
            if "error" in resultado:
                log_error("aceptar_propuesta", resultado["error"])
                self._say(f"No pude instalar la skill — {resultado['error']}")
            else:
                self._say(resultado["mensaje"])
                self.historial.append({"role": "user", "content": f"[Skill instalada]: '{nombre}' en {resultado['ruta_final']}. Args: {resultado['argumentos']}"})
            return

        # Listar propuestas
        if accion == "listar_propuestas":
            resultado = listar_propuestas_pendientes()
            self._say(resultado["mensaje"])
            self.contexto_ejecucion = json.dumps(resultado, ensure_ascii=False)
            return

        # Guardar conocimiento
        if accion == "guardar_conocimiento":
            titulo = params[0] if len(params) > 0 else "Nota sin título"
            contenido = params[1] if len(params) > 1 else mensaje
            categoria = params[2] if len(params) > 2 else "general"
            tags = params[3] if len(params) > 3 else ""
            resultado = guardar_conocimiento(titulo, contenido, categoria, tags)
            if "error" in resultado:
                log_error("guardar_conocimiento", resultado["error"])
                self._say(f"No pude guardar el conocimiento — {resultado['error']}")
            else:
                self._say(mensaje or "Guardado en la base de conocimiento.")
                self.historial.append({"role": "user", "content": f"[Conocimiento guardado]: titulo='{titulo}', categoria='{categoria}'"})
            return

        # Recordatorio rápido
        if accion == "recordatorio_rapido":
            texto = params[0] if params else mensaje
            minutos = params[1] if len(params) > 1 else "10"
            resultado = recordatorio_rapido(texto, minutos)
            if "error" in resultado:
                self._say(f"No pude programar el recordatorio — {resultado['error']}")
            else:
                self._say(mensaje or f'Listo, te recuerdo "{texto}" en {minutos} minutos.')
                self.historial.append({"role": "user", "content": f"[Recordatorio programado]: '{texto}' en {minutos} minutos"})
            return

        # Descargas
        if accion == "descargar":
            url = params[0] if params else ""
            nombre = params[1] if len(params) > 1 else ""
            self._status(f"Descargando: {url}")
            resultado = descargar_archivo(url, nombre)
            if "error" in resultado:
                self._say(f"No pude descargar el archivo — {resultado['error']}")
            else:
                self._say(f"Archivo descargado en {resultado['ruta']}")
            self.contexto_ejecucion = f"Descarga de archivo: {json.dumps(resultado, ensure_ascii=False)}"
            return

        if accion == "descargar_video":
            url = params[0] if params else ""
            self._status(f"Descargando video: {url}")
            resultado = descargar_video(url)
            if "error" in resultado:
                self._say(f"No pude descargar el video — {resultado['error']}")
            else:
                self._say(f"Video '{resultado.get('titulo','')}' descargado en {resultado['carpeta']}")
            self.contexto_ejecucion = f"Descarga de video: {json.dumps(resultado, ensure_ascii=False)}"
            return

        # Acciones del sistema operativo (bloque final: cmd / código / archivos /
        # abrir / listar / leer / crear). Extraído a su propio método para acotar
        # el tamaño del despachador y dejar un punto único donde tocar el SO.
        self._ejecutar_accion_os(accion, params, mensaje)

    def _ejecutar_accion_os(self, accion: str, params: list, mensaje: str) -> None:
        """Ejecuta las acciones que tocan el sistema operativo y presenta el
        resultado. Se invoca al final de _procesar_respuesta_llm cuando la acción
        no fue ninguna de las especializadas de arriba."""
        self._status(f"Ejecutando: {accion}")
        resultado = {}
        try:
            if accion == "cmd":
                resultado = ejecutar_cmd(params[0])
            elif accion == "cmd_admin":
                resultado = ejecutar_cmd_admin(params[0])
            elif accion == "ejecutar_codigo":
                resultado = ejecutar_codigo_python(params[0])
            elif accion == "ejecutar_codigo_visible":
                resultado = ejecutar_codigo_visible(params[0])
            elif accion == "copiar_archivo":
                resultado = copiar_archivo(params[0], params[1] if len(params) > 1 else "workspace")
            elif accion == "mover_archivo":
                resultado = mover_archivo(params[0], params[1] if len(params) > 1 else "workspace")
            elif accion == "ajustar_brillo":
                resultado = ajustar_brillo(params[0] if params else "50")
                if "error" in resultado:
                    registrar_limitacion("ajustar_brillo", f"Falla en este equipo: {resultado['error']}")
            elif accion == "abrir":
                resultado = abrir_programa_inteligente(params[0])
            elif accion == "listar":
                resultado = listar_directorio(params[0] if params else "")
            elif accion == "leer":
                resultado = leer_archivo(params[0])
            elif accion == "crear":
                resultado = crear_archivo(params[0], params[1] if len(params) > 1 else "")
            else:
                self._say(mensaje)
                return
        except Exception as e:
            log_error(accion, str(e))
            resultado = {"error": str(e)}

        if "error" in resultado:
            self._say(f"No pude completarlo — {resultado['error']}")
        elif accion == "abrir":
            # Decir qué programa se abrió realmente (puede diferir del texto pedido)
            nombre_real = resultado.get("nombre", "")
            extra = ""
            if resultado.get("alternativas"):
                extra = f" (si no era ese, también tengo: {', '.join(resultado['alternativas'])})"
            self._say(f"Abriendo {nombre_real}.{extra}")
        elif mensaje:
            self._say(mensaje)

        # Registrar "deshacer" para acciones reversibles (por ahora, mover_archivo).
        if accion == "mover_archivo" and "error" not in resultado:
            org, dst = resultado.get("origen"), resultado.get("destino")
            if org and dst:
                self._registrar_undo(
                    f"mover «{dst}» de vuelta a «{org}»",
                    lambda o=dst, d=org: mover_archivo(o, d))

        # Para acciones que devuelven contenido visible (listar/leer), mostrarlo
        if accion == "listar" and "contenido" in resultado:
            items = "\n".join(f"  - {i['nombre']} ({i['tipo']})" for i in resultado["contenido"])
            self._say(f"{resultado.get('ruta','')}\n{items}", hablar=False)
        elif accion == "leer" and "contenido" in resultado:
            self._say(resultado["contenido"], hablar=False)

        self.contexto_ejecucion = f"Última acción ({accion}) resultado: {json.dumps(resultado, ensure_ascii=False)}"
        self.historial.append({"role": "user", "content": f"Resultado interno (invisible para {ADMIN_NOMBRE}): {json.dumps(resultado, ensure_ascii=False)}"})
