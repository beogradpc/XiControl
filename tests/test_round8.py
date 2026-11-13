"""Tests for: Zigbee plugs controlled through their hub over the LAN, one connection choice for all devices,
changing the connection of several devices at once, and the sign on a tile that tells local network from
Mi cloud. The hub is simulated - nothing here talks to a real device or account."""
import atexit
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests import fake_device as fd  # noqa: E402
from xc import gateway  # noqa: E402
from xc.cloud import CloudError  # noqa: E402
from xc.device import LOCAL_ONLY_TEXT, ControlError, DeviceController  # noqa: E402
from xc.miio import MiioDevice, MiioError, MiioUnsupported  # noqa: E402

app = QApplication.instance() or QApplication([])


def free_windows():
    for w in QApplication.topLevelWidgets():
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


atexit.register(free_windows)

HUB_IP = "127.0.0.31"
SID, SID_OFF = "lumi.158d0001aaaaaa", "lumi.158d0001bbbbbb"
HUB = {"did": "900", "name": "Hall hub", "model": "lumi.gateway.mieu01", "ip": HUB_IP, "token": "ab" * 16}
PLUG = {"did": SID, "name": "Seedlings", "model": "lumi.plug.mmeu01", "parent_id": "900"}
PLUG_OFF = {"did": SID_OFF, "name": "Unplugged", "model": "lumi.plug.mmeu01", "parent_id": "900"}
MOTION = {"did": "lumi.158d0001cccccc", "name": "Motion", "model": "lumi.sensor_motion.v2", "parent_id": "900"}


class FakeCloud:
    """What DeviceController needs from the Mi cloud, with a log of what was asked."""
    logged_in = True

    def __init__(self):
        self.calls = []
        self.fail = False

    def _log(self, *call):
        self.calls.append(call)
        if self.fail:
            raise CloudError("cloud down")

    def miot_get(self, did, pairs):
        self._log("get", did, list(pairs))
        return [{"did": did, "siid": s, "piid": p, "code": 0, "value": f"cloud {s}.{p}"} for s, p in pairs]

    def miot_set(self, did, siid, piid, value):
        self._log("set", did, siid, piid, value)
        return [{"code": 0}]

    def miot_action(self, did, siid, aiid, ins, timeout=20):
        self._log("action", did, siid, aiid)
        return {"code": 0}

    def rpc(self, did, method, params=None, timeout=20):
        self._log("rpc", did, method)
        return ["ok"]


class Home:
    """A hub with its Zigbee devices: the controllers, wired together the way the main window does it."""

    def __init__(self, cloud=None, **changes):
        self.cloud = cloud
        self.infos = {d["did"]: dict(d, **changes.get(d["name"], {})) for d in (HUB, PLUG, PLUG_OFF, MOTION)}
        self.ctls = {}

    def ctl(self, did):
        if did not in self.ctls:
            self.ctls[did] = DeviceController(self.infos[did], self.cloud, self._hub)
        return self.ctls[did]

    def _hub(self, info):
        parent = self.infos.get(info.get("parent_id") or "")
        return self.ctl(parent["did"]) if parent else None


def with_hub(test):
    """Runs the test with a simulated hub listening on 127.0.0.31; the test gets the hub's plugs."""
    def run():
        children = {SID: {"neutral_0": "off", "load_power": 0.0}, SID_OFF: {"neutral_0": "unknown"}}
        stop = fd.start_background(HUB_IP, dev=fd.make_hub(children), stop=threading.Event())
        saved = DeviceController.mode_for_all, DeviceController.local_networks
        DeviceController.mode_for_all = DeviceController.local_networks = None
        try:
            test(children)
        finally:
            DeviceController.mode_for_all, DeviceController.local_networks = saved
            stop.set()
            time.sleep(0.6)   # the simulated hub lets go of its port
    run.__name__ = test.__name__
    return run


