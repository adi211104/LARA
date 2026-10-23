# Auditoría de viabilidad — LARA

Fecha de revisión: 10 de octubre de 2026
Alcance: revisión estática del repositorio, compilación de Python y ejecución de `python probar_cerebro.py`. No se usaron cuentas, micrófono, cámara ni servicios de nube durante la revisión.

## Dictamen

Actualización 2026-10-14: incorporados recursos adaptativos, utilidades locales
sin LLM, recordatorios persistentes y clientes de nube opcionales sin claves.
Detalles y límites de verificación en LEEME y CHANGELOG. Esta mejora no cierra
la auditoría completa ni certifica un agente autónomo general. La disponibilidad
de un modelo local y la aceleración de la RX 570 quedan pendientes de validar.

**LARA es viable como base de un asistente local para Windows, pero no es viable continuar agregando capacidades sobre la arquitectura actual.** Primero debe convertirse en una plataforma pequeña, observable y segura. El mayor problema no es la falta de modelos: es que funciones muy distintas están acopladas al mismo flujo, con límites de permisos incompletos y sin pruebas de integración.

La meta “local, gratis, potente y funcional” es alcanzable si se define con precisión:

| Meta | Viabilidad | Decisión recomendada |
|---|---|---|
| Local/offline para conversación, voz, memoria y acciones básicas | Alta | Ollama + Whisper/Kokoro + SQLite, sin claves obligatorias. |
| Gratis en uso | Alta, con coste de hardware/energía | Eliminar la nube del camino crítico; mantenerla solo como conector opcional. |
| Potente para razonamiento largo, visión y navegación compleja en CPU modesta | Limitada | Ofrecer calidad degradada local, o declarar la nube como opcional. No prometer equivalencia. |
| Automatizar Windows de forma segura | Media | Requiere un corredor de herramientas con permisos, vista previa y lista permitida. |
| Ejecutar “muchas cosas” sin mantenimiento | Baja hoy; media tras refactor | Reducir superficie, versionar contratos y probar cada integración. |

## Evidencia recogida

- `python -m compileall -q .` terminó correctamente.
- `python probar_cerebro.py` terminó correctamente. Valida reflejos, filtros de selección local/nube, JSON de router y memoria si Ollama está presente. El entorno de revisión no tenía Ollama disponible, por lo que las pruebas en vivo de modelo/embeddings se omitieron.
- El proyecto contiene módulos muy grandes: `core/cerebro.py` (~1.769 líneas), `core/llm_engine.py` (~1.416), `gui.py` (~1.150) y `core/skills_voz.py` (~898). Esto explica que un cambio de voz, UI o herramienta pueda afectar al flujo completo.
- La batería actual no prueba GUI, audio real, confirmaciones, persistencia, errores de red, instalación limpia, RAG, seguridad ni acciones reales de Windows.

## Hallazgos priorizados

### A-01 — El límite de permisos se puede evitar por rutas rápidas (crítico)

`core/modos.py` aplica el modo de seguridad a decisiones que pasan por `LaraCerebro._procesar_respuesta_llm`. Sin embargo, `LaraCerebro._pre_despacho_rapido` ejecuta directamente notas, dictado, apertura de programas y operaciones de sistema sin pasar por el mismo evaluador. Esto rompe la premisa de que `manual`, `plan` y `lectura` son un único control de acceso. Además, el fallback “si no hay interfaz de confirmación, autoriza” en `_confirmar` no es seguro por defecto.

**Corrección antes de nuevas funciones:** una única función de autorización debe envolver *toda* herramienta, incluida la ruta de reflejos y las llamadas internas. Sin callback de confirmación, una acción riesgosa debe denegarse, no autorizarse. El modo `lectura` debe ser una garantía verificable.

### A-02 — Comandos arbitrarios y registro dinámico amplían demasiado la superficie (crítico)

`core/skills_os.py` usa `subprocess` con `shell=True` para comandos generados a partir de texto. `core/skills_registry.py` puede descubrir y ejecutar scripts en `core/`. Aunque el modo manual atenúa el riesgo, un modelo, una memoria o una instrucción web maliciosa todavía pueden proponer acciones peligrosas.

**Corrección:** sustituir el comando genérico por herramientas con esquemas tipados y listas permitidas. Conservar una consola explícita solo para el usuario, con vista previa, confirmación y registro. Las skills deben declararse en un manifiesto versionado y permitido, no descubrirse por escaneo.

### A-03 — “Local y gratis” no coincide con la implementación (alto)

