"""Operacoes do console que mexem na configuracao instalada.

Sao as que quebraram em campo: remover equipamento deixava referencia orfa e
travava todo cadastro seguinte, e renomear a usina deixava os topicos no nome
antigo - a telemetria sumia do servidor sem erro em lugar nenhum.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from gridco_gateway.config import validate_configuration


def _base() -> dict:
    return {
        "schema_version": "1.0.0",
        "configuration_id": "teste",
        "revision": 1,
        "plant": {"id": "usina_velha", "name": "Velha", "company": "Grid Co",
                  "timezone": "America/Sao_Paulo",
                  "metadata": {"complex": "UFV", "topic_slug": "usina_velha"}},
        "general": {"gateway_id": "GW", "plant_id": "usina_velha", "default_qos": 1,
                    "default_retain": False, "auto_generate_topics": True,
                    "telemetry_topic_pattern": "dev/read/UFV/{plant}/{type}/{id}",
                    "command_subscribe_filter": "dev/write/UFV/usina_velha/+/+",
                    "command_feedback_topic": "dev/write/UFV/usina_velha/feedback",
                    "v3_configuration_topic": "dev/write/UFV/usina_velha/gateway/configuration/v3/set",
                    "v3_status_topic": "dev/read/UFV/usina_velha/gateway/status"},
        "runtime": {"enabled": False, "allow_remote_configuration": False,
                    "worker_watchdog_seconds": 30, "shutdown_timeout_seconds": 15,
                    "log_level": "INFO"},
        "mqtt": {"enabled": False, "host": "localhost", "port": 8883, "client_id": "x",
                 "tls": {"enabled": False, "ca_file": "", "cert_file": "", "key_file": "",
                         "server_hostname": "", "insecure": False}},
        "storage": {"database": "gateway.db", "max_buffer_messages": 1000},
        "channels": [{"id": "ch1", "name": "Rede", "transport": "tcp",
                      "ip": "192.168.1.10", "port": 502}],
        "templates": [{"id": "tpl", "name": "Inversor", "device_type": "inverter"}],
        "requests": [{"id": "rq", "template_id": "tpl", "function_code": 3,
                      "address": 32000, "quantity": 2}],
        "fields": [{"id": "f1", "template_id": "tpl", "request_id": "rq",
                    "json_key": "active_power", "source_type": "modbus",
                    "register_offset": 0, "data_type": "int16"}],
        "devices": [
            {"id": "inv1", "name": "Inversor 1", "device_type": "inverter",
             "channel_id": "ch1", "template_id": "tpl", "unit_id": 1,
             "metadata": {"mqtt_topic_index": 1}},
            {"id": "inv2", "name": "Inversor 2", "device_type": "inverter",
             "channel_id": "ch1", "template_id": "tpl", "unit_id": 2,
             "metadata": {"mqtt_topic_index": 2}},
        ],
        "topics": [
            {"id": "inv1-telemetry", "device_id": "inv1", "purpose": "telemetry",
             "topic": "dev/read/UFV/usina_velha/inverter/1", "qos": 1, "retain": False},
            {"id": "inv2-telemetry", "device_id": "inv2", "purpose": "telemetry",
             "topic": "dev/read/UFV/usina_velha/inverter/2", "qos": 1, "retain": False},
        ],
        "commands": [{"id": "cmd1", "device_id": "inv1", "name": "liga",
                      "function_code": 6, "address": 40200}],
        "sequences": [{"id": "seq1", "device_id": "inv1", "name": "partida", "steps": []}],
        "alarms": [], "events": [], "pid": [], "auto_reclosing": {"enabled": False},
    }


class ConsoleAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        raiz = Path(self._dir.name)
        (raiz / "config").mkdir()
        (raiz / "data").mkdir()
        self.conf = raiz / "config" / "gateway.json"
        self.conf.write_text(json.dumps(_base(), indent=2), encoding="utf-8")
        self._antigo = dict(os.environ)
        os.environ["GRIDCO_CONFIG"] = str(self.conf)
        os.environ["GRIDCO_DATA_DIR"] = str(raiz / "data")
        from gridco_gateway.console_app import Ponte
        self.ponte = Ponte()

    def tearDown(self) -> None:
        os.environ.clear()
        os.environ.update(self._antigo)
        self._dir.cleanup()

    def _lido(self) -> dict:
        return json.loads(self.conf.read_text(encoding="utf-8"))

    def test_renomear_usina_renomeia_os_topicos(self) -> None:
        r = self.ponte.definir_usina("UFV Nova", "usina_nova")
        self.assertTrue(r["ok"], r)
        self.assertEqual(2, r["topicos_renomeados"])
        cfg = self._lido()
        self.assertEqual("usina_nova", cfg["plant"]["metadata"]["topic_slug"])
        for topico in cfg["topics"]:
            self.assertIn("/UFV/usina_nova/", topico["topic"])
            self.assertNotIn("usina_velha", topico["topic"])
        self.assertEqual("dev/write/UFV/usina_nova/+/+",
                         cfg["general"]["command_subscribe_filter"])

    def test_salvar_o_mesmo_nome_conserta_topico_que_ficou_para_tras(self) -> None:
        """O estado em que um PC fica depois de renomear numa versao sem o
        conserto: topic_slug novo, topicos velhos. Salvar o mesmo nome e o
        primeiro reflexo de quem tenta arrumar, e precisa funcionar."""
        cfg = self._lido()
        cfg["plant"]["metadata"]["topic_slug"] = "usina_nova"
        cfg["plant"]["id"] = "usina_nova"
        self.conf.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        r = self.ponte.definir_usina("UFV Nova", "usina_nova")
        self.assertTrue(r["ok"], r)
        self.assertEqual(2, r["topicos_renomeados"])
        for topico in self._lido()["topics"]:
            self.assertIn("/UFV/usina_nova/", topico["topic"])

    def test_renomear_mantem_o_que_o_engine_publica_coerente(self) -> None:
        from gridco_gateway.engine import GatewayEngine
        self.ponte.definir_usina("UFV Nova", "usina_nova")
        cfg = self._lido()
        topico, _, _ = GatewayEngine._telemetry_topic(cfg, cfg["devices"][0])
        self.assertEqual("dev/read/UFV/usina_nova/inverter/1", topico)

    def test_remover_leva_junto_comando_e_sequencia(self) -> None:
        r = self.ponte.remover("inv1")
        self.assertTrue(r["ok"], r)
        cfg = self._lido()
        self.assertEqual(["inv2"], [d["id"] for d in cfg["devices"]])
        self.assertEqual([], cfg["commands"])
        self.assertEqual([], cfg["sequences"])
        # O canal e o modelo continuam: inv2 ainda usa os dois.
        self.assertEqual(["ch1"], [c["id"] for c in cfg["channels"]])
        self.assertEqual(["tpl"], [t["id"] for t in cfg["templates"]])
        self.assertEqual([], [m for m in validate_configuration(cfg) if m.level == "error"])

    def test_remover_o_ultimo_leva_canal_e_modelo(self) -> None:
        self.ponte.remover("inv1")
        r = self.ponte.remover("inv2")
        self.assertTrue(r["ok"], r)
        cfg = self._lido()
        self.assertEqual([], cfg["devices"])
        self.assertEqual([], cfg["channels"])
        self.assertEqual([], cfg["templates"])
        self.assertEqual([], cfg["requests"])
        self.assertEqual([], cfg["fields"])

    def test_reparar_conserta_configuracao_ja_quebrada(self) -> None:
        # Estado deixado pela remocao incompleta da versao anterior.
        cfg = self._lido()
        cfg["devices"] = [d for d in cfg["devices"] if d["id"] != "inv1"]
        cfg["topics"] = [t for t in cfg["topics"] if t["device_id"] != "inv1"]
        self.conf.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        self.assertTrue([m for m in validate_configuration(cfg) if m.level == "error"])

        r = self.ponte.reparar()
        self.assertTrue(r["ok"], r)
        self.assertEqual({"commands": 1, "sequences": 1}, r["achados"])
        self.assertEqual([], [m for m in validate_configuration(self._lido())
                              if m.level == "error"])

    def test_broker_recusa_1883(self) -> None:
        r = self.ponte.definir_broker("app.gridco.com.br", 1883)
        self.assertFalse(r["ok"])
        self.assertIn("1883", r["erro"])
        # e nao encostou na configuracao
        self.assertEqual(8883, self._lido()["mqtt"]["port"])


if __name__ == "__main__":
    unittest.main()
