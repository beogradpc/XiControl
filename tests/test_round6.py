"""Tests for: tile readings and the three tile sizes, consumption history, the notifications list and bell,
automations per device, and who devices are shared with."""
import atexit
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir(r"C:\Windows\Fonts"):      # real fonts, so that text widths mean something
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from tests import fake_home  # noqa: E402
from tests.test_fixes import _session  # noqa: E402
from xc import energy  # noqa: E402
from xc.cloud import XiaomiCloud, cron_text  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402
from xc.summary import Summary, metric_text  # noqa: E402

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


def settle():
    """Lets widgets that were replaced (deleteLater) really go away."""
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def _urn(kind, name):
    return f"urn:miot-spec-v2:{kind}:{name}:0000:x:1"


def _prop(iid, name, fmt, access=("read", "notify"), unit=None, values=None, rng=None):
    p = {"iid": iid, "type": _urn("property", name), "description": name.replace("-", " ").title(), "format": fmt,
         "access": list(access)}
    if unit:
        p["unit"] = unit
    if values:
        p["value-list"] = [{"value": v, "description": d} for v, d in values]
    if rng:
        p["value-range"] = rng
    return p


SENSOR_SPEC = {"type": _urn("device", "temperature-humidity-sensor"), "services": [
    {"iid": 2, "type": _urn("service", "temperature-humidity-sensor"), "description": "Sensor", "properties": [
        _prop(1, "temperature", "float", unit="celsius"), _prop(2, "relative-humidity", "float", unit="percentage"),
        _prop(3, "pm2.5-density", "uint16")]},
    {"iid": 3, "type": _urn("service", "battery"), "description": "Battery", "properties": [
        _prop(1, "battery-level", "uint8", unit="percentage")]},
    {"iid": 4, "type": _urn("service", "magnet-sensor"), "description": "Door", "properties": [
        _prop(1, "contact-state", "bool"), _prop(2, "illumination", "uint8", values=[(1, "Weak"), (2, "Strong")])]},
]}
PLUG_SPEC = {"type": _urn("device", "outlet"), "services": [
    {"iid": 2, "type": _urn("service", "switch"), "description": "Switch", "properties": [
        _prop(1, "on", "bool", access=("read", "write", "notify"))]},
    {"iid": 11, "type": _urn("service", "power-consumption"), "description": "Power", "properties": [
        _prop(1, "power-consumption", "float"), _prop(2, "electric-power", "uint16", unit="watt")]},
]}
LIGHT_SPEC = {"type": _urn("device", "light"), "services": [
    {"iid": 2, "type": _urn("service", "light"), "description": "Light", "properties": [
        _prop(1, "on", "bool", access=("read", "write", "notify")),
        _prop(2, "brightness", "uint8", access=("read", "write", "notify"), unit="percentage", rng=[1, 100, 1])]},
]}


# -- 1: tile readings ---------------------------------------------------------------------------------------
def test_tiles_pick_the_readings_that_matter():
    s = Summary(MiotSpec(SENSOR_SPEC))
    assert [m.label for m in s.metrics] == ["Temperature", "Humidity", "PM2.5", "Light", "Door", "Battery"]
    values = {(2, 1): 23.456, (2, 2): 46.0, (2, 3): 12, (3, 1): 87, (4, 1): False, (4, 2): 2}
    got = s.metric_values(values)
    assert [(label, text) for _sign, label, text in got] == [
        ("Temperature", "23.5 °C"), ("Humidity", "46 %"), ("PM2.5", "12 µg/m³"), ("Light", "Strong"),
        ("Door", "Open"), ("Battery", "87 %")]
    assert s.metric_values({(2, 1): 20.0}) == [("🌡", "Temperature", "20 °C")]      # only what is known
    assert len(s.metric_values(values, 3)) == 3
    for pair in ((2, 1), (2, 2), (3, 1), (4, 1)):
        assert pair in s.tile_pairs()                                             # so they get polled
    # the battery isn't said twice: the status line can leave it out
    assert "battery" in s.status_text(values) and "battery" not in s.status_text(values, battery=False)
    # a plug shows its power, not the unitless counter; a purifier's display brightness is not a reading
    assert [m.label for m in Summary(MiotSpec(PLUG_SPEC)).metrics] == ["Power"]
    assert [m.label for m in Summary(MiotSpec(LIGHT_SPEC)).metrics] == ["Brightness"]
    assert "Brightness" not in [m.label for m in Summary(MiotSpec(fake_home.PURIFIER_SPEC)).metrics]
    spec = MiotSpec(SENSOR_SPEC)
    left = MiotSpec({"type": "x", "services": [{"iid": 2, "type": _urn("service", "x"), "description": "", "properties": [
        _prop(1, "left-time", "uint16", unit="minutes"), _prop(2, "cleaning-time", "uint32", unit="seconds")]}]})
    assert metric_text(left.services[0].props[0], 95) == "1 h 35 min"
    assert metric_text(left.services[0].props[1], 45) == "45 s" and metric_text(left.services[0].props[1], 600) == "10 min"
    assert metric_text(spec.services[2].props[0], True) == "Closed"


