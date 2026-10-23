# Motores y mejora continua de LARA — 23 de octubre de 2026

## Vista de progreso y autonomía de Open Code

### Referencias al programa creado

El registro aportado por el usuario mostró que «abre el programa» se convirtió
erróneamente en iniciar LARA. La causa era que las tareas nuevas enviaban solo
la nueva frase y el árbol, sin contexto conversacional ni relación con los archivos
creados. Ahora planificación y decisiones reciben objetivo original, últimos turnos
y artefactos de la sesión. La lista se guarda en la sesión y se reconstruye desde
registros `[escribir]` de sesiones anteriores cuando existen.

Las órdenes habituales «abre el programa», «abre el programa que hiciste» y
equivalentes reconocidos abren el único programa registrado mediante una ruta
determinista, sin pedir al modelo que adivine. Los scripts Python se lanzan con el
intérprete de LARA en una consola Windows independiente; necesitan su propia pausa
si deben permanecer abiertos al terminar. Si hay varios artefactos ejecutables
de distinto nombre, se solicita cuál; si falta el archivo, no se inventa una ruta.
Se siguen respetando modos de planificación/lectura y de confirmación.

Se bloquea el comando observado `INICIAR_LARA.bat` y relanzamientos directos
identificados de entradas de la aplicación. Este filtro no es un sandbox general
para comandos arbitrarios. El árbol excluye `.tools`, archivos ocultos y `.key`;
las herramientas de archivo bloquean `.env`, `.tools` y `.key`.

Pruebas añadidas: reapertura y apertura del artefacto correcto sin llamar al modelo,
contexto en una petición de seguimiento, rechazo del relanzamiento observado,
exclusión de configuración privada y ambigüedad entre varios ejecutables.
La apertura visible se comprobó con el lanzamiento simulado, evitando abrir otro
programa durante los tests; no se afirma una validación visual de esa ventana.

### Recuperación de sesiones y cuota

Se corrigió la apertura del historial: ahora reconstruye el agente usando el mismo
ID y restaura la carpeta guardada, sin crear una sesión al volver a elegir carpeta.
Las sesiones antiguas tituladas exactamente `Code: Desktop` pueden recuperar el
escritorio; para otras carpetas sin metadatos se elige la carpeta una vez dentro
de la sesión existente. No se borran sesiones duplicadas previas.

El objetivo y los resultados recientes del agente se guardan como punto de
continuación en la sesión. Los cambios se escriben mediante archivo temporal y
reemplazo atómico. Las sesiones antiguas sin punto de continuación recuperan el
objetivo del historial y deben inspeccionar los archivos antes de actuar.
No se supone que una acción terminó correctamente si faltan sus resultados.

La cola de interfaz procesa eventos del trabajador desde el hilo Tk principal.
Se limita a 200 turnos la visualización de sesiones grandes; el archivo conserva
el historial completo. El árbol de trabajo limita el recorrido a 200 entradas y
omite dependencias antes de descender. Estas correcciones reducen bloqueos; no
se atribuye un cierre nativo específico sin volcado o registro del fallo.

Se comprobó el servicio FreeLLMAPI activo y con peticiones completadas. Sus logs
mostraron también agotamiento de TPM/TPD de Groq para GPT-OSS: un catálogo de modelos
no elimina los límites de las claves disponibles. Open Code ahora añade un
respaldo directo de Google, validando la decisión antes de ejecutarla. No se
garantiza continuidad cuando todos los proveedores configurados agotan su cuota.

Verificación: 61 pruebas correctas, prueba Tk de reapertura sin clon con carpeta y
avance recuperados, y una decisión real de Open Code sin despachar herramientas.

La vista normal oculta árbol, contenido leído, comandos y salida de terminal.
Muestra progreso breve y el resumen final; «Detalles técnicos» permite consultar
el registro completo de la sesión actual sin perder eventos al alternar la vista.
Las confirmaciones necesarias en modos con permisos siguen mostrando el comando
real para decidir con información. No se presentan razonamientos internos como
si fueran una transcripción: las etiquetas de progreso describen trabajo en curso.

Por petición explícita del usuario, «Modo automático» en Open Code ahora autoriza
escrituras y comandos sin preguntar cada paso. «Solicitar permisos» confirma ambas
acciones y «Aceptar ediciones» confirma comandos. Los modos globales planificación
y lectura siguen bloqueando modificaciones. Este ajuste es exclusivo de Open Code;
no habilita todas las herramientas experimentales del chat normal. Los comandos
se ejecutan con los permisos del proceso de LARA y no constituyen un sandbox.

«Continuar» sin un punto de ejecución en memoria se detiene con una explicación:
no se envía al modelo como una tarea nueva ni se inventa otro objetivo. El agente
recibe instrucciones de usar cmd.exe en Windows y evitar heredocs de Bash.

