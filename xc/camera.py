"""Live video and recordings for Xiaomi cameras.

Stock Xiaomi cameras have no RTSP. Ways around that, best first:
  * WebRTC through the Mi cloud - newer cameras (2023+) offer the session they also give to Amazon Alexa:
    the Mi cloud only carries the offer/answer, the video then flows directly between the camera and this PC.
    Works for cameras at another location too. go2rtc (github.com/AlexxIT/go2rtc) is the WebRTC client;
    XiControl runs a tiny local "WHEP" endpoint that go2rtc posts its offer to (WebRtcBridge).
  * go2rtc's own Xiaomi source ("p2p"): straight from a camera on the same network.
  * HLS through the Mi cloud - older cameras offer the stream they give to Google Home (10-20 s behind).
Battery cameras and doorbells sleep; they are woken first with the command the Xiaomi Home app uses for
that model (WAKE_COMMANDS).

Recordings (motion / doorbell clips kept in the Mi cloud) are encrypted HLS playlists that the FFmpeg inside
Qt can't play as they are: the piece addresses have no file extension (refused), and on many cameras every
piece is a complete MP4 file of its own instead of a slice of one stream. XiControl therefore downloads and
decrypts the pieces itself and hands them to the player from its local endpoint, as a chain of MP4 files or
as a plain HLS playlist - whichever the recording is made of (WebRtcBridge.recording_url).
"""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
import zipfile
from urllib.parse import urljoin

import requests

from .cloud import CloudAuthError, CloudError

log = logging.getLogger(__name__)

RELEASES_API = "https://api.github.com/repos/AlexxIT/go2rtc/releases/latest"
RELEASES_PAGE = "https://github.com/AlexxIT/go2rtc/releases/latest"
HLS_SERVICE = "camera-stream-for-google-home"
WEBRTC_SERVICE = "camera-stream-for-amazon-alexa"
STREAM_SERVICES = (HLS_SERVICE, WEBRTC_SERVICE)
ASLEEP_CODE = -706010002


class CameraError(RuntimeError):
    """A camera stream could not be started, for a reason worth showing to the user as it is."""

# -- waking battery cameras, doorbells and video locks ----------------------------------------------
_W0 = ("wakeup", {"video": 1})
_W1 = ("wakeup", {"params": {"video": "1"}})
_W2 = ("wakeup_mcu", {})
_W3 = ("wakeup_host", {})
_W4 = ("wakeup_host", {"video": 1})
_W5 = ("wakeup_host", {"params": {"video": "1"}})
WAKE_COMMANDS = {
    **dict.fromkeys(("loock.cateye.v01", "loock.cateye.v02", "loock.lock.fcl112", "loock.lock.fvl109",
                     "loock.lock.pfvl10", "isa.cateye.hldb6", "yunlu.door.sd2101", "yunlu.door.sd2103",
                     "yunlu.door.sd2104"), _W0),
    **dict.fromkeys(("madv.cateye.dle3jz", "madv.cateye.dlowle", "madv.cateye.mic2jz", "madv.cateye.miowlv2",
                     "madv.cateye.miv2jz", "madv.cateye.owlg2"), _W1),
    **dict.fromkeys(("chuangmi.cateye.i023a01", "chuangmi.cateye.ipc018"), _W2),
    **dict.fromkeys(("midr.camera.bw400", "midr.camera.bw400g", "xiaomi.lock.s1", "xiaomi.lock.s2"), _W3),
    **dict.fromkeys(("loock.cateye.v06", "loock.lock.h01myk", "loock.lock.r2", "loock.lock.t1pro",
                     "loock.lock.t2pv1", "lumi.lock.wbmcn1", "madv.cateye.dlowl"), _W4),
    **dict.fromkeys(("madv.cateye.dle4jz", "madv.cateye.mi3iot", "madv.cateye.mi3sg", "madv.cateye.miowl3",
                     "midr.camera.bw300", "midr.cateye.db400a", "midr.cateye.ph300", "midr.cateye.sd400",
                     "xiaomi.lock.b04", "xiaomi.lock.b04s", "xiaomi.lock.b04sv", "xiaomi.lock.g300",
                     "xiaomi.lock.m30fa", "xiaomi.lock.m30fc", "xiaomi.lock.m30max", "xiaomi.lock.s20",
                     "xiaomi.lock.s20pro", "ynsur.lock.m1", "ynsur.lock.s11m", "ynsur.lock.s12m"), _W5),
}


