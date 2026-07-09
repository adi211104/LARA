"""
ui_tema.py — Tema visual ÚNICO de LARA (modo claro, estilo HUD técnico).

Inspirado en los paneles holográficos de diagnóstico (azul técnico + naranja
de energía sobre fondo claro). TODA la interfaz importa sus colores de aquí:
gui.py, gui_opencode.py, gui_explicacion.py y cualquier diálogo nuevo.

Reglas de uso (acordadas con Adi):
  - VERDE se usa ÚNICAMENTE en las pestañas "Chat" y "Open Code" de la cabecera.
  - ACENTO (cian técnico) es el color principal de bordes, títulos y botones.
  - NARANJA es para resaltes puntuales: estados activos, alertas, energía,
    elementos destacados de los diagramas. No abusar.
  - FONDO claro con PANEL blanco para tarjetas; TEXTO azul marino.
"""

# ── Paleta (modo claro) ─────────────────────────────────────────
FONDO    = "#F2F6FA"   # fondo general: claro con tinte azulado
PANEL    = "#FFFFFF"   # paneles, tarjetas, zonas de contenido
BORDE    = "#C6D8E6"   # bordes suaves de paneles
TEXTO    = "#13293D"   # azul marino oscuro: texto principal
SUAVE    = "#5B7C99"   # texto secundario / etiquetas apagadas
ACENTO   = "#0E7CA8"   # cian técnico: acento principal (botones, títulos, bordes activos)
ACENTO_2 = "#63B3D1"   # cian claro: líneas finas, decoración HUD
NARANJA  = "#E8801A"   # naranja de energía: resaltes, estados activos, alertas
VERDE    = "#0B7324"   # SOLO pestañas Chat / Open Code

# ── Tipografía ──────────────────────────────────────────────────
FUENTE   = ("Georgia", 12)
FUENTE_B = ("Georgia", 12, "bold")
FUENTE_I = ("Georgia", 12, "italic")
FUENTE_T = ("Georgia", 16, "bold")   # títulos grandes (paneles/diagramas)

# Compatibilidad con el código anterior (BG oscuro ya no existe):
# los módulos deben migrar a FONDO/TEXTO/ACENTO; no añadir alias oscuros aquí.
