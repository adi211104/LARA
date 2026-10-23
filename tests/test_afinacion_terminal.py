import _entorno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from core import memoria_vectorial as memoria, sesiones, llm_engine
from core.cerebro import LaraCerebro


class AfinacionTerminal(unittest.TestCase):
    def test_guardado_sin_red_con_recuperacion_lexica(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(memoria, 'RUTA_DB', Path(tmp) / 'memoria.db'), \
             patch.object(memoria, 'RUTA_JSON_LEGADO', Path(tmp) / 'ausente.json'), \
             patch.object(memoria, '_db_lista', False), \
             patch.object(memoria, '_embedding', side_effect=AssertionError('No usar red')):
            memoria.indexar('amplificador pendiente de reparar', generar_embedding=False)
            self.assertEqual(len(memoria.recordar('amplificador pendiente')), 1)

    def test_sesion_no_solicita_embedding(self):
        with patch.object(memoria, 'indexar') as indexar:
            sesiones.guardar_turno('chat', 'test-afinacion', 'user', 'mensaje de prueba')
            indexar.assert_called_once_with('mensaje de prueba', 'user', generar_embedding=False)

    def test_respuesta_rapida_conserva_contexto(self):
        cerebro = LaraCerebro(on_speak=lambda _: None)
        respuesta = cerebro.procesar('ayuda')
        self.assertEqual(cerebro.historial[-2]['content'], 'ayuda')
        self.assertEqual(cerebro.historial[-1], {'role': 'assistant', 'content': respuesta})

    def test_limpieza_no_rompe_codigo(self):
        for codigo in ('def potencia(x):\n    return x ** 2 * 3', '```python\nx = a * b * c\n```'):
            self.assertEqual(llm_engine._limpiar_formato(codigo), codigo)

    def test_limpieza_conserva_json_y_operadores(self):
        import json
        texto = json.dumps({'respuesta': 'x ** 2 * 3 y **literal**',
                            'formula': r'\frac{a}{b}'})
        self.assertEqual(llm_engine._limpiar_formato(texto), texto)

    def test_json_gateway_contrato_y_correccion_contexto(self):
        with patch('core.gateway_llm.activo', return_value=True), \
             patch('core.gateway_llm.completar', return_value={
                 'modelo': llm_engine.MODELO_PROFUNDO, 'texto': '{"destino":"martes"}'}) as completar:
            respuesta = llm_engine.razonar_profundo(
                'Ahora martes. JSON con destino.', '',
                [{'role': 'user', 'content': 'Destino anterior viernes'}])
        self.assertEqual(respuesta, '{"destino":"martes"}')
        self.assertTrue(completar.call_args.kwargs['json_mode'])
        mensajes = completar.call_args.args[0]
        self.assertIn('sin traducirlos', mensajes[0]['content'])
        self.assertIn('sustituye el dato anterior', mensajes[0]['content'])
        self.assertEqual(mensajes[1]['content'], 'Destino anterior viernes')
        self.assertIn('Ahora martes', mensajes[-1]['content'])
        self.assertFalse(completar.call_args.kwargs['modelo'].startswith('auto'))

    def test_rechaza_sustitucion_motor_profundo(self):
        with patch('core.gateway_llm.activo', return_value=True), \
             patch('core.gateway_llm.completar', return_value={
                 'modelo': 'moderador', 'texto': 'respuesta indebida'}), \
             patch.object(llm_engine, 'client', None), \
             patch.object(llm_engine, '_llamar_gemini', return_value='respaldo correcto'):
            self.assertEqual(llm_engine.razonar_profundo('pregunta', '', []), 'respaldo correcto')

    def test_json_respaldo_groq(self):
        cliente = MagicMock()
        cliente.chat.completions.create.return_value.choices[0].finish_reason = 'stop'
        cliente.chat.completions.create.return_value.choices[0].message.content = '{"salida":42}'
        with patch('core.gateway_llm.activo', return_value=False), \
             patch.object(llm_engine, 'client', cliente), \
             patch.object(llm_engine, 'registrar_groq'), \
             patch.object(llm_engine, '_cfg_esfuerzo', return_value={
                 'tokens_profundo': 1200, 'doble_pasada': False}):
            self.assertEqual(llm_engine.razonar_profundo('JSON con salida', '', []), '{"salida":42}')
        self.assertEqual(cliente.chat.completions.create.call_args.kwargs['response_format'],
                         {'type': 'json_object'})

    def test_json_respaldo_gemini(self):
        with patch('core.gateway_llm.activo', return_value=False), \
             patch.object(llm_engine, 'client', None), \
             patch.object(llm_engine, '_llamar_gemini', return_value='{"resultado":1}') as gemini:
            self.assertEqual(llm_engine.razonar_profundo('JSON con resultado', '', []), '{"resultado":1}')
        self.assertTrue(gemini.call_args.kwargs['json_mode'])

    def test_sin_herramientas_no_pasa_por_router(self):
        cerebro = LaraCerebro(on_speak=lambda _: None)
        with patch('core.cerebro.razonar_profundo', return_value='respuesta verificada'), \
             patch('core.cerebro.consultar_lara', side_effect=AssertionError('router prohibido')), \
             patch.object(cerebro, '_pre_despacho_rapido', side_effect=AssertionError('acción prohibida')):
            self.assertEqual(cerebro.procesar('abre calculadora sin herramientas'), 'respuesta verificada')


if __name__ == '__main__':
    unittest.main()
