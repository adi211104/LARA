"""
skills_explicar.py — Genera explicaciones ANIMADAS en un solo lienzo para LARA.

Por qué: a Adi no le gustaban las diapositivas que se borraban entre sí; quiere
UNA sola escena tipo HUD donde el tema entero se va construyendo en "actos".
Cada acto añade elementos visuales (banderas, personas esquemáticas, gráficos
de barras, cajas, flechas...) sobre el MISMO lienzo mientras LARA narra; lo
anterior permanece salvo que el acto pida limpiar.

Formato que devuelve generar_explicacion_visual(tema, contexto=""):
{
 "titulo": "2-4 palabras",
 "narracion_intro": "1 frase que continúa la conversación (sin saludos)",
 "actos": [ {"narracion": "...", "limpiar": false, "elementos": [<elemento>...]} ]
}
Los elementos usan coordenadas en PORCENTAJE 0-100 del lienzo (el renderer de
gui_explicacion.py escala). Todo se sanea aquí: tipos válidos, coordenadas al
rango, máximo de actos/elementos, colores de paleta o #hex reales.

Usa gemini-2.5-flash en modo JSON (barato y fiable para estructura).
"""

import os
import re
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

_MODELO = "gemini-2.5-flash"
_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"),
                       http_options=types.HttpOptions(timeout=90000))

# ── Vocabulario permitido (el renderer solo entiende esto) ─────────────────
_TIPOS = {"titulo", "texto", "caja", "circulo", "linea", "flecha",
          "poligono", "barra", "bandera", "persona",
          "conector", "icono", "anillo", "grafico_linea"}
_ANIMS = {"trazar", "aparecer", "pulsar", "mover"}
_PALETA = {"acento", "naranja", "texto", "suave"}
_POSES = {"parado", "corriendo", "senalando", "pateando", "sentado"}
_ICONOS = {"chispa", "check", "cruz", "reloj", "candado", "engranaje",
           "rayo", "objetivo", "flecha_ciclo", "escudo"}
_MAX_ACTOS = 8
_MAX_ELEMENTOS = 20

