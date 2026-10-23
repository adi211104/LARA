# LARA: traslado, modelo pequeño y FreeLLMAPI

## Estado comprobado — 23/10/2026

En el equipo actual: Ryzen 3 3200G, unos 8 GB de RAM y alrededor de 1 GB libre.
Se probó Ollama oficial 0.40.10 en una carpeta aislada .tools/ollama-prueba,
puerto loopback 11435, sin instalación global ni cambios persistentes de entorno.
Descarga verificada contra el SHA256 oficial y ejecutable con firma válida.

Ollama detectó Radeon RX 570, 4 GiB de VRAM, mediante Vulkan. Qwen3 0.6B
descargó todas sus capas a GPU; /api/ps informó también carga completa en VRAM
para 1.7B. Esto verifica inferencia en esta RX 570, no entrenamiento ni la RX 580.

| Modelo Q4_K_M | Primera respuesta, carga incluida | Dos respuestas posteriores | Tokens/s de generación | Contrato estricto |
|---|---:|---:|---:|---:|
| qwen3:0.6b | 8,279 s | 1,191 / 1,089 s | 20,98–36,37 | 1/3 |
| qwen3:1.7b | 5,366 s | 0,917 / 0,793 s | 29,16–31,15 | 2/3 |

Tres casos sintéticos: suma, referencia a un nombre de archivo y corrección de
una fecha. Los datos centrales fueron correctos; hubo claves renombradas o extras.
El caso de fecha decía «fecha actual», algo ambiguo para exigir literalmente
«fecha»: la puntuación estricta no equivale a medir comprensión general. Prueba
muy pequeña, sin herramientas ni voz; no demuestra capacidad de programación.
Los tokens/s excluyen carga y procesamiento del prompt, no son latencia completa.
Contexto 1024, dos hilos, thinking apagado, máximo 120 tokens. Los modelos fueron
descargados de memoria al terminar. Evidencia ignorada por Git:
.tools/ollama-prueba/resultados.json y resultados_1_7b.json.

El cliente real core.llm_local.chat también respondió {"resultado":56} a 8 por 7,
en 4,821 s con carga en frío, una respuesta y cero fallos. No se ejecutaron acciones.
Se añadió think=false por defecto para Qwen3 en chat, streaming y precalentamiento;
LARA_OLLAMA_THINK=1 permite cambiarlo. Los modelos anteriores no reciben esa opción.
79 pruebas de regresión correctas. No se alteró .env ni se promovió el pequeño
como modelo de acciones: sus fallos de contrato no justifican esa promoción.

## ¿Conviene la máquina de 24 GB y RX 580 de 4 GB?

Sí, por el margen de RAM para Windows, LARA, editor y contexto del modelo.
La RX 580 sigue teniendo solo 4 GB de VRAM: la RAM del sistema no se suma como
VRAM de igual velocidad. La velocidad total depende también del procesador,
controladores y SSD. No se ha comprobado Vulkan en esa máquina: repetir la prueba.
No hay garantía de una gran mejora de tokens/s frente a la RX 570 ya acelerada.

Comenzar con Qwen3 1.7B y contexto 2048. Probar 4B solo después de medir estabilidad
y memoria, no descargar familias grandes indiscriminadamente. No se habilita LoRA
en esa RX 580 por asumir que el soporte de inferencia implica soporte de entrenamiento.

## Traslado reproducible

1. Instalar Git, Python compatible con el proyecto (3.12 o superior), Node.js
   >=20.18 y <25, npm >=10, y Ollama para Windows desde sus fuentes oficiales.
2. Clonar el repositorio privado https://github.com/adi211104/LARA con la cuenta
   autorizada. Abrir esa carpeta como proyecto en el editor.
3. Ejecutar INSTALAR_LARA.bat. Crea .venv, instala requirements.lock, genera .env
   si falta y ejecuta la suite. No dar por terminada una instalación con errores.
4. Completar .env localmente. GitHub no contiene las claves actuales.
5. Con Ollama abierto, ejecutar:

```
ollama pull qwen3:1.7b
python tools/probar_ollama.py --modelo qwen3:1.7b
```

6. Revisar el informe .tools/ollama-prueba/resultados.json y ollama ps mientras
   procesa. El script descarga de memoria el modelo al terminar. En .env, perfil
   inicial opcional para la nueva máquina (no son credenciales):

```
LARA_LLM_LOCAL=1
LARA_OLLAMA_MODEL=qwen3:1.7b
LARA_OLLAMA_URL=http://127.0.0.1:11434
LARA_OLLAMA_NUM_CTX=2048
LARA_OLLAMA_THREADS=3
LARA_OLLAMA_KEEP_ALIVE=60s
LARA_OLLAMA_THINK=0
```

Mantener un solo modelo y una petición paralela al principio. Estas variables
de servidor Ollama se configuran antes de iniciarlo: OLLAMA_MAX_LOADED_MODELS=1,
OLLAMA_NUM_PARALLEL=1. No confundirlas con la configuración del cliente LARA.
No precargar embeddings y voz pesada a la vez sin medir memoria.

7. Instalar e iniciar el gateway como se describe abajo. Comprobar sus motores y
   aprobar el perfil de acciones solo tras evaluarlo.
8. Ejecutar python tools/entrenar_intencion.py para regenerar el pequeño detector
   de seguimientos. Sus pesos locales .tools/intencion_codigo.json no están en Git.
