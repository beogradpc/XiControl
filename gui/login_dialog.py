"""Sign in to a Mi account (QR code or password) to fetch devices and tokens."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from xc.cloud import SERVERS, CaptchaRequired, TwoFactorRequired, XiaomiCloud, friendly

from .controls import muted, run_dialog
from .workers import run_task


class CaptchaDialog(QDialog):
    def __init__(self, image: bytes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Captcha")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Type the characters shown in the picture:"))
        pic = QLabel()
        pm = QPixmap()
        pm.loadFromData(image)
        pic.setPixmap(pm.scaledToHeight(80, Qt.SmoothTransformation) if not pm.isNull() else pm)
        pic.setStyleSheet("background:white; border-radius:6px; padding:6px;")
        lay.addWidget(pic, 0, Qt.AlignCenter)
        self.edit = QLineEdit()
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class LoginDialog(QDialog):
    def __init__(self, server: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to your Mi account")
        self.setMinimumSize(480, 600)
        self.cloud: XiaomiCloud | None = None
        self._cancel = False
        self._qr_gen = 0
        self._code_hint = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(12)
        t = QLabel("Sign in to your Mi account")
        t.setObjectName("H2")
        lay.addWidget(t)
        lay.addWidget(muted("Used to download your device list and the secret tokens needed to control "
                            "them over your home network. Your password is never stored."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Region (server)"))
        self.server = QComboBox()
        for code, name in SERVERS.items():
            self.server.addItem(f"{name}  ({code})", code)
        self.server.setCurrentIndex(max(0, self.server.findData(server)))
        self.server.currentIndexChanged.connect(lambda _i: self.tabs.currentIndex() == 0 and self.start_qr())
        row.addWidget(self.server, 1)
        lay.addLayout(row)
        lay.addWidget(muted("Pick the region selected in the Mi Home app (Profile → Settings → Region). "
                            "Europe is correct for Serbia and most of Europe."))

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # QR tab
        qr = QWidget()
        ql = QVBoxLayout(qr)
        ql.setAlignment(Qt.AlignHCenter)
        self.qr_pic = QLabel("Loading QR code…")
        self.qr_pic.setFixedSize(260, 260)
        self.qr_pic.setAlignment(Qt.AlignCenter)
        self.qr_pic.setStyleSheet("background:white; color:#333; border-radius:10px;")
        ql.addWidget(self.qr_pic, 0, Qt.AlignHCenter)
        self.qr_status = muted("Open the Mi Home app on your phone, tap + (or the scan icon) and scan this code.")
        self.qr_status.setAlignment(Qt.AlignCenter)
        ql.addWidget(self.qr_status)
        qrow = QHBoxLayout()
        again = QPushButton("New QR code")
        again.clicked.connect(self.start_qr)
        qrow.addWidget(again)
        self.open_link = QPushButton("Open sign-in page in browser")
        self.open_link.setEnabled(False)
        qrow.addWidget(self.open_link)
        ql.addLayout(qrow)
        self.tabs.addTab(qr, "QR code (recommended)")
        self.tabs.setMinimumHeight(390)

        # Password tab
        pw = QWidget()
        pl = QFormLayout(pw)
        pl.setVerticalSpacing(10)
        self.user = QLineEdit()
        self.user.setPlaceholderText("E-mail, phone number or Xiaomi ID")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self.password_login)
        pl.addRow("Account", self.user)
        pl.addRow("Password", self.password)
        self.pw_btn = QPushButton("Sign in")
        self.pw_btn.setObjectName("Primary")
        self.pw_btn.clicked.connect(self.password_login)
        pl.addRow("", self.pw_btn)
        self.pw_status = muted("")
        pl.addRow(self.pw_status)
        self.tabs.addTab(pw, "Password")

        bb = QDialogButtonBox(QDialogButtonBox.Cancel)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.adjustSize()
        self.start_qr()

    def _new_cloud(self) -> XiaomiCloud:
        return XiaomiCloud(self.server.currentData())

    # -- QR -------------------------------------------------------------------
    def start_qr(self):
        self._qr_gen += 1
        gen = self._qr_gen
        cloud = self._new_cloud()
        self.qr_pic.setPixmap(QPixmap())
        self.qr_pic.setText("Loading QR code…")
        self.qr_status.setText("Open the Mi Home app on your phone, tap + (or the scan icon) and scan this code.")

        def shown(data):
            if gen != self._qr_gen:
                return
            pm = QPixmap()
            pm.loadFromData(data["image"])
            self.qr_pic.setPixmap(pm.scaled(250, 250, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            url = data.get("login_url")
            if url:
                self.open_link.setEnabled(True)
                try:
                    self.open_link.clicked.disconnect()
                except (RuntimeError, TypeError):
                    pass
                self.open_link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(url)))
            run_task(cloud.qr_wait, data["lp"], data["timeout"],
                     lambda: self._cancel or gen != self._qr_gen,
                     on_done=lambda _r: gen == self._qr_gen and self._success(cloud),
                     on_error=lambda e: gen == self._qr_gen and self.qr_status.setText(f"⚠ {friendly(e)}"),
                     owner=self)

        run_task(cloud.qr_start, on_done=shown,
                 on_error=lambda e: gen == self._qr_gen and (self.qr_pic.setText("Could not load QR code"),
                                                              self.qr_status.setText(f"⚠ {friendly(e)}")),
                 owner=self)

    # -- password ---------------------------------------------------------------
    def password_login(self, captcha: str | None = None):
        user, pw = self.user.text().strip(), self.password.text()
        if not user or not pw:
            self.pw_status.setText("Enter your account and password.")
            return
        if not captcha or not isinstance(captcha, str):
            captcha = None
            self.cloud_pw = self._new_cloud()
        cloud = self.cloud_pw
        self.pw_btn.setEnabled(False)
        self.pw_status.setText("Signing in…")
        run_task(cloud.password_login, user, pw, captcha,
                 on_done=lambda _r: self._success(cloud), on_error=self._pw_error, owner=self)

    def _pw_error(self, exc):
        self.pw_btn.setEnabled(True)
        if isinstance(exc, CaptchaRequired):
            d = CaptchaDialog(exc.image, self)
            if run_dialog(d) == QDialog.Accepted and d.edit.text().strip():
                self.password_login(d.edit.text().strip())
            else:
                self.pw_status.setText("Cancelled.")
            return
        if isinstance(exc, TwoFactorRequired):
            self._code_hint = str(exc)
            self._ask_code(self._code_hint)
            return
        if self._code_hint and "not accepted" in str(exc):
            self._ask_code(f"{exc}.\n{self._code_hint}")
            return
        self.pw_status.setText(f"⚠ {friendly(exc)}\nIf this keeps failing, use the QR code tab instead.")

    def _ask_code(self, text: str):
        code, ok = QInputDialog.getText(self, "Verification code", f"{text}\nEnter it here:")
        if ok and code.strip():
            self.pw_status.setText("Verifying…")
            self.pw_btn.setEnabled(False)
            run_task(self.cloud_pw.submit_2fa, code.strip(),
                     on_done=lambda _r: self._success(self.cloud_pw), on_error=self._pw_error, owner=self)
        else:
            self._code_hint = ""
            self.pw_status.setText("Cancelled.")

    def _success(self, cloud: XiaomiCloud):
        self.cloud = cloud
        self._cancel = True
        self.accept()

    def reject(self):
        self._cancel = True
        super().reject()