`core/llm_engine.py`, visión y varias rutas de respaldo inicializan y utilizan Groq/Gemini. El modelo Ollama de 3B se dedica principalmente al enrutado. Si no hay claves, internet o cuota, muchas funciones cambian de calidad o fallan.

**Corrección:** separar proveedores por capacidades e implementar el perfil `local-only` como una configuración soportada y probada. La nube debe ser un conector opcional, visible y desactivado por defecto en ese perfil.

### A-04 — Instalación y dependencias no son reproducibles (alto)

`INICIAR_LARA.bat` ejecuta `pip install -r requirements.txt` en cada arranque, sin entorno virtual ni versiones fijadas. `pyproject.toml` no replica todas las dependencias recientes declaradas en `requirements.txt` (por ejemplo, el bloque Kokoro). Una actualización de PyPI puede cambiar el comportamiento o romper el inicio.

**Corrección:** crear `requirements.lock` con versiones y hashes, alinear `pyproject.toml`, añadir `.env.example`, script de bootstrap idempotente y CI en una instalación nueva. El lanzador final no debe instalar paquetes.

### A-05 — Núcleo monolítico y responsabilidades mezcladas (alto)

`cerebro.py` decide, autoriza, ejecuta, persiste, habla, hace reintentos y controla el modo agente. GUI y voz contienen concurrencia propia. Este diseño hace difícil diagnosticar “no funciona como quisiera” porque no hay contratos claros entre planificador, herramientas, estado y UI.

**Corrección:** extraer cuatro capas: `App/GUI`, `AssistantService`, `ToolRunner` y `Providers`. Cada herramienta devuelve un resultado tipado; el servicio emite eventos; GUI/voz solo consumen eventos.

### A-06 — Persistencia y privacidad necesitan un contrato (medio-alto)

La ruta base se fuerza a OneDrive cuando existe; memoria, transcripciones, auditoría, RAG y datos del taller pueden sincronizarse fuera del equipo. No se documentan retención, cifrado, borrado, conflictos de sincronización ni backup.

**Corrección:** elegir directorio de datos en el primer inicio, permitir modo local puro, documentar cada archivo de datos, añadir exportar/borrar todo y usar operaciones atómicas/migraciones para SQLite y JSON.

### A-07 — Observabilidad insuficiente y errores silenciados (medio)

Hay numerosos `except Exception` que continúan el flujo. Es adecuado aislar integraciones opcionales, pero sin un evento estructurado con causa, proveedor, duración y acción solicitada el usuario solo percibe fallos intermitentes.

**Corrección:** logging estructurado con IDs de turno, niveles, rotación y una pantalla de salud. Convertir los fallos de dependencias requeridas en mensajes accionables; no ocultarlos.

### A-08 — Cobertura de pruebas insuficiente para el riesgo (medio)

El banco de pruebas actual es útil como smoke test de router, pero no simula proveedores ni evita que una regresión ejecute una herramienta. No hay una suite automatizada por capas ni pruebas de instalación.

**Corrección:** introducir `pytest`, dobles de herramientas/proveedores y pruebas obligatorias de autorización, JSON inválido, timeouts, cancelación, persistencia, rutas y UI mínima. No ejecutar acciones reales de sistema en CI.

### A-09 — Varias promesas de producto no son verificables (medio)

El antiguo LEEME acumulaba muchas novedades, métricas y afirmaciones históricas sin una matriz de plataformas, modelo, hardware, versión ni prueba reproducible. Eso dificulta saber qué debe funcionar ahora.

**Corrección aplicada en esta revisión:** se reemplazó por una guía breve y se concentró el estado, límites y hoja de ruta en este documento.

## Arquitectura objetivo

```text
GUI / Consola / Voz
        │ eventos y solicitudes
        ▼
AssistantService ── Estado de sesión / memoria / cancelación
        │ plan tipado
        ▼
PolicyEngine ── permisos, consentimiento, auditoría
        │ herramienta autorizada
        ▼
ToolRunner ── archivos | Windows | web opcional | tareas
        │ resultados tipados
        ▼
Providers ── LLM local | STT | TTS | embeddings | nube opcional
```

Reglas de diseño:

1. Ningún proveedor, modelo o UI llama a `subprocess`, archivos o red de forma directa: todo pasa por `ToolRunner` y `PolicyEngine`.
2. Cada herramienta declara `id`, argumentos validados, efecto (`read`, `write`, `execute`, `network`), reversibilidad y prueba.
3. El planificador solo propone; no tiene privilegios para ejecutar.
4. Los datos y las claves no se mezclan con el código. El perfil `local-only` no inicializa clientes de nube.
5. Las capacidades se activan por manifiesto, no por autodetección de scripts.

