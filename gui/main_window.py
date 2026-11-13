from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from xc import autostart
from xc.camera import RELEASES_PAGE, CameraError, Go2rtc, WebRtcBridge, supports_go2rtc
from xc.catalog import category
from xc.cloud import SERVERS, CloudAuthError, XiaomiCloud, friendly
from xc.config import Config, app_dir
from xc.device import MODES, DeviceController
from xc.miio import discover
from xc.spec import SpecStore

from . import theme
from .controls import muted, run_dialog
from .device_page import DevicePage
from .dialogs import (DeviceDialog, ImportDialog, ManageDialog, RemoveDialog, RenameDialog, RoomDialog, ScanDialog,
                      SettingsDialog)
from .home import HomePage
from .hub import Hub
from .icon import app_icon
from .login_dialog import LoginDialog
from .net import broadcast_targets, local_networks
from .pair_dialog import QrPairDialog
from .tray import Tray
from .workers import run_task

log = logging.getLogger(__name__)


class _SessionRelay(QObject):
    """Carries a silently renewed Mi account session from a worker thread to the UI thread."""
    renewed = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config | None = None):
        super().__init__()
        self.setWindowTitle("XiControl")
        self.setWindowIcon(app_icon())
        self.resize(1140, 780)
        self.setMinimumSize(760, 560)
        self.cfg = cfg or Config()
        self._quitting = False
        self._expired_cloud = None      # the session the Mi cloud rejected (sign-in question pending)
        self._auth_tray_told = False
        self.specs = SpecStore(os.path.join(app_dir(), "spec_cache"))
        self.bridge = WebRtcBridge(self._camera_for_bridge)   # WebRTC signalling for go2rtc
        self.go2rtc = Go2rtc(os.path.join(app_dir(), "go2rtc"), self.bridge)
        self._refresh_networks()
        self._net_timer = QTimer(self)                        # the PC may change networks (laptop, VPN)
        self._net_timer.timeout.connect(self._refresh_networks)
        self._net_timer.start(60_000)
        self._session_relay = _SessionRelay(self)
        self._session_relay.renewed.connect(self._session_renewed)
        self.cloud: XiaomiCloud | None = None
        if self.cfg.data.get("cloud_session"):
            self._attach_cloud(XiaomiCloud(self.cfg.data.get("server", "de"), self.cfg.data["cloud_session"]))
        self.controllers: dict[str, DeviceController] = {}
        DeviceController.mode_for_all = self.cfg.data.get("connection") or None
        self.page: DevicePage | None = None
        self._auth_warned = False
        self._auth_pending = False

        self.firmware: dict[str, dict] = {}   # did -> firmware state from the Mi cloud (see check_firmware_all)
        self._fw_timer = QTimer(self)
        self._fw_timer.timeout.connect(self.check_firmware_all)
        self._fw_timer.start(6 * 3600 * 1000)
        self.hub = Hub(self.cfg, self.specs, self.controller, self)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.home = HomePage(self)
        self.home.open_device.connect(self.open_device)
        self.home.power.connect(self.quick_power)
        self.stack.addWidget(self.home)
        self.hub.changed.connect(self.home.refresh_tile)
        self.hub.auth_expired.connect(self.cloud_expired)
        self.tray = Tray(self)
        self.hub.changed.connect(self.tray.device_changed)
        if self.tray.available:  # windows may all be hidden while XiControl keeps running in the tray
            QApplication.instance().setQuitOnLastWindowClosed(False)
        try:
            autostart.refresh()
        except OSError as exc:
            log.warning("could not update the start-with-Windows entry: %s", exc)

        self._toast = QLabel(self)
        self._toast.setObjectName("Toast")
        self._toast.hide()
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(self._toast.hide)
        try:
            QApplication.styleHints().colorSchemeChanged.connect(self._system_theme_changed)
        except AttributeError:  # Qt < 6.5
            pass

        self.refresh_home()
        QTimer.singleShot(300, self.hub.start)
        QTimer.singleShot(500, self.background_scan)
        QTimer.singleShot(700, self._startup_notes)
        QTimer.singleShot(20_000, self.check_firmware_all)
        QTimer.singleShot(3_000, self.load_homes)
        self.messages: list[dict] = []     # the Mi account's notifications (see load_notifications)
        self._msg_timer = QTimer(self)
        self._msg_timer.timeout.connect(self.load_notifications)
        self._msg_timer.start(5 * 60 * 1000)
        QTimer.singleShot(6_000, self.load_notifications)

    # -- small helpers ------------------------------------------------------------------------
    def toast(self, text: str, ms: int = 2200):
        self._toast.setText(text)
        self._toast.adjustSize()
        self._place_toast()
        self._toast.show()
        self._toast.raise_()
        self._toast_timer.start(ms)

    def _place_toast(self):
        t = self._toast
        t.move((self.width() - t.width()) // 2, self.height() - t.height() - 28)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_toast()

    def bring_to_front(self):
        """From the tray, a notification, or when XiControl is started a second time."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def showEvent(self, e):
        super().showEvent(e)
        # things that waited while XiControl ran hidden in the tray
        QTimer.singleShot(400, self._startup_notes)
        QTimer.singleShot(500, self._ask_sign_in)

    def _startup_notes(self):
        if self.cfg.warning and self.isVisible():
            text, self.cfg.warning = self.cfg.warning, ""
            QMessageBox.warning(self, "XiControl", text)

    @property
    def close_to_tray(self) -> bool:
        return self.tray.available and bool(self.cfg.data.get("close_to_tray", True))

    @property
    def signed_in(self) -> bool:
        return bool(self.cloud and self.cloud.logged_in)

    def build_welcome(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(36, 10, 36, 36)
        outer.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        card = QFrame()
        card.setObjectName("Card")
        card.setMaximumWidth(560)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(34, 30, 34, 30)
        lay.setSpacing(14)
        t = QLabel("Add your Xiaomi devices")
        t.setObjectName("H2")
        lay.addWidget(t)
        lay.addWidget(muted("Sign in with your Mi account and your devices and their keys are imported "
                            "automatically. Wi-Fi devices are then controlled directly on your network. "
                            "Brand-new devices are set up in the Xiaomi Home app on your phone first."))
        b = QPushButton("Sign in with Mi account")
        b.setObjectName("Primary")
        b.setMinimumHeight(44)
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(self.account_clicked)
        lay.addWidget(b)
        row = QHBoxLayout()
        for text, slot in (("Scan local network", self.scan), ("Add by IP and token", self.add_device)):
            x = QPushButton(text)
            x.setObjectName("Link")
            x.setCursor(Qt.PointingHandCursor)
            x.clicked.connect(slot)
            row.addWidget(x)
        row.addStretch(1)
        lay.addLayout(row)
        outer.addWidget(card)
        return page

    # -- navigation ------------------------------------------------------------------------------
    def controller(self, info: dict) -> DeviceController:
        did = str(info["did"])
        c = self.controllers.get(did)
        if c is None or c.info is not info:
            c = DeviceController(info, self.cloud, self._hub_controller)
            self.controllers[did] = c
        return c

    def _hub_controller(self, info: dict) -> DeviceController | None:
        """The controller of the gateway a Zigbee device hangs on, if that gateway is in the device list."""
        parent = self.cfg.device(info["parent_id"]) if info.get("parent_id") else None
        return self.controller(parent) if parent else None

    # -- local network or Mi cloud ---------------------------------------------------------------
    def link_info(self, did) -> tuple[str, str]:
        """("local" | "cloud" | "", explanation): how this device is reached right now - for its tile."""
        info = self.cfg.device(did)
        if not info:
            return "", ""
        ctl, st = self.controller(info), self.hub.state(did)
        kind = st.transport if (st.online and st.transport) else ctl.route()
        if not kind:
            return "", ""
        hub = ctl.hub()
        if kind == "local":
            text = "Local network - XiControl talks to this device directly, without Xiaomi's servers"
            if hub is not None:
                text = (f"Local network - through its hub “{hub.info.get('name') or hub.did}”, without "
                        "Xiaomi's servers")
        else:
            text = "Mi cloud - through Xiaomi's servers"
            if ctl.mode == "auto" and not ctl.local_capable():
                text += " (this kind of device has no connection of its own on the local network)"
            elif ctl.mode == "auto" and ctl.local_possible():
                text += " (the local network didn't answer - it is tried again later)"
            elif ctl.mode == "auto" and ctl.elsewhere():
                text += " (the device is on another network)"
        mode = MODES[ctl.mode] + (" - chosen for all devices in Settings" if DeviceController.mode_for_all else "")
        return kind, f"{text}\nConnection: {mode}"

    def set_device_mode(self, dids, mode: str):
        """Automatic / local network only / Mi cloud only, for one or several devices."""
        changed = []
        for did in dids:
            info = self.cfg.device(did)
            if info and mode in MODES and info.get("mode", "auto") != mode:
                info["mode"] = mode
                changed.append(str(did))
        if not changed:
            return
        self.cfg.save()
        self._connection_changed(changed)
        self.toast(f"{MODES[mode]}: {len(changed)} device{'s' if len(changed) != 1 else ''}")

    def _connection_changed(self, dids=None):
        """The way devices are reached was changed (all of them if dids is None): what was learnt about
        the old way is forgotten and they are read again."""
        from xc.catalog import legacy_profile
        for d in self.cfg.devices:
            did = str(d["did"])
            if dids is not None and did not in dids:
                continue
            ctl = self.controllers.get(did)
            if ctl is not None:
                ctl.forget_route()
            st = self.hub.state(did)
            st.transport, st.fails, st.next_poll = "", 0, 0.0
            if legacy_profile(d.get("model", "")) and st.spec_state in ("ready", "missing"):
                st.spec_state, st.summary = "none", None   # legacy panel on the LAN, standard one in the cloud
        reopen = self.page.did if self.page else None
        self.refresh_home()
        if reopen and (dids is None or reopen in dids):
            self.open_device(reopen)
        self.hub.poll_all()

    def refresh_home(self):
        self.home.set_menu(self.signed_in)
        self.home.rebuild()

    def open_device(self, did):
        info = self.cfg.device(did)
        if not info:
            return
        self.close_device()
        self.page = DevicePage(info, self.controller(info), self.hub, self.specs, self)
        self.page.back.connect(self.go_home)
        self.page.edit_requested.connect(self.edit_device)
        self.page.remove_requested.connect(self.remove_device)
        self.page.cloud_expired.connect(self.cloud_expired)
        self.stack.addWidget(self.page)
        self.stack.setCurrentWidget(self.page)
        if self.update_text(did):
            QTimer.singleShot(1200, lambda p=self.page: p is self.page and p.firmware_changed())

    def close_device(self):
        if self.page:
            self.page.stop()
            self.stack.removeWidget(self.page)
            self.page.deleteLater()
            self.page = None

    def go_home(self):
        self.close_device()
        self.stack.setCurrentWidget(self.home)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Back) and self.page:
            self.go_home()
            return
        super().keyPressEvent(e)

    def quick_power(self, did, on):
        name = (self.cfg.device(did) or {}).get("name", "Device")
        self.hub.set_power(did, on, done=lambda: self.toast(f"{name} switched {'on' if on else 'off'}"),
                           error=lambda e: self.toast(f"Couldn't switch {name}: {friendly(e)}", 5000))

    # -- account ------------------------------------------------------------------------------------
    def _attach_cloud(self, cloud: XiaomiCloud | None):
        if cloud is not None:
            cloud.on_session_changed = self._session_relay.renewed.emit
        self.cloud = cloud
        for c in getattr(self, "controllers", {}).values():
            c.cloud = cloud

    def _session_renewed(self, session: dict):
        if self.cloud is not None:
            self.cfg.data["cloud_session"] = session
            self.cfg.save()

    def _save_session(self):
        self.cfg.data["server"] = self.cloud.server
        self.cfg.data["cloud_session"] = self.cloud.export_session()
        self.cfg.save()

    def account_clicked(self):
        if self.signed_in:
            self.sync_from_cloud()
        else:
            self.sign_in()

    def sign_in(self):
        dlg = LoginDialog(self.cfg.data.get("server", "de"), self)
        if run_dialog(dlg) != LoginDialog.Accepted or not dlg.cloud:
            return
        self._attach_cloud(dlg.cloud)
        self._auth_warned = self._auth_tray_told = False
        self._expired_cloud = None
        self._save_session()
        self.home.set_menu(True)
        self.hub.poll_all()
        self.sync_from_cloud()

    def sync_from_cloud(self):
        self.toast("Getting your devices from Xiaomi…", 60000)
        run_task(self.cloud.get_devices, on_done=self._cloud_devices, on_error=self._cloud_failed, owner=self)

    def _cloud_failed(self, exc):
        self._toast.hide()
        if isinstance(exc, CloudAuthError):
            self.cloud_expired()
            return
        QMessageBox.warning(self, "Mi account", f"Couldn't get the device list:\n{friendly(exc)}")

    def _cloud_devices(self, devices, searched=False):
        self._toast.hide()
        if not devices:
            if not searched:
                self.toast("No devices in this region - looking in the other regions…", 180000)
                run_task(self.cloud.find_regions, on_done=self._regions_found, on_error=self._cloud_failed,
                         owner=self)
                return
            QMessageBox.information(self, "Mi account",
                                    "No devices were found on this account. Add them in the Xiaomi Home app on "
                                    "your phone first, then sync again.")
            return
        existing = {str(d.get("did")) for d in self.cfg.devices}
        dlg = ImportDialog(devices, existing, self)
        if run_dialog(dlg) != ImportDialog.Accepted:
            return
        self._import(dlg.selected())
        self.toast("Devices imported")

    def _regions_found(self, found: dict):
        self._toast.hide()
        if not found:
            self._cloud_devices([], searched=True)
            return
        server, devices = max(found.items(), key=lambda kv: len(kv[1]))
        here = SERVERS.get(self.cloud.server, self.cloud.server)
        there = SERVERS.get(server, server)
        if QMessageBox.question(self, "Mi account",
                                f"Your devices are in the {there} region ({len(devices)} devices), not in {here}.\n\n"
                                f"Switch XiControl to {there}?") != QMessageBox.Yes:
            return
        self.cloud.server = server
        self._save_session()
        self._cloud_devices(devices, searched=True)

    def _import(self, devices: list[dict]):
        for d in devices:
            old = self.cfg.device(d["did"])
            d = dict(d)
            d.pop("online", None)
            if old and old.get("renamed"):
                d.pop("name", None)
            if old and old.get("room_set"):
                d.pop("room", None)
            if not old and category(d.get("model", ""))[0] == "camera":
                d["mode"] = "cloud"  # most cameras don't answer on the LAN
            if old and old.get("model") != d.get("model"):
                self.controllers.pop(str(d["did"]), None)
                self.hub.forget(d["did"])
            self.cfg.upsert(d)
        self.cfg.save()
        self.refresh_home()
        self.hub.poll_all()

    def cloud_expired(self):
        """The Mi cloud rejected the session and it couldn't be renewed silently."""
        if self.cloud is not None and self._expired_cloud is not self.cloud:
            log.warning("Mi account session expired and could not be renewed")
            self._expired_cloud = self.cloud
        self._ask_sign_in()

    def _ask_sign_in(self):
        if self.cloud is None or self._expired_cloud is not self.cloud or self._auth_warned or self._auth_pending:
            return  # nothing pending, or the user has signed in again meanwhile
        if not self.isVisible():  # running in the tray: notify once, ask when the window opens
            if not self._auth_tray_told:
                self._auth_tray_told = True
                self.tray.sign_in_needed()
            return
        if QApplication.activeModalWidget() is not None:  # don't stack a question on top of a dialog
            self._auth_pending = True
            QTimer.singleShot(3000, self._retry_ask_sign_in)
            return
        self._auth_warned = True
        if QMessageBox.question(self, "Mi account",
                                "Your Mi account session has ended and couldn't be renewed automatically. "
                                "Devices on your local network keep working, but cloud-only devices need you to "
                                "sign in again.\n\nSign in now?") == QMessageBox.Yes:
            self.sign_in()

    def _retry_ask_sign_in(self):
        self._auth_pending = False
        self._ask_sign_in()

    # -- cameras -----------------------------------------------------------------------------------------
    def _refresh_networks(self):
        DeviceController.local_networks = local_networks()

    def camera_list(self) -> list[dict]:
        """Every camera of the device list, as go2rtc needs them."""
        return [{"did": str(d["did"]), "ip": d.get("ip") or "", "model": d.get("model") or ""}
                for d in self.cfg.devices
                if category(d.get("model", ""))[0] == "camera" or supports_go2rtc(d.get("model"))]

    def _camera_for_bridge(self, did):
        """Called from the WebRTC bridge's thread when go2rtc asks for a camera."""
        info = self.cfg.device(did)
        if info is None:
            raise CameraError("This camera is not in the device list any more.")
        if not self.signed_in:
            raise CameraError("Sign in to your Mi account first.")
        model = info.get("model", "")
        return self.cloud, model, self.specs.get(model)

    def install_go2rtc(self, then=None, declined=None):
        if QMessageBox.question(
                self, "Live video",
                "Live video from Xiaomi cameras uses go2rtc, a free open-source helper "
                "(github.com/AlexxIT/go2rtc, about 7 MB).\n\nXiControl downloads it from GitHub, checks its "
                "checksum and runs it on this PC only (address 127.0.0.1).\n\nDownload it now?") != QMessageBox.Yes:
            if declined:
                declined()
            return
        self.toast("Downloading go2rtc…", 180000)

        def done(_path):
            self.toast("go2rtc installed")
            if then:
                then()

        def failed(exc):
            self._toast.hide()
            QMessageBox.warning(self, "Live video",
                                f"Couldn't install go2rtc:\n{friendly(exc)}\n\nYou can also download "
                                f"go2rtc_win64.zip yourself from {RELEASES_PAGE}\nand put go2rtc.exe into "
                                f"{self.go2rtc.folder}")

        run_task(self.go2rtc.download, on_done=done, on_error=failed, owner=self)

    # -- add / edit / remove -------------------------------------------------------------------------
    def add_device(self, ip=None):
        dlg = DeviceDialog({"ip": ip} if isinstance(ip, str) else None, self)
        if isinstance(ip, str):
            dlg.setWindowTitle("Add device")
        if run_dialog(dlg) != DeviceDialog.Accepted:
            return
        info = dlg.result_info()
        info.pop("renamed", None)
        saved = self.cfg.upsert(info)
        self.cfg.save()
        self.refresh_home()
        self.hub.poll_all()
        self.open_device(str(saved["did"]))

    def on_account(self, info: dict) -> bool:
        """Is this a device of the signed-in Mi account (not one added by hand)?"""
        did = str(info.get("did", ""))
        return self.signed_in and bool(did) and not did.startswith("manual-")

    def edit_device(self, did):
        info = self.cfg.device(did)
        if not info:
            return
        dlg = DeviceDialog(info, self, can_sync_name=self.on_account(info) and not info.get("shared"))
        if run_dialog(dlg) != DeviceDialog.Accepted:
            return
        new = dlg.result_info()
        sync_name = dlg.name_to_sync()
        if sync_name:
            run_task(self.cloud.rename_device, str(did), sync_name,
                     on_done=lambda _r: self.toast("Renamed in the Xiaomi Home app too"),
                     on_error=lambda e: QMessageBox.warning(
                         self, "Rename", f"The name was changed in XiControl, but not on the Mi account:\n"
                                         f"{friendly(e)}"), owner=self)
        model_changed = new.get("model") != info.get("model")
        if str(new["did"]) != str(did):  # a manual device got its real ID from Detect
            self.cfg.remove(did)
            self.controllers.pop(str(did), None)
            self.hub.forget(did)
            info = self.cfg.upsert(new)
        else:
            info.clear()
            info.update(new)
        if model_changed:
            self.controllers.pop(str(info["did"]), None)
            self.hub.forget(info["did"])
        self.cfg.save()
        self.refresh_home()
        self.open_device(str(info["did"]))

    def remove_device(self, did, account=False):
        info = self.cfg.device(did) or {}
        name = info.get("name", did)
        if not self.on_account(info):
            why = ("Sign in to your Mi account to be able to delete devices from it." if not self.signed_in
                   else "This device was added by hand - it is not on your Mi account.")
        elif info.get("shared"):
            why = "This device was shared with you by another Mi account - only its owner can delete it."
        else:
            why = ""
        dlg = RemoveDialog(name, not why, why, self, account_first=account)
        if run_dialog(dlg) != RemoveDialog.Accepted:
            return
        if not dlg.delete_from_account:
            self._forget_devices([did])
            return
        self.toast(f"Deleting {name} from your Mi account…", 60000)

        def done(was_there):
            self._forget_devices([did])
            self.toast(f"{name} was deleted from your Mi account" if was_there else
                       f"{name} was not on your Mi account any more - removed from XiControl", 5000)

        def failed(exc):
            self._toast.hide()
            if isinstance(exc, CloudAuthError):
                self.cloud_expired()
                return
            QMessageBox.warning(self, "Delete device",
                                f"“{name}” was NOT deleted from your Mi account:\n{friendly(exc)}\n\n"
                                "Nothing was changed in XiControl either.")

        run_task(self.cloud.unbind_device, str(did), info.get("pid"), on_done=done, on_error=failed, owner=self)

    def _forget_devices(self, dids):
        """Removes devices from this app (not from the Mi account)."""
        for did in dids:
            self.cfg.remove(did)
            self.controllers.pop(str(did), None)
            self.hub.forget(did)
            self.tray.alerts.forget(did)
        self.cfg.save()
        if self.page and self.page.did in {str(d) for d in dids}:
            self.go_home()
        self.refresh_home()

    def manage_devices(self):
        def status(did):
            st = self.hub.state(did)
            return "Offline" if st.online is False else "Online" if st.online else "Needs sign-in" \
                if st.needs_cloud else "…"
        from .firmware import version_text
        dlg = ManageDialog(self.cfg.devices, status, self, firmware_for=lambda did: version_text(self.firmware.get(did)),
                           can_delete_account=self.signed_in,
                           mode_for=lambda did: (self.cfg.device(did) or {}).get("mode") or "auto",
                           set_mode=self.set_device_mode)
        if run_dialog(dlg) != ManageDialog.Accepted:
            return
        dids = dlg.selected()
        if not dids:
            return
        if dlg.delete_from_account:
            if QMessageBox.warning(
                    self, "Delete from my Mi account",
                    f"Delete {len(dids)} device(s) from your Mi account?\n\nThey disappear from the Xiaomi Home app "
                    "too, their automations stop, and each has to be reset and set up again to come back. This "
                    "can't be undone.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
                self._delete_from_account(dids)
            return
        if QMessageBox.question(self, "Manage devices", f"Remove {len(dids)} device(s) from XiControl?\n"
                                                        "They stay on your Mi account.") == QMessageBox.Yes:
            self._forget_devices(dids)
            self.toast(f"Removed {len(dids)} device(s) from XiControl")

    def _delete_from_account(self, dids):
        """Deletes several devices from the Mi account, one after the other, each checked afterwards."""
        owned = [d for d in dids if self.on_account(self.cfg.device(d) or {}) and not (self.cfg.device(d) or {}).get("shared")]
        others = [d for d in dids if d not in owned]
        pids = {d: (self.cfg.device(d) or {}).get("pid") for d in owned}
        cloud = self.cloud
        self.toast(f"Deleting {len(owned)} device(s) from your Mi account…", 120000)

        def work():
            done, failed = [], []
            for did in owned:
                try:
                    cloud.unbind_device(did, pids.get(did))
                    done.append(did)
                except CloudAuthError:
                    raise
                except Exception as exc:  # noqa: BLE001 - reported below, the others still go
                    failed.append((did, friendly(exc)))
            return done, failed

        def finished(result):
            done, failed = result
            self._toast.hide()
            self._forget_devices(done + others)
            text = f"Deleted {len(done)} device(s) from your Mi account."
            if others:
                text += f"\n{len(others)} device(s) not on your account were removed from XiControl only."
            if failed:
                names = "\n".join(f"• {(self.cfg.device(d) or {}).get('name', d)}: {e}" for d, e in failed[:8])
                QMessageBox.warning(self, "Delete from my Mi account", text + f"\n\nNOT deleted:\n{names}")
            else:
                self.toast(text.splitlines()[0], 5000)

        def failed(exc):
            self._toast.hide()
            if isinstance(exc, CloudAuthError):
                self.cloud_expired()
                return
            QMessageBox.warning(self, "Delete from my Mi account", f"Nothing was deleted:\n{friendly(exc)}")

        run_task(work, on_done=finished, on_error=failed, owner=self)

    def pair_camera(self):
        """Adds a brand-new camera to the Mi account with a QR code, then imports it."""
        if not self.signed_in:
            if QMessageBox.question(self, "Add a new camera",
                                    "A new camera is added to your Mi account, so you have to be signed in.\n\n"
                                    "Sign in now?") == QMessageBox.Yes:
                self.sign_in()
            return
        dlg = QrPairDialog(self.cloud, self.cfg.data.get("pair_ssid", ""), self)
        ok = run_dialog(dlg) == QrPairDialog.Accepted
        if dlg.ssid_used != self.cfg.data.get("pair_ssid", ""):
            self.cfg.data["pair_ssid"] = dlg.ssid_used  # the Wi-Fi name only, never the password
            self.cfg.save()
        if not ok or not dlg.new_did:
            return
        new_did = dlg.new_did
        self.toast("Getting the new camera from Xiaomi…", 60000)

        def got(devices):
            self._toast.hide()
            mine = [d for d in devices if str(d["did"]) == new_did]
            if not mine:
                QMessageBox.information(self, "Add a new camera", "The camera was added to your Mi account. It "
                                        "will appear here after + → Sync devices from Mi account.")
                return
            self._import(mine)
            self.open_device(new_did)

        run_task(self.cloud.get_devices, on_done=got, on_error=self._cloud_failed, owner=self)

    # -- right-click menu, rename, room ----------------------------------------------------------------
    def tile_menu(self, did, pos):
        from .device_menu import build_device_menu
        menu = build_device_menu(self, did, self)
        menu.exec(pos)
        menu.deleteLater()

    def run_device_command(self, did, command):
        from xc.actions import run_step, step_from
        info = self.cfg.device(did)
        if not info:
            return
        step = step_from(did, info.get("name") or did, command)
        run_task(run_step, self.controller(info), step,
                 on_done=lambda _r: (self.toast(f"{command['label']} ✓"),
                                     QTimer.singleShot(800, lambda: self.hub.poll(did, force=True))),
                 on_error=lambda e: QMessageBox.warning(self, info.get("name") or "Device",
                                                        f"{command['label']} failed:\n{friendly(e)}"), owner=self)

    def set_tray(self, did, on):
        info = self.cfg.device(did)
        if not info:
            return
        if on:
            info["tray"] = True
        else:
            info.pop("tray", None)
        self.cfg.save()
        self.toast("Added to the tray menu" if on else "Removed from the tray menu")

    def rename_device(self, did):
        info = self.cfg.device(did)
        if not info:
            return
        dlg = RenameDialog(info.get("name") or "", self.on_account(info) and not info.get("shared"), self)
        if run_dialog(dlg) != RenameDialog.Accepted:
            return
        name, sync = dlg.new_name(), dlg.sync_to_account()
        info["name"] = name
        if sync:
            info.pop("renamed", None)
            run_task(self.cloud.rename_device, str(did), name,
                     on_done=lambda _r: self.toast("Renamed in the Xiaomi Home app too"),
                     on_error=lambda e: QMessageBox.warning(
                         self, "Rename", f"The name was changed in XiControl, but not on the Mi account:\n"
                                         f"{friendly(e)}"), owner=self)
        else:
            info["renamed"] = True
        self.cfg.save()
        self.refresh_home()
        if self.page and self.page.did == str(did):
            self.page.title.setText(name)

    def move_device(self, did):
        info = self.cfg.device(did)
        if not info:
            return
        if not self.signed_in or not self.on_account(info) or info.get("shared"):
            self._ask_room(did, None, None)
            return
        self.toast("Getting the rooms of your Mi account…", 20000)

        def got(homes):
            self._toast.hide()
            home = next((h for h in homes if str(did) in h["dids"] or any(str(did) in r["dids"] for r in h["rooms"])),
                        None)
            self._ask_room(did, home["rooms"] if home else None, home)

        run_task(self.cloud.homes, on_done=got,
                 on_error=lambda _e: (self._toast.hide(), self._ask_room(did, None, None)), owner=self)

    def _ask_room(self, did, account_rooms, home):
        info = self.cfg.device(did)
        if not info:
            return
        local = sorted({d.get("room") for d in self.cfg.devices if d.get("room")})
        dlg = RoomDialog(info.get("name") or did, info.get("room") or "", local, account_rooms, self)
        if run_dialog(dlg) != RoomDialog.Accepted:
            return
        room, target = dlg.room_name(), dlg.account_room()
        info["room"] = room
        if target is not None:
            info.pop("room_set", None)
            run_task(self.cloud.move_to_room, str(did), target["id"], home["owner"],
                     on_done=lambda _r: self.toast(f"Moved to “{room}” in the Xiaomi Home app too"),
                     on_error=lambda e: QMessageBox.warning(
                         self, "Move to room", f"The room was changed in XiControl, but not on the Mi account:\n"
                                               f"{friendly(e)}"), owner=self)
        else:
            info["room_set"] = True
        self.cfg.save()
        self.refresh_home()

    # -- notifications ------------------------------------------------------------------------------------
    def load_notifications(self, then=None, failed=None):
        """Reads the notifications of the Mi account (one request) and updates the number on the bell."""
        if not self.signed_in:
            self._update_bell()
            return

        def done(messages):
            self.messages = messages
            self._update_bell()
            if then:
                then()

        def bad(exc):
            if isinstance(exc, CloudAuthError):
                self.cloud_expired()
            if failed:
                failed(exc)

        run_task(self.cloud.messages, on_done=done, on_error=bad, owner=self)

    def all_notifications(self) -> list[dict]:
        """The Mi account's notifications and XiControl's own alerts, newest first; "new" = not seen here yet."""
        seen = self.cfg.data.get("notifications_seen")
        items = []
        for m in self.messages:
            items.append(dict(m, new=m["new"] if seen is None else m["time"] > seen))
        for m in self.tray.log:
            items.append(dict(m, new=m["time"] > (seen or 0)))
        return sorted(items, key=lambda m: -m["time"])

    def _update_bell(self):
        self.home.bell.set_count(sum(1 for m in self.all_notifications() if m["new"]))

    def open_notifications(self):
        from .notifications import NotificationsDialog
        run_dialog(NotificationsDialog(self, self))
        self.cfg.data["notifications_seen"] = time.time()   # what was on the list counts as read from now on
        self.cfg.save()
        self._update_bell()

    def show_sharing(self, did=None):
        from .sharing import SharingDialog
        run_dialog(SharingDialog(self, did, self))

    # -- tile size ----------------------------------------------------------------------------------------
    def tile_size(self) -> str:
        size = self.cfg.data.get("tile_size", "medium")
        return size if size in ("small", "medium", "large") else "medium"

    def set_tile_size(self, size: str):
        self.cfg.data["tile_size"] = size
        self.cfg.save()
        self.refresh_home()

    # -- homes ("families") ------------------------------------------------------------------------------
    def homes(self) -> list[dict]:
        return self.cfg.data.get("homes") or []

    def current_home_id(self) -> str | None:
        hid = self.cfg.data.get("home_id")
        return hid if hid and any(h["id"] == hid for h in self.homes()) else None

    def current_home(self) -> dict | None:
        hid = self.current_home_id()
        return next((h for h in self.homes() if h["id"] == hid), None)

    def set_home(self, home_id):
        self.cfg.data["home_id"] = home_id
        self.cfg.save()
        self.home.room = None
        self.refresh_home()

    def load_homes(self, then=None):
        """Reads the homes and rooms of the Mi account; every device learns which home it is in."""
        if not self.signed_in:
            return

        def done(homes):
            self._store_homes(homes)
            if then:
                then()

        run_task(self.cloud.homes, on_done=done, on_error=self._refresh_failed, owner=self)

    def _store_homes(self, homes):
        home_of, room_of = {}, {}
        for h in homes:
            for did in h["dids"]:
                home_of[did] = h
            for r in h["rooms"]:
                for did in r["dids"]:
                    home_of[did] = h
                    room_of[did] = r["name"]
        changed = self.cfg.data.get("homes") != homes
        self.cfg.data["homes"] = homes
        for d in self.cfg.devices:
            did = str(d["did"])
            h = home_of.get(did)
            if h is not None and (d.get("home_id") != h["id"] or d.get("home") != h["name"]):
                d["home_id"], d["home"] = h["id"], h["name"]
                changed = True
            if did in room_of and not d.get("room_set") and d.get("room") != room_of[did]:
                d["room"] = room_of[did]
                changed = True
        if changed:
            self.cfg.save()
            reopen = self.page.did if self.page else None
            self.refresh_home()
            if reopen and self.page is None:
                self.open_device(reopen)

    def _edit_home(self, title, fn, *args, done_text="", after=None):
        self.toast(f"{title}…", 20000)

        def ok(_r):
            self.toast(done_text or "Saved on your Mi account")
            if after:
                after()
            self.load_homes()

        def bad(exc):
            self._toast.hide()
            if isinstance(exc, CloudAuthError):
                self.cloud_expired()
                return
            QMessageBox.warning(self, title, f"Nothing was changed:\n{friendly(exc)}")

        run_task(fn, *args, on_done=ok, on_error=bad, owner=self)

    def _ask_name(self, title, label, text=""):
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, title, label, text=text)
        name = (name or "").strip()
        return name if ok and name else None

    def rename_home(self, home):
        name = self._ask_name("Rename home", "New name of the home:", home["name"])
        if name and name != home["name"]:
            self._edit_home("Renaming the home", self.cloud.rename_home, home, name,
                            done_text=f"The home is now called “{name}”")

    def add_room(self, home):
        name = self._ask_name("Add room", f"Name of the new room in “{home['name']}”:")
        if name:
            self._edit_home("Adding the room", self.cloud.add_room, home, name, done_text=f"Room “{name}” added")

    def rename_room(self, home, room):
        name = self._ask_name("Rename room", "New name of the room:", room["name"])
        if not name or name == room["name"]:
            return
        old = room["name"]

        def local(_r=None):
            for d in self.cfg.devices:
                if d.get("room") == old and str(d.get("home_id") or "") == home["id"]:
                    d["room"] = name
            self.cfg.save()
            self.refresh_home()

        self._edit_home("Renaming the room", self.cloud.rename_room, home, room, name,
                        done_text=f"The room is now called “{name}”", after=local)

    def delete_room(self, home, room):
        n = len(room.get("dids") or [])
        text = (f"Delete the room “{room['name']}” in “{home['name']}”?\n\n"
                + (f"Its {n} device(s) stay in the home, without a room." if n else "There are no devices in it."))
        if QMessageBox.question(self, "Delete room", text) != QMessageBox.Yes:
            return
        self._edit_home("Deleting the room", self.cloud.delete_room, home, room,
                        done_text=f"Room “{room['name']}” deleted")

    # -- refresh -----------------------------------------------------------------------------------------
    def refresh_all(self):
        """⟳ / F5: read every device now and pick up changes on the Mi account (new devices, names, rooms)."""
        self.toast("Refreshing all devices…")
        self.hub.refresh_now()
        if self.signed_in:
            run_task(self.cloud.get_devices, on_done=self._merge_account, on_error=self._refresh_failed, owner=self)
            self.check_firmware_all()
            self.load_homes()

    def _refresh_failed(self, exc):
        if isinstance(exc, CloudAuthError):
            self.cloud_expired()

    def _merge_account(self, devices: list[dict]):
        """Brings the devices already in XiControl up to date and offers the new ones."""
        changed = False
        for d in devices:
            info = self.cfg.device(d["did"])
            if info is None:
                continue
            fields = ["ip", "token", "pid", "shared", "parent_id", "mac", "home_id", "home"]
            if not info.get("renamed"):
                fields.append("name")
            if not info.get("room_set"):
                fields.append("room")
            for k in fields:
                v = d.get(k)
                if v not in (None, "") and info.get(k) != v and not (k == "ip" and info.get("mode") == "local"):
                    info[k] = v
                    changed = True
        if changed:
            self.cfg.save()
            reopen = self.page.did if self.page else None
            self.refresh_home()
            if reopen and self.page is None:
                self.open_device(reopen)
        known = {str(x["did"]) for x in self.cfg.devices}
        new = [d for d in devices if str(d["did"]) not in known]
        if not new:
            return
        names = "\n".join(f"• {d.get('name') or d['did']}" for d in new[:10]) + ("\n…" if len(new) > 10 else "")
        if QMessageBox.question(self, "New devices", f"{len(new)} new device(s) on your Mi account:\n{names}\n\n"
                                                     "Add them to XiControl?") == QMessageBox.Yes:
            self._import(new)
            self.toast(f"Added {len(new)} device(s)")

    # -- firmware ----------------------------------------------------------------------------------------
    def check_firmware_all(self):
        if not self.signed_in or not self.cfg.devices:
            return
        dids = [str(d["did"]) for d in self.cfg.devices]

        def done(status):
            self.firmware.update(status)
            self.firmware_changed(None)

        run_task(self.cloud.firmware_status, dids, on_done=done, owner=self)

    def update_text(self, did) -> str | None:
        from .firmware import has_update
        fw = self.firmware.get(str(did))
        return f"Firmware update available: {fw.get('curr')} → {fw.get('latest')}" if has_update(fw) else None

    def firmware_changed(self, did):
        for d in ([did] if did else list(self.home.tiles)):
            self.home.refresh_tile(d)
        if self.page and (did is None or self.page.did == str(did)):
            self.page.firmware_changed()

    def show_firmware(self, did, parent=None):
        from .firmware import FirmwareDialog
        run_dialog(FirmwareDialog(self, did, parent or self))

    def show_firmware_list(self):
        from .firmware import FirmwareListDialog
        run_dialog(FirmwareListDialog(self, self))

    # -- actions and scenes ------------------------------------------------------------------------------
    def open_actions(self, did=None, device_filter=None):
        """did: start a new action with this device; device_filter: show only what involves that device."""
        from .actions_window import ActionsDialog
        dlg = ActionsDialog(self, self, device_filter=device_filter)
        if did is not None:
            QTimer.singleShot(0, lambda: dlg.new(did))
        run_dialog(dlg)

    def add_to_action(self, did):
        from PySide6.QtWidgets import QInputDialog

        from .actions_window import ActionEditor
        actions = self.cfg.data.setdefault("actions", [])
        target = None
        if actions:
            names = ["A new action…"] + [a.get("name") or "Action" for a in actions]
            choice, ok = QInputDialog.getItem(self, "Add to an action", "Add this device to:", names, 0, False)
            if not ok:
                return
            if choice != names[0]:
                target = actions[names.index(choice) - 1]
        dlg = ActionEditor(self, target, did, self)
        if run_dialog(dlg) != ActionEditor.Accepted:
            return
        result = dlg.result_action()
        if target is None:
            actions.append(result)
        else:
            actions[actions.index(target)] = result
        self.cfg.save()
        self.actions_changed()
        self.toast(f"Saved “{result['name']}” - ⚡ on the home screen runs it")

    def actions_changed(self):
        self.home.rebuild_actions()

    def run_user_action(self, action: dict):
        from xc.actions import describe_result, run_action
        self.toast(f"Running “{action.get('name')}”…", 30000)

        def done(failed):
            self.toast(describe_result(action, failed), 6000 if failed else 3000)
            for step in action.get("steps") or []:
                if step.get("did"):
                    QTimer.singleShot(900, lambda d=step["did"]: self.hub.poll(d, force=True))

        run_task(run_action, action, self.controller, self.cfg.device, friendly, on_done=done,
                 on_error=lambda e: self.toast(f"“{action.get('name')}” failed: {friendly(e)}", 6000), owner=self)

    def run_scene(self, scene: dict):
        if not self.signed_in:
            return
        self.toast(f"Running the scene “{scene['name']}”…", 15000)
        run_task(self.cloud.run_scene, scene, on_done=lambda _r: self.toast(f"Scene “{scene['name']}” started ✓"),
                 on_error=lambda e: (self._toast.hide(), QMessageBox.warning(
                     self, "Scene", f"The scene “{scene['name']}” didn't run:\n{friendly(e)}")), owner=self)

    # -- scanning -------------------------------------------------------------------------------------
    def scan(self):
        dlg = ScanDialog(self.cfg.devices, self.cloud if self.signed_in else None, self)
        run_dialog(dlg)
        self._apply_ip_updates(dlg.updates)
        if dlg.add_info:
            self._import([dlg.add_info])
            self.open_device(str(dlg.add_info["did"]))
        elif dlg.add_ip:
            self.add_device(dlg.add_ip)

    def _apply_ip_updates(self, updates: dict):
        if not updates:
            return
        for did, ip in updates.items():
            d = self.cfg.device(did)
            if d:
                d["ip"] = ip
        self.cfg.save()
        self.toast(f"Updated the address of {len(updates)} device(s)")

    def background_scan(self):
        """At start-up: find devices on the network and fix IP addresses that changed."""
        if not any(d.get("token") for d in self.cfg.devices):
            return

        def done(found: dict):
            by_id = {v: k for k, v in found.items()}
            updates = {}
            for d in self.cfg.devices:
                try:
                    dev_id = int(str(d.get("did")))
                except ValueError:
                    continue
                if dev_id in by_id and d.get("ip") != by_id[dev_id]:
                    updates[str(d["did"])] = by_id[dev_id]
            self._apply_ip_updates(updates)
            if updates:
                self.hub.poll_all()

        run_task(discover, 3.0, targets=broadcast_targets(), on_done=done, owner=self)

    # -- settings ---------------------------------------------------------------------------------------
    def settings(self):
        dlg = SettingsDialog(self.cfg, self.cloud if self.signed_in else None, app_dir(), self)
        if run_dialog(dlg) != SettingsDialog.Accepted:
            if dlg.sign_in_requested:
                self.sign_in()
            return
        new_server = dlg.server.currentData()
        self.cfg.data["server"] = new_server
        self.cfg.data["refresh_seconds"] = dlg.refresh.value()
        self.hub.set_interval(dlg.refresh.value())
        self.cfg.data["close_to_tray"] = dlg.close_to_tray.isChecked()
        self.cfg.data["notifications"] = dlg.notifications.isChecked()
        connection = dlg.connection.currentData() or ""
        connection_changed = connection != (self.cfg.data.get("connection") or "")
        self.cfg.data["connection"] = connection
        DeviceController.mode_for_all = connection or None
        if dlg.autostart.isEnabled() and dlg.autostart.isChecked() != autostart.is_enabled():
            try:
                autostart.set_enabled(dlg.autostart.isChecked())
            except OSError as exc:
                QMessageBox.warning(self, "Start with Windows", f"Windows didn't allow the change:\n{exc}")
        new_theme = dlg.theme.currentData()
        resync = False
        if dlg.sign_out:
            self.go2rtc.stop()
            self._attach_cloud(None)
            self.cfg.data["cloud_session"] = None
            self.home.set_menu(False)
            self.hub.poll_all()
        elif self.signed_in and self.cloud.server != new_server:
            self.cloud.server = new_server  # the same session works in every region
            self.cfg.data["cloud_session"] = self.cloud.export_session()
            resync = True
        self.cfg.save()
        if connection_changed:
            self._connection_changed()
        if new_theme != self.cfg.data.get("theme", "light"):
            self.set_theme(new_theme)
        if resync and QMessageBox.question(
                self, "Mi account region",
                f"Get your device list from the {SERVERS.get(new_server, new_server)} region now?") == QMessageBox.Yes:
            self.sync_from_cloud()

    def set_theme(self, mode: str):
        """mode: light / dark / system. Rebuilds the visible screens in the new colours."""
        self.cfg.data["theme"] = mode
        self.cfg.save()
        theme.apply(QApplication.instance(), mode)
        reopen = self.page.did if self.page else None
        self.refresh_home()
        if reopen:
            self.open_device(reopen)

    def _system_theme_changed(self, *_):
        if self.cfg.data.get("theme") == "system":
            self.set_theme("system")

    def toggle_theme(self):
        self.set_theme("light" if theme.MODE == "dark" else "dark")

    def closeEvent(self, e):
        if not self._quitting and self.close_to_tray:
            e.ignore()
            self.go_home()  # the background poll takes over the open device (and its alerts)
            self.hide()
            self.tray.first_hide_hint()
            return
        self._shutdown()
        super().closeEvent(e)
        QTimer.singleShot(0, QApplication.quit)

    def quit_app(self):
        """Tray menu → Quit: really exit, also when the window is hidden."""
        self._quitting = True
        self._shutdown()
        QApplication.quit()

    def _shutdown(self):
        if self.page:
            self.page.stop()
        for did in list(self.bridge.sessions):  # tell (battery) cameras right away that nobody is watching
            try:
                self.bridge.close_session(did, timeout=4)
            except Exception:  # noqa: BLE001 - quitting must not fail because a camera is unreachable
                pass
        self.go2rtc.stop()
        self.bridge.stop()
        self.cfg.save()
        self.tray.hide()
