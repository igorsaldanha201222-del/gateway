# -*- mode: python ; coding: utf-8 -*-
"""Empacotamento do console para Windows.

Janela nativa em tkinter. O Tk desenha por Win32/GDI e suas DLLs (tcl86t.dll,
tk86t.dll, _tkinter.pyd) vao DENTRO do executavel: nao ha runtime externo para
faltar ou divergir de maquina para maquina.

Foi uma troca deliberada. A versao anterior usava WebView2 via pywebview, e
renderizou a pagina sem estilo num PC de campo, sem erro nenhum - a classe de
falha que se quer justamente evitar num gateway.

    py -3 -m PyInstaller deploy/windows/gridco-console.spec --noconfirm --clean
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

RAIZ = Path(SPECPATH).resolve().parents[1]

import sys as _sys
_sys.path.insert(0, str(Path(SPECPATH)))
from gerar_build_info import gerar as _gerar_build_info  # noqa: E402

datas = collect_data_files("tzdata")

# Carimbo de origem: versao, commit e data. E' o que diz, olhando a tela numa
# usina remota, se aquele PC ja' pegou o binario novo.
datas += [(str(_gerar_build_info()), ".")]

# O catalogo vai dentro: e' ele que alimenta o cadastro de equipamento.
datas += [(str(RAIZ / "config" / "template_catalog.json"), ".")]

# Marca: icone da janela e logo da faixa. Gerados por deploy/windows/gerar_marca.py
# a partir do logo institucional.
MARCA = RAIZ / "deploy" / "windows"
datas += [(str(MARCA / "gridco.ico"), "."), (str(MARCA / "gridco-logo.png"), ".")]

a = Analysis(
    [str(RAIZ / "deploy" / "windows" / "console_exe.py")],
    pathex=[str(RAIZ)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "gridco_gateway",
        "gridco_gateway.console_ui",
        "gridco_gateway.console_app",
        "gridco_gateway.device_finder",
        "tzdata",
        # Estado do servico no painel: consulta o SCM.
        "win32serviceutil", "win32service", "win32timezone",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["unittest", "pydoc", "doctest", "test", "lib2to3",
              # Sem WebView2, sem .NET: a razao de ser desta versao.
              "webview", "clr", "clr_loader", "pythonnet"],
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
    name="gridco-console",
    icon=str(RAIZ / "deploy" / "windows" / "gridco.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # janela propria, sem console preto atras
    # Grava a configuracao em C:\ProgramData e reinicia o servico: as duas
    # coisas exigem elevacao.
    uac_admin=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
