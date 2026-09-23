from __future__ import annotations

import socket
import struct
import threading
import unittest

from gridco_gateway.modbus import ModbusTCPClient, crc16


class FakeModbusServer:
    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    @staticmethod
    def exact(conn, count):
        data = b""
        while len(data) < count:
            data += conn.recv(count - len(data))
        return data

    def run(self):
        conn, _ = self.socket.accept()
        with conn:
            while not self.stop.is_set():
                try:
                    header = self.exact(conn, 7)
                except (OSError, ConnectionError):
                    break
                transaction, protocol, length, unit = struct.unpack(">HHHB", header)
                pdu = self.exact(conn, length - 1)
                if pdu[0] == 3:
                    quantity = struct.unpack(">H", pdu[3:5])[0]
                    body = bytes([3, quantity * 2]) + struct.pack(f">{quantity}H", *range(1, quantity + 1))
                else:
                    body = pdu[:5]
                conn.sendall(struct.pack(">HHHB", transaction, protocol, len(body) + 1, unit) + body)

    def close(self):
        self.stop.set()
        try:
            self.socket.close()
        except OSError:
            pass


class ModbusTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(0xCDC5, crc16(bytes.fromhex("01030000000A")))

    def test_tcp_read_and_write(self):
        server = FakeModbusServer(); server.start()
        client = ModbusTCPClient("127.0.0.1", server.port, 1)
        self.assertEqual([1, 2, 3], client.read(1, 3, 0, 3))
        self.assertEqual([1], client.read(255, 3, 0, 1))
        client.write(1, 6, 100, [55])
        client.close(); server.close()


if __name__ == "__main__":
    unittest.main()
