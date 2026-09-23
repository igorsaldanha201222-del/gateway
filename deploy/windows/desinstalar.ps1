# Remove o Gateway Grid Co deste PC.
#
# Uso, num PowerShell COMO ADMINISTRADOR:
#     .\deploy\windows\desinstalar.ps1
#
# Por padrao PRESERVA a configuracao, o banco e os logs em
# C:\ProgramData\GridCo\Gateway - sao dados da usina, nao do programa.
# Para apagar tambem, passe -ApagarDados (pede confirmacao).

[CmdletBinding()]
param(
    [string]$DestinoPrograma = "C:\Program Files\Grid Co\Gateway",
    [string]$DestinoDados    = "C:\ProgramData\GridCo\Gateway",
    [string]$Tarefa          = "GridCo Gateway",
    [switch]$ApagarDados
)

$ErrorActionPreference = "Stop"

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Rode este script num PowerShell aberto como administrador." }

if (Get-ScheduledTask -TaskName $Tarefa -ErrorAction SilentlyContinue) {
    Write-Host "Parando e removendo a tarefa '$Tarefa'..."
    try { Stop-ScheduledTask -TaskName $Tarefa -ErrorAction Stop } catch {}
    Start-Sleep -Seconds 2
    Unregister-ScheduledTask -TaskName $Tarefa -Confirm:$false
} else {
    Write-Host "Tarefa '$Tarefa' nao estava registrada."
}

# O processo pode sobreviver ao fim da tarefa; encerra pelo nome.
Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Encerrando processo pid $($_.Id)..."
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

foreach ($pasta in @([Environment]::GetFolderPath("CommonPrograms"),
                     [Environment]::GetFolderPath("CommonDesktopDirectory"))) {
    $lnk = Join-Path $pasta "Gateway Grid Co.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Host "Atalho removido: $lnk" }
}

if (Test-Path $DestinoPrograma) {
    Remove-Item $DestinoPrograma -Recurse -Force
    Write-Host "Programa removido: $DestinoPrograma"
}

if ($ApagarDados) {
    if (Test-Path $DestinoDados) {
        Write-Host ""
        Write-Host "ATENCAO: isto apaga a configuracao da usina, o banco com a fila nao"
        Write-Host "publicada e todo o historico de eventos em:"
        Write-Host "  $DestinoDados"
        $r = Read-Host "Digite APAGAR para confirmar"
        if ($r -ceq "APAGAR") {
            Remove-Item $DestinoDados -Recurse -Force
            Write-Host "Dados apagados."
        } else {
            Write-Host "Nada foi apagado."
        }
    }
} elseif (Test-Path $DestinoDados) {
    Write-Host ""
    Write-Host "Dados PRESERVADOS em: $DestinoDados"
    Write-Host "  Reinstalar por cima reaproveita a configuracao e a fila pendente."
}

Write-Host ""
Write-Host "Desinstalacao concluida."