def test_tiles_come_in_three_sizes():
    from gui.home import TILE_SIZES, DeviceTile
    from gui.hub import DeviceState
    st = DeviceState()
    st.summary, st.online = Summary(MiotSpec(SENSOR_SPEC)), True
    st.values = {(2, 1): 23.4, (2, 2): 46.0, (2, 3): 12, (3, 1): 87, (4, 1): True, (4, 2): 1}
    info = {"did": "1", "name": "Bedroom thermometer with a very long name", "model": "miaomiaoce.sensor_ht.t2"}
    tiles = {size: DeviceTile(info, size) for size in TILE_SIZES}
    for size, t in tiles.items():
        t.show()
        t.update_state(st, False, None)
        assert (t.width(), t.height()) == TILE_SIZES[size]
    small, medium, large = tiles["small"], tiles["medium"], tiles["large"]
    assert small.status.isHidden() and small.metrics.isHidden() and "23.4 °C" in small.name.toolTip()
    assert "23.4 °C" in medium.metrics.text() and "46 %" in medium.metrics.text() and "87 %" not in medium.metrics.text()
    assert "…" not in medium.metrics.text()               # whole readings only
    assert "Battery: 87 %" in medium.metrics.toolTip()
    shown = [(v.text(), n.text()) for v, n in large.cells if v.text()]
    assert len(shown) == 6 and shown[0][0].endswith("23.4 °C") and shown[0][1] == "Temperature"
    assert shown[5][0].endswith("87 %") and "battery" not in large.status.text()
    for t in tiles.values():                              # long names are cut with "…", never clipped
        assert t.name.fontMetrics().horizontalAdvance(t.name.text()) <= t.width()
        t.close()
    assert DeviceTile(info, "nonsense").size_name == "medium"


# -- 2: consumption -------------------------------------------------------------------------------------------
def test_consumption_history_days_weeks_months_years():
    spec = MiotSpec(PLUG_SPEC)
    assert energy.energy_source("cuco.plug.v2eur", spec) == {"key": "11.1", "day": "stat_day_v3",
                                                             "month": "stat_month_v3", "kwh": 0.01}
    assert energy.energy_source("other.plug.x", spec)["kwh"] is None            # unit of this maker unknown
    assert energy.energy_source("lumi.plug.mmeu01", MiotSpec({"type": "x", "services": []})) == {
        "key": "powerCost", "day": "stat_day", "month": "stat_month", "kwh": 0.001}
    assert energy.energy_source("chuangmi.plug.hmi206", MiotSpec(LIGHT_SPEC)) is None

    c = XiaomiCloud("de", _session())
    sent = []
    day0 = 1793980800 - 8 * 3600 + 86400 * 3       # some midnight in China
    c.api = lambda path, data, **kw: (sent.append((path, data)), {"result": (
        [{"value": f"[{70 + i}]", "time": day0 + i * 86400} for i in range(10)][::-1] if data["data_type"] == "stat_day_v3"
        else [{"value": "[1101]", "time": day0}, {"value": "[5593]", "time": day0 - 280 * 86400},
              {"value": "bad", "time": 1}])})[1]
    data = energy.fetch(c, "7", energy.energy_source("cuco.plug.v2eur", spec), now=day0 + 11 * 86400)
    assert sent[0][0] == "v2/user/statistics" and sent[0][1]["key"] == "11.1" and sent[0][1]["did"] == "7"
    assert data["unit"] == "kWh" and data["days"][0] == (day0, 0.7) and data["days"][-1][1] == 0.79
    assert [v for _t, v in data["months"]] == [55.93, 11.01]                    # oldest first, bad rows dropped
    days = energy.bars(data, "day")
    assert len(days) == 10 and days[0][1] == 0.7 and days[0][0] == time.strftime("%d.%m.", time.gmtime(day0 + 8 * 3600))
    weeks = energy.bars(data, "week")
    assert abs(sum(v for _l, v, _t in weeks) - sum(v for _t, v in data["days"])) < 1e-6 and len(weeks) in (2, 3)
    assert any("days)" in long for _l, _v, long in weeks)                       # an incomplete week says so
    months = energy.bars(data, "month")
    assert [v for _l, v, _t in months] == [55.93, 11.01]
    years = energy.bars(data, "year")
    assert sum(v for _l, v, _t in years) == 66.94 and all("months)" in long for _l, _v, long in years)
    assert energy.number(0.7, "kWh") == "0.70 kWh" and energy.number(12.34, "kWh") == "12.3 kWh"
    assert energy.number(1234.5, "kWh") == "1 234 kWh" and energy.number(70, None) == "70" and energy.number(5.5, "") == "5.50"
    text = energy.summary(days, "day", "kWh")
    assert text.startswith("Total 7.45 kWh") and "per day" in text and "most: 0.79 kWh" in text
    assert "No consumption" in energy.summary([], "day", "kWh")
    raw = energy.fetch(c, "7", energy.energy_source("other.plug.x", spec), now=day0 + 11 * 86400)
    assert raw["unit"] is None and raw["days"][0] == (day0, 70.0)


