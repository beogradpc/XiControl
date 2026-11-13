"""Home screen: room filter chips, pinned actions and a grid of device tiles with a quick power button and a
right-click menu."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from xc.catalog import category

from .flow import FlowLayout
from .theme import LinkMark, PowerButton, repolish


TILE_SIZES = {"small": (224, 58), "medium": (232, 150), "large": (300, 214)}
TILE_SIZE_NAMES = {"small": "Small - the name only", "medium": "Medium - the main readings",
                   "large": "Large - all readings"}


class DeviceTile(QFrame):
    """One device on the home screen. Three sizes: small (icon and name), medium (status and the main
    readings in one line) and large (status and up to six readings with their names)."""
    clicked = Signal(str)
    power = Signal(str, bool)
    menu_requested = Signal(str, QPoint)   # did, global position

    def __init__(self, info: dict, size: str = "medium", parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.did = str(info["did"])
        self.size_name = size if size in TILE_SIZES else "medium"
        self.W, self.H = TILE_SIZES[self.size_name]
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        small, large = self.size_name == "small", self.size_name == "large"
        _k, icon, _l = category(info.get("model", ""))
        self.icon = QLabel(icon)
        self.icon.setObjectName("IconSmall" if small else "Icon")
        self.icon.setFixedSize(*((32, 32) if small else (44, 44)))
        self.icon.setAlignment(Qt.AlignCenter)
        self.badge = QLabel("⬆")
        self.badge.setObjectName("Badge")
        self.badge.hide()
        self.pwr = PowerButton(28 if small else 34)
        self.pwr.hide()
        self.pwr.toggled_by_user.connect(lambda on: self.power.emit(self.did, on))
        self.name = QLabel(info.get("name") or info.get("model") or self.did)
        self.name.setObjectName("TileName")
        self.link = LinkMark(14 if small else 15)   # local network or Mi cloud - in front of the name
        self.status = QLabel("…")
        self.status.setObjectName("TileStatus")
        self.metrics = QLabel("")            # medium: the main readings in one line
        self.metrics.setObjectName("TileMetrics")
        self.cells: list[tuple[QLabel, QLabel]] = []   # large: (value, name) per reading
        if small:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(12, 8, 10, 8)
            lay.setSpacing(10)
            lay.addWidget(self.icon)
            names = QHBoxLayout()
            names.setSpacing(5)
            names.addWidget(self.link)
            names.addWidget(self.name, 1)
            lay.addLayout(names, 1)
            lay.addWidget(self.badge)
            lay.addWidget(self.pwr)
            self.status.hide()
            self.metrics.hide()
            self._name_width = self.W - 22 - 32 - 10 - 6
        else:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(16, 14, 14, 12)
            lay.setSpacing(2)
            top = QHBoxLayout()
            top.addWidget(self.icon)
            top.addWidget(self.badge, 0, Qt.AlignTop)
            top.addStretch(1)
            top.addWidget(self.pwr, 0, Qt.AlignTop)
            lay.addLayout(top)
            lay.addStretch(1)
            names = QHBoxLayout()
            names.setSpacing(5)
            names.addWidget(self.link)
            names.addWidget(self.name, 1)
            lay.addLayout(names)
            lay.addWidget(self.status)
            self._name_width = self.W - 34
            if large:
                self.metrics.hide()
                grid = QGridLayout()
                grid.setContentsMargins(0, 6, 0, 0)
                grid.setHorizontalSpacing(10)
                grid.setVerticalSpacing(4)
                for i in range(6):
                    cell = QVBoxLayout()
                    cell.setSpacing(0)
                    value = QLabel("")
                    value.setObjectName("TileValue")
                    label = QLabel("")
                    label.setObjectName("TileLabel")
                    cell.addWidget(value)
                    cell.addWidget(label)
                    grid.addLayout(cell, i // 2, i % 2)
                    self.cells.append((value, label))
                grid.setColumnStretch(0, 1)
                grid.setColumnStretch(1, 1)
                lay.addLayout(grid)
            else:
                lay.addSpacing(3)
                lay.addWidget(self.metrics)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setEnabled(False)
        self.setGraphicsEffect(self._fx)
        self._full_name = self.name.text()
        self._elide()
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(self.did, self.mapToGlobal(pos)))

    def _elide(self):
        width = self._name_width
        if self.size_name == "small":   # the name shares its row with the power button and the update mark
            width -= (38 if not self.pwr.isHidden() else 0) + (30 if not self.badge.isHidden() else 0)
        if not self.link.isHidden():
            width -= self.link.width() + 5
        fm = self.name.fontMetrics()
        self.name.setText(fm.elidedText(self._full_name, Qt.ElideRight, max(40, width)))
        if self.size_name != "small":
            self.name.setToolTip(self._full_name)

    def set_update(self, text: str | None):
        """text: 'Firmware update available: …' or None."""
        self.badge.setVisible(bool(text))
        self.badge.setToolTip(text or "")
        self._elide()

    def update_state(self, st, has_power: bool, is_on, link: tuple[str, str] = ("", "")):
        """link: ("local" | "cloud" | "", explanation) - how the device is reached right now."""
        self.link.set_kind(*link)
        readings = st.summary.metric_values(st.values) if st.summary is not None else []
        # the battery is among the readings of a medium or large tile, so the status line leaves it out
        with_battery = self.size_name == "small" or not readings
        if st.online is False:
            text, cls = "Offline", "TileStatusBad"
        elif st.needs_cloud:
            text, cls = "Sign in to Mi account to control", "TileStatus"
        elif st.setup_needed:
            text, cls = "Needs setup - open to fix", "TileStatus"
        elif st.summary is not None and st.online:
            text, cls = st.summary.status_text(st.values, battery=with_battery) or "Online", "TileStatus"
        elif st.online:
            text, cls = "Online", "TileStatus"
        else:
            text, cls = "Connecting…", "TileStatus"
        fm = self.status.fontMetrics()
        self.status.setText(fm.elidedText(text, Qt.ElideRight, self.W - 34))
        self.status.setToolTip(st.error or text)
        repolish(self.status, cls)
        tip = "\n".join(f"{label}: {value}" for _sign, label, value in readings)
        if self.size_name == "small":
            self.name.setToolTip("\n".join(x for x in (self._full_name, text, tip) if x))
        elif self.size_name == "medium":
            fm = self.metrics.fontMetrics()
            line = ""
            for sign, _label, value in readings[:3]:   # as many whole readings as fit - none cut in half
                longer = (line + "   " if line else "") + f"{sign} {value}".strip()
                if line and fm.horizontalAdvance(longer) > self.W - 30:
                    break
                line = longer
            self.metrics.setText(fm.elidedText(line, Qt.ElideRight, self.W - 30))
            self.metrics.setToolTip(tip)
        else:
            for i, (value, label) in enumerate(self.cells):
                if i < len(readings):
                    sign, name, val = readings[i]
                    value.setText(f"{sign} {val}".strip())
                    label.setText(label.fontMetrics().elidedText(name, Qt.ElideRight, (self.W - 44) // 2))
                else:
                    value.setText("")
                    label.setText("")
        self.pwr.setVisible(has_power)
        self.pwr.setEnabled(st.online is not False)
        self.pwr.set_state(is_on if st.online else None)
        self._elide()
        on_look = is_on or (is_on is None and st.online)
        if self.size_name == "small":
            repolish(self.icon, "IconSmallOn" if on_look else "IconSmall")
        else:
            repolish(self.icon, "IconOn" if on_look else "Icon")
        self._fx.setOpacity(0.55)
        self._fx.setEnabled(st.online is False)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.did)
        super().mouseReleaseEvent(e)


class BellButton(QPushButton):
    """The notifications button: a bell with the number of unread notifications on it."""

    def __init__(self, parent=None):
        super().__init__("🔔", parent)
        self.setObjectName("IconBtn")
        self.count = 0

    def set_count(self, n: int):
        self.count = max(0, int(n))
        self.setToolTip(f"Notifications - {self.count} new" if self.count else "Notifications")
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self.count:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        text = str(self.count) if self.count < 100 else "99+"
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        f.setBold(True)
        p.setFont(f)
        w = max(16, p.fontMetrics().horizontalAdvance(text) + 8)
        r = QRectF(self.width() - w - 1, 1, w, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#E5484D"))
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QColor("white"))
        p.drawText(r, Qt.AlignCenter, text)
        p.end()


class HomePage(QWidget):
    open_device = Signal(str)
    power = Signal(str, bool)

    def __init__(self, main):
        super().__init__()
        self.setObjectName("Page")
        self.main = main
        self.room = None
        self.tiles: dict[str, DeviceTile] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # header
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(36, 26, 28, 6)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.home_btn = QPushButton("My Home")
        self.home_btn.setObjectName("HomeTitle")
        self.home_btn.setCursor(Qt.PointingHandCursor)
        self.home_btn.setToolTip("Click to choose a home - right-click to edit it (name, rooms)")
        self.home_btn.clicked.connect(self._home_menu)
        self.home_btn.setContextMenuPolicy(Qt.CustomContextMenu)
        self.home_btn.customContextMenuRequested.connect(self._home_edit_menu)
        col.addWidget(self.home_btn, 0, Qt.AlignLeft)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("Muted")
        col.addWidget(self.subtitle)
        hl.addLayout(col, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search devices…")
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(220)
        self.search.setToolTip("Filter by name, room or model")
        self.search.textChanged.connect(self._apply_filter)
        hl.addWidget(self.search, 0, Qt.AlignTop)
        self.online_only = QPushButton("Online only")
        self.online_only.setObjectName("Chip")
        self.online_only.setCheckable(True)
        self.online_only.setCursor(Qt.PointingHandCursor)
        self.online_only.setToolTip("Hide devices that are offline")
        self.online_only.setChecked(bool(main.cfg.data.get("online_only")))
        self.online_only.toggled.connect(self._online_only_toggled)
        hl.addWidget(self.online_only, 0, Qt.AlignTop)
        self.bell = BellButton()
        self.bell.setCursor(Qt.PointingHandCursor)
        self.bell.set_count(0)
        self.bell.clicked.connect(main.open_notifications)
        hl.addWidget(self.bell, 0, Qt.AlignTop)
        self.size_btn = QPushButton("▦")
        self.size_btn.setObjectName("IconBtn")
        self.size_btn.setToolTip("Tile size")
        self.size_btn.setCursor(Qt.PointingHandCursor)
        self.size_menu = QMenu(self)
        for key, text in TILE_SIZE_NAMES.items():
            act = self.size_menu.addAction(text, lambda k=key: main.set_tile_size(k))
            act.setCheckable(True)
            act.setData(key)
        self.size_btn.setMenu(self.size_menu)
        hl.addWidget(self.size_btn, 0, Qt.AlignTop)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("IconBtn")
        self.refresh_btn.setToolTip("Refresh all devices now (F5)")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.clicked.connect(main.refresh_all)
        hl.addWidget(self.refresh_btn, 0, Qt.AlignTop)
        QShortcut(QKeySequence.Refresh, self, activated=main.refresh_all)
        self.actions_btn = QPushButton("⚡")
        self.actions_btn.setObjectName("IconBtn")
        self.actions_btn.setToolTip("Actions and scenes - several commands with one click")
        self.actions_btn.setCursor(Qt.PointingHandCursor)
        self.actions_btn.clicked.connect(main.open_actions)
        hl.addWidget(self.actions_btn, 0, Qt.AlignTop)
        self.add_btn = QPushButton("+")
        self.add_btn.setObjectName("IconBtn")
        self.add_btn.setToolTip("Add devices")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_menu = QMenu(self)
        self.add_btn.setMenu(self.add_menu)
        hl.addWidget(self.add_btn, 0, Qt.AlignTop)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("IconBtn")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.clicked.connect(main.toggle_theme)
        hl.addWidget(self.theme_btn, 0, Qt.AlignTop)
        gear = QPushButton("⚙")
        gear.setObjectName("IconBtn")
        gear.setToolTip("Settings")
        gear.setCursor(Qt.PointingHandCursor)
        gear.clicked.connect(main.settings)
        hl.addWidget(gear, 0, Qt.AlignTop)
        root.addWidget(head)

        # pinned actions
        self.actions_bar = QWidget()
        self.actions_flow = FlowLayout(self.actions_bar, spacing=8)
        self.actions_flow.setContentsMargins(36, 6, 28, 2)
        root.addWidget(self.actions_bar)

        # room chips
        self.chips_area = QScrollArea()
        self.chips_area.setWidgetResizable(True)
        self.chips_area.setFixedHeight(54)
        self.chips_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chips_host = QWidget()
        self.chips_host.setObjectName("Scroll")
        self.chips_lay = QHBoxLayout(self.chips_host)
        self.chips_lay.setContentsMargins(36, 8, 28, 8)
        self.chips_lay.setSpacing(8)
        self.chips_area.setWidget(self.chips_host)
        root.addWidget(self.chips_area)

        # tiles
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("Scroll")
        outer = QVBoxLayout(self.grid_host)
        outer.setContentsMargins(36, 10, 28, 28)
        self.flow = FlowLayout(spacing=14)
        outer.addLayout(self.flow)
        outer.addStretch(1)
        self.area.setWidget(self.grid_host)
        root.addWidget(self.area, 1)

        self.empty = main.build_welcome()
        root.addWidget(self.empty, 1)

    # -- building ---------------------------------------------------------------------------
    def set_menu(self, signed_in: bool):
        m = self.add_menu
        m.clear()
        m.addAction("Sync devices from Mi account" if signed_in else "Sign in to Mi account…", self.main.account_clicked)
        m.addAction("Add a new camera with a QR code…", self.main.pair_camera)
        m.addAction("Scan local network…", self.main.scan)
        m.addAction("Add device by IP and token…", self.main.add_device)
        m.addSeparator()
        m.addAction("Manage devices…", self.main.manage_devices)
        m.addAction("Firmware updates…", self.main.show_firmware_list)

    def rebuild(self):
        from . import theme
        dark = theme.MODE == "dark"
        self.theme_btn.setText("☀" if dark else "☾")
        self.theme_btn.setToolTip("Switch to light mode" if dark else "Switch to dark mode")
        self._update_home_title()
        devices = sorted(self.home_devices(), key=lambda d: (d.get("room") or "~", (d.get("name") or "").lower()))
        rooms = []
        for d in devices:
            r = d.get("room") or ""
            if r and r not in rooms:
                rooms.append(r)
        if self.room not in rooms:
            self.room = None
        # chips
        while self.chips_lay.count():
            it = self.chips_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if rooms:
            for name in [None] + rooms:
                b = QPushButton(name or "All devices")
                b.setObjectName("Chip")
                b.setCheckable(True)
                b.setChecked(name == self.room)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, n=name: self.set_room(n))
                self.chips_lay.addWidget(b)
        self.chips_lay.addStretch(1)
        self.chips_area.setVisible(bool(rooms))
        # tiles
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.tiles.clear()
        size = self.main.tile_size()
        for act in self.size_menu.actions():
            act.setChecked(act.data() == size)
        self.flow.set_spacing(10 if size == "small" else 14)
        for d in devices:
            tile = DeviceTile(d, size)
            tile.clicked.connect(self.open_device)
            tile.power.connect(self.power)
            tile.menu_requested.connect(self.main.tile_menu)
            self.flow.addWidget(tile)
            self.tiles[str(d["did"])] = tile
            tile.setVisible(self._wanted(d))
            self.refresh_tile(str(d["did"]))
        has = bool(devices)
        self.area.setVisible(has)
        self.empty.setVisible(not has)
        self.search.setVisible(len(devices) > 8)
        self.online_only.setVisible(len(devices) > 8)
        self.add_btn.setVisible(True)
        self.rebuild_actions()
        self.update_subtitle()

    def rebuild_actions(self):
        """The pinned actions as buttons above the tiles."""
        while self.actions_flow.count():
            it = self.actions_flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        pinned = [a for a in self.main.cfg.data.get("actions") or [] if a.get("pinned")]
        for a in pinned:
            b = QPushButton(f"▶ {a.get('name') or 'Action'}")
            b.setObjectName("Chip")
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(" · ".join(s.get("label", "") for s in a.get("steps") or []))
            b.clicked.connect(lambda _=False, x=a: self.main.run_user_action(x))
            self.actions_flow.addWidget(b)
        self.actions_bar.setVisible(bool(pinned))

    # -- homes -------------------------------------------------------------------------------------------
    def home_devices(self) -> list[dict]:
        """The devices of the chosen home (all of them when no home is chosen)."""
        hid = self.main.current_home_id()
        devices = self.main.cfg.devices
        return devices if hid is None else [d for d in devices if str(d.get("home_id") or "") == hid]

    def _update_home_title(self):
        homes = self.main.homes()
        home = self.main.current_home()
        if home is not None:
            text = home["name"]
        elif len(homes) > 1:
            text = "All homes"
        elif homes:
            text = homes[0]["name"]
        else:
            text = "My Home"
        self.home_btn.setText(f"{text} ▾" if len(homes) > 1 else text)

    def _home_menu(self):
        m = self.build_home_menu()
        m.exec(self.home_btn.mapToGlobal(self.home_btn.rect().bottomLeft()))
        m.deleteLater()

    def build_home_menu(self) -> QMenu:
        """Left click on the home's name: choose which home is shown."""
        homes = self.main.homes()
        m = QMenu(self)
        if len(homes) > 1:
            a = m.addAction("All homes", lambda: self.main.set_home(None))
            a.setCheckable(True)
            a.setChecked(self.main.current_home_id() is None)
        for h in homes:
            n = sum(1 for d in self.main.cfg.devices if str(d.get("home_id") or "") == h["id"])
            a = m.addAction(f"{h['name']}   ({n})", lambda hid=h["id"]: self.main.set_home(hid))
            a.setCheckable(True)
            a.setChecked(self.main.current_home_id() == h["id"])
        if not homes:
            a = m.addAction("Sign in to see the homes of your Mi account" if not self.main.signed_in else
                            "Loading the homes of your Mi account…")
            a.setEnabled(False)
        m.addSeparator()
        m.addAction("Edit this home…" if self.main.current_home() else "Edit a home…",
                    lambda: self._home_edit_menu(self.home_btn.rect().bottomLeft()))
        return m

    def _home_edit_menu(self, pos):
        m = self.build_home_edit_menu()
        m.exec(self.home_btn.mapToGlobal(pos))
        m.deleteLater()

    def build_home_edit_menu(self) -> QMenu:
        """Right click on the home's name: rename the home, add / rename / delete rooms."""
        m = QMenu(self)
        home = self.main.current_home()
        homes = [home] if home else self.main.homes()
        if not homes:
            a = m.addAction("Sign in to your Mi account to edit your homes")
            a.setEnabled(False)
        for h in homes:
            target = m if len(homes) == 1 else m.addMenu(h["name"])
            if len(homes) == 1:
                title = target.addAction(h["name"])
                title.setEnabled(False)
            target.addAction("Rename home…", lambda h=h: self.main.rename_home(h))
            target.addAction("Add room…", lambda h=h: self.main.add_room(h))
            rooms = h.get("rooms") or []
            if rooms:
                ren = target.addMenu("Rename room")
                dele = target.addMenu("Delete room")
                for r in rooms:
                    ren.addAction(r["name"] or "(no name)", lambda h=h, r=r: self.main.rename_room(h, r))
                    dele.addAction(r["name"] or "(no name)", lambda h=h, r=r: self.main.delete_room(h, r))
        m.addSeparator()
        m.addAction("Reload homes from the Mi account", self.main.load_homes)
        return m

    def _wanted(self, d: dict) -> bool:
        """Should this device's tile be shown with the current room, search text and 'online only'?"""
        hid = self.main.current_home_id()
        if hid is not None and str(d.get("home_id") or "") != hid:
            return False
        if self.room is not None and d.get("room") != self.room:
            return False
        text = self.search.text().strip().lower()
        if text and not all(word in " ".join((d.get("name") or "", d.get("room") or "",
                                              d.get("model") or "")).lower() for word in text.split()):
            return False
        if self.online_only.isChecked() and self.main.hub.state(d["did"]).online is False:
            return False
        return True

    def _apply_filter(self, *_):
        for did, tile in self.tiles.items():
            tile.setVisible(self._wanted(self.main.cfg.device(did) or {"did": did}))
        self.flow.invalidate()
        self.grid_host.updateGeometry()

    def _online_only_toggled(self, on):
        self.main.cfg.data["online_only"] = bool(on)
        self._apply_filter()

    def set_room(self, room):
        self.room = room
        for i in range(self.chips_lay.count()):
            w = self.chips_lay.itemAt(i).widget()
            if isinstance(w, QPushButton):
                w.setChecked((w.text() == "All devices" and room is None) or w.text() == room)
        self._apply_filter()

    def refresh_tile(self, did):
        tile = self.tiles.get(str(did))
        if tile is None:
            return
        hub = self.main.hub
        link_info = getattr(self.main, "link_info", None)
        tile.update_state(hub.state(did), hub.has_power(did), hub.is_on(did),
                          link_info(did) if link_info else ("", ""))
        tile.set_update(self.main.update_text(did))
        if self.online_only.isChecked():
            want = self._wanted(self.main.cfg.device(did) or {"did": did})
            if want != (not tile.isHidden()):
                tile.setVisible(want)
                self.flow.invalidate()
                self.grid_host.updateGeometry()
        self.update_subtitle()

    def update_subtitle(self):
        devices = self.home_devices()
        n = len(devices)
        online = sum(1 for d in devices if self.main.hub.state(d["did"]).online)
        on = sum(1 for d in devices if self.main.hub.is_on(d["did"]))
        if not n:
            self.subtitle.setText("No devices yet")
            return
        parts = [f"{n} device{'s' if n != 1 else ''}", f"{online} online"]
        if on:
            parts.append(f"{on} switched on")
        states = [self.main.hub.state(d["did"]) for d in devices]
        local = sum(1 for st in states if st.online and st.transport == "local")
        cloud = sum(1 for st in states if st.online and st.transport == "cloud")
        if local or cloud:
            parts.append(f"{local} on the local network, {cloud} through the Mi cloud")
        self.subtitle.setText("  ·  ".join(parts))
        self.subtitle.setToolTip("The sign in front of a device's name: a green house = reached over your local "
                                 "network, a blue cloud = through the Mi cloud.\nRight-click a device → Connection "
                                 "to choose; Settings → Connection chooses for all devices at once.")
