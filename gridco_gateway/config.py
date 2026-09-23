"""Carregamento, normalizacao e validacao transacional da configuracao V3."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
LIST_SECTIONS = (
    "channels",
    "templates",
    "requests",
    "fields",
    "devices",
    "topics",
    "commands",
    "alarms",
    "events",
    "sequences",
    "pid",
)
LIMITS = {
    "channels": 64,
    "templates": 256,
    "requests": 4096,
    "fields": 16384,
    "devices": 1024,
    "topics": 4096,
    "commands": 4096,
    "alarms": 4096,
    "events": 4096,
    "sequences": 512,
    "pid": 128,
}
NUMERIC_TYPES = {
    "uint16",
    "int16",
    "uint32",
    "int32",
    "uint64",
    "int64",
    "float32",
    "float64",
    "bool",
    "bool_bit",
    "bool_nonzero",
    "bcd64",
}
TEXT_TYPES = {"string", "ascii", "string_ascii", "hex", "hex_digits", "serial_hex"}
SOURCE_TYPES = {
    "modbus",
    "static",
    "timestamp",
    "quality",
    "derived",
    "linked",
    "linked_stringbox",
    "linked_string_box",
    "related",
    "cache",
}
# Operacoes cujos operandos A e B sao limites de uma faixa de campos, nao valores.
RANGE_OPERATIONS = {15, 16}
DIRECT_SOURCES = {"modbus", "static"}


@dataclass(frozen=True)
class ValidationMessage:
    level: str
    path: str
    message: str
    code: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class ConfigurationError(ValueError):
    def __init__(self, messages: Iterable[ValidationMessage]):
        self.messages = tuple(messages)
        errors = [m for m in self.messages if m.level == "error"]
        text = errors[0].message if errors else "configuracao invalida"
        super().__init__(text)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def configuration_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest().upper()


def _deep_defaults(target: dict[str, Any], defaults: dict[str, Any]) -> None:
    for key, value in defaults.items():
        if key not in target:
            target[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_defaults(target[key], value)


def normalize_configuration(raw: dict[str, Any]) -> dict[str, Any]:
    """Retorna copia normalizada sem modificar o objeto recebido.

    O contrato de planta V1/V2 e aceito diretamente. As secoes ``runtime``,
    ``mqtt``, ``storage`` e ``web`` sao extensoes exclusivas do runtime V3.
    """

    cfg = copy.deepcopy(raw)
    for section in LIST_SECTIONS:
        cfg.setdefault(section, [])
    cfg.setdefault("auto_reclosing", {"enabled": False})
    cfg.setdefault("plant", {})
    cfg.setdefault("general", {})
    plant_id = str(cfg["plant"].get("id") or cfg["general"].get("plant_id") or "gateway_generico")
    gateway_id = str(cfg["general"].get("gateway_id") or f"GRIDCO-{plant_id}")
    defaults = {
        "schema_version": "1.0.0",
        "configuration_id": f"{plant_id}-gridco-v1",
        "revision": 1,
        "runtime": {
            "enabled": False,
            "worker_watchdog_seconds": 30,
            "shutdown_timeout_seconds": 15,
            "log_level": "INFO",
        },
        "mqtt": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": 1883,
            "client_id": gateway_id,
            "keepalive_seconds": 45,
            "connect_timeout_seconds": 10,
            "ack_timeout_seconds": 15,
            "reconnect_min_seconds": 1,
            "reconnect_max_seconds": 60,
            "username_env": "GRIDCO_MQTT_USERNAME",
            "password_env": "GRIDCO_MQTT_PASSWORD",
            "username_credential": "mqtt-username",
            "password_credential": "mqtt-password",
            "tls": {
                "enabled": False,
                "ca_file": "",
                "cert_file": "",
                "key_file": "",
                "server_hostname": "",
                "insecure": False,
            },
        },
        "storage": {
            "database": "gateway.db",
            "max_buffer_messages": 100000,
            "retention_days": 30,
            "replay_interval_ms": 250,
            "replay_batch_size": 25,
            "max_attempts": 0,
            "event_retention_days": 30,
        },
        "web": {"host": "0.0.0.0", "port": 8080, "max_body_bytes": 8388608},
    }
    _deep_defaults(cfg, defaults)
    cfg["plant"].setdefault("id", plant_id)
    cfg["plant"].setdefault("name", plant_id)
    cfg["plant"].setdefault("timezone", cfg["general"].get("timezone", "America/Sao_Paulo"))
    cfg["general"].setdefault("gateway_id", gateway_id)
    cfg["general"].setdefault("plant_id", plant_id)
    cfg["general"].setdefault("default_qos", cfg["general"].get("mqtt_qos", 1))
    cfg["general"].setdefault("default_retain", cfg["general"].get("mqtt_retain", False))
    cfg["general"].setdefault(
        "command_subscribe_filter", f"dev/write/UFV/{plant_id}/+/+"
    )
    cfg["general"].setdefault(
        "command_feedback_topic", f"dev/write/UFV/{plant_id}/feedback"
    )
    cfg["general"].setdefault(
        "v3_configuration_topic", f"dev/write/UFV/{plant_id}/gateway/configuration/v3/set"
    )
    cfg["general"].setdefault(
        "v3_status_topic", f"dev/read/UFV/{plant_id}/gateway/status"
    )
    return cfg


def _records(cfg: dict[str, Any], section: str, out: list[ValidationMessage]) -> list[dict[str, Any]]:
    value = cfg.get(section, [])
    if not isinstance(value, list):
        out.append(ValidationMessage("error", section, "deve ser uma lista", "type.list"))
        return []
    if len(value) > LIMITS[section]:
        out.append(
            ValidationMessage(
                "error", section, f"possui {len(value)} registros; limite V3 = {LIMITS[section]}", "limit.records"
            )
        )
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, item in enumerate(value):
        path = f"{section}[{index}]"
        if not isinstance(item, dict):
            out.append(ValidationMessage("error", path, "registro deve ser objeto JSON", "type.object"))
            continue
        record_id = str(item.get("id", ""))
        if not record_id:
            out.append(ValidationMessage("error", f"{path}.id", "id obrigatorio", "id.required"))
        elif not SAFE_ID.fullmatch(record_id):
            out.append(ValidationMessage("error", f"{path}.id", "id contem caracteres inseguros", "id.unsafe"))
        elif record_id in ids:
            out.append(ValidationMessage("error", f"{path}.id", f"id duplicado: {record_id}", "id.duplicate"))
        ids.add(record_id)
        rows.append(item)
    return rows


def validate_configuration(raw: Any) -> list[ValidationMessage]:
    messages: list[ValidationMessage] = []
    if not isinstance(raw, dict):
        return [ValidationMessage("error", "$", "a raiz deve ser um objeto JSON", "type.object")]
    try:
        cfg = normalize_configuration(raw)
    except (TypeError, ValueError) as exc:
        return [ValidationMessage("error", "$", f"falha ao normalizar: {exc}", "normalize.failed")]

    if not str(cfg.get("schema_version", "")).startswith("1."):
        messages.append(
            ValidationMessage("error", "schema_version", "somente o contrato de planta 1.x e aceito", "schema.unsupported")
        )
    for key in ("plant", "general", "runtime", "mqtt", "storage", "web"):
        if not isinstance(cfg.get(key), dict):
            messages.append(ValidationMessage("error", key, "deve ser um objeto JSON", "type.object"))

    rows = {section: _records(cfg, section, messages) for section in LIST_SECTIONS}
    ids = {section: {str(x.get("id", "")) for x in values} for section, values in rows.items()}

    plant_id = str((cfg.get("plant") or {}).get("id", ""))
    if not SAFE_ID.fullmatch(plant_id):
        messages.append(ValidationMessage("error", "plant.id", "identificador de usina invalido", "id.unsafe"))

    for index, channel in enumerate(rows["channels"]):
        path = f"channels[{index}]"
        transport = str(channel.get("transport", "tcp")).lower()
        if transport not in {"tcp", "ethernet", "rtu", "serial"}:
            messages.append(ValidationMessage("error", f"{path}.transport", "use tcp ou rtu", "channel.transport"))
        if transport in {"tcp", "ethernet"}:
            if not str(channel.get("ip", "")):
                messages.append(ValidationMessage("error", f"{path}.ip", "IP/hostname obrigatorio", "channel.ip"))
            port = int(channel.get("port", 502) or 0)
            if not 1 <= port <= 65535:
                messages.append(ValidationMessage("error", f"{path}.port", "porta fora de 1..65535", "range.port"))
        else:
            serial_device = channel.get("serial_device")
            if not serial_device:
                messages.append(
                    ValidationMessage(
                        "error",
                        f"{path}.serial_device",
                        "no GRIDCO informe /dev/serial/by-id/... ou /dev/ttyS...",
                        "channel.serial_device",
                    )
                )
            elif not str(serial_device).startswith("/dev/"):
                messages.append(ValidationMessage("error", f"{path}.serial_device", "caminho deve iniciar com /dev/", "path.device"))
            if int(channel.get("baudrate", 9600) or 0) not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
                messages.append(ValidationMessage("error", f"{path}.baudrate", "baudrate nao suportado", "channel.baudrate"))
            if str(channel.get("parity", "none")).lower() not in {"none", "n", "even", "e", "odd", "o", "0", "1", "2"}:
                messages.append(ValidationMessage("error", f"{path}.parity", "paridade invalida", "channel.parity"))

    requests_by_id = {str(x.get("id")): x for x in rows["requests"]}
    fields_by_id = {str(x.get("id")): x for x in rows["fields"]}
    channels_by_id = {str(x.get("id")): x for x in rows["channels"]}
    for index, request in enumerate(rows["requests"]):
        path = f"requests[{index}]"
        if str(request.get("template_id", "")) not in ids["templates"]:
            messages.append(ValidationMessage("error", f"{path}.template_id", "template inexistente", "reference.template"))
        fc = int(request.get("function_code", 3) or 0)
        quantity = int(request.get("quantity", 0) or 0)
        limit = 2000 if fc in {1, 2} else 125
        if fc not in {1, 2, 3, 4}:
            messages.append(ValidationMessage("error", f"{path}.function_code", "leitura aceita FC1, FC2, FC3 ou FC4", "modbus.read_fc"))
        if not 1 <= quantity <= limit:
            messages.append(ValidationMessage("error", f"{path}.quantity", f"quantidade deve estar em 1..{limit}", "modbus.quantity"))
        address = int(request.get("address", -1) if request.get("address") is not None else -1)
        if not 0 <= address <= 65535:
            messages.append(ValidationMessage("error", f"{path}.address", "endereco deve estar em 0..65535", "modbus.address"))

    word_widths = {"uint16": 1, "int16": 1, "bool": 1, "bool_bit": 1, "bool_nonzero": 1,
                   "uint32": 2, "int32": 2, "float32": 2, "bcd64": 4,
                   "uint64": 4, "int64": 4, "float64": 4}
    for index, field in enumerate(rows["fields"]):
        path = f"fields[{index}]"
        template_id = str(field.get("template_id", ""))
        if template_id not in ids["templates"]:
            messages.append(ValidationMessage("error", f"{path}.template_id", "template inexistente", "reference.template"))
        source = str(field.get("source_type", "modbus")).lower()
        if source not in SOURCE_TYPES:
            messages.append(ValidationMessage("error", f"{path}.source_type", f"origem nao suportada: {source}", "field.source"))
        data_type = str(field.get("data_type", "uint16")).lower()
        if data_type not in NUMERIC_TYPES | TEXT_TYPES:
            messages.append(ValidationMessage("error", f"{path}.data_type", f"tipo nao suportado: {data_type}", "field.data_type"))
        if source == "modbus":
            request_id = str(field.get("request_id", ""))
            request = requests_by_id.get(request_id)
            if request is None:
                messages.append(ValidationMessage("error", f"{path}.request_id", "request inexistente", "reference.request"))
            elif str(request.get("template_id")) != template_id:
                messages.append(ValidationMessage("error", f"{path}.request_id", "request pertence a outro template", "reference.request_template"))
            else:
                raw_offset = int(field.get("register_offset", 0) or 0)
                buffer_offset = int(request.get("buffer_offset", 0) or 0)
                # O contrato CODESYS V2 tambem aceita offsets no buffer global.
                # No Python cada request tem seu proprio buffer, portanto convertemos
                # para a posicao relativa sem exigir migracao dos JSONs existentes.
                offset = raw_offset - buffer_offset if buffer_offset and raw_offset >= buffer_offset else raw_offset
                width = int(field.get("word_count", word_widths.get(data_type, 1)) or 1)
                if offset < 0 or offset + width > int(request.get("quantity", 0) or 0):
                    messages.append(ValidationMessage("error", f"{path}.register_offset", "campo ultrapassa a resposta da request", "field.bounds"))
        if not str(field.get("json_key", "")):
            messages.append(ValidationMessage("error", f"{path}.json_key", "chave JSON obrigatoria", "field.json_key"))
        if source == "derived":
            meta = field.get("metadata") if isinstance(field.get("metadata"), dict) else {}
            # Os JSONs V2 também marcam valores de sistema (nome do device,
            # domínio, status simplificado etc.) como ``derived``. O decoder
            # resolve ``system_value`` antes do motor de cálculos.
            if int(meta.get("system_value", 0) or 0):
                continue
            operation = int(meta.get("calc_operation", 0) or 0)
            if operation not in set(range(1, 17)):
                messages.append(ValidationMessage("error", f"{path}.metadata.calc_operation", "operacao calculada deve estar em 1..16", "field.calc"))
            if operation in RANGE_OPERATIONS:
                # 15 (soma) e 16 (media) seguem o firmware WAGO: A e B limitam uma
                # faixa contigua de campos do template, cada membro e leitura direta
                # e o proprio campo nao pode estar dentro dela.
                order = [str(item.get("id", "")) for item in rows["fields"] if str(item.get("template_id", "")) == template_id]
                first_ref, last_ref = str(meta.get("calc_field_a", "")), str(meta.get("calc_field_b", ""))
                if first_ref in order and last_ref in order:
                    first, last = order.index(first_ref), order.index(last_ref)
                    members = order[first:last + 1]
                    if first > last or str(field.get("id", "")) in members:
                        messages.append(ValidationMessage("error", f"{path}.metadata", "faixa A..B invalida: A antes de B e o proprio campo fora dela", "field.calc_range"))
                    elif any(str(fields_by_id[member].get("source_type", "modbus")).lower() not in DIRECT_SOURCES for member in members):
                        messages.append(ValidationMessage("error", f"{path}.metadata", "faixa de soma/media aceita somente leitura direta", "field.calc_range"))
            for name in ("calc_field_a", "calc_field_b"):
                ref = str(meta.get(name, ""))
                if name == "calc_field_b" and operation in {1, 9}:
                    continue
                if not ref or ref not in fields_by_id:
                    messages.append(ValidationMessage("error", f"{path}.metadata.{name}", "campo calculado referenciado inexistente", "reference.field"))

    for index, device in enumerate(rows["devices"]):
        path = f"devices[{index}]"
        if str(device.get("channel_id", "")) not in ids["channels"]:
            messages.append(ValidationMessage("error", f"{path}.channel_id", "canal inexistente", "reference.channel"))
        if str(device.get("template_id", "")) not in ids["templates"]:
            messages.append(ValidationMessage("error", f"{path}.template_id", "template inexistente", "reference.template"))
        unit_id = int(device.get("unit_id", 0) or 0)
        channel = channels_by_id.get(str(device.get("channel_id", "")), {})
        transport = str(channel.get("transport", "tcp")).lower()
        minimum, maximum = (1, 247) if transport in {"rtu", "serial"} else (0, 255)
        if not minimum <= unit_id <= maximum:
            messages.append(ValidationMessage("error", f"{path}.unit_id", f"Unit ID deve estar em {minimum}..{maximum} para {transport}", "modbus.unit_id"))
        for key in ("poll_interval_ms", "publish_interval_ms", "stale_timeout_ms"):
            if int(device.get(key, 1000) or 0) < 100:
                messages.append(ValidationMessage("error", f"{path}.{key}", "intervalo minimo = 100 ms", "range.interval"))

    telemetry_topics: set[str] = set()
    for index, topic in enumerate(rows["topics"]):
        path = f"topics[{index}]"
        if str(topic.get("device_id", "")) not in ids["devices"]:
            messages.append(ValidationMessage("error", f"{path}.device_id", "device inexistente", "reference.device"))
        value = str(topic.get("topic", ""))
        if not value or "\x00" in value or len(value.encode("utf-8")) > 65535:
            messages.append(ValidationMessage("error", f"{path}.topic", "topico MQTT invalido", "mqtt.topic"))
        if str(topic.get("purpose", "")).lower() == "telemetry":
            if value in telemetry_topics:
                messages.append(ValidationMessage("warning", f"{path}.topic", "topico de telemetria duplicado", "mqtt.topic_duplicate"))
            telemetry_topics.add(value)
        qos = int(topic.get("qos", 1) or 0)
        if qos not in {0, 1}:
            messages.append(ValidationMessage("error", f"{path}.qos", "runtime V3 aceita QoS 0 ou 1", "mqtt.qos"))

    for index, command in enumerate(rows["commands"]):
        path = f"commands[{index}]"
        if str(command.get("device_id", "")) not in ids["devices"]:
            messages.append(ValidationMessage("error", f"{path}.device_id", "device inexistente", "reference.device"))
        fc = int(command.get("function_code", 0) or 0)
        if fc not in {5, 6, 15, 16}:
            messages.append(ValidationMessage("error", f"{path}.function_code", "escrita aceita somente FC5, FC6, FC15 ou FC16", "modbus.write_fc"))
        if not 0 <= int(command.get("address", -1) if command.get("address") is not None else -1) <= 65535:
            messages.append(ValidationMessage("error", f"{path}.address", "endereco deve estar em 0..65535", "modbus.address"))

    for index, sequence in enumerate(rows["sequences"]):
        path = f"sequences[{index}]"
        if str(sequence.get("device_id", "")) not in ids["devices"]:
            messages.append(ValidationMessage("error", f"{path}.device_id", "device inexistente", "reference.device"))
        steps = sequence.get("steps", [])
        if not isinstance(steps, list) or len(steps) > 64:
            messages.append(ValidationMessage("error", f"{path}.steps", "steps deve ser lista com no maximo 64 itens", "sequence.steps"))
        elif any(str(step.get("type", "write")).lower() not in {"write", "double_bit", "delay", "pulse", "success", "fail"}
                 for step in steps if isinstance(step, dict)):
            messages.append(ValidationMessage("error", f"{path}.steps", "a V3 inicial aceita write, double_bit, delay, pulse, success e fail", "sequence.operation"))

    if any(item.get("enabled", True) for item in rows["alarms"]):
        messages.append(ValidationMessage("error", "alarms", "motor de alarmes deve ser migrado e validado antes de habilitar", "feature.alarms_pending"))
    if any(item.get("enabled", True) for item in rows["events"]):
        messages.append(ValidationMessage("error", "events", "motor de eventos deve ser migrado e validado antes de habilitar", "feature.events_pending"))
    if any(item.get("enabled", False) for item in rows["pid"]):
        messages.append(ValidationMessage("error", "pid", "PID fica bloqueado no runtime Linux ate validacao de controle", "feature.pid_safety_gate"))
    auto_reclosing = cfg.get("auto_reclosing", {})
    if isinstance(auto_reclosing, dict) and auto_reclosing.get("enabled", False):
        messages.append(ValidationMessage("error", "auto_reclosing.enabled", "religamento automatico nao pode ser migrado sem estudo de seguranca", "feature.reclose_safety_gate"))
    general = cfg.get("general", {}) if isinstance(cfg.get("general"), dict) else {}
    if general.get("scada_server_enabled") or general.get("scada_server_enable"):
        messages.append(ValidationMessage("warning", "general.scada_server_enabled", "ponte Modbus TCP SCADA nao esta ativada na V3 inicial", "feature.scada_pending"))

    mqtt = cfg.get("mqtt") if isinstance(cfg.get("mqtt"), dict) else {}
    if mqtt.get("enabled", True):
        if not str(mqtt.get("host", "")):
            messages.append(ValidationMessage("error", "mqtt.host", "broker obrigatorio", "mqtt.host"))
        if not 1 <= int(mqtt.get("port", 0) or 0) <= 65535:
            messages.append(ValidationMessage("error", "mqtt.port", "porta fora de 1..65535", "range.port"))
        tls = mqtt.get("tls") if isinstance(mqtt.get("tls"), dict) else {}
        if tls.get("insecure"):
            messages.append(ValidationMessage("warning", "mqtt.tls.insecure", "verificacao TLS desabilitada", "security.tls_insecure"))
    username = os.environ.get(str(mqtt.get("username_env", "GRIDCO_MQTT_USERNAME")), "")
    password = os.environ.get(str(mqtt.get("password_env", "GRIDCO_MQTT_PASSWORD")), "")
    if username and not password:
        messages.append(ValidationMessage("warning", "mqtt.password_env", "senha MQTT nao esta presente no ambiente atual", "secret.missing"))

    storage = cfg.get("storage") if isinstance(cfg.get("storage"), dict) else {}
    if int(storage.get("max_buffer_messages", 0) or 0) < 100:
        messages.append(ValidationMessage("error", "storage.max_buffer_messages", "limite minimo = 100", "storage.limit"))
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    if not 1 <= int(web.get("port", 0) or 0) <= 65535:
        messages.append(ValidationMessage("error", "web.port", "porta fora de 1..65535", "range.port"))
    return messages


def require_valid_configuration(raw: Any) -> dict[str, Any]:
    messages = validate_configuration(raw)
    if any(item.level == "error" for item in messages):
        raise ConfigurationError(messages)
    return normalize_configuration(raw)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ConfigurationError([ValidationMessage("error", "$", "a raiz deve ser objeto", "type.object")])
    return value


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ConfigurationManager:
    def __init__(self, path: Path, versions_dir: Path):
        self.path = path
        self.versions_dir = versions_dir
        self._lock = threading.RLock()
        self._active: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        with self._lock:
            cfg = require_valid_configuration(load_json(self.path))
            self._active = cfg
            return copy.deepcopy(cfg)

    def current(self) -> dict[str, Any]:
        with self._lock:
            if self._active is None:
                return self.load()
            return copy.deepcopy(self._active)

    def apply(self, raw: dict[str, Any], origin: str = "web") -> dict[str, Any]:
        cfg = require_valid_configuration(raw)
        with self._lock:
            now = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
            cfg["revision"] = max(int(cfg.get("revision", 0) or 0), int((self._active or {}).get("revision", 0) or 0) + 1)
            cfg.setdefault("metadata", {})
            if isinstance(cfg["metadata"], dict):
                cfg["metadata"]["v3_applied_at"] = datetime.now(UTC).isoformat()
                cfg["metadata"]["v3_applied_by"] = origin[:128]
            cfg = require_valid_configuration(cfg)
            sha = configuration_sha256(cfg)
            self.versions_dir.mkdir(parents=True, exist_ok=True)
            version_path = self.versions_dir / f"gateway-{now}-{sha[:12]}.json"
            atomic_write_json(version_path, cfg)
            atomic_write_json(self.path, cfg)
            self._active = cfg
            return {
                "configuration": copy.deepcopy(cfg),
                "sha256": sha,
                "version_file": str(version_path),
            }
