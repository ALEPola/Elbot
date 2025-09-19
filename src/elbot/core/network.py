"""Network-related helpers for Elbot."""

from __future__ import annotations

import errno
import socket
from typing import Iterable, List


LOCAL_IPV4 = "127.0.0.1"
LOCAL_IPV6 = "::1"


def _is_port_open(port: int, *, timeout: float = 0.3) -> bool:
    candidates = [(socket.AF_INET, LOCAL_IPV4)]
    if socket.has_ipv6:
        candidates.append((socket.AF_INET6, LOCAL_IPV6))

    for family, host in candidates:
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            sock.settimeout(timeout)
        except OSError:
            continue
        try:
            destination = (host, port) if family == socket.AF_INET else (host, port, 0, 0)
            try:
                sock.connect(destination)
            except OSError as exc:
                err = getattr(exc, "errno", None)
                if err in (errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EADDRNOTAVAIL):
                    continue
                winerr = getattr(exc, "winerror", None)
                if winerr in (10061, 10060, 10051):  # Windows equivalents of conn refused/unreachable
                    continue
                continue
            else:
                return True
        finally:
            try:
                sock.close()
            except OSError:
                pass
    return False


def detect_port_conflicts(ports: Iterable[int]) -> List[int]:
    """Return the subset of *ports* that appear to be in use on localhost."""

    conflicts: list[int] = []
    for port in ports:
        if _is_port_open(port):
            conflicts.append(port)
    return conflicts
