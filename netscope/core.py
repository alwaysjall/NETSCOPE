"""Inti Noctura NETSCOPE: sesi, eksekusi pengecekan, dan audit penuh.

``Session`` dipakai bersama oleh CLI (``main.py``) dan menu interaktif
(``menu.py``) sehingga perilaku keduanya identik. Setiap method:
  * memvalidasi input (kesalahan input -> hasil ``ok=False``, bukan exception)
  * menyimpan hasil ke riwayat sesi (dipakai untuk laporan)
  * mencetak tampilan Rich bila ``quiet`` bernilai False
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from rich.console import Console

from netscope import ui
from netscope.config import load_config
from netscope.modules import dns as dns_mod
from netscope.modules import http as http_mod
from netscope.modules import network as net_mod
from netscope.modules import ping as ping_mod
from netscope.modules import reports as rep_mod
from netscope.modules import ssl_check as ssl_mod
from netscope.utils import (TargetError, is_ip, make_result, parse_ports,
                            parse_target)

#: Batas riwayat agar sesi panjang tidak memakan memori tanpa batas.
MAX_HISTORY = 200


class Session:
    """Status sesi: konfigurasi, console, dan riwayat hasil."""

    def __init__(self, console: Console, config: Optional[Dict[str, Any]] = None,
                 quiet: bool = False) -> None:
        self.console = console
        if config is None:
            config, self.config_warnings = load_config()
        else:
            self.config_warnings = []
        self.config: Dict[str, Any] = config
        self.quiet = quiet
        self.history: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- util
    def record(self, result: Dict[str, Any]) -> Dict[str, Any]:
        self.history.append(result)
        if len(self.history) > MAX_HISTORY:
            del self.history[: len(self.history) - MAX_HISTORY]
        return result

    def _show(self, render: Callable[[Console, Dict[str, Any]], None],
              result: Dict[str, Any]) -> None:
        if not self.quiet:
            render(self.console, result)

    @property
    def timeout(self) -> float:
        return float(self.config["timeout"])

    # ------------------------------------------------------------ pengecekan
    def local_info(self) -> Dict[str, Any]:
        result = self.record(net_mod.local_info())
        self._show(net_mod.render_local, result)
        return result

    def resolve(self, host: str) -> Dict[str, Any]:
        result = self.record(net_mod.resolve_host(host, self.timeout))
        self._show(net_mod.render_resolve, result)
        return result

    def ports(self, target: str, spec: str = "common") -> Dict[str, Any]:
        try:
            plist = parse_ports(spec)
        except TargetError as exc:
            result = make_result("network.ports", str(target), False, error=str(exc))
        else:
            result = net_mod.check_ports(target, plist, timeout=min(self.timeout, 3.0))
        self.record(result)
        self._show(net_mod.render_ports, result)
        return result

    def public_ip(self) -> Dict[str, Any]:
        result = self.record(net_mod.public_ip(self.timeout, self.config["user_agent"]))
        self._show(net_mod.render_public_ip, result)
        return result

    def dns(self, domain: str, types: Optional[List[str]] = None) -> Dict[str, Any]:
        result = self.record(dns_mod.lookup(domain, types, self.timeout,
                                            self.config["dns_server"]))
        self._show(dns_mod.render, result)
        return result

    def reverse(self, ip: str) -> Dict[str, Any]:
        result = self.record(dns_mod.reverse(ip, self.timeout, self.config["dns_server"]))
        self._show(dns_mod.render_reverse, result)
        return result

    def ping(self, target: str, count: Optional[int] = None,
             method: str = "auto") -> Dict[str, Any]:
        result = self.record(ping_mod.ping(
            target, count if count is not None else self.config["ping_count"],
            min(self.timeout, 5.0), method))
        self._show(ping_mod.render, result)
        return result

    def http(self, url: str, method: str = "GET", insecure: bool = False) -> Dict[str, Any]:
        result = self.record(http_mod.check(
            url, self.timeout, method, verify=not insecure,
            user_agent=self.config["user_agent"]))
        self._show(http_mod.render, result)
        return result

    def ssl(self, target: str) -> Dict[str, Any]:
        result = self.record(ssl_mod.inspect(target, self.timeout))
        self._show(ssl_mod.render, result)
        return result

    # --------------------------------------------------------------- audit
    def full_audit(self, target: str) -> List[Dict[str, Any]]:
        """Jalankan resolve, DNS, ping, HTTP, dan SSL untuk satu domain.

        Satu langkah gagal tidak menghentikan langkah lain (kecuali target
        tidak valid, yang dihentikan di awal).
        """
        try:
            host, _ = parse_target(target)
        except TargetError as exc:
            result = self.record(make_result("audit", str(target), False, error=str(exc)))
            if not self.quiet:
                ui.render_failure(self.console, result)
            return [result]

        steps: List[Callable[[], Dict[str, Any]]] = [
            lambda: self.resolve(host),
            lambda: self.ping(host),
            lambda: self.http(host),
            lambda: self.ssl(host),
        ]
        if not is_ip(host):
            steps.insert(1, lambda: self.dns(host))
        return [step() for step in steps]

    # ------------------------------------------------------------- laporan
    def report(self, fmt: str = "html", everything: bool = False,
               results: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Simpan laporan dari ``results`` (atau seluruh riwayat sesi)."""
        data = results if results is not None else self.history
        directory = self.config.get("report_dir", "")
        if everything or fmt == "all":
            outs = rep_mod.save_all(data, directory)
        else:
            outs = [rep_mod.save_report(data, fmt, directory)]
        for r in outs:  # laporan sengaja tidak dimasukkan ke riwayat
            self._show(rep_mod.render, r)
        return outs


def summarize(results: List[Dict[str, Any]]) -> Dict[str, int]:
    """Hitung ringkasan lulus/gagal dari daftar hasil."""
    passed = sum(1 for r in results if r.get("ok"))
    return {"total": len(results), "passed": passed, "failed": len(results) - passed}


__all__ = ["Session", "summarize"]
