"""
voz_id.py — Reconocimiento de hablantes multi-persona para LARA.

¿Por qué esta versión? La huella antigua (solo media+desviación de MFCC) era tan
pobre que casi cualquier par de voces superaba el umbral: LARA llamó "adi" a dos
personas distintas. Ahora la huella es mucho más rica y la decisión más estricta:

  Huella (64 dimensiones, en 4 bloques ponderados y normalizados por separado):
    1. MFCC (13 coef.): media + desviación ................ timbre (26)
    2. DELTAS de MFCC: media + desviación ................. dinámica temporal (26)
    3. Prosodia: cruces por cero y centroide espectral
       (media + desviación de cada uno) ................... brillo/pitch aprox. (4)
    4. Proporción de energía en 4 bandas de frecuencia
       (media + desviación) ............................... distribución tonal (8)

  Decisión (identificar): el coseno entre huellas crudas SATURA cerca de 1
  (todas las voces comparten un componente enorme común al habla), así que con
  2+ personas enroladas primero se ESTANDARIZAN las huellas: se resta la media
  de los centros de todos los perfiles y se divide por la dispersión por
  dimensión. Eso elimina el componente común y el coseno pasa a medir solo lo
  que distingue a las personas entre sí. El puntaje de cada persona es la MEDIA
  DEL TOP-3 de similitudes con sus muestras (no el máximo de una sola, que es
  ruidoso). Solo se acepta si el mejor puntaje >= UMBRAL, saca al segundo una
  ventaja >= MARGEN y ADEMÁS el audio queda a distancia razonable del perfil
  ganador (<= FACTOR_DIST x su dispersión interna): ese freno detiene a voces
  desconocidas que "apuntan" en la dirección de alguien pero desde lejos.
  Si solo hay una persona enrolada no
  hay con quién estandarizar ni comparar, así que se usa el coseno crudo con un
  umbral muy exigente (UMBRAL_SOLO). Si nada de eso se cumple se devuelve
  (None, mejor_score): mejor decir "no te reconozco" que confundir personas.

  MFCC: se usa python_speech_features si está instalado; si no, hay un fallback
  SOLO-numpy (banco de filtros mel + DCT propio), así disponible() es True con
  solo numpy. Ambos producen 13 coeficientes -> misma dimensión de huella.

  COMPATIBILIDAD con voces.json antiguo: las huellas viejas tenían 26 dimensiones;
  al cargar se DESCARTAN las muestras cuya dimensión no coincida con DIM y se
  ignoran los perfiles que queden vacíos (habrá que re-enrolar esas voces con el
  entrenamiento guiado; ver frases_entrenamiento()).

Datos en KNOWLEDGE_DIR/voces.json. El candado de voz está APAGADO por defecto.
"""

import json
import numpy as np
from pathlib import Path

from config import KNOWLEDGE_DIR

RUTA = KNOWLEDGE_DIR / "voces.json"
CONFIG = KNOWLEDGE_DIR / "voz_config.json"

# ── Calibración (ajustada con el test sintético de 3 voces + impostor; luego
# ENDURECIDA tras confundir a dos personas reales entre sí: mejor decir "no te
# reconozco" que ponerle a alguien el nombre de otro — ver identificar()). ──
# UMBRAL/MARGEN operan sobre el coseno ESTANDARIZADO (ver identificar): ahí la
# misma voz puntúa alto (>0.7) y las demás bajan mucho (a menudo negativo).
UMBRAL = 0.62        # puntaje mínimo del mejor candidato para aceptar
MARGEN = 0.26        # ventaja mínima del mejor sobre el segundo candidato
UMBRAL_SOLO = 0.93   # coseno CRUDO mínimo cuando solo hay UNA persona enrolada
FACTOR_DIST = 1.6    # la distancia del audio al perfil ganador no puede superar
                     # FACTOR_DIST x la dispersión interna de ese perfil (frena
                     # a impostores que "apuntan" hacia una persona pero de lejos)
