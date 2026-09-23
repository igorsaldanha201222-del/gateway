"""Achador de equipamentos conhecidos na rede de campo (somente leitura).

Cada assinatura le poucos registradores de identidade ou de grandeza com faixa
fisica estreita (frequencia da rede, tensao, codigo de produto) e vem de um mapa
ja usado na frota: skill mapa-de-rede-usinas, catalogo de templates V3 e
templates do publicador WAGO REV15. O campo ``source`` diz de onde veio.

Regra herdada do mapa de rede: numero plausivel nao e prova. Assinatura so casa
com valor diferente de zero dentro da faixa fisica, entao equipamento que
devolve zero em qualquer endereco fica sem identificacao em vez de ganhar um
modelo errado. Nada e escrito: apenas FC03/FC04.
"""

from __future__ import annotations

import argparse
import copy
import ipaddress
import json
import math
import socket
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .modbus import ModbusClient, ModbusError, ModbusTCPClient

GRID_HZ = (59.0, 61.0)
TCU_PRODUCT_ID = 4659
CONFIDENT = 0.9
IDENTIFIED = 0.8
MIN_SCORE = 0.5
# Endereco que nenhum mapa conhecido usa: escravo que devolve dado aqui devolve
# dado em qualquer lugar, e ai so assinatura forte vale.
NONEXISTENT_ADDRESS = 65530
# Excecoes que provam que existe um escravo atendendo (funcao/endereco/valor
# ilegal). 4, 10 e 11 aparecem em conversores e loggers para ID sem equipamento.
PRESENT_EXCEPTIONS = {1, 2, 3}


class Reader:
    """Leituras de um escravo com cache e intervalo minimo entre requisicoes.

    O intervalo existe porque conversores serial<->TCP podem devolver a resposta
    de um escravo na requisicao de outro quando o polling e apertado.
    """

    def __init__(self, client: ModbusClient, host: str, unit: int, pacing: float = 0.2):
        self.client = client
        self.host = host
        self.unit = int(unit)
        self.pacing = max(0.0, float(pacing))
        self.requests = 0
        self._cache: dict[tuple[int, int, int], list[int] | None] = {}
        self._last = 0.0

    @property
    def host_octets(self) -> list[int]:
        try:
            return list(ipaddress.IPv4Address(self.host).packed)
        except ValueError:
            return []

    def get(self, function_code: int, address: int, quantity: int) -> list[int] | None:
        key = (function_code, address, quantity)
        if key not in self._cache:
            wait = self.pacing - (time.monotonic() - self._last)
            if self._last and wait > 0:
                time.sleep(wait)
            try:
                self._cache[key] = self.client.read(self.unit, function_code, address, quantity)
            except (ModbusError, OSError):
                self._cache[key] = None
            self._last = time.monotonic()
            self.requests += 1
        return self._cache[key]

    def accepts_any_address(self) -> bool:
        return self.get(3, NONEXISTENT_ADDRESS, 2) is not None


def _s16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _s32(words: list[int]) -> int:
    value = (words[0] << 16) | words[1]
    return value - (1 << 32) if value & 0x80000000 else value


def _f32(words: list[int], swapped: bool = False) -> float:
    high, low = (words[1], words[0]) if swapped else (words[0], words[1])
    return struct.unpack(">f", struct.pack(">HH", high, low))[0]


def _text(words: list[int] | None) -> str:
    if not words:
        return ""
    raw = struct.pack(f">{len(words)}H", *words)
    return " ".join("".join(chr(b) if 32 <= b < 127 else " " for b in raw).split())


def _within(value: float | None, low: float, high: float) -> bool:
    return value is not None and math.isfinite(value) and low <= value <= high


def _real_float(value: float) -> bool:
    # Inteiros pequenos lidos como float32 viram numeros subnormais (1e-44) que
    # passam em qualquer faixa: nao sao medida, sao o layout errado.
    return math.isfinite(value) and (value == 0.0 or abs(value) >= 1e-3)


