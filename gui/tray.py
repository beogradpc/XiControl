"""System-tray icon: quick on/off for favourite devices, and notifications (device offline / back
online, filter or brush running out, Mi account sign-in needed) while XiControl runs in the background."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from xc.alerts import Alerts, summarize
from xc.catalog import category
from xc.cloud import friendly
from xc.summary import Summary

from .icon import app_icon

log = logging.getLogger(__name__)
MAINTENANCE_QUIET_MS = 20_000   # wait this long for further reminders before showing them together


class Tray(QObject):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.alerts = Alerts(main.cfg.data.get("alerts_low"))
        self.history: list[tuple[str, str, str]] = []   # every notification, also when not shown
        self.log: list[dict] = []                       # the same for the Notifications window (newest last)
        self._click_target = None                        # did, "sign-in" or None
        # reminders that turn up together (e.g. while the first readings come in after start-up)
        # are shown as ONE notification
        self._maintenance: list[tuple[str, str, str]] = []
        self._maintenance_timer = QTimer(self)
        self._maintenance_timer.setSingleShot(True)
        self._maintenance_timer.setInterval(MAINTENANCE_QUIET_MS)
        self._maintenance_timer.timeout.connect(self._flush_maintenance)
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = None
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.rebuild_menu)
        if self.available:
            self.icon = QSystemTrayIcon(app_icon(), self)
            self.icon.setContextMenu(self.menu)
            self.icon.activated.connect(self._activated)
            self.icon.messageClicked.connect(self._message_clicked)
            self.icon.show()
        self.rebuild_menu()
        self.update_tooltip()

    @property
    def notifications_on(self) -> bool:
        return bool(self.main.cfg.data.get("notifications", True))

    # -- menu ---------------------------------------------------------------------------------
    def rebuild_menu(self):
        m = self.menu
        m.clear()
        op = m.addAction("Open XiControl", self.main.bring_to_front)
        f = QFont(op.font())
        f.setBold(True)
        op.setFont(f)
        m.addSeparator()
        hub = self.main.hub
        favourites = [d for d in self.main.cfg.devices if d.get("tray")]
        if not favourites:
            hint = m.addAction("Add devices here with ⋯ → Show in tray menu")
            hint.setEnabled(False)
        for d in favourites:
            did = str(d["did"])
            st = hub.state(did)
            on = hub.is_on(did)
            _k, icon, _l = category(d.get("model", ""))
            if st.online is False:
                state = "offline"
            elif on is None:
                state = "…"
            else:
                state = "on" if on else "off"
            a = QAction(f"{icon}  {d.get('name') or did}   ({state})", m)
            a.setCheckable(True)
            a.setChecked(bool(on))
            a.setEnabled(hub.has_power(did) and st.online is not False and on is not None)
            a.toggled.connect(lambda checked, x=did: self._switch(x, checked))
            m.addAction(a)
        m.addSeparator()
        m.addAction("Quit XiControl", self.main.quit_app)

    def _switch(self, did, on):
        name = (self.main.cfg.device(did) or {}).get("name", "Device")
        self.main.hub.set_power(did, on, error=lambda e: self.notify(
            "error", f"Couldn't switch {name}", friendly(e), did, force=True))

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.main.bring_to_front()

    def update_tooltip(self):
        if not self.icon:
            return
        devs = self.main.cfg.devices
        online = sum(1 for d in devs if self.main.hub.state(d["did"]).online)
        self.icon.setToolTip(f"XiControl · {len(devs)} devices · {online} online")

    # -- notifications --------------------------------------------------------------------------
    def device_changed(self, did):
        did = str(did)
        info = self.main.cfg.device(did)
        if not info:
            return
        st = self.main.hub.state(did)
        name = info.get("name") or did
        events = self.alerts.device(did, name, st.online, st.fails)
        if isinstance(st.summary, Summary) and st.online:
            levels = {}
            for p, label in st.summary.life_levels:
                v = st.values.get((p.siid, p.piid))
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    levels[label] = v
            reminders = self.alerts.consumables(did, name, levels)
            if reminders:
                self._maintenance += [(did, e) for e in reminders]
                self._maintenance_timer.start()
                self.main.cfg.data["alerts_low"] = self.alerts.low_keys()  # don't repeat them next start
        for kind, title, text in events:
            self.notify(kind, title, text, did)
        self.update_tooltip()

    def _flush_maintenance(self):
        pending, self._maintenance = self._maintenance, []
        note = summarize([e for _did, e in pending])
        if note:
            kind, title, text = note
            self.notify(kind, title, text, pending[0][0] if len(pending) == 1 else None)
            self.main.cfg.save()

    def sign_in_needed(self):
        self.notify("sign-in", "Mi account: sign in again",
                    "The session ended and couldn't be renewed. Click here to sign in.", "sign-in", force=True)

    def notify(self, kind, title, text, target=None, force=False):
        """force: shown even with notifications switched off (the user asked for this action)."""
        self.history.append((kind, title, text))
        self.log.append({"kind": "app", "title": f"{title} - {text}" if text else title, "time": time.time(),
                         "did": target if target and target != "sign-in" else "", "device": "XiControl"})
        del self.log[:-50]
        if hasattr(self.main, "home"):
            self.main._update_bell()
        log.info("notification: %s - %s", title, text)
        if not self.icon or not (force or self.notifications_on):
            return
        self._click_target = target
        warn = kind in ("offline", "consumable", "sign-in", "error")
        self.icon.showMessage(title, text, QSystemTrayIcon.Warning if warn else QSystemTrayIcon.Information, 8000)

    def _message_clicked(self):
        target, self._click_target = self._click_target, None
        self.main.bring_to_front()
        if target == "sign-in":
            self.main.sign_in()
        elif target and self.main.cfg.device(target):
            self.main.open_device(target)

    def first_hide_hint(self):
        if self.icon and not self.main.cfg.data.get("tray_hint_shown"):
            self.main.cfg.data["tray_hint_shown"] = True
            self.main.cfg.save()
            self.icon.showMessage("XiControl keeps running",
                                  "It's here in the tray, watching your devices. Right-click for quick switches; "
                                  "Quit XiControl closes it.", QSystemTrayIcon.Information, 6000)

    def hide(self):
        self._maintenance_timer.stop()
        if self.icon:
            self.icon.hide()
