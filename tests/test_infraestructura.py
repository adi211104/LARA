"""Regresiones de A-04 (instalación), A-06 (datos) y A-07 (logging)."""

import json
import re
import tomllib
import unittest
from pathlib import Path

import _entorno  # noqa: F401  (aísla los datos antes de importar config)

RAIZ = Path(__file__).resolve().parent.parent


def _nombres_requirements(archivo: Path) -> set[str]:
    nombres = set()
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.split("#", 1)[0].strip()
        if linea and not linea.startswith("-"):
            nombres.add(_canonico(re.split(r"[<>=!~\[; ]", linea, maxsplit=1)[0]))
    return nombres


def _canonico(nombre: str) -> str:
    return re.sub(r"[-_.]+", "-", nombre).lower()


class Instalacion(unittest.TestCase):
    def test_pyproject_coincide_con_requirements(self):
        datos = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
        pyproject = {_canonico(re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0])
                     for d in datos["project"]["dependencies"]}
        self.assertEqual(pyproject, _nombres_requirements(RAIZ / "requirements.txt"))

    def test_lock_fija_todas_las_dependencias_directas(self):
        lock = RAIZ / "requirements.lock"
        fijadas = {}
        for linea in lock.read_text(encoding="utf-8").splitlines():
            if linea and not linea.startswith("#"):
                self.assertRegex(linea, r"^[A-Za-z0-9_.\-]+==[^=\s]+$")
                nombre, version = linea.split("==")
                fijadas[_canonico(nombre)] = version
        faltan = _nombres_requirements(RAIZ / "requirements.txt") - set(fijadas)
        self.assertEqual(faltan, set())

    def test_lanzador_no_instala_paquetes(self):
        lanzador = (RAIZ / "INICIAR_LARA.bat").read_text(encoding="utf-8").lower()
        self.assertNotIn("pip install", lanzador)

    def test_env_example_no_contiene_secretos(self):
        for linea in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines():
            if "KEY" in linea and "=" in linea and not linea.startswith("#"):
                self.assertEqual(linea.split("=", 1)[1].strip(), "", linea)


class Datos(unittest.TestCase):
    def test_lara_data_dir_redirige_todos_los_datos(self):
        import config
        self.assertEqual(Path(config.BASE_DIR), Path(_entorno.DATA_DIR))
        for carpeta in (config.WORKSPACE, config.KNOWLEDGE_DIR, config.DOCUMENTOS_RAG):
            self.assertTrue(Path(carpeta).is_relative_to(_entorno.DATA_DIR))


class Logging(unittest.TestCase):
    def test_evento_estructurado_lleva_id_de_turno(self):
        from core import logger
        tid = logger.nuevo_turno()
        logger.log_evento("prueba", accion="abrir", ms=12)
        for h in logger._eventos.handlers:
            h.flush()
        ultima = logger.EVENTOS_FILE.read_text(encoding="utf-8").strip().splitlines()[-1]
        registro = json.loads(ultima)
        self.assertEqual(registro["turno"], tid)
        self.assertEqual(registro["evento"], "prueba")
        self.assertEqual(registro["accion"], "abrir")


if __name__ == "__main__":
    unittest.main()
