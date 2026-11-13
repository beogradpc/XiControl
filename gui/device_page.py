"""Device page: a hero with a big power button, then modes, main sliders, actions and readings.
Everything else is folded under "More settings"; raw commands and details sit in the ⋯ menu."""
from __future__ import annotations

import re
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QButtonGroup, QColorDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget)

from xc.camera import (fetch_cloud_stream, is_battery_camera, list_events, live_options, supports_go2rtc,
                       wake)
from xc.catalog import category, fill
from xc.cloud import CloudAuthError, friendly
from xc.device import DeviceOffline, NeedsSignIn
from xc.summary import LegacySummary, Summary
from xc.energy import energy_source
from xc.vacuum_map import supports_map

from .controls import ask_action_inputs, muted, run_dialog
from .hub import OFFLINE_TEXT
from .settings_panel import SettingsPanel
from .theme import PowerButton, repolish
from .workers import run_task

MODE_ICONS = [
    (("sleep", "night", "moon"), "🌙"), (("auto", "smart", "intelligent"), "✨"), (("favorite", "favourite"), "⭐"),
    (("custom", "manual", "diy"), "✋"), (("silent", "quiet", "low", "mute", "gentle", "eco"), "🍃"),
    (("turbo", "strong", "max", "high", "boost", "powerful"), "⚡"), (("standard", "medium", "normal",
     "balanced", "middle"), "◐"), (("day", "sun"), "☀"), (("reading", "read"), "📖"), (("tv", "movie", "cinema"),
     "🎬"), (("warm",), "🔥"), (("cool", "cold", "defrost", "frozen"), "❄"), (("dry",), "💨"),
    (("fan", "wind"), "🌀"), (("mop", "water", "wet"), "💧"), (("sweep", "vacuum", "clean"), "🧹"),
    (("fry", "cook", "bake", "roast", "grill"), "🍳"), (("chicken",), "🍗"), (("fish", "shrimp"), "🐟"),
    (("fries", "potato", "chips"), "🍟"), (("steak", "meat", "beef"), "🥩"), (("cake", "dessert"), "🧁"),
    (("vegetable",), "🥦"), (("off", "close", "none"), "○"),
]
ACTION_ICONS = [  # checked in order: "start charge" must hit "charge" before "start"
    (("charge", "home", "dock", "back"), "🏠"), (("find", "locate", "identify"), "🔔"),
    (("pause",), "❚❚"), (("stop", "cancel", "end"), "■"), (("start", "play", "resume"), "▶"),
    (("toggle",), "⇄"), (("next",), "⏭"), (("previous", "prev"), "⏮"), (("spot",), "◎"), (("reset",), "↺"),
]


def pick_icon(text: str, table, default="●") -> str:
    t = (text or "").lower()
    m = re.fullmatch(r"(?:level|gear|speed|fan)?\s*(\d{1,2})", t)
    if table is MODE_ICONS and m:
        return m.group(1)
    for words, icon in table:
        if any(w == t or w in t.split() or (len(w) > 3 and w in t) for w in words):
            return icon
    return default


def section(title: str) -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("Card")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(22, 18, 22, 20)
    lay.setSpacing(12)
    if title:
        t = QLabel(title)
        t.setObjectName("SectionTitle")
        lay.addWidget(t)
    return card, lay


class BigSlider(QWidget):
    """Label + value on top, a wide slider below. Sends the value when released."""
    changed = Signal(object)

    def __init__(self, label: str, lo, hi, step, fmt):
        super().__init__()
        self.fmt = fmt
        self.scale = 1 if float(step).is_integer() else int(round(1 / step)) or 1
        self.step = step
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        top = QHBoxLayout()
        name = QLabel(label)
        name.setStyleSheet("font-weight:600;")
        top.addWidget(name)
        top.addStretch(1)
        self.val = QLabel("—")
        self.val.setObjectName("Muted")
        top.addWidget(self.val)
        lay.addLayout(top)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * self.scale), int(hi * self.scale))
        self.slider.setSingleStep(max(1, int(step * self.scale)))
        self.slider.setPageStep(max(1, int((hi - lo) * self.scale / 10)))
        self.slider.setMinimumHeight(26)
        self.slider.valueChanged.connect(self._moved)
        self.slider.sliderReleased.connect(self._send)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self._send)
        lay.addWidget(self.slider)

    def _value(self):
        v = self.slider.value() / self.scale
        return int(round(v)) if self.scale == 1 else round(v, 3)

    def _moved(self, _raw):
        self.val.setText(self.fmt(self._value()))
        if not self.slider.isSliderDown():
            self._debounce.start()

    def _send(self):
        self._debounce.stop()
        self.changed.emit(self._value())

    def set_value(self, v):
        if not isinstance(v, (int, float)) or self.slider.isSliderDown() or self._debounce.isActive():
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(v * self.scale)))
        self.slider.blockSignals(False)
        self.val.setText(self.fmt(v))


