"""Consumption card on the page of a plug (or another metering device): bars per day, week, month or year."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from xc import energy
from xc.cloud import CloudAuthError, friendly

from . import theme
from .workers import run_task

PERIOD_NAMES = {"day": "Days", "week": "Weeks", "month": "Months", "year": "Years"}


class BarChart(QWidget):
    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, float, str]] = []
        self.unit = None
        self.message = "Loading…"
        self._hover = -1
        self.setMinimumHeight(230)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)

    def set_rows(self, rows, unit, message=""):
        self.rows, self.unit, self.message, self._hover = rows, unit, message, -1
        self.update()

    def _geometry(self):
        left, right, top, bottom = 8, 8, 26, 26
        n = max(1, len(self.rows))
        w = (self.width() - left - right) / n
        return left, top, w, self.height() - top - bottom

    def mouseMoveEvent(self, e):
        left, _top, w, _h = self._geometry()
        i = int((e.position().x() - left) // w) if self.rows else -1
        i = i if 0 <= i < len(self.rows) else -1
        if i != self._hover:
            self._hover = i
            self.setToolTip(f"{self.rows[i][2]}: {energy.number(self.rows[i][1], self.unit)}" if i >= 0 else "")
            self.update()

    def leaveEvent(self, _e):
        self._hover = -1
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = theme.MODE == "dark"
        text = QColor("#F2F3F5" if dark else "#1C1F24")
        muted = QColor("#9AA0AA" if dark else "#7A808C")
        bar, bar_hover = QColor("#18A999"), QColor("#12C2AE")
        grid = QColor(255, 255, 255, 26) if dark else QColor(0, 0, 0, 20)
        if not self.rows:
            p.setPen(muted)
            p.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        left, top, w, h = self._geometry()
        peak = max(v for _l, v, _t in self.rows) or 1.0
        small = QFont(self.font())
        small.setPointSizeF(7.5)
        p.setFont(small)
        for frac in (0.5, 1.0):   # two faint guide lines; the top one says how much it stands for
            y = top + h - h * frac
            p.setPen(QPen(grid, 1))
            p.drawLine(int(left), int(y), int(self.width() - 8), int(y))
        p.setPen(muted)
        p.drawText(QRectF(left, top - 15, 200, 12), Qt.AlignLeft | Qt.AlignVCenter,
                   "max " + energy.number(peak, self.unit))
        n = len(self.rows)
        every = max(1, round(n / max(1, (self.width() - 16) // 46)))   # as many labels as fit
        gap = min(6.0, w * 0.22)
        for i, (label, v, _long) in enumerate(self.rows):
            x = left + i * w
            bh = max(2.0, h * v / peak) if v > 0 else 1.0
            r = QRectF(x + gap / 2, top + h - bh, w - gap, bh)
            p.setPen(Qt.NoPen)
            p.setBrush(bar_hover if i == self._hover else bar)
            p.drawRoundedRect(r, min(4.0, r.width() / 2), min(4.0, r.width() / 2))
            if i % every == 0 or i == n - 1 and (n - 1) % every > every // 2:
                p.setPen(muted)
                p.drawText(QRectF(x - 20, top + h + 6, w + 40, 14), Qt.AlignCenter, label)
            if (i == self._hover or n <= 12) and r.top() - 15 > top - 2:   # not over the "max" text
                p.setPen(text if i == self._hover else muted)
                p.drawText(QRectF(x - 30, r.top() - 15, w + 60, 13), Qt.AlignCenter,
                           energy.number(v, "" if self.unit else None) if n > 8 and i != self._hover
                           else energy.number(v, self.unit))
        p.end()


class EnergyCard(QFrame):
    def __init__(self, page, source: dict):
        super().__init__()
        self.setObjectName("Card")
        self.page, self.source = page, source
        self.data = None
        self.period = "day"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Consumption")
        t.setObjectName("SectionTitle")
        head.addWidget(t)
        head.addStretch(1)
        box = QFrame()
        box.setObjectName("SegBox")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(3, 3, 3, 3)
        bl.setSpacing(2)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key in energy.PERIODS:
            b = QPushButton(PERIOD_NAMES[key])
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.set_period(k))
            self.group.addButton(b)
            bl.addWidget(b)
            self.buttons[key] = b
        self.buttons["day"].setChecked(True)
        head.addWidget(box)
        lay.addLayout(head)
        self.chart = BarChart()
        lay.addWidget(self.chart)
        self.info = QLabel("")
        self.info.setObjectName("Small")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.load()

    def load(self):
        main = self.page.main
        if not main.signed_in:
            self.chart.set_rows([], "", "Sign in to your Mi account to see the consumption history.")
            return
        run_task(energy.fetch, main.cloud, self.page.did, self.source, on_done=self._loaded, on_error=self._failed,
                 owner=self)

    def _loaded(self, data):
        self.data = data
        self.set_period(self.period)

    def _failed(self, exc):
        if isinstance(exc, CloudAuthError):
            self.page.cloud_expired.emit()
        self.chart.set_rows([], "", f"The consumption history couldn't be read: {friendly(exc)}")

    def set_period(self, period: str):
        self.period = period
        self.buttons[period].setChecked(True)
        if self.data is None:
            return
        rows = energy.bars(self.data, period)
        unit = self.data["unit"]
        self.chart.set_rows(rows, unit, "The Mi cloud has no consumption recorded for this device yet.")
        note = "" if unit else "  ·  The unit of this maker isn't known - the numbers are as the device reports them."
        self.info.setText(energy.summary(rows, period, unit) + note if rows else "")
