from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from pathlib import Path

from gridco_gateway.config import normalize_configuration, validate_configuration
from gridco_gateway.decoder import build_payload

ROOT = Path(__file__).resolve().parents[1]


def string_voltage(index: int, offset: int) -> dict:
    return {"id": f"v{index}", "template_id": "tpl", "request_id": "req", "json_key": f"_string_voltage_{index}",
            "source_type": "modbus", "data_type": "int16", "register_offset": offset, "gain": 0.1, "offset": 0,
            "enabled": True, "metadata": {"publish": False, "decimals": 1}}


def derived(field_id: str, key: str, operation: int, first: str, last: str) -> dict:
    return {"id": field_id, "template_id": "tpl", "json_key": key, "source_type": "derived", "data_type": "float32",
            "enabled": True, "default_value": "0",
            "metadata": {"publish": True, "decimals": 1, "calc_operation": operation,
                         "calc_field_a": first, "calc_field_b": last}}


class RangeOperationTests(unittest.TestCase):
    """Operacoes 15 e 16 com as regras do firmware WAGO REV15."""

    def configuration(self, fields: list[dict]) -> dict:
        return {"plant": {"timezone": "UTC"},
                "requests": [{"id": "req", "template_id": "tpl", "function_code": 3, "address": 32016, "quantity": 3}],
                "fields": fields}

    def payload(self, fields: list[dict], words: list[int]):
        device = {"id": "inv", "template_id": "tpl", "device_type": "inverter"}
        return build_payload(self.configuration(fields), device, {"req": words}, 192, datetime.now(UTC), {}, None)

    def test_average_and_sum_of_contiguous_string_voltages(self):
        fields = [string_voltage(1, 0), string_voltage(2, 1), string_voltage(3, 2),
                  derived("avg", "voltage_dc", 16, "v1", "v3"), derived("sum", "sum_dc", 15, "v1", "v3")]
        payload, diagnostics = self.payload(fields, [6000, 6100, 6200])
        self.assertAlmostEqual(610.0, payload["voltage_dc"])
        self.assertAlmostEqual(1830.0, payload["sum_dc"])
        self.assertEqual(192, diagnostics["field_quality"]["voltage_dc"])

    def test_one_invalid_member_invalidates_the_whole_average(self):
        fields = [string_voltage(1, 0), string_voltage(2, 1), string_voltage(3, 2),
                  derived("avg", "voltage_dc", 16, "v1", "v3")]
        # Resposta curta: a terceira string nao decodifica.
        payload, diagnostics = self.payload(fields, [6000, 6100])
        self.assertEqual(28, diagnostics["field_quality"]["voltage_dc"])

    def test_validator_accepts_range_and_rejects_reversed_or_calculated_member(self):
        base = normalize_configuration(json.loads((ROOT / "config" / "gateway.json").read_text(encoding="utf-8")))
        base["channels"] = [{"id": "c1", "name": "c1", "transport": "tcp", "enabled": True, "ip": "127.0.0.1",
                             "port": 502, "timeout_ms": 2000, "retries": 2, "poll_interval_ms": 1000}]
        base["templates"] = [{"id": "tpl", "name": "tpl", "device_type": "inverter", "enabled": True}]
        base["requests"] = self.configuration([])["requests"]
        base["devices"] = [{"id": "inv", "name": "inv", "channel_id": "c1", "unit_id": 1, "template_id": "tpl",
                            "device_type": "inverter", "enabled": True, "poll_interval_ms": 1000,
                            "publish_interval_ms": 10000, "stale_timeout_ms": 30000, "metadata": {}}]
        strings = [string_voltage(1, 0), string_voltage(2, 1), string_voltage(3, 2)]

        def range_errors(fields):
            return [m for m in validate_configuration(dict(base, fields=fields)) if m.level == "error" and m.code.startswith("field.calc")]

        self.assertEqual([], range_errors(strings + [derived("avg", "voltage_dc", 16, "v1", "v3")]))
        self.assertTrue(range_errors(strings + [derived("avg", "voltage_dc", 16, "v3", "v1")]))
        chained = strings[:1] + [derived("mid", "mid", 2, "v1", "v2")] + strings[1:]
        self.assertTrue(range_errors(chained + [derived("avg", "voltage_dc", 16, "v1", "v3")]))


if __name__ == "__main__":
    unittest.main()
