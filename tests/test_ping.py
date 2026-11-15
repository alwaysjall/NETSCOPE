import subprocess

import pytest

from netscope import ui
from netscope.modules import ping as pg

LINUX_OUT = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=10.5 ms
64 bytes from 1.1.1.1: icmp_seq=2 ttl=57 time=12.0 ms
64 bytes from 1.1.1.1: icmp_seq=4 ttl=57 time=11.5 ms

--- 1.1.1.1 ping statistics ---
4 packets transmitted, 3 received, 25% packet loss, time 3004ms
"""

WIN_OUT = """Reply from 1.1.1.1: bytes=32 time=15ms TTL=57
Reply from 1.1.1.1: bytes=32 time<1ms TTL=57
Packets: Sent = 4, Received = 2, Lost = 2 (50% loss),
"""

MAC_OUT = """64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=9.876 ms
4 packets transmitted, 4 packets received, 0.0% packet loss
"""


def test_parse_linux():
    p = pg.parse_output(LINUX_OUT)
    assert p["times"] == [10.5, 12.0, 11.5] and (p["sent"], p["received"]) == (4, 3)


def test_parse_windows_and_mac():
    p = pg.parse_output(WIN_OUT)
    assert p["times"] == [15.0, 1.0] and (p["sent"], p["received"]) == (4, 2)
    p = pg.parse_output(MAC_OUT)
    assert p["times"] == [9.876] and (p["sent"], p["received"]) == (4, 4)


def test_compute_stats():
    s = pg.compute_stats([10, 20, 30], 4)
    assert s["loss_pct"] == 25.0 and s["min"] == 10 and s["max"] == 30
    assert s["avg"] == 20 and s["jitter"] == 10
    assert pg.compute_stats([], 4)["loss_pct"] == 100.0
    assert pg.compute_stats([5.0], 1)["jitter"] == 0.0
    assert pg.compute_stats([], 0)["loss_pct"] == 100.0


def test_build_command_per_os():
    assert pg.build_command("ping", "h", 3, 2, "Windows") == ["ping", "-n", "3", "-w", "2000", "h"]
    assert pg.build_command("ping", "h", 3, 2, "Darwin") == ["ping", "-c", "3", "-W", "2000", "h"]
    assert pg.build_command("ping", "h", 3, 2, "Linux") == ["ping", "-c", "3", "-W", "2", "h"]
    assert "-6" in pg.build_command("ping", "2001:db8::1", 1, 1, "Linux")


def test_quality():
    assert pg.quality({"received": 0}) == "down"
    base = {"received": 4, "loss_pct": 0, "avg": 20, "jitter": 2}
    assert pg.quality(base) == "excellent"
    assert pg.quality(dict(base, avg=100)) == "good"
    assert pg.quality(dict(base, loss_pct=10)) == "fair"
    assert pg.quality(dict(base, loss_pct=60)) == "poor"


def test_ping_validation():
    for bad in (0, 51, "x"):
        assert not pg.ping("127.0.0.1", bad)["ok"]
    assert not pg.ping("bad host")["ok"]
    assert not pg.ping("127.0.0.1", 1, method="udp")["ok"]


def test_tcp_ping_open_and_closed(tcp_listener, closed_port):
    r = pg.ping("127.0.0.1:%d" % tcp_listener, 3, 1.0, "tcp")
    assert r["ok"] and r["data"]["stats"]["received"] == 3
    assert r["data"]["method"] == "tcp:%d" % tcp_listener
    # port tertutup (RST) tetap membuktikan host hidup
    r = pg.ping("127.0.0.1:%d" % closed_port, 2, 1.0, "tcp")
    assert r["ok"] and r["data"]["stats"]["received"] == 2


def test_icmp_localhost_or_fallback():
    r = pg.ping("127.0.0.1", 2, 2.0, "auto")
    assert r["ok"], r["error"]
    assert r["data"]["stats"]["received"] == 2


def test_icmp_mocked_success(monkeypatch):
    monkeypatch.setattr(pg, "find_ping", lambda ipv6=False: "/bin/ping")

    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, LINUX_OUT, "")

    monkeypatch.setattr(pg.subprocess, "run", run)
    r = pg.ping("1.1.1.1", 4, 2, "icmp")
    assert r["ok"] and r["data"]["stats"]["loss_pct"] == 25.0 and r["data"]["method"] == "icmp"


def test_icmp_permission_falls_back_to_tcp(monkeypatch, tcp_listener):
    monkeypatch.setattr(pg, "find_ping", lambda ipv6=False: "/bin/ping")
    monkeypatch.setattr(pg.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 2, "", "ping: socket: Operation not permitted"))
    r = pg.ping("127.0.0.1:%d" % tcp_listener, 2, 1, "auto")
    assert r["ok"] and r["data"]["method"].startswith("tcp") and "TCP" in r["data"]["note"]
    r = pg.ping("127.0.0.1", 1, 1, "icmp")
    assert not r["ok"] and "tidak tersedia" in r["error"]


def test_no_binary_falls_back(monkeypatch, tcp_listener):
    monkeypatch.setattr(pg, "find_ping", lambda ipv6=False: None)
    r = pg.ping("127.0.0.1:%d" % tcp_listener, 1, 1, "auto")
    assert r["ok"] and "tidak ditemukan" in r["data"]["note"]


def test_icmp_timeout_and_oserror(monkeypatch):
    monkeypatch.setattr(pg, "find_ping", lambda ipv6=False: "/bin/ping")

    def timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1, output=b"")

    monkeypatch.setattr(pg.subprocess, "run", timeout)
    r = pg.ping("127.0.0.1", 1, 1, "icmp")
    assert not r["ok"]

    def oserr(cmd, **kw):
        raise OSError("exec failed")

    monkeypatch.setattr(pg.subprocess, "run", oserr)
    assert not pg.ping("127.0.0.1", 1, 1, "icmp")["ok"]


def test_sparkline_and_render():
    console = ui.make_console(width=100)
    assert pg.sparkline(console, []) == "-"
    assert len(pg.sparkline(console, [1, 2, 3, 4])) == 4
    assert pg.sparkline(console, [5, 5, 5]) == "\u2581" * 3
    pg.render(console, pg.ping("127.0.0.1", 2, 1, "tcp"))
    pg.render(console, pg.ping("bad host"))