MAX_MUESTRAS = 14    # muestras máximas guardadas por persona (antes 10: con más
                     # muestras el centro/dispersión de cada perfil es más fiable)

# ── Geometría de la huella ──
NUMCEP = 13
_BANDAS_HZ = (0.0, 300.0, 1000.0, 3000.0)  # límites inferiores; la última llega a sr/2
DIM = 2 * NUMCEP + 2 * NUMCEP + 4 + 2 * (len(_BANDAS_HZ))  # 26+26+4+8 = 64

# Peso de cada bloque dentro de la huella (cada bloque se normaliza a norma 1
# y luego se multiplica por su peso: así ningún bloque domina por su escala).
_PESO_MFCC = 1.0
_PESO_DELTA = 0.8
_PESO_PROSODIA = 0.6
_PESO_BANDAS = 0.6

_NFFT = 1024
_VENTANA_S = 0.025   # ventana de análisis: 25 ms
_SALTO_S = 0.010     # salto entre ventanas: 10 ms


def _cargar(p, defecto):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return defecto
    return defecto


def _guardar(p, data):
    try:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cargar_voces() -> dict:
    """
    Carga voces.json descartando muestras de dimensión incompatible.

    Las huellas del formato antiguo (26 dims) no son comparables con las nuevas
    (DIM dims): mezclar dimensiones rompería el coseno, así que se filtran aquí.
    Un perfil que quede sin muestras válidas se ignora por completo.
    """
    crudas = _cargar(RUTA, {})
    voces = {}
    for nombre, muestras in crudas.items():
        validas = [m for m in muestras if isinstance(m, list) and len(m) == DIM]
        if validas:
            voces[nombre] = validas
    return voces


def disponible() -> bool:
    """True si se puede calcular la huella (numpy basta gracias al fallback)."""
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


# ── Extracción de características ───────────────────────────────
def _en_tramas(señal: np.ndarray, sr: int):
    """Divide la señal en tramas solapadas (25 ms / 10 ms). None si es muy corta."""
    n = int(sr * _VENTANA_S)
    salto = int(sr * _SALTO_S)
    if señal.size < n:
        return None
    num = 1 + (señal.size - n) // salto
    idx = np.arange(n)[None, :] + salto * np.arange(num)[:, None]
    return señal[idx]


