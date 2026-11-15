import json
import subprocess
import sys

import pytest

from netscope import banner, ui
from netscope.config import DEFAULTS
from netscope.core import Session, summarize
from netscope.main import build_parser, main
from netscope.menu import Menu


def make_session(quiet=True, **cfg):
    config = dict(DEFAULTS, **cfg)
    return Session(ui.make_console(width=100), config, quiet=quiet)


# ------------------------------------------------------------------- core
def test_session_records_history(tcp_listener):
    s = make_session()
    s.resolve("localhost")
    s.ports("127.0.0.1", str(tcp_listener))
    s.ping("127.0.0.1:%d" % tcp_listener, 1, "tcp")
    assert len(s.history) == 3
    assert summarize(s.history) == {"total": 3, "passed": 3, "failed": 0}


def test_session_bad_inputs_never_raise():
    s = make_session()
    assert not s.ports("127.0.0.1", "99999")["ok"]
    assert not s.ports("bad host", "80")["ok"]
    assert not s.dns("bad host")["ok"]
    assert not s.reverse("nope")["ok"]
    assert not s.http("")["ok"]
    assert not s.ssl("")["ok"]
    assert not s.full_audit("bad host!")[0]["ok"]


def test_history_is_capped():
    s = make_session()
    for i in range(250):
        s.record({"ok": True, "n": i})
    assert len(s.history) == 200 and s.history[-1]["n"] == 249


def test_full_audit_local(http_server, monkeypatch):
    """Audit pada IP: DNS dilewati; kegagalan satu langkah tidak menghentikan yang lain."""
    s = make_session()
    out = s.full_audit("127.0.0.1")
    modules = [r["module"] for r in out]
    assert modules == ["network.resolve", "ping", "http", "ssl"]
    assert "dns" not in modules


def test_report_from_session(tmp_path):
    s = make_session(report_dir=str(tmp_path))
    assert not s.report("html")[0]["ok"]  # riwayat kosong
    s.resolve("localhost")
    outs = s.report("all")
    assert len(outs) == 4 and all(o["ok"] for o in outs)
    assert len(list(tmp_path.iterdir())) == 4


def test_session_loads_config_when_none():
    s = Session(ui.make_console(), None, quiet=True)
    assert s.config["timeout"] == DEFAULTS["timeout"]


# ------------------------------------------------------------------- CLI
def test_parser_basics():
    p = build_parser()
    a = p.parse_args(["ping", "example.com", "-c", "2", "-m", "tcp", "--json"])
    assert (a.command, a.host, a.count, a.method, a.json) == ("ping", "example.com", 2, "tcp", True)
    assert p.parse_args(["ports", "x", "-p", "web"]).ports == "web"


def test_main_version_and_help(capsys):
    assert main(["--version"]) == 0
    assert "Noctura NETSCOPE" in capsys.readouterr().out
    assert main(["--help"]) == 0
    assert main(["ping"]) == 2           # argumen wajib hilang
    assert main(["bogus"]) == 2
    assert main(["ports", "x", "--report", "pdf"]) == 2


