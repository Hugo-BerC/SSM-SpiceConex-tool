#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MIN_PY_MAJOR=3
MIN_PY_MINOR=10
PLUGIN_MIN_VERSION="1.2.764.0"
TMP_SETUP=$(mktemp -d)
trap 'rm -rf "$TMP_SETUP"' EXIT

say() { printf '%s\n' "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# Run a noisy command in the background and show only a compact spinner.
# stdout/stderr are kept in a temporary log and shown only if the step fails.
run_quiet() {
    label="$1"; shift
    log="$TMP_SETUP/step.log"
    err="$TMP_SETUP/step.err"
    : >"$log"; : >"$err"
    "$@" >"$log" 2>"$err" &
    pid=$!
    frames='|/-\\'; i=0
    while kill -0 "$pid" 2>/dev/null; do
        frame=$(printf '%s' "$frames" | cut -c $((i % 4 + 1)))
        printf '\r[%s] %s   ' "$frame" "$label"
        sleep 0.12
        i=$((i + 1))
    done
    if wait "$pid"; then
        printf '\r[OK] %s\033[K\n' "$label"
        return 0
    fi
    printf '\r[FAIL] %s\033[K\n' "$label" >&2
    if [ -s "$err" ]; then tail -30 "$err" >&2; elif [ -s "$log" ]; then tail -30 "$log" >&2; fi
    return 1
}

version_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; }
find_python() { for candidate in python3 python; do if command -v "$candidate" >/dev/null 2>&1 && version_ok "$candidate"; then command -v "$candidate"; return 0; fi; done; return 1; }
run_as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; elif command -v sudo >/dev/null 2>&1; then sudo "$@"; else fail "This step needs administrator privileges. Run as root or install sudo."; fi; }

OS_KIND=""
if [ "$(uname -s)" = "Darwin" ]; then OS_KIND="macOS"
elif [ "$(uname -s)" = "Linux" ]; then if grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null || [ -n "${WSL_DISTRO_NAME:-}" ]; then OS_KIND="WSL"; else OS_KIND="Linux"; fi
else fail "Unsupported shell/OS: $(uname -s). On native Windows run setup.ps1 instead."; fi

printf '\n'
printf '    ███████╗██████╗ ██╗ ██████╗███████╗\n'
printf '    ██╔════╝██╔══██╗██║██╔════╝██╔════╝\n'
printf '    ███████╗██████╔╝██║██║     █████╗  \n'
printf '    ╚════██║██╔═══╝ ██║██║     ██╔══╝  \n'
printf '    ███████║██║     ██║╚██████╗███████╗\n'
printf '    ╚══════╝╚═╝     ╚═╝ ╚═════╝╚══════╝\n'
printf '                 SPICECONEX\n\n'
say "Detected platform: $OS_KIND"
say "Application directory: $APP_DIR"

# xterm on minimal Debian/WSL images may be present without the core X11 fonts.
# Install them only when xterm is actually available so SSM sessions do not
# start with "cannot load font" warnings.
if [ "$OS_KIND" = "Linux" ] || [ "$OS_KIND" = "WSL" ]; then
    if command -v xterm >/dev/null 2>&1 && [ -f /etc/debian_version ] && ! dpkg-query -W -f='${Status}' xfonts-base 2>/dev/null | grep -q 'install ok installed'; then
        # sudo credentials are requested below before privileged operations.
        :
    fi
fi

# Cache sudo credentials before backgrounded privileged operations.
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    say "Administrator privileges are required for system components."
    sudo -v
fi

if { [ "$OS_KIND" = "Linux" ] || [ "$OS_KIND" = "WSL" ]; } && command -v xterm >/dev/null 2>&1 && [ -f /etc/debian_version ] && ! dpkg-query -W -f='${Status}' xfonts-base 2>/dev/null | grep -q 'install ok installed'; then
    run_quiet 'Installing X11 terminal fonts' run_as_root apt-get update
    run_quiet 'Installing X11 terminal fonts' run_as_root apt-get install -y xfonts-base
fi

