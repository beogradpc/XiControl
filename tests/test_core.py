import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests import fake_device as fake  # noqa: E402
from xc import miio  # noqa: E402
from xc.cloud import XiaomiCloud, _RC4  # noqa: E402
from xc.device import DeviceController  # noqa: E402
from xc.spec import MiotSpec, SpecStore  # noqa: E402


def test_rc4_known_vector():
    # RC4("Key", "Plaintext") = BBF316E8D940AF0AD3 (without the 1024-byte drop)
    r = _RC4.__new__(_RC4)
    s = list(range(256)); j = 0; key = b"Key"
    for i in range(256):
        j = (j + s[i] + key[i % 3]) & 0xFF; s[i], s[j] = s[j], s[i]
    r.s, r.i, r.j = s, 0, 0
    assert r.crypt(b"Plaintext").hex().upper() == "BBF316E8D940AF0AD3"


def test_cloud_signing_roundtrip():
    c = XiaomiCloud("de", {"user_id": "1", "ssecurity": base64.b64encode(b"x" * 16).decode(),
                           "service_token": "t"})
    nonce = base64.b64encode(b"12345678\x00\x00\x00\x01").decode()
    signed = c._signed_nonce(nonce)
    enc = base64.b64encode(_RC4(base64.b64decode(signed)).crypt(b'{"a":1}')).decode()
    dec = _RC4(base64.b64decode(signed)).crypt(base64.b64decode(enc))
    assert json.loads(dec) == {"a": 1}
    assert c._api_url("home/device_list") == "https://de.api.io.mi.com/app/home/device_list"
    assert XiaomiCloud("cn")._api_url("x") == "https://api.io.mi.com/app/x"
    sig = XiaomiCloud._sha1_sign("POST", "https://de.api.io.mi.com/app/home/device_list", {"data": "{}"}, signed)
    assert len(base64.b64decode(sig)) == 20


def test_local_protocol_against_fake_device():
    stop = fake.start_background()
    try:
        dev = miio.MiioDevice("127.0.0.1", fake.TOKEN, timeout=1.5)
        assert dev.handshake() == fake.DEVICE_ID
        assert dev.send("miIO.info")["model"] == fake.MODEL
        ctl = DeviceController({"did": "123", "ip": "127.0.0.1", "token": fake.TOKEN, "model": fake.MODEL},
                               cloud=None)
        spec = MiotSpec(fake.SPEC)
        vals = ctl.get_props(spec.readable_pairs())
        assert vals[(2, 3)] == 80 and vals[(3, 1)] == 23.4
        ctl.set_prop(2, 3, 42)
        assert ctl.get_props([(2, 3)])[(2, 3)] == 42
        ctl.action(2, 1)
        assert ctl.get_props([(2, 1)])[(2, 1)] is False
        ctl.action(4, 1, [{"piid": 1, "value": 30}])
        assert ctl.get_props([(4, 1)])[(4, 1)] == 30
        try:
            ctl.raw("bogus")
            raise AssertionError("expected error")
        except Exception as exc:  # noqa: BLE001
            assert "unknown method" in str(exc)
        found = miio.discover(timeout=1.5, extra_targets=["127.0.0.1"])
        assert found.get("127.0.0.1") == fake.DEVICE_ID
        bad = DeviceController({"did": "1", "ip": "127.0.0.1", "token": "ff" * 16, "model": ""}, None)
        try:
            bad.get_props([(2, 1)])
            raise AssertionError("expected error")
        except Exception as exc:  # noqa: BLE001
            assert "token" in str(exc).lower() or "reply" in str(exc).lower()
    finally:
        stop.set()


def test_spec_parsing_and_cache():
    spec = MiotSpec(fake.SPEC)
    p = spec.prop(2, 2)
    assert p.label == "Mode" and p.value_text(2) == "Reading" and p.writable
    assert spec.prop(2, 5).is_color and spec.prop(2, 5).value_text(0xFF8800) == "#FF8800"
    assert spec.prop(3, 1).value_text(23.456) == "23.46 °C"
    with tempfile.TemporaryDirectory() as d:
        store = SpecStore(d)
        store.put(fake.MODEL, fake.SPEC)
        assert store.get(fake.MODEL).services[1].props[0].name == "on"



def test_cloud_api_with_mocked_server():
    ssec = base64.b64encode(b"s" * 16).decode()
    c = XiaomiCloud("de", {"user_id": "42", "ssecurity": ssec, "service_token": "tok"})
    calls = []

    class Resp:
        status_code = 200

        def __init__(self, text):
            self.text = text

    def fake_post(url, data, headers, cookies, timeout):
        signed = c._signed_nonce(data["_nonce"])
        req = json.loads(_RC4(base64.b64decode(signed)).crypt(base64.b64decode(data["data"])))
        calls.append((url.split("/app/")[1], req))
        path = url.split("/app/")[1]
        if path == "home/device_list":
            body = {"code": 0, "result": {"list": [
                {"did": "111", "name": "Lamp", "model": "yeelink.light.color1", "localip": "192.168.1.5",
                 "token": "ab" * 16, "isOnline": True}]}}
        elif path == "v2/homeroom/gethome":
            body = {"code": 0, "result": {"homelist": [{"id": 9, "roomlist": [{"name": "Kitchen", "dids": ["111", "blt.1"]}]}]}}
        elif path == "v2/user/get_device_cnt":
            body = {"code": 0, "result": {"share": {"share_family": []}}}
        elif path == "v2/home/home_device_list":
            body = {"code": 0, "result": {"device_info": [{"did": "blt.1", "name": "Sensor", "model": "x.sensor"}],
                                          "has_more": False}}
        elif path == "miotspec/prop/get":
            body = {"code": 0, "result": [{**p, "code": 0, "value": 7} for p in req["params"]]}
        else:
            body = {"code": 0, "result": None}
        enc = base64.b64encode(_RC4(base64.b64decode(signed)).crypt(json.dumps(body).encode())).decode()
        return Resp(enc)

    orig = c.api_http.post
    c.api_http.post = fake_post
    try:
        devs = c.get_devices()
        assert {d["did"] for d in devs} == {"111", "blt.1"}
        lamp = next(d for d in devs if d["did"] == "111")
        assert lamp["room"] == "Kitchen" and lamp["ip"] == "192.168.1.5"
        ctl = DeviceController({"did": "blt.1", "model": "x"}, c)
        assert ctl.get_props([(2, 1), (3, 1)]) == {(2, 1): 7, (3, 1): 7}
        assert ctl.last_transport == "cloud"
    finally:
        c.api_http.post = orig


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
