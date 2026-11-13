"""Settings and device list, stored as JSON in the user's app-data folder.

Secrets (the Mi account session, device tokens, camera stream addresses) are encrypted with
xc.secure before they are written; in memory everything is plain. The previous good file is
kept as config.json.bak, and a damaged file is set aside instead of being overwritten."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import threading
import time

from . import secure

log = logging.getLogger(__name__)

SECRET_DEVICE_FIELDS = ("token", "rtsp_url")


def app_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "XiControl")
    else:
        path = os.path.join(os.path.expanduser("~"), ".xicontrol")
    os.makedirs(path, exist_ok=True)
    return path


DEFAULTS = {
    "server": "de",
    "refresh_seconds": 10,
    "cloud_session": None,
    "devices": [],
    "close_to_tray": True,
    "notifications": True,
}


class Config:
    def __init__(self, path: str | None = None):
        self.path = path or os.path.join(app_dir(), "config.json")
        self._lock = threading.Lock()
        self.data = dict(DEFAULTS)
        self.warning = ""           # shown to the user once after start-up
        self._plain_on_disk = False  # an old, unencrypted file that must be rewritten
        raw = self._read(self.path)
        if raw is None and os.path.exists(self.path):
            bad = f"{self.path}.damaged-{time.strftime('%Y%m%d-%H%M%S')}"
            try:
                os.replace(self.path, bad)
            except OSError:
                bad = self.path
            raw = self._read(self.path + ".bak")
            self.warning = ("The settings file was damaged and has been restored from the last backup."
                            if raw is not None else
                            "The settings file was damaged, so XiControl started with empty settings.")
            self.warning += f"\nThe damaged file was kept as {os.path.basename(bad)}."
            log.warning("config.json unreadable, moved to %s (backup used: %s)", bad, raw is not None)
        if raw:
            self.data.update(raw)
        self.data["devices"] = list(self.data.get("devices") or [])
        self._decrypt_secrets()
        if self._plain_on_disk and secure.AVAILABLE:
            try:
                self.save()  # encrypt keys left in plain text by older versions
            except OSError as exc:
                log.warning("could not rewrite config.json encrypted: %s", exc)

    @staticmethod
    def _read(path: str):
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _decrypt_secrets(self):
        lost = False
        sess = self.data.get("cloud_session")
        if isinstance(sess, str):
            try:
                self.data["cloud_session"] = json.loads(secure.decrypt(sess))
            except (secure.SecretError, ValueError):
                self.data["cloud_session"] = None
                lost = True
        elif isinstance(sess, dict):
            self._plain_on_disk = True
        for d in self.devices:
            for k in SECRET_DEVICE_FIELDS:
                v = d.get(k)
                if not v:
                    continue
                if secure.is_encrypted(v):
                    try:
                        d[k] = secure.decrypt(v)
                    except secure.SecretError:
                        d[k] = ""
                        lost = True
                else:
                    self._plain_on_disk = True
        if lost:
            self.warning = (self.warning + "\n\n" if self.warning else "") + (
                "Some saved keys can't be read by this Windows account (the settings were copied from another "
                "PC or user). Sign in to your Mi account again to restore them.")
            log.warning("some secrets could not be decrypted")

    def _encrypted_copy(self) -> dict:
        out = dict(self.data)
        sess = self.data.get("cloud_session")
        out["cloud_session"] = secure.encrypt(json.dumps(sess)) if sess else None
        out["devices"] = [{**d, **{k: secure.encrypt(d[k]) for k in SECRET_DEVICE_FIELDS if d.get(k)}}
                          for d in self.devices]
        return out

    def save(self):
        with self._lock:
            data = self._encrypted_copy()
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if os.path.exists(self.path) and not self._plain_on_disk:
                try:
                    shutil.copyfile(self.path, self.path + ".bak")
                except OSError:
                    pass
            os.replace(tmp, self.path)
            if self._plain_on_disk:  # don't leave the old plain-text keys behind in the backup
                shutil.copyfile(self.path, self.path + ".bak")
                self._plain_on_disk = False

    # -- devices ---------------------------------------------------------------
    @property
    def devices(self) -> list[dict]:
        return self.data["devices"]

    def device(self, did: str) -> dict | None:
        for d in self.devices:
            if str(d.get("did")) == str(did):
                return d
        return None

    def upsert(self, info: dict) -> dict:
        existing = self.device(info["did"])
        if existing:
            for k, v in info.items():
                if v not in (None, "") or k not in existing:
                    existing[k] = v
            return existing
        info.setdefault("mode", "auto")
        self.devices.append(info)
        return info

    def remove(self, did: str):
        self.data["devices"] = [d for d in self.devices if str(d.get("did")) != str(did)]
