from __future__ import annotations

import json
import struct
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from gridco_gateway.decoder import build_payload, decode_words
from gridco_gateway.storage import Storage


class DecoderTests(unittest.TestCase):
    def test_integer_float_bit_and_string(self) -> None:
        self.assertEqual(-2, decode_words([0xFFFE], {"data_type": "int16", "gain": 1, "offset": 0}))
        words = list(struct.unpack(">2H", struct.pack(">f", 12.5)))
        self.assertAlmostEqual(12.5, decode_words(words, {"data_type": "float32", "gain": 1, "offset": 0}))
        self.assertEqual(1, decode_words([0x0008], {"data_type": "bool", "bit_offset": 3}))
        self.assertEqual("ABCD", decode_words([0x4142, 0x4344], {"data_type": "string_ascii", "word_count": 2}))

    def test_word_swap(self) -> None:
        self.assertEqual(0x12345678, decode_words([0x5678, 0x1234], {
            "data_type": "uint32", "word_order": "swapped", "byte_order": "big", "gain": 1, "offset": 0,
        }))

    def test_pac3220_float64_energy_wh_to_kwh(self) -> None:
        words = list(struct.unpack(">4H", struct.pack(">d", 123456789.0)))
        field = {
            "data_type": "float64", "word_order": "normal", "byte_order": "big",
            "gain": 0.001, "offset": 0,
        }
        self.assertAlmostEqual(123456.789, decode_words(words, field), places=6)
        swapped = dict(field, word_order="swapped")
        self.assertAlmostEqual(123456.789, decode_words(list(reversed(words)), swapped), places=6)

    def test_offset_relativo_nao_e_confundido_com_buffer_global(self) -> None:
        """O caso Huawei: register_offset relativo que colide com buffer_offset.

        A request le 32016..32055 (buffer_offset 12, quantity 40). A tensao da
        string 7 esta em register_offset 12 - igual ao buffer_offset. Decidir
        campo a campo por 'offset >= buffer_offset' subtraia 12 e devolvia a
        leitura da string 1: valor plausivel, nao erro.
        """
        config = {
            "plant": {"timezone": "UTC"},
            "requests": [{"id": "req", "address": 32016, "buffer_offset": 12, "quantity": 40}],
            "fields": [
                {"id": f"s{n}", "template_id": "tpl", "request_id": "req",
                 "json_key": f"string_voltage_{n:02d}", "source_type": "modbus",
                 "data_type": "uint16", "register_offset": (n - 1) * 2, "gain": 1,
                 "offset": 0, "enabled": True, "metadata": {"publish": True, "decimals": 0}}
                for n in (1, 7)
            ],
        }
        device = {"id": "inv", "template_id": "tpl"}
        palavras = list(range(100, 140))          # posicao i vale 100 + i
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 100})
            try:
                payload, _ = build_payload(config, device, {"req": palavras}, 192,
                                           datetime(2026, 8, 7, tzinfo=UTC), {}, storage)
                self.assertEqual(100, payload["string_voltage_01"])   # posicao 0
                self.assertEqual(112, payload["string_voltage_07"])   # posicao 12, nao 0
            finally:
                storage.close()

    def test_offset_cumulativo_continua_funcionando(self) -> None:
        """O caso STI Norland: offset que so faz sentido como buffer global.

        buffer_offset 70, quantity 7: o offset 72 nao cabe em 0..6, entao a
        unica leitura coerente e a posicao 2 dentro do bloco.
        """
        config = {
            "plant": {"timezone": "UTC"},
            "requests": [{"id": "req", "address": 500, "buffer_offset": 70, "quantity": 7}],
            "fields": [
                {"id": "x", "template_id": "tpl", "request_id": "req", "json_key": "x",
                 "source_type": "modbus", "data_type": "uint16", "register_offset": 72,
                 "gain": 1, "offset": 0, "enabled": True,
                 "metadata": {"publish": True, "decimals": 0}},
            ],
        }
        device = {"id": "ncu", "template_id": "tpl"}
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 100})
            try:
                payload, _ = build_payload(config, device, {"req": [10, 11, 12, 13, 14, 15, 16]},
                                           192, datetime(2026, 8, 7, tzinfo=UTC), {}, storage)
                self.assertEqual(12, payload["x"])
            finally:
                storage.close()

    def test_payload_derived_linked_and_daily_energy(self) -> None:
        config = {
            "plant": {"timezone": "UTC"},
            # quantity e obrigatoria na configuracao real (a validacao exige
            # 1..limit) e e' o que permite decidir se register_offset e relativo
            # a request ou indice de buffer global. Aqui 10 nao cabe em 0..0,
            # entao e' cumulativo e vira posicao 0.
            "requests": [{"id": "req", "buffer_offset": 10, "quantity": 1}],
            "fields": [
                {"id": "a", "template_id": "tpl", "request_id": "req", "json_key": "total",
                 "source_type": "modbus", "data_type": "uint16", "register_offset": 10, "gain": 1,
                 "offset": 0, "enabled": True, "metadata": {"publish": True, "decimals": 0}},
                {"id": "mask", "template_id": "tpl", "json_key": "mask", "source_type": "static",
                 "data_type": "uint16", "source_expression": "3", "enabled": True,
                 "metadata": {"publish": False}},
                {"id": "extract", "template_id": "tpl", "json_key": "bits", "source_type": "derived",
                 "data_type": "uint16", "enabled": True,
                 "metadata": {"publish": True, "calc_operation": 14, "calc_field_a": "a", "calc_field_b": "mask", "decimals": 0}},
                {"id": "daily", "template_id": "tpl", "json_key": "daily", "source_type": "derived",
                 "data_type": "uint16", "enabled": True,
                 "metadata": {"publish": True, "calc_operation": 9, "calc_field_a": "a", "decimals": 0}},
                {"id": "linked", "template_id": "tpl", "json_key": "remote", "source_type": "linked_stringbox",
                 "data_type": "uint16", "source_expression": "current", "enabled": True,
                 "metadata": {"publish": True, "decimals": 1, "max_age_seconds": 30}}
            ],
        }
        device = {"id": "meter", "template_id": "tpl", "metadata": {"linked_string_box": "sb"}}
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 100})
            sampled = datetime(2026, 8, 7, tzinfo=UTC)
            snapshots = {"sb": {"payload": {"current": 5.5}, "quality": 192, "epoch": sampled.timestamp()}}
            first, _ = build_payload(config, device, {"req": [7]}, 192, sampled, snapshots, storage)
            second, _ = build_payload(config, device, {"req": [10]}, 192, sampled, snapshots, storage)
            self.assertEqual(3, first["bits"])
            self.assertEqual(5.5, first["remote"])
            self.assertEqual(0, first["daily"])
            self.assertEqual(3, second["daily"])
            storage.close()

    def test_independent_inverter_json_omits_string_keys_and_links(self) -> None:
        config = {
            "plant": {"timezone": "UTC"},
            "requests": [{"id": "inv-req", "buffer_offset": 0}],
            "fields": [
                {"id": "power", "template_id": "inv-tpl", "request_id": "inv-req",
                 "json_key": "active_power", "source_type": "modbus", "data_type": "uint16",
                 "register_offset": 0, "gain": 1, "offset": 0, "enabled": True,
                 "metadata": {"publish": True}},
                {"id": "string", "template_id": "inv-tpl", "json_key": "string_current_01",
                 "source_type": "linked_stringbox", "data_type": "uint16", "enabled": True,
                 "metadata": {"publish": True, "linked_source_key": "current_ch_1"}},
            ],
        }
        device = {
            "id": "inv-01", "device_type": "inverter", "template_id": "inv-tpl",
            "metadata": {
                "include_string_fields": False,
                "string_payload_mode": "independent",
                "linked_string_box": "longmax-01",
            },
        }
        sampled = datetime(2026, 8, 8, tzinfo=UTC)
        snapshots = {
            "longmax-01": {
                "payload": {"current_ch_1": 8.5}, "quality": 192,
                "epoch": sampled.timestamp(),
            }
        }
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 100})
            payload, diagnostics = build_payload(
                config, device, {"inv-req": [125]}, 192, sampled, snapshots, storage
            )
            self.assertEqual(125, payload["active_power"])
            self.assertNotIn("string_current_01", payload)
            self.assertNotIn("string_current_01", diagnostics["field_quality"])
            self.assertEqual("inverter", payload["device_type"])
            self.assertEqual(192, payload["communication_fault"])
            self.assertEqual("2026-08-08T00:00:00+00:00", payload["timestamp"])
            storage.close()

    def test_stringbox_publishes_current_ch_key(self) -> None:
        config = {
            "plant": {"timezone": "UTC"},
            "requests": [{"id": "sb-req", "buffer_offset": 0}],
            "fields": [{
                "id": "current", "template_id": "sb-tpl", "request_id": "sb-req",
                "json_key": "current_ch_1", "source_type": "modbus", "data_type": "uint16",
                "register_offset": 0, "gain": 0.001, "offset": 0, "enabled": True,
                "metadata": {"publish": True, "register_json_key": "current_ch_1", "decimals": 3},
            }],
        }
        device = {"id": "sb-01", "device_type": "stringbox", "template_id": "sb-tpl"}
        sampled = datetime(2026, 8, 8, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 100})
            payload, diagnostics = build_payload(
                config, device, {"sb-req": [8342]}, 192, sampled, {}, storage
            )
            self.assertEqual(8.342, payload["current_ch_1"])
            self.assertNotIn("string_current_01", payload)
            self.assertEqual(192, diagnostics["field_quality"]["current_ch_1"])
            storage.close()

if __name__ == "__main__":
    unittest.main()
