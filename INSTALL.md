# Solaris Asset Manager — Installation Guide

**Version:** 1.0.0  
**Requires:** Houdini 20+ (Solaris / USD), Python 3.10+

---

## Quick Install

| Platform | Method |
|----------|--------|
| **Windows** | Double-click `install\install.bat` — or run the `.exe` from Releases |
| **Linux**   | `bash install/install.sh` |
| **macOS**   | Double-click `install/install.command` — or `bash install/install.sh` |

The installer will:
1. Auto-detect your Houdini installation
2. Copy plugin files to a directory you choose
3. Write a Houdini package JSON so Houdini loads the plugin on startup
4. The **Asset Manager** shelf tab appears next time you launch Houdini

---

## What the Installer Creates

```
{install_dir}/             ← you choose this (default: ~/Documents/solaris_asset_manager)
├── scripts/               ← Python source (added to PYTHONPATH)
├── config/                ← naming_conventions.json, renderer_settings.json
├── houdini/scripts/       ← 456.py startup + drag-drop handler
├── shelf/                 ← asset_manager.shelf (added to HOUDINI_TOOLBAR_PATH)
└── package/               ← reference copy of the package JSON

{houdini_prefs}/packages/
└── asset_manager.json     ← Houdini package file (written by installer)
```

### Houdini Prefs Directory (auto-detected)

| Platform | Default Location |
|----------|-----------------|
| Windows  | `%USERPROFILE%\Documents\houdini21.5\` |
| Linux    | `~/houdini21.5/` |
| macOS    | `~/houdini21.5/` or `~/Library/Preferences/houdini/21.5/` |

---

## Manual Installation (Advanced)

If the automated installer doesn't work, follow these steps:

### 1 — Copy the plugin files

```bash
# Choose your install location
INSTALL_DIR="$HOME/solaris_asset_manager"
cp -r . "$INSTALL_DIR"
```

### 2 — Write the Houdini package JSON

Create `{houdini_prefs}/packages/asset_manager.json` with the following content,
replacing `INSTALL_DIR` with the absolute path from Step 1:

```json
{
    "env": [
        {
            "PYTHONPATH": {
                "value": "INSTALL_DIR/scripts",
                "method": "prepend"
            }
        },
        {
            "HOUDINI_SCRIPT_PATH": {
                "value": "INSTALL_DIR/houdini/scripts",
                "method": "prepend"
            }
        },
        {
            "HOUDINI_TOOLBAR_PATH": {
                "value": "INSTALL_DIR/shelf",
                "method": "prepend"
            }
        }
    ]
}
```

### 3 — Restart Houdini

The **Asset Manager** shelf tab will appear automatically.

---

## Building the Installer EXE (Developers)

Requires [PyInstaller](https://pyinstaller.org):

```bash
pip install pyinstaller
python install/build_exe.py
```

Output: `dist/SolarisAssetManager_Installer.exe` (Windows) or
`dist/SolarisAssetManager_Installer` (Linux/macOS).

The EXE bundles all plugin source files — no separate download needed.

---

## Uninstalling

1. Delete the plugin install directory (`~/Documents/solaris_asset_manager` or wherever you chose)
2. Delete `{houdini_prefs}/packages/asset_manager.json`
3. Restart Houdini

---

## Troubleshooting

**Shelf doesn't appear**
- Verify `{houdini_prefs}/packages/asset_manager.json` exists
- Check that the paths inside the JSON match the actual install location
- Run Houdini from the command line and look for import errors

**"Python not found" when running install.bat**
- Open *Houdini Command Line Tools* from the Start menu (runs inside Houdini's Python)
- Then run: `python install\installer.py`

**macOS: "install.command cannot be opened"**
- Right-click → Open → Open (first time only, to bypass Gatekeeper)
- Or: `chmod +x install/install.command && bash install/install.command`

**Linux: headless / SSH server**
```bash
bash install/install.sh --cli
# or
python3 install/installer.py --cli
```

---

## Renderer Support

| Renderer | Status |
|----------|--------|
| Karma CPU | ✓ Fully supported |
| Karma XPU | ✓ Fully supported |
| Arnold    | ✓ Supported (requires Arnold for Houdini) |
| Redshift  | ✓ Supported (requires Redshift for Houdini) |
