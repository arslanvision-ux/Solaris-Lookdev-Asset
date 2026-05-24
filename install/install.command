#!/usr/bin/env bash
# Solaris Asset Manager — macOS double-click installer
# Place this file at the project root or install/ and double-click in Finder.
# macOS may ask you to approve running it — right-click -> Open the first time.

# Change to the script's directory so relative paths work
cd "$(dirname "$0")"

# Run the shared shell installer
bash install.sh

# Keep Terminal open so the user can read the output
echo ""
read -rp "  Press Enter to close …"