## Hoja de ruta propuesta

### Fase 0 — Congelar y medir (1–3 días)

- Crear una rama de estabilización y no añadir funciones.
- Reproducir los tres fallos más molestos del usuario en una tabla: entrada, resultado esperado, resultado real, log/ID de turno y prioridad.
- Añadir pruebas de humo de GUI, voz y perfil sin internet.
- Versionar entorno y preparar `.env.example` sin secretos.

**Salida:** instalación limpia repetible y una lista de defectos real, no suposiciones.

### Fase 1 — Seguridad y fiabilidad base (1–2 semanas)

- **Avance 2026-10-10:** se creó `LaraCerebro._autorizar_accion`, usado por el despacho LLM y los atajos de notas, dictado, sistema y skills. Una acción de riesgo sin UI de confirmación ahora se deniega. Se añadieron regresiones en `tests/test_fase1_policy.py`.
- **Avance 2026-10-10:** `auto` confirma acciones riesgosas; se pausaron por defecto `cmd`, elevación, ejecución de código, skills dinámicas y automatización de teclado/ratón. Su interruptor de desarrollo es `LARA_ENABLE_EXPERIMENTAL_TOOLS=1`; no es una opción de producción.
- **Avance 2026-10-10:** se añadió `.env.example`, sin secretos y con las claves opcionales documentadas.
- **Avance 2026-10-10 (segunda pasada):**
  - *A-01:* se cerraron cuatro rutas que seguían ejecutando sin pasar por `_autorizar_accion`: el reintento "usa permisos de administrador" (cerraba procesos elevados incluso en `lectura`), "deshaz lo último", las rutinas (`modo taller`, `modo noche`…; ahora cada paso con efecto se autoriza y sin autorizador se deniega) y el alias `aprender_de_archivo`, que no figuraba como acción de riesgo. Nueva categoría `ACCIONES_EFECTO` (`abrir`, `url`, `rutina`): directas en `manual`/`auto`, bloqueadas en `plan`/`lectura`. El dictado de la GUI se detiene si el modo cambia a `plan`/`lectura` a mitad.
  - *A-02:* `abrir_url_en_navegador`, `abrir_programa`, el respaldo literal de `abrir_programa_inteligente` y `_lanzar_uri` pasaban texto del modelo a `cmd.exe` con `shell=True`, una acción **inocua** (sin confirmación): una URL como `https://x.com" & calc & "` ejecutaba un comando. Ahora usan `os.startfile` o listas de argumentos, validan la URL (solo http/https) y rechazan metacaracteres. Brillo y bloqueo de pantalla tampoco usan shell. `ejecutar_cmd`, `ejecutar_cmd_admin` y la ejecución de código se niegan **dentro de la propia función** sin `LARA_ENABLE_EXPERIMENTAL_TOOLS=1` (defensa en profundidad).
  - *Open Code:* sin UI de confirmación aprobaba todo; ahora deniega. Los comandos del modelo se confirman también en la pestaña "auto" durante la Fase 1, el modo global `plan`/`lectura` bloquea escribir y ejecutar, y cada operación queda en `logs/auditoria.log`.
  - *A-04:* `requirements.lock` (108 paquetes con versión exacta, incluidas las transitivas, verificadas en Python 3.14.6), `INSTALAR_LARA.bat` (crea `.venv`, instala el lock, crea `.env` si falta y ejecuta las pruebas), `INICIAR_LARA.bat` ya no instala paquetes, `pyproject.toml` alineado con `requirements.txt` (faltaba el bloque Kokoro) y `tools/generar_lock.py` para regenerar el lock.
  - *A-06 (parcial):* `LARA_DATA_DIR` y `LARA_DATA_LOCAL=1` permiten sacar los datos de OneDrive. Sin ellas se conserva la ruta histórica para no "perder" datos existentes.
  - *A-07 (parcial):* `errores.log` rota (1 MB × 5) y lleva ID de turno; nuevo `logs/eventos.jsonl` con inicio/fin de turno y duración, decisiones de autorización y el proveedor (local o nube) que resolvió cada turno.
  - *A-08:* la batería pasa de 6 a 32 pruebas (`python -m unittest discover -s tests`), aisladas en un directorio temporal: nunca tocan los datos reales. Se verificó con mutación que detectan la reintroducción de un bypass.
