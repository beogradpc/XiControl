"""Light / dark themes in the spirit of the Xiaomi Home app: soft background, white rounded
cards, large type, and a sea-green accent used only for icons and power buttons."""
from __future__ import annotations

from PySide6.QtCore import Property, QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QAbstractButton, QApplication, QStyleFactory, QWidget

LIGHT = {
    "bg": "#F2F3F5", "card": "#FFFFFF", "fill": "#F4F5F7", "fill2": "#E9EBEE", "border": "#E6E8EB",
    "text": "#1B1D20", "muted": "#8C929B", "accent": "#16B3A1", "accent_hover": "#13A090",
    "accent_soft": "#E2F5F2", "on_accent": "#FFFFFF", "bad": "#E5484D", "bad_soft": "#FDECEC",
    "warn": "#D9822B", "warn_soft": "#FDF2E5", "ok": "#16B3A1", "ok_soft": "#E2F5F2", "shadow": "#14000000",
    "local": "#2F9E5B", "cloud": "#3B82F6",
}
DARK = {
    "bg": "#0F1113", "card": "#1B1E22", "fill": "#24282D", "fill2": "#2E3339", "border": "#2A2E34",
    "text": "#ECEEF1", "muted": "#8E959F", "accent": "#2CC7B4", "accent_hover": "#4AD3C2",
    "accent_soft": "#16332F", "on_accent": "#06201C", "bad": "#F06A6F", "bad_soft": "#3A1F21",
    "warn": "#F0A44E", "warn_soft": "#3A2E1E", "ok": "#2CC7B4", "ok_soft": "#16332F", "shadow": "#40000000",
    "local": "#55C98A", "cloud": "#6AA7FF",
}

P: dict = dict(LIGHT)
MODE = "light"


def c(name: str) -> str:
    return P[name]


def qc(name: str) -> QColor:
    return QColor(P[name])


