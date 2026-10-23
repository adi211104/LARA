# Pruebas y afinación por terminal — 2026-10-15

## Método reproducible

`python tools/benchmark_lara.py --salida benchmark.json --timeout 65`

Cinco solicitudes sintéticas, cada una en un proceso nuevo y directorio temporal.
No se inicia micrófono ni agente de fondo. Solo se autoriza gestión de tareas de
prueba. Se consultaron los proveedores cloud ya configurados. No se publican claves.
Los segundos miden el turno completo, sin el arranque (registrado por separado).
Son muestras individuales, no promedios ni percentiles; red y modelos varían.

## Resultados

| Caso | Antes, segundos | Última medición, segundos | Evaluación |
|---|---:|---:|---|
| Ayuda | 3.333 | 0.061 | Correcta |
| Crear tarea | 3.209 | 0.065 | Correcta; registro persistido comprobado |
| Resistencia LED | 4.731 | 3.145 | 500 ohm y 0.20 W correctos; consejos adicionales requieren revisión |
| Tres cajas mal etiquetadas | 5.530 | 3.393 | Parcial: asignación final correcta, justificación contradictoria |
| Función mediana | 4.980 | 2.935 | Correcta por inspección; no se ejecutó el código generado |

El arranque de la ronda final fue de 3.8–4.3 segundos aproximadamente. Los tres
casos de IA se resolvieron mediante Groq, modelo `openai/gpt-oss-120b`, no por
inferencia local. No se ha demostrado conversación avanzada totalmente offline.

Evidencia completa: `benchmark_antes.json`, `benchmark_antes_complejo.json`,
`benchmark_despues.json`, `benchmark_final.json`, `benchmark_razonamiento_alto.json`.
Las dos últimas contienen respectivamente comandos locales finales y pruebas
finales de IA con esfuerzo alto. Se conservan también resultados fallidos.

## Cambios implementados

- El guardado de sesiones persiste memoria sin calcular embeddings en la ruta
  de respuesta. Conserva recuperación léxica; el enriquecimiento vectorial puede
  hacerse con `reindexar_embeddings`. Los nuevos turnos no tienen búsqueda
  semántica completa hasta reindexarlos. No se borró memoria existente.
- Las respuestas rápidas se incorporan al historial, no solo los mensajes del usuario.
- Se evita duplicar el último mensaje del usuario al construir la petición del router.
- Las solicitudes explícitas «sin herramientas», «sin ejecutar», «no lo ejecutes»,
  «no ejecutes herramientas» o «solo texto» usan una ruta de generación textual
  que no despacha rutinas, skills ni explicaciones visuales. No es un detector
  universal de todas las formas posibles de expresar esa intención.
- El razonamiento profundo no streaming solicita esfuerzo alto para GPT-OSS y
  al menos 4096 tokens; rechaza salidas truncadas antes de probar el respaldo.
  Puede consumir más cuota. No cambia todos los demás motores o rutas streaming.
- Se preserva código reconocido al limpiar formato; no se eliminan sus operadores.
- Banco de pruebas con límite por proceso y salida UTF-8 en Windows.

## Hallazgos que NO se dan por resueltos

La primera solución de las cajas daba la asignación correcta, pero afirmaba
erróneamente que la mezcla ya estaba identificada. Una ronda posterior incluso
asignó contenidos compatibles con etiquetas que debían ser falsas. Con esfuerzo
alto regresó a la asignación correcta, pero mantuvo una justificación inválida.
Añadir instrucciones de verificación NO demostró resolver este problema.

En una ronda intermedia, el router desvió «sin herramientas» hacia una explicación
visual y devolvió solo una introducción tras 17.045 segundos. Esto motivó la ruta
textual determinista y su prueba de regresión.

El cálculo final añadió un consejo de tocar una resistencia caliente: no debe
tomarse como recomendación segura. Para uso técnico real hacen falta validación
de recomendaciones y comprobaciones independientes, no solo respuestas fluidas.

Las pruebas de mediana incluyen tres asserts correctos, pero no ejercitan la
excepción de lista vacía ni comprueban explícitamente que la entrada siga intacta.

La suite automatizada alcanzó 43 pruebas correctas. No es una medida de precisión
global del modelo. La batería real es pequeña: no permite certificar tareas fuertes,
autonomía tipo Jarvis, voz, navegación ni ejecución de programas de extremo a extremo.

