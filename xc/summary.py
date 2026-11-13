"""Picks the few controls that matter out of a device's full MIoT spec, the way the
Xiaomi Home app does: a power button, a status line, modes, the main sliders, the main
actions and a handful of readings. Everything else goes under "More settings"."""
from __future__ import annotations

from dataclasses import dataclass

from .friendly import prop_label
from .spec import UNITS, Action, MiotSpec, Prop

STATUS_NAMES = ("status", "working-state", "cooking-status", "run-state", "state", "fault")
SLIDER_NAMES = ("brightness", "color-temperature", "fan-level", "speed-level", "target-temperature",
                "target-humidity", "volume", "fan-percent", "target-time", "cook-time", "target-cooking-time",
                "temperature-setting", "mop-mode-level")
MODE_NAMES = ("mode", "cooking-mode", "fan-level", "suction-level", "sweep-mode", "mop-mode", "water-level",
              "heat-level", "spray-level", "physical-controls-locked")
# live readings shown as tiles (consumables like filters go to Maintenance instead)
READING_NAMES = ("temperature", "relative-humidity", "pm2.5-density", "pm10-density", "co2-density",
                 "tvoc-density", "hcho-density", "air-quality", "battery-level", "illumination",
                 "electric-power", "power-consumption", "left-time", "cleaning-area", "cleaning-time",
                 "remain-time", "left-cook-time", "target-temperature", "water-level")
SKIP_ACTION_WORDS = ("reset", "clear", "factory", "set-", "get-", "query", "sync", "upgrade", "map")
# services that are not settings: the camera's cloud stream is opened from the "Live video" card
HIDDEN_SERVICES = ("device-information", "camera-stream-for-google-home", "camera-stream-for-amazon-alexa")
ACTION_ORDER = [  # checked in this order; first match decides the position
    (("charge", "home", "dock", "back"), 6), (("find", "locate", "identify"), 7),
    (("start", "play", "resume", "open"), 0), (("toggle",), 1), (("pause",), 2), (("stop", "cancel", "close"), 3),
    (("next",), 8), (("previous",), 9),
]


# What a home-screen tile shows, most telling first: (property name, sign, label).
TILE_METRICS = [
    ("temperature", "🌡", "Temperature"), ("relative-humidity", "💧", "Humidity"),
    ("pm2.5-density", "🌫", "PM2.5"), ("pm10-density", "🌫", "PM10"), ("co2-density", "🌫", "CO₂"),
    ("tvoc-density", "🌫", "TVOC"), ("hcho-density", "🌫", "HCHO"), ("air-quality", "🍃", "Air quality"),
    ("illumination", "☀", "Light"), ("electric-power", "⚡", "Power"),
    ("contact-state", "🚪", "Door"), ("motion-state", "🏃", "Motion"),
    ("battery-level", "🔋", "Battery"), ("filter-life-level", "♻", "Filter"),
    ("left-time", "⏱", "Time left"), ("cleaning-area", "🧹", "Cleaned"), ("cleaning-time", "⏱", "Cleaning"),
    ("target-temperature", "🎯", "Target"), ("target-humidity", "🎯", "Target humidity"),
    ("brightness", "🔆", "Brightness"), ("color-temperature", "💡", "Colour"), ("fan-level", "🌀", "Fan"),
    ("download-speed", "⬇", "Download"), ("upload-speed", "⬆", "Upload"),
    ("connected-device-number", "📶", "Clients"),
]
# units for values whose description names none
DEFAULT_UNITS = {"battery-level": "%", "filter-life-level": "%", "pm2.5-density": "µg/m³", "pm10-density": "µg/m³",
                 "brightness": "%", "cleaning-area": "m²"}
LIGHT_ONLY = ("brightness", "color-temperature")
MAX_METRICS = 6


@dataclass
class Metric:
    prop: Prop
    sign: str
    label: str