# --- Native Qt/XCB libraries (Debian/Ubuntu, including WSLg) ---------------
# PySide6 wheels include the Qt xcb plugin, but not all its system libraries.
# Install only missing packages; do not force XCB on systems using Wayland.
if { [ "$OS_KIND" = "Linux" ] || [ "$OS_KIND" = "WSL" ]; } && [ -f /etc/debian_version ]; then
    QT_XCB_PACKAGES="libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-shape0 libxcb-randr0"
    QT_MISSING=""
    for qt_pkg in $QT_XCB_PACKAGES; do
        if ! dpkg-query -W -f='${Status}' "$qt_pkg" 2>/dev/null | grep -q 'install ok installed'; then
            QT_MISSING="$QT_MISSING $qt_pkg"
        fi
    done
    if [ -n "$QT_MISSING" ]; then
        say "Missing Qt/XCB system packages:$QT_MISSING"
        run_quiet 'Updating package index for Qt libraries' run_as_root apt-get update
        # Word splitting is intentional: package names are fixed above.
        run_quiet 'Installing Qt/XCB runtime libraries' run_as_root apt-get install -y $QT_MISSING
    else
        say '[OK] Qt/XCB runtime libraries already installed'
    fi
fi

# --- Python ---------------------------------------------------------------
PYTHON_BIN=""
if PYTHON_BIN=$(find_python); then say "Using Python: $PYTHON_BIN"
else
    case "$OS_KIND" in
        macOS)
            if ! command -v brew >/dev/null 2>&1; then say "Homebrew not found. Installing Homebrew..."; run_quiet 'Installing Homebrew' /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"; [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)" || true; [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)" || true; fi
            run_quiet 'Installing Python 3.12' brew install python@3.12
            PYTHON_BIN="$(brew --prefix python@3.12)/bin/python3.12" ;;
        Linux|WSL)
            [ -f /etc/debian_version ] || fail "Python 3.10+ is required. Automatic installation is implemented for Debian/Ubuntu systems."
            run_quiet 'Updating package index' run_as_root apt-get update
            run_quiet 'Installing Python and venv support' run_as_root apt-get install -y python3 python3-venv python3-pip
            if PYTHON_BIN=$(find_python); then :
            elif apt-cache show python3.12 >/dev/null 2>&1; then run_quiet 'Installing Python 3.12' run_as_root apt-get install -y python3.12 python3.12-venv; PYTHON_BIN="$(command -v python3.12)"
            elif apt-cache show python3.11 >/dev/null 2>&1; then run_quiet 'Installing Python 3.11' run_as_root apt-get install -y python3.11 python3.11-venv; PYTHON_BIN="$(command -v python3.11)"
            else fail "The installed Debian/Ubuntu Python is older than 3.10 and no supported Python 3.10+ package was found."; fi ;;
    esac
fi
version_ok "$PYTHON_BIN" || fail "Python 3.10+ is required. Found: $PYTHON_BIN"

# --- Git -------------------------------------------------------------------
if command -v git >/dev/null 2>&1; then
    say "Git detected: $(git --version)"
else
    case "$OS_KIND" in
        macOS)
            if ! command -v brew >/dev/null 2>&1; then
                say "Homebrew not found. Installing Homebrew for Git..."
                run_quiet 'Installing Homebrew' /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                [ -x /opt/homebrew/bin/brew ] && eval "$(/opt/homebrew/bin/brew shellenv)" || true
                [ -x /usr/local/bin/brew ] && eval "$(/usr/local/bin/brew shellenv)" || true
            fi
            run_quiet 'Installing Git' brew install git
            ;;
        Linux|WSL)
            [ -f /etc/debian_version ] || fail "Git is required for in-app updates. Automatic Git installation currently supports Debian/Ubuntu systems."
            run_quiet 'Installing Git' run_as_root apt-get update
            run_quiet 'Installing Git' run_as_root apt-get install -y git
            ;;
    esac
    command -v git >/dev/null 2>&1 || fail "Git could not be installed or located in PATH."
fi

# --- AWS CLI v2 -----------------------------------------------------------
aws_cli_v2_ok() { command -v aws >/dev/null 2>&1 && aws --version 2>&1 | grep -q 'aws-cli/2\.'; }
if aws_cli_v2_ok; then say "AWS CLI v2 detected: $(aws --version 2>&1)"
else
    say "AWS CLI v2 not found. Installing the official AWS CLI v2..."
    case "$OS_KIND" in
        macOS|Linux|WSL)
            curl -fsSL https://awscli.amazonaws.com/v2/install.sh -o "$TMP_SETUP/aws-install.sh"
            chmod +x "$TMP_SETUP/aws-install.sh"
            run_quiet 'Installing AWS CLI v2' run_as_root "$TMP_SETUP/aws-install.sh" --install-dir /usr/local/aws-cli --bin-dir /usr/local/bin
            ;;
    esac
    aws_cli_v2_ok || fail "AWS CLI v2 could not be installed or located in PATH."
fi

