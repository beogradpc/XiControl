"""Tests for account storage, session renewal, device reachability, discovery and cameras."""
import base64
import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import xc.cloud as cloudmod  # noqa: E402
from tests import fake_device as fake  # noqa: E402
from xc import camera, miio, secure  # noqa: E402
from xc.cloud import CloudAuthError, CloudError, XiaomiCloud  # noqa: E402
from xc.config import Config  # noqa: E402
from xc.device import DeviceController, DeviceOffline, NeedsSignIn  # noqa: E402
from xc.log import redact  # noqa: E402
from xc.spec import MiotSpec  # noqa: E402

TOKEN = "0123456789abcdef0123456789abcdef"
SSEC = base64.b64encode(b"s" * 16).decode()


def _session(**kw):
    return {"user_id": "42", "ssecurity": SSEC, "service_token": "tok", **kw}


# -- secrets on disk ----------------------------------------------------------------------------
def test_secret_roundtrip():
    if not secure.AVAILABLE:
        return
    enc = secure.encrypt("hello ✓")
    assert enc.startswith("dpapi:") and "hello" not in enc
    assert secure.decrypt(enc) == "hello ✓"
    assert secure.encrypt("") == "" and secure.decrypt("plain") == "plain"


def test_config_encrypts_old_plain_file_and_backup():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        plain = {"cloud_session": _session(pass_token="V1:secretpass"),
                 "devices": [{"did": "1", "name": "Lamp", "token": TOKEN, "rtsp_url": "rtsp://u:p@cam/1"}]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plain, f)
        cfg = Config(path)
        assert cfg.devices[0]["token"] == TOKEN and cfg.data["cloud_session"]["pass_token"] == "V1:secretpass"
        if not secure.AVAILABLE:
            return
        for name in ("config.json", "config.json.bak"):
            text = open(os.path.join(d, name), encoding="utf-8").read()
            assert TOKEN not in text and "secretpass" not in text and "u:p@" not in text, name
            assert "dpapi:" in text
        again = Config(path)
        assert again.devices[0]["token"] == TOKEN
        assert again.devices[0]["rtsp_url"] == "rtsp://u:p@cam/1"
        assert again.data["cloud_session"]["service_token"] == "tok" and not again.warning


def test_config_damaged_file_restored_from_backup():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        cfg = Config(path)
        cfg.upsert({"did": "7", "name": "Fan", "token": TOKEN})
        cfg.save()
        cfg.save()  # second save makes config.json.bak
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        again = Config(path)
        assert [x["did"] for x in again.devices] == ["7"] and again.devices[0]["token"] == TOKEN
        assert "restored" in again.warning
        assert any(n.startswith("config.json.damaged-") for n in os.listdir(d))


def test_config_unreadable_secret_is_dropped_with_warning():
    if not secure.AVAILABLE:
        return
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"devices": [{"did": "1", "token": "dpapi:AAAAAAAA"}], "cloud_session": "dpapi:QUJD"}, f)
        cfg = Config(path)
        assert cfg.devices[0]["token"] == "" and cfg.data["cloud_session"] is None
        assert "Sign in" in cfg.warning


# -- reachability -------------------------------------------------------------------------------
def test_cloud_only_without_sign_in_is_not_an_expired_session():
    ctl = DeviceController({"did": "123", "ip": "192.168.1.9", "token": TOKEN, "mode": "cloud"}, None)
    assert ctl.needs_sign_in() and "Sign in" in ctl.unreachable_reason()
    try:
        ctl.get_props([(2, 1)])
        raise AssertionError("expected NeedsSignIn")
    except NeedsSignIn:
        pass
    lan_only = DeviceController({"did": "5", "ip": "", "token": "", "mode": "local"}, None)
    assert not lan_only.needs_sign_in() and "IP address" in lan_only.unreachable_reason()


def test_cloud_offline_device_is_reported_offline():
    c = XiaomiCloud("de", _session())
    c.api = lambda path, data, **kw: {
        "code": 0, "result": [{**p, "code": -704042011} for p in data["params"]]}
    ctl = DeviceController({"did": "blt.1", "model": "x"}, c)
    try:
        ctl.get_props([(2, 1), (3, 1)])
        raise AssertionError("expected DeviceOffline")
    except DeviceOffline:
        pass


