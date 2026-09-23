@echo off
rem Atualiza o gateway agora, sem esperar a tarefa das 03:00.
rem Dois cliques. Pede elevacao sozinho e ja' sabe o repositorio.
rem
rem Duplo clique num .ps1 abre o Notepad em vez de executar - por isso este
rem arquivo existe.

rem ===========================================================
set "REPO=igorsaldanha201222-del/gateway"
set "CANAL=estavel"
rem ===========================================================

title Atualizar Gateway Grid Co
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Pedindo permissao de administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo.
        echo Nao foi possivel elevar. Clique com o botao direito neste arquivo
        echo e escolha "Executar como administrador".
        echo.
        pause
    )
    exit /b
)

set "SCRIPT=C:\Program Files\Grid Co\Gateway\atualizar.ps1"
set "EXE=C:\Program Files\Grid Co\Gateway\gridco-gateway.exe"
if not exist "%SCRIPT%" set "SCRIPT=%~dp0atualizar.ps1"

echo ============================================
echo   Gateway Grid Co - atualizacao
echo ============================================
echo Repositorio: %REPO%  (canal %CANAL%)
echo.
if exist "%EXE%" (
    echo Versao atual:
    "%EXE%" --build
    echo.
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Repo "%REPO%" -Canal %CANAL% %*

echo.
if exist "%EXE%" (
    echo Versao depois:
    "%EXE%" --build
)
echo.
echo Log completo em:
echo   C:\ProgramData\GridCo\Gateway\data\logs\atualizacao.log
echo.
pause
