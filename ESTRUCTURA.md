# Estructura de LARA

Mapa del proyecto para orientarse rápido. LARA es un asistente de escritorio
tipo JARVIS para Windows: cerebro híbrido local + nube, voz, visión, memoria
persistente y ejecución de acciones en el sistema operativo.

## Arranque

| Archivo | Qué hace |
|---|---|
| `INICIAR_LARA.bat` | Instala dependencias que falten y abre la GUI. Punto de entrada recomendado. |
| `gui.py` | Interfaz gráfica (ventana principal). |
| `main.py` | Modo consola (capa fina sobre `core/cerebro.py`). |
| `probar_cerebro.py` | Banco de pruebas manual: reflejo, filtros, validador, modelo local y memoria. |

## Configuración

| Archivo | Qué hace |
|---|---|
| `.env` | API keys (Gemini, Groq, Serper). **No se versiona** (`.gitignore`). |
| `config.py` | Rutas base (OneDrive/Taller_IA), identidad, carpetas conocidas, herramientas. |
| `ui_tema.py` | Tema/paleta de la GUI. |

## Núcleo (`core/`)

### Orquestación
| Módulo | Responsabilidad |
|---|---|
| `cerebro.py` | **Núcleo**: procesa cada turno, decide y ejecuta la acción. Compartido por GUI y consola. |
| `llm_engine.py` | Los motores en la nube (router Groq, razonamiento profundo, gestor Compound) y niveles de esfuerzo. |
| `llm_local.py` | Cliente de Ollama (Qwen 3B router local + embeddings locales). |
| `modos.py` | **Modos de operación** (auto / manual / plan / lectura) y control de confirmación + auditoría de acciones. |

### Memoria y sesiones
| Módulo | Responsabilidad |
|---|---|
| `sesiones.py` | Historial por sesión (persistencia entre reinicios). |
| `memoria_vectorial.py` | Memoria semántica de largo plazo (embeddings + SQLite). |
| `skills_memoria.py` | Utilidades de memoria de turnos. |
| `skills_conocimiento.py` | Base de conocimiento guardado. |
| `uso_tokens.py` | Contabilidad de tokens y ahorro (local vs nube). |

### Skills (capacidades)
| Módulo | Responsabilidad |
|---|---|
| `skills_os.py` | Ejecutar comandos, código, archivos, brillo, abrir programas. **Acciones de riesgo.** |
| `skills_sistema.py` | Hora, batería, volumen, captura, portapapeles, IP, wifi... (instantáneas). |
| `skills_programas.py` | Apertura inteligente de programas (menú inicio / URI de Windows). |
| `skills_control.py` | Automatización: teclear, clic, teclas, dictado. |
| `skills_web.py` | Leer páginas web. |
| `skills_download.py` | Descargar archivos y videos (yt-dlp). |
| `skills_vision.py` | Visión multimodal: pantalla, imágenes, PDF, video, audio. |
| `skills_voz.py` | Voz: TTS (edge-tts) + STT (whisper). |
| `skills_tasks.py` | Gestión del taller: tareas, clientes, compras, proyectos. |
| `skills_registry.py` | Registro y ejecución de skills instaladas dinámicamente. |
| `skills_propuestas.py` | Auto-generación de skills nuevas (propuestas). |
| `skills_novedades.py` | Novedades del agente de fondo y registro de limitaciones. |
| `skills_explicar.py` | Explicaciones visuales animadas (holograma en la GUI). |
| `skills_audio.py` | Tonos/chimes sintetizados. |

### Voz e interacción por micrófono
| Módulo | Responsabilidad |
|---|---|
| `stt_local.py` | Transcripción local (faster-whisper). |
| `wake_word.py` | Palabra de activación ("hey jarvis" / "Lara"). |
| `voz_id.py` | Reconocimiento del hablante (candado de voz). |

### Modos y fondo
| Módulo | Responsabilidad |
|---|---|
| `agente_fondo.py` | Agente en segundo plano: alertas, recordatorios, investigación. |
| `rutinas.py` | Rutinas programables ("modo taller", "modo noche"...). |
| `modo_reunion.py` | Escucha ambiente + análisis a demanda. |
| `modo_lectura.py` / `modo_nocturno.py` | Perfiles de lectura / nocturno. |
| `diagnostico.py` | Auto-diagnóstico de sistemas ("revísate"). |
| `open_code.py` | Panel de código embebido. |
| `logger.py` | Logging centralizado de errores. |

### Paneles de GUI
| Archivo | Responsabilidad |
|---|---|
| `gui_explicacion.py` | Panel de explicación visual (animación / slides). |
| `gui_opencode.py` | Panel de código (OpenCode). |

## Datos en runtime (fuera del repo)

Todo esto vive en `Taller_IA/` (OneDrive) y **no se versiona**:

- `conocimiento/` — memoria, base de conocimiento, nivel de esfuerzo, modos, rutinas.
- `logs/` — `errores.log` y `auditoria.log` (acciones ejecutadas).
- `workspace/` — archivos de trabajo generados por LARA.
- `skills_pendientes/` — propuestas de skills antes de instalarlas.