def _grid_hz(value: float | None) -> bool:
    return _within(value, *GRID_HZ)


Check = Callable[[Reader], "tuple[float, str] | None"]


@dataclass(frozen=True)
class Signature:
    key: str
    manufacturer: str
    model: str
    device_type: str
    source: str
    check: Check
    extras: Callable[[Reader], dict[str, Any]] | None = None
    # Equipamento que responde igual em qualquer Unit ID (a NCU, por exemplo):
    # achado uma vez, os demais IDs do mesmo host nao sao varridos.
    unit_agnostic: bool = False


def _pextron_pcpt4(r: Reader):
    words = r.get(3, 136, 2)
    if words and words[0] == 0x0020 and words[1] >> 8 == 0x04:
        return 0.98, f"tipo 0x0020 e versao 0x{words[1]:04X} em 136-137"
    return None


def _sti_ncu(r: Reader):
    words = r.get(3, 30002, 5)
    if words and r.host_octets and words[1:5] == r.host_octets:
        return 0.95, f"IP {'.'.join(map(str, words[1:5]))} gravado em 30003-30006"
    return None


def _sti_tcu(r: Reader):
    # Sem o IP da NCU e com o ProductId do TCU: e um TCU atendido direto, como
    # atras da ponte de radio Digi, um Unit ID por tracker.
    product = r.get(3, 30500, 1)
    if product and product[0] == TCU_PRODUCT_ID:
        return 0.9, f"ProductId {TCU_PRODUCT_ID} em 30500"
    return None


def _sti_tcus(r: Reader) -> dict[str, Any]:
    # A base muda de usina para usina (Acopiara 30500, Aparecida 30544).
    for base in (30500, 30544):
        count = 0
        while count < 200:
            product = r.get(3, base + count * 22, 1)
            if not product or product[0] != TCU_PRODUCT_ID:
                break
            count += 1
        if count:
            return {"tcu_base": base, "tcu_count": count, "tcu_block": 22}
    return {}


def _huawei_native(r: Reader):
    model = _text(r.get(3, 30000, 15))
    if model.startswith("SUN2000"):
        return 0.95, f"modelo '{model}' em 30000"
    frequency = r.get(3, 32085, 1)
    if frequency and _grid_hz(frequency[0] / 100):
        return 0.75, f"frequencia {frequency[0] / 100:.2f} Hz em 32085 (mapa nativo)"
    return None


def _huawei_smartlogger(r: Reader):
    # 40000 e o relogio do SmartLogger em segundos Unix. A janela de um dia em
    # torno da hora atual separa o relogio de um contador qualquer de 32 bits.
    words = r.get(3, 40000, 2)
    if words:
        epoch = (words[0] << 16) | words[1]
        if abs(epoch - time.time()) <= 86400:
            return 0.9, f"relogio {time.strftime('%Y-%m-%d %H:%M', time.gmtime(epoch))} UTC em 40000"
    return None


def _nhs_ups(r: Reader):
    text = _text(r.get(3, 0, 10))
    if "NHS" in text:
        return 0.95, f"texto '{text}' em 0"
    return None


def _schneider_pm5100(r: Reader):
    words = r.get(3, 3109, 2)
    if words and _grid_hz(_f32(words)):
        return 0.9, f"frequencia {_f32(words):.2f} Hz em 3109 (float32)"
    return None


def _abb_m1m(r: Reader):
    words = r.get(3, 23346, 1)
    if words and _grid_hz(words[0] / 100):
        return 0.9, f"frequencia {words[0] / 100:.2f} Hz em 23346"
    return None


def _siemens_7sr10(r: Reader):
    words = r.get(4, 59, 2)
    if words and _grid_hz(_s32(words) / 1000):
        return 0.9, f"frequencia {_s32(words) / 1000:.3f} Hz em 30060 (FC04 59)"
    return None


