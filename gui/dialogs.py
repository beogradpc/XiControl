"""Import, add/edit, scan and settings dialogs."""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from xc import autostart, miio
from xc.catalog import category
from xc.cloud import SERVERS
from xc.device import MODES, DeviceController

from .controls import muted
from .theme import no_arrows
from .workers import run_task


def _item(text, data=None, checkable=False, checked=False):
    it = QTableWidgetItem(str(text or ""))
    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | (Qt.ItemIsUserCheckable if checkable else Qt.NoItemFlags))
    if checkable:
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    if data is not None:
        it.setData(Qt.UserRole, data)
    return it


def _table(headers):
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().hide()
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setShowGrid(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    t.horizontalHeader().setStretchLastSection(True)
    return t


class ImportDialog(QDialog):
    """Pick which devices from the Mi account to add."""

    def __init__(self, devices: list[dict], existing: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import devices from your Mi account")
        self.resize(860, 520)
        self.devices = devices
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        t = QLabel(f"Found {len(devices)} devices")
        t.setObjectName("H2")
        lay.addWidget(t)
        lay.addWidget(muted("“LAN + cloud” devices are controlled directly over your network. "
                            "“Cloud only” devices (Bluetooth/Zigbee devices behind a gateway, some cameras "
                            "and speakers) go through Xiaomi's servers."))
        self.table = _table(["", "Name", "Room", "Model", "IP address", "Control", "Status"])
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        for d in devices:
            r = self.table.rowCount()
            self.table.insertRow(r)
            local = bool(d.get("ip") and d.get("token")) and not str(d["did"]).startswith(("blt.", "lumi."))
            kind, icon, _l = category(d.get("model", ""))
            self.table.setItem(r, 0, _item("", d["did"], checkable=True, checked=True))
            self.table.setItem(r, 1, _item(f"{icon}  {d['name']}"))
            self.table.setItem(r, 2, _item(d.get("room")))
            self.table.setItem(r, 3, _item(d.get("model")))
            self.table.setItem(r, 4, _item(d.get("ip")))
            control = "Cloud (camera)" if kind == "camera" and str(d["did"]) not in existing else \
                ("LAN + cloud" if local else "Cloud only")
            self.table.setItem(r, 5, _item(control))
            status = "Already added" if str(d["did"]) in existing else ("Online" if d.get("online") else "Offline")
            self.table.setItem(r, 6, _item(status))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, state in (("Select all", True), ("Select none", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._check_all(s))
            row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Import selected")
        bb.button(QDialogButtonBox.Ok).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

    def _check_all(self, on):
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def selected(self) -> list[dict]:
        chosen = {self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())
                  if self.table.item(r, 0).checkState() == Qt.Checked}
        return [d for d in self.devices if d["did"] in chosen]


class DeviceDialog(QDialog):
    """Add a device by IP + token, or edit an existing one."""

    def __init__(self, info: dict | None = None, parent=None, can_sync_name: bool = False):
        """can_sync_name: the device is on the signed-in Mi account, so a new name can be sent there too."""
        super().__init__(parent)
        self.setWindowTitle("Edit device" if info else "Add device")
        self.setMinimumWidth(520)
        self.info = dict(info or {})
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.name = QLineEdit(self.info.get("name", ""))
        self.ip = QLineEdit(self.info.get("ip", ""))
        self.ip.setPlaceholderText("e.g. 192.168.1.50")
        self.token = QLineEdit(self.info.get("token", ""))
        self.token.setPlaceholderText("32 hex characters")
        self.token.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        mrow = QHBoxLayout()
        self.model = QLineEdit(self.info.get("model", ""))
        self.model.setPlaceholderText("e.g. zhimi.airpurifier.mb4 - leave empty and press Detect")
        det = QPushButton("Detect")
        det.setToolTip("Ask the device for its model over the network (needs IP + token)")
        det.clicked.connect(self.detect)
        mrow.addWidget(self.model, 1)
        mrow.addWidget(det)
        self.sync_name = QCheckBox("Also rename it in the Xiaomi Home app")
        self.sync_name.setToolTip("Sends the new name to your Mi account, so the phone app shows it too")
        self.sync_name.setVisible(bool(info) and can_sync_name)
        self.mode = QComboBox()
        for code, text in (("auto", "Automatic - local network if it is on yours, else Mi cloud"),
                           ("local", "Local network only (also for a network reached through a VPN)"),
                           ("cloud", "Mi cloud only")):
            self.mode.addItem(text, code)
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.info.get("mode", "auto"))))
        self.rtsp = QLineEdit(self.info.get("rtsp_url", ""))
        self.rtsp.setPlaceholderText("Cameras only, e.g. rtsp://192.168.1.60:554/live (usually not needed)")
        self.rtsp.setEchoMode(QLineEdit.PasswordEchoOnEdit if "@" in self.rtsp.text() else QLineEdit.Normal)
        form.addRow("Name", self.name)
        form.addRow("", self.sync_name)
        form.addRow("IP address", self.ip)
        form.addRow("Token", self.token)
        form.addRow("Model", mrow)
        form.addRow("Connection", self.mode)
        if DeviceController.mode_for_all:
            form.addRow("", muted(f"Settings → Connection is “{MODES[DeviceController.mode_for_all]}” for all "
                                  "devices right now. This choice counts again once that is set back to "
                                  "“As set for each device”."))
        form.addRow("RTSP stream", self.rtsp)
        lay.addLayout(form)
        self.status = muted("")
        lay.addWidget(self.status)
        if not info:
            lay.addWidget(muted("Tip: signing in with your Mi account fills all of this in automatically."))
        row = QHBoxLayout()
        test = QPushButton("Test connection")
        test.clicked.connect(self.detect)
        row.addWidget(test)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)

    def detect(self):
        ip, token = self.ip.text().strip(), self.token.text().strip()
        if not ip:
            self.status.setText("⚠ Enter the IP address first")
            return
        if not token:
            self.status.setText("Contacting the device…")
            run_task(miio.hello, ip, on_done=self._hello_done,
                     on_error=lambda e: self.status.setText(f"⚠ No Xiaomi device answered at {ip} ({e})"),
                     owner=self)
            return
        try:
            dev = miio.MiioDevice(ip, token, timeout=2.5, retries=1)
        except miio.MiioError as exc:
            self.status.setText(f"⚠ {exc}")
            return
        self.status.setText("Contacting the device…")

        def done(res):
            dev_id, info = res
            model = (info or {}).get("model", "")
            if model:
                self.model.setText(model)
            if not self.info.get("did") or str(self.info.get("did")).startswith("manual-"):
                self.info["did"] = str(dev_id)
            if info and info.get("mac"):
                self.info["mac"] = info["mac"]
            self.status.setText(f"✓ Connected. Model: {model or 'unknown'}, device ID {dev_id}")

        run_task(lambda: (dev.handshake(), dev.send("miIO.info", [])), on_done=done,
                 on_error=lambda e: self.status.setText(f"⚠ {e}"), owner=self)

    def _hello_done(self, res):
        dev_id, token = res
        if not self.info.get("did") or str(self.info.get("did")).startswith("manual-"):
            self.info["did"] = str(dev_id)
        if token:
            self.token.setText(token)
            self.status.setText(f"✓ Found device ID {dev_id} and it shared its token. Press Detect again for "
                                "the model.")
        else:
            self.status.setText(f"✓ Found device ID {dev_id}. Now enter its token - or sign in to your Mi account, "
                                "which fills it in automatically.")

    def _save(self):
        tok = self.token.text().strip()
        if tok:
            try:
                miio.parse_token(tok)
            except miio.MiioError as exc:
                QMessageBox.warning(self, "Token", str(exc))
                return
        rtsp = self.rtsp.text().strip()
        if rtsp and not rtsp.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
            QMessageBox.warning(self, "RTSP stream", "The stream address must start with rtsp://, rtsps://, "
                                                     "http:// or https://")
            return
        if not self.info.get("did") and not (self.ip.text().strip() and tok):
            QMessageBox.warning(self, "Add device", "Enter the IP address and token.")
            return
        self.accept()

    def result_info(self) -> dict:
        info = dict(self.info)
        info.update({
            "name": self.name.text().strip() or self.model.text().strip() or self.ip.text().strip(),
            "ip": self.ip.text().strip(),
            "token": self.token.text().strip(),
            "model": self.model.text().strip(),
            "mode": self.mode.currentData(),
            "rtsp_url": self.rtsp.text().strip(),
        })
        if not info.get("did"):
            info["did"] = f"manual-{info['ip']}"
        if self.info.get("name") and info["name"] != self.info.get("name"):
            info["renamed"] = True
        return info

    def name_to_sync(self) -> str | None:
        """The new name, if it changed and the user wants it on the Mi account as well."""
        new = self.name.text().strip()
        if new and new != self.info.get("name") and self.sync_name.isVisibleTo(self) and self.sync_name.isChecked():
            return new
        return None


