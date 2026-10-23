"""Presupuesto local conservador, sin instalar drivers ni modificar Windows."""
import ctypes
import os


def memoria_gb():
    if os.name != "nt":
        return None
    class Estado(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
            (n, ctypes.c_ulonglong) for n in ("total", "available", "page", "page_free", "virtual", "virtual_free", "extended")]
    estado = Estado()
    estado.length = ctypes.sizeof(Estado)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(estado)):
        return None
    return round(estado.total / 1024 ** 3, 1)


def aplicar():
    ram = memoria_gb()
    ligero = ram is not None and ram <= 9
    valores = {
        "LARA_WHISPER_MODEL": "base" if ligero else "small",
        "LARA_OLLAMA_NUM_CTX": "2048" if ligero else "4096",
        "LARA_OLLAMA_KEEP_ALIVE": "1m" if ligero else "3m",
        "LARA_OLLAMA_THREADS": str(max(1, min(4, (os.cpu_count() or 2) - 1))),
    }
    for clave, valor in valores.items():
        os.environ.setdefault(clave, valor)
    return {"ram_gb": ram, "cpu_hilos": os.cpu_count(), "perfil": "ligero" if ligero else "equilibrado"}