def _sungrow_inverter(r: Reader):
    code = r.get(4, 4999, 1)
    serial = _text(r.get(4, 4989, 10))
    if code and code[0] and len(serial) >= 6:
        return 0.9, f"tipo 0x{code[0]:04X} em 5000 e serie '{serial}'"
    return None


def _pextron_urp6000(r: Reader):
    words = r.get(3, 711, 1)
    if words and _grid_hz(words[0] * 0.00390625):
        return 0.85, f"frequencia {words[0] * 0.00390625:.2f} Hz em 711"
    return None


def _solis_5g(r: Reader):
    words = r.get(4, 3042, 1)
    if words and _grid_hz(words[0] / 100):
        return 0.85, f"frequencia {words[0] / 100:.2f} Hz em 3043 (FC04 3042)"
    return None


def _growatt_tl3x(r: Reader):
    frequency, voltage = r.get(4, 37, 1), r.get(4, 50, 1)
    if frequency and voltage and _grid_hz(frequency[0] / 100) and _within(voltage[0] / 10, 100, 900):
        return 0.85, f"frequencia {frequency[0] / 100:.2f} Hz em 37 e tensao {voltage[0] / 10:.1f} V em 50"
    return None


def _growatt_legacy(r: Reader):
    words = r.get(4, 13, 1)
    if words and _grid_hz(words[0] / 100):
        return 0.8, f"frequencia {words[0] / 100:.2f} Hz em 13"
    return None


def _chint_cps(r: Reader):
    words = r.get(4, 43, 1)
    if words and _grid_hz(_s16(words[0]) / 10):
        return 0.8, f"frequencia {_s16(words[0]) / 10:.1f} Hz em 43"
    return None


def _schneider_p3u30(r: Reader):
    words = r.get(3, 2013, 3)
    if not words:
        return None
    volts = [_s16(value) for value in words]
    if min(volts) >= 100 and max(volts) <= 40000 and max(volts) - min(volts) <= 0.1 * max(volts):
        return 0.75, f"tensoes de linha {volts} V em 2013-2015"
    return None


def _novus_station(r: Reader):
    ambient, poa = r.get(3, 692, 2), r.get(3, 0, 2)
    if ambient and poa and _within(_f32(ambient), 5, 70) and _within(_f32(poa), -10, 2000):
        return 0.6, f"temperatura ambiente {_f32(ambient):.1f} C em 692 e POA {_f32(poa):.0f} W/m2 em 0"
    return None


def _acopiara_station(r: Reader):
    ambient, ghi = r.get(3, 5, 2), r.get(3, 7, 2)
    if ambient and ghi and _within(_f32(ambient), 5, 70) and _within(_f32(ghi), -10, 2000):
        return 0.55, f"temperatura ambiente {_f32(ambient):.1f} C em 5 e GHI {_f32(ghi):.0f} W/m2 em 7"
    return None


def _hukseflux_station(r: Reader):
    words = r.get(3, 0, 24)
    if not words:
        return None
    values = [_f32(words[index:index + 2]) for index in range(0, 24, 2)]
    if (any(abs(value) >= 0.01 for value in values)
            and all(_real_float(value) and _within(value, -100, 3000) for value in values)):
        return 0.5, f"12 float32 plausiveis em 0-23: {[round(value, 1) for value in values]}"
    return None


def _electron_ep4(r: Reader):
    words = r.get(3, 29, 4)
    if not words:
        return None
    temperatures = [_s16(value) / 10 for value in words]
    # Transformador em operacao nao fica abaixo de 15 C no clima das usinas.
    if sum(_within(value, 15, 150) for value in temperatures) >= 3 and len(set(temperatures)) > 1:
        return 0.5, f"temperaturas {temperatures} C em 29-32"
    return None


