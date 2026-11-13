"""Actions made in XiControl: a name and a list of steps ("all lights off", "vacuum + purifier on") that run
one after the other with one click. Steps are plain dicts so they can be saved in config.json:

    {"did": "123", "kind": "power" | "prop" | "action" | "raw" | "wait", "label": "Lamp: Turn off",
     "siid": 2, "piid": 1, "aiid": 1, "value": False, "method": "set_power", "params": ["off"], "seconds": 5}
"""
from __future__ import annotations

import time
import uuid

from .friendly import action_label, needs_confirmation
from .spec import UNITS
from .summary import LegacySummary, Summary

WAIT_MAX = 600
# device commands that don't belong in a one-click action (they are for the device page only)
NOT_FOR_ACTIONS = ("reset", "clear", "factory", "delete", "restore", "upgrade", "calibrat", "split", "merge",
                   "get-", "query", "sync", "set-", "download", "build-map", "remote", "imu")


def new_action(name: str = "") -> dict:
    return {"id": uuid.uuid4().hex[:12], "name": name, "steps": [], "pinned": False}


def _command(label, kind, **fields) -> dict:
    return {"label": label, "kind": kind, **fields}


def device_commands(summary) -> list[dict]:
    """What a device can do inside an action. Commands that need a number carry "needs_value" and "range"."""
    out = []
    if isinstance(summary, LegacySummary):
        pw = summary.profile.get("power")
        if pw:
            for word, key in (("Turn on", "on"), ("Turn off", "off")):
                method, params = pw[key]
                out.append(_command(word, "raw", method=method, params=params))
        return out
    if not isinstance(summary, Summary):
        return out
    if summary.power is not None:
        p = summary.power
        out.append(_command("Turn on", "power", siid=p.siid, piid=p.piid, value=True))
        out.append(_command("Turn off", "power", siid=p.siid, piid=p.piid, value=False))
    for p in summary.modes:
        for v in p.values:
            out.append(_command(f"{p.label}: {p.value_text(v.get('value'))}", "prop", siid=p.siid, piid=p.piid,
                                value=v.get("value")))
    for p in summary.sliders:
        out.append(_command(f"{p.label}…", "prop", siid=p.siid, piid=p.piid, needs_value=True,
                            range=list(p.range or [0, 100, 1]), unit=UNITS.get(p.unit, p.unit or "")))
    seen = set()
    for a in list(summary.actions) + [a for s in summary.services for a in s.actions]:
        key = (a.siid, a.aiid)
        if key in seen or a.ins or needs_confirmation(a) or any(w in a.name for w in NOT_FOR_ACTIONS):
            continue
        seen.add(key)
        out.append(_command(action_label(a)[0], "action", siid=a.siid, aiid=a.aiid))
    return out


def step_from(did: str, device_name: str, command: dict, value=None) -> dict:
    step = {"did": str(did), "kind": command["kind"], "label": f"{device_name}: {command['label'].rstrip('…')}"}
    for k in ("siid", "piid", "aiid", "value", "method", "params"):
        if k in command:
            step[k] = command[k]
    if command.get("needs_value"):
        step["value"] = value
        step["label"] += f" → {value}{(' ' + command['unit']) if command.get('unit') else ''}"
    return step


def wait_step(seconds: int) -> dict:
    seconds = max(1, min(WAIT_MAX, int(seconds)))
    return {"kind": "wait", "seconds": seconds, "label": f"Wait {seconds} s"}


def run_step(ctl, step: dict, sleep=time.sleep):
    kind = step.get("kind")
    if kind == "wait":
        sleep(max(0, min(WAIT_MAX, int(step.get("seconds") or 0))))
        return None
    if kind in ("power", "prop"):
        return ctl.set_prop(step["siid"], step["piid"], step.get("value"))
    if kind == "action":
        return ctl.action(step["siid"], step["aiid"], [])
    if kind == "raw":
        return ctl.raw(step["method"], step.get("params") or [])
    raise ValueError(f"unknown step kind {kind!r}")


def run_action(action: dict, controller_for, device_for, friendly=str, sleep=time.sleep) -> list[tuple[dict, str]]:
    """Runs every step; a failing step doesn't stop the others. Returns [(step, error)] for the failed ones.
    controller_for(info) -> DeviceController; device_for(did) -> device info or None."""
    failed = []
    for step in action.get("steps") or []:
        if step.get("kind") == "wait":
            run_step(None, step, sleep)
            continue
        info = device_for(step.get("did"))
        if info is None:
            failed.append((step, "The device is no longer in XiControl"))
            continue
        try:
            run_step(controller_for(info), step, sleep)
        except Exception as exc:  # noqa: BLE001 - reported to the user, the next step still runs
            failed.append((step, friendly(exc)))
    return failed


def describe_result(action: dict, failed: list[tuple[dict, str]]) -> str:
    total = sum(1 for s in action.get("steps") or [] if s.get("kind") != "wait")
    name = action.get("name") or "Action"
    if not failed:
        return f"“{name}” done ✓"
    first_step, first_error = failed[0]
    who = first_step.get("label", "").split(":")[0]
    return f"“{name}”: {total - len(failed)} of {total} done - {who}: {first_error.splitlines()[0][:80]}"
