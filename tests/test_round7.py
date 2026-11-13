"""Tests for: switching an automation of the Xiaomi Home app on and off, sharing a device with another Mi
account and stopping a share. The Mi cloud is faked - nothing here talks to a real account."""
import atexit
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox  # noqa: E402

from tests.test_fixes import _session  # noqa: E402
from tests.test_round6 import _Main  # noqa: E402
from xc.cloud import CloudError, XiaomiCloud  # noqa: E402

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
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


# -- the requests, exactly as the Xiaomi Home app sends them -----------------------------------------------------
def test_cloud_requests_for_automation_switch_and_sharing():
    c = XiaomiCloud("de", _session())
    sent = []
    answers = {
        "share/get_share_user": {"result": {"list": [
            {"userid": 123456, "nickname": "Ana", "sharetime": 1700000000, "status": 1, "isReadOnly": True, "icon": ""},
            {"userid": 5, "nickname": None, "sharetime": 0, "status": 0, "isReadOnly": False},
            {"userid": 6, "nickname": "Old app", "status": 1}, {"nickname": "no id"}, "junk"]}},
        "home/profile": {"result": {"userid": 123456, "nickname": " Ana ", "icon": "x"}},
    }

    def api(path, data, **kw):
        sent.append((path, data))
        return answers.get(path, {"code": 0, "result": {}})
    c.api = api
    scene = {"id": 31, "name": "Door left open", "home_id": 11, "owner": "99", "type": 0}
    c.set_scene_enabled(scene, False)
    assert sent[-1] == ("appgateway/miot/appsceneservice/AppSceneService/SetSceneStatus",
                        {"scene_id": "31", "home_id": "11", "owner_uid": 99, "enable": False})
    c.set_scene_enabled(scene, 1)
    assert sent[-1][1]["enable"] is True
    users = c.device_shares("77", None)
    assert sent[-1] == ("share/get_share_user", {"did": "77", "pid": 0})
    assert users == [
        {"userid": "123456", "name": "Ana", "time": 1700000000, "readonly": True, "status": 1},
        {"userid": "5", "name": "", "time": 0, "readonly": False, "status": 0},
        {"userid": "6", "name": "Old app", "time": 0, "readonly": None, "status": 1}]
    assert c.find_user(" 123456 ") == {"userid": "123456", "name": "Ana"}
    assert sent[-1] == ("home/profile", {"id": "123456"})
    n = len(sent)
    assert c.find_user("ana@example.com") is None and len(sent) == n             # not a Mi ID: nothing is asked
    answers["home/profile"] = {"result": {"userid": 1, "nickname": None, "icon": ""}}
    assert c.find_user("1") is None                                               # the cloud knows no such account
    c.share_device("77", 123456)
    assert sent[-1] == ("share/share_request", {"command": "share_request", "did": "77", "userid": "123456"})
    c.share_device("77", "123456", readonly=True)
    assert sent[-1][1] == {"command": "share_request", "did": "77", "userid": "123456", "type": "readonly"}
    c.unshare_device("77", 123456, 8)
    assert sent[-1] == ("share/delete_user", {"list": [{"did": "77", "pid": 8, "userid": ["123456"]}]})


# -- the switch next to an automation -------------------------------------------------------------------------
def _scenes():
    return [
        {"id": "2", "name": "Door left open", "home_id": "11", "home": "Home", "owner": 9, "type": 0, "manual": False,
         "enabled": True, "triggers": [{"kind": "device", "did": "blt.1", "device": "Door", "model": "", "text": "Opens"}],
         "actions": [{"did": "55", "device": "Hub", "model": "", "text": "Play ringtone", "scene_id": ""}]},
        {"id": "3", "name": "Evening lamp", "home_id": "11", "home": "Home", "owner": 9, "type": 0, "manual": False,
         "enabled": False, "triggers": [{"kind": "timer", "did": "", "device": "", "model": "", "text": "Every day 21:01"}],
         "actions": [{"did": "55", "device": "Hub", "model": "", "text": "Turn on", "scene_id": ""}]}]


