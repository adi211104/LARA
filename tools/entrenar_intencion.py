"""Entrenamiento CPU pequeño con evaluación independiente y activación condicionada."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import intencion_codigo as ic


def main():
    inicio = time.perf_counter()
    positivos = [f'{v} {obj}' for v in ('abre', 'ejecuta', 'inicia', 'lanza', 'arranca')
                 for obj in ('el programa', 'el programa que hiciste', 'el que creaste', 'el script', 'lo que hiciste')]
    negativos = [f'{v} {obj}' for v in ('abre', 'ejecuta', 'inicia', 'lanza', 'arranca')
                 for obj in ('Lara', 'calculadora', 'Chrome', 'el navegador', 'otro programa')]
    negativos += ['no abras el programa', 'explica el programa', 'modifica el script', 'crea otro programa',
                  'dibuja un rectángulo', 'comprueba el programa', 'lee el archivo', 'continúa', 'repite',
                  'el programa falla', 'corrige el programa', 'borra el programa']
    ejemplos = [(t, 1) for t in positivos] + [(t, 0) for t in negativos]
    modelo = ic.entrenar(ejemplos)
    # Preguntas reservadas antes del entrenamiento: no se ajusta con sus resultados.
    test = [('abre el programa que acabas de hacer', 1), ('ejecuta el script que creaste', 1),
            ('inicia el programa que hiciste por favor', 1), ('lanza el que creaste', 1),
            ('arranca el programa que hiciste', 1), ('por favor abre calculadora', 0),
            ('ejecuta Chrome', 0), ('abre otro programa', 0), ('modifica el programa que hiciste', 0),
            ('explica el script que creaste', 0), ('no ejecutes el programa', 0),
            ('muestra el código del programa', 0)]
    resultados = []
    for texto, y in test:
        prob = ic.probabilidad(texto, modelo)
        resultados.append({'texto': texto, 'esperado': y, 'predicho': int(prob >= 0.9), 'probabilidad': round(prob, 4)})
    aciertos = sum(r['esperado'] == r['predicho'] for r in resultados)
    carpeta = ROOT / '.tools' / 'entrenamiento_intencion'
    carpeta.mkdir(parents=True, exist_ok=True)
    for nombre, datos in (('train', ejemplos), ('test', test)):
        (carpeta / (nombre+'.jsonl')).write_text(''.join(json.dumps({'texto': t, 'abrir_artefacto': y}, ensure_ascii=False)+'\n'
                                                   for t,y in datos), encoding='utf-8')
    modelo['train_sha256'] = hashlib.sha256((carpeta/'train.jsonl').read_bytes()).hexdigest()
    # No sustituir un modelo de producción si el candidato falla la suite.
    aprobado = aciertos == len(test)
    if aprobado:
        ic.MODELO.parent.mkdir(parents=True, exist_ok=True)
        temporal = ic.MODELO.with_suffix('.tmp')
        temporal.write_text(json.dumps(modelo, ensure_ascii=False), encoding='utf-8')
        temporal.replace(ic.MODELO)
    reporte = {'entrenamiento': len(ejemplos), 'test': len(test), 'aciertos': aciertos, 'activado': aprobado,
               'segundos': round(time.perf_counter()-inicio, 3), 'resultados': resultados,
               'tipo': modelo['tipo'], 'nota': 'Detector de intención pequeño. No es fine-tuning de un LLM.'}
    (carpeta / 'resultado.json').write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in reporte.items() if k != 'resultados'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
