"""
Hover preview popup for the Gallery.

Displays a larger, animated preview of an asset when the user hovers over
its thumbnail card. Plays a PNG sequence (rendered by the turntable
pipeline) via a QTimer.

The popup is frameless, follows the cursor on appearance, and disappears
when the cursor leaves the source card. When pinned, it stays open until
the user clicks elsewhere.
"""

import os
import re

from asset_manager.qt_compat import QtWidgets, QtCore, QtGui

from .styles import COLORS


# Filenames produced by the turntable renderer are zero-padded:
#   frame_0000.png, frame_0001.png, ...
_FRAME_RE = re.compile(r"^frame_(\d+)\.png$", re.IGNORECASE)


def find_turntable_dir(asset_name: str, thumbnail_path: str,
                       usd_output_path: str = "") -> str:
    """Locate the per-asset turntable frame directory.

    Checks for frames in a dedicated turntable subfolder first, then falls
    back to legacy locations for backwards compatibility.

    Resolution order:
      1. <dirname(thumbnail_path)>/turntable/<asset_name>_turntable/
      2. <dirname(thumbnail_path)>/<asset_name>_turntable/  (legacy)
      3. <dirname(usd_output_path)>/turntable/<asset_name>_turntable/
      4. <dirname(usd_output_path)>/<asset_name>_turntable/  (legacy)
      5. <home>/houdini/asset_manager/thumbnails/turntable/<asset_name>_turntable/
      6. <home>/houdini/asset_manager/thumbnails/<asset_name>_turntable/  (legacy)
    Returns "" if none exist.
    """
    candidates = []
    if thumbnail_path:
        thumb_dir = os.path.dirname(thumbnail_path)
        candidates.append(os.path.join(thumb_dir, "turntable", f"{asset_name}_turntable"))
        candidates.append(os.path.join(thumb_dir, f"{asset_name}_turntable"))
    if usd_output_path:
        usd_dir = os.path.dirname(usd_output_path)
        candidates.append(os.path.join(usd_dir, "turntable", f"{asset_name}_turntable"))
        candidates.append(os.path.join(usd_dir, f"{asset_name}_turntable"))
    houdini_base = os.path.join(os.path.expanduser("~"), "houdini", "asset_manager", "thumbnails")
    candidates.append(os.path.join(houdini_base, "turntable", f"{asset_name}_turntable"))
    candidates.append(os.path.join(houdini_base, f"{asset_name}_turntable"))

    for c in candidates:
        c = c.replace("\\", "/")
        if os.path.isdir(c):
            return c
    return ""


def list_turntable_frames(turntable_dir: str) -> "list[str]":
    """Return all frame_NNNN.png files in the directory, sorted by index."""
    if not turntable_dir or not os.path.isdir(turntable_dir):
        return []
    frames = []
    for fn in os.listdir(turntable_dir):
        m = _FRAME_RE.match(fn)
        if m:
            frames.append((int(m.group(1)),
                           os.path.join(turntable_dir, fn)))
    frames.sort(key=lambda x: x[0])
    return [p for _idx, p in frames]