def _qss(p: dict) -> str:
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; font-size: 10.5pt; color: {p['text']}; }}
QMainWindow, QDialog, QWidget#Page {{ background: {p['bg']}; }}
QScrollArea, QScrollArea > QWidget > QWidget#Scroll {{ background: transparent; border: none; }}
QLabel {{ background: transparent; }}
QLabel#Muted {{ color: {p['muted']}; }}
QLabel#Small {{ color: {p['muted']}; font-size: 9pt; }}
QLabel#Title {{ font-size: 21pt; font-weight: 600; }}
QPushButton#HomeTitle {{ font-size: 21pt; font-weight: 600; background: transparent; border: none; padding: 0px; text-align: left; color: {p['text']}; }}
QPushButton#HomeTitle:hover {{ color: {p['accent']}; }}
QLabel#PageTitle {{ font-size: 15pt; font-weight: 600; }}
QLabel#H2 {{ font-size: 12pt; font-weight: 600; }}
QLabel#SectionTitle {{ color: {p['muted']}; font-size: 9.5pt; font-weight: 600; }}
QLabel#HeroStatus {{ font-size: 19pt; font-weight: 600; }}
QLabel#StatValue {{ font-size: 15pt; font-weight: 600; }}
QLabel#TileName {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#TileStatus {{ color: {p['muted']}; font-size: 9pt; }}
QLabel#TileStatusBad {{ color: {p['bad']}; font-size: 9pt; }}
QLabel#TileMetrics {{ font-size: 9.5pt; }}
QLabel#StateOn {{ color: {p['on_accent']}; background: {p['accent']}; border-radius: 10px; padding: 3px 12px; font-size: 8.5pt; font-weight: 600; }}
QLabel#StateOff {{ color: {p['muted']}; background: {p['fill2']}; border-radius: 10px; padding: 3px 12px; font-size: 8.5pt; font-weight: 600; }}
QLabel#NewDot {{ color: #E5484D; font-size: 8pt; }}
QLabel#TileValue {{ font-size: 10.5pt; font-weight: 600; }}
QLabel#TileLabel {{ color: {p['muted']}; font-size: 8pt; }}
QLabel#IconSmall {{ background: {p['fill']}; border-radius: 16px; font-size: 13pt; }}
QLabel#IconSmallOn {{ background: {p['accent_soft']}; border-radius: 16px; font-size: 13pt; }}
QLabel#Badge {{ color: {p['accent']}; font-size: 12pt; font-weight: 700; padding: 2px 4px; }}
QLabel#Icon {{ background: {p['fill']}; border-radius: 22px; font-size: 18pt; }}
QLabel#IconOn {{ background: {p['accent_soft']}; border-radius: 22px; font-size: 18pt; }}
QLabel#HeroIcon {{ background: {p['fill']}; border-radius: 40px; font-size: 34pt; }}
QLabel#HeroIconOn {{ background: {p['accent_soft']}; border-radius: 40px; font-size: 34pt; }}
QFrame#BannerError {{ background: {p['bad_soft']}; border-radius: 14px; }}
QFrame#BannerError QLabel {{ color: {p['bad']}; }}
QFrame#BannerWarn {{ background: {p['warn_soft']}; border-radius: 14px; }}
QFrame#BannerWarn QLabel {{ color: {p['warn']}; }}
QFrame#BannerOk {{ background: {p['ok_soft']}; border-radius: 14px; }}
QFrame#BannerOk QLabel {{ color: {p['ok']}; }}
QLabel#TileIcon {{ font-size: 17pt; }}
QLabel#TileText {{ font-size: 10pt; }}
QLabel#TileTextOn {{ font-size: 10pt; color: {p['accent']}; font-weight: 600; }}
QLabel#RowValue {{ color: {p['muted']}; }}
QLabel#RowLabel {{ font-size: 10.5pt; }}
QLabel#GroupIcon {{ font-size: 13pt; color: {p['accent']}; min-width: 22px; }}
QFrame#SegBox {{ background: {p['fill']}; border-radius: 11px; }}
QPushButton#Seg {{ background: transparent; border-radius: 9px; padding: 6px 14px; color: {p['muted']}; }}
QPushButton#Seg:hover {{ color: {p['text']}; }}
QPushButton#Seg:checked {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']}; }}
QProgressBar#Life {{ background: {p['fill2']}; border: none; border-radius: 4px; }}
QProgressBar#Life::chunk {{ background: {p['accent']}; border-radius: 4px; }}
QProgressBar#Life[low="true"]::chunk {{ background: {p['bad']}; }}
QLabel#GroupTitle {{ font-weight: 600; padding-top: 6px; }}
QLabel#Toast {{ background: {p['text']}; color: {p['bg']}; border-radius: 18px; padding: 9px 18px; }}
QFrame#Card {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 18px; }}
QFrame#Tile {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 18px; }}
QFrame#Tile:hover {{ border-color: {p['accent']}; }}
QFrame#Stat {{ background: {p['fill']}; border: none; border-radius: 14px; }}
QFrame#Sep {{ background: {p['border']}; max-height: 1px; min-height: 1px; border: none; }}
QPushButton {{ background: {p['fill']}; border: none; border-radius: 12px; padding: 8px 16px; }}
QPushButton:hover {{ background: {p['fill2']}; }}
QPushButton:disabled {{ color: {p['muted']}; }}
QPushButton#Primary {{ background: {p['accent']}; color: {p['on_accent']}; font-weight: 600; }}
QPushButton#Primary:hover {{ background: {p['accent_hover']}; }}
QPushButton#Primary:disabled {{ background: {p['fill2']}; color: {p['muted']}; }}
QPushButton#Danger {{ color: {p['bad']}; }}
QPushButton#Link {{ background: transparent; color: {p['accent']}; padding: 4px 6px; font-weight: 600; }}
QPushButton#Link:hover {{ text-decoration: underline; }}
QPushButton#IconBtn {{ background: transparent; border-radius: 19px; min-width: 38px; max-width: 38px;
    min-height: 38px; max-height: 38px; padding: 0; font-size: 15pt; }}
QPushButton#IconBtn:hover {{ background: {p['fill2']}; }}
QPushButton#IconBtn::menu-indicator {{ image: none; width: 0; }}
QPushButton#Chip {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 14px; padding: 6px 16px; min-height: 18px; }}
QPushButton#Chip:checked {{ background: {p['text']}; color: {p['bg']}; border-color: {p['text']}; }}
QPushButton#ModeTile {{ background: {p['fill']}; border: 2px solid transparent; border-radius: 14px;
    padding: 8px 6px; min-height: 66px; }}