def test_main_json_output_is_pure(capsys, tcp_listener):
    code = main(["ports", "127.0.0.1", "-p", str(tcp_listener), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)          # stdout HARUS JSON murni (tanpa banner/Rich)
    assert code == 0 and data["ok"] and data["data"]["counts"]["open"] == 1


def test_main_failure_exit_codes(capsys):
    assert main(["resolve", "bad host", "--json", "--no-color"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["error"]
    assert main(["ping", "127.0.0.1", "-t", "0", "--json"]) == 2   # timeout di luar batas
    assert main(["ping", "127.0.0.1", "-c", "99", "--json"]) == 1


def test_main_audit_json_list(capsys, http_server):
    code = main(["audit", "127.0.0.1", "--json", "-t", "3"])
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list) and len(data) == 4
    assert code in (0, 1)


def test_main_report_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NETSCOPE_REPORT_DIR", str(tmp_path))
    assert main(["resolve", "localhost", "--report", "md", "--no-banner", "--no-color"]) == 0
    assert len(list(tmp_path.glob("*.md"))) == 1
    capsys.readouterr()
    assert main(["resolve", "localhost", "--report", "json", "--json"]) == 0
    json.loads(capsys.readouterr().out)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_public_ip_requires_consent(capsys, monkeypatch):
    called = []
    monkeypatch.setattr("netscope.modules.network.requests.get",
                        lambda *a, **k: called.append(1))
    assert main(["public-ip", "--json"]) == 1
    assert "persetujuan" in json.loads(capsys.readouterr().out)["error"]
    assert called == []     # tidak ada request tanpa izin


def test_public_ip_with_yes(capsys, monkeypatch):
    class R:
        text = "203.0.113.9"

        def raise_for_status(self):
            pass

    monkeypatch.setattr("netscope.modules.network.requests.get", lambda *a, **k: R())
    assert main(["public-ip", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["ip"] == "203.0.113.9"


def test_config_command(capsys):
    assert main(["config", "--set", "timeout", "12"]) == 0
    assert main(["config", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"timeout": 12.0' in out
    assert main(["config", "--set", "timeout", "abc"]) == 2
    assert main(["config", "--set", "nope", "1"]) == 2


def test_no_color_env(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    console = ui.make_console(file=__import__("io").StringIO(), force_terminal=True)
    with console.capture() as cap:
        console.print("[ok]x[/ok]")
    import re
    # NO_COLOR melarang warna; atribut seperti tebal (\x1b[1m) tetap diperbolehkan.
    colors = re.findall(r"\x1b\[([0-9;]*)m", cap.get())
    assert not any(re.search(r"(^|;)(3[0-8]|4[0-8]|9[0-7]|10[0-7])(;|$)", c) for c in colors)


def test_subprocess_entrypoint():
    r = subprocess.run([sys.executable, "-m", "netscope", "--version"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "NETSCOPE" in r.stdout


# ------------------------------------------------------------------- menu
class Scripted:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt, default=None):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        val = self.answers.pop(0)
        return val if val else (default or "")


def run_menu(answers, **cfg):
    s = make_session(quiet=False, **cfg)
    s.console = ui.make_console(width=100, file=__import__("io").StringIO())
    m = Menu(s, ask=Scripted(answers), show_banner=False)
    return m.run(), s, s.console.file.getvalue()


def test_menu_quit_and_eof():
    code, _, out = run_menu(["q"])
    assert code == 0 and "Sampai jumpa" in out
    code, _, out = run_menu([])          # EOF langsung
    assert code == 0 and "Sampai jumpa" in out


def test_menu_unknown_choice_and_empty():
    code, s, out = run_menu(["", "zzz", "q"])
    assert code == 0 and "Pilihan tidak dikenal" in out and s.history == []


def test_menu_flow_resolve_ports_report(tcp_listener, tmp_path):
    code, s, out = run_menu(
        ["2", "localhost", "3", "127.0.0.1", str(tcp_listener), "r", "md", "q"],
        report_dir=str(tmp_path))
    assert code == 0 and len(s.history) == 2
    assert len(list(tmp_path.glob("*.md"))) == 1


def test_menu_public_ip_declined_sends_nothing(monkeypatch):
    called = []
    monkeypatch.setattr("netscope.modules.network.requests.get", lambda *a, **k: called.append(1))
    _, _, out = run_menu(["p", "n", "q"])
    assert called == [] and "Tidak ada data yang dikirim" in out


def test_menu_report_without_history():
    _, _, out = run_menu(["r", "q"])
    assert "Belum ada hasil" in out


def test_menu_settings_validation_and_save():
    _, s, out = run_menu(["s", "timeout", "999", "q"])
    assert "timeout harus" in out and s.config["timeout"] == DEFAULTS["timeout"]
    _, s, out = run_menu(["s", "timeout", "20", "y", "q"])
    assert s.config["timeout"] == 20.0 and "Tersimpan" in out


def test_menu_ping_non_numeric():
    _, s, out = run_menu(["6", "127.0.0.1", "abc", "q"])
    assert "harus berupa angka" in out and s.history == []


def test_menu_survives_unexpected_exception(monkeypatch):
    s = make_session(quiet=False)
    s.console = ui.make_console(width=100, file=__import__("io").StringIO())
    monkeypatch.setattr(s, "resolve", lambda h: (_ for _ in ()).throw(RuntimeError("kaboom")))
    m = Menu(s, ask=Scripted(["2", "x.com", "q"]), show_banner=False)
    assert m.run() == 0 and "kaboom" in s.console.file.getvalue()


def test_menu_keyboard_interrupt_returns_to_menu(monkeypatch):
    s = make_session(quiet=False)
    s.console = ui.make_console(width=100, file=__import__("io").StringIO())
    monkeypatch.setattr(s, "resolve", lambda h: (_ for _ in ()).throw(KeyboardInterrupt()))
    m = Menu(s, ask=Scripted(["2", "x.com", "q"]), show_banner=False)
    assert m.run() == 0 and "Dibatalkan" in s.console.file.getvalue()


def test_menu_help_and_info():
    _, s, out = run_menu(["h", "1", "q"])
    assert "Tips" in out and "Sistem" in out and len(s.history) == 1


# ------------------------------------------------------------ banner / ui
@pytest.mark.parametrize("width", [30, 50, 80, 120])
def test_banner_renders_at_any_width(width):
    console = ui.make_console(width=width, file=__import__("io").StringIO())
    banner.show_banner(console, animate=False)
    out = console.file.getvalue()
    assert "NETSCOPE" in out or "N E T S C O P E" in out


def test_banner_ascii_fallback():
    class AsciiConsole:
        encoding = "ascii"

    assert not ui.is_unicode(AsciiConsole())
    assert banner.LOGO_ASCII and all(ord(c) < 128 for l in banner.LOGO_ASCII for c in l)
    assert ui.icon(AsciiConsole(), "ok") == "+"


def test_gradient_bounds():
    assert banner.gradient_color(banner.GRADIENT, -1) == banner.GRADIENT[0]
    assert banner.gradient_color(banner.GRADIENT, 2) == banner.GRADIENT[-1]
    assert banner.gradient_color(["#000000", "#ffffff"], 0.5) == "#808080"


def test_ui_cell_is_markup_safe():
    t = ui.cell("[red]boom[/red]\x1b[31m\nline2")
    assert "[red]" in t.plain and "\x1b" not in t.plain and "\n" not in t.plain
    assert ui.cell(None).plain == "-" and ui.cell("").plain == "-"
