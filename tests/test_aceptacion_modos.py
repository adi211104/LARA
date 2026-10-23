"""Criterio de aceptación de la Fase 1 (AUDITORIA.md):

    "En modo lectura y plan, una prueba automatizada demuestra que no se llama
     a ninguna función que modifique archivos, procesos, teclado, ratón o red
     con efecto."

Todas las funciones con efecto se sustituyen por dobles; ninguna prueba
ejecuta acciones reales ni necesita claves, red o modelos.
"""

import json
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import _entorno  # noqa: F401  (aísla los datos antes de importar config)
from core import modos
from core.cerebro import LaraCerebro

# Funciones con efecto que cerebro.py importa por nombre.
EFECTOS_CEREBRO = [
    "ejecutar_cmd", "ejecutar_cmd_admin", "ejecutar_codigo_python", "ejecutar_codigo_visible",
    "copiar_archivo", "mover_archivo", "ajustar_brillo", "abrir_programa",
    "abrir_url_en_navegador", "crear_archivo", "abrir_programa_inteligente",
    "cerrar_programa_inteligente", "editar_archivo", "insertar_en_archivo", "restaurar_bak",
    "escribir_texto", "abrir_y_escribir", "_clic", "presionar",
    "descargar_archivo", "descargar_video", "guardar_conocimiento", "recordatorio_rapido",
    "proponer_skill", "aceptar_propuesta", "ejecutar_skill_registrada", "aprender_de_archivo",
    "consultar_gestor_compound", "generar_codigo_grande",
    "tarea_crear", "tarea_completar", "tarea_eliminar", "cliente_crear", "compra_crear",
    "compra_actualizar_estado", "proyecto_crear",
]
# Funciones con efecto importadas dentro de los métodos (import perezoso).
EFECTOS_MODULO = [
    "core.skills_sistema.ejecutar",
    "core.skills_programas.abrir_programa_inteligente",
    "core.skills_programas.cerrar_programa_inteligente",
    "core.skills_os.ajustar_brillo",
    "core.rutinas.ejecutar_rutina",
]

OPS_SISTEMA_RIESGO = sorted(modos.SISTEMA_OPS_RIESGO)
ACCIONES_CON_EFECTO = sorted((modos.ACCIONES_RIESGO | modos.ACCIONES_EFECTO) - {"rutina", "deshacer"})


def _cerebro(on_confirm=lambda *_: True):
    c = LaraCerebro(on_confirm=on_confirm)
    c._persistir = lambda *_: None
    c._status = lambda *_: None
    c._say = lambda texto, hablar=True: c._buffer.append(texto)
    return c


class _ConDobles(unittest.TestCase):
    """Sustituye TODAS las funciones con efecto y verifica que no se llamen."""

    def setUp(self):
        self.modo_anterior = modos._modo_actual
        self._pila = ExitStack()
        self.dobles = {}
        for nombre in EFECTOS_CEREBRO:
            self.dobles[nombre] = self._pila.enter_context(
                patch(f"core.cerebro.{nombre}", MagicMock(return_value={})))
        for ruta in EFECTOS_MODULO:
            self.dobles[ruta] = self._pila.enter_context(
                patch(ruta, MagicMock(return_value={})))

    def tearDown(self):
        self._pila.close()
        modos._modo_actual = self.modo_anterior

    def llamados(self):
        return sorted(n for n, d in self.dobles.items() if d.called)


