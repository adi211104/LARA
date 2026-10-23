import _entorno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core import intencion_codigo as ic


class IntencionLocal(unittest.TestCase):
    def test_entrenamiento_y_abstencion(self):
        datos = [(v+' '+o, 1) for v in ('abre','ejecuta','inicia','lanza')
                 for o in ('el programa', 'el programa que hiciste', 'el script que creaste')]
        datos += [(v+' '+o, 0) for v in ('abre','ejecuta','inicia','lanza')
                  for o in ('Lara','calculadora','otro programa')]
        modelo = ic.entrenar(datos)
        with tempfile.TemporaryDirectory() as tmp, patch.object(ic, 'MODELO', Path(tmp)/'modelo.json'):
            ic.MODELO.write_text(json.dumps(modelo), encoding='utf-8')
            self.assertTrue(ic.pide_abrir_artefacto('abre el programa que hiciste'))
            for t in ('no abras el programa', 'abre calculadora', 'explica el script',
                      'abre C:/main.py', 'ejecuta otro programa', 'abre el programa Lara'):
                self.assertFalse(ic.pide_abrir_artefacto(t), t)
        with patch.object(ic, 'MODELO', Path(tmp)/'no-existe.json'):
            self.assertFalse(ic.pide_abrir_artefacto('abre el programa'))

    def test_plan_local_no_consume_cuota(self):
        from core.open_code import AgenteCodigo
        with tempfile.TemporaryDirectory() as tmp, patch.dict('os.environ', {'LARA_CODE_PLAN_LLM': '0'}), \
             patch('core.gateway_llm.completar', side_effect=AssertionError('No consultar nube')):
            a = AgenteCodigo(tmp)
            self.assertIn('Probar entradas', a._planificar('crea programa', ''))
