import socket

import pytest
import requests

from netscope.modules import network as net


def test_local_info_shape():
    r = net.local_info()
    assert r["ok"] and r["module"] == "network.local"
    d = r["data"]
    for key in ("hostname", "platform", "os", "python", "interfaces", "ipv6_supported"):
        assert key in d
    assert isinstance(d["interfaces"], list)


def test_resolve_localhost_and_ip():
    r = net.resolve_host("localhost")
    assert r["ok"] and (r["data"]["ipv4"] or r["data"]["ipv6"])
    r = net.resolve_host("127.0.0.1")
    assert r["ok"] and r["data"]["ipv4"] == ["127.0.0.1"]


def test_resolve_invalid_and_unresolvable():
    assert not net.resolve_host("bad host")["ok"]
    r = net.resolve_host("nonexistent-zzz.invalid")
    assert not r["ok"] and "Resolusi gagal" in r["error"]


def test_check_ports_open_closed(tcp_listener, closed_port):
    r = net.check_ports("127.0.0.1", [tcp_listener, closed_port], timeout=1.0)
    assert r["ok"]
    states = {p["port"]: p["state"] for p in r["data"]["ports"]}
    assert states[tcp_listener] == "open"
    assert states[closed_port] == "closed"
    assert r["data"]["counts"]["open"] == 1 and r["data"]["counts"]["closed"] == 1
    open_entry = next(p for p in r["data"]["ports"] if p["state"] == "open")
    assert open_entry["latency_ms"] is not None


def test_check_ports_sorted_and_errors(tcp_listener):
    ports = [tcp_listener + 0, 1]
    r = net.check_ports("127.0.0.1", ports, timeout=0.5)
    assert [p["port"] for p in r["data"]["ports"]] == sorted(ports)
    assert not net.check_ports("bad host", [80])["ok"]
    assert not net.check_ports("127.0.0.1", [])["ok"]
    assert not net.check_ports("nonexistent-zzz.invalid", [80], timeout=1)["ok"]


def test_service_name():
    assert net.service_name(443) == "https"
    assert net.service_name(54321) == "-"


def test_public_ip_ok(monkeypatch):
    class Resp:
        text = "203.0.113.7\n"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(net.requests, "get", lambda *a, **k: Resp())
    r = net.public_ip()
    assert r["ok"] and r["data"]["ip"] == "203.0.113.7"


def test_public_ip_failures(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no net")

    monkeypatch.setattr(net.requests, "get", boom)
    r = net.public_ip()
    assert not r["ok"] and "ConnectionError" in r["error"]

    class Junk:
        text = "<html>hacked</html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(net.requests, "get", lambda *a, **k: Junk())
    r = net.public_ip()
    assert not r["ok"] and "tidak valid" in r["error"]


def test_probe_unreachable_is_filtered(monkeypatch):
    def fail(*a, **k):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr(net.socket, "create_connection", fail)
    assert net._probe_port("192.0.2.1", 80, 0.1)["state"] == "filtered"


def test_render_all(capsys):
    from netscope import ui
    console = ui.make_console(width=100, force_terminal=False)
    net.render_local(console, net.local_info())
    net.render_resolve(console, net.resolve_host("localhost"))
    net.render_resolve(console, net.resolve_host("bad host"))
    net.render_ports(console, net.check_ports("127.0.0.1", [1], timeout=0.3))
    net.render_public_ip(console, {"ok": False, "error": "x", "data": {}})
    net.render_public_ip(console, {"ok": True, "error": None, "data": {"ip": "1.2.3.4", "source": "s"}})
