"""The map of a robot vacuum on its device page: rooms in colours with their names, walls, no-go zones, the
path of the current cleaning, the dock and the robot. Read from the Mi cloud (see xc.vacuum_map)."""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from xc import vacuum_map as vm
from xc.cloud import CloudAuthError, friendly

from . import theme
from .workers import run_task

REFRESH_MS = 20_000
# soft room colours (light theme, dark theme) - neighbouring rooms get different numbers from the vacuum
ROOM_COLORS = [("#9ED8C9", "#2F6B5E"), ("#A9C8F5", "#2F4F80"), ("#F5D49A", "#7A5A22"), ("#E9B0C9", "#7A3654"),
               ("#C4B5F0", "#4B3F80"), ("#B8E0A1", "#3F6B2A"), ("#F2B8A0", "#7A4430"), ("#A7DDE8", "#2E6470")]


def _palette():
    dark = theme.MODE == "dark"
    i = 1 if dark else 0
    return {
        "bg": QColor("#20232A" if dark else "#F4F5F7"),
        "wall": QColor("#C9CDD4" if dark else "#5B6170"),
        "floor": QColor("#3A3F4A" if dark else "#DDE1E7"),
        "door": QColor("#4A505C" if dark else "#E8EAEE"),
        "rooms": [QColor(c[i]) for c in ROOM_COLORS],
        "path": QColor(255, 255, 255, 170) if dark else QColor(40, 60, 90, 150),
        "text": QColor("#F2F3F5" if dark else "#1C1F24"),
        "halo": QColor(0, 0, 0, 150) if dark else QColor(255, 255, 255, 200),
        "nogo": QColor(230, 70, 70, 70), "nogo_line": QColor(220, 60, 60),
        "robot": QColor("#18A999"), "dock": QColor("#F0A030"),
    }


def render_grid(m: dict) -> QImage:
    """One pixel per map cell, top row first (the vacuum stores the bottom row first)."""
    w, h = int(m["width"]), int(m["height"])
    cells = vm.grid(m)
    pal = _palette()
    colors = vm.room_colors(m)
    rooms = pal["rooms"]
    lut = {vm.UNKNOWN: 0, vm.WALL: pal["wall"].rgba(), vm.FLOOR: pal["floor"].rgba(), vm.DOOR: pal["door"].rgba()}
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0)
    for hrow in range(h):
        y = h - 1 - hrow
        base = hrow * w
        for x in range(w):
            v = cells[base + x]
            if v == vm.UNKNOWN:
                continue
            c = lut.get(v)
            if c is None:
                if v >= vm.ROOM_INDEX:
                    n = colors.get(v, v)
                    c = rooms[(n - 1) % len(rooms)].rgba() if n else rooms[v % len(rooms)].rgba()
                else:
                    c = pal["floor"].rgba()
                lut[v] = c
            img.setPixel(x, y, c)
    return img


