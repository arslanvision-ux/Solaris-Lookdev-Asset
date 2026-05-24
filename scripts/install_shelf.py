"""
One-shot installer for the Asset Manager shelf on Houdini 21 (Windows).

Usage:
    1. Open Houdini's Python Shell.
    2. Run:
           exec(open(r"E:\\PROJECTS\\ASSET_MANAGER\\scripts\\install_shelf.py").read())

What it does:
    - Copies shelf/asset_manager.shelf into your Houdini user toolbar folder
      (Documents\\houdini21.0\\toolbar) so Houdini picks it up on next launch.
    - Sets ASSET_MANAGER_ROOT so the shelf tool resolves the package path even
      if the project is moved later.
    - Adds the shelf to the current shelf set so it appears immediately.
"""

import os
import shutil
import sys


def _resolve_project_root():
    # When run as a real file, __file__ points at scripts/install_shelf.py
    # When run via exec(open(...).read()), __file__ is undefined — fall back
    # to ASSET_MANAGER_ROOT or a known location.
    try:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    env_root = os.environ.get("ASSET_MANAGER_ROOT")
    if env_root and os.path.isdir(env_root):
        return env_root
    for candidate in (
        r"E:\PROJECTS\ASSET_MANAGER",
        os.path.expanduser(r"~\PROJECTS\ASSET_MANAGER"),
    ):
        if os.path.isdir(os.path.join(candidate, "shelf")):
            return candidate
    raise RuntimeError(
        "Cannot locate ASSET_MANAGER project root. "
        "Set the ASSET_MANAGER_ROOT environment variable."
    )


PROJECT_ROOT = _resolve_project_root()
SHELF_SRC = os.path.join(PROJECT_ROOT, "shelf", "asset_manager.shelf")


def _houdini_user_prefs():
    try:
        import hou
        return hou.expandString("$HOUDINI_USER_PREF_DIR")
    except Exception:
        # Fallback for non-Houdini context (typical Windows path for H21)
        return os.path.join(
            os.path.expanduser("~"), "Documents", "houdini21.0"
        )


def install():
    if not os.path.isfile(SHELF_SRC):
        raise FileNotFoundError(f"Shelf source not found: {SHELF_SRC}")

    user_prefs = _houdini_user_prefs()
    toolbar_dir = os.path.join(user_prefs, "toolbar")
    os.makedirs(toolbar_dir, exist_ok=True)

    dst = os.path.join(toolbar_dir, "asset_manager.shelf")
    shutil.copy2(SHELF_SRC, dst)
    print(f"[AssetManager] Copied shelf to: {dst}")

    # Persist ASSET_MANAGER_ROOT so the shelf script is portable.
    os.environ["ASSET_MANAGER_ROOT"] = PROJECT_ROOT
    print(f"[AssetManager] ASSET_MANAGER_ROOT={PROJECT_ROOT}")

    # If we're inside Houdini, load the shelf into the current session so a
    # restart isn't required.
    try:
        import hou
        hou.shelves.loadFile(dst)
        if "asset_manager_shelf" in hou.shelves.shelves():
            print("[AssetManager] Shelf loaded into this session.")
            print("[AssetManager] To pin the tab: right-click the shelf tab bar"
                  " -> Shelves... -> tick 'Asset Manager'.")
        else:
            print("[AssetManager] Shelf file copied but didn't appear in "
                  "hou.shelves.shelves(). Restart Houdini to pick it up.")
    except Exception as e:
        print(f"[AssetManager] Shelf file copied. Restart Houdini to pick it up. ({e})")


if __name__ == "__main__":
    install()
else:
    install()
