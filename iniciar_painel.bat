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
echo Mantenha esta janela aberta enquanto usar o painel.
echo Abra http://127.0.0.1:8050 no navegador.
"%PANEL_PYTHON%" "%~dp0painel_historico_device096.py"
pause
