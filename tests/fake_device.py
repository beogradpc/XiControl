"""A simulated Xiaomi Wi-Fi device speaking the miIO protocol, for testing.

Run:  python tests/fake_device.py            (listens on 0.0.0.0:54321)
Token: 00112233445566778899aabbccddeeff, model: xiaomi.light.fake
"""
import json
import os
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from xc.miio import HELLO, MAGIC, PORT, Crypto, _md5  # noqa: E402

TOKEN = "00112233445566778899aabbccddeeff"
DEVICE_ID = 0x1A2B3C4D
MODEL = "xiaomi.light.fake"

SPEC = {
    "type": "urn:miot-spec-v2:device:light:0000A001:xiaomi-fake:1",
    "description": "Light",
    "services": [
        {"iid": 1, "type": "urn:miot-spec-v2:service:device-information:00007801:xiaomi-fake:1",
         "description": "Device Information",
         "properties": [
             {"iid": 1, "type": "urn:miot-spec-v2:property:manufacturer:00000001:xiaomi-fake:1",
              "description": "Device Manufacturer", "format": "string", "access": ["read"]},
             {"iid": 2, "type": "urn:miot-spec-v2:property:model:00000002:xiaomi-fake:1",
              "description": "Device Model", "format": "string", "access": ["read"]}]},
        {"iid": 2, "type": "urn:miot-spec-v2:service:light:00007802:xiaomi-fake:1", "description": "Light",
         "properties": [
             {"iid": 1, "type": "urn:miot-spec-v2:property:on:00000006:xiaomi-fake:1", "description": "Switch Status",
              "format": "bool", "access": ["read", "write", "notify"]},
             {"iid": 2, "type": "urn:miot-spec-v2:property:mode:00000008:xiaomi-fake:1", "description": "Mode",
              "format": "uint8", "access": ["read", "write", "notify"],
              "value-list": [{"value": 0, "description": "Day"}, {"value": 1, "description": "Night"},
                             {"value": 2, "description": "Reading"}]},
             {"iid": 3, "type": "urn:miot-spec-v2:property:brightness:0000000D:xiaomi-fake:1",
              "description": "Brightness", "format": "uint8", "access": ["read", "write", "notify"],
              "unit": "percentage", "value-range": [1, 100, 1]},
             {"iid": 4, "type": "urn:miot-spec-v2:property:color-temperature:0000000F:xiaomi-fake:1",
              "description": "Color Temperature", "format": "uint32", "access": ["read", "write", "notify"],
              "unit": "kelvin", "value-range": [2700, 6500, 1]},
             {"iid": 5, "type": "urn:miot-spec-v2:property:color:0000000E:xiaomi-fake:1", "description": "Color",
              "format": "uint32", "access": ["read", "write", "notify"], "unit": "rgb",
              "value-range": [1, 16777215, 1]}],
         "actions": [{"iid": 1, "type": "urn:miot-spec-v2:action:toggle:00002811:xiaomi-fake:1",
                      "description": "Toggle", "in": [], "out": []}]},
        {"iid": 3, "type": "urn:miot-spec-v2:service:environment:0000780A:xiaomi-fake:1",
         "description": "Environment",
         "properties": [
             {"iid": 1, "type": "urn:miot-spec-v2:property:temperature:00000020:xiaomi-fake:1",
              "description": "Temperature", "format": "float", "access": ["read", "notify"], "unit": "celsius",
              "value-range": [-30, 100, 0.1]},
             {"iid": 2, "type": "urn:miot-spec-v2:property:filter-life-level:0000001E:xiaomi-fake:1",
              "description": "Filter Life Level", "format": "uint8", "access": ["read", "notify"],
              "unit": "percentage", "value-range": [0, 100, 1]}]},
        {"iid": 4, "type": "urn:miot-spec-v2:service:custom:0000780B:xiaomi-fake:1",
         "description": "Timer",
         "properties": [
             {"iid": 1, "type": "urn:miot-spec-v2:property:countdown-time:00000055:xiaomi-fake:1",
              "description": "Countdown Time", "format": "uint16", "access": ["read", "write"],
              "unit": "minutes", "value-range": [0, 480, 1]},
             {"iid": 2, "type": "urn:miot-spec-v2:property:alarm:00000012:xiaomi-fake:1",
              "description": "Alarm", "format": "bool", "access": ["read", "write"]}],
         "actions": [{"iid": 1, "type": "urn:miot-spec-v2:action:start-countdown:00002812:xiaomi-fake:1",
                      "description": "Start Countdown", "in": [1], "out": []}]},
    ],
}

