"""Encrypts secrets (device tokens, the Mi account session) before they are written to disk.

On Windows this uses DPAPI: the data can only be decrypted by the same Windows user on the
same PC, so a copied config.json is useless to anyone else. Elsewhere values stay readable."""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"
_ENTROPY = b"XiControl secret v1"


class SecretError(Exception):
    """A stored secret could not be decrypted (e.g. the config was copied from another PC)."""


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob),
                                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
                                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _UI_FORBIDDEN = 0x01

    def _blob(data: bytes):
        buf = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(fn, data: bytes, *extra) -> bytes:
        inp, _keep1 = _blob(data)
        ent, _keep2 = _blob(_ENTROPY)
        out = _Blob()
        if not fn(ctypes.byref(inp), *extra, ctypes.byref(ent), None, None, _UI_FORBIDDEN, ctypes.byref(out)):
            raise SecretError(f"Windows could not process the secret (error {ctypes.get_last_error()})")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(ctypes.cast(out.pbData, ctypes.c_void_p))

    def _protect(data: bytes) -> bytes:
        return _call(_crypt32.CryptProtectData, data, "XiControl")

    def _unprotect(data: bytes) -> bytes:
        return _call(_crypt32.CryptUnprotectData, data, None)

    AVAILABLE = True
else:  # pragma: no cover - the app targets Windows
    AVAILABLE = False


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(text: str) -> str:
    """'dpapi:…' for a non-empty string on Windows; the value unchanged otherwise."""
    if not text or not AVAILABLE or is_encrypted(text):
        return text
    return PREFIX + base64.b64encode(_protect(text.encode("utf-8"))).decode()


def decrypt(value: str) -> str:
    """Plain text for a value written by encrypt(). Raises SecretError if it can't be read here."""
    if not is_encrypted(value):
        return value
    if not AVAILABLE:
        raise SecretError("Encrypted settings can only be read on Windows")
    try:
        raw = base64.b64decode(value[len(PREFIX):])
    except ValueError as exc:
        raise SecretError("Damaged secret") from exc
    return _unprotect(raw).decode("utf-8")
