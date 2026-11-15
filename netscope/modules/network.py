"""Informasi jaringan: info lokal, resolusi host, cek port TCP, IP publik (opt-in).

Semua fitur bekerja tanpa root. Cek port memakai koneksi TCP biasa
(``connect``), bukan raw socket.
"""

from __future__ import annotations

import platform
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import requests
from rich.console import Console
from rich.text import Text

from netscope import ui
from netscope.utils import (TargetError, detect_platform, fmt_ms, make_result,
                            parse_target, validate_host)

MODULE = "network"
PUBLIC_IP_ENDPOINT = "https://api.ipify.org"

_SERVICES = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp", 53: "dns", 80: "http",
    110: "pop3", 143: "imap", 443: "https", 465: "smtps", 587: "submission",
    993: "imaps", 995: "pop3s", 3306: "mysql", 3389: "rdp", 5432: "postgres",
    8000: "http-alt", 8080: "http-proxy", 8443: "https-alt",
}


def service_name(port: int) -> str:
    return _SERVICES.get(port, "-")


# --------------------------------------------------------------------------
# Info lokal
# --------------------------------------------------------------------------
def _local_ip(family: int, probe: str) -> Optional[str]:
    """Alamat lokal untuk rute keluar. UDP ``connect`` tidak mengirim paket."""
    sock = None
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        sock.connect((probe, 80))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


def _interfaces() -> List[Dict[str, Any]]:
    try:
        return [{"index": idx, "name": name} for idx, name in socket.if_nameindex()]
    except (OSError, AttributeError):
        return []


def local_info() -> Dict[str, Any]:
    """Kumpulkan info jaringan lokal. Tidak pernah raise."""
    start = time.perf_counter()
    data: Dict[str, Any] = {}
    try:
        data["hostname"] = socket.gethostname()
    except OSError:
        data["hostname"] = None
    try:
        data["fqdn"] = socket.getfqdn()
    except OSError:
        data["fqdn"] = None
    data["platform"] = detect_platform()
    data["os"] = "%s %s" % (platform.system(), platform.release())
    data["machine"] = platform.machine()
    data["python"] = platform.python_version()
    data["local_ipv4"] = _local_ip(socket.AF_INET, "192.0.2.1")
    data["local_ipv6"] = _local_ip(socket.AF_INET6, "2001:db8::1") if socket.has_ipv6 else None
    data["interfaces"] = _interfaces()
    data["ipv6_supported"] = bool(socket.has_ipv6)
    return make_result(MODULE + ".local", "localhost", True, data,
                       duration=time.perf_counter() - start)


# --------------------------------------------------------------------------
# Resolve host
# --------------------------------------------------------------------------
def resolve_host(host: str, timeout: float = 8.0) -> Dict[str, Any]:
    """Resolve hostname via resolver sistem (``getaddrinfo``)."""
    start = time.perf_counter()
    try:
        host = validate_host(host)
    except TargetError as exc:
        return make_result(MODULE + ".resolve", str(host), False, error=str(exc))

    old = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return make_result(MODULE + ".resolve", host, False,
                           error="Resolusi gagal: %s" % (exc.strerror or exc),
                           duration=time.perf_counter() - start)
    except OSError as exc:
        return make_result(MODULE + ".resolve", host, False,
                           error="Kesalahan jaringan: %s" % exc,
                           duration=time.perf_counter() - start)
    finally:
        socket.setdefaulttimeout(old)

    v4: List[str] = []
    v6: List[str] = []
    for family, _t, _p, _c, sockaddr in infos:
        addr = sockaddr[0]
        if family == socket.AF_INET and addr not in v4:
            v4.append(addr)
        elif family == socket.AF_INET6 and addr not in v6:
            v6.append(addr)
    try:
        canonical = socket.getfqdn(host) if v4 or v6 else None
    except OSError:
        canonical = None
    data = {"host": host, "ipv4": v4, "ipv6": v6, "canonical": canonical}
    return make_result(MODULE + ".resolve", host, bool(v4 or v6), data,
                       error=None if (v4 or v6) else "Tidak ada alamat ditemukan.",
                       duration=time.perf_counter() - start)


# --------------------------------------------------------------------------
# Cek port TCP
# --------------------------------------------------------------------------
def _probe_port(host: str, port: int, timeout: float) -> Dict[str, Any]:
    start = time.perf_counter()
    state, err = "closed", None
    sock = None
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        state = "open"
    except socket.timeout:
        state, err = "filtered", "timeout"
    except ConnectionRefusedError:
        state = "closed"
    except OSError as exc:
        # Network unreachable / host unreachable dsb. -> tidak bisa dijangkau
        state, err = "filtered", exc.strerror or str(exc)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return {"port": port, "state": state, "service": service_name(port),
            "latency_ms": round((time.perf_counter() - start) * 1000.0, 1)
            if state == "open" else None,
            "detail": err}


