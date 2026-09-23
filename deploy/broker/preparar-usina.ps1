# Monta o pacote de UMA usina, pronto para instalar: binarios, configuracao com
# o topico da usina, e o certificado e a chave dela ja' dentro.
#
#   .\preparar-usina.ps1 -Usina acopiara
#   .\preparar-usina.ps1 -Usina pedra_branca -Nome "UFV Pedra Branca"
#
# Sai um .zip em deploy\broker\pacotes\. Na usina: extrair e dar duplo clique em
# INSTALAR.bat. Nao ha certificado para importar a mao nem senha para digitar.
#
# Depois disso, atualizacao de software vai sozinha pelo GitHub. O certificado
# fica no PC e nao e' tocado pelas atualizacoes.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Usina,
    [string]$Nome,
    [string]$Servidor = "app.gridco.com.br",
    [int]$Porta = 8883,
    [string]$Repo = "igorsaldanha201222-del/gateway",
    [int]$Dias = 1825
)

$ErrorActionPreference = "Stop"
$broker = $PSScriptRoot
$raiz = Split-Path -Parent (Split-Path -Parent $broker)
$entrega = Join-Path $raiz "entrega"

if (-not (Test-Path (Join-Path $entrega "gridco-gateway.exe"))) {
    throw "entrega\ sem os binarios. Baixe o release antes."
}

# --- 1. certificado desta usina -------------------------------------------
$destCert = Join-Path (Join-Path (Join-Path $broker "certificados") "usinas") $Usina
if (-not (Test-Path (Join-Path $destCert "usina.key"))) {
    & (Join-Path $broker "gerar-certificado-usina.ps1") -Usina $Usina -Dias $Dias | Out-Null
    Write-Host "Certificado emitido para '$Usina'."
} else {
    Write-Host "Certificado de '$Usina' ja existia; reaproveitado."
}

# --- 2. pasta do pacote ----------------------------------------------------
$pacotes = Join-Path $broker "pacotes"
$pasta = Join-Path $pacotes $Usina
if (Test-Path $pasta) { Remove-Item $pasta -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pasta | Out-Null

Copy-Item (Join-Path $entrega "*") $pasta -Recurse -Force
Copy-Item (Join-Path $destCert "usina.crt") $pasta -Force
Copy-Item (Join-Path $destCert "usina.key") $pasta -Force

# --- 3. configuracao com o nome e o topico desta usina --------------------
# O topic_slug TEM de ser igual ao CN do certificado: e' ele que a ACL do
# broker usa para prender cada usina ao proprio ramo do topico.
$conf = Join-Path $pasta "gateway.json"
$json = Get-Content $conf -Raw | ConvertFrom-Json
$json.plant.id = $Usina
$json.plant.name = if ($Nome) { $Nome } else { $Usina }
$json.plant.metadata.topic_slug = $Usina
$json.general.plant_id = $Usina
$json.general.command_subscribe_filter = "dev/write/UFV/$Usina/+/+"
$json.general.command_feedback_topic   = "dev/write/UFV/$Usina/feedback"
$json.general.v3_configuration_topic   = "dev/write/UFV/$Usina/gateway/configuration/v3/set"
$json.general.v3_status_topic          = "dev/read/UFV/$Usina/gateway/status"
$json.mqtt.host = $Servidor
$json.mqtt.port = $Porta
$json.mqtt.client_id = "GRIDCO-$($Usina.ToUpper())"
$json.mqtt.tls.enabled = $true
$json.mqtt.tls.server_hostname = $Servidor
$json | ConvertTo-Json -Depth 40 | Out-File -FilePath $conf -Encoding utf8

# --- 4. INSTALAR.bat ja' com o repositorio da atualizacao ----------------
$bat = Join-Path $pasta "INSTALAR.bat"
if (Test-Path $bat) {
    $texto = Get-Content $bat -Raw
    if ($texto -notmatch [regex]::Escape($Repo)) {
        Write-Host "AVISO: INSTALAR.bat nao menciona $Repo; confira o parametro -Repo dele."
    }
}

# --- 5. zip ----------------------------------------------------------------
$zip = Join-Path $pacotes "gridco-gateway-$Usina.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $pasta "*") -DestinationPath $zip -CompressionLevel Optimal

Write-Host ""
Write-Host "Pacote pronto: $zip"
Write-Host "  usina    : $Usina"
Write-Host "  servidor : ${Servidor}:${Porta}"
Write-Host "  topico   : dev/read/UFV/$Usina/<tipo>/<indice>"
Write-Host ""
Write-Host "Na usina: extrair e dar duplo clique em INSTALAR.bat. Nada mais."
Write-Host ""
Write-Host "Este zip contem a CHAVE PRIVADA da usina. Nao publique, nao mande em"
Write-Host "grupo. Um por usina, e cada um so' serve para a sua."
