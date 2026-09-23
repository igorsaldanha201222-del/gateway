"""Orquestrador do gateway: polling, publicacao, comandos e hot reload."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .config import ConfigurationManager, configuration_sha256
from .decoder import build_payload
from .modbus import ModbusClient, ModbusError, create_client
from .mqtt import MQTTWorker, collapse_filters
from .storage import Storage


LOG = logging.getLogger(__name__)


class GatewayEngine:
    def __init__(self, manager: ConfigurationManager, storage: Storage, config_base: Path):
        self.manager = manager
        self.storage = storage
        self.config_base = config_base
        self.stop_event = threading.Event()
        self.run_event = threading.Event()
        self._lock = threading.RLock()
        self._reload_lock = threading.Lock()
        self._config = manager.current()
        self._channel_threads: dict[str, threading.Thread] = {}
        self._channel_clients: dict[str, ModbusClient] = {}
        self._channel_epoch = 0
        self._channel_health: dict[str, dict[str, Any]] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._device_health: dict[str, dict[str, Any]] = {}
        self._commands: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=1000)
        self._command_thread: threading.Thread | None = None
        self._mqtt: MQTTWorker | None = None
        self._started_at = time.monotonic()
        self._last_prune = 0.0
        self._last_heartbeat = 0.0
        self._supervisor: threading.Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._supervisor and self._supervisor.is_alive():
                return
            self.stop_event.clear()
            self._start_mqtt_locked()
            self._command_thread = threading.Thread(target=self._command_loop, name="command-worker", daemon=True)
            self._command_thread.start()
            self._supervisor = threading.Thread(target=self._supervisor_loop, name="gateway-supervisor", daemon=True)
            self._supervisor.start()
            if self._config.get("runtime", {}).get("enabled", False):
                self.run_event.set()
                self._start_channels_locked()
        self.storage.event("INFO", "runtime", "service.started", "Gateway Grid Co iniciado")

    def stop(self, timeout: float = 15) -> None:
        self.stop_event.set()
        self.run_event.clear()
        self._stop_channels(timeout)
        with self._lock:
            mqtt = self._mqtt
            self._mqtt = None
        if mqtt:
            mqtt.stop(timeout)
        if self._command_thread:
            self._command_thread.join(timeout)
        if self._supervisor:
            self._supervisor.join(timeout)
        self.storage.event("INFO", "runtime", "service.stopped", "Gateway V3 encerrado")

    def start_acquisition(self) -> None:
        with self._lock:
            self.run_event.set()
            self._start_channels_locked()
        self.storage.event("INFO", "runtime", "acquisition.started", "Aquisicao Modbus iniciada")

    def stop_acquisition(self) -> None:
        self.run_event.clear()
        self._stop_channels(10)
        self.storage.event("INFO", "runtime", "acquisition.stopped", "Aquisicao Modbus parada")

    def reload_configuration(self) -> None:
        with self._reload_lock:
            was_running = self.run_event.is_set()
            self._stop_channels(10)
            with self._lock:
                mqtt = self._mqtt
                self._mqtt = None
            if mqtt:
                mqtt.stop(10)
            with self._lock:
                self._config = self.manager.load()
                self._start_mqtt_locked()
                if was_running:
                    self._start_channels_locked()
            self.storage.event(
                "INFO", "configuration", "configuration.reloaded", "Configuracao V3 recarregada",
                {"sha256": configuration_sha256(self._config), "revision": self._config.get("revision")},
            )

    def apply_configuration(self, raw: dict[str, Any], origin: str) -> dict[str, Any]:
        result = self.manager.apply(raw, origin)
        self.reload_configuration()
        return {key: value for key, value in result.items() if key != "configuration"} | {
            "revision": result["configuration"].get("revision")
        }

    def _subscriptions(self) -> list[str]:
        general = self._config.get("general", {})
        filters = [
            str(general.get("command_subscribe_filter", "")),
            str(general.get("v3_configuration_topic", "")),
            f"dev/write/UFV/{(self._config.get('plant') or {}).get('id','gateway_generico')}/gateway/command",
        ]
        filters.extend(
            str(row.get("topic", "")) for row in self._config.get("topics", [])
            if str(row.get("purpose", "")).lower() == "command"
        )
        # Os topicos de comando por device ja caem no coringa da usina; assinar
        # cada um estourava o limite de 8 filtros da AWS IoT e derrubava a conexao.
        return collapse_filters(filters)

    def _start_mqtt_locked(self) -> None:
        settings = dict(self._config.get("mqtt", {}))
        if not settings.get("enabled", True):
            return
        settings.update(
            replay_interval_ms=int(self._config.get("storage", {}).get("replay_interval_ms", 250)),
            replay_batch_size=int(self._config.get("storage", {}).get("replay_batch_size", 25)),
            status_topic=str(self._config.get("general", {}).get("v3_status_topic", "")),
        )
        self._mqtt = MQTTWorker(self.storage, settings, self.config_base, self._subscriptions(), self._on_mqtt_message)
        self._mqtt.start()

    def _start_channels_locked(self) -> None:
        epoch = self._channel_epoch
        for channel in self._config.get("channels", []):
            channel_id = str(channel.get("id", ""))
            if not channel_id or not channel.get("enabled", True):
                continue
            thread = self._channel_threads.get(channel_id)
            if thread and thread.is_alive():
                continue
            thread = threading.Thread(
                target=self._channel_loop,
                args=(channel_id, epoch),
                name=f"modbus-{channel_id}"[:63],
                daemon=True,
            )
            self._channel_threads[channel_id] = thread
            thread.start()

    def _stop_channels(self, timeout: float) -> None:
        with self._lock:
            self._channel_epoch += 1
            clients = list(self._channel_clients.values())
            threads = list(self._channel_threads.values())
            self._channel_threads.clear()
            self._channel_clients.clear()
        for client in clients:
            client.close()
        deadline = time.monotonic() + timeout
        for thread in threads:
            if thread is threading.current_thread():
                continue
            thread.join(max(0, deadline - time.monotonic()))

    def _channel_loop(self, channel_id: str, epoch: int) -> None:
        config = self.manager.current()
        channel = next((row for row in config.get("channels", []) if str(row.get("id")) == channel_id), None)
        if channel is None:
            return
        devices = [
            row for row in config.get("devices", [])
            if row.get("enabled", True) and str(row.get("channel_id")) == channel_id
        ]
        if not devices:
            self._channel_health[channel_id] = {"state": "idle", "devices": 0, "last_cycle": ""}
            return
        client: ModbusClient | None = None
        next_poll = {str(device.get("id")): 0.0 for device in devices}
        next_publish = {str(device.get("id")): 0.0 for device in devices}
        error_logged: dict[str, float] = {}
        try:
            client = create_client(channel)
            with self._lock:
                self._channel_clients[channel_id] = client
            self._channel_health[channel_id] = {"state": "running", "devices": len(devices), "last_cycle": "", "errors": 0}
            while self.run_event.is_set() and not self.stop_event.is_set() and epoch == self._channel_epoch:
                did_work = False
                for device in devices:
                    if not self.run_event.is_set() or self.stop_event.is_set() or epoch != self._channel_epoch:
                        break
                    device_id = str(device.get("id", ""))
                    now = time.monotonic()
                    if now < next_poll[device_id]:
                        continue
                    did_work = True
                    poll_ms = max(100, int(device.get("poll_interval_ms", channel.get("poll_interval_ms", 1000))))
                    next_poll[device_id] = now + poll_ms / 1000.0
                    raw, errors = self._poll_device(config, client, device)
                    quality = 192 if not errors else 28
                    sampled_at = datetime.now(UTC)
                    with self._lock:
                        snapshots = {key: dict(value) for key, value in self._snapshots.items()}
                    try:
                        payload, diagnostics = build_payload(config, device, raw, quality, sampled_at, snapshots, self.storage)
                    except Exception as exc:
                        payload = {"timestamp": sampled_at.isoformat(), "communication_fault": 28}
                        diagnostics = {"valid_fields": 0, "invalid_fields": -1, "error": str(exc)}
                        errors.append(str(exc))
                        quality = 28
                    topic_info = self._telemetry_topic(config, device)
                    topic = topic_info[0]
                    snapshot = {
                        "payload": payload,
                        "quality": quality,
                        "epoch": sampled_at.timestamp(),
                        "sampled_at": sampled_at.isoformat(),
                        "diagnostics": diagnostics,
                    }
                    with self._lock:
                        self._snapshots[device_id] = snapshot
                        previous = self._device_health.get(device_id, {})
                        self._device_health[device_id] = {
                            "state": "online" if quality == 192 else "fault",
                            "quality": quality,
                            "last_sample": sampled_at.isoformat(),
                            "last_error": "; ".join(errors)[:1000],
                            "polls": int(previous.get("polls", 0)) + 1,
                            "errors": int(previous.get("errors", 0)) + (1 if errors else 0),
                        }
                    if topic:
                        self.storage.save_telemetry(device_id, topic, payload, quality)
                    if errors and now - error_logged.get(device_id, 0) >= 60:
                        self.storage.event("WARNING", "modbus", "device.poll_failed", f"Falha em {device_id}", {"errors": errors})
                        error_logged[device_id] = now
                    silent = bool((device.get("metadata") or {}).get("silent_telemetry") or device.get("read_without_publish"))
                    if topic and not silent and now >= next_publish[device_id]:
                        publish_ms = max(100, int(device.get("publish_interval_ms", 10000)))
                        next_publish[device_id] = now + publish_ms / 1000.0
                        body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                        self.storage.enqueue(topic, body, topic_info[1], topic_info[2])
                    inter_ms = int((device.get("metadata") or {}).get("inter_device_ms", (channel.get("metadata") or {}).get("inter_device_ms", 0)) or 0)
                    if inter_ms:
                        self.stop_event.wait(inter_ms / 1000.0)
                self._channel_health[channel_id]["last_cycle"] = datetime.now(UTC).isoformat()
                if not did_work:
                    self.stop_event.wait(0.05)
        except Exception as exc:
            self._channel_health[channel_id] = {"state": "failed", "devices": len(devices), "last_error": str(exc)}
            self.storage.event("ERROR", "modbus", "channel.failed", f"Canal {channel_id} falhou", {"error": str(exc)})
        finally:
            if client:
                client.close()
            with self._lock:
                if self._channel_clients.get(channel_id) is client:
                    self._channel_clients.pop(channel_id, None)

    @staticmethod
    def _poll_device(config: dict[str, Any], client: ModbusClient, device: Mapping[str, Any]) -> tuple[dict[str, list[int]], list[str]]:
        requests = [
            row for row in config.get("requests", [])
            if row.get("enabled", True) and str(row.get("template_id")) == str(device.get("template_id"))
        ]
        raw: dict[str, list[int]] = {}
        errors: list[str] = []
        unit_id = int(device.get("unit_id", 1))
        for request in requests:
            request_id = str(request.get("id", ""))
            retries = max(0, int(request.get("retries", 0) or 0))
            for attempt in range(retries + 1):
                try:
                    raw[request_id] = client.read(
                        unit_id,
                        int(request.get("function_code", 3)),
                        int(request.get("address", 0)),
                        int(request.get("quantity", 1)),
                    )
                    break
                except Exception as exc:
                    if attempt >= retries:
                        errors.append(f"{request_id}: {exc}")
        return raw, errors

    @staticmethod
    def _telemetry_topic(config: dict[str, Any], device: Mapping[str, Any]) -> tuple[str, int, bool]:
        for topic in config.get("topics", []):
            if str(topic.get("device_id")) == str(device.get("id")) and str(topic.get("purpose", "")).lower() == "telemetry":
                return str(topic.get("topic", "")), int(topic.get("qos", 1)), bool(topic.get("retain", False))
        general = config.get("general", {})
        if general.get("auto_generate_topics") or (general.get("metadata") or {}).get("auto_topics"):
            pattern = str(general.get("telemetry_topic_pattern") or (general.get("metadata") or {}).get("telemetry_pattern") or "dev/read/UFV/{plant}/{type}/{index}")
            plant = config.get("plant") or {}
            # {plant} sai do slug, que preserva maiuscula; {index} e' o numero
            # explicito do device, nunca a posicao dele na lista.
            plant_slug = str((plant.get("metadata") or {}).get("topic_slug") or plant.get("id", ""))
            index = (device.get("metadata") or {}).get("mqtt_topic_index", "")
            return pattern.format(
                plant=plant_slug,
                type=device.get("device_type", "device"),
                index=index,
                id=device.get("id", ""),
            ), int(general.get("default_qos", 1)), bool(general.get("default_retain", False))
        return "", 1, False

    def _on_mqtt_message(self, topic: str, payload: bytes) -> None:
        try:
            self._commands.put_nowait((topic, payload))
        except queue.Full:
            self.storage.event("ERROR", "command", "queue.full", "Fila de comandos cheia", {"topic": topic})

    def _command_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                topic, payload = self._commands.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._handle_command(topic, payload)
            except Exception as exc:
                self.storage.event("ERROR", "command", "command.failed", str(exc), {"topic": topic})
                self._feedback("failed", str(exc), request_id="")

    def _handle_command(self, topic: str, payload: bytes) -> None:
        if len(payload) > 8 * 1024 * 1024:
            raise ValueError("payload de comando excede 8 MiB")
        message = json.loads(payload.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("comando deve ser objeto JSON")
        request_id = str(message.get("request_id") or message.get("rid") or uuid.uuid4().hex[:16])[:96]
        general = self._config.get("general", {})
        if topic == str(general.get("v3_configuration_topic", "")):
            if not self._config.get("runtime", {}).get("allow_remote_configuration", False):
                raise PermissionError("configuracao remota V3 esta desabilitada")
            candidate = message.get("configuration")
            if not isinstance(candidate, dict):
                raise ValueError("configuration ausente")
            expected = str(message.get("sha256", "")).upper()
            actual = configuration_sha256(candidate)
            if not expected or expected != actual:
                raise ValueError("SHA-256 ausente ou divergente")
            result = self.apply_configuration(candidate, f"mqtt:{request_id}")
            self._feedback("success", "Configuracao V3 aplicada", request_id, result)
            return
        action = str(message.get("action") or message.get("cmd") or "").lower()
        gateway_topic = f"dev/write/UFV/{(self._config.get('plant') or {}).get('id','')}/gateway/command"
        if topic == gateway_topic or action in {"start", "stop"} and str(message.get("cmd", "")).lower() == "gateway":
            if action == "start":
                self.start_acquisition()
            elif action == "stop":
                self.stop_acquisition()
            else:
                raise ValueError("acao do gateway deve ser start ou stop")
            self._feedback("success", f"Gateway {action}", request_id)
            return
        device_id = self._device_for_command_topic(topic)
        if not device_id:
            raise ValueError("topico nao corresponde a um device comandavel")
        result = self.execute_device_command(device_id, action, message)
        self._feedback("success", "Comando executado", request_id, result)

    def _device_for_command_topic(self, topic_value: str) -> str:
        for topic in self._config.get("topics", []):
            if str(topic.get("purpose", "")).lower() == "command" and str(topic.get("topic")) == topic_value:
                return str(topic.get("device_id", ""))
        return ""

    def execute_device_command(self, device_id: str, action: str, message: Mapping[str, Any]) -> dict[str, Any]:
        device = next((row for row in self._config.get("devices", []) if str(row.get("id")) == device_id), None)
        if not device or not device.get("enabled", True) or not device.get("commands_enabled", False):
            raise PermissionError("device inexistente, desabilitado ou sem comandos")
        sequence = next((row for row in self._config.get("sequences", []) if row.get("enabled", True) and str(row.get("device_id")) == device_id and str(row.get("command_name", "")).lower() == action), None)
        if sequence:
            return self._execute_sequence(device, sequence)
        command = next(
            (row for row in self._config.get("commands", []) if row.get("enabled", True)
             and str(row.get("device_id")) == device_id
             and action in {str(row.get("id", "")).lower(), str(row.get("name", "")).lower(), str((row.get("metadata") or {}).get("action", "")).lower()}),
            None,
        )
        if not command:
            raise PermissionError("acao nao esta na allowlist de comandos")
        if command.get("use_payload"):
            raw = message.get("values", [message.get("value")])
            values = raw if isinstance(raw, list) else [raw]
        else:
            fixed = command.get("values", [command.get("fixed_value", 0)])
            values = fixed if isinstance(fixed, list) else [fixed]
        self._write(device, int(command.get("function_code", 6)), int(command.get("address", 0)), values)
        pulse_ms = int(command.get("pulse_ms", 0) or 0)
        if pulse_ms:
            self.stop_event.wait(pulse_ms / 1000.0)
            self._write(device, int(command.get("function_code", 6)), int(command.get("address", 0)), [0] * len(values))
        return {"device_id": device_id, "command_id": command.get("id"), "values": values}

    def _execute_sequence(self, device: Mapping[str, Any], sequence: Mapping[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        timeout = max(1.0, int(sequence.get("timeout_ms", 30000)) / 1000.0)
        completed = 0
        for step in sequence.get("steps", []):
            if time.monotonic() - started > timeout:
                raise TimeoutError("timeout da sequencia")
            kind = str(step.get("type", "write")).lower()
            if kind == "delay":
                self.stop_event.wait(max(0, int(step.get("delay_ms", 0))) / 1000.0)
            elif kind in {"write", "double_bit", "pulse"}:
                values = step.get("values", [step.get("value", 0)])
                values = values if isinstance(values, list) else [values]
                self._write(device, int(step.get("function_code", 6)), int(step.get("address", 0)), values)
                if kind == "pulse":
                    self.stop_event.wait(max(1, int(step.get("pulse_ms", 500))) / 1000.0)
                    self._write(device, int(step.get("function_code", 6)), int(step.get("address", 0)), [0] * len(values))
            elif kind in {"success"}:
                completed += 1
                break
            elif kind == "fail":
                raise RuntimeError(str(step.get("description") or "sequencia marcou falha"))
            else:
                raise ValueError(f"passo {kind} ainda exige verificacao especifica no template")
            completed += 1
        return {"device_id": device.get("id"), "sequence_id": sequence.get("id"), "steps_completed": completed}

    def _write(self, device: Mapping[str, Any], function_code: int, address: int, values: list[Any]) -> None:
        channel_id = str(device.get("channel_id", ""))
        with self._lock:
            client = self._channel_clients.get(channel_id)
        temporary = False
        if client is None:
            channel = next((row for row in self._config.get("channels", []) if str(row.get("id")) == channel_id), None)
            if channel is None:
                raise ModbusError("canal do comando nao encontrado")
            client = create_client(channel)
            temporary = True
        try:
            client.write(int(device.get("unit_id", 1)), function_code, address, values)
        finally:
            if temporary:
                client.close()

    def _feedback(self, status: str, message: str, request_id: str, detail: Any = None) -> None:
        topic = str(self._config.get("general", {}).get("command_feedback_topic", ""))
        if not topic:
            return
        body = json.dumps(
            {"v": 3, "request_id": request_id, "status": status, "message": message,
             "timestamp": datetime.now(UTC).isoformat(), "detail": detail or {}},
            ensure_ascii=False, separators=(",", ":"),
        )
        self.storage.enqueue(topic, body, 1, False)

    def _supervisor_loop(self) -> None:
        while not self.stop_event.wait(5):
            now = time.monotonic()
            if now - self._last_prune > 3600:
                result = self.storage.prune()
                if any(result.values()):
                    self.storage.event("INFO", "storage", "storage.pruned", "Retencao aplicada", result)
                self._last_prune = now
            if now - self._last_heartbeat > 60:
                topic = str(self._config.get("general", {}).get("v3_status_topic", ""))
                if topic:
                    summary = self.status(compact=True)
                    self.storage.enqueue(topic, json.dumps(summary, ensure_ascii=False, separators=(",", ":")), 1, True)
                self._last_heartbeat = now
            if self.run_event.is_set():
                with self._lock:
                    missing = [cid for cid, thread in self._channel_threads.items() if not thread.is_alive()]
                if missing:
                    self.storage.event("ERROR", "watchdog", "worker.dead", "Worker Modbus parou", {"channels": missing})

    def status(self, compact: bool = False) -> dict[str, Any]:
        with self._lock:
            channel_health = {key: dict(value) for key, value in self._channel_health.items()}
            device_health = {key: dict(value) for key, value in self._device_health.items()}
        mqtt_status = self._mqtt.status() if self._mqtt else {"connected": False, "state": "disabled"}
        result = {
            "version": 3,
            "service": "online" if not self.stop_event.is_set() else "stopping",
            "acquisition": "running" if self.run_event.is_set() else "stopped",
            "uptime_seconds": int(time.monotonic() - self._started_at),
            "configuration_id": self._config.get("configuration_id"),
            "configuration_revision": self._config.get("revision"),
            "configuration_sha256": configuration_sha256(self._config),
            "mqtt": mqtt_status,
            "buffer": self.storage.queue_stats(),
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if not compact:
            result["channels"] = channel_health
            result["devices"] = device_health
            result["storage"] = self.storage.health()
        return result