def test_consumption_chart_draws():
    from gui import theme
    from gui.energy import BarChart
    theme.apply(app, "light")
    chart = BarChart()
    chart.resize(700, 240)
    chart.set_rows([(f"{i:02d}.11.", 0.5 + i / 10, f"day {i}") for i in range(1, 31)], "kWh")
    assert chart.grab().width() == 700
    chart.set_rows([], None, "nothing yet")
    assert chart.grab().height() == 240 and chart.message == "nothing yet"


# -- 3: notifications -----------------------------------------------------------------------------------------
MESSAGES = {"result": {"messages": [
    {"msg_id": 5, "type": 6, "did": "blt.1", "title": "Low battery", "content": "vrata senzor", "is_new": 1, "ctime": 2000,
     "params": {"model": "isa.magnet.dw2hl", "body": {"homeRoomExtra": {"homeName": "Home", "roomName": "hodnik"}}}},
    {"msg_id": 9, "type": 13, "did": "", "title": "kuhinja Motion detected - Action completed", "content": "", "is_new": 0,
     "ctime": 3000, "params": {}},
    "junk"]}}


def test_cloud_messages_shares_and_scene_details():
    c = XiaomiCloud("de", _session())
    sent = []

    def api(path, data, **kw):
        sent.append((path, data))
        if path.startswith("v2/message/"):
            return MESSAGES
        if path == "share/device_share_list":
            return {"result": {"list": {"77": [{"nickname": "Ana", "userid": 123456, "mtime": 1700000000}]}}}
        if path == "v2/homeroom/gethome":
            return {"result": {"homelist": [{"id": 11, "name": "Home", "uid": 99, "dids": [], "roomlist": []}]}}
        if path.endswith("GetSceneList"):
            return {"result": {"scene_info_list": [
                {"scene_id": 31, "name": "Door left open", "type": 0, "enable": True,
                 "scene_trigger": {"triggers": [{"src": "device", "key": "prop.4121", "name": "Timeout, not closed",
                                                 "extra_json": {"device_name": "vrata senzor", "did": "blt.1",
                                                                "model": "isa.magnet.dw2hl"}}]},
                 "scene_action": {"actions": [{"type": 0, "name": "Turn on", "payload_json": {
                     "command": "set_properties", "device_name": "Lamp", "did": "55", "model": "x.light.y"}}]}},
                {"scene_id": 32, "name": "Evening", "type": 0, "enable": False,
                 "scene_trigger": {"triggers": [{"src": "timer", "key": "timer.crontab", "name": "每天 21:01",
                                                 "value_json": "0 1 21 * * 0,1,2,3,4,5,6 *"}]},
                 "scene_action": {"actions": [{"type": 2, "name": 'Run "sirena"', "payload_json": {"scene_id": "40"}}]}}]}}
        return {"result": {}}
    c.api = api
    msgs = c.messages()
    assert [m["id"] for m in msgs] == ["9", "5"]                                  # newest first, junk skipped
    assert msgs[0]["device"] == "Logs" and msgs[1] == {
        "id": "5", "type": 6, "did": "blt.1", "title": "Low battery", "device": "vrata senzor", "home": "Home",
        "room": "hodnik", "model": "isa.magnet.dw2hl", "time": 2000, "new": True}
    c.device_messages("blt.1", 20)
    assert sent[-1] == ("v2/message/v2/list", {"type": 6, "did": "blt.1", "limit": 20})
    assert c.shares() == {"77": [{"userid": "123456", "name": "Ana", "time": 1700000000}]}
    door, evening = c.scenes()
    assert door["triggers"] == [{"kind": "device", "did": "blt.1", "device": "vrata senzor", "model": "isa.magnet.dw2hl",
                                 "text": "Timeout, not closed"}]
    assert door["actions"][0]["did"] == "55" and door["actions"][0]["text"] == "Turn on" and door["enabled"]
    assert evening["triggers"][0]["text"] == "Every day 21:01" and not evening["enabled"] and not evening["manual"]
    assert evening["actions"][0]["text"] == 'Run "sirena"'
    assert cron_text("0 30 6 * * 1,2,3,4,5 *") == "Mon, Tue, Wed, Thu, Fri 06:30"
    assert cron_text("nonsense") == "At a set time"


