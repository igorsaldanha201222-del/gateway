"""Cliente MQTT 3.1.1 QoS 0/1 com TLS, reconexao e replay SQLite."""

from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .storage import Storage


# A AWS IoT Core aceita no maximo 8 filtros por SUBSCRIBE e, acima disso, fecha
# a conexao sem SUBACK nem motivo (medido: 8 passa, 9 derruba).
MAX_FILTERS_PER_SUBSCRIBE = 8


class MQTTError(RuntimeError):
    pass


def encode_remaining_length(value: int) -> bytes:
    if not 0 <= value <= 268435455:
        raise MQTTError("remaining length MQTT fora do limite")
    result = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        result.append(digit)
        if not value:
            return bytes(result)


def encode_utf8(value: str) -> bytes:
    body = value.encode("utf-8")
    if len(body) > 65535 or "\x00" in value:
        raise MQTTError("string MQTT invalida")
    return struct.pack(">H", len(body)) + body


def encode_binary(value: bytes) -> bytes:
    if len(value) > 65535:
        raise MQTTError("campo binario MQTT excede 65535 bytes")
    return struct.pack(">H", len(value)) + value


def topic_matches(topic_filter: str, topic: str) -> bool:
    filter_parts = topic_filter.split("/")
    topic_parts = topic.split("/")
    for index, part in enumerate(filter_parts):
        if part == "#":
            return index == len(filter_parts) - 1
        if index >= len(topic_parts) or (part != "+" and part != topic_parts[index]):
            return False
    return len(filter_parts) == len(topic_parts)


def collapse_filters(filters: list[str]) -> list[str]:
    """Remove repetidos e topicos exatos que um filtro coringa ja cobre."""
    unique = [item for item in dict.fromkeys(filters) if item]
    wildcards = [item for item in unique if "+" in item or "#" in item]
    return [
        item for item in unique
        if item in wildcards or not any(topic_matches(wildcard, item) for wildcard in wildcards)
    ]


