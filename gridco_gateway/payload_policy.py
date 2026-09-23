"""Politicas de payload por device, independentes do mapa Modbus legado."""

from __future__ import annotations

from typing import Any, Mapping


STRING_LINK_SOURCES = {"linked_stringbox", "linked_string_box"}


def stringbox_current_key(json_key: Any) -> str:
    """Normaliza correntes da String Box para ``current_ch_1..N``."""
    key = str(json_key or "")
    lower = key.lower()
    if lower.startswith("current_ch_"):
        suffix = lower[len("current_ch_"):]
    elif lower.startswith("string_current_"):
        suffix = lower[len("string_current_"):]
    else:
        suffix = ""
    if suffix.isdigit():
        return f"current_ch_{int(suffix)}"
    return ""


def is_string_payload_field(field: Mapping[str, Any]) -> bool:
    """Identifica campos de strings que podem ser separados do inversor."""
    return (
        str(field.get("json_key", "")).lower().startswith("string_")
        or str(field.get("source_type", "")).lower() in STRING_LINK_SOURCES
    )


def uses_independent_string_payload(device: Mapping[str, Any]) -> bool:
    """Retorna True quando String Box/Longmax publica como device separado."""
    metadata = device.get("metadata") if isinstance(device.get("metadata"), Mapping) else {}
    return (
        str(device.get("device_type", "")).lower() == "inverter"
        and (
            metadata.get("include_string_fields") is False
            or str(metadata.get("string_payload_mode", "")).lower() == "independent"
        )
    )


def detach_inverter_string_devices(
    configuration: dict[str, Any], *, remove_template_fields: bool = True
) -> dict[str, int]:
    """Remove associacoes inversor/stringbox em uma configuracao migrada.

    O objeto informado e alterado. Os devices String Box permanecem intactos e
    continuam usando seus proprios topicos de telemetria.
    """
    templates = {
        str(item.get("id", "")): str(item.get("device_type", "")).lower()
        for item in configuration.get("templates", [])
        if isinstance(item, dict)
    }
    stringbox_templates = {
        template_id for template_id, device_type in templates.items()
        if device_type == "stringbox"
    }
    fields = configuration.get("fields", [])
    inverter_templates = {
        template_id for template_id, device_type in templates.items()
        if device_type == "inverter" and any(
            isinstance(field, dict)
            and str(field.get("template_id", "")) == template_id
            and is_string_payload_field(field)
            for field in fields
        )
    }
    detached = 0
    associations = 0
    for device in configuration.get("devices", []):
        if not isinstance(device, dict) or str(device.get("template_id", "")) not in inverter_templates:
            continue
        metadata = device.get("metadata") if isinstance(device.get("metadata"), dict) else {}
        metadata = dict(metadata)
        associations += int(bool(metadata.pop("linked_string_box", None)))
        linked_many = metadata.pop("linked_string_boxes", None)
        associations += len(linked_many) if isinstance(linked_many, list) else int(bool(linked_many))
        metadata["include_string_fields"] = False
        metadata["string_payload_mode"] = "independent"
        device["metadata"] = metadata
        detached += 1
    removed = 0
    string_currents_enabled = 0
    string_api_aliases = 0
    if remove_template_fields and inverter_templates:
        kept = []
        for field in fields:
            should_remove = (
                isinstance(field, dict)
                and str(field.get("template_id", "")) in inverter_templates
                and is_string_payload_field(field)
            )
            if should_remove:
                removed += 1
            else:
                kept.append(field)
        configuration["fields"] = kept
    for field in configuration.get("fields", []):
        if (
            isinstance(field, dict)
            and str(field.get("template_id", "")) in stringbox_templates
            and stringbox_current_key(field.get("json_key"))
        ):
            metadata = field.get("metadata") if isinstance(field.get("metadata"), dict) else {}
            metadata = dict(metadata)
            changed = False
            if metadata.get("publish") is not True:
                metadata["publish"] = True
                string_currents_enabled += 1
                changed = True
            api_key = stringbox_current_key(field.get("json_key"))
            if api_key and str(field.get("json_key", "")) != api_key:
                metadata["register_json_key"] = api_key
                metadata.pop("publish_json_key", None)
                field["json_key"] = api_key
                string_api_aliases += 1
                changed = True
            if changed:
                field["metadata"] = metadata
    return {
        "inverter_devices": detached,
        "associations_removed": associations,
        "string_fields_removed": removed,
        "string_currents_enabled": string_currents_enabled,
        "string_api_aliases": string_api_aliases,
    }
