"""Decides when a device change is worth a notification, so the tray doesn't nag.

  * offline: only for a device that was online in this session and then missed OFFLINE_AFTER
    polls in a row (one lost UDP packet is not an outage); "back online" only after that.
  * consumables (filter, brushes, mop): once when the level drops to LOW_LEVEL %, and again
    only after it went back up (part replaced and the counter reset) and fell again. What has
    been reported is remembered across restarts (see low_keys), so starting the app does not
    repeat old reminders."""
from __future__ import annotations

OFFLINE_AFTER = 2
LOW_LEVEL = 10
REARM_ABOVE = LOW_LEVEL + 5


class Alerts:
    def __init__(self, low=()):
        """low: consumables already reported, as saved by low_keys()."""
        self._seen_online: set[str] = set()
        self._offline: set[str] = set()
        self._low: set[tuple[str, str]] = {(str(a), str(b)) for a, b in (low or []) if a and b}

    def low_keys(self) -> list[list[str]]:
        return sorted([did, label] for did, label in self._low)

    def forget(self, did: str):
        did = str(did)
        self._seen_online.discard(did)
        self._offline.discard(did)
        self._low = {k for k in self._low if k[0] != did}

    def device(self, did: str, name: str, online, fails: int) -> list[tuple[str, str, str]]:
        """online: True / False / None (unknown). Returns [(kind, title, text)]."""
        did = str(did)
        if online is True:
            self._seen_online.add(did)
            if did in self._offline:
                self._offline.discard(did)
                return [("online", f"{name} is back online", "It's answering again.")]
        elif online is False and fails >= OFFLINE_AFTER and did in self._seen_online and did not in self._offline:
            self._offline.add(did)
            return [("offline", f"{name} is offline",
                     "It stopped answering - check that it has power and is connected to Wi-Fi.")]
        return []

    def consumables(self, did: str, name: str, levels: dict[str, float]) -> list[tuple[str, str, str]]:
        """levels: {'Filter life': 8, ...} in percent."""
        did = str(did)
        out = []
        for label, value in levels.items():
            key = (did, label)
            if value <= LOW_LEVEL and key not in self._low:
                self._low.add(key)
                out.append(("consumable", f"{name}: {label} {int(value)}%",
                            "Time to replace it soon. Reset the counter on the device page afterwards."))
            elif value >= REARM_ABOVE:
                self._low.discard(key)
        return out


def summarize(events: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """Several maintenance reminders as one notification."""
    if not events:
        return None
    if len(events) == 1:
        return events[0]
    names = [title for _kind, title, _text in events]
    more = f" and {len(names) - 3} more" if len(names) > 3 else ""
    return ("consumable", f"{len(names)} parts need replacing soon", "; ".join(names[:3]) + more + ".")
