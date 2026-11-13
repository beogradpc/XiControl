"""Widgets generated from a MIoT spec: one row per property, one button per action."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from xc.spec import Action, Prop, Service

from .theme import ToggleSwitch, no_arrows

INT_LIMIT = 2_000_000_000


class PropRow(QWidget):
    """Base row: label on the left, an editor on the right."""
    changed = Signal(object, object)  # (Prop, value)

    def __init__(self, prop: Prop, label: str | None = None, help_text: str | None = None, parent=None):
        super().__init__(parent)
        self.prop = prop
        self.value = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(1)
        self.label = QLabel(label or prop.label)
        self.label.setObjectName("RowLabel")
        self.label.setToolTip(f"{prop.name}  (siid {prop.siid}, piid {prop.piid}, {prop.format}, "
                              f"{'/'.join(prop.access)})")
        self.label.setMinimumWidth(170)
        left.addWidget(self.label)
        if help_text:
            h = QLabel(help_text)
            h.setObjectName("Small")
            h.setWordWrap(True)
            left.addWidget(h)
        lay.addLayout(left, 3)
        self.editor_box = QHBoxLayout()
        self.editor_box.setSpacing(8)
        lay.addLayout(self.editor_box, 2)
        self.build()

    def build(self):
        self.text = QLabel("—")
        self.text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.editor_box.addStretch(1)
        self.editor_box.addWidget(self.text)

    def set_value(self, value):
        self.value = value
        self.show_value(value)

    def show_value(self, value):
        self.text.setText(self.prop.value_text(value))

    def emit(self, value):
        self.changed.emit(self.prop, value)


class BoolRow(PropRow):
    def build(self):
        self.editor_box.addStretch(1)
        self.sw = ToggleSwitch()
        self.sw.toggled.connect(lambda on: self.emit(bool(on)))
        self.editor_box.addWidget(self.sw)

    def show_value(self, value):
        self.sw.setCheckedSilently(bool(value))


class EnumRow(PropRow):
    def build(self):
        self.combo = QComboBox()
        self.combo.setMinimumWidth(180)
        for v in self.prop.values:
            self.combo.addItem(self.prop.value_text(v.get("value")), v.get("value"))
        self.combo.setCurrentIndex(-1)
        self.combo.setPlaceholderText("—")
        self.combo.activated.connect(lambda i: self.emit(self.combo.itemData(i)))
        self.editor_box.addStretch(1)
        self.editor_box.addWidget(self.combo)

    def show_value(self, value):
        if self.combo.view().isVisible():
            return  # don't yank the list away while the user is choosing
        self.combo.blockSignals(True)
        i = self.combo.findData(value)
        if i < 0 and value is not None:
            self.combo.addItem(str(value), value)
            i = self.combo.count() - 1
        self.combo.setCurrentIndex(i)
        self.combo.blockSignals(False)


class SegmentRow(PropRow):
    """2-4 choices shown side by side as pill buttons."""

    def build(self):
        from PySide6.QtWidgets import QButtonGroup
        self.editor_box.addStretch(1)
        box = QFrame()
        box.setObjectName("SegBox")
        bl = QHBoxLayout(box)
        bl.setContentsMargins(3, 3, 3, 3)
        bl.setSpacing(2)
        self.group = QButtonGroup(self)
        self.btns = {}
        for v in self.prop.values:
            val = v.get("value")
            b = QPushButton(self.prop.value_text(val))
            b.setObjectName("Seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, x=val: self.emit(x))
            self.group.addButton(b)
            bl.addWidget(b)
            self.btns[val] = b
        self.editor_box.addWidget(box)

    def show_value(self, value):
        b = self.btns.get(value)
        if b is not None:
            b.setChecked(True)


class RangeRow(PropRow):
    """A slider with its value next to it, or a number box for very wide ranges."""

    def build(self):
        lo, hi, step = (list(self.prop.range) + [1])[:3]
        step = step or 1
        self.lo, self.hi, self.step = lo, hi, step
        self.scale = 1 if not self.prop.is_float and float(step).is_integer() else int(round(1 / step)) or 1
        span = (hi - lo) * self.scale
        self.slider = self.spin = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self._from_slider)
        if span <= 5000:
            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
            self.slider.setSingleStep(max(1, int(step * self.scale)))
            self.slider.setPageStep(max(1, int(span / 10)))
            self.slider.setMinimumWidth(160)
            self.slider.setMinimumHeight(28)
            self.slider.sliderReleased.connect(self._from_slider)
            self.slider.valueChanged.connect(self._slider_moved)
            self.editor_box.addWidget(self.slider, 1)
            self.val = QLabel("—")
            self.val.setObjectName("RowValue")
            self.val.setMinimumWidth(70)
            self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.editor_box.addWidget(self.val)
            return
        if self.prop.is_float or self.scale != 1:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(max(1, len(str(step).split(".")[-1])))
            self.spin.setSingleStep(step)
            self.spin.setRange(lo, hi)
        else:
            self.spin = QSpinBox()
            self.spin.setRange(int(max(lo, -INT_LIMIT)), int(min(hi, INT_LIMIT)))
            self.spin.setSingleStep(int(step))
        self.spin.setKeyboardTracking(False)
        no_arrows(self.spin)
        self.spin.setMinimumWidth(130)
        self.spin.editingFinished.connect(lambda: self._send(self.spin.value()))
        self.editor_box.addStretch(1)
        self.editor_box.addWidget(self.spin)

    def _slider_moved(self, raw):
        self.val.setText(self.prop.value_text(self._clean(raw / self.scale)))
        if not self.slider.isSliderDown():
            self._debounce.start()  # keyboard / wheel / click on the groove

    def _from_slider(self):
        self._debounce.stop()
        self._send(self.slider.value() / self.scale)

    def _clean(self, v):
        v = round(v / self.step) * self.step
        v = min(max(v, self.lo), self.hi)
        return round(v, 4) if self.prop.is_float else int(round(v))

    def _send(self, v):
        v = self._clean(v)
        if v != self.value:
            self.value = v
            self.emit(v)

    def show_value(self, value):
        if not isinstance(value, (int, float)):
            return
        if self.slider is not None:
            if self.slider.isSliderDown() or self._debounce.isActive():
                return
            self.slider.blockSignals(True)
            self.slider.setValue(int(round(value * self.scale)))
            self.slider.blockSignals(False)
            self.val.setText(self.prop.value_text(value))
        elif not self.spin.hasFocus():
            self.spin.blockSignals(True)
            self.spin.setValue(value if isinstance(self.spin, QDoubleSpinBox) else int(round(value)))
            self.spin.blockSignals(False)


class ColorRow(PropRow):
    def build(self):
        self.editor_box.addStretch(1)
        self.hex = QLabel("—")
        self.hex.setObjectName("Muted")
        self.btn = QPushButton()
        self.btn.setFixedSize(64, 28)
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.clicked.connect(self._pick)
        self.editor_box.addWidget(self.hex)
        self.editor_box.addWidget(self.btn)

    def _pick(self):
        start = QColor(f"#{(self.value or 0xFFFFFF) & 0xFFFFFF:06X}")
        c = QColorDialog.getColor(start, self, "Choose color")
        if c.isValid():
            v = (c.red() << 16) | (c.green() << 8) | c.blue()
            self.emit(max(1, v))

    def show_value(self, value):
        if isinstance(value, int):
            self.hex.setText(f"#{value & 0xFFFFFF:06X}")
            self.btn.setStyleSheet(f"background:#{value & 0xFFFFFF:06X}; border-radius:7px;")


class TextRow(PropRow):
    def build(self):
        self.edit = QLineEdit()
        self.edit.setMinimumWidth(180)
        self.edit.returnPressed.connect(lambda: self.emit(self.edit.text()))
        btn = QPushButton("Set")
        btn.clicked.connect(lambda: self.emit(self.edit.text()))
        self.editor_box.addWidget(self.edit, 1)
        self.editor_box.addWidget(btn)

    def show_value(self, value):
        if not self.edit.hasFocus():
            self.edit.setText("" if value is None else str(value))


def make_row(prop: Prop, label: str | None = None, help_text: str | None = None) -> PropRow:
    a = (label, help_text)
    if not prop.writable:
        return PropRow(prop, *a)
    if prop.is_bool:
        return BoolRow(prop, *a)
    if prop.values:
        short = len(prop.values) <= 4 and sum(len(prop.value_text(v.get("value"))) for v in prop.values) <= 36
        return SegmentRow(prop, *a) if short else EnumRow(prop, *a)
    if prop.is_color:
        return ColorRow(prop, *a)
    if prop.is_number and prop.range and len(prop.range) >= 2:
        return RangeRow(prop, *a)
    if prop.is_number:
        prop.range = [-INT_LIMIT, INT_LIMIT, 1]
        return RangeRow(prop, *a)
    return TextRow(prop, *a)


def ask_action_inputs(parent, action: Action, service: Service) -> list | None:
    """Ask the user for an action's input parameters. Returns [{'piid','value'}] or None if cancelled."""
    ins = []
    for piid in action.ins:
        prop = next((p for p in service.props if p.piid == piid), None)
        title = action.label
        if prop is None:
            text, ok = QInputDialog.getText(parent, title, f"Value for parameter {piid}:")
            if not ok:
                return None
            ins.append({"piid": piid, "value": text})
            continue
        if prop.is_bool:
            item, ok = QInputDialog.getItem(parent, title, prop.label, ["On", "Off"], 0, False)
            val = item == "On"
        elif prop.values:
            labels = [prop.value_text(v.get("value")) for v in prop.values]
            item, ok = QInputDialog.getItem(parent, title, prop.label, labels, 0, False)
            val = prop.values[labels.index(item)].get("value") if ok else None
        elif prop.is_number and prop.range:
            lo, hi = prop.range[0], prop.range[1]
            if prop.is_float:
                val, ok = QInputDialog.getDouble(parent, title, prop.label, lo, lo, hi, 2)
            else:
                val, ok = QInputDialog.getInt(parent, title, prop.label, int(lo), int(max(lo, -INT_LIMIT)),
                                              int(min(hi, INT_LIMIT)))
        else:
            val, ok = QInputDialog.getText(parent, title, prop.label)
        if not ok:
            return None
        ins.append({"piid": piid, "value": val})
    return ins


