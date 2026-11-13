"""The right-click menu of a device tile: open, switch, the device's own actions, rename, room, firmware, remove."""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from xc.actions import device_commands
from xc.device import MODES, DeviceController

from .firmware import has_update, is_bluetooth

MAX_QUICK = 6        # the device's main actions directly in the menu
MAX_MORE = 20        # the rest in a submenu


def build_device_menu(main, did: str, parent=None) -> QMenu:
    did = str(did)
    info = main.cfg.device(did) or {"did": did}
    st = main.hub.state(did)
    m = QMenu(parent)
    title = m.addAction(info.get("name") or did)
    title.setEnabled(False)
    m.addAction("Open", lambda: main.open_device(did))
    if main.hub.has_power(did):
        on = main.hub.is_on(did)
        a = m.addAction("Turn off" if on else "Turn on", lambda: main.quick_power(did, not on))
        a.setEnabled(st.online is not False)

    # the device's own commands (as on its page)
    commands = [c for c in device_commands(st.summary) if c["kind"] == "action"]
    if commands:
        m.addSeparator()
        for c in commands[:MAX_QUICK]:
            a = m.addAction(c["label"], lambda c=c: main.run_device_command(did, c))
            a.setEnabled(st.online is not False)
        if len(commands) > MAX_QUICK:
            more = m.addMenu("More actions")
            for c in commands[MAX_QUICK:MAX_QUICK + MAX_MORE]:
                more.addAction(c["label"], lambda c=c: main.run_device_command(did, c))
    elif st.summary is None:
        a = m.addAction("Loading this device's actions…")
        a.setEnabled(False)
        main.hub.ensure_summary(did)

    m.addSeparator()
    m.addAction("Add to an action…", lambda: main.add_to_action(did))
    m.addAction("Rename…", lambda: main.rename_device(did))
    m.addAction("Move to room…", lambda: main.move_device(did))
    fav = m.addAction("Show in tray menu")
    fav.setCheckable(True)
    fav.setChecked(bool(info.get("tray")))
    fav.toggled.connect(lambda on: main.set_tray(did, on))
    fw = main.firmware.get(did)
    label = "Firmware…" if not has_update(fw) else f"Firmware update available ({fw.get('latest')})…"
    a = m.addAction(label, lambda: main.show_firmware(did))
    a.setEnabled(main.signed_in or is_bluetooth(did))
    m.addAction("Automations with this device…", lambda: main.open_actions(device_filter=did))
    a = m.addAction("Sharing…", lambda: main.show_sharing(did))
    a.setEnabled(main.on_account(info))
    m.addAction("Rename and connection…", lambda: main.edit_device(did))
    for_all = DeviceController.mode_for_all
    conn = m.addMenu("Connection" if not for_all else f"Connection - “{MODES[for_all]}” for all devices")
    for code, text in (("auto", "Automatic - local network first, Mi cloud as backup"),
                       ("local", "Local network only"), ("cloud", "Mi cloud only")):
        a = conn.addAction(text, lambda code=code: main.set_device_mode([did], code))
        a.setCheckable(True)
        a.setChecked((for_all or info.get("mode") or "auto") == code)
        a.setEnabled(not for_all)
    if for_all:
        conn.addSeparator()
        conn.addAction("Choose per device again (Settings)…", main.settings)

    m.addSeparator()
    m.addAction("Remove from XiControl…", lambda: main.remove_device(did))
    a = m.addAction("Delete from Mi account…", lambda: main.remove_device(did, account=True))
    a.setEnabled(main.on_account(info) and not info.get("shared"))
    if not a.isEnabled():
        a.setToolTip("Sign in first - or the device was added by hand / shared with you")
    return m
