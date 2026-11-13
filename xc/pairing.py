"""Adding a NEW camera to the Mi account with a QR code, the way the Xiaomi Home app does it:

  1. the app asks the Mi cloud for a one-time "bind key",
  2. it shows a QR code holding that key and the Wi-Fi name and password,
  3. the camera (reset, in pairing mode) reads the code with its lens, joins the Wi-Fi and registers
     itself on the account with the key,
  4. the app asks the cloud every few seconds whether the key has been used.

QR text:  b=<bind key>&s=<base64 Wi-Fi name>&p=<base64 scrambled password>&t=<time zone>&r=<region>
The password is XOR-ed with a fixed key before base64 (a character that would become NUL stays as it is).
"""
from __future__ import annotations

import base64

# The scrambling key of the Xiaomi Home app. In the Android app it comes from a native library, so it could
# not be read from the APK that was analysed; this is the widely published value.
QR_KEY = "89JFSjo8HUbhou5776NJOMp9i90ghg7Y78G78t68899y79HY7g7y87y9ED45Ew30O0jkkl"


def scramble(password: str) -> str:
    out = []
    for i, ch in enumerate(password):
        x = ord(ch) ^ ord(QR_KEY[i % len(QR_KEY)])
        out.append(chr(x) if x else ch)
    return "".join(out)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def qr_content(bindkey: str, ssid: str, password: str, region: str = "de", timezone: str = "") -> str:
    """The text of the pairing QR code. region: the Mi account server ('de', 'sg', 'us'...); 'cn' is left out,
    as in the app."""
    if not (bindkey and ssid and password):
        raise ValueError("The pairing key, Wi-Fi name and password are all needed")
    text = f"b={bindkey}&s={_b64(ssid)}&p={_b64(scramble(password))}"
    if timezone:
        text += f"&t={timezone}"
    if region and region != "cn":
        text += f"&r={region}"
    return text


def qr_matrix(text: str) -> list[list[bool]]:
    """The QR code as rows of dark/light modules (without the quiet zone)."""
    import segno
    qr = segno.make(text, error="m", micro=False)
    return [[bool(cell) for cell in row] for row in qr.matrix]