def _find_prop(spec, name: str):
    for svc in getattr(spec, "services", None) or []:
        for p in svc.props:
            if p.name == name:
                return p
    return None


def wake_command(model: str, spec=None):
    """(method, params) that wakes this model, or None if it never sleeps."""
    model = (model or "").lower()
    if model in WAKE_COMMANDS:
        return WAKE_COMMANDS[model]
    if _find_prop(spec, "wakeup-host") is not None:
        return _W3
    if ".cateye." in model:
        return ("wakeup", {"video": "1"})
    return None


def is_battery_camera(model: str, spec=None) -> bool:
    return wake_command(model, spec) is not None


def wake(cloud, did: str, model: str, spec=None, timeout: float = 10.0):
    """Wakes a sleeping camera. Returns None if the model needs no wake-up, True once it reports being
    awake (or can't report), False if it did not confirm in time."""
    cmd = wake_command(model, spec)
    if cmd is None:
        return None
    method, params = cmd
    t0 = time.monotonic()
    try:
        cloud.rpc(did, method, params, timeout=8)
    except CloudAuthError:
        raise
    except CloudError as exc:  # the reply often times out although the camera did wake up
        log.debug("wake %s: %s", model, exc)
    prop = _find_prop(spec, "wakeup-host")
    if prop is None or not prop.readable:
        time.sleep(2.0)
        return True
    while time.monotonic() - t0 < timeout:
        try:
            res = cloud.miot_get(did, [(prop.siid, prop.piid)])
            if res and res[0].get("value") in (1, True):
                return True
        except CloudAuthError:
            raise
        except CloudError:
            pass
        time.sleep(0.7)
    return False


# -- Mi cloud HLS (older cameras) ---------------------------------------------------------------
def cloud_stream_action(spec):
    """(service, action) that makes the Mi cloud return an HLS address, or None."""
    for svc in getattr(spec, "services", None) or []:
        if svc.name != HLS_SERVICE:
            continue
        for a in svc.actions:
            if a.name == "start-hls-stream":
                return svc, a
    return None


def default_inputs(service, action, overrides: dict | None = None) -> list:
    """Input VALUES for a stream action, in order: the given overrides by property name, else the first
    allowed value of each parameter."""
    overrides = overrides or {}
    values = []
    for piid in action.ins:
        p = next((x for x in service.props if x.piid == piid), None)
        if p is not None and p.name in overrides:
            v = overrides[p.name]
        elif p is None:
            v = ""
        elif p.values:
            v = p.values[0].get("value")
        elif p.is_bool:
            v = False
        elif p.is_number and p.range:
            v = p.range[0]
        else:
            v = ""
        values.append(v)
    return values


def stream_url_from_result(result) -> str | None:
    """The first stream address in an action result ({'out': [values]} or {'out': [{piid, value}]})."""
    outs = result.get("out") if isinstance(result, dict) else result
    for item in outs or []:
        v = item.get("value") if isinstance(item, dict) else item
        if isinstance(v, str) and v.startswith(("http://", "https://", "rtsp://", "rtsps://")):
            return v
    return None


def wait_for_playlist(url: str, timeout: float = 25.0) -> None:
    """The cloud hands out the HLS address at once, but the playlist only exists a few seconds later,
    when the camera has started uploading. Wait for it, so the player doesn't start with an error."""
    if not url.startswith("http"):
        return
    deadline = time.monotonic() + timeout
    while True:
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200 and "#EXTM3U" in r.text:
                return
        except requests.RequestException:
            pass
        if time.monotonic() > deadline:
            raise CameraError("The camera didn't start sending video to the Mi cloud - it may be offline, "
                               "asleep or busy.")
        time.sleep(1.5)


def fetch_cloud_stream(controller, spec) -> str:
    found = cloud_stream_action(spec)
    if not found:
        raise CameraError("This camera doesn't offer a stream through the Mi cloud.")
    svc, action = found
    url = stream_url_from_result(controller.cloud_action(svc.siid, action.aiid, default_inputs(svc, action)))
    if not url:
        raise CameraError("The Mi cloud didn't return a stream address for this camera.")
    wait_for_playlist(url)
    return url


