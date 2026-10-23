"""
skills_vision.py — Ojos de LARA (multimodal con Gemini).

Capacidades:
- analizar_imagen(ruta, pregunta): describe/analiza una foto o captura.
- analizar_pantalla(pregunta): toma captura de la pantalla del usuario y la
  analiza para recomendar qué hacer (estilo JARVIS mirando por encima del hombro).
- analizar_archivo(ruta, pregunta): entiende imágenes, PDF, video, audio y
  documentos de texto/código. Usa la File API de Gemini para formatos pesados.
- aprender_de_archivo(ruta): analiza un archivo y guarda un resumen permanente
  en la base de conocimiento de LARA, para que "aprenda" de él entre sesiones.

Bajo costo: usa gemini-2.5-flash (multimodal, barato). Para texto/código extrae
el contenido localmente y lo manda como texto (más barato que subir el archivo).
"""

import os
import time
from pathlib import Path

from google import genai
from dotenv import load_dotenv

load_dotenv()

VISION_MODEL = "gemini-2.5-flash"
from google.genai import types as _types
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"),
                       http_options=_types.HttpOptions(timeout=90000)) if os.getenv("GEMINI_API_KEY") else None

_PERSONA = ("Eres LARA, asistente personal tipo JARVIS para el taller de electrónica de Adi. "
            "Respondes en español, claro y directo, con un toque de humor seco ocasional. "
            "Sin markdown ni LaTeX (se lee en consola/voz): texto plano.")

# Extensiones por tipo de manejo
_IMG = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
_TEXTO = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html", ".css",
          ".c", ".cpp", ".h", ".java", ".xml", ".ini", ".log", ".bat", ".ps1", ".sql"}
_SUBIR = {".pdf", ".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".ogg",
          ".docx", ".pptx", ".xlsx"}  # formatos que conviene subir a la File API


def _generar(contenido, max_tokens: int = 1200) -> str:
    """Llamada multimodal a Gemini con reintentos ante errores transitorios."""
    if _client is None:
        raise RuntimeError("La visión necesita GEMINI_API_KEY. Configura ese proveedor para analizar imágenes.")
    import time
    from google.genai import types
    try:
        _pensar = types.ThinkingConfig(thinking_budget=0)
    except Exception:
        _pensar = None
    cfg = types.GenerateContentConfig(
        temperature=0.3, max_output_tokens=max_tokens, system_instruction=_PERSONA,
        thinking_config=_pensar,
    )
    ultimo = None
    for intento in range(3):
        try:
            resp = _client.models.generate_content(model=VISION_MODEL, contents=contenido, config=cfg)
            try:
                from core.uso_tokens import registrar_genai
                registrar_genai(resp, VISION_MODEL)
            except Exception:
                pass
            return (resp.text or "").strip() or "(Gemini no devolvió texto para este contenido.)"
        except Exception as e:
            ultimo = e
            transitorio = any(s in str(e) for s in ("429", "503", "500", "UNAVAILABLE", "INTERNAL", "overloaded"))
            if transitorio and intento < 2:
                time.sleep(4 * (intento + 1))
                continue
            raise
    raise ultimo


def analizar_imagen(ruta: str, pregunta: str = "") -> dict:
    """Analiza una imagen local pasándola en línea (rápido, sin subida)."""
    try:
        from PIL import Image
        p = Path(ruta)
        if not p.exists():
            return {"error": f"No encuentro la imagen: {ruta}"}
        img = Image.open(p)
        prompt = pregunta or ("Describe con detalle qué hay en esta imagen. Si es un circuito, "
                              "componente, placa o equipo electrónico, identifícalo e indica lo "
                              "relevante para repararlo o usarlo.")
        texto = _generar([img, prompt])
        return {"estado": "ok", "respuesta": texto}
    except Exception as e:
        return {"error": str(e)}


def analizar_pantalla(pregunta: str = "") -> dict:
    """Captura la pantalla y la analiza para recomendar acciones."""
    try:
        from PIL import ImageGrab
        captura = ImageGrab.grab()
        prompt = pregunta or ("Esta es la pantalla actual de Adi. Mira qué está haciendo y dale una "
                              "recomendación útil y concreta: posibles errores en pantalla, siguiente "
                              "paso lógico, o un atajo. Si no hay nada que recomendar, dilo en una línea.")
        texto = _generar([captura, prompt])
        return {"estado": "ok", "respuesta": texto}
    except Exception as e:
        return {"error": str(e)}


