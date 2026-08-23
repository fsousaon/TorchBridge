@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Execute INSTALAR.bat primeiro.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m torchbridge.calibrate
pause
