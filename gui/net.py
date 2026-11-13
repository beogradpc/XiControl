"""This PC's real networks (with their actual netmasks): broadcast addresses for discovery, and the
list of directly connected networks used to decide whether a device can be reached over the LAN."""
from __future__ import annotations

import ipaddress

from xc.miio import broadcast_for


def _entries():
    """(address, prefix length) of every running, non-loopback, non-VPN-tunnel IPv4 interface."""
    from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
    flag = QNetworkInterface.InterfaceFlag
    for itf in QNetworkInterface.allInterfaces():
        f = itf.flags()
        if not (f & flag.IsUp and f & flag.IsRunning) or f & flag.IsLoopBack or f & flag.IsPointToPoint:
            continue
        for e in itf.addressEntries():
            ip = e.ip()
            if ip.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                yield ip.toString(), e.prefixLength()


def broadcast_targets() -> list[str] | None:
    """None if the interfaces can't be read - discovery then guesses /24 networks itself."""
    try:
        out = ["255.255.255.255"]
        for addr, prefix in _entries():
            b = broadcast_for(addr, prefix)
            if b and b not in out:
                out.append(b)
        return out
    except Exception:  # noqa: BLE001
        return None


def local_networks() -> list | None:
    """The IPv4 networks this PC is directly connected to (as ipaddress networks), or None if unknown.
    Point-to-point links (/31, /32 - e.g. Tailscale) and link-local addresses don't count."""
    try:
        nets = []
        for addr, prefix in _entries():
            if not 8 <= prefix < 31:
                continue
            net = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            if net.is_link_local or net in nets:
                continue
            nets.append(net)
        return nets
    except Exception:  # noqa: BLE001
        return None