class _Cfg:
    def __init__(self, devices):
        self.data, self.devices = {"refresh_seconds": 30}, devices
        self.saved = 0

    def device(self, did):
        return next((d for d in self.devices if str(d["did"]) == str(did)), None)

    def save(self):
        self.saved += 1


class _Main:
    signed_in = True

    def __init__(self, devices, cloud):
        self.cfg, self.cloud = _Cfg(devices), cloud
        self.opened = []
        self.messages, self.app_log = [], []

    def open_device(self, did):
        self.opened.append(did)

    def load_notifications(self, then=None, failed=None):
        self.messages = self.cloud.messages()
        if then:
            then()

    def all_notifications(self):
        return sorted(self.messages + self.app_log, key=lambda m: -m["time"])

    def on_account(self, info):
        return True

    def run_scene(self, scene):
        self.opened.append(("scene", scene["id"]))

    def run_user_action(self, action):
        pass

    def actions_changed(self):
        pass

    def toast(self, text, ms=0):
        self.opened.append(("toast", text))


def test_notifications_window_and_bell():
    from gui.home import BellButton
    from gui.notifications import NotificationsDialog, when
    c = XiaomiCloud("de", _session())
    c.api = lambda path, data, **kw: MESSAGES
    main = _Main([{"did": "blt.1", "name": "vrata senzor", "model": "isa.magnet.dw2hl"}], c)
    main.app_log = [{"kind": "app", "title": "Lamp is offline", "time": 2500, "did": "", "device": "XiControl", "new": True}]
    dlg = NotificationsDialog(main)
    dlg.show()
    settle()
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    for wanted in ("Low battery", "vrata senzor", "Home | hodnik", "Lamp is offline", "Logs"):
        assert wanted in texts, (wanted, texts)
    order = [t for t in texts if t in ("Low battery", "Lamp is offline", "kuhinja Motion detected - Action completed")]
    assert order == ["kuhinja Motion detected - Action completed", "Lamp is offline", "Low battery"]   # newest first
    assert "2 new" in dlg.note.text()
    dlg._open({"did": "gone"})
    assert main.opened == [] and "isn't in XiControl" in dlg.note.text()
    dlg._open({"did": "blt.1"})
    assert main.opened == ["blt.1"]
    assert when(0) == "" and ":" in when(time.time())
    bell = BellButton()
    bell.resize(38, 38)
    bell.set_count(13)
    assert "13 new" in bell.toolTip() and bell.grab().width() == 38
    bell.set_count(0)
    assert bell.toolTip() == "Notifications"


