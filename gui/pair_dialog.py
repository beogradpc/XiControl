"""Add a new camera to the Mi account by showing it a QR code (see xc.pairing)."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, QTimeZone
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QVBoxLayout)

from xc import pairing
from xc.cloud import friendly

from .controls import muted
from .workers import run_task

PAIRING_MINUTES = 5


def qr_pixmap(text: str, size: int = 340) -> QPixmap:
    matrix = pairing.qr_matrix(text)
    n = len(matrix)
    quiet = 4
    scale = max(2, size // (n + 2 * quiet))
    img = QImage((n + 2 * quiet) * scale, (n + 2 * quiet) * scale, QImage.Format_RGB32)
    img.fill(QColor("#FFFFFF"))
    p = QPainter(img)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#000000"))
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                p.drawRect((x + quiet) * scale, (y + quiet) * scale, scale, scale)
    p.end()
    return QPixmap.fromImage(img)


def system_timezone() -> str:
    try:
        return bytes(QTimeZone.systemTimeZoneId()).decode("ascii")
    except Exception:  # noqa: BLE001
        return ""


class QrPairDialog(QDialog):
    def __init__(self, cloud, last_ssid: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add a new camera")
        self.setMinimumWidth(470)
        self.cloud = cloud
        self.new_did: str | None = None
        self.ssid_used = last_ssid
        self._key = None
        self._deadline = 0.0
        self._checking = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(12)
        t = QLabel("Add a new camera with a QR code")
        t.setObjectName("H2")
        lay.addWidget(t)
        self.intro = muted("For cameras that are set up by showing them a QR code (most Xiaomi cameras and "
                           "doorbells). The camera is added to your Mi account, so it also appears in the "
                           "Xiaomi Home app.\n\n1. Power the camera on and reset it (hold its reset button until "
                           "it says it is waiting for a connection).\n2. Enter the Wi-Fi it should join - "
                           "2.4 GHz for most models.")
        lay.addWidget(self.intro)
        form = QFormLayout()
        form.setVerticalSpacing(10)
        self.ssid = QLineEdit(last_ssid)
        self.ssid.setPlaceholderText("Wi-Fi name, exactly as the router shows it")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self.start)
        show = QCheckBox("Show")
        show.toggled.connect(lambda on: self.password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        prow = QHBoxLayout()
        prow.addWidget(self.password, 1)
        prow.addWidget(show)
        form.addRow("Wi-Fi name", self.ssid)
        form.addRow("Wi-Fi password", prow)
        lay.addLayout(form)
        self.form_note = muted("The password is only put into the QR code on this screen - it isn't saved or sent "
                               "anywhere.")
        lay.addWidget(self.form_note)

        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        self.qr.setStyleSheet("background:white; border-radius:10px; padding:6px;")
        self.qr.hide()
        lay.addWidget(self.qr, 0, Qt.AlignHCenter)
        self.status = muted("")
        self.status.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        self.go = QPushButton("Show QR code")
        self.go.setObjectName("Primary")
        self.go.clicked.connect(self.start)
        row.addWidget(self.go)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._check)

    def start(self):
        ssid, password = self.ssid.text().strip(), self.password.text()
        if not ssid or not password:
            self.status.setText("⚠ Enter the Wi-Fi name and password.")
            return
        self.go.setEnabled(False)
        self.status.setText("Getting a pairing key from Xiaomi…")
        run_task(self.cloud.get_bindkey, on_done=lambda key: self._show(key, ssid, password),
                 on_error=lambda e: (self.status.setText(f"⚠ {friendly(e)}"), self.go.setEnabled(True)), owner=self)

    def _show(self, key, ssid, password):
        self._key = key
        self.ssid_used = ssid
        text = pairing.qr_content(key, ssid, password, self.cloud.server, system_timezone())
        self.qr.setPixmap(qr_pixmap(text))
        self.qr.show()
        self.intro.setText("3. Hold this QR code 10-20 cm in front of the camera's lens until it confirms that "
                           "it has read the code. Then wait - the camera joins the Wi-Fi and reports to Xiaomi.")
        self.form_note.hide()
        self.go.setText("New QR code")
        self.go.setEnabled(True)
        self._deadline = time.monotonic() + PAIRING_MINUTES * 60
        self.status.setText("Waiting for the camera…")
        self.adjustSize()
        self.timer.start(4000)

    def _check(self):
        if self._key is None or self._checking:
            return
        if time.monotonic() > self._deadline:
            self.status.setText("⚠ No camera reported in. Check the Wi-Fi password and that the Wi-Fi is 2.4 GHz, "
                                "reset the camera and try again - or add it with the phone app.")
            return
        key = self._key
        self._checking = True
        run_task(self.cloud.check_bindkey, key, on_done=lambda r: self._checked(key, r),
                 on_error=lambda e: self._checked(key, {"error": friendly(e)}), owner=self)

    def _checked(self, key, res: dict):
        self._checking = False
        if key != self._key:
            return
        ret = res.get("ret", 0)
        if ret == 1 and res.get("bind_did"):
            self.new_did = str(res["bind_did"])
            self.timer.stop()
            self.status.setText("✓ The camera was added to your Mi account.")
            QTimer.singleShot(900, self.accept)
            return
        if ret < 0:
            self._key = None
            self.status.setText("⚠ Xiaomi rejected the pairing key (it may have expired). Press “New QR code”.")
            return
        if "error" in res:
            self.status.setText(f"Waiting for the camera…  ({res['error']})")
        try:
            wait = min(10, max(2, int(res.get("check_after") or 3)))
        except (TypeError, ValueError):
            wait = 3
        self.timer.start(wait * 1000)

    def reject(self):
        self.timer.stop()
        self._key = None
        super().reject()
