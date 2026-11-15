"""Konfigurasi pengguna dengan validasi dan penanganan error yang aman.

Lokasi file: ``$NETSCOPE_CONFIG`` atau ``~/.config/netscope/config.json``.
File yang rusak / nilai tidak valid tidak pernah membuat program crash:
nilai bermasalah diganti default dan sebuah peringatan dikembalikan.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple

from netscope import __version__
from netscope.utils import TargetError, is_ip

DEFAULTS: Dict[str, Any] = {
    "timeout": 8.0,        # detik, untuk semua operasi jaringan
    "ping_count": 4,       # jumlah paket ping
    "dns_server": "",      # kosong = resolver sistem
    "report_dir": "",      # kosong = otomatis
    "user_agent": "Noctura-NETSCOPE/%s" % __version__,
}

LIMITS = {"timeout": (1.0, 120.0), "ping_count": (1, 50)}


def config_path() -> Path:
    env = os.environ.get("NETSCOPE_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / "netscope" / "config.json"


def _coerce(key: str, value: Any) -> Any:
    """Validasi satu nilai. Raise ``ValueError`` bila tidak valid."""
    if key == "timeout":
        num = float(value)
        lo, hi = LIMITS["timeout"]
        if not lo <= num <= hi:
            raise ValueError("timeout harus %.0f-%.0f detik" % (lo, hi))
        return num
    if key == "ping_count":
        num = int(value)
        lo, hi = LIMITS["ping_count"]
        if not lo <= num <= hi:
            raise ValueError("ping_count harus %d-%d" % (lo, hi))
        return num
    if key == "dns_server":
        text = str(value).strip()
        if text and not is_ip(text):
            raise ValueError("dns_server harus alamat IP (contoh 1.1.1.1) atau kosong")
        return text
    if key in ("report_dir", "user_agent"):
        text = str(value).strip()
        if key == "user_agent" and not text:
            raise ValueError("user_agent tidak boleh kosong")
        if any(ord(ch) < 32 for ch in text):
            raise ValueError("%s mengandung karakter kontrol" % key)
        return text
    raise KeyError(key)


def validate_config(raw: Any) -> Tuple[Dict[str, Any], List[str]]:
    """Gabungkan ``raw`` dengan default; kembalikan ``(config, peringatan)``."""
    cfg = dict(DEFAULTS)
    warnings: List[str] = []
    if not isinstance(raw, dict):
        return cfg, ["Isi konfigurasi bukan objek JSON; memakai default."]
    for key, value in raw.items():
        if key not in DEFAULTS:
            warnings.append("Kunci konfigurasi tidak dikenal diabaikan: %s" % key)
            continue
        try:
            cfg[key] = _coerce(key, value)
        except (ValueError, TypeError) as exc:
            warnings.append("Nilai '%s' tidak valid (%s); memakai default." % (key, exc))
    return cfg, warnings


def load_config() -> Tuple[Dict[str, Any], List[str]]:
    """Baca konfigurasi dari disk, lalu terapkan override env ``NETSCOPE_*``."""
    path = config_path()
    raw: Any = {}
    warnings: List[str] = []
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append("Gagal membaca %s (%s); memakai default." % (path, exc))
        raw = {}
    cfg, more = validate_config(raw)
    warnings.extend(more)

    for key in DEFAULTS:
        env_val = os.environ.get("NETSCOPE_" + key.upper())
        if env_val is None or env_val == "":
            continue
        try:
            cfg[key] = _coerce(key, env_val)
        except (ValueError, TypeError) as exc:
            warnings.append("Env NETSCOPE_%s diabaikan (%s)." % (key.upper(), exc))
    return cfg, warnings


def save_config(cfg: Dict[str, Any]) -> Path:
    """Simpan konfigurasi. Raise ``OSError`` bila gagal menulis."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean, _ = validate_config({k: v for k, v in cfg.items() if k in DEFAULTS})
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))
    return path


def update_value(cfg: Dict[str, Any], key: str, value: Any) -> Dict[str, Any]:
    """Validasi dan set satu kunci pada ``cfg``. Raise ``TargetError`` bila salah."""
    if key not in DEFAULTS:
        raise TargetError("Kunci tidak dikenal: %s (tersedia: %s)" % (key, ", ".join(DEFAULTS)))
    try:
        cfg[key] = _coerce(key, value)
    except (ValueError, TypeError) as exc:
        raise TargetError(str(exc))
    return cfg