9. Abrir INICIAR_LARA.bat. Voz: los pesos grandes de modelos_voz no se versionan;
   consultar modelos_voz/LEEME.txt para instalarlos aparte si se desea esa voz.

Para conservar memoria y sesiones, cerrar LARA y copiar por un medio privado su
directorio de datos Taller_IA. Definir LARA_DATA_DIR en la nueva máquina antes del
primer arranque. No subir ese directorio al repositorio, ni .env, ni .tools. Las
rutas de trabajo antiguas pueden no existir: elegir la carpeta nueva en cada
sesión y comprobarla antes de ejecutar. No reutilizar ciegamente rutas del otro PC.
No copiar .venv: recrearla. GitHub conserva código y documentación, no conversaciones.

## Claves de FreeLLMAPI: qué falta realmente

FreeLLMAPI agrega proveedores: no regala las credenciales de esos proveedores.
En este equipo se encontraron una clave Google y una Groq habilitadas, con estado
guardado healthy. /v1/models devolvió 280 entradas. Eso no significa 280 modelos
ejecutables con tus cuotas, ni asegura que una clave conserve cuota ahora mismo.
También existe SERPER_API_KEY para búsquedas, distinta de los motores de lenguaje.

Dos tipos de clave distintos:

- Clave del proveedor: se obtiene en tu propia cuenta y se introduce en el panel
  de FreeLLMAPI. Groq: https://console.groq.com/keys ; Google:
  https://aistudio.google.com/api-keys . Este lanzador importa las ya configuradas
  en GROQ_API_KEY y GEMINI_API_KEY de .env al iniciar.
- Clave unificada local: la genera FreeLLMAPI para que LARA lo use. El lanzador la
  guarda en .tools/gateway-lara.json. No poner ahí una clave de Groq/Google.

Instalación nueva (consumen red y disco; el servicio se clona en .tools):

```
python tools/gateway_lara.py instalar
python tools/gateway_lara.py iniciar
python tools/mejora_lara.py gateway
```

Para iniciar, tener al menos una clave válida Groq/Google en .env y quitar
LARA_GATEWAY_ENABLED=0 o cambiarlo a 1. Si se deja a cero, el servicio no arranca.
Dejar LARA_GATEWAY_API_KEY vacío en .env cuando el lanzador gestione la clave;
las variables explícitas de entorno prevalecen sobre .tools/gateway-lara.json.
Se corrigió el adaptador para que los campos vacíos de clave/perfil en la
plantilla .env no oculten una clave generada o un perfil local ya aprobado.
El panel está en http://127.0.0.1:3001 . Sus credenciales administrativas generadas
se guardan en .tools/gateway-admin.json: consultar solo localmente, no compartir.

Para añadir proveedores adicionales, crear sus claves en sus portales e ingresarlas
en Keys del panel. No pegarlas en el chat ni habilitar facturación automáticamente.
El lanzador no importa por sí mismo variables nuevas de todos los proveedores.
No comprar la suscripción del catálogo para solucionar cuotas: son cosas distintas.

## Sacarle provecho sin saturar ni pagar inadvertidamente

- Lo determinista (hora, comandos inequívocos) no necesita modelo. El local atiende
  tareas sencillas; el código, análisis técnico, textos largos y niveles de esfuerzo
  altos pasan a nube. Esto ya está contemplado en core/llm_engine.py.
- Para razonamiento se exige un modelo explícito; auto:smart queda para conversación.
  El catálogo automático puede elegir motores especializados poco adecuados.
  Ejemplo: LARA_GATEWAY_REASONING_MODEL=openai/gpt-oss-120b, sujeto a disponibilidad.
- Añadir dos o tres proveedores independientes con uso gratuito comprobado puede
  dar más disponibilidad; varias claves de una misma cuenta no multiplican su cuota.
- En tareas de acciones, evaluar primero y usar modelos exactos aprobados. En la
  nueva instalación el perfil comienza sin aprobar: seguir MEJORA_CONTINUA.md y
  tools/gateway_lara.py aprobar-acciones --informe <informe válido>. No copiar solo
  una lista de modelos para saltarse la evaluación.
- Mantener planes locales, limitar historial/contexto, resumir resultados largos y
  usar pruebas de código locales. Esas medidas ahorran tokens y errores acumulados.
- Revisar límites y errores en el panel; 429 significa cuota/ritmo, no falta de RAM.
  Esperar la recuperación o cambiar a otro proveedor disponible, no reintentar
  indefinidamente. Los niveles gratuitos y sus condiciones pueden cambiar.
- No exponer los puertos del gateway u Ollama a Internet. El diseño actual de LARA
  exige gateway loopback: esta guía instala todo en el otro PC, no un servidor LAN.

Referencias: https://github.com/tashfeenahmed/freellmapi ,
https://docs.ollama.com/gpu , https://ollama.com/library/qwen3:1.7b .

## Publicación

Repositorio privado autorizado: adi211104/LARA. Se conservan las exclusiones de
.env, .tools, datos de ejecución y pesos grandes. tools/revisar_publicacion.py
revisa archivos publicables e historial con patrones de secretos sin imprimirlos.
La revisión no encontró coincidencias antes de publicar; es heurística, no una
garantía de ausencia total de información privada. Verificar después de añadir
archivos nuevos. Las claves deben restaurarse por un medio privado, no por Git.