def test_lan_failure_switches_to_cloud_first_then_back():
    c = XiaomiCloud("de", _session())
    c.api = lambda path, data, **kw: {
        "code": 0, "result": [{**p, "code": 0, "value": 1} for p in data["params"]]}
    ctl = DeviceController({"did": "9", "ip": "127.0.0.1", "token": TOKEN, "mode": "auto"}, c)
    calls = []

    class DeadLan:
        def miot_get(self, did, pairs):
            calls.append("lan")
            raise miio.MiioTimeout("no reply")
    ctl._local_dev = lambda: DeadLan()
    assert ctl.get_props([(2, 1)]) == {(2, 1): 1} and calls == ["lan"]
    assert ctl._order() == ["cloud", "local"]           # LAN failed: cloud first for a while
    ctl.get_props([(2, 1)])
    assert calls == ["lan"] and ctl.last_transport == "cloud"
    ctl._local_retry_at = 0                              # ...and later the LAN is tried first again
    assert ctl._order() == ["local", "cloud"]


# -- Mi account ------------------------------------------------------------------------------------
class _Resp:
    def __init__(self, text="", status=200, cookies=None, headers=None):
        self.text, self.status_code = text, status
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.history = []


def test_expired_session_is_renewed_with_pass_token():
    saved = []
    c = XiaomiCloud("de", _session(pass_token="V1:pt", device_id="abcdef"))
    c.on_session_changed = saved.append
    seen_tokens = []

    def fake_once(path, data, timeout, server, *more):
        seen_tokens.append(c.service_token)
        if c.service_token == "tok":
            raise CloudAuthError("expired")
        return {"code": 0, "result": "ok"}
    c._api_once = fake_once

    class FakeSession:
        def __init__(self):
            import requests
            self.cookies = requests.cookies.RequestsCookieJar()

        def get(self, url, **kw):
            if "serviceLogin" in url:
                assert self.cookies.get("passToken") == "V1:pt" and self.cookies.get("userId") == "42"
                return _Resp('&&&START&&&{"code":0,"ssecurity":"' + SSEC + '","location":"https://sts/x",'
                             '"userId":42,"passToken":"V1:pt2"}')
            self.cookies.set("serviceToken", "fresh", domain="sts.api.io.mi.com")
            return _Resp("ok", cookies={"serviceToken": "fresh"})

    orig = cloudmod.requests.Session
    cloudmod.requests.Session = FakeSession
    try:
        assert c.api("home/device_list", {})["result"] == "ok"
    finally:
        cloudmod.requests.Session = orig
    assert seen_tokens == ["tok", "fresh"] and c.pass_token == "V1:pt2"
    assert saved and saved[0]["service_token"] == "fresh" and saved[0]["pass_token"] == "V1:pt2"


def test_no_pass_token_means_sign_in_again():
    c = XiaomiCloud("de", _session())
    c._api_once = lambda *a: (_ for _ in ()).throw(CloudAuthError("expired"))
    try:
        c.api("x", {})
        raise AssertionError("expected CloudAuthError")
    except CloudAuthError:
        pass


def test_network_errors_are_readable():
    import requests
    c = XiaomiCloud("de", _session())

    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("HTTPSConnectionPool(host=...) Max retries exceeded")
    c.api_http.post = boom
    try:
        c.api("home/device_list", {})
        raise AssertionError("expected CloudError")
    except CloudError as exc:
        assert "internet connection" in str(exc) and "Pool" not in str(exc)


def test_two_factor_by_sms():
    c = XiaomiCloud("de")
    calls = []

    class FakeHttp:
        cookies = []

        def get(self, url, **kw):
            calls.append(("GET", url.split("?")[0].rsplit("/", 1)[-1]))
            if "identity/list" in url:
                assert "context=CTX" in url
                return _Resp('&&&START&&&{"code":0,"flag":4}')
            return _Resp('&&&START&&&{"code":0,"maskedPhone":"+381***12"}')

        def post(self, url, **kw):
            calls.append(("POST", url.rsplit("/", 1)[-1]))
            return _Resp('&&&START&&&{"code":0}')
    c.http = FakeHttp()
    where = c._start_2fa("https://account.xiaomi.com/fe/service/identity/authStart?sid=xiaomiio&context=CTX")
    assert where == "by SMS to +381***12"
    assert ("POST", "sendPhoneTicket") in calls and c._pw_state["verify"] == "Phone"


def test_find_regions():
    c = XiaomiCloud("de", _session())
    c.get_devices = lambda server=None: [{"did": "1"}] * (3 if server == "sg" else 0)
    assert list(c.find_regions()) == ["sg"]


def test_time_zone_cookie():
    tz = cloudmod._tz_cookies()
    assert tz["timezone"].startswith("GMT") and len(tz["timezone"]) == 9 and tz["is_daylight"] in ("0", "1")


# -- discovery -----------------------------------------------------------------------------------
def test_broadcast_addresses():
    assert miio.broadcast_for("192.168.240.50", 24) == "192.168.240.255"
    assert miio.broadcast_for("10.0.4.20", 23) == "10.0.5.255"
    assert miio.broadcast_for("100.75.64.92", 32) is None      # Tailscale
    assert miio.broadcast_for("100.75.64.92", 24) is None      # CGNAT / VPN range
    assert miio.broadcast_for("169.254.1.2", 16) is None
    assert "100.75.64.255" not in miio._local_broadcasts()