def _electron_ep4_whole(r: Reader):
    # Layout em graus inteiros do catalogo: maximas em 0-3 e atuais em 4-7.
    words = r.get(3, 0, 8)
    if not words:
        return None
    maximum, current = words[:4], words[4:]
    in_range = sum(_within(value, 15, 150) for value in current)
    if in_range >= 3 and len(set(current)) > 1 and all(high >= now for high, now in zip(maximum, current)):
        return 0.55, f"temperaturas atuais {current} C em 4-7 e maximas {maximum} C em 0-3"
    return None


def _sungrow_logger(r: Reader):
    code = r.get(4, 7999, 1)
    if code and code[0]:
        return 0.5, f"tipo 0x{code[0]:04X} em 8000"
    return None


def _self_described(r: Reader):
    words = r.get(3, 0, 10)
    text = _text(words)
    letters = sum(char.isalpha() for char in text)
    if words and letters >= 6 and len(text) >= 0.6 * len(words) * 2:
        return 0.3, f"texto '{text}' em 0"
    return None


# Ordem: identidade forte primeiro. A busca para no primeiro casamento com
# confianca >= CONFIDENT, o que poupa requisicoes em conversor serial.
SIGNATURES: tuple[Signature, ...] = (
    Signature("pextron_pcpt4", "Pextron", "PCPT4", "rele_termico", "skill mapa-de-rede/pextron_pcpt4.md", _pextron_pcpt4),
    Signature("sti_norland_ncu", "STI Norland", "NCU", "tracker_ncu", "skill mapa-de-rede/sti_norland_tracker.md",
              _sti_ncu, _sti_tcus, unit_agnostic=True),
    Signature("sti_norland_tcu", "STI Norland", "TCU", "tcu", "skill mapa-de-rede/sti_norland_tracker.md", _sti_tcu),
    Signature("huawei_sun2000_native", "Huawei", "SUN2000 (mapa nativo)", "inverter",
              "skill mapa-de-rede/huawei_sun2000.md", _huawei_native),
    Signature("huawei_smartlogger", "Huawei", "SmartLogger", "logger", "leitura de campo Betania 15/09/2026",
              _huawei_smartlogger),
    Signature("nhs_ups", "NHS", "Nobreak", "ups", "leitura de campo Betania 15/09/2026", _nhs_ups),
    Signature("schneider_pm5100", "Schneider", "PM5100", "meter", "catalogo V3 / REV15", _schneider_pm5100),
    Signature("abb_m1m", "ABB", "M1M", "meter", "REV15 runtime_templates", _abb_m1m),
    Signature("siemens_7sr10", "Siemens", "Reyrolle 7SR10", "relay", "skill mapa-de-rede/siemens_7sr10.md", _siemens_7sr10),
    Signature("sungrow_string_inverter", "Sungrow", "String inverter", "inverter",
              "skill mapa-de-rede/sungrow_string_inverter", _sungrow_inverter),
    Signature("pextron_urp6000", "Pextron", "URP6000", "relay", "skill mapa-de-rede/pextron_urp6000", _pextron_urp6000),
    Signature("solis_5g", "Solis", "5G trifasico", "inverter", "skill mapa-de-rede/solis_5g", _solis_5g),
    Signature("growatt_tl3x", "Growatt", "TL3-X", "inverter", "skill mapa-de-rede/growatt_tl3x", _growatt_tl3x),
    Signature("growatt_legacy", "Growatt", "Layout antigo (33 KTL3-S)", "inverter",
              "skill mapa-de-rede/growatt_legacy", _growatt_legacy),
    Signature("chint_cps", "Chint", "CPS 403X / SCA", "inverter", "catalogo V3", _chint_cps),
    Signature("schneider_p3u30", "Schneider", "P3U30", "relay", "catalogo V3 / REV15", _schneider_p3u30),
    Signature("novus_station", "Novus", "Estacao solarimetrica", "weather", "catalogo V3", _novus_station),
    Signature("acopiara_station", "Acopiara", "Estacao solarimetrica", "weather", "catalogo V3", _acopiara_station),
    Signature("hukseflux_station", "Hukseflux", "Estacao (12 float32)", "weather", "REV15 runtime_templates",
              _hukseflux_station),
    Signature("electron_ep4", "Electron", "EP4/TH104", "rele_termico", "catalogo V3", _electron_ep4),
    Signature("electron_ep4_whole", "Electron", "EP4/TH104 (graus inteiros)", "rele_termico", "catalogo V3",
              _electron_ep4_whole),
    Signature("sungrow_logger", "Sungrow", "Logger1000/3000", "logger", "skill mapa-de-rede/sungrow_logger1000",
              _sungrow_logger),
    Signature("self_described", "?", "texto de identificacao", "unknown", "registradores 0-9 em ASCII", _self_described),
)


