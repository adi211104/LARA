import _entorno
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.open_code import AgenteCodigo


class Recuperacion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = AgenteCodigo(self.tmp.name, session_id='test')
        p = patch.object(self.a, '_decidir_google', side_effect=RuntimeError('Google simulado no disponible'))
        p.start()
        self.addCleanup(p.stop)

    def test_input_real_y_codigo_de_salida(self):
        with patch('core.open_code.subprocess.run', wraps=subprocess.run) as run:
            r = self.a._cmd(f'"{sys.executable}" -c "print(int(input())*2)"', '7\n')
        self.assertIn('14', r)
        self.assertIn('salida: 0', r)
        self.assertEqual(run.call_args.kwargs['input'], '7\n')
        r = self.a._cmd(f'"{sys.executable}" -c "input()"')
        self.assertIn('Falta stdin', r)
        self.assertIn('salida: 1', r)

    def test_429_no_es_error_json_y_respeta_retry_after(self):
        error = RuntimeError('rate_limit try again in 9.3075s')
        error.status_code = 429
        error.response = SimpleNamespace(headers={'retry-after': '3'})
        decision = {'accion': 'terminar', 'resumen': 'listo'}
        respuesta = SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(decision)))])
        cliente = Mock()
        cliente.chat.completions.create.side_effect = [error, respuesta]
        mensajes = [{'role': 'system', 'content': 'sistema'}, {'role': 'user', 'content': 'tarea'}]
        with patch('core.gateway_llm.activo', return_value=False), patch('core.open_code.client', cliente), \
             patch.object(self.a._cancelar, 'wait', return_value=False) as esperar:
            self.assertEqual(self.a._decidir(mensajes), decision)
        esperar.assert_called_once_with(3)
        self.assertEqual(len(mensajes), 2)

    def test_cancelacion_durante_cuota(self):
        e = RuntimeError('rate_limit')
        cliente = Mock()
        cliente.chat.completions.create.side_effect = e
        with patch('core.gateway_llm.activo', return_value=False), patch('core.open_code.client', cliente), \
             patch.object(self.a._cancelar, 'wait', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'Detenido'):
                self.a._decidir([{'role': 'system', 'content': 's'}])
        self.assertEqual(cliente.chat.completions.create.call_count, 1)

    def test_gateway_codigo_valida_formato_y_modelo(self):
        d = {'accion': 'cmd', 'comando': 'python app.py', 'entrada': '5\n3\n\n'}
        with patch('core.gateway_llm.activo', return_value=True), \
             patch('core.gateway_llm.modelos_acciones', return_value=['modelo']), \
             patch('core.gateway_llm.completar', return_value={'modelo': 'modelo', 'texto': json.dumps(d)}):
            self.assertEqual(self.a._decidir([]), d)
        with self.assertRaises(ValueError):
            self.a._validar_decision({'accion': 'cmd', 'comando': 123})

    def test_reanuda_sin_repetir_escritura(self):
        with patch.object(self.a, '_planificar', return_value='plan'), \
             patch.object(self.a, '_decidir', side_effect=[{'accion': 'leer', 'archivo': 'a.py'}, RuntimeError('cuota')]), \
             patch.object(self.a, '_ejecutar_accion', return_value='resultado') as ejecutar:
            self.a.ejecutar_tarea('lee a.py')
        self.assertIsNotNone(self.a._pendiente)
        with patch.object(self.a, '_planificar', side_effect=AssertionError('no replanificar')), \
             patch.object(self.a, '_decidir', return_value={'accion': 'terminar', 'resumen': 'listo'}):
            self.a.ejecutar_tarea('continuar')
        self.assertEqual(ejecutar.call_count, 1)
        self.assertIsNone(self.a._pendiente)

    def test_continuar_sin_contexto_no_inventa_tarea(self):
        with patch.object(self.a, '_planificar') as plan, patch.object(self.a, '_decidir') as decidir:
            self.a.ejecutar_tarea('continúa')
        plan.assert_not_called()
        decidir.assert_not_called()

    def test_reabrir_recupera_carpeta_y_punto_sin_clonar(self):
        from core import sesiones
        sid = self.a.session_id
        self.a._pendiente = [{'role': 'system', 'content': 'sistema'},
                             {'role': 'user', 'content': 'Objetivo original: rectángulo en terminal'}]
        self.a._guardar_estado()
        with patch('core.open_code.sesiones.crear_sesion', side_effect=AssertionError('No crear clon')):
            recuperado = AgenteCodigo(self.tmp.name, session_id=sid)
        self.assertEqual(recuperado._pendiente, self.a._pendiente)
        self.assertEqual(sesiones.cargar_sesion('code', sid)['carpeta'], str(Path(self.tmp.name).resolve()))

    def test_arbol_ignora_dependencias_antes_de_recorrer(self):
        base = Path(self.tmp.name)
        (base / 'node_modules').mkdir()
        (base / 'node_modules' / 'oculto.py').write_text('x', encoding='utf-8')
        (base / 'visible.py').write_text('x', encoding='utf-8')
        salida = self.a._listar('')
        self.assertIn('visible.py', salida)
        self.assertNotIn('oculto.py', salida)

    def test_abre_artefacto_sin_consultar_modelo_ni_lanzar_lara(self):
        from core import modos
        self.a._escribir('draw_rectangle.py', "input('Ancho: ')")
        self.a._guardar_estado()
        recuperado = AgenteCodigo(self.tmp.name, session_id=self.a.session_id)
        with patch.object(recuperado, '_planificar', side_effect=AssertionError('No planificar otra tarea')), \
             patch.object(recuperado, '_decidir', side_effect=AssertionError('No adivinar programa')), \
             patch('core.open_code.subprocess.Popen') as abrir, patch.object(modos, 'get_modo', return_value='auto'):
            recuperado.ejecutar_tarea('abre el programa')
        self.assertEqual(abrir.call_count, 1)
        self.assertEqual(abrir.call_args.args[0][1], str(Path(self.tmp.name) / 'draw_rectangle.py'))

    def test_contexto_de_seguimiento_conserva_objetivo_y_artefacto(self):
        from core import sesiones
        sesiones.guardar_turno('code', self.a.session_id, 'user', 'Crea un rectángulo en la terminal')
        self.a._escribir('draw_rectangle.py', 'print(1)')
        self.a._pendiente = None
        self.a._guardar_estado()
        capturados = []
        with patch.object(self.a, '_planificar', side_effect=lambda tarea, arbol: capturados.append(tarea) or 'plan'), \
             patch.object(self.a, '_decidir', return_value={'accion': 'terminar', 'resumen': 'listo'}):
            self.a.ejecutar_tarea('cambia el borde por almohadillas')
        self.assertIn('rectángulo en la terminal', capturados[0])
        self.assertIn('draw_rectangle.py', capturados[0])

    def test_no_relanza_lara_ni_lee_configuracion_privada(self):
        with patch('core.open_code.subprocess.run') as run:
            salida = self.a._cmd(r'"Lara 7.2V\INICIAR_LARA.bat"')
        run.assert_not_called()
        self.assertIn('bloqueado', salida)
        for ruta in ('.env', '.tools/gateway-admin.json'):
            with self.assertRaises(ValueError):
                self.a._resolver(ruta)

    def test_varios_artefactos_pide_nombre_sin_adivinar(self):
        self.a._escribir('uno.py', 'print(1)')
        self.a._escribir('dos.py', 'print(2)')
        with patch('core.open_code.subprocess.Popen') as abrir:
            salida = self.a._abrir_programa_creado()
        abrir.assert_not_called()
        self.assertIn('varios programas', salida)

    def test_cuota_gateway_salta_directo_a_otro_proveedor(self):
        from core.gateway_llm import GatewayError
        d = {'accion': 'terminar', 'resumen': 'verificado'}
        self.a._decidir_google.side_effect = None
        self.a._decidir_google.return_value = d
        with patch('core.gateway_llm.activo', return_value=True), \
             patch('core.gateway_llm.modelos_acciones', return_value=['modelo']), \
             patch('core.gateway_llm.completar', side_effect=GatewayError('cuota', 429)), \
             patch('core.open_code.client') as cliente:
            self.assertEqual(self.a._decidir([{'role':'system','content':'s'}, {'role':'user','content':'t'}]), d)
        cliente.chat.completions.create.assert_not_called()

    def test_fallo_informa_avance_sin_afirmar_objetivo_terminado(self):
        eventos = []
        self.a.on_log = lambda tipo, texto: eventos.append((tipo, texto))
        with patch.object(self.a, '_decidir', side_effect=[
            {'accion':'escribir','archivo':'prueba.py','contenido':'print(1)'},
            {'accion':'cmd','comando':'python prueba.py'}, RuntimeError('sin cuota')]), \
             patch.object(self.a, '_cmd', return_value='[código de salida: 0]\n1'), \
             patch('core.open_code.modos.get_modo', return_value='auto'):
            self.a.ejecutar_tarea('crea prueba.py y comprueba el resultado')
        self.assertFalse(any(t == 'terminado' for t, s in eventos))
        self.assertTrue(any('prueba terminó con código de salida 0' in s for t, s in eventos))
        self.assertIsNotNone(self.a._pendiente)
