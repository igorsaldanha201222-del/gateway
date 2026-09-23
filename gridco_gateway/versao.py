"""De onde veio este binario.

Empacotado, o build grava um ``build-info.json`` ao lado do executavel com a
versao, o commit e a data. Rodando do repositorio, o commit e' lido do git na
hora. Sem nenhum dos dois, diz "dev" - nunca inventa um numero, porque a razao
de existir deste modulo e' justamente responder "atualizou ou nao?".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from . import __version__

ARQUIVO = "build-info.json"


def _do_pacote() -> dict | None:
    locais = []
    if getattr(sys, "_MEIPASS", None):
        locais.append(Path(sys._MEIPASS) / ARQUIVO)
    if getattr(sys, "frozen", False):
        locais.append(Path(sys.executable).resolve().parent / ARQUIVO)
    for p in locais:
        try:
            if p.is_file():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _do_git() -> dict | None:
    raiz = Path(__file__).resolve().parents[1]
    if not (raiz / ".git").exists():
        return None
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=raiz, capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
    try:
        commit = git("rev-parse", "--short", "HEAD")
        if not commit:
            return None
        sujo = bool(git("status", "--porcelain"))
        return {"versao": __version__, "commit": commit + ("+" if sujo else ""),
                "data": git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M"),
                "origem": "repositorio"}
    except Exception:
        return None


@lru_cache(maxsize=1)
def info() -> dict:
    dados = _do_pacote() or _do_git() or {}
    return {
        "versao": dados.get("versao") or __version__,
        "commit": dados.get("commit") or "dev",
        "data": dados.get("data") or "",
        "tag": dados.get("tag") or "",
        "origem": dados.get("origem") or ("pacote" if _do_pacote() else "desconhecida"),
    }


def curta() -> str:
    """Ex.: '1.0.0 · d47266c' — o que aparece na faixa do console."""
    i = info()
    return f"{i['versao']} · {i['commit']}" if i["commit"] else i["versao"]


def longa() -> str:
    i = info()
    partes = [f"v{i['versao']}", f"commit {i['commit']}"]
    if i["tag"]:
        partes.append(f"tag {i['tag']}")
    if i["data"]:
        partes.append(i["data"])
    return " · ".join(partes)