class HoverPreviewPopup(QtWidgets.QWidget):
    """A frameless popup that plays a turntable PNG sequence.

    Designed as a singleton owned by GalleryTab — calling `show_for(...)`
    swaps in a new asset's frames; calling `hide_preview()` stops
    playback. Reuse avoids the cost of recreating the widget on every
    hover.
    """

    # Loop modes
    LOOP        = "loop"
    PING_PONG   = "pingpong"
    ONCE        = "once"

    def __init__(self, parent=None):
        super().__init__(
            parent,
            QtCore.Qt.ToolTip | QtCore.Qt.FramelessWindowHint,
        )
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, False)
        self.setAttribute(QtCore.Qt.WA_ShowWithoutActivating, True)

        self._frames: "list[str]" = []
        self._pixmap_cache: "dict[int, QtGui.QPixmap]" = {}
        self._frame_index = 0
        self._direction = 1            # +1 or -1 (ping-pong toggles this)
        self._loop_mode = self.LOOP
        self._pinned = False
        self._target_pixel_size = QtCore.QSize(384, 384)
        self._current_uid = ""

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._advance)

        # Build UI
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        frame = QtWidgets.QFrame()
        frame.setObjectName("hoverPreviewFrame")
        frame.setStyleSheet(
            f"#hoverPreviewFrame {{"
            f"  background-color: {COLORS['bg_darkest']};"
            f"  border: 1px solid {COLORS['accent']};"
            f"  border-radius: 6px;"
            f"}}"
        )
        frame_layout = QtWidgets.QVBoxLayout(frame)
        frame_layout.setContentsMargins(6, 6, 6, 6)
        frame_layout.setSpacing(4)

        self._image_label = QtWidgets.QLabel()
        self._image_label.setAlignment(QtCore.Qt.AlignCenter)
        self._image_label.setMinimumSize(64, 64)
        frame_layout.addWidget(self._image_label)

        self._caption = QtWidgets.QLabel()
        self._caption.setAlignment(QtCore.Qt.AlignCenter)
        self._caption.setStyleSheet(
            f"color: {COLORS['text_secondary']}; "
            f"font-size: 10px; padding: 2px;"
        )
        frame_layout.addWidget(self._caption)

        layout.addWidget(frame)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def show_for(self, *,
                 uid: str,
                 name: str,
                 frames: "list[str]",
                 fallback_pixmap: "QtGui.QPixmap | None" = None,
                 fps: int = 24,
                 loop_mode: str = "loop",
                 target_size: int = 384,
                 frames_per_cycle: int = 0,
                 num_hdris: int = 1,
                 anchor_global_pos: "QtCore.QPoint | None" = None) -> None:
        """Start playing the given frame list near anchor_global_pos.

        Args:
            uid:                Asset UID (used to avoid restarting if the
                                same card is hovered again).
            name:               Display label shown under the image.
            frames:             Ordered list of PNG paths. If empty,
                                fallback_pixmap is shown statically.
            fallback_pixmap:    Static image shown when no frames exist.
            fps:                Playback rate.
            loop_mode:          'loop', 'pingpong', or 'once'.
            target_size:        Target pixel dimension (square) for the
                                preview. Frames are scaled to fit.
            frames_per_cycle:   Number of frames in one HDRI cycle (used
                                by the caption to show "HDRI 2/4").
            num_hdris:          Number of HDRI cycles in the sequence.
            anchor_global_pos:  Where to place the popup. Defaults to
                                the current cursor position.
        """
        # If we're already showing this asset, just keep going.
        if uid == self._current_uid and self.isVisible():
            return

        self._current_uid = uid
        self._loop_mode = loop_mode
        self._direction = 1
        self._frame_index = 0
        self._pixmap_cache.clear()
        self._target_pixel_size = QtCore.QSize(target_size, target_size)
        self._frames_per_cycle = max(0, int(frames_per_cycle))
        self._num_hdris = max(1, int(num_hdris))
        self._asset_name = name

        # Decide content: animated sequence or static fallback.
        self._frames = list(frames) if frames else []

        if self._frames:
            interval_ms = max(int(1000.0 / max(fps, 1)), 16)
            self._timer.start(interval_ms)
            self._show_frame(0)
        else:
            self._timer.stop()
            if fallback_pixmap is not None and not fallback_pixmap.isNull():
                scaled = fallback_pixmap.scaled(
                    self._target_pixel_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )
                self._image_label.setPixmap(scaled)
            else:
                self._image_label.setText("(no preview)")
            self._caption.setText(name)

        # Position the popup near the anchor.
        if anchor_global_pos is None:
            anchor_global_pos = QtGui.QCursor.pos()
        self._reposition(anchor_global_pos)

        self.show()
        self.raise_()

    def hide_preview(self) -> None:
        """Stop playback and hide the popup."""
        self._timer.stop()
        self._current_uid = ""
        self._pinned = False
        self.hide()

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = bool(pinned)

    @property
    def is_pinned(self) -> bool:
        return self._pinned

    @property
    def current_uid(self) -> str:
        return self._current_uid

    # ──────────────────────────────────────────────
    # Internals
    # ──────────────────────────────────────────────

    def _reposition(self, anchor_global_pos: QtCore.QPoint) -> None:
        """Place the popup adjacent to the cursor, clamped to the screen.

        Prefers right-of-cursor; flips to left if it would go off-screen.
        """
        self.adjustSize()
        size = self.size()
        if size.width() < 100:
            size = QtCore.QSize(
                self._target_pixel_size.width() + 20,
                self._target_pixel_size.height() + 50,
            )

        screen = QtGui.QGuiApplication.screenAt(anchor_global_pos)
        if screen is None:
            screen = QtGui.QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()

        offset = 16
        x = anchor_global_pos.x() + offset
        y = anchor_global_pos.y() + offset

        if x + size.width() > avail.right():
            x = anchor_global_pos.x() - size.width() - offset
        if y + size.height() > avail.bottom():
            y = avail.bottom() - size.height() - 8
        if x < avail.left():
            x = avail.left() + 8
        if y < avail.top():
            y = avail.top() + 8

        self.move(x, y)

    def _show_frame(self, idx: int) -> None:
        if not self._frames:
            return
        idx = max(0, min(idx, len(self._frames) - 1))
        pm = self._pixmap_cache.get(idx)
        if pm is None:
            raw = QtGui.QPixmap(self._frames[idx])
            if raw.isNull():
                return
            pm = raw.scaled(
                self._target_pixel_size,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            # Cap cache to avoid unbounded memory on long sequences.
            if len(self._pixmap_cache) < 512:
                self._pixmap_cache[idx] = pm
        self._image_label.setPixmap(pm)
        self._frame_index = idx
        self._update_caption()

    def _update_caption(self) -> None:
        self._caption.setText(self._asset_name)

    def _advance(self) -> None:
        if not self._frames:
            self._timer.stop()
            return
        n = len(self._frames)
        new_idx = self._frame_index + self._direction

        if self._loop_mode == self.LOOP:
            new_idx %= n
        elif self._loop_mode == self.PING_PONG:
            if new_idx >= n:
                self._direction = -1
                new_idx = n - 2
            elif new_idx < 0:
                self._direction = 1
                new_idx = 1
            new_idx = max(0, min(new_idx, n - 1))
        else:  # ONCE
            if new_idx >= n:
                self._timer.stop()
                return
            new_idx = max(0, min(new_idx, n - 1))

        self._show_frame(new_idx)

    # Hide on user input outside the popup itself (unless pinned).
    def leaveEvent(self, event):
        if not self._pinned:
            # Defer briefly so the cursor can move from card -> popup
            # without instantly hiding; the gallery's own leave handler
            # also drives this, so this is just a safety net.
            pass
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            # Click on the popup toggles pin state.
            self._pinned = not self._pinned
        super().mousePressEvent(event)


class TurntableSequenceDialog(QtWidgets.QDialog):
    """Full turntable sequence viewer with playback controls and render metadata."""

    def __init__(self, asset_name: str, frames: "list[str]",
                 fps: int = 24, loop_mode: str = "loop",
                 frames_per_cycle: int = 0, num_hdris: int = 1,
                 thumb_path: str = "", usd_path: str = "", status: str = "",
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Turntable — {asset_name}")
        self.resize(1000, 960)
        self.setSizeGripEnabled(True)

        self._frames: "list[str]" = list(frames) if frames else []
        self._pixmap_cache: "dict[int, QtGui.QPixmap]" = {}
        self._frame_index = 0
        self._direction = 1
        self._loop_mode = loop_mode
        self._asset_name = asset_name
        self._frames_per_cycle = max(0, int(frames_per_cycle))
        self._num_hdris = max(1, int(num_hdris))
        self._playing = True

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._advance)

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(8)

        # ── Top widget: image + playback controls ──
        top_widget = QtWidgets.QWidget()
        top_layout = QtWidgets.QVBoxLayout(top_widget)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        self._image_label = QtWidgets.QLabel()
        self._image_label.setAlignment(QtCore.Qt.AlignCenter)
        self._image_label.setStyleSheet(
            f"background-color: {COLORS['bg_darkest']};"
        )
        self._image_label.setMinimumSize(400, 300)
        top_layout.addWidget(self._image_label, 1)

        # Playback status line
        self._info_label = QtWidgets.QLabel("")
        self._info_label.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 11px;")
        top_layout.addWidget(self._info_label)

        # Controls row
        control_row = QtWidgets.QHBoxLayout()
        control_row.setSpacing(8)

        self._play_btn = QtWidgets.QPushButton("Pause")
        self._play_btn.setFixedWidth(80)
        self._play_btn.clicked.connect(self._toggle_play)
        control_row.addWidget(self._play_btn)

        control_row.addWidget(QtWidgets.QLabel("Speed:"))
        self._speed_combo = QtWidgets.QComboBox()
        self._speed_combo.addItems(["0.25x", "0.5x", "1x", "2x"])
        self._speed_combo.setCurrentIndex(2)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setFixedWidth(80)
        control_row.addWidget(self._speed_combo)

        control_row.addStretch()
        loop_lbl = QtWidgets.QLabel(f"Loop: {loop_mode}")
        loop_lbl.setStyleSheet(f"color: {COLORS['text_secondary']}; font-size: 10px;")
        control_row.addWidget(loop_lbl)
        top_layout.addLayout(control_row)

        # ── Bottom widget: render metadata ──
        meta_text = QtWidgets.QPlainTextEdit()
        meta_text.setReadOnly(True)
        meta_text.setMinimumHeight(60)
        meta_text.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        meta_text.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {COLORS['bg_darkest']}; "
            f"color: {COLORS['text_secondary']}; "
            f"font-family: Consolas, monospace; font-size: 11px; }}"
        )
        info = self._load_meta(thumb_path, asset_name, usd_path, status)
        meta_text.setPlainText(self._format_meta(info))

        # ── Splitter: playback top, metadata bottom ──
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(top_widget)
        splitter.addWidget(meta_text)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setSizes([680, 200])
        splitter.splitterMoved.connect(lambda *_: self._invalidate_cache())
        main_layout.addWidget(splitter, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        main_layout.addLayout(btn_row)

        self._timer_interval = max(int(1000.0 / max(fps, 1)), 16)
        if self._frames:
            self._timer.start(self._timer_interval)
            self._show_frame(0)
        else:
            self._image_label.setText("(no turntable frames)")
            self._info_label.setText(asset_name)

    # ── Metadata helpers ──

    @staticmethod
    def _load_meta(thumb_path: str, asset_name: str,
                   usd_path: str = "", status: str = "") -> dict:
        info: dict = {}
        if thumb_path:
            sidecar = os.path.splitext(thumb_path)[0] + ".json"
            if os.path.exists(sidecar):
                try:
                    import json as _json
                    with open(sidecar, "r", encoding="utf-8") as f:
                        info = _json.load(f)
                except Exception as e:
                    info["sidecar_error"] = str(e)
        info.setdefault("asset_name", asset_name)
        if usd_path:
            info.setdefault("usd_output_path", usd_path)
        if status:
            info.setdefault("status", status)
        return info

    @staticmethod
    def _format_meta(info: dict) -> str:
        order = [
            "asset_name", "rendered_at", "render_time_seconds",
            "renderer_key", "engine", "samples",
            "resolution", "fov", "focal",
            "aperture_h", "aperture_v",
            "clip_near", "clip_far",
            "yaw", "pitch", "distance",
            "hdri",
            "source_dir", "source_geo_path",
            "thumbnail", "usd_path", "usd_output_path", "status",
        ]
        lines = []
        seen: set = set()
        for k in order:
            if k in info:
                v = info[k]
                if k == "render_time_seconds":
                    try:
                        v = f"{float(v):.2f}s"
                    except (TypeError, ValueError):
                        pass
                lines.append(f"{k:22s} : {v}")
                seen.add(k)
        textures = info.get("textures")
        if isinstance(textures, dict) and textures:
            lines.append("")
            lines.append("textures:")
            for map_type, path in textures.items():
                lines.append(f"  {map_type:14s} : {path}")
            seen.add("textures")
        for k, v in info.items():
            if k not in seen:
                lines.append(f"{k:22s} : {v}")
        return "\n".join(lines)

    # ── Playback ──

    def _toggle_play(self):
        self._playing = not self._playing
        self._play_btn.setText("Resume" if not self._playing else "Pause")
        if self._playing and self._frames:
            self._timer.start(self._timer_interval)
        else:
            self._timer.stop()

    def _on_speed_changed(self, idx):
        speeds = [0.25, 0.5, 1.0, 2.0]
        if 0 <= idx < len(speeds):
            self._timer.setInterval(max(int(self._timer_interval / speeds[idx]), 16))

    def _invalidate_cache(self):
        self._pixmap_cache.clear()
        if self._frames:
            self._show_frame(self._frame_index)

    def _show_frame(self, idx: int):
        if not self._frames:
            return
        idx = max(0, min(idx, len(self._frames) - 1))
        pm = self._pixmap_cache.get(idx)
        if pm is None:
            raw = QtGui.QPixmap(self._frames[idx])
            if raw.isNull():
                return
            pm = raw.scaled(
                self._image_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            if len(self._pixmap_cache) < 512:
                self._pixmap_cache[idx] = pm
        self._image_label.setPixmap(pm)
        self._frame_index = idx
        self._update_info()

    def _update_info(self):
        if not self._frames:
            self._info_label.setText(self._asset_name)
            return
        state = "paused" if not self._playing else "playing"
        if self._frames_per_cycle > 0:
            cycle = min((self._frame_index // self._frames_per_cycle) + 1, self._num_hdris)
            local = (self._frame_index % self._frames_per_cycle) + 1
            self._info_label.setText(
                f"{self._asset_name}  •  {state}  •  "
                f"frame {self._frame_index + 1}/{len(self._frames)}  •  "
                f"HDRI {cycle}/{self._num_hdris}"
            )
        else:
            self._info_label.setText(
                f"{self._asset_name}  •  {state}  •  "
                f"frame {self._frame_index + 1}/{len(self._frames)}"
            )

    def _advance(self):
        if not self._frames or not self._playing:
            return
        n = len(self._frames)
        new_idx = self._frame_index + self._direction

        if self._loop_mode == HoverPreviewPopup.LOOP:
            new_idx %= n
        elif self._loop_mode == HoverPreviewPopup.PING_PONG:
            if new_idx >= n:
                self._direction = -1
                new_idx = n - 2
            elif new_idx < 0:
                self._direction = 1
                new_idx = 1
            new_idx = max(0, min(new_idx, n - 1))
        else:  # ONCE
            if new_idx >= n:
                self._timer.stop()
                self._playing = False
                self._play_btn.setText("Resume")
                return
            new_idx = max(0, min(new_idx, n - 1))

        self._show_frame(new_idx)