def _mfcc_numpy(señal: np.ndarray, sr: int) -> np.ndarray:
    """
    MFCC solo-numpy (fallback si falta python_speech_features):
    pre-énfasis -> tramas Hamming -> espectro de potencia -> banco de 26 filtros
    mel -> log -> DCT-II ortonormal -> primeros NUMCEP coeficientes.
    """
    nfilt = 26
    señal = np.append(señal[0], señal[1:] - 0.97 * señal[:-1])  # pre-énfasis
    tramas = _en_tramas(señal, sr)
    if tramas is None:
        return np.zeros((0, NUMCEP))
    tramas = tramas * np.hamming(tramas.shape[1])
    potencia = (np.abs(np.fft.rfft(tramas, _NFFT)) ** 2) / _NFFT

    # Banco de filtros triangulares en escala mel
    a_mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
    de_mel = lambda m: 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    puntos_hz = de_mel(np.linspace(0.0, a_mel(sr / 2.0), nfilt + 2))
    bins = np.floor((_NFFT + 1) * puntos_hz / sr).astype(int)
    banco = np.zeros((nfilt, _NFFT // 2 + 1))
    for i in range(nfilt):
        a, b, c = bins[i], bins[i + 1], bins[i + 2]
        if b > a:
            banco[i, a:b] = (np.arange(a, b) - a) / (b - a)
        if c > b:
            banco[i, b:c] = (c - np.arange(b, c)) / (c - b)

    energia = np.log(np.maximum(potencia @ banco.T, 1e-10))

    # DCT-II ortonormal implementada como producto matricial (sin scipy)
    k = np.arange(nfilt)
    dct = np.cos(np.pi * np.outer(np.arange(NUMCEP), (2 * k + 1) / (2.0 * nfilt)))
    dct *= np.sqrt(2.0 / nfilt)
    dct[0] /= np.sqrt(2.0)
    return energia @ dct.T


def _mfcc(señal: np.ndarray, sr: int) -> np.ndarray:
    """MFCC con python_speech_features si existe; si no, el fallback numpy."""
    try:
        from python_speech_features import mfcc as _psf_mfcc
        return _psf_mfcc(señal, samplerate=sr, numcep=NUMCEP, nfft=_NFFT)
    except Exception:
        return _mfcc_numpy(señal, sr)


def _bloque(v, peso: float) -> np.ndarray:
    """Normaliza un bloque de características a norma 1 y le aplica su peso."""
    v = np.asarray(v, dtype=np.float64)
    n = np.linalg.norm(v)
    return (v / n) * peso if n > 0 else v * 0.0


_RECORTE_VENTANA_S = 0.020   # ventanas de energía para detectar dónde hay voz
_RECORTE_MARGEN_S = 0.08     # margen que se deja a cada lado de la voz detectada


def _recortar_silencio(señal: np.ndarray, sr: int) -> np.ndarray:
    """
    Recorta el silencio/ruido de fondo al inicio y final de la grabación.

    ¿Por qué? escuchar() graba hasta 1.3-1.8 s de silencio DESPUÉS de que la
    persona deja de hablar (para no cortar a media frase), y ese silencio
    puede ser un tercio o más del audio. Ese tramo tiene una firma espectral
    parecida entre cualquier grabación (ruido de fondo, no de la voz), así
    que diluye la huella y hace que personas distintas se parezcan más entre
    sí — justo la causa más probable de confundir a una persona con otra.
    Si no se detecta un tramo claro de voz, se devuelve la señal completa
    (mejor una huella algo diluida que ninguna).
    """
    n = max(1, int(sr * _RECORTE_VENTANA_S))
    num = señal.size // n
    if num < 4:
        return señal
    ventanas = señal[:num * n].reshape(num, n)
    rms = np.sqrt(np.mean(ventanas.astype(np.float64) ** 2, axis=1) + 1e-9)
    pico = rms.max()
    if pico <= 1e-6:
        return señal
    umbral = max(pico * 0.12, float(np.median(rms)) * 2.0)
    con_voz = np.where(rms >= umbral)[0]
    if con_voz.size == 0:
        return señal
    margen = max(1, int(_RECORTE_MARGEN_S / _RECORTE_VENTANA_S))
    ini = max(0, (con_voz[0] - margen) * n)
    fin = min(señal.size, (con_voz[-1] + 1 + margen) * n)
    recortada = señal[ini:fin]
    return recortada if recortada.size >= sr // 2 else señal


def huella(crudo: bytes, sr: int = 16000):
    """
    Vector de huella de voz (lista de DIM floats, norma 1) de un audio int16,
    o None si la muestra es demasiado corta / vacía.
    """
    señal = np.frombuffer(crudo, dtype=np.int16).astype(np.float32)
    if señal.size < sr // 2:  # menos de 0.5 s: poco fiable
        return None
    señal = _recortar_silencio(señal, sr)

    coef = _mfcc(señal, sr)
    if coef.shape[0] < 3:  # se necesitan varias tramas para deltas/estadísticas
        return None
    delta = np.diff(coef, axis=0)

    # Prosodia y bandas sobre las mismas tramas (sin pre-énfasis)
    tramas = _en_tramas(señal, sr)
    if tramas is None:
        return None
    signos = np.sign(tramas)
    signos[signos == 0] = 1
    zcr = (np.abs(np.diff(signos, axis=1)) > 0).mean(axis=1)

    espectro = np.abs(np.fft.rfft(tramas * np.hamming(tramas.shape[1]), _NFFT)) ** 2
    frec = np.fft.rfftfreq(_NFFT, d=1.0 / sr)
    total = espectro.sum(axis=1) + 1e-9
    centroide = (espectro * frec).sum(axis=1) / total / (sr / 2.0)  # 0..1

    limites = list(_BANDAS_HZ) + [sr / 2.0]
    ratios = np.stack(
        [espectro[:, (frec >= limites[i]) & (frec < limites[i + 1])].sum(axis=1) / total
         for i in range(len(_BANDAS_HZ))],
        axis=1,
    )

    v = np.concatenate([
        _bloque(np.concatenate([coef.mean(0), coef.std(0)]), _PESO_MFCC),
        _bloque(np.concatenate([delta.mean(0), delta.std(0)]), _PESO_DELTA),
        _bloque([zcr.mean(), zcr.std(), centroide.mean(), centroide.std()], _PESO_PROSODIA),
        _bloque(np.concatenate([ratios.mean(0), ratios.std(0)]), _PESO_BANDAS),
    ])
    n = np.linalg.norm(v)
    return (v / n).tolist() if n > 0 else None


# ── Enrolado e identificación ───────────────────────────────────
def enrolar(nombre: str, crudo: bytes, sr: int = 16000) -> dict:
    """Añade una muestra de voz de 'nombre' a su perfil (máx MAX_MUESTRAS)."""
    h = huella(crudo, sr)
    if h is None:
        return {"error": "Muestra demasiado corta o sin voz. Habla unos segundos."}
    voces = _cargar_voces()  # de paso purga huellas de dimensión antigua
    voces.setdefault(nombre, []).append(h)
    voces[nombre] = voces[nombre][-MAX_MUESTRAS:]
    _guardar(RUTA, voces)
    return {"estado": "ok", "nombre": nombre, "muestras": len(voces[nombre])}


# ── Aprendizaje SIN el botón de entrenar ────────────────────────────
# Dos vías para que LARA reconozca voces sin pasar por la pantalla de
# entrenamiento:
#   1. aprender_incremental(): cada vez que identifica a alguien CON SEGURIDAD
#      durante la charla normal, refuerza su perfil con esa muestra. Así el
#      reconocimiento mejora solo con el uso y se adapta a cambios de micrófono
#      o ambiente, sin que hagas nada.
#   2. Aprendizaje pasivo por voz: dices "Lara, aprende mi voz" y va enrolando
#      las siguientes frases que hables (iniciar_aprendizaje / alimentar_
#      aprendizaje), sin frases guiadas ni botón.

# Margen extra sobre el umbral para considerar una identificación "muy segura"
# y digna de reforzar el perfil (no reforzamos con dudas: envenenaría el perfil).
_MARGEN_REFUERZO = 0.12
_aprendizaje = {"nombre": None, "restantes": 0}


def aprender_incremental(nombre: str, crudo: bytes, sr: int = 16000,
                         score: float = None) -> dict:
    """
    Refuerza el perfil EXISTENTE de 'nombre' con una muestra nueva captada en la
    conversación. No crea perfiles nuevos (para eso está el aprendizaje pasivo).
    Si se pasa 'score', solo refuerza cuando la identificación fue muy segura.
    """
    if score is not None and score < (UMBRAL + _MARGEN_REFUERZO):
        return {"estado": "omitido", "motivo": "poca confianza"}
    voces = _cargar_voces()
    if nombre not in voces:
        return {"estado": "omitido", "motivo": "perfil inexistente"}
    return enrolar(nombre, crudo, sr)


def iniciar_aprendizaje(nombre: str, muestras: int = 6) -> dict:
    """Empieza a aprender la voz de 'nombre' de forma pasiva: las próximas
    'muestras' intervenciones habladas se enrolan automáticamente."""
    nombre = (nombre or "").strip() or "Yo"
    _aprendizaje["nombre"] = nombre
    _aprendizaje["restantes"] = max(3, int(muestras))
    return {"estado": "aprendiendo", "nombre": nombre, "faltan": _aprendizaje["restantes"]}


def aprendizaje_activo() -> bool:
    return _aprendizaje["nombre"] is not None and _aprendizaje["restantes"] > 0


def cancelar_aprendizaje() -> None:
    _aprendizaje["nombre"] = None
    _aprendizaje["restantes"] = 0


def alimentar_aprendizaje(crudo: bytes, sr: int = 16000) -> dict:
    """
    Aporta una intervención hablada a la sesión de aprendizaje pasivo en curso.
    Devuelve el estado: "aprendiendo" (faltan N), "completo" (ya terminó) o
    "inactivo"/"error". Una muestra mala (muy corta/ruido) no cuenta y se sigue
    esperando otra buena.
    """
    if not aprendizaje_activo():
        return {"estado": "inactivo"}
    nombre = _aprendizaje["nombre"]
    r = enrolar(nombre, crudo, sr)
    if "error" in r:
        return {"estado": "muestra_descartada", "motivo": r["error"], "nombre": nombre}
    _aprendizaje["restantes"] -= 1
    if _aprendizaje["restantes"] <= 0:
        _aprendizaje["nombre"] = None
        return {"estado": "completo", "nombre": nombre, "muestras": r["muestras"]}
    return {"estado": "aprendiendo", "nombre": nombre, "faltan": _aprendizaje["restantes"]}


def identificar(crudo: bytes, sr: int = 16000):
    """
    Devuelve (nombre, score) del hablante identificado, o (None, mejor_score).

    Con 2+ personas: las huellas se ESTANDARIZAN (se resta la media de los
    centros de los perfiles —cada persona pesa igual, tenga las muestras que
    tenga— y se divide por la desviación por dimensión) y se compara con coseno
    en ese espacio. Score por persona = media del TOP-3 de similitudes con sus
    muestras. Acepta solo si: mejor >= UMBRAL y (mejor - segundo) >= MARGEN.
    Con una sola persona enrolada no hay con quién estandarizar: se usa el
    coseno crudo y se exige UMBRAL_SOLO. Ante la duda devuelve None: es
    preferible "no te reconozco" a llamar "adi" a otra persona.
    """
    h = huella(crudo, sr)
    if h is None:
        return (None, 0.0)
    voces = _cargar_voces()
    if not voces:
        return (None, 0.0)

    hv = np.asarray(h, dtype=np.float64)
    nombres = list(voces.keys())
    matrices = {n: np.asarray(voces[n], dtype=np.float64) for n in nombres}

    if len(nombres) == 1:
        # Sin segunda persona no se puede estandarizar: coseno crudo + umbral duro
        m = matrices[nombres[0]]
        sims = sorted((float(np.dot(hv, fila)) for fila in m), reverse=True)
        mejor = float(np.mean(sims[:3]))
        return (nombres[0], mejor) if mejor >= UMBRAL_SOLO else (None, mejor)

    # Estandarización tipo LDA diagonal: se resta la media de los CENTROS de
    # los perfiles (quita el componente común a todo el habla, que hace que el
    # coseno crudo sature cerca de 1) y se divide por la desviación DENTRO de
    # cada persona (media de las desviaciones por perfil). Así las dimensiones
    # que separan a las personas se amplifican y las ruidosas se atenúan.
    centros = np.stack([matrices[n].mean(axis=0) for n in nombres])
    mu = centros.mean(axis=0)
    sigma = np.stack([matrices[n].std(axis=0) for n in nombres]).mean(axis=0)
    sigma = np.maximum(sigma, 0.05 * (sigma.mean() + 1e-12))  # piso anti-ruido

    def _blanquear(v):
        return (v - mu) / sigma

    def _z(v):
        z = _blanquear(v)
        n = np.linalg.norm(z)
        return z / n if n > 0 else z

    hz = _z(hv)
    hw = _blanquear(hv)
    puntajes = {}
    for nombre in nombres:
        sims = sorted((float(np.dot(hz, _z(fila))) for fila in matrices[nombre]), reverse=True)
        puntajes[nombre] = float(np.mean(sims[:3]))

    orden = sorted(puntajes.items(), key=lambda par: -par[1])
    mejor_nombre, mejor = orden[0]
    segundo = orden[1][1]
    if mejor < UMBRAL or (mejor - segundo) < MARGEN:
        return (None, mejor)

    # Freno anti-impostor: el coseno estandarizado solo mide DIRECCIÓN, así que
    # una 4ª voz desconocida puede "apuntar" hacia alguien estando lejísimos.
    # Se exige además que la DISTANCIA del audio al perfil ganador sea del orden
    # de la dispersión interna de ese perfil (<= FACTOR_DIST veces).
    mw = np.stack([_blanquear(f) for f in matrices[mejor_nombre]])
    d_query = float(np.sort(np.linalg.norm(mw - hw, axis=1))[:3].mean())
    if len(mw) >= 2:
        pares = [np.linalg.norm(mw[i] - mw[j]) for i in range(len(mw)) for j in range(i + 1, len(mw))]
        d_perfil = float(np.median(pares))
    else:
        d_perfil = 0.0
    if d_perfil <= 1e-9:
        # sin dispersión medible (1 sola muestra): usa la del resto como referencia
        otras = []
        for n2 in nombres:
            if n2 == mejor_nombre or len(matrices[n2]) < 2:
                continue
            ow = np.stack([_blanquear(f) for f in matrices[n2]])
            otras += [np.linalg.norm(ow[i] - ow[j]) for i in range(len(ow)) for j in range(i + 1, len(ow))]
        d_perfil = float(np.median(otras)) if otras else d_query  # sin referencia: no frenar
    if d_query > FACTOR_DIST * d_perfil:
        return (None, mejor)
    return (mejor_nombre, mejor)


def eliminar_persona(nombre: str) -> bool:
    """Borra el perfil de voz completo de 'nombre'. True si existía."""
    voces = _cargar_voces()
    if nombre not in voces:
        return False
    del voces[nombre]
    _guardar(RUTA, voces)
    return True


def frases_entrenamiento() -> list:
    """
    Frases en español fonéticamente variadas para el entrenamiento guiado:
    vocales abiertas y cerradas, nasales, fricativas y entonación de pregunta.

    Son 7 (antes 5): con solo 5 muestras el centro y la dispersión de cada
    perfil quedaban poco fiables y eso favorecía confundir a una persona con
    otra; más muestras (y más variadas) dan una huella más estable.
    """
    return [
        "Hola Lara, soy yo; aprende a reconocer mi voz ahora.",
        "El murciélago añejo comía feliz cardillo y kiwi junto al río.",
        "Mis niños sueñan con montañas nevadas y mañanas soleadas.",
        "Susana cose seis camisas grises en su casa de la esquina.",
        "¿Puedes encender la luz azul del laboratorio, por favor?",
        "Treinta y tres tazas trituradas ruedan por el taller de trabajo.",
        "Que ganga, un juguete jamás jugado guardado bajo la vieja escalera.",
    ]


# ── configuración del candado de voz ────────────────────────
def get_config() -> dict:
    return _cargar(CONFIG, {"candado": False, "dueno": "Adi"})


def set_candado(activo: bool, dueno: str = None) -> None:
    cfg = get_config()
    cfg["candado"] = bool(activo)
    if dueno:
        cfg["dueno"] = dueno
    _guardar(CONFIG, cfg)


def autorizado(crudo: bytes, sr: int = 16000) -> bool:
    """
    Con el candado activado: True solo si la voz coincide con el dueño.
    Con el candado desactivado (o sin perfiles válidos): siempre True.
    """
    cfg = get_config()
    if not cfg.get("candado"):
        return True
    voces = _cargar_voces()
    if not voces:
        return True  # nada enrolado todavía -> no bloquear
    nombre, _ = identificar(crudo, sr)
    return nombre == cfg.get("dueno", "Adi")


def personas() -> list:
    """Nombres con perfil de voz válido (se ignoran perfiles vacíos/antiguos)."""
    return list(_cargar_voces().keys())
