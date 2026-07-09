"""
gui.py — Interfaz gráfica de LARA (modo claro, estilo HUD técnico, ordenado).

- Cabecera: título ◢ LARA + pestañas 💬 Chat / 💻 Open Code a la izquierda
  (VERDE solo aquí, por pedido de Adi), estado a la derecha (NARANJA cuando
  procesa, SUAVE en reposo).
- Barra lateral: ➕ Nueva sesión y RECIENTES (clic abre; clic derecho elimina).
- Centro: chat sobre panel blanco con borde. Al explicar un tema, el chat se
  reemplaza por un panel de slides animados DENTRO de la misma ventana.
- Debajo de la barra de entrada: selector de esfuerzo (como en Claude Desktop)
  que cambia el nivel de core.llm_engine y muestra el modelo activo.
- Escucha SIEMPRE activa: háblale cuando quieras. Mientras LARA habla, di
  "LARA, ..." para interrumpirla; di "LARA silencio" para que deje de hablar.
- Config: uso de tokens, candado de voz, ENTRENAMIENTO de voz por frases
  (voz_id), prueba "¿Quién habla?" y dispositivos de audio.

Colores: TODOS vienen de ui_tema.py (modo claro). Nada de paletas locales.
"""

import os
import time
import queue
import ctypes
import threading
import tkinter as tk
from tkinter import filedialog, scrolledtext

from config import ADMIN_NOMBRE
from core.cerebro import LaraCerebro
from core.agente_fondo import iniciar_agente_fondo
from core.skills_registry import auto_registrar_skills_core
from core import skills_voz, skills_audio, sesiones, uso_tokens, voz_id
from gui_explicacion import PanelExplicacion
from gui_opencode import OpenCodePanel
from ui_tema import (FONDO, PANEL, BORDE, TEXTO, SUAVE, ACENTO, ACENTO_2,
                     NARANJA, VERDE, FUENTE, FUENTE_B, FUENTE_I, FUENTE_T)

# Otro agente implementa NIVELES_ESFUERZO / set_nivel_esfuerzo / get_nivel_esfuerzo /
# modelo_del_nivel en core.llm_engine; programamos contra ese contrato con red de seguridad.
try:
    from core import llm_engine as _llm
except Exception:
    _llm = None

PALABRAS_SILENCIO = ("silencio", "cállate", "callate", "calla", "guarda silencio")
PALABRAS_VOZ_ON = ("activa la voz", "enciende la voz", "habla de nuevo", "reactiva la voz", "puedes hablar")

# Niveles por si llm_engine aún no expone NIVELES_ESFUERZO (contrato en desarrollo)
_NIVELES_DEF = ("bajo", "medio", "alto", "extra", "max", "ultracode")

# Frases por si voz_id aún no expone frases_entrenamiento() (contrato en desarrollo)
_FRASES_DEF = [
    "Hola LARA, soy yo y esta es mi voz de siempre.",
    "El taller funciona mejor cuando todo está en orden.",
    "Hoy quiero revisar los proyectos pendientes contigo.",
    "Uno, dos, tres, probando el reconocimiento de voz.",
    "Gracias LARA, con esto terminamos el registro.",
]


class LaraGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._escucha_on = True
        self._parar_escucha = threading.Event()
        self._ocupada = False
        self._panel = None
        self.tab_code = None   # pestaña Open Code (se crea al abrirla)
        self._cola = queue.Queue()   # cola de tareas: la entrada NUNCA se bloquea

        root.title("LARA — Asistente del Taller")
        root.configure(bg=FONDO)
        root.geometry("1020x720")
        root.minsize(820, 560)

        self.cerebro = LaraCerebro(
            on_status=self._status_safe,
            on_speak=lambda m: skills_voz.hablar(m),
            on_holograma=self._mostrar_panel_safe,
            espacio="chat",
            # Voz en STREAMING: en respuestas profundas LARA empieza a hablar
            # apenas tiene la primera frase (el texto final se imprime al terminar).
            on_speak_stream=lambda gen: skills_voz.hablar_stream(gen),
        )
        skills_voz.set_voz_activa(True)

        self._construir_ui()
        self._arrancar_servicios()
        self._refrescar_sesiones()
        threading.Thread(target=self._worker_proceso, daemon=True).start()
        self._saludo()
        self._iniciar_escucha()

    # ── cola de procesamiento (un solo trabajador, entrada no bloqueante) ──
    def _encolar(self, funcion, etiqueta_usuario=None):
        if etiqueta_usuario:
            self.imprimir(etiqueta_usuario[0], etiqueta_usuario[1], etiqueta_usuario[2])
        self._cola.put(funcion)

    def _worker_proceso(self):
        while True:
            funcion = self._cola.get()
            self._set_ocupada(True)
            try:
                respuesta = funcion()
            except Exception as e:
                respuesta = f"Tuve un error interno: {e}"
            if respuesta:
                self.imprimir("LARA", respuesta, "lara")
            self._set_ocupada(False)
            self.root.after(0, self._refrescar_sesiones)
            # Refrescar la etiqueta de motor: qué cerebro respondió este turno
            try:
                self.root.after(0, lambda: self.lbl_modelo.config(text=self._modelo_activo()))
            except Exception:
                pass
            self._cola.task_done()

    # ── UI ──────────────────────────────────────────────────────
    def _construir_ui(self):
        # Cabecera: título + pestañas a la izquierda, estado a la derecha
        header = tk.Frame(self.root, bg=FONDO, highlightthickness=1, highlightbackground=BORDE)
        header.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(header, text="◢ LARA", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(side="left", padx=12, pady=8)
        # Pestañas Chat / Open Code: ÚNICO lugar de la interfaz donde se usa VERDE
        self.btn_tab_chat = tk.Button(header, text="💬 Chat", command=lambda: self._mostrar_tab("chat"),
                                      font=FUENTE_B, bg=VERDE, fg="#FFFFFF",
                                      activebackground=VERDE, activeforeground="#FFFFFF",
                                      relief="flat", padx=10, pady=2, cursor="hand2", bd=0)
        self.btn_tab_chat.pack(side="left", padx=(6, 2), pady=6)
        self.btn_tab_code = tk.Button(header, text="💻 Open Code", command=lambda: self._mostrar_tab("code"),
                                      font=FUENTE_B, bg=FONDO, fg=VERDE,
                                      activebackground=VERDE, activeforeground="#FFFFFF",
                                      relief="flat", padx=10, pady=2, cursor="hand2", bd=0)
        self.btn_tab_code.pack(side="left", padx=2, pady=6)
        self.lbl_estado = tk.Label(header, text="● escuchando", font=FUENTE, fg=SUAVE, bg=FONDO)
        self.lbl_estado.pack(side="right", padx=12)

        cont = tk.Frame(self.root, bg=FONDO)
        cont.pack(fill="both", expand=True, padx=10, pady=10)

        # ── Barra lateral: solo Nueva sesión + RECIENTES ────────
        lateral = tk.Frame(cont, bg=FONDO, width=232, highlightthickness=1, highlightbackground=BORDE)
        lateral.pack(side="left", fill="y")
        lateral.pack_propagate(False)
        self._boton(lateral, "➕  Nueva sesión", self._nueva_sesion, ancho=True)
        tk.Frame(lateral, bg=BORDE, height=1).pack(fill="x", padx=10, pady=4)
        tk.Label(lateral, text="RECIENTES", font=FUENTE, fg=SUAVE, bg=FONDO).pack(anchor="w", padx=12, pady=(4, 2))
        self.lista = tk.Listbox(lateral, bg=PANEL, fg=TEXTO, relief="flat", font=FUENTE,
                                selectbackground=ACENTO, selectforeground=PANEL,
                                activestyle="none", bd=0, highlightthickness=1,
                                highlightbackground=BORDE)
        self.lista.pack(fill="both", expand=True, padx=8, pady=4)
        self.lista.bind("<<ListboxSelect>>", self._click_sesion)
        self.lista.bind("<Button-3>", self._menu_sesion)        # clic derecho
        self._sesion_ids = []
        self._menu = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXTO, font=FUENTE,
                             activebackground=ACENTO, activeforeground=PANEL)
        self._menu.add_command(label="🗑  Eliminar conversación", command=self._eliminar_sesion_menu)
        self._menu_idx = None

        # ── Panel principal (pestañas: Chat / Open Code) ─
        self.principal = tk.Frame(cont, bg=FONDO)
        self.principal.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.tab_chat = tk.Frame(self.principal, bg=FONDO)
        self.tab_chat.pack(fill="both", expand=True)
        principal = self.tab_chat  # los widgets del chat viven dentro de su pestaña

        # Chip de documento activo (arriba del chat)
        self.fila_doc = tk.Frame(principal, bg=PANEL, highlightthickness=1, highlightbackground=BORDE)
        self.lbl_doc = tk.Label(self.fila_doc, text="", font=FUENTE, fg=ACENTO, bg=PANEL)
        self.lbl_doc.pack(side="left", padx=(10, 6), pady=4)
        tk.Button(self.fila_doc, text="✕", command=self._quitar_documento, font=FUENTE_B,
                  bg=PANEL, fg=SUAVE, activebackground=PANEL, activeforeground=NARANJA,
                  relief="flat", cursor="hand2", bd=0).pack(side="right", padx=8)

        # Contenedor central (chat o panel de explicación)
        self.centro = tk.Frame(principal, bg=PANEL, highlightthickness=1, highlightbackground=BORDE)
        self.centro.pack(fill="both", expand=True)
        self.chat = scrolledtext.ScrolledText(
            self.centro, wrap="word", font=FUENTE, bg=PANEL, fg=TEXTO,
            insertbackground=TEXTO, relief="flat", padx=14, pady=12, state="disabled",
            highlightthickness=0)
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_config("lara", foreground=ACENTO, font=FUENTE_B)
        self.chat.tag_config("yo", foreground=TEXTO, font=FUENTE_B)
        self.chat.tag_config("sys", foreground=SUAVE, font=FUENTE_I)
        self.chat.tag_config("cuerpo", foreground=TEXTO, font=FUENTE)

        # Botonera del chat: UNA sola fila con espaciado uniforme
        barra = tk.Frame(principal, bg=FONDO)
        barra.pack(fill="x", pady=(8, 4))
        self._boton(barra, "📷 Ver pantalla", self._click_pantalla)
        self.btn_vivo = self._boton(barra, "📡 En vivo: OFF", self._toggle_pantalla_vivo)
        self._boton(barra, "📎 Subir", self._click_subir)
        self._boton(barra, "🧠 Aprender", self._click_aprender)
        self.btn_voz = self._boton(barra, "🔊 Voz: ON", self._toggle_voz)
        self.btn_escucha = self._boton(barra, "👂 Escucha: ON", self._toggle_escucha)
        self.btn_reunion = self._boton(barra, "🎙️ Reunión: OFF", self._toggle_reunion)
        self._boton(barra, "⚙️ Config", self._abrir_config)

        # Entrada
        entrada = tk.Frame(principal, bg=PANEL, highlightthickness=1, highlightbackground=BORDE)
        entrada.pack(fill="x", pady=(4, 0))
        self.txt = tk.Entry(entrada, font=FUENTE, bg=PANEL, fg=TEXTO, insertbackground=ACENTO,
                            relief="flat", highlightthickness=0)
        self.txt.pack(side="left", fill="x", expand=True, ipady=8, padx=10)
        self.txt.bind("<Return>", lambda e: self._enviar_texto())
        self.txt.focus_set()
        b = tk.Button(entrada, text="➤", command=self._enviar_texto, font=FUENTE_B, bg=PANEL, fg=ACENTO,
                      activebackground=ACENTO, activeforeground=PANEL, relief="flat", padx=14,
                      cursor="hand2", bd=0)
        b.pack(side="right")

        # Fila discreta de esfuerzo (como Claude Desktop): selector a la derecha
        self._construir_fila_esfuerzo(principal)

    def _construir_fila_esfuerzo(self, parent):
        """Selector de nivel de esfuerzo + modelo activo, debajo de la entrada."""
        fila = tk.Frame(parent, bg=FONDO)
        fila.pack(fill="x", pady=(4, 0))
        try:
            niveles = list(getattr(_llm, "NIVELES_ESFUERZO", None) or _NIVELES_DEF)
        except Exception:
            niveles = list(_NIVELES_DEF)
        try:
            inicial = _llm.get_nivel_esfuerzo()
        except Exception:
            inicial = niveles[1] if len(niveles) > 1 else niveles[0]
        if inicial not in niveles:
            inicial = niveles[0]
        self.lbl_modelo = tk.Label(fila, text=self._modelo_activo(), font=FUENTE_I,
                                   fg=SUAVE, bg=FONDO)
        self.lbl_modelo.pack(side="right", padx=(0, 6))
        self.var_esfuerzo = tk.StringVar(value=inicial)
        om = tk.OptionMenu(fila, self.var_esfuerzo, *niveles, command=self._cambiar_esfuerzo)
        om.config(bg=FONDO, fg=ACENTO, font=FUENTE, relief="flat", bd=0,
                  highlightthickness=1, highlightbackground=BORDE,
                  activebackground=ACENTO, activeforeground=PANEL, cursor="hand2")
        om["menu"].config(bg=PANEL, fg=TEXTO, font=FUENTE,
                          activebackground=ACENTO, activeforeground=PANEL)
        om.pack(side="right", padx=4)
        tk.Label(fila, text="Esfuerzo", font=FUENTE, fg=SUAVE, bg=FONDO).pack(side="right", padx=(0, 2))

    def _modelo_activo(self) -> str:
        # Tras cada turno muestra QUÉ MOTOR respondió de verdad (⚡ reflejo /
        # 🏠 local / ☁ nube) — transparencia del cerebro híbrido. Antes del
        # primer turno, muestra el router preferido del nivel como siempre.
        try:
            motor = str(getattr(_llm, "get_ultimo_motor", lambda: "")() or "")
            if motor:
                return motor
        except Exception:
            pass
        try:
            return str(_llm.modelo_del_nivel())
        except Exception:
            return ""

    def _cambiar_esfuerzo(self, nivel):
        try:
            _llm.set_nivel_esfuerzo(nivel)
        except Exception:
            pass
        # Al cambiar el nivel se muestra su router preferido (no el motor del
        # turno pasado); tras el siguiente turno vuelve a indicar quién respondió.
        try:
            self.lbl_modelo.config(text=str(_llm.modelo_del_nivel()))
        except Exception:
            pass

    def _boton(self, parent, texto, cmd, ancho=False):
        b = tk.Button(parent, text=texto, command=cmd, font=FUENTE_B, bg=FONDO, fg=ACENTO,
                      activebackground=ACENTO, activeforeground=PANEL, relief="flat",
                      padx=10, pady=6, cursor="hand2", bd=0, anchor="w" if ancho else "center")
        if ancho:
            b.pack(fill="x", padx=8, pady=4)
        else:
            b.pack(side="left", padx=4)
        return b

    # ── helpers thread-safe ─────────────────────────────────────
    def _status_safe(self, msg, color=SUAVE):
        self.root.after(0, lambda: self.lbl_estado.config(text=msg, fg=color))

    def _imprimir(self, quien, texto, tag):
        self.chat.config(state="normal")
        if quien:
            self.chat.insert("end", f"{quien}  ", tag)
        self.chat.insert("end", f"{texto}\n\n", "cuerpo" if tag != "sys" else "sys")
        self.chat.config(state="disabled")
        self.chat.see("end")

    def imprimir(self, quien, texto, tag):
        self.root.after(0, lambda: self._imprimir(quien, texto, tag))

    def _set_ocupada(self, v):
        self._ocupada = v
        # NARANJA cuando procesa (estado activo); SUAVE en reposo
        if v:
            self._status_safe("● procesando...", NARANJA)
        else:
            self._status_safe("● escuchando" if self._escucha_on else "lista", SUAVE)

    # ── arranque ────────────────────────────────────────────────
    def _arrancar_servicios(self):
        iniciar_agente_fondo()
        # El agente de fondo avisa EN VOZ ALTA lo crítico (tareas que vencen ya).
        try:
            from core.agente_fondo import set_voz_alertas
            set_voz_alertas(lambda m: skills_voz.hablar(m))
        except Exception:
            pass
        try:
            auto_registrar_skills_core()
        except Exception:
            pass

    def _saludo(self):
        # Briefing proactivo tipo JARVIS: saluda por tu nombre y te pone al día
        # (tareas urgentes, compras, novedades). Si falla, saludo simple.
        try:
            from core import rutinas
            texto = rutinas.briefing()
        except Exception:
            texto = f"Sistemas en línea, {ADMIN_NOMBRE}. Te escucho. ¿En qué trabajamos hoy?"
        self.imprimir("LARA", texto, "lara")
        # Ignorar el micrófono mientras suena el briefing (anti auto-activación).
        self._saludando = True
        skills_voz.hablar(texto, on_done=lambda: setattr(self, "_saludando", False))
        # Red de seguridad: si el on_done no llegara, reactivar la escucha igual.
        threading.Timer(12.0, lambda: setattr(self, "_saludando", False)).start()

    # ── sesiones ────────────────────────────────────────────────
    def _refrescar_sesiones(self):
        self.lista.delete(0, "end")
        self._sesion_ids = []
        for s in sesiones.listar_sesiones("chat"):
            marca = "● " if s["id"] == self.cerebro.session_id else "   "
            self.lista.insert("end", f"{marca}{s['titulo'][:24]}")
            self._sesion_ids.append(s["id"])

    def _nueva_sesion(self):
        self._cerrar_panel()
        self.cerebro.nueva_sesion()
        self._quitar_documento()
        self._limpiar_chat()
        self._refrescar_sesiones()
        self.imprimir("LARA", "Nueva conversación lista.", "lara")

    def _click_sesion(self, _evt):
        sel = self.lista.curselection()
        if not sel:
            return
        sid = self._sesion_ids[sel[0]]
        if sid == self.cerebro.session_id:
            return
        self._cerrar_panel()
        self.cerebro.cargar_sesion(sid)
        data = sesiones.cargar_sesion("chat", sid)
        self._limpiar_chat()
        self.imprimir("", f"— {data.get('titulo','')} —", "sys")
        for t in data.get("turnos", []):
            if t["role"] == "user" and t["content"].startswith("["):
                continue
            quien = "Tú" if t["role"] == "user" else "LARA"
            tag = "yo" if t["role"] == "user" else "lara"
            self.imprimir(quien, t["content"], tag)
        self._refrescar_sesiones()

    def _menu_sesion(self, evt):
        idx = self.lista.nearest(evt.y)
        if idx < 0 or idx >= len(self._sesion_ids):
            return
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(idx)
        self._menu_idx = idx
        try:
            self._menu.tk_popup(evt.x_root, evt.y_root)
        finally:
            self._menu.grab_release()

    def _eliminar_sesion_menu(self):
        if self._menu_idx is None or self._menu_idx >= len(self._sesion_ids):
            return
        sid = self._sesion_ids[self._menu_idx]
        era_actual = (sid == self.cerebro.session_id)
        sesiones.eliminar_sesion("chat", sid)
        if era_actual:
            restantes = sesiones.listar_sesiones("chat")
            if restantes:
                self.cerebro.cargar_sesion(restantes[0]["id"])
                self._cargar_en_chat(restantes[0]["id"])
            else:
                self.cerebro.nueva_sesion()
                self._limpiar_chat()
                self.imprimir("LARA", "Conversación eliminada.", "lara")
        self._refrescar_sesiones()

    def _cargar_en_chat(self, sid):
        data = sesiones.cargar_sesion("chat", sid)
        self._limpiar_chat()
        self.imprimir("", f"— {data.get('titulo','')} —", "sys")
        for t in data.get("turnos", []):
            if t["role"] == "user" and t["content"].startswith("["):
                continue
            quien = "Tú" if t["role"] == "user" else "LARA"
            self.imprimir(quien, t["content"], "yo" if t["role"] == "user" else "lara")

    def _limpiar_chat(self):
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.config(state="disabled")

    # ── panel de explicación (in-window) ────────────────────────
    def _mostrar_panel_safe(self, contenido):
        self.root.after(0, lambda: self._mostrar_panel(contenido))

    def _mostrar_panel(self, contenido):
        if self._panel is None:
            self._panel = PanelExplicacion(self.centro, on_cerrar=self._cerrar_panel)
        self.chat.pack_forget()
        self._panel.pack(fill="both", expand=True)
        try:
            self._panel.mostrar(contenido)
        except Exception as e:
            self._cerrar_panel()
            self.imprimir("", f"(No pude animar la explicación: {e})", "sys")

    def _cerrar_panel(self):
        if self._panel is not None:
            try:
                self._panel._cancelar_pendientes()
                self._panel.pack_forget()
            except Exception:
                pass
        self.chat.pack(fill="both", expand=True)

    # ── chat ────────────────────────────────────────────────────
    def _enviar_texto(self):
        texto = self.txt.get().strip()
        if not texto:
            return
        self.txt.delete(0, "end")
        self._encolar(lambda: self.cerebro.procesar(texto), ("Tú", texto, "yo"))

    def _click_pantalla(self):
        self._encolar(lambda: self.cerebro.ver_pantalla(),
                      ("", "📷 Capturando y analizando tu pantalla...", "sys"))

    def _toggle_pantalla_vivo(self):
        self.cerebro.modo_pantalla = not self.cerebro.modo_pantalla
        on = self.cerebro.modo_pantalla
        self.btn_vivo.config(text=f"📡 En vivo: {'ON' if on else 'OFF'}")
        self.imprimir("", "📡 Pantalla en vivo " + ("activada: pregúntame sobre lo que veo." if on else "desactivada."), "sys")

    # ── archivos ────────────────────────────────────────────────
    def _click_subir(self):
        ruta = filedialog.askopenfilename(
            title="Elige una foto, archivo, video o PDF",
            filetypes=[("Todos", "*.*"),
                       ("Imágenes", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("Documentos", "*.pdf *.docx *.txt *.md *.csv"),
                       ("Video/Audio", "*.mp4 *.mov *.mkv *.webm *.mp3 *.wav *.m4a")])
        if not ruta:
            return
        # Cargar como chip y dejar que pregunte por texto o voz (sin diálogos extra)
        self.cerebro.cargar_documento(ruta)
        self.fila_doc.pack(fill="x", pady=(0, 6), before=self.centro)
        self.lbl_doc.config(text=f"📎 {os.path.basename(ruta)}  —  pregúntame por texto o voz")
        self.imprimir("", f"📎 Archivo cargado: {os.path.basename(ruta)}. Pregúntame lo que quieras sobre él (o ✕ para soltarlo).", "sys")
        skills_voz.hablar("Archivo cargado. ¿Qué quieres saber?")

    def _click_aprender(self):
        ruta = filedialog.askopenfilename(title="Archivo para que LARA aprenda y recuerde")
        if not ruta:
            return
        self._encolar(lambda: self.cerebro.analizar_archivo_directo(ruta, aprender=True),
                      ("Tú", f"🧠 Aprende de: {os.path.basename(ruta)}", "yo"))

    def _quitar_documento(self):
        self.cerebro.limpiar_documento()
        self.lbl_doc.config(text="")
        self.fila_doc.pack_forget()

    # ── pestañas (Chat / Open Code) ─────────────────────────────
    def _mostrar_tab(self, nombre):
        if nombre == "code":
            if self.tab_code is None:
                self.tab_code = OpenCodePanel(self.principal)
            self.tab_chat.pack_forget()
            self.tab_code.pack(fill="both", expand=True)
        else:
            if self.tab_code is not None:
                self.tab_code.pack_forget()
            self.tab_chat.pack(fill="both", expand=True)
        # VERDE solo aquí: pestaña activa fondo verde/texto blanco, inactiva texto verde
        activo, inactivo = {"bg": VERDE, "fg": "#FFFFFF"}, {"bg": FONDO, "fg": VERDE}
        self.btn_tab_chat.config(**(activo if nombre == "chat" else inactivo))
        self.btn_tab_code.config(**(activo if nombre == "code" else inactivo))

    # ── voz: salida ─────────────────────────────────────────────
    def _toggle_voz(self):
        nuevo = not skills_voz.VOZ_ACTIVA
        skills_voz.set_voz_activa(nuevo)
        self.btn_voz.config(text=f"🔊 Voz: {'ON' if nuevo else 'OFF'}")

    # ── voz: escucha siempre activa con barge-in ────────────────
    def _toggle_escucha(self):
        self._escucha_on = not self._escucha_on
        if self._escucha_on:
            self.btn_escucha.config(text="👂 Escucha: ON")
            self._iniciar_escucha()
            self._status_safe("● escuchando", SUAVE)
        else:
            self.btn_escucha.config(text="👂 Escucha: OFF")
            self._parar_escucha.set()
            self._status_safe("lista", SUAVE)

    def _toggle_reunion(self):
        from core import modo_reunion
        if modo_reunion.activo():
            r = modo_reunion.detener()
            self.btn_reunion.config(text="🎙️ Reunión: OFF")
            self.imprimir("", f"🎙️ Reunión detenida ({r.get('fragmentos', 0)} fragmentos escuchados).", "sys")
        else:
            # La transcripción se alimenta del bucle de escucha: asegúralo ON.
            if not self._escucha_on:
                self._toggle_escucha()
            modo_reunion.iniciar_externo()
            self.btn_reunion.config(text="🎙️ Reunión: ON")
            self.imprimir("", "🎙️ Modo reunión ON. Escucho la sala; di \"Lara, ¿qué opinas?\" cuando quieras mi análisis.", "sys")

    def _iniciar_escucha(self):
        self._parar_escucha.clear()
        threading.Thread(target=self._bucle_escucha, daemon=True).start()

    def _bucle_escucha(self):
        from core.skills_control import escribir_texto, _enfocar
        while not self._parar_escucha.is_set():
            dictando = self.cerebro.modo_dictado
            t0 = time.time()
            res = skills_voz.escuchar(espera_inicio=20.0,
                                      fin_silencio=1.8 if dictando else None,
                                      max_frase=20.0 if dictando else None)
            if self._parar_escucha.is_set():
                break
            if "texto" not in res or not res["texto"]:
                continue
            texto = res["texto"]
            low = texto.lower()
            audio, srr = res.get("audio"), res.get("sr", 16000)

            # Al arrancar, LARA dice el briefing en voz alta. Sin auriculares, el
            # micrófono lo capta y se auto-activaría. Mientras saluda, ignoramos
            # todo lo que se oiga (evita el "Buenas noches, Adi..." como si lo
            # hubieras dicho tú).
            if getattr(self, "_saludando", False):
                continue

            # ANTI-ECO: ¿LARA estaba hablando mientras se grababa esta frase?
            # (su voz sale por los parlantes y el micrófono la vuelve a captar)
            solapa = skills_voz.hablo_desde(res.get("t_voz", t0) - 0.7)
            if solapa and skills_voz.es_eco(texto):
                continue  # era su propia voz: no es una pregunta tuya

            # APRENDIZAJE PASIVO de voz (sin botón): si dijiste "aprende mi voz",
            # cada frase que hables se enrola sola hasta completar el perfil. No
            # se trata como comando. Va ANTES del candado para poder aprender
            # aunque el candado esté activo.
            try:
                if voz_id.aprendizaje_activo() and audio is not None:
                    est = voz_id.alimentar_aprendizaje(audio, srr)
                    if est.get("estado") == "completo":
                        msg = f"Listo, ya reconozco tu voz, {est['nombre']}."
                        self.imprimir("", f"🎙️ {msg}", "sys")
                        skills_voz.hablar(msg)
                    elif est.get("estado") == "aprendiendo":
                        self.imprimir("", f"🎙️ Aprendiendo tu voz... faltan {est['faltan']} frases. Sigue hablándome.", "sys")
                    continue
            except Exception:
                pass

            # Candado de voz: ignorar a quien no esté autorizado
            try:
                if audio is not None and not voz_id.autorizado(audio, srr):
                    continue
            except Exception:
                pass

            # Identificar al hablante (si voz_id lo permite): la etiqueta del
            # mensaje y el cerebro sabrán QUIÉN habló.
            hablante = None
            _score = 0.0
            try:
                if audio is not None:
                    hablante, _score = voz_id.identificar(audio, srr)
            except Exception:
                hablante = None

            # REFUERZO AUTOMÁTICO: si te reconoció CON SEGURIDAD, mejora tu perfil
            # solo con esta muestra. Así reconoce cada vez mejor con el uso, sin
            # que vuelvas a entrenar (aprender_incremental ignora las dudas).
            try:
                if hablante and audio is not None:
                    voz_id.aprender_incremental(hablante, audio, srr, score=_score)
            except Exception:
                pass

            # MODO REUNIÓN: transcribe TODO lo que se habla en la sala, pero solo
            # responde cuando dices "Lara" (si no, respondería a cada frase de la
            # conversación). Al decir "Lara" cae al flujo normal y el cerebro usa
            # la transcripción como contexto para su análisis/opinión.
            try:
                from core import modo_reunion, wake_word
                if modo_reunion.activo():
                    modo_reunion.añadir_externo(texto, hablante)
                    # Detección DIFUSA: el STT suele destrozar "Lara" (plara,
                    # copinas...). menciona_lara reconoce esas variantes, así no
                    # te ignora por un error de transcripción.
                    if not wake_word.menciona_lara(texto):
                        continue
            except Exception:
                pass

            # MODO DICTADO: teclea lo que digas (frase por frase) hasta "termina el dictado"
            if self.cerebro.modo_dictado:
                if any(f in low for f in self.cerebro._DICTADO_FIN):
                    self.cerebro.modo_dictado = False
                    self.imprimir("", "✍️ Dictado terminado.", "sys")
                    skills_voz.hablar("Dictado terminado.")
                else:
                    if self.cerebro.dictado_destino:
                        _enfocar(self.cerebro.dictado_destino)  # llevar foco al programa destino
                    escribir_texto(texto + " ", visible=False)
                    self.imprimir("✍️ dictado", texto, "yo")
                continue

            # Comando: silencio / reactivar voz
            if any(p in low for p in PALABRAS_SILENCIO):
                skills_voz.set_voz_activa(False)
                self.imprimir("", "🔇 Voz desactivada (\"silencio\").", "sys")
                self.root.after(0, lambda: self.btn_voz.config(text="🔊 Voz: OFF"))
                continue
            if any(p in low for p in PALABRAS_VOZ_ON):
                skills_voz.set_voz_activa(True)
                self.root.after(0, lambda: self.btn_voz.config(text="🔊 Voz: ON"))

            # Voz solapada con la de LARA: solo interrumpe si dices "LARA"
            # (el eco puro ya se descartó arriba; esto cubre lo demás)
            if solapa:
                from core import wake_word as _ww
                if _ww.menciona_lara(texto):
                    skills_voz.detener_voz()
                    if self._panel is not None:
                        try:
                            self._panel._cancelar_pendientes()
                        except Exception:
                            pass
                    comando = self._quitar_wake(texto)
                    if comando:
                        self._responder_voz(comando, hablante)
                continue

            # En reposo: procesa (quitando un "LARA" inicial si lo hay)
            comando = self._quitar_wake(texto)
            if comando:
                self._responder_voz(comando, hablante)

    @staticmethod
    def _quitar_wake(texto: str) -> str:
        # Quita un "Lara" inicial aunque el STT lo haya destrozado (plara,
        # clara...). Si solo dijiste el nombre, devuelve "" (no hay orden).
        try:
            from core import wake_word
            activo, orden = wake_word._extraer_orden(texto)
            if activo:
                return orden.strip()
        except Exception:
            pass
        t = texto.strip()
        bajo = t.lower()
        for w in ("lara,", "lara ", "lara"):
            if bajo.startswith(w):
                return t[len(w):].strip(" ,")
        return t

    def _responder_voz(self, texto, hablante=None):
        # Encola: no bloquea la escucha (puedes seguir hablando / interrumpir).
        # Si se identificó al hablante, se lo pasamos al cerebro (parámetro
        # opcional; si esa versión aún no lo acepta, caemos a la firma vieja).
        etiqueta = f"Tú (voz · {hablante})" if hablante else "Tú (voz)"

        def tarea():
            if hablante:
                try:
                    return self.cerebro.procesar(texto, hablante=hablante)
                except TypeError:
                    return self.cerebro.procesar(texto)
            return self.cerebro.procesar(texto)

        self._encolar(tarea, (etiqueta, texto, "yo"))

    # ── configuración (tokens % + candado + entrenamiento de voz + audio) ──
    def _abrir_config(self):
        disp = skills_audio.listar_dispositivos()
        win = tk.Toplevel(self.root)
        win.title("Configuración")
        win.configure(bg=FONDO)
        win.geometry("620x680")
        win.attributes("-topmost", True)

        tk.Label(win, text="USO DE TOKENS (hoy, en %)", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(anchor="w", padx=16, pady=(16, 2))
        caja = tk.Text(win, height=6, bg=PANEL, fg=TEXTO, font=FUENTE, relief="flat",
                       highlightthickness=1, highlightbackground=BORDE, wrap="word")
        caja.pack(fill="x", padx=16)

        def refrescar_tokens():
            caja.config(state="normal")
            caja.delete("1.0", "end")
            caja.insert("end", uso_tokens.resumen_hoy())
            caja.config(state="disabled")
        refrescar_tokens()
        self._boton_simple(win, "↻ Actualizar", refrescar_tokens).pack(anchor="w", padx=16, pady=6)

        # ── Reconocimiento y ENTRENAMIENTO de voz ───────────────
        cfg = voz_id.get_config()
        tk.Frame(win, bg=BORDE, height=1).pack(fill="x", padx=16, pady=6)
        tk.Label(win, text="🔐 RECONOCIMIENTO DE VOZ", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(anchor="w", padx=16)
        var_lock = tk.BooleanVar(value=cfg.get("candado", False))
        tk.Checkbutton(win, text="Solo responder a mi voz", variable=var_lock, bg=FONDO, fg=TEXTO,
                       selectcolor=PANEL, activebackground=FONDO, activeforeground=ACENTO, font=FUENTE,
                       command=lambda: voz_id.set_candado(var_lock.get())).pack(anchor="w", padx=16)

        # Entrenamiento guiado: nombre + frases de frases_entrenamiento()
        fila_ent = tk.Frame(win, bg=FONDO)
        fila_ent.pack(fill="x", padx=16, pady=(6, 2))
        tk.Label(fila_ent, text="Nombre", font=FUENTE, fg=SUAVE, bg=FONDO).pack(side="left")
        var_nombre = tk.StringVar(value="")
        ent_nombre = tk.Entry(fila_ent, textvariable=var_nombre, font=FUENTE, bg=PANEL, fg=TEXTO,
                              insertbackground=ACENTO, relief="flat", highlightthickness=1,
                              highlightbackground=BORDE, width=18)
        ent_nombre.pack(side="left", padx=8, ipady=3)
        self._boton_simple(fila_ent, "🎙️ Entrenar esta voz",
                           lambda: self._entrenar_voz(var_nombre.get().strip() or ADMIN_NOMBRE,
                                                      refrescar_personas)).pack(side="left", padx=4)
        self._boton_simple(fila_ent, "🧪 ¿Quién habla?", self._quien_habla).pack(side="left", padx=4)

        # Personas conocidas + eliminar
        disp_id = "disponible" if voz_id.disponible() else "no disponible (falta numpy)"
        tk.Label(win, text=f"Personas conocidas · {disp_id}", font=FUENTE_I,
                 fg=SUAVE, bg=FONDO).pack(anchor="w", padx=16, pady=(6, 0))
        fila_per = tk.Frame(win, bg=FONDO)
        fila_per.pack(fill="x", padx=16, pady=(2, 4))
        lista_per = tk.Listbox(fila_per, bg=PANEL, fg=TEXTO, relief="flat", font=FUENTE,
                               selectbackground=ACENTO, selectforeground=PANEL, height=3,
                               activestyle="none", bd=0, highlightthickness=1,
                               highlightbackground=BORDE)
        lista_per.pack(side="left", fill="x", expand=True)

        def refrescar_personas():
            lista_per.delete(0, "end")
            try:
                for p in voz_id.personas():
                    lista_per.insert("end", f" {p}")
            except Exception:
                pass

        def eliminar_persona():
            sel = lista_per.curselection()
            if not sel:
                return
            nombre = lista_per.get(sel[0]).strip()
            try:
                voz_id.eliminar_persona(nombre)
                self.imprimir("", f"🗑 Voz de «{nombre}» eliminada.", "sys")
            except Exception as e:
                self.imprimir("", f"⚠️ No pude eliminar esa voz: {e}", "sys")
            refrescar_personas()

        refrescar_personas()
        self._boton_simple(fila_per, "🗑 Eliminar", eliminar_persona).pack(side="left", padx=8)

        # ── Audio ───────────────────────────────────────────────
        tk.Frame(win, bg=BORDE, height=1).pack(fill="x", padx=16, pady=6)
        tk.Label(win, text="🎤 Micrófono (entrada)", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(anchor="w", padx=16)
        entradas = [("Predeterminado del sistema", None)] + [(n, i) for i, n in disp.get("entrada", [])]
        var_in = tk.StringVar(value=self._etiqueta_actual(skills_audio.get_input_device(), entradas))
        self._optionmenu(win, var_in, [e[0] for e in entradas])
        tk.Label(win, text="🔊 Salida (parlantes/auriculares)", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(anchor="w", padx=16, pady=(8, 0))
        salidas = [("Predeterminado del sistema", None)] + [(n, i) for i, n in disp.get("salida", [])]
        var_out = tk.StringVar(value=self._etiqueta_actual(skills_audio.get_output_device(), salidas))
        self._optionmenu(win, var_out, [s[0] for s in salidas])

        def guardar():
            for nombre, idx in entradas:
                if nombre == var_in.get():
                    skills_audio.set_input_device(idx)
            for nombre, idx in salidas:
                if nombre == var_out.get():
                    skills_audio.set_output_device(idx)
            self.imprimir("", "⚙️ Configuración guardada.", "sys")
            win.destroy()

        self._boton_simple(win, "Guardar", guardar).pack(pady=16)

    # ── entrenamiento de voz (varias frases guiadas) ────────────
    def _frases_entrenamiento(self):
        """Frases guiadas de voz_id (otro agente las define); con respaldo local."""
        try:
            frases = list(voz_id.frases_entrenamiento())
            if frases:
                return frases
        except Exception:
            pass
        return list(_FRASES_DEF)

    def _entrenar_voz(self, nombre, al_terminar=None):
        """Enrola la voz de `nombre` leyendo varias frases en voz alta (en hilo)."""
        def trabajo():
            frases = self._frases_entrenamiento()
            total = len(frases)
            self.imprimir("", f"🎙️ Entrenando la voz de «{nombre}»: lee en voz alta cada frase ({total} en total).", "sys")
            muestras = None
            ok = 0
            for i, frase in enumerate(frases, 1):
                self.imprimir("", f"({i}/{total}) Lee: «{frase}»", "sys")
                res = skills_voz.escuchar(espera_inicio=10.0, fin_silencio=1.2, max_frase=12.0)
                if "audio" not in res:
                    self.imprimir("", f"({i}/{total}) No te escuché — {res.get('error', 'sin audio')}. Sigo con la siguiente.", "sys")
                    continue
                try:
                    r = voz_id.enrolar(nombre, res["audio"], res.get("sr", 16000))
                except Exception as e:
                    self.imprimir("", f"({i}/{total}) ⚠️ No pude registrar la muestra: {e}", "sys")
                    continue
                if isinstance(r, dict) and "error" in r:
                    self.imprimir("", f"({i}/{total}) ⚠️ {r['error']}", "sys")
                    continue
                ok += 1
                if isinstance(r, dict):
                    muestras = r.get("muestras", muestras)
                self.imprimir("", f"({i}/{total}) ✅ Muestra registrada.", "sys")
            resumen = f"🎙️ Entrenamiento de «{nombre}» terminado: {ok}/{total} frases registradas"
            if muestras is not None:
                resumen += f" · {muestras} muestras en total"
            self.imprimir("", resumen + ".", "sys")
            if al_terminar:
                self.root.after(0, al_terminar)
        threading.Thread(target=trabajo, daemon=True).start()

    def _quien_habla(self):
        """Graba una frase y muestra a quién se parece la voz (con su score)."""
        def trabajo():
            self.imprimir("", "🧪 Di una frase para identificar tu voz...", "sys")
            res = skills_voz.escuchar(espera_inicio=8.0, fin_silencio=1.2, max_frase=10.0)
            if "audio" not in res:
                self.imprimir("", f"No te escuché — {res.get('error', 'sin audio')}.", "sys")
                return
            try:
                nombre, score = voz_id.identificar(res["audio"], res.get("sr", 16000))
            except Exception as e:
                self.imprimir("", f"⚠️ No pude identificar la voz: {e}", "sys")
                return
            try:
                sc = f"{float(score):.2f}"
            except Exception:
                sc = str(score)
            if nombre:
                self.imprimir("", f"🧪 Suena a «{nombre}» (score {sc}).", "sys")
            else:
                self.imprimir("", f"🧪 No reconozco esa voz (score {sc}).", "sys")
        threading.Thread(target=trabajo, daemon=True).start()

    def _boton_simple(self, parent, texto, cmd):
        return tk.Button(parent, text=texto, command=cmd, font=FUENTE_B, bg=PANEL, fg=ACENTO,
                         activebackground=ACENTO, activeforeground=PANEL, relief="flat", padx=12, pady=5,
                         cursor="hand2", bd=0, highlightthickness=1, highlightbackground=BORDE)

    def _optionmenu(self, parent, var, opciones):
        om = tk.OptionMenu(parent, var, *opciones)
        om.config(bg=PANEL, fg=TEXTO, font=FUENTE, relief="flat", highlightthickness=1,
                  highlightbackground=BORDE, activebackground=ACENTO, activeforeground=PANEL)
        om["menu"].config(bg=PANEL, fg=TEXTO, font=FUENTE,
                          activebackground=ACENTO, activeforeground=PANEL)
        om.pack(fill="x", padx=16)
        return om

    def _etiqueta_actual(self, idx, opciones):
        for nombre, i in opciones:
            if i == idx:
                return nombre
        return opciones[0][0]


def main():
    # ARREGLO "la ventana se encoge": pyautogui llama SetProcessDpiAware() al
    # importarse, y como se importa DESPUÉS de crear la ventana (p. ej. al
    # ejecutar la acción de volumen), Windows re-escala el proceso a mitad de
    # sesión y la ventana Tk se ENCOGE en pantallas con escala >100%. Al
    # declarar el proceso DPI-aware AQUÍ, antes de tk.Tk(), ya nada cambia.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # per-monitor v1 / system aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()   # respaldo para Windows viejos
        except Exception:
            pass
    root = tk.Tk()
    LaraGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
