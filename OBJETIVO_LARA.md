# Objetivo operativo de LARA y ensayo del 23 de octubre de 2026

Objetivo: completar tareas pequeñas de programación con contexto persistente,
verificación independiente, apertura del artefacto correcto y recuperación de
proveedores limitada en tiempo, manteniendo la interfaz disponible. No se define
como «cero demora»: depende de red, cuotas y hardware, y no se ha alcanzado
fiabilidad general ni disponibilidad cloud garantizada.

## Criterios de trabajo

| Criterio | Meta | Evidencia actual |
|---|---|---|
| Seguimientos locales de apertura | Menos de 0,2 s sin consultar IA | 0,023–0,036 s, lanzamiento simulado |
| Creación/cambio pequeño | Intentar menos de 20 s por tarea cuando un proveedor responda | Muestras entre 5 y 8 s tras ajustes; no garantiza p95 |
| Conservación de contexto | No confundir el artefacto con LARA; mismo ID y carpeta al reabrir | Pruebas de regresión y ensayo real |
| Corrección del programa | Casos normales, límites y errores pasan un verificador independiente | Últimas dos rondas: función, seguimiento y contexto pasan |
| Fallos de proveedor | Esperas limitadas y avance recuperable | Cooldown y fallback probados; ambos proveedores agotaron cuota |
| Calidad de entrenamiento | Datos correctos separados de evaluación | Detector local entrenado y pruebas separadas; no se entrenó un LLM |

Los tiempos de apertura solo miden selección y petición de lanzamiento: no tiempo
de una ventana real lista para interactuar. No se midió p95 ni carga sostenida.

## Qué probó el agente

Se añadió `tools/ensayo_lara.py`. Cada ensayo usa un directorio temporal,
configuración de sesiones aislada y modelos reales. El agente genera el programa,
lo ejecuta con stdin, recibe una solicitud de cambio y reabre la misma sesión.
La apertura final se intercepta para comprobar el destino sin abrir ventanas.
Solo se admiten comandos `python archivo.py` locales en ese ensayo. Esto limita
las herramientas del ensayo; no equivale a un sandbox del sistema operativo.

Un verificador independiente comprueba `dibujar(5,3)`, `(1,4)`, `(4,1)` y ValueError
ante cero, negativos y medidas no enteras. No utiliza los asserts que inventa
el modelo como criterio de corrección. `--corregir` devuelve sus fallos al agente
y permite hasta dos reparaciones iniciales y una después del seguimiento.

```
python tools/ensayo_lara.py --salida .tools/ensayos/mi_prueba.json --corregir
```

| Ronda | Crear | Cambiar borde | Apertura | Resultado |
|---|---:|---:|---:|---|
| Antes del ajuste de cuotas | 6,027 s | 44,723 s | 0,023 s | Pasaron comprobaciones |
| Primer ajuste | 10,226 s | 5,603 s | 0,031 s | Falló ancho 1; no se aprueba |
| Ronda posterior | 7,693 s | 8,136 s | 0,030 s | Pasaron comprobaciones; finalizó el ciclo |
| Plan inicial local | 5,141 s | 5,375 s | 0,036 s | Programa correcto; faltó cierre del agente por cuota |

Son ejecuciones distintas y modelos/proveedores variables: no representan una
comparación estadística controlada ni una aceleración garantizada. Se conserva
el resultado fallido. Los tiempos excluyen importación inicial y arranque de GUI.
La última ronda tardó 5,289/6,154 s si se incluyen verificaciones externas.

Se confirmó además mediante una petición aislada que Google devolvía 429
RESOURCE_EXHAUSTED. El servicio local FreeLLMAPI respondió al diagnóstico, pero
tener servidor y catálogo disponibles no significa que queden cuotas upstream.

Evidencia en `.tools/ensayos/antes.json`, `despues.json`, `final.json`,
`plan_local.json` y sus carpetas de archivos. No se alteró el programa de tu
escritorio con estos ensayos.

## Ajustes implementados

- Plan inicial local de tres pasos: evita una petición extra de IA. Puede pedirse
  planificación generada con `LARA_CODE_PLAN_LLM=1`; modo planificación conserva IA.
- Peticiones de Open Code al gateway con JSON explícito, hasta 1800 tokens y
  timeout específico. Menos reserva de tokens por paso; archivos grandes pueden
  necesitar división y una salida truncada se rechaza.
