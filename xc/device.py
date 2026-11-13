"""One controllable device: picks local (LAN) or cloud transport automatically."""
from __future__ import annotations

import ipaddress
import threading
import time

from . import gateway
from .cloud import CloudAuthError, CloudError, XiaomiCloud
from .miio import MiioDevice, MiioError, MiioUnsupported, parse_token


MIOT_CODES = {
    0: "OK", 1: "Accepted (running)", -4001: "Property not readable", -4002: "Property not writable",
    -4003: "Property/action not found", -4004: "Device error", -4005: "Action failed",
    -704030013: "Property not readable", -704030023: "Property not writable",
    -704040002: "Service not found", -704040003: "Property not found", -704040005: "Action not found",
    -704042011: "Device offline", -704220043: "Value out of range", -704010000: "Unauthorized",
    -704053036: "Device busy", -704002000: "Device timeout", -704220035: "Wrong parameters",
    -706010002: "The device did not answer (it may be asleep)",
}
OFFLINE_CODE = -704042011
LOCAL_RETRY_SECONDS = 300    # after the LAN failed, use the cloud first for this long ...
LOCAL_RETRY_MAX = 3600       # ... doubling after every further failure, up to an hour
MODES = {"auto": "Automatic", "local": "Local network only", "cloud": "Mi cloud only"}
LOCAL_ONLY_TEXT = ("This device only works through the Mi cloud, and its connection is set to "
                   "“Local network only”.")


def describe_code(code) -> str:
    return MIOT_CODES.get(code, f"code {code}")


def plain_values(ins) -> list:
    """Action inputs as the cloud wants them: [{'piid': 1, 'value': 5}] -> [5]."""
    return [x["value"] if isinstance(x, dict) and "value" in x else x for x in ins or []]


class ControlError(Exception):
    pass


class NeedsSignIn(ControlError):
    """The device can only be reached through the Mi cloud and nobody is signed in."""


class DeviceOffline(ControlError):
    """The Mi cloud says the device is not connected."""


