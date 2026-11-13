"""Device categories (for icons/grouping) and control profiles for older devices
that predate the MIoT spec and only understand legacy miIO commands."""
from __future__ import annotations

CATEGORIES = [
    ("vacuum", "🧹", "Vacuum", ("vacuum", "sweeper", "mop", "roborock", "dreame", "ijai", "viomi.v")),
    ("camera", "📷", "Camera", ("camera", "cateye", ".ipc", "chuangmi.cam", "isa.camera", "mxiang", "dbell",
                               "doorbell")),
    ("speaker", "🔊", "Speaker", ("wifispeaker", "speaker", "soundbar")),
    ("kitchen", "🍳", "Kitchen", ("fryer", "cooker", "kettle", "oven", "microwave", "foodprocessor")),
    ("air", "🌬️", "Air", ("airpurifier", "airp", "airfresh", "fan", "humidifier", "dehumidifier",
                         "heater", "aircondition", "airrtc", ".acpartner")),
    ("light", "💡", "Light", ("light", "lamp", "bulb", "yeelink", "ceiling", "strip.")),
    ("plug", "🔌", "Plug", ("plug", "outlet", "powerstrip", "switch", "ctrl_", ".relay")),
    ("sensor", "🌡️", "Sensor", ("sensor", "weather", "magnet", "motion", "plantmon", "flora")),
    ("gateway", "📡", "Gateway", ("gateway", "hub", "router")),
]


def category(model: str) -> tuple[str, str, str]:
    m = (model or "").lower()
    for key, icon, label, needles in CATEGORIES:
        if any(n in m for n in needles):
            return key, icon, label
    return "other", "📦", "Device"


# Legacy profiles -----------------------------------------------------------
ROBOROCK_STATES = {
    1: "Starting", 2: "Charger disconnected", 3: "Idle", 4: "Remote control", 5: "Cleaning",
    6: "Returning to dock", 7: "Manual mode", 8: "Charging", 9: "Charging problem", 10: "Paused",
    11: "Spot cleaning", 12: "Error", 13: "Shutting down", 14: "Updating", 15: "Docking",
    16: "Going to target", 17: "Zone cleaning", 18: "Room cleaning", 22: "Emptying bin",
    23: "Washing mop", 26: "Going to wash mop", 100: "Fully charged",
}

LEGACY_PROFILES = [
    {
        "match": ("roborock.vacuum", "rockrobo.vacuum"),
        "title": "Robot vacuum",
        "status": {"method": "get_status", "params": [], "kind": "dict"},
        "fields": [
            ("state", "Status", ROBOROCK_STATES, ""),
            ("battery", "Battery", None, "%"),
            ("fan_power", "Suction", {101: "Quiet", 102: "Balanced", 103: "Turbo", 104: "Max", 105: "Mop only"}, ""),
            ("clean_time", "Last clean time", None, "s"),
            ("clean_area", "Last clean area", "area", ""),
            ("error_code", "Error code", None, ""),
        ],
        "buttons": [
            ("▶  Start", "app_start", []),
            ("⏸  Pause", "app_pause", []),
            ("⏹  Stop", "app_stop", []),
            ("🏠  Dock", "app_charge", []),
            ("◎  Spot clean", "app_spot", []),
            ("🔔  Find me", "find_me", [""]),
        ],
        "choices": [
            ("Suction", "set_custom_mode", ["{v}"],
             [(101, "Quiet"), (102, "Balanced"), (103, "Turbo"), (104, "Max")]),
        ],
        "sliders": [],
    },
    {
        "match": ("yeelink.light",),
        "title": "Yeelight",
        "power": {"key": "power", "on_value": "on", "on": ("set_power", ["on", "smooth", 400]),
                  "off": ("set_power", ["off", "smooth", 400])},
        "status": {"method": "get_prop", "params": ["power", "bright", "ct", "rgb", "color_mode"], "kind": "list"},
        "fields": [
            ("power", "Power", None, ""),
            ("bright", "Brightness", None, "%"),
            ("ct", "Color temperature", None, "K"),
        ],
        "buttons": [("Toggle", "toggle", [])],
        "choices": [],
        "sliders": [
            ("Brightness", "set_bright", 1, 100, ["{v}", "smooth", 400], "bright"),
            ("Color temperature", "set_ct_abx", 1700, 6500, ["{v}", "smooth", 400], "ct"),
        ],
    },
    {
        "match": ("chuangmi.plug", "qmi.powerstrip", "zimi.powerstrip", "lumi.plug"),
        "title": "Smart plug",
        "power": {"key": "power", "on_value": "on", "on": ("set_power", ["on"]), "off": ("set_power", ["off"])},
        "status": {"method": "get_prop", "params": ["power", "temperature"], "kind": "list"},
        "fields": [("power", "Power", None, ""), ("temperature", "Temperature", None, "°C")],
        "buttons": [],
        "choices": [],
        "sliders": [],
    },
]


def legacy_profile(model: str) -> dict | None:
    m = (model or "").lower()
    for p in LEGACY_PROFILES:
        if any(m.startswith(x) for x in p["match"]):
            return p
    return None


def fill(template, value):
    if isinstance(template, list):
        return [fill(t, value) for t in template]
    if template == "{v}":
        return value
    return template
