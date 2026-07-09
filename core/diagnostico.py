"""
diagnostico.py — Auto-chequeo de LARA (Mejora de robustez).

Comprueba de un vistazo qué subsistemas están sanos: micrófono, STT local y en
la nube, voz (TTS), conexión con los modelos, perfiles de voz, wake word local
y memoria. Sirve para que, cuando algo "a veces no funciona", tú (o LARA) veáis
al instante DÓNDE está el problema en vez de adivinar.

Se invoca por voz/texto ("diagnóstico", "revísate") o desde código:
    from core import diagnostico
    print(diagnostico.texto())

Cada prueba está aislada: si una falla, las demás igual se ejecutan.
"""

import os


def _ok(cond):
    # ASCII a propósito: la consola de Windows (cp1252) crashea con emojis, y
    # este texto se imprime tanto en la GUI como en la consola.
    return "[OK]  " if cond else "[X]   "


def _probar_microfono() -> tuple:
    try:
        from core.skills_audio import listar_dispositivos
        d = listar_dispositivos()
        entradas = d.get("entrada", [])
        if entradas:
            return True, f"{len(entradas)} micrófono(s) detectado(s)"
        return False, "no se detectó ningún micrófono de entrada"
    except Exception as e:
        return False, f"error al listar dispositivos: {e}"


def _probar_stt_local() -> tuple:
    try:
        from core import stt_local
        if stt_local.disponible():
            return True, "Whisper local cargado (offline, rápido)"
        # Intento de carga (por si aún no se probó)
        if stt_local.precargar():
            return True, "Whisper local disponible (offline)"
        return False, "no instalado — se usará Whisper en la nube (pip install faster-whisper)"
    except Exception as e:
        return False, f"error: {e}"


def _probar_tts() -> tuple:
    try:
        import edge_tts  # noqa: F401
        from core import skills_voz
        return True, f"voz neuronal lista (velocidad {skills_voz.VELOCIDAD_TTS})"
    except Exception:
        try:
            import pyttsx3  # noqa: F401
            return True, "solo voz offline (edge-tts no disponible)"
        except Exception as e:
            return False, f"sin motor de voz: {e}"


def _probar_llm() -> tuple:
    """Ping mínimo al router Groq (1 token). Reporta si responde o falla."""
    groq_key = os.getenv("GROQ_API_KEY")
    gem_key = os.getenv("GEMINI_API_KEY")
    if not groq_key and not gem_key:
        return False, "faltan las API keys (revisa el archivo .env)"
    try:
        from core.llm_engine import client, MODELO_ROUTER
        # Timeout corto y sin reintentos: un diagnóstico no debe colgarse 90 s.
        cli = client
        try:
            cli = client.with_options(timeout=8.0, max_retries=0)
        except Exception:
            pass
        r = cli.chat.completions.create(
            model=MODELO_ROUTER,
            messages=[{"role": "user", "content": "di ok"}],
            max_tokens=3, temperature=0,
        )
        _ = r.choices[0].message.content
        return True, "modelos respondiendo con normalidad"
    except Exception as e:
        s = str(e)
        if any(k in s for k in ("503", "429", "UNAVAILABLE", "rate")):
            return False, "los servidores de IA están saturados ahora (temporal); hay respaldo automático"
        return False, f"no respondió: {s[:80]}"


def _probar_llm_local() -> tuple:
    try:
        from core import llm_local
        if not llm_local.activo():
            return True, "desactivado a propósito (LARA_LLM_LOCAL=0); todo va a la nube"
        if llm_local.disponible():
            st = llm_local.stats()
            return True, (f"cerebro local activo (Ollama: {llm_local.modelo()}) — "
                          f"{st['respuestas']} respuestas locales esta sesión, {st['fallos']} caídas a nube")
        return False, (f"Ollama no responde o falta el modelo ({llm_local.modelo()}). "
                       f"Abre Ollama y/o haz: ollama pull {llm_local.modelo()} — mientras, uso la nube")
    except Exception as e:
        return False, f"error: {e}"


def _probar_voz_id() -> tuple:
    try:
        from core import voz_id
        if not voz_id.disponible():
            return False, "no disponible (falta numpy)"
        gente = voz_id.personas()
        if gente:
            return True, f"perfiles: {', '.join(gente)}"
        return True, "activo, pero sin voces enroladas (di 'aprende mi voz')"
    except Exception as e:
        return False, f"error: {e}"


def _probar_wake_local() -> tuple:
    try:
        from core import wake_word
        if wake_word.local_disponible():
            return True, "wake word local instantáneo activo (openWakeWord)"
        return False, "sin wake word local — usa el de transcripción (pip install openwakeword)"
    except Exception as e:
        return False, f"error: {e}"


def _probar_memoria() -> tuple:
    try:
        from core import memoria_vectorial
        n = memoria_vectorial.total()
        modo = "por palabras (sin embeddings)"
        try:
            from core import llm_local
            if llm_local.embed_disponible():
                modo = f"semántica LOCAL ({llm_local.modelo_embed()}) — sin gastar nube"
            else:
                modo = ("semántica en la nube (Gemini). Para hacerla local: "
                        f"ollama pull {llm_local.modelo_embed()}")
        except Exception:
            pass
        return True, f"{n} recuerdos indexados — búsqueda {modo}"
    except Exception as e:
        return False, f"error: {e}"


def ejecutar(incluir_llm: bool = True) -> dict:
    """Corre todas las pruebas y devuelve {nombre: (ok, detalle)}."""
    pruebas = {
        "Micrófono": _probar_microfono,
        "Voz (salida)": _probar_tts,
        "Reconocimiento de voz (STT) local": _probar_stt_local,
        "Reconocimiento de hablante": _probar_voz_id,
        "Wake word local": _probar_wake_local,
        "Cerebro local (Ollama)": _probar_llm_local,
        "Memoria semántica": _probar_memoria,
    }
    if incluir_llm:
        pruebas["Conexión con los modelos (nube)"] = _probar_llm
    resultado = {}
    for nombre, fn in pruebas.items():
        try:
            resultado[nombre] = fn()
        except Exception as e:
            resultado[nombre] = (False, f"fallo inesperado: {e}")
    return resultado


def texto(incluir_llm: bool = True) -> str:
    """Diagnóstico formateado como texto legible/hablable."""
    res = ejecutar(incluir_llm=incluir_llm)
    lineas = ["Diagnóstico de sistemas:"]
    for nombre, (ok, detalle) in res.items():
        lineas.append(f"{_ok(ok)} {nombre}: {detalle}")
    fallos = [n for n, (ok, _) in res.items() if not ok]
    if fallos:
        lineas.append("")
        lineas.append("A revisar: " + ", ".join(fallos) + ".")
    else:
        lineas.append("")
        lineas.append("Todos los sistemas operativos.")
    return "\n".join(lineas)


def resumen_hablado(incluir_llm: bool = False) -> str:
    """Versión corta para decir en voz alta (sin listar todo si está bien)."""
    res = ejecutar(incluir_llm=incluir_llm)
    fallos = [n for n, (ok, _) in res.items() if not ok]
    if not fallos:
        return "Todos los sistemas operativos y en línea."
    return "Detecté problemas en: " + ", ".join(fallos) + ". Revisa la pantalla para el detalle."
