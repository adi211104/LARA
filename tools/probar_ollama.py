"""Prueba pequeña de inferencia local; descarga y arranque se hacen por separado."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import requests


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://127.0.0.1:11434')
    p.add_argument('--modelo', default='qwen3:0.6b')
    p.add_argument('--salida', default='.tools/ollama-prueba/resultados.json')
    a = p.parse_args()
    partes = urlsplit(a.url)
    if partes.hostname not in ('127.0.0.1', 'localhost', '::1') or partes.scheme != 'http' or partes.username:
        p.error('La prueba solo admite un servidor HTTP local sin credenciales.')
    sesion = requests.Session()
    sesion.trust_env = False
    casos = [
        ('calculo', [{'role': 'user', 'content': 'Cuánto es 17 + 25? Devuelve SOLO JSON con total numérico.'}], {'total': 42}),
        ('contexto', [{'role': 'user', 'content': 'El programa creado se llama rectangulo.py.'},
                      {'role': 'assistant', 'content': 'Entendido.'},
                      {'role': 'user', 'content': '¿Cómo se llama ese programa? SOLO JSON con archivo.'}], {'archivo': 'rectangulo.py'}),
        ('correccion', [{'role': 'user', 'content': 'Cliente Ana, visita viernes. Corrección: lunes. SOLO JSON con cliente y fecha actual.'}],
         {'cliente': 'Ana', 'fecha': 'lunes'}),
    ]
    resultados = []
    try:
        for nombre, mensajes, esperado in casos:
            t = time.perf_counter()
            try:
                r = sesion.post(a.url.rstrip('/') + '/api/chat', json={
                    'model': a.modelo, 'messages': mensajes, 'stream': False, 'think': False,
                    'format': 'json', 'keep_alive': '60s',
                    'options': {'num_ctx': 1024, 'num_predict': 120, 'num_thread': 2, 'temperature': 0}
                }, timeout=(3, 45))
                r.raise_for_status()
                d = r.json()
                texto = d.get('message', {}).get('content', '')
                try:
                    correcto = json.loads(texto) == esperado and d.get('done_reason') != 'length'
                except ValueError:
                    correcto = False
                segundos = d.get('eval_duration', 0) / 1e9
                dato = {'caso': nombre, 'correcto': correcto, 'respuesta': texto,
                        'segundos': round(time.perf_counter() - t, 3),
                        'carga_s': round(d.get('load_duration', 0) / 1e9, 3),
                        'tokens_s': round(d.get('eval_count', 0) / segundos, 2) if segundos else None}
                resultados.append(dato)
                print(json.dumps(dato, ensure_ascii=False), flush=True)
            except requests.RequestException as e:
                resultados.append({'caso': nombre, 'correcto': False, 'error': type(e).__name__,
                                   'segundos': round(time.perf_counter() - t, 3)})
                print('Fallo local acotado; no insistir ni consumir nube.', flush=True)
                break
        estado = sesion.get(a.url.rstrip('/') + '/api/ps', timeout=3).json()
        salida = Path(a.salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        salida.write_text(json.dumps({'modelo': a.modelo, 'casos': resultados,
            'aciertos': sum(c['correcto'] for c in resultados), 'carga_modelos': estado},
            ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        # Liberar el modelo incluso después de una prueba fallida.
        try:
            sesion.post(a.url.rstrip('/') + '/api/generate', json={
                'model': a.modelo, 'keep_alive': 0, 'stream': False}, timeout=5)
        except requests.RequestException:
            pass
        sesion.close()


if __name__ == '__main__':
    main()