def test_hello_reads_id_and_shared_token():
    stop = threading.Event()
    dev = dict(fake.LAMP, share_token=True)
    fake.start_background("127.0.0.5", dev, stop)
    try:
        dev_id, token = miio.hello("127.0.0.5", timeout=1.5)
        assert dev_id == fake.DEVICE_ID and token == fake.TOKEN
    finally:
        stop.set()


# -- cameras -------------------------------------------------------------------------------------
CAM_SPEC = {"type": "urn:miot-spec-v2:device:camera:0000A01C:chuangmi-ipc019:1", "services": [
    {"iid": 2, "type": "urn:miot-spec-v2:service:camera-control:0000782F:x:1", "description": "Camera Control",
     "properties": [{"iid": 1, "type": "urn:miot-spec-v2:property:on:00000006:x:1", "description": "Switch",
                     "format": "bool", "access": ["read", "write"]}]},
    {"iid": 5, "type": "urn:miot-spec-v2:service:camera-stream-for-google-home:00007830:x:1",
     "description": "Camera Stream for Google Home",
     "properties": [{"iid": 1, "type": "urn:miot-spec-v2:property:stream-address:00000097:x:1",
                     "description": "Stream Address", "format": "string", "access": []},
                    {"iid": 2, "type": "urn:miot-spec-v2:property:video-attribute:0000009A:x:1",
                     "description": "Video Attribute", "format": "uint8", "access": [],
                     "value-list": [{"value": 0, "description": "Auto"}, {"value": 1, "description": "SD"}]}],
     "actions": [{"iid": 1, "type": "urn:miot-spec-v2:action:start-hls-stream:0000004D:x:1",
                  "description": "Start HLS Stream", "in": [2], "out": [1]}]},
]}


def test_cloud_stream_for_cameras():
    spec = MiotSpec(CAM_SPEC)
    svc, action = camera.cloud_stream_action(spec)
    assert action.name == "start-hls-stream" and action.out == [1]
    assert camera.default_inputs(svc, action) == [0]          # plain values: what the Mi cloud accepts
    assert camera.stream_url_from_result({"code": 0, "out": ["https://x/live.m3u8", 1700000000]}) == \
        "https://x/live.m3u8"
    assert camera.stream_url_from_result({"out": [{"piid": 1, "value": "https://y/a.m3u8"}]}) == "https://y/a.m3u8"
    assert camera.cloud_stream_action(MiotSpec({"services": CAM_SPEC["services"][:1]})) is None


def test_go2rtc_config():
    cams = [{"did": "1063461234", "ip": "192.168.240.60", "model": "chuangmi.camera.ipc019"},
            {"did": "55", "ip": "", "model": "isa.camera.hlc7"},          # no IP: skipped
            {"did": "66", "ip": "192.168.240.61", "model": "zhimi.airpurifier.mb4"}]
    cfg = camera.Go2rtc.build_config("42", "V1:pt", "de", cams, 18554, 11984)
    assert cfg["xiaomi"] == {"42": "V1:pt"}
    assert cfg["rtsp"]["listen"] == "127.0.0.1:18554" and cfg["api"]["listen"].startswith("127.0.0.1:")
    assert cfg["streams"] == {"cam_1063461234": "xiaomi://42:de@192.168.240.60?did=1063461234"
                                                "&model=chuangmi.camera.ipc019"}
    assert camera.supports_go2rtc("isa.dbell.x") and not camera.supports_go2rtc("yeelink.light.x")


def test_go2rtc_explains_missing_pieces():
    g = camera.Go2rtc(tempfile.mkdtemp())
    g.find = lambda: None
    try:
        g.stream_url(None, [], "1")
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass
    g.find = lambda: "go2rtc.exe"
    try:
        g.stream_url(XiaomiCloud("de", _session()), [], "1")  # signed in, but no passToken
        raise AssertionError("expected PermissionError")
    except PermissionError as exc:
        assert "Sign in" in str(exc)


# -- misc ------------------------------------------------------------------------------------------
def test_log_hides_secrets():
    text = redact(f'token {TOKEN} serviceToken=abc123 "ssecurity": "xyz" passToken V1:abcdefghijkl')
    assert TOKEN not in text and "abc123" not in text and "xyz" not in text and "abcdefghijkl" not in text


def test_poll_backoff():
    from gui.hub import backoff
    assert [backoff(n) for n in (1, 2, 3, 4, 5, 9)] == [0, 0, 30, 60, 120, 300]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