def check_ports(target: str, ports: List[int], timeout: float = 3.0,
                workers: int = 32) -> Dict[str, Any]:
    """Cek daftar port TCP pada ``target`` (koneksi penuh, tanpa root)."""
    start = time.perf_counter()
    try:
        host, _ = parse_target(target)
    except TargetError as exc:
        return make_result(MODULE + ".ports", str(target), False, error=str(exc))
    if not ports:
        return make_result(MODULE + ".ports", host, False, error="Daftar port kosong.")

    resolved = resolve_host(host, timeout=timeout)
    if not resolved["ok"]:
        return make_result(MODULE + ".ports", host, False, error=resolved["error"],
                           duration=time.perf_counter() - start)

    workers = max(1, min(workers, len(ports)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda p: _probe_port(host, p, timeout), ports))
    results.sort(key=lambda r: r["port"])
    counts = {s: sum(1 for r in results if r["state"] == s)
              for s in ("open", "closed", "filtered")}
    data = {"host": host, "ports": results, "counts": counts}
    return make_result(MODULE + ".ports", host, True, data,
                       duration=time.perf_counter() - start)


# --------------------------------------------------------------------------
# IP publik (opt-in: mengirim request ke layanan pihak ketiga)
# --------------------------------------------------------------------------
def public_ip(timeout: float = 8.0, user_agent: str = "Noctura-NETSCOPE") -> Dict[str, Any]:
    """Cari IP publik. HANYA dipanggil atas persetujuan eksplisit pengguna."""
    start = time.perf_counter()
    try:
        resp = requests.get(PUBLIC_IP_ENDPOINT, timeout=timeout,
                            headers={"User-Agent": user_agent})
        resp.raise_for_status()
        ip = resp.text.strip()
        if not ip or len(ip) > 45 or not all(c in "0123456789abcdefABCDEF.:" for c in ip):
            raise ValueError("respons tidak valid")
    except requests.RequestException as exc:
        return make_result(MODULE + ".public_ip", "public", False,
                           error="Gagal menghubungi layanan IP publik: %s" % exc.__class__.__name__,
                           duration=time.perf_counter() - start)
    except ValueError as exc:
        return make_result(MODULE + ".public_ip", "public", False,
                           error="Respons layanan IP publik tidak valid.",
                           duration=time.perf_counter() - start)
    return make_result(MODULE + ".public_ip", "public", True,
                       {"ip": ip, "source": PUBLIC_IP_ENDPOINT},
                       duration=time.perf_counter() - start)


# --------------------------------------------------------------------------
# Render
# --------------------------------------------------------------------------
def render_local(console: Console, result: Dict[str, Any]) -> None:
    d = result["data"]
    ui.section(console, "Network Info", "mesin lokal")
    rows = [
        ("Hostname", d.get("hostname")),
        ("FQDN", d.get("fqdn")),
        ("Platform", d.get("platform")),
        ("OS", d.get("os")),
        ("Arsitektur", d.get("machine")),
        ("Python", d.get("python")),
        ("IPv4 lokal", d.get("local_ipv4")),
        ("IPv6 lokal", d.get("local_ipv6")),
        ("Dukungan IPv6", "ya" if d.get("ipv6_supported") else "tidak"),
    ]
    ui.print_kv(console, rows, "Sistem")
    ifaces = d.get("interfaces") or []
    if ifaces:
        ui.data_table(console, ["#", "Interface"],
                      [(i["index"], i["name"]) for i in ifaces], "Interface")
    else:
        ui.note(console, "Daftar interface tidak tersedia di platform ini.")


def render_resolve(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Resolve", result["target"])
    ui.result_header(console, result)
    if not result["ok"]:
        ui.render_failure(console, result, "Periksa ejaan domain dan koneksi internet.")
        return
    d = result["data"]
    rows = [("Host", d["host"]), ("Canonical", d.get("canonical"))]
    rows += [("IPv4", ip) for ip in d["ipv4"]]
    rows += [("IPv6", ip) for ip in d["ipv6"]]
    ui.print_kv(console, rows, "Alamat")


def render_ports(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Port Check", result["target"])
    ui.result_header(console, result)
    if not result["ok"]:
        ui.render_failure(console, result)
        return
    rows = []
    for r in result["data"]["ports"]:
        rows.append((r["port"], r["service"],
                     Text(r["state"].upper(), style=ui.state_style(r["state"])),
                     fmt_ms(r["latency_ms"])))
    ui.data_table(console, ["Port", "Layanan", "Status", "Latensi"], rows)
    c = result["data"]["counts"]
    ui.note(console, "open=%d  closed=%d  filtered=%d" % (c["open"], c["closed"], c["filtered"]))


def render_public_ip(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Public IP")
    if not result["ok"]:
        ui.render_failure(console, result, "Pastikan perangkat terhubung ke internet.")
        return
    ui.print_kv(console, [("IP publik", result["data"]["ip"]),
                          ("Sumber", result["data"]["source"])], "Hasil")