class RemoveDialog(QDialog):
    """Remove a device from this app only - or delete it from the Mi account as well."""

    def __init__(self, name: str, can_delete: bool, why_not: str = "", parent=None, account_first: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Remove device")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        t = QLabel(f"Remove “{name}”")
        t.setObjectName("H2")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.app_only = QRadioButton("Remove it from XiControl only")
        self.app_only.setChecked(True)
        lay.addWidget(self.app_only)
        lay.addWidget(muted("It stays on your Mi account and in the Xiaomi Home app. You can import it again any "
                            "time with + → Sync devices."))
        self.account = QRadioButton("Delete it from my Mi account too")
        self.account.setEnabled(can_delete)
        lay.addWidget(self.account)
        lay.addWidget(muted(why_not if not can_delete else
                            "The device is unbound from your account: it disappears from the Xiaomi Home app as "
                            "well, its automations stop, and it has to be reset and set up again to come back."))
        self.sure = QCheckBox("I understand that this can't be undone")
        self.sure.setVisible(False)
        lay.addWidget(self.sure)
        self.bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = self.bb.button(QDialogButtonBox.Ok)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        for w in (self.app_only, self.account, self.sure):
            w.setCursor(Qt.PointingHandCursor)
            w.toggled.connect(self._update)
        if account_first and can_delete:
            self.account.setChecked(True)
        self._update()

    def _update(self, *_):
        delete = self.account.isChecked()
        self.sure.setVisible(delete)
        self.ok.setText("Delete from my Mi account" if delete else "Remove")
        self.ok.setObjectName("Danger" if delete else "Primary")
        self.ok.style().unpolish(self.ok)
        self.ok.style().polish(self.ok)
        self.ok.setEnabled(not delete or self.sure.isChecked())

    @property
    def delete_from_account(self) -> bool:
        return self.account.isChecked() and self.sure.isChecked()


class RenameDialog(QDialog):
    def __init__(self, name: str, can_sync: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Rename device")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        t = QLabel("New name")
        t.setObjectName("H2")
        lay.addWidget(t)
        self.name = QLineEdit(name)
        self.name.selectAll()
        lay.addWidget(self.name)
        self.sync = QCheckBox("Also rename it in the Xiaomi Home app")
        self.sync.setToolTip("Sends the new name to your Mi account, so the phone app shows it too")
        self.sync.setVisible(can_sync)
        self.sync.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.sync)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.old = name
        self.name.textChanged.connect(lambda t: self.ok.setEnabled(bool(t.strip()) and t.strip() != self.old))
        self.ok.setEnabled(False)

    def new_name(self) -> str:
        return self.name.text().strip()

    def sync_to_account(self) -> bool:
        return self.sync.isVisibleTo(self) and self.sync.isChecked()


class RoomDialog(QDialog):
    """Move a device to another room - in XiControl, and if asked also in the Xiaomi Home app."""

    def __init__(self, name: str, current: str, local_rooms: list[str], account_rooms: list[dict] | None = None,
                 parent=None):
        """account_rooms: [{'id', 'name'}] of the device's home on the Mi account (None: not signed in)."""
        super().__init__(parent)
        self.setWindowTitle("Move to room")
        self.setMinimumWidth(460)
        self.current = current or ""
        self.account_rooms = account_rooms or []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        t = QLabel(f"Move “{name}” to a room")
        t.setObjectName("H2")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.room = QComboBox()
        self.room.setEditable(True)
        self.room.setInsertPolicy(QComboBox.NoInsert)
        names = []
        for r in [*(x["name"] for x in self.account_rooms), *local_rooms]:
            if r and r not in names:
                names.append(r)
        self.room.addItems(names)
        self.room.setCurrentText(self.current)
        self.room.lineEdit().setPlaceholderText("Room name - pick one or type a new one")
        lay.addWidget(self.room)
        self.sync = QCheckBox("Also move it in the Xiaomi Home app")
        self.sync.setCursor(Qt.PointingHandCursor)
        self.sync.setVisible(account_rooms is not None)
        lay.addWidget(self.sync)
        self.note = muted("")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.room.currentTextChanged.connect(self._update)
        self._update()

    def room_name(self) -> str:
        return self.room.currentText().strip()

    def account_room(self) -> dict | None:
        """The Mi account room to move the device to, if that was asked for and is possible."""
        if not (self.sync.isVisibleTo(self) and self.sync.isEnabled() and self.sync.isChecked()):
            return None
        return self._match()

    def _match(self):
        want = self.room_name().lower()
        return next((r for r in self.account_rooms if r["name"].strip().lower() == want), None)

    def _update(self, *_):
        name = self.room_name()
        self.ok.setEnabled(name != self.current)
        possible = self._match() is not None
        self.sync.setEnabled(possible)
        if not self.sync.isVisibleTo(self):
            self.note.setText("Rooms are kept in XiControl only while you are not signed in.")
        elif not name:
            self.note.setText("Without a room the device is shown under “All devices” only.")
        elif not possible:
            self.note.setText("This room isn't in the Xiaomi Home app - it is kept in XiControl only. (New rooms are "
                              "made in the Xiaomi Home app.)")
        else:
            self.note.setText("")


class ManageDialog(QDialog):
    """All devices in one list, to remove many of them from this app at once."""

    def __init__(self, devices: list[dict], status_for, parent=None, firmware_for=None, can_delete_account=False,
                 mode_for=None, set_mode=None):
        """status_for(did) -> short text such as 'Online' / 'Offline'; firmware_for(did) -> version text;
        mode_for(did) -> 'auto' / 'local' / 'cloud'; set_mode(dids, mode) changes it for several devices."""
        super().__init__(parent)
        self.mode_for, self.set_mode = mode_for, set_mode
        self.setWindowTitle("Manage devices")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        t = QLabel(f"{len(devices)} devices in XiControl")
        t.setObjectName("H2")
        lay.addWidget(t)
        lay.addWidget(muted("Tick the devices you don't want in XiControl. Normally they are removed from this app "
                            "only - they stay on your Mi account and can be imported again."
                            + (" The ticked devices can also be given another connection: local network or "
                               "Mi cloud." if set_mode else "")))
        self.table = _table(["", "Name", "Room", "Model", "Status", "Firmware"] + (["Connection"] if mode_for else []))
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        for d in sorted(devices, key=lambda x: ((x.get("room") or "~"), (x.get("name") or "").lower())):
            r = self.table.rowCount()
            self.table.insertRow(r)
            _k, icon, _l = category(d.get("model", ""))
            self.table.setItem(r, 0, _item("", str(d["did"]), checkable=True))
            self.table.setItem(r, 1, _item(f"{icon}  {d.get('name') or d['did']}"))
            self.table.setItem(r, 2, _item(d.get("room")))
            self.table.setItem(r, 3, _item(d.get("model")))
            self.table.setItem(r, 4, _item(status_for(str(d["did"]))))
            self.table.setItem(r, 5, _item(firmware_for(str(d["did"])) if firmware_for else ""))
            if mode_for:
                self.table.setItem(r, 6, _item(MODES.get(mode_for(str(d["did"])), "")))
        self.table.itemChanged.connect(self._count)
        lay.addWidget(self.table, 1)
        self.account = QCheckBox("Also delete them from my Mi account (they disappear from the Xiaomi Home app too)")
        self.account.setVisible(can_delete_account)
        self.account.setCursor(Qt.PointingHandCursor)
        self.account.toggled.connect(self._count)
        lay.addWidget(self.account)
        row = QHBoxLayout()
        for text, fn in (("Select offline", lambda r: self.table.item(r, 4).text() == "Offline"),
                         ("Select none", lambda r: False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: self._select(f))
            row.addWidget(b)
        self.mode_btn = QPushButton("Connection of the ticked")
        self.mode_btn.setToolTip("How XiControl reaches the ticked devices")
        self.mode_menu = QMenu(self)
        for code, text in MODES.items():
            self.mode_menu.addAction(text, lambda code=code: self._set_mode(code))
        self.mode_btn.setMenu(self.mode_menu)
        self.mode_btn.setVisible(bool(set_mode and mode_for))
        row.addWidget(self.mode_btn)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Close)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setObjectName("Danger")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self._count()

    def _select(self, wanted):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.Checked if wanted(r) else Qt.Unchecked)
        self.table.blockSignals(False)
        self._count()

    def selected(self) -> list[str]:
        return [self.table.item(r, 0).data(Qt.UserRole) for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked]

    @property
    def delete_from_account(self) -> bool:
        return self.account.isVisibleTo(self) and self.account.isChecked()

    def _set_mode(self, mode: str):
        dids = self.selected()
        if not dids or not self.set_mode:
            return
        self.set_mode(dids, mode)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            did = self.table.item(r, 0).data(Qt.UserRole)
            if did in dids:
                self.table.item(r, 6).setText(MODES.get(self.mode_for(did), ""))
        self.table.blockSignals(False)

    def _count(self, *_):
        n = len(self.selected())
        where = "XiControl and my Mi account" if self.account.isChecked() else "XiControl"
        self.ok.setText(f"Remove {n} from {where}" if n else f"Remove from {where}")
        self.ok.setEnabled(n > 0)
        self.mode_btn.setEnabled(n > 0)