## Corrección de Open Code: programas interactivos y cuota

Open Code ahora consulta el gateway con un modelo explícito permitido y valida
su propio formato de decisiones (distinto al router de chat). Si el gateway falla,
prueba la cadena de modelos directos. La planificación también admite gateway.
Esto corrige la omisión anterior: la primera integración no cubría Open Code.

Los comandos de prueba admiten `entrada`, texto enviado a stdin, por ejemplo:
`{"accion":"cmd","comando":"python draw_rectangle.py","entrada":"5\n3\n\n"}`.
Incluye el ENTER final para la pausa. Sin ese campo, stdin sigue cerrado para
evitar bloqueos. Los resultados muestran código de salida y una indicación
específica si se produjo EOF por falta de entrada. No se debe quitar input() del
programa para adaptarlo a una prueba sin stdin.

Los errores 429 ya no añaden una corrección de JSON engañosa. Se respeta Retry-After
o la espera indicada por el proveedor, limitada a 30 s por espera, cancelable con
Detener, y se intenta otro modelo. Se reduce el historial enviado conservando el
objetivo y los resultados recientes. Las respuestas truncadas no se ejecutan.

Si se interrumpe el ciclo, `continuar` retoma el historial sin repetir automáticamente
herramientas anteriores. El punto de continuación ahora se persiste en la sesión,
junto con la carpeta, según la corrección de recuperación descrita arriba.

La confirmación del modo automático descrita en la primera corrección fue
sustituida por la autorización explícita posterior del usuario, descrita arriba.
Una prueba de stdin capturada no certifica una ventana de terminal visible. Para
doble clic puede entregarse un .bat con `python` y `pause`; en una terminal abierta
la propia sesión ya permanece abierta después de terminar el proceso.

Se probó el `draw_rectangle.py` existente del escritorio sin modificarlo: 5×3,
1×4 y 4×1 dieron dimensiones exactas; entradas no numéricas, cero y negativas
se recuperaron correctamente. Una prueba mantuvo stdin abierto, comprobó que
el proceso seguía esperando después de las medidas y terminó con código 0 al
enviar el ENTER final. Eso confirma la pausa del proceso, no una ventana GUI.

## Qué está integrado

- FreeLLMAPI se instala por separado en `.tools/freellmapi`, excluido de Git,
  fijado al commit `26484157fd15f5fb66f54d3a86f568997ea304de`.
- El adaptador `core/gateway_llm.py` usa HTTP loopback, autenticación, timeout,
  respuestas completas y validación JSON. No añade dependencias al entorno principal.
- Conversación profunda usa `auto:smart`; las decisiones de acciones usan un
  modelo explícito evaluado. Se rechaza una respuesta de otro modelo y el uso de
  aliases `auto` para acciones. Se admiten identificadores equivalentes con prefijo
  de proveedor, siempre que figuren en la lista permitida.
- La política de permisos de LARA sigue controlando cualquier acción. El gateway
  no ejecuta herramientas. Si falla, se conserva el respaldo Groq/Gemini/local.
- El razonamiento por voz pasa por la misma integración. En gateway se entrega
  texto completo; no hay streaming incremental de tokens en este adaptador.
- El registro para aprendizaje guarda pregunta, respuesta, tiempo, motor, acción
  y estado técnico en SQLite. `ok` significa que el turno terminó sin excepción,
  no que el contenido sea verdadero ni que una acción se haya verificado externamente.
- La revisión explícita permite aprobar, corregir o rechazar. El modelo no se
  autoaprueba. Las correcciones quedan separadas de la respuesta original.
- Hay exportación de ejemplos revisados, evaluación sin ejecutar acciones,
  comparación de candidatos, activación condicionada y reversión.
- Hay un entrenador LoRA/QLoRA opcional en `tools/entrenar_lara.py`. No utiliza
  los datos de test para entrenar y no publica ni activa el adaptador resultante.

## Uso cotidiano

Iniciar mediante `INICIAR_LARA.bat`; si existe configuración del gateway, arranca
el servicio auxiliar en segundo plano, limitado a `127.0.0.1:3001`.

Pedidos disponibles en chat:

- `mis motores` y `estado del gateway`.
- `aprendizaje`: estado e instrucciones.
- `activar aprendizaje` / `desactivar aprendizaje`: requieren la política de escritura.
- `respuesta correcta` / `respuesta incorrecta`: revisa el último turno capturado.
- `corrige respuesta: aquí va la respuesta correcta`: conserva una corrección explícita.

La captura está habilitada en esta instalación por la solicitud de integración.
El valor predeterminado para instalaciones nuevas sigue siendo apagado. Se crearon
20 ejemplos sintéticos iniciales de órdenes; TODOS están pendientes, sin aprobación.
El botón «Aprendizaje» de la GUI muestra el estado, no comienza a entrenar pesos.

