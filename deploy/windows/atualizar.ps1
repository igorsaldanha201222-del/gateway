# Atualiza o gateway a partir de um release do GitHub.
#
# Uso, num PowerShell COMO ADMINISTRADOR, no PC da usina:
#     .\atualizar.ps1 -Repo "sua-org/gridco-gateway"
#
# Fluxo: le a versao instalada, consulta o release mais recente, e so' troca o
# binario se a versao for diferente e o SHA-256 conferir. Guarda o anterior
# para rollback.
#
# Repositorio privado: exporte um token de leitura antes de rodar, com
#     $env:GRIDCO_GITHUB_TOKEN = "<token>"
# O script nunca grava o token em disco.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Tag,
    [string]$DestinoPrograma = "C:\Program Files\Grid Co\Gateway",
    [string]$Tarefa          = "GridCo Gateway",
    [string]$Ativo           = "gridco-gateway.exe",
    [switch]$Forcar,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Rode este script num PowerShell aberto como administrador." }

$alvo    = Join-Path $DestinoPrograma $Ativo
$backup  = Join-Path $DestinoPrograma "$Ativo.anterior"
if (-not (Test-Path $alvo)) { throw "Gateway nao instalado em $alvo. Rode instalar.ps1 antes." }

function Reiniciar-Tarefa {
    if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName $Tarefa
        Start-Sleep -Seconds 3
        $p = Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue
        if ($p) { Write-Host "Gateway no ar: pid $($p.Id)" }
        else { Write-Warning "A tarefa foi iniciada mas o processo nao aparece. Confira o log." }
    }
}

# ---------- rollback ----------
if ($Rollback) {
    if (-not (Test-Path $backup)) { throw "Nao ha versao anterior guardada em $backup" }
    Write-Host "Voltando para a versao anterior..."
    try { Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue } catch {}
    Start-Sleep -Seconds 2
    Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1
    $atual = Join-Path $DestinoPrograma "$Ativo.revertido"
    Move-Item $alvo $atual -Force
    Move-Item $backup $alvo -Force
    Move-Item $atual $backup -Force
    Write-Host "Rollback aplicado: $(& $alvo --version)"
    Reiniciar-Tarefa
    return
}

# ---------- versao instalada ----------
$instalada = (& $alvo --version).Trim()
Write-Host "Instalada : $instalada"

# ---------- release ----------
$cabecalhos = @{ "User-Agent" = "gridco-gateway-updater"; "Accept" = "application/vnd.github+json" }
if ($env:GRIDCO_GITHUB_TOKEN) { $cabecalhos["Authorization"] = "Bearer $env:GRIDCO_GITHUB_TOKEN" }

if ($Tag) { $url = "https://api.github.com/repos/$Repo/releases/tags/$Tag" }
else      { $url = "https://api.github.com/repos/$Repo/releases/latest" }

try {
    $release = Invoke-RestMethod -Uri $url -Headers $cabecalhos
} catch {
    throw "Nao consegui consultar $url : $($_.Exception.Message)"
}
$publicada = ($release.tag_name -replace '^v', '').Trim()
Write-Host "No GitHub : $publicada  ($($release.tag_name))"

if ($publicada -eq $instalada -and -not $Forcar) {
    Write-Host ""
    Write-Host "Ja esta na versao publicada. Nada a fazer. (-Forcar reinstala mesmo assim.)"
    return
}

# ---------- download ----------
$aExe = $release.assets | Where-Object { $_.name -eq $Ativo }
$aSha = $release.assets | Where-Object { $_.name -eq "$Ativo.sha256" }
if (-not $aExe) { throw "O release nao tem o ativo '$Ativo'." }
if (-not $aSha) { throw "O release nao tem '$Ativo.sha256'. Sem soma de verificacao eu nao troco o binario." }

$tmp = Join-Path $env:TEMP ("gridco-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$novoExe = Join-Path $tmp $Ativo
$novoSha = Join-Path $tmp "$Ativo.sha256"

$baixar = $cabecalhos.Clone()
$baixar["Accept"] = "application/octet-stream"
Write-Host "Baixando..."
Invoke-WebRequest -Uri $aExe.url -Headers $baixar -OutFile $novoExe
Invoke-WebRequest -Uri $aSha.url -Headers $baixar -OutFile $novoSha

# ---------- verificacao ----------
$esperado = ((Get-Content $novoSha -Raw) -split '\s+')[0].Trim().ToLower()
$obtido   = (Get-FileHash $novoExe -Algorithm SHA256).Hash.ToLower()
if ($esperado -ne $obtido) {
    Remove-Item $tmp -Recurse -Force
    throw "SHA-256 nao confere. Esperado $esperado, obtido $obtido. O binario NAO foi trocado."
}
Write-Host "SHA-256 confere."

# ---------- troca ----------
Write-Host "Parando o gateway..."
try { Stop-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Seconds 2
Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 2

if (Test-Path $backup) { Remove-Item $backup -Force }
Move-Item $alvo $backup -Force
Copy-Item $novoExe $alvo -Force
Remove-Item $tmp -Recurse -Force

$agora = (& $alvo --version).Trim()
Write-Host "Atualizado: $instalada -> $agora"
Write-Host "Anterior guardado em: $backup  (volte com -Rollback)"

Reiniciar-Tarefa
