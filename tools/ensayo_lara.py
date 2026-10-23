"""Ensayo real aislado del agente: genera, prueba y modifica un programa, sin ventanas."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--salida', required=True)
    p.add_argument('--corregir', action='store_true', help='Devolver fallos del verificador al agente, máximo dos reparaciones')
    a = p.parse_args()
    reporte = {'inicio': time.strftime('%Y-%m-%dT%H:%M:%S'), 'eventos': [], 'comprobaciones': {}}
    destino = Path(a.salida).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    def guardar():
        destino.write_text(json.dumps(reporte, ensure_ascii=False, indent=2), encoding='utf-8')
    with tempfile.TemporaryDirectory(prefix='lara_ensayo_') as datos:
        os.environ['LARA_DATA_DIR'] = datos
        os.environ['LARA_LEARNING_CAPTURE'] = '0'
        from core.open_code import AgenteCodigo
        from core import modos
        modos._modo_actual = 'auto'
        carpeta = Path(datos) / 'proyecto'
        carpeta.mkdir()
        def log(tipo, texto):
            reporte['eventos'].append({'tipo': tipo, 'texto': texto[:5000], 'segundos': round(time.perf_counter()-inicio, 3)})
            guardar()
            print(tipo + ': ' + texto[:120], flush=True)
        class AgenteEnsayo(AgenteCodigo):
            def _cmd(self, comando, entrada=None):
                # Este ensayo solo permite scripts Python locales; sin shell,
                # red, instalaciones, ejecutables ni comandos encadenados.
                m = re.fullmatch(r'python(?:\.exe)?\s+([\w.-]+\.py)', comando.strip())
                if not m:
                    return 'Prueba aislada: usa python archivo.py con entrada de prueba. No instalar paquetes.'
                archivo = self._resolver(m[1])
                entorno = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))}
                entorno.update(PYTHONIOENCODING='utf-8')
                try:
                    r = subprocess.run([sys.executable, str(archivo)], input=entrada or '',
                        capture_output=True, text=True, encoding='utf-8', errors='replace',
                        cwd=self.carpeta, env=entorno, timeout=5)
                    return f'Código de salida: {r.returncode}\n{r.stdout[:2000]}\n{r.stderr[:500]}'
                except subprocess.TimeoutExpired:
                    return 'La prueba excedió 5 segundos.'
        inicio = time.perf_counter()
        agente = AgenteEnsayo(str(carpeta), on_log=log)
        instruccion = ('Crea rectangulo.py sin dependencias. Función dibujar(ancho, alto) devuelve un str con '
            'exactamente alto líneas de ancho caracteres: borde * e interior espacios. Si las medidas no '
            'son enteros positivos, lanza ValueError. Al ejecutarlo pide ancho y alto con input, muestra '
            'el dibujo y espera un ENTER final. Prueba con entrada 5,3 y ENTER. No crees imágenes ni otros programas.')
        agente.ejecutar_tarea(instruccion)
        reporte['crear_s'] = round(time.perf_counter()-inicio, 3)
        def verificar(simbolo):
            archivo = carpeta / 'rectangulo.py'
            if not archivo.exists():
                return {'correcto': False, 'error': 'No existe rectangulo.py'}
            # Verificador independiente; no usa assertions suministradas por el LLM.
            codigo = ("import runpy; d=runpy.run_path('rectangulo.py'); f=d['dibujar']; "
                "assert f(5,3)==B*5+'\\n'+B+' '*3+B+'\\n'+B*5; "
                "assert f(1,4)=='\\n'.join([B]*4); assert f(4,1)==B*4\n"
                "for w,h in [(0,3),(-1,2),(2,0),(1.5,2)]:\n"
                " try: f(w,h)\n except ValueError: pass\n else: raise AssertionError('Aceptó medidas inválidas')\n")
            env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ('KEY', 'TOKEN', 'SECRET', 'PASSWORD'))}
            try:
                r = subprocess.run([sys.executable, '-c', 'B='+repr(simbolo)+';'+codigo], cwd=carpeta,
                    env=env, capture_output=True, text=True, timeout=5)
                return {'correcto': r.returncode == 0, 'error': r.stderr[-1500:]}
            except subprocess.TimeoutExpired:
                return {'correcto': False, 'error': 'timeout'}
        reporte['comprobaciones']['rectangulo'] = verificar('*')
        if a.corregir and not reporte['comprobaciones']['rectangulo']['correcto']:
            reporte['fallo_inicial'] = reporte['comprobaciones']['rectangulo']
            for _ in range(2):
                agente.ejecutar_tarea('El verificador independiente encontró un fallo. Corrige rectangulo.py conservando el objetivo. '
                    'dibujar(1,4) debe devolver "*\\n*\\n*\\n*" y dibujar(4,1) "****". '
                    'Prueba ambos casos y las medidas inválidas con un script de prueba sin input. Error: ' +
                    reporte['comprobaciones']['rectangulo']['error'][-1000:])
                reporte['comprobaciones']['rectangulo'] = verificar('*')
                if reporte['comprobaciones']['rectangulo']['correcto']:
                    break
        reporte['crear_con_reparacion_s'] = round(time.perf_counter()-inicio, 3)
        guardar()
        inicio_seguimiento = time.perf_counter()
        agente.ejecutar_tarea('Ahora cambia el borde del programa que hiciste por #. Conserva las medidas y la entrada de terminal. Comprueba el cambio.')
        reporte['seguimiento_s'] = round(time.perf_counter()-inicio_seguimiento, 3)
        reporte['comprobaciones']['seguimiento'] = verificar('#')
        if a.corregir and not reporte['comprobaciones']['seguimiento']['correcto']:
            agente.ejecutar_tarea('El verificador detectó regresión tras cambiar el borde. Corrige solo rectangulo.py. '
                'dibujar(1,4) debe devolver "#\\n#\\n#\\n#", dibujar(4,1) "####", y 5x3 tiene borde #. '
                'Conserva ValueError para medidas inválidas. Usa un script de pruebas local y termina con resultados reales.')
            reporte['comprobaciones']['seguimiento'] = verificar('#')
        reporte['seguimiento_con_reparacion_s'] = round(time.perf_counter()-inicio_seguimiento, 3)
        recuperado = AgenteEnsayo(str(carpeta), session_id=agente.session_id, on_log=log)
        t = time.perf_counter()
        with patch('core.open_code.subprocess.Popen') as abrir:
            recuperado.ejecutar_tarea('abre el programa')
        reporte['abrir_s'] = round(time.perf_counter()-t, 3)
        reporte['comprobaciones']['abrir_correcto'] = bool(abrir.call_count == 1 and
            Path(abrir.call_args.args[0][1]).name == 'rectangulo.py')
        reporte['comprobaciones']['misma_sesion'] = recuperado.session_id == agente.session_id
        reporte['nota'] = 'Modelo real, verificadores independientes; apertura visible simulada. No es sandbox del sistema operativo.'
        artefactos = destino.parent / (destino.stem + '_archivos')
        artefactos.mkdir(exist_ok=True)
        for f in carpeta.glob('*.py'):
            (artefactos / f.name).write_bytes(f.read_bytes())
        # Solo conservar como propuesta supervisada un programa que pasó ambas comprobaciones.
        if (reporte['comprobaciones']['rectangulo']['correcto']
                and reporte['comprobaciones']['seguimiento']['correcto']):
            ejemplo = {'messages': [{'role': 'user', 'content': instruccion.replace('borde *', 'borde #')},
                                    {'role': 'assistant', 'content': (carpeta/'rectangulo.py').read_text(encoding='utf-8')}],
                       'evidencia': 'Verificador independiente de medidas 5x3,1x4,4x1 y ValueError para medidas inválidas.',
                       'estado': 'verificado_en_ensayo_no_exportado_a_train'}
            (destino.parent/(destino.stem+'_ejemplo.json')).write_text(json.dumps(ejemplo, ensure_ascii=False, indent=2), encoding='utf-8')
        guardar()
        import logging
        logging.shutdown()
    print(json.dumps({k:v for k,v in reporte.items() if k != 'eventos'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