class MapView(QWidget):
    def __init__(self):
        super().__init__()
        self.m: dict | None = None
        self.image: QImage | None = None
        self.message = "Loading the map…"
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_map(self, m: dict):
        self.m = m
        self.image = render_grid(m)
        self.message = ""
        self.update()

    def set_message(self, text: str):
        if self.m is None:
            self.message = text
            self.update()

    def _bounds(self):
        """The part of the grid that holds something (unknown cells around it are cut off)."""
        img = self.image
        w, h = img.width(), img.height()
        xs, ys = [], []
        for y in range(h):
            for x in range(w):
                if img.pixel(x, y) >> 24:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return QRectF(0, 0, w, h)
        pad = 3
        return QRectF(max(0, min(xs) - pad), max(0, min(ys) - pad),
                      min(w, max(xs) + pad + 1) - max(0, min(xs) - pad), min(h, max(ys) + pad + 1) - max(0, min(ys) - pad))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = _palette()
        p.setPen(Qt.NoPen)
        p.setBrush(pal["bg"])
        p.drawRoundedRect(self.rect(), 12, 12)
        if self.image is None or self.m is None:
            p.setPen(pal["text"])
            p.drawText(self.rect().adjusted(16, 16, -16, -16), Qt.AlignCenter | Qt.TextWordWrap, self.message)
            return
        src = self._bounds()
        margin = 14
        avail = self.rect().adjusted(margin, margin, -margin, -margin)
        scale = min(avail.width() / src.width(), avail.height() / src.height())
        dw, dh = src.width() * scale, src.height() * scale
        ox = avail.x() + (avail.width() - dw) / 2
        oy = avail.y() + (avail.height() - dh) / 2
        p.drawImage(QRectF(ox, oy, dw, dh), self.image, src)

        def pt(x_mm, y_mm):
            col, row = vm.to_cell(self.m, x_mm, y_mm)
            return QPointF(ox + (col - src.x()) * scale, oy + (row - src.y()) * scale)

        m = self.m
        # no-go zones and virtual walls
        for zone in m.get("fb_regions") or []:
            pts = zone.get("fb_point") or []
            if len(pts) >= 6:
                poly = QPolygonF([pt(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)])
                p.setPen(QPen(pal["nogo_line"], 1.5))
                p.setBrush(pal["nogo"])
                p.drawPolygon(poly)
        for wall in m.get("fb_walls") or []:
            w = wall.get("wall_points") or []
            if len(w) >= 4:
                p.setPen(QPen(pal["nogo_line"], 3, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(pt(w[0], w[1]), pt(w[2], w[3]))
        # path of the current cleaning
        points = vm.path_points(m)
        if len(points) > 1:
            path = QPainterPath(pt(points[0]["x"], points[0]["y"]))
            for q in points[1:]:
                path.lineTo(pt(q["x"], q["y"]))
            p.setPen(QPen(pal["path"], max(1.2, scale * 0.35), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        # room names
        f = QFont(self.font())
        f.setPointSizeF(max(8.0, min(11.0, scale * 1.6)))
        f.setBold(True)
        p.setFont(f)
        for r in vm.rooms(m):
            if r["x"] is None or r["y"] is None:
                continue
            c = pt(r["x"], r["y"])
            rect = QRectF(c.x() - 80, c.y() - 12, 160, 24)
            p.setPen(pal["halo"])
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                p.drawText(rect.translated(dx, dy), Qt.AlignCenter, r["name"])
            p.setPen(pal["text"])
            p.drawText(rect, Qt.AlignCenter, r["name"])
        # dock and robot
        size = max(10.0, min(22.0, scale * 5))
        if m.get("have_pile"):
            c = pt(m.get("pile_x") or 0, m.get("pile_y") or 0)
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(pal["dock"])
            p.drawRoundedRect(QRectF(c.x() - size / 2, c.y() - size / 2, size, size), 4, 4)
        pos = m.get("position")
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            c = pt(pos["x"], pos["y"])
            p.setPen(QPen(QColor("white"), 2))
            p.setBrush(pal["robot"])
            p.drawEllipse(c, size / 2 + 1, size / 2 + 1)
            yaw = pos.get("yaw")
            if isinstance(yaw, (int, float)):
                a = math.radians(yaw / 100.0)   # hundredths of a degree, counter-clockwise; y points up
                tip = QPointF(c.x() + math.cos(a) * size * 0.75, c.y() - math.sin(a) * size * 0.75)
                p.setPen(QPen(QColor("white"), 2.2, Qt.SolidLine, Qt.RoundCap))
                p.drawLine(c, tip)
        p.end()


class MapCard(QFrame):
    """The card on a vacuum's page: the map, when it was read, and a refresh button."""

    def __init__(self, page, spec):
        super().__init__()
        self.setObjectName("Card")
        self.page, self.spec = page, spec
        self._busy = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 16)
        lay.setSpacing(8)
        head = QHBoxLayout()
        t = QLabel("Map")
        t.setObjectName("SectionTitle")
        head.addWidget(t)
        self.status = QLabel("")
        self.status.setObjectName("Small")
        head.addWidget(self.status, 1)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("IconBtn")
        self.refresh_btn.setToolTip("Read the map again")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(self.load)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)
        self.view = MapView()
        lay.addWidget(self.view)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.load)
        self.timer.start(REFRESH_MS)
        self.load()

    def load(self):
        if self._busy or not self.isVisibleTo(self.window()) and self.view.m is not None:
            return
        main = self.page.main
        self._busy = True
        model = self.page.info.get("model", "")
        run_task(vm.fetch_map, main.cloud if main.signed_in else None, self.page.did, model, self.spec,
                 on_done=self._loaded, on_error=self._failed, owner=self)

    def _loaded(self, m):
        self._busy = False
        self.view.set_map(m)
        names = [r["name"] for r in vm.rooms(m)]
        rooms = f"{len(names)} room{'s' if len(names) != 1 else ''}" if names else "no rooms yet"
        self.status.setText(f"   {rooms} · updated {time.strftime('%H:%M:%S')}")

    def _failed(self, exc):
        self._busy = False
        if isinstance(exc, CloudAuthError):
            self.page.cloud_expired.emit()
        text = str(exc) if isinstance(exc, vm.MapError) else f"The map couldn't be read: {friendly(exc)}"
        self.view.set_message(text)
        if self.view.m is not None:
            self.status.setText(f"   ⚠ {text[:80]}")

    def stop(self):
        self.timer.stop()
