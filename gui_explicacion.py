"""
gui_explicacion.py — Motor de explicaciones animadas de LARA en UN solo lienzo.

Por qué: a Adi no le gustaban las diapositivas que se borraban; quiere UNA
escena tipo HUD donde el tema entero se construye en "actos" (ver
core/skills_explicar.py). Cada acto añade elementos al MISMO lienzo mientras
LARA narra; lo dibujado permanece entre actos salvo que el acto pida
"limpiar". Al terminar la narración de un acto entra el siguiente.

Animaciones por elemento:
  - "trazar":  las líneas se dibujan progresivamente de origen a destino.
  - "aparecer": fundido interpolando el color desde el fondo del lienzo.
  - "pulsar":  late 2-3 veces en NARANJA y se asienta en su color final.
  - "mover":   entra y se desliza hasta su "destino".
Primitivas avanzadas: banderas (franjas + emblema estrella/sol/círculo),
personas de palitos con poses, gráficos de barras que crecen con ejes.

Tema CLARO de ui_tema (nada de verde aquí). Controles: ✕ Cerrar, ⏸/▶ pausa
y ↻ repetir; el pie muestra la narración del acto actual. Si el contenido
llega en el formato viejo de "slides", se convierte internamente a actos.
"""

import math
import re
import tkinter as tk

from core import skills_voz
from ui_tema import (FONDO, PANEL, BORDE, TEXTO, SUAVE, ACENTO, ACENTO_2,
                     NARANJA, FUENTE, FUENTE_B, FUENTE_I, FUENTE_T)

FUENTE_P = ("Georgia", 9)   # etiquetas pequeñas (barras, banderas)

_PALETA = {"acento": ACENTO, "naranja": NARANJA, "texto": TEXTO,
           "suave": SUAVE, "acento2": ACENTO_2}


def _hex_a_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_a_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _interp(c1, c2, t):
    a, b = _hex_a_rgb(c1), _hex_a_rgb(c2)
    return _rgb_a_hex([a[i] + (b[i] - a[i]) * t for i in range(3)])


