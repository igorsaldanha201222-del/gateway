@echo off
rem Instalador de dois cliques do Gateway Grid Co.
rem Pede elevacao sozinho, entra na propria pasta e libera os arquivos vindos
rem de rede. Nao precisa abrir PowerShell nem saber o caminho.

title Instalar Gateway Grid Co
cd /d "%~dp0"

rem --- ja esta elevado? ---
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

cd /d "%~dp0"
echo ============================================
echo   Gateway Grid Co - instalacao
echo ============================================
echo Pasta: %cd%
echo.

if not exist "gridco-gateway.exe" (
    echo ERRO: gridco-gateway.exe nao esta nesta pasta.
    echo Extraia o zip inteiro antes de rodar - nao rode de dentro do zip.
    echo.
    pause
    exit /b 1
)

rem Arquivo vindo de rede ou de zip baixado vem bloqueado pelo Windows.
echo Liberando arquivos...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse | Unblock-File -ErrorAction SilentlyContinue"

echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
set RESULTADO=%errorlevel%

echo.
if %RESULTADO% neq 0 (
    echo ============================================
    echo   FALHOU. Leia a mensagem acima.
    echo ============================================
) else (
    echo ============================================
    echo   PRONTO. O servico esta rodando.
    echo ============================================
    echo.
    if exist "gridco-console.exe" (
        choice /c SN /n /m "Abrir o console agora? [S/N] "
        if errorlevel 2 goto :fim
        echo Abrindo... o console pede permissao de administrador toda vez
        echo que abre, porque ele grava configuracao. Aceite o aviso.
        start "" "%~dp0gridco-console.exe"
    )
)

:fim
echo.
pause
exit /b %RESULTADO%
