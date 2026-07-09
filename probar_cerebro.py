# -*- coding: utf-8 -*-
"""
probar_cerebro.py — Banco de pruebas del cerebro híbrido de LARA.

Ejecuta:  python probar_cerebro.py
(no toca la nube: solo prueba el reflejo, el filtro simple/fuerte, el
validador y — si Ollama está corriendo — el modelo local y la memoria).

Úsalo después de cambiar core/llm_engine.py, core/llm_local.py o
core/memoria_vectorial.py para confirmar que nada se rompió.
"""

import io
import json
import sys
import time

# La consola de Windows suele ser cp1252 y los motores usan emojis (⚡🏠☁):
# forzamos UTF-8 en la salida para que el banco nunca reviente por eso.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from core import llm_engine as le

FALLOS = []


def caso(nombre, condicion, detalle=""):
    if condicion:
        print(f"  OK  {nombre}")
    else:
        print(f"  MAL {nombre}  {detalle}")
        FALLOS.append(nombre)


print("=== 1. Router REFLEJO (0 s, sin IA) ===")
reflejos = {
    "abre chrome": ("abrir", ["chrome"]),
    "Lara, abre el bloc de notas": ("abrir", ["bloc de notas"]),
    "puedes abrir spotify?": ("abrir", ["spotify"]),
    "abre descargas": ("sistema", ["abrir_carpeta", "descargas"]),
    "abre la papelera": ("cmd", None),
    "vacía la papelera": ("sistema", ["papelera"]),
    "qué hora es": ("sistema", ["hora"]),
    "qué día es hoy": ("hablar", None),
    "sube el brillo al 80": ("ajustar_brillo", [80]),
    "brillo al 50": ("ajustar_brillo", [50]),
    "baja el volumen": ("sistema", ["volumen_bajar"]),
    "silencia la pc": ("sistema", ["silenciar"]),
    "captura la pantalla": ("sistema", ["captura"]),
    "cuánta batería queda": ("sistema", ["bateria"]),
    "cuál es mi ip": ("sistema", ["ip"]),
    "bloquea la pc": ("sistema", ["bloquear"]),
    "recuérdame sacar la placa en 15 minutos": ("recordatorio_rapido", ["sacar la placa", "15"]),
    "recuérdame llamar a Ramos en una hora": ("recordatorio_rapido", ["llamar a Ramos", "60"]),
}
for frase, (accion_esp, params_esp) in reflejos.items():
    raw = le._router_rapido(frase)
    d = json.loads(raw) if raw else {}
    ok = d.get("accion") == accion_esp and (params_esp is None or d.get("params") == params_esp)
    caso(f"reflejo: {frase!r}", ok, f"-> {d}")

print("\n=== 2. Frases que NO deben resolverse por reflejo ===")
for frase in ["abre chrome y busca vuelos", "sube el brillo", "copia esto al portapapeles",
              "explícame el brillo de una estrella", "abre la carpeta que te dije"]:
    caso(f"no-reflejo: {frase!r}", le._router_rapido(frase) is None)

print("\n=== 3. Filtro simple (local) vs fuerte (nube) ===")
for frase, esperado in [
        ("abre chrome", True), ("que hora es", True), ("hola lara", True),
        ("busca el precio del dolar hoy", True),
        ("explícame cómo funciona un transistor mosfet", False),
        ("diseña un circuito regulador de 5V", False),
        ("escribe un programa en python que ordene una lista", False),
        ("x" * 200, False)]:
    caso(f"apto_local={esperado}: {frase[:50]!r}", le._apto_para_local(frase) == esperado)

print("\n=== 4. Validador de la salida del modelo local ===")
caso("rechaza acción inventada",
     le._validar_router_local('{"accion":"volar","params":[]}') is None)
caso("rechaza hablar vacío",
     le._validar_router_local('{"accion":"hablar","params":[],"mensaje":""}') is None)
caso("normaliza params string",
     json.loads(le._validar_router_local(
         '{"accion":"abrir","params":"chrome","mensaje":"ok"}'))["params"] == ["chrome"])
caso("acepta decisión sana",
     le._validar_router_local(
         '{"accion":"buscar","params":["clima lima"],"mensaje":"Buscando"}') is not None)
caso("rechaza op de sistema inventada",
     le._validar_router_local(
         '{"accion":"sistema","params":["modo_avion"],"mensaje":"ok"}') is None)
caso("normaliza op de sistema válida",
     json.loads(le._validar_router_local(
         '{"accion":"sistema","params":["HORA"],"mensaje":"ok"}'))["params"] == ["hora"])

print("\n=== 5. Modelo LOCAL en vivo (si Ollama corre) ===")
ll = le._llm_local()
if ll is not None and ll.activo() and ll.disponible():
    t0 = time.time()
    raw = le._consultar_router_local("hola lara, cómo estás", [])
    d = json.loads(raw) if raw else {}
    caso(f"local responde charla ({time.time()-t0:.1f}s)",
         d.get("accion") == "hablar" and len(d.get("mensaje", "")) > 10, f"-> {d}")
    t0 = time.time()
    raw = le._consultar_router_local("busca las noticias de hoy en Perú", [])
    d = json.loads(raw) if raw else {}
    caso(f"local enruta búsqueda a la nube ({time.time()-t0:.1f}s)",
         d.get("accion") == "buscar", f"-> {d}")
else:
    print("  (Ollama no está corriendo o el cerebro local está apagado — saltado)")

print("\n=== 6. Memoria semántica ===")
try:
    from core import memoria_vectorial as mv
    vec, modelo = mv._embedding("el cliente del amplificador paga los viernes")
    if vec:
        caso(f"embeddings funcionando ({modelo}, {len(vec)} dims)", True)
        sim = mv._coseno(vec, mv._embedding("cuando cobro lo del ampli")[0] or [])
        dif = mv._coseno(vec, mv._embedding("la receta de la pizza napolitana")[0] or [])
        caso(f"semántica coherente (relacionado {sim:.2f} > distinto {dif:.2f})", sim > dif)
    else:
        print("  (sin embeddings disponibles: usará búsqueda por palabras — no es error)")
except Exception as e:
    caso("memoria semántica importa", False, str(e))

print("\n" + "=" * 50)
if FALLOS:
    print(f"RESULTADO: {len(FALLOS)} PRUEBAS FALLARON:")
    for f in FALLOS:
        print("  -", f)
    sys.exit(1)
print("RESULTADO: TODAS LAS PRUEBAS PASARON ✔")
