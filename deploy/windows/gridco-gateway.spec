# -*- mode: python ; coding: utf-8 -*-
"""Empacotamento do gateway para Windows.

Um unico .exe de console. O console nao aparece porque a tarefa agendada roda
como SYSTEM, sem sessao interativa; manter o modo console (em vez de --noconsole)
evita a armadilha do pythonw, onde escrever em stdout sem console fechado
derruba o processo.

Roda a partir da raiz do projeto:
    py -3 -m PyInstaller deploy/windows/gridco-gateway.spec --noconfirm
"""

import sys as _sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

RAIZ = Path(SPECPATH).resolve().parents[1]  # deploy/windows -> deploy -> raiz
_sys.path.insert(0, str(Path(SPECPATH)))
from gerar_build_info import gerar as _gerar_build_info  # noqa: E402

# tzdata e' pacote so' de dados. O Windows nao tem base de fusos do sistema,
# entao sem isto o zoneinfo("America/Sao_Paulo") do decoder falha no PC da
# usina - e a energia diaria vira data errada, nao erro.
datas = collect_data_files("tzdata")

# Carimbo de origem: versao, commit e data.
datas += [(str(_gerar_build_info()), ".")]

# A CA da Grid Co viaja DENTRO do executavel: uma so para a frota, atualizada
# junto com o binario. Sem arquivo solto para alguem esquecer de copiar.
datas += [(str(RAIZ / "config" / "ca-gridco.crt"), ".")]

# A chave privada da frota NAO entra aqui, e a diferenca importa.
#
# A CA acima e publica por definicao - existe para ser distribuida. Uma chave
# privada dentro deste .exe seria outra coisa: o release e publico, e extrair
# um arquivo de dentro de um executavel PyInstaller leva meia duzia de
# comandos. Publicar o binario seria publicar a credencial da frota inteira, e
# quem a tivesse publicaria telemetria no nome de qualquer usina e receberia o
# comando de todas.
#
# O certificado da usina viaja no pacote de instalacao (preparar-usina.ps1) e
# fica em ProgramData, numa pasta fechada para SYSTEM e Administradores. Ele
# nao e tocado pelas atualizacoes: o .exe troca, a credencial fica.

a = Analysis(
    [str(RAIZ / "deploy" / "windows" / "gateway_exe.py")],
    pathex=[str(RAIZ)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "gridco_gateway",
        "tzdata",
        # O servico do Windows: o PyInstaller nao acha estes por analise
        # estatica porque so' sao importados dentro de winservice.py.
        "gridco_gateway.winservice",
        "win32serviceutil", "win32service", "win32event", "servicemanager",
        "win32timezone",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # O app e' so' biblioteca padrao e headless: nada de GUI nem de testes.
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "lib2to3"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="gridco-gateway",
    icon=str(RAIZ / "deploy" / "windows" / "gridco.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX desligado de proposito: antivirus de usina costuma barrar binario
    # comprimido, e o ganho de tamanho nao paga o risco de nao subir no boot.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
