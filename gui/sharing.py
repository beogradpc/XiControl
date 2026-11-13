"""Sharing: who the devices of the Mi account are shared with, inviting another Mi account to a device and
stopping a share (what "Share device" does in the Xiaomi Home app)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from xc.cloud import CloudError, friendly

from .controls import muted, run_dialog
from .workers import run_task

PENDING = 0   # the status of an invitation the other person hasn't accepted yet (1 = accepted)


def access_text(user: dict) -> str:
    ro = user.get("readonly")
    text = "" if ro is None else "View only" if ro else "Control"
    if user.get("status") == PENDING:
        text = (text + " · " if text else "") + "invited"
    return text


class ShareWithDialog(QDialog):
    """Asks for the Mi ID of the other account, looks the account up and shows whose it is before sharing."""

    def __init__(self, main, device_name: str, parent=None):
        super().__init__(parent)
        self.main = main
        self.user: dict | None = None
        self.setWindowTitle("Share device")
        self.setMinimumWidth(480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        t = QLabel(f"Share “{device_name}”")
        t.setObjectName("H2")
        t.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(muted("Type the Mi ID of the other person's Mi account (the number in Xiaomi Home → Profile, "
                            "under their name) and press Find."))
        row = QHBoxLayout()
        self.uid = QLineEdit()
        self.uid.setPlaceholderText("Mi ID, e.g. 1234567890")
        self.uid.setMaxLength(20)
        self.uid.textChanged.connect(self._typed)
        self.uid.returnPressed.connect(self.find)
        row.addWidget(self.uid, 1)
        self.find_btn = QPushButton("Find")
        self.find_btn.setCursor(Qt.PointingHandCursor)
        self.find_btn.setAutoDefault(False)
        self.find_btn.clicked.connect(self.find)
        row.addWidget(self.find_btn)
        lay.addLayout(row)
        self.found = QLabel("")
        self.found.setObjectName("RowLabel")
        self.found.setWordWrap(True)
        lay.addWidget(self.found)
        self.view_only = QCheckBox("View only - they can see the device, but not control it")
        self.view_only.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.view_only)
        lay.addWidget(muted("They get an invitation in their Xiaomi Home app and the device appears on their "
                            "account once they accept it."))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Share")
        self.ok.setObjectName("Primary")
        self.ok.setAutoDefault(False)
        self.ok.setEnabled(False)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._typed("")

    def _typed(self, _text):
        self.user = None
        self.ok.setEnabled(False)
        self.found.setText("")
        self.find_btn.setEnabled(self.uid.text().strip().isdigit())

    def find(self):
        uid = self.uid.text().strip()
        if not uid.isdigit():
            return
        if uid == str(self.main.cloud.user_id):
            self.found.setText("⚠ That is your own Mi ID.")
            return
        self.find_btn.setEnabled(False)
        self.found.setText("Looking the account up…")
        run_task(self.main.cloud.find_user, uid, on_done=lambda u, x=uid: self._found(x, u),
                 on_error=lambda e, x=uid: self._failed(x, e), owner=self)

    def _found(self, uid, user):
        if uid != self.uid.text().strip():
            return                         # the number was changed while the answer was on its way
        self.find_btn.setEnabled(True)
        if not user:
            self.found.setText("⚠ There is no Mi account with this Mi ID.")
            return
        self.user = user
        self.found.setText(f"✓ {user['name']}  ·  Mi ID {user['userid']}")
        self.ok.setEnabled(True)

    def _failed(self, uid, exc):
        if uid != self.uid.text().strip():
            return
        self.find_btn.setEnabled(True)
        self.found.setText(f"⚠ {friendly(exc)}")

    def readonly(self) -> bool:
        return self.view_only.isChecked()


class SharingDialog(QDialog):
    """did given: the people one device is shared with; otherwise every shared device of the account."""

    def __init__(self, main, did: str | None = None, parent=None):
        super().__init__(parent)
        self.main, self.did = main, str(did) if did else None
        info = main.cfg.device(self.did) if self.did else None
        self.info = info or {}
        self.rows: list[dict] = []
        self.setWindowTitle("Sharing")
        self.resize(680, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        t = QLabel(f"Sharing · {info.get('name')}" if info else "Shared devices")
        t.setObjectName("H2")
        t.setWordWrap(True)
        lay.addWidget(t)
        self.status = muted("Loading…")
        lay.addWidget(self.status)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Device", "Shared with", "Mi ID", "Access", "Since"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1 if self.did else 0, QHeaderView.Stretch)
        self.table.setColumnHidden(0, bool(self.did))
        self.table.itemSelectionChanged.connect(self._selection)
        lay.addWidget(self.table, 1)
        self.note = muted("")
        lay.addWidget(self.note)
        row = QHBoxLayout()
        self.share_btn = QPushButton("Share with a Mi account…")
        self.share_btn.setObjectName("Primary")
        self.share_btn.setCursor(Qt.PointingHandCursor)
        self.share_btn.setToolTip("Invite another Mi account to use this device")
        self.share_btn.clicked.connect(self.share)
        self.share_btn.setVisible(bool(self.did))
        row.addWidget(self.share_btn)
        self.stop_btn = QPushButton("Stop sharing")
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setToolTip("Stop sharing the selected row: that account loses the device")
        self.stop_btn.clicked.connect(self.stop)
        self.stop_btn.setEnabled(False)
        row.addWidget(self.stop_btn)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.mine = not self.info.get("shared")
        if not main.signed_in:
            self.status.setText("Sign in to your Mi account to see who your devices are shared with.")
            self.share_btn.setEnabled(False)
        elif self.did and not self.mine:
            self.status.setText("This device is shared with you by another Mi account - only its owner can share it.")
            self.share_btn.setEnabled(False)
        else:
            self.reload()

    # -- reading ------------------------------------------------------------------------------------------
    def _pid(self, did) -> int:
        return int((self.main.cfg.device(did) or {}).get("pid") or 0)

    def _fetch(self) -> dict:
        """{did: [users]} - for one device, or for every shared device of the account (with the access each
        person has, which only the per-device list tells)."""
        cloud = self.main.cloud
        if self.did:
            return {self.did: cloud.device_shares(self.did, self._pid(self.did))}
        shares = cloud.shares()
        for did in list(shares):
            try:
                detailed = cloud.device_shares(did, self._pid(did))
            except CloudError:
                continue
            if detailed:
                shares[did] = detailed
        return shares

    def reload(self, message: str = ""):
        self.status.setText("Loading…")
        self.stop_btn.setEnabled(False)
        run_task(self._fetch, on_done=lambda shares: self._loaded(shares, message),
                 on_error=lambda e: self.status.setText(f"⚠ {friendly(e)}"), owner=self)

    def _loaded(self, shares: dict, message: str = ""):
        rows = []
        for did, users in shares.items():
            if self.did and did != self.did:
                continue
            info = self.main.cfg.device(did) or {}
            for u in users:
                rows.append(dict(u, did=str(did), device=info.get("name") or str(did)))
        rows.sort(key=lambda r: (r["device"].lower(), (r.get("name") or "").lower()))
        self.rows = rows
        self.table.setRowCount(0)
        for u in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            since = time.strftime("%d.%m.%Y", time.localtime(u["time"])) if u.get("time") else ""
            for c, text in enumerate((u["device"], u.get("name") or "(no name)", u["userid"], access_text(u), since)):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, c, item)
        if self.did:
            text = (f"Shared with {len(rows)} other account{'s' if len(rows) != 1 else ''}." if rows else
                    "This device isn't shared with anyone.")
        else:
            n = len({r["did"] for r in rows})
            text = (f"{n} device{'s are' if n != 1 else ' is'} shared with other accounts." if rows else
                    "None of your devices is shared with another account.")
        self.status.setText(f"{message}  {text}" if message else text)
        self.note.setText("Select a row and press “Stop sharing” to take the device back." if self.did else
                          "Select a row and press “Stop sharing” to take the device back. To share a device, "
                          "right-click it on the home screen → Sharing….")
        self._selection()

    def selected(self) -> dict | None:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) and self.table.selectedItems() else None

    def _selection(self):
        self.stop_btn.setEnabled(self.selected() is not None)

    # -- changing -----------------------------------------------------------------------------------------
    def share(self):
        if not (self.did and self.mine and self.main.signed_in):
            return
        dlg = ShareWithDialog(self.main, self.info.get("name") or "device", self)
        ok = run_dialog(dlg) == QDialog.Accepted
        user, readonly = dlg.user, dlg.readonly()
        if not ok or not user:
            return
        if any(r["userid"] == user["userid"] for r in self.rows):
            self.status.setText(f"⚠ The device is already shared with {user['name']}.")
            return
        self.status.setText(f"Sending the invitation to {user['name']}…")
        self.share_btn.setEnabled(False)

        def done(_r):
            self.share_btn.setEnabled(True)
            self.reload(f"Invitation sent to {user['name']} ✓ - they have to accept it in their Xiaomi Home app.")

        def failed(exc):
            self.share_btn.setEnabled(True)
            self.status.setText(f"⚠ The device wasn't shared: {friendly(exc)}")

        run_task(self.main.cloud.share_device, self.did, user["userid"], readonly, on_done=done, on_error=failed,
                 owner=self)

    def stop(self):
        u = self.selected()
        if u is None:
            return
        who = u.get("name") or f"Mi ID {u['userid']}"
        if QMessageBox.question(self, "Stop sharing", f"Stop sharing “{u['device']}” with {who}?\n\n"
                                "The device disappears from their Xiaomi Home app. You can share it again later.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        self.status.setText("Stopping the share…")
        self.stop_btn.setEnabled(False)
        run_task(self.main.cloud.unshare_device, u["did"], u["userid"], self._pid(u["did"]),
                 on_done=lambda _r: self.reload(f"No longer shared with {who} ✓."),
                 on_error=lambda e: (self.status.setText(f"⚠ The share wasn't stopped: {friendly(e)}"),
                                     self._selection()), owner=self)