class PanelExplicacion(tk.Frame):
    """Panel que anima la explicación por actos sobre un único lienzo."""

    def __init__(self, master, on_cerrar=None):
        super().__init__(master, bg=FONDO)
        self.on_cerrar = on_cerrar
        self.contenido = None
        self.actos = []
        self.idx = 0
        self._token = 0
        self._anim_ids = []      # animaciones de elementos (cancelables aparte)
        self._auto_id = None     # temporizador de avance de acto
        self._narrando = False
        self._pausado = False
        self._mostrados = []     # elementos ya en escena (para redibujar final)
        self._redim_id = None

        # ── Barra superior de controles ──
        top = tk.Frame(self, bg=FONDO)
        top.pack(fill="x", padx=10, pady=8)

        def _boton(padre, txt, cmd):
            return tk.Button(padre, text=txt, command=cmd, font=FUENTE_B,
                             bg=FONDO, fg=ACENTO, activebackground=ACENTO,
                             activeforeground=PANEL, relief="flat",
                             cursor="hand2", bd=0)

        _boton(top, "✕ Cerrar", self._cerrar).pack(side="left")
        self.lbl_titulo = tk.Label(top, text="", font=FUENTE_B, fg=TEXTO, bg=FONDO)
        self.lbl_titulo.pack(side="left", padx=14)
        self.btn_repetir = _boton(top, "↻ repetir", self._repetir)
        self.btn_repetir.pack(side="right", padx=2)
        self.btn_pausa = _boton(top, "⏸", self._toggle_pausa)
        self.btn_pausa.pack(side="right", padx=2)
        self.lbl_pos = tk.Label(top, text="", font=FUENTE, fg=SUAVE, bg=FONDO)
        self.lbl_pos.pack(side="right", padx=8)

        # ── Lienzo único ──
        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=1,
                                highlightbackground=BORDE)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.canvas.bind("<Configure>", self._al_redimensionar)

        # ── Pie: narración del acto actual ──
        self.lbl_caption = tk.Label(self, text="", font=FUENTE_I, fg=SUAVE,
                                    bg=FONDO, wraplength=860, justify="left")
        self.lbl_caption.pack(fill="x", padx=16, pady=(0, 12))

    # ═══════════════ API pública ═══════════════
    def mostrar(self, contenido: dict):
        """Arranca la escena. Acepta el formato de actos o el viejo de slides."""
        self.contenido = contenido
        self._iniciar()

    def _cancelar_pendientes(self):
        """Invalida todo lo programado (lo llama también gui.py al ocultar)."""
        self._token += 1
        self._narrando = False
        self._cancelar_anims()
        self._cancelar_auto()

    # ═══════════════ ciclo de vida ═══════════════
    def _iniciar(self):
        self._cancelar_pendientes()
        skills_voz.detener_voz()
        contenido = self.contenido or {}
        actos = contenido.get("actos")
        if not actos and contenido.get("slides"):
            actos = self._slides_a_actos(contenido)
        self.actos = actos or []
        self.idx = 0
        self._pausado = False
        self.btn_pausa.config(text="⏸")
        self._mostrados = []
        self.lbl_titulo.config(text=str(contenido.get("titulo", "")).upper())
        self.canvas.delete("all")
        self._marco_hud()
        if self.actos:
            self._ejecutar_acto(0, con_intro=True)

    def _cerrar(self):
        self._cancelar_pendientes()
        skills_voz.detener_voz()
        if self.on_cerrar:
            self.on_cerrar()

    def _repetir(self):
        if self.contenido:
            self._iniciar()

    def _toggle_pausa(self):
        if not self.actos:
            return
        if not self._pausado:
            # Pausa: corta voz y animaciones, deja la escena en su estado final.
            self._pausado = True
            self.btn_pausa.config(text="▶")
            self._narrando = False
            skills_voz.detener_voz()
            self._cancelar_anims()
            self._cancelar_auto()
            self._redibujar_final()
        else:
            # Reanuda: vuelve a narrar el acto actual (ya dibujado) y sigue.
            self._pausado = False
            self.btn_pausa.config(text="⏸")
            token = self._token
            acto = self.actos[self.idx]
            self._narrar_acto(acto.get("narracion", ""), token,
                              n_elem=len(acto.get("elementos", [])))

    # ── temporizadores ──
    def _after(self, ms, fn):
        aid = self.after(ms, fn)
        self._anim_ids.append(aid)
        return aid

    def _cancelar_anims(self):
        for aid in self._anim_ids:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._anim_ids = []

    def _cancelar_auto(self):
        if self._auto_id:
            try:
                self.after_cancel(self._auto_id)
            except Exception:
                pass
            self._auto_id = None

    # ═══════════════ actos ═══════════════
    def _ejecutar_acto(self, i, con_intro=False):
        token = self._token
        self.idx = i
        acto = self.actos[i]
        self.lbl_pos.config(text=f"acto {i + 1}/{len(self.actos)}")

        if acto.get("limpiar"):
            self._cancelar_anims()
            self.canvas.delete("all")
            self._marco_hud()
            self._mostrados = []

        elementos = acto.get("elementos", [])
        # Se registran YA como mostrados para que un redimensionado pinte su
        # estado final aunque el dibujo aún no haya arrancado.
        self._mostrados.extend(elementos)

        narr = acto.get("narracion", "")
        if con_intro:
            intro = str((self.contenido or {}).get("narracion_intro", "")).strip()
            if intro:
                narr = (intro + " " + narr).strip()
        self.lbl_caption.config(text=narr)

        def _dibujar():
            # Arranca el dibujo escalonado justo cuando la voz EMPIEZA a sonar
            # (no cuando se la mandó a hablar: edge-tts tarda en sintetizar y
            # antes el dibujo iba adelantado a la narración).
            for k, el in enumerate(elementos):
                self._after(140 + k * 240,
                            lambda el=el, t=token: self._animar_elemento(el, t))
        self._narrar_acto(narr, token, n_elem=len(elementos), al_empezar=_dibujar)

    def _narrar_acto(self, narr, token, n_elem=0, al_empezar=None):
        """Sincroniza el acto con la voz (o con un temporizador si no hay voz)."""
        anim_ms = 900 + n_elem * 240   # que el dibujo alcance a terminar

        def _empezar():
            if token != self._token or self._pausado:
                return
            self._pulso_narracion(True)
            if al_empezar:
                al_empezar()

        if skills_voz.VOZ_ACTIVA and narr:
            # on_start/on_done llegan desde el hilo de voz: saltamos al de Tk.
            # on_start dispara justo cuando el audio EMPIEZA a sonar de verdad
            # (tras sintetizarlo), así el dibujo y la voz arrancan juntos.
            skills_voz.hablar(
                narr,
                on_start=lambda: self.after(0, _empezar),
                on_done=lambda: self.after(80, lambda: self._fin_narracion(token)))
        else:
            _empezar()
            palabras = len(narr.split())
            ms = max(2600, palabras * 330, anim_ms + 800)
            self._auto_id = self.after(ms, lambda: self._fin_narracion(token))

    def _fin_narracion(self, token):
        if token != self._token or self._pausado:
            return
        self._narrando = False
        if self.idx < len(self.actos) - 1:
            self._auto_id = self.after(
                450, lambda: (self._ejecutar_acto(self.idx + 1)
                              if token == self._token and not self._pausado else None))
        else:
            self.canvas.delete("pulso")
            self.lbl_pos.config(text=f"acto {len(self.actos)}/{len(self.actos)} ✓")

    # ── pulso "narrando" (puntito naranja que late en la esquina) ──
    def _pulso_narracion(self, activo: bool):
        self._narrando = activo
        if activo:
            self._animar_pulso(0)

    def _animar_pulso(self, fase):
        if not self._narrando or self._pausado:
            self.canvas.delete("pulso")
            return
        self.canvas.delete("pulso")
        W, H = self._dim()
        r = 4 + 2.5 * (1 + math.sin(fase * 0.55)) / 2
        self.canvas.create_oval(W - 26 - r, H - 24 - r, W - 26 + r, H - 24 + r,
                                outline=NARANJA, width=2, tags="pulso")
        self._after(90, lambda: self._animar_pulso(fase + 1))

    # ═══════════════ redimensionado ═══════════════
    def _al_redimensionar(self, _evt):
        if not self.actos or not self._mostrados:
            return
        if self._redim_id:
            try:
                self.after_cancel(self._redim_id)
            except Exception:
                pass
        self._redim_id = self.after(140, self._redibujar_final)

    def _redibujar_final(self):
        """Redibuja el estado FINAL de todo lo mostrado (sin animar de nuevo)."""
        self._redim_id = None
        self._cancelar_anims()
        self.canvas.delete("all")
        self._marco_hud()
        W, H = self._dim()
        for el in self._mostrados:
            try:
                acc = self._construir(el, W, H)
                self._aplicar_final(acc)
            except Exception:
                pass

    def _dim(self):
        self.update_idletasks()
        W = max(self.canvas.winfo_width(), 640)
        H = max(self.canvas.winfo_height(), 380)
        return W, H

    def _marco_hud(self):
        """Fondo tipo HUD: rejilla tenue + arcos de radar + esquinas decorativas."""
        cv = self.canvas
        W, H = self._dim()

        # Rejilla tenue (textura de interfaz, no distrae del contenido)
        paso = max(46, int(min(W, H) / 9))
        for gx in range(paso, W, paso):
            cv.create_line(gx, 0, gx, H, fill=BORDE, width=1, stipple="gray25", tags="hudfondo")
        for gy in range(paso, H, paso):
            cv.create_line(0, gy, W, gy, fill=BORDE, width=1, stipple="gray25", tags="hudfondo")

        # Arcos de "radar" en dos esquinas opuestas
        for (cx, cy, a0) in ((26, H - 26, 0), (W - 26, 26, 180)):
            for rr in (14, 24):
                cv.create_arc(cx - rr, cy - rr, cx + rr, cy + rr, start=a0, extent=90,
                             style="arc", outline=ACENTO_2, width=1, tags="hudfondo")

        cv.tag_lower("hudfondo")

        L = 22
        for (cx, cy, dx, dy) in [(10, 10, 1, 1), (W - 10, 10, -1, 1),
                                 (10, H - 10, 1, -1), (W - 10, H - 10, -1, -1)]:
            cv.create_line(cx, cy, cx + dx * L, cy, fill=ACENTO_2, width=2)
            cv.create_line(cx, cy, cx, cy + dy * L, fill=ACENTO_2, width=2)

    # ═══════════════ motor de animación por elemento ═══════════════
    def _color(self, c, defecto=ACENTO):
        if not c:
            return defecto
        c = str(c).strip().lower()
        if c in _PALETA:
            return _PALETA[c]
        if re.fullmatch(r"#[0-9a-f]{6}", c):
            return c
        return defecto

    def _animar_elemento(self, el, token):
        if token != self._token:
            return
        W, H = self._dim()
        try:
            acc = self._construir(el, W, H)
        except Exception:
            return
        anim = el.get("anim") or ("trazar" if el.get("tipo") in ("linea", "flecha")
                                  else "aparecer")
        self._ejecutar_anim(acc, anim, el, token, W, H)

    def _ejecutar_anim(self, acc, anim, el, token, W, H):
        """Fase 1: revelado (fundidos, trazos y barras que crecen) en paralelo.
        Fase 2 según anim: pulso naranja o deslizamiento hasta 'destino'."""
        pasos = 8
        fades = [a for a in acc if a[0] == "fade"]
        trazos = [a for a in acc if a[0] == "trazo"]
        creces = [a for a in acc if a[0] == "crece"]
        cv = self.canvas

        def paso(p=1):
            if token != self._token:
                return
            t = p / pasos
            for _, cid, prop, col in fades:
                cv.itemconfig(cid, **{prop: _interp(PANEL, col, t)})
            for _, cid, x1, y1, x2, y2, col in trazos:
                cv.coords(cid, x1, y1, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
                cv.itemconfig(cid, fill=_interp(PANEL, col, min(1.0, t * 1.6)))
            for _, cid, x1, yb, x2, yt, col in creces:
                cv.coords(cid, x1, yb + (yt - yb) * t, x2, yb)
                c = _interp(PANEL, col, t)
                cv.itemconfig(cid, fill=c, outline=c)
            if p < pasos:
                self._after(30, lambda: paso(p + 1))
            else:
                despues()

        def despues():
            if anim == "pulsar" and fades:
                self._fase_pulsar(fades, token)
            elif anim == "mover" and el.get("destino"):
                ox = el.get("x", el.get("x1", 50)) / 100.0 * W
                oy = el.get("y", el.get("y1", 50)) / 100.0 * H
                dx = el["destino"][0] / 100.0 * W - ox
                dy = el["destino"][1] / 100.0 * H - oy
                ids = sorted({a[1] for a in acc})
                self._fase_mover(ids, dx, dy, token)

        paso()

    def _fase_pulsar(self, fades, token, k=0):
        """Late 3 veces hacia NARANJA y se asienta en el color final."""
        if token != self._token:
            return
        total = 18
        s = abs(math.sin(k * math.pi / 6.0))
        for _, cid, prop, col in fades:
            try:
                self.canvas.itemconfig(cid, **{prop: _interp(col, NARANJA, s)})
            except Exception:
                pass
        if k < total:
            self._after(60, lambda: self._fase_pulsar(fades, token, k + 1))

    def _fase_mover(self, ids, dx, dy, token, p=0, pasos=14):
        """Desliza el conjunto de ítems hasta el destino en pasos suaves."""
        if token != self._token or p >= pasos:
            return
        for cid in ids:
            try:
                self.canvas.move(cid, dx / pasos, dy / pasos)
            except Exception:
                pass
        self._after(28, lambda: self._fase_mover(ids, dx, dy, token, p + 1, pasos))

    def _aplicar_final(self, acc):
        """Fija el estado final de un elemento (para redibujos sin animación)."""
        cv = self.canvas
        for a in acc:
            if a[0] == "fade":
                cv.itemconfig(a[1], **{a[2]: a[3]})
            elif a[0] == "trazo":
                cv.coords(a[1], a[2], a[3], a[4], a[5])
                cv.itemconfig(a[1], fill=a[6])
            elif a[0] == "crece":
                cv.coords(a[1], a[2], a[5], a[4], a[3])
                cv.itemconfig(a[1], fill=a[6], outline=a[6])

    # ═══════════════ constructores de elementos ═══════════════
    # Cada constructor CREA los ítems "ocultos" (color = PANEL) y devuelve la
    # lista de acciones que el motor anima o fija:
    #   ("fade", id, propiedad, color_final)
    #   ("trazo", id, x1, y1, x2, y2, color_final)
    #   ("crece", id, x1, y_base, x2, y_tope, color_final)
    def _construir(self, el, W, H):
        t = el.get("tipo")
        X = lambda v: float(v) / 100.0 * W
        Y = lambda v: float(v) / 100.0 * H
        cv = self.canvas
        acc = []

        if t == "titulo":
            col = self._color(el.get("color"), ACENTO)
            it = cv.create_text(X(el.get("x", 50)), Y(el.get("y", 7)),
                                text=el.get("texto", ""), fill=PANEL,
                                font=FUENTE_T, width=W * 0.86, justify="center")
            acc.append(("fade", it, "fill", col))

        elif t == "texto":
            col = self._color(el.get("color"), TEXTO)
            it = cv.create_text(X(el.get("x", 50)), Y(el.get("y", 50)),
                                text=el.get("texto", ""), fill=PANEL,
                                font=FUENTE, width=W * 0.42, justify="center")
            acc.append(("fade", it, "fill", col))

        elif t == "caja":
            self._el_caja(el, X, Y, W, acc)

        elif t == "circulo":
            col = self._color(el.get("color"), ACENTO)
            r = float(el.get("r", 5)) / 100.0 * min(W, H)
            cx, cy = X(el.get("x", 50)), Y(el.get("y", 50))
            if el.get("relleno"):
                it = cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", col))
                acc.append(("fade", it, "outline", col))
            else:
                it = cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                                    outline=PANEL, width=2)
                acc.append(("fade", it, "outline", col))

        elif t in ("linea", "flecha"):
            col = self._color(el.get("color"), ACENTO)
            x1, y1 = X(el.get("x1", 20)), Y(el.get("y1", 50))
            x2, y2 = X(el.get("x2", 80)), Y(el.get("y2", 50))
            it = cv.create_line(x1, y1, x1, y1, fill=PANEL, width=2,
                                arrow="last" if t == "flecha" else "none")
            acc.append(("trazo", it, x1, y1, x2, y2, col))

        elif t == "poligono":
            col = self._color(el.get("color"), ACENTO)
            pts = []
            for p in el.get("puntos", []):
                pts += [X(p[0]), Y(p[1])]
            if len(pts) >= 6:
                if el.get("relleno", True):
                    it = cv.create_polygon(pts, fill=PANEL, outline=PANEL)
                    acc.append(("fade", it, "fill", col))
                    acc.append(("fade", it, "outline", col))
                else:
                    it = cv.create_polygon(pts, fill="", outline=PANEL, width=2)
                    acc.append(("fade", it, "outline", col))

        elif t == "barra":
            self._el_barra(el, X, Y, acc)

        elif t == "bandera":
            self._el_bandera(el, X, Y, acc)

        elif t == "persona":
            self._el_persona(el, X, Y, W, H, acc)

        elif t == "conector":
            self._el_conector(el, X, Y, acc)

        elif t == "icono":
            self._el_icono(el, X, Y, W, H, acc)

        elif t == "anillo":
            self._el_anillo(el, X, Y, W, H, acc)

        elif t == "grafico_linea":
            self._el_grafico_linea(el, X, Y, acc)

        return acc

    def _el_conector(self, el, X, Y, acc):
        """Conector curvo tipo circuito/HUD entre dos puntos (spline de 3 puntos)."""
        cv = self.canvas
        col = self._color(el.get("color"), ACENTO_2)
        x1, y1 = X(el.get("x1", 20)), Y(el.get("y1", 50))
        x2, y2 = X(el.get("x2", 80)), Y(el.get("y2", 50))
        curv = float(el.get("curvatura", 0.35) or 0.0)
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy) or 1.0
        px, py = -dy / dist, dx / dist  # perpendicular unitario
        offset = curv * dist * 0.35
        mx, my = (x1 + x2) / 2.0 + px * offset, (y1 + y2) / 2.0 + py * offset
        it = cv.create_line(x1, y1, mx, my, x2, y2, fill=PANEL, width=2,
                            smooth=True, splinesteps=24)
        acc.append(("fade", it, "fill", col))

    def _el_anillo(self, el, X, Y, W, H, acc):
        """Aro decorativo tipo radar HUD (puramente estético)."""
        cv = self.canvas
        col = self._color(el.get("color"), ACENTO_2)
        r = float(el.get("r", 15)) / 100.0 * min(W, H)
        grosor = max(1, float(el.get("grosor", 2)))
        cx, cy = X(el.get("x", 50)), Y(el.get("y", 50))
        it = cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=PANEL, width=grosor)
        acc.append(("fade", it, "outline", col))

    def _el_grafico_linea(self, el, X, Y, acc):
        """Gráfico de tendencia: ejes trazados + polilínea + puntos que aparecen."""
        cv = self.canvas
        x, y = X(el.get("x", 20)), Y(el.get("y", 40))
        w, h = X(el.get("w", 40)), Y(el.get("h", 25))
        vals = el.get("valores") or [0.0, 0.0]
        etiq = el.get("etiquetas") or []
        n = len(vals)
        vmin, vmax = min(vals), max(vals)
        rango = (vmax - vmin) or 1.0
        base = y + h
        col = self._color(el.get("color"), ACENTO)

        ej1 = cv.create_line(x, base, x, base, fill=PANEL, width=2)
        acc.append(("trazo", ej1, x, base, x, y, SUAVE))
        ej2 = cv.create_line(x, base, x, base, fill=PANEL, width=2)
        acc.append(("trazo", ej2, x, base, x + w, base, SUAVE))

        pts = []
        for i, v in enumerate(vals):
            px = x + w * (i / max(1, n - 1))
            py = base - ((v - vmin) / rango) * (h * 0.86)
            pts.append((px, py))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            it = cv.create_line(x1, y1, x1, y1, fill=PANEL, width=2)
            acc.append(("trazo", it, x1, y1, x2, y2, col))
        for i, (px, py) in enumerate(pts):
            d = cv.create_oval(px - 3, py - 3, px + 3, py + 3, fill=PANEL, outline=PANEL)
            acc.append(("fade", d, "fill", col))
            acc.append(("fade", d, "outline", col))
            if i < len(etiq) and etiq[i]:
                te = cv.create_text(px, base + 11, text=etiq[i], fill=PANEL, font=FUENTE_P)
                acc.append(("fade", te, "fill", SUAVE))

    def _el_icono(self, el, X, Y, W, H, acc):
        """Glifo vectorial pequeño (chispa, check, cruz, reloj, candado, engranaje,
        rayo, objetivo, flecha_ciclo, escudo) para reforzar una idea junto a un texto."""
        cv = self.canvas
        col = self._color(el.get("color"), ACENTO)
        s = float(el.get("tam", 5)) / 100.0 * min(W, H)
        cx, cy = X(el.get("x", 50)), Y(el.get("y", 50))
        forma = el.get("forma", "chispa")

        def trazo(x1, y1, x2, y2, ancho=2.2):
            it = cv.create_line(x1, y1, x1, y1, fill=PANEL, width=ancho, capstyle="round")
            acc.append(("trazo", it, x1, y1, x2, y2, col))

        def oval(x1, y1, x2, y2, relleno=False, ancho=2):
            if relleno:
                it = cv.create_oval(x1, y1, x2, y2, fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", col))
                acc.append(("fade", it, "outline", col))
            else:
                it = cv.create_oval(x1, y1, x2, y2, outline=PANEL, width=ancho)
                acc.append(("fade", it, "outline", col))

        def poligono(pts, relleno=True):
            if relleno:
                it = cv.create_polygon(pts, fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", col))
                acc.append(("fade", it, "outline", col))
            else:
                it = cv.create_polygon(pts, fill="", outline=PANEL, width=2)
                acc.append(("fade", it, "outline", col))

        if forma == "check":
            trazo(cx - s * 0.6, cy, cx - s * 0.15, cy + s * 0.5, 3)
            trazo(cx - s * 0.15, cy + s * 0.5, cx + s * 0.65, cy - s * 0.55, 3)
        elif forma == "cruz":
            trazo(cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55, 3)
            trazo(cx - s * 0.55, cy + s * 0.55, cx + s * 0.55, cy - s * 0.55, 3)
        elif forma == "reloj":
            oval(cx - s, cy - s, cx + s, cy + s)
            trazo(cx, cy, cx, cy - s * 0.65, 2)
            trazo(cx, cy, cx + s * 0.45, cy + s * 0.2, 2)
        elif forma == "candado":
            cuerpo = cv.create_rectangle(cx - s * 0.55, cy - s * 0.05, cx + s * 0.55,
                                         cy + s * 0.75, fill=PANEL, outline=PANEL)
            acc.append(("fade", cuerpo, "fill", col))
            acc.append(("fade", cuerpo, "outline", col))
            arco = cv.create_arc(cx - s * 0.4, cy - s * 0.85, cx + s * 0.4, cy - s * 0.05,
                                 start=0, extent=180, style="arc", outline=PANEL, width=3)
            acc.append(("fade", arco, "outline", col))
        elif forma == "engranaje":
            oval(cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55)
            for k in range(8):
                a = k * math.pi / 4.0
                trazo(cx + s * 0.5 * math.cos(a), cy + s * 0.5 * math.sin(a),
                      cx + s * 0.85 * math.cos(a), cy + s * 0.85 * math.sin(a), 2.5)
            oval(cx - s * 0.18, cy - s * 0.18, cx + s * 0.18, cy + s * 0.18, relleno=True)
        elif forma == "rayo":
            pts = [cx + s * 0.15, cy - s, cx - s * 0.45, cy + s * 0.15,
                   cx, cy + s * 0.15, cx - s * 0.15, cy + s,
                   cx + s * 0.5, cy - s * 0.15, cx, cy - s * 0.15]
            poligono(pts, relleno=True)
        elif forma == "objetivo":
            oval(cx - s, cy - s, cx + s, cy + s)
            oval(cx - s * 0.55, cy - s * 0.55, cx + s * 0.55, cy + s * 0.55)
            oval(cx - s * 0.15, cy - s * 0.15, cx + s * 0.15, cy + s * 0.15, relleno=True)
        elif forma == "flecha_ciclo":
            arco = cv.create_arc(cx - s, cy - s, cx + s, cy + s, start=25, extent=280,
                                 style="arc", outline=PANEL, width=3)
            acc.append(("fade", arco, "outline", col))
            ang = math.radians(25)
            ax, ay = cx + s * math.cos(ang), cy - s * math.sin(ang)
            poligono([ax, ay - s * 0.22, ax + s * 0.3, ay, ax - s * 0.02, ay + s * 0.28],
                     relleno=True)
        elif forma == "escudo":
            pts = [cx, cy - s, cx + s * 0.7, cy - s * 0.55, cx + s * 0.7, cy + s * 0.15,
                   cx, cy + s, cx - s * 0.7, cy + s * 0.15, cx - s * 0.7, cy - s * 0.55]
            poligono(pts, relleno=False)
        else:   # chispa (por defecto)
            for k in range(4):
                a = k * math.pi / 2.0 + math.pi / 4.0
                trazo(cx, cy, cx + s * math.cos(a), cy + s * math.sin(a))
            oval(cx - s * 0.22, cy - s * 0.22, cx + s * 0.22, cy + s * 0.22, relleno=True)

    def _el_caja(self, el, X, Y, W, acc):
        cv = self.canvas
        col = self._color(el.get("color"), ACENTO)
        x, y = X(el.get("x", 30)), Y(el.get("y", 40))
        w, h = X(el.get("w", 30)), Y(el.get("h", 12))
        rect = cv.create_rectangle(x, y, x + w, y + h, outline=PANEL, width=2)
        acc.append(("fade", rect, "outline", col))
        e = 8   # esquinas HUD decorativas
        for (cx, cy, dx, dy) in [(x, y, 1, 1), (x + w, y, -1, 1),
                                 (x, y + h, 1, -1), (x + w, y + h, -1, -1)]:
            l1 = cv.create_line(cx, cy, cx + dx * e, cy, fill=PANEL, width=3)
            l2 = cv.create_line(cx, cy, cx, cy + dy * e, fill=PANEL, width=3)
            acc.append(("fade", l1, "fill", col))
            acc.append(("fade", l2, "fill", col))
        txt = cv.create_text(x + w / 2, y + h / 2, text=el.get("texto", ""),
                             fill=PANEL, font=FUENTE, width=max(20, w - 16),
                             justify="center")
        acc.append(("fade", txt, "fill", TEXTO))

    def _el_barra(self, el, X, Y, acc):
        """Mini gráfico de barras: ejes que se trazan y barras que CRECEN."""
        cv = self.canvas
        x, y = X(el.get("x", 20)), Y(el.get("y", 40))
        w, h = X(el.get("w", 30)), Y(el.get("h", 30))
        vals = el.get("valores") or [1]
        etiq = el.get("etiquetas") or []
        n = len(vals)
        vmax = max(vals) or 1.0
        base = y + h
        # ejes (izquierdo y base) trazados en gris azulado
        ej1 = cv.create_line(x, base, x, base, fill=PANEL, width=2)
        acc.append(("trazo", ej1, x, base, x, y, SUAVE))
        ej2 = cv.create_line(x, base, x, base, fill=PANEL, width=2)
        acc.append(("trazo", ej2, x, base, x + w, base, SUAVE))
        bw = w / (n * 1.6)
        col_base = self._color(el.get("color"), ACENTO)
        for i, v in enumerate(vals):
            cx = x + w * (i + 0.5) / n
            alto = (v / vmax) * (h * 0.86)
            col = NARANJA if v == vmax and n > 1 else col_base
            rect = cv.create_rectangle(cx - bw / 2, base, cx + bw / 2, base,
                                       fill=PANEL, outline=PANEL)
            acc.append(("crece", rect, cx - bw / 2, base, cx + bw / 2,
                        base - alto, col))
            # valor encima de la barra
            vtxt = ("%g" % round(v, 1))
            tv = cv.create_text(cx, base - alto - 9, text=vtxt, fill=PANEL,
                                font=FUENTE_P)
            acc.append(("fade", tv, "fill", TEXTO))
            if i < len(etiq) and etiq[i]:
                te = cv.create_text(cx, base + 11, text=etiq[i], fill=PANEL,
                                    font=FUENTE_P)
                acc.append(("fade", te, "fill", SUAVE))

    def _el_bandera(self, el, X, Y, acc):
        """Bandera: rectángulo base + franjas + emblema centrado opcional."""
        cv = self.canvas
        x, y = X(el.get("x", 40)), Y(el.get("y", 30))
        w, h = X(el.get("w", 20)), Y(el.get("h", 12))
        franjas = el.get("franjas") or [{"color": "#9aa7b1", "orientacion": "v"}]
        n = len(franjas)
        orient = franjas[0].get("orientacion", "v")
        for i, f in enumerate(franjas):
            col = self._color(f.get("color"), ACENTO_2)
            if orient == "v":
                r = cv.create_rectangle(x + i * w / n, y, x + (i + 1) * w / n,
                                        y + h, fill=PANEL, outline=PANEL)
            else:
                r = cv.create_rectangle(x, y + i * h / n, x + w,
                                        y + (i + 1) * h / n, fill=PANEL,
                                        outline=PANEL)
            acc.append(("fade", r, "fill", col))
            acc.append(("fade", r, "outline", col))
        marco = cv.create_rectangle(x, y, x + w, y + h, outline=PANEL, width=1)
        acc.append(("fade", marco, "outline", SUAVE))
        emb = el.get("emblema")
        if isinstance(emb, dict):
            ecol = self._color(emb.get("color"), "#d4a017")
            cx, cy = x + w / 2, y + h / 2
            r = min(w, h) * 0.30
            forma = emb.get("forma", "circulo")
            if forma == "estrella":
                it = cv.create_polygon(self._pts_estrella(cx, cy, r),
                                       fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", ecol))
                acc.append(("fade", it, "outline", ecol))
            elif forma == "sol":
                rc = r * 0.62
                it = cv.create_oval(cx - rc, cy - rc, cx + rc, cy + rc,
                                    fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", ecol))
                acc.append(("fade", it, "outline", ecol))
                for k in range(10):
                    a = k * math.pi / 5.0
                    ln = cv.create_line(cx + rc * 1.15 * math.cos(a),
                                        cy + rc * 1.15 * math.sin(a),
                                        cx + r * 1.25 * math.cos(a),
                                        cy + r * 1.25 * math.sin(a),
                                        fill=PANEL, width=2)
                    acc.append(("fade", ln, "fill", ecol))
            else:   # circulo
                it = cv.create_oval(cx - r * 0.7, cy - r * 0.7,
                                    cx + r * 0.7, cy + r * 0.7,
                                    fill=PANEL, outline=PANEL)
                acc.append(("fade", it, "fill", ecol))
                acc.append(("fade", it, "outline", ecol))

    @staticmethod
    def _pts_estrella(cx, cy, r):
        """Estrella de 5 puntas (10 vértices alternando radio externo/interno)."""
        pts = []
        for i in range(10):
            rr = r if i % 2 == 0 else r * 0.42
            a = -math.pi / 2 + i * math.pi / 5.0
            pts += [cx + rr * math.cos(a), cy + rr * math.sin(a)]
        return pts

    def _el_persona(self, el, X, Y, W, H, acc):
        """Figura de palitos: cabeza-círculo y extremidades según la pose."""
        cv = self.canvas
        col = self._color(el.get("color"), TEXTO)
        cx, cy = X(el.get("x", 50)), Y(el.get("y", 55))
        u = max(9.0, 0.045 * H * float(el.get("escala", 1.0)))
        pose = el.get("pose", "parado")

        cabeza = cv.create_oval(cx - 0.45 * u, cy - 1.65 * u,
                                cx + 0.45 * u, cy - 0.75 * u,
                                outline=PANEL, width=3)
        acc.append(("fade", cabeza, "outline", col))

        def seg(x1, y1, x2, y2):
            ln = cv.create_line(x1, y1, x1, y1, fill=PANEL, width=3,
                                capstyle="round")
            acc.append(("trazo", ln, x1, y1, x2, y2, col))

        hombro, pelvis = cy - 0.5 * u, cy + 0.55 * u
        if pose == "corriendo":
            seg(cx - 0.15 * u, cy - 0.75 * u, cx + 0.15 * u, pelvis)      # tronco
            seg(cx, cy - 0.45 * u, cx + 0.75 * u, cy - 0.75 * u)          # brazo delante
            seg(cx, cy - 0.45 * u, cx - 0.75 * u, cy - 0.1 * u)           # brazo detrás
            seg(cx + 0.15 * u, pelvis, cx + 0.85 * u, cy + 1.25 * u)      # pierna delante
            seg(cx + 0.15 * u, pelvis, cx - 0.85 * u, cy + 1.45 * u)      # pierna detrás
        elif pose == "senalando":
            seg(cx, cy - 0.75 * u, cx, pelvis)
            seg(cx, hombro, cx + 1.05 * u, cy - 0.6 * u)                  # brazo que señala
            seg(cx, hombro, cx - 0.45 * u, cy + 0.1 * u)
            seg(cx, pelvis, cx - 0.4 * u, cy + 1.6 * u)
            seg(cx, pelvis, cx + 0.4 * u, cy + 1.6 * u)
        elif pose == "pateando":
            seg(cx, cy - 0.75 * u, cx, pelvis)
            seg(cx, hombro, cx + 0.75 * u, cy - 0.25 * u)
            seg(cx, hombro, cx - 0.75 * u, cy - 0.25 * u)
            seg(cx, pelvis, cx + 1.15 * u, cy + 0.35 * u)                 # patada
            seg(cx, pelvis, cx - 0.3 * u, cy + 1.6 * u)
        elif pose == "sentado":
            seg(cx, cy - 0.75 * u, cx, cy + 0.35 * u)
            seg(cx, cy + 0.35 * u, cx + 0.75 * u, cy + 0.35 * u)          # muslo
            seg(cx + 0.75 * u, cy + 0.35 * u, cx + 0.75 * u, cy + 1.2 * u)  # canilla
            seg(cx, cy - 0.45 * u, cx + 0.55 * u, cy - 0.05 * u)
            seg(cx, cy - 0.45 * u, cx + 0.5 * u, cy + 0.05 * u)
        else:   # parado
            seg(cx, cy - 0.75 * u, cx, pelvis)
            seg(cx, hombro, cx + 0.65 * u, cy + 0.05 * u)
            seg(cx, hombro, cx - 0.65 * u, cy + 0.05 * u)
            seg(cx, pelvis, cx - 0.4 * u, cy + 1.6 * u)
            seg(cx, pelvis, cx + 0.4 * u, cy + 1.6 * u)

    # ═══════════════ compatibilidad con el formato viejo ═══════════════
    @staticmethod
    def _slides_a_actos(contenido):
        """Convierte 'slides' (título + items + tipo) a actos simples."""
        actos = []
        for s in contenido.get("slides", []):
            elementos = [{"tipo": "titulo", "x": 50, "y": 8,
                          "texto": s.get("titulo", "")}]
            items = s.get("items", [])
            n = max(len(items), 1)
            y0, y1 = 20.0, 92.0
            paso = (y1 - y0) / n
            for k, it in enumerate(items):
                yc = y0 + paso * k + paso / 2
                alto = min(12.0, paso * 0.62)
                elementos.append({"tipo": "caja", "x": 25.0, "y": yc - alto / 2,
                                  "w": 50.0, "h": alto, "texto": str(it)})
                if s.get("tipo") == "flujo" and k < n - 1:
                    elementos.append({"tipo": "flecha", "x1": 50.0,
                                      "y1": yc + alto / 2 + 1,
                                      "x2": 50.0, "y2": yc + paso - alto / 2 - 1,
                                      "color": "suave"})
            actos.append({"narracion": s.get("narracion", ""),
                          "limpiar": True, "elementos": elementos})
        return actos
