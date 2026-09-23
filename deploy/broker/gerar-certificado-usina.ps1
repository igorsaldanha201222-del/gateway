# Emite o certificado de UMA usina, assinado pela CA da Grid Co.
#
#   .\gerar-certificado-usina.ps1 -Usina acopiara
#   .\gerar-certificado-usina.ps1 -Usina pedra_branca -Dias 1825
#
# O que sai, em certificados\usinas\<usina>\:
#   ca-gridco.crt   a autoridade  (identica para todas, ja vai dentro do .exe)
#   usina.crt       o certificado desta usina
#   usina.key       a chave privada desta usina  <- so' dela, nunca de outra
#
# O CN do certificado e o slug da usina, e o mosquitto.conf usa
# use_identity_as_username: o broker passa a enxergar o CN como se fosse o nome
# de usuario. Com isso a ACL separa usina por usina sem cadastrar senha nenhuma,
# e um certificado vazado de uma usina nao da acesso ao topico das outras.
#
# E o mesmo modelo do IOT2050 V3, que provisionava CA + certificado + chave no
# cofre do equipamento. A diferenca e que aqui o cofre e o ProgramData com ACL
# restrita, e quem importa e o console.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Usina,
    [int]$Dias = 1825,
    [string]$Saida = "$PSScriptRoot\certificados",
    [string]$OpenSSL = "C:\Program Files\Git\usr\bin\openssl.exe"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $OpenSSL)) { throw "openssl nao encontrado em $OpenSSL (vem com o Git para Windows)" }

# Mesmo motivo do gerar-certificados.ps1: o openssl fala no stderr mesmo quando
# da certo, entao quem julga e o codigo de saida.
function Rodar-OpenSSL {
    param([string[]]$Argumentos)
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $saida = & $OpenSSL @Argumentos 2>&1
    $codigo = $LASTEXITCODE
    $ErrorActionPreference = $anterior
    if ($codigo -ne 0) { throw "openssl falhou ($codigo): $($saida -join ' ')" }
}

# O slug vira CN e o CN vira usuario no broker: precisa ser o MESMO texto que o
# topic_slug do gateway.json, senao a ACL nega o proprio dono.
if ($Usina -notmatch '^[a-z0-9_]+$') {
    throw "slug invalido '$Usina': use so' minusculas, numeros e _ (igual ao topic_slug do gateway.json)"
}

$Saida = (Resolve-Path $Saida -ErrorAction SilentlyContinue).Path
if (-not $Saida) { throw "pasta de certificados nao existe; rode gerar-certificados.ps1 antes" }

$ca = Join-Path $Saida "ca"
if (-not (Test-Path "$ca.key")) { throw "CA nao encontrada em $ca.key; rode gerar-certificados.ps1 antes" }

$destino = Join-Path (Join-Path $Saida "usinas") $Usina
New-Item -ItemType Directory -Force -Path $destino | Out-Null
$base = Join-Path $destino "usina"
$conf = Join-Path $destino "openssl.cnf"

# clientAuth, nao serverAuth: este certificado prova quem CONECTA. Sem
# subjectAltName de proposito - ninguem verifica nome de host num cliente.
@"
[req]
distinguished_name = dn
req_extensions     = ext
prompt             = no
[dn]
C  = BR
O  = Grid Co
OU = Usinas
CN = $Usina
[ext]
basicConstraints = CA:FALSE
keyUsage         = critical, digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
"@ | Set-Content -LiteralPath $conf -Encoding ascii

Rodar-OpenSSL @("genrsa", "-out", "$base.key", "2048")
Rodar-OpenSSL @("req", "-new", "-key", "$base.key", "-out", "$base.csr", "-config", $conf)
Rodar-OpenSSL @("x509", "-req", "-in", "$base.csr", "-CA", "$ca.crt", "-CAkey", "$ca.key",
                "-CAcreateserial", "-out", "$base.crt", "-days", "$Dias", "-sha256",
                "-extfile", $conf, "-extensions", "ext")
Remove-Item "$base.csr" -Force -ErrorAction SilentlyContinue

Copy-Item "$ca.crt" (Join-Path $destino "ca-gridco.crt") -Force

Write-Host ""
Write-Host "Usina '$Usina' emitida em $destino"
& $OpenSSL x509 -in "$base.crt" -noout -subject -issuer -dates -ext extendedKeyUsage 2>&1 |
    ForEach-Object { "  $_" }
Write-Host ""
Write-Host "LEVAR PARA A USINA : usina.crt + usina.key  (importar pelo console, aba Broker)"
Write-Host "NAO PRECISA LEVAR  : ca-gridco.crt ja' esta dentro do .exe"
Write-Host "NUNCA SAI DAQUI    : ca.key"
Write-Host ""
Write-Host "Acrescente a linha correspondente em acl.txt (user $Usina)."
