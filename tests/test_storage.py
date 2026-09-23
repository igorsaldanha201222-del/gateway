from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gridco_gateway.storage import Storage


class StorageTests(unittest.TestCase):
    def test_fifo_ack_retry_and_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "gateway.db", {"max_buffer_messages": 100})
            first = storage.enqueue("a", "1", 1, False)
            second = storage.enqueue("b", "2", 0, True)
            rows = storage.next_messages(10)
            self.assertEqual([first, second], [row["id"] for row in rows])
            storage.retry(first, "offline", 100)
            self.assertEqual([], storage.next_messages(10))
            storage.acknowledge(first)
            self.assertEqual([second], [row["id"] for row in storage.next_messages(10)])
            storage.acknowledge(second)
            self.assertEqual(0, storage.queue_stats()["messages"])
            for index in range(110):
                storage.enqueue("x", str(index))
            self.assertEqual(100, storage.queue_stats()["messages"])
            self.assertTrue(storage.health()["healthy"])
            storage.close()

    def test_latest_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "gateway.db", {"max_buffer_messages": 100})
            storage.save_telemetry("dev", "topic", {"value": 42, "timestamp": "now"}, 192)
            row = storage.telemetry_for_device("dev")
            self.assertEqual(42, row["payload"]["value"])
            storage.close()

    def test_prune_commits_before_wal_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            storage = Storage(Path(temporary) / "gateway.db", {"max_buffer_messages": 100})
            storage.enqueue("topic", "payload")
            storage.event("INFO", "test", "test.event", "evento de teste")
            self.assertEqual(
                {"events": 0, "expired_messages": 0, "overflow_messages": 0},
                storage.prune(),
            )
            self.assertTrue(storage.health()["healthy"])
            storage.close()


if __name__ == "__main__":
    unittest.main()
