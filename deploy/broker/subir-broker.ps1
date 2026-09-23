# Sobe um broker Mosquitto local, com TLS, para validar tudo antes do servidor.
#
#   .\subir-broker.ps1                 # sobe em primeiro plano; Ctrl+C encerra
#   .\subir-broker.ps1 -Parar
#
# Cria as senhas na primeira execucao e mostra quais sao. Sao senhas de TESTE:
# no servidor, gere outras com mosquitto_passwd.

[CmdletBinding()]
param(
    [switch]$Parar,
    [string]$Base = $PSScriptRoot,
    [string]$Mosquitto = "C:\Program Files\mosquitto\mosquitto.exe"
)

$ErrorActionPreference = "Stop"
if (-not $Base -or $Base -eq "") { $Base = (Get-Location).Path }
$Base = (Resolve-Path $Base).Path

if ($Parar) {
    Get-Process mosquitto -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Host "Broker encerrado."
    return
}

if (-not (Test-Path $Mosquitto)) { throw "mosquitto nao encontrado em $Mosquitto" }
$passwd = Join-Path (Split-Path $Mosquitto) "mosquitto_passwd.exe"

$certs = Join-Path $Base "certificados"
if (-not (Test-Path (Join-Path $certs "broker.crt"))) {
    throw "Certificados ausentes. Rode antes: .\gerar-certificados.ps1"
}

$dados = Join-Path $Base "dados"
if (-not (Test-Path $dados)) { New-Item -ItemType Directory -Force -Path $dados | Out-Null }

# --- senhas de teste -------------------------------------------------------
$senhas = Join-Path $Base "senhas.txt"
$arquivoSegredo = Join-Path $Base "senhas-de-teste.txt"
if (-not (Test-Path $senhas)) {
    $gerar = {
        -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
    }
    $conta = @{ gateway = & $gerar; servidor = & $gerar; observador = & $gerar }
    $primeiro = $true
    foreach ($u in $conta.Keys) {
        if ($primeiro) { & $passwd -c -b $senhas $u $conta[$u] | Out-Null; $primeiro = $false }
        else { & $passwd -b $senhas $u $conta[$u] | Out-Null }
    }
    # Guardado em claro porque sao credenciais de TESTE, locais, e alguem
    # precisa le-las para configurar o gateway. No servidor nao se faz isso.
    ($conta.Keys | ForEach-Object { "$_=$($conta[$_])" }) | Set-Content $arquivoSegredo -Encoding ascii
    Write-Host "Senhas de teste criadas em: $arquivoSegredo"
}
Get-Content $arquivoSegredo | ForEach-Object { "  $_" }

# --- config com os caminhos desta maquina ---------------------------------
$confOrigem = Join-Path $Base "mosquitto.conf"
$confUso = Join-Path $dados "mosquitto-em-uso.conf"
$texto = (Get-Content -LiteralPath $confOrigem -Raw -Encoding UTF8).Replace("__BASE__", $Base.Replace("\", "/"))
# SEM BOM: Set-Content -Encoding UTF8 no PowerShell 5.1 grava BOM, e o
# mosquitto para na primeira linha com "Unknown configuration variable".
[IO.File]::WriteAllText($confUso, $texto, (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "Broker subindo:"
Write-Host "  1883  MQTT limpo"
Write-Host "  8883  MQTT sobre TLS"
Write-Host "  config: $confUso"
Write-Host "  log   : $dados\mosquitto.log"
Write-Host ""
Write-Host "Ctrl+C encerra."
Write-Host ""
& $Mosquitto -c $confUso -v
