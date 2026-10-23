"""Recordatorios persistentes. Recupera vencidos al abrir LARA.

Entrega al menos una vez: si el proceso muere tras notificar y antes de
confirmar en SQLite, puede repetirse. No despierta un PC apagado.
"""
import math
import sqlite3
import threading
import time
from config import KNOWLEDGE_DIR

DB = KNOWLEDGE_DIR / "recordatorios.sqlite3"
_lock = threading.Lock()
_hilo = None


def _conectar():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS avisos (id INTEGER PRIMARY KEY, texto TEXT NOT NULL, vence REAL NOT NULL, estado TEXT NOT NULL DEFAULT 'pendiente')")
    conn.commit()
    return conn


def crear(texto, minutos):
    minutos = float(minutos)
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("Indica el texto del recordatorio.")
    if not math.isfinite(minutos) or not 0 < minutos <= 525600:
        raise ValueError("El plazo debe estar entre 0 y 525600 minutos, sin incluir cero.")
    conn = _conectar()
    try:
        with conn:
            cur = conn.execute("INSERT INTO avisos(texto, vence) VALUES (?, ?)",
                               (texto.strip(), time.time() + minutos * 60))
        return {"estado": "ok", "id": cur.lastrowid, "texto": texto.strip(), "minutos": minutos}
    finally:
        conn.close()


def listar():
    conn = _conectar()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM avisos WHERE estado='pendiente' ORDER BY vence")]
    finally:
        conn.close()


def cancelar(numero):
    conn = _conectar()
    try:
        with conn:
            cur = conn.execute("UPDATE avisos SET estado='cancelado' WHERE id=? AND estado='pendiente'", (numero,))
        return cur.rowcount == 1
    finally:
        conn.close()


def entregar_vencidos(notificar, ahora=None):
    # Transacción exclusiva también entre procesos: dos ventanas no duplican
    # la entrega. El callback debe ser breve y no modificar esta tabla.
    conn = _conectar()
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            filas = conn.execute("SELECT * FROM avisos WHERE estado='pendiente' AND vence<=? ORDER BY vence LIMIT 20",
                                 (time.time() if ahora is None else ahora,)).fetchall()
            for fila in filas:
                notificar(fila["texto"])
                conn.execute("UPDATE avisos SET estado='entregado' WHERE id=?", (fila["id"],))
        return len(filas)
    finally:
        conn.close()


def iniciar(notificar):
    global _hilo
    with _lock:
        if _hilo is not None and _hilo.is_alive():
            return
        def bucle():
            from core.logger import log_error
            while True:
                try:
                    entregar_vencidos(notificar)
                except Exception as exc:
                    log_error("recordatorios", str(exc))
                time.sleep(2)
        _hilo = threading.Thread(target=bucle, daemon=True, name="lara-recordatorios")
        _hilo.start()
