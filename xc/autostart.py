"""'Start with Windows': a per-user Run entry that starts XiControl hidden in the tray."""
from __future__ import annotations

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "XiControl"


def command(executable: str | None = None, frozen: bool | None = None, main_py: str | None = None) -> str:
    """The command line Windows runs at sign-in (arguments are for tests)."""
    executable = executable or sys.executable
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        return f'"{executable}" --tray'
    folder, exe = os.path.split(executable)
    if exe.lower() == "python.exe" and os.path.exists(os.path.join(folder, "pythonw.exe")):
        executable = os.path.join(folder, "pythonw.exe")  # no console window
    main_py = main_py or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    return f'"{executable}" "{main_py}" --tray'


def supported() -> bool:
    return sys.platform == "win32"


def _open(write=False):
    import winreg
    access = winreg.KEY_READ | (winreg.KEY_SET_VALUE if write else 0)
    return winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, access)


def current() -> str | None:
    """The command stored in the Run key, or None."""
    if not supported():
        return None
    import winreg
    try:
        with _open() as key:
            return winreg.QueryValueEx(key, NAME)[0]
    except OSError:
        return None


def is_enabled() -> bool:
    return current() is not None


def set_enabled(on: bool):
    if not supported():
        return
    import winreg
    with _open(write=True) as key:
        if on:
            winreg.SetValueEx(key, NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, NAME)
            except FileNotFoundError:
                pass


def refresh():
    """Keeps an existing entry pointing at this copy of the app (e.g. after the folder moved)."""
    stored = current()
    if stored is not None and stored != command():
        set_enabled(True)