Siguiente prioridad: ampliar casos con verificadores objetivos, varios turnos,
fallos de red, permisos denegados y pruebas repetidas; medir voz y búsqueda por separado.

## Voz e internet: pruebas adicionales solicitadas

El usuario eligió únicamente audio de muestra: no se abrió el micrófono ni se
ejecutaron los comandos transcritos. No se modificaron ajustes de audio del usuario.

Reproducir con:

```
python tools/benchmark_voz_web.py voz --salida benchmark_voz_nuevo.json
python tools/benchmark_voz_web.py web --salida benchmark_web_nuevo.json
python tools/benchmark_voz_web.py web_chat --salida benchmark_web_chat_nuevo.json
```

### Reconocimiento

Audio sintético español de Kokoro, convertido por FFmpeg a PCM mono de 16 kHz.
Una cuarta muestra añade ruido gaussiano reproducible, SNR global aproximada 10 dB.
Los tiempos siguientes miden transcripción, no síntesis, captura ni respuesta del asistente.

| Entrada | Transcripción | Segundos | Evaluación |
|---|---|---:|---|
| Lara abre el bloc de notas | Lara abre el bloc de notas. | 4.402 | Correcta, primera carga incluida |
| Recuérdame revisar el amplificador en quince minutos | Recuérdame revisar el amplificador en 15 minutos. | 0.517 | Correcta semánticamente |
| Busca en internet cómo funciona un transistor | Busca en Internet cómo funciona un transistor. | 0.486 | Correcta |
| Primera frase con ruido | Clara abre el bloc de notas. | 0.816 | Error en nombre de activación |
| Silencio de 3 segundos | Rechazado como no voz | 0.002 | Sin falso positivo en esta muestra |

El WER del reporte ignora mayúsculas, acentos y puntuación, pero no normaliza
números: cuenta `quince` frente a `15` como error aunque no cambie la intención.
No extrapolar tres frases limpias a una precisión general del 100 %.

Whisper local no encontró el modelo configurado en caché. La prueba deshabilitó
descargas nuevas deliberadamente; esto no significa que el modelo no pueda
instalarse. Las transcripciones exitosas usaron la cadena cloud de respaldo.
El reporte no distingue cuál de los dos respaldos respondió; no atribuirlas
con certeza a Groq o Google. No hay prueba de STT completamente offline.

Hallazgo adicional de salida de voz: Edge TTS 7.2.8 devolvió HTTP 403 al abrir
el WebSocket de síntesis, aunque el catálogo sí listó es-PE-CamilaNeural.
Esto es un fallo de síntesis, no de reconocimiento. Kokoro local sí generó WAV:
10.320 s la primera muestra y 4.045/3.811 s las siguientes, incluyendo intentos
fallidos previos de Edge. No son tiempos puros de Kokoro. No se reprodujo audio.

Evidencia: `benchmark_voz.json` (Edge falló), `benchmark_voz_kokoro.json` (respaldo).
Pendiente: voz humana/acento, micrófono, distancia, ruido de taller real,
activación «Lara», límites de frase y conversación bidireccional sin eco.

### Búsqueda

La función real `buscar_en_web` respondió vía Serper y resumen en 2.637 s para
documentación Python y 2.612 s para datos NASA. Devolvió respectivamente un enlace
intermediado por Google Translate a docs.python.org y cuatro enlaces NASA.
Se verificó por separado la existencia y pertinencia de las páginas oficiales:
[Python: estructuras de datos](https://docs.python.org/3/tutorial/datastructures.html)
y [NASA: Moon facts](https://science.nasa.gov/moon/facts/).

La prueba desde `LaraCerebro.procesar` tardó 6.914 s y sí mostró el estado de
búsqueda. Sin embargo, no incluyó enlaces en la respuesta pese a pedirlos,
y mezcló referencias a Python 3.15.0 y 3.6.15. Resultado: búsqueda conectada,
pero respuesta final incompleta en trazabilidad y selección de versiones.

La implementación resume snippets de resultados: no es una lectura completa
de todos los documentos enlazados ni una verificación independiente de cada dato.
No se certificó actualidad de noticias, búsqueda sin conexión, acceso a páginas
autenticadas ni funcionamiento sin cuota/clave Serper. No se cambió proveedor.

Evidencia: `benchmark_web.json`, `benchmark_web_chat.json`.
Prioridad siguiente: mostrar las fuentes reales en la respuesta final,
identificar motor STT en cada transcripción y preparar el modelo local antes
de declarar modo offline disponible.