- Un 429 del gateway pausa esa ruta durante un minuto y pasa a Google en lugar
  de repetir inmediatamente el mismo proveedor. Google también se pausa si agota
  su cuota. No se crean cuentas ni se compran cuotas.
- Presupuesto de decisiones configurable (`LARA_CODE_DECISION_TIMEOUT=30`),
  llamadas del gateway/Google de hasta 10 s, directas hasta 8 s y esperas explícitas
  de cuota de hasta 3 s. Son límites de cliente: no un plazo duro de sistema
  operativo ni una cancelación instantánea de una llamada HTTP en curso.
- Cuando falla el siguiente paso, se muestra el avance y el resultado real de la
  última prueba. No se declara el objetivo completo terminado. La última prueba
  solo corresponde a los cambios posteriores registrados en la misma ejecución.

## Entrenamiento realizado y alcance

Se entrenó una regresión logística pequeña de intención con 62 ejemplos sintéticos
revisados por construcción, usando únicamente biblioteca estándar y CPU. Detecta
referencias a abrir el programa creado en la sesión, sin generar código. Se evalúa
con 12 frases reservadas que no se incluyeron en el entrenamiento: 12/12 en esta
suite pequeña. Las frases son cercanas al dominio de entrenamiento: no implica
comprensión general del lenguaje ni precisión del 100 % en uso real.

El detector exige alta confianza (0,9) y referencias explícitas. Ante negaciones,
rutas o instrucciones incompatibles se abstiene. Solo funciona si hay artefactos
en la sesión; la apertura determinista mantiene las comprobaciones de destino y
los permisos. Se conserva el reconocimiento exacto anterior si falta el modelo.

```
python tools/entrenar_intencion.py
```

Se activa solo si supera la suite incluida. Evidencia y ejemplos:
`.tools/entrenamiento_intencion/{train.jsonl,test.jsonl,resultado.json}`.
Pesos activos: `.tools/intencion_codigo.json`. No es entrenamiento de GPT-OSS,
Gemini ni un ajuste LoRA. El entrenamiento local tardó 0,076 s.
Una comprobación de cuatro frases en el detector activo promedió 0,423 ms por
frase; es una muestra corta e incluye lectura local del archivo de pesos.

Los programas de las rondas que superaron las dos comprobaciones se conservan
también como ejemplos con evidencia (`*_ejemplo.json`). No se añaden los fallidos
a train ni se suben conversaciones del usuario. Son pocos ejemplos especializados,
insuficientes para justificar un ajuste generativo o afirmar una mejora general.

## Entrenamiento generativo pendiente

En el diagnóstico había alrededor de 1,5 GB de RAM libres. Python 3.11 no tenía
torch, transformers, PEFT, TRL ni datasets. No se instalaron pesos ni se inició
LoRA en esta PC saturada. El entrenador generativo separado ya existente sigue
disponible, pero requiere más datos revisados y un entorno con recursos suficientes.

Prioridad siguiente: ampliar tareas y verificadores independientes, ejecutar rondas
en varios días y proveedores, y medir tasa de finalización y p95. No sustituir
verificación de software por entrenamiento ni promover un modelo por una muestra buena.

Cierre de esta entrega: 69 pruebas de regresión correctas; compilación Python y
comprobación de whitespace correctas. Los resultados no certifican todos los
programas que genere LARA ni disponibilidad continua de los servicios gratuitos.

## Afinación y prueba de fuerza — 23 de octubre de 2026

Se ejecutaron turnos reales de LaraCerebro desde terminal: cálculos, lógica,
instrucciones, generación de tres funciones Python y contexto de dos turnos.
Cada caso usa datos temporales, voz desactivada, captura de aprendizaje apagada
y prohibición de herramientas. Sí consume la cuota de los motores configurados.
No se modificaron documentos, programas ni sesiones reales del usuario al probar.

| Ronda, mismos 16 casos | Aciertos estrictos | Media | Mediana | Máximo |
|---|---:|---:|---:|---:|
| Inicial | 10/16 | 4,703 s | 1,744 s | 18,429 s |
| Contrato JSON y prioridad de correcciones | 12/16 | 4,044 s | 1,913 s | 16,787 s |
| Modelo explícito e historial por turnos | 13/16 | 3,404 s | 2,144 s | 19,571 s |

