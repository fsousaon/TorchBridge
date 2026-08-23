@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo O TorchBridge ainda nao foi instalado.
  call INSTALAR.bat
  if errorlevel 1 exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m torchbridge
exit /b 0
