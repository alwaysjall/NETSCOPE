"""SSL/TLS inspector: sertifikat, masa berlaku, SAN, protokol, cipher.

Strategi:
  1. Handshake dengan verifikasi penuh (rantai + hostname).
  2. Bila verifikasi gagal, handshake kedua TANPA verifikasi hanya untuk
     membaca sertifikat agar penyebab masalah bisa dilaporkan.
     Tidak ada data yang dikirim selain ClientHello standar.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import ssl
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.text import Text

from netscope import ui
from netscope.utils import TargetError, make_result, parse_target

MODULE = "ssl"
EXPIRING_SOON_DAYS = 30


def _name_to_dict(rdns: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for rdn in rdns or ():
        for key, value in rdn:
            out.setdefault(key, value)
    return out


def hostname_matches(host: str, cert: Dict[str, Any]) -> bool:
    """Cocokkan host dengan SAN (fallback CN). Mendukung wildcard satu label."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    sans = cert.get("subjectAltName", ()) or ()
    if ip is not None:
        for kind, value in sans:
            if kind == "IP Address":
                try:
                    if ipaddress.ip_address(value) == ip:
                        return True
                except ValueError:
                    continue
        return False

    host = host.lower().rstrip(".")
    candidates = [v.lower() for k, v in sans if k == "DNS"]
    if not candidates:
        cn = _name_to_dict(cert.get("subject")).get("commonName")
        if cn:
            candidates = [cn.lower()]
    for pattern in candidates:
        if pattern == host:
            return True
        if pattern.startswith("*.") and host.count(".") >= pattern.count("."):
            if host.split(".", 1)[1] == pattern[2:] and "." in host:
                return True
    return False