El registro vive bajo el directorio de datos existente:
`conocimiento/aprendizaje/experiencias.sqlite3`. Si tu configuración usa OneDrive,
esa carpeta también puede sincronizarse. La captura no sube datasets a servicios de IA.
La conversación cloud normal sí envía las peticiones al proveedor seleccionado.

La redacción elimina claves conocidas del entorno/configuración, correos, patrones
comunes de teléfonos y nombres de carpetas de usuario. Es heurística: antes de
exportar a otra máquina o subir datos hay que revisar nombres, direcciones y otros
datos personales. No se considera anonimización garantizada.

## Instalación y configuración del gateway

La instalación actual reutiliza las claves Groq y Google existentes, importadas
localmente en el almacenamiento cifrado del gateway. No se abrieron cuentas nuevas
ni se contrataron servicios. Las claves no se imprimen en consola.

```
python tools/gateway_lara.py instalar
python tools/gateway_lara.py iniciar
python tools/mejora_lara.py gateway
python tools/gateway_lara.py detener
```

Se necesita Node compatible y npm. La instalación usa `npm ci` y compila servidor
y panel; detener el servicio antes de reinstalar en Windows para liberar módulos
nativos. No se instala un servicio permanente de Windows ni una tarea programada.

La configuración generada queda en `.tools/gateway-lara.json`; la contraseña de
administración local y clave de cifrado en `.tools/gateway-admin.json`. Ambos
archivos son privados, están excluidos de Git y no deben compartirse. En el panel
local se puede iniciar sesión con las credenciales de ese archivo. No se muestran
en la documentación. Los logs auxiliares también pueden contener datos privados.

Variables opcionales en `.env` (tienen prioridad sobre la configuración generada):

```
LARA_GATEWAY_ENABLED=1
LARA_GATEWAY_URL=http://127.0.0.1:3001/v1
LARA_GATEWAY_API_KEY=clave-unificada-del-gateway
LARA_GATEWAY_CHAT_MODEL=auto:smart
LARA_GATEWAY_ACTION_MODELS=gpt-oss-120b,openai/gpt-oss-120b
LARA_GATEWAY_TIMEOUT=20
```

No copies claves Groq/Gemini al campo de clave unificada. Para desactivar la ruta
gateway usa `LARA_GATEWAY_ENABLED=0`; para parar también su proceso usa `detener`.
El servidor realiza sus propios chequeos de salud/cuota de proveedores.

Esta integración cubre decisiones y razonamiento textual. Visión, audio, gestor
Compound y otros helpers existentes conservan sus conexiones específicas.
El catálogo del servicio no equivale a cuotas garantizadas ni a igual calidad de
todos sus modelos. No se verificaron todos los proveedores del catálogo.

## Revisión y datasets

```
python tools/mejora_lara.py estado
python tools/mejora_lara.py listar
python tools/mejora_lara.py generar
python tools/mejora_lara.py revisar 1 corregido --correccion "Respuesta comprobada" --evidencia "Comprobación realizada"
python tools/mejora_lara.py exportar --destino salidas_aprendizaje/dataset-001
```

`generar` produce 20 propuestas deterministas por ejecución, sin consultas cloud
ni ejecución de programas. Son ejemplos de formato de acciones, no conocimiento
especializado nuevo. La revisión indica la procedencia; no se entrena con respuestas
generadas sin revisar. No hay generación indiscriminada de conversaciones con otro LLM.

La exportación exige al menos 10 preguntas distintas revisadas y datos en las tres
particiones. Ese mínimo solo permite probar el mecanismo; no garantiza calidad.
Para un ajuste útil harán falta más diversidad y pruebas con tus tareas reales.
La misma pregunta normalizada siempre pertenece a la misma partición (hash estable:
80 % train, 10 % validación, 10 % test). Se deduplican preguntas, prevalece la revisión
aprobada/corregida más reciente, y un rechazo excluye todo ese grupo. No hay detector
de equivalencia semántica: las paráfrasis cercanas también necesitan revisión.

El manifiesto registra hashes de cada archivo. El entrenador comprueba integridad
y ausencia de preguntas idénticas entre particiones. El conjunto fijo de evaluación
de código no se exporta como datos de entrenamiento.

## Evaluación automática y cambio de modelo

```
python tools/mejora_lara.py evaluar --modelo gpt-oss-120b --perfil acciones --salida acciones.json
python tools/mejora_lara.py evaluar --modelo auto:smart --perfil conversacion --salida conversacion.json
python tools/mejora_lara.py ciclo --candidato ID_MODELO --perfil conversacion --destino salidas_aprendizaje/ciclo-001 --activar-si-mejora
python tools/mejora_lara.py revertir
```

