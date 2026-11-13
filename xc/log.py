"""Log file (%APPDATA%\\XiControl\\xicontrol.log) with secrets masked, for finding out what went wrong."""
from __future__ import annotations

import logging
import logging.handlers
import os
import re

_SECRETS = [
    (re.compile(r"\b[0-9a-fA-F]{32}\b"), "<token>"),
    (re.compile(r"V1:[A-Za-z0-9+/=_\-]{8,}"), "V1:<hidden>"),
    (re.compile(r"((?:service_?token|ssecurity|pass_?token|yetAnotherServiceToken|password)[\"']?\s*[:=]\s*[\"']?)"
                r"[^\"'\s,;&}]+", re.I), r"\1<hidden>"),
]


def redact(text: str) -> str:
    for rx, rep in _SECRETS:
        text = rx.sub(rep, text)
    return text


class _RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def setup(folder: str, console: bool = False) -> str:
    """Sends the 'xc' and 'gui' loggers to a rotating file; returns the file's path."""
    path = os.path.join(folder, "xicontrol.log")
    root = logging.getLogger()
    if any(getattr(h, "_xicontrol", False) for h in root.handlers):
        return path
    fmt = _RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    except OSError:
        fh = logging.StreamHandler()
    fh.setFormatter(fmt)
    fh._xicontrol = True
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch._xicontrol = True
        root.addHandler(ch)
    root.setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return path
