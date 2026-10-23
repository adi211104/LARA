"""Aísla las pruebas de los datos reales del usuario.

Debe importarse ANTES que config/core en cada módulo de prueba: config.py
calcula BASE_DIR al importarse, así que LARA_DATA_DIR tiene que existir antes.
Las pruebas nunca escriben en OneDrive ni en Taller_IA real.
"""

import atexit
import logging
import os
import shutil
import tempfile

DATA_DIR = tempfile.mkdtemp(prefix="lara_tests_")


@atexit.register
def _limpiar():
    logging.shutdown()  # suelta los archivos de log antes de borrar en Windows
    shutil.rmtree(DATA_DIR, ignore_errors=True)



os.environ["LARA_DATA_DIR"] = DATA_DIR
os.environ["LARA_ENABLE_EXPERIMENTAL_TOOLS"] = "0"
os.environ["LARA_GATEWAY_ENABLED"] = "0"
os.environ["LARA_LEARNING_CAPTURE"] = "0"