# -- 1: the protocol of the hub ------------------------------------------------------------------------------
@with_hub
def test_a_plug_is_read_and_switched_through_its_hub(children):
    dev = MiioDevice(HUB_IP, HUB["token"], timeout=2, retries=1)
    prof = gateway.profile("lumi.plug.mmeu01")
    assert gateway.read(dev, SID, prof, [(2, 1)]) == [{"did": SID, "siid": 2, "piid": 1, "code": 0, "value": False}]
    assert gateway.write(dev, SID, prof, 2, 1, True) == [{"did": SID, "siid": 2, "piid": 1, "code": 0}]
    assert children[SID]["neutral_0"] == "on"
    # what the hub doesn't know is left out; asking only for that never reaches the hub
    assert [x["piid"] for x in gateway.read(dev, SID, prof, [(1, 4), (2, 1)])] == [1]
    for call in (lambda: gateway.read(dev, SID, prof, [(1, 4)]), lambda: gateway.write(dev, SID, prof, 3, 1, True)):
        try:
            call()
            raise AssertionError("expected MiioUnsupported")
        except MiioUnsupported:
            pass
    # a plug the hub has lost: an error, never a made-up "off"
    try:
        gateway.read(dev, SID_OFF, prof, [(2, 1)])
        raise AssertionError("expected an error for a device the hub has no contact with")
    except MiioError as exc:
        assert "no contact" in str(exc) and not isinstance(exc, MiioUnsupported)
    assert gateway.profile("lumi.sensor_motion.v2") is None and gateway.profile("") is None


# -- 2: the controller picks the hub -------------------------------------------------------------------------
@with_hub
def test_controller_uses_the_hub_and_keeps_the_cloud_as_backup(children):
    cloud = FakeCloud()
    home = Home(cloud)
    plug, hub = home.ctl(SID), home.ctl("900")
    assert plug.hub() is hub and plug.local_capable() and plug.local_possible() and plug.route() == "local"
    assert plug.get_props([(2, 1)]) == {(2, 1): False} and plug.last_transport == "local"
    assert cloud.calls == []                                    # the tile's values never left the house
    assert plug.set_prop(2, 1, True) == 0 and children[SID]["neutral_0"] == "on" and cloud.calls == []
    assert plug.get_props([(2, 1)]) == {(2, 1): True}
    # the device's page also asks for things the hub doesn't have: those are added from the cloud
    values = plug.get_props([(1, 4), (2, 1)])
    assert values == {(2, 1): True, (1, 4): "cloud 1.4"} and plug.last_transport == "local"
    assert cloud.calls == [("get", SID, [(1, 4)])]
    cloud.fail = True                                           # ... and are not missed when the cloud is down
    assert plug.get_props([(1, 4), (2, 1)]) == {(2, 1): True}
    cloud.fail = False
    # what only the cloud can do goes there without marking the LAN as failed
    del cloud.calls[:]
    plug.action(2, 1)
    plug.raw("get_prop", ["power"])
    assert [c[0] for c in cloud.calls] == ["action", "rpc"] and plug._local_fails == 0 and plug.route() == "local"
    # a plug the hub has lost: the cloud answers instead, and goes first for a while
    lost = home.ctl(SID_OFF)
    assert lost.get_props([(2, 1)]) == {(2, 1): "cloud 2.1"} and lost.last_transport == "cloud"
    assert lost._local_fails == 1 and lost.route() == "cloud" and lost.prefers_cloud()
    lost.forget_route()
    assert lost.route() == "local"
    # other Zigbee devices of the same hub stay with the cloud
    motion = home.ctl(MOTION["did"])
    assert motion.hub() is None and not motion.local_capable() and motion.route() == "cloud" and motion.cloud_only()
    assert hub.hub() is None and hub.route() == "local"         # the hub itself is an ordinary Wi-Fi device


