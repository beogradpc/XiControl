"""Tests for the tray: notification debounce, the start-with-Windows command, the icon and the menu.
Nothing here writes to the Windows registry."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests import fake_home  # noqa: E402
from xc import autostart  # noqa: E402
from xc.alerts import Alerts  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402
from xc.summary import Summary  # noqa: E402

app = QApplication.instance() or QApplication([])


# -- notification debounce ----------------------------------------------------------------------
def test_offline_needs_two_failures_and_a_device_that_was_online():
    a = Alerts()
    assert a.device("1", "Lamp", False, 5) == []                 # never seen online: stays quiet
    assert a.device("1", "Lamp", True, 0) == []
    assert a.device("1", "Lamp", False, 1) == []                 # one lost packet is not an outage
    ev = a.device("1", "Lamp", False, 2)
    assert [k for k, _t, _x in ev] == ["offline"] and "Lamp is offline" in ev[0][1]
    assert a.device("1", "Lamp", False, 3) == []                 # told once
    assert a.device("1", "Lamp", None, 3) == []                  # unknown is not "back"
    assert [k for k, _t, _x in a.device("1", "Lamp", True, 0)] == ["online"]
    assert a.device("1", "Lamp", True, 0) == []


def test_consumable_reminder_once_until_replaced():
    a = Alerts()
    assert a.consumables("2", "Purifier", {"Filter life": 40}) == []
    ev = a.consumables("2", "Purifier", {"Filter life": 9})
    assert len(ev) == 1 and ev[0][1] == "Purifier: Filter life 9%"
    assert a.consumables("2", "Purifier", {"Filter life": 8}) == []
    assert a.consumables("2", "Purifier", {"Filter life": 12}) == []   # wobbling around 10 % stays quiet
    assert a.consumables("2", "Purifier", {"Filter life": 9}) == []
    assert a.consumables("2", "Purifier", {"Filter life": 100}) == []  # replaced and reset
    assert len(a.consumables("2", "Purifier", {"Filter life": 10})) == 1
    a.forget("2")
    assert len(a.consumables("2", "Purifier", {"Filter life": 5})) == 1


# -- start with Windows -------------------------------------------------------------------------
def test_autostart_command_without_registry():
    assert autostart.command(r"C:\Apps\XiControl\XiControl.exe", frozen=True) == \
        '"C:\\Apps\\XiControl\\XiControl.exe" --tray'
    with tempfile.TemporaryDirectory() as d:
        for exe in ("python.exe", "pythonw.exe"):
            open(os.path.join(d, exe), "w").close()
        cmd = autostart.command(os.path.join(d, "python.exe"), frozen=False, main_py=r"C:\X Control\main.py")
        assert cmd == f'"{os.path.join(d, "pythonw.exe")}" "C:\\X Control\\main.py" --tray'   # no console window
    default = autostart.command(frozen=False)
    assert default.endswith('main.py" --tray') and os.path.isfile(default.split('"')[3])


def test_autostart_enable_disable_refresh_with_fake_registry():
    import types
    store = {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_READ=1, KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *a: Key(),
        QueryValueEx=lambda key, name: (store[name], 1) if name in store else (_ for _ in ()).throw(OSError()),
        SetValueEx=lambda key, name, _r, _t, value: store.__setitem__(name, value),
        DeleteValue=lambda key, name: store.pop(name) if name in store else (_ for _ in ()).throw(FileNotFoundError()),
    )
    real, sys.modules["winreg"] = sys.modules.get("winreg"), fake
    real_supported = autostart.supported
    autostart.supported = lambda: True
    try:
        assert not autostart.is_enabled()
        autostart.set_enabled(True)
        assert store["XiControl"] == autostart.command() and autostart.is_enabled()
        store["XiControl"] = '"C:\\old place\\pythonw.exe" "C:\\old place\\main.py" --tray'
        autostart.refresh()                                 # the app moved: the entry follows it
        assert store["XiControl"] == autostart.command()
        autostart.set_enabled(False)
        assert store == {} and not autostart.is_enabled()
        autostart.set_enabled(False)                        # already off: no error
        autostart.refresh()                                 # off stays off
        assert store == {}
    finally:
        autostart.supported = real_supported
        if real is not None:
            sys.modules["winreg"] = real
        else:
            sys.modules.pop("winreg", None)


# -- consumables reach the background poll ----------------------------------------------------
def test_summary_polls_consumables():
    s = Summary(MiotSpec(fake_home.PURIFIER_SPEC))
    assert [(p.name, label) for p, label in s.life_levels] == [("filter-life-level", "Filter life")]
    assert (4, 1) in s.tile_pairs()
    v = Summary(MiotSpec(fake_home.VACUUM_SPEC))
    assert [label for _p, label in v.life_levels] == ["Brush life"]


# -- icon -----------------------------------------------------------------------------------------
def test_icon_file():
    from gui.icon import app_icon, save_ico
    with tempfile.TemporaryDirectory() as d:
        path = save_ico(os.path.join(d, "x.ico"), sizes=(16, 32, 256))
        data = open(path, "rb").read()
    assert data[:6] == b"\x00\x00\x01\x00\x03\x00"                   # ICO header, 3 images
    first = int.from_bytes(data[18:22], "little")
    assert data[first:first + 8] == b"\x89PNG\r\n\x1a\n"
    assert data[6 + 16 * 2] == 0                                       # 256 px is stored as 0
    assert not app_icon().isNull()


# -- tray menu and notifications (no real tray offscreen: the history records them) ------------
class _Cfg:
    def __init__(self, devices):
        self.data = {"devices": devices, "notifications": True}
        self.devices = devices

    def device(self, did):
        return next((d for d in self.devices if str(d["did"]) == str(did)), None)

    def save(self):
        pass


class _Main(QObject):
    def __init__(self):
        super().__init__()
        from gui.hub import Hub
        devices = [{"did": "2", "name": "Air purifier", "model": "zhimi.airpurifier.fake", "tray": True},
                   {"did": "3", "name": "Lamp", "model": "yeelink.light.x"}]
        self.cfg = _Cfg(devices)
        self.hub = Hub(self.cfg, None, None)
        self.opened = []

    def bring_to_front(self):
        self.opened.append("window")

    def quit_app(self):
        pass

    def sign_in(self):
        self.opened.append("sign-in")

    def open_device(self, did):
        self.opened.append(did)


def test_tray_menu_and_alerts():
    from gui.tray import Tray
    main = _Main()
    tray = Tray(main)
    st = main.hub.state("2")
    st.summary = Summary(MiotSpec(fake_home.PURIFIER_SPEC))
    st.online, st.values = True, {(2, 1): True, (4, 1): 55}
    tray.device_changed("2")
    tray.rebuild_menu()
    texts = [a.text() for a in tray.menu.actions()]
    assert texts[0] == "Open XiControl" and texts[-1] == "Quit XiControl"
    fav = next(a for a in tray.menu.actions() if "Air purifier" in a.text())
    assert fav.isCheckable() and fav.isChecked() and fav.isEnabled() and "(on)" in fav.text()
    assert not any("Lamp" in t for t in texts)                       # not a favourite
    st.values[(4, 1)] = 7
    tray.device_changed("2")
    assert tray.history == [] and tray._maintenance_timer.isActive()   # reminders wait to be grouped
    tray._flush_maintenance()
    assert main.cfg.data["alerts_low"] == [["2", "Filter life"]]        # remembered for the next start
    st.online, st.fails = False, 2
    tray.device_changed("2")
    assert [k for k, _t, _x in tray.history] == ["consumable", "offline"]
    tray.rebuild_menu()
    fav = next(a for a in tray.menu.actions() if "Air purifier" in a.text())
    assert not fav.isEnabled() and "(offline)" in fav.text()
    tray.sign_in_needed()
    tray._click_target = "sign-in"
    tray._message_clicked()
    assert main.opened == ["window", "sign-in"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