# -- WebRTC through the Mi cloud (newer cameras) ---------------------------------------------------
def webrtc_action(spec):
    """(service, action) of the Alexa-style WebRTC session (offer in, answer out), or None."""
    for svc in getattr(spec, "services", None) or []:
        if svc.name != WEBRTC_SERVICE:
            continue
        for a in svc.actions:
            if a.name == "initiate-webrtc-session" and a.out:
                names = {p.piid: p.name for p in svc.props}
                if "webrtc-offer" in (names.get(i) for i in a.ins):
                    return svc, a
    return None


_DIRECTIONS = ("a=sendonly", "a=recvonly", "a=sendrecv", "a=inactive")


def fix_answer_sdp(answer: str) -> str:
    """Xiaomi cameras answer with an empty 'a=' line where the direction attribute belongs (seen on
    BW300/BW500); go2rtc crashes on a track it can't match to a direction. Every media section gets
    exactly one valid direction - the camera only sends, so 'a=sendonly'."""
    eol = "\r\n" if "\r\n" in answer else "\n"
    out: list[str] = []
    section: list[str] = []
    has_dir = False

    def flush():
        nonlocal section, has_dir
        if section and section[0].startswith("m=") and not has_dir:
            idx = next((i for i, ln in enumerate(section) if ln.strip() == "a="), None)
            if idx is not None:
                section[idx] = "a=sendonly"
            else:
                mid = next((i for i, ln in enumerate(section) if ln.startswith("a=mid:")), len(section) - 1)
                section.insert(mid + 1, "a=sendonly")
        out.extend(ln for ln in section if ln.strip() != "a=")
        section, has_dir = [], False

    for ln in answer.splitlines():
        if ln.startswith("m="):
            flush()
        if ln.strip() in _DIRECTIONS:
            has_dir = True
        section.append(ln)
    flush()
    return eol.join(out) + eol


def _answer_from(result) -> str | None:
    for item in (result.get("out") if isinstance(result, dict) else None) or []:
        v = item.get("value") if isinstance(item, dict) else item
        if isinstance(v, str) and "v=0" in v and "m=" in v:
            return v
    return None


def start_webrtc(cloud, did: str, model: str, spec, offer: str, attempts: int = 5) -> tuple[str, str]:
    """Asks the camera (through the Mi cloud) to answer a WebRTC offer. Returns (answer SDP, session id).
    Sleeping cameras are woken first; right after waking the camera refuses for a moment, so it is asked
    a few times."""
    found = webrtc_action(spec)
    if not found:
        raise CameraError("This camera doesn't offer WebRTC through the Mi cloud.")
    svc, action = found
    session_id = str(uuid.uuid4())
    values = default_inputs(svc, action, {"session-id": session_id, "webrtc-offer": offer,
                                          "param-sleep-status": False})
    sleepy = wake(cloud, did, model, spec)
    last = "no answer"
    for attempt in range(attempts):
        try:
            result = cloud.miot_action(did, svc.siid, action.aiid, values, timeout=20)
        except CloudAuthError:
            raise
        except CloudError as exc:
            result, last = None, str(exc)
        if isinstance(result, dict):
            code = result.get("code", 0)
            answer = _answer_from(result) if code == 0 else None
            if answer:
                return fix_answer_sdp(answer), session_id
            last = result.get("description") or f"code {code}"
            if code not in (0, ASLEEP_CODE) and sleepy is None:
                break
        if attempt + 1 < attempts:
            time.sleep(1.5)
            if sleepy is not None:
                wake(cloud, did, model, spec, timeout=6)
    hint = " It may be asleep (battery camera), offline or showing video to someone else." if sleepy is not None \
        else " It may be offline or busy."
    raise CameraError(f"The camera didn't answer ({last}).{hint}")


def stop_webrtc(cloud, did: str, spec, session_id: str, timeout: int = 10) -> None:
    """Tells the camera the viewer is gone, so a battery camera can go back to sleep."""
    for svc in getattr(spec, "services", None) or []:
        if svc.name != WEBRTC_SERVICE:
            continue
        for a in svc.actions:
            if a.name == "webrtc-session-disconnected":
                try:
                    cloud.miot_action(did, svc.siid, a.aiid, [session_id], timeout=timeout)
                except CloudError as exc:
                    log.debug("ending the WebRTC session failed: %s", exc)
                return


