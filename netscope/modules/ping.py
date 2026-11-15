"""Ping: ICMP lewat binary ``ping`` sistem (tanpa root), cadangan TCP ping.

* Linux / Kali / Termux / macOS / Windows didukung (flag disesuaikan per OS).
* Output ping diparsing lalu statistik dihitung ulang sendiri agar konsisten.
* Bila binary ``ping`` tidak ada atau tidak diizinkan, otomatis beralih ke
  TCP ping (waktu ``connect`` ke port 443/80).
"""

from __future__ import annotations

import math
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.text import Text

from netscope import ui
from netscope.utils import (TargetError, fmt_ms, is_ip, make_result,
                            parse_target)

MODULE = "ping"
_TIME_RE = re.compile(r"time\s*[=<]\s*([\d.,]+)\s*ms", re.IGNORECASE)
_SUMMARY_RE = re.compile(r"(\d+)\s+packets? transmitted,\s*(\d+)\s+(?:packets? )?received",
                         re.IGNORECASE)
_WIN_SUMMARY_RE = re.compile(r"Sent\s*=\s*(\d+),\s*Received\s*=\s*(\d+)", re.IGNORECASE)
_TCP_FALLBACK_PORTS = (443, 80)


def find_ping(ipv6: bool = False) -> Optional[str]:
    """Path binary ping, atau ``None`` bila tidak tersedia."""
    if ipv6 and platform.system() == "Darwin":
        return shutil.which("ping6") or shutil.which("ping")
    return shutil.which("ping")


def build_command(binary: str, host: str, count: int, timeout: float,
                  system: Optional[str] = None) -> List[str]:
    """Susun argumen ping sesuai OS. ``host`` sudah divalidasi (tidak diawali '-')."""
    system = system or platform.system()
    wait_s = max(1, int(math.ceil(timeout)))
    if system == "Windows":
        return [binary, "-n", str(count), "-w", str(wait_s * 1000), host]
    if system == "Darwin":
        # macOS: -W dalam milidetik, -t = batas total (detik)
        return [binary, "-c", str(count), "-W", str(wait_s * 1000), host]
    cmd = [binary, "-c", str(count), "-W", str(wait_s)]
    if is_ip(host) and ":" in host and os.path.basename(binary) == "ping":
        cmd.append("-6")
    cmd.append(host)
    return cmd


def parse_output(text: str) -> Dict[str, Any]:
    """Ekstrak waktu balasan + sent/received dari output ping (multi-OS)."""
    times: List[float] = []
    for raw in _TIME_RE.findall(text):
        try:
            times.append(float(raw.replace(",", ".")))
        except ValueError:
            continue
    sent = received = None
    m = _SUMMARY_RE.search(text) or _WIN_SUMMARY_RE.search(text)
    if m:
        sent, received = int(m.group(1)), int(m.group(2))
    return {"times": times, "sent": sent, "received": received}


def compute_stats(times: List[float], sent: int, received: Optional[int] = None) -> Dict[str, Any]:
    """Hitung statistik latensi, loss, dan jitter."""
    received = len(times) if received is None else received
    loss = 100.0 if sent <= 0 else round((sent - received) * 100.0 / sent, 1)
    loss = max(0.0, min(100.0, loss))
    stats: Dict[str, Any] = {"sent": sent, "received": received, "loss_pct": loss,
                             "min": None, "avg": None, "max": None, "jitter": None}
    if times:
        stats["min"] = round(min(times), 2)
        stats["max"] = round(max(times), 2)
        stats["avg"] = round(sum(times) / len(times), 2)
        if len(times) > 1:
            diffs = [abs(b - a) for a, b in zip(times, times[1:])]
            stats["jitter"] = round(sum(diffs) / len(diffs), 2)
        else:
            stats["jitter"] = 0.0
    return stats


def _icmp_ping(binary: str, host: str, count: int, timeout: float) -> Dict[str, Any]:
    cmd = build_command(binary, host, count, timeout)
    env = dict(os.environ, LC_ALL="C", LANG="C")
    limit = count * (math.ceil(timeout) + 1) + 5
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                              timeout=limit, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        parsed = parse_output(out)
        return {"method": "icmp", "times": parsed["times"], "sent": count,
                "raw_error": "ping melebihi batas waktu"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"method": "icmp", "unavailable": True, "raw_error": str(exc)}

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    parsed = parse_output(combined)
    low = combined.lower()
    if not parsed["times"] and parsed["sent"] is None:
        # Tidak ada ringkasan sama sekali -> kemungkinan izin/ICMP diblokir/host tak dikenal
        if "operation not permitted" in low or "permission denied" in low or "socket:" in low:
            return {"method": "icmp", "unavailable": True,
                    "raw_error": "ICMP tidak diizinkan pada lingkungan ini"}
        if "unknown host" in low or "name or service not known" in low \
                or "could not find host" in low or "temporary failure in name resolution" in low:
            return {"method": "icmp", "times": [], "sent": count,
                    "raw_error": "Host tidak dapat di-resolve"}
    sent = parsed["sent"] if parsed["sent"] else count
    return {"method": "icmp", "times": parsed["times"], "sent": sent,
            "received": parsed["received"], "raw_error": None}


