#!/usr/bin/env bash
# =============================================================================
#  Noctura NETSCOPE - installer (Termux, Kali Linux, Debian/Ubuntu, Arch, macOS)
#
#  Pemakaian:
#     bash install.sh              # instal (tanpa root)
#     bash install.sh --uninstall  # hapus
#     bash install.sh --help
#
#  Yang dilakukan:
#     1. memeriksa Python >= 3.8 (dan menawarkan cara memasangnya bila belum ada)
#     2. membuat virtualenv terisolasi di  ~/.local/share/netscope/venv
#        (aman untuk Kali / Debian yang menerapkan PEP 668)
#     3. memasang dependensi dari requirements.txt
#     4. membuat perintah  `netscope`  di folder bin pengguna
#
#  Tidak memerlukan root. Tidak mengirim data apa pun.
# =============================================================================
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="netscope"
MIN_PY_MAJOR=3
MIN_PY_MINOR=8

# ---- warna (dimatikan bila bukan terminal atau NO_COLOR di-set) --------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_V=$'\033[38;2;167;139;250m'; C_C=$'\033[38;2;34;211;238m'
    C_P=$'\033[38;2;244;114;182m'; C_G=$'\033[38;2;52;211;153m'
    C_Y=$'\033[38;2;251;191;36m';  C_R=$'\033[38;2;248;113;113m'
    C_D=$'\033[2m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
    C_V=""; C_C=""; C_P=""; C_G=""; C_Y=""; C_R=""; C_D=""; C_B=""; C_0=""
fi

# ---- simbol: Unicode bila locale UTF-8, jika tidak ASCII ---------------------
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
    *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*)
        S_INFO="❯"; S_OK="✔"; S_WARN="▲"; S_ERR="✘"; S_STAR="✦" ;;
    *)
        S_INFO=">"; S_OK="+"; S_WARN="!"; S_ERR="x"; S_STAR="*" ;;
esac

info() { printf '%s%s%s %s\n' "$C_C" "$S_INFO" "$C_0" "$*"; }
ok()   { printf '%s%s%s %s\n' "$C_G" "$S_OK" "$C_0" "$*"; }
warn() { printf '%s%s%s %s\n' "$C_Y" "$S_WARN" "$C_0" "$*" >&2; }
fail() { printf '%s%s%s %s\n' "$C_R" "$S_ERR" "$C_0" "$*" >&2; exit 1; }

