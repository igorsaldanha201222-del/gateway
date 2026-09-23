from __future__ import annotations

import socket
import struct
import tempfile
import threading
import time
import unittest
import os
from pathlib import Path
from unittest.mock import patch

from gridco_gateway.mqtt import MQTTConnection, MQTTWorker, encode_remaining_length, topic_matches
from gridco_gateway.storage import Storage


class FakeBroker:
    def __init__(self):
        self.socket = socket.socket(); self.socket.bind(("127.0.0.1", 0)); self.socket.listen(1)
        self.port = self.socket.getsockname()[1]; self.received = []; self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    @staticmethod
    def packet(conn):
        first = conn.recv(1)
        if not first: raise ConnectionError()
        remaining=0; multiplier=1
        while True:
            digit=conn.recv(1)[0]; remaining+=(digit&127)*multiplier
            if not digit&128: break
            multiplier*=128
        body=b""
        while len(body)<remaining: body+=conn.recv(remaining-len(body))
        return first[0],body

    @staticmethod
    def send(conn, first, body): conn.sendall(bytes([first])+encode_remaining_length(len(body))+body)

    def run(self):
        try:
            conn,_=self.socket.accept()
            with conn:
                first,_=self.packet(conn); assert first>>4==1; self.send(conn,0x20,b"\x00\x00")
                first,body=self.packet(conn); assert first>>4==8; packet_id=body[:2]; self.send(conn,0x90,packet_id+b"\x01")
                while not self.stop.is_set():
                    first,body=self.packet(conn); kind=first>>4
                    if kind==3:
                        length=struct.unpack(">H",body[:2])[0]; topic=body[2:2+length].decode(); offset=2+length
                        if (first>>1)&3:
                            packet_id=body[offset:offset+2];offset+=2;self.send(conn,0x40,packet_id)
                        self.received.append((topic,body[offset:]))
                    elif kind==12:self.send(conn,0xD0,b"")
        except (OSError, ConnectionError, AssertionError): pass

    def start(self): self.thread.start()
    def close(self):
        self.stop.set()
        try:self.socket.close()
        except OSError:pass


class MQTTTests(unittest.TestCase):
    def test_helpers(self):
        self.assertEqual(b"\xC1\x02", encode_remaining_length(321))
        self.assertTrue(topic_matches("a/+/c", "a/b/c"))
        self.assertTrue(topic_matches("a/#", "a/b/c"))
        self.assertFalse(topic_matches("a/+/c", "a/b/d"))

    def test_systemd_credentials_are_resolved_without_putting_secrets_in_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mqtt-password").write_text("protected-secret\n", encoding="utf-8")
            connection = MQTTConnection({
                "password_env": "GRIDCO_TEST_PASSWORD",
                "password_credential": "mqtt-password",
            }, root, lambda *_: None)
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(root), "GRIDCO_TEST_PASSWORD": "fallback"}):
                self.assertEqual(connection._secret("password_env", "password_credential"), "protected-secret")
                self.assertEqual(connection._path("credential:mqtt-password"), str(root / "mqtt-password"))

    def test_worker_publishes_and_acknowledges_buffer(self):
        broker=FakeBroker();broker.start()
        with tempfile.TemporaryDirectory() as temporary:
            storage=Storage(Path(temporary)/"gateway.db",{"max_buffer_messages":100})
            storage.enqueue("telemetry/test",'{"value":1}',1,False)
            worker=MQTTWorker(storage,{"host":"127.0.0.1","port":broker.port,"client_id":"test",
                "keepalive_seconds":10,"connect_timeout_seconds":1,"ack_timeout_seconds":1,
                "reconnect_min_seconds":0.1,"reconnect_max_seconds":0.2,"replay_interval_ms":1,
                "replay_batch_size":5,"tls":{"enabled":False}},Path(temporary),["commands/#"],lambda *_:None)
            worker.start()
            deadline=time.time()+3
            while time.time()<deadline and storage.queue_stats()["messages"]: time.sleep(.02)
            worker.stop(2)
            self.assertEqual(0,storage.queue_stats()["messages"])
            self.assertEqual(b'{"value":1}',broker.received[0][1])
            storage.close()
        broker.close()


if __name__ == "__main__": unittest.main()
