@echo off
REM ============================================================
REM  Lanzador de LARA — interfaz grafica con voz y vision.
REM  Ya NO instala paquetes en cada arranque (auditoria A-04):
REM  la instalacion se hace una vez con INSTALAR_LARA.bat, que
REM  crea el entorno .venv con versiones fijadas.
REM ============================================================
title LARA - Asistente del Taller
cd /d "%~dp0"

set "PYTHON_LARA=.venv\Scripts\python.exe"
if not exist "%PYTHON_LARA%" (
    echo [AVISO] No existe el entorno .venv. Ejecuta INSTALAR_LARA.bat para una
    echo         instalacion reproducible. Por ahora uso el Python del sistema.
    set "PYTHON_LARA=python"
)

echo Iniciando LARA...
if exist ".tools\gateway-lara.json" (
    "%PYTHON_LARA%" tools\gateway_lara.py iniciar
)

REM ---- Cerebro HIBRIDO local + nube ------------------------------------
REM Lo simple ("abre chrome", "que hora es") lo resuelve el modelo LOCAL de
REM Ollama en esta PC (rapido, sin limites, sin internet). Lo fuerte
REM (razonamiento tecnico, codigo) y las busquedas van a la nube.
REM Requisitos: tener Ollama abierto y los modelos bajados:
REM     ollama pull qwen2.5:3b-instruct    (decisiones simples)
REM     ollama pull embeddinggemma         (memoria semantica local)
REM Si Ollama no corre, LARA usa solo la nube (no pasa nada). Pon 0 para
REM apagar el cerebro local a proposito.
REM Los valores por defecto viven en Python; respetar .env y preferencias del usuario.

REM Abrir la interfaz grafica
"%PYTHON_LARA%" gui.py

if errorlevel 1 (
    echo.
    echo Hubo un problema al iniciar LARA. Revisa el mensaje de arriba.
    pause
)
