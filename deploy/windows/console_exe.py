"""Ponto de entrada do console (janela nativa)."""

from __future__ import annotations

import sys

from gridco_gateway.console_ui import main

if __name__ == "__main__":
    sys.exit(main())
