from __future__ import annotations

import unittest

from gridco_gateway.payload_policy import detach_inverter_string_devices


class PayloadPolicyTests(unittest.TestCase):
    def test_detaches_inverter_but_preserves_independent_stringbox(self) -> None:
        configuration = {
            "templates": [
                {"id": "inv-tpl", "device_type": "inverter"},
                {"id": "sb-tpl", "device_type": "stringbox"},
            ],
            "fields": [
                {"id": "power", "template_id": "inv-tpl", "json_key": "active_power", "source_type": "modbus"},
                {"id": "string", "template_id": "inv-tpl", "json_key": "string_current_01", "source_type": "linked_stringbox"},
                {"id": "current", "template_id": "sb-tpl", "json_key": "current_ch_1", "source_type": "modbus", "metadata": {"publish": False}},
            ],
            "devices": [
                {"id": "inv-01", "template_id": "inv-tpl", "device_type": "inverter", "metadata": {"linked_string_box": "sb-01"}},
                {"id": "sb-01", "template_id": "sb-tpl", "device_type": "stringbox", "metadata": {"map": "Longmax"}},
            ],
            "topics": [
                {"device_id": "inv-01", "purpose": "telemetry", "topic": "inverter/01"},
                {"device_id": "sb-01", "purpose": "telemetry", "topic": "stringbox/01"},
            ],
        }
        result = detach_inverter_string_devices(configuration)
        self.assertEqual(1, result["inverter_devices"])
        self.assertEqual(1, result["associations_removed"])
        self.assertEqual(1, result["string_fields_removed"])
        self.assertEqual(1, result["string_currents_enabled"])
        self.assertEqual(0, result["string_api_aliases"])
        self.assertFalse(configuration["devices"][0]["metadata"]["include_string_fields"])
        self.assertNotIn("linked_string_box", configuration["devices"][0]["metadata"])
        self.assertEqual(["power", "current"], [field["id"] for field in configuration["fields"]])
        self.assertTrue(configuration["fields"][1]["metadata"]["publish"])
        self.assertEqual(
            "current_ch_1", configuration["fields"][1]["json_key"],
        )
        self.assertEqual(2, len(configuration["topics"]))

    def test_normalizes_previous_string_current_alias_back_to_current_ch(self) -> None:
        configuration = {
            "templates": [{"id": "sb-tpl", "device_type": "stringbox"}],
            "fields": [{
                "id": "current", "template_id": "sb-tpl",
                "json_key": "string_current_01", "source_type": "modbus",
                "metadata": {"publish": True},
            }],
            "devices": [],
        }
        result = detach_inverter_string_devices(configuration)
        self.assertEqual(1, result["string_api_aliases"])
        self.assertEqual("current_ch_1", configuration["fields"][0]["json_key"])


if __name__ == "__main__":
    unittest.main()
