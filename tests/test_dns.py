"""Tes DNS dengan resolver palsu (tanpa jaringan)."""

import dns.exception
import dns.name
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.resolver
import pytest

from netscope import ui
from netscope.modules import dns as dnsmod


def _answer(rtype, texts, ttl=300):
    rrset = dns.rrset.from_text("example.com.", ttl, "IN", rtype, *texts)

    class A(list):
        pass

    ans = A(list(rrset))
    ans.rrset = rrset
    return ans


class FakeResolver:
    def __init__(self, table):
        self.table = table
        self.nameservers = ["9.9.9.9"]
        self.timeout = self.lifetime = 1

    def resolve(self, name, rtype, raise_on_no_answer=True):
        key = (str(name).rstrip(".").lower(), str(rtype))
        val = self.table.get(key, dns.resolver.NoAnswer())
        if isinstance(val, Exception):
            raise val
        return val


@pytest.fixture
def fake(monkeypatch):
    table = {
        ("example.com", "A"): _answer("A", ["192.0.2.1", "192.0.2.2"]),
        ("example.com", "MX"): _answer("MX", ["10 mail.example.com."]),
        ("example.com", "TXT"): _answer("TXT", ['"v=spf1 -all"', '"other"']),
        ("_dmarc.example.com", "TXT"): _answer("TXT", ['"v=DMARC1; p=none"']),
        ("example.com", "SOA"): _answer("SOA", ["ns.example.com. admin.example.com. 1 7200 3600 1209600 300"]),
        ("gone.invalid", "A"): dns.resolver.NXDOMAIN(),
        ("slow.example", "A"): dns.resolver.LifetimeTimeout(timeout=1, errors=[]),
        ("broken.example", "A"): dns.resolver.NoNameservers(),
    }
    monkeypatch.setattr(dnsmod, "_make_resolver", lambda timeout, server="": FakeResolver(table))
    return table


def test_query_a(fake):
    r = dnsmod.query("example.com", "A")
    assert r["ok"] and [x["value"] for x in r["data"]["records"]] == ["192.0.2.1", "192.0.2.2"]
    assert r["data"]["ttl"] == 300


def test_query_mx_soa_txt(fake):
    mx = dnsmod.query("example.com", "MX")["data"]["records"][0]
    assert mx == {"value": "mail.example.com", "priority": 10}
    soa = dnsmod.query("example.com", "SOA")["data"]["records"][0]
    assert soa["serial"] == 1 and soa["minimum"] == 300
    txt = dnsmod.query("example.com", "TXT")["data"]["records"]
    assert txt[0]["value"] == "v=spf1 -all"


def test_query_no_answer_is_ok(fake):
    r = dnsmod.query("example.com", "CAA")
    assert r["ok"] and r["data"]["status"] == "no_records" and r["data"]["records"] == []


def test_query_errors(fake):
    r = dnsmod.query("gone.invalid", "A")
    assert not r["ok"] and "NXDOMAIN" in r["error"]
    r = dnsmod.query("slow.example", "A")
    assert not r["ok"] and "Timeout" in r["error"] and r["data"]["status"] == "timeout"
    r = dnsmod.query("broken.example", "A")
    assert not r["ok"] and "nameserver" in r["error"]


def test_query_invalid_input(fake):
    assert not dnsmod.query("bad host", "A")["ok"]
    assert "tidak didukung" in dnsmod.query("example.com", "ZZZ")["error"]


def test_lookup_full_with_email_security(fake):
    r = dnsmod.lookup("example.com")
    assert r["ok"]
    assert r["data"]["total_records"] >= 5
    assert r["data"]["email_security"] == {"spf": "present", "dmarc": "present"}


def test_lookup_nxdomain_stops(fake):
    r = dnsmod.lookup("gone.invalid", ["A", "MX"])
    assert not r["ok"] and "NXDOMAIN" in r["error"]


def test_lookup_rejects_ip_and_bad_types(fake):
    assert "reverse" in dnsmod.lookup("1.1.1.1")["error"].lower()
    assert not dnsmod.lookup("example.com", ["A", "BOGUS"])["ok"]
    assert not dnsmod.lookup("bad host")["ok"]


def test_txt_truncated(monkeypatch):
    long = "x" * 500
    # satu record TXT yang terdiri dari dua potongan string (2 x 250 karakter)
    table = {("example.com", "TXT"): _answer("TXT", ['"%s" "%s"' % (long[:250], long[250:])])}
    monkeypatch.setattr(dnsmod, "_make_resolver", lambda t, s="": FakeResolver(table))
    v = dnsmod.query("example.com", "TXT")["data"]["records"][0]["value"]
    assert len(v) == dnsmod.MAX_TXT_LEN + 3 and v.endswith("...")


def test_reverse(monkeypatch):
    ans = [type("P", (), {"target": dns.name.from_text("one.one.one.one.")})()]
    table = {("1.1.1.1.in-addr.arpa", "PTR"): ans}
    monkeypatch.setattr(dnsmod, "_make_resolver", lambda t, s="": FakeResolver(table))
    r = dnsmod.reverse("1.1.1.1")
    assert r["ok"] and r["data"]["names"] == ["one.one.one.one"]
    r = dnsmod.reverse("8.8.8.8")
    assert not r["ok"] and "PTR" in r["error"]
    assert not dnsmod.reverse("not-an-ip")["ok"]


def test_make_resolver_fallback(monkeypatch):
    def broken(*a, **k):
        raise dns.resolver.NoResolverConfiguration("none")

    real = dns.resolver.Resolver
    calls = {"n": 0}

    def factory(configure=True):
        calls["n"] += 1
        if configure:
            broken()
        return real(configure=False)

    monkeypatch.setattr(dnsmod.dns.resolver, "Resolver", factory)
    res = dnsmod._make_resolver(2.0)
    assert res.nameservers == ["1.1.1.1", "8.8.8.8"]
    res = dnsmod._make_resolver(2.0, "9.9.9.9")
    assert res.nameservers == ["9.9.9.9"] and res.lifetime == 2.0


def test_render(fake):
    console = ui.make_console(width=100)
    dnsmod.render(console, dnsmod.lookup("example.com"))
    dnsmod.render(console, dnsmod.lookup("gone.invalid", ["A"]))
    dnsmod.render_reverse(console, {"ok": False, "error": "x", "target": "1.1.1.1",
                                    "module": "dns.reverse", "duration_ms": 1, "data": {}})
