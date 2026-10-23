"""Genera requirements.lock: versiones exactas de requirements.txt y de sus
dependencias transitivas, tomadas del entorno Python que lo ejecuta (A-04).

Uso (desde la raíz del proyecto, con el entorno virtual activo):
    python tools/generar_lock.py requirements.txt > requirements.lock.nuevo
Revisa el resultado, conserva la cabecera de requirements.lock y reemplázalo.
"""
import sys
from importlib import metadata
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name as cn

raices = []
for linea in open(sys.argv[1], encoding="utf-8"):
    linea = linea.split("#", 1)[0].strip()
    if linea:
        raices.append(linea)

dists = {cn(d.metadata["Name"]): d for d in metadata.distributions()}
vistos, pila, faltan = {}, [cn(r) for r in raices], []
while pila:
    n = pila.pop()
    if n in vistos:
        continue
    d = dists.get(n)
    if d is None:
        faltan.append(n); continue
    vistos[n] = (d.metadata["Name"], d.version)
    for req in d.requires or []:
        r = Requirement(req)
        if r.marker and not r.marker.evaluate({"extra": ""}):
            continue
        pila.append(cn(r.name))
for n in sorted(vistos):
    if n == "setuptools":  # herramienta de empaquetado, no de LARA
        continue
    print(f"{vistos[n][0]}=={vistos[n][1]}")
if faltan:
    print("# FALTAN:", faltan, file=sys.stderr)
