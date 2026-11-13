"""Tests for: readable action buttons, the main actions of a vacuum, firmware, rooms, Mi Home scenes, XiControl
actions, the right-click menu of a tile, refreshing, and deleting several devices from the Mi account."""
import atexit
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from tests import fake_home  # noqa: E402
from tests.test_fixes import TOKEN, _session  # noqa: E402
from xc import actions  # noqa: E402
from xc.cloud import XiaomiCloud  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402
from xc.summary import LegacySummary, Summary  # noqa: E402

app = QApplication.instance() or QApplication([])


def free_windows():
    for w in QApplication.topLevelWidgets():
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


atexit.register(free_windows)


def pump(done, seconds=4.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return done()


def _urn(kind, name):
    return f"urn:miot-spec-v2:{kind}:{name}:0000:x:1"


def _act(iid, name, ins=()):
    return {"iid": iid, "type": _urn("action", name), "description": name.replace("-", " ").title(),
            "in": list(ins), "out": []}


# a scale like the Body Composition Scale S400: many actions, most of them want typed-in text (JSON)
SCALE_SPEC = {"type": _urn("device", "body-scale"), "services": [
    {"iid": 2, "type": _urn("service", "body-scale"), "description": "Scale", "properties": [
        {"iid": 1, "type": _urn("property", "user-info"), "description": "User Info", "format": "string",
         "access": ["write"]},
        {"iid": 2, "type": _urn("property", "count"), "description": "Count", "format": "uint8", "access": ["write"],
         "value-range": [0, 10, 1]}],
     "actions": [_act(1, "wakeup-device")] + [_act(i, n, [1]) for i, n in enumerate(
         ("update-user", "delete-user", "reset-user", "update-base-weight", "get-offline-record", "share-user"), 2)]
     + [_act(10 + i, f"long-action-name-number-{i}") for i in range(14)]},   # 5 of them are main buttons
]}

VACUUM_SPEC = {"type": _urn("device", "vacuum"), "services": [
    {"iid": 2, "type": _urn("service", "vacuum"), "description": "Robot Cleaner", "properties": [
        {"iid": 2, "type": _urn("property", "status"), "description": "Status", "format": "uint8",
         "access": ["read", "notify"], "value-list": [{"value": 1, "description": "Idle"},
                                                     {"value": 4, "description": "Sweeping"}]},
        {"iid": 9, "type": _urn("property", "mode"), "description": "Mode", "format": "uint8",
         "access": ["read", "write"], "value-list": [{"value": 1, "description": "Silent"},
                                                    {"value": 2, "description": "Basic"}]}],
     "actions": [_act(1, "start-sweep"), _act(2, "stop-sweeping"), _act(3, "stop-and-gocharge"),
                 _act(4, "start-only-sweep"), _act(5, "start-mop"), _act(6, "start-sweep-mop"),
                 _act(7, "pause-sweeping"),
                 _act(8, "continue-sweep"), _act(9, "start-custom-sweep"), _act(17, "start-build-map"),
                 _act(18, "start-dust-arrest"), _act(19, "start-mop-wash"), _act(16, "start-vacuum-room-sweep", [9])]},
    {"iid": 6, "type": _urn("service", "identify"), "description": "Identify", "properties": [],
     "actions": [_act(1, "identify")]},
]}


# -- 2: action buttons stay readable ----------------------------------------------------------------------
def test_action_buttons_wrap_and_text_actions_are_technical():
    from gui.settings_panel import SettingsPanel
    panel = SettingsPanel(Summary(MiotSpec(SCALE_SPEC)))
    panel.resize(540, 900)
    panel.show()
    pump(lambda: False, 0.3)
    buttons = [b for b in panel.findChildren(QPushButton) if b.text().startswith("Long Action")]
    assert len(buttons) == 9
    for b in buttons:   # nothing squeezed: every text has its full room
        assert b.width() >= b.sizeHint().width(), (b.text(), b.width(), b.sizeHint().width())
        assert b.geometry().right() <= panel.width()
    assert len({b.y() for b in buttons}) > 1          # wrapped to more than one row
    # the ones that want typed-in JSON are under "Technical details" (hidden until "Show")
    tech = panel.tech_box
    texts = {b.text() for b in tech.findChildren(QPushButton)}
    assert "Delete User…" in texts and "Update User…" in texts
    actions_card = buttons[0].parentWidget()
    assert not any(b.text() == "Delete User…" for b in actions_card.findChildren(QPushButton))
    panel.close()


def test_main_actions_cover_every_role():
    s = Summary(MiotSpec(VACUUM_SPEC))
    names = [a.name for a in s.actions]
    assert len(names) == 6
    for wanted in ("start-sweep", "pause-sweeping", "stop-sweeping", "stop-and-gocharge", "identify"):
        assert wanted in names, names
    assert names.index("start-sweep") < names.index("pause-sweeping") < names.index("stop-and-gocharge")


# -- cloud requests ------------------------------------------------------------------------------------------------
def test_cloud_firmware_room_and_scene_requests():
    c = XiaomiCloud("de", _session())
    sent = []

    def api(path, data, **kw):
        sent.append((path, data))
        if path == "home/multi_checkversion":
            return {"result": {"list": [{"did": d, "curr": "1.0", "latest": "1.1" if d == "5" else "1.0",
                                         "isLatest": d != "5"} for d in data["dids"]]}}
        if path == "home/checkversion":
            return {"result": {"did": data["did"], "curr": "1.0", "latest": "1.1", "isLatest": False}}
        if path == "v2/homeroom/gethome":
            return {"result": {"homelist": [{"id": 11, "name": "Home", "uid": 99, "dids": ["1"], "roomlist": [
                {"id": 21, "name": "Kitchen", "dids": ["5", "6"]}]}]}}
        if path.endswith("GetSceneList"):
            return {"result": {"scene_info_list": [
                {"scene_id": 31, "name": "All off", "type": 0, "enable": True,
                 "scene_trigger": {"triggers": [{"src": "user", "key": "user.click"}]}},
                {"scene_id": 32, "name": "Too warm", "type": 0, "enable": True,
                 "scene_trigger": {"triggers": [{"src": "device", "key": "prop.3.1001"}]}}]}}
        return {"result": {}}
    c.api = api
    status = c.firmware_status([str(i) for i in range(150)])
    assert [len(d["dids"]) for p, d in sent if p == "home/multi_checkversion"] == [100, 50]
    assert len(status) == 150 and status["5"]["isLatest"] is False
    assert c.check_firmware("5", 3)["latest"] == "1.1" and sent[-1] == ("home/checkversion", {"did": "5", "pid": 3})
    c.start_firmware_update("5", 0)
    assert sent[-1] == ("home/devupgrade", {"did": "5", "pid": 0, "platform": "android"})
    homes = c.homes()
    assert homes[0]["owner"] == 99 and homes[0]["rooms"][0] == {"id": "21", "name": "Kitchen", "dids": ["5", "6"]}
    c.move_to_room("5", "21", 99)
    assert sent[-1] == ("homeroom/bind_device_to_room", {"id": "21", "did": "5", "home_owner": 99})
    scenes = c.scenes()
    assert [(s["name"], s["manual"]) for s in scenes] == [("All off", True), ("Too warm", False)]
    assert sent[-1][1] == {"home_id": "11", "owner_uid": 99, "get_type": 2, "app_version": 12}
    c.run_scene(scenes[0])
    assert sent[-1] == ("appgateway/miot/appsceneservice/AppSceneService/NewRunScene",
                        {"scene_id": "31", "scene_type": 0, "home_id": "11", "owner_uid": "99"})


# -- 6: actions made in XiControl --------------------------------------------------------------------------------
def test_actions_engine():
    summ = Summary(MiotSpec(fake_home.PURIFIER_SPEC))
    cmds = actions.device_commands(summ)
    labels = [c["label"] for c in cmds]
    assert labels[:2] == ["Turn on", "Turn off"] and any(lbl.startswith("Mode:") for lbl in labels)
    assert not any("Reset" in lbl for lbl in labels)                      # no maintenance in one-click actions
    vac = actions.device_commands(Summary(MiotSpec(VACUUM_SPEC)))
    vlabels = [c["label"] for c in vac]
    assert "Start Sweep" in vlabels and "Start Build Map" not in vlabels  # no map building by accident
    assert not any(c.get("aiid") == 16 for c in vac)                      # needs room ids: not one-click
    from xc.catalog import legacy_profile
    legacy = actions.device_commands(LegacySummary(legacy_profile("yeelink.light.color3")))
    assert [c["label"] for c in legacy] == ["Turn on", "Turn off"] and legacy[0]["kind"] == "raw"

    on = next(c for c in cmds if c["label"] == "Turn off")
    a = actions.new_action("Leaving")
    a["steps"] = [actions.step_from("1", "Purifier", on), actions.wait_step(3),
                  actions.step_from("2", "Lamp", legacy[1]), actions.step_from("3", "Gone", on)]
    assert a["steps"][0]["label"] == "Purifier: Turn off" and a["steps"][1]["seconds"] == 3

    calls, slept = [], []

    class Ctl:
        def __init__(self, did):
            self.did = did

        def set_prop(self, siid, piid, value):
            if self.did == "1":
                calls.append(("set", self.did, siid, piid, value))
                return 0
            raise RuntimeError("offline")

        def raw(self, method, params):
            calls.append(("raw", self.did, method))

    devices = {"1": {"did": "1"}, "2": {"did": "2"}}
    failed = actions.run_action(a, lambda info: Ctl(info["did"]), devices.get, sleep=slept.append)
    assert calls == [("set", "1", on["siid"], on["piid"], False), ("raw", "2", legacy[1]["method"])]
    assert slept == [3] and len(failed) == 1 and "no longer" in failed[0][1]
    assert actions.describe_result(a, failed).startswith("“Leaving”: 2 of 3 done - Gone")
    assert actions.describe_result(a, []) == "“Leaving” done ✓"


# -- dialogs ----------------------------------------------------------------------------------------------------
def test_room_and_rename_dialogs():
    from gui.dialogs import RenameDialog, RoomDialog
    d = RoomDialog("Lamp", "Kitchen", ["Kitchen", "Office"], [{"id": "21", "name": "Kitchen"},
                                                              {"id": "22", "name": "Living room"}])
    assert not d.ok.isEnabled()                                  # nothing changed yet
    d.room.setCurrentText("living ROOM")
    assert d.ok.isEnabled() and d.sync.isEnabled() and d.account_room() is None   # not ticked
    d.sync.setChecked(True)
    assert d.account_room() == {"id": "22", "name": "Living room"}
    d.room.setCurrentText("Garage")                              # not on the account: XiControl only
    assert not d.sync.isEnabled() and d.account_room() is None and "XiControl only" in d.note.text()
    offline = RoomDialog("Lamp", "", ["Office"], None)
    assert offline.sync.isHidden()
    r = RenameDialog("Lamp", can_sync=False)
    assert not r.ok.isEnabled() and r.sync.isHidden()
    r.name.setText("Desk lamp")
    assert r.ok.isEnabled() and r.new_name() == "Desk lamp" and not r.sync_to_account()


def test_manage_dialog_can_delete_from_the_account():
    from gui.dialogs import ManageDialog
    devices = [{"did": "1", "name": "A", "model": "x"}, {"did": "2", "name": "B", "model": "y"}]
    m = ManageDialog(devices, lambda did: "Online", firmware_for=lambda did: "1.0 → 1.1" if did == "2" else "1.0",
                     can_delete_account=True)
    assert m.table.item(1, 5).text() == "1.0 → 1.1"
    m.table.item(0, 0).setCheckState(m.table.item(0, 0).checkState().__class__.Checked)
    assert not m.delete_from_account and m.ok.text() == "Remove 1 from XiControl"
    m.account.setChecked(True)
    assert m.delete_from_account and "my Mi account" in m.ok.text()
    plain = ManageDialog(devices, lambda did: "Online")
    assert plain.account.isHidden() and not plain.delete_from_account


# -- 5: firmware ---------------------------------------------------------------------------------------------------
class _Main:
    """The parts of the main window the dialogs and menus use."""

    def __init__(self, devices, cloud=None):
        self.cfg = _Cfg(devices)
        self.cloud = cloud
        self.signed_in = cloud is not None
        self.firmware = {}
        self.changed = []
        self.hub = _Hub()

    def firmware_changed(self, did):
        self.changed.append(did)

    def on_account(self, info):
        return self.signed_in and not str(info.get("did")).startswith("manual-")


class _Cfg:
    def __init__(self, devices):
        self.data, self.devices = {"refresh_seconds": 30}, devices
        self.saved = 0

    def device(self, did):
        return next((d for d in self.devices if str(d["did"]) == str(did)), None)

    def save(self):
        self.saved += 1


class _Hub:
    def __init__(self):
        from gui.hub import DeviceState
        self.states = {}
        self.State = DeviceState

    def state(self, did):
        return self.states.setdefault(str(did), self.State())

    def has_power(self, did):
        st = self.state(did)
        return isinstance(st.summary, Summary) and st.summary.power is not None

    def is_on(self, did):
        st = self.state(did)
        return st.summary.is_on(st.values) if st.summary else None

    def ensure_summary(self, did):
        pass


def test_firmware_dialog_updates_and_shows_progress():
    import gui.firmware as fwmod
    from PySide6.QtWidgets import QMessageBox

    class Cloud:
        def __init__(self):
            self.started, self.answers = [], [
                {"curr": "2.2.8", "latest": "2.2.10", "isLatest": False, "description": "Fix known issues"},
                {"curr": "2.2.8", "latest": "2.2.10", "isLatest": False, "updating": True,
                 "ota_status": "downloading", "ota_progress": 40},
                {"curr": "2.2.10", "latest": "2.2.10", "isLatest": True, "updating": False, "ota_status": "idle"}]

        def device_record(self, did):
            return {"pid": 0}

        def check_firmware(self, did, pid):
            return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

        def start_firmware_update(self, did, pid):
            self.started.append((did, pid))

    cloud = Cloud()
    main = _Main([{"did": "5", "name": "Purifier"}], cloud)
    old_poll, old_q = fwmod.POLL_MS, QMessageBox.question
    fwmod.POLL_MS = 50
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    try:
        dlg = fwmod.FirmwareDialog(main, "5")
        dlg.show()
        assert pump(lambda: dlg.update_btn.isVisible(), 3)
        assert "2.2.10" in dlg.latest.text() and "Fix known issues" in dlg.notes.toPlainText()
        dlg.update_btn.click()
        assert pump(lambda: cloud.started == [("5", 0)], 3)
        assert pump(lambda: "newest firmware now" in dlg.state.text(), 5), dlg.state.text()
        assert main.firmware["5"]["isLatest"] is True and "5" in main.changed and dlg.progress.isHidden()
        dlg.close()
    finally:
        fwmod.POLL_MS, QMessageBox.question = old_poll, old_q
    ble = fwmod.FirmwareDialog(_Main([{"did": "blt.3.x", "name": "Thermometer"}], None), "blt.3.x")
    assert "phone" in ble.note.text().lower() or "sign in" in ble.note.text().lower()
    assert not ble.update_btn.isVisible()
    ble.close()


# -- 3: the right-click menu ---------------------------------------------------------------------------------------
def test_device_menu_offers_actions_rename_room_firmware_and_removal():
    from gui.device_menu import build_device_menu

    class Cloud:
        logged_in = True
    devices = [{"did": "7", "name": "Robot", "model": "x.vacuum.y"},
               {"did": "manual-1", "name": "By hand", "model": "x.plug.y"}]
    main = _Main(devices, Cloud())
    st = main.hub.state("7")
    st.summary, st.online = Summary(MiotSpec(VACUUM_SPEC)), True
    main.firmware["7"] = {"curr": "1", "latest": "2", "isLatest": False}
    menu = build_device_menu(main, "7")
    texts = [a.text() for a in menu.actions() if a.text()]
    assert texts[0] == "Robot" and "Open" in texts
    for wanted in ("Start Sweep", "Pause Sweeping", "Add to an action…", "Rename…", "Move to room…",
                   "Remove from XiControl…", "Delete from Mi account…"):
        assert wanted in texts, (wanted, texts)
    assert any(t.startswith("Firmware update available (2)") for t in texts)
    more = next(a.menu() for a in menu.actions() if a.text() == "More actions")
    assert more is not None and len(more.actions()) >= 3
    delete = next(a for a in menu.actions() if a.text() == "Delete from Mi account…")
    assert delete.isEnabled()
    hand = build_device_menu(main, "manual-1")
    assert not next(a for a in hand.actions() if a.text() == "Delete from Mi account…").isEnabled()


# -- 4: refresh ------------------------------------------------------------------------------------------------------
def test_refresh_reads_also_the_waiting_devices():
    from gui.hub import Hub
    from xc.device import DeviceController
    devices = [{"did": str(i), "name": f"D{i}", "model": "zhimi.airpurifier.fake", "ip": "127.0.0.1", "token": TOKEN,
                "mode": "local"} for i in (1, 2)]
    asked = []

    class Ctl(DeviceController):
        def get_props(self, pairs):
            asked.append(self.did)
            return {}
    ctls = {}
    hub = Hub(_Cfg(devices), None, lambda info: ctls.setdefault(info["did"], Ctl(info, None)))
    for d in devices:
        st = hub.state(d["did"])
        st.summary, st.spec_state = Summary(MiotSpec(fake_home.PURIFIER_SPEC)), "ready"
        st.next_poll = time.monotonic() + 300        # waiting after failures
    hub.poll_all()
    pump(lambda: False, 0.3)
    assert asked == []
    hub.refresh_now()
    assert pump(lambda: sorted(asked) == ["1", "2"], 3)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
