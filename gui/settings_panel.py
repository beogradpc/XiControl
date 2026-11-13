"""The settings part of a device page: everything that isn't a main control, sorted into
plain-language groups (Settings, Timers, Maintenance, Actions, Technical details)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from xc.friendly import (GROUP_ORDER, action_group, action_label, needs_confirmation, prop_group, prop_label)
from xc.summary import Summary

from .controls import make_row
from .flow import FlowLayout

GROUP_ICONS = {"Settings": "⚙", "Timers": "⏱", "Maintenance": "🧰", "Actions": "▶", "Technical details": "ⓘ"}


def _sep():
    s = QFrame()
    s.setObjectName("Sep")
    return s


class LifeRow(QWidget):
    """Maintenance item: name, a progress bar for remaining life, and an optional reset button."""

    def __init__(self, label: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(6)
        top = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("RowLabel")
        top.addWidget(name)
        top.addStretch(1)
        self.text = QLabel("—")
        self.text.setObjectName("RowValue")
        top.addWidget(self.text)
        self.reset_box = QHBoxLayout()
        top.addLayout(self.reset_box)
        lay.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setObjectName("Life")
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.hide()
        lay.addWidget(self.bar)
        self.level = None   # Prop with %
        self.left = None    # Prop with time left

    def update_values(self, values):
        parts = []
        if self.level is not None:
            v = values.get((self.level.siid, self.level.piid))
            if isinstance(v, (int, float)):
                self.bar.show()
                self.bar.setValue(int(v))
                self.bar.setProperty("low", v <= 10)
                self.bar.style().unpolish(self.bar)
                self.bar.style().polish(self.bar)
                parts.append(f"{int(v)}%" + (" - replace soon" if v <= 10 else ""))
        if self.left is not None:
            v = values.get((self.left.siid, self.left.piid))
            if v is not None:
                parts.append(f"{self.left.value_text(v)} left")
        if parts:
            self.text.setText(" · ".join(parts))


TECHNICAL_INPUTS = ("id", "miid", "did", "time", "version", "hash", "code", "index", "idx")


def _is_technical(action, svc) -> bool:
    """Is this a command between the phone app and the device rather than one for people? Those ask for
    typed-in text (often JSON), internal ids or many values at once (e.g. a scale's "update user")."""
    props = {p.piid: p for p in svc.props}
    if len(action.ins) > 2:
        return True
    for piid in action.ins:
        p = props.get(piid)
        if p is None or p.format == "string" or any(w in p.name.split("-") for w in TECHNICAL_INPUTS):
            return True
    return False


class SettingsPanel(QWidget):
    prop_changed = Signal(object, object)
    action_requested = Signal(object, object)   # (Action, Service)

    def __init__(self, summary: Summary):
        super().__init__()
        self.summary = summary
        self.rows = {}          # (siid, piid) -> PropRow
        self.life_rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        main_name = summary.main.name if summary.main else ""

        groups: dict[str, list] = {g: [] for g in GROUP_ORDER}
        life: dict[str, LifeRow] = {}   # one LifeRow per consumable (service)
        for svc in summary.services:
            for p in svc.props:
                if (p.siid, p.piid) in summary.featured_props or not (p.readable or p.writable):
                    continue
                g = prop_group(p, svc)
                label, help_ = prop_label(p, svc, main_name)
                if g == "Maintenance":
                    key = svc.name if svc is not summary.main else p.name
                    row = life.get(key)
                    if row is None:
                        title = svc.label if svc is not summary.main else label
                        row = life[key] = LifeRow(title)
                        groups[g].append(("life", row))
                        self.life_rows.append(row)
                    if p.unit == "percentage" and row.level is None:
                        row.level = p
                    elif row.left is None:
                        row.left = p
                    else:
                        groups["Technical details"].append(("prop", p, label, help_))
                    continue
                groups[g].append(("prop", p, label, help_))
            for a in svc.actions:
                if (a.siid, a.aiid) in summary.featured_actions:
                    continue
                g = action_group(a)
                if g == "Actions" and _is_technical(a, svc):
                    g = "Technical details"   # needs internal ids or raw text: only useful to experts
                if g == "Maintenance":
                    target = life.get(svc.name) or (next(iter(life.values())) if len(life) == 1 else None)
                    if target is not None:
                        self._reset_button(target, a, svc)
                        continue
                groups[g].append(("action", a, svc))

        self.tech_box = None
        for g in GROUP_ORDER:
            items = groups[g]
            if not items:
                continue
            lay.addWidget(self._group_card(g, items))

    # -- building -------------------------------------------------------------------------------
    def _group_card(self, title, items):
        card = QFrame()
        card.setObjectName("Card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 16, 22, 16)
        cl.setSpacing(4)
        head = QHBoxLayout()
        icon = QLabel(GROUP_ICONS.get(title, ""))
        icon.setObjectName("GroupIcon")
        head.addWidget(icon)
        t = QLabel(title)
        t.setObjectName("H2")
        head.addWidget(t)
        head.addStretch(1)
        cl.addLayout(head)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(0)
        if title == "Technical details":
            toggle = QPushButton("Show")
            toggle.setObjectName("Link")
            toggle.setCursor(Qt.PointingHandCursor)
            head.addWidget(toggle)
            body.hide()
            toggle.clicked.connect(lambda: (body.setVisible(not body.isVisible()),
                                            toggle.setText("Hide" if body.isVisible() else "Show")))
            self.tech_box = body
            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(8)
            r = 0
            for it in items:
                if it[0] == "prop":
                    _k, p, label, _h = it
                    if p.writable:   # rare: a writable technical setting - give it a real control
                        row = make_row(p, label)
                        row.changed.connect(self.prop_changed)
                        self.rows[(p.siid, p.piid)] = row
                        grid.addWidget(row, r, 0, 1, 2)
                    else:
                        name = QLabel(label)
                        name.setObjectName("Muted")
                        val = QLabel("—")
                        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
                        grid.addWidget(name, r, 0)
                        grid.addWidget(val, r, 1)
                        self.rows[(p.siid, p.piid)] = _ValueLabel(p, val)
                    r += 1
                elif it[0] == "action":
                    _k, a, svc = it
                    grid.addWidget(self._action_button(a, svc), r, 0, 1, 2, Qt.AlignLeft)
                    r += 1
            grid.setColumnStretch(1, 1)
            bl.addLayout(grid)
            cl.addWidget(body)
            return card

        if title == "Actions":
            flow = FlowLayout(spacing=8)   # wraps to more rows instead of squeezing the button texts
            for _k, a, svc in items:
                flow.addWidget(self._action_button(a, svc, primary=True))
            bl.addLayout(flow)
            cl.addWidget(body)
            return card

        first = True
        for it in items:
            if not first:
                bl.addWidget(_sep())
            first = False
            if it[0] == "life":
                bl.addWidget(it[1])
            elif it[0] == "prop":
                _k, p, label, help_ = it
                row = make_row(p, label, help_)
                row.changed.connect(self.prop_changed)
                self.rows[(p.siid, p.piid)] = row
                bl.addWidget(row)
            elif it[0] == "action":
                _k, a, svc = it
                w = QWidget()
                wl = QHBoxLayout(w)
                wl.setContentsMargins(0, 8, 0, 8)
                label, help_ = action_label(a)
                col = QVBoxLayout()
                n = QLabel(label)
                n.setObjectName("RowLabel")
                col.addWidget(n)
                if help_:
                    h = QLabel(help_)
                    h.setObjectName("Small")
                    col.addWidget(h)
                wl.addLayout(col, 1)
                wl.addWidget(self._action_button(a, svc, text="Run"))
                bl.addWidget(w)
        cl.addWidget(body)
        return card

    def _action_button(self, a, svc, primary=False, text=None):
        label, help_ = action_label(a)
        b = QPushButton((text or label) + ("…" if a.ins else ""))
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(help_ or label)
        b.clicked.connect(lambda: self._run(a, svc))
        return b

    def _reset_button(self, row: LifeRow, a, svc):
        label, _help = action_label(a)
        b = QPushButton("Reset")
        b.setObjectName("Link")
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(label)
        b.clicked.connect(lambda: self._run(a, svc))
        row.reset_box.addWidget(b)

    def _run(self, a, svc):
        if needs_confirmation(a):
            label, help_ = action_label(a)
            text = f"{label}?"
            if help_:
                text += f"\n\n{help_}"
            if QMessageBox.question(self, label, text) != QMessageBox.Yes:
                return
        self.action_requested.emit(a, svc)

    # -- values ----------------------------------------------------------------------------------
    def update_values(self, values: dict):
        for key, row in self.rows.items():
            if key in values:
                row.set_value(values[key])
        for r in self.life_rows:
            r.update_values(values)


class _ValueLabel:
    def __init__(self, prop, label):
        self.prop, self.label = prop, label

    def set_value(self, v):
        self.label.setText(self.prop.value_text(v))
