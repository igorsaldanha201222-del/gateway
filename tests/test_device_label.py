from __future__ import annotations

import unittest
from datetime import UTC, datetime

from gridco_gateway.decoder import build_payload


def config_with(device_type_field: dict | None) -> dict:
    fields = [{"id": "ang", "template_id": "tpl", "request_id": "req", "json_key": "tcu_ang_atual",
               "source_type": "modbus", "data_type": "int16", "register_offset": 0, "gain": 0.1, "offset": 0,
               "enabled": True, "metadata": {"publish": True, "decimals": 1}}]
    if device_type_field:
        fields.insert(0, device_type_field)
    return {"plant": {"timezone": "UTC"}, "requests": [{"id": "req", "template_id": "tpl"}], "fields": fields}


class DeviceLabelTests(unittest.TestCase):
    """A chave device_type do payload leva o rotulo da frota (Tcu1), nao o tipo tecnico."""

    device = {"id": "tcu-07", "name": "Tcu7", "template_id": "tpl", "device_type": "tcu"}

    def payload(self, field):
        payload, _ = build_payload(config_with(field), self.device, {"req": [-553]}, 192, datetime.now(UTC), {}, None)
        return payload

    def test_template_with_label_system_value_publishes_device_name(self):
        field = {"id": "dt", "template_id": "tpl", "json_key": "device_type", "source_type": "static",
                 "data_type": "string", "source_expression": "", "enabled": True,
                 "metadata": {"publish": True, "system_value": 13, "quote_value": True}}
        self.assertEqual("Tcu7", self.payload(field)["device_type"])

    def test_template_without_label_keeps_technical_type(self):
        self.assertEqual("tcu", self.payload(None)["device_type"])
        legacy = {"id": "dt", "template_id": "tpl", "json_key": "device_type", "source_type": "static",
                  "data_type": "string", "source_expression": "stringbox11", "enabled": True,
                  "metadata": {"publish": True}}
        self.assertEqual("tcu", self.payload(legacy)["device_type"])


if __name__ == "__main__":
    unittest.main()
