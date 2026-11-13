"""Tests for: camera paths (wake-up, WebRTC bridge, HLS, recordings), LAN/cloud choice by network,
reading all cloud devices in one request, managing devices on the Mi account, and QR pairing."""
import atexit
import base64
import ipaddress
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from tests import fake_device as fake  # noqa: E402
from tests import fake_home  # noqa: E402
from tests.test_fixes import CAM_SPEC, TOKEN, _session  # noqa: E402
from xc import camera, miio, pairing  # noqa: E402
from xc.alerts import Alerts, summarize  # noqa: E402
from xc.cloud import CloudError, XiaomiCloud  # noqa: E402
from xc.device import DeviceController, plain_values  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402
from xc.summary import LegacySummary, Summary  # noqa: E402

app = QApplication.instance() or QApplication([])


def free_windows():
    """Deletes every window that is still around. A dialog without a parent that outlives the test can
    crash Qt's own clean-up when Python exits (seen as a random 'Aborted' after all tests passed)."""
    for w in QApplication.topLevelWidgets():
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


atexit.register(free_windows)


def pump(done, seconds=4.0):
    """Runs the Qt event loop until done() is true (worker results arrive through it)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return done()


def _urn(kind, name):
    return f"urn:miot-spec-v2:{kind}:{name}:0000:x:1"


# a battery camera of the newer kind: WebRTC session for Alexa + a wake-up flag
WEBRTC_SPEC = {"type": _urn("device", "camera"), "services": [
    {"iid": 2, "type": _urn("service", "camera-control"), "description": "Camera Control", "properties": [
        {"iid": 1, "type": _urn("property", "on"), "description": "Switch", "format": "bool",
         "access": ["read", "write"]}]},
    {"iid": 10, "type": _urn("service", "camera-stream-for-amazon-alexa"), "description": "Alexa", "properties": [
        {"iid": 2, "type": _urn("property", "video-attribute"), "description": "", "format": "uint8", "access": [],
         "value-list": [{"value": 1, "description": "1920_1080_30"}, {"value": 2, "description": "1280_720_30"}]},
        {"iid": 6, "type": _urn("property", "stream-address"), "description": "", "format": "string", "access": []},
        {"iid": 10, "type": _urn("property", "session-id"), "description": "", "format": "string", "access": []},
        {"iid": 11, "type": _urn("property", "webrtc-offer"), "description": "", "format": "string", "access": []},
        {"iid": 12, "type": _urn("property", "param-sleep-status"), "description": "", "format": "bool",
         "access": []}],
     "actions": [
        {"iid": 1, "type": _urn("action", "initiate-webrtc-session"), "description": "", "in": [2, 10, 11, 12],
         "out": [6]},
        {"iid": 3, "type": _urn("action", "webrtc-session-disconnected"), "description": "", "in": [10], "out": []}]},
    {"iid": 11, "type": _urn("service", "camera-stream-for-google-home"), "description": "Google", "properties": [],
     "actions": [{"iid": 1, "type": _urn("action", "initiate-webrtc-session"), "description": "", "in": [],
                  "out": []}]},
    {"iid": 12, "type": _urn("service", "banya-ipc"), "description": "", "properties": [
        {"iid": 13, "type": _urn("property", "wakeup-host"), "description": "", "format": "uint8",
         "access": ["read", "notify"]}]},
]}
ANSWER = ("v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1\r\n"
          "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=mid:0\r\na=sendonly\r\na=rtpmap:96 H264/90000\r\n"
          "m=audio 9 UDP/TLS/RTP/SAVPF 101\r\na=mid:1\r\na=\r\na=rtpmap:101 opus/48000/2\r\n")


class no_sleep:
    """Makes time.sleep instant inside a with-block (the camera code waits between attempts)."""

    def __enter__(self):
        self.real, time.sleep = time.sleep, lambda _s: None

    def __exit__(self, *exc):
        time.sleep = self.real


# -- LAN or cloud ------------------------------------------------------------------------------------
def test_cloud_actions_send_plain_values_lan_keeps_piids():
    c = XiaomiCloud("de", _session())
    calls = []

    def api(path, data, **kw):
        calls.append((path, data))
        return {"code": 0, "result": {"code": 0, "out": ["x"]}}
    c.api = api
    ctl = DeviceController({"did": "blt.9", "model": "m"}, c)
    ctl.action(4, 1, [{"piid": 1, "value": 30}, {"piid": 2, "value": "a"}])
    assert calls[-1] == ("miotspec/action", {"params": {"did": "blt.9", "siid": 4, "aiid": 1, "in": [30, "a"]}})
    assert plain_values([5, {"piid": 1, "value": 6}]) == [5, 6] and plain_values(None) == []
    sent = []

    class Lan:
        def miot_action(self, did, siid, aiid, ins):
            sent.append(ins)
            return {"code": 0}
    lan = DeviceController({"did": "9", "ip": "127.0.0.1", "token": TOKEN}, None)
    lan._local_dev = lambda: Lan()
    lan.action(4, 1, [{"piid": 1, "value": 30}])
    assert sent == [[{"piid": 1, "value": 30}]]


def test_lan_is_only_tried_on_this_pcs_networks():
    old = DeviceController.local_networks
    DeviceController.local_networks = [ipaddress.ip_network("192.168.240.0/24")]
    try:
        c = XiaomiCloud("de", _session())
        here = DeviceController({"did": "1", "ip": "192.168.240.7", "token": TOKEN}, c)
        away = DeviceController({"did": "2", "ip": "192.168.31.7", "token": TOKEN}, c)
        public = DeviceController({"did": "3", "ip": "178.148.1.2", "token": TOKEN}, c)
        forced = DeviceController({"did": "4", "ip": "192.168.31.8", "token": TOKEN, "mode": "local"}, c)
        assert here._order() == ["local", "cloud"] and not here.cloud_only() and not here.prefers_cloud()
        assert away._order() == ["cloud"] and away.cloud_only() and away.elsewhere() and away.prefers_cloud()
        assert public._order() == ["cloud"] and not public.on_local_network()
        assert forced._order() == ["local"]                       # the user's explicit choice (VPN)
        alone = DeviceController({"did": "5", "ip": "192.168.31.9", "token": TOKEN}, None)
        assert "another network" in alone.unreachable_reason() and alone.needs_sign_in()
        DeviceController.local_networks = None                    # unknown: any private address is tried
        assert away._order() == ["local", "cloud"] and public._order() == ["cloud"]
    finally:
        DeviceController.local_networks = old


def test_lan_retry_backs_off():
    c = XiaomiCloud("de", _session())
    c.api = lambda path, data, **kw: {"code": 0, "result": [{**p, "code": 0, "value": 1} for p in data["params"]]}
    ctl = DeviceController({"did": "9", "ip": "127.0.0.1", "token": TOKEN, "mode": "auto"}, c)

    class DeadLan:
        def miot_get(self, did, pairs):
            raise miio.MiioTimeout("no reply")
    ctl._local_dev = lambda: DeadLan()
    waits = []
    for _ in range(6):
        ctl._local_retry_at = 0
        ctl.get_props([(2, 1)])
        waits.append(round(ctl._local_retry_at - time.monotonic()))
    assert waits == [300, 600, 1200, 2400, 3600, 3600]
    # asking the device who it is counts too: a silent LAN address puts the cloud first, an answer resets that
    ctl._local_retry_at, ctl._local_fails = 0, 0

    class Silent:
        def send(self, method, params=None):
            raise miio.MiioTimeout("no reply")
    ctl._local_dev = lambda: Silent()
    try:
        ctl.info_local()
        raise AssertionError("expected a time-out")
    except miio.MiioError:
        pass
    assert ctl.prefers_cloud() and ctl._order() == ["cloud", "local"]

    class Alive:
        def send(self, method, params=None):
            return {"model": "x"}
    ctl._local_dev = lambda: Alive()
    assert ctl.info_local() == {"model": "x"} and not ctl.prefers_cloud()


# -- Mi account: rename, delete, pairing key ------------------------------------------------------------
def test_account_requests():
    c = XiaomiCloud("de", _session())
    calls = []

    def api(path, data, **kw):
        calls.append((path, data))
        if path == "user/del_owner_device_batch":
            return {"code": 0, "result": {"123": 0}}
        if path == "v2/user/get_bindkey":
            return {"code": 0, "result": {"ret": 0, "bindkey": "KEY1"}}
        if path == "v2/user/check_bindkey":
            return {"code": 0, "result": {"ret": "1", "bind_did": "777"}}
        return {"code": 0, "result": {}}
    c.api = api
    c.rename_device("123", "Lampa")
    assert calls[-1] == ("device/rename", {"app_version": 1, "newName": "Lampa", "did": "123", "is_group": False})
    assert c.delete_devices([{"did": 123, "pid": "0"}]) == {"123": 0}
    assert calls[-1] == ("user/del_owner_device_batch", {"devList": [{"did": "123", "pid": 0}]})
    assert c.delete_devices([]) == {}
    assert c.get_bindkey() == "KEY1" and c.check_bindkey("KEY1") == {"ret": 1, "bind_did": "777"}


def test_unbind_checks_owner_and_that_the_device_is_gone():
    c = XiaomiCloud("de", _session())      # user 42
    state = {"list": [{"did": "123", "pid": 6, "uid": 42}, {"did": "555", "pid": 0, "uid": 99}], "really": False}
    deleted = []

    def api(path, data, **kw):
        if path == "home/device_list":
            return {"code": 0, "result": {"list": list(state["list"])}}
        if path == "user/del_owner_device_batch":
            deleted.append(data)
            did = data["devList"][0]["did"]
            if state["really"]:
                state["list"] = [d for d in state["list"] if d["did"] != did]
            return {"code": 0, "result": {did: 0}}
        raise AssertionError(path)
    c.api = api
    assert c.unbind_device("999") is False and deleted == []       # not on the account: nothing sent
    for did, words in (("555", "another Mi account"), ("123", "still on the account")):
        try:
            c.unbind_device(did)
            raise AssertionError("expected CloudError")
        except CloudError as exc:
            assert words in str(exc)
    assert [d["devList"][0]["did"] for d in deleted] == ["123"]    # the shared device was never touched
    state["really"] = True
    assert c.unbind_device("123") is True and deleted[-1] == {"devList": [{"did": "123", "pid": 6}]}


# -- cameras -----------------------------------------------------------------------------------------
def test_recordings_use_a_signed_get():
    c = XiaomiCloud("de", _session())
    seen = {}

    class R:
        status_code = 200
        text = ('{"code":0,"data":{"thirdPartPlayUnits":[{"fileId":"F1","createTime":1700000000000,'
                '"eventType":"ObjectMotion","duration":9.1,"isAlarm":true},{"createTime":1}]}}')

    def fake_get(url, params, headers, cookies, timeout):
        seen.update(url=url, params=params)
        return R()
    c.api_http.get = fake_get
    events = camera.list_events(c, "5", "isa.camera.hlc8a")
    assert seen["url"] == "https://de.business.smartcamera.api.io.mi.com/common/app/get/eventlist"
    assert set(seen["params"]) == {"data", "rc4_hash__", "signature", "ssecurity", "_nonce"}
    assert "did" not in seen["params"]["data"]                      # encrypted
    assert events == [{"time": 1700000000.0, "name": "Motion", "duration": 9.1, "file_id": "F1", "is_alarm": True}]
    url = c.camera_event_url("5", "isa.camera.hlc8a", "F1")
    assert url.startswith("https://de.business.smartcamera.api.io.mi.com/common/app/m3u8?")
    assert "yetAnotherServiceToken=tok" in url
    assert "%7B" not in url and "%22" not in url and "fileId" not in url      # the request data is encrypted
    assert camera.event_name("PeopleMotion") == "Person" and camera.event_name("SomethingNew") == "Something new"


def _encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad = 16 - len(data) % 16
    e = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return e.update(data + bytes([pad]) * pad) + e.finalize()


def _box(name: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + name + payload


def test_recording_playlists_are_read_and_pieces_decrypted():
    base = "https://cam.example/app/m3u8?data=x"
    text = "\n".join([
        "#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-TARGETDURATION:35", "#EXT-X-MEDIA-SEQUENCE:7",
        '#EXT-X-KEY:METHOD=AES-128,URI="https://cam.example/key?did=1&keyInfo=a,b",IV=0x000102030405060708090a0b0c0d0e0f',
        "#EXTINF:2.998", "https://cam.example/seg?did=1&stoId=A", "#EXT-X-DISCONTINUITY",
        "#EXTINF:3.002", "seg?did=1&stoId=B", "#EXT-X-KEY:METHOD=NONE", "#EXTINF:1.0", "plain", "#EXT-X-ENDLIST"])
    parts, local, variant = camera.parse_recording(text, base)
    assert variant is None and [p["url"] for p in parts] == [
        "https://cam.example/seg?did=1&stoId=A", "https://cam.example/app/seg?did=1&stoId=B",
        "https://cam.example/app/plain"]
    assert parts[0]["key"] == "https://cam.example/key?did=1&keyInfo=a,b" and parts[0]["iv"] == bytes(range(16))
    assert parts[1]["key"] == parts[0]["key"] and parts[2]["key"] is None
    # the local player gets short names with an extension and no encryption lines
    lines = local.splitlines()
    assert [x for x in lines if not x.startswith("#")] == ["0.ts", "1.ts", "2.ts"]
    assert not any("KEY" in x for x in lines) and "#EXT-X-DISCONTINUITY" in lines and lines[-1] == "#EXT-X-ENDLIST"
    # without an IV the piece's number in the playlist is the IV (HLS rule)
    parts, _local, _v = camera.parse_recording(
        '#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:7\n#EXT-X-KEY:METHOD=AES-128,URI="k"\n#EXTINF:1,\na\n#EXTINF:1,\nb\n', base)
    assert [int.from_bytes(p["iv"], "big") for p in parts] == [7, 8]
    # a list of qualities points to the real playlist; fMP4 keeps its init section as a piece
    parts, _local, variant = camera.parse_recording("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhd/list.m3u8\n", base)
    assert parts == [] and variant == "https://cam.example/app/hd/list.m3u8"
    parts, local, _v = camera.parse_recording('#EXTM3U\n#EXT-X-MAP:URI="init"\n#EXTINF:1,\nfrag\n', base)
    assert [p["init"] for p in parts] == [True, False] and '#EXT-X-MAP:URI="0.mp4"' in local and "1.ts" in local
    for bad in ('#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="k"\n#EXTINF:1,\na\n',
                "#EXTM3U\n#EXTINF:1,\n#EXT-X-BYTERANGE:100@0\na\n"):
        try:
            camera.parse_recording(bad, base)
            raise AssertionError("expected CameraError")
        except camera.CameraError:
            pass
    key, iv = bytes(range(16, 32)), bytes(range(16))
    for size in (0, 1, 15, 16, 17, 1000):
        data = os.urandom(size)
        assert camera.decrypt_piece(_encrypt(data, key, iv), key, iv) == data
    try:
        camera.decrypt_piece(b"x" * 15, key, iv)
        raise AssertionError("expected CameraError")
    except camera.CameraError:
        pass
    film = _box(b"ftyp", b"isom") + _box(b"free") + _box(b"mdat", b"x" * 40) + _box(b"moov", b"y" * 20)
    assert camera.piece_kind(film) == "mp4"
    assert camera.piece_kind(_box(b"styp", b"msdh") + _box(b"moof", b"z" * 16) + _box(b"mdat", b"x")) == "fmp4"
    assert camera.piece_kind(b"G" + bytes(187) + b"G" + bytes(187)) == "ts"
    assert camera.piece_kind(b"\x8f\x10 not video at all") == "" and camera.piece_kind(b"") == ""


def test_recordings_play_from_the_local_endpoint():
    """A recording as the Mi cloud hands it out - encrypted, addresses without a file extension, every
    piece an MP4 file of its own - reaches the player as a chain of plain .mp4 files."""
    import http.server
    import threading
    key, iv = os.urandom(16), os.urandom(16)
    films = [_box(b"ftyp", b"isom") + _box(b"moov", bytes([n]) * 300) + _box(b"mdat", os.urandom(5000))
             for n in range(3)]
    ts = [(b"G" + os.urandom(187)) * 20 for _ in range(2)]
    asked = []

    class Cloud(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            asked.append(self.path)
            me = f"http://127.0.0.1:{self.server.server_address[1]}"
            kind, _, n = self.path.strip("/").partition("?n=")
            if kind in ("list", "tslist"):
                pieces = films if kind == "list" else ts
                name = "seg" if kind == "list" else "tsseg"
                rows = ["#EXTM3U", "#EXT-X-TARGETDURATION:35",
                        f'#EXT-X-KEY:METHOD=AES-128,URI="{me}/key?n=1",IV=0x{iv.hex()}']
                for i in range(len(pieces)):
                    rows += ["#EXTINF:3.0", f"{me}/{name}?n={i}", "#EXT-X-DISCONTINUITY"]
                body = "\n".join(rows + ["#EXT-X-ENDLIST"]).encode()
            elif kind == "key":
                body = key
            elif kind in ("seg", "tsseg"):
                body = _encrypt((films if kind == "seg" else ts)[int(n)], key, iv)
            elif kind == "expired":
                self.send_error(401)
                return
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/MP2T")       # what the real cloud says, whatever it sends
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Cloud)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    remote = f"http://127.0.0.1:{server.server_address[1]}"
    bridge = camera.WebRtcBridge(lambda did: None)
    try:
        url = bridge.recording_url(lambda: remote + "/list")
        assert url.startswith(f"http://127.0.0.1:{bridge.port}/rec/{bridge.secret}/") and url.endswith("/index.ffconcat")
        folder = url.rsplit("/", 1)[0]
        r = requests.get(url, timeout=5)
        assert r.text == "ffconcat version 1.0\nfile '0.mp4'\nfile '1.mp4'\nfile '2.mp4'\n"
        for n, film in enumerate(films):
            r = requests.get(f"{folder}/{n}.mp4", timeout=5)
            assert r.status_code == 200 and r.content == film and r.headers["Content-Type"] == "video/mp4"
        # the player may jump around inside a file
        r = requests.get(f"{folder}/1.mp4", headers={"Range": "bytes=10-19"}, timeout=5)
        assert r.status_code == 206 and r.content == films[1][10:20]
        assert r.headers["Content-Range"] == f"bytes 10-19/{len(films[1])}"
        r = requests.get(f"{folder}/1.mp4", headers={"Range": "bytes=-5"}, timeout=5)
        assert r.status_code == 206 and r.content == films[1][-5:]
        assert requests.get(f"{folder}/1.mp4", headers={"Range": "bytes=999999-"}, timeout=5).status_code == 416
        # every piece and the key were fetched from the cloud once, however often the player asks
        assert pump(lambda: sum(1 for p in asked if p.startswith("/seg")) == 3, 3)
        assert sum(1 for p in asked if p.startswith("/key")) == 1
        # nothing without the secret, nothing that isn't part of the recording
        assert requests.get(url.replace(bridge.secret, "wrong"), timeout=5).status_code == 404
        assert requests.get(f"{folder}/7.mp4", timeout=5).status_code == 404
        assert requests.get(f"{folder}/../../etc.mp4", timeout=5).status_code == 404
        # a recording made of MPEG-TS slices stays one HLS stream - with names the player accepts
        url = bridge.recording_url(lambda: remote + "/tslist")
        assert url.endswith("/index.m3u8")
        r = requests.get(url, timeout=5)
        assert r.headers["Content-Type"] == "application/vnd.apple.mpegurl"
        assert "0.ts" in r.text and "1.ts" in r.text and "KEY" not in r.text and "127.0.0.1" not in r.text
        assert requests.get(url.rsplit("/", 1)[0] + "/1.ts", timeout=5).content == ts[1]
        # readable reasons when the cloud says no
        for path, words in (("/expired", "sign in again"), ("/nothing", "deleted"), ("/key?n=1", "no video")):
            try:
                bridge.recording_url(lambda p=path: remote + p)
                raise AssertionError("expected CameraError")
            except camera.CameraError as exc:
                assert words in str(exc)
    finally:
        bridge.stop()
        server.shutdown()
        server.server_close()


def test_wake_commands_per_model():
    assert camera.wake_command("midr.camera.bw300") == ("wakeup_host", {"params": {"video": "1"}})
    assert camera.wake_command("midr.camera.bw400g") == ("wakeup_host", {})
    assert camera.wake_command("madv.cateye.mi3sg") == ("wakeup_host", {"params": {"video": "1"}})
    assert camera.wake_command("chuangmi.camera.021a04") is None        # mains powered: never sleeps
    assert camera.wake_command("some.cateye.new") == ("wakeup", {"video": "1"})
    assert camera.wake_command("new.camera.x", MiotSpec(WEBRTC_SPEC)) == ("wakeup_host", {})
    assert camera.is_battery_camera("midr.camera.bw300") and not camera.is_battery_camera("isa.camera.hlc8a")


class _SleepyCloud:
    """A battery camera: the wake command times out, the camera reports awake on the 2nd look, and the
    first stream request still fails."""

    def __init__(self):
        self.calls, self.reads, self.starts = [], 0, 0

    def rpc(self, did, method, params=None, timeout=20):
        self.calls.append(("rpc", method, params))
        raise CloudError("Xiaomi cloud error -3: timeout")

    def miot_get(self, did, pairs):
        self.reads += 1
        return [{"siid": pairs[0][0], "piid": pairs[0][1], "code": 0, "value": 1 if self.reads >= 2 else 0}]

    def miot_action(self, did, siid, aiid, ins, timeout=20):
        self.calls.append(("action", siid, aiid, list(ins)))
        if aiid == 3:
            return {"code": 0}
        self.starts += 1
        if self.starts == 1:
            return {"code": -706010002, "description": "remote service error"}
        return {"code": 0, "out": [ANSWER]}


def test_webrtc_answer_is_repaired():
    fixed = camera.fix_answer_sdp(ANSWER)
    lines = fixed.split("\r\n")
    assert fixed.count("a=sendonly") == 2 and "a=" not in lines and fixed.endswith("\r\n")
    assert lines[lines.index("m=audio 9 UDP/TLS/RTP/SAVPF 101") + 2] == "a=sendonly"   # where the bad line was
    assert camera.fix_answer_sdp(fixed) == fixed                                         # already fine
    bare = camera.fix_answer_sdp("v=0\nm=video 9 X 96\na=mid:0\na=rtpmap:96 H264/90000\n")
    assert bare.split("\n")[3] == "a=sendonly"                                           # missing: after a=mid


def test_webrtc_session_wakes_the_camera_and_retries():
    spec, cloud = MiotSpec(WEBRTC_SPEC), _SleepyCloud()
    svc, action = camera.webrtc_action(spec)
    assert (svc.siid, action.aiid) == (10, 1)                        # the Alexa one: the Google one has no answer
    with no_sleep():
        answer, session = camera.start_webrtc(cloud, "77", "midr.camera.bw300", spec, "OFFER")
    assert answer.count("a=sendonly") == 2 and len(session) == 36
    assert cloud.calls[0] == ("rpc", "wakeup_host", {"params": {"video": "1"}})
    starts = [c for c in cloud.calls if c[0] == "action"]
    assert len(starts) == 2 and starts[0][3] == [1, session, "OFFER", False]   # 1080p, id, offer, flag
    camera.stop_webrtc(cloud, "77", spec, session)
    assert cloud.calls[-1] == ("action", 10, 3, [session])
    assert camera.webrtc_action(MiotSpec(CAM_SPEC)) is None


def test_bridge_answers_go2rtc_over_http():
    spec = MiotSpec({**WEBRTC_SPEC, "services": WEBRTC_SPEC["services"][:3]})   # mains powered: no wake-up

    class Cloud:
        calls, timeouts = [], []

        def miot_action(self, did, siid, aiid, ins, timeout=20):
            self.calls.append((did, aiid, ins[2] if aiid == 1 else ins))
            self.timeouts.append(timeout)
            return {"code": 0, "out": [ANSWER]} if aiid == 1 else {"code": 0}
    cloud, who = Cloud(), {"ok": True}

    def resolve(did):
        if not who["ok"]:
            raise camera.CameraError("Sign in to your Mi account first.")
        return cloud, "isa.camera.700sa", spec
    bridge = camera.WebRtcBridge(resolve)
    try:
        url = bridge.url("77")
        assert url.startswith("http://127.0.0.1:") and bridge.secret in url
        r = requests.post(url, data="OFFER-SDP", timeout=5)
        assert r.status_code == 201 and r.headers["Content-Type"] == "application/sdp"
        assert r.text.count("a=sendonly") == 2 and cloud.calls[-1] == ("77", 1, "OFFER-SDP")
        assert "77" in bridge.sessions
        assert requests.post(url.replace(bridge.secret, "wrong"), data="x", timeout=5).status_code == 404
        who["ok"] = False
        assert requests.post(url, data="x", timeout=5).status_code == 502
        assert bridge.errors["77"] == "Sign in to your Mi account first."
        who["ok"] = True
        session = bridge.sessions["77"]
        bridge.close_session("77")
        assert cloud.calls[-1] == ("77", 3, [session]) and "77" not in bridge.sessions
        # quitting the app ends a running session quickly (a camera far away must not hold up the exit)
        assert requests.post(url, data="OFFER-SDP", timeout=5).status_code == 201
        bridge.close_session("77", timeout=4)
        assert cloud.calls[-1][1] == 3 and cloud.timeouts[-1] == 4 and not bridge.sessions
    finally:
        bridge.stop()


def test_live_options_best_first():
    wspec, hspec = MiotSpec(WEBRTC_SPEC), MiotSpec(CAM_SPEC)
    info = {"did": "1", "ip": "192.168.240.9", "model": "isa.camera.700sa"}
    assert camera.live_options(info, wspec, True) == ["webrtc", "p2p"]
    assert camera.live_options(info, wspec, False) == ["webrtc"]              # at another location
    assert camera.live_options({**info, "rtsp_url": "rtsp://x"}, hspec, True) == ["rtsp", "p2p", "hls"]
    assert camera.live_options(info, hspec, False) == ["hls"]
    assert camera.live_options(info, None, False) == []


def test_go2rtc_config_gets_a_webrtc_stream_per_camera():
    class Bridge:
        def url(self, did):
            return f"http://127.0.0.1:9/whep/s/{did}"
    cams = [{"did": "11", "ip": "192.168.240.60", "model": "isa.camera.700sa"},
            {"did": "22", "ip": "", "model": "midr.camera.bw300"}]
    cfg = camera.Go2rtc.build_config("42", "V1:pt", "de", cams, 18554, 11984, Bridge())
    assert cfg["streams"] == {
        "cam_11": "xiaomi://42:de@192.168.240.60?did=11&model=isa.camera.700sa",
        "rtc_11": "webrtc:http://127.0.0.1:9/whep/s/11",
        "rtc_22": "webrtc:http://127.0.0.1:9/whep/s/22"}
    assert camera.stream_name("blt.3", "webrtc") == "rtc_blt_3"


def test_hls_waits_until_the_playlist_exists():
    answers = [(404, ""), (200, "not yet"), (200, "#EXTM3U\n#EXT-X-VERSION:3\n")]
    asked = []

    class R:
        def __init__(self, status, text):
            self.status_code, self.text = status, text

    def fake_get(url, timeout):
        asked.append(url)
        return R(*answers[min(len(asked) - 1, len(answers) - 1)])
    real = camera.requests.get
    camera.requests.get = fake_get
    try:
        with no_sleep():
            camera.wait_for_playlist("https://x/live.m3u8", timeout=5)
            assert len(asked) == 3
            answers[:] = [(404, "")]
            try:
                camera.wait_for_playlist("https://x/live.m3u8", timeout=0)
                raise AssertionError("expected RuntimeError")
            except RuntimeError as exc:
                assert "Mi cloud" in str(exc)
    finally:
        camera.requests.get = real

    class Ctl:
        def cloud_action(self, siid, aiid, ins, timeout=20):
            Ctl.seen = (siid, aiid, ins)
            return {"code": 0, "out": ["rtsp://cam/live"]}       # not http: nothing to wait for
    assert camera.fetch_cloud_stream(Ctl(), MiotSpec(CAM_SPEC)) == "rtsp://cam/live" and Ctl.seen == (5, 1, [0])


# -- QR pairing ----------------------------------------------------------------------------------------
def test_pairing_qr_code():
    assert pairing.scramble("") == ""
    s = pairing.scramble("lozinka123")
    assert s != "lozinka123" and len(s) == 10 and pairing.scramble(s) == "lozinka123"
    assert pairing.scramble(pairing.QR_KEY[:5]) == pairing.QR_KEY[:5]      # would become NUL: left as it is
    text = pairing.qr_content("KEY", "Mreža 2.4", "tajna=1", "de", "Europe/Belgrade")
    parts = dict(p.split("=", 1) for p in text.split("&"))
    assert list(parts) == ["b", "s", "p", "t", "r"] and parts["b"] == "KEY"
    assert base64.b64decode(parts["s"]).decode() == "Mreža 2.4"
    assert pairing.scramble(base64.b64decode(parts["p"]).decode()) == "tajna=1"
    assert parts["t"] == "Europe/Belgrade" and parts["r"] == "de"
    assert "&r=" not in pairing.qr_content("K", "s", "p", "cn") and "&t=" not in pairing.qr_content("K", "s", "p")
    try:
        pairing.qr_content("K", "ssid", "")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    matrix = pairing.qr_matrix(text)
    assert len(matrix) == len(matrix[0]) >= 21 and matrix[0][0] is True     # finder pattern corner


# -- hub: all cloud devices in one request ---------------------------------------------------------------
class _Cfg:
    def __init__(self, devices):
        self.data, self.devices = {"refresh_seconds": 30}, devices

    def device(self, did):
        return next((d for d in self.devices if str(d["did"]) == str(did)), None)


def test_hub_reads_cloud_devices_in_one_request():
    from gui.hub import Hub
    devices = [{"did": f"blt.{i}", "name": f"D{i}", "model": "zhimi.airpurifier.fake"} for i in (1, 2, 3, 4)]

    class Cloud:
        logged_in = True
        requests = []

        def online_states(self):
            return {"blt.1": True, "blt.2": True, "blt.4": False}

        def miot_get_many(self, params):
            self.requests.append(params)
            out = []
            for p in params:
                if p["did"] in ("blt.1", "blt.4"):   # blt.4 is switched off: the cloud still has its values
                    out.append({**p, "code": 0, "value": True if (p["siid"], p["piid"]) == (2, 1) else 3})
                elif p["did"] == "blt.2":
                    out.append({**p, "code": -704042011})
            return list(reversed(out))               # the real cloud doesn't keep the order either
    cloud, ctls = Cloud(), {}
    hub = Hub(_Cfg(devices), None, lambda info: ctls.setdefault(info["did"], DeviceController(info, cloud)))
    for d in devices:
        st = hub.state(d["did"])
        st.summary, st.spec_state = Summary(MiotSpec(fake_home.PURIFIER_SPEC)), "ready"
    changed = []
    hub.changed.connect(changed.append)
    hub.poll_all()
    assert pump(lambda: not hub._batch_pending and changed)
    assert len(cloud.requests) == 1
    assert {p["did"] for p in cloud.requests[0]} == {"blt.1", "blt.2", "blt.3", "blt.4"}
    one, two, three, four = (hub.state(f"blt.{i}") for i in (1, 2, 3, 4))
    assert one.online is True and one.values[(2, 1)] is True and one.transport == "cloud" and not one.pending
    assert two.online is False and "Offline" in two.error and two.fails == 1
    assert three.online is None and not three.pending              # nothing said about it: unknown stays unknown
    # the account lists it as offline: remembered values are kept, but it is not "online"
    assert four.online is False and four.values[(2, 1)] is True and "Offline" in four.error
    assert set(changed) == {"blt.1", "blt.2", "blt.4"} and hub.cloud_online["blt.4"] is False


def test_a_lan_address_that_doesnt_answer_is_read_with_the_others():
    """Devices with an address in this PC's network that are silent there (switched off, or behind an
    isolated Wi-Fi): after one failed LAN try they are read through the Mi cloud together with the rest,
    and whether they are online is what the account says - not that the cloud still has their values."""
    from gui.hub import OFFLINE_TEXT, Hub
    devices = [{"did": "11", "name": "Heater (off)", "model": "zhimi.airpurifier.fake", "ip": "127.0.0.1",
                "token": TOKEN},
               {"did": "12", "name": "Router", "model": "xiaomi.router.fake", "ip": "127.0.0.1", "token": TOKEN},
               {"did": "13", "name": "Purifier", "model": "zhimi.airpurifier.fake", "ip": "127.0.0.1",
                "token": TOKEN}]

    class Cloud:
        logged_in = True
        batches, single = [], []

        def online_states(self):
            return {"11": False, "12": True, "13": True}

        def miot_get_many(self, params):
            self.batches.append(sorted({p["did"] for p in params}))
            return [{**p, "code": 0, "value": 3} for p in params]

        def miot_get(self, did, pairs):      # the cloud remembers the values of a device that is switched off
            self.single.append(did)
            return [{"did": did, "siid": s, "piid": p, "code": 0, "value": 3} for s, p in pairs]

    class DeadLan:
        def miot_get(self, did, pairs):
            raise miio.MiioTimeout("no reply")

        def send(self, method, params=None):
            raise miio.MiioTimeout("no reply")
    cloud, ctls = Cloud(), {}
    for d in devices:
        ctls[d["did"]] = DeviceController(d, cloud)
        ctls[d["did"]]._local_dev = lambda: DeadLan()
    hub = Hub(_Cfg(devices), None, lambda info: ctls[info["did"]])
    nothing_to_read = MiotSpec({"type": _urn("device", "router"), "services": []})
    for d in devices:
        st = hub.state(d["did"])
        st.summary = Summary(nothing_to_read if d["did"] == "12" else MiotSpec(fake_home.PURIFIER_SPEC))
        st.spec_state = "ready"
    assert not any(c.prefers_cloud() for c in ctls.values())      # at first the LAN is worth a try
    hub.poll_all()
    assert pump(lambda: cloud.batches and not hub._batch_pending, 6)
    assert all(c.prefers_cloud() and not c.cloud_only() for c in ctls.values())
    assert sorted(cloud.single) == ["11", "13"]                    # the fallback of the very first read
    heater, router, purifier = (hub.state(x) for x in ("11", "12", "13"))
    assert heater.online is False and heater.error == OFFLINE_TEXT and heater.values[(2, 1)] == 3
    assert router.online is True and router.transport == "cloud" and not router.values
    assert purifier.online is True and purifier.values[(2, 2)] == 3
    assert cloud.batches == [["13"]]                               # the heater is off: its values are known
    # from now on everything goes in the one request; nobody is asked separately
    cloud.single.clear()
    hub.poll_all()
    assert pump(lambda: len(cloud.batches) == 2 and not hub._batch_pending)
    assert cloud.single == [] and heater.online is False
    # when the LAN is tried again and still silent, remembered values don't make the heater "online"
    ctls["11"]._local_retry_at = 0
    assert not ctls["11"].prefers_cloud()
    hub.poll("11", force=True)
    assert pump(lambda: cloud.single == ["11"] and not heater.pending)
    assert heater.online is False and heater.error == OFFLINE_TEXT and ctls["11"].prefers_cloud()
    # ...unless the account's answer is too old to go by
    hub._online_at -= 3600
    assert not hub.listed_offline("11")


def test_old_devices_get_the_legacy_panel_only_on_the_lan():
    from gui.hub import Hub
    old = DeviceController.local_networks
    DeviceController.local_networks = [ipaddress.ip_network("192.168.240.0/24")]
    try:
        devices = [{"did": "1", "name": "Here", "model": "yeelink.light.color3", "ip": "127.0.0.9", "token": TOKEN},
                   {"did": "2", "name": "Away", "model": "yeelink.light.color3", "ip": "192.168.31.5",
                    "token": TOKEN}]

        class Cloud:
            logged_in = True

            def miot_get_many(self, params):
                return []

            def miot_get(self, did, pairs):
                return []

            def rpc(self, did, method, params=None, timeout=20):
                return ["on", 50, 4000, 0, 2]

        class Specs:
            def get(self, model):
                return MiotSpec(fake.SPEC)
        cloud, ctls = Cloud(), {}
        hub = Hub(_Cfg(devices), Specs(), lambda info: ctls.setdefault(info["did"], DeviceController(info, cloud)))
        hub.ensure_summary("1")
        hub.ensure_summary("2")
        assert pump(lambda: hub.state("1").summary is not None and hub.state("2").summary is not None)
        assert isinstance(hub.state("1").summary, LegacySummary)    # LAN: the old commands
        assert isinstance(hub.state("2").summary, Summary)          # cloud: the standard description
    finally:
        DeviceController.local_networks = old


# -- notifications ----------------------------------------------------------------------------------------
def test_reminders_are_remembered_and_grouped():
    a = Alerts()
    first = a.consumables("1", "Purifier", {"Filter life": 0}) + a.consumables("2", "Vacuum", {"Brush life": 5})
    assert len(first) == 2 and a.low_keys() == [["1", "Filter life"], ["2", "Brush life"]]
    again = Alerts(a.low_keys())                                     # the next start of the app
    assert again.consumables("1", "Purifier", {"Filter life": 0}) == []
    assert len(again.consumables("3", "Fan", {"Filter life": 3})) == 1
    assert summarize([]) is None and summarize(first[:1]) == first[0]
    kind, title, text = summarize(first + first)
    assert kind == "consumable" and title == "4 parts need replacing soon" and text.endswith("and 1 more.")


# -- dialogs -----------------------------------------------------------------------------------------------
def test_remove_and_manage_dialogs():
    from PySide6.QtCore import Qt

    from gui.dialogs import ManageDialog, RemoveDialog
    d = RemoveDialog("Lamp", True)
    assert d.app_only.isChecked() and d.ok.isEnabled() and not d.delete_from_account
    d.account.setChecked(True)
    assert not d.ok.isEnabled() and "Mi account" in d.ok.text()       # needs the "can't be undone" tick
    d.sure.setChecked(True)
    assert d.ok.isEnabled() and d.delete_from_account
    blocked = RemoveDialog("Lamp", False, "Sign in first")
    assert not blocked.account.isEnabled() and not blocked.delete_from_account
    devices = [{"did": "1", "name": "A", "model": "x"}, {"did": "2", "name": "B", "model": "y"}]
    m = ManageDialog(devices, lambda did: "Offline" if did == "2" else "Online")
    assert m.selected() == [] and not m.ok.isEnabled()
    m._select(lambda r: m.table.item(r, 4).text() == "Offline")
    assert m.selected() == ["2"] and m.ok.isEnabled() and "1" in m.ok.text()
    m.table.item(0, 0).setCheckState(Qt.Checked)
    assert len(m.selected()) == 2


def test_dialogs_are_freed_after_closing():
    import shiboken6
    from gui.controls import run_dialog
    from gui.dialogs import ManageDialog, RemoveDialog
    parent = QWidget()
    dialogs = [ManageDialog([{"did": "1", "name": "A", "model": "x"}], lambda did: "Online", parent),
               RemoveDialog("Lamp", True, "", parent)]
    for d in dialogs:
        d.exec = lambda: 1                                            # "closed with OK" at once
        assert run_dialog(d) == 1
        assert shiboken6.isValid(d)                                   # still readable by the caller...
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)    # ...and gone once the event loop runs
    assert not any(shiboken6.isValid(d) for d in dialogs)
    assert not parent.findChildren(ManageDialog) and not parent.findChildren(RemoveDialog)
    parent.deleteLater()


def test_camera_window_tries_the_next_way():
    from gui.camera_view import CameraWindow, Source
    asked = []

    def first():
        asked.append("a")
        raise camera.CameraError("camera asleep")

    def second():
        asked.append("b")
        raise FileNotFoundError("go2rtc is not installed")
    closed = []
    w = CameraWindow("Cam", [Source("Mi cloud (WebRTC)", first, closed=lambda: closed.append(1)),
                             Source("Mi cloud (HLS)", second)])
    try:
        assert pump(lambda: asked == ["a", "b"], 6)
        assert pump(lambda: "retrying" in w.status.text(), 3)
        assert w.picker.count() == 2 and not w.picker.isHidden()
    finally:
        w.close()
    assert closed == [1]


def test_camera_window_waits_for_the_user_when_sign_in_is_missing():
    from gui.camera_view import CameraWindow, Source
    from xc.device import NeedsSignIn
    calls = []

    def needs_account():
        calls.append(1)
        raise NeedsSignIn("Sign in to your Mi account first")
    w = CameraWindow("Cam", [Source("Mi cloud (HLS)", needs_account)])
    try:
        assert pump(lambda: "Sign in" in w.status.text(), 4)
        pump(lambda: False, 0.5)
        assert "retrying" not in w.status.text() and not w._retry_timer.isActive() and calls == [1]
    finally:
        w.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
