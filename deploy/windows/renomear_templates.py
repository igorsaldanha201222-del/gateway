"""Tira nome de usina dos templates do catalogo.

Template e' um modelo de equipamento, nao um registro de onde foi usado. Mas
tirar "Acopiara" e "Pedra Branca" faz seis pares ficarem com nome igual, e em
todos o conteudo difere. Nesses casos o nome ganha v1, v2, e o QUE diferencia
vai para a descricao, apurado campo a campo - nao inventado.

    py -3 deploy/windows/renomear_templates.py           # so mostra
    py -3 deploy/windows/renomear_templates.py --aplicar
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CATALOGO = Path(__file__).resolve().parents[2] / "config" / "template_catalog.json"

# O que realmente diferencia cada versao, por catalog_id. Apurado comparando
# campo a campo, ignorando o id interno do bloco, que muda por construcao.
DIFERENCA = {
    "catalog-acopiara-tpl-rele-protecao-p3u30":
        "Tensoes de linha com ganho 1.",
    "catalog-pedra-branca-tpl-pb-p3u30":
        "Tensoes de linha com ganho 0,1. Unica diferenca para a outra versao.",
    "catalog-acopiara-tpl-stringbox-fr-dcmg-mmpd":
        "Publica as correntes como current_ch_1..24. Blocos 0+60, 48+103, 208+30.",
    "catalog-pedra-branca-tpl-pb-longmax-stringbox":
        "Publica as correntes como string_current_01..24. Blocos de 30 em 30.",
    "catalog-rev15-longmax-stringbox-24ch-acopiara":
        "Publica as correntes como current_ch_1..24.",
    "catalog-rev15-longmax-stringbox-24ch-pb":
        "Publica as correntes como string_current_01..24.",
    "catalog-acopiara-tpl-multimedidor-pm5000-register-list":
        "Mesmo mapa da outra versao; difere so' no tipo do default_value ('0' em vez de 0).",
    "catalog-pedra-branca-tpl-pb-pm5100":
        "Mesmo mapa da outra versao; difere so' no tipo do default_value (0 em vez de '0').",
    # Electron EP4 TH104: tres entradas, mapas diferentes. Qual esta certo NAO
    # esta arbitrado aqui - isso se resolve medindo no equipamento.
    "catalog-acopiara-tpl-rele-termico-ept-iot":
        "Mapa curto: 1 bloco (0+8) e 8 variaveis Modbus. As outras versoes leem 19.",
    "catalog-pedra-branca-tpl-pb-electron-ep4-th104":
        "Mapa longo: 3 blocos (29+4, 37+4, 45+5) e 19 variaveis Modbus.",
    "catalog-rev15-electron-ep4-th104-acopiara":
        "Mesmos blocos da outra versao longa (29+4, 37+4, 45+5); difere em detalhe de campo.",
    # Longmax "string box" generico: dois mapas bem distintos.
    "catalog-rev15-longmax-stringbox-naturagua":
        "24 correntes em 10+N, com bandeirolas no/low/high. Mapa confirmado no equipamento.",
    "catalog-naturagua-naturagua-string-box":
        "Extraido do V7: 6 blocos, 84 variaveis Modbus, inclui barramento e temperaturas. "
        "Campos marcados precisam de validacao antes de habilitar.",
}

USINA = re.compile(r"\s*(de\s+)?(Acopiara|Pedra\s*Branca|Natur[áa]gua)\b\s*", re.I)


def limpar(nome: str) -> str:
    n = re.sub(r"\s{2,}", " ", USINA.sub(" ", nome))
    return n.strip(" ·-")


def main() -> int:
    aplicar = "--aplicar" in sys.argv
    dados = json.loads(CATALOGO.read_text(encoding="utf-8"))
    entradas = dados["entries"]

    # Agrupa pelo nome ja' limpo, na ordem em que aparecem no catalogo.
    grupos: dict[str, list] = defaultdict(list)
    for e in entradas:
        grupos[limpar(e["name"])].append(e)

    mudancas = []
    planejados = []          # nome final de cada entrada, mesmo em simulacao
    for base, lista in grupos.items():
        for i, e in enumerate(lista, start=1):
            novo = base if len(lista) == 1 else f"{base} v{i}"
            planejados.append(novo)
            if novo != e["name"]:
                mudancas.append((e["name"], novo))
            if not aplicar:
                continue
            e["name"] = novo
            if isinstance(e.get("template"), dict):
                e["template"]["name"] = novo
                if e["template"].get("model"):
                    e["template"]["model"] = limpar(str(e["template"]["model"]))
            if e.get("model"):
                e["model"] = limpar(str(e["model"]))
            # A descricao passa a carregar o que separa uma versao da outra.
            extra = DIFERENCA.get(str(e.get("catalog_id")))
            if len(lista) > 1 and extra:
                desc = str(e.get("description") or "").strip()
                if extra not in desc:
                    e["description"] = (desc + " " + extra).strip()

    for antigo, novo in mudancas:
        print(f"  {antigo}\n    -> {novo}")
    print(f"\n{len(mudancas)} de {len(entradas)} templates renomeados.")

    repetidos = sorted({n for n in planejados if planejados.count(n) > 1})
    if repetidos:
        print("\nATENCAO, nomes repetidos restantes: " + ", ".join(repetidos))
        return 1
    print("Nenhum nome repetido.")

    sem_explicacao = [e["catalog_id"] for base, lista in grupos.items() if len(lista) > 1
                      for e in lista if not DIFERENCA.get(str(e.get("catalog_id")))]
    if sem_explicacao:
        print("\nVersoes sem explicacao na descricao (v1/v2 sem dizer o porque):")
        for c in sem_explicacao:
            print("  -", c)

    if aplicar:
        CATALOGO.write_text(json.dumps(dados, ensure_ascii=False, indent=1) + "\n",
                            encoding="utf-8")
        print(f"\nGravado: {CATALOGO}")
    else:
        print("\n(simulacao; use --aplicar para gravar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
