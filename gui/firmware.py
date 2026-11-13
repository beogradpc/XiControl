"""Firmware: the installed and the newest version of a device, what's new, and starting an update - the same
way the Xiaomi Home app does it (the Mi cloud tells the device to fetch the update itself)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from xc.cloud import friendly

from .controls import muted
from .workers import run_task

POLL_MS = 3000
STATUS_TEXT = {"idle": "", "downloading": "Downloading the update…", "downloaded": "Update downloaded",
               "installing": "Installing the update…", "upgrading": "Installing the update…",
               "succeed": "Update finished", "success": "Update finished", "failed": "The update failed"}


def is_bluetooth(did: str) -> bool:
    return str(did).startswith("blt.")


def has_update(fw: dict | None) -> bool:
    return bool(fw) and fw.get("isLatest") is False and bool(fw.get("latest"))


def version_text(fw: dict | None) -> str:
    if not fw:
        return "—"
    if has_update(fw):
        return f"{fw.get('curr') or '?'} → {fw.get('latest')}"
    return fw.get("curr") or "—"


class FirmwareDialog(QDialog):
    """One device: versions, release notes and the update button with its progress."""

    def __init__(self, main, did: str, parent=None):
        super().__init__(parent)
        self.main, self.did = main, str(did)
        self.info = main.cfg.device(self.did) or {"did": self.did}
        self.setWindowTitle("Firmware")
        self.setMinimumWidth(500)
        self._pid = self.info.get("pid")
        self._fw: dict = dict(main.firmware.get(self.did) or {})
        self._updating = False
        self._deadline = 0.0
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        t = QLabel(f"Firmware · {self.info.get('name') or self.did}")
        t.setObjectName("H2")
        t.setWordWrap(True)
        lay.addWidget(t)
        form = QFormLayout()
        form.setVerticalSpacing(8)
        self.curr = QLabel("—")
        self.latest = QLabel("—")
        self.state = QLabel("")
        self.state.setWordWrap(True)
        for w in (self.curr, self.latest):
            w.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Installed version", self.curr)
        form.addRow("Newest version", self.latest)
        form.addRow("", self.state)
        lay.addLayout(form)
        self.notes_title = QLabel("What's new")
        self.notes_title.setObjectName("SectionTitle")
        lay.addWidget(self.notes_title)
        self.notes = QPlainTextEdit()      # release notes can be long: they scroll instead of being cut off
        self.notes.setReadOnly(True)
        self.notes.setFrameShape(QFrame.NoFrame)
        self.notes.setFixedHeight(150)
        lay.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.note = muted("")
        lay.addWidget(self.note)
        row = QHBoxLayout()
        self.again = QPushButton("Check again")
        self.again.clicked.connect(self.check)
        row.addWidget(self.again)
        row.addStretch(1)
        self.update_btn = QPushButton("Update now")
        self.update_btn.setObjectName("Primary")
        self.update_btn.clicked.connect(self.start_update)
        row.addWidget(self.update_btn)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        for b in (self.again, self.update_btn):
            b.setCursor(Qt.PointingHandCursor)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.check)
        self._show()
        if is_bluetooth(self.did):
            self.note.setText("Bluetooth devices get their updates from the Xiaomi Home app on a phone nearby - "
                              "the phone sends the update over Bluetooth. XiControl can't do that from a PC.")
        if not main.signed_in:
            self.note.setText("Sign in to your Mi account to check for firmware updates.")
            self.again.setEnabled(False)
        else:
            self.check()

    # -- talking to the Mi cloud ---------------------------------------------------------------------
    def _fetch(self):
        cloud = self.main.cloud
        if self._pid is None:
            record = cloud.device_record(self.did) or {}
            self._pid = record.get("pid") or 0
        return cloud.check_firmware(self.did, self._pid)

    def check(self):
        if self._busy or not self.main.signed_in:
            return
        self._busy = True
        self.again.setEnabled(False)
        if not self._updating:
            self.state.setText("Checking…")
        run_task(self._fetch, on_done=self._checked, on_error=self._failed, owner=self)

    def _checked(self, fw: dict):
        self._busy = False
        self.again.setEnabled(True)
        if fw:
            self._fw = fw
            self.main.firmware[self.did] = fw
            self.main.firmware_changed(self.did)
        self._show()
        if self._updating:
            done = not fw.get("updating") and fw.get("ota_status") in ("idle", "succeed", "success", "failed")
            if done or time.monotonic() > self._deadline:
                self._updating = False
                self.progress.hide()
                failed = fw.get("ota_status") == "failed" or fw.get("ota_failed_code")
                if failed:
                    self.state.setText(f"⚠ The update failed: {fw.get('ota_failed_reason') or 'no reason given'}")
                elif not has_update(fw):
                    self.state.setText("✓ The device runs the newest firmware now.")
                else:
                    self.state.setText("The device hasn't reported the new version yet - check again in a few "
                                       "minutes.")
            else:
                self.timer.start(POLL_MS)

    def _failed(self, exc):
        self._busy = False
        self.again.setEnabled(True)
        self.state.setText(f"⚠ {friendly(exc)}")
        if self._updating and time.monotonic() < self._deadline:
            self.timer.start(POLL_MS)  # the device may be restarting

    def _show(self):
        fw = self._fw
        self.curr.setText(fw.get("curr") or self.info.get("fw_version") or "—")
        self.latest.setText(fw.get("latest") or "—")
        notes = (fw.get("description") or "").strip()
        if self.notes.toPlainText() != notes:
            self.notes.setPlainText(notes)
        self.notes.setVisible(bool(notes) and has_update(fw))
        self.notes_title.setVisible(self.notes.isVisible())
        status = STATUS_TEXT.get(str(fw.get("ota_status") or ""), "")
        if fw.get("updating") or self._updating:
            self.progress.show()
            self.progress.setValue(int(fw.get("ota_progress") or 0))
            self.state.setText(status or "Updating…")
        elif fw and not self._busy:
            self.state.setText("An update is available." if has_update(fw) else
                               "✓ This is the newest firmware." if fw.get("latest") else "")
        can = has_update(fw) and not is_bluetooth(self.did) and self.main.signed_in and not self._updating \
            and not fw.get("updating")
        self.update_btn.setVisible(can)
        self.adjustSize()

    # -- updating ------------------------------------------------------------------------------------
    def start_update(self):
        fw = self._fw
        name = self.info.get("name") or self.did
        text = (f"Update “{name}” from {fw.get('curr')} to {fw.get('latest')}?\n\n"
                "The device downloads the update itself, installs it and restarts - it can't be used for a few "
                "minutes. Keep it switched on and connected until it is finished.")
        if QMessageBox.question(self, "Firmware update", text) != QMessageBox.Yes:
            return
        self.update_btn.hide()
        self.state.setText("Starting the update…")
        pid = self._pid or 0

        def started(_r):
            self._updating = True
            self._deadline = time.monotonic() + max(300, int(fw.get("timeout_time") or 180) + 240)
            self.progress.setValue(0)
            self.progress.show()
            self.timer.start(POLL_MS)

        def refused(exc):
            self.state.setText(f"⚠ The update didn't start: {friendly(exc)}")
            self._show()

        run_task(self.main.cloud.start_firmware_update, self.did, pid, on_done=started, on_error=refused, owner=self)

    def reject(self):
        self.timer.stop()
        super().reject()


class FirmwareListDialog(QDialog):
    """All devices: which have a firmware update waiting."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.setWindowTitle("Firmware updates")
        self.resize(760, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        t = QLabel("Firmware updates")
        t.setObjectName("H2")
        lay.addWidget(t)
        self.status = muted("Checking your devices…")
        lay.addWidget(self.status)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Device", "Room", "Installed", "Newest"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.details)
        lay.addWidget(self.table, 1)
        lay.addWidget(muted("Bluetooth devices aren't listed: the Xiaomi Home app on a phone updates them."))
        row = QHBoxLayout()
        self.details_btn = QPushButton("Details and update…")
        self.details_btn.setObjectName("Primary")
        self.details_btn.clicked.connect(self.details)
        row.addWidget(self.details_btn)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.load()

    def load(self):
        if not self.main.signed_in:
            self.status.setText("Sign in to your Mi account to check for firmware updates.")
            return
        dids = [str(d["did"]) for d in self.main.cfg.devices]
        run_task(self.main.cloud.firmware_status, dids, on_done=self._loaded,
                 on_error=lambda e: self.status.setText(f"⚠ {friendly(e)}"), owner=self)

    def _loaded(self, status: dict):
        self.main.firmware.update(status)
        self.main.firmware_changed(None)
        rows = []
        for d in self.main.cfg.devices:
            fw = status.get(str(d["did"]))
            if fw:
                rows.append((not has_update(fw), (d.get("name") or "").lower(), d, fw))
        rows.sort(key=lambda r: (r[0], r[1]))
        self.table.setRowCount(0)
        for _latest, _name, d, fw in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            name = QTableWidgetItem(("⬆ " if has_update(fw) else "") + (d.get("name") or str(d["did"])))
            name.setData(Qt.UserRole, str(d["did"]))
            self.table.setItem(r, 0, name)
            self.table.setItem(r, 1, QTableWidgetItem(d.get("room") or ""))
            self.table.setItem(r, 2, QTableWidgetItem(fw.get("curr") or "—"))
            self.table.setItem(r, 3, QTableWidgetItem(fw.get("latest") if has_update(fw) else "✓ up to date"))
        n = sum(1 for r in rows if not r[0])
        self.status.setText(f"{n} device{'s have' if n != 1 else ' has'} a firmware update." if n else
                            f"All {len(rows)} devices that the Mi cloud updates run the newest firmware.")
        if n:
            self.table.selectRow(0)

    def details(self, *_):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        did = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
        self.main.show_firmware(did, self)
