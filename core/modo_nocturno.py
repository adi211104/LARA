"""
Script para activar o desactivar el modo oscuro completo de Windows.

Argumentos aceptados:
    activar    -> Cambia el tema a oscuro  (AppsUseLightTheme = 0, SystemUsesLightTheme = 0)
    desactivar -> Cambia el tema a claro   (AppsUseLightTheme = 1, SystemUsesLightTheme = 1)

Uso: python modo_nocturno.py <activar|desactivar>

NOTA: Generado por LARA. No se ejecuta automáticamente; muévelo a core/ tras revisarlo.
"""

import sys
import winreg
import subprocess


def cambiar_modo_nocturno(accion: str) -> None:
    registry_path = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

    # 0 = oscuro, 1 = claro — se aplica a AMBAS claves para dark mode completo
    # (AppsUseLightTheme controla las apps; SystemUsesLightTheme controla barra de tareas y menú inicio)
    valores = {"activar": 0, "desactivar": 1}

    if accion not in valores:
        print(f"Error: acción '{accion}' no válida. Use 'activar' o 'desactivar'.")
        sys.exit(1)

    valor = valores[accion]

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_SET_VALUE
        )
        # Ambas claves necesarias para dark mode completo en Windows 10/11
        for key_name in ("AppsUseLightTheme", "SystemUsesLightTheme"):
            winreg.SetValueEx(key, key_name, 0, winreg.REG_DWORD, valor)
        winreg.CloseKey(key)

        modo_texto = "oscuro" if accion == "activar" else "claro"
        print(f"Registro actualizado: modo {modo_texto} configurado (apps + sistema).")

        # Reiniciar explorer.exe para que los cambios se apliquen inmediatamente.
        # CORRECCIÓN: Popen con lista sin shell=True (pasar lista con shell=True en Windows
        # serializa la lista como string y CMD no la interpreta correctamente).
        print("Reiniciando explorer.exe para aplicar cambios...")
        subprocess.run(["taskkill", "/f", "/im", "explorer.exe"], capture_output=True)
        subprocess.Popen(["explorer.exe"])

        print("Operación completada con éxito.")

    except PermissionError:
        print("Error: permisos insuficientes para modificar el registro. Intenta con cmd_admin.")
        sys.exit(1)
    except Exception as e:
        print(f"Error crítico al modificar el registro: {e}")
        sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Sin argumento: activar modo oscuro por defecto
        accion = "activar"
    else:
        accion = sys.argv[1].lower()
    cambiar_modo_nocturno(accion)