import json

import pytest

from netscope import ui
from netscope.modules import reports as rp
from netscope.utils import make_result

EVIL = '<script>alert(1)</script> & "q" [bold]x[/bold]'


@pytest.fixture
def results():
    return [
        make_result("http", "https://example.com", True,
                    {"status": 200, "chain": [{"url": "https://example.com", "status": 200}],
                     "headers": {"X": "should-be-skipped"}, "server": EVIL}, duration=0.12),
        make_result("ssl", "example.com:443", False, error="boom | pipe `tick`"),
    ]


def test_flatten():
    rows = dict(rp.flatten({"a": 1, "b": [1, 2], "c": [{"x": 1}, {"x": 2}], "d": None,
                            "e": True, "f": [], "headers": {"k": "v"}}))
    assert rows["a"] == "1" and rows["b"] == "1, 2"
    assert rows["c[1].x"] == "1" and rows["c[2].x"] == "2"
    assert rows["d"] == "-" and rows["e"] == "ya" and rows["f"] == "-"
    assert not any(k.startswith("headers") for k in rows)


def test_build_document(results):
    meta = rp.build_document(results)["report"]
    assert meta["checks"] == 2 and meta["passed"] == 1 and meta["failed"] == 1


def test_json_roundtrip(results):
    doc = json.loads(rp.to_json(rp.build_document(results)))
    assert doc["results"][0]["target"] == "https://example.com"
    assert doc["results"][0]["data"]["server"] == EVIL


def test_html_escapes_untrusted(results):
    out = rp.to_html(rp.build_document(results))
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out
    assert out.startswith("<!DOCTYPE html>") and "Noctura" in out
    assert "should-be-skipped" not in out
    assert "b-ok" in out and "b-bad" in out


def test_markdown_and_txt(results):
    md = rp.to_markdown(rp.build_document(results))
    assert md.startswith("# ") and "boom \\| pipe 'tick'" in md
    txt = rp.to_txt(rp.build_document(results))
    assert "[1] HTTP" in txt and "GAGAL" in txt and "Error   : boom" in txt


@pytest.mark.parametrize("fmt", rp.FORMATS)
def test_save_each_format(results, tmp_path, fmt):
    r = rp.save_report(results, fmt, str(tmp_path))
    assert r["ok"], r["error"]
    p = tmp_path / r["data"]["path"].split("/")[-1]
    assert p.is_file() and p.stat().st_size == r["data"]["bytes"] > 0
    assert p.suffix == "." + fmt


def test_no_overwrite(results, tmp_path):
    a = rp.save_report(results, "txt", str(tmp_path))["data"]["path"]
    b = rp.save_report(results, "txt", str(tmp_path))["data"]["path"]
    assert a != b and len(list(tmp_path.iterdir())) == 2


def test_save_all_and_alias(results, tmp_path):
    outs = rp.save_all(results, str(tmp_path))
    assert all(o["ok"] for o in outs) and len(outs) == 4
    assert rp.save_report(results, "markdown", str(tmp_path))["data"]["format"] == "md"


def test_errors(results, tmp_path):
    assert "Format tidak didukung" in rp.save_report(results, "pdf", str(tmp_path))["error"]
    assert "Belum ada hasil" in rp.save_report([], "html", str(tmp_path))["error"]
    blocker = tmp_path / "f"
    blocker.write_text("x")
    r = rp.save_report(results, "html", str(blocker / "sub"))
    assert not r["ok"] and "Gagal menulis" in r["error"]


def test_filename_is_safe():
    name = rp.make_filename([{"target": "../../etc/passwd"}], "html")
    assert "/" not in name and ".." not in name and name.endswith(".html")


def test_default_dir_exists():
    assert rp.default_report_dir().is_dir()


def test_render(results, tmp_path):
    console = ui.make_console(width=100)
    rp.render(console, rp.save_report(results, "html", str(tmp_path)))
    rp.render(console, rp.save_report([], "html", str(tmp_path)))