- **Pendiente:** cerrar A-01 y A-02 *estructuralmente* (`PolicyEngine`/`ToolRunner`) antes de volver a habilitar modo agente, comandos o skills generadas. Lo hecho es contención verificada, no el diseño final.
- **Pendiente:** hashes en `requirements.lock` y una instalación limpia automatizada (CI).

**Criterio de aceptación:** en modo `lectura` y `plan`, una prueba automatizada demuestra que no se llama a ninguna función que modifique archivos, procesos, teclado, ratón o red con efecto. **Estado: cumplido** por `tests/test_aceptacion_modos.py`, que sustituye por dobles todas las funciones con efecto y recorre cada acción de riesgo o efecto, cada operación de sistema riesgosa, los atajos, rutinas, deshacer y el reintento elevado, también con las herramientas experimentales activadas.

### Fase 2 — Perfil local-first (2–3 semanas)

- Crear configuración explícita `local-only`, `hybrid` y `cloud-optional`.
- Soportar sin claves: Ollama para chat, embeddings y clasificación; Whisper local para STT; Kokoro/Piper para TTS; SQLite para memoria.
- Mostrar en cada turno el proveedor, tiempo, coste estimado y fallback.
- Separar búsqueda/visión de la conversación; si no existen localmente, dar un mensaje claro en lugar de llamar a la nube silenciosamente.

**Criterio de aceptación:** conversación, memoria, voz y tareas locales se ejecutan durante una hora sin internet ni `.env`.

### Fase 3 — Refactor y herramientas confiables (3–6 semanas)

- Extraer `PolicyEngine` y `ToolRunner`, luego dividir `cerebro.py` por casos de uso sin cambiar comportamiento.
- Reemplazar comandos generales por 10–15 herramientas de alto valor: abrir apps permitidas, leer/escribir bajo un workspace permitido, tareas, notas, búsqueda opcional y diagnóstico.
- Migrar datos a un esquema con migraciones y controles de retención.

**Criterio de aceptación:** cada herramienta tiene contrato, validación, confirmación cuando aplica, auditoría y pruebas.

### Fase 4 — Volver a ampliar (continuo)

Solo después de las fases anteriores: RAG, visión local, automatización visual, modo reunión y agente multipaso. Cada capacidad debe nacer apagada, con un benchmark de hardware y un botón claro para revocar permisos/datos.

## Qué retirar o pausar ahora

No se recomienda borrar código de inmediato sin telemetría, pero sí **pausar y ocultar** de la experiencia por defecto:

- Modo agente multipaso.
- Generación, instalación y autodetección de skills.
- `cmd`, `cmd_admin` y ejecución de código desde decisiones del modelo.
- Automatización de clic/teclado sin una vista previa contextual.
- Investigación/alertas proactivas en segundo plano hasta definir privacidad, coste y reglas de notificación.

Estas funciones son las que más multiplican fallos y riesgos, y no son necesarias para validar el núcleo: conversación local, voz, memoria, notas, tareas y apertura explícita de aplicaciones.

## Backlog de decisiones abiertas

Antes de implementar Fase 2 hay que decidir:

1. Hardware objetivo mínimo (CPU, RAM, GPU opcional, almacenamiento).
2. Si LARA será estrictamente local o si la nube podrá activarse de forma explícita por tarea.
3. Alcance real de automatización: asistente que recomienda, o agente que modifica el PC.
4. Política de privacidad para voz, reuniones, documentos y OneDrive.
5. Las 5–10 tareas que justifican el producto; todo lo demás debe esperar.

## Registro de cambios de auditoría

| Fecha | Cambio | Motivo |
|---|---|---|
| 2026-10-10 | Se añade esta auditoría y se simplifica `LEEME.md`. | Sustituir afirmaciones acumuladas por estado verificable, límites y un plan de estabilización. |
| 2026-10-10 | Se aplica la contención inicial de Fase 1 y seis pruebas de regresión. | Impedir la ejecución silenciosa por rutas rápidas y pausar superficies de ejecución dinámica. |
| 2026-10-10 | Se rediseña la GUI y se documenta en `CHANGELOG.md`. | Mejorar legibilidad, jerarquía visual y percepción de estado sin alterar el núcleo del asistente. |
| 2026-10-10 | Segunda pasada de Fase 1: cuatro bypass de autorización cerrados, inyección de comandos vía URL corregida, Open Code contenido, instalación fijada, directorio de datos configurable, logging estructurado y 32 pruebas. | Cumplir el criterio de aceptación de la Fase 1 y los pendientes de A-04 sin esperar decisiones de producto. |
