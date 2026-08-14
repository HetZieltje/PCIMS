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

outside_root="$test_root/outside"
mkdir -p "$outside_root"
printf '%s\n' untouched > "$outside_root/version-marker"
if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" \
    PCIMS_INSTALL_ROOT="$outside_root" PYTHON="$fake_python" \
    sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Installer unexpectedly accepted an external install root." >&2
    exit 1
fi
test "$(cat "$outside_root/version-marker")" = untouched

stale_previous="$data_home/pcims/.application.previous.99999999"
mv "$install_root" "$stale_previous"
mkdir "$data_home/pcims/.install.lock"
printf '%s\n' 99999999 > "$data_home/pcims/.install.lock/pid"
if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" FAKE_PIP_FAIL=1 sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Interrupted installation recovery ignored a staged failure." >&2
    exit 1
fi
test "$(cat "$install_root/version-marker")" = old
test ! -e "$stale_previous"
test ! -e "$data_home/pcims/.install.lock"

mkdir "$data_home/pcims/.install.lock"
printf '%s\n' "$$" > "$data_home/pcims/.install.lock/pid"
if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Installer ignored a live installation lock." >&2
    exit 1
fi
test "$(cat "$install_root/version-marker")" = old
rm -f "$data_home/pcims/.install.lock/pid"
rmdir "$data_home/pcims/.install.lock"

if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" FAKE_PIP_FAIL=1 sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Installer unexpectedly accepted a failed staged install." >&2
    exit 1
fi
test "$(cat "$install_root/version-marker")" = old

mkdir -p "$data_home/applications" "$test_root/fake-bin"
printf '%s\n' old-desktop > "$data_home/applications/pcims.desktop"
cat > "$test_root/fake-bin/mv" <<'EOF'
#!/bin/sh
set -eu
destination=
for argument do
    destination=$argument
done
case "$destination" in
    */pcims.desktop) exit 31 ;;
esac
exec /usr/bin/mv "$@"
EOF
chmod +x "$test_root/fake-bin/mv"

if PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" PATH="$test_root/fake-bin:$PATH" \
    sh scripts/install-linux.sh >/dev/null 2>&1
then
    printf '%s\n' "Installer unexpectedly accepted a failed desktop commit." >&2
    exit 1
fi
test "$(cat "$install_root/version-marker")" = old
test "$(cat "$data_home/applications/pcims.desktop")" = old-desktop

PCIMS_PLATFORM=Linux XDG_DATA_HOME="$data_home" PCIMS_INSTALL_ROOT="$install_root" \
    PYTHON="$fake_python" sh scripts/install-linux.sh >/dev/null

test -x "$install_root/bin/python"
test ! -e "$install_root/version-marker"
grep -F "$install_root/bin/python" "$data_home/applications/pcims.desktop" >/dev/null
printf '%s\n' "transactional Linux installer smoke: OK"
