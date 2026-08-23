@echo off
setlocal
cd /d "%~dp0"
echo.
echo TORCHBRIDGE - INSTALACAO
echo.
where py >nul 2>nul
if errorlevel 1 goto sem_python
py -3 -m venv .venv
if errorlevel 1 goto falha
call .venv\Scripts\activate.bat
if errorlevel 1 goto falha
python -m pip install --upgrade pip
if errorlevel 1 goto falha
python -m pip install -e .
if errorlevel 1 goto falha
echo.
echo Instalacao concluida. Use INICIAR_TORCHBRIDGE.bat.
pause
exit /b 0

:sem_python
echo Python 3 nao foi encontrado.
echo Instale em https://www.python.org/downloads/windows/ e marque "Add Python to PATH".
pause
exit /b 1

:falha
echo.
echo A instalacao nao foi concluida. Verifique a conexao e tente novamente.
pause
exit /b 1