RECORDING_KEEP_SECONDS = 3600          # a recording opened in the app can be (re)played this long
RECORDING_CACHE_BYTES = 300 * 1024 ** 2  # pieces of one recording kept in memory ahead of the player
_ATTR = re.compile('([A-Z0-9-]+)=("[^"]*"|[^,]*)')


def _attrs(line: str) -> dict[str, str]:
    """'#EXT-X-KEY:METHOD=AES-128,URI="..."' -> {'METHOD': 'AES-128', 'URI': '...'}"""
    return {k: v.strip('"') for k, v in _ATTR.findall(line.split(":", 1)[1] if ":" in line else "")}


def parse_recording(text: str, base: str) -> tuple[list[dict], str, str | None]:
    """Reads an HLS playlist. Returns
      * its pieces in order: {"url", "key": address of the AES-128 key or None, "iv": bytes, "init": bool};
      * the same playlist for the local player: pieces renamed to '0.ts', '1.ts', ... and the encryption
        lines left out (the player gets the pieces already decrypted);
      * the address of the first quality, if this playlist is only a list of qualities."""
    parts: list[dict] = []
    out: list[str] = []
    key, iv, seq, variant, variant_next = None, None, 0, None, False

    def add(link: str, init: bool) -> int:
        parts.append({"url": urljoin(base, link), "key": key, "init": init,
                      "iv": iv if iv is not None else seq.to_bytes(16, "big")})
        return len(parts) - 1

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                seq = int(line.split(":", 1)[1])
            except ValueError:
                pass
        if line.startswith(("#EXT-X-KEY", "#EXT-X-SESSION-KEY")):
            a = _attrs(line)
            method = a.get("METHOD", "NONE").upper()
            if method == "NONE":
                key, iv = None, None
            elif method == "AES-128" and a.get("URI"):
                key = urljoin(base, a["URI"])
                try:
                    iv = bytes.fromhex(a["IV"][2:].zfill(32)) if a.get("IV") else None
                except ValueError:
                    raise CameraError("This recording's playlist is damaged (unreadable IV)") from None
            else:
                raise CameraError(f"This recording is protected in a way XiControl can't play ({method})")
        elif line.startswith("#EXT-X-MAP"):
            out.append(f'#EXT-X-MAP:URI="{add(_attrs(line).get("URI", ""), True)}.mp4"')
        elif line.startswith("#EXT-X-BYTERANGE"):
            raise CameraError("This recording is stored in a way XiControl can't play (byte ranges)")
        elif line.startswith("#"):
            variant_next = line.startswith("#EXT-X-STREAM-INF")
            out.append(line)
        elif variant_next:
            variant = variant or urljoin(base, line)
            variant_next = False
        else:
            out.append(f"{add(line, False)}.ts")
            seq += 1
    return parts, "\n".join(out) + "\n", variant


