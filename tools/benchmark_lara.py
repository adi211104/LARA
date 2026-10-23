"""Benchmark terminal real, datos aislados, límite por caso y reporte JSON.

No arranca micrófono, GUI ni agente de fondo. Solo autoriza gestión de tareas
en el directorio temporal. Los textos sintéticos pueden enviarse a la nube.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CASOS = {
    'ayuda': 'ayuda',
    'tareas': 'nueva tarea revisar el amplificador',
    'calculo': 'Calcula la resistencia para un LED de 2 V y 20 mA conectado a 12 V. Indica resistencia y potencia disipada. No ejecutes herramientas.',
    'logica': 'Tengo 3 cajas: una dice manzanas, otra naranjas, otra mezcla. Todas las etiquetas son incorrectas. ¿De qué caja saco una fruta para identificar las tres y por qué? Responde sin herramientas.',
    'codigo': 'Escribe una función Python que calcule la mediana de una lista sin modificarla, acepte números negativos y lance ValueError si está vacía. Incluye tres pruebas. Solo muestra código, no lo ejecutes.',
}


def worker(nombre):
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix='lara_bench_') as datos:
        os.environ['LARA_DATA_DIR'] = datos
        os.environ['LARA_ENABLE_EXPERIMENTAL_TOOLS'] = '0'
        inicio = time.perf_counter()
        from core.cerebro import LaraCerebro
        from core import skills_voz, llm_engine
        skills_voz.set_voz_activa(False)
        estados = []
        cerebro = LaraCerebro(on_status=lambda m: estados.append([round(time.perf_counter()-inicio, 3), m]),
                             on_confirm=lambda a, d: a == 'gestion')
        arranque = time.perf_counter()-inicio
        inicio_turno = time.perf_counter()
        respuesta = cerebro.procesar(CASOS[nombre])
        segundos = time.perf_counter()-inicio_turno
        evidencia = None
        if nombre == 'tareas':
            from core.skills_tasks import tarea_listar
            evidencia = tarea_listar()
        salida = dict(caso=nombre, pregunta=CASOS[nombre], respuesta=respuesta,
                      segundos=round(segundos, 3), arranque_s=round(arranque, 3),
                      motor=llm_engine.get_ultimo_motor(), estados=estados, evidencia=evidencia)
        print('RESULTADO_JSON=' + json.dumps(salida, ensure_ascii=False), flush=True)
        import logging
        logging.shutdown()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser()
    p.add_argument('--worker', choices=CASOS)
    p.add_argument('--salida', default='benchmark.json')
    p.add_argument('--casos', nargs='+', choices=CASOS, default=list(CASOS))
    p.add_argument('--timeout', type=int, default=90)
    args = p.parse_args()
    if args.worker:
        worker(args.worker)
        return
    resultados = []
    for caso in args.casos:
        try:
            r = subprocess.run([sys.executable, __file__, '--worker', caso], cwd=ROOT,
                               env=dict(os.environ, PYTHONIOENCODING='utf-8'),
                               capture_output=True, text=True, encoding='utf-8', timeout=args.timeout)
            lineas = [l for l in r.stdout.splitlines() if l.startswith('RESULTADO_JSON=')]
            dato = json.loads(lineas[-1].split('=', 1)[1]) if lineas else dict(caso=caso, error='Sin resultado', exit_code=r.returncode)
        except subprocess.TimeoutExpired:
            dato = dict(caso=caso, error='timeout', limite_s=args.timeout)
        resultados.append(dato)
        Path(args.salida).write_text(json.dumps(resultados, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(dato, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
