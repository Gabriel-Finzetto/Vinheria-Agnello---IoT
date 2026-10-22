@echo off
setlocal
cd /d "%~dp0"
set "PANEL_PYTHON=%~dp0.venv\Scripts\python.exe"
if exist "%PANEL_PYTHON%" goto instalar
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m venv "%~dp0.venv"
) else (
    python -m venv "%~dp0.venv"
)
if not exist "%PANEL_PYTHON%" (
    echo Instale o Python 3.10 ou superior e execute este arquivo novamente.
    pause
    exit /b 1
)

:instalar
"%PANEL_PYTHON%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo Nao foi possivel instalar as bibliotecas. Confira a mensagem acima.
    pause
    exit /b 1
)

:executar
echo Iniciando painel e adaptador Smart Data Models.
echo Mantenha esta janela aberta e inicie a simulacao no Wokwi.
"%PANEL_PYTHON%" -u "%~dp0iniciar_projeto.py"
pause