def _extraer_texto_local(p: Path) -> str:
    """Extrae texto de archivos de texto/código, PDF y docx sin subirlos."""
    ext = p.suffix.lower()
    if ext in _TEXTO:
        return p.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            r = PdfReader(str(p))
            return "\n".join((pag.extract_text() or "") for pag in r.pages)
        except Exception:
            return ""  # PDF escaneado/imagen -> se subirá a la File API
    if ext == ".docx":
        try:
            import docx
            d = docx.Document(str(p))
            return "\n".join(par.text for par in d.paragraphs)
        except Exception:
            return ""
    return ""


def _analizar_subiendo(p: Path, prompt: str) -> str:
    """Sube archivos pesados (video/audio/pdf escaneado) a la File API y los analiza."""
    archivo = _client.files.upload(file=str(p))
    # Video/audio requieren procesamiento: esperar a estado ACTIVE
    espera = 0
    while getattr(archivo.state, "name", str(archivo.state)) == "PROCESSING" and espera < 120:
        time.sleep(2)
        espera += 2
        archivo = _client.files.get(name=archivo.name)
    try:
        return _generar([archivo, prompt], max_tokens=1500)
    finally:
        try:
            _client.files.delete(name=archivo.name)
        except Exception:
            pass


def analizar_archivo(ruta: str, pregunta: str = "") -> dict:
    """
    Analiza cualquier archivo soportado: imagen, PDF, video, audio, documento o
    código. Elige la vía más barata (texto local) o la File API si hace falta.
    """
    try:
        p = Path(ruta)
        if not p.exists():
            return {"error": f"No encuentro el archivo: {ruta}"}
        ext = p.suffix.lower()

        if ext in _IMG:
            return analizar_imagen(str(p), pregunta)

        prompt_def = ("Analiza este archivo y resume su contenido y lo importante para Adi "
                      "(taller de electrónica). Si hay datos técnicos, números o pasos, consérvalos.")
        prompt = pregunta or prompt_def

        # 1) Intento barato: extraer texto local
        if ext in _TEXTO or ext in {".pdf", ".docx"}:
            texto = _extraer_texto_local(p)
            if texto and texto.strip():
                if len(texto) > 30000:
                    texto = texto[:30000] + "\n...[truncado]"
                resp = _generar([f"{prompt}\n\nContenido del archivo '{p.name}':\n{texto}"])
                return {"estado": "ok", "respuesta": resp}

        # 2) Formatos pesados o PDF escaneado -> File API
        if ext in _SUBIR or ext in _IMG:
            resp = _analizar_subiendo(p, prompt)
            return {"estado": "ok", "respuesta": resp}

        return {"error": f"Tipo de archivo no soportado para análisis: {ext}"}
    except Exception as e:
        return {"error": str(e)}


def aprender_de_archivo(ruta: str, categoria: str = "aprendido") -> dict:
    """
    Analiza un archivo y guarda un resumen permanente en la base de conocimiento.
    Así LARA 'aprende' de fotos/PDFs/videos que le subes y lo recuerda después.
    """
    try:
        from core.skills_conocimiento import guardar_conocimiento
    except Exception:
        from skills_conocimiento import guardar_conocimiento

    p = Path(ruta)
    if not p.exists():
        return {"error": f"No encuentro el archivo: {ruta}"}

    res = analizar_archivo(str(p), pregunta=(
        "Resume este archivo en hechos concretos y reutilizables para un taller de electrónica. "
        "Incluye datos técnicos, modelos, valores y pasos. Empieza con una línea de título."))
    if "error" in res:
        return res

    contenido = res["respuesta"]
    titulo = f"Aprendido de {p.name}"
    primera = contenido.strip().split("\n", 1)[0][:60]
    if primera:
        titulo = primera

    guardado = guardar_conocimiento(
        titulo=titulo,
        contenido=f"[Fuente: {p.name}]\n\n{contenido}",
        categoria=categoria,
        tags=f"{p.suffix.lower().strip('.')}, archivo, aprendido",
    )
    return {"estado": "ok", "titulo": titulo, "resumen": contenido, "guardado": guardado}
