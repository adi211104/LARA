"""
Script para activar o desactivar el modo lectura (filtro de luz cálida) en Windows.
Usa SetDeviceGammaRamp vía ctypes — no requiere librerías externas.

Argumentos aceptados:
    activar    -> Aplica filtro de luz cálida (reduce azul y verde)
    desactivar -> Restaura los colores originales de la pantalla

Uso: python modo_lectura.py <activar|desactivar>

NOTA: Generado por LARA. No se ejecuta automáticamente; muévelo a core/ tras revisarlo.
LIMITACIÓN: SetDeviceGammaRamp puede no funcionar en todos los monitores o drivers.
  Si falla, considera usar el Modo Noche nativo de Windows (Night Light en la configuración).
"""

import sys
import ctypes


def _obtener_dc():
    """Obtiene y valida el Device Context de la pantalla completa."""
    hdc = ctypes.windll.user32.GetDC(0)
    if not hdc:
        raise RuntimeError("No se pudo obtener el contexto de dispositivo de la pantalla.")
    return hdc


def aplicar_filtro_calido(intensidad_azul: float = 0.5, intensidad_verde: float = 0.85) -> None:
    """
    Ajusta la rampa de gamma para crear un efecto de luz cálida.
    intensidad_azul: 0.0–1.0 (menor = más cálido, se recomienda 0.4–0.6)
    intensidad_verde: 0.0–1.0 (menor = más naranja, se recomienda 0.8–0.9)
    """
    hdc = _obtener_dc()
    try:
        ramp = (ctypes.c_ushort * 768)()
        for i in range(256):
            ramp[i]       = int((i / 255.0) * 65535)                          # Rojo: máximo (calidez)
            ramp[i + 256] = int((i / 255.0) * 65535 * intensidad_verde)       # Verde: reducido
            ramp[i + 512] = int((i / 255.0) * 65535 * intensidad_azul)        # Azul: muy reducido

        resultado = ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
        if resultado == 0:
            raise RuntimeError(
                "SetDeviceGammaRamp falló. Es posible que tu monitor o driver no soporten "
                "ajuste de gamma por software. Prueba el Modo Noche nativo de Windows."
            )
        print("Modo lectura activado: filtro de luz cálida aplicado.")
    finally:
        ctypes.windll.user32.ReleaseDC(0, hdc)


def restaurar_colores() -> None:
    """Restaura la rampa de gamma a valores lineales (colores originales)."""
    hdc = _obtener_dc()
    try:
        ramp = (ctypes.c_ushort * 768)()
        for i in range(256):
            valor = int((i / 255.0) * 65535)
            ramp[i]       = valor   # Rojo
            ramp[i + 256] = valor   # Verde
            ramp[i + 512] = valor   # Azul

        resultado = ctypes.windll.gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp))
        if resultado == 0:
            raise RuntimeError("SetDeviceGammaRamp falló al restaurar los colores.")
        print("Colores restaurados al estado original.")
    finally:
        ctypes.windll.user32.ReleaseDC(0, hdc)


if __name__ == "__main__":
    # CORRECCIÓN CRÍTICA: reemplaza el menú con input() (que colgaba el proceso
    # al ejecutarse con stdin cerrado desde LARA) por argumentos de línea de comandos.
    if len(sys.argv) < 2:
        # Sin argumento: activar por defecto (comportamiento más útil)
        accion = "activar"
    else:
        accion = sys.argv[1].lower()

    try:
        if accion == "activar":
            aplicar_filtro_calido()
        elif accion == "desactivar":
            restaurar_colores()
        else:
            print(f"Error: acción '{accion}' no válida. Use 'activar' o 'desactivar'.")
            sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)