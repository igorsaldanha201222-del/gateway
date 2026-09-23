"""Cliente Modbus TCP/RTU autocontido para o runtime industrial V3."""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from typing import Any

try:  # Disponivel no Debian; mantem testes/importacao possiveis no Windows.
    import termios
except ImportError:  # pragma: no cover - exercitado somente fora de POSIX
    termios = None  # type: ignore[assignment]


class ModbusError(RuntimeError):
    pass


class ModbusTimeout(ModbusError):
    pass


class ModbusProtocolError(ModbusError):
    pass


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _read_values(function_code: int, payload: bytes, quantity: int) -> list[int]:
    if function_code & 0x80:
        code = payload[0] if payload else -1
        raise ModbusProtocolError(f"excecao Modbus {code}")
    if function_code in {1, 2}:
        if not payload:
            raise ModbusProtocolError("resposta de bits sem byte-count")
        byte_count = payload[0]
        data = payload[1:]
        if byte_count != len(data) or byte_count < (quantity + 7) // 8:
            raise ModbusProtocolError("byte-count invalido na resposta de bits")
        return [(data[index // 8] >> (index % 8)) & 1 for index in range(quantity)]
    if function_code in {3, 4}:
        if not payload:
            raise ModbusProtocolError("resposta de registradores sem byte-count")
        byte_count = payload[0]
        data = payload[1:]
        if byte_count != len(data) or byte_count != quantity * 2:
            raise ModbusProtocolError("byte-count invalido na resposta de registradores")
        return list(struct.unpack(f">{quantity}H", data))
    raise ModbusProtocolError(f"FC de leitura nao suportado: {function_code}")


def build_write_pdu(function_code: int, address: int, values: list[int | bool]) -> bytes:
    if not 0 <= address <= 65535:
        raise ModbusProtocolError("endereco fora de 0..65535")
    if not values:
        raise ModbusProtocolError("escrita sem valores")
    if function_code == 5:
        value = 0xFF00 if bool(values[0]) else 0x0000
        return struct.pack(">BHH", 5, address, value)
    if function_code == 6:
        return struct.pack(">BHH", 6, address, int(values[0]) & 0xFFFF)
    if function_code == 15:
        if len(values) > 1968:
            raise ModbusProtocolError("FC15 excede 1968 coils")
        packed = bytearray((len(values) + 7) // 8)
        for index, value in enumerate(values):
            if bool(value):
                packed[index // 8] |= 1 << (index % 8)
        return struct.pack(">BHHB", 15, address, len(values), len(packed)) + packed
    if function_code == 16:
        if len(values) > 123:
            raise ModbusProtocolError("FC16 excede 123 registradores")
        words = [int(value) & 0xFFFF for value in values]
        return struct.pack(">BHHB", 16, address, len(words), len(words) * 2) + struct.pack(f">{len(words)}H", *words)
    raise ModbusProtocolError("escrita aceita FC5, FC6, FC15 ou FC16")


def validate_write_response(request_pdu: bytes, response_fc: int, payload: bytes) -> None:
    request_fc = request_pdu[0]
    if response_fc & 0x80:
        code = payload[0] if payload else -1
        raise ModbusProtocolError(f"excecao Modbus {code}")
    if response_fc != request_fc:
        raise ModbusProtocolError("function code divergente na escrita")
    expected = request_pdu[1:5]
    if payload[:4] != expected:
        raise ModbusProtocolError("eco de escrita divergente")


class ModbusClient:
    def read(self, unit_id: int, function_code: int, address: int, quantity: int) -> list[int]:
        pdu = struct.pack(">BHH", function_code, address, quantity)
        response_fc, payload = self.transact(unit_id, pdu)
        if response_fc != function_code and not response_fc & 0x80:
            raise ModbusProtocolError("function code divergente")
        return _read_values(response_fc, payload, quantity)

    def write(self, unit_id: int, function_code: int, address: int, values: list[int | bool]) -> None:
        pdu = build_write_pdu(function_code, address, values)
        response_fc, payload = self.transact(unit_id, pdu)
        validate_write_response(pdu, response_fc, payload)

    def transact(self, unit_id: int, pdu: bytes) -> tuple[int, bytes]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class ModbusTCPClient(ModbusClient):
    def __init__(self, host: str, port: int = 502, timeout: float = 2.0):
        self.host = host
        self.port = int(port)
        self.timeout = max(0.1, float(timeout))
        self._socket: socket.socket | None = None
        self._transaction_id = 0
        self._lock = threading.RLock()

    def _connect(self) -> socket.socket:
        if self._socket is None:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._socket = sock
        return self._socket

    @staticmethod
    def _recv_exact(sock: socket.socket, count: int) -> bytes:
        result = bytearray()
        while len(result) < count:
            try:
                chunk = sock.recv(count - len(result))
            except socket.timeout as exc:
                raise ModbusTimeout("timeout aguardando resposta Modbus TCP") from exc
            if not chunk:
                raise ModbusProtocolError("conexao Modbus TCP encerrada")
            result.extend(chunk)
        return bytes(result)

    def transact(self, unit_id: int, pdu: bytes) -> tuple[int, bytes]:
        if not 0 <= int(unit_id) <= 255:
            raise ModbusProtocolError("Unit ID Modbus TCP fora de 0..255")
        with self._lock:
            self._transaction_id = (self._transaction_id % 65535) + 1
            transaction_id = self._transaction_id
            frame = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, int(unit_id)) + pdu
            try:
                sock = self._connect()
                sock.sendall(frame)
                header = self._recv_exact(sock, 7)
                response_id, protocol_id, length, response_unit = struct.unpack(">HHHB", header)
                if response_id != transaction_id or protocol_id != 0 or response_unit != int(unit_id):
                    raise ModbusProtocolError("cabecalho MBAP divergente")
                if not 2 <= length <= 254:
                    raise ModbusProtocolError("tamanho MBAP invalido")
                response = self._recv_exact(sock, length - 1)
                return response[0], response[1:]
            except (OSError, ModbusError):
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                try:
                    self._socket.close()
                finally:
                    self._socket = None


_BAUD_NAMES = {
    1200: "B1200", 2400: "B2400", 4800: "B4800", 9600: "B9600",
    19200: "B19200", 38400: "B38400", 57600: "B57600", 115200: "B115200",
}


class ModbusRTUClient(ModbusClient):
    def __init__(
        self,
        device: str,
        baudrate: int = 9600,
        data_bits: int = 8,
        parity: str | int = "none",
        stop_bits: int = 1,
        timeout: float = 2.0,
    ):
        self.device = device
        self.baudrate = int(baudrate)
        self.data_bits = int(data_bits)
        self.parity = str(parity).lower()
        self.stop_bits = int(stop_bits)
        self.timeout = max(0.1, float(timeout))
        self._fd: int | None = None
        self._lock = threading.RLock()
        self._last_frame_at = 0.0

    def _open(self) -> int:
        if termios is None:
            raise ModbusError("Modbus RTU requer POSIX/termios")
        if self._fd is not None:
            return self._fd
        fd = os.open(self.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[0] = 0
            attrs[1] = 0
            attrs[2] = termios.CLOCAL | termios.CREAD
            attrs[3] = 0
            attrs[2] |= {5: termios.CS5, 6: termios.CS6, 7: termios.CS7, 8: termios.CS8}[self.data_bits]
            if self.parity in {"even", "e", "2"}:
                attrs[2] |= termios.PARENB
            elif self.parity in {"odd", "o", "1"}:
                attrs[2] |= termios.PARENB | termios.PARODD
            if self.stop_bits == 2:
                attrs[2] |= termios.CSTOPB
            speed_name = _BAUD_NAMES.get(self.baudrate)
            if not speed_name or not hasattr(termios, speed_name):
                raise ModbusError(f"baudrate nao suportado: {self.baudrate}")
            speed = getattr(termios, speed_name)
            attrs[4] = speed
            attrs[5] = speed
            attrs[6][termios.VMIN] = 0
            attrs[6][termios.VTIME] = 1
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            termios.tcflush(fd, termios.TCIOFLUSH)
            self._fd = fd
            return fd
        except Exception:
            os.close(fd)
            raise

    def _read_frame(self, fd: int) -> bytes:
        deadline = time.monotonic() + self.timeout
        result = bytearray()
        expected: int | None = None
        while time.monotonic() < deadline:
            try:
                chunk = os.read(fd, 256)
            except BlockingIOError:
                chunk = b""
            if chunk:
                result.extend(chunk)
                if len(result) >= 3 and expected is None:
                    expected = 5 if result[1] & 0x80 else (5 + result[2] if result[1] in {1, 2, 3, 4} else 8)
                if expected is not None and len(result) >= expected:
                    return bytes(result[:expected])
            time.sleep(0.002)
        raise ModbusTimeout("timeout aguardando resposta Modbus RTU")

    def transact(self, unit_id: int, pdu: bytes) -> tuple[int, bytes]:
        if not 1 <= int(unit_id) <= 247:
            raise ModbusProtocolError("RTU exige Unit ID em 1..247")
        with self._lock:
            fd = self._open()
            bits_per_char = 1 + self.data_bits + (0 if self.parity in {"none", "n", "0"} else 1) + self.stop_bits
            silence = max(0.00175, 3.5 * bits_per_char / self.baudrate)
            remaining = silence - (time.monotonic() - self._last_frame_at)
            if remaining > 0:
                time.sleep(remaining)
            body = bytes([int(unit_id)]) + pdu
            checksum = crc16(body)
            frame = body + struct.pack("<H", checksum)
            try:
                if termios is not None:
                    termios.tcflush(fd, termios.TCIFLUSH)
                written = 0
                while written < len(frame):
                    written += os.write(fd, frame[written:])
                if termios is not None:
                    termios.tcdrain(fd)
                response = self._read_frame(fd)
                self._last_frame_at = time.monotonic()
                if len(response) < 5:
                    raise ModbusProtocolError("resposta RTU curta")
                received_crc = struct.unpack("<H", response[-2:])[0]
                if crc16(response[:-2]) != received_crc:
                    raise ModbusProtocolError("CRC16 invalido")
                if response[0] != int(unit_id):
                    raise ModbusProtocolError("Unit ID divergente")
                return response[1], response[2:-2]
            except (OSError, ModbusError):
                self.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                try:
                    os.close(self._fd)
                finally:
                    self._fd = None


def create_client(channel: dict[str, Any]) -> ModbusClient:
    timeout = max(0.1, float(channel.get("timeout_ms", 2000)) / 1000.0)
    transport = str(channel.get("transport", "tcp")).lower()
    if transport in {"tcp", "ethernet"}:
        return ModbusTCPClient(str(channel.get("ip", "")), int(channel.get("port", 502)), timeout)
    if transport in {"rtu", "serial"}:
        return ModbusRTUClient(
            str(channel.get("serial_device", "")),
            int(channel.get("baudrate", 9600)),
            int(channel.get("data_bits", 8)),
            channel.get("parity", "none"),
            int(channel.get("stop_bits", 1)),
            timeout,
        )
    raise ModbusError(f"transporte desconhecido: {transport}")