def test_automation_switch_sends_the_change_and_updates_the_counts():
    from gui import actions_window
    from gui.theme import ToggleSwitch
    scenes = _scenes()

    class Cloud:
        calls = []
        fail = False

        def scenes(self):
            return scenes

        def set_scene_enabled(self, scene, enable):
            self.calls.append((scene["id"], enable))
            if self.fail:
                raise CloudError("Xiaomi cloud error -6: permission denied")
    cloud = Cloud()
    main = _Main([{"did": "55", "name": "Hub", "model": "y"}], cloud)
    dlg = actions_window.ActionsDialog(main)
    dlg.show()
    assert pump(lambda: dlg.scenes is not None)
    settle()
    on_sw, off_sw = [w for w in dlg.findChildren(ToggleSwitch) if w.isVisibleTo(dlg)]
    assert on_sw.isChecked() and not off_sw.isChecked()
    assert dlg.auto_title.text() == "Automation (Xiaomi Home)  ·  1 on, 1 off"
    off_sw.click()                                                              # Evening lamp: on
    assert not off_sw.isEnabled()                                               # no second click while it is sent
    assert pump(lambda: cloud.calls == [("3", True)] and off_sw.isEnabled())
    assert scenes[1]["enabled"] and off_sw.isChecked()
    assert dlg.auto_title.text() == "Automation (Xiaomi Home)  ·  2 on, 0 off"
    assert ("toast", "“Evening lamp” switched on ✓") in main.opened
    rows = [w.text() for w in dlg.findChildren(QLabel) if w.objectName() == "RowLabel" and w.isVisibleTo(dlg)]
    assert rows == ["Door left open", "Evening lamp"]                           # the row stays where it was
    # a refusal of the cloud puts the switch back and says why
    warned = []
    old = QMessageBox.warning
    QMessageBox.warning = staticmethod(lambda *a, **k: warned.append(a[2]))
    try:
        cloud.fail = True
        on_sw.click()                                                           # Door left open: off - refused
        assert pump(lambda: bool(warned))
    finally:
        QMessageBox.warning = old
    assert cloud.calls[-1] == ("2", False) and "permission denied" in warned[0] and "Door left open" in warned[0]
    assert on_sw.isChecked() and on_sw.isEnabled() and scenes[0]["enabled"]
    assert dlg.auto_title.text() == "Automation (Xiaomi Home)  ·  2 on, 0 off"
    dlg.close()


# -- sharing --------------------------------------------------------------------------------------------------
class _ShareCloud:
    user_id = 99

    def __init__(self):
        self.table = {"77": [{"userid": "123456", "name": "Ana", "time": 1700000000, "readonly": True, "status": 1}]}
        self.calls = []
        self.fail = None

    def shares(self):
        return {did: [{"userid": u["userid"], "name": u["name"], "time": u["time"]} for u in users]
                for did, users in self.table.items() if users}

    def device_shares(self, did, pid=0):
        return list(self.table.get(did, []))

    def find_user(self, uid):
        self.calls.append(("find", uid))
        return {"userid": "222", "name": "Marko"} if uid == "222" else None

    def share_device(self, did, userid, readonly=False):
        self.calls.append(("share", did, userid, readonly))
        if self.fail:
            raise CloudError(self.fail)
        self.table.setdefault(did, []).append({"userid": userid, "name": "Marko", "time": 0, "readonly": readonly,
                                                "status": 0})

    def unshare_device(self, did, userid, pid=0):
        self.calls.append(("unshare", did, userid, pid))
        if self.fail:
            raise CloudError(self.fail)
        self.table[did] = [u for u in self.table[did] if u["userid"] != userid]


def test_share_dialog_looks_the_account_up_first():
    from gui.sharing import ShareWithDialog
    cloud = _ShareCloud()
    dlg = ShareWithDialog(_Main([], cloud), "Thermometer")
    dlg.show()
    assert not dlg.ok.isEnabled() and not dlg.find_btn.isEnabled()
    dlg.uid.setText("ana@example.com")
    assert not dlg.find_btn.isEnabled()                                         # only a Mi ID (a number) is accepted
    dlg.uid.setText("99")
    dlg.find()
    assert "your own Mi ID" in dlg.found.text() and cloud.calls == []
    dlg.uid.setText("333")
    dlg.find()
    assert pump(lambda: "no Mi account" in dlg.found.text()) and not dlg.ok.isEnabled()
    dlg.uid.setText("222")
    dlg.find()
    assert pump(lambda: dlg.ok.isEnabled())
    assert dlg.found.text() == "✓ Marko  ·  Mi ID 222" and dlg.user == {"userid": "222", "name": "Marko"}
    dlg.uid.setText("2223")                                                     # changed afterwards: ask again
    assert dlg.user is None and not dlg.ok.isEnabled()
    assert not dlg.readonly()
    dlg.view_only.setChecked(True)
    assert dlg.readonly()
    dlg.close()