Los tiempos miden el procesamiento de la respuesta, no la importación/arranque.
En casos de contexto incluyen ambos turnos. La media bajó, pero la mediana y el
máximo no mejoraron. Una ronda por variante y proveedores/cuotas variables no
permiten atribuir toda la diferencia al ajuste ni estimar rendimiento estable.

Cambios implementados en core/llm_engine.py:

- Historial con roles separados y hasta seis turnos de 2000 caracteres cada uno,
  en lugar de cuatro recortados a 300 e incrustados en una sola pregunta.
- Instrucciones de prioridad del mensaje actual: una corrección reemplaza el dato
  antiguo sin borrar otros datos del contexto. Los tres casos de contexto finales
  pasaron; antes fallaba el cambio de viernes a lunes.
- Contrato JSON explícito y modo JSON en gateway, Groq, Gemini y respaldo local.
  La limpieza de presentación ya no modifica operadores ni literales de JSON.
  Las peticiones JSON por streaming usan la ruta completa, no una ruta sin contrato.
- Razonamiento mediante modelo explícito, no auto:smart. Variable opcional
  LARA_GATEWAY_REASONING_MODEL; por defecto MODELO_PROFUNDO (gpt-oss-120b).
  Se rechazan sustituciones de modelo del gateway y se pasa al respaldo directo.
  En rondas anteriores auto eligió también motores de moderación y robótica.
- Petición profunda al gateway acotada a 10 segundos. No se duplican llamadas de
  autorrevisión para JSON; no se añadió un ciclo de reparación ilimitado.

Resultado final por categoría: cálculo 3/3, lógica 3/4, instrucciones 3/3,
código 1/3 y contexto 3/3. El fallo de lógica fue falta de servicio/cuota, no una
respuesta lógica errónea. Dos respuestas de código usaron la clave inglesa code
en lugar de codigo y siguen siendo fallos del contrato: no se maquillaron con
renombrado de campos. El ejecutor de código admite un subconjunto restringido de
Python, por lo que un rechazo suyo no siempre significa un programa incorrecto.
No es un aislamiento de seguridad completo para código arbitrario.

Tres casos adicionales redactados después del ajuste pasaron 3/3: saldo con una
clave distinta, corrección de fecha conservando otros campos y literales con
asteriscos. No se sumaron a los 16 del antes/después. Regresión: 74 pruebas
correctas; compilación y comprobación de whitespace sin errores.

Reproducción (requiere motores disponibles; consume cuota):

```
python tools/prueba_fuerza.py --casos 0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 --salida .tools/fuerza/repeticion.json
python tools/prueba_fuerza.py --casos 16 17 18 --salida .tools/fuerza/adicionales_repeticion.json
python -m unittest discover -s tests
```

Evidencia local ignorada por Git: .tools/fuerza/reporte.json (inicial),
afinado.json (intermedia), final.json y adicionales.json. Un plazo por caso de
40 segundos y corte tras tres fallos de servicio consecutivos limitan la batería.
No se envían las respuestas esperadas al modelo.

### Nivel y objetivo pendiente

LARA es la aplicación que organiza motores y herramientas, no un modelo GPT
entrenado aquí. Estos cambios son ingeniería de contexto, selección y contratos;
no modificaron pesos ni entrenaron GPT-OSS/Gemini. Esta batería no mide todavía
voz, navegación real, autonomía larga ni todas las funciones de Code.

No hay evidencia para afirmar que iguala a Luna, Terra o Sol: haría falta ejecutar
modelos identificados por versión sobre las mismas tareas, repetidamente, con
presupuesto comparable y verificación independiente. Las denominaciones por sí
solas no son una escala universal. Referencias oficiales consultadas:
https://developers.openai.com/api/docs/models/all y
https://developers.openai.com/api/docs/guides/model-selection .

Objetivo práctico siguiente: 90 % o más de tareas acotadas completadas con su
contrato correcto en varias rondas y días, reportando por separado calidad,
disponibilidad y latencia. Pendientes: validación de esquemas explícitos cuando
la tarea los aporte, pruebas más amplias de código y recuperación de cuota sin
degradar silenciosamente a motores especializados. No se promete disponibilidad
gratuita ilimitada ni cero demoras con el hardware actual.