_ESQUEMA = """Devuelve SOLO un JSON con esta forma exacta:
{
 "titulo": "tema en 2-4 palabras",
 "narracion_intro": "1 frase hablada que CONTINÚA la conversación en curso",
 "actos": [
   { "narracion": "1-2 frases habladas, naturales, en español",
     "limpiar": false,
     "elementos": [ <elemento>, <elemento>, ... ] }
 ]
}

CATÁLOGO DE ELEMENTOS (coordenadas SIEMPRE en porcentaje 0-100 del lienzo;
x,y es el centro salvo en caja/barra/bandera donde es la esquina superior izquierda):
- {"tipo":"titulo","x":50,"y":7,"texto":"..."}                    → texto grande de acento
- {"tipo":"texto","x":..,"y":..,"texto":"..","color":".."}        → etiqueta corta
- {"tipo":"caja","x":..,"y":..,"w":..,"h":..,"texto":"..","color":".."}
- {"tipo":"circulo","x":..,"y":..,"r":..,"color":"..","relleno":true|false}
- {"tipo":"linea","x1":..,"y1":..,"x2":..,"y2":..,"color":".."}
- {"tipo":"flecha","x1":..,"y1":..,"x2":..,"y2":..,"color":".."}
- {"tipo":"poligono","puntos":[[x,y],[x,y],[x,y]...],"color":"..","relleno":true}
- {"tipo":"barra","x":..,"y":..,"w":..,"h":..,"etiquetas":["A","B"],"valores":[12,30]}
   → mini gráfico de barras con ejes; usa CIFRAS REALES si las conoces
- {"tipo":"bandera","x":..,"y":..,"w":..,"h":..,
   "franjas":[{"color":"#D91023","orientacion":"v"},{"color":"#FFFFFF","orientacion":"v"},{"color":"#D91023","orientacion":"v"}],
   "emblema":{"forma":"circulo|estrella|sol","color":"#hex"}}   (emblema es opcional)
   → bandera construida con franjas; USA LOS COLORES #hex REALES del país
- {"tipo":"persona","x":..,"y":..,"escala":1.0,"pose":"parado|corriendo|senalando|pateando|sentado","color":".."}
   → figura humana esquemática escenificando una acción
- {"tipo":"conector","x1":..,"y1":..,"x2":..,"y2":..,"curvatura":-1.0..1.0,"color":".."}
   → línea CURVA tipo circuito/HUD que conecta dos puntos (mejor que "flecha" para
     enlazar cajas/iconos entre sí sin verse rígido); curvatura 0 = casi recta.
- {"tipo":"icono","x":..,"y":..,"tam":5,"forma":"chispa|check|cruz|reloj|candado|engranaje|rayo|objetivo|flecha_ciclo|escudo","color":".."}
   → glifo vectorial pequeño para reforzar una idea junto a un texto/caja
     (energía=rayo/chispa, seguridad=candado/escudo, proceso=engranaje/flecha_ciclo,
     correcto=check, incorrecto=cruz, tiempo=reloj, meta=objetivo)
- {"tipo":"anillo","x":..,"y":..,"r":..,"grosor":2,"color":".."}
   → aro/arco decorativo tipo radar HUD (puramente estético, para dar
     sensación de interfaz holográfica; úsalo detrás o alrededor de lo importante)
- {"tipo":"grafico_linea","x":..,"y":..,"w":..,"h":..,"etiquetas":["Ene","Feb"],"valores":[10,15,9]}
   → gráfico de tendencia (línea + puntos), para evoluciones en el tiempo; usa
     CIFRAS REALES si las conoces
Todo elemento acepta "anim":"trazar|aparecer|pulsar|mover" ("mover" exige "destino":[x,y])
y "color" con nombre de paleta ("acento","naranja","texto","suave") o un #hex real.

REGLAS DE ORO (en caso de conflicto entre reglas, PRECISIÓN #1 y SIN SOLAPES #2
   ganan siempre sobre densidad/estilo visual — un diagrama feo pero honesto y
   legible es mejor que uno bonito, inventado o ilegible):

1. PRECISIÓN ANTE TODO — es lo más importante de todo el esquema:
   a) Si el mensaje trae un bloque "[HOY ES ...]", esa es la fecha real: cualquier
      fecha de un dato que NO coincida con hoy es de OTRO día y debes decirlo así
      en la narración (ej. "esto fue ayer, 2 de julio"), nunca presentarla como
      si fuera de hoy. Nunca inventes ni asumas la fecha de otra forma.
   b) Si el mensaje trae un bloque "[DATOS CONFIRMADOS POR BÚSQUEDA WEB...]", ese
      bloque es tu ÚNICA fuente de verdad para hechos de actualidad: cada línea
      trae su propia fecha — compárala contra [HOY ES ...] y usa EXACTAMENTE
      esos equipos, marcadores, fechas y nombres, tal cual aparecen. PROHIBIDO
      agregar partidos, rondas, cifras, goleadores o resultados que no estén
      ahí, y PROHIBIDO completar huecos con lo que recuerdes de tu
      entrenamiento (puede ser de una edición/temporada vieja y estarías
      mintiendo). PROHIBIDO usar placeholders genéricos ("Equipo C", "Equipo D",
      "Jugador 2", "Resultado pendiente") para simular un cuadro más completo:
      si no tienes el dato real de una llave/casilla, esa llave/casilla NO SE
      DIBUJA — mejor un bracket incompleto pero 100% real que uno completo con
      relleno inventado.
   c) Si el mensaje trae un bloque "[SIN DATOS FRESCOS]", NO inventes cifras,
      marcadores ni resultados "de hoy": sé honesto en la narración (di que no
      tienes el dato actual) y limita el diagrama a lo genérico/atemporal del
      tema (reglas, formato, historia), dejando claro que no es el estado
      actual.
   d) La DENSIDAD de elementos depende de cuántos datos REALES tengas, nunca al
      revés: si solo hay 2-3 datos confirmados, un acto con 3-5 elementos reales
      y honestos es correcto; jamás infles la cantidad con relleno inventado.
   e) Banderas: usa colores oficiales reales. Si no estás seguro al 100% del
      tono exacto de un país, usa la aproximación más ampliamente reconocida;
      si tienes dudas serias, evita "bandera" y usa "icono" o "texto" en su
      lugar en vez de inventar una combinación de colores no oficial.
2. SIN SOLAPES — nada debe dibujarse encima de otra cosa:
   a) Antes de fijar el x,y de cada elemento, revisa mentalmente que su área no
      pise la de ningún otro elemento del mismo acto (ni de actos previos que
      sigan en pantalla). Dos elementos NUNCA comparten el mismo x,y.
   b) Un "icono" o "anillo" que acompaña a una caja/texto va A UN LADO de ella
      (con su propio x,y claramente fuera del rectángulo de esa caja), NUNCA
      centrado sobre su texto. Una "bandera" junto a una caja de equipo necesita
      su propio espacio separado (dale su w completo de por medio): nunca la
      superpongas al texto de la caja.
   c) Los elementos puramente decorativos ("anillo" sin función, "icono" suelto)
      van en zonas VACÍAS del lienzo (esquinas, márgenes, huecos entre
      secciones) — nunca sobre una caja, bandera, texto o conector. Ante la
      duda, omite el adorno antes que tapar contenido.
   d) Si pides un formato de LLAVES/BRACKET: cajas del mismo tamaño alineadas en
      columnas por ronda (ronda 1 a la izquierda, siguientes rondas hacia la
      derecha), con separación vertical pareja entre cajas de la misma columna;
      une cada enfrentamiento con un "conector" de curvatura baja (0.1 a 0.25)
      que vaya limpio de una caja a la siguiente SIN cruzar otras cajas ni
      conectores. Nada de círculos/anillos grandes en medio del árbol: estorban
      las líneas y el texto.
3. UNA sola escena que se CONSTRUYE: de 3 a 8 actos; lo dibujado PERMANECE entre
   actos (pon "limpiar":true solo si de verdad necesitas empezar una sección nueva).
   Distribuye los elementos por TODO el lienzo para que no se solapen entre actos.
4. Esto es un HUD holográfico tipo JARVIS, NO una lista de viñetas: combina al
   menos dos tipos de elemento distintos por acto cuando los datos lo permitan
   (cajas + conectores, banderas + barras, persona + texto...). Usa "pulsar"
   para destacar lo importante en naranja y "conector" en vez de "flecha" al
   enlazar piezas del diagrama entre sí. La variedad visual NUNCA justifica
   inventar contenido ni solapar elementos (reglas 1 y 2 mandan).
5. PROHIBIDO ser genérico: nada de cajas vacías con una sola palabra ni frases
   de relleno tipo "esto es importante". Cada texto/caja debe llevar un dato,
   nombre, cifra, paso o ejemplo CONCRETO y específico del tema pedido — si el
   tema lo permite, con números reales (fechas, cantidades, medidas, marcador),
   y siempre reales (regla 1), nunca inventados para "verse completo".
6. PROHIBIDO saludar, presentarte o decir tu nombre ("hola", "saludos", "soy LARA"):
   la narración fluye como si continuaras la charla que ya está en curso.
7. Texto plano: sin markdown, sin asteriscos, sin LaTeX, sin emojis.
8. El TEMA puede incluir una petición de FORMATO ("...en un diagrama de llaves",
   "...en una línea de tiempo"): respétalo con las primitivas disponibles
   (línea de tiempo = "grafico_linea" o cajas alineadas cronológicamente con
   conectores) pero el CONTENIDO de cada pieza sale del CONTEXTO real (regla 1),
   nunca inventado para "rellenar" el formato pedido.
9. Las narraciones son lo que LARA dice en voz alta mientras dibuja: claras, concretas
   y cortas (nada de relleno vago: cada frase debe aportar un hecho del tema)."""