def test_sharing_window_shares_and_stops_sharing():
    from gui import sharing
    cloud = _ShareCloud()
    main = _Main([{"did": "77", "name": "Thermometer", "pid": 8}, {"did": "88", "name": "Lamp", "shared": True}], cloud)
    dlg = sharing.SharingDialog(main, "77")
    dlg.show()
    assert pump(lambda: dlg.table.rowCount() == 1)
    assert [dlg.table.item(0, c).text() for c in range(1, 4)] == ["Ana", "123456", "View only"]
    assert dlg.share_btn.isVisibleTo(dlg) and dlg.share_btn.isEnabled() and not dlg.stop_btn.isEnabled()

    def answer(user, readonly, result=QDialog.Accepted):
        def fake(d):
            d.user = user
            d.view_only.setChecked(readonly)
            d.deleteLater()
            return result
        return fake
    old_run, old_q = sharing.run_dialog, QMessageBox.question
    asked = []
    try:
        sharing.run_dialog = answer({"userid": "222", "name": "Marko"}, False, QDialog.Rejected)
        dlg.share()
        assert not any(c[0] == "share" for c in cloud.calls)                    # cancelled: nothing is sent
        sharing.run_dialog = answer({"userid": "123456", "name": "Ana"}, False)
        dlg.share()
        assert "already shared with Ana" in dlg.status.text() and not any(c[0] == "share" for c in cloud.calls)
        sharing.run_dialog = answer({"userid": "222", "name": "Marko"}, False)
        dlg.share()
        assert pump(lambda: dlg.table.rowCount() == 2)
        assert cloud.calls[-1] == ("share", "77", "222", False)
        assert "Invitation sent to Marko" in dlg.status.text() and "Shared with 2 other accounts." in dlg.status.text()
        assert [dlg.table.item(1, c).text() for c in range(1, 4)] == ["Marko", "222", "Control · invited"]
        # stopping: asks first, "No" sends nothing
        dlg.table.selectRow(1)
        assert dlg.stop_btn.isEnabled()
        QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.No)[1])
        dlg.stop()
        assert "Thermometer" in asked[0] and "Marko" in asked[0] and not any(c[0] == "unshare" for c in cloud.calls)
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        dlg.stop()
        assert pump(lambda: dlg.table.rowCount() == 1 and "No longer shared with Marko" in dlg.status.text())
        assert cloud.calls[-1] == ("unshare", "77", "222", 8)
        # a refusal of the cloud is shown and nothing changes
        cloud.fail = "Xiaomi cloud error -1: no permission"
        dlg.table.selectRow(0)
        dlg.stop()
        assert pump(lambda: "wasn't stopped" in dlg.status.text())
        assert "no permission" in dlg.status.text() and dlg.table.rowCount() == 1 and dlg.stop_btn.isEnabled()
        sharing.run_dialog = answer({"userid": "222", "name": "Marko"}, True)
        dlg.share()
        assert pump(lambda: "wasn't shared" in dlg.status.text())
        assert cloud.calls[-1] == ("share", "77", "222", True) and dlg.share_btn.isEnabled()
        cloud.fail = None
        # the overview of all shared devices can stop a share too
        every = sharing.SharingDialog(main)
        every.show()
        assert pump(lambda: every.table.rowCount() == 1)
        assert not every.share_btn.isVisibleTo(every)
        every.table.selectRow(0)
        every.stop()
        assert pump(lambda: "None of your devices is shared" in every.status.text())
        assert cloud.calls[-1] == ("unshare", "77", "123456", 8)
        every.close()
    finally:
        sharing.run_dialog, QMessageBox.question = old_run, old_q
    # a device that someone else shared with you can't be shared further
    theirs = sharing.SharingDialog(main, "88")
    theirs.show()
    app.processEvents()
    assert "only its owner can share it" in theirs.status.text() and not theirs.share_btn.isEnabled()
    theirs.close()
    dlg.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
