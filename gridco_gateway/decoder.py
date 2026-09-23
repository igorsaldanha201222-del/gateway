"""Decodificacao de registradores e montagem do contrato JSON GRIDCO."""

from __future__ import annotations

import math
import struct
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .storage import Storage
from .payload_policy import (
    is_string_payload_field,
    stringbox_current_key,
    uses_independent_string_payload,
)


class DecodeError(ValueError):
    pass


def _metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("metadata", {})
    return value if isinstance(value, Mapping) else {}


def word_width(field: Mapping[str, Any]) -> int:
    kind = str(field.get("data_type", "uint16")).lower()
    if kind in {"uint32", "int32", "float32"}:
        return 2
    if kind in {"uint64", "int64", "float64", "bcd64", "bcd"}:
        return 4
    if kind in {"string", "ascii", "string_ascii", "hex", "hex_digits", "serial_hex"}:
        return max(1, min(125, int(field.get("word_count") or field.get("bit_offset") or 8)))
    return 1


def _ordered_bytes(words: list[int], field: Mapping[str, Any]) -> bytes:
    values = [int(word) & 0xFFFF for word in words]
    word_order = str(field.get("word_order", "normal")).lower()
    byte_order = str(field.get("byte_order", "big")).lower()
    if word_order in {"swapped", "little_endian", "cdab", "dcba", "reverse"}:
        values.reverse()
    result = bytearray()
    for value in values:
        pair = struct.pack(">H", value)
        if byte_order in {"little", "swapped", "ba"} or word_order in {"badc", "dcba"}:
            pair = pair[::-1]
        result.extend(pair)
    return bytes(result)


def decode_words(words: list[int], field: Mapping[str, Any]) -> Any:
    width = word_width(field)
    if len(words) < width:
        raise DecodeError(f"campo requer {width} words, recebeu {len(words)}")
    words = words[:width]
    kind = str(field.get("data_type", "uint16")).lower()
    raw_bytes = _ordered_bytes(words, field)
    if kind == "uint16":
        value: Any = struct.unpack(">H", raw_bytes[:2])[0]
    elif kind == "int16":
        value = struct.unpack(">h", raw_bytes[:2])[0]
    elif kind == "uint32":
        value = struct.unpack(">I", raw_bytes[:4])[0]
    elif kind == "int32":
        value = struct.unpack(">i", raw_bytes[:4])[0]
    elif kind == "float32":
        value = struct.unpack(">f", raw_bytes[:4])[0]
    elif kind == "uint64":
        value = struct.unpack(">Q", raw_bytes[:8])[0]
    elif kind == "int64":
        value = struct.unpack(">q", raw_bytes[:8])[0]
    elif kind == "float64":
        value = struct.unpack(">d", raw_bytes[:8])[0]
    elif kind in {"bool", "bool_bit"}:
        bit = int(field.get("bit_offset", 0) or 0)
        if not 0 <= bit <= 15:
            raise DecodeError("bit_offset deve estar em 0..15")
        value = 1 if (int(words[0]) & (1 << bit)) else 0
    elif kind == "bool_nonzero":
        value = 1 if int(words[0]) else 0
    elif kind in {"string", "ascii", "string_ascii"}:
        value = raw_bytes.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    elif kind in {"hex", "hex_digits", "serial_hex"}:
        value = raw_bytes.hex().upper()
    elif kind in {"bcd64", "bcd"}:
        digits = "".join(f"{byte >> 4:X}{byte & 0x0F:X}" for byte in raw_bytes)
        if any(character not in "0123456789" for character in digits):
            raise DecodeError("valor BCD contem nibble A..F")
        value = digits.lstrip("0") or "0"
    else:
        raise DecodeError(f"data_type nao suportado: {kind}")
    if isinstance(value, float) and not math.isfinite(value):
        raise DecodeError("NaN/Inf nao pode ser publicado")
    if isinstance(value, (int, float)) and kind not in {"bool", "bool_bit", "bool_nonzero"}:
        value = value * float(field.get("gain", 1.0) or 0.0) + float(field.get("offset", 0.0) or 0.0)
    mapping = field.get("status_mapping", {})
    if isinstance(mapping, Mapping) and mapping:
        mapped = mapping.get(str(int(value) if isinstance(value, float) and value.is_integer() else value))
        if mapped is not None:
            value = mapped
    return value