def tcp_ping(host: str, count: int, timeout: float, port: Optional[int] = None) -> Dict[str, Any]:
    """Ukur waktu ``connect`` TCP. Port tertutup (RST) tetap dihitung sebagai balasan."""
    ports = (port,) if port else _TCP_FALLBACK_PORTS
    chosen = ports[0]
    times: List[float] = []
    for p in ports:
        times = []
        for _ in range(count):
            start = time.perf_counter()
            sock = None
            try:
                sock = socket.create_connection((host, p), timeout=timeout)
                times.append((time.perf_counter() - start) * 1000.0)
            except ConnectionRefusedError:
                times.append((time.perf_counter() - start) * 1000.0)
            except OSError:
                pass
            finally:
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
            time.sleep(0.05)
        chosen = p
        if times:
            break
    return {"method": "tcp:%d" % chosen, "times": times, "sent": count, "received": len(times)}


def ping(target: str, count: int = 4, timeout: float = 4.0,
         method: str = "auto") -> Dict[str, Any]:
    """Ping ``target``. ``method``: ``auto`` | ``icmp`` | ``tcp``."""
    start = time.perf_counter()
    try:
        count = int(count)
    except (TypeError, ValueError):
        return make_result(MODULE, str(target), False, error="Jumlah ping harus angka.")
    if not 1 <= count <= 50:
        return make_result(MODULE, str(target), False, error="Jumlah ping harus 1-50.")
    if method not in ("auto", "icmp", "tcp"):
        return make_result(MODULE, str(target), False, error="Metode harus auto, icmp, atau tcp.")
    try:
        host, port = parse_target(target)
    except TargetError as exc:
        return make_result(MODULE, str(target), False, error=str(exc))

    raw: Optional[Dict[str, Any]] = None
    note: Optional[str] = None
    if method in ("auto", "icmp"):
        binary = find_ping(ipv6=is_ip(host) and ":" in host)
        if binary:
            raw = _icmp_ping(binary, host, count, timeout)
            if raw.get("unavailable"):
                note = raw.get("raw_error")
                raw = None
        else:
            note = "Binary 'ping' tidak ditemukan"
        if raw is None and method == "icmp":
            return make_result(MODULE, host, False,
                               error="ICMP ping tidak tersedia: %s." % (note or "tidak diketahui"),
                               duration=time.perf_counter() - start)
    if raw is None:
        raw = tcp_ping(host, count, timeout, port)
        if note:
            note += "; beralih ke TCP ping"

    times = raw.get("times", [])
    stats = compute_stats(times, int(raw.get("sent") or count), raw.get("received"))
    ok = stats["received"] > 0
    error = None
    if not ok:
        error = raw.get("raw_error") or "Tidak ada balasan dari host (100% packet loss)."
    data = {"host": host, "method": raw["method"], "times": [round(t, 2) for t in times],
            "stats": stats, "note": note}
    return make_result(MODULE, host, ok, data, error=error,
                       duration=time.perf_counter() - start)


def quality(stats: Dict[str, Any]) -> str:
    """Penilaian kualitas koneksi: excellent/good/fair/poor/down."""
    if not stats.get("received"):
        return "down"
    avg, loss, jitter = stats["avg"] or 0.0, stats["loss_pct"], stats["jitter"] or 0.0
    if loss >= 50 or avg >= 500:
        return "poor"
    if loss > 0 or avg >= 200 or jitter >= 50:
        return "fair"
    if avg >= 80 or jitter >= 20:
        return "good"
    return "excellent"


_QUALITY_STYLE = {"excellent": "ok", "good": "ok", "fair": "warn", "poor": "bad", "down": "bad"}
_SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"


def sparkline(console: Console, values: List[float]) -> str:
    """Grafik mini latensi; fallback ASCII bila terminal tidak UTF-8."""
    if not values:
        return "-"
    chars = _SPARK if ui.is_unicode(console) else "._-=+*#@"
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    return "".join(chars[min(len(chars) - 1, int((v - lo) / span * (len(chars) - 1)))]
                   for v in values)


def render(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Ping", result["target"])
    ui.result_header(console, result)
    data = result["data"]
    if data and data.get("note"):
        ui.note(console, data["note"], style="warn", mark="warn")
    if not result["ok"]:
        ui.render_failure(console, result,
                          "Host mungkin menolak ICMP; coba metode tcp." if data else None)
        return
    s = data["stats"]
    q = quality(s)
    ui.print_kv(console, [
        ("Metode", data["method"]),
        ("Terkirim / diterima", "%d / %d" % (s["sent"], s["received"])),
        ("Packet loss", Text("%.1f%%" % s["loss_pct"],
                             style="ok" if s["loss_pct"] == 0 else "bad")),
        ("Min / Avg / Max", "%s / %s / %s" % (fmt_ms(s["min"]), fmt_ms(s["avg"]), fmt_ms(s["max"]))),
        ("Jitter", fmt_ms(s["jitter"])),
        ("Grafik", Text(sparkline(console, data["times"]), style="accent2")),
        ("Kualitas", Text(q.upper(), style=_QUALITY_STYLE[q])),
    ], "Statistik")
