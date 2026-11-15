"""Pembuat laporan: JSON, Markdown, TXT, dan HTML (dark theme, satu file).

Laporan hanya disimpan lokal di folder ``reports/``; tidak ada yang dikirim
ke mana pun. Semua nilai di-escape (HTML) sehingga data dari server yang
tidak tepercaya tidak dapat menyisipkan markup/skrip ke laporan.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rich.console import Console

from netscope import __app_name__, __tool_name__, __version__, ui
from netscope.utils import detect_platform, now_iso, safe_filename

FORMATS = ("json", "md", "txt", "html")
_SKIP_KEYS = {"headers"}  # header mentah terlalu panjang; ringkasan sudah ada


# --------------------------------------------------------------------------
# Lokasi folder laporan
# --------------------------------------------------------------------------
def default_report_dir() -> Path:
    """``reports/`` di root proyek bila bisa ditulis, jika tidak ``~/netscope-reports``."""
    project = Path(__file__).resolve().parents[2] / "reports"
    try:
        project.mkdir(parents=True, exist_ok=True)
        if os.access(str(project), os.W_OK):
            return project
    except OSError:
        pass
    return Path.home() / "netscope-reports"


def resolve_dir(configured: str = "") -> Path:
    path = Path(configured).expanduser() if configured else default_report_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_filename(results: List[Dict[str, Any]], fmt: str, stamp: Optional[datetime] = None) -> str:
    stamp = stamp or datetime.now()
    first = results[0]["target"] if results else "session"
    label = safe_filename(first, 30)
    return "noctura_%s_%s.%s" % (label, stamp.strftime("%Y%m%d_%H%M%S"), fmt)


def unique_path(directory: Path, name: str) -> Path:
    """Hindari menimpa file yang ada: tambahkan -1, -2, ... bila perlu."""
    candidate = directory / name
    stem, suffix, i = candidate.stem, candidate.suffix, 1
    while candidate.exists():
        candidate = directory / ("%s-%d%s" % (stem, i, suffix))
        i += 1
    return candidate


# --------------------------------------------------------------------------
# Ratakan data hasil menjadi pasangan (kunci, nilai) yang mudah dibaca
# --------------------------------------------------------------------------
def _scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "ya" if value else "tidak"
    return str(value).replace("\r", " ").replace("\n", " ")


def flatten(data: Any, prefix: str = "") -> List[Tuple[str, str]]:
    """Ratakan dict/list bertingkat menjadi daftar ``(path, nilai)``."""
    rows: List[Tuple[str, str]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in _SKIP_KEYS:
                continue
            rows.extend(flatten(value, "%s.%s" % (prefix, key) if prefix else str(key)))
    elif isinstance(data, (list, tuple)):
        if not data:
            rows.append((prefix, "-"))
        elif all(not isinstance(v, (dict, list, tuple)) for v in data):
            rows.append((prefix, ", ".join(_scalar(v) for v in data)))
        else:
            for i, value in enumerate(data, 1):
                rows.extend(flatten(value, "%s[%d]" % (prefix, i)))
    else:
        rows.append((prefix, _scalar(data)))
    return rows


def build_document(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Bungkus hasil dengan metadata laporan."""
    return {
        "report": {
            "tool": "%s %s" % (__app_name__, __tool_name__),
            "version": __version__,
            "generated_at": now_iso(),
            "platform": detect_platform(),
            "checks": len(results),
            "passed": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
        },
        "results": results,
    }


# --------------------------------------------------------------------------
# Renderer per format
# --------------------------------------------------------------------------
def to_json(doc: Dict[str, Any]) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, default=str) + "\n"


def to_txt(doc: Dict[str, Any]) -> str:
    meta = doc["report"]
    line = "=" * 64
    out = [line, "  %s" % meta["tool"], "  Observe the network. Own the night.", line,
           "Versi      : %s" % meta["version"],
           "Dibuat     : %s" % meta["generated_at"],
           "Platform   : %s" % meta["platform"],
           "Pengecekan : %d (lulus %d, gagal %d)" % (meta["checks"], meta["passed"], meta["failed"]),
           ""]
    for i, res in enumerate(doc["results"], 1):
        out.append("-" * 64)
        out.append("[%d] %s -> %s   [%s]" % (i, str(res["module"]).upper(), res["target"],
                                             "OK" if res["ok"] else "GAGAL"))
        out.append("-" * 64)
        out.append("Waktu   : %s (%.0f ms)" % (res["timestamp"], res["duration_ms"]))
        if res.get("error"):
            out.append("Error   : %s" % res["error"])
        for key, value in flatten(res.get("data", {})):
            out.append("  %-34s %s" % (key, value))
        out.append("")
    return "\n".join(out) + "\n"


