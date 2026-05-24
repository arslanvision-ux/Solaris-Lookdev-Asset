"""
Main Python Panel for the Houdini Solaris LOP Asset Manager.
A QTabWidget with Scanner, Gallery, and Settings tabs.
This is the entry point registered with Houdini's Python Panel system.
"""

import os
import sys

from asset_manager.qt_compat import QtWidgets, QtCore, QtGui
import tempfile


# Ensure our package is importable
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from asset_manager.database.asset_db import AssetDatabase
from asset_manager.ui.scanner_tab import ScannerTab
from asset_manager.ui.gallery_tab import GalleryTab
from asset_manager.ui.settings_tab import SettingsTab
from asset_manager.ui.turntable_tab import TurntableTab
from asset_manager.ui.styles import MAIN_STYLESHEET, COLORS


class AssetManagerPanel(QtWidgets.QDialog):
    """
    The main Asset Manager panel. Designed to run as a Houdini Python Panel
    or as a standalone dialog for development/testing.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        print("[AssetManager] [1/5] Initializing Window...")
        self.setWindowTitle("Solaris Asset Manager")
        self.setObjectName("AssetManagerMain")
        self.setMinimumSize(800, 600)

        # Initialize database
        print("[AssetManager] [2/5] Initializing Database...")
        self._db = self._init_database()

        # Build UI
        print("[AssetManager] [3/5] Building UI Components...")
        self._build_ui()
        
        if MAIN_STYLESHEET:
            print("[AssetManager] [4/5] Applying Stylesheet...")
            self.setStyleSheet(MAIN_STYLESHEET)

        # Load gallery on startup
        print("[AssetManager] [5/5] Refreshing Gallery...")
        self._gallery_tab.refresh_gallery()
        print("[AssetManager] Ready.")


    def _init_database(self) -> AssetDatabase:
        """Initialize the SQLite database in a sensible location."""
        # Check for environment variable
        db_path = os.environ.get("ASSET_MANAGER_DB")
        if not db_path:
            # Fallback to user home
            db_dir = os.path.join(os.path.expanduser("~"), "houdini", "asset_manager")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "assets.db")
            
        print(f"[AssetManager] Using Database: {db_path}")
        return AssetDatabase(db_path)

    def _build_ui(self):

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Title Bar ──
        title_bar = QtWidgets.QFrame()
        title_bar.setFixedHeight(52)
        title_bar.setStyleSheet(
            f"background-color: {COLORS['bg_dark']}; "
            f"border-bottom: 1px solid {COLORS['border']};"
        )
        title_layout = QtWidgets.QHBoxLayout(title_bar)
        title_layout.setContentsMargins(16, 0, 16, 0)

        # Logo / Title
        logo_label = QtWidgets.QLabel("◆")
        logo_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 20px; font-weight: bold;"
        )
        title_layout.addWidget(logo_label)

        title = QtWidgets.QLabel("Solaris Asset Manager")
        title.setStyleSheet(
            f"color: {COLORS['text_primary']}; font-size: 15px; "
            f"font-weight: 700; letter-spacing: 0.5px;"
        )
        title_layout.addWidget(title)

        version_label = QtWidgets.QLabel("v1.0")
        version_label.setStyleSheet(
            f"color: {COLORS['text_dim']}; font-size: 10px;"
        )
        title_layout.addWidget(version_label)

        title_layout.addStretch()

        # DB status
        self._db_status = QtWidgets.QLabel(f"● {self._db.count} assets")
        self._db_status.setStyleSheet(
            f"color: {COLORS['accent']}; font-size: 11px;"
        )
        title_layout.addWidget(self._db_status)

        layout.addWidget(title_bar)

        # ── Tab Widget ──
        self._tabs = QtWidgets.QTabWidget()

        # Scanner tab
        self._scanner_tab = ScannerTab(self._db)
        self._scanner_tab.assets_processed.connect(self._on_assets_processed)
        self._tabs.addTab(self._scanner_tab, "  Scanner  ")

        # Gallery tab
        self._gallery_tab = GalleryTab(self._db)
        self._tabs.addTab(self._gallery_tab, "  Gallery  ")

        # Settings tab
        self._settings_tab = SettingsTab(self._db)
        self._settings_tab.settings_changed.connect(self._on_settings_changed)
        self._tabs.addTab(self._settings_tab, "  Settings  ")

        # Turn Table tab
        self._turntable_tab = TurntableTab(self._db)
        self._turntable_tab.settings_changed.connect(self._on_settings_changed)
        self._tabs.addTab(self._turntable_tab, "  Turn Table  ")

        layout.addWidget(self._tabs)


    def _on_assets_processed(self):
        """Called when batch processing completes."""
        self._gallery_tab.refresh_gallery()
        self._update_db_status()

    def _on_settings_changed(self):
        """Called when settings change."""
        self._gallery_tab.refresh_gallery()
        self._update_db_status()

    def _update_db_status(self):
        count = self._db.count
        self._db_status.setText(f"● {count} assets")


# ────────────────────────────────────────────────────────
# Houdini Python Panel Interface
# ────────────────────────────────────────────────────────

def onCreateInterface():
    """
    Houdini Python Panel callback.
    Called by Houdini when creating the panel interface.
    """
    return AssetManagerPanel()


# ────────────────────────────────────────────────────────
# Standalone testing
# ────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)

    # Load Inter font if available
    font_db = QtGui.QFontDatabase()
    app.setFont(QtGui.QFont("Inter", 10))

    panel = AssetManagerPanel()
    panel.resize(1100, 750)
    panel.show()
    sys.exit(app.exec())
