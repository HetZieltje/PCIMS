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
python_command=${PYTHON:-python3}
mkdir -p "$data_home"
data_home=$(CDPATH= cd -- "$data_home" && pwd -P)
case "$data_home" in
    ""|/)
        printf '%s\n' "Refusing unsafe application data root: $data_home" >&2
        exit 1
        ;;
esac
application_parent="$data_home/pcims"
install_root="$application_parent/application"
if [ -n "${PCIMS_INSTALL_ROOT:-}" ] && [ "$PCIMS_INSTALL_ROOT" != "$install_root" ]; then
    printf '%s\n' "PCIMS_INSTALL_ROOT must be $install_root" >&2
    exit 1
fi
if [ -L "$application_parent" ]; then
    printf '%s\n' "Refusing symbolic-link application directory: $application_parent" >&2
    exit 1
fi
mkdir -p "$application_parent"
resolved_application_parent=$(CDPATH= cd -- "$application_parent" && pwd -P)
if [ "$resolved_application_parent" != "$application_parent" ]; then
    printf '%s\n' "Application directory escaped its data root." >&2
    exit 1
fi
desktop_directory="$data_home/applications"
if [ -L "$desktop_directory" ]; then
    printf '%s\n' "Refusing symbolic-link desktop directory: $desktop_directory" >&2
    exit 1
fi
desktop_target="$desktop_directory/pcims.desktop"
desktop_temporary="$desktop_target.tmp"
install_parent=$(dirname -- "$install_root")
install_name=$(basename -- "$install_root")
staging_root="$install_parent/.${install_name}.new.$$"
previous_root="$install_parent/.${install_name}.previous.$$"
lock_directory="$install_parent/.install.lock"
lock_pid_file="$lock_directory/pid"
old_moved=0
new_installed=0
lock_owned=0

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
    if [ "$lock_owned" -eq 1 ] && [ -d "$lock_directory" ] && [ ! -L "$lock_directory" ]; then
        if [ -f "$lock_pid_file" ] && [ ! -L "$lock_pid_file" ]; then
            rm -f -- "$lock_pid_file"
        fi
        rmdir -- "$lock_directory"
    fi
    exit "$status"
}
trap cleanup EXIT HUP INT TERM

if ! mkdir "$lock_directory" 2>/dev/null; then
    if [ -L "$lock_directory" ] || [ ! -d "$lock_directory" ]; then
        printf '%s\n' "Installation lock is not a regular directory." >&2
        exit 1
    fi
    lock_holder=
    if [ -f "$lock_pid_file" ] && [ ! -L "$lock_pid_file" ]; then
        lock_holder=$(sed -n '1p' "$lock_pid_file")
    fi
    case "$lock_holder" in
        *[!0-9]*|"") ;;
        *)
            if kill -0 "$lock_holder" 2>/dev/null; then
                printf '%s\n' "Another PCIMS installation is already running." >&2
                exit 1
            fi
            ;;
    esac
    if [ -f "$lock_pid_file" ] && [ ! -L "$lock_pid_file" ]; then
        rm -f -- "$lock_pid_file"
    fi
    if ! rmdir -- "$lock_directory" || ! mkdir "$lock_directory"; then
        printf '%s\n' "Stale installation lock could not be recovered." >&2
        exit 1
    fi
fi
lock_owned=1
printf '%s\n' "$$" > "$lock_pid_file"

stale_previous=
stale_previous_count=0
for candidate in "$install_parent"/."$install_name".previous.*; do
    if [ ! -e "$candidate" ] && [ ! -L "$candidate" ]; then
        continue
    fi
    if [ -L "$candidate" ] || [ ! -d "$candidate" ]; then
        printf '%s\n' "Unsafe stale installation path: $candidate" >&2
        exit 1
    fi
    stale_previous=$candidate
    stale_previous_count=$((stale_previous_count + 1))
done
if [ "$stale_previous_count" -gt 1 ]; then
    printf '%s\n' "Multiple interrupted installations require manual recovery." >&2
    exit 1
fi
if [ "$stale_previous_count" -eq 1 ]; then
    if [ -e "$install_root" ]; then
        if [ -L "$install_root" ] || [ ! -d "$install_root" ]; then
            printf '%s\n' "Installation target is not a regular directory: $install_root" >&2
            exit 1
        fi
        rm -rf -- "$stale_previous"
    else
        mv "$stale_previous" "$install_root"
    fi
fi
for candidate in "$install_parent"/."$install_name".new.*; do
    if [ ! -e "$candidate" ] && [ ! -L "$candidate" ]; then
        continue
    fi
    if [ -L "$candidate" ] || [ ! -d "$candidate" ]; then
        printf '%s\n' "Unsafe stale installation path: $candidate" >&2
        exit 1
    fi
    rm -rf -- "$candidate"
done

if [ -e "$staging_root" ] || [ -e "$previous_root" ]; then
    printf '%s\n' "Temporary installation path already exists." >&2
    exit 1
fi

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
    if ! update-desktop-database "$desktop_directory"; then
        printf '%s\n' "Warning: desktop menu cache could not be refreshed." >&2
    fi
fi

printf 'PCIMS installed. Launch it from your desktop menu or run:\n%s\n' \
    "$install_root/bin/python -m pcims.app.application"
