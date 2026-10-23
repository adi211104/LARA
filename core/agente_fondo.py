"""
agente_fondo.py
"Tercer cerebro" de LARA: corre en un hilo de fondo, independiente de la
conversación, y avisa proactivamente (notificación de escritorio) sobre
tareas urgentes y, opcionalmente, usa el modelo agéntico de Groq
("compound") para tareas autónomas como investigar proveedores o precios.

IMPORTANTE: revisa en https://console.groq.com/docs/models el identificador
exacto del modelo "compound" (suele ser algo como "compound-beta" o
"groq/compound-beta" según la versión vigente de la API). Reemplaza
MODELO_AGENTE abajo con el valor correcto antes de usarlo.

Instalar dependencia para notificaciones de escritorio:
    pip install plyer
"""

import threading
import time
import os
from groq import Groq
from dotenv import load_dotenv

from core.skills_tasks import tareas_pendientes_urgentes, compra_listar, proyecto_listar
from core.skills_novedades import (
    proximos_temas, guardar_novedad,
    listar_limitaciones_pendientes, marcar_limitacion_resuelta,
)
from core.skills_propuestas import proponer_skill

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"), timeout=20, max_retries=0) if os.getenv("GROQ_API_KEY") else None

MODELO_AGENTE = "groq/compound"  # gestor agéntico de Groq (antes 'compound-beta', ya retirado)
INTERVALO_MINUTOS = 30
TEMAS_POR_CICLO = 3  # cuántos temas de la cola investiga cada vez (no 100 de golpe)

# Voz proactiva (Mejora 7): si el llamador conecta una función de voz, LARA
# ANUNCIA en voz alta las alertas críticas (tareas urgentes), no solo las
# notifica en pantalla — presencia tipo JARVIS. Por defecto None = solo notifica.
_voz_alertas = None


def set_voz_alertas(callback) -> None:
    """Conecta la voz para alertas críticas. callback(texto) habla el aviso.
    Pásalo desde main.py/gui.py (ej: skills_voz.hablar). None lo desactiva."""
    global _voz_alertas
    _voz_alertas = callback


def _hablar_alerta(texto: str) -> None:
    if _voz_alertas is None:
        return
    try:
        _voz_alertas(texto)
    except Exception:
        pass


def _notificar(titulo: str, mensaje: str) -> None:
    """Notificación de escritorio en Windows. Si falla, cae a print en consola."""
    try:
        from plyer import notification
        notification.notify(title=titulo, message=mensaje, timeout=12)
    except Exception:
        print(f"\n🔔 [{titulo}] {mensaje}\n")


def revisar_tareas_urgentes() -> None:
    urgentes = tareas_pendientes_urgentes()
    if not urgentes:
        return
    resumen = "\n".join(
        f"- {t['titulo']} (vence en {t['dias_restantes']}d, prioridad {t['prioridad']})"
        for t in urgentes
    )
    _notificar("LARA — Tareas urgentes", resumen)
    # Aviso hablado solo para lo verdaderamente urgente (vence hoy o mañana),
    # para no interrumpir por todo. Presencia tipo JARVIS.
    criticas = [t for t in urgentes if t.get("dias_restantes", 9) <= 1]
    if criticas:
        n = len(criticas)
        _hablar_alerta(
            f"Atención: {n} tarea{'s' if n != 1 else ''} vence{'n' if n != 1 else ''} muy pronto. "
            f"La más urgente: {criticas[0]['titulo']}.")


def recordatorio_rapido(texto: str, minutos: float) -> dict:
    """
    Recordatorio persistente en SQLite, independiente de las tareas del taller.
    Si LARA está cerrada al vencer, lo entrega al siguiente inicio.
    """
    try:
        from core.recordatorios import crear, iniciar
        resultado = crear(texto, minutos)
        iniciar(_entregar_recordatorio)
        return resultado
    except Exception as e:
        return {"error": str(e)}


def investigacion_autonoma() -> None:
    """
    Ejemplo de tarea de fondo usando el modelo agéntico de Groq:
    revisa compras pendientes y pide a compound que busque mejores
    precios/proveedores usando su propia herramienta de búsqueda integrada.
    Esto NO toca tu modelo conversacional (Gemini/Gemma) ni el historial de chat.
    """
    pendientes = compra_listar(filtro_estado="pendiente").get("compras", [])
    if not pendientes:
        return

    items = ", ".join(c["item"] for c in pendientes[:5])
    try:
        respuesta = client.chat.completions.create(
            model=MODELO_AGENTE,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Busca en internet precios actuales y proveedores en Perú para "
                        f"estos componentes electrónicos: {items}. "
                        f"Responde en español, máximo 6 líneas, con precios y dónde comprar."
                    ),
                }
            ],
        )
        texto = respuesta.choices[0].message.content.strip()
        _notificar("LARA — Reporte de compras", texto[:300])
    except Exception as e:
        print(f"[AGENTE FONDO] Error en investigación autónoma: {e}")


