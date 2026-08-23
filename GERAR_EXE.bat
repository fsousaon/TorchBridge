@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  call INSTALAR.bat
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if errorlevel 1 goto falha
.venv\Scripts\pyinstaller.exe --noconfirm --clean TorchBridge.spec
if errorlevel 1 goto falha
echo.
echo Executavel criado em dist\TorchBridge\TorchBridge.exe
pause
exit /b 0

:falha
echo Falha ao gerar o executavel.
pause
exit /b 1