def decrypt_piece(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC with the usual padding, as HLS uses it for whole pieces."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if len(key) != 16 or len(iv) != 16 or len(data) % 16:
        raise CameraError("This recording can't be decrypted (the key doesn't fit)")
    d = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = d.update(data) + d.finalize()
    pad = plain[-1] if plain else 0
    return plain[:-pad] if 1 <= pad <= 16 and plain.endswith(bytes([pad]) * pad) else plain


def piece_kind(data: bytes) -> str:
    """'mp4' = a complete MP4 file, 'fmp4' = a fragment that needs an init section, 'ts' = MPEG-TS,
    '' = something else."""
    if data[:1] == b"G" and (len(data) < 189 or data[188:189] == b"G"):
        return "ts"
    pos, first = 0, True
    while pos + 8 <= len(data):
        size, box = int.from_bytes(data[pos:pos + 4], "big"), data[pos + 4:pos + 8]
        if first and box not in (b"ftyp", b"styp", b"moov", b"moof", b"sidx", b"free", b"skip", b"wide", b"mdat"):
            return ""
        first = False
        if box == b"moov":
            return "mp4"
        if box in (b"styp", b"moof", b"sidx"):
            return "fmp4"
        if size == 1 and pos + 16 <= len(data):
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
        if size < 8:
            break
        pos += size
    return "fmp4" if not first else ""


class WebRtcBridge:
    """The app's local HTTP endpoint for video (127.0.0.1 only; every address contains a random secret):
      * WHEP - go2rtc posts its WebRTC offer here, the answer comes from the camera through the Mi cloud;
      * recordings - the player gets a recording from here, already downloaded and decrypted."""

    def __init__(self, resolve):
        """resolve(did) -> (cloud, model, spec); raises with a readable message if that isn't possible."""
        self.resolve = resolve
        self.secret = secrets.token_urlsafe(12)
        self.sessions: dict[str, str] = {}     # did -> session id of the running stream
        self.errors: dict[str, str] = {}       # did -> why the last attempt failed
        self._recordings: dict[str, dict] = {}  # id -> the pieces of one recording (see recording_url)
        self._rec_lock = threading.Lock()
        bridge = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):  # no console output
                pass

            def do_POST(self):
                try:  # take the whole request first, so an early answer never resets the connection
                    length = min(int(self.headers.get("Content-Length") or 0), 200_000)
                    offer = self.rfile.read(length).decode("utf-8", "replace")
                except (ValueError, OSError):
                    self.send_error(400)
                    return
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) != 3 or parts[0] != "whep" or parts[1] != bridge.secret:
                    self.send_error(404)
                    return
                did = parts[2]
                try:
                    answer = bridge.answer(did, offer)
                except Exception as exc:  # noqa: BLE001 - reported to the viewer through bridge.errors
                    bridge.errors[did] = str(exc) or exc.__class__.__name__
                    log.info("WebRTC for camera %s failed: %s", did, exc.__class__.__name__)
                    self.send_error(502)
                    return
                body = answer.encode("utf-8")
                self.send_response(201)
                self.send_header("Content-Type", "application/sdp")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                parts = self.path.split("?")[0].strip("/").split("/")
                if len(parts) != 4 or parts[0] != "rec" or parts[1] != bridge.secret:
                    self.send_error(404)
                    return
                try:
                    data, ctype = bridge.recording_file(parts[2], parts[3])
                except (KeyError, IndexError, ValueError):
                    self.send_error(404)
                    return
                except Exception as exc:  # noqa: BLE001 - the player then shows "the stream stopped"
                    log.info("recording piece %s failed: %s: %s", parts[3], exc.__class__.__name__, exc)
                    self.send_error(502)
                    return
                start, end, status = 0, len(data) - 1, 200
                rng = (self.headers.get("Range") or "").strip()
                if rng.startswith("bytes=") and "," not in rng and data:
                    first, _, last = rng[6:].partition("-")
                    try:
                        if first:
                            start, end = int(first), min(int(last), end) if last else end
                        elif last:
                            start = max(0, len(data) - int(last))
                        status = 206
                    except ValueError:
                        start, end, status = 0, len(data) - 1, 200
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{len(data)}")
                        self.end_headers()
                        return
                body = data[start:end + 1]
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Cache-Control", "no-store")
                    if status == 206:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass  # the player was closed in the middle of a download

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, name="webrtc-bridge", daemon=True).start()

    def url(self, did: str) -> str:
        return f"http://127.0.0.1:{self.port}/whep/{self.secret}/{did}"

    def answer(self, did: str, offer: str) -> str:
        cloud, model, spec = self.resolve(did)
        self.errors.pop(did, None)
        old = self.sessions.pop(did, None)
        if old:
            stop_webrtc(cloud, did, spec, old)
        answer, session_id = start_webrtc(cloud, did, model, spec, offer)
        self.sessions[did] = session_id
        return answer

    def close_session(self, did: str, timeout: int = 10) -> None:
        session_id = self.sessions.pop(str(did), None)
        if not session_id:
            return
        try:
            cloud, _model, spec = self.resolve(did)
        except Exception:  # noqa: BLE001
            return
        stop_webrtc(cloud, did, spec, session_id, timeout)

    # -- recordings ---------------------------------------------------------------------------------
    @staticmethod
    def _get(url: str, timeout: int = 30) -> requests.Response:
        r = requests.get(url, timeout=timeout)
        if r.status_code in (401, 403):
            raise CameraError("The Mi account session has expired - sign in again to play recordings")
        if r.status_code != 200:
            raise CameraError(f"The Mi cloud didn't hand out this recording (HTTP {r.status_code}) - it may "
                              "have been deleted")
        return r

    def recording_url(self, fetch) -> str:
        """Prepares one recording and returns the local address the player can open.
        fetch() -> the recording's (signed) playlist address at the Mi cloud. Blocks while the playlist and
        the first piece are loaded; the other pieces follow in the background, ahead of the player."""
        url = fetch()
        r = self._get(url, 20)
        if "#EXTM3U" not in r.text[:200]:
            # e.g. battery cameras without cloud storage: the event is listed, its video stays on the camera
            raise CameraError("The Mi cloud has no video for this event. Cameras without cloud storage keep "
                              "their recordings on the camera itself - watch those in the Xiaomi Home app.")
        parts, playlist, variant = parse_recording(r.text, url)
        if variant and not parts:  # only a list of qualities: take the first one
            r = self._get(variant, 20)
            parts, playlist, _v = parse_recording(r.text, variant)
        media = [i for i, p in enumerate(parts) if not p["init"]]
        if not media:
            raise CameraError("This recording is empty")
        rid = secrets.token_urlsafe(9)
        rec = {"parts": parts, "playlist": playlist, "concat": "", "kind": "", "data": {}, "keys": {},
               "lock": threading.Lock(), "at": time.monotonic()}
        with self._rec_lock:
            # one recording's pieces at a time are kept in memory; old entries go after a while
            for old_rid, old in list(self._recordings.items()):
                old["data"] = {}
                if rec["at"] - old["at"] > RECORDING_KEEP_SECONDS:
                    del self._recordings[old_rid]
            self._recordings[rid] = rec
        kind = piece_kind(self._piece(rid, media[0]))
        rec["kind"] = kind
        base = f"http://127.0.0.1:{self.port}/rec/{self.secret}/{rid}/"
        threading.Thread(target=self._prefetch, args=(rid,), name="recording", daemon=True).start()
        if kind == "mp4" and len(media) == len(parts):
            # every piece is a film of its own: the player gets them as a chain, not as one stream
            rec["concat"] = "ffconcat version 1.0\n" + "".join(f"file '{i}.mp4'\n" for i in media)
            return base + "index.ffconcat"
        return base + "index.m3u8"

    def _piece(self, rid: str, n: int) -> bytes:
        """One piece of a recording, decrypted. Downloaded once; a second asker waits for the first."""
        rec = self._recordings[rid]
        part = rec["parts"][n]
        data = rec["data"].get(n)
        if data is not None:  # already here: don't wait while the next piece is being downloaded
            return data
        with rec["lock"]:
            data = rec["data"].get(n)
            if data is None:
                data = self._get(part["url"], 60).content
                if part["key"]:
                    key = rec["keys"].get(part["key"])
                    if key is None:
                        key = rec["keys"][part["key"]] = self._get(part["key"], 20).content
                    data = decrypt_piece(data, key, part["iv"])
                if self._recordings.get(rid) is rec:
                    rec["data"][n] = data
            return data

    def _prefetch(self, rid: str) -> None:
        try:
            rec = self._recordings[rid]
            for n in range(len(rec["parts"])):
                newest = max(self._recordings.values(), key=lambda x: x["at"], default=None)
                if newest is not rec or sum(len(d) for d in rec["data"].values()) > RECORDING_CACHE_BYTES:
                    return
                self._piece(rid, n)
        except Exception as exc:  # noqa: BLE001 - the player's own request for that piece reports it
            log.debug("loading a recording ahead failed: %s", exc)

    def recording_file(self, rid: str, name: str) -> tuple[bytes, str]:
        """What the player asked for -> (content, content type). KeyError/IndexError/ValueError = no such file."""
        rec = self._recordings[rid]
        if name == "index.m3u8":
            return rec["playlist"].encode("utf-8"), "application/vnd.apple.mpegurl"
        if name == "index.ffconcat":
            return rec["concat"].encode("utf-8"), "text/plain"
        number, _, ext = name.partition(".")
        if not number.isdigit():
            raise KeyError(name)
        return self._piece(rid, int(number)), "video/mp4" if ext == "mp4" else "video/mp2t"

    def stop(self):
        try:
            self._server.shutdown()
            self._server.server_close()
        except OSError:
            pass


