"""Instala y arranca una copia aislada de FreeLLMAPI, fijada a una revisión."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SERVICIO = ROOT / '.tools' / 'freellmapi'
REVISION = '26484157fd15f5fb66f54d3a86f568997ea304de'
CONFIG = ROOT / '.tools' / 'gateway-lara.json'


def instalar():
    import requests
    try:
        with requests.Session() as sesion:
            sesion.trust_env = False
            r = sesion.get('http://127.0.0.1:3001/api/auth/status', timeout=1)
            if r.ok:
                raise ValueError('Detén el gateway antes de instalar: python tools/gateway_lara.py detener')
    except requests.RequestException:
        pass
    if not SERVICIO.exists():
        subprocess.run(['git', 'clone', '--depth', '1', 'https://github.com/tashfeenahmed/freellmapi.git', str(SERVICIO)], check=True)
    actual = subprocess.check_output(['git', '-C', str(SERVICIO), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != REVISION:
        subprocess.run(['git', '-C', str(SERVICIO), 'fetch', '--depth', '1', 'origin', REVISION], check=True)
        # No resetear copias con modificaciones del usuario.
        cambios = subprocess.check_output(['git', '-C', str(SERVICIO), 'status', '--porcelain'], text=True)
        if cambios.strip():
            raise ValueError('La copia del gateway tiene cambios: conserva su revisión y revísalos antes de actualizar.')
        subprocess.run(['git', '-C', str(SERVICIO), 'checkout', '--detach', REVISION], check=True)
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise ValueError('Necesitas Node.js y npm compatibles con el package.json del gateway.')
    subprocess.run([npm, 'ci', '--workspace', 'shared', '--workspace', 'server', '--workspace', 'client', '--include-workspace-root', '--no-audit', '--no-fund'], cwd=SERVICIO, check=True)
    subprocess.run([npm, 'run', 'build', '-w', 'server'], cwd=SERVICIO, check=True)
    subprocess.run([npm, 'run', 'build', '-w', 'client'], cwd=SERVICIO, check=True)


def iniciar():
    from config import PROYECTO_DIR  # carga .env sin mostrarlo
    import requests
    if os.getenv('LARA_GATEWAY_ENABLED') == '0':
        print('Gateway desactivado en .env; se mantiene la conexión directa de LARA.')
        return
    if not (SERVICIO / 'server' / 'dist' / 'index.js').exists():
        raise ValueError('Primero ejecuta: python tools/gateway_lara.py instalar')
    url = 'http://127.0.0.1:3001'
    sesion = requests.Session()
    sesion.trust_env = False
    try:
        sesion.get(url + '/api/auth/status', timeout=1)
        if CONFIG.exists():
            print('Servicio ya accesible. Usa mejora_lara.py gateway para comprobar la clave.')
            return
        raise ValueError('El puerto 3001 ya está ocupado. No se cambia un servicio ajeno.')
    except requests.RequestException:
        pass
    secretos = ROOT / '.tools' / 'gateway-admin.json'
    if secretos.exists():
        cred = json.loads(secretos.read_text(encoding='utf-8'))
    else:
        cred = {'email': 'lara@localhost.local', 'password': secrets.token_urlsafe(32), 'encryption': secrets.token_hex(32)}
        secretos.write_text(json.dumps(cred), encoding='utf-8')
    claves = [{'platform': plataforma, 'key': os.environ[variable], 'label': 'LARA existente'}
              for plataforma, variable in (('groq', 'GROQ_API_KEY'), ('google', 'GEMINI_API_KEY'))
              if os.getenv(variable)]
    if not claves:
        raise ValueError('No hay claves Groq/Gemini existentes. Configura al menos una antes de iniciar.')
    db = SERVICIO / 'server' / 'data' / 'lara.db'
    anterior = json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.exists() else {}
    entorno = dict(os.environ, HOST='127.0.0.1', PORT='3001', NODE_ENV='production',
                   ENCRYPTION_KEY=cred['encryption'], FREEAPI_DB_PATH=str(db),
                   CLIENT_DIST=str(SERVICIO / 'client' / 'dist'),
                   FALLBACK_TIME_BUDGET_MS='15000', PROVIDER_TIMEOUT_GROQ='15000', PROVIDER_TIMEOUT_GOOGLE='15000',
                   FREEAPI_CONFIG_JSON=json.dumps({'admin': {'email': cred['email'], 'password': cred['password']},
                                                  'keys': claves, 'routing': {'strategy': 'reliable'}}))
    with (ROOT / '.tools' / 'gateway.stdout.log').open('a', encoding='utf-8') as stdout, \
         (ROOT / '.tools' / 'gateway.stderr.log').open('a', encoding='utf-8') as stderr:
        proceso = subprocess.Popen([shutil.which('node'), str(SERVICIO / 'server' / 'dist' / 'index.js')],
            cwd=SERVICIO, env=entorno, stdout=stdout, stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    for _ in range(40):
        if proceso.poll() is not None:
            raise ValueError('El servicio no arrancó. Diagnóstico en .tools/gateway.stderr.log (puede contener datos privados).')
        try:
            r = sesion.get(url + '/api/auth/status', timeout=1)
            if r.ok and db.exists():
                con = sqlite3.connect(str(db))
                try:
                    fila = con.execute("SELECT value FROM settings WHERE key='unified_api_key'").fetchone()
                finally:
                    con.close()
                if fila:
                    CONFIG.write_text(json.dumps(dict(anterior, enabled=True, url=url + '/v1', api_key=fila[0],
                        action_models=anterior.get('action_models', ''), pid=proceso.pid, revision=REVISION)), encoding='utf-8')
                    print('Gateway local iniciado. Se conserva el perfil de acciones previamente evaluado, si existe.')
                    return
        except (requests.RequestException, sqlite3.Error):
            pass
        time.sleep(0.5)
    proceso.terminate()
    raise ValueError('El servicio excedió el plazo de arranque y se detuvo.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('comando', choices=['instalar', 'iniciar', 'aprobar-acciones', 'detener'])
    p.add_argument('--informe')
    a = p.parse_args()
    try:
        if a.comando == 'instalar':
            instalar()
        elif a.comando == 'iniciar':
            iniciar()
        elif a.comando == 'detener':
            conf = json.loads(CONFIG.read_text(encoding='utf-8'))
            if os.name != 'nt':
                raise ValueError('Este lanzador de detención comprueba la identidad del proceso en Windows.')
            env = dict(os.environ, LARA_GATEWAY_PID=str(int(conf['pid'])),
                       LARA_GATEWAY_ENTRY=str(SERVICIO / 'server' / 'dist' / 'index.js'))
            comando = ("$procesoLara = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $env:LARA_GATEWAY_PID); "
                       "if ($procesoLara -and $procesoLara.Name -eq 'node.exe' -and "
                       "$procesoLara.CommandLine.Contains($env:LARA_GATEWAY_ENTRY)) "
                       "{ Stop-Process -Id $procesoLara.ProcessId; Write-Output 'Gateway detenido.' } "
                       "else { Write-Output 'No hay un proceso de este gateway que detener.' }")
            subprocess.run(['powershell', '-NoProfile', '-Command', comando], env=env, check=True)
        else:
            if not a.informe:
                raise ValueError('Indica --informe de una evaluación de acciones.')
            from core.evaluacion import SUITE
            import hashlib
            reporte = json.loads(Path(a.informe).read_text(encoding='utf-8'))
            esperados = [c['id'] for c in json.loads(SUITE.read_text(encoding='utf-8'))['acciones']]
            if (reporte['perfil'] != 'acciones' or reporte['suite_sha256'] != hashlib.sha256(SUITE.read_bytes()).hexdigest()
                    or [c['id'] for c in reporte['casos']] != esperados
                    or any(c['correcto'] is not True for c in reporte['casos'])
                    or reporte['modelo'].lower().startswith('auto')):
                raise ValueError('El informe no aprueba la suite actual completa de acciones.')
            conf = json.loads(CONFIG.read_text(encoding='utf-8'))
            if conf.get('action_models'):
                raise ValueError('Ya hay un perfil de acciones; usa comparar/promover para sustituirlo.')
            modelos = list(dict.fromkeys([reporte['modelo']] + [c['modelo_real'] for c in reporte['casos']]))
            conf['action_models'] = ','.join(modelos)
            CONFIG.write_text(json.dumps(conf), encoding='utf-8')
            print('Perfil de acciones habilitado tras aprobar la suite: ' + ', '.join(modelos))
    except (ValueError, subprocess.CalledProcessError) as e:
        print('Error: ' + str(e))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
