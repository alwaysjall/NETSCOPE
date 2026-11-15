"""Tema & komponen UI terminal bersama (Rich).

Semua nilai dari jaringan (header HTTP, nama sertifikat, TXT record, dst.)
dibungkus ``rich.text.Text`` sehingga tidak pernah diinterpretasi sebagai markup
Rich. Ini mencegah tampilan rusak / injeksi gaya dari server yang tidak tepercaya.
"""

from __future__ import annotations

import os
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

PALETTE = {
    "violet": "#a78bfa",
    "cyan": "#22d3ee",
    "pink": "#f472b6",
    "green": "#34d399",
    "yellow": "#fbbf24",
    "red": "#f87171",
    "gray": "#6b7280",
    "blue": "#93c5fd",
}

THEME = Theme({
    "accent": "bold #a78bfa",
    "accent2": "#22d3ee",
    "pink": "#f472b6",
    "ok": "bold #34d399",
    "warn": "bold #fbbf24",
    "bad": "bold #f87171",
    "muted": "#6b7280",
    "key": "#93c5fd",
    "title": "bold #c4b5fd",
})

_ICONS = {
    "ok": ("\u2714", "+"),
    "bad": ("\u2718", "x"),
    "warn": ("\u25b2", "!"),
    "info": ("\u25cf", "*"),
    "arrow": ("\u276f", ">"),
    "dot": ("\u2022", "-"),
    "moon": ("\u263e", "("),
    "star": ("\u2726", "*"),
}

_STATE_STYLE = {
    "open": "ok", "closed": "muted", "filtered": "warn", "error": "bad",
    "ok": "ok", "valid": "ok", "present": "ok", "missing": "bad",
    "expired": "bad", "expiring_soon": "warn", "no_records": "muted",
    "timeout": "warn",
}


def make_console(no_color: bool = False, record: bool = False,
                 width: Optional[int] = None, file: Any = None,
                 force_terminal: Optional[bool] = None) -> Console:
    """Buat Console bertema Noctura. ``NO_COLOR`` di environment dihormati."""
    return Console(
        theme=THEME,
        highlight=False,
        no_color=True if (no_color or os.environ.get("NO_COLOR")) else None,
        record=record,
        width=width,
        file=file,
        force_terminal=force_terminal,
    )


def is_unicode(console: Console) -> bool:
    enc = (console.encoding or "").lower().replace("-", "").replace("_", "")
    return enc.startswith("utf")


def icon(console: Console, name: str) -> str:
    uni, asc = _ICONS[name]
    return uni if is_unicode(console) else asc


def box_style(console: Console) -> box.Box:
    return box.ROUNDED if is_unicode(console) else box.ASCII


def state_style(state: Any) -> str:
    return _STATE_STYLE.get(str(state).lower(), "")


def cell(value: Any, style: str = "") -> Text:
    """Ubah nilai apa pun menjadi Text aman (tanpa markup)."""
    if isinstance(value, Text):
        return value
    if value is None or value == "":
        return Text("-", style="muted")
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return Text(text, style=style)


def status_text(console: Console, ok: bool, yes: str = "OK", no: str = "GAGAL") -> Text:
    if ok:
        return Text("%s %s" % (icon(console, "ok"), yes), style="ok")
    return Text("%s %s" % (icon(console, "bad"), no), style="bad")


def section(console: Console, title: str, subtitle: str = "") -> None:
    """Judul bagian dengan garis pemisah."""
    label = Text()
    label.append(" %s " % icon(console, "moon"), style="pink")
    label.append(title.upper(), style="title")
    if subtitle:
        label.append("  %s  " % icon(console, "dot"), style="muted")
        label.append(subtitle, style="accent2")
    label.append(" ")
    console.print()
    console.print(Rule(label, style="#4c3f91", characters="\u2500" if is_unicode(console) else "-"))


def result_header(console: Console, result: dict) -> None:
    """Satu baris ringkas: status, modul, target dan durasi."""
    line = Text()
    line.append(status_text(console, bool(result.get("ok"))))
    line.append("  ")
    line.append(str(result.get("module", "")).upper(), style="accent")
    line.append("  %s  " % icon(console, "arrow"), style="muted")
    line.append(cell(result.get("target", "")))
    line.append("   %.0f ms" % float(result.get("duration_ms", 0.0) or 0.0), style="muted")
    console.print(line)


def kv_table(console: Console, rows: Iterable[Tuple[str, Any]],
             title: Optional[str] = None) -> Table:
    """Tabel dua kolom (kunci -> nilai) dalam panel."""
    table = Table.grid(padding=(0, 2))
    table.add_column(style="key", no_wrap=True)
    table.add_column(overflow="fold")
    for key, value in rows:
        table.add_row(Text(str(key)), cell(value))
    return table


def print_kv(console: Console, rows: Sequence[Tuple[str, Any]], title: str = "") -> None:
    panel = Panel(
        kv_table(console, rows),
        title=Text(title, style="title") if title else None,
        title_align="left",
        border_style="#4c3f91",
        box=box_style(console),
        padding=(0, 2),
        expand=False,
    )
    console.print(panel)


def data_table(console: Console, columns: Sequence[str], rows: Sequence[Sequence[Any]],
               title: str = "") -> None:
    table = Table(
        box=box_style(console),
        border_style="#4c3f91",
        header_style="accent",
        title=Text(title, style="title") if title else None,
        title_justify="left",
        expand=False,
    )
    for name in columns:
        table.add_column(Text(name), overflow="fold")
    for row in rows:
        table.add_row(*[cell(v) for v in row])
    console.print(table)


def error_panel(console: Console, message: str, hint: Optional[str] = None,
                title: str = "Error") -> None:
    body = Text()
    body.append(cell(message))
    if hint:
        body.append("\n")
        body.append("%s " % icon(console, "info"), style="warn")
        body.append(cell(hint, "muted"))
    console.print(Panel(body, title=Text(title, style="bad"), title_align="left",
                        border_style="#f87171", box=box_style(console), expand=False))


def note(console: Console, message: str, style: str = "muted", mark: str = "info") -> None:
    line = Text()
    line.append("%s " % icon(console, mark), style=style or "muted")
    line.append(cell(message, style))
    console.print(line)


def warnings_block(console: Console, warnings: Iterable[str]) -> None:
    for item in warnings:
        note(console, item, style="warn", mark="warn")


def render_failure(console: Console, result: dict, hint: Optional[str] = None) -> None:
    """Tampilkan kegagalan standar bila ada pesan error."""
    if result.get("error"):
        error_panel(console, str(result["error"]), hint=hint)


def pause(console: Console, message: str = "Tekan Enter untuk melanjutkan") -> None:
    """Jeda interaktif; EOF/Ctrl+C diperlakukan sebagai lanjut."""
    try:
        console.input(Text("\n%s %s " % (icon(console, "dot"), message), style="muted"))
    except (EOFError, KeyboardInterrupt):
        console.print()


def ms_style(value: Optional[float]) -> str:
    """Warna latensi: hijau cepat, kuning sedang, merah lambat."""
    if value is None:
        return "muted"
    if value < 100:
        return "ok"
    if value < 300:
        return "warn"
    return "bad"


def chunk(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]
