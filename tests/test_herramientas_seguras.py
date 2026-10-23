"""Regresiones de A-02: sin shell para texto que viene del modelo, candado a
nivel de función para comandos/código y confirmación en el agente Open Code.

Nada se ejecuta de verdad: subprocess y os.startfile se sustituyen por dobles.
"""

import os
import unittest
from unittest.mock import patch

import _entorno  # noqa: F401  (aísla los datos antes de importar config)
from core import modos, skills_os, skills_programas


class UrlsYProgramas(unittest.TestCase):
    def test_url_con_inyeccion_se_rechaza_sin_lanzar_nada(self):
        maliciosas = ['https://x.com" & calc & "', "https://x.com\n calc",
                      "https://x.com|calc", "javascript:alert(1)", "https://x.com^&calc"]
        with patch("os.startfile", create=True) as startfile, \
                patch("core.skills_os.subprocess.Popen") as popen:
            for url in maliciosas:
                with self.subTest(url=url):
                    self.assertIn("error", skills_os.abrir_url_en_navegador(url))
        startfile.assert_not_called()
        popen.assert_not_called()

    def test_url_valida_se_abre_sin_shell(self):
        with patch("os.startfile", create=True) as startfile, \
                patch("core.skills_os.subprocess.Popen") as popen:
            r = skills_os.abrir_url_en_navegador("example.com/buscar?q=a%20b&x=1", "edge")
        self.assertEqual(r["url"], "https://example.com/buscar?q=a%20b&x=1")
        startfile.assert_called_once_with("microsoft-edge:https://example.com/buscar?q=a%20b&x=1")
        popen.assert_not_called()

    def test_chrome_recibe_la_url_como_argumento_de_lista(self):
        with patch("core.skills_os._ruta_chrome", return_value=r"C:\chrome.exe"), \
                patch("core.skills_os.subprocess.Popen") as popen:
            skills_os.abrir_url_en_navegador("https://example.com", "chrome")
        args, kwargs = popen.call_args
        self.assertEqual(args[0], [r"C:\chrome.exe", "https://example.com"])
        self.assertNotIn("shell", kwargs)

    def test_abrir_programa_con_metacaracteres_se_rechaza(self):
        with patch("os.startfile", create=True) as startfile:
            self.assertIn("error", skills_os.abrir_programa('notepad" & calc & "'))
        startfile.assert_not_called()

    def test_respaldo_literal_de_programas_no_usa_shell(self):
        with patch("core.skills_programas.buscar_programa", return_value=[]), \
                patch("os.startfile", create=True) as startfile, \
                patch("core.skills_programas.subprocess.Popen") as popen:
            r = skills_programas.abrir_programa_inteligente("zzz & calc")
        self.assertIn("error", r)
        startfile.assert_not_called()
        popen.assert_not_called()


class CandadoDeFuncion(unittest.TestCase):
    @patch.dict(os.environ, {"LARA_ENABLE_EXPERIMENTAL_TOOLS": "0"})
    def test_comandos_y_codigo_se_niegan_sin_interruptor(self):
        with patch("core.skills_os.subprocess.run") as run, \
                patch("core.skills_os.subprocess.Popen") as popen:
            for funcion, arg in ((skills_os.ejecutar_cmd, "dir"),
                                 (skills_os.ejecutar_cmd_admin, "dir"),
                                 (skills_os.ejecutar_codigo_python, "print(1)"),
                                 (skills_os.ejecutar_codigo_visible, "print(1)")):
                with self.subTest(funcion=funcion.__name__):
                    self.assertIn("pausada", funcion(arg)["error"])
        run.assert_not_called()
        popen.assert_not_called()


class AgenteOpenCode(unittest.TestCase):
    def setUp(self):
        from core.open_code import AgenteCodigo
        self.modo_anterior = modos._modo_actual
        self.carpeta = _entorno.DATA_DIR
        with patch("core.open_code.sesiones.crear_sesion", return_value="test"):
            self.crear = lambda **kw: AgenteCodigo(self.carpeta, **kw)
            self.agente = self.crear(modo="auto")

    def tearDown(self):
        modos._modo_actual = self.modo_anterior

    def test_modo_permisos_sin_ui_los_comandos_se_deniegan(self):
        modos._modo_actual = "manual"
        self.agente.modo = 'permisos'
        with patch.object(self.agente, "_cmd") as cmd:
            r = self.agente._ejecutar_accion("cmd", {"comando": "dir"})
        cmd.assert_not_called()
        self.assertIn("NO autorizó", r)

    @patch.dict(os.environ, {"LARA_ENABLE_EXPERIMENTAL_TOOLS": "0"})
    def test_pestana_auto_ejecuta_sin_confirmar(self):
        modos._modo_actual = "manual"
        preguntas = []
        self.agente.on_confirmar = lambda accion, detalle: preguntas.append(accion) or True
        with patch.object(self.agente, "_cmd", return_value="ok") as cmd:
            self.agente._ejecutar_accion("cmd", {"comando": "dir"})
        self.assertEqual(preguntas, [])
        cmd.assert_called_once_with("dir")

    def test_modo_global_lectura_bloquea_escribir_y_cmd(self):
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            self.agente.on_confirmar = lambda *_: True
            with patch.object(self.agente, "_cmd") as cmd, \
                    patch.object(self.agente, "_escribir") as escribir:
                self.agente._ejecutar_accion("cmd", {"comando": "dir"})
                self.agente._ejecutar_accion("escribir", {"archivo": "a.txt", "contenido": "x"})
            cmd.assert_not_called()
            escribir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
