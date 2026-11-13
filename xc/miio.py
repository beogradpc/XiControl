"""Local miIO protocol (UDP port 54321) used by Xiaomi Wi-Fi devices.

Every packet is a 32-byte header followed by an AES-128-CBC encrypted JSON
payload. The AES key and IV are derived from the device's 16-byte token.
"""
from __future__ import annotations

import ipaddress
import json
import random
import socket
import struct
import threading
import time
from hashlib import md5

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PORT = 54321
MAGIC = 0x2131
HELLO = bytes.fromhex("21310020" + "ff" * 28)


class MiioError(Exception):
    pass


class MiioTimeout(MiioError):
    pass


class MiioUnsupported(MiioError):
    """This can't be done over the LAN at all (the device isn't at fault - nothing was sent to it)."""


class MiioDeviceError(MiioError):
    def __init__(self, code, message):
        super().__init__(f"Device error {code}: {message}")
        self.code = code
        self.message = message


def _md5(data: bytes) -> bytes:
    return md5(data).digest()


def parse_token(token: str) -> bytes:
    token = (token or "").strip()
    try:
        raw = bytes.fromhex(token)
    except ValueError as exc:
        raise MiioError("Token must be 32 hexadecimal characters") from exc
    if len(raw) != 16:
        raise MiioError("Token must be 32 hexadecimal characters")
    return raw


class Crypto:
    def __init__(self, token: bytes):
        self.token = token
        self.key = _md5(token)
        self.iv = _md5(self.key + token)

    def encrypt(self, plaintext: bytes) -> bytes:
        padder = padding.PKCS7(128).padder()
        data = padder.update(plaintext) + padder.finalize()
        enc = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).encryptor()
        return enc.update(data) + enc.finalize()

    def decrypt(self, ciphertext: bytes) -> bytes:
        dec = Cipher(algorithms.AES(self.key), modes.CBC(self.iv)).decryptor()
        data = dec.update(ciphertext) + dec.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(data) + unpadder.finalize()


def build_packet(crypto: Crypto, device_id: bytes, stamp: int, payload: dict) -> bytes:
    enc = crypto.encrypt(json.dumps(payload, separators=(",", ":")).encode() + b"\x00")
    header = struct.pack(">HHI4sI", MAGIC, 32 + len(enc), 0, device_id, stamp & 0xFFFFFFFF)
    return header + _md5(header + crypto.token + enc) + enc


def parse_header(packet: bytes) -> tuple[int, bytes, int]:
    if len(packet) < 32:
        raise MiioError("Packet too short")
    magic, length, _unknown, device_id, stamp = struct.unpack(">HHI4sI", packet[:16])
    if magic != MAGIC:
        raise MiioError("Not a miIO packet")
    return length, device_id, stamp


def decode_payload(crypto: Crypto, packet: bytes) -> dict:
    body = packet[32:]
    if not body:
        return {}
    raw = crypto.decrypt(body).rstrip(b"\x00")
    candidates = (
        raw,
        raw.replace(b',,"otu_stat"', b',"otu_stat"'),
        raw[: raw.rfind(b"\x00")] if b"\x00" in raw else raw,
        raw.replace(b'"value":00', b'"value":0'),
    )
    for cand in candidates:
        try:
            return json.loads(cand.decode("utf-8", errors="replace"))
        except ValueError:
            continue
    raise MiioError("Device sent an unreadable reply")