def _md_escape(text: str) -> str:
    return str(text).replace("|", "\\|").replace("`", "'")


def to_markdown(doc: Dict[str, Any]) -> str:
    meta = doc["report"]
    out = ["# \u263e %s" % meta["tool"], "",
           "> Observe the network. Own the night.", "",
           "| Item | Nilai |", "|---|---|",
           "| Versi | %s |" % _md_escape(meta["version"]),
           "| Dibuat | %s |" % _md_escape(meta["generated_at"]),
           "| Platform | %s |" % _md_escape(meta["platform"]),
           "| Pengecekan | %d (lulus %d, gagal %d) |" % (meta["checks"], meta["passed"], meta["failed"]),
           ""]
    for i, res in enumerate(doc["results"], 1):
        badge = "OK" if res["ok"] else "GAGAL"
        out += ["## %d. %s - `%s` [%s]" % (i, str(res["module"]).upper(),
                                           _md_escape(res["target"]), badge), "",
                "_%s - %.0f ms_" % (res["timestamp"], res["duration_ms"]), ""]
        if res.get("error"):
            out += ["> **Error:** %s" % _md_escape(res["error"]), ""]
        rows = flatten(res.get("data", {}))
        if rows:
            out += ["| Kunci | Nilai |", "|---|---|"]
            out += ["| `%s` | %s |" % (_md_escape(k), _md_escape(v)) for k, v in rows]
            out.append("")
    return "\n".join(out) + "\n"


_HTML_CSS = """
:root{--bg:#0b0a14;--panel:#14122a;--line:#2a2552;--txt:#e5e7eb;--mut:#8b8fa8;
--vio:#a78bfa;--cya:#22d3ee;--pink:#f472b6;--ok:#34d399;--bad:#f87171}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 10% -10%,#1d1650 0,transparent 60%),
radial-gradient(900px 500px at 100% 0,#3a1450 0,transparent 55%),var(--bg);
color:var(--txt);font:15px/1.55 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:32px 16px}
main{max-width:980px;margin:0 auto}
header{padding:28px;border:1px solid var(--line);border-radius:18px;
background:linear-gradient(135deg,rgba(167,139,250,.12),rgba(34,211,238,.06));margin-bottom:22px}
h1{margin:0;font-size:30px;letter-spacing:.5px;
background:linear-gradient(90deg,var(--vio),var(--cya),var(--pink));-webkit-background-clip:text;
background-clip:text;color:transparent}
.tag{color:var(--mut);margin:.3rem 0 1rem;font-style:italic}
.meta{display:flex;flex-wrap:wrap;gap:10px}
.chip{border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 12px;
font-size:13px;color:var(--mut)}.chip b{color:var(--txt);font-weight:600}
section.card{border:1px solid var(--line);background:var(--panel);border-radius:16px;
padding:20px;margin:16px 0}
h2{margin:0 0 4px;font-size:18px;color:#c4b5fd}
.sub{color:var(--mut);font-size:13px;margin-bottom:12px}
.badge{display:inline-block;border-radius:6px;padding:1px 9px;font-size:12px;font-weight:700;
margin-left:8px;vertical-align:middle}
.b-ok{background:rgba(52,211,153,.15);color:var(--ok);border:1px solid rgba(52,211,153,.4)}
.b-bad{background:rgba(248,113,113,.15);color:var(--bad);border:1px solid rgba(248,113,113,.4)}
.err{border-left:3px solid var(--bad);background:rgba(248,113,113,.08);padding:8px 12px;
border-radius:6px;margin:8px 0;color:#fecaca}
table{width:100%;border-collapse:collapse;font-size:13.5px}
td{border-top:1px solid var(--line);padding:6px 8px;vertical-align:top;word-break:break-word}
td:first-child{color:var(--cya);width:34%;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}
footer{text-align:center;color:var(--mut);font-size:12.5px;margin:28px 0 4px}
@media print{body{background:#fff;color:#000}section.card,header{border-color:#ccc;background:#fff}
td:first-child{color:#036}}
"""


