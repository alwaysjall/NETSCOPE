"""Utilitas bersama: validasi target, format hasil, deteksi platform."""

from __future__ import annotations

import ipaddress
import os
import platform
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$", re.IGNORECASE)

#: Port umum untuk preset ``common``.
COMMON_PORTS = [21, 22, 23, 25, 53, 80, 110, 143, 443, 465, 587, 993, 995,
                3306, 3389, 5432, 8080, 8443]
#: Port web untuk preset ``web``.
WEB_PORTS = [80, 443, 8000, 8080, 8443]
#: Batas port per sekali cek (alat diagnostik, bukan scanner massal).
MAX_PORTS = 100


class TargetError(ValueError):
    """Input target (host/URL/port) tidak valid."""


# --------------------------------------------------------------------------
# Hasil standar
# --------------------------------------------------------------------------
def now_iso() -> str:
    """Waktu lokal saat ini (ISO 8601, detik)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def make_result(module: str, target: str, ok: bool,
                data: Optional[Dict[str, Any]] = None,
                error: Optional[str] = None,
                duration: float = 0.0) -> Dict[str, Any]:
    """Bentuk dict hasil standar yang dipakai semua modul."""
    return {
        "module": module,
        "target": target,
        "ok": bool(ok),
        "error": error,
        "timestamp": now_iso(),
        "duration_ms": round(duration * 1000.0, 1),
        "data": data if data is not None else {},
    }


# --------------------------------------------------------------------------
# Validasi target
# --------------------------------------------------------------------------
def is_ip(value: str) -> bool:
    """True bila ``value`` adalah alamat IPv4/IPv6 yang valid."""
    try:
        ipaddress.ip_address(str(value).strip())
        return True
    except ValueError:
        return False


def validate_host(raw: Any) -> str:
    """Validasi & normalisasi hostname / IP. Mengembalikan host ASCII (IDNA)."""
    if raw is None:
        raise TargetError("Target kosong.")
    host = str(raw).strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host:
        raise TargetError("Target kosong.")
    if any(ch.isspace() or ord(ch) < 32 for ch in host):
        raise TargetError("Target tidak boleh mengandung spasi/karakter kontrol.")
    if "%" in host:
        raise TargetError("Zone-id IPv6 (%) tidak didukung.")

    if is_ip(host):
        return str(ipaddress.ip_address(host))

    host = host.rstrip(".")
    if not host:
        raise TargetError("Target kosong.")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise TargetError("Hostname mengandung karakter yang tidak valid.")
    host = host.lower()
    if len(host) > 253:
        raise TargetError("Hostname terlalu panjang (maks 253 karakter).")
    if re.fullmatch(r"[\d.]+", host):
        raise TargetError("'%s' bukan alamat IPv4 yang valid." % host)
    for label in host.split("."):
        if not _LABEL_RE.match(label):
            raise TargetError("Hostname tidak valid: '%s'." % host)
    return host


def _check_port(value: Any) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise TargetError("Port harus berupa angka 1-65535.")
    if not 1 <= port <= 65535:
        raise TargetError("Port harus di antara 1 dan 65535.")
    return port


def parse_target(raw: Any, default_port: Optional[int] = None) -> Tuple[str, Optional[int]]:
    """Urai input pengguna menjadi ``(host, port)``.

    Menerima: ``example.com``, ``example.com:8443``, ``https://example.com/x``,
    ``[2001:db8::1]:443``, ``192.0.2.1``, ``2001:db8::1``.
    """
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise TargetError("Target kosong.")
    if is_ip(text.strip("[]")):
        return validate_host(text), default_port

    candidate = text if "://" in text else "//" + text
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise TargetError("Format target tidak valid: '%s'." % text)
    if not host:
        raise TargetError("Format target tidak valid: '%s'." % text)
    if port is not None:
        port = _check_port(port)
    return validate_host(host), port if port is not None else default_port


def normalize_url(raw: Any) -> str:
    """Pastikan URL http(s) valid; tambahkan ``https://`` bila skema tidak ada."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise TargetError("URL kosong.")
    if any(ch.isspace() for ch in text):
        raise TargetError("URL tidak boleh mengandung spasi.")
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
        host = parts.hostname
    except ValueError:
        raise TargetError("URL tidak valid: '%s'." % text)
    if parts.scheme.lower() not in ("http", "https"):
        raise TargetError("Hanya skema http/https yang didukung (bukan '%s')." % parts.scheme)
    if not host:
        raise TargetError("URL tidak memiliki host.")
    validate_host(host)
    if port is not None:
        _check_port(port)
    return text


def parse_ports(spec: Any, limit: int = MAX_PORTS) -> List[int]:
    """Urai spesifikasi port: ``80,443``, ``8000-8010``, ``common``, ``web``."""
    text = "" if spec is None else str(spec).strip().lower()
    if not text:
        raise TargetError("Daftar port kosong.")
    ports: List[int] = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        if token == "common":
            ports.extend(COMMON_PORTS)
        elif token == "web":
            ports.extend(WEB_PORTS)
        elif "-" in token:
            lo_s, _, hi_s = token.partition("-")
            lo, hi = _check_port(lo_s), _check_port(hi_s)
            if lo > hi:
                raise TargetError("Rentang port terbalik: '%s'." % token)
            if hi - lo + 1 > limit:
                raise TargetError("Maksimal %d port per pengecekan." % limit)
            ports.extend(range(lo, hi + 1))
        else:
            ports.append(_check_port(token))
    unique = sorted(set(ports))
    if not unique:
        raise TargetError("Daftar port kosong.")
    if len(unique) > limit:
        raise TargetError("Maksimal %d port per pengecekan (diminta %d)." % (limit, len(unique)))
    return unique


# --------------------------------------------------------------------------
# Format & platform
# --------------------------------------------------------------------------
def safe_filename(text: Any, maxlen: int = 40) -> str:
    """Ubah teks apa pun menjadi potongan nama file yang aman."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("._-")
    return cleaned[:maxlen] or "report"


def fmt_ms(value: Optional[float]) -> str:
    """Format milidetik; ``None`` menjadi tanda hubung."""
    if value is None:
        return "-"
    return "%.1f ms" % value


def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or os.path.isdir("/data/data/com.termux")


def detect_platform() -> str:
    """Nama platform yang ramah dibaca: Termux, Kali Linux, Linux, macOS, Windows."""
    if is_termux():
        return "Termux"
    system = platform.system()
    if system == "Linux":
        try:
            with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.startswith("ID="):
                        if line.strip().split("=", 1)[1].strip('"').lower() == "kali":
                            return "Kali Linux"
                        break
        except OSError:
            pass
        return "Linux"
    if system == "Darwin":
        return "macOS"
    return system or "Unknown"