class MQTTConnection:
    def __init__(self, settings: dict[str, Any], base_dir: Path, on_message: Callable[[str, bytes], None]):
        self.settings = settings
        self.base_dir = base_dir
        self.on_message = on_message
        self.socket: socket.socket | ssl.SSLSocket | None = None
        self.packet_id = 0
        self.pubacks: set[int] = set()
        self.last_io = time.monotonic()

    def _next_packet_id(self) -> int:
        self.packet_id = (self.packet_id % 65535) + 1
        return self.packet_id

    def _path(self, value: Any) -> str | None:
        text = str(value or "")
        if not text:
            return None
        if text.startswith("credential:"):
            name = text.partition(":")[2]
            if not name or name != Path(name).name:
                raise MQTTError("nome de credencial systemd invalido")
            directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
            if not directory:
                raise MQTTError("diretorio de credenciais systemd indisponivel")
            return str(Path(directory) / name)
        if text.startswith("embutido:"):
            # Certificado que viaja DENTRO do executavel. E' o caso da CA da
            # Grid Co: uma so para a frota inteira, e atualizada junto com o
            # binario - sem arquivo solto para alguem esquecer de copiar.
            nome = text.partition(":")[2]
            if not nome or nome != Path(nome).name:
                raise MQTTError("nome de arquivo embutido invalido")
            candidatos = []
            if getattr(sys, "_MEIPASS", None):
                candidatos.append(Path(sys._MEIPASS) / nome)
            if getattr(sys, "frozen", False):
                candidatos.append(Path(sys.executable).resolve().parent / nome)
            candidatos.append(Path(__file__).resolve().parents[1] / "config" / nome)
            candidatos.append(self.base_dir / nome)
            for c in candidatos:
                if c.is_file():
                    return str(c)
            raise MQTTError(
                f"certificado embutido '{nome}' nao encontrado: "
                + ", ".join(str(c) for c in candidatos))
        path = Path(text)
        return str(path if path.is_absolute() else self.base_dir / path)

    def _secret(self, environment_key: str, credential_key: str) -> str:
        value = os.environ.get(str(self.settings.get(environment_key, "")), "")
        credential_name = str(self.settings.get(credential_key, ""))
        directory = os.environ.get("CREDENTIALS_DIRECTORY", "")
        if credential_name and directory:
            target = Path(directory) / Path(credential_name).name
            try:
                value = target.read_text(encoding="utf-8")
            except FileNotFoundError:
                pass
            if len(value) > 65535:
                raise MQTTError("credencial MQTT excede 65535 caracteres")
        return value.strip()

    def connect(self) -> None:
        host = str(self.settings.get("host", "127.0.0.1"))
        port = int(self.settings.get("port", 1883))
        timeout = float(self.settings.get("connect_timeout_seconds", 10))
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        tls = self.settings.get("tls") if isinstance(self.settings.get("tls"), dict) else {}
        if tls.get("enabled"):
            context = ssl.create_default_context(cafile=self._path(tls.get("ca_file")))
            cert_file = self._path(tls.get("cert_file"))
            key_file = self._path(tls.get("key_file"))
            if cert_file:
                context.load_cert_chain(cert_file, key_file)
            if tls.get("insecure"):
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            hostname = str(tls.get("server_hostname") or host)
            sock = context.wrap_socket(sock, server_hostname=hostname)
        self.socket = sock
        keepalive = max(10, int(self.settings.get("keepalive_seconds", 45)))
        client_id = str(self.settings.get("client_id") or f"gridco-{os.getpid()}")
        username = self._secret("username_env", "username_credential")
        password = self._secret("password_env", "password_credential")
        flags = 0x02  # clean session
        payload = encode_utf8(client_id)
        status_topic = str(self.settings.get("status_topic", ""))
        if status_topic:
            flags |= 0x04 | 0x08 | 0x20  # Will ativo, QoS 1, retain.
            payload += encode_utf8(status_topic)
            payload += encode_binary(json.dumps({"v": 3, "online": False}, separators=(",", ":")).encode())
        if username:
            flags |= 0x80
            payload += encode_utf8(username)
        if password:
            flags |= 0x40
            payload += encode_utf8(password)
        variable = encode_utf8("MQTT") + bytes([4, flags]) + struct.pack(">H", keepalive)
        self._send(0x10, variable + payload)
        packet_type, _, response = self.read_packet(timeout)
        if packet_type != 2 or len(response) != 2:
            raise MQTTError("CONNACK ausente")
        if response[1] != 0:
            reasons = {1: "protocolo", 2: "client_id", 3: "broker indisponivel", 4: "credenciais", 5: "nao autorizado"}
            raise MQTTError(f"broker recusou conexao: {reasons.get(response[1], response[1])}")

    def close(self) -> None:
        if self.socket is not None:
            try:
                self.socket.close()
            finally:
                self.socket = None

    def _send(self, first_byte: int, payload: bytes) -> None:
        if self.socket is None:
            raise MQTTError("MQTT desconectado")
        try:
            self.socket.sendall(bytes([first_byte]) + encode_remaining_length(len(payload)) + payload)
            self.last_io = time.monotonic()
        except OSError as exc:
            raise MQTTError(f"falha de envio MQTT: {exc}") from exc

    def _recv_exact(self, count: int) -> bytes:
        if self.socket is None:
            raise MQTTError("MQTT desconectado")
        data = bytearray()
        while len(data) < count:
            chunk = self.socket.recv(count - len(data))
            if not chunk:
                raise MQTTError("broker encerrou a conexao")
            data.extend(chunk)
        self.last_io = time.monotonic()
        return bytes(data)

    def read_packet(self, timeout: float) -> tuple[int, int, bytes]:
        if self.socket is None:
            raise MQTTError("MQTT desconectado")
        self.socket.settimeout(max(0.01, timeout))
        first = self._recv_exact(1)[0]
        multiplier = 1
        remaining = 0
        for _ in range(4):
            digit = self._recv_exact(1)[0]
            remaining += (digit & 0x7F) * multiplier
            if not digit & 0x80:
                break
            multiplier *= 128
        else:
            raise MQTTError("remaining length MQTT malformado")
        if remaining > 8 * 1024 * 1024:
            raise MQTTError("pacote MQTT maior que 8 MiB")
        return first >> 4, first & 0x0F, self._recv_exact(remaining)

    def subscribe(self, filters: list[str], qos: int = 1) -> None:
        for start in range(0, len(filters), MAX_FILTERS_PER_SUBSCRIBE):
            self._subscribe_batch(filters[start:start + MAX_FILTERS_PER_SUBSCRIBE], qos)

    def _subscribe_batch(self, filters: list[str], qos: int) -> None:
        if not filters:
            return
        packet_id = self._next_packet_id()
        payload = struct.pack(">H", packet_id) + b"".join(encode_utf8(item) + bytes([qos]) for item in filters)
        self._send(0x82, payload)
        deadline = time.monotonic() + float(self.settings.get("ack_timeout_seconds", 15))
        while time.monotonic() < deadline:
            try:
                packet_type, _, body = self.read_packet(deadline - time.monotonic())
            except socket.timeout:
                continue
            if packet_type == 9 and len(body) >= 3 and struct.unpack(">H", body[:2])[0] == packet_id:
                if any(code == 0x80 for code in body[2:]):
                    raise MQTTError("broker recusou uma assinatura")
                return
            self._process(packet_type, 0, body)
        raise MQTTError("timeout aguardando SUBACK")

    def _process(self, packet_type: int, flags: int, body: bytes) -> None:
        if packet_type == 3:
            if len(body) < 2:
                raise MQTTError("PUBLISH sem topico")
            topic_length = struct.unpack(">H", body[:2])[0]
            if len(body) < 2 + topic_length:
                raise MQTTError("PUBLISH truncado")
            topic = body[2:2 + topic_length].decode("utf-8")
            offset = 2 + topic_length
            qos = (flags >> 1) & 0x03
            packet_id = None
            if qos:
                if len(body) < offset + 2:
                    raise MQTTError("PUBLISH QoS sem packet id")
                packet_id = struct.unpack(">H", body[offset:offset + 2])[0]
                offset += 2
            self.on_message(topic, body[offset:])
            if qos == 1 and packet_id is not None:
                self._send(0x40, struct.pack(">H", packet_id))
        elif packet_type == 4 and len(body) == 2:
            self.pubacks.add(struct.unpack(">H", body)[0])
        elif packet_type in {2, 9, 13}:
            return
        elif packet_type == 14:
            raise MQTTError("broker enviou DISCONNECT")

    def poll(self, timeout: float = 0.25) -> None:
        try:
            packet_type, flags, body = self.read_packet(timeout)
            self._process(packet_type, flags, body)
        except socket.timeout:
            pass

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        variable = encode_utf8(topic)
        packet_id = None
        if qos == 1:
            packet_id = self._next_packet_id()
            variable += struct.pack(">H", packet_id)
        first = 0x30 | (0x02 if qos == 1 else 0) | (0x01 if retain else 0)
        self._send(first, variable + payload)
        if packet_id is None:
            return
        deadline = time.monotonic() + float(self.settings.get("ack_timeout_seconds", 15))
        while time.monotonic() < deadline:
            if packet_id in self.pubacks:
                self.pubacks.remove(packet_id)
                return
            try:
                packet_type, flags, body = self.read_packet(deadline - time.monotonic())
                self._process(packet_type, flags, body)
            except socket.timeout:
                continue
        raise MQTTError("timeout aguardando PUBACK")

    def ping_if_needed(self) -> None:
        keepalive = max(10, int(self.settings.get("keepalive_seconds", 45)))
        if time.monotonic() - self.last_io >= keepalive * 0.6:
            self._send(0xC0, b"")


