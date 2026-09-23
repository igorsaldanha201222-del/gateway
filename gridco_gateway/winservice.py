"""Servico do Windows para o Gateway Grid Co.

Um .exe comum nao pode ser registrado com ``sc.exe create``: o Service Control
Manager inicia o binario e espera um "estou vivo" em ate 30 s; sem resposta ele
considera que travou e derruba. Este modulo implementa esse handshake com
``win32serviceutil``, e e' o que faz o gateway aparecer em services.msc.

Uso pelo executavel empacotado:

    gridco-gateway.exe service install     registra
    gridco-gateway.exe service start       inicia
    gridco-gateway.exe service stop        para
    gridco-gateway.exe service remove      remove

Sem argumento nenhum, o executavel assume que quem o lancou foi o SCM.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

# Caminho padrao da instalacao. O servico roda como LocalSystem, que nao tem
# diretorio de trabalho util, entao nada aqui pode ser relativo.
BASE_PADRAO = Path(r"C:\ProgramData\GridCo\Gateway")


def _caminhos() -> tuple[Path, Path]:
    config = Path(os.environ.get("GRIDCO_CONFIG", BASE_PADRAO / "config" / "gateway.json"))
    dados = Path(os.environ.get("GRIDCO_DATA_DIR", BASE_PADRAO / "data"))
    return config, dados


class GatewayService(win32serviceutil.ServiceFramework):
    _svc_name_ = "GridCoGateway"
    _svc_display_name_ = "Gateway Grid Co"
    _svc_description_ = (
        "Le os equipamentos por Modbus TCP, guarda em buffer SQLite e publica por MQTT. "
        "Sem interface: acompanhe pelo log em C:\\ProgramData\\GridCo\\Gateway\\data\\logs."
    )

    def __init__(self, args):
        super().__init__(args)
        self.parar = threading.Event()
        # Evento nativo so' para o SCM; o laco do gateway usa o threading.Event.
        self.evento_scm = win32event.CreateEvent(None, 0, 0, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self.parar.set()
        win32event.SetEvent(self.evento_scm)

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        config, dados = _caminhos()
        # main() le a linha de comando; como servico nao ha uma util, monta-se.
        sys.argv = [sys.argv[0], "--config", str(config), "--data-dir", str(dados)]
        try:
            from .__main__ import main
            main(stop_event=self.parar)
        except Exception as exc:  # o SCM so' mostra "erro"; o Visualizador de Eventos mostra isto
            servicemanager.LogErrorMsg(f"Gateway Grid Co falhou: {exc}")
            raise
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STOPPED,
            (self._svc_name_, ""),
        )


def executar_como_servico() -> None:
    """Entra no modo dispatcher: usado quando o SCM inicia o processo."""
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(GatewayService)
    servicemanager.StartServiceCtrlDispatcher()


def linha_de_comando(argumentos: list[str]) -> None:
    """Trata ``gridco-gateway.exe service <acao>``."""
    win32serviceutil.HandleCommandLine(GatewayService, argv=[sys.argv[0]] + argumentos)