# -- 5: automations per device, 4: sharing ----------------------------------------------------------------------
def test_automations_are_listed_per_device():
    from gui.actions_window import ActionsDialog
    scenes = [
        {"id": "1", "name": "sirena", "home_id": "11", "home": "Home", "owner": 9, "type": 0, "manual": True, "enabled": True,
         "triggers": [{"kind": "user", "did": "", "device": "", "model": "", "text": "Tap to run"}],
         "actions": [{"did": "55", "device": "Hub", "model": "", "text": "Play ringtone", "scene_id": ""}]},
        {"id": "2", "name": "Door left open", "home_id": "11", "home": "Home", "owner": 9, "type": 0, "manual": False,
         "enabled": True, "triggers": [{"kind": "device", "did": "blt.1", "device": "vrata senzor", "model": "",
                                        "text": "Timeout, not closed"}],
         "actions": [{"did": "", "device": "", "model": "", "text": 'Run "sirena"', "scene_id": "1"}]},
        {"id": "3", "name": "Evening lamp", "home_id": "11", "home": "Home", "owner": 9, "type": 0, "manual": False,
         "enabled": False, "triggers": [{"kind": "timer", "did": "", "device": "", "model": "", "text": "Every day 21:01"}],
         "actions": [{"did": "55", "device": "Hub", "model": "", "text": "Turn on", "scene_id": ""}]}]

    class Cloud:
        def scenes(self):
            return scenes
    devices = [{"did": "blt.1", "name": "vrata senzor (renamed here)", "model": "x"}, {"did": "55", "name": "Hub", "model": "y"}]
    main = _Main(devices, Cloud())
    dlg = ActionsDialog(main)
    dlg.show()
    assert pump(lambda: dlg.scenes is not None)
    settle()
    texts = [w.text() for w in dlg.findChildren(QLabel)]
    assert "Manual controls (Xiaomi Home)" in texts and "Automation (Xiaomi Home)  ·  1 on, 1 off" in texts
    assert 'vrata senzor: Timeout, not closed  ▸  Run "sirena"' in texts and "Every day 21:01  ▸  Hub: Turn on" in texts
    from gui.theme import ToggleSwitch
    states = [w.isChecked() for w in dlg.findChildren(ToggleSwitch) if w.isVisibleTo(dlg)]
    assert states == [True, False]                                             # switched-on ones first
    assert [dlg.filter.itemText(i) for i in range(dlg.filter.count())] == ["Everything", "Only: Hub",
                                                                           "Only: vrata senzor (renamed here)"]
    dlg.filter.setCurrentIndex(dlg.filter.findData("blt.1"))
    settle()
    texts = [w.text() for w in dlg.findChildren(QLabel) if w.isVisibleTo(dlg)]
    assert "Door left open" in texts and "Evening lamp" not in texts and "sirena" not in texts
    assert "Automation (Xiaomi Home)  ·  1 on, 0 off" in texts
    dlg.close()
    only = ActionsDialog(main, device_filter="55")
    only.show()
    assert pump(lambda: only.scenes is not None)
    settle()
    texts = [w.text() for w in only.findChildren(QLabel) if w.isVisibleTo(only)]
    assert "Evening lamp" in texts and "sirena" in texts and "Door left open" not in texts
    only.close()


def test_sharing_window_lists_who_has_access():
    from gui.sharing import SharingDialog

    class Cloud:
        def shares(self):
            return {"77": [{"userid": "123456", "name": "Ana", "time": 1700000000}], "88": [
                {"userid": "5", "name": "", "time": 0}]}

        def device_shares(self, did, pid=0):
            return [dict(u, readonly=True, status=1) for u in self.shares().get(did, [])]
    main = _Main([{"did": "77", "name": "Thermometer"}, {"did": "88", "name": "Lamp"}], Cloud())
    all_dlg = SharingDialog(main)
    all_dlg.show()
    assert pump(lambda: all_dlg.table.rowCount() == 2)
    assert "2 devices are shared" in all_dlg.status.text()
    assert [all_dlg.table.item(1, c).text() for c in range(4)] == ["Thermometer", "Ana", "123456", "View only"]
    assert not all_dlg.share_btn.isVisibleTo(all_dlg) and not all_dlg.stop_btn.isEnabled()
    one = SharingDialog(main, "77")
    one.show()
    assert pump(lambda: one.table.rowCount() == 1)
    assert "Shared with 1 other account." in one.status.text() and one.table.isColumnHidden(0)
    none = SharingDialog(_Main([{"did": "99", "name": "Fan"}], Cloud()), "99")
    none.show()
    assert pump(lambda: "isn't shared" in none.status.text())
    for d in (all_dlg, one, none):
        d.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
