"""Evaluación aislada de decisiones/texto: jamás despacha acciones del modelo."""
import hashlib
import json
from pathlib import Path
import time

from core import gateway_llm

SUITE = Path(__file__).resolve().parents[1] / 'evaluaciones' / 'base.json'


def evaluar(modelo, perfil, salida, completar=None):
    casos = json.loads(SUITE.read_text(encoding='utf-8'))[perfil]
    if not casos:
        raise ValueError('Suite vacía.')
    llamar = completar or gateway_llm.completar
    resultados = []
    if perfil == 'acciones':
        sistema = ('Eres LARA. Devuelve SOLO JSON con accion, params (lista), mensaje y '
                   'requiere_razonamiento_profundo (boolean). Acciones disponibles: abrir(programa), '
                   'leer(ruta), buscar(consulta), gestion(instrucción), hablar. '
                   'Responde hablar con params=[] si se pide explicar sin ejecutar. '
                   'No afirmes haber ejecutado nada; no ejecutes herramientas.')
    else:
        sistema = 'Responde solo el JSON solicitado, sin markdown ni herramientas. Verifica los cálculos y restricciones.'
    for caso in casos:
        t = time.perf_counter()
        try:
            r = llamar([{'role': 'system', 'content': sistema}, {'role': 'user', 'content': caso['pregunta']}],
                       perfil, modelo=modelo, max_tokens=1600)
            datos = json.loads(r['texto'])
            correcto = isinstance(datos, dict) and all(datos.get(k) == v and
                (not isinstance(v, bool) or type(datos.get(k)) is bool) for k, v in caso['esperado'].items())
            resultado = {'id': caso['id'], 'correcto': correcto, 'respuesta': r['texto'], 'modelo_real': r['modelo']}
        except Exception:
            resultado = {'id': caso['id'], 'correcto': False, 'error': 'Petición fallida o JSON inválido'}
        resultado['segundos'] = round(time.perf_counter()-t, 3)
        resultados.append(resultado)
    reporte = {'version': 1, 'suite_sha256': hashlib.sha256(SUITE.read_bytes()).hexdigest(),
               'perfil': perfil, 'modelo': modelo, 'casos': resultados,
               'aciertos': sum(r['correcto'] for r in resultados), 'total': len(resultados),
               'segundos': round(sum(r['segundos'] for r in resultados), 3),
               'alcance': 'Pruebas sintéticas sin ejecutar acciones. No certifica uso general.'}
    Path(salida).write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding='utf-8')
    return reporte


def comparar(base, candidato, limite_segundos=120):
    a, b = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (base, candidato))
    ids = [r['id'] for r in a['casos']]
    if (a['suite_sha256'] != b['suite_sha256'] or a['perfil'] != b['perfil']
            or a['suite_sha256'] != hashlib.sha256(SUITE.read_bytes()).hexdigest()
            or not ids or len(set(ids)) != len(ids) or ids != [r['id'] for r in b['casos']]):
        raise ValueError('Informes no comparables: misma suite, perfil y casos requeridos.')
    aciertos_a = sum(r['correcto'] is True for r in a['casos'])
    aciertos_b = sum(r['correcto'] is True for r in b['casos'])
    regresiones = [x['id'] for x, y in zip(a['casos'], b['casos']) if x['correcto'] and not y['correcto']]
    segundos = sum(r['segundos'] for r in b['casos'])
    pasa = (aciertos_b > aciertos_a and not regresiones and 0 <= segundos <= limite_segundos
            and not b['modelo'].lower().startswith('auto')
            and (b['perfil'] != 'acciones' or aciertos_b == len(b['casos'])))
    return {'elegible': pasa, 'regresiones': regresiones, 'aciertos_base': aciertos_a,
            'aciertos_candidato': aciertos_b, 'modelo': b['modelo'], 'perfil': b['perfil'],
            'nota': 'Exige mejora estricta y ninguna regresión; acciones exige todos los casos correctos.'}
