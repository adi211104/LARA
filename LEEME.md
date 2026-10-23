# LARA — estado real y guía de continuidad

Traslado al equipo de 24 GB, prueba real de Ollama y configuración de claves:
[TRASLADO_Y_MOTORES.md](TRASLADO_Y_MOTORES.md).

Objetivo medible, pruebas reales y entrenamiento local pequeño:
[OBJETIVO_LARA.md](OBJETIVO_LARA.md). El detector entrenado reconoce seguimientos;
no se ha entrenado un modelo generativo completo ni se garantizan cuotas cloud.

## Integración del 23 de octubre de 2026

FreeLLMAPI opcional, registro revisable, evaluación de candidatos y entrenador
LoRA separado: instrucciones y límites en [MEJORA_CONTINUA.md](MEJORA_CONTINUA.md).
En el chat: `aprendizaje`, `respuesta correcta`, `respuesta incorrecta`,
`corrige respuesta: texto correcto` y `estado del gateway`.
Los datos registrados no se convierten automáticamente en conocimiento verdadero.

## Pruebas reales por terminal — 15 de octubre de 2026

Resultados y limitaciones en [PRUEBAS_TERMINAL.md](PRUEBAS_TERMINAL.md).
Se corrigieron esperas de memoria, historial de respuestas rápidas y el desvío
a herramientas cuando se solicita explícitamente no ejecutarlas. No equivalen
a una certificación de autonomía ni de razonamiento infalible.

## Mejoras del 14 de octubre de 2026

Escribe `ayuda` para descubrir funciones o usa los accesos del chat. Ahora hay
comandos deterministas que no gastan tokens:

| Pedido | Resultado |
|---|---|
| `nueva tarea revisar la placa` | Guarda una tarea tras autorización. |
| `mis tareas` / `completar tarea 1` | Consulta o completa tareas por ID. |
| `temporizador 30 segundos` | Programa una notificación persistente. |
| `pomodoro` | Programa el final de una sesión de 25 minutos; no encadena ciclos automáticamente. |
| `mis recordatorios` / `cancelar recordatorio 2` | Consulta fecha e ID o cancela un aviso. |
| `mis motores` | Explica recursos y configuración sin mostrar claves. |

Los avisos viven en `conocimiento/recordatorios.sqlite3`. Se recuperan después
de reiniciar LARA; el equipo y LARA deben estar activos para avisar a tiempo.
Si estuvo cerrada, entrega los vencidos al abrir. Una interrupción justo durante
la notificación puede repetir un aviso (entrega al menos una vez).

Equipo inspeccionado: Ryzen 3 3200G, 4 hilos, 7,9 GiB de RAM y RX 570 con unos
4 GB de VRAM. El perfil automático usa Whisper `base`, contexto local 2048,
3 hilos de inferencia y libera el modelo tras 1 minuto inactivo. Son valores
conservadores, no un benchmark de velocidad; `.env` permite sobrescribirlos.
Whisper base puede perder precisión frente a small en ambientes ruidosos.

