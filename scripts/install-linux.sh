#!/bin/sh
set -eu

if [ "$(uname -s)" != "Linux" ]; then
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

"$python_command" -m venv "$install_root"
"$install_root/bin/python" -m pip install --upgrade "$project_directory"

mkdir -p "$desktop_directory"
escaped_executable=$(printf '%s' "$install_root/bin/pcims" | sed 's/[&|]/\\&/g')
sed "s|@PCIMS_EXECUTABLE@|$escaped_executable|g" \
    "$project_directory/packaging/linux/pcims.desktop" > "$desktop_temporary"
chmod 644 "$desktop_temporary"
mv "$desktop_temporary" "$desktop_target"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$desktop_directory"
fi

printf 'PCIMS installed. Launch it from your desktop menu or run:\n%s\n' \
    "$install_root/bin/pcims"
