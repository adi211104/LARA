"""
uso_tokens.py — Contador de uso de tokens de LARA.

Registra los tokens consumidos por cada llamada a los modelos (Groq y Gemini)
agrupados por día y por modelo, para poder vigilar el gasto diario y no
excederse. Se guarda en KNOWLEDGE_DIR/uso_tokens.json (compartido por OneDrive).

Se invoca pasándole directamente el objeto de respuesta de cada SDK:
  - registrar_groq(resp)   -> usa resp.usage (prompt_tokens / completion_tokens)
  - registrar_genai(resp)  -> usa resp.usage_metadata (prompt/candidates token count)
Todo es defensivo: si algo falla al contar, nunca tumba la llamada principal.
"""

import json
import threading
from datetime import date
from pathlib import Path

from config import KNOWLEDGE_DIR

RUTA = KNOWLEDGE_DIR / "uso_tokens.json"
_lock = threading.Lock()

# Presupuesto diario de referencia (tokens) para mostrar % de consumo y no excederse.
PRESUPUESTO_DIARIO = 200_000


def _cargar() -> dict:
    if RUTA.exists():
        try:
            return json.loads(RUTA.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _guardar(data: dict) -> None:
    try:
        RUTA.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def registrar(modelo: str, entrada: int, salida: int) -> None:
    """Suma tokens de entrada/salida al día de hoy, por modelo y total."""
    if not entrada and not salida:
        return
    hoy = date.today().isoformat()
    with _lock:
        data = _cargar()
        dia = data.setdefault(hoy, {"entrada": 0, "salida": 0, "llamadas": 0, "por_modelo": {}})
        dia["entrada"] += int(entrada or 0)
        dia["salida"] += int(salida or 0)
        dia["llamadas"] += 1
        m = dia["por_modelo"].setdefault(modelo, {"entrada": 0, "salida": 0, "llamadas": 0})
        m["entrada"] += int(entrada or 0)
        m["salida"] += int(salida or 0)
        m["llamadas"] += 1
        # No dejar crecer el archivo para siempre: conservar últimos 60 días
        if len(data) > 60:
            for k in sorted(data.keys())[:-60]:
                data.pop(k, None)
        _guardar(data)


def registrar_local(tipo: str) -> None:
    """
    Cuenta un turno resuelto SIN nube: 'reflejo' (regex instantáneo, 0 tokens)
    o 'local' (modelo Ollama en esta PC). Mide cuánto ahorra el cerebro
    híbrido — cada uno de estos habría sido una llamada a Groq/Gemini.
    """
    hoy = date.today().isoformat()
    with _lock:
        data = _cargar()
        dia = data.setdefault(hoy, {"entrada": 0, "salida": 0, "llamadas": 0, "por_modelo": {}})
        ahorro = dia.setdefault("ahorro", {"reflejo": 0, "local": 0})
        ahorro[tipo] = ahorro.get(tipo, 0) + 1
        _guardar(data)


def registrar_groq(resp, modelo: str = "groq") -> None:
    try:
        u = resp.usage
        registrar(modelo, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))
    except Exception:
        pass


def registrar_genai(resp, modelo: str = "gemini") -> None:
    try:
        u = resp.usage_metadata
        entrada = getattr(u, "prompt_token_count", 0) or 0
        salida = (getattr(u, "candidates_token_count", 0) or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
        registrar(modelo, entrada, salida)
    except Exception:
        pass


def uso_hoy() -> dict:
    hoy = date.today().isoformat()
    return _cargar().get(hoy, {"entrada": 0, "salida": 0, "llamadas": 0, "por_modelo": {}})


def uso_total() -> dict:
    """Acumulado histórico (todos los días guardados)."""
    data = _cargar()
    tot = {"entrada": 0, "salida": 0, "llamadas": 0}
    for dia in data.values():
        tot["entrada"] += dia.get("entrada", 0)
        tot["salida"] += dia.get("salida", 0)
        tot["llamadas"] += dia.get("llamadas", 0)
    return tot


def resumen_hoy() -> str:
    """Resumen del día con porcentajes (por modelo y vs presupuesto diario)."""
    d = uso_hoy()
    total = d["entrada"] + d["salida"]
    pct_pres = (total / PRESUPUESTO_DIARIO * 100) if PRESUPUESTO_DIARIO else 0
    lineas = [f"Hoy: {total:,} tokens — {pct_pres:.1f}% del presupuesto diario ({PRESUPUESTO_DIARIO:,})",
              f"   ({d['entrada']:,} entrada · {d['salida']:,} salida · {d['llamadas']} llamadas)"]
    ahorro = d.get("ahorro") or {}
    if ahorro:
        tot_ahorro = sum(ahorro.values())
        lineas.append(f"   Resuelto en tu PC sin gastar nube: {tot_ahorro} turnos "
                      f"({ahorro.get('reflejo', 0)} instantáneos + {ahorro.get('local', 0)} del modelo local)")
    for modelo, m in sorted(d.get("por_modelo", {}).items(), key=lambda x: -(x[1]['entrada'] + x[1]['salida'])):
        mt = m["entrada"] + m["salida"]
        pct = (mt / total * 100) if total else 0
        lineas.append(f"  • {modelo}: {pct:.0f}%  ({mt:,} tokens · {m['llamadas']} llamadas)")
    return "\n".join(lineas)