@with_hub
def test_plug_through_hub_without_the_cloud(children):
    home = Home(None)
    plug, lost = home.ctl(SID), home.ctl(SID_OFF)
    assert plug.route() == "local" and not plug.cloud_possible() and plug.unreachable_reason() is None
    assert plug.get_props([(2, 1)]) == {(2, 1): False}
    assert plug.get_props([(1, 4), (2, 1)]) == {(2, 1): False}   # no cloud to ask for the rest
    plug.set_prop(2, 1, True)
    assert children[SID]["neutral_0"] == "on"
    for call in (lambda: lost.get_props([(2, 1)]), lambda: plug.action(2, 1)):
        try:
            call()
            raise AssertionError("expected ControlError")
        except ControlError as exc:
            assert "LAN:" in str(exc)
    # no hub in the list, a hub of another kind, or a hub without its key: no local way
    orphan = DeviceController(dict(PLUG), None, lambda info: None)
    assert orphan.hub() is None and not orphan.local_possible() and orphan.needs_sign_in()
    assert DeviceController(dict(PLUG), None).hub() is None
    other = Home(None, **{"Hall hub": {"model": "lumi.gateway.mgl001"}})
    assert other.ctl(SID).hub() is None
    keyless = Home(None, **{"Hall hub": {"token": ""}})
    assert keyless.ctl(SID).hub() is not None and not keyless.ctl(SID).local_possible()
    keyless.infos[SID]["mode"] = "local"
    assert "hub" in keyless.ctl(SID).unreachable_reason()