def to_html(doc: Dict[str, Any]) -> str:
    esc = lambda v: html.escape(str(v), quote=True)  # noqa: E731
    meta = doc["report"]
    parts = [
        "<!DOCTYPE html><html lang=\"id\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        "<title>Noctura NETSCOPE Report</title><style>%s</style></head><body><main>" % _HTML_CSS,
        "<header><h1>\u263e %s</h1><p class=\"tag\">Observe the network. Own the night.</p>"
        % esc(meta["tool"]),
        "<div class=\"meta\">",
    ]
    for label, value in (("Versi", meta["version"]), ("Dibuat", meta["generated_at"]),
                         ("Platform", meta["platform"]),
                         ("Lulus", "%d/%d" % (meta["passed"], meta["checks"])),
                         ("Gagal", meta["failed"])):
        parts.append("<span class=\"chip\">%s: <b>%s</b></span>" % (esc(label), esc(value)))
    parts.append("</div></header>")
    for i, res in enumerate(doc["results"], 1):
        cls, txt = ("b-ok", "OK") if res["ok"] else ("b-bad", "GAGAL")
        parts.append(
            "<section class=\"card\"><h2>%d. %s &rarr; %s<span class=\"badge %s\">%s</span></h2>"
            "<div class=\"sub\">%s &middot; %.0f ms</div>"
            % (i, esc(str(res["module"]).upper()), esc(res["target"]), cls, txt,
               esc(res["timestamp"]), res["duration_ms"]))
        if res.get("error"):
            parts.append("<div class=\"err\">%s</div>" % esc(res["error"]))
        rows = flatten(res.get("data", {}))
        if rows:
            parts.append("<table>")
            parts.extend("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v)) for k, v in rows)
            parts.append("</table>")
        parts.append("</section>")
    parts.append("<footer>Dibuat oleh %s &middot; hanya disimpan lokal &middot; v%s</footer>"
                 "</main></body></html>" % (esc(meta["tool"]), esc(meta["version"])))
    return "".join(parts)


_RENDERERS = {"json": to_json, "md": to_markdown, "txt": to_txt, "html": to_html}


# --------------------------------------------------------------------------
# API utama
# --------------------------------------------------------------------------
def save_report(results: List[Dict[str, Any]], fmt: str = "html",
                directory: str = "") -> Dict[str, Any]:
    """Simpan ``results`` ke file. Mengembalikan dict hasil standar (``data.path``)."""
    from netscope.utils import make_result
    fmt = str(fmt).lower().strip()
    if fmt == "markdown":
        fmt = "md"
    if fmt not in FORMATS:
        return make_result("report", "-", False,
                           error="Format tidak didukung: %s (pilih: %s)" % (fmt, ", ".join(FORMATS)))
    if not results:
        return make_result("report", "-", False,
                           error="Belum ada hasil untuk dilaporkan. Jalankan pengecekan dulu.")
    try:
        folder = resolve_dir(directory)
        path = unique_path(folder, make_filename(results, fmt))
        content = _RENDERERS[fmt](build_document(results))
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return make_result("report", "-", False,
                           error="Gagal menulis laporan: %s" % (exc.strerror or exc))
    return make_result("report", str(path.name), True,
                       {"path": str(path), "format": fmt, "bytes": path.stat().st_size,
                        "checks": len(results)})


def save_all(results: List[Dict[str, Any]], directory: str = "") -> List[Dict[str, Any]]:
    return [save_report(results, fmt, directory) for fmt in FORMATS]


def render(console: Console, result: Dict[str, Any]) -> None:
    ui.section(console, "Report")
    if not result["ok"]:
        ui.render_failure(console, result)
        return
    d = result["data"]
    ui.print_kv(console, [("File", d["path"]), ("Format", d["format"].upper()),
                          ("Ukuran", "%d byte" % d["bytes"]),
                          ("Pengecekan", d["checks"])], "Laporan tersimpan")
