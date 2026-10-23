"""Registro local revisable. Ninguna respuesta del LLM se autoaprueba."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from config import KNOWLEDGE_DIR, PROYECTO_DIR

DIRECTORIO = KNOWLEDGE_DIR / 'aprendizaje'


@contextmanager
def _con():
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DIRECTORIO / 'experiencias.sqlite3'), timeout=5)
    c.row_factory = sqlite3.Row
    try:
        with c:
            c.execute('''CREATE TABLE IF NOT EXISTS experiencias (
                id INTEGER PRIMARY KEY, fecha REAL, pregunta TEXT, respuesta TEXT,
                estado TEXT, accion TEXT, motor TEXT, ms INTEGER, revision TEXT DEFAULT 'pendiente',
                correccion TEXT, evidencia TEXT, grupo TEXT)''')
            c.execute('CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT)')
            yield c
    finally:
        c.close()


def captura_activa():
    with _con() as c:
        f = c.execute("SELECT valor FROM ajustes WHERE clave='captura'").fetchone()
    return f['valor'] == '1' if f else os.getenv('LARA_LEARNING_CAPTURE', '0') == '1'


def configurar_captura(activa):
    with _con() as c:
        c.execute("INSERT OR REPLACE INTO ajustes VALUES ('captura', ?)", ('1' if activa else '0',))


def redactar(texto):
    t = str(texto or '')[:32000]
    for nombre, secreto in os.environ.items():
        if any(p in nombre.upper() for p in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET')) and len(secreto) >= 8:
            t = t.replace(secreto, '[SECRETO]')
    for nombre in ('gateway-lara.json', 'gateway-admin.json'):
        try:
            valores = json.loads((PROYECTO_DIR / '.tools' / nombre).read_text(encoding='utf-8'))
            for campo in ('api_key', 'password', 'encryption'):
                secreto = valores.get(campo, '')
                if len(secreto) >= 8:
                    t = t.replace(secreto, '[SECRETO]')
        except (OSError, ValueError):
            pass
    t = re.sub(r'(?i)\b(?:sk-|gsk_|freellmapi-)[\w-]{8,}', '[SECRETO]', t)
    t = re.sub(r'(?i)(api[_ -]?key|password|contraseña|token)\s*[:=]\s*\S+', r'\1=[SECRETO]', t)
    t = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[CORREO]', t)
    t = re.sub(r'(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)', '[TELEFONO]', t)
    t = re.sub(r'(?i)[a-z]:\\Users\\[^\\\s]+', r'C:\\Users\\[USUARIO]', t)
    return t


def registrar(pregunta, respuesta, estado, accion, motor, ms):
    if not captura_activa():
        return None
    p, r = redactar(pregunta), redactar(respuesta)
    grupo = hashlib.sha256(' '.join(p.lower().split()).encode()).hexdigest()
    with _con() as c:
        cur = c.execute('''INSERT INTO experiencias
            (fecha,pregunta,respuesta,estado,accion,motor,ms,grupo) VALUES (?,?,?,?,?,?,?,?)''',
            (time.time(), p, r, estado, accion or '', motor or '', ms, grupo))
        return cur.lastrowid


def listar(limite=30):
    with _con() as c:
        return [dict(f) for f in c.execute('SELECT * FROM experiencias ORDER BY id DESC LIMIT ?', (limite,))]


def revisar(identificador, revision, correccion='', evidencia=''):
    if revision not in ('aprobado', 'corregido', 'rechazado'):
        raise ValueError('Revisión desconocida.')
    if revision == 'corregido' and not correccion.strip():
        raise ValueError('Falta la respuesta corregida.')
    if revision != 'rechazado' and not evidencia.strip():
        raise ValueError('Indica cómo comprobaste la respuesta; el modelo no puede aprobarse a sí mismo.')
    with _con() as c:
        fila = c.execute('SELECT * FROM experiencias WHERE id=?', (identificador,)).fetchone()
        if not fila:
            raise ValueError('Ejemplo inexistente.')
        if revision == 'aprobado' and fila['estado'] != 'ok':
            raise ValueError('Un fallo necesita una corrección, no una aprobación.')
        c.execute('UPDATE experiencias SET revision=?,correccion=?,evidencia=? WHERE id=?',
                  (revision, redactar(correccion), redactar(evidencia), identificador))


def estado():
    with _con() as c:
        cuentas = {f[0]: f[1] for f in c.execute('SELECT revision,count(*) FROM experiencias GROUP BY revision')}
    return {'captura': captura_activa(), 'ejemplos': cuentas, 'modelo_activo': _leer_activo()}


def generar_candidatos():
    """Variantes de órdenes con respuesta conocida. Son propuestas, nunca aprobaciones."""
    activo_antes = captura_activa()
    identificadores = []
    try:
        configurar_captura(True)
        for programa in ('calculadora', 'bloc de notas', 'explorador', 'chrome', 'firefox'):
            for patron in ('Abre {}', 'Por favor abre {}', 'Quiero abrir {}', 'Inicia {}'):
                pregunta = 'Devuelve una decisión JSON de LARA (accion, params, mensaje): ' + patron.format(programa)
                respuesta = json.dumps({'accion': 'abrir', 'params': [programa],
                                        'mensaje': 'Voy a abrir ' + programa,
                                        'requiere_razonamiento_profundo': False}, ensure_ascii=False)
                identificadores.append(registrar(pregunta, respuesta, 'ok', 'abrir', 'plantilla_pendiente', 0))
    finally:
        configurar_captura(activo_antes)
    return {'generados': len(identificadores), 'ids': identificadores,
            'nota': 'Propuestas locales pendientes: revisar antes de exportar; no se ejecutó ningún programa.'}


def exportar(destino):
    """Solo revisados; misma pregunta siempre en el mismo split, rechazos por grupo excluidos."""
    with _con() as c:
        filas = c.execute('SELECT * FROM experiencias ORDER BY id').fetchall()
    bloqueados = {f['grupo'] for f in filas if f['revision'] == 'rechazado'}
    grupos = {}
    for f in filas:
        if f['grupo'] not in bloqueados and f['revision'] in ('aprobado', 'corregido'):
            grupos[f['grupo']] = f
    if len(grupos) < 10:
        raise ValueError('Se requieren al menos 10 preguntas distintas revisadas para separar train/validación/test.')
    destino = Path(destino)
    particiones = {'train': [], 'validacion': [], 'test': []}
    # Partición por hash estable (no cambia al añadir nuevas experiencias).
    for g, f in sorted(grupos.items()):
        n = int(g[:8], 16) % 10
        split = 'test' if n == 0 else 'validacion' if n == 1 else 'train'
        respuesta = f['correccion'] if f['revision'] == 'corregido' else f['respuesta']
        particiones[split].append({'messages': [{'role': 'user', 'content': f['pregunta']},
                                               {'role': 'assistant', 'content': respuesta}]})
    if any(not v for v in particiones.values()):
        raise ValueError('Faltan ejemplos en alguna partición; revisa más preguntas antes de entrenar.')
    destino.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for nombre, filas_split in particiones.items():
        contenido = ''.join(json.dumps(f, ensure_ascii=False) + '\n' for f in filas_split)
        archivo = destino / (nombre + '.jsonl')
        archivo.write_text(contenido, encoding='utf-8')
        hashes[archivo.name] = hashlib.sha256(archivo.read_bytes()).hexdigest()
    manifiesto = {'version': 1, 'archivos': hashes,
                  'procedencia': [{'id': f['id'], 'grupo': g, 'revision': f['revision']} for g, f in sorted(grupos.items())],
                  'cantidades': {k: len(v) for k, v in particiones.items()},
                  'nota': 'Redacción heurística: revisar datos antes de subir. Test nunca se usa para entrenar.'}
    (destino / 'manifest.json').write_text(json.dumps(manifiesto, indent=2), encoding='utf-8')
    return manifiesto


def _leer_activo():
    try:
        datos = json.loads((DIRECTORIO / 'activo.json').read_text(encoding='utf-8'))
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def modelo_activo(perfil):
    return _leer_activo().get(perfil)


def activar(perfil, modelo):
    DIRECTORIO.mkdir(parents=True, exist_ok=True)
    anterior = _leer_activo()
    (DIRECTORIO / 'anterior.json').write_text(json.dumps(anterior), encoding='utf-8')
    nuevo = dict(anterior, **{perfil: modelo})
    temporal = DIRECTORIO / 'activo.tmp'
    temporal.write_text(json.dumps(nuevo), encoding='utf-8')
    temporal.replace(DIRECTORIO / 'activo.json')


def revertir():
    anterior = DIRECTORIO / 'anterior.json'
    if not anterior.exists():
        raise ValueError('No hay versión anterior.')
    datos = json.loads(anterior.read_text(encoding='utf-8'))
    actual = _leer_activo()
    temporal = DIRECTORIO / 'activo.tmp'
    temporal.write_text(json.dumps(datos), encoding='utf-8')
    temporal.replace(DIRECTORIO / 'activo.json')
    anterior.write_text(json.dumps(actual), encoding='utf-8')


def comando(texto, ultimo_id, autorizar):
    t = texto.strip().lower().rstrip('.!?')
    if t in ('aprendizaje', 'estado de aprendizaje'):
        e = estado()
        return (f"Registro para aprendizaje: {'activo' if e['captura'] else 'apagado'}. "
                f"Ejemplos por revisión: {e['ejemplos']}.\n"
                "Actívalo con 'activar aprendizaje'. Después usa 'respuesta correcta', "
                "'respuesta incorrecta' o 'corrige respuesta: texto correcto'. "
                "El registro es local y redacta datos comunes; no entrena ni sube conversaciones automáticamente.")
    revision = None
    correccion = ''
    if t == 'respuesta correcta':
        revision = 'aprobado'
    elif t == 'respuesta incorrecta':
        revision = 'rechazado'
    elif t.startswith('corrige respuesta:'):
        revision, correccion = 'corregido', texto.split(':', 1)[1].strip()
    ajuste = t in ('activar aprendizaje', 'desactivar aprendizaje')
    if not ajuste and not revision:
        return None
    permitido, motivo = autorizar('aprendizaje', [texto])
    if not permitido:
        return motivo
    if ajuste:
        configurar_captura(t == 'activar aprendizaje')
        return 'Registro local activado. Los ejemplos quedan pendientes de revisión.' if t.startswith('activar') else 'Registro para aprendizaje desactivado.'
    if ultimo_id is None:
        return 'No hay una respuesta registrada en este chat para revisar. Activa aprendizaje y realiza una consulta.'
    try:
        revisar(ultimo_id, revision, correccion, 'Revisión explícita del usuario en el chat')
        return f'Ejemplo #{ultimo_id}: {revision}. No se ha entrenado ni activado ningún modelo.'
    except ValueError as e:
        return str(e)
