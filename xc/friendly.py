"""Plain-language names, explanations and groups for device settings.

Xiaomi's device descriptions use technical names ("physical-controls-locked",
"filter-left-time"). This turns them into what a person expects to read, and sorts
each setting into a group: Settings, Timers, Maintenance, Actions or Technical details."""
from __future__ import annotations

from .spec import Action, Prop, Service

# property name -> (label, help). Label None keeps the device's own label.
PROPS = {
    "physical-controls-locked": ("Child lock", "Locks the buttons on the device itself."),
    "child-lock": ("Child lock", "Locks the buttons on the device itself."),
    "alarm": ("Sounds", "Beep when buttons are pressed or settings change."),
    "buzzer": ("Sounds", "Beep when buttons are pressed or settings change."),
    "mute": ("Mute", "Silence the device's sounds and voice prompts."),
    "volume": ("Volume", None),
    "no-disturb": ("Do not disturb", "Keeps the device quiet during the night hours."),
    "dnd": ("Do not disturb", "Keeps the device quiet during the night hours."),
    "indicator-light": ("Indicator light", "The status light on the device."),
    "led": ("Indicator light", "The status light on the device."),
    "screen-brightness": ("Display brightness", None),
    "display": ("Display", None),
    "temperature-display-unit": ("Temperature unit", None),
    "default-power-on-state": ("After a power cut", "What the device does when power comes back."),
    "power-on-state": ("After a power cut", "What the device does when power comes back."),
    "flex-mode": ("Flex mode", None),
    "favorite-level": ("Favorite speed", "The fan speed used in Favorite mode."),
    "favorite-speed": ("Favorite speed", "The fan speed used in Favorite mode."),
    "auto-keep": ("Keep settings", None),
    "countdown-time": ("Turn off after", "Switches the device off automatically after this time. 0 = never."),
    "off-delay-time": ("Turn off after", "Switches the device off automatically after this time. 0 = never."),
    "delay-time": ("Delay", None),
    "timer": ("Timer", None),
    "filter-life-level": ("Filter life", None),
    "filter-left-time": ("Filter time left", None),
    "filter-used-time": ("Filter used for", None),
    "brush-life-level": ("Brush life", None),
    "main-brush-life-level": ("Main brush life", None),
    "side-brush-life-level": ("Side brush life", None),
    "mop-life-level": ("Mop life", None),
    "brush-left-time": ("Brush time left", None),
    "fault": ("Error status", None),
    "motor-speed": ("Fan motor speed", None),
    "rssi": ("Wi-Fi signal", None),
    "firmware-revision": ("Firmware", None),
    "serial-number": ("Serial number", None),
    "air-quality": ("Air quality", None),
    "tank-filter": ("Water tank", None),
    "water-level": ("Water level", None),
    "dry": ("Drying", "Dries the filter after humidifying to prevent odours."),
    "speed-level": ("Speed", None),
    "horizontal-swing": ("Swing left/right", None),
    "vertical-swing": ("Swing up/down", None),
    "horizontal-angle": ("Swing angle", None),
    "anion": ("Ionizer", "Releases negative ions to freshen the air."),
    "heat-level": ("Heating level", None),
    "keep-warm": ("Keep warm", "Keeps food warm after cooking finishes."),
    "auto-keep-warm": ("Keep warm", "Keeps food warm after cooking finishes."),
}

ACTIONS = {
    "reset-filter-life": ("Reset filter", "Do this after you replace the filter."),
    "reset-brush-life": ("Reset brush", "Do this after you replace the brush."),
    "reset-main-brush-life": ("Reset main brush", "Do this after you replace the main brush."),
    "reset-side-brush-life": ("Reset side brush", "Do this after you replace the side brush."),
    "reset-mop-life": ("Reset mop", "Do this after you replace the mop."),
    "reset-filter": ("Reset filter", "Do this after you replace the filter."),
    "identify": ("Find device", "Makes the device beep or flash so you can find it."),
    "toggle": ("Toggle", None),
}

# names that only an engineer cares about
TECHNICAL = ("rssi", "motor-speed", "fault", "firmware", "serial", "voltage", "electric-current", "wifi",
             "country", "version", "code", "debug", "use-time", "work-time", "total", "count", "lifetime",
             "ota", "mcu", "button-pressed", "error")
MAINTENANCE = ("life", "filter", "brush", "mop", "consumable", "left-time", "used-time", "detergent",
               "dust-bag", "hepa", "sensor-dirty")
TIMERS = ("countdown", "timer", "delay", "appointment", "schedule", "reservation", "off-delay", "timing")

GROUP_ORDER = ["Settings", "Timers", "Maintenance", "Actions", "Technical details"]
GENERIC = ("on", "switch-status", "brightness", "mode", "level", "status", "color", "volume", "speed")


def prop_label(prop: Prop, service: Service | None, main_service_name: str = "") -> tuple[str, str | None]:
    if prop.name in PROPS:
        label, help_ = PROPS[prop.name]
        # "brightness" of a screen service reads better as "Display brightness" etc.
        if prop.name in GENERIC and service is not None and service.name != main_service_name:
            return _join(service, label or prop.label), help_
        return label or prop.label, help_
    if service is not None and service.name in PROPS and prop.name in ("on", "switch-status"):
        return PROPS[service.name]
    if prop.name in GENERIC and service is not None and service.name != main_service_name:
        if prop.name in ("on", "switch-status"):
            return service.label, None
        return _join(service, prop.label), None
    return prop.label, None


def _join(service: Service, label: str) -> str:
    svc = PROPS.get(service.name, (service.label, None))[0] or service.label
    if label.lower().startswith(svc.lower()):
        return label
    return f"{svc} {label[:1].lower()}{label[1:]}"


def action_label(action: Action) -> tuple[str, str | None]:
    if action.name in ACTIONS:
        return ACTIONS[action.name]
    return action.label, None


def _has(name: str, words) -> bool:
    """Whole-word match on dash-separated names ("default" must not match "fault")."""
    tokens = name.split("-")
    return any((w in name) if "-" in w else (w in tokens) for w in words)


def prop_group(prop: Prop, service: Service | None) -> str:
    n = prop.name
    svc = service.name if service else ""
    if not prop.writable:
        if _has(n, MAINTENANCE) or _has(svc, ("filter", "brush", "mop", "consumable")):
            return "Maintenance"
        return "Technical details"
    if _has(n, TIMERS) or _has(svc, TIMERS):
        return "Timers"
    if _has(n, TECHNICAL):
        return "Technical details"
    return "Settings"


def action_group(action: Action) -> str:
    if action.name.startswith("reset") or _has(action.name, MAINTENANCE):
        return "Maintenance"
    return "Actions"


def needs_confirmation(action: Action) -> bool:
    return _has(action.name, ("reset", "clear", "factory", "delete", "del", "remove", "unbind", "restore"))
