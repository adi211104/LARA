"""
skills_download.py
Skill de descarga de archivos para LARA.
Colócalo junto a skills_os.py en el mismo paquete.
"""

import requests
from pathlib import Path
from config import WORKSPACE


def descargar_archivo(url: str, nombre: str = "") -> dict:
    """
    Descarga un archivo directo (PDF, ZIP, imagen, datasheet, firmware, etc.)
    al WORKSPACE. Funciona para cualquier link que apunte directo a un archivo,
    NO sirve para páginas como YouTube (usa descargar_video para eso).
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        with requests.get(url, headers=headers, stream=True, timeout=30) as r:
            r.raise_for_status()
            nombre_final = nombre or url.split("/")[-1].split("?")[0] or "descarga.bin"
            target = WORKSPACE / nombre_final
            with open(target, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return {"estado": "ok", "ruta": str(target), "tamano_bytes": target.stat().st_size}
    except requests.exceptions.HTTPError as e:
        return {"error": f"Error HTTP: {e}"}
    except Exception as e:
        return {"error": str(e)}


def descargar_video(url: str) -> dict:
    """
    Descarga un video (YouTube y otros sitios soportados) usando yt-dlp.
    Requiere: pip install yt-dlp
    Úsalo solo para contenido propio o de uso personal legítimo;
    respeta los derechos de autor y los términos de servicio del sitio de origen.
    """
    try:
        import yt_dlp
    except ImportError:
        return {"error": "Falta la librería yt-dlp. Instala con: pip install yt-dlp"}

    try:
        opciones = {
            "outtmpl": str(WORKSPACE / "%(title)s.%(ext)s"),
            "format": "best",
            "quiet": True,
        }
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=True)
        return {"estado": "ok", "titulo": info.get("title", ""), "carpeta": str(WORKSPACE)}
    except Exception as e:
        return {"error": str(e)}
