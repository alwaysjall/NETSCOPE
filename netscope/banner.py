"""Banner Noctura NETSCOPE: logo ASCII bergradien + panel info."""

from __future__ import annotations

import time
from typing import List, Sequence, Tuple

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

from netscope import __app_name__, __tagline__, __tool_name__, __version__, ui
from netscope.utils import detect_platform

LOGO_UNICODE = [
    "\u2588\u2588\u2588\u2557   \u2588\u2588\u2557 \u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2557\u2588\u2588\u2557   \u2588\u2588\u2557\u2588\u2588\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2588\u2588\u2588\u2557 ",
    "\u2588\u2588\u2588\u2588\u2557  \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2550\u2550\u255d\u255a\u2550\u2550\u2588\u2588\u2554\u2550\u2550\u255d\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557",
    "\u2588\u2588\u2554\u2588\u2588\u2557 \u2588\u2588\u2551\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551        \u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2551",
    "\u2588\u2588\u2551\u255a\u2588\u2588\u2557\u2588\u2588\u2551\u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2551        \u2588\u2588\u2551   \u2588\u2588\u2551   \u2588\u2588\u2551\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2557\u2588\u2588\u2554\u2550\u2550\u2588\u2588\u2551",
    "\u2588\u2588\u2551 \u255a\u2588\u2588\u2588\u2588\u2551\u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2557   \u2588\u2588\u2551   \u255a\u2588\u2588\u2588\u2588\u2588\u2588\u2554\u255d\u2588\u2588\u2551  \u2588\u2588\u2551\u2588\u2588\u2551  \u2588\u2588\u2551",
    "\u255a\u2550\u255d  \u255a\u2550\u2550\u2550\u255d \u255a\u2550\u2550\u2550\u2550\u2550\u255d  \u255a\u2550\u2550\u2550\u2550\u2550\u255d   \u255a\u2550\u255d    \u255a\u2550\u2550\u2550\u2550\u2550\u255d \u255a\u2550\u255d  \u255a\u2550\u255d\u255a\u2550\u255d  \u255a\u2550\u255d",
]

LOGO_ASCII = [
    " _   _  ___   ____ _____ _   _ ____      _    ",
    "| \\ | |/ _ \\ / ___|_   _| | | |  _ \\    / \\   ",
    "|  \\| | | | | |     | | | | | | |_) |  / _ \\  ",
    "| |\\  | |_| | |___  | | | |_| |  _ <  / ___ \\ ",
    "|_| \\_|\\___/ \\____| |_|  \\___/|_| \\_\\/_/   \\_\\",
]

#: Gradien violet -> cyan -> pink (dari atas ke bawah).
GRADIENT = ["#a78bfa", "#8b9cf8", "#60b5f5", "#22d3ee", "#7fb8f2", "#f472b6"]


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def gradient_color(stops: Sequence[str], t: float) -> str:
    """Warna pada posisi ``t`` (0..1) di sepanjang ``stops``."""
    t = max(0.0, min(1.0, t))
    if len(stops) == 1:
        return stops[0]
    pos = t * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    frac = pos - i
    r1, g1, b1 = _hex_to_rgb(stops[i])
    r2, g2, b2 = _hex_to_rgb(stops[i + 1])
    return "#%02x%02x%02x" % (_lerp(r1, r2, frac), _lerp(g1, g2, frac), _lerp(b1, b2, frac))


def gradient_text(line: str, stops: Sequence[str] = GRADIENT) -> Text:
    """Warnai satu baris secara horizontal dengan gradien."""
    text = Text(no_wrap=True)
    n = max(len(line) - 1, 1)
    for i, ch in enumerate(line):
        if ch == " ":
            text.append(ch)
        else:
            text.append(ch, style="bold " + gradient_color(stops, i / n))
    return text


def logo_lines(console: Console) -> List[str]:
    return LOGO_UNICODE if ui.is_unicode(console) else LOGO_ASCII


def build_logo(console: Console) -> Group:
    return Group(*[gradient_text(line) for line in logo_lines(console)])


def build_banner(console: Console, compact: bool = False) -> Group:
    """Susun banner lengkap sebagai renderable (mudah diuji)."""
    width = console.width
    logo = logo_lines(console)
    logo_w = max(len(line) for line in logo)
    parts = []
    if compact or width < logo_w + 4:
        title = Text()
        title.append("%s " % ui.icon(console, "moon"), style="pink")
        title.append(__app_name__.upper(), style="bold #c4b5fd")
        title.append("  %s  " % ui.icon(console, "dot"), style="muted")
        title.append(__tool_name__, style="bold #22d3ee")
        parts.append(Align.center(title))
    else:
        parts.append(Align.center(build_logo(console)))
        sub = Text()
        sub.append("%s  " % ui.icon(console, "star"), style="pink")
        sub.append("N E T S C O P E", style="bold #22d3ee")
        sub.append("  %s" % ui.icon(console, "star"), style="pink")
        parts.append(Align.center(sub))
    tag = Text(__tagline__, style="italic #8b8fa8")
    parts.append(Align.center(tag))
    info = Text()
    info.append("v%s" % __version__, style="accent")
    info.append("  %s  " % ui.icon(console, "dot"), style="muted")
    info.append(detect_platform(), style="accent2")
    info.append("  %s  " % ui.icon(console, "dot"), style="muted")
    info.append("tanpa root", style="ok")
    info.append("  %s  " % ui.icon(console, "dot"), style="muted")
    info.append("tanpa telemetri", style="ok")
    parts.append(Align.center(info))
    return Group(*parts)


def show_banner(console: Console, animate: bool = True, compact: bool = False) -> None:
    """Cetak banner. Animasi hanya aktif pada terminal interaktif."""
    banner = build_banner(console, compact=compact)
    panel = Panel(banner, border_style="#4c3f91", box=ui.box_style(console),
                  padding=(1, 2), expand=False if console.width > 100 else True)
    if animate and console.is_terminal and not console.is_dumb_terminal:
        lines = logo_lines(console)
        if console.width >= max(len(l) for l in lines) + 4 and not compact:
            for line in lines:
                console.print(Align.center(gradient_text(line)))
                time.sleep(0.04)
            console.clear()
    console.print(Align.center(panel))
    console.print()
