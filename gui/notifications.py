"""The Notifications window: what the Xiaomi Home app shows under Notifications (the newest message of every
device and the automation log, read from the Mi account) plus XiControl's own alerts. A device's row opens
its earlier messages."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from xc.catalog import category
from xc.cloud import friendly

from .controls import muted, run_dialog
from .workers import run_task


def when(t: float) -> str:
    """12:56 for today, otherwise 'Tue 12.11. 12:56'."""
    if not t:
        return ""
    lt = time.localtime(t)
    if time.strftime("%Y%j", lt) == time.strftime("%Y%j"):
        return time.strftime("%H:%M", lt)
    return time.strftime("%a %d.%m. %H:%M", lt)


def message_row(msg: dict, on_open=None, on_history=None) -> QFrame:
    """One notification: icon, device and place, text, time; a dot when it is new."""
    card = QFrame()
    card.setObjectName("Card")
    row = QHBoxLayout(card)
    row.setContentsMargins(12, 9, 12, 9)
    row.setSpacing(12)
    kind = msg.get("kind")
    icon = QLabel("⚡" if msg.get("type") == 13 else "🔔" if kind == "app" else category(msg.get("model", ""))[1])
    icon.setObjectName("IconSmall")
    icon.setFixedSize(32, 32)
    icon.setAlignment(Qt.AlignCenter)
    row.addWidget(icon, 0, Qt.AlignTop)
    col = QVBoxLayout()
    col.setSpacing(1)
    head = QHBoxLayout()
    place = " | ".join(x for x in (msg.get("home"), msg.get("room")) if x)
    who = QLabel(msg.get("device") or ("XiControl" if kind == "app" else "Xiaomi Home"))
    who.setObjectName("Small")
    head.addWidget(who)
    if msg.get("new"):
        dot = QLabel("●")
        dot.setObjectName("NewDot")
        dot.setToolTip("New")
        head.addWidget(dot)
    head.addStretch(1)
    t = QLabel(when(msg.get("time", 0)))
    t.setObjectName("Small")
    head.addWidget(t)
    col.addLayout(head)
    if place:
        pl = QLabel(place)
        pl.setObjectName("Small")
        col.addWidget(pl)
    title = QLabel(msg.get("title") or "")
    title.setObjectName("RowLabel")
    title.setWordWrap(True)
    title.setTextInteractionFlags(Qt.TextSelectableByMouse)
    col.addWidget(title)
    row.addLayout(col, 1)
    buttons = QVBoxLayout()
    buttons.setSpacing(2)
    if on_history is not None and msg.get("did") and msg.get("type") != 13:
        b = QPushButton("Earlier")
        b.setObjectName("Link")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip("Earlier notifications of this device")
        b.clicked.connect(lambda: on_history(msg))
        buttons.addWidget(b)
    if on_open is not None and msg.get("did"):
        b = QPushButton("Open")
        b.setObjectName("Link")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip("Open this device")
        b.clicked.connect(lambda: on_open(msg))
        buttons.addWidget(b)
    row.addLayout(buttons)
    return card


class _ListDialog(QDialog):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(640, 680)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(22, 20, 22, 16)
        self.lay.setSpacing(10)
        self.title = QLabel(title)
        self.title.setObjectName("H2")
        self.title.setWordWrap(True)
        self.lay.addWidget(self.title)
        self.note = muted("")
        self.lay.addWidget(self.note)
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("Scroll")
        self.list = QVBoxLayout(host)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(6)
        area.setWidget(host)
        self.lay.addWidget(area, 1)
        self.foot = QHBoxLayout()
        self.lay.addLayout(self.foot)

    def clear(self):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().hide()   # gone at once; the object itself is freed a moment later
                it.widget().deleteLater()

    def close_button(self):
        self.foot.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        self.foot.addWidget(bb)


class DeviceMessagesDialog(_ListDialog):
    """Earlier notifications of one device."""

    def __init__(self, main, did: str, name: str, parent=None):
        super().__init__(f"Notifications · {name}", parent)
        self.note.setText("Loading…")
        self.close_button()
        run_task(main.cloud.device_messages, did, 40, on_done=self._show,
                 on_error=lambda e: self.note.setText(f"⚠ {friendly(e)}"), owner=self)

    def _show(self, messages):
        self.note.setText(f"The last {len(messages)} notifications of this device." if messages else
                          "No earlier notifications.")
        for m in messages:
            m = dict(m, new=False, home="", room="")
            self.list.addWidget(message_row(m))
        self.list.addStretch(1)


class NotificationsDialog(_ListDialog):
    def __init__(self, main, parent=None):
        super().__init__("Notifications", parent)
        self.main = main
        refresh = QPushButton("Refresh")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.reload)
        self.foot.addWidget(refresh)
        self.close_button()
        self.rebuild()
        self.reload()

    def reload(self):
        if not self.main.signed_in:
            return
        self.note.setText("Loading…")
        self.main.load_notifications(then=self.rebuild, failed=lambda e: self.note.setText(f"⚠ {friendly(e)}"))

    def rebuild(self):
        self.clear()
        items = self.main.all_notifications()
        new = sum(1 for m in items if m.get("new"))
        if not self.main.signed_in:
            self.note.setText("Sign in to your Mi account to see the notifications of the Xiaomi Home app. "
                              "XiControl's own alerts are listed below.")
        else:
            self.note.setText((f"{new} new · " if new else "") + "The newest notification of every device, as in "
                              "the Xiaomi Home app. “Earlier” shows a device's older ones.")
        if not items:
            self.list.addWidget(muted("No notifications."))
        for m in items:
            self.list.addWidget(message_row(m, self._open, self._history if m.get("kind") != "app" else None))
        self.list.addStretch(1)

    def _open(self, msg):
        if self.main.cfg.device(msg["did"]) is None:
            self.note.setText("⚠ That device isn't in XiControl (use + → Sync devices from Mi account).")
            return
        self.accept()
        self.main.open_device(msg["did"])

    def _history(self, msg):
        run_dialog(DeviceMessagesDialog(self.main, msg["did"], msg.get("device") or "device", self))
