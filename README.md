# LARA

Asistente de escritorio Windows en desarrollo: interfaz gráfica, voz, herramientas,
sesiones de trabajo y motores locales/nube. No se garantiza autonomía completa ni
disponibilidad ilimitada de servicios gratuitos. Revisar acciones sobre datos reales.

## Para continuar en otra computadora

Leer [Traslado y motores](TRASLADO_Y_MOTORES.md): instalación, prueba real de Ollama,
perfil para el equipo de 24 GB y configuración de las claves de FreeLLMAPI.

1. Ejecutar `INSTALAR_LARA.bat` y comprobar que termina sin errores.
2. Configurar `.env` localmente; nunca subir claves al repositorio.
3. Configurar Ollama y/o los motores cloud según la guía.
4. Abrir `INICIAR_LARA.bat`.

## Documentación y comprobaciones

- [Estado y guía general](LEEME.md).
- [Objetivo, afinación y resultados medidos](OBJETIVO_LARA.md).
- [Gateway, evaluación y aprendizaje supervisado](MEJORA_CONTINUA.md).
- [Auditoría y límites de seguridad](AUDITORIA.md).
- Pruebas: `python -m unittest discover -s tests`.

Las claves, conversaciones, bases de datos, entornos virtuales y pesos de modelos
no se distribuyen en GitHub. Hay que configurarlos o trasladarlos por separado.
