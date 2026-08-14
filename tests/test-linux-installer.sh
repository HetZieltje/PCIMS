#!/bin/sh
set -eu

test_root=$(mktemp -d)
cleanup() {
    rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM

fake_python="$test_root/fake-python"
cat > "$fake_python" <<'EOF'
#!/bin/sh
set -eu
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
    mkdir -p "$3/bin"
    cp "$0" "$3/bin/python"
    chmod +x "$3/bin/python"
    exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
    if [ "${FAKE_PIP_FAIL:-0}" = "1" ]; then
        exit 23
    fi
    exit 0
fi
exit 0
EOF
chmod +x "$fake_python"

data_home="$test_root/data"
install_root="$data_home/pcims/application"
mkdir -p "$install_root"
printf '%s\n' old > "$install_root/version-marker"

if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" FAKE_PIP_FAIL=1 sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Installer unexpectedly accepted a failed staged install." >&2
    exit 1
fi
test "$(cat "$install_root/version-marker")" = old

PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" sh scripts/install-linux.sh >/dev/null

test -x "$install_root/bin/python"
test ! -e "$install_root/version-marker"
grep -F "$install_root/bin/python" "$data_home/applications/pcims.desktop" >/dev/null
printf '%s\n' "transactional Linux installer smoke: OK"
