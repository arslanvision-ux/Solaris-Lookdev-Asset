#!/usr/bin/env python3
"""
Build the standalone installer EXE (Windows) or app bundle (macOS) using PyInstaller.

Usage:
    cd install
    python build_exe.py

Requirements:
    pip install pyinstaller

Output:
    dist/SolarisAssetManager_Installer.exe   (Windows)
    dist/SolarisAssetManager_Installer       (Linux)
    dist/SolarisAssetManager_Installer.app   (macOS with --windowed)
"""

import subprocess
import sys
import platform
from pathlib import Path

ROOT        = Path(__file__).parent.parent
INSTALL_DIR = Path(__file__).parent
DIST_NAME   = "SolarisAssetManager_Installer"

# ── Data to bundle inside the EXE (source -> dest inside the bundle) ──────────
SEP = ";" if platform.system() == "Windows" else ":"

BUNDLE_DATA = [
    (str(ROOT / "scripts"),  "scripts"),
    (str(ROOT / "config"),   "config"),
    (str(ROOT / "houdini"),  "houdini"),
    (str(ROOT / "shelf"),    "shelf"),
    (str(ROOT / "package"),  "package"),
    (str(ROOT / "README.md"), "."),
]

# ── Build command ─────────────────────────────────────────────────────────────
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--clean",
    f"--name={DIST_NAME}",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(INSTALL_DIR),
]

# Windowed (no console) on Windows and macOS; keep console on Linux for clarity
if platform.system() in ("Windows", "Darwin"):
    cmd.append("--windowed")

# Icon (optional — place icon.ico / icon.icns in install/ to use)
ico_win = INSTALL_DIR / "icon.ico"
ico_mac = INSTALL_DIR / "icon.icns"
if platform.system() == "Windows" and ico_win.exists():
    cmd += ["--icon", str(ico_win)]
elif platform.system() == "Darwin" and ico_mac.exists():
    cmd += ["--icon", str(ico_mac)]

# Bundle plugin source files
for src, dst in BUNDLE_DATA:
    if Path(src).exists():
        cmd += ["--add-data", f"{src}{SEP}{dst}"]

# Entry point
cmd.append(str(INSTALL_DIR / "installer.py"))

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Building {DIST_NAME} …\n")
    print("Command:\n  " + " \\\n    ".join(cmd) + "\n")
    try:
        subprocess.run(cmd, check=True)
        out = ROOT / "dist" / (
            DIST_NAME + (".exe" if platform.system() == "Windows" else "")
        )
        print(f"\n✓ Built: {out}")
    except subprocess.CalledProcessError as e:
        print(f"\n✗ Build failed (exit {e.returncode})")
        print("Make sure PyInstaller is installed:  pip install pyinstaller")
        sys.exit(1)
