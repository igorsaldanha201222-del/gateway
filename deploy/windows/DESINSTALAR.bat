@echo off
rem Remove o Gateway Grid Co. Preserva configuracao, banco e logs.

title Desinstalar Gateway Grid Co
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Pedindo permissao de administrador...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    exit /b
)

cd /d "%~dp0"
echo ============================================
echo   Gateway Grid Co - remocao
echo ============================================
echo.
echo A configuracao da usina e o banco NAO serao apagados.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0desinstalar.ps1"
echo.
pause
