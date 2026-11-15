"""Titik masuk Noctura NETSCOPE.

Tanpa argumen -> menu interaktif. Dengan subperintah -> mode CLI langsung.

Kode keluar: 0 sukses | 1 ada pengecekan gagal | 2 salah penggunaan | 130 dibatalkan
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from rich.console import Console

from netscope import __app_name__, __tagline__, __tool_name__, __version__, banner, ui
from netscope.config import (DEFAULTS, config_path, load_config, save_config,
                             update_value)
from netscope.core import Session, summarize
from netscope.menu import Menu
from netscope.modules.reports import FORMATS
from netscope.utils import TargetError

EPILOG = """contoh:
  netscope                         menu interaktif
  netscope audit example.com       audit lengkap satu domain
  netscope ping 1.1.1.1 -c 5       ping 5 paket
  netscope ports example.com -p web
  netscope http example.com --report html
  netscope ssl example.com:443 --json
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netscope",
        description="%s %s - %s" % (__app_name__, __tool_name__, __tagline__),
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-V", "--version", action="version",
                        version="%s %s %s" % (__app_name__, __tool_name__, __version__))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true",
                        help="cetak hasil sebagai JSON (tanpa tampilan Rich)")
    common.add_argument("--no-color", action="store_true", help="matikan warna")
    common.add_argument("--no-banner", action="store_true", help="sembunyikan banner")
    common.add_argument("-t", "--timeout", type=float, metavar="DETIK",
                        help="timeout jaringan (1-120)")
    common.add_argument("--report", choices=list(FORMATS) + ["all"], metavar="FORMAT",
                        help="simpan laporan: %s | all" % " | ".join(FORMATS))

    sub = parser.add_subparsers(dest="command", metavar="PERINTAH")

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, parents=[common], help=help_text, description=help_text)

    add("menu", "buka menu interaktif")
    add("info", "info jaringan mesin lokal")
    p = add("resolve", "resolve domain ke IP")
    p.add_argument("host")
    p = add("ports", "cek port TCP")
    p.add_argument("host")
    p.add_argument("-p", "--ports", default="common",
                   help="80,443 | 1-1024 | common | web (default: common)")
    p = add("dns", "DNS lookup")
    p.add_argument("domain")
    p.add_argument("-r", "--records", default="",
                   help="tipe record dipisah koma, mis. A,MX,TXT")
    p = add("reverse", "reverse DNS (PTR)")
    p.add_argument("ip")
    p = add("ping", "ping host")
    p.add_argument("host")
    p.add_argument("-c", "--count", type=int, help="jumlah paket (1-50)")
    p.add_argument("-m", "--method", choices=["auto", "icmp", "tcp"], default="auto")
    p = add("http", "HTTP checker")
    p.add_argument("url")
    p.add_argument("-X", "--request", choices=["GET", "HEAD"], default="GET")
    p.add_argument("-k", "--insecure", action="store_true",
                   help="jangan verifikasi sertifikat TLS")
    p = add("ssl", "SSL/TLS inspector")
    p.add_argument("host", help="host atau host:port")
    p = add("audit", "audit penuh: resolve, DNS, ping, HTTP, SSL")
    p.add_argument("domain")
    p = add("public-ip", "lihat IP publik (mengirim 1 request ke api.ipify.org)")
    p.add_argument("--yes", action="store_true", help="setuju mengirim request")
    p = add("config", "lihat / ubah konfigurasi")
    p.add_argument("--set", nargs=2, metavar=("KUNCI", "NILAI"), help="ubah dan simpan satu nilai")
    return parser


def _emit_json(results: List[Dict[str, Any]]) -> None:
    payload: Any = results[0] if len(results) == 1 else results
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")


def _exit_code(results: List[Dict[str, Any]]) -> int:
    return 0 if results and all(r.get("ok") for r in results) else 1


def run_command(args: argparse.Namespace, session: Session) -> List[Dict[str, Any]]:
    cmd = args.command
    if cmd == "info":
        return [session.local_info()]
    if cmd == "resolve":
        return [session.resolve(args.host)]
    if cmd == "ports":
        return [session.ports(args.host, args.ports)]
    if cmd == "dns":
        types = [t.strip() for t in args.records.split(",") if t.strip()] or None
        return [session.dns(args.domain, types)]
    if cmd == "reverse":
        return [session.reverse(args.ip)]
    if cmd == "ping":
        return [session.ping(args.host, args.count, args.method)]
    if cmd == "http":
        return [session.http(args.url, args.request, args.insecure)]
    if cmd == "ssl":
        return [session.ssl(args.host)]
    if cmd == "audit":
        return session.full_audit(args.domain)
    raise ValueError("perintah tidak dikenal: %s" % cmd)