banner() {
    printf '\n%s' "$C_V"
    cat <<'EOF'
   _  _  ___   ___ _____ _   _ ___    _
  | \| |/ _ \ / __|_   _| | | | _ \  /_\
  | .` | (_) | (__  | | | |_| |   / / _ \
  |_|\_|\___/ \___| |_|  \___/|_|_\/_/ \_\
EOF
    printf '%s' "$C_0"
    printf '  %s%s N E T S C O P E  %s%s  %sinstaller%s\n\n' "$C_C" "$S_STAR" "$S_STAR" "$C_0" "$C_D" "$C_0"
}

usage() {
    cat <<EOF
Noctura NETSCOPE installer

  bash install.sh              instal ke ~/.local (tanpa root)
  bash install.sh --uninstall  hapus venv dan perintah 'netscope'
  bash install.sh --help       bantuan ini

Variabel opsional:
  NETSCOPE_HOME   lokasi data/venv  (default: ~/.local/share/netscope)
  NETSCOPE_BIN    lokasi perintah   (default: ~/.local/bin, Termux: \$PREFIX/bin)
  PYTHON          interpreter Python yang dipakai
EOF
}

# ---- deteksi lingkungan -------------------------------------------------------
is_termux() {
    case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
    [ -d /data/data/com.termux ]
}

detect_platform() {
    if is_termux; then echo "Termux"; return; fi
    case "$(uname -s 2>/dev/null)" in
        Darwin) echo "macOS" ;;
        Linux)
            if [ -r /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release 2>/dev/null
                case "${ID:-}" in
                    kali) echo "Kali Linux"; return ;;
                    *) echo "${PRETTY_NAME:-Linux}"; return ;;
                esac
            fi
            echo "Linux" ;;
        *) echo "$(uname -s 2>/dev/null || echo Unknown)" ;;
    esac
}

find_python() {
    local cand
    for cand in "${PYTHON:-}" python3 python; do
        [ -n "$cand" ] || continue
        command -v "$cand" >/dev/null 2>&1 || continue
        if "$cand" -c "import sys; sys.exit(0 if sys.version_info >= ($MIN_PY_MAJOR, $MIN_PY_MINOR) else 1)" 2>/dev/null; then
            command -v "$cand"
            return 0
        fi
    done
    return 1
}

python_install_hint() {
    if is_termux; then
        echo "pkg update && pkg install -y python"
    elif command -v apt-get >/dev/null 2>&1; then
        echo "sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    elif command -v pacman >/dev/null 2>&1; then
        echo "sudo pacman -S --needed python python-pip"
    elif command -v dnf >/dev/null 2>&1; then
        echo "sudo dnf install -y python3 python3-pip"
    elif command -v brew >/dev/null 2>&1; then
        echo "brew install python"
    else
        echo "pasang Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} dari python.org"
    fi
}

# ---- lokasi ----------------------------------------------------------------
if is_termux; then
    DEFAULT_BIN="${PREFIX:-/data/data/com.termux/files/usr}/bin"
else
    DEFAULT_BIN="$HOME/.local/bin"
fi
NETSCOPE_HOME="${NETSCOPE_HOME:-$HOME/.local/share/netscope}"
NETSCOPE_BIN="${NETSCOPE_BIN:-$DEFAULT_BIN}"
VENV_DIR="$NETSCOPE_HOME/venv"
LAUNCHER="$NETSCOPE_BIN/$APP_NAME"

# ---- uninstall -------------------------------------------------------------
do_uninstall() {
    banner
    local removed=0
    if [ -f "$LAUNCHER" ] && grep -q "Noctura NETSCOPE launcher" "$LAUNCHER" 2>/dev/null; then
        rm -f "$LAUNCHER" && ok "Menghapus $LAUNCHER" && removed=1
    elif [ -e "$LAUNCHER" ]; then
        warn "$LAUNCHER bukan buatan installer ini; dibiarkan."
    fi
    if [ -d "$NETSCOPE_HOME" ]; then
        rm -rf "$NETSCOPE_HOME" && ok "Menghapus $NETSCOPE_HOME" && removed=1
    fi
    [ "$removed" -eq 1 ] || info "Tidak ada yang perlu dihapus."
    info "Konfigurasi (~/.config/netscope) dan folder reports/ tidak dihapus."
    exit 0
}

# ---- argumen ---------------------------------------------------------------
case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    --uninstall|uninstall) do_uninstall ;;
    "") ;;
    *) usage; fail "Opsi tidak dikenal: $1" ;;
esac

banner
PLATFORM="$(detect_platform)"
info "Platform : ${C_B}${PLATFORM}${C_0}"

[ -f "$SCRIPT_DIR/requirements.txt" ] || fail "requirements.txt tidak ditemukan di $SCRIPT_DIR (jalankan dari folder proyek)."
[ -d "$SCRIPT_DIR/netscope" ]         || fail "Folder 'netscope/' tidak ditemukan di $SCRIPT_DIR."

if [ "$(id -u 2>/dev/null || echo 1)" = "0" ] && ! is_termux; then
    warn "Anda menjalankan sebagai root. NETSCOPE tidak membutuhkan root; disarankan pakai user biasa."
fi

# ---- 1. Python ---------------------------------------------------------------
PY="$(find_python)" || {
    warn "Python >= ${MIN_PY_MAJOR}.${MIN_PY_MINOR} tidak ditemukan."
    fail "Pasang dulu dengan:  $(python_install_hint)"
}
ok "Python   : $("$PY" -c 'import sys; print(sys.version.split()[0])') ($PY)"

# ---- 2. virtualenv ---------------------------------------------------------
mkdir -p "$NETSCOPE_HOME" || fail "Tidak bisa membuat $NETSCOPE_HOME"
if [ -x "$VENV_DIR/bin/python" ] && ! "$VENV_DIR/bin/python" -c "import sys" >/dev/null 2>&1; then
    warn "Venv lama rusak; dibuat ulang."
    rm -rf "$VENV_DIR"
fi
if [ ! -x "$VENV_DIR/bin/python" ]; then
    info "Membuat virtualenv di $VENV_DIR ..."
    if ! "$PY" -m venv "$VENV_DIR" >/dev/null 2>&1; then
        rm -rf "$VENV_DIR"
        if is_termux; then
            fail "Gagal membuat venv. Coba:  pkg install -y python"
        fi
        fail "Gagal membuat venv. Di Kali/Debian pasang:  sudo apt install -y python3-venv"
    fi
fi
ok "Virtualenv siap"

# ---- 3. dependensi ---------------------------------------------------------
info "Memasang dependensi (rich, requests, dnspython) ..."
VPY="$VENV_DIR/bin/python"
"$VPY" -m pip install --quiet --disable-pip-version-check --upgrade pip >/dev/null 2>&1 || true
if ! "$VPY" -m pip install --quiet --disable-pip-version-check -r "$SCRIPT_DIR/requirements.txt"; then
    fail "Gagal memasang dependensi. Periksa koneksi internet lalu jalankan ulang install.sh."
fi
ok "Dependensi terpasang"

# ---- 4. launcher -----------------------------------------------------------
mkdir -p "$NETSCOPE_BIN" || fail "Tidak bisa membuat $NETSCOPE_BIN"
if [ -e "$LAUNCHER" ] && ! grep -q "Noctura NETSCOPE launcher" "$LAUNCHER" 2>/dev/null; then
    fail "$LAUNCHER sudah ada dan bukan milik installer ini. Pindahkan dulu atau set NETSCOPE_BIN."
fi
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Noctura NETSCOPE launcher (dibuat oleh install.sh)
export PYTHONPATH="$SCRIPT_DIR\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VPY" -m netscope "\$@"
EOF
chmod +x "$LAUNCHER" || fail "Gagal membuat $LAUNCHER dapat dieksekusi."
ok "Perintah : $LAUNCHER"

# ---- 5. verifikasi ---------------------------------------------------------
if VERSION_OUT="$("$LAUNCHER" --version 2>&1)"; then
    ok "Verifikasi: $VERSION_OUT"
else
    printf '%s\n' "$VERSION_OUT" >&2
    fail "Instalasi selesai tetapi verifikasi gagal."
fi

case ":$PATH:" in
    *":$NETSCOPE_BIN:"*) ;;
    *)
        warn "$NETSCOPE_BIN belum ada di PATH. Tambahkan dengan:"
        printf '    echo '\''export PATH="%s:$PATH"'\'' >> ~/.bashrc && source ~/.bashrc\n' "$NETSCOPE_BIN"
        ;;
esac

printf '\n%s%s Selesai!%s Jalankan:  %snetscope%s   (menu interaktif)\n' "$C_P" "$S_STAR" "$C_0" "$C_B" "$C_0"
printf '  %snetscope audit example.com%s   %snetscope --help%s\n' "$C_C" "$C_0" "$C_C" "$C_0"
printf '  %sCatatan: jangan hapus/pindah folder ini; launcher memakai kode di sini.%s\n\n' "$C_D" "$C_0"