El ciclo evalúa base y candidato sobre la misma suite, compara y, si se indicó
`--activar-si-mejora`, activa solo cuando hay mejora estricta, ninguna regresión y
se cumple el presupuesto temporal. Para acciones además exige 100 % de los casos.
Un empate, fallo de red o JSON inválido no promueve al candidato. La suite es
pequeña: esto es un filtro inicial, no una garantía de precisión general.
Los aliases dinámicos `auto` no se promueven como candidatos fijos.

Para evaluar candidatos de acciones, incluir temporalmente sus IDs y los IDs reales
de respuesta en `LARA_GATEWAY_ACTION_MODELS`, manteniendo primero el modelo actual.
El adaptador comprueba que la respuesta corresponde al modelo solicitado; no acepta
un modelo diferente aunque también esté en la lista. Las pruebas nunca despachan las
acciones que genera el candidato. La activación inicial puede usar
`gateway_lara.py aprobar-acciones --informe acciones.json` cuando la suite completa
ha pasado y aún no existe un perfil inicial.

Los informes contienen respuestas sintéticas, tiempos, errores y modelo real usado.
`comparar` y `promover` permiten revisar informes previos por separado. `revertir`
recupera la selección anterior, no cambia pesos ni elimina datasets.

## Entrenamiento de pesos

En un entorno separado Python 3.10–3.12:

```
py -3.11 -m venv .tools/train-env
.tools/train-env/Scripts/python -m pip install -r requirements-train.txt
.tools/train-env/Scripts/python tools/entrenar_lara.py --dataset salidas_aprendizaje/dataset-001 --base Qwen/Qwen2.5-0.5B-Instruct --salida salidas_aprendizaje/adaptador-001
```

CUDA usa QLoRA 4 bits; `--cpu` permite un experimento pequeño en float32, lento.
Para comprobar los datos sin instalar PyTorch:

```
python tools/entrenar_lara.py --dataset salidas_aprendizaje/dataset-001 --salida salidas_aprendizaje/adaptador-001 --solo-validar
```

El entrenador descarga el modelo base si hace falta, entrena una época, evalúa
validación y guarda adaptador/tokenizador y procedencia. Nunca sobrescribe una
carpeta de candidato existente. Dependencias de entrenamiento son rangos separados,
no un lock validado de GPU. La suite actual comprueba validación de datos, no un
entrenamiento numérico completo.

Después es necesario servir el modelo base con su adaptador, registrarlo como
endpoint personalizado del gateway y evaluar ese ID. El servicio de inferencia del
adaptador no se despliega automáticamente. No se inventa un ID servido por el mero
hecho de haber guardado pesos. Soup puede añadirse más adelante como entrenador
alternativo; no se instaló en el entorno de LARA.

No se entrenaron pesos en esta entrega: faltan ejemplos revisados y entorno de
entrenamiento preparado. Tu Python principal es 3.14; se detectó también 3.11, pero
no se instalaron PyTorch ni pesos de entrenamiento. La RX 570 no se presenta como
GPU CUDA compatible. No hay entrenamientos nocturnos ni envíos cloud programados.

## Verificación real de esta entrega

- Suite de regresión final: 53 pruebas correctas; compilación Python y comprobación
  de whitespace correctas. Servidor y panel auxiliares compilaron correctamente.
- Servicio FreeLLMAPI compilado y comprobado por HTTP real, además de pruebas con
  un servidor de respuestas controladas.
- Conversación: 6/6 casos objetivos correctos, 16.138 s acumulados.
- Decisiones de acciones: 8/8 correctos, 7.090 s acumulados. No se ejecutaron programas.
- Comparación estable repetida del mismo modelo: base 8/8, candidato 8/8; no se
  promovió por empate. Esto verifica la barrera de promoción, no una mejora de pesos.
- Turnos completos: ayuda 0.090 s, tarea persistida 0.081 s, cálculo 1.351 s,
  lógica 22.611 s (respaldo directo tras espera), código 1.598 s.
- Los cálculos centrales fueron correctos; la explicación libre del cálculo incluyó
  una justificación eléctrica defectuosa. La suite objetiva no verifica cada frase
  adicional: se mantiene la necesidad de comprobación técnica independiente.

Evidencia local: `.tools/evaluacion_gateway_auto.json`,
`.tools/evaluacion_gateway_acciones.json`, `.tools/benchmark_integracion.json`.
La comparación estable está en `.tools/comparacion_acciones_estable/`. Se conservan
informes intermedios fallidos durante la instalación/reconfiguración; no representan
comparaciones de calidad válidas y no activaron candidatos.
Los resultados sintéticos de una ronda no son una estimación estadística de calidad.
