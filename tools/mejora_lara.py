"""Control del aprendizaje y evaluaciones; no ejecuta acciones producidas por modelos."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest='comando', required=True)
    for nombre in ('estado', 'listar', 'gateway', 'revertir', 'generar'):
        s.add_parser(nombre)
    captura = s.add_parser('captura')
    captura.add_argument('valor', choices=['on', 'off'])
    revision = s.add_parser('revisar')
    revision.add_argument('id', type=int)
    revision.add_argument('revision', choices=['aprobado', 'corregido', 'rechazado'])
    revision.add_argument('--correccion', default='')
    revision.add_argument('--evidencia', default='')
    exportar = s.add_parser('exportar')
    exportar.add_argument('--destino', required=True)
    evaluar = s.add_parser('evaluar')
    evaluar.add_argument('--modelo', required=True)
    evaluar.add_argument('--perfil', choices=['acciones', 'conversacion'], required=True)
    evaluar.add_argument('--salida', required=True)
    ciclo = s.add_parser('ciclo')
    ciclo.add_argument('--candidato', required=True, help='ID de un modelo ya servido por el gateway')
    ciclo.add_argument('--perfil', choices=['acciones', 'conversacion'], required=True)
    ciclo.add_argument('--destino', required=True, help='Carpeta nueva de informes')
    ciclo.add_argument('--activar-si-mejora', action='store_true')
    for nombre in ('comparar', 'promover'):
        c = s.add_parser(nombre)
        c.add_argument('--base', required=True)
        c.add_argument('--candidato', required=True)
        c.add_argument('--limite-segundos', type=float, default=120)
    a = p.parse_args()
    from core import aprendizaje as ap, gateway_llm as gw, evaluacion as ev
    try:
        if a.comando == 'estado':
            r = ap.estado()
        elif a.comando == 'listar':
            r = ap.listar()
        elif a.comando == 'captura':
            ap.configurar_captura(a.valor == 'on')
            r = ap.estado()
        elif a.comando == 'generar':
            r = ap.generar_candidatos()
        elif a.comando == 'revisar':
            ap.revisar(a.id, a.revision, a.correccion, a.evidencia)
            r = {'id': a.id, 'revision': a.revision}
        elif a.comando == 'exportar':
            r = ap.exportar(a.destino)
        elif a.comando == 'gateway':
            r = gw.diagnostico()
        elif a.comando == 'revertir':
            ap.revertir()
            r = ap.estado()
        elif a.comando == 'evaluar':
            r = ev.evaluar(a.modelo, a.perfil, a.salida)
            r = {k: v for k, v in r.items() if k != 'casos'}
        elif a.comando == 'ciclo':
            carpeta = Path(a.destino)
            carpeta.mkdir(parents=True, exist_ok=False)
            base = ap.modelo_activo(a.perfil) or (gw.modelos_acciones()[0] if a.perfil == 'acciones' and gw.modelos_acciones()
                                                  else os.getenv('LARA_GATEWAY_CHAT_MODEL', 'auto:smart'))
            ev.evaluar(base, a.perfil, carpeta / 'base.json')
            ev.evaluar(a.candidato, a.perfil, carpeta / 'candidato.json')
            r = ev.comparar(carpeta / 'base.json', carpeta / 'candidato.json')
            if r['elegible'] and a.activar_si_mejora:
                ap.activar(a.perfil, a.candidato)
                r['activado'] = True
            (carpeta / 'comparacion.json').write_text(json.dumps(r, indent=2), encoding='utf-8')
        else:
            r = ev.comparar(a.base, a.candidato, a.limite_segundos)
            if a.comando == 'promover':
                if not r['elegible']:
                    raise ValueError('Candidato no elegible: no se cambia el modelo activo.')
                if r['perfil'] == 'acciones' and (r['modelo'] not in gw.modelos_acciones() or r['modelo'].lower().startswith('auto')):
                    raise ValueError('El candidato de acciones debe estar en la lista explícita permitida.')
                ap.activar(r['perfil'], r['modelo'])
                r['activado'] = True
        print(json.dumps(r, ensure_ascii=False, indent=2))
    except (ValueError, OSError, gw.GatewayError) as e:
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
