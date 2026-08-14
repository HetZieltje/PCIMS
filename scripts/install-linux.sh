#!/bin/sh
set -eu

platform=${PCIMS_PLATFORM:-$(uname -s)}
if [ "$platform" != "Linux" ]; then
    printf '%s\n' "This installer supports Linux only." >&2
    exit 1
fi

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_directory=$(CDPATH= cd -- "$script_directory/.." && pwd)
data_home=${XDG_DATA_HOME:-"${HOME:?}/.local/share"}
install_root=${PCIMS_INSTALL_ROOT:-"$data_home/pcims/application"}
python_command=${PYTHON:-python3}
desktop_directory="$data_home/applications"
desktop_target="$desktop_directory/pcims.desktop"
desktop_temporary="$desktop_target.tmp"
install_parent=$(dirname -- "$install_root")
install_name=$(basename -- "$install_root")
staging_root="$install_parent/.${install_name}.new.$$"
previous_root="$install_parent/.${install_name}.previous.$$"
old_moved=0
new_installed=0

case "$install_root" in
    ""|/)
        printf '%s\n' "Refusing unsafe installation target: $install_root" >&2
        exit 1
        ;;
esac
if [ -e "$staging_root" ] || [ -e "$previous_root" ]; then
    printf '%s\n' "Temporary installation path already exists." >&2
    exit 1
fi

cleanup() {
    status=$?
    if [ "$new_installed" -eq 1 ] && [ -d "$install_root" ] && [ ! -L "$install_root" ]; then
        rm -rf -- "$install_root"
    fi
    if [ "$old_moved" -eq 1 ] && [ -d "$previous_root" ] && [ ! -L "$previous_root" ]; then
        mv "$previous_root" "$install_root"
    fi
    if [ -d "$staging_root" ] && [ ! -L "$staging_root" ]; then
        rm -rf -- "$staging_root"
    fi
    if [ -f "$desktop_temporary" ] && [ ! -L "$desktop_temporary" ]; then
        rm -f -- "$desktop_temporary"
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$install_parent"
"$python_command" -m venv "$staging_root"
"$staging_root/bin/python" -m pip install \
    --require-hashes -r "$project_directory/requirements.lock"
"$staging_root/bin/python" -m pip install \
    --require-hashes -r "$project_directory/requirements-build.lock"
"$staging_root/bin/python" -m pip install \
    --no-build-isolation --no-deps "$project_directory"
"$staging_root/bin/python" -m pip check
(
    cd "${TMPDIR:-/tmp}"
    "$staging_root/bin/python" "$project_directory/scripts/smoke-installed.py"
)

mkdir -p "$desktop_directory"
escaped_python=$(printf '%s' "$install_root/bin/python" | sed 's/[&|]/\\&/g')
sed "s|@PCIMS_PYTHON@|$escaped_python|g" \
    "$project_directory/packaging/linux/pcims.desktop" > "$desktop_temporary"
chmod 644 "$desktop_temporary"

if [ -e "$install_root" ]; then
    if [ -L "$install_root" ] || [ ! -d "$install_root" ]; then
        printf '%s\n' "Installation target is not a regular directory: $install_root" >&2
        exit 1
    fi
    mv "$install_root" "$previous_root"
    old_moved=1
fi
if ! mv "$staging_root" "$install_root"; then
    if [ "$old_moved" -eq 1 ]; then
        mv "$previous_root" "$install_root"
        old_moved=0
    fi
    exit 1
fi
new_installed=1
mv "$desktop_temporary" "$desktop_target"
new_installed=0
old_moved=0
if [ -d "$previous_root" ] && [ ! -L "$previous_root" ]; then
    rm -rf -- "$previous_root"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_directory"
fi

printf 'PCIMS installed. Launch it from your desktop menu or run:\n%s\n' \
    "$install_root/bin/python -m pcims.app.application"
