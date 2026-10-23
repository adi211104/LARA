"""Clasificador pequeño entrenable de seguimientos; no es un modelo generativo."""
import json
import math
from pathlib import Path
import re
import unicodedata

from config import PROYECTO_DIR

MODELO = PROYECTO_DIR / '.tools' / 'intencion_codigo.json'


def normalizar(texto):
    t = ''.join(c for c in unicodedata.normalize('NFD', texto.lower()) if unicodedata.category(c) != 'Mn')
    return ' '.join(re.findall(r'[a-z0-9]+', t))


def caracteristicas(texto):
    palabras = normalizar(texto).split()
    return set(palabras + [' '.join(palabras[i:i+2]) for i in range(len(palabras)-1)])


def entrenar(ejemplos, epocas=250):
    vocabulario = sorted(set().union(*(caracteristicas(t) for t, y in ejemplos)))
    pesos = {k: 0.0 for k in vocabulario}
    sesgo = 0.0
    datos = [(caracteristicas(t), y) for t, y in ejemplos]
    for _ in range(epocas):
        for x, y in datos:
            z = max(-30, min(30, sesgo + sum(pesos.get(k, 0) for k in x)))
            error = y - 1 / (1 + math.exp(-z))
            sesgo += 0.03 * error
            for k in x:
                pesos[k] += 0.03 * (error - 0.01 * pesos[k])
    return {'version': 1, 'tipo': 'regresion_logistica_palabras', 'pesos': pesos, 'sesgo': sesgo,
            'alcance': 'Solo intención de abrir el artefacto; no entiende código ni genera respuestas.'}


def probabilidad(texto, modelo):
    z = modelo['sesgo'] + sum(modelo['pesos'].get(k, 0) for k in caracteristicas(texto))
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def pide_abrir_artefacto(texto):
    """Alta confianza y referencia explícita. Abstenerse ante negación o nombres."""
    t = normalizar(texto)
    if (re.search(r'\b(no|nunca|jamas|sin)\b', t) or re.search(r'\.(py|exe|bat|cmd)\b|[:\\/]', texto.lower())
            or not re.search(r'\b(abr[ei]|abrir|ejecuta|ejecutar|inicia|lanza|arranca|marcha)\b', t)
            or not any(r in t for r in ('el programa', 'lo que', 'ese programa', 'eso', 'el script', 'el que'))):
        return False
    try:
        modelo = json.loads(MODELO.read_text(encoding='utf-8'))
        return probabilidad(texto, modelo) >= 0.9
    except (OSError, ValueError, KeyError, TypeError):
        return False
