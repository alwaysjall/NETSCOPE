"""HTTP checker: status, latensi, rantai redirect, header keamanan.

Redirect diikuti secara manual (maks ``MAX_REDIRECTS``) agar setiap hop
tercatat. Hanya skema http/https yang diikuti. Body respons tidak diunduh
(``stream=True``), sehingga aman untuk file besar.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from rich.console import Console
from rich.text import Text

from netscope import ui
from netscope.utils import TargetError, fmt_ms, make_result, normalize_url

MODULE = "http"
MAX_REDIRECTS = 10
REDIRECT_CODES = (301, 302, 303, 307, 308)

#: header -> (judul, bobot). Bobot menentukan skor audit (total 100).
SECURITY_HEADERS = {
    "strict-transport-security": ("HSTS", 25),
    "content-security-policy": ("CSP", 25),
    "x-content-type-options": ("X-Content-Type-Options", 15),
    "x-frame-options": ("X-Frame-Options", 15),
    "referrer-policy": ("Referrer-Policy", 10),
    "permissions-policy": ("Permissions-Policy", 10),
}


def grade_for(score: int) -> str:
    for threshold, letter in ((90, "A"), (75, "B"), (60, "C"), (40, "D")):
        if score >= threshold:
            return letter
    return "F"


def audit_headers(headers: Dict[str, str], is_https: bool = True) -> Dict[str, Any]:
    """Nilai header keamanan. ``headers`` boleh case-insensitive atau tidak."""
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    # CSP dengan frame-ancestors setara dengan X-Frame-Options
    csp = lower.get("content-security-policy", "")
    items: List[Dict[str, Any]] = []
    score = 0
    for key, (title, weight) in SECURITY_HEADERS.items():
        value = lower.get(key)
        applicable = not (key == "strict-transport-security" and not is_https)
        present = value is not None
        if key == "x-frame-options" and not present and "frame-ancestors" in csp.lower():
            present, value = True, "(via CSP frame-ancestors)"
        if not applicable:
            weight_eff, status = 0, "n/a"
        else:
            weight_eff, status = weight, ("present" if present else "missing")
        if applicable and present:
            score += weight_eff
        items.append({"header": title, "key": key, "status": status,
                      "value": value if present else None, "weight": weight_eff})
    max_score = sum(i["weight"] for i in items) or 1
    pct = int(round(score * 100.0 / max_score))
    leaks = {k: lower[k] for k in ("server", "x-powered-by", "x-aspnet-version") if k in lower}
    return {"score": pct, "grade": grade_for(pct), "items": items, "info_leak": leaks}


def _one_request(session: requests.Session, method: str, url: str, timeout: float,
                 verify: bool) -> requests.Response:
    return session.request(method, url, timeout=timeout, allow_redirects=False,
                           stream=True, verify=verify)


def check(url: str, timeout: float = 8.0, method: str = "GET", verify: bool = True,
          user_agent: str = "Noctura-NETSCOPE", follow: bool = True) -> Dict[str, Any]:
    """Periksa URL. Mengembalikan dict hasil standar."""
    start = time.perf_counter()
    method = str(method).upper()
    if method not in ("GET", "HEAD"):
        return make_result(MODULE, str(url), False, error="Metode harus GET atau HEAD.")
    try:
        current = normalize_url(url)
    except TargetError as exc:
        return make_result(MODULE, str(url), False, error=str(exc))

    chain: List[Dict[str, Any]] = []
    final_resp: Optional[requests.Response] = None
    error: Optional[str] = None
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "*/*"})
    first_url = current
    try:
        for hop in range(MAX_REDIRECTS + 1):
            t0 = time.perf_counter()
            try:
                resp = _one_request(session, method, current, timeout, verify)
            except requests.exceptions.SSLError as exc:
                error = "Verifikasi SSL gagal: %s" % _short(exc)
                break
            except requests.exceptions.ConnectTimeout:
                error = "Timeout saat menghubungi server."
                break
            except requests.exceptions.ReadTimeout:
                error = "Server tidak merespons tepat waktu (read timeout)."
                break
            except requests.exceptions.ConnectionError as exc:
                error = "Koneksi gagal: %s" % _short(exc)
                break
            except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema,
                    requests.exceptions.InvalidSchema) as exc:
                error = "URL tidak valid: %s" % _short(exc)
                break
            except requests.exceptions.RequestException as exc:
                error = "Permintaan gagal: %s" % _short(exc)
                break
            elapsed = (time.perf_counter() - t0) * 1000.0
            chain.append({"url": current, "status": resp.status_code,
                          "reason": resp.reason or "", "time_ms": round(elapsed, 1)})
            location = resp.headers.get("Location")
            if follow and resp.status_code in REDIRECT_CODES and location:
                nxt = urljoin(current, location)
                resp.close()
                try:
                    current = normalize_url(nxt)
                except TargetError as exc:
                    error = "Redirect ke URL tidak valid/didukung: %s" % exc
                    break
                if any(h["url"] == current for h in chain):
                    error = "Redirect loop terdeteksi."
                    break
                if hop == MAX_REDIRECTS:
                    error = "Terlalu banyak redirect (>%d)." % MAX_REDIRECTS
                    break
                continue
            final_resp = resp
            break
    finally:
        pass

    data: Dict[str, Any] = {"url": first_url, "method": method, "chain": chain,
                            "redirects": max(0, len(chain) - 1)}
    if final_resp is not None:
        try:
            headers = {k: v for k, v in final_resp.headers.items()}
            final_url = chain[-1]["url"]
            data.update({
                "final_url": final_url,
                "status": final_resp.status_code,
                "reason": final_resp.reason or "",
                "http_version": {10: "HTTP/1.0", 11: "HTTP/1.1"}.get(
                    getattr(final_resp.raw, "version", 11), "HTTP/1.1"),
                "server": headers.get("Server"),
                "content_type": headers.get("Content-Type"),
                "content_length": headers.get("Content-Length"),
                "headers": headers,
                "security": audit_headers(headers, final_url.lower().startswith("https://")),
                "total_ms": round((time.perf_counter() - start) * 1000.0, 1),
            })
        finally:
            final_resp.close()
        ok = final_resp.status_code < 400
        if not ok:
            error = "Server merespons HTTP %d %s." % (final_resp.status_code, final_resp.reason or "")
    else:
        ok = False
        if chain and error is None:
            error = "Tidak ada respons akhir."
    session.close()
    return make_result(MODULE, first_url, ok, data, error=error,
                       duration=time.perf_counter() - start)


def _short(exc: Exception, limit: int = 160) -> str:
    """Pesan error ringkas & ramah; sembunyikan detail internal urllib3."""
    text = " ".join(str(exc).split())
    low = text.lower()
    if "nameresolutionerror" in low or "name or service not known" in low \
            or "temporary failure in name resolution" in low \
            or "nodename nor servname" in low or "getaddrinfo failed" in low:
        return "nama host tidak dapat di-resolve (DNS)"
    if "connection refused" in low:
        return "koneksi ditolak oleh server"
    if "network is unreachable" in low or "no route to host" in low:
        return "jaringan tidak dapat dijangkau"
    if "connection reset" in low:
        return "koneksi direset oleh server"
    return text if len(text) <= limit else text[:limit] + "..."


def _status_style(code: int) -> str:
    if code < 300:
        return "ok"
    if code < 400:
        return "warn"
    return "bad"


def render(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "HTTP Check", result["target"])
    ui.result_header(console, result)
    d = result["data"]
    if d.get("chain"):
        rows = [(i + 1, Text("%d" % h["status"], style=_status_style(h["status"])),
                 h["url"], fmt_ms(h["time_ms"])) for i, h in enumerate(d["chain"])]
        ui.data_table(console, ["Hop", "Status", "URL", "Waktu"], rows, "Rantai request")
    if not result["ok"]:
        ui.render_failure(console, result,
                          "Coba metode HEAD, atau periksa URL dan koneksi Anda." if d.get("chain") or d.get("status") else None)
        if "status" not in d:
            return
    ui.print_kv(console, [
        ("Status", Text("%s %s" % (d["status"], d["reason"]), style=_status_style(d["status"]))),
        ("URL akhir", d["final_url"]),
        ("Redirect", d["redirects"]),
        ("Versi HTTP", d["http_version"]),
        ("Server", d["server"]),
        ("Content-Type", d["content_type"]),
        ("Content-Length", d["content_length"]),
        ("Total waktu", fmt_ms(d["total_ms"])),
    ], "Ringkasan")
    sec = d["security"]
    rows = []
    for item in sec["items"]:
        style = {"present": "ok", "missing": "bad", "n/a": "muted"}[item["status"]]
        value = item["value"] or "-"
        if len(value) > 60:
            value = value[:57] + "..."
        rows.append((item["header"], Text(item["status"].upper(), style=style), value))
    ui.data_table(console, ["Header", "Status", "Nilai"], rows,
                  "Header keamanan - skor %d/100 (grade %s)" % (sec["score"], sec["grade"]))
    if sec["info_leak"]:
        ui.note(console, "Info server terbuka: " + ", ".join(
            "%s=%s" % kv for kv in sec["info_leak"].items()), style="warn", mark="warn")