@with_hub
def test_a_plug_the_hub_has_lost_is_offline_at_once(children):
    """The cloud still answers for it, with the values it remembers - that must not look like "online"."""
    from gui.hub import Hub
    from xc.spec import MiotSpec

    class Cfg:
        data = {"refresh_seconds": 30}

        def __init__(self, devices):
            self.devices = devices

        def device(self, did):
            return next((d for d in self.devices if str(d["did"]) == str(did)), None)

    class Specs:
        def get(self, model):
            return MiotSpec({"type": "urn:miot-spec-v2:device:outlet:0000:lumi:1", "services": [
                {"iid": 2, "type": "urn:miot-spec-v2:service:switch:0000:lumi:1", "description": "Switch",
                 "properties": [{"iid": 1, "type": "urn:miot-spec-v2:property:on:0000:lumi:1", "description": "On",
                                 "format": "bool", "access": ["read", "write", "notify"]}]}]})

    def pump(done, seconds=5.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end and not done():
            app.processEvents()
            time.sleep(0.01)
        return done()

    cloud = FakeCloud()
    cloud.miot_get = lambda did, pairs: [{"did": did, "siid": s, "piid": p, "code": 0, "value": True} for s, p in pairs]
    home = Home(cloud)
    hub = Hub(Cfg(list(home.infos.values())), Specs(), lambda info: home.ctl(info["did"]))
    for did in (SID, SID_OFF):
        hub.ensure_summary(did)
    assert pump(lambda: hub.state(SID).online is not None and hub.state(SID_OFF).online is not None)
    good, lost = hub.state(SID), hub.state(SID_OFF)
    assert good.online is True and good.transport == "local" and hub.is_on(SID) is False
    assert lost.online is False and "hub has no contact" in lost.error and home.ctl(SID_OFF).lost_by_hub
    # the account's own list has the last word once it is there
    hub.cloud_online[SID_OFF] = True
    lost.next_poll = 0.0
    hub.poll(SID_OFF, force=True)
    assert pump(lambda: lost.online is True) and lost.transport == "cloud"
    # and the plug is back on the LAN as soon as the hub hears it again
    children[SID_OFF]["neutral_0"] = "on"
    home.ctl(SID_OFF).forget_route()
    hub.poll(SID_OFF, force=True)
    assert pump(lambda: lost.transport == "local") and hub.is_on(SID_OFF) is True
    assert not home.ctl(SID_OFF).lost_by_hub
    hub.timer.stop()


# -- 3: one choice for all devices ---------------------------------------------------------------------------
@with_hub
def test_connection_for_all_devices_overrides_each_device(children):
    cloud = FakeCloud()
    home = Home(cloud, Seedlings={"mode": "cloud"})
    plug, motion, hub = home.ctl(SID), home.ctl(MOTION["did"]), home.ctl("900")
    wifi = DeviceController({"did": "7", "ip": "127.0.0.9", "token": "cd" * 16, "mode": "local"}, cloud)
    manual = DeviceController({"did": "manual-1", "ip": "127.0.0.9", "token": "cd" * 16}, cloud)
    assert (plug.mode, plug.route(), wifi.route(), hub.route()) == ("cloud", "cloud", "local", "local")
    try:
        DeviceController.mode_for_all = "local"
        assert (plug.mode, plug.route(), wifi.route(), hub.route()) == ("local", "local", "local", "local")
        assert plug.get_props([(1, 4), (2, 1)]) == {(2, 1): False} and cloud.calls == []   # nothing leaves the LAN
        assert motion.route() == "" and motion.unreachable_reason() == LOCAL_ONLY_TEXT
        assert not motion.needs_sign_in()                       # signing in wouldn't help
        DeviceController.mode_for_all = "cloud"
        assert (plug.route(), wifi.route(), hub.route(), motion.route()) == ("cloud",) * 4
        assert manual.route() == "" and "IP address" in manual.unreachable_reason()
        DeviceController.mode_for_all = "auto"
        assert (plug.mode, plug.route(), wifi.route(), motion.route()) == ("auto", "local", "local", "cloud")
        DeviceController.mode_for_all = "nonsense"              # a damaged setting counts as automatic
        assert plug.mode == "auto"
    finally:
        DeviceController.mode_for_all = None
    assert (plug.mode, wifi.mode) == ("cloud", "local")         # each device's own choice was never touched


# -- 4: the sign on a tile -----------------------------------------------------------------------------------
def _coloured(widget, name) -> int:
    """How many pixels of the widget have (nearly) the theme colour `name`."""
    from gui import theme
    img = widget.grab().toImage()
    want = QColor(theme.c(name))
    n = 0
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            if abs(c.red() - want.red()) + abs(c.green() - want.green()) + abs(c.blue() - want.blue()) < 40:
                n += 1
    return n


def test_tile_shows_local_network_or_mi_cloud():
    from gui import theme
    from gui.home import TILE_SIZES, DeviceTile
    from gui.hub import DeviceState
    theme.apply(app, "light")
    st = DeviceState()
    st.online = True
    info = {"did": "1", "name": "Living room lamp with a very long name indeed", "model": "yeelink.light.x"}
    for size in TILE_SIZES:
        t = DeviceTile(info, size)
        t.show()
        t.update_state(st, True, True)
        assert t.link.isHidden() and t.link.kind == ""            # not known yet: no sign at all
        plain = t.name.text()
        t.update_state(st, True, True, ("local", "Local network - directly"))
        app.processEvents()
        assert not t.link.isHidden() and t.link.kind == "local" and t.link.toolTip().startswith("Local network")
        assert _coloured(t.link, "local") > 25 and _coloured(t.link, "cloud") == 0
        # the sign sits in front of the name, on its line, and the name gives up the room for it
        assert t.link.x() < t.name.x() and abs(t.link.geometry().center().y() - t.name.geometry().center().y()) <= 3
        assert len(t.name.text()) < len(plain) and t.name.text().endswith("…")
        assert t.name.geometry().right() <= t.width() and t.link.width() <= 16
        t.update_state(st, True, True, ("cloud", "Mi cloud - through Xiaomi's servers"))
        app.processEvents()
        assert t.link.kind == "cloud" and _coloured(t.link, "cloud") > 25 and _coloured(t.link, "local") == 0
        t.update_state(st, True, True, ("nonsense", "x"))
        assert t.link.isHidden() and t.link.toolTip() == ""
        t.close()
    for mode in ("light", "dark"):                                # the two signs never share a colour
        theme.apply(app, mode)
        assert len({theme.c("local"), theme.c("cloud"), theme.c("accent"), theme.c("bad")}) == 4
    theme.apply(app, "light")


# -- 5: dialogs ----------------------------------------------------------------------------------------------
def test_manage_dialog_changes_the_connection_of_several_devices():
    from PySide6.QtCore import Qt
    from gui.dialogs import ManageDialog
    devices = [{"did": "1", "name": "A", "model": "x", "mode": "auto"}, {"did": "2", "name": "B", "model": "x"},
               {"did": "3", "name": "C", "model": "x", "mode": "cloud"}]
    asked = []

    def set_mode(dids, mode):
        asked.append((sorted(dids), mode))
        for d in devices:
            if d["did"] in dids:
                d["mode"] = mode

    m = ManageDialog(devices, lambda did: "Online", mode_for=lambda did: next(
        d for d in devices if d["did"] == did).get("mode") or "auto", set_mode=set_mode)
    m.show()
    col = m.table.columnCount() - 1
    assert m.table.horizontalHeaderItem(col).text() == "Connection"
    rows = {m.table.item(r, 0).data(Qt.UserRole): r for r in range(m.table.rowCount())}
    assert [m.table.item(rows[d], col).text() for d in "123"] == ["Automatic", "Automatic", "Mi cloud only"]
    assert not m.mode_btn.isHidden() and not m.mode_btn.isEnabled()          # nothing ticked yet
    for did in "12":
        m.table.item(rows[did], 0).setCheckState(Qt.Checked)
    assert m.mode_btn.isEnabled()
    local = next(a for a in m.mode_menu.actions() if a.text() == "Local network only")
    local.trigger()
    assert asked == [(["1", "2"], "local")]
    assert [m.table.item(rows[d], col).text() for d in "123"] == ["Local network only"] * 2 + ["Mi cloud only"]
    assert m.selected() == ["1", "2"] or sorted(m.selected()) == ["1", "2"]   # still ticked, nothing removed
    m.close()
    plain = ManageDialog(devices, lambda did: "Online")                       # as before, without the column
    assert plain.table.columnCount() == 6 and plain.mode_btn.isHidden()
    plain.close()


def test_settings_dialog_offers_one_connection_for_all():
    from gui.dialogs import DeviceDialog, SettingsDialog

    class Cfg:
        data = {"connection": "local"}

    dlg = SettingsDialog(Cfg, None, os.path.dirname(__file__))
    assert [dlg.connection.itemData(i) for i in range(dlg.connection.count())] == ["", "auto", "local", "cloud"]
    assert dlg.connection.currentData() == "local" and "can't be reached" in dlg.connection_note.text()
    dlg.connection.setCurrentIndex(0)
    assert dlg.connection.currentData() == "" and "green house" in dlg.connection_note.text()
    dlg.close()
    Cfg.data = {}
    dlg = SettingsDialog(Cfg, None, os.path.dirname(__file__))
    assert dlg.connection.currentData() == ""                    # nothing chosen: each device as set
    dlg.close()
    # the device's own dialog says so while its choice is overridden
    from PySide6.QtWidgets import QLabel
    saved = DeviceController.mode_for_all
    try:
        DeviceController.mode_for_all = "cloud"
        d = DeviceDialog({"did": "1", "name": "A", "mode": "local"})
        assert any("Mi cloud only" in x.text() and "for all" in x.text() for x in d.findChildren(QLabel))
        assert d.result_info()["mode"] == "local"                 # ... and keeps that choice
        d.close()
        DeviceController.mode_for_all = None
        d = DeviceDialog({"did": "1", "name": "A", "mode": "local"})
        assert not any("for all" in x.text() for x in d.findChildren(QLabel))
        d.close()
    finally:
        DeviceController.mode_for_all = saved


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
