# Gera dist\gridco-gateway.exe a partir do codigo do repositorio.
# Uso, a partir de qualquer lugar:  .\deploy\windows\build.ps1
#
# Nao instala nada. Quem instala e' instalar.ps1, que roda como administrador.

[CmdletBinding()]
param(
    [switch]$PularTestes
)

$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $raiz
Write-Host "Projeto: $raiz"

# 'python' no PATH costuma ser o stub da Microsoft Store, que falha calado.
$py = "py"
$argsPy = @("-3")
try { & $py -3 --version | Out-Null } catch { $py = "python"; $argsPy = @() }
$versao = & $py @argsPy --version
Write-Host "Python : $versao"

if (-not $PularTestes) {
    Write-Host ""
    Write-Host "== testes =="
    $env:PYTHONPATH = $raiz
    & $py @argsPy -m unittest discover -s tests
    if ($LASTEXITCODE -ne 0) { throw "Os testes falharam. Nada foi empacotado." }

    Write-Host ""
    Write-Host "== validacao da configuracao base =="
    & $py @argsPy -m gridco_gateway --config config\gateway.json --validate
    if ($LASTEXITCODE -ne 0) { throw "config\gateway.json invalida. Nada foi empacotado." }
}

Write-Host ""
Write-Host "== PyInstaller =="
& $py @argsPy -m PyInstaller "deploy\windows\gridco-gateway.spec" --noconfirm --clean --distpath dist --workpath build\pyinstaller
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falhou." }

$exe = Join-Path $raiz "dist\gridco-gateway.exe"
if (-not (Test-Path $exe)) { throw "PyInstaller terminou mas $exe nao existe." }

# Prova de vida: o binario tem que responder fora do ambiente Python.
Write-Host ""
Write-Host "== conferindo o executavel =="
$saida = & $exe --version
Write-Host "  --version -> $saida"
& $exe --config "config\gateway.json" --validate
if ($LASTEXITCODE -ne 0) { throw "O executavel nao validou a configuracao base." }

$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "OK: $exe  ($mb MB)"
Write-Host "Proximo passo, como administrador:  .\deploy\windows\instalar.ps1"
