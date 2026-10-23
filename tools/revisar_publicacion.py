"""Auditoría conservadora previa a publicar. Nunca imprime valores secretos."""
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATRONES = [
    re.compile(rb'(?:gsk_|gh[pousr]_|hf_)[A-Za-z0-9_\-]{20,}'),
    re.compile(rb'AIza[A-Za-z0-9_\-]{30,}'),
    re.compile(rb'\bsk-[A-Za-z0-9_\-]{24,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
]


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def revisar():
    archivos = sorted(set(git('ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')) - {''})
    hallazgos = []
    for nombre in archivos:
        p = ROOT / nombre
        if not p.is_file():
            continue
        if p.stat().st_size > 50 * 1024**2:
            hallazgos.append({'archivo': nombre, 'motivo': 'Archivo mayor de 50 MB'})
            continue
        contenido = p.read_bytes()
        if any(patron.search(contenido) for patron in PATRONES):
            hallazgos.append({'archivo': nombre, 'motivo': 'Posible secreto; revisar sin publicarlo'})
        if p.name == '.env' or p.suffix in ('.db', '.sqlite', '.sqlite3', '.key') or '.tools' in p.parts:
            hallazgos.append({'archivo': nombre, 'motivo': 'Estado privado o credenciales'})
    # Comprobar también objetos históricos, sin mostrar diffs ni sus contenidos.
    historicos = 0
    for linea in git('rev-list', '--objects', '--all').decode().splitlines():
        oid, _, nombre = linea.partition(' ')
        if not nombre:
            continue
        tipo = git('cat-file', '-t', oid).strip()
        if tipo != b'blob':
            continue
        tamano = int(git('cat-file', '-s', oid))
        if tamano > 50 * 1024**2:
            hallazgos.append({'archivo': nombre, 'motivo': 'Objeto histórico mayor de 50 MB'})
            continue
        contenido = git('cat-file', 'blob', oid)
        historicos += 1
        if any(patron.search(contenido) for patron in PATRONES):
            hallazgos.append({'archivo': nombre, 'motivo': 'Posible secreto en historial'})
    return {'archivos_actuales': len(archivos), 'objetos_historicos': historicos,
            'hallazgos': hallazgos,
            'limite': 'Detección heurística: no certifica ausencia de datos personales o secretos desconocidos.'}


if __name__ == '__main__':
    print(json.dumps(revisar(), ensure_ascii=False, indent=2))
