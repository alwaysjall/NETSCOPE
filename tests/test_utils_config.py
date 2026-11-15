import json

import pytest

from netscope import config, utils
from netscope.utils import TargetError


# ------------------------------------------------------------- validate_host
@pytest.mark.parametrize("raw,expected", [
    ("Example.COM", "example.com"),
    ("example.com.", "example.com"),
    ("  a-b.example.org  ", "a-b.example.org"),
    ("127.0.0.1", "127.0.0.1"),
    ("::1", "::1"),
    ("[2001:db8::1]", "2001:db8::1"),
    ("localhost", "localhost"),
    ("bücher.de", "xn--bcher-kva.de"),
])
def test_validate_host_ok(raw, expected):
    assert utils.validate_host(raw) == expected


@pytest.mark.parametrize("raw", [
    "", "   ", None, "bad host", "a..b", "-bad.com", "bad-.com", "exa mple.com",
    "999.1.1.1", "1.2.3", "host\nname", "a" * 64 + ".com", ("a." * 130) + "com",
    "fe80::1%eth0", "exa$mple.com", "http://x",
])
def test_validate_host_bad(raw):
    with pytest.raises(TargetError):
        utils.validate_host(raw)


# -------------------------------------------------------------- parse_target
@pytest.mark.parametrize("raw,default,expected", [
    ("example.com", None, ("example.com", None)),
    ("example.com:8443", None, ("example.com", 8443)),
    ("https://example.com/path?q=1", None, ("example.com", None)),
    ("https://example.com:444/x", 443, ("example.com", 444)),
    ("example.com", 443, ("example.com", 443)),
    ("192.0.2.1", 80, ("192.0.2.1", 80)),
    ("[2001:db8::1]:443", None, ("2001:db8::1", 443)),
    ("2001:db8::1", 443, ("2001:db8::1", 443)),
    ("user@example.com", None, ("example.com", None)),
])
def test_parse_target_ok(raw, default, expected):
    assert utils.parse_target(raw, default) == expected


@pytest.mark.parametrize("raw", ["", None, "example.com:99999", "example.com:0",
                                 "example.com:abc", "://", "http://"])
def test_parse_target_bad(raw):
    with pytest.raises(TargetError):
        utils.parse_target(raw)


# ------------------------------------------------------------- normalize_url
def test_normalize_url():
    assert utils.normalize_url("example.com") == "https://example.com"
    assert utils.normalize_url("http://example.com/a") == "http://example.com/a"
    for bad in ("", "ftp://example.com", "file:///etc/passwd", "javascript:alert(1)",
                "http://", "https://exa mple.com", "http://example.com:70000",
                "http://-bad.com"):
        with pytest.raises(TargetError):
            utils.normalize_url(bad)


# --------------------------------------------------------------- parse_ports
def test_parse_ports():
    assert utils.parse_ports("80,443") == [80, 443]
    assert utils.parse_ports("443, 80, 80") == [80, 443]
    assert utils.parse_ports("8000-8003") == [8000, 8001, 8002, 8003]
    assert utils.parse_ports("web") == utils.WEB_PORTS
    assert 22 in utils.parse_ports("common")
    assert utils.parse_ports("22,web") == sorted(set([22] + utils.WEB_PORTS))


def test_parse_ports_tolerates_empty_tokens():
    assert utils.parse_ports("80,,") == [80]
    assert utils.parse_ports(" 80 , 443 ,") == [80, 443]


@pytest.mark.parametrize("spec", ["", None, "0", "65536", "abc", "10-5", "1-1000",
                                  ",", "-1", "1-65535"])
def test_parse_ports_bad(spec):
    with pytest.raises(TargetError):
        utils.parse_ports(spec)


# --------------------------------------------------------------------- misc
def test_safe_filename_and_fmt():
    assert utils.safe_filename("a/b\\c:d*e") == "a_b_c_d_e"
    assert utils.safe_filename("../../etc/passwd") == "etc_passwd"
    assert utils.safe_filename("") == "report"
    assert len(utils.safe_filename("x" * 200)) == 40
    assert utils.fmt_ms(None) == "-"
    assert utils.fmt_ms(12.345) == "12.3 ms"


def test_make_result_shape():
    r = utils.make_result("m", "t", True, {"a": 1}, duration=0.5)
    assert set(r) == {"module", "target", "ok", "error", "timestamp", "duration_ms", "data"}
    assert r["duration_ms"] == 500.0 and r["data"] == {"a": 1}
    assert utils.make_result("m", "t", False, error="x")["data"] == {}


def test_detect_platform(monkeypatch):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert utils.detect_platform() == "Termux"
    monkeypatch.delenv("PREFIX")
    assert isinstance(utils.detect_platform(), str)


# -------------------------------------------------------------------- config
def test_defaults_when_no_file():
    cfg, warns = config.load_config()
    assert cfg == config.DEFAULTS and warns == []


def test_save_and_load_roundtrip():
    cfg, _ = config.load_config()
    config.update_value(cfg, "timeout", "15")
    config.update_value(cfg, "dns_server", "1.1.1.1")
    path = config.save_config(cfg)
    assert path.is_file()
    loaded, warns = config.load_config()
    assert loaded["timeout"] == 15.0 and loaded["dns_server"] == "1.1.1.1" and warns == []


def test_corrupt_file_does_not_crash():
    config.config_path().write_text("{ not json", encoding="utf-8")
    cfg, warns = config.load_config()
    assert cfg == config.DEFAULTS
    assert warns and "Gagal membaca" in warns[0]


def test_invalid_values_fall_back_with_warnings():
    config.config_path().write_text(json.dumps({
        "timeout": 9999, "ping_count": "abc", "dns_server": "not-an-ip",
        "mystery": 1, "user_agent": "UA-OK"}), encoding="utf-8")
    cfg, warns = config.load_config()
    assert cfg["timeout"] == config.DEFAULTS["timeout"]
    assert cfg["ping_count"] == config.DEFAULTS["ping_count"]
    assert cfg["dns_server"] == ""
    assert cfg["user_agent"] == "UA-OK"
    assert len(warns) == 4


def test_non_object_json():
    config.config_path().write_text("[1,2,3]", encoding="utf-8")
    cfg, warns = config.load_config()
    assert cfg == config.DEFAULTS and warns


def test_env_override(monkeypatch):
    monkeypatch.setenv("NETSCOPE_TIMEOUT", "3")
    monkeypatch.setenv("NETSCOPE_PING_COUNT", "99")  # di luar batas -> diabaikan
    cfg, warns = config.load_config()
    assert cfg["timeout"] == 3.0
    assert cfg["ping_count"] == config.DEFAULTS["ping_count"]
    assert any("PING_COUNT" in w for w in warns)


def test_update_value_errors():
    cfg = dict(config.DEFAULTS)
    for key, val in (("nope", "1"), ("timeout", "0"), ("timeout", "x"),
                     ("ping_count", "0"), ("dns_server", "evil.com"), ("user_agent", "  ")):
        with pytest.raises(TargetError):
            config.update_value(cfg, key, val)
    assert cfg == config.DEFAULTS


def test_save_unwritable(tmp_path, monkeypatch):
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("NETSCOPE_CONFIG", str(blocker / "sub" / "c.json"))
    with pytest.raises(OSError):
        config.save_config(dict(config.DEFAULTS))
