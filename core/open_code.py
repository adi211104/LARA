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
  - "auto"      -> escribe sola; durante la Fase 1 los comandos se confirman
                   igual (salvo LARA_ENABLE_EXPERIMENTAL_TOOLS=1).
  El modo global de LARA 'plan'/'lectura' bloquea escribir y cmd en cualquier
  modo de la pestaña. Toda escritura/comando queda en logs/auditoria.log.
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
import os
import re
import subprocess
import threading
import sys
import time
from pathlib import Path

from core.llm_engine import client, MODELO_ROUTER, get_nivel_esfuerzo, _cadena_routers
from core import modos, sesiones

MAX_ITERACIONES = 20
MAX_SALIDA = 2500
_IGNORADOS = (".git", "__pycache__", ".venv", "node_modules", '.tools', 'venv')

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
- {{"accion":"cmd","comando":"python app.py","entrada":"5\\n3\\n\\n","pensamiento":"probar entrada interactiva"}} -> proporciona stdin de prueba, sin abrir ventanas
- {{"accion":"terminar","resumen":"qué hice y resultado","pensamiento":"..."}}  -> terminar la tarea

Reglas:
- SIGUE EL PLAN acordado; en "pensamiento" indica qué paso del plan estás ejecutando.
- Las rutas del árbol son EXACTAS y relativas a la carpeta de trabajo: un archivo listado como "app.py" está en la RAÍZ; "src\\app.py" está dentro de "src". NUNCA inventes ni "adivines" rutas.
- NUNCA repitas una acción cuyo resultado ya conoces. Si algo falla dos veces, cambia de estrategia.
- Nada de rutas absolutas ni '..'.
- En "escribir" manda el contenido ÍNTEGRO del archivo (no diffs).
- Verifica tu trabajo con "cmd" cuando tenga sentido (ejecutar el script, tests, etc.).
- Si un comando falla, lee el error y corrígelo en el siguiente ciclo."""
    base += """
