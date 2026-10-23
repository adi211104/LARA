"""Adaptador opcional /v1. No despacha herramientas ni administra claves upstream."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests


class GatewayError(RuntimeError):
    def __init__(self, mensaje, status_code=None):
        super().__init__(mensaje)
        self.status_code = status_code


def _local():
    try:
        datos = json.loads((Path(__file__).resolve().parents[1] / '.tools' / 'gateway-lara.json').read_text(encoding='utf-8'))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def activo():
    return os.getenv('LARA_GATEWAY_ENABLED', '1' if _local().get('enabled') else '0') == '1'


def _config():
    local = _local()
    url = os.getenv('LARA_GATEWAY_URL', local.get('url', 'http://127.0.0.1:3001/v1')).rstrip('/')
    partes = urlsplit(url)
    if (partes.scheme not in ('http', 'https') or partes.hostname not in ('localhost', '127.0.0.1', '::1')
            or partes.username or partes.password or partes.query or partes.fragment):
        raise GatewayError('El gateway debe ser una dirección loopback sin credenciales en la URL.')
    # La plantilla .env contiene campos vacíos: no deben ocultar la clave
    # unificada que el lanzador acaba de generar en la configuración local.
    clave = (os.getenv('LARA_GATEWAY_API_KEY', '').strip() or local.get('api_key', '')).strip()
    if not clave:
        raise GatewayError('Falta LARA_GATEWAY_API_KEY.')
    return url, clave


def modelos_acciones():
    modelos = os.getenv('LARA_GATEWAY_ACTION_MODELS', '').strip() or _local().get('action_models', '')
    return [m.strip() for m in modelos.split(',') if m.strip()]


def validar_decision(texto):
    try:
        d = json.loads(texto)
    except (ValueError, TypeError):
        raise GatewayError('El modelo no devolvió una decisión JSON.') from None
    if not isinstance(d, dict):
        raise GatewayError('Decisión no estructurada.')
    if (not isinstance(d.get('accion'), str) or not re.fullmatch(r'[a-z_]{1,48}', d['accion'])
            or not isinstance(d.get('mensaje'), str) or len(d['mensaje']) > 24000
            or not isinstance(d.get('params'), list) or len(d['params']) > 12
            or any(not isinstance(p, (str, int, float, bool)) or p is None for p in d['params'])
            or type(d.get('requiere_razonamiento_profundo', False)) is not bool):
        raise GatewayError('La decisión no cumple el esquema de LARA.')
    return d


def completar(messages, perfil='conversacion', *, modelo=None, max_tokens=1800, timeout_s=None, json_mode=False):
    """Una petición acotada. El router upstream gestiona sus propios reintentos."""
    url, clave = _config()
    if perfil not in ('acciones', 'conversacion'):
        raise GatewayError('Perfil desconocido.')
    permitidos = modelos_acciones()
    elegido = modelo or (permitidos[0] if perfil == 'acciones' and permitidos else
                         os.getenv('LARA_GATEWAY_CHAT_MODEL', 'auto:smart'))
    if perfil == 'acciones' and (not permitidos or elegido not in permitidos or elegido.lower().startswith('auto')):
        raise GatewayError('Configura modelos explícitos para acciones; auto está reservado a conversación.')
    if modelo is None:
        from core.aprendizaje import modelo_activo
        elegido = modelo_activo(perfil) or elegido
        if perfil == 'acciones' and (elegido not in permitidos or elegido.lower().startswith('auto')):
            raise GatewayError('El candidato activo no está permitido para acciones.')
    cuerpo = dict(model=elegido, messages=messages, temperature=0.2,
                  max_tokens=max_tokens, stream=False)
    if perfil == 'acciones' or json_mode:
        cuerpo['response_format'] = {'type': 'json_object'}
    try:
        with requests.Session() as sesion:
            sesion.trust_env = False
            r = sesion.post(url + '/chat/completions',
                            headers={'Authorization': 'Bearer ' + clave}, json=cuerpo,
                            allow_redirects=False,
                            timeout=(3, min(60, max(1, timeout_s if timeout_s is not None else float(os.getenv('LARA_GATEWAY_TIMEOUT', '20'))))))
            r.raise_for_status()
            data = r.json()
        choice = data['choices'][0]
        texto = choice['message']['content']
        if choice.get('finish_reason') in ('length', 'content_filter') or not isinstance(texto, str) or not texto.strip():
            raise GatewayError('Respuesta vacía, filtrada o truncada.')
        real = str(data.get('model') or elegido)
        if perfil == 'acciones':
            if real not in permitidos or real.split('/')[-1] != elegido.split('/')[-1]:
                raise GatewayError('El gateway cambió a un modelo de acciones no permitido.')
            validar_decision(texto)
        return {'texto': texto, 'modelo': real, 'perfil': perfil}
    except GatewayError:
        raise
    except requests.HTTPError as e:
        estado = e.response.status_code if e.response is not None else None
        raise GatewayError('Gateway rechazó la petición' + (f' (HTTP {estado}).' if estado else '.'), estado) from None
    except Exception:
        # Nunca incluir cuerpos upstream, cabeceras ni excepciones con claves.
        raise GatewayError('Gateway no disponible o respuesta incompatible.') from None


def diagnostico():
    try:
        url, clave = _config()
        with requests.Session() as sesion:
            sesion.trust_env = False
            r = sesion.get(url + '/models', headers={'Authorization': 'Bearer ' + clave}, timeout=(2, 4), allow_redirects=False)
            r.raise_for_status()
            cantidad = len(r.json().get('data', []))
        return {'disponible': True, 'modelos': cantidad, 'acciones': modelos_acciones(), 'activado': activo()}
    except Exception:
        return {'disponible': False, 'activado': activo(),
                'detalle': 'Revisa servicio local, clave unificada y URL. No se modificó la configuración.'}