class IconTile(QPushButton):
    """A big square-ish button: icon above, label below."""

    def __init__(self, icon: str, text: str, checkable=False, name="ModeTile"):
        super().__init__()
        self.setObjectName(name)
        self.setCheckable(checkable)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(text)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(2)
        self.icon = QLabel(icon)
        self.icon.setObjectName("TileIcon")
        self.text = QLabel(text)
        self.text.setObjectName("TileText")
        for w in (self.icon, self.text):
            w.setAlignment(Qt.AlignCenter)
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(w)
        self.toggled.connect(lambda on: repolish(self.text, "TileTextOn" if on else "TileText"))


class ModeGrid(QWidget):
    """Tiles for an enum (Mi Home style mode picker)."""
    chosen = Signal(object)

    def __init__(self, options: list[tuple[object, str]], columns=4):
        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        cols = min(columns, max(1, len(options)))
        for i, (value, text) in enumerate(options):
            b = IconTile(pick_icon(text, MODE_ICONS), text, checkable=True)
            b.clicked.connect(lambda _=False, v=value: self.chosen.emit(v))
            self.group.addButton(b)
            grid.addWidget(b, i // cols, i % cols)
            self.buttons[value] = b
        for c in range(cols):
            grid.setColumnStretch(c, 1)

    def set_value(self, value):
        b = self.buttons.get(value)
        if b is not None:
            b.setChecked(True)
        else:
            self.group.setExclusive(False)
            for x in self.buttons.values():
                x.setChecked(False)
            self.group.setExclusive(True)


class DevicePage(QWidget):
    back = Signal()
    edit_requested = Signal(str)
    remove_requested = Signal(str)
    cloud_expired = Signal()

    def __init__(self, info: dict, controller, hub, specs, main):
        super().__init__()
        self.setObjectName("Page")
        self.info, self.ctl, self.hub, self.specs, self.main = info, controller, hub, specs, main
        self.did = str(info["did"])
        self.summary = None
        self.updaters = []          # callables(values) refreshing the featured widgets
        self.cards: list = []          # SettingsPanel(s) needing every value
        self._polling = False
        self._fails = 0
        self.cat, icon, _label = category(info.get("model", ""))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # top bar ---------------------------------------------------------------------
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(20, 16, 22, 8)
        back = QPushButton("←")
        back.setObjectName("IconBtn")
        back.setToolTip("Back to all devices")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self.back)
        bl.addWidget(back)
        self.title = QLabel(info.get("name") or info.get("model") or "Device")
        self.title.setObjectName("PageTitle")
        bl.addWidget(self.title, 1)
        more = QPushButton("⋯")
        more.setObjectName("IconBtn")
        more.setToolTip("Device options")
        more.setCursor(Qt.PointingHandCursor)
        menu = QMenu(self)
        menu.addAction("Rename and connection…", lambda: self.edit_requested.emit(self.did))
        menu.addAction("Move to room…", lambda: main.move_device(self.did))
        menu.addAction("Add to an action…", lambda: main.add_to_action(self.did))
        menu.addAction("Firmware…", lambda: main.show_firmware(self.did))
        menu.addAction("Automations with this device…", lambda: main.open_actions(device_filter=self.did))
        menu.addAction("Sharing…", lambda: main.show_sharing(self.did))
        menu.addAction("Device info", self.show_info)
        menu.addAction("Send commands (advanced)…", self.show_console)
        menu.addAction("Re-download device description", self.reload_spec)
        fav = menu.addAction("Show in tray menu")
        fav.setCheckable(True)
        fav.setChecked(bool(info.get("tray")))
        fav.setToolTip("A quick on/off switch for this device in the tray icon's menu")
        fav.toggled.connect(self._toggle_tray)
        menu.addSeparator()
        menu.addAction("Remove from XiControl…", lambda: self.remove_requested.emit(self.did))
        delete = menu.addAction("Delete from Mi account…", lambda: main.remove_device(self.did, account=True))
        delete.setEnabled(main.on_account(info) and not info.get("shared"))
        more.setMenu(menu)
        bl.addWidget(more)
        root.addWidget(bar)

        # scrolling body, centred column ---------------------------------------------------
        area = QScrollArea()
        area.setWidgetResizable(True)
        host = QWidget()
        host.setObjectName("Scroll")
        hl = QHBoxLayout(host)
        hl.setContentsMargins(24, 6, 24, 30)
        hl.addStretch(1)
        self.col = QWidget()
        self.col.setMaximumWidth(780)
        self.col.setMinimumWidth(420)
        self.body = QVBoxLayout(self.col)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)
        hl.addWidget(self.col, 6)
        hl.addStretch(1)
        area.setWidget(host)
        root.addWidget(area, 1)

        # hero
        hero, hlay = section("")
        hrow = QHBoxLayout()
        hrow.setSpacing(18)
        self.hero_icon = QLabel(icon)
        self.hero_icon.setObjectName("HeroIcon")
        self.hero_icon.setFixedSize(80, 80)
        self.hero_icon.setAlignment(Qt.AlignCenter)
        hrow.addWidget(self.hero_icon)
        tcol = QVBoxLayout()
        tcol.setSpacing(4)
        tcol.addStretch(1)
        self.hero_status = QLabel("Connecting…")
        self.hero_status.setObjectName("HeroStatus")
        self.hero_status.setWordWrap(True)
        tcol.addWidget(self.hero_status)
        self.hero_sub = QLabel(self._sub_text())
        self.hero_sub.setObjectName("Muted")
        tcol.addWidget(self.hero_sub)
        tcol.addStretch(1)
        hrow.addLayout(tcol, 1)
        self.power = PowerButton(72)
        self.power.setToolTip("Power")
        self.power.hide()
        self.power.toggled_by_user.connect(self._power_clicked)
        hrow.addWidget(self.power, 0, Qt.AlignVCenter)
        hlay.addLayout(hrow)
        self.body.addWidget(hero)

        self.banner = QFrame()
        self.banner.setObjectName("BannerWarn")
        bl2 = QHBoxLayout(self.banner)
        bl2.setContentsMargins(16, 12, 12, 12)
        bl2.setSpacing(12)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        bl2.addWidget(self.banner_text, 1)
        self.banner_btn = QPushButton()
        self.banner_btn.setObjectName("Primary")
        self.banner_btn.hide()
        self._banner_cb = None
        self.banner_btn.clicked.connect(lambda: self._banner_cb and self._banner_cb())
        bl2.addWidget(self.banner_btn, 0, Qt.AlignVCenter)
        self.banner.hide()
        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(self.banner.hide)
        self.body.addWidget(self.banner)

        self.dynamic = QVBoxLayout()
        self.dynamic.setSpacing(14)
        self.body.addLayout(self.dynamic)
        self.dynamic.addWidget(muted("Loading controls…"))
        self.body.addStretch(1)

        self.timer = QTimer(self)
        self.timer.setInterval(max(3, int(main.cfg.data.get("refresh_seconds", 10))) * 1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

        hub.changed.connect(self._hub_changed)
        hub.spec_ready.connect(self._spec_ready)
        hub.active_did = self.did  # this page polls the device; the background poll skips it
        st = hub.state(self.did)
        if st.spec_state in ("ready", "missing", "failed") and (st.summary or st.spec_state != "failed"):
            QTimer.singleShot(0, lambda: self._spec_ready(self.did))
        else:
            if st.spec_state == "failed":
                st.spec_state = "none"
            hub.ensure_summary(self.did)

    # -- helpers -------------------------------------------------------------------------------
    def _sub_text(self, transport: str = "") -> str:
        parts = [self.info.get("room") or ""]
        if transport == "local":
            hub = self.ctl.hub()
            parts.append("Connected on your network" + (f" through “{hub.info.get('name') or 'its hub'}”"
                                                        if hub is not None else ""))
        elif transport == "cloud":
            parts.append("Connected via Mi cloud")
        return "  ·  ".join(p for p in parts if p)

    def show_banner(self, text, kind="error", ms=7000, button=None):
        """button: optional (label, callback) shown on the right of the message."""
        repolish(self.banner, {"error": "BannerError", "warn": "BannerWarn", "ok": "BannerOk"}[kind])
        for w in self.banner.findChildren(QLabel):
            repolish(w)
        self.banner_text.setText(text)
        self._banner_cb = button[1] if button else None
        if button:
            self.banner_btn.setText(button[0])
        self.banner_btn.setVisible(bool(button))
        self.banner.show()
        if ms:
            self._banner_timer.start(ms)
        else:
            self._banner_timer.stop()

    def _error(self, exc, prefix=""):
        if isinstance(exc, CloudAuthError):
            self.cloud_expired.emit()
        self.show_banner(prefix + friendly(exc))

    def firmware_changed(self):
        """Mentions a waiting firmware update once, with a button to its details."""
        text = self.main.update_text(self.did)
        if text and not getattr(self, "_fw_told", False) and not self.banner.isVisible():
            self._fw_told = True
            self.show_banner(f"⬆ {text}", "ok", 0, ("Details…", lambda: self.main.show_firmware(self.did)))

    def stop(self):
        if getattr(self, "_stopped", False):  # leaving the page and quitting the app both end up here
            return
        self._stopped = True
        self.timer.stop()
        if getattr(self, "map_card", None) is not None:
            self.map_card.stop()
        if self.hub.active_did == self.did:
            self.hub.active_did = None
        try:
            self.hub.changed.disconnect(self._hub_changed)
            self.hub.spec_ready.disconnect(self._spec_ready)
        except (RuntimeError, TypeError):
            pass

    # -- building the controls --------------------------------------------------------------
    def _clear_dynamic(self):
        self.energy_card = None
        if getattr(self, "map_card", None) is not None:
            self.map_card.stop()
            self.map_card = None
        while self.dynamic.count():
            it = self.dynamic.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.updaters.clear()
        self.cards.clear()

    def _spec_ready(self, did):
        if did != self.did:
            return
        st = self.hub.state(did)
        self.summary = st.summary
        self._clear_dynamic()
        if self.cat == "camera":
            self.dynamic.addWidget(self._camera_card(st.spec))
        if supports_map(st.spec):
            from .vacuum_map import MapCard
            self.map_card = MapCard(self, st.spec)
            self.dynamic.addWidget(self.map_card)
        source = energy_source(self.info.get("model", ""), st.spec)
        if source and self.main.on_account(self.info):
            from .energy import EnergyCard
            self.energy_card = EnergyCard(self, source)
        if isinstance(self.summary, Summary):
            self._build_miot(self.summary, st.spec)
        elif isinstance(self.summary, LegacySummary):
            self._build_legacy(self.summary)
        else:
            if st.spec_state == "failed":
                msg = f"Couldn't load this device's controls: {st.error}\nCheck the internet connection, then use ⋯ → Re-download."
            elif not self.info.get("model"):
                msg = "The model of this device is unknown. Use ⋯ → Rename and connection → Detect."
            else:
                msg = (f"Xiaomi hasn't published a control description for “{self.info.get('model')}”. "
                       "You can still send commands with ⋯ → Send commands.")
            card, lay = section("")
            lay.addWidget(muted(msg))
            self.dynamic.addWidget(card)
        if getattr(self, "energy_card", None) is not None:
            self.dynamic.insertWidget(self._energy_position(), self.energy_card)
        self._apply(st.values)
        self.refresh()

    def _energy_position(self) -> int:
        """Right above the settings groups (the last widget), below the main controls."""
        n = self.dynamic.count()
        last = self.dynamic.itemAt(n - 1).widget() if n else None
        return n - 1 if isinstance(last, SettingsPanel) else n

    LIVE_TEXT = {
        "rtsp": "Your camera's own RTSP stream.",
        "webrtc": "Through the Mi cloud (WebRTC): the picture comes straight from the camera, with almost no "
                  "delay - also when the camera is at another location.",
        "p2p": "Directly from the camera on your network (go2rtc).",
        "hls": "Through the Mi cloud (HLS): works from anywhere, about 10-20 seconds behind.",
    }
    LIVE_LABEL = {"rtsp": "RTSP", "webrtc": "Mi cloud (WebRTC)", "p2p": "Local network", "hls": "Mi cloud (HLS)"}

    def _camera_card(self, spec):
        card, lay = section("Live video")
        kinds = live_options(self.info, spec, self.ctl.on_local_network())
        if not kinds:
            if supports_go2rtc(self.info.get("model")) and self.info.get("ip") and not self.ctl.on_local_network():
                text = ("This camera is on another network and doesn't offer a stream through the Mi cloud, so "
                        "it can only be watched from its own network.")
            else:
                text = ("XiControl can't play this camera's video. If it offers an RTSP stream (some models or "
                        "custom firmware), add the address under ⋯ → Rename and connection.")
            lay.addWidget(muted(text))
        else:
            row = QHBoxLayout()
            b = QPushButton("▶  Watch live video")
            b.setObjectName("Primary")
            b.setMinimumHeight(44)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(self.open_live)
            row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
            text = self.LIVE_TEXT[kinds[0]]
            if kinds[0] in ("webrtc", "p2p") and is_battery_camera(self.info.get("model"), spec):
                text += " This camera sleeps to save its battery - waking it takes a few seconds."
            if len(kinds) > 1:
                others = ", ".join(self.LIVE_LABEL[k] for k in kinds[1:])
                text += f" If that fails, XiControl tries: {others}."
            lay.addWidget(muted(text))
        if self.ctl.cloud_possible():
            sep = QFrame()
            sep.setObjectName("Sep")
            lay.addWidget(sep)
            head = QHBoxLayout()
            t = QLabel("Recordings")
            t.setObjectName("SectionTitle")
            head.addWidget(t)
            head.addStretch(1)
            self.events_btn = QPushButton("Show")
            self.events_btn.setObjectName("Link")
            self.events_btn.setCursor(Qt.PointingHandCursor)
            self.events_btn.setToolTip("Motion and doorbell clips this camera saved to the Mi cloud (last 3 days)")
            self.events_btn.clicked.connect(self.load_events)
            head.addWidget(self.events_btn)
            lay.addLayout(head)
            self.events_box = QVBoxLayout()
            self.events_box.setSpacing(2)
            lay.addLayout(self.events_box)
        return card

    # -- camera recordings ------------------------------------------------------------------------
    def load_events(self):
        self.events_btn.setEnabled(False)
        self.events_btn.setText("Loading…")
        cloud, did, model = self.main.cloud, self.did, self.info.get("model", "")
        run_task(list_events, cloud, did, model, on_done=self._show_events,
                 on_error=lambda e: (self._error(e, "Couldn't get the recordings: "),
                                     self.events_btn.setEnabled(True), self.events_btn.setText("Show")),
                 owner=self)

    def _show_events(self, events: list):
        self.events_btn.setEnabled(True)
        self.events_btn.setText("Refresh")
        while self.events_box.count():
            it = self.events_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not events:
            self.events_box.addWidget(muted("No recordings in the last 3 days. Recordings need motion detection "
                                            "(or the doorbell) and cloud storage to be on in the Xiaomi Home app."))
            return
        for ev in events[:15]:
            w = QWidget()
            row = QHBoxLayout(w)
            row.setContentsMargins(0, 3, 0, 3)
            when = time.strftime("%a %d.%m.  %H:%M:%S", time.localtime(ev["time"]))
            lab = QLabel(when)
            lab.setMinimumWidth(170)
            row.addWidget(lab)
            name = QLabel(ev["name"] + (f"  ·  {ev['duration']:.0f} s" if ev["duration"] else ""))
            name.setObjectName("Muted")
            row.addWidget(name, 1)
            b = QPushButton("▶ Play")
            b.setObjectName("Link")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, e=ev: self.play_event(e))
            row.addWidget(b)
            self.events_box.addWidget(w)
        if len(events) > 15:
            self.events_box.addWidget(muted(f"Showing the newest 15 of {len(events)}."))

    def play_event(self, ev: dict):
        from .camera_view import CameraWindow, Source
        cloud, did, model = self.main.cloud, self.did, self.info.get("model", "")
        when = time.strftime("%d.%m. %H:%M", time.localtime(ev["time"]))
        # The app fetches and decrypts the recording itself and plays it from its local endpoint - the
        # player can't use the Mi cloud's playlist directly (see xc.camera).
        bridge = self.main.bridge
        src = Source("Recording", lambda: bridge.recording_url(
            lambda: cloud.camera_event_url(did, model, ev["file_id"], ev["is_alarm"])))
        CameraWindow(f"{self.info.get('name', 'Camera')} · {ev['name']} {when}", [src], self.window(),
                     live=False).show()

    def _build_miot(self, s: Summary, spec):
        self.power.setVisible(s.power is not None)

        # modes
        if s.modes:
            card, lay = section("")
            for i, m in enumerate(s.modes):
                if i:
                    sep = QFrame()
                    sep.setObjectName("Sep")
                    lay.addWidget(sep)
                t = QLabel(m.label)
                t.setObjectName("SectionTitle")
                lay.addWidget(t)
                grid = ModeGrid([(v.get("value"), m.value_text(v.get("value"))) for v in m.values],
                                columns=4 if len(m.values) != 3 else 3)
                grid.chosen.connect(lambda v, p=m: self.set_prop(p, v))
                lay.addWidget(grid)
                self.updaters.append(lambda vals, p=m, g=grid: (p.siid, p.piid) in vals and g.set_value(vals[(p.siid, p.piid)]))
            self.dynamic.addWidget(card)

        # sliders + colour
        if s.sliders or s.color:
            card, lay = section("")
            lay.setSpacing(18)
            for p in s.sliders:
                lo, hi = p.range[0], p.range[1]
                step = (p.range[2] if len(p.range) > 2 else 1) or 1
                sl = BigSlider(p.label, lo, hi, step, p.value_text)
                sl.changed.connect(lambda v, prop=p: self.set_prop(prop, v))
                lay.addWidget(sl)
                self.updaters.append(lambda vals, prop=p, w=sl: (prop.siid, prop.piid) in vals and w.set_value(vals[(prop.siid, prop.piid)]))
            if s.color:
                lay.addWidget(self._color_row(s.color))
            self.dynamic.addWidget(card)

        # actions
        if s.actions:
            card, lay = section("")
            grid = QGridLayout()
            grid.setSpacing(10)
            cols = min(4, len(s.actions))
            for i, a in enumerate(s.actions):
                b = IconTile(pick_icon(a.name.replace('-', ' '), ACTION_ICONS), a.label, name="ActionTile")
                svc = next(x for x in s.services if x.siid == a.siid)
                b.clicked.connect(lambda _=False, act=a, sv=svc: self.run_action(act, sv))
                grid.addWidget(b, i // cols, i % cols)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            lay.addLayout(grid)
            self.dynamic.addWidget(card)

        # readings
        if s.readings:
            self.dynamic.addWidget(self._stats([(p.label, (lambda vals, prop=p: prop.value_text(
                vals.get((prop.siid, prop.piid))) if (prop.siid, prop.piid) in vals else "—")) for p in s.readings]))

        # everything else, in plain-language groups
        panel = SettingsPanel(s)
        if panel.rows or panel.life_rows or panel.layout().count():
            panel.prop_changed.connect(self.set_prop)
            panel.action_requested.connect(self.run_action)
            self.cards.append(panel)
            self.dynamic.addSpacing(6)
            self.dynamic.addWidget(panel)
        else:
            panel.deleteLater()

    def _build_legacy(self, s: LegacySummary):
        prof = s.profile
        self.power.setVisible(bool(prof.get("power")))
        for title, method, template, options in prof["choices"]:
            card, lay = section(title)
            grid = ModeGrid([(v, t) for v, t in options])
            grid.chosen.connect(lambda v, m=method, t=template, ti=title:
                                self.raw(m, fill(t, v), f"{ti} changed"))
            lay.addWidget(grid)
            self.dynamic.addWidget(card)
        if prof["sliders"]:
            card, lay = section("")
            for title, method, lo, hi, template, key in prof["sliders"]:
                unit = "%" if key == "bright" else ("K" if key == "ct" else "")
                sl = BigSlider(title, lo, hi, 1, lambda v, u=unit: f"{v} {u}".strip())
                sl.changed.connect(lambda v, m=method, t=template, ti=title: self.raw(m, fill(t, v), f"{ti} → {v}"))
                lay.addWidget(sl)
                self.updaters.append(lambda vals, k=key, w=sl: k in vals and w.set_value(_num(vals[k])))
            self.dynamic.addWidget(card)
        if prof["buttons"]:
            card, lay = section("")
            grid = QGridLayout()
            grid.setSpacing(10)
            cols = min(3, len(prof["buttons"]))
            for i, (text, method, params) in enumerate(prof["buttons"]):
                label = text.split("  ")[-1].strip()
                b = IconTile(pick_icon(label, ACTION_ICONS), label, name="ActionTile")
                b.clicked.connect(lambda _=False, m=method, p=params, t=label: self.raw(m, p, f"{t} ✓"))
                grid.addWidget(b, i // cols, i % cols)
            for c in range(cols):
                grid.setColumnStretch(c, 1)
            lay.addLayout(grid)
            self.dynamic.addWidget(card)
        stats = []
        for key, title, mapping, unit in prof["fields"][1:]:
            def fmt(vals, k=key, mp=mapping, u=unit):
                if k not in vals:
                    return "—"
                v = vals[k]
                if mp == "area" and isinstance(v, (int, float)):
                    return f"{v / 1_000_000:.1f} m²"
                if isinstance(mp, dict):
                    return str(mp.get(v, v))
                if u == "s" and isinstance(v, (int, float)):
                    return f"{int(v) // 60} min"
                return f"{v} {u}".strip()
            stats.append((title, fmt))
        if stats:
            self.dynamic.addWidget(self._stats(stats))

    def _stats(self, items):
        card, lay = section("")
        grid = QGridLayout()
        grid.setSpacing(10)
        cols = 3
        for i, (title, fmt) in enumerate(items):
            f = QFrame()
            f.setObjectName("Stat")
            fl = QVBoxLayout(f)
            fl.setContentsMargins(14, 12, 14, 12)
            fl.setSpacing(2)
            v = QLabel("—")
            v.setObjectName("StatValue")
            fl.addWidget(v)
            t = QLabel(title)
            t.setObjectName("Small")
            t.setWordWrap(True)
            fl.addWidget(t)
            grid.addWidget(f, i // cols, i % cols)
            self.updaters.append(lambda vals, w=v, fn=fmt: w.setText(fn(vals)))
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        lay.addLayout(grid)
        return card

    def _color_row(self, prop):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        t = QLabel(prop.label)
        t.setStyleSheet("font-weight:600;")
        lay.addWidget(t)
        row = QHBoxLayout()
        row.setSpacing(10)
        for hexcol in ("#FFFFFF", "#FFB561", "#FF5A4E", "#FF4FA3", "#9B6BFF", "#4C8DFF", "#2CC7B4", "#7ED957"):
            b = QPushButton()
            b.setObjectName("Swatch")
            b.setStyleSheet(f"background:{hexcol};")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(hexcol)
            v = int(hexcol[1:], 16)
            b.clicked.connect(lambda _=False, val=v: self.set_prop(prop, val))
            row.addWidget(b)
        custom = QPushButton("Custom…")
        custom.setObjectName("Link")
        custom.clicked.connect(lambda: self._pick_color(prop))
        row.addWidget(custom)
        row.addStretch(1)
        self.current_swatch = QLabel()
        self.current_swatch.setFixedSize(30, 30)
        row.addWidget(QLabel("Now:"))
        row.addWidget(self.current_swatch)
        lay.addLayout(row)

        def upd(vals):
            v = vals.get((prop.siid, prop.piid))
            if isinstance(v, int):
                self.current_swatch.setStyleSheet(f"background:#{v & 0xFFFFFF:06X}; border-radius:15px;")
        self.updaters.append(upd)
        return w

    def _pick_color(self, prop):
        cur = self.hub.state(self.did).values.get((prop.siid, prop.piid)) or 0xFFFFFF
        col = QColorDialog.getColor(QColor(f"#{cur & 0xFFFFFF:06X}"), self, "Choose color")
        if col.isValid():
            self.set_prop(prop, max(1, (col.red() << 16) | (col.green() << 8) | col.blue()))

    # -- state -------------------------------------------------------------------------------------
    def _apply(self, values: dict):
        st = self.hub.state(self.did)
        s = self.summary
        on = s.is_on(values) if s else None
        if st.online is False:
            text = "Offline"
        elif s is not None and values:
            text = s.status_text(values) or ("On" if on else "Online")
        elif st.online:
            text = "Online"
        elif st.online is None and st.error:
            text = "Sign in needed"
        else:
            text = "Connecting…"
        self.hero_status.setText(text)
        self.hero_sub.setText(self._sub_text(st.transport) if st.online else
                              (st.error if st.error else self._sub_text()))
        self.power.set_state(on if st.online else None)
        self.power.setEnabled(st.online is not False)
        repolish(self.hero_icon, "HeroIconOn" if (on or (on is None and st.online)) else "HeroIcon")
        for fn in self.updaters:
            fn(values)
        for c in self.cards:
            c.update_values(values)

    def _hub_changed(self, did):
        if did == self.did:
            self._apply(self.hub.state(did).values)

    def refresh(self, full=False):
        if self._polling:
            return
        st = self.hub.state(self.did)
        s = self.summary
        reason = self.ctl.unreachable_reason()
        if reason:
            if self.ctl.needs_sign_in():
                self.show_banner("This device isn't reachable on your network directly (it's Bluetooth/Zigbee, set "
                                 "to “Mi cloud only”, or its address and key are unknown). Sign in to your Mi "
                                 "account to control it.", "warn", 0, ("Sign in", self.main.sign_in))
            else:
                self.show_banner(reason, "warn", 0, ("Fix…", lambda: self.edit_requested.emit(self.did)))
            self.hub.poll(self.did, force=True)
            return
        if isinstance(s, Summary):
            if full or self.cards:
                pairs = s.page_pairs()
            else:
                pairs = sorted(set(s.tile_pairs()) | s.featured_props)
                pairs = [x for x in pairs if s.spec.prop(*x) and s.spec.prop(*x).readable]
            fn, args, conv = self.ctl.get_props, (pairs,), None
        elif isinstance(s, LegacySummary):
            fn, args, conv = self.ctl.raw, (s.profile["status"]["method"], s.profile["status"]["params"]), s.parse
        else:
            self.hub.poll(self.did, force=True)
            return
        self._polling = True

        def done(res):
            self._polling = False
            self._fails = 0
            values = conv(res) if conv else res
            # the Mi cloud also answers for a device that is switched off - with the values it remembers
            if self.ctl.last_transport == "cloud" and self.hub.listed_offline(self.did):
                self.hub.push(self.did, values, False, "cloud")
                st.error = OFFLINE_TEXT
                self.show_banner("The Mi cloud reports this device as offline. The values shown are the last "
                                 "ones it sent.", "warn", 0)
                self.hub.changed.emit(self.did)
                return
            if self.banner.objectName() != "BannerOk" and self.banner.isVisible() and not self._banner_timer.isActive():
                self.banner.hide()
            self.hub.push(self.did, values, True, self.ctl.last_transport)

        def failed(exc):
            self._polling = False
            self._fails += 1
            st.online = None if isinstance(exc, NeedsSignIn) else False
            st.error = friendly(exc)
            if isinstance(exc, CloudAuthError):
                self.cloud_expired.emit()
            if self._fails == 1 or self._fails % 6 == 0:
                if isinstance(exc, (DeviceOffline, NeedsSignIn)):
                    self.show_banner(friendly(exc), "error", 0)
                else:
                    self.show_banner("The device isn't responding. It may be switched off at the wall or "
                                     f"disconnected from Wi-Fi.\n{friendly(exc)}", "error", 0)
            self.hub.changed.emit(self.did)

        run_task(fn, *args, on_done=done, on_error=failed, owner=self)

    # -- commands ------------------------------------------------------------------------------------
    def _power_clicked(self, on):
        self.hub.set_power(self.did, on, error=lambda e: self._error(e, "Couldn't switch the device: "))

    def set_prop(self, prop, value):
        st = self.hub.state(self.did)
        st.values[(prop.siid, prop.piid)] = value  # optimistic
        self._apply(st.values)
        run_task(self.ctl.set_prop, prop.siid, prop.piid, value,
                 on_done=lambda _r: self._changed_ok(),
                 on_error=lambda e: (self._error(e, f"Couldn't change {prop.label}: "), self.refresh()),
                 owner=self)

    def run_action(self, action, service):
        ins = ask_action_inputs(self, action, service) if action.ins else []
        if ins is None:
            return
        run_task(self.ctl.action, action.siid, action.aiid, ins,
                 on_done=lambda _r: (self.main.toast(f"{action.label} ✓"), self._changed_ok()),
                 on_error=lambda e: self._error(e, f"{action.label} failed: "), owner=self)

    def raw(self, method, params, done_text=None):
        run_task(self.ctl.raw, method, params,
                 on_done=lambda _r: (self.main.toast(done_text or "Done"), self._changed_ok()),
                 on_error=lambda e: self._error(e, "Command failed: "), owner=self)

    def _changed_ok(self):
        QTimer.singleShot(700, self.refresh)

    # -- menu actions -----------------------------------------------------------------------------
    def show_info(self):
        from .advanced import InfoDialog
        run_dialog(InfoDialog(self.info, self.ctl, self.hub.state(self.did), self))

    def show_console(self):
        from .advanced import ConsoleDialog
        run_dialog(ConsoleDialog(self.ctl, self))

    def _toggle_tray(self, on):
        if on:
            self.info["tray"] = True
        else:
            self.info.pop("tray", None)
        self.main.cfg.save()
        self.main.toast("Added to the tray menu" if on else "Removed from the tray menu")

    def reload_spec(self):
        self._clear_dynamic()
        self.dynamic.addWidget(muted("Loading controls…"))
        self.hub.reload_spec(self.did)

    def open_live(self, _checked=False, allow_go2rtc=True):
        """Opens the live view with every way this camera offers, best first."""
        from .camera_view import CameraWindow
        main, did = self.main, self.did
        spec = self.hub.state(did).spec
        kinds = live_options(self.info, spec, self.ctl.on_local_network())
        via_go2rtc = [k for k in kinds if k in ("webrtc", "p2p")]
        if via_go2rtc and not allow_go2rtc:
            kinds = [k for k in kinds if k not in via_go2rtc]
        elif via_go2rtc:
            others = [k for k in kinds if k not in via_go2rtc]
            if not main.signed_in or not main.cloud.can_renew:
                if not others:
                    self.show_banner("Live video needs the Mi account sign-in (the camera's keys come from it). "
                                     + ("Sign in once more - older versions of XiControl didn't keep the key it "
                                        "needs." if main.signed_in else ""), "warn", 0, ("Sign in", main.sign_in))
                    return
                kinds = others
            elif not main.go2rtc.find():
                # go2rtc is the WebRTC / camera-protocol client; without it only the slower ways remain
                main.install_go2rtc(
                    then=lambda: main.page is not None and main.page.did == did and main.page.open_live(),
                    declined=(lambda: main.page is not None and main.page.did == did
                              and main.page.open_live(allow_go2rtc=False)) if others else None)
                return
        if not kinds:
            return
        sources = [self._live_source(k, spec) for k in kinds]
        CameraWindow(self.info.get("name", "Camera"), sources, self.window()).show()

    def _live_source(self, kind: str, spec):
        from .camera_view import Source
        main, did, ctl = self.main, self.did, self.ctl
        model = self.info.get("model", "")
        label = self.LIVE_LABEL[kind]
        if kind == "rtsp":
            url = self.info["rtsp_url"]
            return Source(label, lambda: url)
        if kind == "hls":
            return Source(label, lambda: fetch_cloud_stream(ctl, spec), "10-20 s behind")
        cloud, cams = main.cloud, main.camera_list()
        if kind == "webrtc":
            return Source(label, lambda: main.go2rtc.stream_url(cloud, cams, did, "webrtc"), "direct from the camera",
                          detail=lambda: main.bridge.errors.get(did),
                          closed=lambda: run_task(main.bridge.close_session, did))

        def local():
            wake(cloud, did, model, spec)  # doorbells and battery cameras sleep
            return main.go2rtc.stream_url(cloud, cams, did, "p2p")
        return Source(label, local, "your network")


def _num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