def _parsear_json(texto: str) -> dict:
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    candidato = m.group(0) if m else texto
    candidato = re.sub(r",\s*([}\]])", r"\1", candidato)
    try:
        return json.loads(candidato)
    except json.JSONDecodeError:
        candidato += "]" * (candidato.count("[") - candidato.count("]"))
        candidato += "}" * (candidato.count("{") - candidato.count("}"))
        candidato = re.sub(r",\s*([}\]])", r"\1", candidato)
        return json.loads(candidato)


# ── Saneo ───────────────────────────────────────────────────────────────────
def _num(v, defecto=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(defecto)


def _pct(v, defecto=50.0):
    """Coordenada en porcentaje, siempre dentro de 0-100."""
    return max(0.0, min(100.0, _num(v, defecto)))


def _color_valido(c):
    """Acepta nombre de paleta o #hex de 6 (o 3) dígitos; si no, None."""
    if not isinstance(c, str):
        return None
    c = c.strip().lower()
    if c in _PALETA:
        return c
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        return c
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        return "#" + "".join(ch * 2 for ch in c[1:])
    return None


def _clamp_bbox(x, y, w, h):
    """Ajusta la esquina (x,y) para que la caja w×h no se salga de 0-100."""
    x = max(0.0, min(x, 100.0 - w))
    y = max(0.0, min(y, 100.0 - h))
    return x, y


def _clamp_centro(cx, cy, radio_x, radio_y):
    """Ajusta un centro para que su bbox (±radio) no se salga de 0-100."""
    radio_x = min(radio_x, 50.0)
    radio_y = min(radio_y, 50.0)
    cx = max(radio_x, min(cx, 100.0 - radio_x))
    cy = max(radio_y, min(cy, 100.0 - radio_y))
    return cx, cy


def _san_elemento(e):
    """Valida y normaliza un elemento; devuelve None si es inservible."""
    if not isinstance(e, dict):
        return None
    t = str(e.get("tipo", "")).strip().lower()
    if t not in _TIPOS:
        return None
    el = {"tipo": t}

    anim = str(e.get("anim", "")).strip().lower()
    if anim in _ANIMS:
        el["anim"] = anim
    c = _color_valido(e.get("color"))
    if c:
        el["color"] = c

    if t in ("linea", "flecha", "conector"):
        for k in ("x1", "y1", "x2", "y2"):
            el[k] = _pct(e.get(k))
    elif t == "poligono":
        pts = []
        for p in (e.get("puntos") or []):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append([_pct(p[0]), _pct(p[1])])
        if len(pts) < 3:
            return None
        el["puntos"] = pts[:24]
        el["relleno"] = bool(e.get("relleno", True))
    else:
        el["x"] = _pct(e.get("x"))
        el["y"] = _pct(e.get("y"))

    if t in ("titulo", "texto", "caja"):
        el["texto"] = str(e.get("texto", "")).strip()[:90]
    # cajas/barras/banderas usan (x,y) como esquina: si w/h las saca del
    # lienzo, se reubica la esquina (nunca se recorta el tamaño pedido).
    if t in ("caja", "barra", "bandera"):
        el["w"] = max(2.0, min(96.0, _num(e.get("w"), 20)))
        el["h"] = max(2.0, min(90.0, _num(e.get("h"), 10)))
        el["x"], el["y"] = _clamp_bbox(el["x"], el["y"], el["w"], el["h"])
    if t == "circulo":
        el["r"] = max(0.5, min(45.0, _num(e.get("r"), 5)))
        el["relleno"] = bool(e.get("relleno", False))
        el["x"], el["y"] = _clamp_centro(el["x"], el["y"], el["r"], el["r"])
    if t == "anillo":
        el["r"] = max(3.0, min(45.0, _num(e.get("r"), 15)))
        el["grosor"] = max(1.0, min(6.0, _num(e.get("grosor"), 2)))
        el["x"], el["y"] = _clamp_centro(el["x"], el["y"], el["r"], el["r"])
    if t == "icono":
        el["forma"] = str(e.get("forma", "")).strip().lower()
        if el["forma"] not in _ICONOS:
            el["forma"] = "chispa"
        el["tam"] = max(2.0, min(14.0, _num(e.get("tam"), 5)))
        el["x"], el["y"] = _clamp_centro(el["x"], el["y"], el["tam"], el["tam"])
    if t == "grafico_linea":
        el["w"] = max(10.0, min(96.0, _num(e.get("w"), 40)))
        el["h"] = max(8.0, min(90.0, _num(e.get("h"), 25)))
        el["x"], el["y"] = _clamp_bbox(el["x"], el["y"], el["w"], el["h"])
        vals = []
        for v in (e.get("valores") or [])[:10]:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
        if len(vals) < 2:
            return None
        el["valores"] = vals
        el["etiquetas"] = [str(s).strip()[:14]
                           for s in (e.get("etiquetas") or [])[:len(vals)]]
    if t == "barra":
        vals = []
        for v in (e.get("valores") or [])[:6]:
            try:
                vals.append(abs(float(v)))
            except (TypeError, ValueError):
                pass
        if not vals:
            return None
        el["valores"] = vals
        el["etiquetas"] = [str(s).strip()[:14]
                           for s in (e.get("etiquetas") or [])[:len(vals)]]
    if t == "bandera":
        franjas = []
        for f in (e.get("franjas") or [])[:6]:
            if isinstance(f, dict):
                fc = _color_valido(f.get("color")) or "#9aa7b1"
                fo = "h" if str(f.get("orientacion", "v")).lower().startswith("h") else "v"
                franjas.append({"color": fc, "orientacion": fo})
        if not franjas:
            return None
        el["franjas"] = franjas
        emb = e.get("emblema")
        if isinstance(emb, dict):
            forma = str(emb.get("forma", "circulo")).strip().lower()
            if forma in ("circulo", "estrella", "sol"):
                el["emblema"] = {"forma": forma,
                                 "color": _color_valido(emb.get("color")) or "#d4a017"}
    if t == "persona":
        el["escala"] = max(0.4, min(4.0, _num(e.get("escala"), 1.0)))
        pose = str(e.get("pose", "parado")).strip().lower()
        pose = pose.replace("ñ", "n").replace("á", "a").replace("é", "e")
        el["pose"] = pose if pose in _POSES else "parado"
        # La figura ocupa ~5.5*escala% a los lados y ~7.5*escala% arriba/abajo
        # del centro (ver gui_explicacion._el_persona): reubicar para que quepa.
        mx, my = 5.5 * el["escala"], 7.5 * el["escala"]
        el["x"], el["y"] = _clamp_centro(el["x"], el["y"], mx, my)
    if t == "conector":
        el["curvatura"] = max(-1.0, min(1.0, _num(e.get("curvatura"), 0.35)))

    dest = e.get("destino")
    if isinstance(dest, (list, tuple)) and len(dest) >= 2:
        el["destino"] = [_pct(dest[0]), _pct(dest[1])]
    if el.get("anim") == "mover" and "destino" not in el:
        el["anim"] = "aparecer"   # sin destino no hay a dónde mover
    return el


def _san_actos(brutos):
    """Filtra la lista de actos del modelo al formato que espera el renderer."""
    actos = []
    for a in (brutos or [])[:_MAX_ACTOS]:
        if not isinstance(a, dict):
            continue
        elementos = []
        for e in (a.get("elementos") or [])[:_MAX_ELEMENTOS * 2]:
            el = _san_elemento(e)
            if el:
                elementos.append(el)
            if len(elementos) >= _MAX_ELEMENTOS:
                break
        narr = str(a.get("narracion", "")).strip()
        if not elementos and not narr:
            continue
        actos.append({"narracion": narr,
                      "limpiar": bool(a.get("limpiar", False)),
                      "elementos": elementos})
    return actos


def generar_explicacion_visual(tema: str, contexto: str = "") -> dict:
    """Genera la escena animada por actos. Devuelve dict o {'error':...}."""
    import datetime as _dt
    _dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    _hoy = _dt.datetime.now()
    fecha_hoy = f"{_dias[_hoy.weekday()]} {_hoy.strftime('%d/%m/%Y')}"

    extra = ""
    if contexto:
        extra = ("\nCONTEXTO (turnos previos y/o datos de búsqueda; si trae un bloque "
                 "[DATOS CONFIRMADOS POR BÚSQUEDA WEB] o [SIN DATOS FRESCOS], sigue la regla de "
                 f"PRECISIÓN del punto 1 al pie de la letra):\n{contexto}\n")
    prompt = (f"Eres el motor visual de LARA, una asistente tipo JARVIS que ya está "
              f"conversando con el usuario. [HOY ES {fecha_hoy}] — esta es la fecha real "
              f"ahora mismo; cualquier dato con otra fecha es de OTRO día, dilo así.\n"
              f"Construye una escena animada que explique: {tema}.{extra}\n\n{_ESQUEMA}")
    try:
        resp = _client.models.generate_content(
            model=_MODELO,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.5,
                max_output_tokens=12000,
                response_mime_type="application/json",
                # Presupuesto de "pensamiento" para que separe bien qué datos
                # del CONTEXTO son reales antes de escribir el JSON final: en 0
                # el modelo tendía a rellenar huecos con memoria vieja/incorrecta.
                thinking_config=(types.ThinkingConfig(thinking_budget=1024)),
            ),
        )
        try:
            from core.uso_tokens import registrar_genai
            registrar_genai(resp, _MODELO)
        except Exception:
            pass
        data = _parsear_json((resp.text or "{}").strip())

        titulo = str(data.get("titulo", "") or tema[:40]).strip()[:48]
        intro = str(data.get("narracion_intro", "")).strip()
        # Sin saludos aunque el modelo desobedezca: recorta la frase de saludo.
        intro = re.sub(r"^\s*(¡?\s*(hola|saludos|buen[oa]s?\b[^.!,]*)[.!,]?\s*)+",
                       "", intro, flags=re.IGNORECASE)
        intro = re.sub(r"^\s*soy\s+lara[^.!]*[.!]\s*", "", intro, flags=re.IGNORECASE)

        actos = _san_actos(data.get("actos"))
        if not actos:
            return {"error": "no se generaron actos"}
        return {"titulo": titulo, "narracion_intro": intro, "actos": actos}
    except Exception as e:
        return {"error": str(e)}
