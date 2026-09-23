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
    [switch]$IniciarAquisicao,
    # Atualizacao automatica: informe o repositorio para ligar.
    [string]$Repo,
    [ValidateSet("estavel", "teste")][string]$Canal = "estavel",
    [string]$HoraAtualizacao = "03:00",
    [int]$DispersaoMinutos   = 120,
    [string]$TarefaAtualizacao = "GridCo Gateway - atualizacao"
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
Write-Host "Servico copiado."

# O console tambem vai para Program Files: e' o unico lugar previsivel para o
# atualizador encontrar e manter em dia. Rodando de uma pasta qualquer, ele
# ficaria para tras enquanto o servico avanca.
$exeConsole = Join-Path (Split-Path -Parent $Exe) "gridco-console.exe"
if (-not (Test-Path $exeConsole)) { $exeConsole = Join-Path $PSScriptRoot "gridco-console.exe" }
$alvoConsole = Join-Path $DestinoPrograma "gridco-console.exe"
if (Test-Path $exeConsole) {
    Copy-Item $exeConsole $alvoConsole -Force
    Write-Host "Console copiado."
    # Atalho no Menu Iniciar e na Area de Trabalho, para nao depender de
    # alguem lembrar onde o zip foi extraido.
    # CommonPrograms, nao CommonStartMenu + "Programas": em disco a pasta se
    # chama "Programs" mesmo em Windows em portugues.
    $shell = New-Object -ComObject WScript.Shell
    $criados = 0
    foreach ($pasta in @([Environment]::GetFolderPath("CommonPrograms"),
                         [Environment]::GetFolderPath("CommonDesktopDirectory"))) {
        if (-not $pasta -or -not (Test-Path $pasta)) { continue }
        try {
            $lnk = $shell.CreateShortcut((Join-Path $pasta "Gateway Grid Co.lnk"))
            $lnk.TargetPath = $alvoConsole
            $lnk.WorkingDirectory = $DestinoPrograma
            $lnk.IconLocation = $alvoConsole
            $lnk.Description = "Console do Gateway Grid Co"
            $lnk.Save()
            $criados++
        } catch {
            Write-Host "  aviso: nao criei o atalho em $pasta ($($_.Exception.Message))"
        }
    }
    Write-Host "Atalhos criados: $criados (Menu Iniciar e Area de Trabalho)."
} else {
    Write-Host "AVISO: gridco-console.exe nao encontrado ao lado do instalador."
    Write-Host "  O servico funciona, mas nao havera console nem atualizacao dele."
}

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
# --- 9. atualizacao automatica --------------------------------------------
$alvoAtualizar = Join-Path $DestinoPrograma "atualizar.ps1"
Copy-Item (Join-Path $PSScriptRoot "atualizar.ps1") $alvoAtualizar -Force -ErrorAction SilentlyContinue

if (Get-ScheduledTask -TaskName $TarefaAtualizacao -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TarefaAtualizacao -Confirm:$false
}

if ($Repo) {
    # Dispersao: 200 PCs acordando no mesmo minuto batem no GitHub juntos e
    # saturam o link da usina. Cada um sorteia o proprio atraso.
    $argumentos = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" -Repo "{1}" -Canal {2} ' +
                   '-Automatico -EsperaMaxSegundos {3}') -f
                   $alvoAtualizar, $Repo, $Canal, ($DispersaoMinutos * 60)
    $acaoAt = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argumentos `
        -WorkingDirectory $DestinoPrograma
    $gatilhoAt = New-ScheduledTaskTrigger -Daily -At $HoraAtualizacao
    $contaAt = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $opcoesAt = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TarefaAtualizacao -Action $acaoAt -Trigger $gatilhoAt `
        -Principal $contaAt -Settings $opcoesAt `
        -Description "Busca release novo de $Repo (canal $Canal), confere SHA-256 e desfaz sozinho se o servico nao voltar." | Out-Null
    Write-Host ""
    Write-Host "Atualizacao automatica: diaria as $HoraAtualizacao, canal $Canal,"
    Write-Host "  espalhada em ate $DispersaoMinutos min, de $Repo."
} else {
    Write-Host ""
    Write-Host "Atualizacao automatica NAO ligada. Para ligar, reinstale com:"
    Write-Host "  .\instalar.ps1 -Repo ""<org>/<repo>"""
}

Write-Host ""
Write-Host "Log            : $dirDados\logs\gateway.log"
Write-Host "Log atualizacao: $dirDados\logs\atualizacao.log"
Write-Host "Banco          : $dirDados\gateway.db"
Write-Host "Configuracao   : $alvoConf"
Write-Host ""
Write-Host "Conferir:   Get-Process gridco-gateway"
Write-Host "Log ao vivo: Get-Content '$dirDados\logs\gateway.log' -Wait -Tail 20"
