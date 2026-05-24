"""
Qt compatibility shim: PySide6 (H20.5+) with PySide2 fallback (H19/H20).
Import QtWidgets, QtCore, QtGui from here instead of directly from PySide6/2.
"""

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    QT_MAJOR = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui  # type: ignore[no-redef]
    QT_MAJOR = 2
    # PySide6 moved QAction to QtGui; re-export it there so callers are uniform.
    if not hasattr(QtGui, "QAction"):
        QtGui.QAction = QtWidgets.QAction  # type: ignore[attr-defined]

__all__ = ["QtWidgets", "QtCore", "QtGui", "QT_MAJOR"]