class AceptacionPlanYLectura(_ConDobles):
    def test_decisiones_del_modelo_no_ejecutan_nada(self):
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            for accion in ACCIONES_CON_EFECTO:
                with self.subTest(modo=modo, accion=accion):
                    c = _cerebro()
                    c._procesar_respuesta_llm(json.dumps(
                        {"accion": accion, "params": ["x", "y"], "mensaje": ""}))
                    self.assertEqual(self.llamados(), [], accion)
            for op in OPS_SISTEMA_RIESGO:
                with self.subTest(modo=modo, sistema=op):
                    c = _cerebro()
                    c._procesar_respuesta_llm(json.dumps(
                        {"accion": "sistema", "params": [op, "50"], "mensaje": ""}))
                    self.assertEqual(self.llamados(), [], op)

    def test_modo_bloquea_aunque_se_activen_herramientas_experimentales(self):
        # Con el interruptor de desarrollo encendido, cmd/código/teclado ya no
        # los frena la pausa de Fase 1: debe frenarlos el propio modo.
        with patch.dict("os.environ", {"LARA_ENABLE_EXPERIMENTAL_TOOLS": "1"}):
            self.test_decisiones_del_modelo_no_ejecutan_nada()
            self.test_atajos_y_reflejos_no_ejecutan_nada()

    def test_atajos_y_reflejos_no_ejecutan_nada(self):
        frases = ["anota comprar pan", "dicta en el bloc de notas",
                  "bloquea la pantalla", "silencia el volumen", "sube el volumen"]
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            for frase in frases:
                with self.subTest(modo=modo, frase=frase):
                    c = _cerebro()
                    c._pre_despacho_rapido(frase)
                    self.assertEqual(self.llamados(), [], frase)
                    self.assertFalse(c.modo_dictado)

    def test_reintento_como_administrador_respeta_modo(self):
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            c = _cerebro()
            c._ultimo_cierre_fallido = "notepad"
            c._procesar("usa permisos de administrador")
            self.assertEqual(self.llamados(), [], modo)

    def test_deshacer_respeta_modo(self):
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            c = _cerebro()
            inversa = MagicMock()
            c._registrar_undo("mover a.txt", inversa)
            c._procesar("deshaz")
            inversa.assert_not_called()

    def test_rutinas_respetan_modo(self):
        for modo in ("plan", "lectura"):
            modos._modo_actual = modo
            c = _cerebro()
            with patch("core.rutinas.detectar_rutina", return_value="modo taller"):
                c._procesar("modo taller")
            self.assertEqual(self.llamados(), [], modo)


class RutasEnModoManual(_ConDobles):
    def setUp(self):
        super().setUp()
        modos._modo_actual = "manual"

    def test_reintento_admin_sin_confirmacion_no_cierra(self):
        c = _cerebro(on_confirm=None)
        c._ultimo_cierre_fallido = "notepad"
        c._procesar("usa permisos de administrador")
        self.dobles["core.skills_programas.cerrar_programa_inteligente"].assert_not_called()

    def test_reintento_admin_confirmado_cierra(self):
        c = _cerebro(on_confirm=lambda *_: True)
        c._ultimo_cierre_fallido = "notepad"
        c._procesar("usa permisos de administrador")
        self.dobles["core.skills_programas.cerrar_programa_inteligente"].assert_called_once_with(
            "notepad", admin=True)

    def test_abrir_programa_no_pide_confirmacion(self):
        preguntas = []
        c = _cerebro(on_confirm=lambda *a: preguntas.append(a) or False)
        c._procesar_respuesta_llm(json.dumps({"accion": "abrir", "params": ["calculadora"]}))
        self.dobles["abrir_programa_inteligente"].assert_called_once()
        self.assertEqual(preguntas, [])


class PasosDeRutina(unittest.TestCase):
    def test_paso_con_efecto_sin_autorizador_se_deniega(self):
        from core import rutinas
        with patch("core.skills_programas.abrir_programa_inteligente") as abrir:
            rutinas._ejecutar_paso({"tipo": "abrir", "programa": "code"})
            rutinas._ejecutar_paso({"tipo": "abrir", "programa": "code"}, lambda *_: False)
        abrir.assert_not_called()

    def test_paso_autorizado_se_ejecuta_y_hablar_no_pide_permiso(self):
        from core import rutinas
        pedidos = []

        def autorizar(accion, params):
            pedidos.append(accion)
            return True

        with patch("core.skills_programas.abrir_programa_inteligente") as abrir:
            rutinas._ejecutar_paso({"tipo": "abrir", "programa": "code"}, autorizar)
            self.assertEqual(rutinas._ejecutar_paso({"tipo": "hablar", "texto": "hola"}, autorizar), "hola")
        abrir.assert_called_once_with("code")
        self.assertEqual(pedidos, ["abrir"])


if __name__ == "__main__":
    unittest.main()
