from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from gridco_gateway.storage import Storage

STATUS = "dev/read/UFV/Betania/gateway/status"
TELEMETRY = "dev/read/UFV/Betania/inverter/1"


class RetainedQueueTests(unittest.TestCase):
    """So o ultimo retido de cada topico fica na fila; telemetria nao e tocada."""

    def test_new_retained_message_replaces_the_queued_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "db.sqlite", {"max_buffer_messages": 1000})
            storage.enqueue(STATUS, '{"n":1}', 1, True)
            storage.enqueue(TELEMETRY, '{"p":1}', 1, False)
            storage.enqueue(STATUS, '{"n":2}', 1, True)
            storage.enqueue(TELEMETRY, '{"p":2}', 1, False)
            queued = [(row["topic"], bytes(row["payload"])) for row in storage.next_messages(10)]
            storage.close()
        self.assertEqual([(TELEMETRY, b'{"p":1}'), (STATUS, b'{"n":2}'), (TELEMETRY, b'{"p":2}')], queued)

    def test_backlog_of_retained_messages_is_compacted_on_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "db.sqlite"
            Storage(path, {"max_buffer_messages": 1000}).close()
            raw = sqlite3.connect(path)
            rows = [(STATUS, f'{{"n":{n}}}'.encode(), 1, 1, "t") for n in range(5)]
            rows += [(TELEMETRY, b'{"p":1}', 1, 0, "t"), (TELEMETRY, b'{"p":2}', 1, 0, "t")]
            raw.executemany("INSERT INTO mqtt_queue(topic,payload,qos,retain,created_at) VALUES(?,?,?,?,?)", rows)
            raw.commit()
            raw.close()
            storage = Storage(path, {"max_buffer_messages": 1000})
            queued = [(row["topic"], bytes(row["payload"])) for row in storage.next_messages(10)]
            storage.close()
        self.assertEqual([(STATUS, b'{"n":4}'), (TELEMETRY, b'{"p":1}'), (TELEMETRY, b'{"p":2}')], queued)


if __name__ == "__main__":
    unittest.main()