QPushButton#ModeTile:hover {{ background: {p['fill2']}; }}
QPushButton#ModeTile:checked {{ background: {p['accent_soft']}; border-color: {p['accent']}; color: {p['accent']}; font-weight: 600; }}
QPushButton#ActionTile {{ background: {p['fill']}; border: 2px solid transparent; border-radius: 14px; padding: 8px 6px; min-height: 66px; }}
QPushButton#ActionTile:hover {{ background: {p['fill2']}; }}
QPushButton#ActionTile:pressed {{ background: {p['accent_soft']}; border-color: {p['accent']}; }}
QPushButton#Swatch {{ border-radius: 15px; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; padding: 0;
    border: 2px solid {p['border']}; }}
QPushButton#MoreBtn {{ background: transparent; text-align: left; padding: 10px 4px; font-weight: 600; color: {p['muted']}; }}
QPushButton#MoreBtn:hover {{ color: {p['text']}; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {p['fill']}; border: 1px solid transparent; border-radius: 10px; padding: 6px 10px;
    selection-background-color: {p['accent']}; selection-color: {p['on_accent']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {p['accent']}; }}
QSpinBox QLineEdit, QDoubleSpinBox QLineEdit {{ padding: 0 0 0 8px; border: none; background: transparent; }}
QAbstractSpinBox[noArrows="true"]::up-button, QAbstractSpinBox[noArrows="true"]::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox QAbstractItemView {{ background: {p['card']}; border: 1px solid {p['border']};
    selection-background-color: {p['fill2']}; selection-color: {p['text']}; padding: 4px; }}
QMenu {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 22px 8px 14px; border-radius: 8px; }}
QMenu::item:selected {{ background: {p['fill']}; }}
QMenu::separator {{ height: 1px; background: {p['border']}; margin: 4px 8px; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {p['muted']}; padding: 8px 14px; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {p['text']}; border-bottom-color: {p['accent']}; }}
QSlider::groove:horizontal {{ height: 8px; background: {p['fill2']}; border-radius: 4px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 4px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; border: 1px solid {p['border']}; width: 22px; height: 22px;
    margin: -8px 0; border-radius: 11px; }}
