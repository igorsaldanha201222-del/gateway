from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gridco_gateway.config import ConfigurationManager, configuration_sha256, validate_configuration


ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads((ROOT / "config" / "gateway.json").read_text(encoding="utf-8"))

    def test_default_configuration_is_valid(self) -> None:
        errors = [item for item in validate_configuration(self.config) if item.level == "error"]
        self.assertEqual([], errors)

    def test_reference_errors_are_detected(self) -> None:
        self.config["devices"] = [{
            "id": "dev-1", "channel_id": "missing", "template_id": "missing", "unit_id": 1,
            "poll_interval_ms": 1000, "publish_interval_ms": 1000, "stale_timeout_ms": 1000,
        }]
        codes = {item.code for item in validate_configuration(self.config) if item.level == "error"}
        self.assertIn("reference.channel", codes)
        self.assertIn("reference.template", codes)

    def test_global_codesys_offsets_are_accepted(self) -> None:
        self.config["templates"] = [{"id": "tpl"}]
        self.config["requests"] = [{"id": "req", "template_id": "tpl", "function_code": 3,
                                     "address": 100, "quantity": 2, "buffer_offset": 20}]
        self.config["fields"] = [{"id": "field", "template_id": "tpl", "request_id": "req",
                                   "json_key": "value", "source_type": "modbus", "data_type": "uint16",
                                   "register_offset": 21}]
        errors = [item for item in validate_configuration(self.config) if item.level == "error"]
        self.assertEqual([], errors)

    def test_unit_id_255_is_valid_only_for_modbus_tcp(self) -> None:
        self.config["channels"] = [
            {"id": "tcp", "transport": "tcp", "ip": "127.0.0.1", "port": 502},
            {"id": "rtu", "transport": "rtu", "serial_device": "/dev/ttyS0", "baudrate": 9600, "parity": "none"},
        ]
        self.config["templates"] = [{"id": "tpl"}]
        common = {
            "template_id": "tpl", "unit_id": 255, "poll_interval_ms": 1000,
            "publish_interval_ms": 1000, "stale_timeout_ms": 1000,
        }
        self.config["devices"] = [{"id": "tcp-device", "channel_id": "tcp", **common}]
        tcp_errors = [item for item in validate_configuration(self.config) if item.level == "error"]
        self.assertEqual([], tcp_errors)
        self.config["devices"] = [{"id": "rtu-device", "channel_id": "rtu", **common}]
        rtu_codes = {item.code for item in validate_configuration(self.config) if item.level == "error"}
        self.assertIn("modbus.unit_id", rtu_codes)

    def test_atomic_apply_creates_immutable_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "gateway.json"
            active.write_text(json.dumps(self.config), encoding="utf-8")
            manager = ConfigurationManager(active, root / "versions")
            manager.load()
            before = configuration_sha256(manager.current())
            changed = manager.current()
            changed["plant"]["name"] = "Nova usina"
            result = manager.apply(changed, "unittest")
            self.assertNotEqual(before, result["sha256"])
            self.assertEqual("Nova usina", manager.load()["plant"]["name"])
            self.assertEqual(1, len(list((root / "versions").glob("*.json"))))


if __name__ == "__main__":
    unittest.main()
