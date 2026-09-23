"""Entrada do servico Gateway Grid Co.

Headless: nao sobe interface web. O processo fica vivo enquanto o motor de
aquisicao roda e encerra de forma limpa em SIGINT/SIGTERM.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
from pathlib import Path

from . import __version__
from .config import ConfigurationManager, ConfigurationError, load_json, validate_configuration
from .engine import GatewayEngine
from .storage import Storage


def app_root() -> Path:
    """Diretorio de referencia para config e dados.

    Empacotado com PyInstaller, ``__file__`` aponta para o diretorio temporario
    de extracao, que muda a cada boot e some no encerramento. O que interessa e
    onde o executavel esta instalado.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    root = app_root()
    parser = argparse.ArgumentParser(description="Gateway Grid Co - Modbus TCP para MQTT")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("GRIDCO_CONFIG", root / "config" / "gateway.json")),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("GRIDCO_DATA_DIR", root / "data")),
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=Path(os.environ["GRIDCO_LOG_FILE"]) if os.environ.get("GRIDCO_LOG_FILE") else None,
        help="arquivo de log rotativo; sem console, e a unica saida que sobra",
    )
    parser.add_argument("--validate", action="store_true", help="valida a configuracao e encerra")
    parser.add_argument("--version", action="version", version=__version__)
    return parser.parse_args()


def _validate(config_path: Path) -> int:
    try:
        messages = validate_configuration(load_json(config_path))
    except Exception as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    for item in messages:
        print(f"{item.level.upper()} {item.path}: {item.message} [{item.code}]")
    errors = sum(item.level == "error" for item in messages)
    print(f"Validacao concluida: {errors} erro(s), {len(messages) - errors} aviso(s)")
    return 1 if errors else 0


def main(stop_event: threading.Event | None = None) -> int:
    """Executa o gateway ate receber sinal.

    ``stop_event`` permite que outro dono do processo peca o encerramento - e o
    caso do servico do Windows, onde quem manda parar e o Service Control
    Manager, nao um sinal POSIX.
    """
    args = parse_args()
    if args.validate:
        return _validate(args.config)

    args.data_dir.mkdir(parents=True, exist_ok=True)
    manager = ConfigurationManager(args.config, args.data_dir / "config_versions")
    try:
        config = manager.load()
    except ConfigurationError as exc:
        for item in exc.messages:
            print(f"{item.level.upper()} {item.path}: {item.message}", file=sys.stderr)
        return 2

    runtime = config.get("runtime", {}) if isinstance(config.get("runtime"), dict) else {}

    # Rodando como processo de segundo plano nao ha console: sem arquivo, o log
    # se perde. Por isso o padrao do executavel empacotado e sempre gravar.
    log_file = args.log_file
    if log_file is None and getattr(sys, "frozen", False):
        log_file = args.data_dir / "logs" / "gateway.log"
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8",
        ))
    logging.basicConfig(
        level=getattr(logging, str(runtime.get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    log = logging.getLogger(__name__)

    storage_settings = config.get("storage", {}) if isinstance(config.get("storage"), dict) else {}
    storage_file = Path(str(storage_settings.get("database", "gateway.db")))
    if not storage_file.is_absolute():
        storage_file = args.data_dir / storage_file

    storage = Storage(storage_file, storage_settings)
    engine = GatewayEngine(manager, storage, args.config.parent)

    stopping = stop_event if stop_event is not None else threading.Event()

    def shutdown(signum: int, _frame: object) -> None:
        if not stopping.is_set():
            log.info("Sinal %s recebido, encerrando", signum)
            stopping.set()

    # Rodando como servico o processo nao tem console, e registrar handler de
    # sinal fora da thread principal levanta ValueError. Quem para e' o SCM.
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, shutdown)

    plant = config.get("plant", {}) if isinstance(config.get("plant"), dict) else {}
    from .versao import longa as versao_longa
    # A primeira linha do log tem que permitir dizer, sem entrar no PC, qual
    # binario esta rodando ali.
    log.info(
        "Gateway Grid Co %s iniciando | planta=%s | config=%s",
        versao_longa(),
        plant.get("id") or plant.get("name") or "(sem id)",
        args.config,
    )

    engine.start()
    try:
        # Sem servidor web, o processo dorme ate receber sinal. O wait com
        # timeout mantem o processo interrompivel no Windows, onde um wait
        # indefinido nao acorda com Ctrl+C.
        while not stopping.wait(1.0):
            pass
    finally:
        engine.stop(float(runtime.get("shutdown_timeout_seconds", 15)))
        storage.close()
        log.info("Gateway encerrado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