# -- which ways exist for a camera -----------------------------------------------------------------
def supports_go2rtc(model: str) -> bool:
    m = (model or "").lower()
    return any(x in m for x in (".camera.", ".cateye.", ".feeder.", ".dbell."))


def live_options(info: dict, spec, on_lan: bool) -> list[str]:
    """Ways to watch this camera live, best first: 'rtsp', 'webrtc', 'p2p', 'hls'."""
    out = []
    if info.get("rtsp_url"):
        out.append("rtsp")
    if webrtc_action(spec):
        out.append("webrtc")
    if on_lan and info.get("ip") and supports_go2rtc(info.get("model")):
        out.append("p2p")
    if cloud_stream_action(spec):
        out.append("hls")
    return out


# -- recorded events -------------------------------------------------------------------------------
EVENT_NAMES = {
    "ObjectMotion": "Motion", "PeopleMotion": "Person", "Face": "Face", "KnownFace": "Known face",
    "BabyCry": "Baby crying", "Pet": "Pet", "AI": "AI event", "Pass": "Someone passed by",
    "Stay": "Someone at the door", "Bell": "Doorbell", "Calling": "Doorbell", "LouderSound": "Loud sound",
    "PeopleCough": "Cough", "CameraCalling": "Call", "Vehicle": "Vehicle", "Package": "Package",
    "Default": "Event",
}