Ollama no respondió en localhost durante esta revisión y no se descargaron
pesos ni instalaron drivers. La RX 570 no figura en la lista oficial ROCm de
Windows: la aceleración mediante Vulkan necesita validación real en esta PC.
Referencia: [hardware de Ollama](https://docs.ollama.com/gpu).

La nube continúa como opción para consultas pesadas. `LARA_CLOUD_MODELS` permite
definir una cadena de modelos separados por comas sin editar Python. Las cuotas
gratuitas no son ilimitadas: [límites de Groq](https://console.groq.com/docs/rate-limits).
La investigación automática de fondo requiere `LARA_CLOUD_BACKGROUND=1`; por
defecto está apagada. El router intenta como máximo tres modelos, con timeout
de 20 segundos por petición y sin reintentos internos del SDK. No es un plazo
global del turno: pueden sumarse otros proveedores y herramientas.

El arranque ya tolera claves ausentes, pero esto no convierte todas las
funciones en offline: conversación libre requiere Ollama o nube, y visión
sigue usando Gemini. Los comandos deterministas anteriores sí son locales.

LARA es un asistente de escritorio para Windows, con interfaz gráfica y de consola, voz, memoria, automatización y gestión personal. El proyecto está en una fase de prototipo avanzado: tiene muchas capacidades integradas, pero aún no ofrece la fiabilidad, el aislamiento ni la instalación reproducible que se esperan de un asistente de uso diario.

La evaluación técnica y el plan para convertirla en un producto local y mantenible están en [AUDITORIA.md](AUDITORIA.md). Ese documento es la fuente de verdad para prioridades; este archivo explica cómo ejecutar el estado actual.

## Qué funciona hoy

- Interfaz gráfica (`gui.py`) y consola (`main.py`).
- Conversación, acciones de Windows, tareas, memoria y búsqueda web mediante un router híbrido.
- Voz: STT local con `faster-whisper` cuando está disponible; TTS con edge-tts y respaldos locales configurables.
- Un modelo local Ollama para decisiones breves y embeddings, si se instala.
- Diagnóstico básico con el comando `diagnóstico` y comprobaciones de router en `probar_cerebro.py`.

## Límites importantes

- **No es completamente local ni completamente gratuito.** La conversación profunda, visión, búsqueda y varias rutas de respaldo usan Groq y Gemini, por lo que requieren claves, conexión y están sujetos a cuotas o cambios de proveedor.
- El modelo local actual (`qwen2.5:3b-instruct`) se usa sobre todo como router; no sustituye de forma fiable el razonamiento, visión o agente de la nube.
- La automatización de Windows puede ejecutar comandos, mover archivos, controlar teclado/ratón y lanzar programas. Úsala en modo `manual` o `lectura` mientras el proyecto se estabiliza.
- Durante la Fase 1, los comandos arbitrarios, ejecución de código, skills dinámicas, dictado automático y control de teclado/ratón están **pausados por defecto**. No deben reactivarse para uso diario; `LARA_ENABLE_EXPERIMENTAL_TOOLS=1` existe exclusivamente para pruebas aisladas.
- La instalación está fijada en `requirements.lock` (versiones exactas verificadas en Python 3.14.6), pero aún sin hashes ni prueba automatizada de instalación limpia.

## Requisitos actuales

- Windows 10/11 y Python 3.12 o superior (verificado con 3.14.6).
- Opcional para las funciones locales: [Ollama](https://ollama.com/) con:

  ```powershell
  ollama pull qwen2.5:3b-instruct
  ollama pull embeddinggemma
  ```

- Para la voz local: los paquetes de `requirements.txt`; Kokoro necesita adicionalmente sus pesos, descritos en `modelos_voz/kokoro/LEEME.txt`.
- Para las funciones que continúan en nube: un archivo `.env` local con las claves correspondientes. Nunca subas ese archivo al repositorio.

## Inicio y verificación

Instalación (una vez, y cada vez que cambie `requirements.lock`):

```powershell
.\INSTALAR_LARA.bat
```

Crea el entorno `.venv`, instala las versiones exactas de `requirements.lock`, crea `.env` desde `.env.example` si no existe y ejecuta las pruebas. Después, abre LARA con `INICIAR_LARA.bat` (ya no instala nada al arrancar).

Equivalente manual:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe gui.py
```

`python probar_cerebro.py` sigue disponible como prueba de humo del router y de Ollama.

### Dónde se guardan los datos

Por defecto, en `OneDrive\Taller_IA` si OneDrive está vinculado (se sincroniza fuera del equipo) y, si no, en `%USERPROFILE%\Taller_IA`. Para fijarlo, define en `.env` `LARA_DATA_DIR=<ruta>` o `LARA_DATA_LOCAL=1`. Si cambias la ruta, mueve antes la carpeta `Taller_IA` existente o LARA empezará sin memoria.

Los registros están en `<datos>\logs\`: `errores.log`, `auditoria.log` (acciones de riesgo) y `eventos.jsonl` (un evento por línea con el ID de turno).

## Modos de seguridad

LARA persiste un modo de operación en su directorio de datos:

| Modo | Comportamiento esperado |
|---|---|
| `manual` | Pide confirmación para acciones clasificadas como riesgosas. Es el valor por defecto. |
| `plan` | Explica los pasos sin ejecutarlos. |
| `lectura` | Debe limitarse a consulta y análisis. |
| `auto` | Ejecuta directamente las acciones que el sistema permita. No recomendado hasta cerrar los hallazgos de seguridad. |

Los modos ayudan, pero no sustituyen una política de permisos completa. Consulta los hallazgos A-01 y A-02 de la auditoría antes de usar comandos o automatización en un equipo con datos importantes.

## Estructura

- `main.py` y `gui.py`: puntos de entrada.
- `core/cerebro.py`: orquestación, enrutado y despacho de acciones.
- `core/llm_engine.py` y `core/llm_local.py`: proveedores de IA nube/local.
- `core/skills_*.py`: acciones y adaptadores de sistema, web, archivos, voz y gestión.
- `config.py`: rutas de datos y configuración base.
- `AUDITORIA.md`: decisiones, riesgos, hoja de ruta y criterios de aceptación.
- `ESTRUCTURA.md`: inventario detallado de módulos.

## Principio para los siguientes cambios

La afinación y prueba de fuerza del 23/10/2026 están documentadas en
[OBJETIVO_LARA.md](OBJETIVO_LARA.md): 10/16 antes, 13/16 después y límites
pendientes. Son resultados de una batería pequeña, no equivalencia con modelos GPT.

No se deben añadir más funciones hasta completar la Fase 0 y cerrar los pendientes de la Fase 1 de la auditoría. Cada funcionalidad nueva debe incluir: una prueba automatizada, una política explícita de permisos, un comportamiento útil sin nube y documentación de sus datos, límites y dependencias.
