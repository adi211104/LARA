import _entorno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core import recordatorios as rec, asistente_util as util


class Recordatorios(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(rec, "DB", Path(self.tmp.name) / "avisos.db")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_persistencia_entrega_unica_y_cancelacion(self):
        with patch.object(rec.time, "time", return_value=100):
            primero = rec.crear("revisar horno", 1)
            segundo = rec.crear("cancelable", 2)
        self.assertEqual(len(rec.listar()), 2)
        self.assertTrue(rec.cancelar(segundo["id"]))
        avisos = []
        self.assertEqual(rec.entregar_vencidos(avisos.append, 159), 0)
        self.assertEqual(rec.entregar_vencidos(avisos.append, 300), 1)
        self.assertEqual(rec.entregar_vencidos(avisos.append, 300), 0)
        self.assertEqual(avisos, ["revisar horno"])
        self.assertEqual(rec.listar(), [])

    def test_fallo_entrega_conserva_pendiente(self):
        rec.crear("aviso", 1)
        def falla(_):
            raise RuntimeError("notificador caído")
        with self.assertRaises(RuntimeError):
            rec.entregar_vencidos(falla, float('inf'))
        self.assertEqual(len(rec.listar()), 1)

    def test_plazos_invalidos(self):
        for n in (0, -1, float('nan'), float('inf'), 525601):
            with self.assertRaises(ValueError):
                rec.crear("aviso", n)

    def test_comandos_no_ejecutan_si_politica_deniega(self):
        with patch('core.skills_tasks.tarea_crear') as crear:
            for texto in ('nueva tarea prueba', 'pomodoro', 'cancelar recordatorio 1'):
                self.assertEqual(util.resolver(texto, lambda *a: (False, 'bloqueado')), 'bloqueado')
            crear.assert_not_called()
        self.assertEqual(rec.listar(), [])

    def test_tareas_sin_llm(self):
        with patch('core.skills_tasks.DB_TAREAS', Path(self.tmp.name) / 'tareas.json'):
            permitir = lambda *a: (True, '')
            self.assertIn('#1', util.resolver('nueva tarea Revisar placa', permitir))
            self.assertIn('Revisar placa', util.resolver('mis tareas', permitir))
            self.assertIn('completada', util.resolver('completar tarea 1', permitir))
            self.assertEqual(util.resolver('mis tareas', permitir), 'No tienes tareas pendientes.')

    def test_importar_cerebro_sin_claves(self):
        env = dict(os.environ, GROQ_API_KEY='', GEMINI_API_KEY='', SERPER_API_KEY='',
                   PYTHON_DOTENV_DISABLED='1', LARA_LLM_LOCAL='0')
        r = subprocess.run([sys.executable, '-c',
                            'from core.cerebro import LaraCerebro; from core import llm_engine; assert llm_engine.client is None; assert llm_engine.genai_client is None'],
                           env=env, capture_output=True, text=True, timeout=40)
        self.assertEqual(r.returncode, 0, r.stderr)
