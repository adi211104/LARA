"""Regresiones de seguridad introducidas durante la Fase 1.

Estas pruebas no ejecutan comandos, no escriben notas y no requieren claves ni
modelos. Protegen especialmente los atajos que antes eludían el control de modo.
"""

import os
import unittest
from unittest.mock import patch

import _entorno  # noqa: F401  (aísla los datos antes de importar config)
from core import modos
from core.cerebro import LaraCerebro
from core import skills_registry


class PoliticaFase1Tests(unittest.TestCase):
    def setUp(self):
        self.modo_anterior = modos._modo_actual
        modos._modo_actual = "manual"

    def tearDown(self):
        modos._modo_actual = self.modo_anterior

    @patch.dict(os.environ, {"LARA_ENABLE_EXPERIMENTAL_TOOLS": "0"})
    def test_herramientas_experimentales_se_bloquean(self):
        for accion in ("cmd", "cmd_admin", "ejecutar_codigo", "ejecutar_skill", "clic", "teclas"):
            decision, motivo = modos.evaluar(accion, ["dato"])
            self.assertEqual(decision, modos.BLOQUEAR, accion)
            self.assertIn("Fase 1", motivo)

    def test_auto_exige_confirmacion_para_riesgo(self):
        modos._modo_actual = "auto"
        decision, _ = modos.evaluar("mover_archivo", ["a", "b"])
        self.assertEqual(decision, modos.CONFIRMAR)

    def test_sin_interfaz_no_autoriza_accion_riesgosa(self):
        cerebro = LaraCerebro(on_confirm=None)
        permitida, motivo = cerebro._autorizar_accion("mover_archivo", ["a", "b"])
        self.assertFalse(permitida)
        self.assertEqual(motivo, "De acuerdo, no lo hago.")

    def test_atajo_anota_respeta_modo_lectura(self):
        modos._modo_actual = "lectura"
        cerebro = LaraCerebro(on_confirm=lambda *_: True)
        cerebro._persistir = lambda *_: None
        cerebro._say = lambda texto: cerebro._buffer.append(texto)
        with patch("core.skills_sistema.ejecutar") as ejecutar:
            respuesta = cerebro._pre_despacho_rapido("anota secreto")
        self.assertIn("solo lectura", respuesta.lower())
        ejecutar.assert_not_called()

    def test_atajo_anota_respeta_modo_plan(self):
        modos._modo_actual = "plan"
        cerebro = LaraCerebro(on_confirm=lambda *_: True)
        cerebro._persistir = lambda *_: None
        cerebro._say = lambda texto: cerebro._buffer.append(texto)
        with patch("core.skills_sistema.ejecutar") as ejecutar:
            respuesta = cerebro._pre_despacho_rapido("anota secreto")
        self.assertIn("modo plan", respuesta.lower())
        ejecutar.assert_not_called()

    @patch.dict(os.environ, {"LARA_ENABLE_EXPERIMENTAL_TOOLS": "0"})
    def test_registro_dinamico_no_escanea_ni_ejecuta(self):
        self.assertEqual(skills_registry.auto_registrar_skills_core(), [])
        resultado = skills_registry.ejecutar_skill_registrada("cualquier_cosa")
        self.assertIn("pausadas", resultado["error"])


if __name__ == "__main__":
    unittest.main()