def event_name(event_type: str) -> str:
    if event_type in EVENT_NAMES:
        return EVENT_NAMES[event_type]
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", event_type or "Event")
    return words[:1].upper() + words[1:].lower()


def list_events(cloud, did: str, model: str, hours: int = 72, limit: int = 30) -> list[dict]:
    """Recorded events of the last hours, newest first: [{'time', 'name', 'duration', 'file_id', 'is_alarm'}]."""
    now = int(time.time() * 1000)
    events = []
    for u in cloud.camera_events(did, model, now - hours * 3600_000, now, limit):
        if not u.get("fileId"):
            continue
        try:
            duration = float(u.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        events.append({"time": (u.get("createTime") or 0) / 1000, "name": event_name(str(u.get("eventType") or "")),
                       "duration": duration, "file_id": u["fileId"], "is_alarm": bool(u.get("isAlarm", True))})
    return events


# -- go2rtc --------------------------------------------------------------------------------------
def _asset_name() -> str:
    machine = platform.machine().lower()
    if sys.platform != "win32":
        return "go2rtc_linux_arm64" if "arm" in machine or "aarch" in machine else "go2rtc_linux_amd64"
    if "arm" in machine:
        return "go2rtc_win_arm64.zip"
    return "go2rtc_win64.zip" if sys.maxsize > 2 ** 32 or machine.endswith("64") else "go2rtc_win32.zip"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _safe(did: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(did))


def stream_name(did: str, kind: str = "p2p") -> str:
    return ("rtc_" if kind == "webrtc" else "cam_") + _safe(did)


class Go2rtc:
    """Finds, downloads, configures and runs go2rtc for the cameras of the signed-in account."""

    EXE = "go2rtc.exe" if sys.platform == "win32" else "go2rtc"

    def __init__(self, folder: str, bridge: WebRtcBridge | None = None):
        self.folder = folder          # %APPDATA%\XiControl\go2rtc
        self.bridge = bridge
        self.proc: subprocess.Popen | None = None
        self.rtsp_port = 0
        self.api_port = 0
        self._config_key = None
        self._lock = threading.Lock()

    # -- installation -----------------------------------------------------------------
    def find(self) -> str | None:
        here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(sys.argv[0]))
        for path in (os.path.join(self.folder, self.EXE), os.path.join(here, self.EXE), shutil.which("go2rtc")):
            if path and os.path.isfile(path):
                return path
        return None

    def download(self) -> str:
        """Downloads the latest go2rtc release from GitHub and checks its SHA-256 before use."""
        name = _asset_name()
        try:
            r = requests.get(RELEASES_API, timeout=20, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            release = r.json()
            asset = next((a for a in release.get("assets") or [] if a.get("name") == name), None)
            if not asset:
                raise CameraError(f"go2rtc {release.get('tag_name', '')} has no {name} download")
            resp = requests.get(asset["browser_download_url"], timeout=120)
            resp.raise_for_status()
            data = resp.content
        except (requests.RequestException, ValueError) as exc:
            raise CameraError(f"GitHub could not be reached ({exc.__class__.__name__}) - check the internet "
                               "connection") from exc
        digest = (asset.get("digest") or "").removeprefix("sha256:").lower()
        if not digest:
            raise CameraError("GitHub did not publish a checksum for this go2rtc download - install it manually")
        if hashlib.sha256(data).hexdigest() != digest:
            raise CameraError("The go2rtc download is damaged (checksum mismatch) - try again")
        os.makedirs(self.folder, exist_ok=True)
        target = os.path.join(self.folder, self.EXE)
        if name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                member = next((n for n in z.namelist() if os.path.basename(n).lower() == self.EXE), None)
                if not member:
                    raise CameraError("go2rtc.exe is missing from the download")
                with z.open(member) as src, open(target + ".tmp", "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with open(target + ".tmp", "wb") as dst:
                dst.write(data)
            os.chmod(target + ".tmp", 0o755)
        self.stop()
        os.replace(target + ".tmp", target)
        log.info("go2rtc %s installed (%s)", release.get("tag_name"), name)
        return target

    # -- running --------------------------------------------------------------------------
    @staticmethod
    def build_config(user_id: str, pass_token: str, region: str, cameras: list[dict],
                     rtsp_port: int, api_port: int, bridge: WebRtcBridge | None = None) -> dict:
        """cameras: [{'did', 'ip', 'model'}]. Every camera gets both kinds of stream; go2rtc only opens
        one when a viewer asks for it (the bridge then says whether the camera can do WebRTC)."""
        streams = {}
        for c in cameras:
            if c.get("ip") and supports_go2rtc(c.get("model")):
                streams[stream_name(c["did"])] = (f"xiaomi://{user_id}:{region}@{c['ip']}"
                                                  f"?did={c['did']}&model={c['model']}")
            if bridge is not None:
                streams[stream_name(c["did"], "webrtc")] = "webrtc:" + bridge.url(c["did"])
        return {
            "api": {"listen": f"127.0.0.1:{api_port}"},
            "rtsp": {"listen": f"127.0.0.1:{rtsp_port}"},
            "webrtc": {"listen": "127.0.0.1:0"},
            "log": {"format": "text", "level": "info"},
            "xiaomi": {str(user_id): pass_token},
            "streams": streams,
        }

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stream_url(self, cloud, cameras: list[dict], did: str, kind: str = "p2p") -> str:
        """Starts (or restarts, if the cameras changed) go2rtc and returns the camera's RTSP address.
        kind: 'p2p' (go2rtc talks to the camera on the LAN) or 'webrtc' (through the Mi cloud bridge)."""
        exe = self.find()
        if not exe:
            raise FileNotFoundError("go2rtc is not installed")
        if not (cloud and cloud.can_renew):
            raise PermissionError("Sign in to your Mi account again to enable live video "
                                  "(XiControl needs the long-lived sign-in key for go2rtc).")
        cam = next((c for c in cameras if str(c.get("did")) == str(did)), None)
        if cam is None:
            raise CameraError("This camera is not in the device list any more.")
        if kind == "p2p" and not cam.get("ip"):
            raise CameraError("The camera's IP address is unknown - sync devices from the Mi account.")
        if kind == "webrtc" and self.bridge is None:
            raise CameraError("This camera doesn't offer WebRTC through the Mi cloud.")
        with self._lock:
            key = (exe, cloud.user_id, cloud.pass_token, cloud.server,
                   tuple(sorted((str(c["did"]), c.get("ip"), c.get("model")) for c in cameras)))
            if not self.running() or key != self._config_key:
                self._start(exe, cloud, cameras, key)
        return f"rtsp://127.0.0.1:{self.rtsp_port}/{stream_name(did, kind)}"

    def _start(self, exe, cloud, cameras, key):
        self.stop()
        self.rtsp_port, self.api_port = _free_port(), _free_port()
        config = self.build_config(cloud.user_id, cloud.pass_token, cloud.server, cameras, self.rtsp_port,
                                   self.api_port, self.bridge)
        os.makedirs(self.folder, exist_ok=True)
        logf = open(os.path.join(self.folder, "go2rtc.log"), "ab")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        # the config (with the sign-in key) is passed on the command line, never written to disk
        self.proc = subprocess.Popen([exe, "-config", json.dumps(config)], cwd=self.folder, stdout=logf,
                                     stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
        logf.close()
        self._config_key = key
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise CameraError(f"go2rtc stopped right after starting - see {self.folder}\\go2rtc.log")
            try:
                with socket.create_connection(("127.0.0.1", self.rtsp_port), timeout=0.3):
                    log.info("go2rtc running (rtsp 127.0.0.1:%s, %d streams)", self.rtsp_port,
                             len(config["streams"]))
                    return
            except OSError:
                time.sleep(0.2)
        raise CameraError("go2rtc did not start in time")

    def stop(self):
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None
            self._config_key = None