class MiioDevice:
    """A single device reachable on the LAN with its token."""

    def __init__(self, ip: str, token: str, timeout: float = 2.5, retries: int = 2):
        self.ip = ip
        self.crypto = Crypto(parse_token(token))
        self.timeout = timeout
        self.retries = retries
        self._device_id: bytes | None = None
        self._stamp = 0
        self._stamp_at = 0.0
        self._msg_id = random.randint(1, 9000)
        self._lock = threading.Lock()

    # -- low level -------------------------------------------------------
    def _socket(self) -> socket.socket:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(self.timeout)
        return sock

    def handshake(self) -> int:
        with self._socket() as sock:
            sock.sendto(HELLO, (self.ip, PORT))
            data, _ = sock.recvfrom(4096)
        _length, device_id, stamp = parse_header(data)
        self._device_id = device_id
        self._stamp = stamp
        self._stamp_at = time.monotonic()
        return int.from_bytes(device_id, "big")

    def send(self, method: str, params=None, extra: dict | None = None):
        """extra: more fields for the request itself, e.g. {"sid": ...} for a device behind a gateway."""
        with self._lock:
            last_exc: Exception | None = None
            for attempt in range(self.retries + 1):
                try:
                    return self._send_once(method, params, bump=attempt * 100, extra=extra)
                except (socket.timeout, TimeoutError, ConnectionResetError, OSError) as exc:
                    last_exc = exc
                    self._device_id = None  # force a new handshake
            raise MiioTimeout(f"No reply from {self.ip} ({last_exc})")

    def _send_once(self, method, params, bump=0, extra=None):
        if self._device_id is None or time.monotonic() - self._stamp_at > 120:
            self.handshake()
        self._msg_id = (self._msg_id + 1 + bump) % 2_000_000_000
        msg_id = self._msg_id
        payload = {"id": msg_id, "method": method, "params": [] if params is None else params}
        if extra:
            payload.update(extra)
        stamp = self._stamp + int(time.monotonic() - self._stamp_at) + 1
        packet = build_packet(self.crypto, self._device_id, stamp, payload)
        with self._socket() as sock:
            sock.sendto(packet, (self.ip, PORT))
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout("timed out")
                sock.settimeout(remaining)
                data, _ = sock.recvfrom(16384)
                try:
                    reply = decode_payload(self.crypto, data)
                except ValueError as exc:
                    raise MiioError("Reply could not be decrypted - is the token correct?") from exc
                if isinstance(reply, dict) and reply.get("id") not in (None, msg_id):
                    continue  # a late answer to an older request
                break
        if "error" in reply:
            err = reply["error"]
            if isinstance(err, dict):
                raise MiioDeviceError(err.get("code"), err.get("message"))
            raise MiioDeviceError(-1, str(err))
        return reply.get("result")

    # -- MIoT helpers ----------------------------------------------------
    def miot_get(self, did: str, pairs: list[tuple[int, int]]) -> list[dict]:
        out: list[dict] = []
        for i in range(0, len(pairs), 12):  # many devices reject large batches
            chunk = [{"did": str(did), "siid": s, "piid": p} for s, p in pairs[i:i + 12]]
            out.extend(self.send("get_properties", chunk) or [])
        return out

    def miot_set(self, did: str, siid: int, piid: int, value) -> list[dict]:
        return self.send("set_properties", [{"did": str(did), "siid": siid, "piid": piid, "value": value}])

    def miot_action(self, did: str, siid: int, aiid: int, ins: list) -> dict:
        return self.send("action", {"did": str(did), "siid": siid, "aiid": aiid, "in": ins})


# -- discovery -----------------------------------------------------------
def hello(ip: str, timeout: float = 2.5) -> tuple[int, str | None]:
    """Asks one device for its ID without a token. Devices that are not set up yet (or very old
    firmware) also reveal their token in the reply; returns (device_id, token or None)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(HELLO, (ip, PORT))
        data, _ = sock.recvfrom(4096)
    _length, device_id, _stamp = parse_header(data)
    checksum = data[16:32]
    token = None if checksum in (b"\xff" * 16, b"\x00" * 16) else checksum.hex()
    return int.from_bytes(device_id, "big"), token


def _usable(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return not (ip.is_loopback or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"))


def _local_broadcasts() -> list[str]:
    """Fallback when the real netmasks are unknown: assume /24 on every LAN address.
    (VPN addresses such as Tailscale's 100.x and link-local 169.254.x are skipped.)"""
    targets = {"255.255.255.255"}
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    try:  # the address used for the default route
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    for addr in addrs:
        if not _usable(addr):
            continue
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
        targets.add(str(net.broadcast_address))
    return sorted(targets)


def broadcast_for(addr: str, prefix: int) -> str | None:
    """Directed broadcast address of addr/prefix, or None for addresses not worth scanning."""
    if prefix >= 31 or prefix < 8 or not _usable(addr):
        return None
    return str(ipaddress.ip_network(f"{addr}/{prefix}", strict=False).broadcast_address)


def discover(timeout: float = 4.0, extra_targets: list[str] | None = None,
             targets: list[str] | None = None) -> dict[str, int]:
    """Broadcast a hello and return {ip: device_id} for every miIO device that answers.
    `targets` replaces the guessed broadcast addresses (e.g. ones computed from real netmasks)."""
    found: dict[str, int] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.3)
    targets = list(dict.fromkeys((targets or _local_broadcasts()) + list(extra_targets or [])))
    try:
        end = time.monotonic() + timeout
        next_send = 0.0
        while time.monotonic() < end:
            if time.monotonic() >= next_send:
                for t in targets:
                    try:
                        sock.sendto(HELLO, (t, PORT))
                    except OSError:
                        pass
                next_send = time.monotonic() + 1.2
            try:
                data, (ip, _port) = sock.recvfrom(1024)
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                continue
            try:
                _length, device_id, _stamp = parse_header(data)
            except MiioError:
                continue
            if data == HELLO:
                continue  # our own broadcast echoed back
            found[ip] = int.from_bytes(device_id, "big")
    finally:
        sock.close()
    return found
