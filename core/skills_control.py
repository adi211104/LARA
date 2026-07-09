"""
skills_control.py — Control físico del equipo (teclado y mouse).

Permite que LARA HAGA cosas por ti de verdad: escribir texto en el programa que
tengas abierto (o abrir uno y escribir en él), mover el mouse de forma visible y
hacer clic. Usas tus ojos para ver cómo lo hace.

- Teclear: pynput (escribe Unicode/acentos bien), por palabras con un pequeño
  retardo para que se vea aparecer el texto.
- Mouse: pyautogui con movimiento animado (duration) para que se vea moverse.

Es deliberadamente conservador: pequeñas pausas para dar tiempo a que la ventana
tome foco antes de escribir, y sin failsafe agresivo que aborte por accidente.
"""

import time
import threading

_lock = threading.Lock()


def _teclado():
    from pynput.keyboard import Controller
    return Controller()


def escribir_texto(texto: str, visible: bool = True) -> dict:
    """Teclea 'texto' en la ventana que esté enfocada en este momento."""
    if not texto:
        return {"error": "No hay texto que escribir."}
    try:
        with _lock:
            kb = _teclado()
            time.sleep(0.3)
            if visible:
                # Palabra por palabra, para que se vea apareciendo
                for i, palabra in enumerate(texto.split(" ")):
                    kb.type(palabra)
                    if i < len(texto.split(" ")) - 1:
                        kb.type(" ")
                    time.sleep(0.06)
            else:
                kb.type(texto)
        return {"estado": "ok", "escrito": texto[:80]}
    except Exception as e:
        return {"error": str(e)}


def _enfocar(programa: str) -> bool:
    """Intenta traer al frente la ventana cuyo título coincide con el programa."""
    try:
        import pygetwindow as gw
        objetivo = programa.lower()
        for w in gw.getAllWindows():
            if w.title and objetivo in w.title.lower():
                try:
                    if w.isMinimized:
                        w.restore()
                    w.activate()
                    return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def abrir_y_escribir(programa: str, texto: str) -> dict:
    """Abre (o enfoca) un programa y escribe el texto dentro. Ideal para dictar al bloc de notas."""
    try:
        from core.skills_programas import abrir_programa_inteligente
    except Exception:
        from skills_programas import abrir_programa_inteligente

    res = abrir_programa_inteligente(programa)
    if "error" in res:
        # Igual intentamos enfocar por si ya estaba abierto
        if not _enfocar(programa):
            return {"error": f"No pude abrir '{programa}': {res['error']}"}
    nombre = res.get("nombre", programa)
    time.sleep(2.2)            # dar tiempo a que la ventana abra y tome foco
    _enfocar(nombre)
    time.sleep(0.4)
    r = escribir_texto(texto)
    if "error" in r:
        return r
    return {"estado": "ok", "programa": nombre, "escrito": texto[:80]}


def mover_mouse(x: int, y: int, duracion: float = 0.6) -> dict:
    """Mueve el mouse de forma visible a las coordenadas (x, y)."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.moveTo(int(x), int(y), duration=max(0.1, duracion))
        return {"estado": "ok", "x": x, "y": y}
    except Exception as e:
        return {"error": str(e)}


def clic(x=None, y=None, dobles: bool = False) -> dict:
    """Hace clic donde está el mouse, o en (x, y) si se dan coordenadas."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        if x is not None and y is not None:
            pyautogui.moveTo(int(x), int(y), duration=0.4)
        if dobles:
            pyautogui.doubleClick()
        else:
            pyautogui.click()
        return {"estado": "ok"}
    except Exception as e:
        return {"error": str(e)}


def presionar(teclas: str) -> dict:
    """Presiona una combinación de teclas, ej: 'ctrl+s', 'enter', 'ctrl+a'."""
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        partes = [t.strip() for t in teclas.replace("+", " ").split() if t.strip()]
        if len(partes) == 1:
            pyautogui.press(partes[0])
        else:
            pyautogui.hotkey(*partes)
        return {"estado": "ok", "teclas": teclas}
    except Exception as e:
        return {"error": str(e)}
