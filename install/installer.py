#!/usr/bin/env python3
"""
Solaris Asset Manager — Cross-platform GUI/CLI Installer
No external dependencies (stdlib only: tkinter + pathlib + shutil + json).

Usage:
    python installer.py          # GUI mode
    python installer.py --cli    # headless CLI mode
"""

import os
import sys
import shutil
import json
import platform
import glob
import re
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    HAS_TK = True
except ImportError:
    HAS_TK = False

# ── Constants ──────────────────────────────────────────────────────────────────
PLUGIN_NAME         = "solaris_asset_manager"
PLUGIN_VERSION      = "1.0.0"
PLUGIN_DISPLAY_NAME = "Solaris Asset Manager"
MIN_HOUDINI_MAJOR   = 19

# Files/dirs to exclude from the installed copy
_EXCLUDE = {"__pycache__", ".claude", ".git", ".github",
            "tests", "install", "dist", "build", "*.spec"}

ACCENT  = "#d4853a"
BG_DARK = "#1c1c1c"
FG_MAIN = "#e8e8e8"
FG_DIM  = "#9a9a9a"


# ── Helpers ────────────────────────────────────────────────────────────────────

def src_root() -> Path:
    """Plugin source root whether running as script or PyInstaller EXE."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)          # PyInstaller extracts here
    return Path(__file__).parent.parent    # install/ -> project root


def find_houdini_prefs() -> "list[tuple[str, Path]]":
    """Return [(version_str, pref_dir), ...] sorted newest-first."""
    system = platform.system()
    patterns: list[str] = []

    if system == "Windows":
        base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
        patterns.append(str(base / "houdini*"))
    elif system == "Darwin":
        patterns.append(str(Path("~/Library/Preferences/houdini").expanduser() / "*"))
        patterns.append(str(Path.home() / "houdini*"))
    else:  # Linux
        patterns.append(str(Path.home() / "houdini*"))

    found: dict[str, Path] = {}
    for pat in patterns:
        for d in glob.glob(pat):
            p = Path(d)
            if not p.is_dir():
                continue
            m = re.search(r"(\d+\.\d+)", p.name)
            if m and int(m.group(1).split(".")[0]) >= MIN_HOUDINI_MAJOR:
                found.setdefault(m.group(1), p)

    return sorted(found.items(),
                  key=lambda x: tuple(int(i) for i in x[0].split(".")),
                  reverse=True)


def default_install_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
    elif system == "Darwin":
        base = Path("~/Library/Application Support").expanduser()
    else:
        base = Path("~/.local/share").expanduser()
    return base / PLUGIN_NAME


def copy_plugin(src: Path, dst: Path):
    """Copy plugin source to dst, stripping __pycache__ and dev artifacts."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in _EXCLUDE or item.name.startswith("."):
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(
                item, target,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
            )
        else:
            shutil.copy2(item, target)


def write_package_json(pref_dir: Path, install_dir: Path) -> Path:
    """Write the Houdini package JSON that wires PYTHONPATH, shelf, scripts."""
    pkg = {
        "env": [
            {
                "PYTHONPATH": {
                    "value": str(install_dir / "scripts"),
                    "method": "prepend",
                }
            },
            {
                "HOUDINI_SCRIPT_PATH": {
                    "value": str(install_dir / "houdini" / "scripts"),
                    "method": "prepend",
                }
            },
            {
                "HOUDINI_TOOLBAR_PATH": {
                    "value": str(install_dir / "shelf"),
                    "method": "prepend",
                }
            },
        ]
    }
    pkg_dir = pref_dir / "packages"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    out = pkg_dir / "asset_manager.json"
    out.write_text(json.dumps(pkg, indent=4))
    return out


# ── GUI installer ──────────────────────────────────────────────────────────────

class InstallerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{PLUGIN_DISPLAY_NAME} — Installer v{PLUGIN_VERSION}")
        self.configure(bg="#2a2a2a")
        self.resizable(False, False)
        self._src       = src_root()
        self._pref_dirs = find_houdini_prefs()
        self._build_ui()
        self._center()

    def _center(self):
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{(self.winfo_screenwidth()-w)//2}+{(self.winfo_screenheight()-h)//2}")

    # ── UI construction ────────────────────────────────────────────────────────

    def _label(self, parent, text, bold=False, color=FG_MAIN, size=10):
        font = ("Segoe UI" if platform.system() == "Windows" else "Helvetica",
                size, "bold" if bold else "normal")
        return tk.Label(parent, text=text, font=font, fg=color, bg="#2a2a2a")

    def _lframe(self, parent, text):
        return tk.LabelFrame(parent, text=text, bg="#2a2a2a", fg=FG_DIM,
                             relief="groove", padx=10, pady=8)

    def _build_ui(self):
        PAD = dict(padx=16, pady=5)

        # Header
        hdr = tk.Frame(self, bg=ACCENT)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"  {PLUGIN_DISPLAY_NAME}",
                 font=("Segoe UI" if platform.system()=="Windows" else "Helvetica", 16, "bold"),
                 fg="white", bg=ACCENT, pady=12).pack(side="left")
        tk.Label(hdr, text=f"v{PLUGIN_VERSION}  ",
                 font=("Segoe UI" if platform.system()=="Windows" else "Helvetica", 10),
                 fg="#ffffffaa", bg=ACCENT).pack(side="right", anchor="s", pady=16)

        self._label(self, "Houdini 20+  •  Solaris / USD Pipeline Tool",
                    color=FG_DIM).pack(**PAD, pady=(10, 2))

        tk.Frame(self, bg="#383838", height=1).pack(fill="x", padx=16)

        # ── Houdini version picker ──
        frm1 = self._lframe(self, "Houdini Installation")
        frm1.pack(fill="x", padx=16, pady=(10, 4))

        if self._pref_dirs:
            self._hou_var = tk.StringVar()
            labels = [f"Houdini {v}   —   {p}" for v, p in self._pref_dirs]
            cb = ttk.Combobox(frm1, textvariable=self._hou_var,
                              values=labels, state="readonly", width=60)
            cb.current(0)
            cb.pack(fill="x")
            self._hou_combo = cb
        else:
            tk.Label(frm1,
                     text="⚠  No Houdini installation detected — enter prefs dir below.",
                     fg="#f39c12", bg="#2a2a2a").pack()
            self._hou_combo = None

        # ── Manual prefs dir ──
        frm2 = self._lframe(self, "Houdini Prefs Dir  (leave blank to use selection above)")
        frm2.pack(fill="x", padx=16, pady=4)
        row = tk.Frame(frm2, bg="#2a2a2a")
        row.pack(fill="x")
        self._pref_var = tk.StringVar()
        tk.Entry(row, textvariable=self._pref_var, width=54,
                 bg="#1c1c1c", fg=FG_MAIN, insertbackground=FG_MAIN).pack(side="left")
        tk.Button(row, text="Browse", command=self._browse_pref,
                  bg="#3c3c3c", fg=FG_MAIN).pack(side="left", padx=6)

        # ── Install dir ──
        frm3 = self._lframe(self, "Plugin Install Directory")
        frm3.pack(fill="x", padx=16, pady=4)
        row2 = tk.Frame(frm3, bg="#2a2a2a")
        row2.pack(fill="x")
        self._install_var = tk.StringVar(value=str(default_install_dir()))
        tk.Entry(row2, textvariable=self._install_var, width=54,
                 bg="#1c1c1c", fg=FG_MAIN, insertbackground=FG_MAIN).pack(side="left")
        tk.Button(row2, text="Browse", command=self._browse_install,
                  bg="#3c3c3c", fg=FG_MAIN).pack(side="left", padx=6)

        # ── Log output ──
        tk.Frame(self, bg="#383838", height=1).pack(fill="x", padx=16, pady=(8, 0))
        self._log = tk.Text(self, height=8, width=72, state="disabled",
                            bg=BG_DARK, fg="#c8c8c8", font=("Consolas", 9),
                            relief="flat", padx=6, pady=4)
        self._log.pack(padx=16, pady=8)

        # ── Buttons ──
        btn_row = tk.Frame(self, bg="#2a2a2a")
        btn_row.pack(pady=(0, 16))
        tk.Button(btn_row, text="  Install  ", command=self._run_install,
                  bg=ACCENT, fg="white",
                  font=("Segoe UI" if platform.system()=="Windows" else "Helvetica",
                        11, "bold"),
                  relief="flat", padx=18, pady=7,
                  activebackground="#e09550").pack(side="left", padx=8)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg="#3c3c3c", fg=FG_MAIN, padx=14, pady=7,
                  relief="flat").pack(side="left")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log_write(self, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")
        self.update()

    def _browse_pref(self):
        d = filedialog.askdirectory(title="Select Houdini Prefs Directory")
        if d:
            self._pref_var.set(d)

    def _browse_install(self):
        d = filedialog.askdirectory(title="Select Plugin Install Directory")
        if d:
            self._install_var.set(d)

    def _resolve_pref_dir(self) -> "Path | None":
        manual = self._pref_var.get().strip()
        if manual:
            return Path(manual)
        if self._pref_dirs and self._hou_combo is not None:
            idx = self._hou_combo.current()
            if 0 <= idx < len(self._pref_dirs):
                return self._pref_dirs[idx][1]
        return None

    def _run_install(self):
        pref_dir = self._resolve_pref_dir()
        if not pref_dir:
            messagebox.showerror("Error", "No Houdini prefs directory selected.")
            return
        install_dir = Path(self._install_var.get().strip())
        if not install_dir.name:
            messagebox.showerror("Error", "No install directory specified.")
            return

        self._log_write(f"Source       : {self._src}")
        self._log_write(f"Install dir  : {install_dir}")
        self._log_write(f"Prefs dir    : {pref_dir}")
        self._log_write("")
        try:
            self._log_write("Copying plugin files …")
            copy_plugin(self._src, install_dir)
            self._log_write("  ✓ Files copied")

            self._log_write("Writing Houdini package JSON …")
            pkg_path = write_package_json(pref_dir, install_dir)
            self._log_write(f"  ✓ {pkg_path}")

            self._log_write("")
            self._log_write("✓ Installation complete!  Restart Houdini to activate.")
            messagebox.showinfo(
                "Installed",
                f"{PLUGIN_DISPLAY_NAME} installed successfully!\n\n"
                f"Restart Houdini to activate the plugin.\n"
                f"The 'Asset Manager' shelf tab will appear automatically.",
            )
        except Exception as exc:
            self._log_write(f"\n✗  ERROR: {exc}")
            messagebox.showerror("Installation Failed", str(exc))


# ── CLI fallback ───────────────────────────────────────────────────────────────

def cli_install():
    print(f"\n{'='*52}")
    print(f"  {PLUGIN_DISPLAY_NAME} v{PLUGIN_VERSION} — CLI Installer")
    print(f"{'='*52}\n")

    pref_dirs = find_houdini_prefs()
    if pref_dirs:
        print("Detected Houdini installations:")
        for i, (ver, path) in enumerate(pref_dirs):
            print(f"  [{i}] Houdini {ver}  —  {path}")
        choice = input("\nSelect [0]: ").strip() or "0"
        pref_dir = pref_dirs[int(choice)][1]
    else:
        raw = input("Houdini prefs directory: ").strip()
        pref_dir = Path(raw)

    default = default_install_dir()
    raw_install = input(f"Install directory [{default}]: ").strip()
    install_dir = Path(raw_install) if raw_install else default

    print(f"\nInstalling to  : {install_dir}")
    print(f"Prefs dir      : {pref_dir}\n")

    copy_plugin(src_root(), install_dir)
    print("  ✓ Files copied")

    pkg = write_package_json(pref_dir, install_dir)
    print(f"  ✓ Package JSON : {pkg}")

    print("\n✓ Done — restart Houdini to activate.\n")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--cli" in sys.argv or not HAS_TK:
        cli_install()
    else:
        try:
            app = InstallerApp()
            app.mainloop()
        except tk.TclError:
            # No display available (headless server)
            cli_install()
