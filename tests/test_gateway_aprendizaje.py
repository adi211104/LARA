import _entorno
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from core import aprendizaje as ap, evaluacion as ev, gateway_llm as gw
from core.cerebro import LaraCerebro
from tools.entrenar_lara import verificar_dataset


class Servidor(BaseHTTPRequestHandler):
    modo = 'ok'
    recibido = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"probado"}]}')

    def do_POST(self):
        Servidor.recibido = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.send_response(200)
        self.end_headers()
        contenido = json.dumps({'accion': 'hablar', 'params': [], 'mensaje': 'hola'})
        if self.modo == 'invalido':
            contenido = 'no es JSON'
        self.wfile.write(json.dumps({'model': 'no-permitido' if self.modo == 'cambio' else 'probado',
            'choices': [{'finish_reason': 'length' if self.modo == 'truncado' else 'stop',
                         'message': {'content': contenido}}]}).encode())


class Integracion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Servidor)
        cls.hilo = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.hilo.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.hilo.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ruta = Path(self.tmp.name)
        self.parche = patch.object(ap, 'DIRECTORIO', self.ruta / 'aprendizaje')
        self.parche.start()
        self.addCleanup(self.parche.stop)
        env = patch.dict(os.environ, {'LARA_GATEWAY_ENABLED': '1', 'LARA_LEARNING_CAPTURE': '0',
            'LARA_GATEWAY_URL': f'http://127.0.0.1:{self.server.server_port}/v1',
            'LARA_GATEWAY_API_KEY': 'clave-solo-de-prueba', 'LARA_GATEWAY_ACTION_MODELS': 'probado'})
        env.start()
        self.addCleanup(env.stop)
        Servidor.modo = 'ok'

    def test_http_real_y_esquema(self):
        r = gw.completar([{'role': 'user', 'content': 'hola'}], 'acciones')
        self.assertEqual(gw.validar_decision(r['texto'])['accion'], 'hablar')
        self.assertEqual(Servidor.recibido['model'], 'probado')
        self.assertEqual(Servidor.recibido['response_format'], {'type': 'json_object'})
        self.assertTrue(gw.diagnostico()['disponible'])

    def test_plantilla_vacia_no_oculta_clave_local_generada(self):
        with patch.dict(os.environ, {'LARA_GATEWAY_API_KEY': ''}), \
             patch.object(gw, '_local', return_value={'api_key': 'valor-local-de-prueba'}):
            self.assertEqual(gw._config()[1], 'valor-local-de-prueba')

    def test_plantilla_vacia_no_oculta_perfil_local_aprobado(self):
        with patch.dict(os.environ, {'LARA_GATEWAY_ACTION_MODELS': ''}), \
             patch.object(gw, '_local', return_value={'action_models': 'evaluado'}):
            self.assertEqual(gw.modelos_acciones(), ['evaluado'])

    def test_rechaza_truncamiento_cambio_de_modelo_y_json_invalido(self):
        for modo in ('truncado', 'cambio', 'invalido'):
            Servidor.modo = modo
            with self.assertRaises(gw.GatewayError):
                gw.completar([], 'acciones')

    def test_no_auto_en_acciones_ni_destinos_remotos(self):
        with self.assertRaises(gw.GatewayError):
            gw.completar([], 'acciones', modelo='auto:smart')
        with patch.dict(os.environ, {'LARA_GATEWAY_URL': 'https://example.org/v1'}):
            with self.assertRaises(gw.GatewayError):
                gw.completar([])

    def test_motor_y_stream_usan_gateway(self):
        from core import llm_engine as llm
        from unittest.mock import Mock
        with patch.object(llm, '_router_rapido', return_value=None), \
             patch.object(llm, '_apto_para_local', return_value=False), \
             patch.dict(os.environ, {'LARA_GATEWAY_REASONING_MODEL': 'probado'}), \
             patch.object(llm, '_llamar_gemini', side_effect=AssertionError('No usar red externa')), \
             patch.object(llm, 'client', Mock()) as groq:
            self.assertEqual(json.loads(llm.consultar_lara('hola', 'sistema', []))['mensaje'], 'hola')
            self.assertIn('Gateway', llm.get_ultimo_motor())
            self.assertTrue(list(llm.razonar_profundo_stream('pregunta', '', [])))
            groq.chat.completions.create.assert_not_called()

    def test_captura_por_defecto_apagada_y_redaccion(self):
        self.assertIsNone(ap.registrar('pregunta', 'respuesta', 'ok', '', '', 1))
        ap.configurar_captura(True)
        with patch.dict(os.environ, {'DEMO_API_KEY': 'secreto-super-largo'}):
            ap.registrar('correo persona@example.com', 'clave secreto-super-largo', 'ok', '', '', 10)
        dato = ap.listar()[0]
        self.assertNotIn('persona@example.com', dato['pregunta'])
        self.assertNotIn('secreto-super-largo', dato['respuesta'])
        self.assertEqual(dato['revision'], 'pendiente')

    def test_comandos_feedback_y_permiso(self):
        c = LaraCerebro(on_speak=lambda _: None, on_confirm=lambda *args: True)
        c.procesar('activar aprendizaje')
        c.procesar('ayuda')
        c.procesar('respuesta correcta')
        filas = ap.listar()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]['revision'], 'aprobado')
        self.assertIn('aprobado', c.procesar('aprendizaje').lower())
        ap.comando('desactivar aprendizaje', None, lambda *a: (False, 'denegado'))
        self.assertTrue(ap.captura_activa())

    def test_revision_y_particiones_sin_fugas(self):
        ap.configurar_captura(True)
        pendiente = ap.registrar('No revisado jamás', 'Respuesta', 'ok', '', '', 2)
        for i in range(80):
            ident = ap.registrar(f'Consulta única número {i}', f'Respuesta número {i}', 'ok', '', '', 1)
            ap.revisar(ident, 'aprobado', evidencia='Comprobación manual')
        with self.assertRaises(ValueError):
            ap.revisar(pendiente, 'aprobado')
        manifiesto = ap.exportar(self.ruta / 'datos')
        self.assertEqual(sum(manifiesto['cantidades'].values()), 80)
        self.assertEqual(verificar_dataset(self.ruta / 'datos'), manifiesto)
        contenido = (self.ruta / 'datos' / 'train.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('No revisado jamás', contenido)
        with (self.ruta / 'datos' / 'train.jsonl').open('a', encoding='utf-8') as f:
            f.write('{}\n')
        with self.assertRaises(ValueError):
            verificar_dataset(self.ruta / 'datos')

    def test_no_autoaprueba_errores(self):
        ap.configurar_captura(True)
        i = ap.registrar('pregunta fallida', '', 'error', '', '', 1)
        with self.assertRaises(ValueError):
            ap.revisar(i, 'aprobado', evidencia='sin prueba')
        ap.revisar(i, 'corregido', 'respuesta correcta', 'Revisado por persona')

    def test_generacion_no_aprueba_ni_activa_captura(self):
        r = ap.generar_candidatos()
        self.assertEqual(r['generados'], 20)
        self.assertFalse(ap.captura_activa())
        self.assertTrue(all(f['revision'] == 'pendiente' for f in ap.listar()))

    def test_evaluacion_comparacion_y_reversion(self):
        casos = json.loads(ev.SUITE.read_text(encoding='utf-8'))['acciones']
        def perfecto(messages, perfil, modelo, max_tokens):
            caso = next(c for c in casos if c['pregunta'] == messages[-1]['content'])
            d = dict(caso['esperado'], mensaje='Texto', requiere_razonamiento_profundo=False)
            return {'texto': json.dumps(d), 'modelo': modelo}
        def fallo(*args, **kwargs):
            raise gw.GatewayError('fallo')
        ev.evaluar('anterior', 'acciones', self.ruta / 'base.json', fallo)
        ev.evaluar('probado', 'acciones', self.ruta / 'candidato.json', perfecto)
        r = ev.comparar(self.ruta / 'base.json', self.ruta / 'candidato.json')
        self.assertTrue(r['elegible'])
        self.assertFalse(ev.comparar(self.ruta / 'candidato.json', self.ruta / 'candidato.json')['elegible'])
        ap.activar('acciones', 'probado')
        self.assertEqual(ap.modelo_activo('acciones'), 'probado')
        ap.revertir()
        self.assertIsNone(ap.modelo_activo('acciones'))


if __name__ == '__main__':
    unittest.main()