def _to_dt(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return datetime.fromtimestamp(ssl.cert_time_to_seconds(text), tz=timezone.utc)
    except (ValueError, OverflowError):
        return None


def _decode_der(der: bytes) -> Dict[str, Any]:
    """Dekode DER menjadi dict ala ``getpeercert()`` tanpa verifikasi."""
    decoder = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)
    if decoder is None:
        return {}
    pem = ssl.DER_cert_to_PEM_cert(der)
    fd, path = tempfile.mkstemp(suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(pem)
        return decoder(path)
    except (ssl.SSLError, OSError, ValueError):
        return {}
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _handshake(host: str, port: int, timeout: float, verify: bool,
               min_version: Optional[ssl.TLSVersion] = None,
               max_version: Optional[ssl.TLSVersion] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if min_version is not None:
        ctx.minimum_version = min_version
    if max_version is not None:
        ctx.maximum_version = max_version
    sni = host
    with socket.create_connection((host, port), timeout=timeout) as raw:
        raw.settimeout(timeout)
        with ctx.wrap_socket(raw, server_hostname=sni) as tls:
            cert = tls.getpeercert()
            if not cert:
                der = tls.getpeercert(binary_form=True)
                cert = _decode_der(der) if der else {}
            cipher = tls.cipher() or (None, None, None)
            info = {"protocol": tls.version(), "cipher": cipher[0], "bits": cipher[2],
                    "alpn": tls.selected_alpn_protocol()}
    return cert, info


def _probe_versions(host: str, port: int, timeout: float) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for label, ver in (("TLSv1.2", ssl.TLSVersion.TLSv1_2), ("TLSv1.3", ssl.TLSVersion.TLSv1_3)):
        try:
            _handshake(host, port, timeout, False, ver, ver)
            out[label] = True
        except (ssl.SSLError, OSError, ValueError):
            out[label] = False
    return out


def summarize_cert(cert: Dict[str, Any], host: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Ringkas dict sertifikat menjadi struktur yang mudah dilaporkan."""
    now = now or datetime.now(timezone.utc)
    subject = _name_to_dict(cert.get("subject"))
    issuer = _name_to_dict(cert.get("issuer"))
    not_before = _to_dt(cert.get("notBefore"))
    not_after = _to_dt(cert.get("notAfter"))
    days_left: Optional[int] = None
    status = "unknown"
    if not_after is not None:
        days_left = int((not_after - now).total_seconds() // 86400)
        if not_after < now:
            status = "expired"
        elif days_left < EXPIRING_SOON_DAYS:
            status = "expiring_soon"
        else:
            status = "valid"
    if not_before is not None and not_before > now:
        status = "not_yet_valid"
    sans = [v for k, v in (cert.get("subjectAltName") or ()) if k in ("DNS", "IP Address")]
    return {
        "subject_cn": subject.get("commonName"),
        "subject_org": subject.get("organizationName"),
        "issuer_cn": issuer.get("commonName"),
        "issuer_org": issuer.get("organizationName"),
        "self_signed": bool(subject) and subject == issuer,
        "serial": cert.get("serialNumber"),
        "version": cert.get("version"),
        "not_before": not_before.isoformat(timespec="seconds") if not_before else None,
        "not_after": not_after.isoformat(timespec="seconds") if not_after else None,
        "days_left": days_left,
        "status": status,
        "san": sans,
        "hostname_match": hostname_matches(host, cert),
        "ocsp": list(cert.get("OCSP", ()) or ()),
        "ca_issuers": list(cert.get("caIssuers", ()) or ()),
    }


def _explain(exc: ssl.SSLError) -> str:
    reason = getattr(exc, "reason", None) or ""
    text = str(getattr(exc, "verify_message", "") or exc)
    low = (reason + " " + text).lower()
    if "certificate has expired" in low:
        return "Sertifikat sudah kedaluwarsa."
    if "self-signed" in low or "self signed" in low:
        return "Sertifikat self-signed / CA tidak tepercaya."
    if "unable to get local issuer" in low or "unable to verify the first" in low:
        return "Rantai sertifikat tidak lengkap atau CA tidak dikenal."
    if "hostname mismatch" in low or "doesn't match" in low:
        return "Nama host tidak cocok dengan sertifikat."
    return "Verifikasi sertifikat gagal: %s" % " ".join(text.split())[:140]


def inspect(target: str, timeout: float = 8.0, probe_versions: bool = True) -> Dict[str, Any]:
    """Inspeksi sertifikat TLS pada ``target`` (default port 443)."""
    start = time.perf_counter()
    try:
        host, port = parse_target(target, default_port=443)
    except TargetError as exc:
        return make_result(MODULE, str(target), False, error=str(exc))
    port = port or 443
    label = "%s:%d" % (host, port)

    verified, verify_error = True, None
    cert: Dict[str, Any] = {}
    info: Dict[str, Any] = {}
    try:
        cert, info = _handshake(host, port, timeout, True)
    except ssl.SSLCertVerificationError as exc:
        verified, verify_error = False, _explain(exc)
    except ssl.SSLError as exc:
        return make_result(MODULE, label, False,
                           error="Handshake TLS gagal: %s" % " ".join(str(exc).split())[:140],
                           duration=time.perf_counter() - start)
    except socket.timeout:
        return make_result(MODULE, label, False, error="Timeout saat menghubungi server.",
                           duration=time.perf_counter() - start)
    except socket.gaierror as exc:
        return make_result(MODULE, label, False,
                           error="Resolusi gagal: %s" % (exc.strerror or exc),
                           duration=time.perf_counter() - start)
    except OSError as exc:
        return make_result(MODULE, label, False,
                           error="Koneksi gagal: %s" % (exc.strerror or exc),
                           duration=time.perf_counter() - start)

    if not verified:
        try:
            cert, info = _handshake(host, port, timeout, False)
        except (ssl.SSLError, OSError, ValueError) as exc:
            return make_result(MODULE, label, False,
                               error="%s (dan sertifikat tidak dapat dibaca)" % verify_error,
                               duration=time.perf_counter() - start)

    summary = summarize_cert(cert, host)
    protocols = _probe_versions(host, port, timeout) if probe_versions else {}
    issues: List[str] = []
    if verify_error:
        issues.append(verify_error)
    if summary["status"] == "expired":
        issues.append("Sertifikat kedaluwarsa.")
    elif summary["status"] == "expiring_soon":
        issues.append("Sertifikat kedaluwarsa dalam %d hari." % summary["days_left"])
    elif summary["status"] == "not_yet_valid":
        issues.append("Sertifikat belum berlaku.")
    if not summary["hostname_match"]:
        issues.append("Nama host tidak cocok dengan CN/SAN sertifikat.")
    if summary["self_signed"] and "self-signed" not in " ".join(issues).lower():
        issues.append("Sertifikat self-signed.")
    if info.get("protocol") in ("TLSv1", "TLSv1.1", "SSLv3"):
        issues.append("Protokol usang: %s." % info["protocol"])

    data = {"host": host, "port": port, "verified": verified, "verify_error": verify_error,
            "connection": info, "certificate": summary, "protocols": protocols, "issues": issues}
    result = make_result(MODULE, label, True, data,
                         duration=time.perf_counter() - start)
    result["healthy"] = bool(verified and not issues)
    return result


def render(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "SSL Inspector", result["target"])
    ui.result_header(console, result)
    if not result["ok"]:
        ui.render_failure(console, result, "Pastikan host mendukung HTTPS pada port tersebut.")
        return
    d = result["data"]
    c, conn = d["certificate"], d["connection"]
    healthy = result.get("healthy", False)
    verdict = Text()
    verdict.append(ui.status_text(console, healthy, "SEHAT", "PERLU PERHATIAN"))
    ui.print_kv(console, [
        ("Verdict", verdict),
        ("Terverifikasi", Text("YA" if d["verified"] else "TIDAK",
                               style="ok" if d["verified"] else "bad")),
        ("Subject (CN)", c["subject_cn"]),
        ("Organisasi", c["subject_org"]),
        ("Issuer", "%s%s" % (c["issuer_cn"] or "-",
                             " (%s)" % c["issuer_org"] if c["issuer_org"] else "")),
        ("Berlaku dari", c["not_before"]),
        ("Berlaku sampai", c["not_after"]),
        ("Sisa hari", Text(str(c["days_left"]) if c["days_left"] is not None else "-",
                           style=ui.state_style(c["status"]))),
        ("Serial", c["serial"]),
        ("Hostname cocok", "ya" if c["hostname_match"] else "TIDAK"),
    ], "Sertifikat")
    ui.print_kv(console, [
        ("Protokol", conn.get("protocol")),
        ("Cipher", "%s (%s bit)" % (conn.get("cipher"), conn.get("bits"))),
        ("ALPN", conn.get("alpn")),
    ] + [("Dukung " + k, "ya" if v else "tidak") for k, v in d["protocols"].items()],
        "Koneksi")
    if c["san"]:
        shown = c["san"][:12]
        rows = [(i + 1, s) for i, s in enumerate(shown)]
        title = "Subject Alt Names (%d)" % len(c["san"])
        if len(c["san"]) > len(shown):
            title += " - ditampilkan %d" % len(shown)
        ui.data_table(console, ["#", "Nama"], rows, title)
    for issue in d["issues"]:
        ui.note(console, issue, style="warn", mark="warn")
    if not d["issues"]:
        ui.note(console, "Tidak ada masalah terdeteksi.", style="ok", mark="ok")
