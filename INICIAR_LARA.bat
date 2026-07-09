@echo off
REM ============================================================
REM  Lanzador de LARA — interfaz grafica con voz y vision.
REM  Funciona en cualquier PC Windows de Adi: la primera vez
REM  instala las dependencias que falten y luego abre la GUI.
REM ============================================================
title LARA - Asistente del Taller
cd /d "%~dp0"

echo Iniciando LARA...

REM Asegurar pip
python -m pip --version >nul 2>&1 || python -m ensurepip --upgrade

REM Instalar dependencias que falten (rapido si ya estan)
python -m pip install --quiet -r requirements.txt

REM ---- Cerebro HIBRIDO local + nube ------------------------------------
REM Lo simple ("abre chrome", "que hora es") lo resuelve el modelo LOCAL de
REM Ollama en esta PC (rapido, sin limites, sin internet). Lo fuerte
REM (razonamiento tecnico, codigo) y las busquedas van a la nube.
REM Requisitos: tener Ollama abierto y los modelos bajados:
REM     ollama pull qwen2.5:3b-instruct    (decisiones simples)
REM     ollama pull embeddinggemma         (memoria semantica local)
REM Si Ollama no corre, LARA usa solo la nube (no pasa nada). Pon 0 para
REM apagar el cerebro local a proposito.
set LARA_LLM_LOCAL=1
set LARA_OLLAMA_MODEL=qwen2.5:3b-instruct
set LARA_OLLAMA_EMBED=embeddinggemma

REM Abrir la interfaz grafica
python gui.py

if errorlevel 1 (
    echo.
    echo Hubo un problema al iniciar LARA. Revisa el mensaje de arriba.
    pause
)