STATE = {(1, 1): "Xiaomi", (1, 2): MODEL, (2, 1): True, (2, 2): 0, (2, 3): 80, (2, 4): 4000, (2, 5): 0xFF8800,
         (3, 1): 23.4, (3, 2): 87, (4, 1): 0, (4, 2): False}


def _lamp_action(state, siid, aiid, ins):
    if (siid, aiid) == (2, 1):
        state[(2, 1)] = not state[(2, 1)]
    if (siid, aiid) == (4, 1):
        state[(4, 1)] = ins[0]["value"] if ins else 0


LAMP = {"token": TOKEN, "did": DEVICE_ID, "model": MODEL, "state": STATE, "action": _lamp_action}


def make_hub(children: dict, token="ab" * 16, did=0x3000001):
    """A simulated Mi Control Hub (lumi.gateway.mieu01). children: {sid: {"neutral_0": "on" | "off" | "unknown"}}
    are its Zigbee plugs; they are read and switched through the hub, as on the real one."""
    def hub_handle(req):
        m, p = req.get("method"), req.get("params")
        if m == "miIO.info":
            return {"model": "lumi.gateway.mieu01", "fw_ver": "3.5.9_004", "mac": "AA:BB:CC:00:00:01"}
        if m == "get_device_prop_exp":
            return [[children.get(row[0], {}).get(name, "unknown") for name in row[1:]] for row in p]
        if m == "toggle_plug":
            child = children.get(req.get("sid"))
            if child is None or child.get(p[0]) == "unknown":
                return {"__error": {"code": -5001, "message": "device offline"}}
            child[p[0]] = p[1]
            return ["ok"]
        if m == "get_properties":      # the hub itself doesn't speak MIoT on the LAN
            return [{**x, "code": -4007} for x in p]
        return {"__error": {"code": -5005, "message": "params error"}}
    return {"token": token, "did": did, "model": "lumi.gateway.mieu01", "state": {}, "handle": hub_handle}


def handle(req, dev=None):
    dev = dev or LAMP
    if dev.get("handle"):
        return dev["handle"](req)
    state = dev["state"]
    m, p = req.get("method"), req.get("params")
    if m == "miIO.info":
        return {"model": dev["model"], "fw_ver": "1.0.0", "mac": "AA:BB:CC:DD:EE:FF",
                "netif": {"localIp": "127.0.0.1"}}
    if m == "get_properties":
        out = []
        for x in p:
            k = (x["siid"], x["piid"])
            if k in state:
                out.append({**x, "code": 0, "value": state[k]})
            else:
                out.append({**x, "code": -4003})
        return out
    if m == "set_properties":
        out = []
        for x in p:
            state[(x["siid"], x["piid"])] = x["value"]
            out.append({"did": x["did"], "siid": x["siid"], "piid": x["piid"], "code": 0})
        return out
    if m == "action":
        dev["action"](state, p["siid"], p["aiid"], p.get("in") or [])
        return {"code": 0}
    return {"__error": {"code": -9999, "message": f"unknown method {m}"}}


def serve(host="0.0.0.0", port=PORT, stop=None, dev=None):
    dev = dev or LAMP
    crypto = Crypto(bytes.fromhex(dev["token"]))
    did_bytes = dev["did"].to_bytes(4, "big")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(0.5)
    start = time.time()
    while not (stop and stop.is_set()):
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        stamp = int(time.time() - start) + 1000
        if data == HELLO:
            hdr = struct.pack(">HHI4sI", MAGIC, 32, 0, did_bytes, stamp)
            # a device that is not set up yet answers with its token instead of ff..ff
            sock.sendto(hdr + (crypto.token if dev.get("share_token") else b"\xff" * 16), addr)
            continue
        try:
            req = json.loads(crypto.decrypt(data[32:]).rstrip(b"\x00"))
        except ValueError:
            continue  # wrong token: real devices stay silent too
        res = handle(req, dev)
        if isinstance(res, dict) and "__error" in res:
            reply = {"id": req["id"], "error": res["__error"]}
        else:
            reply = {"id": req["id"], "result": res}
        enc = crypto.encrypt(json.dumps(reply).encode())
        hdr = struct.pack(">HHI4sI", MAGIC, 32 + len(enc), 0, did_bytes, stamp)
        sock.sendto(hdr + _md5(hdr + crypto.token + enc) + enc, addr)
    sock.close()


def start_background(host="127.0.0.1", dev=None, stop=None):
    stop = stop or threading.Event()
    t = threading.Thread(target=serve, args=(host, PORT, stop, dev), daemon=True)
    t.start()
    time.sleep(0.2)
    return stop


if __name__ == "__main__":
    print(f"Fake device listening on UDP {PORT}. Token: {TOKEN}")
    serve()
