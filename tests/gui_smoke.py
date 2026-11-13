"""Offscreen GUI walkthrough against simulated devices; saves screenshots.
Usage: QT_QPA_PLATFORM=offscreen python tests/gui_smoke.py OUTDIR"""
import json
import os
import sys
import tempfile

# never touch the real settings: app_dir() uses %APPDATA% on Windows and ~ elsewhere
os.environ["HOME"] = os.environ["APPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QScrollArea  # noqa: E402

from tests import fake_home  # noqa: E402
from xc.config import app_dir  # noqa: E402

out = sys.argv[1] if len(sys.argv) > 1 else "."
fake_home.install_specs(os.path.join(app_dir(), "spec_cache"))
devices = fake_home.config_entries() + [
    {"did": "blt.3.abc", "name": "Bedroom thermometer", "model": "miaomiaoce.sensor_ht.t2", "ip": "",
     "token": "", "room": "Bedroom", "mode": "auto"},
    {"did": "998", "name": "Old Roborock", "model": "roborock.vacuum.s5", "ip": "127.0.0.9",
     "token": "aa" * 16, "room": "Bedroom", "mode": "auto"},
    # set to "Mi cloud only" without being signed in: must not claim an expired session
    {"did": "777", "name": "Cloud-only purifier", "model": "zhimi.airpurifier.fake", "ip": "127.0.0.2",
     "token": "11" * 16, "room": "Hall", "mode": "cloud"},
    {"did": "1063461234", "name": "Front door camera", "model": "chuangmi.camera.ipc019", "ip": "127.0.0.20",
     "token": "44" * 16, "room": "Hall", "mode": "cloud"},
    # a Mi Control Hub and a Zigbee plug behind it: the plug is switched through the hub over the LAN
    {"did": "50331649", "name": "Hall hub", "model": "lumi.gateway.mieu01", "ip": "127.0.0.31",
     "token": "ab" * 16, "room": "Hall", "mode": "auto"},
    {"did": "lumi.158d0001aaaaaa", "name": "Zigbee plug", "model": "lumi.plug.mmeu01", "ip": "", "token": "",
     "room": "Hall", "mode": "auto", "parent_id": "50331649"},
]
HUB_PLUGS = {"lumi.158d0001aaaaaa": {"neutral_0": "off"}}


def _urn(kind, name):
    return f"urn:miot-spec-v2:{kind}:{name}:0000:lumi:1"


ZIGBEE_SPECS = {
    "lumi.plug.mmeu01": {"type": _urn("device", "outlet"), "description": "Plug", "services": [
        {"iid": 1, "type": _urn("service", "device-information"), "description": "Device Information",
         "properties": [{"iid": 4, "type": _urn("property", "firmware-revision"), "description": "Firmware",
                         "format": "string", "access": ["read"]}]},
        {"iid": 2, "type": _urn("service", "switch"), "description": "Switch", "properties": [
            {"iid": 1, "type": _urn("property", "on"), "description": "Switch Status", "format": "bool",
             "access": ["read", "write", "notify"]}]}]},
    "lumi.gateway.mieu01": {"type": _urn("device", "gateway"), "description": "Gateway", "services": [
        {"iid": 1, "type": _urn("service", "device-information"), "description": "Device Information",
         "properties": [{"iid": 4, "type": _urn("property", "firmware-revision"), "description": "Firmware",
                         "format": "string", "access": ["read"]}]}]},
}
for _model, _spec in ZIGBEE_SPECS.items():
    with open(os.path.join(app_dir(), "spec_cache", _model + ".json"), "w") as f:
        json.dump(_spec, f)
with open(os.path.join(app_dir(), "spec_cache", "chuangmi.camera.ipc019.json"), "w") as f:
    from tests.test_fixes import CAM_SPEC
    json.dump(CAM_SPEC, f)
with open(os.path.join(app_dir(), "config.json"), "w") as f:
    json.dump({"devices": devices, "refresh_seconds": 5}, f)  # old plain-text format: must get encrypted

stop = fake_home.start_all()
fake_home.fd.start_background("127.0.0.31", fake_home.fd.make_hub(HUB_PLUGS), stop)
from gui import theme  # noqa: E402
from gui.main_window import MainWindow  # noqa: E402

app = QApplication(sys.argv)
theme.apply(app, "light")
win = MainWindow()
win.resize(1180, 820)
win.show()
errors = []
sys.excepthook = lambda *a: errors.append(a)
steps = []


def shot(name):
    win.grab().save(os.path.join(out, name))
    print("saved", name)


def step(ms, fn):
    steps.append((ms, fn))


def open_dev(name):
    did = next(d["did"] for d in win.cfg.devices if d["name"] == name)
    win.open_device(did)


def tile_power(name, on):
    did = next(d["did"] for d in win.cfg.devices if d["name"] == name)
    win.home.tiles[did].pwr.click()


step(5000, lambda: shot("01_home_light.png"))
step(100, lambda: tile_power("Living room lamp", False))
step(1500, lambda: print("lamp on after tile click:", fake_home.fd.STATE[(2, 1)]))
step(100, lambda: open_dev("Air purifier"))
step(2500, lambda: shot("02_purifier_top.png"))
step(100, lambda: win.page.findChild(QScrollArea).verticalScrollBar().setValue(560))
step(400, lambda: shot("03_purifier_settings.png"))
step(100, lambda: win.page.findChild(QScrollArea).verticalScrollBar().setValue(5000))
step(400, lambda: shot("04_purifier_settings_end.png"))
step(100, lambda: open_dev("Robot vacuum"))
step(2500, lambda: win.page.findChild(QScrollArea).verticalScrollBar().setValue(5000))
step(400, lambda: shot("05_vacuum_settings.png"))
step(100, lambda: (win.go_home(), win.home.theme_btn.click()))
step(2500, lambda: shot("06_home_dark.png"))
step(100, lambda: open_dev("Air purifier"))
step(2500, lambda: win.page.findChild(QScrollArea).verticalScrollBar().setValue(560))
step(400, lambda: shot("07_purifier_settings_dark.png"))
step(100, lambda: print("theme saved:", win.cfg.data.get("theme")))
step(100, lambda: (win.go_home(), win.home.theme_btn.click()))
step(300, lambda: print("cloud-only tile:", win.home.tiles["777"].status.text(),
                        "| session-expired dialog shown:", win._auth_warned or win._auth_pending))
step(100, lambda: open_dev("Front door camera"))
step(2500, lambda: shot("08_camera_page.png"))


def _camera_window():
    from gui.camera_view import CameraWindow, Source

    def no_go2rtc():
        raise FileNotFoundError("go2rtc is not installed")

    def no_cloud():
        raise RuntimeError("The camera didn't start sending video to the Mi cloud")
    w = CameraWindow("Front door camera", [Source("Local network", no_go2rtc, "your network"),
                                           Source("Mi cloud (HLS)", no_cloud, "10-20 s behind")], win)
    w.show()
    QTimer.singleShot(800, lambda: (w.grab().save(os.path.join(out, "09_camera_window.png")), w.close()))


def _settings():
    from gui.dialogs import SettingsDialog
    dlg = SettingsDialog(win.cfg, None, app_dir(), win)
    dlg.show()
    QTimer.singleShot(300, lambda: (dlg.grab().save(os.path.join(out, "10_settings.png")), dlg.close()))


def _check_disk():
    text = open(os.path.join(app_dir(), "config.json"), encoding="utf-8").read()
    print("config.json encrypted:", "dpapi:" in text, "| plain token left:", "11" * 16 in text)


def _tray():
    from PySide6.QtGui import QAction
    print("tray available:", win.tray.available, "| close to tray:", win.close_to_tray)
    print("notifications so far:", [t for _k, t, _x in win.tray.history])
    open_dev("Air purifier")
    next(a for a in win.page.findChildren(QAction) if a.text() == "Show in tray menu").trigger()
    win.tray.rebuild_menu()
    print("tray menu:", ascii([a.text() for a in win.tray.menu.actions() if a.text()]))


def _live_button():
    from gui.camera_view import CameraWindow
    win.page.open_live()                       # not signed in: only the Mi cloud way is left, and it says why not
    w = next(x for x in win.findChildren(CameraWindow))
    QTimer.singleShot(1200, lambda: (print("live view:", [s.label for s in w.sources], "|", ascii(w.status.text())),
                                     w.close()))


def _search():
    win.go_home()
    win.home.search.setText("purifier")
    shown = sorted(win.cfg.device(d)["name"] for d, t in win.home.tiles.items() if not t.isHidden())
    print("search 'purifier':", shown)
    win.home.search.setText("")
    win.home.online_only.setChecked(True)
    hidden = sorted(win.cfg.device(d)["name"] for d, t in win.home.tiles.items() if t.isHidden())
    print("online only hides:", hidden)
    win.home.online_only.setChecked(False)


def _dialogs():
    from gui.dialogs import ManageDialog, RemoveDialog
    from gui.pair_dialog import QrPairDialog, qr_pixmap
    d = RemoveDialog("Air purifier", True, "", win)
    d.account.setChecked(True)
    d.show()
    m = ManageDialog(win.cfg.devices, lambda did: "Offline" if win.hub.state(did).online is False else "Online", win)
    m._select(lambda r: m.table.item(r, 4).text() == "Offline")
    m.show()
    q = QrPairDialog(None, "MojWiFi", win)
    q.qr.setPixmap(qr_pixmap("b=TESTKEY&s=TW9qV2lGaQ==&p=AAAA&t=Europe/Belgrade&r=de"))
    q.qr.show()
    q.show()
    QTimer.singleShot(500, lambda: (d.grab().save(os.path.join(out, "11_remove_dialog.png")),
                                    m.grab().save(os.path.join(out, "12_manage_dialog.png")),
                                    q.grab().save(os.path.join(out, "13_qr_dialog.png")),
                                    print("manage: offline selected:", len(m.selected())),
                                    d.close(), m.close(), q.close()))


def _round4():
    from gui.actions_window import ActionsDialog
    from gui.device_menu import build_device_menu
    from xc.actions import device_commands, new_action, step_from
    did = next(d["did"] for d in win.cfg.devices if d["name"] == "Air purifier")
    menu = build_device_menu(win, did, win)
    print("right-click menu:", ascii([a.text() for a in menu.actions() if a.text()]))
    menu.deleteLater()
    off = next(c for c in device_commands(win.hub.state(did).summary) if c["label"] == "Turn off")
    a = new_action("All off")
    a["steps"] = [step_from(did, "Air purifier", off)]
    a["pinned"] = True
    win.cfg.data.setdefault("actions", []).append(a)
    win.actions_changed()
    pinned = [b.text() for b in win.home.actions_bar.findChildren(QPushButton)]
    print("pinned on home:", ascii(pinned), "| bar visible:", not win.home.actions_bar.isHidden())
    dlg = ActionsDialog(win, win)
    dlg.show()
    QTimer.singleShot(400, lambda: (dlg.grab().save(os.path.join(out, "15_actions.png")), dlg.close()))
    win.home.refresh_btn.click()
    win.home.actions_bar.findChildren(QPushButton)[0].click()      # runs "All off"
    print("purifier on before 'All off':", fake_home.PURIFIER_STATE.get((2, 1)))
    QTimer.singleShot(1500, lambda: print("purifier on after 'All off':", fake_home.PURIFIER_STATE.get((2, 1))))


def _homes():
    win.go_home()
    purifier = next(d for d in win.cfg.devices if d["name"] == "Air purifier")
    win.cfg.data["homes"] = [{"id": "1", "name": "Home", "owner": 1, "rooms": [], "dids": [], "raw": {}},
                             {"id": "2", "name": "Work", "owner": 1, "rooms": [{"id": "9", "name": "Office",
                                                                                 "dids": [str(purifier["did"])]}],
                              "dids": [], "raw": {}}]
    for d in win.cfg.devices:
        d["home_id"] = "2" if d is purifier else "1"
    win.home.online_only.setChecked(False)
    win.set_home("2")
    shown = sorted(win.cfg.device(x)["name"] for x, t in win.home.tiles.items() if not t.isHidden())
    menu = [a.text() for a in win.home.build_home_menu().actions() if a.text()]
    edit = [a.text() for a in win.home.build_home_edit_menu().actions() if a.text()]
    print("home picker:", ascii(win.home.home_btn.text()), "| tiles:", shown, "| menu:", ascii(menu), "| edit:", ascii(edit))
    win.set_home(None)
    print("all homes again:", ascii(win.home.home_btn.text()), len(win.home.tiles), "tiles")


def _round6():
    from gui.notifications import NotificationsDialog
    win.go_home()
    sizes = {}
    for size in ("small", "large", "medium"):
        win.set_tile_size(size)
        t = next(iter(win.home.tiles.values()))
        sizes[size] = (t.width(), t.height())
        if size == "large":
            win.grab().save(os.path.join(out, "17_home_large_tiles.png"))
    did = next(d["did"] for d in win.cfg.devices if d["name"] == "Air purifier")
    large_text = [m for m in win.hub.state(did).summary.metric_values(win.hub.state(did).values)]
    print("tile sizes:", sizes, "| purifier readings:", ascii([f"{label}={value}" for _s, label, value in large_text]))
    print("bell count:", win.home.bell.count, "| own alerts:", len(win.tray.log))
    dlg = NotificationsDialog(win, win)
    dlg.show()
    QTimer.singleShot(400, lambda: (dlg.grab().save(os.path.join(out, "18_notifications.png")), dlg.close()))


def _links():
    return {win.cfg.device(did)["name"]: t.link.kind for did, t in win.home.tiles.items()}


def _round8():
    """Local network or Mi cloud: the sign on the tiles, a Zigbee plug through its hub, one choice for all."""
    from xc.device import DeviceController
    win.go_home()
    win.set_tile_size("medium")
    plug = "lumi.158d0001aaaaaa"
    st = win.hub.state(plug)
    print("links:", _links())
    print("zigbee plug:", st.online, st.transport, "| on:", win.hub.is_on(plug), "| tip:",
          ascii(win.home.tiles[plug].link.toolTip().splitlines()[0]))
    print("subtitle:", ascii(win.home.subtitle.text()))
    shot("19_home_links.png")
    win.home.tiles[plug].pwr.click()                       # switched through the hub
    QTimer.singleShot(1500, lambda: print("zigbee plug at the hub after the tile click:",
                                          HUB_PLUGS[plug]["neutral_0"], "| via", win.hub.state(plug).transport))

    def all_cloud():
        DeviceController.mode_for_all = "cloud"            # what Settings -> Connection does
        win._connection_changed()
        print("all through the cloud, not signed in:", _links(), "|",
              ascii(win.hub.state(plug).error or "")[:60])
        DeviceController.mode_for_all = None
        win._connection_changed()
        lamp = next(d["did"] for d in win.cfg.devices if d["name"] == "Living room lamp")
        win.set_device_mode([lamp, plug], "local")
        print("after choosing per device again:", _links(), "| lamp mode:", win.cfg.device(lamp)["mode"])
        menu = win.tile_menu and __import__("gui.device_menu", fromlist=["x"]).build_device_menu(win, plug, win)
        conn = next(a.menu() for a in menu.actions() if a.menu() and a.text().startswith("Connection"))
        print("menu connection:", [(a.text().split(" - ")[0], a.isChecked()) for a in conn.actions() if a.text()])
        menu.deleteLater()
        win.set_tile_size("small")
        shot("20_home_links_small.png")
        win.set_tile_size("medium")
    QTimer.singleShot(2200, all_cloud)


step(100, _camera_window)
step(3500, _live_button)
step(2000, _settings)
step(800, _check_disk)
step(100, _tray)
step(300, _search)
step(300, lambda: shot("14_home_search.png"))
step(100, _dialogs)
step(1200, _round4)
step(2500, lambda: shot("16_home_pinned_action.png"))
step(300, _homes)
step(300, _round6)
step(900, _round8)
step(3600, lambda: None)


def run(i=0):
    if i >= len(steps):
        print("errors:", len(errors))
        import traceback
        for e in errors:
            traceback.print_exception(*e)
        stop.set()
        app.quit()
        return
    ms, fn = steps[i]

    def go():
        try:
            fn()
        except Exception:  # noqa: BLE001 - report it and keep walking, instead of hanging
            errors.append(sys.exc_info())
        run(i + 1)
    QTimer.singleShot(ms, go)


run()
app.exec()
