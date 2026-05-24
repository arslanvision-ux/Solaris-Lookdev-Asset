"""
Thumbnail card widget for the Gallery Manager.
Displays an asset thumbnail with name and status badge.
Supports drag initiation for drop-into-scene workflow.
"""

import os

from asset_manager.qt_compat import QtWidgets, QtCore, QtGui


class ThumbnailWidget(QtWidgets.QFrame):
    """
    A clickable thumbnail card showing an asset preview image,
    the asset name, and a status badge. Supports drag-and-drop.
    """

    # Signals
    clicked = QtCore.Signal(str, QtCore.Qt.KeyboardModifiers)  # UID + modifiers
    double_clicked = QtCore.Signal(str)     # Emits asset UID
    context_menu_requested = QtCore.Signal(str, QtCore.QPoint)  # UID, position
    drag_started = QtCore.Signal(str)       # Emits dragging card's UID
    hover_entered = QtCore.Signal(str)      # Emits UID when cursor enters
    hover_left = QtCore.Signal(str)         # Emits UID when cursor leaves

    # Default size preset (Medium). Per-instance sizes are stored as
    # _thumb_size / _card_w / _card_h so the gallery can spawn cards
    # at any of the predefined scales without subclassing.
    THUMB_SIZE = 160
    CARD_WIDTH = 180
    CARD_HEIGHT = 220

    # (label, thumb px, card width, card height). Used by the gallery's
    # "Icon Size" picker.
    SIZE_PRESETS = {
        "small":   (96,  112, 144),
        "medium":  (160, 180, 220),
        "large":   (220, 240, 288),
        "largest": (300, 320, 376),
    }

    def __init__(self, uid: str, name: str,
                 thumbnail_path: str = "",
                 status: str = "ready",
                 usd_path: str = "",
                 size_preset: str = "medium",
                 parent=None):
        super().__init__(parent)
        self._uid = uid
        self._name = name
        self._thumbnail_path = thumbnail_path
        self._status = status
        self._usd_path = usd_path
        self._selected = False
        self._drag_start_pos = None

        thumb, card_w, card_h = self.SIZE_PRESETS.get(
            size_preset, self.SIZE_PRESETS["medium"]
        )
        self._thumb_size = thumb
        self._card_w = card_w
        self._card_h = card_h

        self.setObjectName("thumbnailCard")
        self.setFixedSize(self._card_w, self._card_h)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.setAttribute(QtCore.Qt.WA_Hover, True)

        self._build_ui()
        self._apply_styles()

    @property
    def uid(self) -> str:
        return self._uid

    @property
    def asset_name(self) -> str:
        return self._name

    @property
    def usd_path(self) -> str:
        return self._usd_path

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # Thumbnail image
        self._thumb_label = QtWidgets.QLabel()
        self._thumb_label.setObjectName("thumbnailImage")
        self._thumb_label.setFixedSize(self._thumb_size, self._thumb_size)
        self._thumb_label.setAlignment(QtCore.Qt.AlignCenter)
        self._thumb_label.setScaledContents(False)
        self._load_thumbnail()
        layout.addWidget(self._thumb_label, alignment=QtCore.Qt.AlignCenter)

        # Bottom row: name + status
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(4)

        self._name_label = QtWidgets.QLabel(self._name)
        self._name_label.setObjectName("assetName")
        self._name_label.setToolTip(self._name)
        self._name_label.setMaximumWidth(self._card_w - 60)
        font_metrics = self._name_label.fontMetrics()
        elided = font_metrics.elidedText(
            self._name, QtCore.Qt.ElideRight, self._card_w - 60
        )
        self._name_label.setText(elided)
        bottom.addWidget(self._name_label)

        bottom.addStretch()

        self._status_badge = QtWidgets.QLabel(self._status.upper())
        self._status_badge.setObjectName("statusBadge")
        self._update_badge_color()
        bottom.addWidget(self._status_badge)

        layout.addLayout(bottom)

    def _load_thumbnail(self):
        """Load the thumbnail image or show a placeholder.

        The render-info overlay used to be stamped onto the bottom-left
        of the image, but it cluttered the gallery. The same info is
        available via the hover tooltip and the double-click preview
        dialog, so the thumbnail itself stays clean.
        """
        if self._thumbnail_path and os.path.exists(self._thumbnail_path):
            # Evict any Qt pixmap cache entry for this path so a re-render
            # always shows the freshly written file rather than a stale copy.
            QtGui.QPixmapCache.remove(self._thumbnail_path)
            pixmap = QtGui.QPixmap(self._thumbnail_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._thumb_size, self._thumb_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation
                )
                self._thumb_label.setPixmap(scaled)
                return

        # Placeholder: draw a simple icon
        from .styles import COLORS
        pixmap = QtGui.QPixmap(self._thumb_size, self._thumb_size)
        pixmap.fill(QtGui.QColor(COLORS['bg_darkest']))
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        pen = QtGui.QPen(QtGui.QColor(COLORS['border']))
        pen.setWidth(2)
        painter.setPen(pen)
        # Draw a cube outline
        cx, cy = self._thumb_size // 2, self._thumb_size // 2
        s = 30
        # Front face
        painter.drawRect(cx - s, cy - s, s * 2, s * 2)
        # Top face
        pts = [
            QtCore.QPoint(cx - s, cy - s),
            QtCore.QPoint(cx - s + 15, cy - s - 15),
            QtCore.QPoint(cx + s + 15, cy - s - 15),
            QtCore.QPoint(cx + s, cy - s),
        ]
        painter.drawPolyline(pts)
        # Right face
        pts2 = [
            QtCore.QPoint(cx + s, cy - s),
            QtCore.QPoint(cx + s + 15, cy - s - 15),
            QtCore.QPoint(cx + s + 15, cy + s - 15),
            QtCore.QPoint(cx + s, cy + s),
        ]
        painter.drawPolyline(pts2)

        # Asset name initial
        painter.setPen(QtGui.QColor(COLORS['text_dim']))
        font = painter.font()
        font.setPixelSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(
            pixmap.rect(), QtCore.Qt.AlignCenter,
            self._name[0].upper() if self._name else "?"
        )
        painter.end()
        self._thumb_label.setPixmap(pixmap)

    def _apply_styles(self):
        from .styles import THUMBNAIL_CARD_STYLE
        self.setStyleSheet(THUMBNAIL_CARD_STYLE)

    def _update_badge_color(self):
        from .styles import get_status_badge_color
        color = get_status_badge_color(self._status)
        self._status_badge.setStyleSheet(
            f"background-color: {color}; color: #ffffff; "
            f"font-size: 9px; padding: 1px 6px; border-radius: 6px;"
        )

    def set_selected(self, selected: bool):
        from .styles import COLORS
        self._selected = selected
        if selected:
            self.setStyleSheet(
                self.styleSheet() +
                f"QFrame#thumbnailCard {{ border: 2px solid {COLORS['accent']}; }}"
            )
        else:
            self._apply_styles()

    def update_thumbnail(self, path: str):
        self._thumbnail_path = path
        self._load_thumbnail()

    def _load_render_info(self) -> dict:
        """Read the JSON sidecar next to the thumbnail if it exists."""
        if not self._thumbnail_path:
            return {}
        sidecar = os.path.splitext(self._thumbnail_path)[0] + ".json"
        if not os.path.exists(sidecar):
            return {}
        try:
            import json as _json
            with open(sidecar, "r", encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return {}

    def _render_info_overlay(self, base: QtGui.QPixmap) -> QtGui.QPixmap:
        """Stamp engine/samples/resolution and render-time onto the
        thumbnail. Two lines: the first carries the rendering config,
        the second the elapsed render time + texture count."""
        info = self._load_render_info()
        if not info:
            return base

        # ── Line 1: engine · samples · resolution ──
        engine = info.get("engine") or info.get("renderer_key") or ""
        samples = info.get("samples")
        res = info.get("resolution")
        line1_bits = []
        if engine:
            line1_bits.append(str(engine))
        if samples is not None:
            line1_bits.append(f"{samples}spp")
        if res and isinstance(res, (list, tuple)) and len(res) == 2:
            line1_bits.append(f"{res[0]}x{res[1]}")

        # ── Line 2: render time · texture count ──
        line2_bits = []
        rt = info.get("render_time_seconds")
        if rt is not None:
            try:
                rt_f = float(rt)
                if rt_f >= 60:
                    line2_bits.append(f"{int(rt_f // 60)}m{rt_f % 60:.0f}s")
                else:
                    line2_bits.append(f"{rt_f:.1f}s")
            except (TypeError, ValueError):
                pass
        textures = info.get("textures") or {}
        if isinstance(textures, dict) and textures:
            line2_bits.append(f"{len(textures)} tex")

        if not line1_bits and not line2_bits:
            return base

        out = QtGui.QPixmap(base)
        painter = QtGui.QPainter(out)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        font = painter.font()
        font.setPixelSize(9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        line_h = fm.height() + 2

        # Stack from the bottom — line 2 (render time) sits below line 1.
        y_offset = 4
        for bits in (line2_bits, line1_bits):
            if not bits:
                continue
            text = " · ".join(bits)
            w = fm.horizontalAdvance(text) + 8
            rect = QtCore.QRect(
                4, out.height() - y_offset - line_h, w, line_h,
            )
            painter.fillRect(rect, QtGui.QColor(0, 0, 0, 170))
            painter.setPen(QtGui.QColor("#d6d6e0"))
            painter.drawText(
                rect, QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter, text,
            )
            y_offset += line_h + 2
        painter.end()
        return out

    def _build_tooltip(self) -> str:
        info = self._load_render_info()
        if not info:
            return self._name
        lines = [f"<b>{self._name}</b>"]

        # Render config
        for label, key in (
            ("Engine",       "engine"),
            ("Renderer",     "renderer_key"),
            ("Samples",      "samples"),
            ("Resolution",   "resolution"),
            ("Render Time",  "render_time_seconds"),
            ("Focal",        "focal"),
            ("Aperture (H)", "aperture_h"),
            ("Clip Near",    "clip_near"),
            ("Clip Far",     "clip_far"),
            ("Yaw",          "yaw"),
            ("Pitch",        "pitch"),
            ("HDRI",         "hdri"),
            ("Rendered At",  "rendered_at"),
        ):
            if key in info and info[key] not in ("", None):
                val = info[key]
                if key == "render_time_seconds":
                    try:
                        val = f"{float(val):.2f}s"
                    except (TypeError, ValueError):
                        pass
                lines.append(f"<b>{label}:</b> {val}")

        # Source / textures
        src = info.get("source_dir")
        if src:
            lines.append("")
            lines.append(f"<b>Source:</b> {src}")
        textures = info.get("textures") or {}
        if isinstance(textures, dict) and textures:
            lines.append(f"<b>Textures ({len(textures)}):</b>")
            for map_type, path in textures.items():
                lines.append(f"  • {map_type}: {path}")

        return "<br>".join(lines)

    def update_status(self, status: str):
        self._status = status
        self._status_badge.setText(status.upper())
        self._update_badge_color()

    # ── Events ──

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._drag_start_pos = event.position().toPoint()
            self.clicked.emit(self._uid, event.modifiers())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.double_clicked.emit(self._uid)
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_start_pos and
            (event.position().toPoint() - self._drag_start_pos).manhattanLength()
                > QtWidgets.QApplication.startDragDistance()):
            self._drag_start_pos = None
            self.drag_started.emit(self._uid)
        super().mouseMoveEvent(event)

    def start_drag(self, usd_paths: "dict[str, str]"):
        """Execute a drag carrying all provided uid→usd_path entries."""
        usd_paths = {uid: p for uid, p in usd_paths.items() if p}
        if not usd_paths:
            return

        drag = QtGui.QDrag(self)
        mime_data = QtCore.QMimeData()

        urls = [QtCore.QUrl.fromLocalFile(p) for p in usd_paths.values()]
        mime_data.setUrls(urls)
        mime_data.setText("\n".join(usd_paths.values()))
        mime_data.setData(
            "application/x-asset-manager-uid",
            "\n".join(usd_paths.keys()).encode("utf-8"),
        )
        drag.setMimeData(mime_data)

        if self._thumb_label.pixmap() and not self._thumb_label.pixmap().isNull():
            scaled = self._thumb_label.pixmap().scaled(
                80, 80, QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            drag.setPixmap(scaled)
            drag.setHotSpot(QtCore.QPoint(40, 40))

        drag.exec(QtCore.Qt.CopyAction)

    def _on_context_menu(self, pos):
        self.context_menu_requested.emit(
            self._uid, self.mapToGlobal(pos)
        )

    def enterEvent(self, event):
        self.hover_entered.emit(self._uid)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_left.emit(self._uid)
        super().leaveEvent(event)

    def thumbnail_global_pos(self) -> QtCore.QPoint:
        """Top-right corner of the thumbnail image in screen coords.

        Used by the gallery to anchor the hover preview popup.
        """
        local = QtCore.QPoint(
            self._thumb_label.x() + self._thumb_label.width(),
            self._thumb_label.y(),
        )
        return self.mapToGlobal(local)
