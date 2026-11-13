"""Zigbee devices behind an older Xiaomi gateway, controlled through that gateway over the LAN.

The gateway answers the miIO protocol with its own token; a command for one of its Zigbee devices names
that device ("sid" = the device's did, e.g. lumi.158d0001234567). Only what was seen working on real
hardware is listed here - everything else about these devices keeps going through the Mi cloud:

    Mi Control Hub DGNWG05LM (lumi.gateway.mieu01, firmware 3.5.9)
        Mi Smart Plug Zigbee ZNCZ04LM (lumi.plug.mmeu01): on/off

Battery devices (motion sensors, buttons, door sensors) can't be asked for their state: they only send
events, and the gateway passes those to the Mi cloud alone."""
from __future__ import annotations

from .miio import MiioError, MiioUnsupported

# gateways that pass commands on to their Zigbee devices
GATEWAYS = ("lumi.gateway.mieu01",)

# model -> {"props": {(siid, piid): gateway property}, "set": {(siid, piid): (method, gateway property)}}
# (siid, piid) is the property in the device's MIoT description, so that the rest of the app sees the
# same values whichever way they came.
_PLUG = {"props": {(2, 1): "neutral_0"}, "set": {(2, 1): ("toggle_plug", "neutral_0")}}
CHILDREN = {"lumi.plug.mmeu01": _PLUG}

NO_CONTACT = "Its hub has no contact with this device right now"


class NoContact(MiioError):
    """The gateway answered, but it doesn't hear from this device (unplugged, or out of reach)."""
CLOUD_ONLY = "This can only be done through the Mi cloud"


def profile(model: str) -> dict | None:
    return CHILDREN.get((model or "").lower())


def _from_gateway(value):
    if value in ("on", "off"):
        return value == "on"
    return value


def read(dev, sid: str, prof: dict, pairs) -> list[dict]:
    """Reads the properties the gateway knows; the answer looks like a MIoT one. Properties the gateway
    doesn't have are left out. dev: the gateway's MiioDevice."""
    known = [p for p in pairs if tuple(p) in prof["props"]]
    if not known:
        raise MiioUnsupported(CLOUD_ONLY)
    result = dev.send("get_device_prop_exp", [[sid] + [prof["props"][tuple(p)] for p in known]])
    row = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else None
    if row is None or len(row) < len(known):
        raise MiioError(f"Unexpected answer from the hub: {result!r}"[:120])
    if all(v == "unknown" for v in row[:len(known)]):
        raise NoContact(NO_CONTACT)   # what the gateway says about a device that is unplugged or out of reach
    return [{"did": sid, "siid": s, "piid": p, "code": 0, "value": _from_gateway(v)}
            for (s, p), v in zip(known, row) if v != "unknown"]


def write(dev, sid: str, prof: dict, siid: int, piid: int, value) -> list[dict]:
    target = prof["set"].get((siid, piid))
    if target is None:
        raise MiioUnsupported(CLOUD_ONLY)
    method, name = target
    result = dev.send(method, [name, "on" if value else "off"], extra={"sid": sid})
    if result != ["ok"]:
        raise MiioError(f"The hub refused: {result!r}"[:120])
    return [{"did": sid, "siid": siid, "piid": piid, "code": 0}]
