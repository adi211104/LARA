"""
main.py — LARA en modo consola.

Capa fina sobre core/cerebro.py (LaraCerebro), que contiene toda la lógica real.
Comandos especiales dentro del chat:
    voz on / voz off   -> activar/desactivar que LARA hable en voz alta
    escuchar           -> capturar un comando por micrófono (push-to-talk)
    pantalla           -> que LARA mire tu pantalla y recomiende
    q / salir / exit   -> cerrar
Para la experiencia completa (gráfica, voz, subir archivos), ejecuta:  python gui.py
"""

from config import ADMIN_NOMBRE
from core.cerebro import LaraCerebro
from core.agente_fondo import iniciar_agente_fondo, set_voz_alertas
from core.skills_registry import auto_registrar_skills_core
from core.skills_memoria import total_turnos_guardados
from core import skills_voz, rutinas


def main():
    print("--- LARA v9.5 Windows Edition (consola) ---")
    print(f"Sistema activo, {ADMIN_NOMBRE}. Comandos: 'voz on/off', 'escuchar' (hablarle una vez),")
    print("  'lara escucha' (manos libres: te activa al decir 'Lara'), 'reunion on/off',")
    print("  'opina' (analiza lo escuchado), 'acta' (guarda la reunión), 'pantalla', 'q' salir.")

    cerebro = LaraCerebro(
        on_status=lambda m: print(f"[{m}]"),
        on_speak=lambda m: skills_voz.hablar(m),
        # Voz en STREAMING para el razonamiento profundo: Lara empieza a hablar
        # apenas tiene la primera frase, sin esperar a terminar toda la respuesta.
        on_speak_stream=lambda gen: skills_voz.hablar_stream(gen),
    )
    skills_voz.set_voz_activa(False)  # en consola, silenciosa por defecto

    # Precargar Whisper LOCAL en segundo plano (offline, menos latencia). Si no
    # está instalado, no pasa nada: seguirá usando Whisper en la nube.
    try:
        import threading as _th
        from core import stt_local
        _th.Thread(target=stt_local.precargar, daemon=True).start()
    except Exception:
        pass

    # Estado del wake word (manos libres) para poder encenderlo/apagarlo.
    wake_parar = {"ev": None}

    if cerebro.historial:
        print(f"[LARA] Memoria cargada: recuerdo los últimos {len(cerebro.historial)} turnos "
              f"({total_turnos_guardados()} en total).")

    iniciar_agente_fondo()
    # El agente de fondo ahora puede AVISAR EN VOZ ALTA lo crítico (tareas que
    # vencen ya), no solo notificarlo en pantalla. Respeta el estado de voz.
    set_voz_alertas(lambda m: skills_voz.hablar(m))
    try:
        nuevas = auto_registrar_skills_core()
        if nuevas:
            print(f"[LARA] Skills auto-registradas: {', '.join(nuevas)}")
    except Exception as e:
        print(f"[LARA] Aviso: no se pudo auto-registrar skills: {e}")

    # Briefing proactivo tipo JARVIS: saluda por tu nombre y te pone al día
    # (tareas urgentes, compras, novedades). Se muestra siempre; se dice en voz
    # alta si la voz está activa.
    try:
        print(f"\nLARA: {rutinas.saludar(on_speak=lambda m: skills_voz.hablar(m))}\n")
    except Exception as e:
        print(f"[LARA] (No pude armar el briefing: {e})")

    while True:
        try:
            prompt = input("Tú: ").strip()
            low = prompt.lower()
            if low in ("q", "salir", "exit"):
                break
            if low in ("voz on", "voz activa"):
                skills_voz.set_voz_activa(True)
                print("[LARA] Voz activada.")
                continue
            if low in ("voz off", "silencio"):
                skills_voz.set_voz_activa(False)
                print("[LARA] Voz desactivada.")
                continue

            # ── Wake word (manos libres): te activa al decir "Lara" ──
            if low in ("lara escucha", "escucha lara", "manos libres", "activar wake", "wake on"):
                if wake_parar["ev"] is not None:
                    print("[LARA] El modo manos libres ya está activo.")
                    continue
                skills_voz.set_voz_activa(True)
                from core import wake_word

                def _al_activar(orden, quien):
                    print(f"\nTú (voz{' - ' + quien if quien else ''}): {orden}")
                    print(f"\nLARA: {cerebro.procesar(orden, quien)}\n")

                _hilo, _ev = wake_word.iniciar(_al_activar, verificar_hablante=True)
                wake_parar["ev"] = _ev
                print("[LARA] Manos libres ACTIVO. Di 'Lara ...' para hablarme. Escribe 'wake off' para parar.")
                continue
            if low in ("wake off", "manos libres off", "parar wake"):
                if wake_parar["ev"] is not None:
                    wake_parar["ev"].set()
                    wake_parar["ev"] = None
                    print("[LARA] Manos libres desactivado.")
                else:
                    print("[LARA] El modo manos libres no estaba activo.")
                continue

            # ── Modo reunión: escucha ambiente + análisis a demanda ──
            if low in ("reunion on", "reunión on", "escuchar reunion", "modo reunion"):
                from core import modo_reunion
                skills_voz.set_voz_activa(True)
                r = modo_reunion.iniciar()
                print(f"[LARA] Modo reunión: {r.get('estado')}. Escribe 'opina' para su análisis o 'reunion off' para parar.")
                continue
            if low in ("reunion off", "reunión off", "parar reunion"):
                from core import modo_reunion
                r = modo_reunion.detener()
                print(f"[LARA] Modo reunión detenido ({r.get('fragmentos', 0)} fragmentos escuchados).")
                continue
            if low in ("opina", "analiza", "que opinas", "qué opinas", "tu opinion", "tu opinión"):
                from core import modo_reunion
                print("[Analizando lo escuchado...]")
                analisis = modo_reunion.analizar()
                print(f"\nLARA: {analisis}\n")
                if skills_voz.VOZ_ACTIVA:
                    skills_voz.hablar(analisis)
                continue
            if low == "acta":
                from core import modo_reunion
                r = modo_reunion.guardar_acta()
                print(f"[LARA] {r}")
                continue

            if low == "escuchar":
                print("[Escuchando... habla cuando quieras]")
                res = skills_voz.escuchar()
                if "error" in res:
                    print(f"[LARA] {res['error']}")
                    continue
                prompt = res["texto"]
                print(f"Tú (voz): {prompt}")
            elif low == "pantalla":
                print(cerebro.ver_pantalla())
                continue

            if not prompt:
                continue

            respuesta = cerebro.procesar(prompt)
            print(f"\nLARA: {respuesta}\n")

        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
