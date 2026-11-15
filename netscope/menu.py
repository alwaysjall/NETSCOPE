"""Menu interaktif Noctura NETSCOPE.

Fungsi input (`ask`) bisa diganti saat pengujian. Ctrl+C membatalkan aksi
yang sedang berjalan dan kembali ke menu; Ctrl+D / EOF keluar dengan rapi.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from netscope import banner, ui
from netscope.config import DEFAULTS, config_path, save_config, update_value
from netscope.core import Session, summarize
from netscope.modules.reports import FORMATS
from netscope.utils import TargetError

AskFn = Callable[[str, Optional[str]], str]


class QuitMenu(Exception):
    """Dilempar saat input berakhir (EOF) atau pengguna memilih keluar."""


MENU_ITEMS: List[Tuple[str, str, str]] = [
    ("1", "Network Info", "Info mesin lokal, IP, interface"),
    ("2", "Resolve Host", "Domain menjadi alamat IPv4 / IPv6"),
    ("3", "Port Check", "Cek port TCP (tanpa root)"),
    ("4", "DNS Lookup", "A, AAAA, MX, NS, TXT, SOA + SPF/DMARC"),
    ("5", "Reverse DNS", "IP menjadi nama host (PTR)"),
    ("6", "Ping", "Latensi, packet loss, jitter"),
    ("7", "HTTP Check", "Status, redirect, header keamanan"),
    ("8", "SSL Inspector", "Sertifikat, masa berlaku, protokol"),
    ("9", "Full Audit", "Semua pengecekan untuk satu domain"),
    ("P", "Public IP", "Lihat IP publik (kirim 1 request, perlu izin)"),
    ("R", "Reports", "Simpan hasil sesi: HTML / MD / JSON / TXT"),
    ("S", "Settings", "Timeout, jumlah ping, DNS server"),
    ("H", "Help", "Bantuan singkat"),
    ("Q", "Quit", "Keluar"),
]


class Menu:
    def __init__(self, session: Session, ask: Optional[AskFn] = None,
                 show_banner: bool = True) -> None:
        self.session = session
        self.console = session.console
        self._ask = ask or self._console_ask
        self.show_banner = show_banner
        self.actions: Dict[str, Callable[[], None]] = {
            "1": self.network_info, "2": self.resolve, "3": self.ports,
            "4": self.dns, "5": self.reverse, "6": self.ping, "7": self.http,
            "8": self.ssl, "9": self.audit, "P": self.public_ip,
            "R": self.reports, "S": self.settings, "H": self.help,
        }

    # ----------------------------------------------------------------- input
    def _console_ask(self, prompt: str, default: Optional[str] = None) -> str:
        label = Text()
        label.append("%s " % ui.icon(self.console, "arrow"), style="pink")
        label.append(prompt, style="bold")
        if default:
            label.append(" [%s]" % default, style="muted")
        label.append(": ")
        try:
            value = self.console.input(label)
        except EOFError:
            raise QuitMenu()
        value = value.strip()
        return value if value else (default or "")

    def ask(self, prompt: str, default: Optional[str] = None) -> str:
        try:
            return self._ask(prompt, default).strip()
        except EOFError:
            raise QuitMenu()

    def confirm(self, prompt: str, default: bool = False) -> bool:
        hint = "Y/n" if default else "y/N"
        answer = self.ask("%s (%s)" % (prompt, hint)).lower()
        if not answer:
            return default
        return answer in ("y", "ya", "yes")

    # ---------------------------------------------------------------- tampilan
    def draw(self) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(justify="right", no_wrap=True)
        table.add_column(no_wrap=True)
        table.add_column(overflow="fold")
        for key, name, desc in MENU_ITEMS:
            style = "bad" if key == "Q" else "accent"
            table.add_row(Text("[%s]" % key, style=style), Text(name, style="bold"),
                          Text(desc, style="muted"))
        self.console.print(Panel(
            table, title=Text(" %s MENU UTAMA " % ui.icon(self.console, "moon"), style="title"),
            title_align="left", border_style="#4c3f91", box=ui.box_style(self.console),
            padding=(1, 3), expand=False))
        s = summarize(self.session.history)
        status = Text()
        status.append("sesi: ", style="muted")
        status.append("%d cek" % s["total"], style="accent2")
        status.append("  lulus ", style="muted")
        status.append(str(s["passed"]), style="ok")
        status.append("  gagal ", style="muted")
        status.append(str(s["failed"]), style="bad" if s["failed"] else "muted")
        self.console.print(status)

    # ------------------------------------------------------------------ aksi
    def network_info(self) -> None:
        self.session.local_info()

    def resolve(self) -> None:
        host = self.ask("Domain atau host")
        if host:
            self.session.resolve(host)

    def ports(self) -> None:
        target = self.ask("Host target")
        if not target:
            return
        spec = self.ask("Port (mis. 80,443 | 1-1024 | common | web)", "common")
        self.session.ports(target, spec)

    def dns(self) -> None:
        domain = self.ask("Domain")
        if not domain:
            return
        raw = self.ask("Tipe record (pisah koma, kosong = default)", "")
        types = [t.strip() for t in raw.split(",") if t.strip()] or None
        self.session.dns(domain, types)

    def reverse(self) -> None:
        ip = self.ask("Alamat IP")
        if ip:
            self.session.reverse(ip)

    def ping(self) -> None:
        target = self.ask("Host target")
        if not target:
            return
        raw = self.ask("Jumlah paket", str(self.session.config["ping_count"]))
        try:
            count = int(raw)
        except ValueError:
            ui.error_panel(self.console, "Jumlah paket harus berupa angka.")
            return
        self.session.ping(target, count)

    def http(self) -> None:
        url = self.ask("URL atau domain")
        if url:
            self.session.http(url)

    def ssl(self) -> None:
        target = self.ask("Host (opsional :port)")
        if target:
            self.session.ssl(target)

    def audit(self) -> None:
        target = self.ask("Domain untuk audit penuh")
        if not target:
            return
        results = self.session.full_audit(target)
        s = summarize(results)
        ui.section(self.console, "Ringkasan audit", target)
        ui.note(self.console, "%d dari %d pengecekan lulus." % (s["passed"], s["total"]),
                style="ok" if s["failed"] == 0 else "warn",
                mark="ok" if s["failed"] == 0 else "warn")
        if self.confirm("Simpan laporan HTML untuk audit ini?"):
            self.session.report("html", results=results)

    def public_ip(self) -> None:
        ui.note(self.console,
                "Fitur ini mengirim 1 request ke api.ipify.org untuk mengetahui IP publik Anda.",
                style="warn", mark="warn")
        if self.confirm("Lanjutkan?"):
            self.session.public_ip()
        else:
            ui.note(self.console, "Dibatalkan. Tidak ada data yang dikirim.")

    def reports(self) -> None:
        if not self.session.history:
            ui.error_panel(self.console, "Belum ada hasil di sesi ini.",
                           "Jalankan satu pengecekan dulu, lalu buat laporan.")
            return
        fmt = self.ask("Format (%s | all)" % " | ".join(FORMATS), "html").lower()
        self.session.report(fmt)

    def settings(self) -> None:
        cfg = self.session.config
        ui.section(self.console, "Settings", str(config_path()))
        ui.print_kv(self.console, [(k, cfg[k]) for k in DEFAULTS], "Konfigurasi aktif")
        key = self.ask("Kunci yang diubah (kosong = kembali)")
        if not key:
            return
        value = self.ask("Nilai baru untuk '%s'" % key)
        try:
            update_value(cfg, key, value)
        except TargetError as exc:
            ui.error_panel(self.console, str(exc))
            return
        ui.note(self.console, "%s = %s (berlaku untuk sesi ini)" % (key, cfg[key]),
                style="ok", mark="ok")
        if self.confirm("Simpan permanen ke file konfigurasi?"):
            try:
                path = save_config(cfg)
                ui.note(self.console, "Tersimpan: %s" % path, style="ok", mark="ok")
            except OSError as exc:
                ui.error_panel(self.console, "Gagal menyimpan: %s" % (exc.strerror or exc))

    def help(self) -> None:
        ui.section(self.console, "Bantuan")
        ui.print_kv(self.console, [
            ("Target", "domain (example.com), URL, IP, atau host:port"),
            ("Port", "80,443  |  1-1024  |  common  |  web (maks 100 port)"),
            ("Batal", "Ctrl+C membatalkan aksi dan kembali ke menu"),
            ("Keluar", "Q atau Ctrl+D"),
            ("Privasi", "Tidak ada telemetri. Hanya target yang Anda masukkan yang dihubungi."),
            ("CLI", "jalankan  netscope --help  untuk mode perintah langsung"),
        ], "Tips")

    # ------------------------------------------------------------- loop utama
    def dispatch(self, choice: str) -> bool:
        """Jalankan satu pilihan. Mengembalikan False bila harus keluar."""
        key = choice.strip().upper()
        if key in ("Q", "QUIT", "EXIT", "0"):
            return False
        action = self.actions.get(key)
        if action is None:
            ui.note(self.console, "Pilihan tidak dikenal: '%s'. Ketik H untuk bantuan."
                    % choice[:20], style="warn", mark="warn")
            return True
        try:
            action()
        except KeyboardInterrupt:
            self.console.print()
            ui.note(self.console, "Dibatalkan.", style="warn", mark="warn")
        except QuitMenu:
            raise
        except Exception as exc:  # jaring pengaman: menu tidak boleh crash
            ui.error_panel(self.console, "Terjadi kesalahan tak terduga: %s: %s"
                           % (exc.__class__.__name__, exc),
                           "Silakan coba lagi atau laporkan sebagai issue.")
        return True

    def run(self) -> int:
        if self.show_banner:
            banner.show_banner(self.console)
        ui.warnings_block(self.console, self.session.config_warnings)
        try:
            while True:
                self.draw()
                choice = self.ask("Pilih menu")
                if not choice:
                    continue
                if not self.dispatch(choice):
                    break
                if self.console.is_terminal:
                    ui.pause(self.console)
        except (QuitMenu, KeyboardInterrupt):
            self.console.print()
        self.console.print(Text("%s Sampai jumpa. Stay curious, stay safe."
                                % ui.icon(self.console, "moon"), style="accent"))
        return 0
