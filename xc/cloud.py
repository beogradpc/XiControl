"""Xiaomi cloud (Mi Home account) client.

Used for three things:
  * signing in (QR code or password) and downloading the device list with tokens,
  * controlling devices that cannot be reached locally (Bluetooth devices behind a
    gateway, cameras, speakers, devices with local control disabled),
  * as a fallback when a device does not answer on the LAN.

The session keeps the long-lived passToken, so when the short-lived service token expires
a new one is fetched silently instead of asking the user to sign in again.
"""
from __future__ import annotations

import base64
import hashlib
import http.cookiejar
import json
import logging
import os
import random
import threading
import time
from urllib.parse import urlencode, urlparse

import requests

log = logging.getLogger(__name__)

ACCOUNT = "https://account.xiaomi.com"
SERVERS = {
    "de": "Europe",
    "cn": "China (Mainland)",
    "us": "United States",
    "ru": "Russia",
    "sg": "Singapore",
    "in": "India",
    "tw": "Taiwan",
    "i2": "India (i2)",
}


class CloudError(Exception):
    pass


class CloudAuthError(CloudError):
    """The saved session is no longer valid - the user has to sign in again."""


class CaptchaRequired(CloudError):
    def __init__(self, image: bytes):
        super().__init__("Captcha required")
        self.image = image


class TwoFactorRequired(CloudError):
    def __init__(self, message: str = "Xiaomi sent a verification code to your e-mail."):
        super().__init__(message)


def friendly(exc) -> str:
    """A short, readable message for an exception shown in the UI."""
    if isinstance(exc, requests.exceptions.Timeout):
        return "Xiaomi's servers did not answer in time - try again."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Cannot reach Xiaomi's servers - check this PC's internet connection."
    if isinstance(exc, requests.exceptions.RequestException):
        return "Problem talking to Xiaomi's servers - try again."
    return str(exc).strip() or exc.__class__.__name__


# -- RC4 (used by the encrypted Mi Home API) -------------------------------
class _RC4:
    def __init__(self, key: bytes):
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) & 0xFF
            s[i], s[j] = s[j], s[i]
        self.s, self.i, self.j = s, 0, 0
        self.crypt(bytes(1024))  # Xiaomi drops the first 1024 bytes of keystream

    def crypt(self, data: bytes) -> bytes:
        s, i, j = self.s, self.i, self.j
        out = bytearray(len(data))
        for n, b in enumerate(data):
            i = (i + 1) & 0xFF
            j = (j + s[i]) & 0xFF
            s[i], s[j] = s[j], s[i]
            out[n] = b ^ s[(s[i] + s[j]) & 0xFF]
        self.i, self.j = i, j
        return bytes(out)


def _to_json(text: str) -> dict:
    return json.loads(text.replace("&&&START&&&", "", 1))


def _cookie(jar, name: str):
    """A cookie by name from any domain (requests' .get() fails when several domains set it)."""
    value = None
    for c in jar:
        if c.name == name and c.value:
            value = c.value
    return value


def _agent() -> str:
    agent_id = "".join(chr(random.randint(65, 69)) for _ in range(13))
    rnd = "".join(chr(random.randint(97, 122)) for _ in range(18))
    return f"{rnd}-{agent_id} APP/com.xiaomi.mihome APPV/10.5.201"


