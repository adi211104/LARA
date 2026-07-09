"""
logger.py
Logging centralizado de errores de LARA. Todo error queda en
Taller_IA/logs/errores.log con fecha/hora, además de imprimirse en consola.
"""

import logging
from config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "errores.log"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)


def log_error(origen: str, mensaje: str) -> None:
    logging.error(f"[{origen}] {mensaje}")
    print(f"[LOG ERROR] {origen}: {mensaje}")
