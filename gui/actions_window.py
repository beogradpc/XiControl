"""The Actions window: actions made in XiControl (several commands with one click, can be pinned to the home
screen), the tap-to-run scenes of the Xiaomi Home app and its automations with their on/off switches."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)
from shiboken6 import isValid

from xc.actions import device_commands, new_action, step_from, wait_step
from xc.cloud import friendly

from .controls import muted, run_dialog
from .theme import ToggleSwitch, no_arrows
from .workers import run_task


def _row_button(text, tip, fn, name=None):
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    if name:
        b.setObjectName(name)
    b.clicked.connect(fn)
    return b


class ActionEditor(QDialog):
    """Name and steps of one action. Steps are picked from what each device offers."""

    def __init__(self, main, action: dict | None = None, did: str | None = None, parent=None):
        super().__init__(parent)
        self.main = main
        self.action = copy.deepcopy(action) if action else new_action()
        self.setWindowTitle("Edit action" if action else "New action")
        self.resize(640, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        t = QLabel("Action")
        t.setObjectName("H2")
        lay.addWidget(t)
        self.name = QLineEdit(self.action.get("name", ""))
        self.name.setPlaceholderText("Name, e.g. “Leaving home”")
        self.name.textChanged.connect(self._update)
        lay.addWidget(self.name)
        lay.addWidget(muted("The steps run one after the other. A step that fails (e.g. the device is offline) "
                            "doesn't stop the others."))
        self.steps = QListWidget()
        self.steps.setSelectionMode(QAbstractItemView.SingleSelection)
        lay.addWidget(self.steps, 1)
        row = QHBoxLayout()
        for text, tip, fn in (("↑", "Move up", lambda: self._move(-1)), ("↓", "Move down", lambda: self._move(1)),
                              ("Remove step", "Remove the selected step", self._remove)):
            row.addWidget(_row_button(text, tip, fn))
        row.addStretch(1)
        lay.addLayout(row)

        box = QFrame()
        box.setObjectName("Card")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(16, 12, 16, 12)
        st = QLabel("Add a step")
        st.setObjectName("SectionTitle")
        bl.addWidget(st)
        pick = QHBoxLayout()
        self.device = QComboBox()
        self.device.setMinimumWidth(220)
        devices = sorted(main.cfg.devices, key=lambda d: ((d.get("room") or "~"), (d.get("name") or "").lower()))
        for d in devices:
            room = f"   ({d['room']})" if d.get("room") else ""
            self.device.addItem(f"{d.get('name') or d['did']}{room}", str(d["did"]))
        self.device.currentIndexChanged.connect(self._fill_commands)
        pick.addWidget(self.device, 2)
        self.command = QComboBox()
        self.command.setMinimumWidth(200)
        self.command.currentIndexChanged.connect(self._command_changed)
        pick.addWidget(self.command, 2)
        self.value = QSpinBox()
        no_arrows(self.value)
        self.value.setFixedWidth(90)
        self.value.hide()
        pick.addWidget(self.value)
        self.add_btn = _row_button("Add", "Add this step", self._add, "Primary")
        pick.addWidget(self.add_btn)
        bl.addLayout(pick)
        wait = QHBoxLayout()
        wait.addWidget(QLabel("…or a pause of"))
        self.wait = QSpinBox()
        no_arrows(self.wait)
        self.wait.setRange(1, 600)
        self.wait.setValue(5)
        self.wait.setSuffix(" s")
        self.wait.setFixedWidth(90)
        wait.addWidget(self.wait)
        wait.addWidget(_row_button("Add pause", "Wait this long before the next step",
                                   lambda: self._append(wait_step(self.wait.value()))))
        wait.addStretch(1)
        bl.addLayout(wait)
        lay.addWidget(box)

        self.bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.bb.button(QDialogButtonBox.Save).setObjectName("Primary")
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        lay.addWidget(self.bb)
        for s in self.action.get("steps") or []:
            self._list_step(s)
        self._spec_hooked = False
        if did is not None:
            i = self.device.findData(str(did))
            if i >= 0:
                self.device.setCurrentIndex(i)
        self._fill_commands()
        self._update()

    # -- device commands ---------------------------------------------------------------------------
    def _fill_commands(self, *_):
        did = self.device.currentData()
        self.command.clear()
        if did is None:
            return
        st = self.main.hub.state(did)
        if st.summary is None and st.spec_state in ("none", "failed", "loading"):
            self.command.addItem("Loading what this device can do…", None)
            self.main.hub.ensure_summary(did)
            if not self._spec_hooked:
                self._spec_hooked = True
                self.main.hub.spec_ready.connect(self._spec_ready)
            self._command_changed()
            return
        commands = device_commands(st.summary)
        if not commands:
            self.command.addItem("Nothing this device can do in an action", None)
        for c in commands:
            self.command.addItem(c["label"], c)
        self._command_changed()

    def _spec_ready(self, did):
        if did == self.device.currentData():
            self._fill_commands()

    def _command_changed(self, *_):
        c = self.command.currentData()
        needs = bool(c and c.get("needs_value"))
        self.value.setVisible(needs)
        if needs:
            lo, hi = c["range"][0], c["range"][1]
            self.value.setRange(int(lo), int(hi))
            self.value.setSuffix(f" {c['unit']}" if c.get("unit") else "")
        self.add_btn.setEnabled(c is not None)

    # -- steps ------------------------------------------------------------------------------------
    def _list_step(self, step):
        it = QListWidgetItem(step.get("label") or step.get("kind"))
        it.setData(Qt.UserRole, step)
        self.steps.addItem(it)

    def _append(self, step):
        self._list_step(step)
        self.steps.setCurrentRow(self.steps.count() - 1)
        self._update()

    def _add(self):
        c = self.command.currentData()
        did = self.device.currentData()
        if c is None or did is None:
            return
        info = self.main.cfg.device(did) or {}
        self._append(step_from(did, info.get("name") or did, c, self.value.value() if c.get("needs_value") else None))

    def _remove(self):
        r = self.steps.currentRow()
        if r >= 0:
            self.steps.takeItem(r)
        self._update()

    def _move(self, delta):
        r = self.steps.currentRow()
        if r < 0 or not 0 <= r + delta < self.steps.count():
            return
        it = self.steps.takeItem(r)
        self.steps.insertItem(r + delta, it)
        self.steps.setCurrentRow(r + delta)

    def _update(self, *_):
        ok = bool(self.name.text().strip()) and any(
            self.steps.item(i).data(Qt.UserRole).get("kind") != "wait" for i in range(self.steps.count()))
        self.bb.button(QDialogButtonBox.Save).setEnabled(ok)

    def result_action(self) -> dict:
        a = self.action
        a["name"] = self.name.text().strip()
        a["steps"] = [self.steps.item(i).data(Qt.UserRole) for i in range(self.steps.count())]
        return a

    def done(self, r):
        if self._spec_hooked:
            try:
                self.main.hub.spec_ready.disconnect(self._spec_ready)
            except (RuntimeError, TypeError):
                pass
            self._spec_hooked = False
        super().done(r)


class ActionsDialog(QDialog):
    """XiControl's own actions and the Xiaomi Home app's scenes, each with a Run button."""

    def __init__(self, main, parent=None, device_filter=None):
        super().__init__(parent)
        self.main = main
        self.device_filter = str(device_filter) if device_filter else None
        self.setWindowTitle("Actions")
        self.resize(760, 700)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        t = QLabel("Actions")
        t.setObjectName("H2")
        lay.addWidget(t)
        lay.addWidget(muted("Several commands with one click - e.g. all lights off and the purifier on. Pin an "
                            "action (☆) to get its button on the home screen. Below them: the manual scenes and "
                            "the automations of the Xiaomi Home app."))
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Show"))
        self.filter = QComboBox()
        self.filter.setMinimumWidth(260)
        self.filter.addItem("Everything", None)
        self.filter.currentIndexChanged.connect(self._filter_changed)
        frow.addWidget(self.filter)
        frow.addStretch(1)
        lay.addLayout(frow)
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("Scroll")
        self.list = QVBoxLayout(host)
        self.list.setContentsMargins(0, 0, 6, 0)
        self.list.setSpacing(6)
        area.setWidget(host)
        lay.addWidget(area, 1)
        row = QHBoxLayout()
        row.addWidget(_row_button("New action…", "Make a new action", self.new, "Primary"))
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.scenes: list[dict] | None = None
        self.scene_error = ""
        self.auto_title, self.auto_shown = None, []
        self.rebuild()
        if main.signed_in:
            run_task(main.cloud.scenes, on_done=self._scenes, on_error=self._scenes_failed, owner=self)

    @property
    def actions(self) -> list:
        return self.main.cfg.data.setdefault("actions", [])

    def _clear(self):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().hide()   # gone at once; the object itself is freed a moment later
                it.widget().deleteLater()

    def _section(self, text):
        lab = QLabel(text)
        lab.setObjectName("SectionTitle")
        self.list.addWidget(lab)
        return lab

    # -- one device or everything -----------------------------------------------------------------------
    def _scene_devices(self, s) -> dict:
        """{did: name} of the devices a scene is started by or acts on."""
        out = {}
        for part in list(s.get("triggers") or []) + list(s.get("actions") or []):
            if part.get("did"):
                info = self.main.cfg.device(part["did"])
                out[part["did"]] = (info or {}).get("name") or part.get("device") or part["did"]
        return out

    def _involves(self, s) -> bool:
        return self.device_filter is None or self.device_filter in self._scene_devices(s)

    def _fill_filter(self):
        names = {}
        for a in self.actions:
            for st in a.get("steps") or []:
                if st.get("did"):
                    info = self.main.cfg.device(st["did"])
                    names[str(st["did"])] = (info or {}).get("name") or str(st["did"])
        for s in self.scenes or []:
            names.update(self._scene_devices(s))
        if self.device_filter and self.device_filter not in names:
            info = self.main.cfg.device(self.device_filter)
            names[self.device_filter] = (info or {}).get("name") or self.device_filter
        self.filter.blockSignals(True)
        self.filter.clear()
        self.filter.addItem("Everything", None)
        for did, name in sorted(names.items(), key=lambda kv: kv[1].lower()):
            self.filter.addItem(f"Only: {name}", did)
        i = self.filter.findData(self.device_filter)
        self.filter.setCurrentIndex(max(0, i))
        self.filter.blockSignals(False)

    def _filter_changed(self, *_):
        self.device_filter = self.filter.currentData()
        self.rebuild()

    @staticmethod
    def _flow_text(s) -> str:
        """'Door sensor: Timeout, not closed  ▸  Hub: Play ringtone' - what starts it and what it does."""
        def side(parts):
            texts = [f"{p['device']}: {p['text']}" if p.get("device") else p["text"] for p in parts]
            return " + ".join(texts[:3]) + (f" + {len(texts) - 3} more" if len(texts) > 3 else "")
        return f"{side(s.get('triggers') or []) or '?'}  ▸  {side(s.get('actions') or []) or '?'}"

    def _automation_row(self, s):
        card, cl = self._card(s["name"], self._flow_text(s))
        sw = ToggleSwitch()
        sw.setCheckedSilently(s["enabled"])
        sw.setToolTip("Switch this automation on or off (as its switch in the Xiaomi Home app)")
        sw.toggled.connect(lambda on, x=s, w=sw: self._set_enabled(x, bool(on), w))
        cl.addWidget(sw)
        return card

    @staticmethod
    def _automation_title(auto) -> str:
        on = sum(1 for s in auto if s["enabled"])
        return f"Automation (Xiaomi Home)  ·  {on} on, {len(auto) - on} off" if auto else "Automation (Xiaomi Home)"

    def _set_enabled(self, s, on, sw):
        """Sends the switch to the Mi account; the row keeps its place, only the counts in the title change."""
        sw.setEnabled(False)

        def done(_r):
            s["enabled"] = on
            if isValid(sw):
                sw.setEnabled(True)
            if self.auto_title is not None and isValid(self.auto_title):
                self.auto_title.setText(self._automation_title(self.auto_shown))
            self.main.toast(f"“{s['name']}” switched {'on' if on else 'off'} ✓")

        def failed(exc):
            if isValid(sw):
                sw.setCheckedSilently(not on)
                sw.setEnabled(True)
            QMessageBox.warning(self, "Automation", f"“{s['name']}” wasn't switched {'on' if on else 'off'}:\n"
                                                    f"{friendly(exc)}")

        run_task(self.main.cloud.set_scene_enabled, s, on, on_done=done, on_error=failed, owner=self)

    def rebuild(self):
        self._clear()
        self.auto_title, self.auto_shown = None, []
        self._fill_filter()
        f = self.device_filter
        self._section("Made in XiControl")
        mine = [a for a in self.actions if f is None or any(str(st.get("did")) == f for st in a.get("steps") or [])]
        if not mine:
            self.list.addWidget(muted("No actions with this device." if f else "No actions yet - press “New action…”."))
        for a in mine:
            self.list.addWidget(self._action_row(a))
        self._section("Manual controls (Xiaomi Home)")
        if not self.main.signed_in:
            self.list.addWidget(muted("Sign in to your Mi account to see them."))
        elif self.scenes is None:
            self.list.addWidget(muted(self.scene_error or "Loading…"))
        else:
            shown = [s for s in self.scenes if self._involves(s)]
            manual = [s for s in shown if s["manual"]]
            if not manual:
                self.list.addWidget(muted("No tap-to-run scenes" + (" with this device." if f else " on your Mi account.")))
            for s in manual:
                self.list.addWidget(self._scene_row(s))
            auto = [s for s in shown if not s["manual"]]
            self.auto_shown = auto
            self.auto_title = self._section(self._automation_title(auto))
            if not auto:
                self.list.addWidget(muted("No automations" + (" with this device." if f else " on your Mi account.")))
            for s in sorted(auto, key=lambda s: (not s["enabled"], s["name"].lower())):
                self.list.addWidget(self._automation_row(s))
            if auto:
                self.list.addWidget(muted("Automations run in the Mi cloud by themselves. The switch turns one on "
                                          "or off, as in the Xiaomi Home app; making and editing them is done in "
                                          "the app."))
        self.list.addStretch(1)

    def _card(self, title, sub):
        card = QFrame()
        card.setObjectName("Card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 8, 10, 8)
        col = QVBoxLayout()
        col.setSpacing(0)
        n = QLabel(title)
        n.setObjectName("RowLabel")
        n.setWordWrap(True)
        col.addWidget(n)
        if sub:
            s = QLabel(sub)
            s.setObjectName("Small")
            s.setWordWrap(True)
            col.addWidget(s)
        cl.addLayout(col, 1)
        return card, cl

    def _action_row(self, a):
        steps = [s.get("label", "") for s in a.get("steps") or []]
        sub = " · ".join(steps[:4]) + (f" · +{len(steps) - 4} more" if len(steps) > 4 else "")
        card, cl = self._card(a.get("name") or "Action", sub)
        cl.addWidget(_row_button("▶ Run", "Run it now", lambda _=False, x=a: self.main.run_user_action(x), "Primary"))
        pin = _row_button("★" if a.get("pinned") else "☆", "Show on the home screen",
                          lambda _=False, x=a: self._pin(x), "IconBtn")
        cl.addWidget(pin)
        cl.addWidget(_row_button("Edit", "Change the name or the steps", lambda _=False, x=a: self.edit(x)))
        cl.addWidget(_row_button("✕", "Delete this action", lambda _=False, x=a: self._delete(x), "IconBtn"))
        return card

    def _scene_row(self, s):
        does = " + ".join(f"{p['device']}: {p['text']}" if p.get("device") else p["text"]
                          for p in (s.get("actions") or [])[:3])
        card, cl = self._card(s["name"], does or s["home"])
        cl.addWidget(_row_button("▶ Run", "Run this scene (as tapping it in the Xiaomi Home app)",
                                 lambda _=False, x=s: self.main.run_scene(x), "Primary"))
        return card

    def _scenes(self, scenes):
        self.scenes = scenes
        self.rebuild()

    def _scenes_failed(self, exc):
        self.scene_error = f"⚠ Couldn't load the scenes: {friendly(exc)}"
        self.rebuild()

    # -- changing the list ------------------------------------------------------------------------------
    def _save(self):
        self.main.cfg.save()
        self.main.actions_changed()
        self.rebuild()

    def new(self, did=None):
        dlg = ActionEditor(self.main, None, did, self)
        if run_dialog(dlg) == QDialog.Accepted:
            self.actions.append(dlg.result_action())
            self._save()

    def edit(self, a):
        dlg = ActionEditor(self.main, a, None, self)
        if run_dialog(dlg) == QDialog.Accepted:
            idx = next((i for i, x in enumerate(self.actions) if x.get("id") == a.get("id")), None)
            if idx is not None:
                self.actions[idx] = dlg.result_action()
                self._save()

    def _pin(self, a):
        a["pinned"] = not a.get("pinned")
        self._save()

    def _delete(self, a):
        if QMessageBox.question(self, "Delete action", f"Delete the action “{a.get('name')}”?") != QMessageBox.Yes:
            return
        self.actions[:] = [x for x in self.actions if x.get("id") != a.get("id")]
        self._save()