def identify(reader: Reader, signatures: Iterable[Signature] = SIGNATURES) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for signature in signatures:
        try:
            result = signature.check(reader)
        except (IndexError, TypeError, ValueError, struct.error):
            result = None
        if not result:
            continue
        score, evidence = result
        item = {
            "key": signature.key,
            "manufacturer": signature.manufacturer,
            "model": signature.model,
            "device_type": signature.device_type,
            "score": score,
            "evidence": evidence,
            "source": signature.source,
            "unit_agnostic": signature.unit_agnostic,
        }
        if signature.extras:
            item.update(signature.extras(reader))
        found.append(item)
        if score >= CONFIDENT:
            break
    if any(item["score"] < IDENTIFIED for item in found) and reader.accepts_any_address():
        found = [item for item in found if item["score"] >= IDENTIFIED]
    return sorted(found, key=lambda item: -item["score"])


def unit_present(client: ModbusClient, unit: int) -> bool:
    try:
        function_code, payload = client.transact(unit, struct.pack(">BHH", 3, 0, 1))
    except (ModbusError, OSError):
        return False
    if function_code & 0x80:
        return bool(payload) and payload[0] in PRESENT_EXCEPTIONS
    return True


def parse_units(text: str) -> list[int]:
    units: set[int] = set()
    for part in filter(None, (piece.strip() for piece in text.split(","))):
        first, _, last = part.partition("-")
        low, high = int(first), int(last or first)
        if not 0 <= low <= high <= 255:
            raise ValueError(f"faixa de Unit ID invalida: {part}")
        units.update(range(low, high + 1))
    return sorted(units)


def expand_hosts(targets: Iterable[str]) -> list[str]:
    hosts: list[str] = []
    for target in targets:
        network = ipaddress.ip_network(target, strict=False)
        hosts.extend(str(host) for host in (network.hosts() if network.num_addresses > 1 else [network.network_address]))
    return hosts


def open_modbus_hosts(hosts: Iterable[str], port: int = 502, timeout: float = 0.8, workers: int = 32) -> list[str]:
    def is_open(host: str) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    hosts = list(hosts)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        flags = list(pool.map(is_open, hosts))
    return [host for host, flag in zip(hosts, flags) if flag]


