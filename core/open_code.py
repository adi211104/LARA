"""
open_code.py — Modo "Open Code" de LARA: programación autónoma.

Flujo por tarea (arregla que antes improvisara a ciegas):
  1. PLANIFICA: antes de tocar nada recibe el árbol REAL de la carpeta y escribe
     un plan breve (qué mirar, qué cambiar, cómo verificar). El plan y el árbol
     acompañan todo el ciclo, así no pierde el norte ni inventa rutas.
  2. EJECUTA en ciclos: cada turno responde UN JSON con una acción (listar, leer,
     escribir, cmd, terminar); se ejecuta y se le devuelve el resultado real.

Robustez del JSON (arregla el error 400 'json_validate_failed' de Groq):
  - Si el modelo pega varios objetos JSON o añade texto, se rescata el PRIMER
    objeto válido con raw_decode — incluso desde el 'failed_generation' que
    Groq devuelve dentro del propio error 400.
  - Si aun así falla, se reintenta con un mensaje correctivo (hasta 3 veces)
    en vez de abortar la tarea entera.
  - Si repite una acción que ya hizo, se le avisa para que cambie de estrategia.

Modos de autonomía (los elige el usuario en la pestaña Open Code):
  - "auto"      -> trabaja sola (comportamiento clásico).
  - "permisos"  -> pide confirmación antes de escribir archivos o ejecutar comandos.
  - "ediciones" -> escribe sin preguntar; confirma solo los comandos.
  - "plan"      -> solo lista/lee y termina entregando un plan; no modifica nada.

Niveles de esfuerzo (get_nivel_esfuerzo de llm_engine): el nivel elegido por el
usuario ajusta los max_tokens del plan y de cada decisión; en "ultracode",
además, al primer "terminar" se inyecta UN ciclo extra de verificación
(releer lo tocado, probar) antes de aceptar el cierre — una sola vez por
tarea para no entrar en bucle.

Seguridad: TODAS las operaciones de archivo están confinadas a la carpeta de
trabajo elegida (no puede salir de ahí). Los comandos se ejecutan dentro de esa
carpeta con timeout. El historial se guarda como sesión del espacio "code".
"""

import json
import re
import subprocess
from pathlib import Path

from core.llm_engine import client, MODELO_ROUTER, get_nivel_esfuerzo
from core import sesiones

MAX_ITERACIONES = 20
MAX_SALIDA = 2500
_IGNORADOS = (".git", "__pycache__", ".venv", "node_modules")

# Presupuesto de tokens por NIVEL DE ESFUERZO (contrato con llm_engine):
# el nivel elegido por el usuario en la GUI también gobierna cuánto puede
# "pensar" el agente de código en cada decisión y en el plan inicial.
_TOKENS_DECIDIR = {"bajo": 1500, "medio": 2000, "alto": 2500,
                   "extra": 3200, "max": 4000, "ultracode": 4000}
_TOKENS_PLAN = {"bajo": 300, "medio": 400, "alto": 500,
                "extra": 650, "max": 800, "ultracode": 800}


def _sistema(carpeta: Path, modo: str) -> str:
    base = f"""Eres LARA en MODO OPEN CODE: una programadora autónoma trabajando dentro de la carpeta:
{carpeta}

Trabajas en CICLOS. En cada turno respondes UN ÚNICO objeto JSON (uno solo, nunca dos, sin texto extra ni ```). Yo ejecuto la acción y te devuelvo el resultado real; entonces decides el siguiente paso.

Acciones:
- {{"accion":"listar","ruta":"","pensamiento":"qué busco"}}  -> ver el árbol de archivos (ruta relativa, "" = raíz)
- {{"accion":"leer","archivo":"src/app.py","pensamiento":"..."}}  -> leer un archivo
- {{"accion":"escribir","archivo":"src/app.py","contenido":"CONTENIDO COMPLETO","pensamiento":"..."}}  -> crear/sobrescribir un archivo COMPLETO
- {{"accion":"cmd","comando":"python -m pytest -q","pensamiento":"probar"}}  -> ejecutar un comando en la carpeta (timeout 60s)
- {{"accion":"terminar","resumen":"qué hice y resultado","pensamiento":"..."}}  -> terminar la tarea

Reglas:
- SIGUE EL PLAN acordado; en "pensamiento" indica qué paso del plan estás ejecutando.
- Las rutas del árbol son EXACTAS y relativas a la carpeta de trabajo: un archivo listado como "app.py" está en la RAÍZ; "src\\app.py" está dentro de "src". NUNCA inventes ni "adivines" rutas.
- NUNCA repitas una acción cuyo resultado ya conoces. Si algo falla dos veces, cambia de estrategia.
- Nada de rutas absolutas ni '..'.
- En "escribir" manda el contenido ÍNTEGRO del archivo (no diffs).
- Verifica tu trabajo con "cmd" cuando tenga sentido (ejecutar el script, tests, etc.).
- Si un comando falla, lee el error y corrígelo en el siguiente ciclo."""
    if modo == "plan":
        base += """

MODO PLANIFICACIÓN ACTIVO: solo puedes usar "listar", "leer" y "terminar". NO escribas archivos ni ejecutes comandos: explora lo necesario y usa "terminar" entregando en "resumen" un plan detallado paso a paso para que el usuario lo apruebe."""
    return base


