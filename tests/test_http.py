import pytest
import requests

from netscope import ui
from netscope.modules import http as h


def test_ok_with_security_headers(http_server):
    r = h.check(http_server + "/ok")
    assert r["ok"], r["error"]
    d = r["data"]
    assert d["status"] == 200 and d["redirects"] == 0 and d["server"] == "TestSrv/1.0"
    sec = d["security"]
    by = {i["key"]: i for i in sec["items"]}
    assert by["x-content-type-options"]["status"] == "present"
    assert by["content-security-policy"]["status"] == "present"
    # frame-ancestors di CSP dianggap setara X-Frame-Options
    assert by["x-frame-options"]["status"] == "present"
    # HSTS tidak berlaku untuk http:// -> tidak dihitung
    assert by["strict-transport-security"]["status"] == "n/a"
    assert sec["info_leak"] == {"server": "TestSrv/1.0"}
    assert 0 <= sec["score"] <= 100


def test_redirect_chain(http_server):
    r = h.check(http_server + "/redir2")
    assert r["ok"]
    d = r["data"]
    assert [x["status"] for x in d["chain"]] == [301, 302, 200]
    assert d["redirects"] == 2 and d["final_url"].endswith("/ok")


def test_no_follow(http_server):
    r = h.check(http_server + "/redir", follow=False)
    assert r["data"]["status"] == 302 and r["data"]["redirects"] == 0


def test_redirect_loop(http_server):
    r = h.check(http_server + "/loop")
    assert not r["ok"] and "loop" in r["error"].lower()


def test_redirect_to_unsupported_scheme(http_server):
    r = h.check(http_server + "/badscheme")
    assert not r["ok"] and "tidak valid" in r["error"]


def test_error_statuses(http_server):
    r = h.check(http_server + "/missing")
    assert not r["ok"] and r["data"]["status"] == 404 and "404" in r["error"]
    r = h.check(http_server + "/boom")
    assert not r["ok"] and r["data"]["status"] == 500


def test_head_method(http_server):
    r = h.check(http_server + "/ok", method="HEAD")
    assert r["ok"] and r["data"]["method"] == "HEAD"
    assert not h.check(http_server, method="POST")["ok"]


def test_invalid_urls():
    for bad in ("", "ftp://x.com", "http://", "bad host", "file:///etc/passwd"):
        r = h.check(bad)
        assert not r["ok"] and r["error"]


def test_connection_refused(closed_port):
    r = h.check("http://127.0.0.1:%d/" % closed_port, timeout=2)
    assert not r["ok"] and "Koneksi gagal" in r["error"]
    assert "HTTPConnectionPool" not in r["error"] or len(r["error"]) < 400


def test_dns_failure_message():
    r = h.check("http://nonexistent-zzz.invalid/", timeout=3)
    assert not r["ok"] and "di-resolve" in r["error"]


def test_grade_and_audit_pure():
    assert [h.grade_for(s) for s in (100, 90, 80, 60, 45, 10)] == ["A", "A", "B", "C", "D", "F"]
    full = {"Strict-Transport-Security": "max-age=1", "Content-Security-Policy": "default-src 'self'",
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer", "Permissions-Policy": "geolocation=()"}
    a = h.audit_headers(full, True)
    assert a["score"] == 100 and a["grade"] == "A" and a["info_leak"] == {}
    empty = h.audit_headers({}, True)
    assert empty["score"] == 0 and empty["grade"] == "F"
    # http: HSTS dikecualikan sehingga skor tetap bisa 100
    assert h.audit_headers({k: v for k, v in full.items() if k != "Strict-Transport-Security"}, False)["score"] == 100


def test_ssl_error_message(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.SSLError("certificate verify failed: self signed")

    monkeypatch.setattr(h, "_one_request", boom)
    r = h.check("https://example.com")
    assert not r["ok"] and "SSL" in r["error"]


def test_timeouts(monkeypatch):
    monkeypatch.setattr(h, "_one_request", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ConnectTimeout()))
    assert "Timeout" in h.check("https://example.com")["error"]
    monkeypatch.setattr(h, "_one_request", lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.ReadTimeout()))
    assert "read timeout" in h.check("https://example.com")["error"]


def test_short_error_messages():
    assert "DNS" in h._short(Exception("NameResolutionError: x"))
    assert "ditolak" in h._short(Exception("[Errno 111] Connection refused"))
    assert len(h._short(Exception("x" * 500))) <= 163


def test_markup_in_headers_is_not_interpreted(http_server):
    """Nilai dari server tidak boleh diinterpretasi sebagai markup Rich."""
    console = ui.make_console(width=100)
    res = h.check(http_server + "/ok")
    res["data"]["server"] = "[bold red]EVIL[/bold red] [link=http://x]click[/link]"
    res["data"]["security"]["info_leak"] = {"server": "[red]x[/red]"}
    with console.capture() as cap:
        h.render(console, res)
    out = cap.get()
    assert "[bold red]EVIL[/bold red]" in out and "[link=http://x]" in out


def test_render_failure_paths(http_server):
    console = ui.make_console(width=100)
    h.render(console, h.check(http_server + "/missing"))
    h.render(console, h.check("bad host"))
    h.render(console, h.check(http_server + "/redir"))