def _default(field: Mapping[str, Any]) -> Any:
    value = field.get("default_value", 0)
    kind = str(field.get("data_type", "uint16")).lower()
    meta = _metadata(field)
    if kind in {"string", "ascii", "string_ascii", "hex", "hex_digits", "serial_hex"} or meta.get("quote_value"):
        return str(value)
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return 0


def _round(value: Any, field: Mapping[str, Any]) -> Any:
    if not isinstance(value, float) or not math.isfinite(value):
        return value
    decimals = max(0, min(9, int(_metadata(field).get("decimals", 3) or 0)))
    result = round(value, decimals)
    return int(result) if decimals == 0 else result


def _system_value(code: int, device: Mapping[str, Any], timestamp: str, quality: int) -> Any:
    meta = _metadata(device)
    table = {
        1: str(device.get("id", "")),
        2: timestamp,
        3: quality,
        4: 1 if quality == 192 else 0,
        5: int(meta.get("simplified_status", 0) or 0),
        6: str(meta.get("domain", "")),
        7: str(meta.get("complex", "")),
        8: str(meta.get("power_plant", "")),
        9: str(device.get("manufacturer", "")),
        10: str(device.get("model", "")),
        11: str(device.get("device_type", "")),
        12: str(meta.get("energy_source", "")),
        13: str(device.get("description") or device.get("name", "")),
    }
    return table.get(code, "")


def _range_calculation(operation: int, order: list[str], direct: set[str], first_id: str, last_id: str,
                       field_id: str, values_by_id: Mapping[str, Any], valid_by_id: Mapping[str, bool]) -> float:
    # Operacoes 15 (soma) e 16 (media) do firmware WAGO: a faixa e posicional no
    # template e um membro invalido invalida o resultado inteiro, porque media de
    # parte das strings seria numero plausivel e errado.
    first, last = order.index(first_id), order.index(last_id)
    members = order[first:last + 1]
    if first > last or field_id in members or not all(member in direct for member in members):
        raise DecodeError("faixa de campos invalida")
    if not all(valid_by_id.get(member, False) for member in members):
        raise DecodeError("membro da faixa invalido")
    total = sum(float(values_by_id[member]) for member in members)
    return total if operation == 15 else total / len(members)


def _calculate(operation: int, a: float, b: float | None) -> float:
    if operation == 1:
        return a
    if b is None:
        raise DecodeError("operacao exige campo B")
    if operation == 2:
        return a + b
    if operation == 3:
        return a - b
    if operation == 4:
        return a * b
    if operation == 5:
        if b == 0:
            raise DecodeError("divisao por zero")
        return a / b
    if operation == 6:
        return math.hypot(a, b)
    if operation == 7:
        if b == 0:
            raise DecodeError("percentual com divisor zero")
        return (a / b) * 100.0
    if operation == 8:
        if b == 0:
            raise DecodeError("razao com divisor zero")
        return a / b
    if operation == 10:
        return 1.0 if int(a) == int(b) else 0.0
    if operation == 11:
        return 1.0 if int(a) != int(b) else 0.0
    if operation == 12:
        return 1.0 if int(a) & int(b) == int(b) else 0.0
    if operation == 13:
        return 1.0 if int(a) & int(b) == 0 else 0.0
    if operation == 14:
        mask = int(b)
        if mask == 0:
            raise DecodeError("mascara zero")
        shift = (mask & -mask).bit_length() - 1
        return float((int(a) & mask) >> shift)
    raise DecodeError(f"operacao calculada nao suportada: {operation}")


