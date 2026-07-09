"""
util_json.py — Extracción ROBUSTA de JSON de las respuestas de un LLM.

Los modelos a veces envuelven el JSON en prosa ("Claro, aquí tienes: {...}") o
añaden texto después. El viejo `re.search(r'\\{.*\\}')` era codicioso: capturaba
de la primera '{' a la ÚLTIMA '}' de TODO el texto y fallaba con llaves sueltas
o varios objetos. Aquí contamos llaves balanceadas (ignorando las que van dentro
de strings) y devolvemos el primer objeto completo y bien formado.
"""

import json


def extraer_json(texto: str):
    """Devuelve el primer objeto JSON (dict/list) presente en 'texto', o None."""
    if not texto:
        return None
    try:
        return json.loads(texto)
    except (json.JSONDecodeError, TypeError):
        pass
    inicio = texto.find("{")
    while inicio != -1:
        prof = 0
        en_str = False
        escape = False
        for i in range(inicio, len(texto)):
            c = texto[i]
            if en_str:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    en_str = False
            else:
                if c == '"':
                    en_str = True
                elif c == "{":
                    prof += 1
                elif c == "}":
                    prof -= 1
                    if prof == 0:
                        try:
                            return json.loads(texto[inicio:i + 1])
                        except json.JSONDecodeError:
                            break
        inicio = texto.find("{", inicio + 1)
    return None
