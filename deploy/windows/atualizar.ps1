# Atualiza o gateway a partir de um release do GitHub.
#
# Manual, no PC da usina, num PowerShell como administrador:
#     .\atualizar.ps1 -Repo "org/repo"
#
# Automatico: o instalar.ps1 registra uma tarefa que chama este script com
# -Automatico. Ver "Atualizacao automatica" no LEIAME.md.
#
# O que protege uma frota de 200 usinas de um binario ruim:
#   1. SHA-256 conferido antes de trocar;
#   2. o binario anterior e' guardado, e se o servico nao voltar a subir a
#      troca e' DESFEITA sozinha;
#   3. espera aleatoria antes de baixar, para 200 PCs nao caírem juntos;
#   4. canal: por padrao so' aceita release estavel, nunca pre-release.
#
# Repositorio privado: variavel de MAQUINA GRIDCO_GITHUB_TOKEN com um token de
# leitura. O script nunca grava o token em disco.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repo,
    [string]$Tag,
    [ValidateSet("estavel", "teste")][string]$Canal = "estavel",
    [string]$DestinoPrograma = "C:\Program Files\Grid Co\Gateway",
    [string]$DestinoDados    = "C:\ProgramData\GridCo\Gateway",
    [string]$Servico         = "GridCoGateway",
    [string]$Ativo           = "gridco-gateway.exe",
    [int]$EsperaMaxSegundos  = 0,
    [int]$SegundosParaConfirmar = 90,
    [switch]$Automatico,
    [switch]$Forcar,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$pastaLog = Join-Path $DestinoDados "data\logs"
$arquivoLog = Join-Path $pastaLog "atualizacao.log"

function Registrar {
    param([string]$Texto, [string]$Nivel = "INFO")
    $linha = "{0} {1} {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Nivel.PadRight(7), $Texto
    if (-not (Test-Path $pastaLog)) { New-Item -ItemType Directory -Force -Path $pastaLog | Out-Null }
    Add-Content -LiteralPath $arquivoLog -Value $linha -Encoding utf8
    if (-not $Automatico) { Write-Host $linha }
}

function Parar { param([string]$Texto) Registrar $Texto "ERRO"; exit 1 }

$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Parar "Precisa rodar como administrador." }

$alvo   = Join-Path $DestinoPrograma $Ativo
$backup = Join-Path $DestinoPrograma "$Ativo.anterior"
if (-not (Test-Path $alvo)) { Parar "Gateway nao instalado em $alvo." }

function Servico-Rodando {
    $s = Get-Service -Name $Servico -ErrorAction SilentlyContinue
    return ($s -and $s.Status -eq "Running")
}

function Subir-Servico {
    try { Start-Service -Name $Servico -ErrorAction Stop } catch {}
}