def _cmd_public_ip(args: argparse.Namespace, session: Session) -> List[Dict[str, Any]]:
    """IP publik butuh persetujuan eksplisit (--yes atau jawaban interaktif)."""
    console = session.console
    if not args.yes:
        if not sys.stdin.isatty() or args.json:
            from netscope.utils import make_result
            res = make_result("network.public_ip", "public", False,
                              error="Butuh persetujuan: tambahkan --yes untuk mengirim 1 request "
                                    "ke api.ipify.org.")
            return [session.record(res)]
        ui.note(console, "Ini akan mengirim 1 request ke api.ipify.org.", style="warn", mark="warn")
        try:
            ok = console.input("Lanjutkan? (y/N): ").strip().lower() in ("y", "ya", "yes")
        except (EOFError, KeyboardInterrupt):
            ok = False
        if not ok:
            console.print("Dibatalkan. Tidak ada data yang dikirim.")
            return []
    return [session.public_ip()]


def _cmd_config(args: argparse.Namespace, console: Console, json_mode: bool) -> int:
    cfg, warnings = load_config()
    if args.set:
        try:
            update_value(cfg, args.set[0], args.set[1])
            path = save_config(cfg)
        except TargetError as exc:
            ui.error_panel(console, str(exc))
            return 2
        except OSError as exc:
            ui.error_panel(console, "Gagal menyimpan konfigurasi: %s" % (exc.strerror or exc))
            return 1
        ui.note(console, "Tersimpan di %s" % path, style="ok", mark="ok")
    if json_mode:
        sys.stdout.write(json.dumps(cfg, indent=2) + "\n")
        return 0
    ui.warnings_block(console, warnings)
    ui.print_kv(console, [(k, cfg[k]) for k in DEFAULTS], "Konfigurasi (%s)" % config_path())
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse: --help/--version -> 0, salah pakai -> 2
        return int(exc.code) if isinstance(exc.code, int) else 2

    json_mode = bool(getattr(args, "json", False))
    console = ui.make_console(no_color=bool(getattr(args, "no_color", False)))
    # Mode JSON: semua tampilan Rich dibungkam; hanya JSON ke stdout.
    command = args.command or "menu"

    try:
        if command == "config":
            return _cmd_config(args, console, json_mode)

        config, warnings = load_config()
        if getattr(args, "timeout", None) is not None:
            try:
                update_value(config, "timeout", args.timeout)
            except TargetError as exc:
                ui.error_panel(console, str(exc))
                return 2
        session = Session(console, config, quiet=json_mode)
        session.config_warnings = warnings

        if command == "menu":
            if json_mode:
                ui.error_panel(console, "--json tidak berlaku untuk menu interaktif.")
                return 2
            return Menu(session, show_banner=not args.no_banner if hasattr(args, "no_banner") else True).run()

        if not json_mode and not getattr(args, "no_banner", False):
            banner.show_banner(console, animate=False, compact=True)
            ui.warnings_block(console, warnings)

        results = _cmd_public_ip(args, session) if command == "public-ip" \
            else run_command(args, session)

        if getattr(args, "report", None):
            outs = session.report(args.report, results=results or None) if results else []
            if json_mode:
                for out in outs:
                    if not out["ok"]:
                        sys.stderr.write("Laporan gagal: %s\n" % out["error"])

        if json_mode:
            _emit_json(results)
        elif len(results) > 1:
            s = summarize(results)
            ui.section(console, "Ringkasan")
            ui.note(console, "%d dari %d pengecekan lulus." % (s["passed"], s["total"]),
                    style="ok" if not s["failed"] else "warn",
                    mark="ok" if not s["failed"] else "warn")
        return _exit_code(results) if results else 0
    except KeyboardInterrupt:
        console.print()
        ui.note(console, "Dibatalkan oleh pengguna.", style="warn", mark="warn")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
