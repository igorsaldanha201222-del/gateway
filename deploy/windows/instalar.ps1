# Instala o Gateway Grid Co como processo de segundo plano que sobe no boot.
#
# Uso, num PowerShell COMO ADMINISTRADOR, a partir da raiz do projeto:
#     .\deploy\windows\instalar.ps1
#
# O que faz:
#   1. copia o .exe para "C:\Program Files\Grid Co\Gateway"
#   2. cria "C:\ProgramData\GridCo\Gateway" com config, dados e log
#   3. registra uma tarefa agendada que roda como SYSTEM no boot, sem janela
#   4. sobe o processo
#
# O que NAO faz, de proposito:
#   - nao sobrescreve uma config que ja exista no PC
#   - nao liga a aquisicao (runtime.enabled). Use -IniciarAquisicao para isso.
#   - nao pede nem grava senha de MQTT. Ver LEIAME.md.

[CmdletBinding()]
param(
    [string]$Exe,
    [string]$Config,
    [string]$DestinoPrograma = "C:\Program Files\Grid Co\Gateway",
    [string]$DestinoDados    = "C:\ProgramData\GridCo\Gateway",
    [string]$Tarefa          = "GridCo Gateway",
    [string]$Servico         = "GridCoGateway",
    [switch]$IniciarAquisicao
)

$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Rode este script num PowerShell aberto como administrador." }

$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Rodando do repositorio, o binario esta em dist\. Numa pasta de entrega, esta
# ao lado deste script. Aceita os dois sem precisar passar parametro.
if (-not $Exe) {
    $Exe = Join-Path $raiz "dist\gridco-gateway.exe"
    if (-not (Test-Path $Exe)) { $Exe = Join-Path $PSScriptRoot "gridco-gateway.exe" }
}
if (-not $Config) {
    $Config = Join-Path $raiz "config\gateway.json"
    if (-not (Test-Path $Config)) { $Config = Join-Path $PSScriptRoot "gateway.json" }
}
if (-not (Test-Path $Exe))    { throw "Executavel nao encontrado: $Exe. Rode antes .\deploy\windows\build.ps1" }
if (-not (Test-Path $Config)) { throw "Configuracao base nao encontrada: $Config" }

$dirConfig = Join-Path $DestinoDados "config"
$dirDados  = Join-Path $DestinoDados "data"
$alvoExe   = Join-Path $DestinoPrograma "gridco-gateway.exe"
$alvoConf  = Join-Path $dirConfig "gateway.json"

Write-Host "Programa : $DestinoPrograma"
Write-Host "Dados    : $DestinoDados"
Write-Host ""

# --- 1. pastas -------------------------------------------------------------
foreach ($d in @($DestinoPrograma, $dirConfig, $dirDados)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

# --- 2. para o que estiver rodando antes de trocar o binario --------------
$existente = Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue
if ($existente) {
    Write-Host "Parando a tarefa agendada da instalacao anterior..."
    try { Stop-ScheduledTask -TaskName $Tarefa -ErrorAction Stop } catch {}
}
$svAntigo = Get-Service -Name $Servico -ErrorAction SilentlyContinue
if ($svAntigo -and $svAntigo.Status -ne "Stopped") {
    Write-Host "Parando o servico existente..."
    Stop-Service -Name $Servico -Force
}
# O bootloader do PyInstaller gera dois processos; matar por nome pega os dois.
Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# --- 3. executavel ---------------------------------------------------------
Copy-Item $Exe $alvoExe -Force
Write-Host "Executavel copiado."

# --- 4. configuracao: nunca sobrescreve a do PC ---------------------------
if (Test-Path $alvoConf) {
    Write-Host "Configuracao ja existe e foi mantida: $alvoConf"
} else {
    Copy-Item $Config $alvoConf
    Write-Host "Configuracao base instalada: $alvoConf"
}

# --- 5. so' registra se a config passar na validacao ----------------------
Write-Host ""
Write-Host "Validando a configuracao instalada..."
& $alvoExe --config $alvoConf --validate
if ($LASTEXITCODE -ne 0) { throw "A configuracao instalada nao passou na validacao. Nada foi registrado." }

# --- 6. aquisicao: decisao explicita, nunca silenciosa --------------------
if ($IniciarAquisicao) {
    $json = Get-Content $alvoConf -Raw | ConvertFrom-Json
    if ($json.runtime.enabled -ne $true) {
        $json.runtime.enabled = $true
        $json | ConvertTo-Json -Depth 40 | Out-File -FilePath $alvoConf -Encoding utf8
        Write-Host "runtime.enabled marcado como true: o gateway vai adquirir assim que subir."
    }
} else {
    Write-Host "runtime.enabled nao foi tocado. O processo sobe e conecta no MQTT, mas nao"
    Write-Host "  adquire ate ser ligado - pela config ou pelo comando remoto de start."
}

# --- 7. servico do Windows ------------------------------------------------
# Aparece em services.msc como "Gateway Grid Co". O handshake com o Service
# Control Manager e' feito em gridco_gateway/winservice.py; sem ele o SCM
# derrubaria o processo em 30 s por achar que travou.

# Migracao: versoes anteriores subiam por tarefa agendada.
if ($existente) {
    Write-Host "Removendo a tarefa agendada da instalacao anterior..."
    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false
}

$jaServico = Get-Service -Name $Servico -ErrorAction SilentlyContinue
if ($jaServico) {
    Write-Host "Servico ja existe; parando e removendo para registrar de novo..."
    if ($jaServico.Status -ne "Stopped") { Stop-Service -Name $Servico -Force }
    & $alvoExe service remove | Out-Null
    Start-Sleep -Seconds 2
}

Write-Host ""
Write-Host "Registrando o servico..."
& $alvoExe service --startup auto install
if ($LASTEXITCODE -ne 0) { throw "Falhou ao registrar o servico." }

# Config e dados por variavel de maquina: o servico roda como LocalSystem e
# nao recebe argumento do SCM.
[Environment]::SetEnvironmentVariable("GRIDCO_CONFIG", $alvoConf, "Machine")
[Environment]::SetEnvironmentVariable("GRIDCO_DATA_DIR", $dirDados, "Machine")

# Recuperacao nativa do SCM: reinicia em 60 s nas tres primeiras falhas e zera
# a contagem a cada 24 h.
& sc.exe failure $Servico reset= 86400 actions= restart/60000/restart/60000/restart/60000 | Out-Null
& sc.exe description $Servico "Gateway Grid Co - le Modbus TCP, guarda em SQLite e publica por MQTT. Sem interface; acompanhe pelo log em $dirDados\logs." | Out-Null

Write-Host "Servico '$Servico' registrado com inicializacao automatica."

# --- 8. sobe agora --------------------------------------------------------
Start-Service -Name $Servico
Start-Sleep -Seconds 4
$sv = Get-Service -Name $Servico
Write-Host ""
Write-Host "Servico        : $($sv.Name)  ($($sv.DisplayName))"
Write-Host "Estado         : $($sv.Status)"
Write-Host "Inicializacao  : $((Get-CimInstance Win32_Service -Filter "Name='$Servico'").StartMode)"
Write-Host ""
Write-Host "Log            : $dirDados\logs\gateway.log"
Write-Host "Banco          : $dirDados\gateway.db"
Write-Host "Configuracao   : $alvoConf"
Write-Host ""
Write-Host "Conferir:   Get-Process gridco-gateway"
Write-Host "Log ao vivo: Get-Content '$dirDados\logs\gateway.log' -Wait -Tail 20"
