"""
Launcher for the Solaris Asset Manager.
Handles panel registration and standalone execution.
"""

import os
import sys
import traceback

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from asset_manager.qt_compat import QtWidgets, QtCore


# Global reference to prevent garbage collection
_PANEL_INSTANCE = None

def launch_panel():
    """
    Launches the Asset Manager as a standalone floating window.
    This is useful for debugging outside of the Python Panel pane.

    Re-clicking the shelf tool while the panel is open just brings it to
    the front. After closing, a fresh instance is created on next launch.
    """
    global _PANEL_INSTANCE

    def _is_healthy(widget):
        """A panel is healthy only if its full UI was built. A partial
        construction (e.g. one tab raised) leaves _tabs unparented to the
        main layout — those should be discarded, not reused."""
        try:
            tabs = getattr(widget, "_tabs", None)
            return tabs is not None and tabs.count() >= 3
        except Exception:
            return False

    # If we still hold a live reference and it's a complete panel, reuse it.
    if _PANEL_INSTANCE is not None:
        try:
            if _is_healthy(_PANEL_INSTANCE):
                if not _PANEL_INSTANCE.isVisible():
                    _PANEL_INSTANCE.show()
                _PANEL_INSTANCE.raise_()
                _PANEL_INSTANCE.activateWindow()
                return
            else:
                try:
                    _PANEL_INSTANCE.deleteLater()
                except Exception:
                    pass
                _PANEL_INSTANCE = None
        except RuntimeError:
            _PANEL_INSTANCE = None

    # Find by objectName in case the shelf cleared sys.modules. Destroy any
    # leftover partials from a previous crashed construction.
    for widget in list(QtWidgets.QApplication.topLevelWidgets()):
        try:
            if widget.objectName() != "AssetManagerMain":
                continue
            if _is_healthy(widget):
                _PANEL_INSTANCE = widget
                if not widget.isVisible():
                    widget.show()
                widget.raise_()
                widget.activateWindow()
                return
            try:
                widget.deleteLater()
            except Exception:
                pass
        except RuntimeError:
            continue

    from asset_manager.ui.main_panel import AssetManagerPanel

    try:
        print("[AssetManager] Creating panel instance...")
        parent = hou.qt.mainWindow() if HAS_HOU else None
        _PANEL_INSTANCE = AssetManagerPanel(parent)
        _PANEL_INSTANCE.setWindowFlags(QtCore.Qt.Window)
        _PANEL_INSTANCE.setModal(False)
        # Actually destroy the widget on close, so re-launching from the
        # shelf builds a fresh panel (and picks up any reloaded code).
        _PANEL_INSTANCE.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        _PANEL_INSTANCE.destroyed.connect(_on_panel_destroyed)
        _PANEL_INSTANCE.resize(1100, 750)

        print("[AssetManager] Showing window...")
        _PANEL_INSTANCE.show()
        _PANEL_INSTANCE.raise_()
        _PANEL_INSTANCE.activateWindow()

    except Exception:
        tb = traceback.format_exc()
        print("[AssetManager] Failed to launch:\n" + tb)
        if HAS_HOU:
            hou.ui.displayMessage(
                "Failed to launch Asset Manager.\n\n" + tb,
                severity=hou.severityType.Error,
                title="Asset Manager Error",
            )
        raise


def _on_panel_destroyed(*_):
    """Drop the global reference when Qt deletes the widget."""
    global _PANEL_INSTANCE
    _PANEL_INSTANCE = None

if __name__ == "__main__":
    app = QtWidgets.QApplication.get_instance()
    if not app:
        app = QtWidgets.QApplication(sys.argv)
    
    launch_panel()
    
    if not HAS_HOU:
        sys.exit(app.exec())