def scan_host(host: str, units: Iterable[int], port: int = 502, timeout: float = 1.0,
              pacing: float = 0.2, stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    client = ModbusTCPClient(host, port, timeout=timeout)
    result: dict[str, Any] = {"host": host, "port": port, "units": []}
    try:
        for unit in units:
            if stop and stop():
                break
            if not unit_present(client, unit):
                continue
            time.sleep(pacing)
            reader = Reader(client, host, unit, pacing)
            matches = identify(reader)
            result["units"].append({"unit": unit, "matches": matches, "requests": reader.requests})
            if matches and matches[0]["unit_agnostic"]:
                result["unit_agnostic"] = True
                break
            time.sleep(pacing)
    finally:
        client.close()
    return result


class FinderJob:
    """Uma busca por vez, em thread, para a tela acompanhar o progresso.

    Os limites existem porque cada Unit ID sem equipamento custa o timeout
    inteiro: uma /24 com 247 IDs levaria horas e ocuparia os conversores.
    """

    MAX_HOSTS = 256
    MAX_UNITS = 64

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"running": False, "reports": []}

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def _update(self, **values: Any) -> None:
        with self._lock:
            self._state.update(values)

    def start(self, targets: Iterable[str], units_text: str, port: int = 502, timeout: float = 1.0,
              pacing: float = 0.2) -> dict[str, Any]:
        targets = [str(target).strip() for target in targets if str(target).strip()]
        units = parse_units(units_text)
        if not units or len(units) > self.MAX_UNITS:
            raise ValueError(f"informe de 1 a {self.MAX_UNITS} Unit IDs")
        hosts = expand_hosts(targets)
        if not hosts or len(hosts) > self.MAX_HOSTS:
            raise ValueError(f"informe de 1 a {self.MAX_HOSTS} enderecos (uma /24 no maximo)")
        with self._lock:
            if self._state.get("running"):
                raise ValueError("ja existe uma busca em andamento")
            self._cancel.clear()
            self._state = {
                "running": True, "phase": "portas", "started_at": int(time.time()), "finished_at": None,
                "targets": targets, "units": units_text, "hosts_total": len(hosts), "open_hosts": [],
                "hosts_done": 0, "current": "", "reports": [], "error": "", "cancelled": False,
            }
        threading.Thread(target=self._run, args=(hosts, units, port, timeout, pacing),
                         name="device-finder", daemon=True).start()
        return self.snapshot()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        return self.snapshot()

    def _run(self, hosts: list[str], units: list[int], port: int, timeout: float, pacing: float) -> None:
        try:
            open_hosts = open_modbus_hosts(hosts, port)
            self._update(phase="unit_ids", open_hosts=open_hosts)
            for host in open_hosts:
                if self._cancel.is_set():
                    break
                self._update(current=host)
                report = scan_host(host, units, port, timeout, pacing, stop=self._cancel.is_set)
                with self._lock:
                    self._state["reports"].append(report)
                    self._state["hosts_done"] += 1
        except Exception as exc:  # noqa: BLE001 - a falha vai para a tela em vez de matar a thread
            self._update(error=str(exc))
        finally:
            self._update(running=False, phase="concluido", current="", finished_at=int(time.time()),
                         cancelled=self._cancel.is_set())


def _format(report: dict[str, Any]) -> str:
    lines = [f"{report['host']}:{report['port']}"]
    if not report["units"]:
        lines.append("  nenhum Unit ID respondeu")
    if report.get("unit_agnostic"):
        lines.append("  responde igual em qualquer Unit ID - demais IDs nao varridos")
    for entry in report["units"]:
        best = entry["matches"][0] if entry["matches"] else None
        if best and best["score"] >= MIN_SCORE:
            extra = "".join(f" {key}={best[key]}" for key in ("tcu_base", "tcu_count") if key in best)
            verdict = "" if best["score"] >= IDENTIFIED else "possivel "
            lines.append(f"  unit {entry['unit']:>3}: {verdict}{best['manufacturer']} {best['model']} "
                         f"[{best['device_type']}] confianca {best['score']:.2f} - {best['evidence']}{extra}")
        else:
            hint = f" ({best['evidence']})" if best else ""
            lines.append(f"  unit {entry['unit']:>3}: nao identificado{hint}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Achar equipamentos Modbus TCP conhecidos (somente leitura)")
    parser.add_argument("targets", nargs="+", help="IP ou rede CIDR, ex.: 192.168.1.64/26")
    parser.add_argument("--units", default="1-20,247,249,255", help="Unit IDs a testar, ex.: 1-12,245-255")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--pacing", type=float, default=0.2, help="intervalo entre requisicoes, em segundos")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    units = parse_units(args.units)
    hosts = open_modbus_hosts(expand_hosts(args.targets), args.port)
    reports = []
    for host in hosts:
        report = scan_host(host, units, args.port, args.timeout, args.pacing)
        reports.append(report)
        if not args.json:
            print(_format(report), flush=True)
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    elif not hosts:
        print("nenhum host com a porta Modbus aberta")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
