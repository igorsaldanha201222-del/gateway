from __future__ import annotations

import struct
import unittest
from pathlib import Path

from gridco_gateway.mqtt import MAX_FILTERS_PER_SUBSCRIBE, MQTTConnection, collapse_filters


class SubscribeLimitTests(unittest.TestCase):
    """A AWS IoT derruba a conexao com mais de 8 filtros num SUBSCRIBE."""

    def test_command_topics_covered_by_plant_wildcard_are_dropped(self):
        filters = ["dev/write/UFV/Betania/+/+", "dev/write/UFV/Betania/gateway/configuration/v3/set",
                   "dev/write/UFV/Betania/gateway/command"]
        filters += [f"dev/write/UFV/Betania/inverter/{n}" for n in range(1, 11)] + ["dev/write/UFV/Betania/relay/1"]
        self.assertEqual(
            ["dev/write/UFV/Betania/+/+", "dev/write/UFV/Betania/gateway/configuration/v3/set"],
            collapse_filters(filters + filters),
        )

    def test_subscribe_never_sends_more_than_eight_filters_per_packet(self):
        connection = MQTTConnection({"ack_timeout_seconds": 1}, Path("."), lambda topic, payload: None)
        sent: list[int] = []

        def fake_send(first_byte, payload):
            filters, offset = 0, 2
            while offset < len(payload):
                size = struct.unpack(">H", payload[offset:offset + 2])[0]
                offset += 2 + size + 1
                filters += 1
            sent.append(filters)

        def fake_read(timeout):
            return 9, 0, struct.pack(">H", connection.packet_id) + bytes([1] * sent[-1])

        connection._send = fake_send
        connection.read_packet = fake_read
        connection.subscribe([f"plant/device/{n}" for n in range(19)], 1)
        self.assertEqual([8, 8, 3], sent)
        self.assertTrue(all(count <= MAX_FILTERS_PER_SUBSCRIBE for count in sent))


if __name__ == "__main__":
    unittest.main()
