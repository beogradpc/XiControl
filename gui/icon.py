"""The app icon (window, taskbar, tray, .exe), drawn in code: a sea-green rounded square with the
same power symbol as the app's power buttons. save_ico() writes it as a multi-size .ico."""
from __future__ import annotations

import struct

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap

ACCENT = "#16B3A1"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def image(size: int, dimmed: bool = False) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#8C929B" if dimmed else ACCENT))
    m = size * 0.04
    p.drawRoundedRect(QRectF(m, m, size - 2 * m, size - 2 * m), size * 0.24, size * 0.24)
    pen = QPen(QColor("#FFFFFF"), max(1.6, size * 0.085), Qt.SolidLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    r = size * 0.24
    c = size / 2
    p.drawArc(QRectF(c - r, c - r + size * 0.04, 2 * r, 2 * r), 120 * 16, 300 * 16)
    p.drawLine(QPointF(c, c - r - size * 0.04), QPointF(c, c + size * 0.02))
    p.end()
    return img


def app_icon(dimmed: bool = False) -> QIcon:
    """dimmed: grey variant for the tray when something needs attention (e.g. sign-in)."""
    icon = QIcon()
    for s in SIZES:
        icon.addPixmap(QPixmap.fromImage(image(s, dimmed)))
    return icon


def save_ico(path: str, sizes=(16, 24, 32, 48, 64, 128, 256)):
    """Writes a Windows .ico with PNG-compressed images (supported since Windows Vista)."""
    from PySide6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:  # painting needs a GUI app object; no window is shown
        import sys
        save_ico._app = QGuiApplication([sys.argv[0], "-platform", "offscreen"])
    blobs = []
    for s in sizes:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QBuffer.WriteOnly)
        image(s).save(buf, "PNG")
        blobs.append((s, bytes(data)))
    out = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    for s, png in blobs:
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    out += b"".join(png for _s, png in blobs)
    with open(path, "wb") as f:
        f.write(out)
    return path
