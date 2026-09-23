# Gera a autoridade certificadora da Grid Co e o certificado do broker.
#
#   .\gerar-certificados.ps1                      # para testar local
#   .\gerar-certificados.ps1 -Host app.gridco.com.br -Dias 825
#
# O que sai:
#   ca.crt   autoridade - vai para TODOS os gateways, pode ir no repositorio
#   ca.key   chave da autoridade - NUNCA sai daqui, nunca vai para o git
#   broker.crt / broker.key    ficam so' no servidor do broker
#
# Por que CA propria e nao Let's Encrypt: o gateway fala com um broker, nao com
# um site. CA propria nao depende de porta 80 aberta nem de renovacao a cada 90
# dias em 200 pontos, e o gateway confia so' nela - o que e' mais restrito, e
# nao menos, que confiar em qualquer CA publica.

[CmdletBinding()]
param(
    [string]$Endereco = "localhost",
    [string[]]$OutrosNomes = @(),
    [int]$Dias = 825,
    [string]$Saida = "$PSScriptRoot\certificados",
    [string]$OpenSSL = "C:\Program Files\Git\usr\bin\openssl.exe"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $OpenSSL)) { throw "openssl nao encontrado em $OpenSSL (vem com o Git para Windows)" }

# O openssl escreve mensagem normal no stderr ("self-signature ok"), e com
# ErrorActionPreference=Stop o PowerShell trataria isso como falha. Por isso
# cada chamada passa por aqui, que julga pelo codigo de saida.
# Operador de chamada, nao Start-Process: o caminho do projeto tem espaco e o
# -ArgumentList quebraria os argumentos no meio.
function Rodar-OpenSSL {
    param([string[]]$Argumentos)
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saida = & $OpenSSL @Argumentos 2>&1
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $anterior
    if ($codigo -ne 0) { throw "openssl falhou ($codigo): $($saida -join ' ')" }
}

if (-not $Saida -or $Saida -eq "\certificados") { $Saida = Join-Path (Get-Location) "certificados" }
if (-not (Test-Path $Saida)) { New-Item -ItemType Directory -Force -Path $Saida | Out-Null }
$Saida = (Resolve-Path $Saida).Path

$ca    = Join-Path $Saida "ca"
$brk   = Join-Path $Saida "broker"
$conf  = Join-Path $Saida "openssl-broker.cnf"

# --- 1. autoridade ---------------------------------------------------------
if (Test-Path "$ca.key") {
    Write-Host "CA ja existe; reaproveitando $ca.crt"
} else {
    # keyUsage e basicConstraints EXPLICITOS: o OpenSSL 3.5, que e o que o
    # Python 3.13+ usa, recusa CA sem eles com
    # "CA cert does not include key usage extension".
    $confCa = Join-Path $Saida "openssl-ca.cnf"
    @"
[req]
distinguished_name = dn
x509_extensions    = ext
prompt             = no
[dn]
C  = BR
O  = Grid Co
CN = Grid Co Gateway CA
[ext]
basicConstraints     = critical, CA:TRUE
keyUsage             = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
"@ | Set-Content -LiteralPath $confCa -Encoding ascii

    Rodar-OpenSSL @("genrsa", "-out", "$ca.key", "4096")
    Rodar-OpenSSL @("req", "-x509", "-new", "-nodes", "-key", "$ca.key", "-sha256",
                    "-days", "3650", "-out", "$ca.crt", "-config", $confCa)
    Write-Host "CA criada: $ca.crt (valida 10 anos)"
}

# --- 2. certificado do broker ---------------------------------------------
# subjectAltName e obrigatorio: cliente moderno ignora o CN.
$alt = @("DNS:$Endereco")
foreach ($n in $OutrosNomes) { $alt += "DNS:$n" }
if ($Endereco -eq "localhost") { $alt += "IP:127.0.0.1" }
if ($Endereco -as [ipaddress]) { $alt = @("IP:$Endereco") }

@"
[req]
distinguished_name = dn
req_extensions     = ext
prompt             = no
[dn]
C  = BR
O  = Grid Co
CN = $Endereco
[ext]
subjectAltName   = $($alt -join ", ")
basicConstraints = CA:FALSE
keyUsage         = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
"@ | Set-Content -LiteralPath $conf -Encoding ascii

Rodar-OpenSSL @("genrsa", "-out", "$brk.key", "2048")
Rodar-OpenSSL @("req", "-new", "-key", "$brk.key", "-out", "$brk.csr", "-config", $conf)
Rodar-OpenSSL @("x509", "-req", "-in", "$brk.csr", "-CA", "$ca.crt", "-CAkey", "$ca.key",
                "-CAcreateserial", "-out", "$brk.crt", "-days", "$Dias", "-sha256",
                "-extfile", $conf, "-extensions", "ext")
Remove-Item "$brk.csr" -Force -ErrorAction SilentlyContinue

Write-Host "Broker: $brk.crt"
Write-Host "  nomes aceitos: $($alt -join ', ')"
Write-Host "  validade: $Dias dias"
Write-Host ""
& $OpenSSL x509 -in "$brk.crt" -noout -subject -issuer -dates -ext subjectAltName 2>&1 |
    ForEach-Object { "  $_" }
Write-Host ""
Write-Host "PARA O GATEWAY  : ca.crt"
Write-Host "PARA O SERVIDOR : broker.crt + broker.key"
Write-Host "NUNCA SAI DAQUI : ca.key"
