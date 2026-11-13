"""Device info and raw-command dialogs, reached from the ⋯ menu on a device page."""
from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from xc.cloud import friendly

from .controls import muted
from .workers import run_task


class InfoDialog(QDialog):
    def __init__(self, info: dict, ctl, state, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Device info")
        self.setMinimumWidth(520)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(9)
        tok = info.get("token") or ""
        mode = {"auto": "Automatic (network first, cloud as backup)", "local": "Local network only",
                "cloud": "Mi cloud only"}.get(info.get("mode", "auto"))
        via = {"local": "your local network", "cloud": "Mi cloud"}.get(state.transport, "—")
        rows = [("Name", info.get("name")), ("Room", info.get("room")), ("Model", info.get("model")),
                ("Device ID", info.get("did")), ("IP address", info.get("ip")), ("MAC", info.get("mac")),
                ("Connection setting", mode), ("Last connected via", via)]
        for k, v in rows:
            lab = QLabel(str(v or "—"))
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            name = QLabel(k)
            name.setObjectName("Muted")
            form.addRow(name, lab)
        self.tok = QLabel((tok[:4] + "•" * 24 + tok[-4:]) if tok else "—")
        self.tok.setTextInteractionFlags(Qt.TextSelectableByMouse)
        trow = QHBoxLayout()
        trow.addWidget(self.tok, 1)
        if tok:
            show = QPushButton("Show")
            show.setObjectName("Link")
            show.clicked.connect(lambda: self.tok.setText(tok))
            trow.addWidget(show)
        name = QLabel("Token")
        name.setObjectName("Muted")
        form.addRow(name, trow)
        lay.addLayout(form)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.hide()
        self.out.setMinimumHeight(160)
        row = QHBoxLayout()
        if ctl.local_possible():
            b = QPushButton("Ask the device for its details")
            b.clicked.connect(self._read)
            row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addWidget(self.out)
        lay.addLayout(row)

    def _read(self):
        self.out.show()
        self.out.setPlainText("Asking the device…")
        run_task(self.ctl.info_local,
                 on_done=lambda r: self.out.setPlainText(json.dumps(r, indent=2, ensure_ascii=False)),
                 on_error=lambda e: self.out.setPlainText(f"Error: {friendly(e)}"), owner=self)


class ConsoleDialog(QDialog):
    """Send any raw miIO / MIoT command."""

    PRESETS = [
        ("miIO.info", "[]"),
        ("get_properties", '[{"did": "DID", "siid": 2, "piid": 1}]'),
        ("set_properties", '[{"did": "DID", "siid": 2, "piid": 1, "value": true}]'),
        ("action", '{"did": "DID", "siid": 2, "aiid": 1, "in": []}'),
        ("get_status", "[]"),
        ("get_prop", '["power"]'),
    ]

    def __init__(self, ctl, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Send commands")
        self.resize(720, 520)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.addWidget(muted("For advanced use: send a raw command to the device. "
                            "“DID” in the parameters is replaced with this device's ID."))
        row = QHBoxLayout()
        self.preset = QComboBox()
        self.preset.addItem("Examples…")
        for m, _p in self.PRESETS:
            self.preset.addItem(m)
        self.preset.activated.connect(self._preset)
        row.addWidget(self.preset)
        self.method = QLineEdit()
        self.method.setPlaceholderText("Command, e.g. get_properties")
        self.method.returnPressed.connect(self.send)
        row.addWidget(self.method, 1)
        send = QPushButton("Send")
        send.setObjectName("Primary")
        send.clicked.connect(self.send)
        row.addWidget(send)
        lay.addLayout(row)
        self.params = QPlainTextEdit()
        self.params.setPlaceholderText("Parameters as JSON, e.g. []")
        self.params.setMaximumHeight(100)
        lay.addWidget(self.params)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setStyleSheet("font-family: Consolas, 'Cascadia Mono', monospace;")
        lay.addWidget(self.out, 1)

    def _preset(self, i):
        if i <= 0:
            return
        m, p = self.PRESETS[i - 1]
        self.method.setText(m)
        self.params.setPlainText(p)
        self.preset.setCurrentIndex(0)

    def send(self):
        method = self.method.text().strip()
        if not method:
            return
        text = self.params.toPlainText().strip().replace('"DID"', json.dumps(self.ctl.did)) or "[]"
        try:
            params = json.loads(text)
        except ValueError as exc:
            self.out.appendPlainText(f"✗ Parameters are not valid JSON: {exc}\n")
            return
        self.out.appendPlainText(f"→ {method} {json.dumps(params)}")
        run_task(self.ctl.raw, method, params,
                 on_done=lambda r: self.out.appendPlainText(
                     f"← [{self.ctl.last_transport}] {json.dumps(r, indent=2, ensure_ascii=False)}\n"),
                 on_error=lambda e: self.out.appendPlainText(f"✗ {friendly(e)}\n"), owner=self)