- La consola de pruebas no es interactiva: input() recibe EOF salvo que proporciones 'entrada'. Para probar programas interactivos, envía todas las respuestas separadas por \\n, incluyendo el ENTER final. No reescribas el programa solo por un EOF de la herramienta.
- En Windows, cmd usa cmd.exe: no uses heredocs Bash (python - <<PY), ni comandos de Linux. Escribe un archivo de prueba con escribir y ejecútalo, o usa python -c con comillas compatibles.
- Mantén el objetivo original: un rectángulo de caracteres en terminal no es una imagen. No añadas Pillow ni dependencias si la tarea no las necesita.
- Usa los artefactos y el historial de la sesión para resolver referencias del usuario. Nunca confundas «abre el programa» con iniciar LARA. No inspecciones .env, .tools ni archivos de credenciales.
- 'pensamiento' contiene solo una actualización breve de progreso, no razonamientos internos ni código. En 'terminar', da retroalimentación útil sin pegar código: archivos creados/cambiados, cómo usarlos, pruebas realizadas y limitaciones pendientes.
- Para mantener visible una consola iniciada con doble clic, usa una pausa final y proporciona un lanzador .bat con python y pause si corresponde. Una prueba capturada no demuestra una ventana visible.
- El resultado incluye código de salida: 0 significa proceso terminado, no que una ventana siga abierta. Informa qué verificaste y qué falta comprobar manualmente.
"""
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
        # Sin UI que pregunte, lo que requiera permiso se deniega (A-01).
        self.on_confirmar = on_confirmar or (lambda accion, detalle: False)
        self._parar = False
        self._cancelar = threading.Event()
        self._pendiente = None
        self._cuota_hasta = 0.0
        self._google_cuota_hasta = 0.0
        self._cambios = 0
        self._ultima_prueba = None
        datos_sesion = sesiones.cargar_sesion('code', self.session_id)
        self._artefactos = [p for p in datos_sesion.get('artefactos', []) if isinstance(p, str)]
        # Migrar los registros de escritura de sesiones anteriores, sin ejecutar nada.
        if not self._artefactos:
            for turno in datos_sesion.get('turnos', []):
                m = re.match(r'^\[escribir\]\s+(.+)$', turno.get('content', ''))
                if m and m[1] not in self._artefactos:
                    self._artefactos.append(m[1])
        pendiente = datos_sesion.get('pendiente')
        if isinstance(pendiente, list) and all(isinstance(m, dict) and isinstance(m.get('content'), str)
                                              and m.get('role') in ('system', 'user', 'assistant') for m in pendiente):
            self._pendiente = pendiente
        elif 'pendiente' not in datos_sesion:
            turnos = datos_sesion.get('turnos', [])
            objetivos = [t['content'] for t in turnos if t.get('role') == 'user'
                         and t.get('content', '').strip().lower() not in ('continua', 'continúa', 'continuar', 'reanudar')]
            if objetivos and not any(t.get('content', '').startswith('[terminado]') for t in turnos[-1:]):
                self._pendiente = [
                    {'role': 'system', 'content': _sistema(self.carpeta, self.modo)},
                    {'role': 'user', 'content': 'Recuperación de sesión antigua. Objetivo original: ' + objetivos[0] +
                     '\nÚltima petición: ' + objetivos[-1] +
                     '\nEl historial antiguo no contiene todas las salidas de herramientas. Primero inspecciona archivos existentes; '
                     'no repitas escrituras o comandos basándote solo en el historial. No cambies el objetivo.'}]
        self._guardar_estado()

    def _guardar_estado(self):
        sesiones.guardar_estado_codigo(self.session_id, str(self.carpeta), self._pendiente, self._artefactos)

    def _contexto_sesion(self):
        datos = sesiones.cargar_sesion('code', self.session_id)
        turnos = datos.get('turnos', [])
        objetivo = next((t['content'] for t in turnos if t.get('role') == 'user'
                         and t.get('content', '').strip().lower() not in ('continua', 'continúa', 'continuar')), '')
        reciente = '\n'.join(t['role'] + ': ' + t.get('content', '')[:800] for t in turnos[-8:])
        return ('Objetivo original de esta sesión: ' + objetivo[:2500] +
                '\nArchivos creados o editados en esta sesión: ' + ', '.join(self._artefactos) +
                '\nConversación reciente:\n' + reciente +
                '\nLas referencias «el programa», «eso», «el que hiciste» aluden a estos artefactos, no a LARA. '
                'No cambies de proyecto ni añadas funciones no solicitadas.')

    def _es_lara(self, ruta):
        from config import PROYECTO_DIR
        ruta = Path(ruta).resolve()
        return ruta in {PROYECTO_DIR / n for n in ('gui.py', 'main.py', 'INICIAR_LARA.bat', 'INSTALAR_LARA.bat')}

    def _abrir_programa_creado(self):
        candidatos = []
        for nombre in self._artefactos:
            p = self._resolver(nombre)
            if p.is_file() and p.suffix.lower() in ('.py', '.bat', '.cmd', '.exe') and not self._es_lara(p):
                candidatos.append(p)
        # Si existe un lanzador y su script homónimo, se consideran el mismo programa.
        grupos = {}
        for p in candidatos:
            grupos.setdefault(p.stem.lower(), []).append(p)
        if len(grupos) != 1:
            nombres = ', '.join(str(p.relative_to(self.carpeta)) for p in candidatos)
            return ('Hay varios programas creados en esta sesión. Indica cuál abrir: ' + nombres
                    if candidatos else 'No encuentro un ejecutable creado en esta sesión. Revisemos el archivo antes de abrirlo.')
        archivos = next(iter(grupos.values()))
        p = next((p for p in archivos if p.suffix.lower() == '.py'), archivos[0])
        if self.modo == 'plan' or modos.get_modo() in ('plan', 'lectura'):
            return 'El modo actual no permite abrir programas.'
        if self.modo in ('permisos', 'ediciones') and not self.on_confirmar('cmd', 'Abrir ' + str(p)):
            return 'Apertura no autorizada.'
        if os.name != 'nt':
            return 'La apertura en una consola visible de este lanzador está implementada para Windows.'
        modos.auditar('open_code.abrir_artefacto', [str(p)], 'autorizado')
        if p.suffix.lower() == '.py':
            argv = [sys.executable, str(p)]
        elif p.suffix.lower() in ('.bat', '.cmd'):
            argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/k', str(p)]
        else:
            argv = [str(p)]
        subprocess.Popen(argv, cwd=str(p.parent), creationflags=subprocess.CREATE_NEW_CONSOLE)
        return f'He solicitado abrir {p.name} en una consola independiente. Introduce allí las medidas. El lanzamiento no verifica por sí solo el funcionamiento del programa.'

    # ── seguridad de rutas ──────────────────────────────────────
    def _resolver(self, rel: str) -> Path:
        destino = (self.carpeta / rel).resolve()
        if self.carpeta not in destino.parents and destino != self.carpeta:
            raise ValueError(f"Ruta fuera de la carpeta de trabajo: {rel}")
        partes = destino.relative_to(self.carpeta).parts
        if any(p.lower() == '.tools' or p.lower() == '.env' or p.lower().endswith('.key') for p in partes):
            raise ValueError('Archivo de credenciales o configuración privada excluido de Open Code.')
        return destino

    # ── herramientas ────────────────────────────────────────────
    def _listar(self, ruta: str) -> str:
        base = self._resolver(ruta or "")
        if not base.exists():
            return f"(no existe: {ruta})"
        lineas = []
        for raiz, directorios, archivos in os.walk(base, followlinks=False):
            directorios[:] = sorted(d for d in directorios if d not in _IGNORADOS
                                    and not (Path(raiz) / d).is_symlink())
            for nombre in directorios + sorted(n for n in archivos if not n.startswith('.') and not n.endswith('.key')):
                p = Path(raiz) / nombre
                lineas.append(f"{'[D] ' if nombre in directorios else '    '}{p.relative_to(self.carpeta)}")
                if len(lineas) >= 200:
                    return '\n'.join(lineas) + '\n...[árbol limitado a 200 entradas]'
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
        self._cambios += 1
        if archivo not in self._artefactos:
            self._artefactos.append(archivo)
        self._guardar_estado()
        return f"Escrito {archivo} ({len(contenido)} caracteres)."

    def _cmd(self, comando: str, entrada=None) -> str:
        try:
            from config import PROYECTO_DIR
            normal = comando.lower().replace('\\', '/')
            if (re.search(r'\b(?:iniciar_lara|instalar_lara)\.bat\b', normal)
                    or (re.search(r'\b(?:gui|main)\.py\b', normal)
                        and (str(PROYECTO_DIR).lower().replace('\\', '/') in normal or self.carpeta == PROYECTO_DIR))):
                return '(bloqueado: Open Code no puede relanzar su propia aplicación; usa el artefacto de la sesión)'
            if entrada is not None and (not isinstance(entrada, str) or len(entrada) > 32000):
                return '(error: entrada de prueba inválida o demasiado larga)'
            opciones = {'input': entrada} if entrada is not None else {'stdin': subprocess.DEVNULL}
            r = subprocess.run(comando, shell=True, capture_output=True, text=True,
                               timeout=60, cwd=str(self.carpeta), encoding='utf-8', errors='replace',
                               env=dict(os.environ, PYTHONIOENCODING='utf-8'), **opciones)
            out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
            out = out.strip() or "(sin salida)"
            salida = out[:MAX_SALIDA] + ("\n...[truncado]" if len(out) > MAX_SALIDA else "")
            if 'EOFError' in out and entrada is None:
                salida += '\n[prueba] Falta stdin: repite cmd con entrada="5\\n3\\n\\n" adaptada a las preguntas. No es necesariamente un fallo del programa.'
            return f'[código de salida: {r.returncode}]\n' + salida
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
        if self.modo != 'plan' and os.getenv('LARA_CODE_PLAN_LLM', '0') != '1':
            return ('1. Revisar el objetivo y los archivos de esta sesión.\n'
                    '2. Implementar el cambio solicitado conservando el contexto.\n'
                    '3. Probar entradas normales, límites y errores; informar resultados y pendientes.')
        try:
            from core import gateway_llm
            if gateway_llm.activo():
                salida = gateway_llm.completar([
                    {'role': 'system', 'content': 'Plan breve, máximo 6 pasos, sin ejecutar acciones. Para input() indica stdin de prueba. No prometas probar ventanas con salida capturada.'},
                    {'role': 'user', 'content': f'Carpeta: {self.carpeta}\nÁrbol: {arbol[:6000]}\nTarea: {instruccion}'}],
                    max_tokens=_TOKENS_PLAN.get(get_nivel_esfuerzo(), 500))
                return salida['texto']
        except Exception:
            pass
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
        from core import gateway_llm
        # Reducir el crecimiento de contexto conservando sistema, objetivo y
        # los últimos resultados. Nunca recortar el código de la nueva decisión.
        contexto = messages[:2] + messages[max(2, len(messages)-8):]
        contexto = [dict(m, content=m['content'][:6000] if i < 2 else m['content'][:1200])
                    for i, m in enumerate(contexto)]
        errores = []
        presupuesto = min(60, max(5, float(os.getenv('LARA_CODE_DECISION_TIMEOUT', '30'))))
        plazo = time.monotonic() + presupuesto
        def restante():
            return max(0.1, plazo - time.monotonic())
        google_probado = False
        if gateway_llm.activo() and gateway_llm.modelos_acciones() and time.monotonic() >= self._cuota_hasta:
            try:
                r = gateway_llm.completar(contexto, modelo=gateway_llm.modelos_acciones()[0],
                    max_tokens=min(1800, _TOKENS_DECIDIR.get(get_nivel_esfuerzo(), 2500)),
                    timeout_s=min(10, restante()), json_mode=True)
                if r['modelo'].split('/')[-1] != gateway_llm.modelos_acciones()[0].split('/')[-1]:
                    raise ValueError('Modelo no permitido para decisiones de código')
                return self._validar_decision(_extraer_json(r['texto']))
            except Exception as e:
                self.on_log('sistema', 'Gateway no disponible para este paso; probando motores directos.')
                if getattr(e, 'status_code', None) == 429:
                    self._cuota_hasta = time.monotonic() + 60
        if time.monotonic() < self._cuota_hasta:
            google_probado = True
            try:
                return self._decidir_google(contexto, min(10, restante()))
            except Exception:
                errores.append('respaldo Google no disponible')
        cadena = list(dict.fromkeys(_cadena_routers()))
        for intento in range(3):
            if self._cancelar.is_set():
                raise RuntimeError('Detenido por el usuario.')
            if time.monotonic() >= plazo or time.monotonic() < self._cuota_hasta:
                break
            try:
                if client is None:
                    raise RuntimeError('No hay motor directo configurado.')
                kwargs = dict(model=cadena[intento % len(cadena)], messages=contexto, temperature=0.2,
                              max_tokens=min(1800, _TOKENS_DECIDIR.get(get_nivel_esfuerzo(), 2500)),
                              timeout=min(8, restante()))
                if intento == 0:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(**kwargs)
                self._registrar_uso(resp)
                if resp.choices[0].finish_reason == 'length':
                    raise ValueError('Respuesta truncada; divide la implementación en archivos pequeños.')
                return self._validar_decision(_extraer_json(resp.choices[0].message.content))
            except Exception as e:
                rescate = _rescatar_de_error(e)
                if rescate is not None:
                    return self._validar_decision(rescate)
                estado = getattr(e, 'status_code', None)
                if estado == 429 or 'rate_limit' in str(e).lower():
                    errores.append('cuota agotada')
                    if not google_probado:
                        google_probado = True
                        try:
                            return self._decidir_google(contexto, min(10, restante()))
                        except Exception:
                            pass
                    espera = min(self._espera_cuota(e), 3, max(0, restante()))
                    self.on_log('sistema', f'Cuota temporal del motor; espera acotada de {espera:g} s. Puedes detener la tarea.')
                    if intento < 2 and self._cancelar.wait(espera):
                        raise RuntimeError('Detenido por el usuario.') from None
                    continue
                if estado in (401, 403, 404, 500, 502, 503):
                    errores.append('motor no disponible')
                    continue
                errores.append('respuesta inválida o fallo de conexión')
                contexto.append({"role": "user", "content":
                                 "Tu última respuesta no fue UN ÚNICO objeto JSON válido. "
                                 "Responde de nuevo: SOLO un objeto JSON con la siguiente acción."})
        if not self._cancelar.is_set() and not google_probado and time.monotonic() < plazo:
            try:
                return self._decidir_google(contexto, min(10, restante()))
            except Exception:
                errores.append('respaldo Google no disponible')
        raise RuntimeError('No se pudo decidir el siguiente paso: ' + ', '.join(sorted(set(errores))) +
                           '. El avance está guardado en esta sesión. Escribe «continuar» y pulsa Ejecutar para retomar.')

    def _decidir_google(self, contexto, timeout_s):
        from core.llm_engine import _llamar_gemini, genai_client
        if genai_client is None:
            raise RuntimeError('Google no configurado')
        if time.monotonic() < self._google_cuota_hasta:
            raise RuntimeError('Google sigue en pausa por cuota; reanuda más tarde.')
        if self._cancelar.is_set():
            raise RuntimeError('Detenido por el usuario')
        self.on_log('sistema', 'Probando Google sin esperar otra vez la cuota del proveedor anterior.')
        try:
            texto = _llamar_gemini(contexto[0]['content'],
                '\n'.join(m['role'] + ': ' + m['content'] for m in contexto[1:]),
                max_tokens=1800, temperature=0.2, json_mode=True, max_reintentos=1, timeout_s=timeout_s)
        except Exception as e:
            if getattr(e, 'status_code', getattr(e, 'code', None)) == 429 or 'RESOURCE_EXHAUSTED' in str(e):
                self._google_cuota_hasta = time.monotonic() + 60
                self.on_log('sistema', 'Google también agotó su cuota; pauso nuevas consultas a este proveedor durante un minuto.')
            raise
        return self._validar_decision(_extraer_json(texto))

    @staticmethod
    def _espera_cuota(error):
        try:
            headers = getattr(getattr(error, 'response', None), 'headers', {})
            valor = headers.get('retry-after')
            if valor is None:
                m = re.search(r'try again in\s+([\d.]+)s', str(error), re.I)
                valor = m[1] if m else 2
            return min(30, max(1, float(valor)))
        except (ValueError, TypeError):
            return 2

    @staticmethod
    def _validar_decision(d):
        campos = {'listar': ('ruta',), 'leer': ('archivo',), 'escribir': ('archivo', 'contenido'),
                  'cmd': ('comando',), 'terminar': ('resumen',)}
        accion = d.get('accion')
        if accion not in campos or any(not isinstance(d.get(c), str) for c in campos[accion]):
            raise ValueError('Acción de código inválida')
        if 'entrada' in d and (not isinstance(d['entrada'], str) or len(d['entrada']) > 32000):
            raise ValueError('Entrada de prueba inválida')
        return d

    # ── bucle del agente ────────────────────────────────────────
    def detener(self):
        self._parar = True
        self._cancelar.set()

    def ejecutar_tarea(self, instruccion: str):
        self._parar = False
        self._cancelar.clear()
        es_continuacion = instruccion.strip().lower().strip(' .!¡') in ('continuar', 'continúa', 'continua', 'reanudar')
        if es_continuacion and not self._pendiente:
            self.on_log('sistema', 'No hay una tarea pendiente recuperable en este panel. Escribe el objetivo original completo para retomarlo; no he modificado archivos.')
            return
        sesiones.guardar_turno(self.espacio, self.session_id, "user", instruccion)
        self.on_log("usuario", instruccion)
        from core.intencion_codigo import pide_abrir_artefacto
        if (re.fullmatch(r'(?:abre|abrir|ejecuta|inicia)\s+(?:el programa|el programa que (?:hiciste|creaste)|lo que (?:hiciste|creaste))[.! ]*', instruccion.strip(), re.I)
                or (self._artefactos and pide_abrir_artefacto(instruccion))):
            try:
                resumen = self._abrir_programa_creado()
            except Exception as e:
                resumen = 'No pude abrir el programa creado: ' + str(e)
            self.on_log('terminado', resumen)
            sesiones.guardar_turno(self.espacio, self.session_id, 'assistant', '[terminado] ' + resumen)
            return

        # 1) PLAN: pensar la tarea antes de actuar
        reanudar = es_continuacion and self._pendiente
        arbol = self._listar("") if not reanudar else ''
        contexto_sesion = self._contexto_sesion()
        plan = self._planificar(instruccion + '\n\n' + contexto_sesion, arbol) if not reanudar else ''
        if plan:
            self.on_log("plan", plan)
            sesiones.guardar_turno(self.espacio, self.session_id, "assistant", f"[plan] {plan}")

        messages = [
            {"role": "system", "content": _sistema(self.carpeta, self.modo)},
            {"role": "user", "content":
                f"Tarea: {instruccion}\n\n{contexto_sesion}\n\nÁrbol actual de la carpeta (rutas exactas):\n{arbol}\n\n"
                + (f"Plan acordado:\n{plan}\n\n" if plan else "")
                + "Ejecuta la tarea paso a paso. Responde con la primera acción."},
        ]
        if reanudar:
            messages = self._pendiente
            self.on_log('sistema', 'Retomando el último resultado guardado; no se repiten acciones anteriores.')
        self._pendiente = messages
        self._guardar_estado()

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
                if self._artefactos:
                    prueba = self._ultima_prueba
                    resumen = 'Archivos conservados: ' + ', '.join(self._artefactos[-6:]) + '.'
                    if prueba and prueba['cambios'] == self._cambios:
                        resumen += (' La última prueba terminó con código de salida 0.' if prueba['ok']
                                    else ' La última prueba falló; necesita corrección.')
                    else:
                        resumen += ' No hay una prueba exitosa registrada después del último cambio.'
                    resumen += ' El proveedor no completó el siguiente paso; el objetivo completo sigue pendiente de confirmar.'
                    self.on_log('sistema', resumen)
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
                self._pendiente = None
                self._guardar_estado()
                return

            resultado = self._ejecutar_accion(accion, decision)

            # Anti-bucle: avisar si repite una acción ya ejecutada
            firma = (accion, decision.get("archivo") or decision.get("comando") or decision.get("ruta", ""), decision.get('entrada'))
            if firma in firmas:
                resultado += ("\n\n[AVISO] Ya habías ejecutado EXACTAMENTE esta acción y conoces su "
                              "resultado. No la repitas: usa lo que ya sabes o cambia de estrategia.")
            firmas.append(firma)

            sesiones.guardar_turno(self.espacio, self.session_id, "assistant",
                                   f"[{accion}] {decision.get('archivo', decision.get('comando', ''))}")
            # Realimentar el resultado al modelo para el siguiente ciclo
            messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"Resultado de '{accion}':\n{resultado}"})
            self._guardar_estado()

        self.on_log("sistema", f"Alcancé el máximo de {MAX_ITERACIONES} ciclos. Pauso aquí.")

    def _ejecutar_accion(self, accion: str, decision: dict) -> str:
        # Modos: planificación no toca nada; permisos/ediciones piden confirmación
        if accion in ("escribir", "cmd"):
            # El modo global de LARA manda sobre el de la pestaña: en 'plan' o
            # 'lectura' el agente de código tampoco escribe ni ejecuta (A-01).
            modo_global = modos.get_modo()
            if self.modo == "plan" or modo_global in ("plan", "lectura"):
                self.on_log("sistema", f"(modo planificación/lectura: '{accion}' bloqueado)")
                return ("(MODO PLANIFICACIÓN: no puedes escribir ni ejecutar. Incluye este paso en el "
                        "plan y usa 'terminar' entregando el plan completo en 'resumen'.)")
            detalle = decision.get("archivo", "") if accion == "escribir" else decision.get("comando", "")
            if accion == 'cmd' and 'entrada' in decision:
                detalle += '\nEntrada de prueba: ' + repr(decision['entrada'][:500])
            # Fase 1 (A-02): un comando propuesto por el modelo SIEMPRE se
            # confirma, incluso en la pestaña "auto", salvo en desarrollo aislado.
            pedir = self.modo == 'permisos' or (accion == 'cmd' and self.modo == 'ediciones')
            if pedir:
                self.on_log("confirmar", f"Esperando tu permiso para {accion}: {detalle}")
                if not self.on_confirmar(accion, detalle):
                    modos.auditar(f"open_code.{accion}", [str(self.carpeta), detalle], "denegado")
                    self.on_log("sistema", "Acción rechazada por el usuario.")
                    return ("(el usuario NO autorizó esta acción; propón una alternativa "
                            "o termina explicando qué queda pendiente)")
            modos.auditar(f"open_code.{accion}", [str(self.carpeta), detalle], "autorizado")

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
            resultado = self._cmd(comando, decision['entrada']) if 'entrada' in decision else self._cmd(comando)
            m = re.search(r'código de salida:\s*(-?\d+)', resultado, re.I)
            self._ultima_prueba = {'ok': bool(m and m[1] == '0'), 'cambios': self._cambios, 'comando': comando}
            self.on_log("salida", resultado)
        else:
            resultado = f"Acción desconocida: {accion}. Usa solo: listar, leer, escribir, cmd, terminar."
            self.on_log("error", resultado)
        return resultado
