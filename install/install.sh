#!/usr/bin/env bash
# Solaris Asset Manager — Linux / macOS Installer
# Usage:  bash install/install.sh
#         bash install/install.sh --cli      (headless / SSH)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALLER="$SCRIPT_DIR/installer.py"
EXTRA_ARGS="${1:-}"

echo ""
echo "  ============================================================"
echo "   Solaris Asset Manager  v1.0.0  |  $(uname -s) Installer"
echo "  ============================================================"
echo ""

run() {
    echo "  Using: $1"
    "$1" "$INSTALLER" $EXTRA_ARGS
    exit 0
}

# ── 1. python3 in PATH ────────────────────────────────────────────────────────
if command -v python3 &>/dev/null; then
    run python3
fi

# ── 2. python in PATH (some distros) ─────────────────────────────────────────
if command -v python &>/dev/null; then
    PY_VER=$(python -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo 0)
    if [ "$PY_VER" = "3" ]; then
        run python
    fi
fi

# ── 3. hython in PATH (Houdini shell) ─────────────────────────────────────────
if command -v hython &>/dev/null; then
    run hython
fi

# ── 4. Common Houdini locations ───────────────────────────────────────────────
for VER in 21.5 21.0 20.5 20.0 19.5 19.0; do
    for BASE in \
        "/opt/hfs$VER"                        \
        "/usr/local/hfs$VER"                  \
        "$HOME/hfs$VER"                       \
        "/Applications/Houdini/Houdini$VER"   \
        "/Applications/Houdini/HoudiniCore$VER" \
        "/Applications/Houdini/HoudiniFX$VER"
    do
        if [ -x "$BASE/bin/hython" ]; then
            echo "  Found Houdini $VER at $BASE"
            run "$BASE/bin/hython"
        fi
    done
done

# ── Not found ─────────────────────────────────────────────────────────────────
echo "  ERROR: Python 3 not found."
echo ""
echo "  Options:"
echo "    A) Install Python 3:"
echo "         Ubuntu/Debian : sudo apt install python3"
echo "         Fedora/RHEL   : sudo dnf install python3"
echo "         macOS (brew)  : brew install python@3"
echo ""
echo "    B) Source a Houdini shell and re-run:"
echo "         source /opt/hfsX.Y/houdini_setup"
echo "         python install/install.sh"
echo ""
exit 1
