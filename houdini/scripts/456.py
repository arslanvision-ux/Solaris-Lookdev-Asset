"""
Houdini startup script for the Asset Manager.
Place this file (or add its contents) to your Houdini user prefs:
  $HOUDINI_USER_PREF_DIR/scripts/456.py

This runs every time a .hip file is opened, setting up the
Asset Manager environment automatically.
"""

import os
import sys


def _setup_asset_manager():
    """Register the Asset Manager on the Python path."""
    # Look for ASSET_MANAGER_ROOT environment variable first
    root = os.environ.get("ASSET_MANAGER_ROOT", "")

    if not root:
        # Fallback: try well-known location
        candidates = [
            r"E:\PROJECTS\ASSET_MANAGER",
            os.path.expanduser("~/PROJECTS/ASSET_MANAGER"),
        ]
        for c in candidates:
            if os.path.isdir(os.path.join(c, "scripts", "asset_manager")):
                root = c
                break

    if not root:
        return

    scripts_dir = os.path.join(root, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    os.environ["ASSET_MANAGER_ROOT"] = root
    print(f"[AssetManager] Ready. Root: {root}")
    print("[AssetManager] Launch: from asset_manager.launcher import launch_panel; launch_panel()")


_setup_asset_manager()
