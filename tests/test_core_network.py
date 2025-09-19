from __future__ import annotations

import socket

from elbot.core import network


def _reserved_listener() -> tuple[socket.socket, int]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((network.LOCAL_IPV4, 0))
    sock.listen(1)
    return sock, sock.getsockname()[1]


def test_detect_port_conflicts_finds_open_port():
    listener, port = _reserved_listener()
    try:
        conflicts = network.detect_port_conflicts([port, port + 1])
    finally:
        listener.close()
    assert port in conflicts


def test_detect_port_conflicts_empty_when_ports_free():
    conflicts = network.detect_port_conflicts([65000, 65001])
    assert conflicts == []