def investigar_novedades() -> None:
    """
    Avanza unos pocos temas de la cola rotativa (TEMAS_POR_CICLO) y le pide a
    compound que investigue cada uno en internet. Guarda los hallazgos y
    notifica un resumen. Con el tiempo, rota por TODOS los temas de la lista
    sin disparar decenas de búsquedas de golpe.
    """
    temas = proximos_temas(TEMAS_POR_CICLO)
    resumenes = []
    for tema in temas:
        try:
            r = client.chat.completions.create(
                model=MODELO_AGENTE,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Busca en internet novedades recientes y relevantes sobre: {tema}. "
                        f"Responde en español, máximo 3 líneas, con nombres concretos de herramientas, "
                        f"modelos o librerías si existen. Si no hay nada nuevo relevante, dilo brevemente."
                    )
                }],
            )
            hallazgo = r.choices[0].message.content.strip()
        except Exception as e:
            hallazgo = f"Error investigando: {e}"
        guardar_novedad(tema, hallazgo)
        resumenes.append(f"- {tema}: {hallazgo[:120]}")

    if resumenes:
        _notificar("LARA — Novedades encontradas", "\n".join(resumenes))


def resolver_limitaciones() -> None:
    """
    Revisa si hay limitaciones registradas (ej: 'ajustar_brillo' falló en este
    equipo). Si hay alguna pendiente, le pide a compound que investigue cómo
    resolverla, luego usa Gemini (generar_codigo_grande) para redactar una
    propuesta de código, y la deja en skills_pendientes/ para tu revisión.
    NUNCA se carga ni se ejecuta sola -- solo queda propuesta y notificada.
    """
    pendientes = listar_limitaciones_pendientes()
    if not pendientes:
        return

    lim = pendientes[0]  # una por ciclo, para no saturar la API
    try:
        investigacion = client.chat.completions.create(
            model=MODELO_AGENTE,
            messages=[{
                "role": "user",
                "content": (
                    f"Busca en internet cómo resolver esta limitación de un asistente Python en Windows: "
                    f"{lim['descripcion']}. Da una solución técnica concreta y el enfoque de implementación "
                    f"en 5-8 líneas, en español."
                )
            }],
        )
        solucion = investigacion.choices[0].message.content.strip()
    except Exception as e:
        print(f"[AGENTE FONDO] Error investigando limitación '{lim['skill']}': {e}")
        return

    from core.llm_engine import generar_codigo_grande  # import diferido: evita ciclos de import
    codigo = generar_codigo_grande(
        f"{lim['skill']}_mejorado",
        f"Limitación original detectada: {lim['descripcion']}\n"
        f"Solución investigada en internet:\n{solucion}\n\n"
        f"Genera una función Python que implemente esta solución como mejora de la skill existente, "
        f"con manejo de errores y comentarios en español."
    )
    resultado = proponer_skill(f"{lim['skill']}_mejorado", codigo)
    marcar_limitacion_resuelta(lim["id"])

    _notificar(
        "LARA — Propuesta para limitación resuelta",
        f"{lim['skill']}: investigué una solución y dejé el código en {resultado.get('ruta', '')}"
    )


def ciclo_agente() -> None:
    contador = 0
    while True:
        try:
            revisar_tareas_urgentes()
            nube_fondo = client is not None and os.getenv("LARA_CLOUD_BACKGROUND", "0") == "1"
            if nube_fondo:
                resolver_limitaciones()

            contador += 1
            if nube_fondo and contador % 2 == 0:
                investigar_novedades()
            if nube_fondo and contador % 4 == 0:
                investigacion_autonoma()
        except Exception as e:
            print(f"[AGENTE FONDO] Error: {e}")
        time.sleep(INTERVALO_MINUTOS * 60)


def iniciar_agente_fondo() -> None:
    from core.recordatorios import iniciar
    iniciar(_entregar_recordatorio)
    hilo = threading.Thread(target=ciclo_agente, daemon=True)
    hilo.start()
    print(f"[LARA] Agente de fondo iniciado (revisa cada {INTERVALO_MINUTOS} min).")


def _entregar_recordatorio(texto):
    _notificar("LARA — Recordatorio", texto)
    _hablar_alerta(texto)