class DeviceController:
    # The PC's directly connected IPv4 networks (ipaddress.ip_network objects), set by the app.
    # None = unknown: every private address is then worth a try.
    local_networks: list | None = None
    # "Connection for all devices" of the settings: auto / local / cloud, or None = as set for each device.
    mode_for_all: str | None = None

    def __init__(self, info: dict, cloud: XiaomiCloud | None, gateway_for=None):
        """gateway_for(info) -> the DeviceController of the hub a Zigbee device hangs on, or None."""
        self.info = info
        self.cloud = cloud
        self._gateway_for = gateway_for
        self.lost_by_hub = False     # its gateway said a moment ago that it doesn't hear from this device
        self.last_transport = ""
        self._local: MiioDevice | None = None
        self._local_key = None
        self._local_retry_at = 0.0
        self._local_fails = 0
        self._lock = threading.Lock()

    # -- properties --------------------------------------------------------------
    @property
    def did(self) -> str:
        return str(self.info.get("did"))

    @property
    def model(self) -> str:
        return self.info.get("model") or ""

    def on_local_network(self) -> bool:
        """Is the device's address on a network this PC is directly connected to?
        Devices at another location keep their home address (e.g. 192.168.31.x) or show a public one;
        trying those over the LAN only wastes time."""
        try:
            addr = ipaddress.ip_address(self.info.get("ip") or "")
        except ValueError:
            return False
        if addr.version != 4:
            return False
        if addr.is_loopback:
            return True
        if not addr.is_private or addr.is_link_local:
            return False
        nets = DeviceController.local_networks
        if nets is None:
            return True
        return any(addr in n for n in nets)

    @property
    def mode(self) -> str:
        """auto / local / cloud: the choice for all devices if there is one, else this device's own."""
        mode = DeviceController.mode_for_all or self.info.get("mode") or "auto"
        return mode if mode in MODES else "auto"

    def hub(self) -> "DeviceController | None":
        """The gateway this Zigbee device is controlled through over the LAN - for the few kinds of
        devices and gateways where that works (see xc.gateway) - or None."""
        if not self._gateway_for or not self.did.startswith("lumi.") or gateway.profile(self.model) is None:
            return None
        hub = self._gateway_for(self.info)
        if hub is None or hub is self or hub.model not in gateway.GATEWAYS:
            return None
        return hub

    def _address_ok(self) -> bool:
        ip, token = self.info.get("ip"), self.info.get("token")
        if not ip or not token:
            return False
        try:
            parse_token(token)
        except MiioError:
            return False
        return True

    def local_capable(self) -> bool:
        """Is this a kind of device that can be reached over the LAN at all? (Bluetooth and most Zigbee
        devices are not: they have no address of their own.)"""
        if self.did.startswith(("blt.", "lumi.", "group.")):
            return self.hub() is not None
        return True

    def local_possible(self) -> bool:
        hub = self.hub()
        if hub is not None:   # through its gateway, with the gateway's address and key
            return hub._address_ok() and (self.mode == "local" or hub.info.get("mode") == "local"
                                          or hub.on_local_network())
        if self.did.startswith(("blt.", "lumi.", "group.")) or not self._address_ok():
            return False
        # "Local network only" is the user's explicit choice (e.g. a network reached through a VPN)
        return self.mode == "local" or self.on_local_network()

    def cloud_possible(self) -> bool:
        return bool(self.cloud and self.cloud.logged_in) and not self.did.startswith("manual-")

    def cloud_only(self) -> bool:
        """Reached through the Mi cloud and nothing else."""
        return self._order() == ["cloud"]

    def prefers_cloud(self) -> bool:
        """Is the Mi cloud the first choice right now - because it is the only way, or because the LAN
        didn't answer a moment ago? Such a device can be read together with the others in one request."""
        order = self._order()
        return bool(order) and order[0] == "cloud"

    def elsewhere(self) -> bool:
        """Has an address and key, but on a network this PC isn't part of."""
        return bool(self.info.get("ip") and self.info.get("token")) and not self.on_local_network() \
            and not self.did.startswith(("blt.", "lumi.", "group."))

    def route(self) -> str:
        """The way this device is reached first right now: "local", "cloud" or "" (not at all)."""
        order = self._order()
        return order[0] if order else ""

    def unreachable_reason(self) -> str | None:
        """Why this device can't be contacted at all with the current settings, or None."""
        if self._order():
            return None
        if self.mode == "local":
            if not self.local_capable():
                return LOCAL_ONLY_TEXT
            if self.hub() is not None:
                return "Check the IP address and token of this device's hub (⋯ → Rename and connection)."
            return "Enter this device's IP address and token (⋯ → Rename and connection)."
        if self.did.startswith("manual-"):
            return "Check this device's IP address and token (⋯ → Rename and connection)."
        if self.elsewhere():
            return "This device is on another network - sign in to your Mi account to control it from here"
        return "Sign in to your Mi account to control this device"

    def needs_sign_in(self) -> bool:
        return (not self._order() and self.mode != "local"
                and not self.did.startswith("manual-"))

    def _local_dev(self) -> MiioDevice:
        hub = self.hub()
        if hub is not None:
            return hub._local_dev()   # one connection per gateway, shared by its devices
        # with the cloud as a fallback, give up on the LAN sooner
        retries = 1 if self.cloud_possible() and self.mode == "auto" else 2
        key = (self.info.get("ip"), self.info.get("token"), retries)
        with self._lock:
            if self._local is None or self._local_key != key:
                self._local = MiioDevice(key[0], key[1], retries=retries)
                self._local_key = key
            return self._local

    def _order(self) -> list[str]:
        mode = self.mode
        local, cloud = self.local_possible(), self.cloud_possible()
        if mode == "local":
            return ["local"] if local else []
        if mode == "cloud":
            return ["cloud"] if cloud else []
        order = (["local"] if local else []) + (["cloud"] if cloud else [])
        if len(order) == 2 and time.monotonic() < self._local_retry_at:
            order.reverse()  # the LAN failed a moment ago - cloud first, the LAN is tried again later
        return order

    def forget_route(self):
        """The connection setting changed: the LAN is tried again right away."""
        self._local_retry_at, self._local_fails = 0.0, 0

    def _local_failed(self):
        """The LAN didn't answer although the cloud is there too: the cloud goes first for a while."""
        self._local_fails += 1
        wait = min(LOCAL_RETRY_MAX, LOCAL_RETRY_SECONDS * 2 ** (self._local_fails - 1))
        self._local_retry_at = time.monotonic() + wait

    def _run(self, local_fn, cloud_fn):
        order = self._order()
        if not order:
            reason = self.unreachable_reason()
            raise NeedsSignIn(reason) if self.needs_sign_in() else ControlError(reason)
        errors = []
        for t in order:
            try:
                if t == "local":
                    result = local_fn(self._local_dev())
                    self._local_retry_at, self._local_fails = 0.0, 0
                else:
                    result = cloud_fn(self.cloud)
                self.last_transport = t
                return result
            except CloudAuthError:
                raise
            except (MiioError, CloudError) as exc:
                if t == "local" and "cloud" in order and not isinstance(exc, MiioUnsupported):
                    self._local_failed()
                errors.append(f"{'LAN' if t == 'local' else 'Cloud'}: {exc}")
        raise ControlError("\n".join(errors))

    # -- MIoT ---------------------------------------------------------------------
    @staticmethod
    def parse_props(items) -> dict[tuple[int, int], object]:
        """Results of a property read -> {(siid, piid): value}. Raises DeviceOffline if the cloud says so."""
        items = [x for x in items or [] if isinstance(x, dict)]
        if items and all(x.get("code") == OFFLINE_CODE for x in items):
            raise DeviceOffline("The Mi cloud reports this device as offline - check its power and Wi-Fi.")
        values = {}
        for item in items:
            if item.get("code", 0) == 0 and "value" in item:
                values[(item.get("siid"), item.get("piid"))] = item["value"]
        return values

    def get_props(self, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], object]:
        if not pairs:
            return {}
        local = self._hub_get(pairs) if self.hub() is not None else (lambda d: d.miot_get(self.did, pairs))
        result = self._run(local, lambda c: c.miot_get(self.did, pairs))
        return self.parse_props(result)

    def _hub_get(self, pairs):
        """Reading through the gateway: what the gateway knows comes from it; the rest of what was asked
        for (e.g. the firmware version on the device's page) is added from the Mi cloud if that is there."""
        prof = gateway.profile(self.model)

        def read(dev):
            try:
                items = gateway.read(dev, self.did, prof, pairs)
            except gateway.NoContact:
                self.lost_by_hub = True
                raise
            self.lost_by_hub = False
            rest = [p for p in pairs if tuple(p) not in prof["props"]]
            if rest and self.mode == "auto" and self.cloud_possible():
                try:
                    items = items + [x for x in self.cloud.miot_get(self.did, rest) or [] if isinstance(x, dict)]
                except CloudError:
                    pass   # the main values are there; the extras show up next time
            return items
        return read

    def set_prop(self, siid: int, piid: int, value):
        if self.hub() is not None:
            prof = gateway.profile(self.model)

            def local(dev):
                return gateway.write(dev, self.did, prof, siid, piid, value)
        else:
            def local(dev):
                return dev.miot_set(self.did, siid, piid, value)
        result = self._run(local, lambda c: c.miot_set(self.did, siid, piid, value))
        code = (result[0] if result else {}).get("code", 0) if isinstance(result, list) else 0
        if code not in (0, 1):
            raise ControlError(f"Device refused: {describe_code(code)}")
        return code

    def action(self, siid: int, aiid: int, ins: list | None = None):
        """ins: [{'piid': n, 'value': v}] (the local protocol's form; converted for the cloud)."""
        ins = ins or []
        result = self._run(self._cloud_only_here() or (lambda d: d.miot_action(self.did, siid, aiid, ins)),
                           lambda c: c.miot_action(self.did, siid, aiid, plain_values(ins)))
        code = result.get("code", 0) if isinstance(result, dict) else 0
        if code not in (0, 1):
            raise ControlError(f"Device refused: {describe_code(code)}")
        return result

    def cloud_action(self, siid: int, aiid: int, ins: list | None = None, timeout: int = 20) -> dict:
        """An action that only the Mi cloud can answer (e.g. asking for a camera stream address)."""
        if not self.cloud_possible():
            raise NeedsSignIn("Sign in to your Mi account first")
        result = self.cloud.miot_action(self.did, siid, aiid, plain_values(ins), timeout=timeout)
        code = result.get("code", 0) if isinstance(result, dict) else 0
        if code not in (0, 1):
            raise ControlError(f"Device refused: {describe_code(code)}")
        return result

    # -- legacy / raw ---------------------------------------------------------------
    def raw(self, method: str, params=None):
        return self._run(self._cloud_only_here() or (lambda d: d.send(method, params)),
                         lambda c: c.rpc(self.did, method, params))

    def _cloud_only_here(self):
        """For a device behind a gateway: the "local" way of something only the Mi cloud can do."""
        if self.hub() is None:
            return None

        def refuse(_dev):
            raise MiioUnsupported(gateway.CLOUD_ONLY)
        return refuse

    def info_local(self) -> dict:
        """Asks the device itself who it is - only possible over the LAN."""
        try:
            result = self._local_dev().send("miIO.info", [])
        except MiioError:
            if "cloud" in self._order():
                self._local_failed()
            raise
        self._local_retry_at, self._local_fails = 0.0, 0
        return result