def build_payload(
    config: Mapping[str, Any],
    device: Mapping[str, Any],
    raw_by_request: Mapping[str, list[int]],
    quality: int,
    sampled_at: datetime,
    snapshots: Mapping[str, Mapping[str, Any]],
    storage: Storage,
) -> tuple[dict[str, Any], dict[str, Any]]:
    template_id = str(device.get("template_id", ""))
    fields = [
        item for item in config.get("fields", [])
        if isinstance(item, Mapping) and str(item.get("template_id")) == template_id and item.get("enabled", True)
    ]
    device_meta = _metadata(device)
    if uses_independent_string_payload(device):
        # A String Box/Longmax publica seu proprio JSON. Neste modo o payload
        # do inversor nao recebe campos string_* nem consulta snapshots ligados.
        fields = [field for field in fields if not is_string_payload_field(field)]
    requests = {
        str(item.get("id", "")): item for item in config.get("requests", [])
        if isinstance(item, Mapping)
    }
    try:
        timezone = ZoneInfo(str((config.get("plant") or {}).get("timezone", "America/Sao_Paulo")))
    except Exception:
        timezone = ZoneInfo("UTC")
    local_time = sampled_at.astimezone(timezone)
    timestamp = local_time.isoformat(timespec="seconds")
    values_by_id: dict[str, Any] = {}
    valid_by_id: dict[str, bool] = {}

    for field in fields:
        field_id = str(field.get("id", ""))
        source = str(field.get("source_type", "modbus")).lower()
        meta = _metadata(field)
        try:
            system_code = int(meta.get("system_value", 0) or 0)
            if system_code:
                value = _system_value(system_code, device, timestamp, quality)
            elif source == "timestamp":
                value = timestamp
            elif source == "quality":
                value = quality
            elif source == "static":
                expression = field.get("source_expression", "")
                value = expression[1:-1] if isinstance(expression, str) and len(expression) > 1 and expression[0] == expression[-1] and expression[0] in "'\"" else expression
                if value in {"", None}:
                    value = field.get("default_value", 0)
            elif source == "modbus":
                request_id = str(field.get("request_id", ""))
                words = raw_by_request[request_id]
                raw_offset = int(field.get("register_offset", 0) or 0)
                request = requests.get(request_id, {})
                buffer_offset = int(request.get("buffer_offset", 0) or 0)
                offset = raw_offset - buffer_offset if buffer_offset and raw_offset >= buffer_offset else raw_offset
                value = decode_words(words[offset:], field)
            else:
                continue
            values_by_id[field_id] = _round(value, field)
            valid_by_id[field_id] = True
        except (KeyError, TypeError, ValueError, DecodeError):
            values_by_id[field_id] = _default(field)
            valid_by_id[field_id] = False

    for field in fields:
        field_id = str(field.get("id", ""))
        source = str(field.get("source_type", "modbus")).lower()
        if source not in {"linked", "linked_stringbox", "linked_string_box", "related", "cache"}:
            continue
        meta = _metadata(field)
        related_device = ""
        if source in {"linked", "linked_stringbox", "linked_string_box"}:
            links = device_meta.get("linked_string_boxes")
            if isinstance(links, list) and links:
                slot = max(0, int(meta.get("linked_slot", 1) or 1) - 1)
                related_device = str(links[min(slot, len(links) - 1)])
            else:
                related_device = str(device_meta.get("linked_string_box", ""))
        elif source == "related":
            role = int(meta.get("related_role", 0) or 0)
            related_device = str({1: device_meta.get("linked_string_box"), 2: device_meta.get("related_meter"),
                                  3: device_meta.get("related_weather"), 4: device_meta.get("related_relay")}.get(role, "") or "")
        else:
            related_device = str(device.get("id", ""))
        source_key = str(meta.get("linked_source_key") or field.get("source_expression") or field.get("json_key", ""))
        snapshot = snapshots.get(related_device, {})
        try:
            snapshot_payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), Mapping) else snapshot
            if source_key in snapshot_payload:
                value = snapshot_payload[source_key]
            else:
                value = snapshot_payload[stringbox_current_key(source_key)]
            age_seconds = max(0.0, sampled_at.timestamp() - float(snapshot.get("epoch", sampled_at.timestamp())))
            max_age = int(meta.get("max_age_seconds", 0) or 0)
            if max_age and age_seconds > max_age:
                raise DecodeError("valor relacionado vencido")
            values_by_id[field_id] = _round(value, field)
            valid_by_id[field_id] = int(snapshot.get("quality", 192)) == 192
        except (KeyError, TypeError, ValueError, DecodeError):
            values_by_id[field_id] = _default(field)
            valid_by_id[field_id] = False

    # Ate quatro passagens permitem campos calculados encadeados sem depender da ordem.
    pending = [field for field in fields if str(field.get("source_type", "")).lower() == "derived"]
    order = [str(field.get("id", "")) for field in fields]
    direct = {str(field.get("id", "")) for field in fields if str(field.get("source_type", "modbus")).lower() in {"modbus", "static"}}
    for _ in range(4):
        next_pending = []
        for field in pending:
            field_id = str(field.get("id", ""))
            meta = _metadata(field)
            operation = int(meta.get("calc_operation", 0) or 0)
            a_id = str(meta.get("calc_field_a", ""))
            b_id = str(meta.get("calc_field_b", ""))
            try:
                if operation in {15, 16}:
                    value = _range_calculation(operation, order, direct, a_id, b_id, field_id, values_by_id, valid_by_id)
                    values_by_id[field_id] = _round(value, field)
                    valid_by_id[field_id] = True
                    continue
                if not valid_by_id.get(a_id, False):
                    raise DecodeError("campo A invalido")
                a = float(values_by_id[a_id])
                if operation == 9:
                    value = storage.daily_energy(str(device.get("id", "")), field_id, local_time.date().isoformat(), a)
                else:
                    if operation not in {1} and not valid_by_id.get(b_id, False):
                        raise DecodeError("campo B invalido")
                    b = None if operation == 1 else float(values_by_id[b_id])
                    value = _calculate(operation, a, b)
                values_by_id[field_id] = _round(value, field)
                valid_by_id[field_id] = True
            except (KeyError, TypeError, ValueError, DecodeError):
                next_pending.append(field)
        if len(next_pending) == len(pending):
            break
        pending = next_pending
    for field in pending:
        field_id = str(field.get("id", ""))
        values_by_id[field_id] = _default(field)
        valid_by_id[field_id] = False

    payload: dict[str, Any] = {}
    diagnostics: dict[str, Any] = {"valid_fields": 0, "invalid_fields": 0, "field_quality": {}}
    for field in fields:
        field_id = str(field.get("id", ""))
        metadata = _metadata(field)
        key = str(metadata.get("publish_json_key") or field.get("json_key", ""))
        valid = bool(valid_by_id.get(field_id, False))
        diagnostics["field_quality"][key] = 192 if valid else 28
        diagnostics["valid_fields" if valid else "invalid_fields"] += 1
        if metadata.get("publish", True) and key:
            payload[key] = values_by_id.get(field_id, _default(field))
    # Metadados comuns pertencem ao proprio device e nao dependem de campos
    # repetidos no mapa legado nem de outro equipamento. A excecao e o rotulo:
    # template com device_type em system_value 13 publica o nome do device
    # ("Tcu1", "Inversor1"), que e o que a frota espera no JSON; o tipo tecnico
    # fica so no topico.
    labelled = any(
        str(field.get("json_key", "")) == "device_type" and int(_metadata(field).get("system_value", 0) or 0) == 13
        for field in fields
    )
    if not (labelled and payload.get("device_type")):
        payload["device_type"] = str(device.get("device_type", "device") or "device")
    payload["timestamp"] = timestamp
    payload["communication_fault"] = quality
    return payload, diagnostics
