import requests
import urllib3
from bs4 import BeautifulSoup

# Silenciar advertencias de SSL para sitios con certificados mal configurados
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def leer_pagina_web(url: str) -> dict:
    """
    Descarga y extrae el texto limpio de una página web.
    Intenta primero con SSL verificado; si falla por certificado,
    reintenta sin verificación (común en sitios de gobierno).
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    }

    # Intentos: primero seguro, luego sin verificar SSL si falla
    intentos = [
        {"verify": True,  "label": "SSL verificado"},
        {"verify": False, "label": "SSL desactivado (certificado inválido)"},
    ]

    for intento in intentos:
        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=10,
                verify=intento["verify"]
            )
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Eliminar basura: scripts, estilos, navegación, footer
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                tag.decompose()

            texto = soup.get_text(separator="\n", strip=True)

            # Limpiar líneas vacías consecutivas
            lineas = [l for l in texto.splitlines() if l.strip()]
            texto_limpio = "\n".join(lineas)

            # Candado de tokens: máximo 2000 caracteres (~500 tokens)
            if len(texto_limpio) > 2000:
                texto_limpio = texto_limpio[:2000] + "\n... [CONTENIDO TRUNCADO]"

            nota = " (⚠️ SSL no verificado)" if not intento["verify"] else ""
            return {"estado": "ok", "url": url + nota, "contenido": texto_limpio}

        except requests.exceptions.SSLError:
            # Solo continuar al siguiente intento si es error SSL
            continue
        except requests.exceptions.Timeout:
            return {"error": f"La página tardó demasiado en responder: {url}"}
        except requests.exceptions.HTTPError as e:
            return {"error": f"Error HTTP {e.response.status_code}: {url}"}
        except Exception as e:
            return {"error": f"No se pudo leer la página: {str(e)}"}

    return {"error": f"No se pudo conectar a {url} ni con SSL verificado ni sin él."}