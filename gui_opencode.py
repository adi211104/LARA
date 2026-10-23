"""
gui_opencode.py — Pestaña del modo Open Code de LARA (integrada en la ventana).

Ya NO es una ventana aparte: es un panel que la GUI principal muestra como
pestaña (💬 Chat / 💻 Open Code en la cabecera).

- Arriba: chip 📁 con la carpeta de trabajo actual (clic para elegirla) y un
  botón 🗀+ para cambiar a otra carpeta donde trabajará el modelo.
- Lateral: botón "➕ Nueva sesión" y el historial; eliminar es con clic derecho
  sobre una sesión (ya no hay botón de eliminar abajo).
- Debajo de la barra de tarea: selector de MODO que controla la autonomía:
    · Solicitar permisos    -> confirma antes de escribir archivos o ejecutar comandos
    · Aceptar ediciones     -> escribe sin preguntar, confirma solo los comandos
    · Modo de planificación -> solo explora y entrega un plan; no toca nada
    · Modo automático       -> trabaja sola de principio a fin

Colores: TODOS vienen de ui_tema.py (modo claro). Aquí NO se usa VERDE: ese
color queda reservado a las pestañas de la cabecera; el acento local es ACENTO.
"""

import os
import re
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from core.open_code import AgenteCodigo
from core import sesiones
from ui_tema import (FONDO, PANEL, BORDE, TEXTO, SUAVE, ACENTO,
                     FUENTE, FUENTE_B, FUENTE_I)

# Todas las etiquetas en acento; el cuerpo en texto. (Solo 3 colores permitidos.)
ETIQUETA = {
    "usuario": "TÚ", "plan": "LARA planifica", "pensamiento": "LARA piensa",
    "listar": "árbol", "leer": "lee", "escribir": "escribe", "cmd": "terminal",
    "salida": "salida", "confirmar": "permiso", "terminado": "LISTO",
    "error": "ERROR", "sistema": "sistema",
}

# (etiqueta visible, clave interna del agente, descripción corta)
MODOS = [
    ("Solicitar permisos",    "permisos",  "confirma cada archivo y comando"),
    ("Aceptar ediciones",     "ediciones", "escribe libre; confirma comandos"),
    ("Modo de planificación", "plan",      "solo explora y entrega un plan"),
    ("Modo automático",       "auto",      "edita y ejecuta pruebas sin confirmaciones"),
]


