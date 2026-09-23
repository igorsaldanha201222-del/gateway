"""Catálogo embarcado e cadastro seguro de instâncias de equipamentos."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from .config import SAFE_ID, canonical_json


class CatalogError(ValueError):
    pass


# O que NAO entra na impressao digital: e' rotulo, nao leitura. Renomear um
# modelo nao pode marcar 200 usinas como desatualizadas.
_COSMETICO = {"name", "description", "version", "metadata", "ui_contract_key_count"}


def _funcional(valor: Any) -> Any:
    if isinstance(valor, dict):
        return {k: _funcional(v) for k, v in valor.items() if k not in _COSMETICO}
    if isinstance(valor, list):
        return [_funcional(v) for v in valor]
    return valor


def digest_modelo(template: Any, requests: Any, fields: Any) -> str:
    """Impressao digital do que o modelo LE, nao de como ele se chama.

    Calculada aqui, e nao lida do catalogo: o campo ``semantic_sha256`` que vem
    no arquivo foi produzido por outro processo e nao e' reproduzivel a partir
    de template+requests+fields. Para comparar o que esta numa usina com o que
    esta no catalogo, os dois lados precisam ser medidos pela mesma regua.

    Nome, descricao e metadados ficam de fora de proposito: mudam por arrumacao
    e nao alteram um unico registrador lido.
    """
    return hashlib.sha256(canonical_json({
        "template": _funcional(template),
        "requests": _funcional(requests),
        "fields": _funcional(fields),
    }).encode("utf-8")).hexdigest()


def digest_da_entrada(entry: dict[str, Any]) -> str:
    return digest_modelo(entry.get("template"), entry.get("requests"), entry.get("fields"))


def digest_da_config(config: dict[str, Any], template_id: str) -> str | None:
    """Impressao digital da copia que esta na configuracao da usina."""
    template = next((t for t in (config.get("templates") or [])
                     if str(t.get("id")) == str(template_id)), None)
    if template is None:
        return None
    requests = [r for r in (config.get("requests") or [])
                if str(r.get("template_id")) == str(template_id)]
    fields = [f for f in (config.get("fields") or [])
              if str(f.get("template_id")) == str(template_id)]
    return digest_modelo(template, requests, fields)


class TemplateCatalog:
    def __init__(self, path: Path, overrides_path: Path | None = None):
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CatalogError(f"catálogo de equipamentos indisponível: {exc}") from exc
        if raw.get("format") != "gridco.gateway.template-catalog" or not isinstance(raw.get("entries"), list):
            raise CatalogError("formato de catálogo inválido")
        self.raw = raw
        self.overrides_path = overrides_path
        self.entries = {str(item.get("catalog_id", "")): item for item in raw["entries"]}
        if overrides_path and overrides_path.is_file():
            try:
                overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
                for catalog_id, entry in overrides.get("entries", {}).items():
                    if catalog_id in self.entries and isinstance(entry, dict):
                        self.entries[catalog_id] = entry
            except (OSError, json.JSONDecodeError):
                # Um override danificado não impede o gateway de usar o catálogo de fábrica.
                pass

    def summary(self) -> dict[str, Any]:
        return {
            "format": self.raw.get("format"),
            "schema_version": self.raw.get("schema_version"),
            "catalog_revision": self.raw.get("catalog_revision"),
            "sha256": self.raw.get("sha256"),
            "summary": self.raw.get("summary", {}),
            "entries": [{
                key: copy.deepcopy(item.get(key))
                for key in (
                    "catalog_id", "name", "manufacturer", "model", "device_type",
                    "description", "semantic_sha256", "provenance",
                )
            } | {
                "request_count": len(item.get("requests", [])),
                "field_count": len(item.get("fields", [])),
            } for item in self.entries.values()],
        }

    def detail(self, catalog_id: str) -> dict[str, Any]:
        item = self.entries.get(str(catalog_id))
        if item is None:
            raise CatalogError("modelo não encontrado no catálogo padrão")
        return copy.deepcopy(item)

    @staticmethod
    def _validate_entry(entry: dict[str, Any]) -> None:
        template = entry.get("template")
        requests = entry.get("requests")
        fields = entry.get("fields")
        if not isinstance(template, dict) or not isinstance(requests, list) or not isinstance(fields, list):
            raise CatalogError("o modelo deve conter template, requests e fields")
        template_id = str(template.get("id", ""))
        if not SAFE_ID.fullmatch(template_id):
            raise CatalogError("ID interno do template inválido")
        request_ids: set[str] = set()
        for request in requests:
            if not isinstance(request, dict) or str(request.get("template_id")) != template_id:
                raise CatalogError("bloco de leitura pertence a outro template")
            request_id = str(request.get("id", ""))
            if not SAFE_ID.fullmatch(request_id) or request_id in request_ids:
                raise CatalogError("ID de bloco de leitura inválido ou duplicado")
            request_ids.add(request_id)
            function = int(request.get("function_code", 0))
            quantity = int(request.get("quantity", 0))
            limit = 2000 if function in {1, 2} else 125
            if function not in {1, 2, 3, 4} or not 1 <= quantity <= limit:
                raise CatalogError("função ou quantidade Modbus inválida")
            if not 0 <= int(request.get("address", -1)) <= 65535:
                raise CatalogError("endereço inicial Modbus inválido")
        field_ids: set[str] = set()
        for field in fields:
            if not isinstance(field, dict) or str(field.get("template_id")) != template_id:
                raise CatalogError("variável pertence a outro template")
            field_id = str(field.get("id", ""))
            if not SAFE_ID.fullmatch(field_id) or field_id in field_ids:
                raise CatalogError("ID de variável inválido ou duplicado")
            field_ids.add(field_id)
            if not str(field.get("json_key", "")):
                raise CatalogError("toda variável deve ter uma chave JSON")
            if str(field.get("source_type", "modbus")) == "modbus" and str(field.get("request_id", "")) not in request_ids:
                raise CatalogError("variável Modbus referencia um bloco inexistente")

    def update(self, catalog_id: str, value: dict[str, Any]) -> dict[str, Any]:
        if self.overrides_path is None:
            raise CatalogError("armazenamento de modelos editáveis indisponível")
        original = self.detail(catalog_id)
        allowed = {"name", "manufacturer", "model", "device_type", "description", "template", "requests", "fields"}
        updated = copy.deepcopy(original)
        for key in allowed:
            if key in value:
                updated[key] = copy.deepcopy(value[key])
        updated["catalog_id"] = original["catalog_id"]
        updated["provenance"] = original.get("provenance", [])
        self._validate_entry(updated)
        digest_value = {
            "template": updated["template"], "requests": updated["requests"], "fields": updated["fields"]
        }
        updated["semantic_sha256"] = hashlib.sha256(canonical_json(digest_value).encode("utf-8")).hexdigest()
        overrides = {"format": "gridco.gateway.template-overrides", "entries": {}}
        if self.overrides_path.is_file():
            try:
                loaded = json.loads(self.overrides_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("entries"), dict):
                    overrides = loaded
            except (OSError, json.JSONDecodeError):
                pass
        overrides["entries"][catalog_id] = updated
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.overrides_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o640)
        os.replace(temporary, self.overrides_path)
        self.entries[catalog_id] = updated
        return self.detail(catalog_id)

    @staticmethod
    def _safe_channel(value: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(value.get("id", "")).strip()
        if not SAFE_ID.fullmatch(channel_id):
            raise CatalogError("ID do canal inválido")
        transport = str(value.get("transport", "tcp")).strip().lower()
        if transport in {"tcp", "ethernet"}:
            host = str(value.get("ip", "")).strip()
            if not host or len(host) > 253 or any(character.isspace() for character in host):
                raise CatalogError("IP ou hostname Modbus TCP inválido")
            port = int(value.get("port", 502))
            if not 1 <= port <= 65535:
                raise CatalogError("porta Modbus TCP inválida")
            return {
                "id": channel_id, "name": str(value.get("name", channel_id)).strip()[:128] or channel_id,
                "transport": "tcp", "enabled": True, "ip": host, "port": port,
                "timeout_ms": 2000, "retries": 2, "poll_interval_ms": 1000,
            }
        if transport not in {"rtu", "serial"}:
            raise CatalogError("transporte deve ser TCP ou RTU")
        serial_device = str(value.get("serial_device", "")).strip()
        if not serial_device.startswith("/dev/") or any(char in serial_device for char in "\x00\r\n"):
            raise CatalogError("porta serial inválida")
        baudrate = int(value.get("baudrate", 9600))
        if baudrate not in {1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise CatalogError("baudrate não suportado")
        parity = str(value.get("parity", "none")).lower()
        if parity not in {"none", "even", "odd"}:
            raise CatalogError("paridade inválida")
        return {
            "id": channel_id, "name": str(value.get("name", channel_id)).strip()[:128] or channel_id,
            "transport": "rtu", "enabled": True, "serial_device": serial_device,
            "baudrate": baudrate, "data_bits": 8, "parity": parity, "stop_bits": int(value.get("stop_bits", 1)),
            "timeout_ms": 2000, "retries": 2, "poll_interval_ms": 1000,
        }

    def onboard(self, current: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        candidate = copy.deepcopy(current)
        for section in ("channels", "templates", "requests", "fields", "devices", "topics"):
            candidate.setdefault(section, [])
        entry = self.detail(str(payload.get("catalog_id", "")))
        device_value = payload.get("device")
        if not isinstance(device_value, dict):
            raise CatalogError("dados do equipamento são obrigatórios")
        device_id = str(device_value.get("id", "")).strip()
        if not SAFE_ID.fullmatch(device_id):
            raise CatalogError("ID do equipamento inválido")
        if any(str(item.get("id")) == device_id for item in candidate["devices"]):
            raise CatalogError("já existe um equipamento com esse ID")
        channel_id = str(device_value.get("channel_id", "")).strip()
        channel = next((item for item in candidate["channels"] if str(item.get("id")) == channel_id), None)
        if channel is None:
            channel_value = payload.get("channel")
            if not isinstance(channel_value, dict):
                raise CatalogError("selecione um canal existente ou informe um novo canal")
            channel = self._safe_channel(channel_value)
            if channel["id"] != channel_id:
                raise CatalogError("o canal do equipamento não corresponde ao novo canal")
            candidate["channels"].append(channel)

        template = entry["template"]
        template_id = str(template.get("id", ""))
        existing_template = next((item for item in candidate["templates"] if str(item.get("id")) == template_id), None)
        if existing_template is None:
            request_ids = {str(item.get("id")) for item in candidate["requests"]}
            field_ids = {str(item.get("id")) for item in candidate["fields"]}
            if any(str(item.get("id")) in request_ids for item in entry["requests"]):
                raise CatalogError("conflito de IDs entre o catálogo e os blocos de leitura atuais")
            if any(str(item.get("id")) in field_ids for item in entry["fields"]):
                raise CatalogError("conflito de IDs entre o catálogo e as variáveis atuais")
            candidate["templates"].append(template)
            candidate["requests"].extend(entry["requests"])
            candidate["fields"].extend(entry["fields"])
        elif canonical_json(existing_template) != canonical_json(template):
            raise CatalogError("a Engenharia alterou este modelo; selecione o modelo ativo ou revise o conflito")

        unit_id = int(device_value.get("unit_id", 1))
        transport = str(channel.get("transport", "tcp")).lower()
        minimum, maximum = (1, 247) if transport in {"rtu", "serial"} else (0, 255)
        if not minimum <= unit_id <= maximum:
            raise CatalogError(f"Unit ID deve estar entre {minimum} e {maximum} para {transport}")
        device_type = str(template.get("device_type") or entry.get("device_type") or "device")
        device_metadata = {
            "catalog_id": entry["catalog_id"],
            "catalog_sha256": entry["semantic_sha256"],
            # Medido por digest_modelo, que e' reproduzivel: e' o que permite
            # depois distinguir "o catalogo mudou" de "alguem editou aqui".
            "template_digest": digest_da_entrada(entry),
        }
        if device_type.lower() == "inverter" and device_value.get("include_string_fields") is False:
            device_metadata["include_string_fields"] = False
            device_metadata["string_payload_mode"] = "independent"
        candidate["devices"].append({
            "id": device_id,
            "name": str(device_value.get("name", device_id)).strip()[:128] or device_id,
            "channel_id": channel_id,
            "unit_id": unit_id,
            "template_id": template_id,
            "device_type": device_type,
            "enabled": device_value.get("enabled") is not False,
            "poll_interval_ms": max(100, int(device_value.get("poll_interval_ms", 1000))),
            # 1 minuto e' o padrao de envio da frota. A leitura Modbus continua
            # a cada 1 s: o que espaca e' a publicacao, nao a aquisicao.
            "publish_interval_ms": max(100, int(device_value.get("publish_interval_ms", 60_000))),
            "stale_timeout_ms": max(100, int(device_value.get("stale_timeout_ms", 30_000))),
            "commands_enabled": False,
            "metadata": device_metadata,
        })
        # O contrato do servidor e' dev/read/UFV/<Planta>/<tipo>/<indice>, com
        # indice numerico. O slug da planta preserva maiuscula; o id nao.
        plant = candidate.get("plant") or {}
        plant_slug = str((plant.get("metadata") or {}).get("topic_slug")
                         or plant.get("id") or "gateway_generico")

        # Indice EXPLICITO, nunca derivado da ordem da lista: inserir um device
        # no meio renumeraria os seguintes e quebraria o historico no servidor.
        indice = device_value.get("mqtt_topic_index", device_value.get("index"))
        if indice is None:
            raise CatalogError("informe o índice do device no tópico MQTT")
        try:
            indice = int(indice)
        except (TypeError, ValueError):
            raise CatalogError("o índice do tópico deve ser um número inteiro")
        if not 1 <= indice <= 9999:
            raise CatalogError("o índice do tópico deve estar entre 1 e 9999")
        conflito = next((
            item for item in candidate["devices"]
            if str(item.get("device_type")) == device_type
            and int((item.get("metadata") or {}).get("mqtt_topic_index", 0) or 0) == indice
        ), None)
        if conflito is not None:
            raise CatalogError(
                f"o índice {indice} já é usado por '{conflito.get('id')}' neste tipo de equipamento")
        device_metadata["mqtt_topic_index"] = indice

        topic = f"dev/read/UFV/{plant_slug}/{device_type}/{indice}"
        if not re.fullmatch(r"[^#+\x00]{1,65535}", topic):
            raise CatalogError("não foi possível gerar o tópico MQTT padrão")
        candidate["topics"].append({
            "id": f"{device_id}-telemetry"[:96], "device_id": device_id,
            "purpose": "telemetry", "topic": topic, "qos": 1, "retain": False,
        })
        return candidate
