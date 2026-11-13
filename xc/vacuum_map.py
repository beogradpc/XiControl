"""Maps of Xiaomi robot vacuums that keep them in the Mi cloud (the newer xiaomi.vacuum.* models, e.g. the
Robot Vacuum S40): which file holds the map, how to download it and how to read it.

The vacuum names its map file in the "vacuum-map" service ("map-obj-name": {"obj_name": "<uid>/<did>/<n>"}).
The Mi cloud hands out a download address for it (v2/home/get_interim_file_url_pro). The file is
{"version": 2, "data": base64} where data is AES-128-CBC encrypted, then zlib-compressed JSON; version 1 files
are just base64 + zlib. The key is derived from the model name and the device id the same way the Xiaomi Home
app's plugin for these vacuums does it.

The map itself is a grid (map_data, zlib + base64), one byte per cell of `resolution` mm, stored bottom row
first: 0 unknown, 1 wall, 2 floor not in a room, 255 door, from ROOM_INDEX on the id of the room. Positions
(robot, dock, path, room names) are in mm in the same coordinate system as origin_x/origin_y.
"""
from __future__ import annotations

import base64
import hashlib
import json
import zlib

import requests

IV = b"ABCDEF1234123412"
ROOM_INDEX = 3
UNKNOWN, WALL, FLOOR, DOOR = 0, 1, 2, 255
MAP_SERVICE = "vacuum-map"


class MapError(RuntimeError):
    """The map could not be fetched or read; the message is meant for the user."""


def _aes(key: bytes, data: bytes, encrypt: bool) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.CBC(IV))
    if encrypt:
        p = padding.PKCS7(128).padder()
        e = cipher.encryptor()
        return e.update(p.update(data) + p.finalize()) + e.finalize()
    d = cipher.decryptor()
    out = d.update(data) + d.finalize()
    u = padding.PKCS7(128).unpadder()
    return u.update(out) + u.finalize()


def map_key(model: str, did: str) -> bytes:
    """MD5 of AES(last 16 characters of the model + did, key = those 16 characters)."""
    model_key = model[-16:].encode()
    return hashlib.md5(_aes(model_key, model_key + str(did).encode(), True)).digest()


def encode_map(m: dict, model: str, did: str) -> bytes:
    """The opposite of decode_map - for tests."""
    blob = _aes(map_key(model, did), zlib.compress(json.dumps(m).encode()), True)
    return json.dumps({"version": 2, "data": base64.b64encode(blob).decode()}).encode()


def decode_map(raw: bytes, model: str, did: str) -> dict:
    text = raw.decode("utf-8", "replace").strip()
    if not text or "This is a null file" in text:
        raise MapError("The vacuum hasn't made a map yet - let it clean once (or start “Build map”).")
    try:
        wrapped = json.loads(text)
    except ValueError:
        wrapped = None
    try:
        if isinstance(wrapped, dict) and wrapped.get("version") == 2:
            plain = _aes(map_key(model, did), base64.b64decode(wrapped["data"]), False)
            m = json.loads(zlib.decompress(plain))
        elif isinstance(wrapped, dict) and isinstance(wrapped.get("data"), dict):
            m = wrapped["data"]
        else:  # version 1: base64 + zlib
            m = json.loads(zlib.decompress(base64.b64decode(text)))
    except (ValueError, KeyError, zlib.error) as exc:
        raise MapError("The map file of this vacuum can't be read (unknown format).") from exc
    if not isinstance(m, dict) or "map_data" not in m:
        raise MapError("The map file of this vacuum can't be read (no map in it).")
    return m


def grid(m: dict) -> bytes:
    """The cells of the map, bottom row first (width * height bytes)."""
    try:
        cells = zlib.decompress(base64.b64decode(m["map_data"]))
    except (ValueError, KeyError, zlib.error) as exc:
        raise MapError("The map of this vacuum is damaged.") from exc
    if len(cells) < int(m.get("width") or 0) * int(m.get("height") or 0):
        raise MapError("The map of this vacuum is incomplete.")
    return cells


def to_cell(m: dict, x: float, y: float) -> tuple[float, float]:
    """A position in mm -> (column, row counted from the TOP of the picture), in cells (not rounded)."""
    res = float(m.get("resolution") or 50)
    col = (x - float(m.get("origin_x") or 0)) / res
    row_from_bottom = (y - float(m.get("origin_y") or 0)) / res
    return col, float(m.get("height") or 0) - row_from_bottom


def path_points(m: dict) -> list[dict]:
    paths = m.get("paths")
    points = paths.get("points") if isinstance(paths, dict) else paths
    if isinstance(points, str):
        try:
            points = json.loads(points)
        except ValueError:
            points = []
    return [p for p in points or [] if isinstance(p, dict) and "x" in p and "y" in p]


def rooms(m: dict) -> list[dict]:
    """[{"id", "name", "x", "y"}] - the rooms with the position of their name label (mm)."""
    out = []
    for i, r in enumerate(m.get("room_attrs") or [], 1):
        if isinstance(r, dict) and r.get("id") is not None:
            out.append({"id": r["id"], "name": (r.get("room_name") or "").strip() or f"Room {i}",
                        "x": r.get("name_pos_x"), "y": r.get("name_pos_y")})
    return out


def room_colors(m: dict) -> dict[int, int]:
    """{grid value: colour number} - neighbouring rooms get different numbers."""
    return {int(r["grid_id"]): int(r.get("color") or 0) for r in m.get("map_room_info") or []
            if isinstance(r, dict) and r.get("grid_id") is not None}


# -- where the map is ---------------------------------------------------------------------------------
def map_service(spec):
    for svc in getattr(spec, "services", None) or []:
        if svc.name == MAP_SERVICE and any(p.name == "map-obj-name" for p in svc.props):
            return svc
    return None


def supports_map(spec) -> bool:
    return map_service(spec) is not None


def map_object(cloud, did: str, spec) -> str:
    """The name of the vacuum's current map file, from its "map-obj-name" property."""
    svc = map_service(spec)
    if svc is None:
        raise MapError("This vacuum doesn't share its map.")
    prop = next(p for p in svc.props if p.name == "map-obj-name")
    items = cloud.miot_get_many([{"did": str(did), "siid": svc.siid, "piid": prop.piid}])
    value = next((x.get("value") for x in items if x.get("code", 0) == 0), None)
    try:
        obj = json.loads(value).get("obj_name") if isinstance(value, str) else None
    except ValueError:
        obj = None
    if not obj:
        raise MapError("The vacuum hasn't made a map yet - let it clean once (or start “Build map”).")
    return obj


def download_map(cloud, obj_name: str) -> bytes:
    res = cloud.api("v2/home/get_interim_file_url_pro", {"obj_name": obj_name})
    url = (res.get("result") or {}).get("url") if isinstance(res.get("result"), dict) else None
    if not url:
        raise MapError("The Mi cloud didn't hand out the map file.")
    r = requests.get(url, timeout=30)
    if r.status_code == 404:
        raise MapError("The vacuum hasn't uploaded its map yet - let it clean once (or start “Build map”).")
    if r.status_code != 200:
        raise MapError(f"The map couldn't be downloaded (HTTP {r.status_code}).")
    return r.content


def fetch_map(cloud, did: str, model: str, spec) -> dict:
    """Downloads and reads the current map of a vacuum (blocking; run it off the UI thread)."""
    if cloud is None or not getattr(cloud, "logged_in", False):
        raise MapError("Sign in to your Mi account to see the map.")
    return decode_map(download_map(cloud, map_object(cloud, did, spec)), model, did)