class ServiceCard(QFrame):
    """A card holding all rows + action buttons of one MIoT service."""
    prop_changed = Signal(object, object)
    action_clicked = Signal(object, object)  # (Action, Service)

    def __init__(self, service: Service, skip_props=(), skip_actions=(), embedded=False, parent=None):
        super().__init__(parent)
        self.setObjectName("" if embedded else "Card")
        self.service = service
        actions = [a for a in service.actions if (a.siid, a.aiid) not in skip_actions]
        props = [p for p in service.props if (p.readable or p.writable) and (p.siid, p.piid) not in skip_props]
        self.empty = not actions and not props
        self.rows: dict[tuple[int, int], PropRow] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*((0, 0, 0, 0) if embedded else (20, 16, 20, 14)))
        lay.setSpacing(4)
        single = len(props) == 1 and not actions and props[0].label.lower() == service.label.lower()
        if not single:
            head = QLabel(service.label)
            head.setObjectName("GroupTitle" if embedded else "H2")
            lay.addWidget(head)
        if actions:
            bar = QHBoxLayout()
            bar.setSpacing(8)
            for a in actions:
                b = QPushButton(a.label + ("…" if a.ins else ""))
                b.setCursor(Qt.PointingHandCursor)
                if embedded:
                    b.setObjectName("Link")
                b.setToolTip(f"{a.name}  (siid {a.siid}, aiid {a.aiid})")
                b.clicked.connect(lambda _=False, act=a: self.action_clicked.emit(act, self.service))
                bar.addWidget(b)
            bar.addStretch(1)
            lay.addSpacing(4)
            lay.addLayout(bar)
        first = True
        for p in props:
            if not first:
                sep = QFrame()
                sep.setObjectName("Sep")
                lay.addWidget(sep)
            first = False
            row = make_row(p)
            row.changed.connect(self.prop_changed)
            self.rows[(p.siid, p.piid)] = row
            lay.addWidget(row)

    def update_values(self, values: dict):
        for key, row in self.rows.items():
            if key in values:
                row.set_value(values[key])


def muted(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(True)
    return lab


def run_dialog(dlg) -> int:
    """Shows a modal dialog and frees it afterwards. Without this every dialog that was ever opened stays
    in memory (hidden) until the app quits. What the dialog holds can still be read by the caller: Qt only
    deletes it once control is back in the event loop."""
    try:
        return dlg.exec()
    finally:
        dlg.deleteLater()
