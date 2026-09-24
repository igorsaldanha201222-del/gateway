# Fecha o ciclo de uma release: baixa os binarios publicados, confere o
# SHA-256, refaz a pasta entrega\ e monta o zip da usina com o certificado.
#
#   .\atualizar-pacote.ps1                      # pega a ultima release
#   .\atualizar-pacote.ps1 -Tag v1.2.1
#   .\atualizar-pacote.ps1 -Usina acopiara -Nome "UFV Acopiara"
#
# Rodar isto ao fim de TODA release. Um zip atrasado e' pior que zip nenhum:
# instala calado uma versao velha, e a diferenca so' aparece quando alguem
# compara o commit no rodape do console.

[CmdletBinding()]
param(
    [string]$Tag,
    [string]$Usina = "gateway",
    [string]$Nome = "Grid Co",
    [string]$Repo = "igorsaldanha201222-del/gateway"
)

$ErrorActionPreference = "Stop"
$broker = $PSScriptRoot
$raiz = Split-Path -Parent (Split-Path -Parent $broker)
$entrega = Join-Path $raiz "entrega"
$cabecalhos = @{ "User-Agent" = "gridco" }

if (-not $Tag) {
    $rel = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers $cabecalhos
    $Tag = $rel.tag_name
}
Write-Host "Release: $Tag"

$base = "https://github.com/$Repo/releases/download/$Tag"
if (-not (Test-Path $entrega)) { New-Item -ItemType Directory -Force -Path $entrega | Out-Null }

# O console pode estar aberto e segurando o arquivo.
Get-Process gridco-console -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

foreach ($nome in @("gridco-gateway.exe", "gridco-console.exe", "atualizar.ps1")) {
    Invoke-WebRequest "$base/$nome" -OutFile (Join-Path $entrega $nome) -UseBasicParsing
    $tmp = Join-Path $entrega "_sha.tmp"
    Invoke-WebRequest "$base/$nome.sha256" -OutFile $tmp -UseBasicParsing
    $esperado = ((Get-Content $tmp -Raw) -split '\s+')[0].ToLower()
    Remove-Item $tmp -Force
    $obtido = (Get-FileHash (Join-Path $entrega $nome) -Algorithm SHA256).Hash.ToLower()
    if ($esperado -ne $obtido) { throw "SHA-256 de $nome nao confere. Pacote NAO foi montado." }
    Write-Host ("  {0,-22} sha-256 confere" -f $nome)
}

Copy-Item (Join-Path $raiz "config\gateway.json") (Join-Path $entrega "gateway.json") -Force
foreach ($nome in @("instalar.ps1", "desinstalar.ps1", "INSTALAR.bat", "ATUALIZAR.bat", "DESINSTALAR.bat")) {
    Copy-Item (Join-Path $raiz "deploy\windows\$nome") (Join-Path $entrega $nome) -Force
}

& (Join-Path $broker "preparar-usina.ps1") -Usina $Usina -Nome $Nome | Out-Null
$zip = Join-Path $raiz "..\gridco-gateway-usina.zip"
Copy-Item (Join-Path $broker "pacotes\gridco-gateway-$Usina.zip") $zip -Force
$zip = (Resolve-Path $zip).Path

# Confere pelo binario, nao pela data do arquivo: zip copiado leva a data da
# copia, e arquivo que nao mudou continua com data velha. So' o proprio .exe
# diz a verdade.
$prova = Join-Path $env:TEMP ("gridco-prova-" + [guid]::NewGuid().ToString("N"))
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::ExtractToDirectory($zip, $prova)
$carimbo = & (Join-Path $prova "gridco-gateway.exe") --build
Remove-Item $prova -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Pacote: $zip"
Write-Host "  o .exe de dentro diz: $carimbo"
if ($carimbo -notmatch [regex]::Escape($Tag)) {
    throw "O binario dentro do zip nao e' da $Tag. Nao leve este pacote para campo."
}
Write-Host "  confere com a release $Tag."
