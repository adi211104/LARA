@echo off
REM ============================================================
REM  Instalacion reproducible de LARA (auditoria A-04).
REM  - Crea el entorno virtual .venv si no existe.
REM  - Instala las versiones EXACTAS de requirements.lock.
REM  - Ejecuta las pruebas de seguridad antes de dar el OK.
REM  Es idempotente: puedes repetirlo sin romper nada.
REM  INICIAR_LARA.bat ya NO instala paquetes en cada arranque.
REM ============================================================
title LARA - Instalacion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creando entorno virtual .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo No se pudo crear el entorno virtual. Instala Python 3.12 o superior y agregalo al PATH.
        pause
        exit /b 1
    )
)

echo Instalando dependencias fijadas en requirements.lock ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.lock
if errorlevel 1 (
    echo La instalacion de dependencias fallo. Revisa el mensaje de arriba.
    pause
    exit /b 1
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Se creo .env a partir de .env.example. Completa solo las claves que vayas a usar.
)

echo Ejecutando pruebas de seguridad ...
".venv\Scripts\python.exe" -m unittest discover -s tests
if errorlevel 1 (
    echo Las pruebas fallaron: no uses LARA hasta revisarlo.
    pause
    exit /b 1
)

echo.
echo Instalacion completa. Abre LARA con INICIAR_LARA.bat
pause