def _extraer_json(texto: str) -> dict:
    """Devuelve el PRIMER objeto JSON válido dentro de 'texto'. Tolera fences de
    markdown, texto alrededor y varios objetos pegados (el bug del error 400)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", (texto or "").strip())
    i = t.find("{")
    if i < 0:
        raise ValueError("la respuesta no contiene JSON")
    obj, _fin = json.JSONDecoder().raw_decode(t[i:])
    if not isinstance(obj, dict):
        raise ValueError("el JSON no es un objeto")
    return obj


def _rescatar_de_error(e: Exception):
    """Groq devuelve 400 'json_validate_failed' con la generación fallida dentro
    del error. Si esa generación contiene un primer JSON usable, lo aprovechamos
    en vez de perder el ciclo."""
    try:
        body = getattr(e, "body", None) or {}
        fg = (body.get("error") or {}).get("failed_generation", "")
        if fg:
            return _extraer_json(fg)
    except Exception:
        pass
    return None


class AgenteCodigo:
    def __init__(self, carpeta: str, on_log=None, session_id: str | None = None,
                 modo: str = "auto", on_confirmar=None):
        self.carpeta = Path(carpeta).resolve()
        self.on_log = on_log or (lambda tipo, texto: None)
        self.espacio = "code"
        self.session_id = session_id or sesiones.crear_sesion("code", titulo=f"Code: {self.carpeta.name}")
        self.modo = modo                     # auto | permisos | ediciones | plan
        self.on_confirmar = on_confirmar or (lambda accion, detalle: True)
        self._parar = False

    # ── seguridad de rutas ──────────────────────────────────────
    def _resolver(self, rel: str) -> Path:
        destino = (self.carpeta / rel).resolve()
        if self.carpeta not in destino.parents and destino != self.carpeta:
            raise ValueError(f"Ruta fuera de la carpeta de trabajo: {rel}")
        return destino

    # ── herramientas ────────────────────────────────────────────
    def _listar(self, ruta: str) -> str:
        base = self._resolver(ruta or "")
        if not base.exists():
            return f"(no existe: {ruta})"
        lineas = []
        for p in sorted(base.rglob("*"))[:200]:
            if any(part in _IGNORADOS for part in p.parts):
                continue
            rel = p.relative_to(self.carpeta)
            lineas.append(f"{'[D] ' if p.is_dir() else '    '}{rel}")
        return "\n".join(lineas) or "(carpeta vacía)"

    def _leer(self, archivo: str) -> str:
        p = self._resolver(archivo)
        if not p.exists() or p.is_dir():
            # Ayuda activa: buscar el archivo por nombre para corregir la ruta
            pista = ""
            try:
                nombre = Path(archivo).name
                otros = [str(x.relative_to(self.carpeta)) for x in self.carpeta.rglob(nombre)
                         if not any(part in _IGNORADOS for part in x.parts)][:5]
                if otros:
                    pista = f" Sí existe con ese nombre en: {', '.join(otros)}. Usa esa ruta EXACTA."
            except Exception:
                pass
            return (f"(no existe '{archivo}'.{pista}" if pista else
                    f"(no existe '{archivo}'. Revisa el árbol con 'listar' y usa la ruta exacta tal como aparece ahí.") + ")"
        txt = p.read_text(encoding="utf-8", errors="ignore")
        return txt[:6000] + ("\n...[truncado]" if len(txt) > 6000 else "")

    def _escribir(self, archivo: str, contenido: str) -> str:
        p = self._resolver(archivo)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido, encoding="utf-8")
        return f"Escrito {archivo} ({len(contenido)} caracteres)."

    def _cmd(self, comando: str) -> str:
        try:
            r = subprocess.run(comando, shell=True, capture_output=True, text=True,
                               timeout=60, cwd=str(self.carpeta), stdin=subprocess.DEVNULL)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            out = out.strip() or "(sin salida)"
            return out[:MAX_SALIDA] + ("\n...[truncado]" if len(out) > MAX_SALIDA else "")
        except subprocess.TimeoutExpired:
            return "(el comando excedió 60s y fue cancelado)"
        except Exception as e:
            return f"(error ejecutando: {e})"

    # ── LLM: plan previo y decisión robusta ─────────────────────
    def _registrar_uso(self, resp):
        try:
            from core.uso_tokens import registrar_groq
            registrar_groq(resp, MODELO_ROUTER)
        except Exception:
            pass

    def _planificar(self, instruccion: str, arbol: str) -> str:
        """Piensa la tarea ANTES de actuar, con el árbol real a la vista."""
        try:
            resp = client.chat.completions.create(
                model=MODELO_ROUTER, temperature=0.3,
                max_tokens=_TOKENS_PLAN.get(get_nivel_esfuerzo(), 500),
                messages=[
                    {"role": "system", "content":
                     "Eres LARA, programadora. Antes de tocar código escribe un plan BREVE y "
                     "concreto (máximo 6 pasos numerados): qué archivos mirar, crear o cambiar, "
                     "y cómo verificarás el resultado. Usa SOLO rutas que aparezcan en el árbol "
                     "(o márcalas como nuevas). Sin código todavía."},
                    {"role": "user", "content":
                     f"Carpeta de trabajo: {self.carpeta}\n\nÁrbol de archivos:\n{arbol}\n\nTarea: {instruccion}"},
                ])
            self._registrar_uso(resp)
            return (resp.choices[0].message.content or "").strip()
        except Exception:
            return ""  # sin plan no se bloquea la tarea; el ciclo sigue igual

    def _decidir(self, messages: list) -> dict:
        """Pide la siguiente acción. Si el JSON viene mal, rescata o reintenta
        con un mensaje correctivo en vez de abortar la tarea."""
        ultimo_error = None
        for intento in range(3):
            try:
                kwargs = dict(model=MODELO_ROUTER, messages=messages, temperature=0.2,
                              max_tokens=_TOKENS_DECIDIR.get(get_nivel_esfuerzo(), 2500))
                if intento == 0:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**kwargs)
                self._registrar_uso(resp)
                return _extraer_json(resp.choices[0].message.content)
            except Exception as e:
                rescate = _rescatar_de_error(e)
                if rescate is not None:
                    return rescate
                ultimo_error = e
                messages.append({"role": "user", "content":
                                 "Tu última respuesta no fue UN ÚNICO objeto JSON válido. "
                                 "Responde de nuevo: SOLO un objeto JSON con la siguiente acción."})
        raise RuntimeError(f"No pude decidir el siguiente paso tras 3 intentos: {ultimo_error}")

    # ── bucle del agente ────────────────────────────────────────
    def detener(self):
        self._parar = True

    def ejecutar_tarea(self, instruccion: str):
        self._parar = False
        sesiones.guardar_turno(self.espacio, self.session_id, "user", instruccion)
        self.on_log("usuario", instruccion)

        # 1) PLAN: pensar la tarea antes de actuar
        arbol = self._listar("")
        plan = self._planificar(instruccion, arbol)
        if plan:
            self.on_log("plan", plan)
            sesiones.guardar_turno(self.espacio, self.session_id, "assistant", f"[plan] {plan}")

        messages = [
            {"role": "system", "content": _sistema(self.carpeta, self.modo)},
            {"role": "user", "content":
                f"Tarea: {instruccion}\n\nÁrbol actual de la carpeta (rutas exactas):\n{arbol}\n\n"
                + (f"Plan acordado:\n{plan}\n\n" if plan else "")
                + "Ejecuta la tarea paso a paso. Responde con la primera acción."},
        ]

        firmas = []  # acciones ya ejecutadas, para avisar si repite
        # ULTRACODE: al primer "terminar" se inyecta UN ciclo extra de verificación.
        # El flag evita el bucle infinito de "verifica -> terminar -> verifica -> ...".
        verificacion_pedida = False
        for _ in range(MAX_ITERACIONES):
            if self._parar:
                self.on_log("sistema", "Detenido por el usuario.")
                return
            try:
                decision = self._decidir(messages)
            except Exception as e:
                self.on_log("error", str(e))
                return

            accion = decision.get("accion", "")
            pensamiento = decision.get("pensamiento", "")
            if pensamiento:
                self.on_log("pensamiento", pensamiento)

            if accion == "terminar":
                # Nivel "ultracode": antes de aceptar el cierre, exigir UNA pasada
                # de verificación (releer lo tocado y probar). Solo una vez por
                # tarea, y nunca en modo planificación (ahí no hay nada que probar).
                if (get_nivel_esfuerzo() == "ultracode" and not verificacion_pedida
                        and self.modo != "plan"):
                    verificacion_pedida = True
                    self.on_log("sistema", "Nivel ultracode: pidiendo verificación extra antes de terminar.")
                    messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
                    messages.append({"role": "user", "content":
                                     "Antes de terminar: verifica tu trabajo. Relee los archivos que "
                                     "tocaste y ejecuta una prueba si aplica. Si encuentras un problema "
                                     "corrígelo; si todo está bien, vuelve a 'terminar'."})
                    continue
                resumen = decision.get("resumen", "Tarea finalizada.")
                self.on_log("terminado", resumen)
                sesiones.guardar_turno(self.espacio, self.session_id, "assistant", f"[terminado] {resumen}")
                return

            resultado = self._ejecutar_accion(accion, decision)

            # Anti-bucle: avisar si repite una acción ya ejecutada
            firma = (accion, decision.get("archivo") or decision.get("comando") or decision.get("ruta", ""))
            if firma in firmas:
                resultado += ("\n\n[AVISO] Ya habías ejecutado EXACTAMENTE esta acción y conoces su "
                              "resultado. No la repitas: usa lo que ya sabes o cambia de estrategia.")
            firmas.append(firma)

            sesiones.guardar_turno(self.espacio, self.session_id, "assistant",
                                   f"[{accion}] {decision.get('archivo', decision.get('comando', ''))}")
            # Realimentar el resultado al modelo para el siguiente ciclo
            messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Resultado de '{accion}':\n{resultado}"})

        self.on_log("sistema", f"Alcancé el máximo de {MAX_ITERACIONES} ciclos. Pauso aquí.")

    def _ejecutar_accion(self, accion: str, decision: dict) -> str:
        # Modos: planificación no toca nada; permisos/ediciones piden confirmación
        if accion in ("escribir", "cmd"):
            if self.modo == "plan":
                self.on_log("sistema", f"(modo planificación: '{accion}' bloqueado)")
                return ("(MODO PLANIFICACIÓN: no puedes escribir ni ejecutar. Incluye este paso en el "
                        "plan y usa 'terminar' entregando el plan completo en 'resumen'.)")
            detalle = decision.get("archivo", "") if accion == "escribir" else decision.get("comando", "")
            pedir = self.modo == "permisos" or (self.modo == "ediciones" and accion == "cmd")
            if pedir:
                self.on_log("confirmar", f"Esperando tu permiso para {accion}: {detalle}")
                if not self.on_confirmar(accion, detalle):
                    self.on_log("sistema", "Acción rechazada por el usuario.")
                    return ("(el usuario NO autorizó esta acción; propón una alternativa "
                            "o termina explicando qué queda pendiente)")

        if accion == "listar":
            try:
                resultado = self._listar(decision.get("ruta", ""))
            except Exception as e:
                resultado = f"(error: {e})"
            self.on_log("listar", resultado)
        elif accion == "leer":
            archivo = decision.get("archivo", "")
            try:
                resultado = self._leer(archivo)
            except Exception as e:
                resultado = f"(error: {e})"
            self.on_log("leer", f"{archivo}:\n{resultado[:800]}")
        elif accion == "escribir":
            archivo = decision.get("archivo", "")
            try:
                resultado = self._escribir(archivo, decision.get("contenido", ""))
                self.on_log("escribir", resultado)
            except Exception as e:
                resultado = f"(error: {e})"
                self.on_log("error", resultado)
        elif accion == "cmd":
            comando = decision.get("comando", "")
            self.on_log("cmd", f"$ {comando}")
            resultado = self._cmd(comando)
            self.on_log("salida", resultado)
        else:
            resultado = f"Acción desconocida: {accion}. Usa solo: listar, leer, escribir, cmd, terminar."
            self.on_log("error", resultado)
        return resultado
