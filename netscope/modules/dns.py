"""Diagnostik DNS memakai dnspython.

Catatan: file ini bernama ``dns.py`` di dalam paket ``netscope.modules``.
Karena memakai ``from __future__ import absolute_import`` implisit (Python 3),
``import dns.resolver`` di bawah selalu menunjuk ke paket dnspython, bukan
modul ini.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import dns.exception
import dns.resolver
import dns.reversename
from rich.console import Console
from rich.text import Text

from netscope import ui
from netscope.utils import TargetError, is_ip, make_result, validate_host

MODULE = "dns"
RECORD_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA", "CAA"]
DEFAULT_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "SOA"]
MAX_TXT_LEN = 300


def _make_resolver(timeout: float, server: str = "") -> dns.resolver.Resolver:
    """Buat resolver. Bila resolv.conf tidak ada (mis. Termux) pakai fallback publik."""
    try:
        resolver = dns.resolver.Resolver(configure=True)
        if not resolver.nameservers:
            raise dns.resolver.NoResolverConfiguration("tanpa nameserver")
    except (dns.resolver.NoResolverConfiguration, OSError):
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ["1.1.1.1", "8.8.8.8"]
    if server:
        resolver.nameservers = [server]
    resolver.timeout = timeout
    resolver.lifetime = timeout
    return resolver


def _fmt_rdata(rtype: str, rdata: Any) -> Dict[str, Any]:
    """Ubah satu rdata menjadi dict ringkas yang aman ditampilkan."""
    if rtype == "MX":
        return {"value": str(rdata.exchange).rstrip("."), "priority": int(rdata.preference)}
    if rtype == "SOA":
        return {"value": "%s %s" % (str(rdata.mname).rstrip("."), str(rdata.rname).rstrip(".")),
                "serial": int(rdata.serial), "refresh": int(rdata.refresh),
                "retry": int(rdata.retry), "expire": int(rdata.expire),
                "minimum": int(rdata.minimum)}
    if rtype == "TXT":
        joined = b"".join(rdata.strings).decode("utf-8", errors="replace")
        if len(joined) > MAX_TXT_LEN:
            joined = joined[:MAX_TXT_LEN] + "..."
        return {"value": joined}
    if rtype == "CAA":
        return {"value": "%d %s %s" % (rdata.flags, rdata.tag.decode("ascii", "replace"),
                                       rdata.value.decode("utf-8", "replace"))}
    return {"value": str(rdata.to_text()).rstrip(".")}


def query(domain: str, rtype: str, timeout: float = 8.0, server: str = "") -> Dict[str, Any]:
    """Query satu tipe record. Mengembalikan dict hasil standar."""
    start = time.perf_counter()
    rtype = str(rtype).upper().strip()
    if rtype not in RECORD_TYPES + ["PTR"]:
        return make_result(MODULE, str(domain), False,
                           error="Tipe record tidak didukung: %s" % rtype)
    try:
        host = validate_host(domain)
    except TargetError as exc:
        return make_result(MODULE, str(domain), False, error=str(exc))

    resolver = _make_resolver(timeout, server)
    records: List[Dict[str, Any]] = []
    status, error = "ok", None
    ttl: Optional[int] = None
    try:
        answer = resolver.resolve(host, rtype, raise_on_no_answer=True)
        ttl = int(answer.rrset.ttl) if answer.rrset is not None else None
        records = [_fmt_rdata(rtype, r) for r in answer]
    except dns.resolver.NoAnswer:
        status = "no_records"
    except dns.resolver.NXDOMAIN:
        status, error = "error", "Domain tidak ada (NXDOMAIN)."
    except dns.resolver.NoNameservers:
        status, error = "error", "Semua nameserver gagal merespons (SERVFAIL/refused)."
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        status, error = "timeout", "Timeout saat query DNS."
    except dns.exception.DNSException as exc:
        status, error = "error", "Kesalahan DNS: %s" % exc.__class__.__name__
    except OSError as exc:
        status, error = "error", "Kesalahan jaringan: %s" % (exc.strerror or exc)

    data = {"domain": host, "type": rtype, "status": status, "ttl": ttl,
            "records": records, "nameserver": ",".join(resolver.nameservers)}
    ok = status in ("ok", "no_records")
    return make_result(MODULE, host, ok, data, error=error,
                       duration=time.perf_counter() - start)


def lookup(domain: str, types: Optional[List[str]] = None, timeout: float = 8.0,
           server: str = "") -> Dict[str, Any]:
    """Query banyak tipe record sekaligus."""
    start = time.perf_counter()
    try:
        host = validate_host(domain)
    except TargetError as exc:
        return make_result(MODULE, str(domain), False, error=str(exc))
    if is_ip(host):
        return make_result(MODULE, host, False,
                           error="Target adalah IP; gunakan reverse lookup (menu DNS > Reverse).")
    wanted = [t.upper() for t in (types or DEFAULT_TYPES)]
    bad = [t for t in wanted if t not in RECORD_TYPES]
    if bad:
        return make_result(MODULE, host, False,
                           error="Tipe record tidak didukung: %s" % ", ".join(bad))

    answers: Dict[str, Any] = {}
    fatal: Optional[str] = None
    for rtype in wanted:
        res = query(host, rtype, timeout, server)
        answers[rtype] = res["data"] if res["data"] else {"status": "error", "records": []}
        if res["error"]:
            answers[rtype]["error"] = res["error"]
            if "NXDOMAIN" in res["error"]:
                fatal = res["error"]
                break
    found = sum(len(v.get("records", [])) for v in answers.values())
    ok = fatal is None and (found > 0 or any(v.get("status") == "no_records"
                                             for v in answers.values()))
    data = {"domain": host, "answers": answers, "total_records": found,
            "email_security": _email_security(host, timeout, server) if ok else {}}
    return make_result(MODULE, host, ok, data, error=fatal or (None if ok else
                       "Tidak ada record yang dapat diambil."),
                       duration=time.perf_counter() - start)


def _email_security(host: str, timeout: float, server: str) -> Dict[str, str]:
    """Deteksi SPF dan DMARC dari TXT record (hanya baca, tanpa mengirim email)."""
    out = {"spf": "missing", "dmarc": "missing"}
    spf = query(host, "TXT", timeout, server)
    for rec in spf["data"].get("records", []) if spf["data"] else []:
        if rec["value"].lower().startswith("v=spf1"):
            out["spf"] = "present"
            break
    dmarc = query("_dmarc." + host, "TXT", timeout, server)
    for rec in dmarc["data"].get("records", []) if dmarc["data"] else []:
        if rec["value"].lower().startswith("v=dmarc1"):
            out["dmarc"] = "present"
            break
    return out


def reverse(ip: str, timeout: float = 8.0, server: str = "") -> Dict[str, Any]:
    """Reverse DNS (PTR) untuk alamat IP."""
    start = time.perf_counter()
    text = str(ip).strip()
    if not is_ip(text):
        return make_result(MODULE + ".reverse", text, False,
                           error="'%s' bukan alamat IP yang valid." % text)
    resolver = _make_resolver(timeout, server)
    name = dns.reversename.from_address(text)
    names: List[str] = []
    error = None
    try:
        answer = resolver.resolve(name, "PTR")
        names = [str(r.target).rstrip(".") for r in answer]
    except dns.resolver.NoAnswer:
        error = "Tidak ada PTR record untuk IP ini."
    except dns.resolver.NXDOMAIN:
        error = "Tidak ada PTR record untuk IP ini (NXDOMAIN)."
    except (dns.exception.Timeout, dns.resolver.LifetimeTimeout):
        error = "Timeout saat query DNS."
    except dns.exception.DNSException as exc:
        error = "Kesalahan DNS: %s" % exc.__class__.__name__
    except OSError as exc:
        error = "Kesalahan jaringan: %s" % (exc.strerror or exc)
    return make_result(MODULE + ".reverse", text, bool(names),
                       {"ip": text, "names": names}, error=error,
                       duration=time.perf_counter() - start)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def render(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "DNS Lookup", result["target"])
    ui.result_header(console, result)
    if not result["ok"]:
        ui.render_failure(console, result, "Cek ejaan domain atau coba dns_server lain (config).")
        return
    d = result["data"]
    rows = []
    for rtype, ans in d["answers"].items():
        recs = ans.get("records", [])
        if not recs:
            label = "tidak ada" if ans.get("status") in ("no_records", None) else ans.get("error", "gagal")
            rows.append((rtype, Text(label, style="muted"), "-", "-"))
            continue
        for rec in recs:
            extra = ""
            if "priority" in rec:
                extra = "prio %d" % rec["priority"]
            rows.append((rtype, rec["value"], extra or "-", ans.get("ttl")))
    ui.data_table(console, ["Tipe", "Nilai", "Info", "TTL"], rows)
    sec = d.get("email_security") or {}
    if sec:
        ui.print_kv(console, [
            ("SPF", Text(sec["spf"].upper(), style=ui.state_style(sec["spf"]))),
            ("DMARC", Text(sec["dmarc"].upper(), style=ui.state_style(sec["dmarc"]))),
        ], "Keamanan email (indikatif)")


def render_reverse(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Reverse DNS", result["target"])
    ui.result_header(console, result)
    if not result["ok"]:
        ui.render_failure(console, result)
        return
    ui.print_kv(console, [("PTR", n) for n in result["data"]["names"]], "Hasil")