QTableWidget {{ background: {p['card']}; border: 1px solid {p['border']}; border-radius: 12px; gridline-color: {p['border']}; }}
QHeaderView::section {{ background: {p['fill']}; border: none; border-bottom: 1px solid {p['border']}; padding: 7px; color: {p['muted']}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['fill2']}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: {p['fill2']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QToolTip {{ background: {p['card']}; color: {p['text']}; border: 1px solid {p['border']}; padding: 5px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {p['border']}; background: {p['fill']}; }}
QCheckBox::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid {p['border']}; background: {p['fill']}; }}
QRadioButton::indicator:checked {{ border: 1px solid {p['accent']}; background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5, stop:0 {p['accent']}, stop:0.5 {p['accent']}, stop:0.62 {p['fill']}, stop:1 {p['fill']}); }}
"""


def system_is_dark() -> bool:
    try:
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


def apply(app: QApplication, mode: str = "light"):
    """mode: 'light', 'dark' or 'system'."""
    global MODE
    dark = mode == "dark" or (mode == "system" and system_is_dark())
    MODE = "dark" if dark else "light"
    P.clear()
    P.update(DARK if dark else LIGHT)
    app.setStyle(QStyleFactory.create("Fusion"))
    pal = QPalette()
    for role, col in [
        (QPalette.Window, P["bg"]), (QPalette.Base, P["fill"]), (QPalette.AlternateBase, P["card"]),
        (QPalette.Text, P["text"]), (QPalette.WindowText, P["text"]), (QPalette.Button, P["fill"]),
        (QPalette.ButtonText, P["text"]), (QPalette.Highlight, P["accent"]),
        (QPalette.HighlightedText, P["on_accent"]), (QPalette.ToolTipBase, P["card"]),
        (QPalette.ToolTipText, P["text"]), (QPalette.PlaceholderText, P["muted"]),
    ]:
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(_qss(P))
    app.setFont(QFont("Segoe UI", 10))


def no_arrows(spin):
    """A number box without up/down arrows. (setButtonSymbols(NoButtons) together with this
    stylesheet shrinks the text field to 1 px, so the arrows are hidden by the stylesheet.)"""
    spin.setProperty("noArrows", True)
    repolish(spin)
    return spin


def repolish(widget, name: str | None = None):
    """Change a widget's objectName (style class) and re-apply the stylesheet."""
    if name is not None and widget.objectName() != name:
        widget.setObjectName(name)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class ToggleSwitch(QAbstractButton):
    """An on/off switch."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self._pos = 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)

    def sizeHint(self):
        return QSize(46, 26)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def setCheckedSilently(self, on: bool):
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

    def get_knob(self):
        return self._pos

    def set_knob(self, v):
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, 46, 26).translated((self.width() - 46) / 2, (self.height() - 26) / 2)
        off, on = qc("fill2"), qc("accent")
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t),
                     int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            col.setAlpha(110)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, 13, 13)
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(r.left() + 3 + t * 20, r.top() + 3, 20, 20))


class LinkMark(QWidget):
    """The small sign in front of a device's name that tells how it is reached: a green house = over the
    local network, a blue cloud = through the Mi cloud. Hidden while that isn't known."""

    def __init__(self, size: int = 16, parent=None):
        super().__init__(parent)
        self._size = size
        self.kind = ""
        self.setFixedSize(size, size)
        self.hide()

    def set_kind(self, kind: str, tip: str = ""):
        kind = kind if kind in ("local", "cloud") else ""
        self.setToolTip(tip if kind else "")
        if kind != self.kind:
            self.kind = kind
            self.update()
        self.setVisible(bool(kind))

    def paintEvent(self, _):
        if not self.kind:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self._size / 16.0, self._size / 16.0)   # drawn on a 16 x 16 grid
        p.setPen(Qt.NoPen)
        p.setBrush(qc(self.kind))
        if self.kind == "local":
            p.drawPolygon(QPolygonF([QPointF(8, 1.6), QPointF(15, 8.2), QPointF(13, 8.2), QPointF(13, 14.2),
                                     QPointF(9.6, 14.2), QPointF(9.6, 10.2), QPointF(6.4, 10.2),
                                     QPointF(6.4, 14.2), QPointF(3, 14.2), QPointF(3, 8.2), QPointF(1, 8.2)]))
        else:
            path = QPainterPath()
            path.setFillRule(Qt.WindingFill)
            path.addEllipse(QPointF(5.0, 9.6), 3.4, 3.4)
            path.addEllipse(QPointF(8.6, 7.0), 4.0, 4.0)
            path.addEllipse(QPointF(11.8, 9.8), 3.2, 3.2)
            path.addRect(QRectF(5.0, 9.6, 6.8, 3.4))
            p.drawPath(path)
        p.end()


class PowerButton(QAbstractButton):
    """Round power button: accent-filled when on, grey when off."""
    toggled_by_user = Signal(bool)

    def __init__(self, size: int = 36, parent=None):
        super().__init__(parent)
        self._size = size
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(size, size)
        self._known = False
        self.clicked.connect(lambda: self.toggled_by_user.emit(self.isChecked()))

    def set_state(self, on):
        """on: True / False / None (unknown)."""
        self._known = on is not None
        self.blockSignals(True)
        self.setChecked(bool(on))
        self.blockSignals(False)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = self._size
        on = self.isChecked() and self._known
        hover = self.underMouse()
        if on:
            bg = qc("accent_hover" if hover else "accent")
            fg = qc("on_accent")
        else:
            bg = qc("fill2" if hover else "fill")
            fg = qc("muted") if self._known else qc("fill2").darker(140)
        if not self.isEnabled():
            bg.setAlpha(120)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawEllipse(QRectF(0, 0, s, s))
        pen = QPen(fg, max(2.0, s / 15.0), Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        r = s * 0.24
        cx = cy = s / 2
        # power symbol: an open circle (gap at the top) and a vertical bar through the gap
        p.drawArc(QRectF(cx - r, cy - r + s * 0.03, 2 * r, 2 * r), 120 * 16, 300 * 16)
        p.drawLine(QPointF(cx, cy - r - s * 0.06), QPointF(cx, cy - s * 0.02))
