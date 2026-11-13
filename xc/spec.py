"""MIoT specification loader.

Every modern Xiaomi device publishes a "spec": a list of services, each with
properties (on/off, brightness, mode, ...) and actions (start cleaning, ...).
Specs are downloaded from miot-spec.org once and cached on disk, then used to
build the device's control panel automatically.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field

import requests

HOSTS = ["https://miot-spec.org", "https://spec.miot-spec.com"]
INSTANCES_TTL = 7 * 86400
RETRY_AFTER_FAILURE = 120  # seconds before the 7 MB model list is downloaded again after a failure


def _write_json(path: str, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _urn_name(urn: str) -> str:
    # urn:miot-spec-v2:property:on:00000006:xiaomi-lamp:1  ->  on
    parts = (urn or "").split(":")
    return parts[3] if len(parts) > 3 else urn


def _title(text: str) -> str:
    text = re.sub(r"[-_]+", " ", text or "").strip()
    return text[:1].upper() + text[1:] if text else ""


@dataclass
class Prop:
    siid: int
    piid: int
    name: str
    description: str
    format: str
    access: list
    unit: str = ""
    range: list | None = None
    values: list = field(default_factory=list)  # [{"value": .., "description": ..}]

    @property
    def readable(self) -> bool:
        return "read" in self.access

    @property
    def writable(self) -> bool:
        return "write" in self.access

    @property
    def label(self) -> str:
        return _title(self.description or self.name)

    @property
    def is_bool(self) -> bool:
        return self.format == "bool"

    @property
    def is_number(self) -> bool:
        return self.format.startswith(("int", "uint", "float"))

    @property
    def is_float(self) -> bool:
        return self.format == "float"

    @property
    def is_color(self) -> bool:
        return self.name == "color" and self.format.startswith("uint")

    def value_text(self, value) -> str:
        if value is None:
            return "—"
        for v in self.values:
            if v.get("value") == value:
                return _title(v.get("description") or str(value))
        if self.is_bool:
            return "On" if value else "Off"
        if self.is_color and isinstance(value, int):
            return f"#{value:06X}"
        unit = UNITS.get(self.unit, self.unit or "")
        if isinstance(value, float):
            value = round(value, 2)
        return f"{value}{(' ' + unit) if unit else ''}"


UNITS = {
    "percentage": "%", "celsius": "°C", "seconds": "s", "minutes": "min", "hours": "h",
    "days": "days", "kelvin": "K", "pascal": "Pa", "watt": "W", "kWh": "kWh", "lux": "lx",
    "mg/m3": "mg/m³", "μg/m3": "µg/m³", "ppm": "ppm", "arcdegrees": "°", "metre": "m",
    "none": "", "rgb": "", "L": "L", "volt": "V", "ampere": "A", "decibel": "dB",
    "square-meter": "m²", "fahrenheit": "°F",
}


@dataclass
class Action:
    siid: int
    aiid: int
    name: str
    description: str
    ins: list  # list of piids within the same service
    out: list = field(default_factory=list)  # piids of the values the action returns

    @property
    def label(self) -> str:
        return _title(self.description or self.name)


@dataclass
class Service:
    siid: int
    name: str
    description: str
    props: list
    actions: list

    @property
    def label(self) -> str:
        return _title(self.description or self.name)


class MiotSpec:
    def __init__(self, data: dict):
        self.raw = data
        self.type = data.get("type", "")
        self.description = data.get("description", "")
        self.services: list[Service] = []
        for s in data.get("services") or []:
            siid = s.get("iid")
            props = []
            for p in s.get("properties") or []:
                props.append(Prop(
                    siid=siid, piid=p.get("iid"), name=_urn_name(p.get("type", "")),
                    description=p.get("description", ""), format=p.get("format", ""),
                    access=p.get("access") or [], unit=p.get("unit") or "",
                    range=p.get("value-range"), values=p.get("value-list") or [],
                ))
            actions = [
                Action(siid=siid, aiid=a.get("iid"), name=_urn_name(a.get("type", "")),
                       description=a.get("description", ""), ins=a.get("in") or [], out=a.get("out") or [])
                for a in s.get("actions") or []
            ]
            self.services.append(Service(siid, _urn_name(s.get("type", "")), s.get("description", ""),
                                         props, actions))

    def prop(self, siid: int, piid: int) -> Prop | None:
        for s in self.services:
            if s.siid == siid:
                for p in s.props:
                    if p.piid == piid:
                        return p
        return None

    def find(self, service_name: str, prop_name: str) -> Prop | None:
        for s in self.services:
            if s.name == service_name:
                for p in s.props:
                    if p.name == prop_name:
                        return p
        return None

    def readable_pairs(self) -> list[tuple[int, int]]:
        return [(p.siid, p.piid) for s in self.services for p in s.props if p.readable]


class SpecStore:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._instances: dict | None = None
        self._instances_failed_at = 0.0
        self._mem: dict[str, MiotSpec | None] = {}

    def _download(self, path: str, timeout=60) -> dict:
        last = None
        for host in HOSTS:
            try:
                r = requests.get(host + path, timeout=timeout)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}"
            except requests.ConnectionError:
                last = "no connection"
            except (requests.RequestException, ValueError) as exc:
                last = exc.__class__.__name__
        raise RuntimeError(f"miot-spec.org could not be reached ({last})")

    def _instances_map(self) -> dict:
        if self._instances is not None:
            return self._instances
        path = os.path.join(self.cache_dir, "instances.json")
        cached = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cached = json.load(f)
            except (OSError, ValueError):
                cached = {}
        if cached and time.time() - cached.get("_updated", 0) < INSTANCES_TTL:
            self._instances = cached
            return cached
        if time.time() - self._instances_failed_at < RETRY_AFTER_FAILURE and not cached:
            raise RuntimeError("miot-spec.org could not be reached a moment ago - will retry shortly")
        try:
            data = self._download("/miot-spec-v2/instances?status=all", timeout=120)
        except RuntimeError:
            self._instances_failed_at = time.time()
            if cached:
                self._instances = cached
                return cached
            raise
        out: dict = {"_updated": time.time()}
        for v in data.get("instances") or []:
            m = v.get("model")
            old = out.get(m)
            if old:
                if old.get("status") == "released" and v.get("status") != "released":
                    continue
                if v.get("version", 0) < old.get("version", 0):
                    continue
            out[m] = {"type": v.get("type"), "status": v.get("status"), "version": v.get("version", 0)}
        _write_json(path, out)
        self._instances = out
        return out

    def get(self, model: str) -> MiotSpec | None:
        """Spec for a model, or None if the model has no MIoT spec (legacy device)."""
        if not model:
            return None
        with self._lock:
            if model in self._mem:
                return self._mem[model]
            spec_file = os.path.join(self.cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".json")
            data = None
            if os.path.exists(spec_file):
                try:
                    with open(spec_file, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = None
            if data is None:
                typ = (self._instances_map().get(model) or {}).get("type")
                if not typ:
                    self._mem[model] = None
                    return None
                data = self._download(f"/miot-spec-v2/instance?type={typ}")
                _write_json(spec_file, data)
            spec = MiotSpec(data) if data.get("services") else None
            self._mem[model] = spec
            return spec

    def put(self, model: str, data: dict):
        """Install a spec manually (e.g. imported from a file)."""
        spec_file = os.path.join(self.cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".json")
        _write_json(spec_file, data)
        self._mem.pop(model, None)

    def forget(self, model: str):
        spec_file = os.path.join(self.cache_dir, re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".json")
        if os.path.exists(spec_file):
            os.remove(spec_file)
        self._mem.pop(model, None)