def _tz_cookies() -> dict:
    """The PC's time zone in the format the Mi Home app sends."""
    std = -time.timezone
    hh, mm = divmod(abs(std) // 60, 60)
    dst = time.localtime().tm_isdst > 0
    return {
        "timezone": f"GMT{'+' if std >= 0 else '-'}{hh:02d}:{mm:02d}",
        "is_daylight": "1" if dst else "0",
        "dst_offset": str((time.timezone - time.altzone) * 1000 if dst else 0),
    }


WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


def cron_text(expr) -> str:
    """'0 1 21 * * 0,1,2,3,4,5,6 *' (sec min hour day month weekday year) -> 'Every day 21:01'."""
    parts = str(expr or "").split()
    if len(parts) < 6:
        return "At a set time"
    try:
        at = f"{int(parts[2]):02d}:{int(parts[1]):02d}"
    except ValueError:
        return "At a set time"
    days = parts[5]
    if days in ("*", "?") or set(days.split(",")) >= {"0", "1", "2", "3", "4", "5", "6"}:
        return f"Every day {at}"
    try:
        names = [WEEKDAYS[int(d) % 7] for d in days.split(",")]
    except ValueError:
        return f"At {at}"
    return f"{', '.join(names)} {at}"


def _scene_trigger(t: dict) -> dict:
    extra = t.get("extra_json") if isinstance(t.get("extra_json"), dict) else {}
    src_kind = t.get("src") or ""
    if src_kind == "timer":
        text = cron_text(t.get("value_json"))
    elif src_kind == "user":
        text = "Tap to run"
    else:
        text = (t.get("name") or "").strip() or "Device event"
    return {"kind": src_kind, "did": str(extra.get("did") or ""), "device": (extra.get("device_name") or "").strip(),
            "model": extra.get("model") or "", "text": text}


def _scene_action(a: dict) -> dict:
    p = a.get("payload_json") if isinstance(a.get("payload_json"), dict) else {}
    return {"did": str(p.get("did") or ""), "device": (p.get("device_name") or "").strip(),
            "model": p.get("model") or "", "text": (a.get("name") or "").strip() or "Action",
            "scene_id": str(p.get("scene_id") or "")}


def _message(m: dict) -> dict:
    params = m.get("params") if isinstance(m.get("params"), dict) else {}
    body = params.get("body") if isinstance(params.get("body"), dict) else {}
    where = body.get("homeRoomExtra") if isinstance(body.get("homeRoomExtra"), dict) else {}
    kind = int(m.get("type") or 0)
    return {"id": str(m.get("msg_id") or ""), "type": kind, "did": str(m.get("did") or ""),
            "title": (m.get("title") or "").strip(),
            "device": (m.get("content") or "").strip() or ("Logs" if kind == 13 else ""),
            "home": where.get("homeName") or "", "room": where.get("roomName") or "",
            "model": params.get("model") or body.get("model") or "", "time": int(m.get("ctime") or 0),
            "new": bool(m.get("is_new"))}


class XiaomiCloud:
    def __init__(self, server: str = "de", session: dict | None = None):
        self.server = server
        self.http = requests.Session()      # sign-in flow (keeps its cookies)
        self.api_http = requests.Session()  # device API (cookies are sent explicitly, never stored)
        self.api_http.cookies.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))
        self.agent = _agent()
        self.device_id = "".join(chr(random.randint(97, 122)) for _ in range(6))
        self.user_id = None
        self.ssecurity = None
        self.service_token = None
        self.pass_token = None
        self.on_session_changed = None      # callable(dict), called after a silent renewal
        self._lock = threading.Lock()
        self._renewed_at = 0.0
        self._renew_failed_at = 0.0
        self._pw_state: dict = {}
        if session:
            self.load_session(session)

    # -- session persistence ---------------------------------------------
    @property
    def logged_in(self) -> bool:
        return bool(self.ssecurity and self.service_token and self.user_id)

    @property
    def can_renew(self) -> bool:
        return bool(self.pass_token and self.user_id)

    def export_session(self) -> dict:
        return {
            "server": self.server,
            "user_id": self.user_id,
            "ssecurity": self.ssecurity,
            "service_token": self.service_token,
            "pass_token": self.pass_token,
            "device_id": self.device_id,
            "agent": self.agent,
        }

    def load_session(self, data: dict):
        self.server = data.get("server") or self.server
        self.user_id = data.get("user_id")
        self.ssecurity = data.get("ssecurity")
        self.service_token = data.get("service_token")
        self.pass_token = data.get("pass_token")
        self.device_id = data.get("device_id") or self.device_id
        self.agent = data.get("agent") or self.agent

    def logout(self):
        self.user_id = self.ssecurity = self.service_token = self.pass_token = None

    def _headers(self):
        return {"User-Agent": self.agent, "Content-Type": "application/x-www-form-urlencoded"}

    def _prepare_login_cookies(self, http=None):
        http = http or self.http
        for dom in ("mi.com", "xiaomi.com"):
            http.cookies.set("sdkVersion", "accountsdk-18.8.15", domain=dom)
            http.cookies.set("deviceId", self.device_id, domain=dom)

    def _finish_location(self, location: str, http=None) -> str:
        http = http or self.http
        r = http.get(location, headers=self._headers(), timeout=20)
        if r.status_code != 200:
            raise CloudError(f"Could not get the service token (HTTP {r.status_code})")
        token = r.cookies.get("serviceToken") or _cookie(http.cookies, "serviceToken")
        if not token:
            raise CloudError("Xiaomi did not return a service token")
        self.service_token = token
        self.pass_token = self.pass_token or _cookie(http.cookies, "passToken")
        return token

    def _signed_in(self, how: str):
        log.info("signed in to the Mi account (%s, region %s, renewable: %s)", how, self.server, self.can_renew)

    # -- QR code login -----------------------------------------------------
    def qr_start(self) -> dict:
        """Returns {'image': png bytes, 'login_url': str, 'lp': url, 'timeout': s}."""
        self._prepare_login_cookies()
        params = {
            "_qrsize": "480",
            "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
            "callback": "https://sts.api.io.mi.com/sts",
            "_hasLogo": "false",
            "sid": "xiaomiio",
            "serviceParam": "",
            "_locale": "en_GB",
            "_dc": str(int(time.time() * 1000)),
        }
        r = self.http.get(f"{ACCOUNT}/longPolling/loginUrl", params=params, timeout=20)
        if r.status_code != 200:
            raise CloudError(f"Could not start QR login (HTTP {r.status_code})")
        data = _to_json(r.text)
        if "qr" not in data:
            raise CloudError(f"Unexpected answer from Xiaomi: {data}")
        img = self.http.get(data["qr"], timeout=20)
        if img.status_code != 200:
            raise CloudError("Could not download the QR code image")
        return {
            "image": img.content,
            "login_url": data.get("loginUrl", ""),
            "lp": data["lp"],
            "timeout": int(data.get("timeout", 300)),
        }

    def qr_wait(self, lp_url: str, timeout: int, cancelled=lambda: False) -> None:
        start = time.time()
        while True:
            if cancelled():
                raise CloudError("Cancelled")
            if time.time() - start > timeout:
                raise CloudError("The QR code expired - try again")
            try:
                r = self.http.get(lp_url, timeout=10)
            except requests.exceptions.Timeout:
                continue
            if r.status_code == 200:
                break
            time.sleep(1)
        data = _to_json(r.text)
        if not data.get("location") or not data.get("ssecurity"):
            raise CloudError(f"Sign-in was not completed ({data.get('desc') or data.get('code') or 'no session'})")
        self.user_id = data.get("userId")
        self.ssecurity = data.get("ssecurity")
        self.pass_token = data.get("passToken") or None
        self._finish_location(data["location"])
        self._signed_in("QR code")

    # -- password login ------------------------------------------------------
    def password_login(self, username: str, password: str, captcha: str | None = None):
        """Raises CaptchaRequired / TwoFactorRequired when user input is needed."""
        st = self._pw_state
        if not captcha:
            self._prepare_login_cookies()
            r = self.http.get(
                f"{ACCOUNT}/pass/serviceLogin?sid=xiaomiio&_json=true",
                headers=self._headers(), cookies={"userId": username}, timeout=20,
            )
            data = _to_json(r.text)
            if "ssecurity" in data and data.get("location"):
                self.user_id, self.ssecurity = data.get("userId"), data["ssecurity"]
                self.pass_token = data.get("passToken") or None
                self._finish_location(data["location"])
                self._signed_in("saved browser session")
                return
            if "_sign" not in data:
                raise CloudError("Xiaomi did not accept this account name")
            st.clear()
            st["fields"] = {
                "sid": "xiaomiio",
                "hash": hashlib.md5(password.encode()).hexdigest().upper(),
                "callback": "https://sts.api.io.mi.com/sts",
                "qs": "%3Fsid%3Dxiaomiio%26_json%3Dtrue",
                "user": username,
                "_sign": data["_sign"],
                "_json": "true",
            }
        fields = dict(st.get("fields") or {})
        if not fields:
            raise CloudError("Start the login again")
        if captcha:
            fields["captCode"] = captcha
        r = self.http.post(f"{ACCOUNT}/pass/serviceLoginAuth2", headers=self._headers(),
                           params=fields, allow_redirects=False, timeout=20)
        if r.status_code != 200:
            raise CloudError(f"Login failed (HTTP {r.status_code})")
        data = _to_json(r.text)
        if data.get("code") == 87001:
            raise CloudError("Wrong captcha - try again")
        if data.get("captchaUrl"):
            url = data["captchaUrl"]
            if url.startswith("/"):
                url = ACCOUNT + url
            img = self.http.get(url, timeout=20)
            raise CaptchaRequired(img.content)
        if len(str(data.get("ssecurity") or "")) > 4:
            self.user_id = data.get("userId")
            self.ssecurity = data["ssecurity"]
            self.pass_token = data.get("passToken") or None
            self._finish_location(data["location"])
            self._signed_in("password")
            return
        if data.get("notificationUrl"):
            raise TwoFactorRequired(f"Xiaomi sent a verification code {self._start_2fa(data['notificationUrl'])}.")
        desc = data.get("desc") or data.get("description") or data.get("code")
        raise CloudError(f"Login failed: {desc}")

    def _start_2fa(self, notification_url: str) -> str:
        """Asks Xiaomi to send a verification code (e-mail or SMS, whatever the account uses).
        Returns where it was sent, e.g. 'to j***@gmail.com' or 'by SMS to +381***12'."""
        h = self._headers()
        r = self.http.get(notification_url.replace("/fe/service/identity/authStart", "/identity/list"),
                          headers=h, timeout=20)
        try:
            flag = int(_to_json(r.text).get("flag") or 8)
        except (ValueError, TypeError):
            flag = 8
        name = "Phone" if flag == 4 else "Email"   # Xiaomi: 4 = SMS, 8 = e-mail
        self._pw_state.update(flag=flag, verify=name)
        masked = ""
        try:
            r = self.http.get(f"{ACCOUNT}/identity/auth/verify{name}", params={"_flag": flag, "_json": "true"},
                              headers=h, timeout=20)
            info = _to_json(r.text)
            masked = info.get("maskedPhone") or info.get("maskedEmail") or ""
        except ValueError:
            pass
        r = self.http.post(f"{ACCOUNT}/identity/auth/send{name}Ticket",
                           data={"_json": "true", "icode": "", "retry": "0"}, headers=h, timeout=20)
        try:
            res = _to_json(r.text)
        except ValueError:
            res = {}
        if res.get("captchaUrl") or res.get("captchaURL"):
            raise CloudError("Xiaomi wants a picture code before it sends the verification code. "
                             "Use the QR code sign-in instead.")
        if res.get("code") not in (0, None):
            raise CloudError(f"Xiaomi couldn't send the verification code: {res.get('desc') or res.get('code')}")
        if flag == 4:
            return f"by SMS to {masked or 'your phone'}"
        return f"to {masked or 'your e-mail'}"

    def submit_2fa(self, code: str):
        st = self._pw_state
        if not st.get("verify"):
            raise CloudError("Start the login again")
        q = {"_flag": st["flag"], "ticket": code.strip(), "trust": "false", "_json": "true"}
        r = self.http.post(f"{ACCOUNT}/identity/auth/verify{st['verify']}", params=q, data=q,
                           headers=self._headers(), allow_redirects=False, timeout=20)
        try:
            res = _to_json(r.text)
        except ValueError:
            res = {"location": r.headers.get("Location")}
        if res.get("code") not in (0, None) or not res.get("location"):
            raise CloudError("The code was not accepted - check it and try again")
        self._finish_verified(res["location"])
        self._signed_in(f"password + {'SMS' if st['flag'] == 4 else 'e-mail'} code")

    def _finish_verified(self, location: str):
        """Follows the redirects after a verification code; the session arrives in cookies and headers."""
        h = self._headers()
        r = self.http.get(location, headers=h, allow_redirects=True, timeout=20)
        for resp in [*r.history, r]:
            pragma = resp.headers.get("extension-pragma")
            if pragma:
                try:
                    self.ssecurity = json.loads(pragma).get("ssecurity") or self.ssecurity
                except ValueError:
                    pass
        token = _cookie(self.http.cookies, "serviceToken")
        if not token and r.text:  # some accounts land on a page linking to the service-token URL
            i = r.text.find("https://sts.api.io.mi.com/sts")
            if i != -1:
                self.http.get(r.text[i:r.text.find('"', i)], headers=h, timeout=20)
                token = _cookie(self.http.cookies, "serviceToken")
        if not token or not self.ssecurity:
            raise CloudError("Verification did not finish - try the QR code sign-in")
        self.service_token = token
        self.user_id = self.user_id or _cookie(self.http.cookies, "userId")
        self.pass_token = _cookie(self.http.cookies, "passToken") or self.pass_token

    # -- silent renewal -------------------------------------------------------
    def renew(self) -> bool:
        """Gets a new service token with the saved passToken, without asking the user.
        Returns False when that isn't possible (the user has to sign in again)."""
        if not self.can_renew:
            return False
        asked = time.monotonic()
        with self._lock:
            if self._renewed_at >= asked:
                return True  # another thread renewed while this one waited
            if asked - self._renew_failed_at < 60:
                return False
            http = requests.Session()
            self._prepare_login_cookies(http)
            http.cookies.set("userId", str(self.user_id), domain="xiaomi.com")
            http.cookies.set("passToken", self.pass_token, domain="xiaomi.com")
            try:
                r = http.get(f"{ACCOUNT}/pass/serviceLogin", params={"sid": "xiaomiio", "_json": "true"},
                             headers=self._headers(), timeout=20)
                data = _to_json(r.text)
                if not (data.get("ssecurity") and data.get("location")):
                    self._renew_failed_at = time.monotonic()
                    log.warning("session renewal refused by Xiaomi (code %s)", data.get("code"))
                    return False
                old_ssecurity, self.ssecurity = self.ssecurity, data["ssecurity"]
                try:
                    self._finish_location(data["location"], http)
                except CloudError:
                    self.ssecurity = old_ssecurity
                    self._renew_failed_at = time.monotonic()
                    return False
            except requests.RequestException as exc:
                raise CloudError(friendly(exc)) from exc
            except ValueError:
                self._renew_failed_at = time.monotonic()
                return False
            self.user_id = data.get("userId") or self.user_id
            self.pass_token = data.get("passToken") or _cookie(http.cookies, "passToken") or self.pass_token
            self._renewed_at = time.monotonic()
        log.info("Mi account session renewed silently")
        if self.on_session_changed:
            try:
                self.on_session_changed(self.export_session())
            except Exception:  # noqa: BLE001
                log.exception("saving the renewed session failed")
        return True

    # -- signed + encrypted API ---------------------------------------------
    def _api_url(self, path: str, server: str | None = None) -> str:
        server = server or self.server
        host = "api.io.mi.com" if server == "cn" else f"{server}.api.io.mi.com"
        return f"https://{host}/app/{path.lstrip('/')}"

    def _signed_nonce(self, nonce: str, ssecurity: str | None = None) -> str:
        h = hashlib.sha256(base64.b64decode(ssecurity or self.ssecurity) + base64.b64decode(nonce))
        return base64.b64encode(h.digest()).decode()

    @staticmethod
    def _sha1_sign(method: str, url: str, params: dict, signed_nonce: str) -> str:
        path = urlparse(url).path
        if path.startswith("/app/"):
            path = path[4:]
        parts = [method.upper(), path] + [f"{k}={v}" for k, v in params.items()] + [signed_nonce]
        return base64.b64encode(hashlib.sha1("&".join(parts).encode()).digest()).decode()

    def _service_url(self, prefix: str, path: str, server: str | None = None) -> str:
        """Another Xiaomi service on the same account, e.g. prefix 'business.smartcamera' (camera recordings)."""
        server = server or self.server
        host = f"{prefix}.api.io.mi.com" if server == "cn" else f"{server}.{prefix}.api.io.mi.com"
        return f"https://{host}/{path.lstrip('/')}"

    def _signed_params(self, method: str, url: str, data: dict, ssecurity: str | None = None) -> tuple[dict, str]:
        """The RC4-encrypted, signed form of a request; returns (params, signed nonce)."""
        ssecurity = ssecurity or self.ssecurity
        millis = int(time.time() * 1000)
        nonce = base64.b64encode(os.urandom(8) + (millis // 60000).to_bytes(4, "big")).decode()
        signed = self._signed_nonce(nonce, ssecurity)
        params = {"data": json.dumps(data, separators=(",", ":"))}
        params["rc4_hash__"] = self._sha1_sign(method, url, params, signed)
        for k, v in list(params.items()):
            params[k] = base64.b64encode(_RC4(base64.b64decode(signed)).crypt(v.encode())).decode()
        params["signature"] = self._sha1_sign(method, url, params, signed)
        params["ssecurity"] = ssecurity
        params["_nonce"] = nonce
        return params, signed

    def signed_get_url(self, prefix: str, path: str, data: dict) -> str:
        """A self-contained address (it carries the session) that a media player can open directly.
        Never log or show it."""
        if not self.logged_in and not self.renew():
            raise CloudAuthError("Not signed in to a Mi account")
        url = self._service_url(prefix, path)
        params, _signed = self._signed_params("GET", url, data)
        params["yetAnotherServiceToken"] = self.service_token
        return url + "?" + urlencode(params)

    def api(self, path: str, data: dict, timeout: int = 20, server: str | None = None,
            method: str = "POST", prefix: str | None = None) -> dict:
        """prefix: None for the Mi Home API, or another service host such as 'business.smartcamera'."""
        if not self.logged_in and not self.renew():
            raise CloudAuthError("Not signed in to a Mi account")
        try:
            return self._api_once(path, data, timeout, server, method, prefix)
        except CloudAuthError:
            if not self.renew():
                raise
            return self._api_once(path, data, timeout, server, method, prefix)

    def _api_once(self, path: str, data: dict, timeout: int, server: str | None,
                  method: str = "POST", prefix: str | None = None) -> dict:
        url = self._service_url(prefix, path, server) if prefix else self._api_url(path, server)
        ssecurity, service_token = self.ssecurity, self.service_token
        params, signed = self._signed_params(method, url, data, ssecurity)
        headers = {
            "Accept-Encoding": "identity",
            "User-Agent": self.agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "x-xiaomi-protocal-flag-cli": "PROTOCAL-HTTP2",
            "MIOT-ENCRYPT-ALGORITHM": "ENCRYPT-RC4",
        }
        cookies = {
            "userId": str(self.user_id),
            "yetAnotherServiceToken": str(service_token),
            "serviceToken": str(service_token),
            "locale": "en_GB",
            **_tz_cookies(),
            "channel": "MI_APP_STORE",
        }
        try:
            if method == "GET":
                r = self.api_http.get(url, params=params, headers=headers, cookies=cookies, timeout=timeout)
            else:
                r = self.api_http.post(url, data=params, headers=headers, cookies=cookies, timeout=timeout)
        except requests.RequestException as exc:
            raise CloudError(friendly(exc)) from exc
        if r.status_code in (401, 403):
            raise CloudAuthError("Mi account session expired - sign in again")
        text = r.text.strip()
        if text.startswith("{"):
            result = json.loads(text)
        else:
            try:
                raw = _RC4(base64.b64decode(signed)).crypt(base64.b64decode(text))
                result = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                raise CloudError(f"Unreadable answer from the Xiaomi cloud (HTTP {r.status_code})") from exc
        code = result.get("code", 0)
        msg = str(result.get("message", ""))
        if code in (2, 3) or "auth err" in msg or msg in ("invalid signature", "SERVICETOKEN_EXPIRED"):
            raise CloudAuthError("Mi account session expired - sign in again")
        if code not in (0, None):
            raise CloudError(f"Xiaomi cloud error {code}: {msg}")
        return result

    # -- device list -----------------------------------------------------------
    def get_devices(self, server: str | None = None) -> list[dict]:
        devices: dict[str, dict] = {}
        rooms: dict[str, str] = {}
        home_of: dict[str, tuple[str, str]] = {}   # did -> (home id, home name)
        errors = []
        try:
            res = self.api("home/device_list", {"getVirtualModel": True, "getHuamiDevices": 1,
                                                "get_split_device": False, "support_smart_home": True},
                           server=server)
            for d in (res.get("result") or {}).get("list") or []:
                devices[str(d.get("did"))] = d
        except CloudAuthError:
            raise
        except CloudError as exc:
            errors.append(str(exc))
        homes = []
        try:
            res = self.api("v2/homeroom/gethome", {"fg": True, "fetch_share": True, "fetch_share_dev": True,
                                                   "limit": 300, "app_ver": 7}, server=server)
            for h in (res.get("result") or {}).get("homelist") or []:
                homes.append((h.get("id"), self.user_id))
                home = (str(h.get("id")), h.get("name") or "")
                for did in h.get("dids") or []:
                    home_of[str(did)] = home
                for room in h.get("roomlist") or []:
                    for did in room.get("dids") or []:
                        rooms[str(did)] = room.get("name") or ""
                        home_of[str(did)] = home
            res = self.api("v2/user/get_device_cnt", {"fetch_own": True, "fetch_share": True}, server=server)
            for h in ((res.get("result") or {}).get("share") or {}).get("share_family") or []:
                homes.append((h.get("home_id"), h.get("home_owner")))
        except CloudAuthError:
            raise
        except CloudError as exc:
            errors.append(str(exc))
        for home_id, owner in homes:
            try:
                home_id, owner = int(home_id), int(owner)
            except (TypeError, ValueError):
                continue
            start = ""
            for _ in range(20):
                try:
                    res = self.api("v2/home/home_device_list", {
                        "home_owner": owner, "home_id": home_id, "limit": 200,
                        "start_did": start, "get_split_device": False, "support_smart_home": True,
                    }, server=server)
                except CloudAuthError:
                    raise
                except CloudError as exc:
                    errors.append(str(exc))
                    break
                result = res.get("result") or {}
                for d in result.get("device_info") or []:
                    devices.setdefault(str(d.get("did")), {}).update(d)
                start = result.get("max_did") or ""
                if not (result.get("has_more") and start):
                    break
        if not devices and errors:
            raise CloudError("; ".join(errors))
        out = []
        for did, d in devices.items():
            try:
                pid = int(d.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            owner = d.get("uid")
            out.append({
                "did": did,
                "name": d.get("name") or d.get("model") or did,
                "model": d.get("model", ""),
                "ip": d.get("localip") or "",
                "token": d.get("token") or "",
                "mac": d.get("mac") or "",
                "online": bool(d.get("isOnline", True)),
                "room": rooms.get(did, ""),
                "pid": pid,                                   # Xiaomi's connection type; needed to unbind
                "parent_id": str(d.get("parent_id") or ""),   # the gateway of a Zigbee/Bluetooth device
                "shared": bool(owner) and str(owner) != str(self.user_id),
                "home_id": home_of.get(did, ("", ""))[0],
                "home": home_of.get(did, ("", ""))[1],
            })
        out.sort(key=lambda x: (x["room"], x["name"].lower()))
        return out

    def online_states(self) -> dict[str, bool]:
        """{did: online} for every device of the account, in one request."""
        res = self.api("home/device_list", {"getVirtualModel": True, "getHuamiDevices": 1,
                                            "get_split_device": False, "support_smart_home": True})
        return {str(d.get("did")): bool(d.get("isOnline", True)) for d in (res.get("result") or {}).get("list") or []}

    def find_regions(self) -> dict[str, list[dict]]:
        """Devices per region, for the regions other than the current one that have any."""
        found = {}
        for server in SERVERS:
            if server == self.server:
                continue
            try:
                devices = self.get_devices(server)
            except CloudAuthError:
                raise
            except CloudError:
                continue
            if devices:
                found[server] = devices
        return found

    # -- control -----------------------------------------------------------------
    def miot_get(self, did: str, pairs: list[tuple[int, int]]) -> list[dict]:
        out = []
        for i in range(0, len(pairs), 40):
            params = [{"did": str(did), "siid": s, "piid": p} for s, p in pairs[i:i + 40]]
            out.extend(self.api("miotspec/prop/get", {"params": params}).get("result") or [])
        return out

    def miot_set(self, did: str, siid: int, piid: int, value) -> list[dict]:
        params = [{"did": str(did), "siid": siid, "piid": piid, "value": value}]
        return self.api("miotspec/prop/set", {"params": params}).get("result") or []

    def miot_get_many(self, params: list[dict]) -> list[dict]:
        """Properties of many devices in few requests. params: [{'did', 'siid', 'piid'}]; the results carry
        did/siid/piid too and are NOT returned in the order asked."""
        out = []
        for i in range(0, len(params), 100):
            out.extend(self.api("miotspec/prop/get", {"params": params[i:i + 100]}, timeout=30).get("result") or [])
        return out

    def miot_action(self, did: str, siid: int, aiid: int, ins: list, timeout: int = 20) -> dict:
        """ins: the input VALUES in the order of the action's parameters (the cloud does not take
        {'piid', 'value'} objects - that is the format of the local protocol)."""
        params = {"did": str(did), "siid": siid, "aiid": aiid, "in": list(ins or [])}
        return self.api("miotspec/action", {"params": params}, timeout=timeout).get("result") or {}

    def rpc(self, did: str, method: str, params=None, timeout: int = 20):
        res = self.api(f"home/rpc/{did}", {"id": random.randint(1, 9999), "method": method,
                                           "params": [] if params is None else params}, timeout=timeout)
        return res.get("result")

    # -- managing the account's devices (same requests as the Xiaomi Home app) ---------------------------
    def rename_device(self, did: str, name: str) -> None:
        self.api("device/rename", {"app_version": 1, "newName": name, "did": str(did),
                                   "is_group": str(did).startswith("group.")})

    def delete_devices(self, devices: list[dict]) -> dict:
        """Unbinds devices from the Mi account - they disappear from Xiaomi Home too and must be paired
        again to come back. devices: [{'did', 'pid'}]. Returns {did: result code}; 0 means removed."""
        dev_list = [{"did": str(d["did"]), "pid": int(d.get("pid") or 0)} for d in devices]
        if not dev_list:
            return {}
        res = self.api("user/del_owner_device_batch", {"devList": dev_list})
        result = res.get("result")
        return result if isinstance(result, dict) else {}

    def unbind_device(self, did: str, pid=None) -> bool:
        """Deletes ONE device from the account and checks that it is really gone.
        Returns False if the device wasn't on the account in the first place."""
        did = str(did)
        res = self.api("home/device_list", {"getVirtualModel": True, "getHuamiDevices": 1,
                                            "get_split_device": False, "support_smart_home": True})
        record = next((d for d in (res.get("result") or {}).get("list") or [] if str(d.get("did")) == did), None)
        if record is None:
            return False
        owner = record.get("uid")
        if owner and str(owner) != str(self.user_id):
            raise CloudError("This device belongs to another Mi account (it was shared with you) - "
                             "only its owner can delete it.")
        if record.get("pid") is not None:
            pid = record["pid"]
        code = self.delete_devices([{"did": did, "pid": pid or 0}]).get(did)
        if code not in (0, None):
            raise CloudError(f"Xiaomi refused to delete the device (code {code}).")
        if did in self.online_states():
            raise CloudError("Xiaomi accepted the request, but the device is still on the account - "
                             "nothing was deleted.")
        log.info("device %s deleted from the Mi account", did)
        return True

    def get_bindkey(self) -> str:
        """A one-time key a new device uses to attach itself to this account (QR-code pairing)."""
        key = ((self.api("v2/user/get_bindkey", {}).get("result")) or {}).get("bindkey")
        if not key:
            raise CloudError("Xiaomi did not return a pairing key")
        return key

    def check_bindkey(self, key: str) -> dict:
        """{'ret': 1, 'bind_did': ...} once a device has used the key; {'ret': 0, 'check_after': s} before."""
        res = self.api("v2/user/check_bindkey", {"bindkey": key}).get("result") or {}
        try:
            res["ret"] = int(res.get("ret", 0))
        except (TypeError, ValueError):
            res["ret"] = 0
        return res

    def device_record(self, did: str) -> dict | None:
        """The account's own record of one device (pid, owner, firmware version...), or None."""
        res = self.api("home/device_list", {"getVirtualModel": True, "getHuamiDevices": 1,
                                            "get_split_device": False, "support_smart_home": True})
        return next((d for d in (res.get("result") or {}).get("list") or [] if str(d.get("did")) == str(did)),
                    None)

    # -- firmware -----------------------------------------------------------------------------------------
    def firmware_status(self, dids) -> dict[str, dict]:
        """Firmware of many devices in few requests (as the Xiaomi Home app checks it):
        {did: {"curr", "latest", "isLatest", "description", "updating", "ota_status", "ota_progress", ...}}.
        Devices the Mi cloud doesn't update (Bluetooth devices get their updates from the phone) are missing."""
        dids = [str(d) for d in dids]
        out: dict[str, dict] = {}
        for i in range(0, len(dids), 100):
            res = self.api("home/multi_checkversion", {"dids": dids[i:i + 100]}, timeout=30)
            result = res.get("result")
            items = result.get("list") if isinstance(result, dict) else result
            for it in items or []:
                if isinstance(it, dict) and it.get("did"):
                    out[str(it["did"])] = it
        return out

    def check_firmware(self, did: str, pid=0) -> dict:
        """Firmware state of one device, including the progress of a running update."""
        res = self.api("home/checkversion", {"did": str(did), "pid": int(pid or 0)})
        result = res.get("result")
        return result if isinstance(result, dict) else {}

    def start_firmware_update(self, did: str, pid=0) -> None:
        """Tells the device to download and install its newest firmware - the request the Xiaomi Home app
        sends. The device restarts afterwards; check_firmware() shows the progress."""
        self.api("home/devupgrade", {"did": str(did), "pid": int(pid or 0), "platform": "android"})
        log.info("firmware update started for device %s", did)

    # -- homes, rooms and scenes ------------------------------------------------------------------------
    def homes(self) -> list[dict]:
        """The account's homes: [{"id", "name", "owner", "rooms": [{"id", "name", "dids"}], "dids"}]."""
        res = self.api("v2/homeroom/gethome", {"fg": True, "fetch_share": True, "fetch_share_dev": True,
                                               "limit": 300, "app_ver": 7})
        homes = []
        for h in (res.get("result") or {}).get("homelist") or []:
            try:
                owner = int(h.get("uid") or self.user_id)
            except (TypeError, ValueError):
                owner = int(self.user_id or 0)
            rooms = [{"id": str(r.get("id")), "name": r.get("name") or "",
                      "dids": [str(x) for x in r.get("dids") or []]} for r in h.get("roomlist") or []]
            homes.append({"id": str(h.get("id")), "name": h.get("name") or "", "owner": owner, "rooms": rooms,
                          "dids": [str(x) for x in h.get("dids") or []],
                          "raw": {k: h.get(k) for k in ("icon", "background", "latitude", "longitude", "city_id",
                                                        "address") if h.get(k) not in (None, "")}})
        return homes

    # editing homes and rooms - the requests the Xiaomi Home app sends
    def rename_home(self, home: dict, name: str) -> None:
        data = {"id": str(home["id"]), "type": "set", "home_owner": int(home["owner"]), "app_ver": 7,
                "name": name}
        raw = home.get("raw") or {}
        for k in ("icon", "city_id", "address"):
            if raw.get(k):
                data[k] = raw[k]
        for k in ("latitude", "longitude"):
            try:
                data[k] = float(raw[k])
            except (KeyError, TypeError, ValueError):
                pass
        self.api("homeroom/ctrhome", data)

    def add_room(self, home: dict, name: str, dids=()) -> str:
        """Makes a new room in a home; returns its id (if the Mi cloud tells it)."""
        res = self.api("homeroom/addroom", {"type": "addroom", "name": name, "home_owner": int(home["owner"]),
                                            "parent_id": str(home["id"]), "background": "",
                                            "did": [str(d) for d in dids]})
        result = res.get("result")
        return str(result.get("id")) if isinstance(result, dict) and result.get("id") is not None else ""

    def rename_room(self, home: dict, room: dict, name: str) -> None:
        self.api("homeroom/ctrroom", {"type": "set", "home_owner": int(home["owner"]), "id": str(room["id"]),
                                      "name": name, "app_ver": 7})

    def delete_room(self, home: dict, room: dict) -> None:
        """Deletes a room; its devices stay in the home without a room."""
        self.api("homeroom/ctrroom", {"type": "delete", "id": str(room["id"]), "app_ver": 7,
                                      "home_owner": int(home["owner"])})

    def move_to_room(self, did: str, room_id: str, home_owner=None) -> None:
        """Moves a device into another room of its home (as in the Xiaomi Home app)."""
        self.api("homeroom/bind_device_to_room", {"id": str(room_id), "did": str(did),
                                                  "home_owner": int(home_owner or self.user_id)})

    def scenes(self) -> list[dict]:
        """Scenes and automations of the Xiaomi Home app, from every home:
        [{"id", "name", "home_id", "home", "owner", "type", "manual", "enabled"}]. "manual" ones are the
        tap-to-run scenes; the others are automations the Mi cloud runs by itself."""
        out = []
        for h in self.homes():
            res = self.api("appgateway/miot/appsceneservice/AppSceneService/GetSceneList",
                           {"home_id": h["id"], "owner_uid": h["owner"], "get_type": 2, "app_version": 12})
            for s in ((res.get("result") or {}).get("scene_info_list")) or []:
                if not isinstance(s, dict) or not s.get("scene_id"):
                    continue
                trig = s.get("scene_trigger") if isinstance(s.get("scene_trigger"), dict) else {}
                manual = any(isinstance(t, dict) and t.get("key") == "user.click" for t in trig.get("triggers") or [])
                try:
                    kind = int(s.get("type") or 0)
                except (TypeError, ValueError):
                    kind = 0
                acts = s.get("scene_action") if isinstance(s.get("scene_action"), dict) else {}
                out.append({"id": str(s["scene_id"]), "name": (s.get("name") or "").strip() or "Scene",
                            "home_id": h["id"], "home": h["name"], "owner": h["owner"], "type": kind,
                            "manual": manual, "enabled": bool(s.get("enable", True)),
                            "triggers": [_scene_trigger(t) for t in trig.get("triggers") or [] if isinstance(t, dict)],
                            "actions": [_scene_action(a) for a in acts.get("actions") or [] if isinstance(a, dict)]})
        return out

    def run_scene(self, scene: dict) -> None:
        """Runs a tap-to-run scene of the Xiaomi Home app (what tapping it on the phone does)."""
        self.api("appgateway/miot/appsceneservice/AppSceneService/NewRunScene",
                 {"scene_id": str(scene["id"]), "scene_type": int(scene.get("type") or 0),
                  "home_id": str(scene["home_id"]), "owner_uid": str(scene["owner"])})

    def set_scene_enabled(self, scene: dict, enable: bool) -> None:
        """Switches an automation of the Xiaomi Home app on or off (the switch next to it in the app)."""
        self.api("appgateway/miot/appsceneservice/AppSceneService/SetSceneStatus",
                 {"scene_id": str(scene["id"]), "home_id": str(scene["home_id"]),
                  "owner_uid": int(scene["owner"]), "enable": bool(enable)})

    # -- message centre (the Notifications page of the Xiaomi Home app) --------------------------------------
    def messages(self) -> list[dict]:
        """The newest message of every device plus the automation log, newest first:
        [{"id", "type", "did", "title", "device", "home", "room", "model", "time", "new"}]."""
        res = self.api("v2/message/v2/typelist", {})
        result = res.get("result")
        return sorted((_message(m) for m in (result.get("messages") if isinstance(result, dict) else None) or []
                       if isinstance(m, dict)), key=lambda m: -m["time"])

    def device_messages(self, did: str, limit: int = 30) -> list[dict]:
        """Earlier messages of one device, newest first."""
        res = self.api("v2/message/v2/list", {"type": 6, "did": str(did), "limit": int(limit)})
        result = res.get("result")
        return sorted((_message(m) for m in (result.get("messages") if isinstance(result, dict) else None) or []
                       if isinstance(m, dict)), key=lambda m: -m["time"])

    # -- sharing ----------------------------------------------------------------------------------------------
    def shares(self) -> dict[str, list[dict]]:
        """Which devices are shared with whom: {did: [{"userid", "name", "time"}]}."""
        res = self.api("share/device_share_list", {})
        result = res.get("result")
        table = result.get("list") if isinstance(result, dict) else None
        out = {}
        for did, users in (table.items() if isinstance(table, dict) else []):
            out[str(did)] = [{"userid": str(u.get("userid") or ""), "name": u.get("nickname") or "",
                              "time": int(u.get("mtime") or 0)} for u in users or [] if isinstance(u, dict)]
        return out

    def device_shares(self, did: str, pid=0) -> list[dict]:
        """The accounts one device is shared with: [{"userid", "name", "time", "readonly", "status"}].
        "readonly" is True for view-only sharing, None when the cloud doesn't say."""
        res = self.api("share/get_share_user", {"did": str(did), "pid": int(pid or 0)})
        result = res.get("result")
        out = []
        for u in (result.get("list") if isinstance(result, dict) else None) or []:
            if not isinstance(u, dict) or not u.get("userid"):
                continue
            ro = u.get("isReadOnly")
            out.append({"userid": str(u["userid"]), "name": u.get("nickname") or "",
                        "time": int(u.get("sharetime") or 0), "readonly": bool(ro) if ro is not None else None,
                        "status": u.get("status")})
        return out

    def find_user(self, userid) -> dict | None:
        """The Mi account with this Mi ID: {"userid", "name"}; None when there is no such account."""
        uid = str(userid).strip()
        if not uid.isdigit():
            return None
        res = self.api("home/profile", {"id": uid})
        result = res.get("result")
        if not isinstance(result, dict) or not (result.get("nickname") or "").strip():
            return None
        return {"userid": str(result.get("userid") or uid), "name": result["nickname"].strip()}

    def share_device(self, did: str, userid, readonly: bool = False) -> None:
        """Invites another Mi account to use a device (Xiaomi Home: device → Share device). The other person
        gets an invitation in their Xiaomi Home app and has to accept it. readonly: they can see it, not control."""
        data = {"command": "share_request", "did": str(did), "userid": str(userid)}
        if readonly:
            data["type"] = "readonly"
        self.api("share/share_request", data)

    def unshare_device(self, did: str, userid, pid=0) -> None:
        """Stops sharing a device with one account."""
        self.api("share/delete_user", {"list": [{"did": str(did), "pid": int(pid or 0), "userid": [str(userid)]}]})

    # -- statistics the Mi cloud keeps (e.g. the consumption of a plug per day and per month) ----------------
    def statistics(self, did: str, key: str, data_type: str, since: int, until: int, limit: int = 100) -> list:
        """[(time, value)] oldest first. key: 'siid.piid' or a legacy name such as 'powerCost';
        data_type: e.g. 'stat_day_v3', 'stat_month_v3', 'stat_day', 'stat_month'."""
        res = self.api("v2/user/statistics", {"did": str(did), "key": key, "data_type": data_type,
                                              "limit": int(limit), "time_start": int(since), "time_end": int(until)},
                       timeout=30)
        out = []
        for row in res.get("result") or []:
            try:
                value = json.loads(row["value"]) if isinstance(row.get("value"), str) else row.get("value")
                value = value[0] if isinstance(value, list) and value else value
                out.append((int(row["time"]), float(value)))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(out)

    # -- camera recordings ------------------------------------------------------------------------------
    def camera_events(self, did: str, model: str, since_ms: int, until_ms: int, limit: int = 30) -> list[dict]:
        """Recorded events (motion, doorbell...) of a camera, newest first."""
        res = self.api("common/app/get/eventlist", {
            "did": str(did), "model": model, "doorBell": ".cateye." in model or "dbell" in model,
            "eventType": "Default", "needMerge": True, "sortType": "DESC", "region": self.server.upper(),
            "language": "en_US", "beginTime": int(since_ms), "endTime": int(until_ms), "limit": int(limit),
        }, method="GET", prefix="business.smartcamera")
        return ((res.get("data") or {}).get("thirdPartPlayUnits")) or []

    def camera_event_url(self, did: str, model: str, file_id: str, is_alarm: bool = True) -> str:
        """HLS address of one recorded event (see signed_get_url: keep it out of logs)."""
        return self.signed_get_url("business.smartcamera", "common/app/m3u8", {
            "did": str(did), "model": model, "fileId": file_id, "isAlarm": bool(is_alarm), "videoCodec": "H265"})