def _duration(value, unit: str) -> str:
    seconds = float(value) * {"seconds": 1, "minutes": 60, "hours": 3600}[unit]
    if seconds < 60:
        return f"{int(seconds)} s"
    minutes = int(round(seconds / 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60:02d} min"


def metric_text(prop: Prop, value) -> str:
    """A value the way a tile shows it: short, with its unit."""
    if value is None:
        return "—"
    if isinstance(value, bool) or prop.is_bool:
        if prop.name == "contact-state":
            return "Closed" if value else "Open"
        if prop.name == "motion-state":
            return "Motion" if value else "No motion"
        return "On" if value else "Off"
    for v in prop.values:
        if v.get("value") == value:
            return prop.value_text(value)
    if not isinstance(value, (int, float)):
        return str(value)[:18]
    if prop.unit in ("seconds", "minutes", "hours"):
        return _duration(value, prop.unit)
    unit = UNITS.get(prop.unit, prop.unit or "") or DEFAULT_UNITS.get(prop.name, "")
    if isinstance(value, float):
        value = round(value, 1) if abs(value) < 1000 else round(value)
        if value == int(value):
            value = int(value)
    return f"{value} {unit}".strip()


def _dev_type(spec: MiotSpec) -> str:
    parts = (spec.type or "").split(":")
    return parts[3] if len(parts) > 3 else ""


class Summary:
    def __init__(self, spec: MiotSpec):
        self.spec = spec
        self.dev_type = _dev_type(spec)
        services = [s for s in spec.services if s.name not in HIDDEN_SERVICES]
        main = next((s for s in services if s.name == self.dev_type), None)
        if main is None:
            main = next((s for s in services if any(p.writable for p in s.props) or s.actions),
                        services[0] if services else None)
        self.main = main
        ordered = ([main] if main else []) + [s for s in services if s is not main]
        self.services = services

        def find(names, pred, pool=None):
            for n in names:
                for s in (pool or ordered):
                    for p in s.props:
                        if p.name == n and pred(p):
                            return p
            return None

        self.power: Prop | None = find(("on",), lambda p: p.is_bool and p.writable and p.readable)
        self.status: Prop | None = find(STATUS_NAMES, lambda p: p.readable and (p.values or p.is_number)
                                        and not p.writable)
        self.battery: Prop | None = find(("battery-level",), lambda p: p.readable)

        modes = []
        for n in MODE_NAMES:
            for s in ordered:
                for p in s.props:
                    if (p.name == n and p.writable and p.values and 1 < len(p.values) <= 12
                            and p not in modes):
                        modes.append(p)
        self.modes: list[Prop] = modes[:3]

        sliders = []
        for n in SLIDER_NAMES:
            for s in ordered:
                for p in s.props:
                    if (p.name == n and p.writable and p.is_number and p.range and not p.values
                            and (p.range[1] - p.range[0]) > 1 and p not in sliders):
                        sliders.append(p)
        self.sliders: list[Prop] = sliders[:4]
        self.color: Prop | None = find(("color",), lambda p: p.is_color and p.writable)

        actions: list[Action] = []
        for s in ordered:
            for a in s.actions:
                if a.ins or any(w in a.name for w in SKIP_ACTION_WORDS):
                    continue
                if a.name == "toggle" and self.power is not None:
                    continue  # the power button already does this
                actions.append(a)

        def rank(a):
            for words, pos in ACTION_ORDER:
                if any(w in a.name for w in words):
                    return pos
            return 20
        actions.sort(key=rank)
        # first the best action of every role (start, pause, stop, dock, find...), then the rest: a vacuum
        # with ten kinds of "start" must still get its Pause and Dock buttons
        picked, roles = [], set()
        for a in actions:
            r = rank(a)
            if r != 20 and r not in roles:
                roles.add(r)
                picked.append(a)
        picked += [a for a in actions if a not in picked]
        self.actions: list[Action] = sorted(picked[:6], key=rank)

        featured = {id(x) for x in [self.power, self.status, self.color, *self.modes, *self.sliders] if x}
        readings = []
        for n in READING_NAMES:
            for s in ordered:
                for p in s.props:
                    if p.name == n and p.readable and not p.writable and id(p) not in featured \
                            and p not in readings:
                        readings.append(p)
        self.readings: list[Prop] = readings[:6]
        self.featured_props = {(p.siid, p.piid) for p in
                               [self.power, self.status, self.color, *self.modes, *self.sliders, *self.readings]
                               if p}
        self.featured_actions = {(a.siid, a.aiid) for a in self.actions}
        # readings for the home-screen tile
        found = []
        for name, sign, label in TILE_METRICS:
            if name in LIGHT_ONLY and self.dev_type != "light":
                continue  # e.g. the brightness of a purifier's display is a setting, not a reading
            hits = [(s, p) for s in ordered for p in s.props if p.name == name and p.readable]
            for s, p in hits[:3]:
                if p is self.power or (p.unit in (None, "", "none") and name == "power-consumption"):
                    continue
                if len(hits) > 1:  # e.g. the chambers of a fridge: name them by their part
                    part = s.label.replace(" Chamber", "").replace(" chamber", "")
                    label = f"{part} target"[:22] if name.startswith("target") else part[:22]
                found.append(Metric(p, sign, label))
        self.metrics: list[Metric] = found[:MAX_METRICS]
        # consumables in percent (filter, brushes, mop) - polled in the background for reminders
        self.life_levels: list[tuple[Prop, str]] = [
            (p, prop_label(p, s, main.name if main else "")[0])
            for s in services for p in s.props if p.readable and p.unit == "percentage" and "life" in p.name]

    # -- what the home-screen tile needs ---------------------------------------------------
    def metric_values(self, values: dict, limit: int = MAX_METRICS) -> list[tuple[str, str, str]]:
        """[(sign, label, text)] for the readings that are known right now."""
        out = []
        for m in self.metrics:
            v = values.get((m.prop.siid, m.prop.piid))
            if v is not None and v != "":
                out.append((m.sign, m.label, metric_text(m.prop, v)))
        return out[:limit]

    def tile_pairs(self) -> list[tuple[int, int]]:
        props = [self.power, self.status, self.battery, self.modes[0] if self.modes else None,
                 *(p for p, _label in self.life_levels), *(m.prop for m in self.metrics)]
        out = []
        for p in props:
            if p and p.readable and (p.siid, p.piid) not in out:
                out.append((p.siid, p.piid))
        return out

    def page_pairs(self) -> list[tuple[int, int]]:
        return [(p.siid, p.piid) for s in self.services for p in s.props if p.readable]

    def is_on(self, values: dict):
        if self.power is None:
            return None
        v = values.get((self.power.siid, self.power.piid))
        return None if v is None else bool(v)

    def status_text(self, values: dict, battery: bool = True) -> str:
        """battery=False: leave the battery out (the tile shows it among its readings)."""
        parts = []
        on = self.is_on(values)
        st = values.get((self.status.siid, self.status.piid)) if self.status else None
        if self.status is not None and self.status.name == "fault" and not st:
            st = None  # "No faults" is not worth a status line
        if st is not None and self.status.value_text(st).lower() in ("none", "no faults", "no fault", "normal"
                                                                     ) and self.status.name == "fault":
            st = None
        if st is not None:
            parts.append(self.status.value_text(st))
        elif on is not None:
            parts.append("On" if on else "Off")
        if self.modes and on is not False:
            m = self.modes[0]
            v = values.get((m.siid, m.piid))
            if v is not None and m.name in ("mode", "cooking-mode") and m.value_text(v).lower() != "none":
                parts.append(m.value_text(v))
        if self.battery and battery:
            b = values.get((self.battery.siid, self.battery.piid))
            if b is not None:
                parts.append(f"{b}% battery")
        return " · ".join(parts)


class LegacySummary:
    """Tile/hero behaviour for legacy devices described by a profile in xc.catalog."""

    def __init__(self, profile: dict):
        self.profile = profile

    def parse(self, result) -> dict:
        st = self.profile["status"]
        if st["kind"] == "dict":
            if isinstance(result, list) and result and isinstance(result[0], dict):
                return result[0]
            return result if isinstance(result, dict) else {}
        return dict(zip(st["params"], result)) if isinstance(result, list) else {}

    def is_on(self, data: dict):
        pw = self.profile.get("power")
        if not pw or pw["key"] not in data:
            return None
        return data[pw["key"]] == pw["on_value"]

    def metric_values(self, data: dict, limit: int = MAX_METRICS) -> list[tuple[str, str, str]]:
        out = []
        for key, title, mapping, unit in self.profile["fields"]:
            if key not in data or key == "power":
                continue
            v = data[key]
            text = str(mapping.get(v, v)) if isinstance(mapping, dict) else f"{v} {unit}".strip()
            out.append(("", title, text))
        return out[:limit]

    def status_text(self, data: dict, battery: bool = True) -> str:
        parts = []
        for key, _title, mapping, unit in self.profile["fields"][:2]:
            if key not in data:
                continue
            v = data[key]
            if isinstance(mapping, dict):
                parts.append(str(mapping.get(v, v)))
            elif key == "power":
                parts.append("On" if v == "on" else "Off")
            elif unit == "%" and key == "battery":
                parts.append(f"{v}% battery")
            else:
                parts.append(f"{v}{unit if unit != '%' else '%'}")
        return " · ".join(parts)
