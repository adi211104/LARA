"""Pruebas reales con audio sintético; nunca abre el micrófono ni ejecuta comandos reconocidos."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unicodedata
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def palabras(texto):
    return re.findall(r'\w+', ''.join(c for c in unicodedata.normalize('NFD', texto.lower())
                                    if unicodedata.category(c) != 'Mn'))


def wer(referencia, texto):
    a, b = palabras(referencia), palabras(texto)
    fila = list(range(len(b) + 1))
    for i, palabra in enumerate(a, 1):
        siguiente = [i]
        for j, otra in enumerate(b, 1):
            siguiente.append(min(fila[j] + 1, siguiente[j-1] + 1, fila[j-1] + (palabra != otra)))
        fila = siguiente
    return round(fila[-1] / max(1, len(a)), 3)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser()
    p.add_argument('modo', choices=['voz', 'web', 'web_chat'])
    p.add_argument('--salida', required=True)
    args = p.parse_args()
    resultados = []
    def guardar(dato):
        resultados.append(dato)
        Path(args.salida).write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dato, ensure_ascii=False), flush=True)
    with tempfile.TemporaryDirectory(prefix='lara_voz_web_') as tmp:
        os.environ['LARA_DATA_DIR'] = tmp
        # Solo usar modelos locales ya descargados; no instalar pesos por sorpresa.
        os.environ['HF_HUB_OFFLINE'] = '1'
        from core import llm_engine
        if args.modo == 'web_chat':
            from core.cerebro import LaraCerebro
            from core import skills_voz
            skills_voz.set_voz_activa(False)
            estados = []
            cerebro = LaraCerebro(on_status=estados.append, on_speak=lambda _: None,
                                 on_confirm=lambda *args: False)
            consulta = 'Busca en internet la documentación oficial de Python sobre listas y dame los enlaces encontrados.'
            t = time.perf_counter()
            respuesta = cerebro.procesar(consulta)
            guardar(dict(consulta=consulta, respuesta=respuesta, estados=estados,
                         segundos=round(time.perf_counter()-t, 3)))
        elif args.modo == 'web':
            for consulta in ('site:docs.python.org/3/tutorial listas Python',
                             'site:nasa.gov Moon facts'):
                t = time.perf_counter()
                respuesta, urls = llm_engine.buscar_en_web(consulta)
                guardar(dict(consulta=consulta, segundos=round(time.perf_counter()-t, 3),
                             respuesta=respuesta, urls=urls))
        else:
            import numpy as np
            from core import skills_voz as voz, stt_local
            voz.set_voz_activa(False)
            voz._dir_tmp = tmp
            frases = ['Lara abre el bloc de notas',
                      'Recuérdame revisar el amplificador en quince minutos',
                      'Busca en internet cómo funciona un transistor']
            audios = []
            for frase in frases:
                t = time.perf_counter()
                ruta = voz._gen_edge(frase)
                motor_muestra = 'edge'
                if not ruta:
                    ruta = voz._gen_kokoro(frase)
                    motor_muestra = 'kokoro'
                if not ruta:
                    guardar(dict(caso='sintesis', referencia=frase, error='No se pudo generar muestra Edge'))
                    continue
                r = subprocess.run(['ffmpeg', '-v', 'error', '-i', ruta, '-f', 's16le',
                                    '-ac', '1', '-ar', '16000', 'pipe:1'], capture_output=True, timeout=30)
                if r.returncode:
                    guardar(dict(caso='decodificacion', error='ffmpeg no pudo decodificar la muestra'))
                    continue
                audios.append((frase, r.stdout))
                guardar(dict(caso='sintesis', referencia=frase, motor= motor_muestra, segundos=round(time.perf_counter()-t, 3)))
            if audios:
                frase, pcm = audios[0]
                datos = np.frombuffer(pcm, dtype=np.int16).astype(float)
                rng = np.random.default_rng(42)
                ruido = rng.normal(0, max(1, np.sqrt(np.mean(datos**2))) / 3.162, datos.size)
                audios.append((frase, np.clip(datos+ruido, -32768, 32767).astype(np.int16).tobytes()))
            for i, (frase, pcm) in enumerate(audios):
                t = time.perf_counter()
                salida = voz.transcribir_crudo(pcm, 16000, len(pcm)/32000, time.time())
                guardar(dict(caso='transcripcion', muestra=i, referencia=frase,
                             texto=salida.get('texto'), error=salida.get('error'),
                             segundos=round(time.perf_counter()-t, 3),
                             wer=wer(frase, salida.get('texto', '')),
                             local_disponible=stt_local.disponible()))
            t = time.perf_counter()
            salida = voz.transcribir_crudo(bytes(16000*2*3), 16000, 3, time.time())
            guardar(dict(caso='silencio', texto=salida.get('texto'), error=salida.get('error'),
                         segundos=round(time.perf_counter()-t, 3)))
        import logging
        logging.shutdown()


if __name__ == '__main__':
    main()