function Derrubar-Servico {
    try { Stop-Service -Name $Servico -Force -ErrorAction SilentlyContinue } catch {}
    Start-Sleep -Seconds 2
    # O bootloader do PyInstaller gera dois processos; por nome pega os dois.
    Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Atualizar-Console {
    <#
      O console e' ferramenta de quem opera, nao servico: melhor esforco, sem
      health check e sem desfazer. Se a janela nao abrir, a aquisicao continua
      rodando do mesmo jeito.

      Antes esta funcao PULAVA o console quando ele estava aberto, para nao
      derrubar a janela de ninguem. Na pratica isso o deixava para tras para
      sempre: quem atualiza a mao abre o console justamente para conferir a
      versao, e roda o ATUALIZAR com ele aberto. O servico subia, o console
      ficava na versao velha, e a conclusao de quem olhava era "atualizei mil
      vezes e nao muda".

      Agora fecha, troca e reabre. Sao dois segundos de janela fechada, contra
      um console que nunca mais avanca.
    #>
    param($Release, $Cabecalhos)
    $nomeC = "gridco-console.exe"
    $alvoC = Join-Path $DestinoPrograma $nomeC
    if (-not (Test-Path $alvoC)) {
        Registrar "Console nao esta em $DestinoPrograma; nada a atualizar nele." "AVISO"
        return
    }

    $aC = $Release.assets | Where-Object { $_.name -eq $nomeC }
    $sC = $Release.assets | Where-Object { $_.name -eq "$nomeC.sha256" }
    if (-not $aC -or -not $sC) { Registrar "Release sem $nomeC; console nao atualizado." "AVISO"; return }

    $tc = Join-Path $env:TEMP ("gridco-c-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tc | Out-Null
    try {
        # Compara primeiro pelo hash publicado: sem diferenca, nem baixa.
        $arqSha = Join-Path $tc "$nomeC.sha256"
        Invoke-WebRequest -Uri $sC.url -Headers $Cabecalhos -OutFile $arqSha
        $esp = ((Get-Content $arqSha -Raw) -split '\s+')[0].Trim().ToLower()
        $local = (Get-FileHash $alvoC -Algorithm SHA256).Hash.ToLower()
        if ($esp -eq $local) { Registrar "Console ja' esta na versao publicada."; return }

        # Baixa e confere ANTES de fechar a janela: se o download falhar ou o
        # hash nao bater, ninguem perdeu o console por nada.
        $novoC = Join-Path $tc $nomeC
        Invoke-WebRequest -Uri $aC.url -Headers $Cabecalhos -OutFile $novoC
        $obt = (Get-FileHash $novoC -Algorithm SHA256).Hash.ToLower()
        if ($esp -ne $obt) { Registrar "SHA-256 do console nao confere; nao trocado." "AVISO"; return }

        $estavaAberto = [bool](Get-Process -Name "gridco-console" -ErrorAction SilentlyContinue)
        if ($estavaAberto) {
            Registrar "Console aberto; fechando para trocar o binario."
            Get-Process -Name "gridco-console" -ErrorAction SilentlyContinue |
                Stop-Process -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }

        # O arquivo pode continuar travado por um instante depois do processo
        # morrer. Tentar uma vez so' e voltar ao problema de origem.
        $trocado = $false
        foreach ($tentativa in 1..5) {
            try { Copy-Item $novoC $alvoC -Force; $trocado = $true; break }
            catch { Start-Sleep -Seconds 2 }
        }
        if (-not $trocado) {
            Registrar "Console em uso; nao foi possivel trocar o binario." "AVISO"
            return
        }
        Registrar "Console atualizado."

        if ($estavaAberto -and -not $Automatico) {
            # So' reabre quando alguem rodou o atualizador a mao. Na tarefa
            # diaria o console roda como SYSTEM, numa sessao sem area de
            # trabalho: abriria um processo invisivel que ninguem fecha.
            try {
                Start-Process -FilePath $alvoC -WorkingDirectory $DestinoPrograma
                Registrar "Console reaberto na versao nova."
            } catch {
                Registrar "Console nao reabriu sozinho: $($_.Exception.Message)" "AVISO"
            }
        }
    } catch {
        Registrar "Console nao atualizado: $($_.Exception.Message)" "AVISO"
    } finally {
        Remove-Item $tc -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Atualizar-A-Si-Mesmo {
    <#
      Troca o proprio atualizar.ps1 pelo do release. Sem isso, melhoria neste
      script exigiria levar arquivo a mao ate cada usina - o oposto do que a
      atualizacao automatica existe para resolver.

      Roda por ULTIMO: o PowerShell ja leu este arquivo, entao a troca so vale
      da proxima execucao - que e' exatamente o desejado.
    #>
    param($Release, $Cabecalhos)
    $eu = $PSCommandPath
    if (-not $eu -or -not (Test-Path $eu)) { return }

    $aS = $Release.assets | Where-Object { $_.name -eq "atualizar.ps1" }
    $sS = $Release.assets | Where-Object { $_.name -eq "atualizar.ps1.sha256" }
    if (-not $aS -or -not $sS) { return }   # release antigo, sem o script

    $ts = Join-Path $env:TEMP ("gridco-s-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $ts | Out-Null
    try {
        $arqSha = Join-Path $ts "atualizar.ps1.sha256"
        Invoke-WebRequest -Uri $sS.url -Headers $Cabecalhos -OutFile $arqSha
        $esp = ((Get-Content $arqSha -Raw) -split '\s+')[0].Trim().ToLower()
        if ((Get-FileHash $eu -Algorithm SHA256).Hash.ToLower() -eq $esp) { return }

        $novo = Join-Path $ts "atualizar.ps1"
        Invoke-WebRequest -Uri $aS.url -Headers $Cabecalhos -OutFile $novo
        if ((Get-FileHash $novo -Algorithm SHA256).Hash.ToLower() -ne $esp) {
            Registrar "SHA-256 do atualizador nao confere; mantido o atual." "AVISO"
            return
        }
        Copy-Item $novo $eu -Force
        Registrar "Atualizador substituido; vale da proxima execucao."
    } catch {
        Registrar "Atualizador nao trocado: $($_.Exception.Message)" "AVISO"
    } finally {
        Remove-Item $ts -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Confirmar-Saude {
    <#
      Sobe o servico e observa. Nao basta o SCM dizer "Running": um binario
      quebrado pode subir e morrer em seguida, e o SCM reinicia em laco. Por
      isso exige dois processos vivos e estado estavel em duas medicoes.
    #>
    param([int]$Segundos)
    Subir-Servico
    $limite = (Get-Date).AddSeconds($Segundos)
    $estaveis = 0
    while ((Get-Date) -lt $limite) {
        Start-Sleep -Seconds 5
        $processos = @(Get-Process -Name "gridco-gateway" -ErrorAction SilentlyContinue)
        if ((Servico-Rodando) -and $processos.Count -ge 1) {
            $estaveis++
            if ($estaveis -ge 3) { return $true }
        } else {
            $estaveis = 0
            Subir-Servico
        }
    }
    return $false
}

# ---------- rollback pedido a mao ----------
if ($Rollback) {
    if (-not (Test-Path $backup)) { Parar "Nao ha versao anterior em $backup" }
    Registrar "Rollback manual pedido."
    Derrubar-Servico
    $temp = Join-Path $DestinoPrograma "$Ativo.trocando"
    Move-Item $alvo $temp -Force
    Move-Item $backup $alvo -Force
    Move-Item $temp $backup -Force
    if (Confirmar-Saude 60) { Registrar ("Rollback aplicado: " + (& $alvo --version)) }
    else { Registrar "Rollback aplicado mas o servico nao confirmou." "AVISO" }
    exit 0
}

# ---------- espera aleatoria ----------
if ($EsperaMaxSegundos -gt 0) {
    $espera = Get-Random -Minimum 0 -Maximum $EsperaMaxSegundos
    Registrar "Aguardando ${espera}s antes de consultar (dispersao da frota)."
    Start-Sleep -Seconds $espera
}

# ---------- versao instalada ----------
try { $instalada = (& $alvo --version).Trim() } catch { Parar "Nao consegui ler a versao instalada: $_" }

# ---------- release ----------
$cabecalhos = @{ "User-Agent" = "gridco-gateway-updater"; "Accept" = "application/vnd.github+json" }
if ($env:GRIDCO_GITHUB_TOKEN) { $cabecalhos["Authorization"] = "Bearer $env:GRIDCO_GITHUB_TOKEN" }

try {
    if ($Tag) {
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/tags/$Tag" -Headers $cabecalhos
    } elseif ($Canal -eq "teste") {
        # Inclui pre-release: e' o canal de quem testa antes da frota.
        $release = (Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases?per_page=10" -Headers $cabecalhos |
                    Where-Object { -not $_.draft } | Select-Object -First 1)
    } else {
        # /releases/latest ja' exclui rascunho e pre-release.
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -Headers $cabecalhos
    }
} catch {
    Registrar "Nao consegui consultar o GitHub: $($_.Exception.Message)" "AVISO"
    exit 0   # sem rede nao e' falha: tenta de novo na proxima janela
}
if (-not $release) { Registrar "Nenhum release no canal '$Canal'." "AVISO"; exit 0 }
if ($release.prerelease -and $Canal -eq "estavel") {
    Registrar "Release $($release.tag_name) e' pre-release; canal estavel ignora." ; exit 0
}

$baixar = $cabecalhos.Clone(); $baixar["Accept"] = "application/octet-stream"

$publicada = ($release.tag_name -replace '^v', '').Trim()
if ($publicada -eq $instalada -and -not $Forcar) {
    Registrar "Servico ja' esta na $instalada."
    # O console e' binario separado e pode estar atrasado mesmo com o servico
    # em dia - por exemplo se estava aberto na janela anterior.
    Atualizar-Console $release $baixar
    Atualizar-A-Si-Mesmo $release $baixar
    exit 0
}

# Versao que ja' derrubou o servico neste PC nao e' tentada de novo a cada
# janela: seria um laco de atualizar-quebrar-desfazer ate' alguem olhar.
$recusada = Join-Path $DestinoDados "data\versao-recusada.txt"
if ((Test-Path $recusada) -and -not $Forcar) {
    $bloqueada = (Get-Content $recusada -Raw).Trim()
    if ($bloqueada -eq $publicada) {
        Registrar "Versao $publicada ja' falhou neste PC e esta bloqueada. Use -Forcar para insistir." "AVISO"
        exit 0
    }
}
Registrar "Instalada $instalada -> publicada $publicada ($($release.tag_name), canal $Canal)."

# ---------- download ----------
$aExe = $release.assets | Where-Object { $_.name -eq $Ativo }
$aSha = $release.assets | Where-Object { $_.name -eq "$Ativo.sha256" }
if (-not $aExe) { Parar "O release nao tem o ativo '$Ativo'." }
if (-not $aSha) { Parar "O release nao tem '$Ativo.sha256'. Sem soma de verificacao nao troco o binario." }

$tmp = Join-Path $env:TEMP ("gridco-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$novoExe = Join-Path $tmp $Ativo
$novoSha = Join-Path $tmp "$Ativo.sha256"
try {
    Invoke-WebRequest -Uri $aExe.url -Headers $baixar -OutFile $novoExe
    Invoke-WebRequest -Uri $aSha.url -Headers $baixar -OutFile $novoSha
} catch {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Registrar "Falha no download: $($_.Exception.Message)" "AVISO"
    exit 0
}

$esperado = ((Get-Content $novoSha -Raw) -split '\s+')[0].Trim().ToLower()
$obtido   = (Get-FileHash $novoExe -Algorithm SHA256).Hash.ToLower()
if ($esperado -ne $obtido) {
    Remove-Item $tmp -Recurse -Force
    Parar "SHA-256 nao confere (esperado $esperado, obtido $obtido). Binario NAO trocado."
}
Registrar "SHA-256 confere."

# ---------- troca, com desfazer automatico ----------
Derrubar-Servico
if (Test-Path $backup) { Remove-Item $backup -Force }
Move-Item $alvo $backup -Force
Copy-Item $novoExe $alvo -Force
Remove-Item $tmp -Recurse -Force

if (Confirmar-Saude $SegundosParaConfirmar) {
    $agora = try { (& $alvo --version).Trim() } catch { "?" }
    Registrar "ATUALIZADO para $agora. Anterior em $backup."
    Atualizar-Console $release $baixar
    Atualizar-A-Si-Mesmo $release $baixar
    exit 0
}

Registrar "O servico nao confirmou em ${SegundosParaConfirmar}s. DESFAZENDO." "ERRO"
Derrubar-Servico
Remove-Item $alvo -Force -ErrorAction SilentlyContinue
Move-Item $backup $alvo -Force
if (Confirmar-Saude 60) {
    Registrar "Voltou para $instalada e o servico subiu. A versao $publicada fica bloqueada aqui." "AVISO"
    # Marca para nao tentar de novo em laco a cada janela.
    Set-Content -LiteralPath (Join-Path $DestinoDados "data\versao-recusada.txt") `
        -Value $publicada -Encoding utf8
    exit 1
}
Parar "Desfez a troca e MESMO ASSIM o servico nao subiu. Precisa de alguem no PC."