class ScanDialog(QDialog):
    """Find Xiaomi Wi-Fi devices on the local network."""

    def __init__(self, known: list[dict], cloud=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Scan the local network")
        self.resize(720, 440)
        self.cloud = cloud
        self.known = {}
        for d in known:
            try:
                self.known[int(str(d.get("did")))] = d
            except ValueError:
                pass
        self.updates: dict[str, str] = {}  # did -> new ip
        self.add_ip = None
        self.add_info = None               # a device from the Mi account, ready to import
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        self.status = QLabel("Searching for devices… (allow access if Windows Firewall asks)")
        lay.addWidget(self.status)
        self.table = _table(["IP address", "Device ID", "Device"])
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.again = QPushButton("Scan again")
        self.again.clicked.connect(self.scan)
        row.addWidget(self.again)
        self.add_btn = QPushButton("Add selected…")
        self.add_btn.clicked.connect(self._add)
        row.addWidget(self.add_btn)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        row.addWidget(bb)
        lay.addLayout(row)
        self.scan()

    def scan(self):
        self.again.setEnabled(False)
        self.table.setRowCount(0)
        self.status.setText("Searching for devices… (allow access if Windows Firewall asks)")
        from .net import broadcast_targets
        run_task(miio.discover, 4.0, targets=broadcast_targets(), on_done=self._found,
                 on_error=lambda e: (self.status.setText(f"⚠ {e}"), self.again.setEnabled(True)), owner=self)

    def _found(self, found: dict):
        self.again.setEnabled(True)
        n_known = 0
        for ip, dev_id in sorted(found.items(), key=lambda x: tuple(int(p) for p in x[0].split("."))):
            r = self.table.rowCount()
            self.table.insertRow(r)
            k = self.known.get(dev_id)
            if k:
                n_known += 1
                text = k.get("name", "")
                if k.get("ip") != ip:
                    self.updates[str(k["did"])] = ip
                    text += f"   (IP updated from {k.get('ip') or 'none'})"
            elif self.cloud:
                text = "Checking your Mi account…"
            else:
                text = "Unknown - sign in to import it, or add it with its token"
            self.table.setItem(r, 0, _item(ip, ip))
            self.table.setItem(r, 1, _item(dev_id))
            self.table.setItem(r, 2, _item(text))
        self.status.setText(f"Found {len(found)} Xiaomi Wi-Fi devices ({n_known} already added). "
                            "Bluetooth and Zigbee devices don't show up here." if found else
                            "No devices answered. Make sure the PC is on the same network as the devices, "
                            "and that Windows Firewall allows this app (set the network to Private).")
        if self.cloud and len(found) > n_known:
            run_task(self.cloud.get_devices, on_done=self._match_cloud,
                     on_error=lambda _e: self._match_cloud([]), owner=self)

    def _match_cloud(self, devices: list[dict]):
        """Names the unknown devices that are on the Mi account, so they can be imported with their token."""
        by_id = {}
        for d in devices:
            try:
                by_id[int(str(d["did"]))] = d
            except ValueError:
                pass
        for r in range(self.table.rowCount()):
            dev_id = int(self.table.item(r, 1).text())
            if dev_id in self.known:
                continue
            d = by_id.get(dev_id)
            if d:
                d = dict(d, ip=self.table.item(r, 0).data(Qt.UserRole))
                self.table.setItem(r, 2, _item(f"{d['name']}  ({d['model']}) - on your Mi account, not added yet", d))
            else:
                self.table.setItem(r, 2, _item("Not on this Mi account (e.g. a router) - add it with its token"))

    def _add(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        r = rows[0].row()
        d = self.table.item(r, 2).data(Qt.UserRole)
        if isinstance(d, dict):
            self.add_info = d
        else:
            self.add_ip = self.table.item(r, 0).data(Qt.UserRole)
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, cfg, cloud, config_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.sign_out = False
        self.sign_in_requested = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(16)

        t = QLabel("Mi account")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        acc = QHBoxLayout()
        if cloud:
            region = SERVERS.get(cloud.server, cloud.server)
            renew = "stays signed in" if cloud.can_renew else "sign in again once to stay signed in"
            acc.addWidget(QLabel(f"Signed in  ·  {region}  ·  {renew}"), 1)
            so = QPushButton("Sign out")
            so.setObjectName("Danger")
            so.clicked.connect(self._sign_out)
            acc.addWidget(so)
        else:
            acc.addWidget(muted("Not signed in - only devices on your network with a known token work."), 1)
            si = QPushButton("Sign in")
            si.setObjectName("Primary")
            si.clicked.connect(self._sign_in)
            acc.addWidget(si)
        lay.addLayout(acc)

        t = QLabel("App")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.theme = QComboBox()
        for code, name in (("light", "Light"), ("dark", "Dark"), ("system", "Same as Windows")):
            self.theme.addItem(name, code)
        self.theme.setCurrentIndex(max(0, self.theme.findData(cfg.data.get("theme", "light"))))
        self.server = QComboBox()
        for code, name in SERVERS.items():
            self.server.addItem(f"{name}  ({code})", code)
        self.server.setCurrentIndex(max(0, self.server.findData(cfg.data.get("server", "de"))))
        self.refresh = QSpinBox()
        self.refresh.setRange(5, 300)
        self.refresh.setSuffix(" seconds")
        no_arrows(self.refresh)
        self.refresh.setValue(max(5, int(cfg.data.get("refresh_seconds", 10))))
        self.connection = QComboBox()
        for code, text in (("", "As set for each device"),
                           ("auto", "All devices: automatic - local network first, Mi cloud as backup"),
                           ("local", "All devices: local network only"),
                           ("cloud", "All devices: Mi cloud only")):
            self.connection.addItem(text, code)
        self.connection.setCurrentIndex(max(0, self.connection.findData(cfg.data.get("connection") or "")))
        self.connection.setToolTip("How XiControl reaches your devices. A single device is set with right-click → "
                                   "Connection, several at once in + → Manage devices.")
        form.addRow("Appearance", self.theme)
        form.addRow("Mi account region", self.server)
        form.addRow("Update device status every", self.refresh)
        form.addRow("Connection", self.connection)
        lay.addLayout(form)
        self.connection_note = muted("")
        self.connection_note.setWordWrap(True)
        lay.addWidget(self.connection_note)
        self.connection.currentIndexChanged.connect(self._connection_note)
        self._connection_note()

        t = QLabel("Running in the background")
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
        tray = QSystemTrayIcon.isSystemTrayAvailable()
        self.close_to_tray = QCheckBox("Keep running in the tray when the window is closed")
        self.close_to_tray.setChecked(bool(cfg.data.get("close_to_tray", True)))
        self.autostart = QCheckBox("Start with Windows (in the tray)")
        self.autostart.setChecked(autostart.is_enabled())
        self.notifications = QCheckBox("Notifications: device offline / back, filter or brush running out")
        self.notifications.setToolTip("Also tells you when the Mi account needs a new sign-in.")
        self.notifications.setChecked(bool(cfg.data.get("notifications", True)))
        for cb in (self.close_to_tray, self.autostart, self.notifications):
            cb.setCursor(Qt.PointingHandCursor)
            lay.addWidget(cb)
        if not tray:
            for cb in (self.close_to_tray, self.notifications):
                cb.setEnabled(False)
                cb.setToolTip("Windows doesn't offer a notification area right now")
        if not autostart.supported():
            self.autostart.setEnabled(False)

        if cloud:
            lay.addWidget(muted("Changing the region switches the Mi account server right away."))

        foot = QHBoxLayout()
        folder = QPushButton("Open data folder")
        folder.setObjectName("Link")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(config_dir)))
        foot.addWidget(folder)
        logfile = os.path.join(config_dir, "xicontrol.log")
        if os.path.exists(logfile):
            lg = QPushButton("Open log")
            lg.setObjectName("Link")
            lg.setToolTip("What XiControl did and which errors happened (keys are hidden)")
            lg.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(logfile)))
            foot.addWidget(lg)
        foot.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        foot.addWidget(bb)
        from xc.secure import AVAILABLE as encrypted
        where = os.path.join(config_dir, "config.json")
        lay.addWidget(muted(f"Device keys and the Mi account session are stored encrypted for your Windows account "
                            f"in {where}." if encrypted else f"Device keys are stored in {where} - keep it private."))
        lay.addLayout(foot)

    def _connection_note(self, *_):
        self.connection_note.setText({
            "": "The sign in front of a device's name shows its way: a green house = local network, a blue cloud "
                "= Mi cloud.",
            "auto": "Every device is tried on the local network first; the Mi cloud is the backup.",
            "local": "Nothing goes through Xiaomi's servers. Devices that only work through the Mi cloud "
                     "(Bluetooth and most Zigbee devices, most cameras) can't be reached while this is chosen.",
            "cloud": "Everything goes through Xiaomi's servers, also the devices on your own network. Devices "
                     "added by hand (IP and token) can't be reached while this is chosen.",
        }[self.connection.currentData() or ""])

    def _sign_out(self):
        self.sign_out = True
        self.accept()

    def _sign_in(self):
        self.sign_in_requested = True
        self.reject()