class MQTTWorker:
    def __init__(
        self,
        storage: Storage,
        settings: dict[str, Any],
        base_dir: Path,
        subscriptions: list[str],
        on_message: Callable[[str, bytes], None],
    ):
        self.storage = storage
        self.settings = settings
        self.base_dir = base_dir
        self.subscriptions = sorted(set(item for item in subscriptions if item))
        self.on_message = on_message
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._status_lock = threading.Lock()
        self._status: dict[str, Any] = {
            "connected": False, "state": "stopped", "last_error": "", "published": 0,
            "received": 0, "reconnects": 0, "connected_since": "",
        }

    def _set_status(self, **values: Any) -> None:
        with self._status_lock:
            self._status.update(values)

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._status)

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="mqtt-worker", daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 10) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)

    def _receive(self, topic: str, payload: bytes) -> None:
        self._set_status(received=int(self.status()["received"]) + 1)
        try:
            self.on_message(topic, payload)
        except Exception as exc:
            self.storage.event("ERROR", "mqtt", "message.handler", str(exc), {"topic": topic})

    def _online_status(self) -> tuple[str, bytes] | None:
        topic = str(self.settings.get("status_topic", ""))
        if not topic:
            return None
        payload = json.dumps({"v": 3, "online": True, "timestamp": time.time()}, separators=(",", ":")).encode()
        return topic, payload

    def _run(self) -> None:
        delay = max(1.0, float(self.settings.get("reconnect_min_seconds", 1)))
        maximum = max(delay, float(self.settings.get("reconnect_max_seconds", 60)))
        self._set_status(state="starting")
        while not self.stop_event.is_set():
            connection = MQTTConnection(self.settings, self.base_dir, self._receive)
            current: dict[str, Any] | None = None
            try:
                self._set_status(state="connecting")
                connection.connect()
                connection.subscribe(self.subscriptions, 1)
                self._set_status(
                    connected=True,
                    state="online",
                    last_error="",
                    connected_since=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    reconnects=int(self.status()["reconnects"]) + 1,
                )
                delay = max(1.0, float(self.settings.get("reconnect_min_seconds", 1)))
                online = self._online_status()
                if online:
                    connection.publish(online[0], online[1], 1, True)
                while not self.stop_event.is_set():
                    batch_size = max(1, int(self.settings.get("replay_batch_size", 25)))
                    messages = self.storage.next_messages(batch_size)
                    if not messages:
                        connection.poll(0.25)
                        connection.ping_if_needed()
                        continue
                    for current in messages:
                        if self.stop_event.is_set():
                            break
                        connection.publish(
                            current["topic"], current["payload"], int(current["qos"]), bool(current["retain"])
                        )
                        self.storage.acknowledge(int(current["id"]))
                        self._set_status(published=int(self.status()["published"]) + 1)
                        current = None
                        interval = max(0.0, float(self.settings.get("replay_interval_ms", 250)) / 1000.0)
                        self.stop_event.wait(interval)
            except Exception as exc:
                error = str(exc)
                if current is not None:
                    self.storage.retry(int(current["id"]), error, delay)
                self._set_status(connected=False, state="offline", last_error=error)
                self.storage.event("WARNING", "mqtt", "connection.failed", error)
                self.stop_event.wait(delay)
                delay = min(maximum, delay * 2)
            finally:
                connection.close()
        self._set_status(connected=False, state="stopped")
