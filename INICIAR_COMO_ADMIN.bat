@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo Execute INSTALAR.bat primeiro.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%CD%\.venv\Scripts\pythonw.exe' -ArgumentList '-m torchbridge' -WorkingDirectory '%CD%' -Verb RunAs"
exit /b 0
