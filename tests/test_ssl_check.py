from datetime import datetime, timedelta, timezone

import pytest

from netscope import ui
from netscope.modules import ssl_check as sc


# --------------------------------------------------------------- hostname
CERT_SAN = {"subject": ((("commonName", "example.com"),),),
            "subjectAltName": (("DNS", "example.com"), ("DNS", "*.example.org"),
                               ("IP Address", "192.0.2.5"))}


@pytest.mark.parametrize("host,expected", [
    ("example.com", True), ("EXAMPLE.com", True), ("www.example.org", True),
    ("a.b.example.org", False),   # wildcard hanya satu label
    ("example.org", False), ("evil.com", False), ("192.0.2.5", True),
    ("192.0.2.6", False), ("xexample.com", False),
])
def test_hostname_matches(host, expected):
    assert sc.hostname_matches(host, CERT_SAN) is expected


def test_hostname_cn_fallback_when_no_san():
    cert = {"subject": ((("commonName", "legacy.example.com"),),)}
    assert sc.hostname_matches("legacy.example.com", cert)
    assert not sc.hostname_matches("other.example.com", cert)
    assert not sc.hostname_matches("example.com", {})


# ---------------------------------------------------------------- summary
def _cert(days_from_now_start, days_valid, cn="example.com", issuer=None):
    now = datetime.now(timezone.utc)
    fmt = lambda d: d.strftime("%b %d %H:%M:%S %Y GMT")  # noqa: E731
    nb = now + timedelta(days=days_from_now_start)
    na = nb + timedelta(days=days_valid)
    subj = ((("commonName", cn),),)
    return {"subject": subj, "issuer": issuer or ((("commonName", "Test CA"),),),
            "notBefore": fmt(nb), "notAfter": fmt(na), "serialNumber": "AB",
            "subjectAltName": (("DNS", cn),)}


def test_summarize_states():
    assert sc.summarize_cert(_cert(-10, 100), "example.com")["status"] == "valid"
    assert sc.summarize_cert(_cert(-100, 10), "example.com")["status"] == "expired"
    s = sc.summarize_cert(_cert(-60, 70), "example.com")
    assert s["status"] == "expiring_soon" and 0 <= s["days_left"] < 30
    assert sc.summarize_cert(_cert(5, 100), "example.com")["status"] == "not_yet_valid"


def test_summarize_self_signed_and_mismatch():
    same = ((("commonName", "x.com"),),)
    s = sc.summarize_cert(_cert(-1, 90, "x.com", issuer=same), "other.com")
    assert s["self_signed"] and not s["hostname_match"]
    assert sc.summarize_cert({}, "x")["status"] == "unknown"


# ------------------------------------------------------- live (lokal) TLS
def test_untrusted_self_signed_reports_issue(tls_factory):
    srv = tls_factory(cn="localhost", days_valid=90)
    r = sc.inspect("127.0.0.1:%d" % srv.port, timeout=3)
    assert r["ok"], r["error"]
    d = r["data"]
    assert d["verified"] is False and r["healthy"] is False
    assert d["certificate"]["subject_cn"] == "localhost"
    assert d["certificate"]["hostname_match"] is True      # SAN IP 127.0.0.1
    assert d["certificate"]["self_signed"] is True
    assert d["connection"]["protocol"] in ("TLSv1.2", "TLSv1.3")
    assert d["protocols"].get("TLSv1.2") is True
    assert any("self" in i.lower() or "tidak tepercaya" in i.lower() for i in d["issues"])


def test_expired_cert(tls_factory):
    srv = tls_factory(days_valid=5, start_offset_days=-30)
    r = sc.inspect("127.0.0.1:%d" % srv.port, timeout=3, probe_versions=False)
    assert r["ok"]
    assert r["data"]["certificate"]["status"] == "expired"
    assert any("kedaluwarsa" in i for i in r["data"]["issues"])


def test_hostname_mismatch(tls_factory):
    srv = tls_factory(cn="other.example", san_dns=("other.example",), san_ip=())
    r = sc.inspect("127.0.0.1:%d" % srv.port, timeout=3, probe_versions=False)
    assert r["ok"] and r["data"]["certificate"]["hostname_match"] is False
    assert any("tidak cocok" in i for i in r["data"]["issues"])


def test_not_a_tls_server(tcp_listener):
    r = sc.inspect("127.0.0.1:%d" % tcp_listener, timeout=2)
    assert not r["ok"] and r["error"]


def test_connection_refused_and_bad_input(closed_port):
    r = sc.inspect("127.0.0.1:%d" % closed_port, timeout=2)
    assert not r["ok"] and "Koneksi gagal" in r["error"]
    assert not sc.inspect("bad host")["ok"]
    assert not sc.inspect("")["ok"]
    assert not sc.inspect("example.com:99999")["ok"]


def test_dns_failure():
    r = sc.inspect("nonexistent-zzz.invalid", timeout=3)
    assert not r["ok"] and "Resolusi gagal" in r["error"]


def test_render(tls_factory, closed_port):
    console = ui.make_console(width=110)
    srv = tls_factory()
    sc.render(console, sc.inspect("127.0.0.1:%d" % srv.port, timeout=3))
    sc.render(console, sc.inspect("127.0.0.1:%d" % closed_port, timeout=1))
