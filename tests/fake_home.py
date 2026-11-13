"""Several simulated devices (lamp, air purifier, robot vacuum, air fryer) on 127.0.0.x,
for trying the UI without real hardware:   python tests/fake_home.py"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests import fake_device as fd  # noqa: E402


def urn(kind, name, dev):
    return f"urn:miot-spec-v2:{kind}:{name}:0000:{dev}:1"


def prop(iid, name, desc, fmt, access, unit=None, rng=None, values=None, dev="fake"):
    p = {"iid": iid, "type": urn("property", name, dev), "description": desc, "format": fmt, "access": access}
    if unit:
        p["unit"] = unit
    if rng:
        p["value-range"] = rng
    if values:
        p["value-list"] = [{"value": v, "description": d} for v, d in values]
    return p


def action(iid, name, desc, ins=()):
    return {"iid": iid, "type": urn("action", name, "fake"), "description": desc, "in": list(ins), "out": []}


RW, R = ["read", "write", "notify"], ["read", "notify"]
INFO = {"iid": 1, "type": urn("service", "device-information", "fake"), "description": "Device Information",
        "properties": [prop(1, "manufacturer", "Device Manufacturer", "string", ["read"])]}

PURIFIER_SPEC = {"type": urn("device", "air-purifier", "fake"), "services": [
    INFO,
    {"iid": 2, "type": urn("service", "air-purifier", "fake"), "description": "Air Purifier", "properties": [
        prop(1, "on", "Switch Status", "bool", RW),
        prop(2, "fault", "Device Fault", "uint8", R, values=[(0, "No Faults"), (1, "Sensor Error")]),
        prop(4, "mode", "Mode", "uint8", RW, values=[(0, "Auto"), (1, "Sleep"), (2, "Favorite"), (3, "Manual")]),
        prop(5, "fan-level", "Fan Level", "uint8", RW, values=[(1, "Level 1"), (2, "Level 2"), (3, "Level 3")]),
    ]},
    {"iid": 3, "type": urn("service", "environment", "fake"), "description": "Environment", "properties": [
        prop(4, "pm2.5-density", "PM2.5 Density", "float", R, "μg/m3", [0, 600, 1]),
        prop(1, "relative-humidity", "Relative Humidity", "uint8", R, "percentage", [0, 100, 1]),
        prop(7, "temperature", "Temperature", "float", R, "celsius", [-40, 125, 0.1]),
    ]},
    {"iid": 4, "type": urn("service", "filter", "fake"), "description": "Filter", "properties": [
        prop(1, "filter-life-level", "Filter Life Level", "uint8", R, "percentage", [0, 100, 1]),
        prop(4, "filter-left-time", "Filter Left Time", "uint16", R, "days", [0, 9999, 1]),
    ], "actions": [action(1, "reset-filter-life", "Reset Filter Life")]},
    {"iid": 6, "type": urn("service", "alarm", "fake"), "description": "Alarm", "properties": [
        prop(1, "alarm", "Alarm", "bool", RW)]},
    {"iid": 13, "type": urn("service", "screen", "fake"), "description": "Screen", "properties": [
        prop(2, "brightness", "Brightness", "uint8", RW, values=[(0, "Off"), (1, "Dim"), (2, "Bright")])]},
    {"iid": 8, "type": urn("service", "physical-control-locked", "fake"), "description": "Child Lock",
     "properties": [prop(1, "physical-controls-locked", "Child Lock", "bool", RW)]},
    {"iid": 10, "type": urn("service", "indicator-light", "fake"), "description": "Indicator Light", "properties": [
        prop(1, "on", "Switch Status", "bool", RW)]},
    {"iid": 11, "type": urn("service", "delay", "fake"), "description": "Delay", "properties": [
        prop(1, "countdown-time", "Countdown Time", "uint16", RW, "minutes", [0, 480, 30])]},
    {"iid": 12, "type": urn("service", "power-state", "fake"), "description": "Power State", "properties": [
        prop(1, "default-power-on-state", "Default Power On State", "uint8", RW,
             values=[(0, "Off"), (1, "On"), (2, "Previous")])]},
    {"iid": 9, "type": urn("service", "custom-service", "fake"), "description": "Favorite Level", "properties": [
        prop(11, "favorite-level", "Favorite Level", "uint8", RW, rng=[0, 14, 1]),
        prop(1, "motor-speed", "Motor Speed", "uint16", R, "rpm", [0, 3000, 1]),
        prop(2, "rssi", "RSSI", "int8", R, rng=[-100, 0, 1])]},
]}
PURIFIER_STATE = {(2, 1): True, (2, 2): 0, (2, 4): 0, (2, 5): 2, (3, 4): 12.0, (3, 1): 46, (3, 7): 22.8,
                  (4, 1): 71, (4, 4): 184, (6, 1): True, (13, 2): 2, (8, 1): False, (9, 11): 8, (9, 1): 812, (9, 2): -54, (10, 1): True, (11, 1): 0, (12, 1): 2}

VACUUM_SPEC = {"type": urn("device", "vacuum", "fake"), "services": [
    INFO,
    {"iid": 2, "type": urn("service", "vacuum", "fake"), "description": "Robot Cleaner", "properties": [
        prop(1, "status", "Status", "uint8", R, values=[(1, "Sweeping"), (2, "Idle"), (3, "Paused"), (4, "Error"),
                                                        (5, "Go Charging"), (6, "Charging")]),
        prop(2, "fault", "Device Fault", "uint8", R, values=[(0, "No Faults")]),
        prop(4, "mode", "Mode", "uint8", RW, values=[(0, "Silent"), (1, "Standard"), (2, "Strong"), (3, "Turbo")]),
        prop(5, "cleaning-time", "Cleaning Time", "uint32", R, "minutes", [0, 999, 1]),
        prop(6, "cleaning-area", "Cleaning Area", "uint32", R, "square-meter", [0, 999, 1]),
    ], "actions": [action(1, "start-sweep", "Start Sweep"), action(2, "stop-sweeping", "Stop Sweeping"),
                   action(3, "pause-sweeping", "Pause")]},
    {"iid": 3, "type": urn("service", "battery", "fake"), "description": "Battery", "properties": [
        prop(1, "battery-level", "Battery Level", "uint8", R, "percentage", [0, 100, 1])],
     "actions": [action(1, "start-charge", "Start Charge")]},
    {"iid": 4, "type": urn("service", "brush-cleaner", "fake"), "description": "Main Brush", "properties": [
        prop(1, "brush-life-level", "Brush Life Level", "uint8", R, "percentage", [0, 100, 1]),
        prop(2, "brush-left-time", "Brush Left Time", "uint16", R, "hours", [0, 300, 1])],
     "actions": [action(1, "reset-brush-life", "Reset Brush Life")]},
    {"iid": 5, "type": urn("service", "identify", "fake"), "description": "Identify",
     "actions": [action(1, "identify", "Find Device")]},
    {"iid": 7, "type": urn("service", "audio", "fake"), "description": "Audio", "properties": [
        prop(1, "volume", "Volume", "uint8", RW, "percentage", [0, 100, 1])]},
]}
VACUUM_STATE = {(2, 1): 6, (2, 2): 0, (2, 4): 1, (2, 5): 34, (2, 6): 41, (3, 1): 87, (4, 1): 8, (4, 2): 24, (7, 1): 60}


def vacuum_action(state, siid, aiid, ins):
    state[(2, 1)] = {(2, 1): 1, (2, 2): 2, (2, 3): 3, (3, 1): 5}.get((siid, aiid), state[(2, 1)])


FRYER_SPEC = {"type": urn("device", "air-fryer", "fake"), "services": [
    INFO,
    {"iid": 2, "type": urn("service", "air-fryer", "fake"), "description": "Air Fryer", "properties": [
        prop(1, "status", "Status", "uint8", R, values=[(0, "Shutdown"), (1, "Standby"), (2, "Pause"),
                                                        (3, "Appointment"), (4, "Cooking"), (5, "Preheat"),
                                                        (6, "Cooked")]),
        prop(3, "target-time", "Target Time", "uint16", RW, "minutes", [1, 60, 1]),
        prop(4, "target-temperature", "Target Temperature", "uint8", RW, "celsius", [40, 200, 5]),
        prop(5, "left-time", "Left Time", "uint16", R, "minutes", [0, 60, 1]),
        prop(9, "cooking-mode", "Recipe", "uint8", RW, values=[(0, "Custom"), (1, "Fries"), (2, "Chicken"),
                                                                (3, "Steak"), (4, "Fish"), (5, "Vegetable"),
                                                                (6, "Cake"), (7, "Defrost")]),
    ], "actions": [action(1, "start-cook", "Start Cooking"), action(2, "cancel-cooking", "Cancel Cooking"),
                   action(3, "pause", "Pause")]},
]}
FRYER_STATE = {(2, 1): 4, (2, 3): 18, (2, 4): 180, (2, 5): 7, (2, 9): 1}


def fryer_action(state, siid, aiid, ins):
    state[(2, 1)] = {1: 4, 2: 1, 3: 2}.get(aiid, state[(2, 1)])


DEVICES = [
    ("127.0.0.1", fd.LAMP, fd.SPEC, "Living room lamp", "Living room"),
    ("127.0.0.2", {"token": "11" * 16, "did": 0x2000001, "model": "zhimi.airpurifier.fake",
                   "state": PURIFIER_STATE, "action": lambda *a: None}, PURIFIER_SPEC, "Air purifier", "Living room"),
    ("127.0.0.3", {"token": "22" * 16, "did": 0x2000002, "model": "dreame.vacuum.fake",
                   "state": VACUUM_STATE, "action": vacuum_action}, VACUUM_SPEC, "Robot vacuum", "Living room"),
    ("127.0.0.4", {"token": "33" * 16, "did": 0x2000003, "model": "careli.fryer.fake",
                   "state": FRYER_STATE, "action": fryer_action}, FRYER_SPEC, "Air fryer", "Kitchen"),
]


def start_all():
    stop = threading.Event()
    for host, dev, _spec, _n, _r in DEVICES:
        fd.start_background(host, dev, stop)
    return stop


def config_entries():
    return [{"did": str(dev["did"]), "name": name, "model": dev["model"], "ip": host, "token": dev["token"],
             "room": room, "mode": "auto"} for host, dev, _s, name, room in DEVICES]


def install_specs(cache_dir):
    import json
    os.makedirs(cache_dir, exist_ok=True)
    for _h, dev, spec, _n, _r in DEVICES:
        with open(os.path.join(cache_dir, dev["model"] + ".json"), "w") as f:
            json.dump(spec, f)


if __name__ == "__main__":
    print("Fake devices running. Add them by IP + token:")
    for host, dev, _s, name, _r in DEVICES:
        print(f"  {name:18s} {host}  token {dev['token']}")
    stop = start_all()
    threading.Event().wait()
