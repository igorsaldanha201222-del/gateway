"""Ponto de entrada do executavel Windows.

Tres modos, decididos pela linha de comando:

    gridco-gateway.exe                      SCM iniciou: entra como servico
    gridco-gateway.exe service install      administra o servico
    gridco-gateway.exe --config ... --validate   uso normal de linha de comando
"""

from __future__ import annotations

import sys


def main() -> int:
    argumentos = sys.argv[1:]

    # Sem argumento nenhum quem chamou foi o Service Control Manager.
    if not argumentos:
        from gridco_gateway.winservice import executar_como_servico
        executar_como_servico()
        return 0

    if argumentos[0] == "service":
        from gridco_gateway.winservice import linha_de_comando
        linha_de_comando(argumentos[1:])
        return 0

    from gridco_gateway.__main__ import main as rodar
    return rodar()


if __name__ == "__main__":
    sys.exit(main())
