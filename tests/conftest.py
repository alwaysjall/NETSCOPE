"""Fixture bersama. Semua tes berjalan OFFLINE (server lokal 127.0.0.1)."""

from __future__ import annotations

import datetime
import http.server
import os
import socket
import ssl
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Jangan pernah menyentuh konfigurasi asli pengguna saat tes."""
    monkeypatch.setenv("NETSCOPE_CONFIG", str(tmp_path / "config.json"))
    for key in list(os.environ):
        if key.startswith("NETSCOPE_") and key != "NETSCOPE_CONFIG":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def closed_port() -> int:
    return free_port()


@pytest.fixture
def tcp_listener():
    """Server TCP sederhana yang menerima koneksi lalu menutupnya."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    srv.settimeout(0.2)
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except (socket.timeout, OSError):
                continue

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield srv.getsockname()[1]
    stop.set()
    t.join(timeout=2)
    srv.close()


# ------------------------------------------------------------------ HTTP
class _Handler(http.server.BaseHTTPRequestHandler):
    def version_string(self):  # satu-satunya header "Server" yang dikirim
        return "TestSrv/1.0"

    def log_message(self, *a):  # senyap
        pass

    def _send(self, code, headers=None, body=b"ok"):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if path == "/ok":
            self._send(200, {
                "Content-Type": "text/plain",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'",
            })
        elif path == "/redir":
            self._send(302, {"Location": "/ok"})
        elif path == "/redir2":
            self._send(301, {"Location": "/redir"})
        elif path == "/loop":
            self._send(302, {"Location": "/loop"})
        elif path == "/badscheme":
            self._send(302, {"Location": "ftp://example.com/x"})
        elif path == "/chain":
            self._send(302, {"Location": "/chain2"})
        elif path == "/missing":
            self._send(404, body=b"nope")
        elif path == "/boom":
            self._send(500, body=b"err")
        else:
            self._send(200)

    do_HEAD = do_GET


@pytest.fixture
def http_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


# ------------------------------------------------------------------- TLS
def make_cert(tmp_path, cn="localhost", days_valid=90, start_offset_days=-1,
              san_dns=("localhost",), san_ip=("127.0.0.1",), name="srv"):
    """Buat sertifikat self-signed (butuh paket `cryptography`)."""
    pytest.importorskip("cryptography")
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Noctura Test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    nb = now + datetime.timedelta(days=start_offset_days)
    na = nb + datetime.timedelta(days=days_valid)
    sans = [x509.DNSName(d) for d in san_dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in san_ip]
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(nb).not_valid_after(na))
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    cert_path = tmp_path / ("%s.crt" % name)
    key_path = tmp_path / ("%s.key" % name)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    return str(cert_path), str(key_path)


class TLSServer:
    def __init__(self, cert, key):
        self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ctx.load_cert_chain(cert, key)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (socket.timeout, OSError):
                continue
            try:
                conn.settimeout(2)
                tls = self.ctx.wrap_socket(conn, server_side=True)
                tls.close()
            except (ssl.SSLError, OSError):
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        self.sock.close()


@pytest.fixture
def tls_factory(tmp_path):
    servers = []

    def factory(**kwargs):
        cert, key = make_cert(tmp_path, **kwargs)
        srv = TLSServer(cert, key)
        servers.append(srv)
        return srv

    yield factory
    for s in servers:
        s.close()