# --- Session Manager plugin ----------------------------------------------
plugin_version() { command -v session-manager-plugin >/dev/null 2>&1 || return 1; session-manager-plugin --version 2>&1 | sed -n 's/.*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -1; }
version_ge_min() {
    min="$PLUGIN_MIN_VERSION"; cur="$1"; OLD_IFS=$IFS; IFS=.; set -- $min; min1=${1:-0}; min2=${2:-0}; min3=${3:-0}; min4=${4:-0}; set -- $cur; cur1=${1:-0}; cur2=${2:-0}; cur3=${3:-0}; cur4=${4:-0}; IFS=$OLD_IFS
    if [ "$cur1" -ne "$min1" ]; then [ "$cur1" -gt "$min1" ]; return; fi
    if [ "$cur2" -ne "$min2" ]; then [ "$cur2" -gt "$min2" ]; return; fi
    if [ "$cur3" -ne "$min3" ]; then [ "$cur3" -gt "$min3" ]; return; fi
    [ "$cur4" -ge "$min4" ]
}
PLUGIN_VERSION="$(plugin_version || true)"
if [ -n "$PLUGIN_VERSION" ] && version_ge_min "$PLUGIN_VERSION"; then say "Session Manager plugin detected: $PLUGIN_VERSION"
else
    [ -n "$PLUGIN_VERSION" ] && say "Session Manager plugin $PLUGIN_VERSION is older than required $PLUGIN_MIN_VERSION. Updating..." || say "Session Manager plugin not found. Installing it..."
    case "$OS_KIND" in
        macOS)
            if [ "$(uname -m)" = "arm64" ]; then URL="https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac_arm64/session-manager-plugin.pkg"; else URL="https://s3.amazonaws.com/session-manager-downloads/plugin/latest/mac/session-manager-plugin.pkg"; fi
            curl -fsSL "$URL" -o "$TMP_SETUP/session-manager-plugin.pkg"
            run_quiet 'Installing Session Manager plugin' run_as_root installer -pkg "$TMP_SETUP/session-manager-plugin.pkg" -target /
            run_as_root mkdir -p /usr/local/bin
            run_as_root ln -sf /usr/local/sessionmanagerplugin/bin/session-manager-plugin /usr/local/bin/session-manager-plugin
            ;;
        Linux|WSL)
            [ -f /etc/debian_version ] || fail "Automatic Session Manager plugin installation currently supports Debian/Ubuntu Linux and WSL."
            if [ "$(uname -m)" = "aarch64" ] || [ "$(uname -m)" = "arm64" ]; then URL="https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_arm64/session-manager-plugin.deb"; else URL="https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb"; fi
            curl -fsSL "$URL" -o "$TMP_SETUP/session-manager-plugin.deb"
            run_quiet 'Installing Session Manager plugin' run_as_root dpkg -i "$TMP_SETUP/session-manager-plugin.deb"
            ;;
    esac
    PLUGIN_VERSION="$(plugin_version || true)"
    [ -n "$PLUGIN_VERSION" ] && version_ge_min "$PLUGIN_VERSION" || fail "Session Manager plugin installation could not be verified. Required >= $PLUGIN_MIN_VERSION."
fi

# --- Python environment ---------------------------------------------------
run_quiet 'Creating Python virtual environment' "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
VENV_PY="$APP_DIR/.venv/bin/python"
run_quiet 'Installing Python package manager' "$VENV_PY" -m pip install --upgrade pip --disable-pip-version-check --no-input
run_quiet 'Installing SpiceConex Python modules' "$VENV_PY" -m pip install -r "$APP_DIR/requirements.txt" --disable-pip-version-check --no-input

cat > "$APP_DIR/spiceconex" <<EOF2
#!/bin/sh
exec "$VENV_PY" "$APP_DIR/ssm_spiceconex.py" "\$@"
EOF2
chmod +x "$APP_DIR/spiceconex"
PREFIX="/usr/local/bin"
run_as_root mkdir -p "$PREFIX"
run_as_root cp "$APP_DIR/spiceconex" "$PREFIX/spiceconex"
run_as_root chmod 755 "$PREFIX/spiceconex"

printf '\n========================================\n'
printf ' SPICECONEX READY\n'
printf '========================================\n'
say "AWS CLI: $(aws --version 2>&1)"
say "Session Manager plugin: $PLUGIN_VERSION"
say "Python environment: $APP_DIR/.venv"
say ''
say 'Setup complete.'
say 'Run SpiceConex from a new terminal with:'
say '  spiceconex'
say ''
say 'Tip: use  spiceconex --demo  to launch the demo mode.'
