"""
logger.py
Logging centralizado de LARA (A-07).

  - logs/errores.log   : errores legibles, con ID de turno, rotado (1 MB x 5).
  - logs/eventos.jsonl : un evento JSON por línea (turno, acción, proveedor,
                         duración, resultado), rotado (2 MB x 5). Sirve para
                         diagnosticar "no funciona como quisiera" con datos.

El ID de turno se fija con nuevo_turno() al empezar cada petición y se
propaga a todo lo que se registre en ese hilo/contexto.
"""

import contextvars
import json
import logging
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler

from config import BASE_DIR

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "errores.log"
EVENTOS_FILE = LOG_DIR / "eventos.jsonl"

_turno_actual = contextvars.ContextVar("lara_turno", default="-")


def _crear_logger(nombre: str, archivo, max_bytes: int, formato: str) -> logging.Logger:
    log = logging.getLogger(nombre)
    if not log.handlers:  # evita handlers duplicados si el módulo se recarga
        h = RotatingFileHandler(str(archivo), maxBytes=max_bytes, backupCount=5,
                                encoding="utf-8", delay=True)
        h.setFormatter(logging.Formatter(formato))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


_errores = _crear_logger("lara.errores", LOG_FILE, 1_000_000,
                         "%(asctime)s [%(levelname)s] %(message)s")
_eventos = _crear_logger("lara.eventos", EVENTOS_FILE, 2_000_000, "%(message)s")


def nuevo_turno() -> str:
    """Genera y activa un ID corto para el turno actual; lo devuelve."""
    tid = uuid.uuid4().hex[:8]
    _turno_actual.set(tid)
    return tid


def turno_actual() -> str:
    return _turno_actual.get()


def log_error(origen: str, mensaje: str) -> None:
    tid = _turno_actual.get()
    _errores.error(f"[turno {tid}] [{origen}] {mensaje}")
    print(f"[LOG ERROR] {origen}: {mensaje}")
    log_evento("error", origen=origen, mensaje=str(mensaje)[:500])


def log_evento(evento: str, **campos) -> None:
    """Registra un evento estructurado. Nunca lanza: el logging no debe
    tumbar el flujo del asistente."""
    try:
        registro = {"ts": datetime.now().isoformat(timespec="milliseconds"),
                    "turno": _turno_actual.get(), "evento": evento}
        registro.update(campos)
        _eventos.info(json.dumps(registro, ensure_ascii=False, default=str))
    except Exception:
        pass