class OpenCodePanel(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=FONDO)
        self.carpeta = None
        self.agente = None
        self._eventos = []
        self._cola_ui = queue.Queue()
        self._ocupado = False
        self._sid_cargada = None
        self._construir()
        self._refrescar_sesiones()
        self.after(60, self._vaciar_cola)

    def _construir(self):
        # ── Barra superior: título + chip de carpeta ────────────
        top = tk.Frame(self, bg=FONDO)
        top.pack(fill="x", padx=12, pady=10)
        tk.Label(top, text="💻 OPEN CODE", font=FUENTE_B, fg=ACENTO, bg=FONDO).pack(side="left")
        self.btn_carpeta = tk.Button(top, text="📁 Elegir carpeta…", command=self._elegir_carpeta,
                                     font=FUENTE_B, bg=FONDO, fg=ACENTO, activebackground=ACENTO,
                                     activeforeground=PANEL, relief="flat", padx=12, pady=3,
                                     cursor="hand2", bd=0, highlightthickness=1,
                                     highlightbackground=BORDE)
        self.btn_carpeta.pack(side="left", padx=(14, 4))
        self._boton(top, "🗀＋", self._elegir_carpeta).pack(side="left")
        self.lbl_ruta = tk.Label(top, text="", font=FUENTE_I, fg=SUAVE, bg=FONDO)
        self.lbl_ruta.pack(side="left", padx=10)
        self.ver_detalles = tk.BooleanVar(value=False)
        tk.Checkbutton(top, text='Detalles técnicos', variable=self.ver_detalles,
                       command=self._repintar, bg=FONDO, fg=SUAVE, selectcolor=PANEL,
                       activebackground=FONDO, activeforeground=TEXTO, font=FUENTE).pack(side='right')

        # ── Cuerpo: historial + consola ─────────────────────────
        cuerpo = tk.Frame(self, bg=FONDO)
        cuerpo.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        lateral = tk.Frame(cuerpo, bg=FONDO, width=220, highlightthickness=1,
                           highlightbackground=BORDE)
        lateral.pack(side="left", fill="y")
        lateral.pack_propagate(False)
        self._boton(lateral, "➕ Nueva sesión", self._nueva_sesion).pack(fill="x", padx=8, pady=(6, 2))
        tk.Label(lateral, text="Historial", font=FUENTE, fg=SUAVE, bg=FONDO).pack(anchor="w", padx=10, pady=(6, 2))
        self.lista = tk.Listbox(lateral, bg=PANEL, fg=TEXTO, relief="flat", font=FUENTE,
                                selectbackground=ACENTO, selectforeground=PANEL, activestyle="none",
                                bd=0, highlightthickness=1, highlightbackground=BORDE)
        self.lista.pack(fill="both", expand=True, padx=8, pady=6)
        self.lista.bind("<<ListboxSelect>>", self._abrir_sesion)
        self.lista.bind("<Button-3>", self._menu_sesion)   # clic derecho: eliminar
        self._sesion_ids = []
        self._menu = tk.Menu(self, tearoff=0, bg=PANEL, fg=TEXTO, font=FUENTE,
                             activebackground=ACENTO, activeforeground=PANEL)
        self._menu.add_command(label="🗑  Eliminar sesión", command=self._eliminar_sesion_menu)
        self._menu_idx = None

        self.consola = scrolledtext.ScrolledText(
            cuerpo, wrap="word", font=FUENTE, bg=PANEL, fg=TEXTO, insertbackground=TEXTO,
            relief="flat", padx=12, pady=10, state="disabled", highlightthickness=1,
            highlightbackground=BORDE)
        self.consola.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.consola.tag_config("cuerpo", foreground=TEXTO)
        self.consola.tag_config("etq", foreground=ACENTO, font=FUENTE_B)

        # ── Barra de tarea ──────────────────────────────────────
        abajo = tk.Frame(self, bg=FONDO)
        abajo.pack(fill="x", padx=12, pady=(0, 4))
        self.entrada = tk.Entry(abajo, font=FUENTE, bg=PANEL, fg=TEXTO, insertbackground=ACENTO,
                                relief="flat", highlightthickness=1, highlightbackground=BORDE)
        self.entrada.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        self.entrada.bind("<Return>", lambda e: self._ejecutar())
        self.btn_run = self._boton(abajo, "▶ Ejecutar tarea", self._ejecutar)
        self.btn_run.pack(side="left")
        self._boton(abajo, "⏹ Detener", self._detener).pack(side="left", padx=4)

        # ── Selector de modo (debajo de la barra) ───────────────
        fila_modo = tk.Frame(self, bg=FONDO)
        fila_modo.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(fila_modo, text="Modo", font=FUENTE, fg=SUAVE, bg=FONDO).pack(side="left")
        self.var_modo = tk.StringVar(value="Modo automático")
        om = tk.OptionMenu(fila_modo, self.var_modo, *[m[0] for m in MODOS],
                           command=lambda _v: self._cambiar_modo())
        om.config(bg=FONDO, fg=ACENTO, font=FUENTE_B, relief="flat", highlightthickness=1,
                  highlightbackground=BORDE, activebackground=ACENTO, activeforeground=PANEL,
                  cursor="hand2", bd=0)
        om["menu"].config(bg=PANEL, fg=TEXTO, font=FUENTE,
                          activebackground=ACENTO, activeforeground=PANEL)
        om.pack(side="left", padx=8)
        self.lbl_modo = tk.Label(fila_modo, text=self._descripcion_modo(),
                                 font=FUENTE_I, fg=SUAVE, bg=FONDO)
        self.lbl_modo.pack(side="left", padx=6)

    def _boton(self, parent, texto, cmd):
        return tk.Button(parent, text=texto, command=cmd, font=FUENTE_B, bg=FONDO, fg=ACENTO,
                         activebackground=ACENTO, activeforeground=PANEL, relief="flat",
                         padx=10, pady=6, cursor="hand2", bd=0,
                         highlightthickness=1, highlightbackground=BORDE)

    # ── modos ───────────────────────────────────────────────────
    def _modo_clave(self) -> str:
        for etq, clave, _d in MODOS:
            if etq == self.var_modo.get():
                return clave
        return "auto"

    def _descripcion_modo(self) -> str:
        for etq, _c, desc in MODOS:
            if etq == self.var_modo.get():
                return f"· {desc}"
        return ""

    def _cambiar_modo(self):
        self.lbl_modo.config(text=self._descripcion_modo())
        if self.agente:
            self.agente.modo = self._modo_clave()
        self._log_raw("sistema", f"Modo: {self.var_modo.get()} ({self._descripcion_modo().strip('· ')}).")

    def _confirmar(self, accion: str, detalle: str) -> bool:
        """El agente (en su hilo) pide permiso; la pregunta se muestra en el hilo
        de la interfaz y aquí se espera la respuesta."""
        evento = threading.Event()
        resultado = {"ok": False}
        verbo = "escribir el archivo" if accion == "escribir" else "ejecutar el comando"

        def preguntar():
            resultado["ok"] = messagebox.askyesno(
                "LARA pide permiso", f"¿Autorizas a LARA a {verbo}?\n\n{detalle}", parent=self)
            evento.set()

        self._cola_ui.put(('callback', preguntar))
        evento.wait(timeout=300)
        return resultado["ok"]

    # ── sesiones ────────────────────────────────────────────────
    def _refrescar_sesiones(self):
        self.lista.delete(0, "end")
        self._sesion_ids = []
        for s in sesiones.listar_sesiones("code"):
            self.lista.insert("end", f" {s['titulo'][:24]}")
            self._sesion_ids.append(s["id"])

    def _nueva_sesion(self):
        if self._ocupado:
            return
        self._sid_cargada = None
        self._limpiar_consola()
        if self.carpeta:
            self.agente = AgenteCodigo(self.carpeta, on_log=self._log_safe,
                                       modo=self._modo_clave(), on_confirmar=self._confirmar)
            self._sid_cargada = self.agente.session_id
            self._log_raw("sistema", f"Nueva sesión lista sobre: {self.carpeta}")
        else:
            self._log_raw("sistema", "Nueva sesión: elige una carpeta de trabajo para empezar.")
        self._refrescar_sesiones()

    def _abrir_sesion(self, _evt):
        if self._ocupado:
            return
        sel = self.lista.curselection()
        if not sel:
            return
        sid = self._sesion_ids[sel[0]]
        if sid == self._sid_cargada and self.agente is not None:
            return
        data = sesiones.cargar_sesion("code", sid)
        carpeta = data.get('carpeta')
        if not carpeta and data.get('titulo') == 'Code: Desktop':
            from config import ESCRITORIO
            carpeta = str(ESCRITORIO)
        self._sid_cargada = sid
        self.carpeta = carpeta if carpeta and os.path.isdir(carpeta) else None
        self.agente = (AgenteCodigo(self.carpeta, on_log=self._log_safe, session_id=sid,
                       modo=self._modo_clave(), on_confirmar=self._confirmar) if self.carpeta else None)
        self.btn_carpeta.config(text='📁 ' + (os.path.basename(self.carpeta) if self.carpeta else 'Elegir carpeta…'))
        self.lbl_ruta.config(text=self.carpeta or '')
        self._limpiar_consola()
        self._log_raw("sistema", f"Sesión cargada: {data.get('titulo','')}")
        for t in data.get("turnos", [])[-200:]:
            texto = t['content']
            tipo = 'usuario' if t['role'] == 'user' else 'pensamiento'
            marca = re.match(r'^\[(plan|terminado|leer|listar|escribir|cmd)\]\s*', texto)
            if tipo != 'usuario' and marca:
                tipo = marca[1]
                texto = texto[marca.end():]
            self._log_raw(tipo, texto[:12000])

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
        sesiones.eliminar_sesion("code", self._sesion_ids[self._menu_idx])
        self._menu_idx = None
        self._refrescar_sesiones()

    # ── carpeta y ejecución ─────────────────────────────────────
    def _elegir_carpeta(self):
        if self._ocupado:
            return
        ruta = filedialog.askdirectory(title="Carpeta donde LARA programará")
        if not ruta:
            return
        self.carpeta = ruta
        nombre = os.path.basename(ruta.rstrip("/\\")) or ruta
        self.btn_carpeta.config(text=f"📁 {nombre}")
        self.lbl_ruta.config(text=ruta)
        self.agente = AgenteCodigo(ruta, on_log=self._log_safe,
                                   modo=self._modo_clave(), on_confirmar=self._confirmar,
                                   session_id=self._sid_cargada)
        self._sid_cargada = self.agente.session_id
        self._refrescar_sesiones()
        self._log_raw("sistema", f"Carpeta de trabajo: {ruta}. Escribe una tarea y pulsa Ejecutar.")

    def _ejecutar(self):
        if self._ocupado:
            return
        if not self.agente:
            self._log_raw("error", "Primero elige una carpeta de trabajo.")
            return
        tarea = self.entrada.get().strip()
        if not tarea:
            return
        self.entrada.delete(0, "end")
        self.btn_run.config(state="disabled")
        self._ocupado = True
        agente = self.agente

        def trabajo():
            try:
                agente.ejecutar_tarea(tarea)
            except Exception as e:
                self._log_safe("error", str(e))
            self._cola_ui.put(('fin', ''))

        threading.Thread(target=trabajo, daemon=True).start()

    def _detener(self):
        if self.agente:
            self.agente.detener()

    # ── consola ─────────────────────────────────────────────────
    def _limpiar_consola(self):
        self._eventos = []
        self.consola.config(state="normal")
        self.consola.delete("1.0", "end")
        self.consola.config(state="disabled")

    def _log_raw(self, tipo, texto):
        self._eventos.append((tipo, texto))
        self._insertar_evento(tipo, texto)

    def _repintar(self):
        self.consola.config(state='normal')
        self.consola.delete('1.0', 'end')
        self.consola.config(state='disabled')
        for tipo, texto in self._eventos:
            self._insertar_evento(tipo, texto)

    def _insertar_evento(self, tipo, texto):
        if not self.ver_detalles.get():
            if tipo in ('leer', 'listar', 'cmd', 'salida'):
                return
            if tipo == 'plan':
                texto = 'Estoy organizando los pasos y cómo comprobar el resultado.'
            elif tipo == 'pensamiento':
                texto = 'Estoy trabajando en el siguiente paso…'
            elif tipo == 'escribir':
                texto = 'He guardado los cambios en los archivos de trabajo.'
            elif tipo == 'confirmar':
                texto = 'Necesito tu confirmación para continuar con esta acción.'
            elif tipo in ('terminado', 'error'):
                texto = re.sub(r'```[\s\S]*?```', '[Código disponible en los detalles técnicos]', texto)
        etiqueta = 'Progreso' if tipo in ('pensamiento', 'plan', 'escribir') and not self.ver_detalles.get() else ETIQUETA.get(tipo, tipo)
        self.consola.config(state="normal")
        self.consola.insert("end", f"[{etiqueta}] ", "etq")
        self.consola.insert("end", f"{texto}\n", "cuerpo")
        self.consola.config(state="disabled")
        self.consola.see("end")

    def _log_safe(self, tipo, texto):
        self._cola_ui.put((tipo, texto))

    def _vaciar_cola(self):
        for _ in range(40):
            try:
                tipo, texto = self._cola_ui.get_nowait()
            except queue.Empty:
                break
            if tipo == 'callback':
                texto()
            elif tipo == 'fin':
                self._ocupado = False
                self.btn_run.config(state='normal')
                self._refrescar_sesiones()
            else:
                self._log_raw(tipo, texto)
        self.after(60, self._vaciar_cola)
