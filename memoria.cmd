@echo off
setlocal
if /I "%~1"=="start" goto run
if /I "%~1"=="stop" goto run
if /I "%~1"=="status" goto run
echo Uso: memoria start ^| stop ^| status
exit /b 2
:run
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Python do projeto nao encontrado em .venv\Scripts\python.exe.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\personal.py" %1
exit /b %errorlevel%
