"""Grava build-info.json com versao, commit e data. Chamado pelos .spec.

Serve para responder, olhando a tela do console numa usina remota: este PC
esta rodando o binario novo ou o antigo?
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def gerar(destino: Path | None = None) -> Path:
    sys.path.insert(0, str(RAIZ))
    from gridco_gateway import __version__

    commit = _git("rev-parse", "--short", "HEAD") or "sem-git"
    if commit != "sem-git" and _git("status", "--porcelain"):
        # Marca que o binario saiu de arvore com alteracao nao commitada: sem
        # isso dois binarios diferentes teriam o mesmo carimbo.
        commit += "+"
    tag = _git("describe", "--tags", "--exact-match") or ""

    # A TAG manda na versao. O atualizar.ps1 compara a saida de --version com o
    # nome da tag do release; se a versao ficasse presa na constante do codigo,
    # a comparacao nunca casaria e cada PC rebaixaria o mesmo binario todas as
    # noites, para sempre.
    versao = (tag.lstrip("vV") or __version__) if tag else __version__

    dados = {
        "versao": versao,
        "commit": commit,
        "tag": tag,
        "data": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "origem": "pacote",
    }
    destino = destino or (RAIZ / "build" / "build-info.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destino


if __name__ == "__main__":
    caminho = gerar()
    print(caminho.read_text(encoding="utf-8"))
