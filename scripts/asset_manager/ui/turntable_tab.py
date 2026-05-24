"""
Turn Table Tab for the Asset Manager UI.

Configures turntable rendering (camera orbit) and the hover preview that
plays the rendered animation when a user mouses over a gallery card.

Multi-HDRI support: the user provides N HDRIs and a frames-per-cycle
value; the turntable does N full orbits, each cycle lit by one HDRI.
Total frame count = N × frames_per_cycle.
"""

import json
import os

from asset_manager.qt_compat import QtWidgets, QtCore, QtGui

from ..database.asset_db import AssetDatabase
from .styles import COLORS


# ── Output format options (label, internal key) ──
_FORMAT_OPTIONS = [
    ("PNG Sequence (recommended)", "png_sequence"),
    ("Animated WebP",              "webp"),
    ("Animated PNG (APNG)",        "apng"),
    ("MP4 (H.264)",                "mp4"),
]

_LOOP_OPTIONS = [
    ("Loop",       "loop"),
    ("Ping-Pong",  "pingpong"),
    ("Play Once",  "once"),
]

_DIRECTION_OPTIONS = [
    ("Clockwise",         "cw"),
    ("Counter-Clockwise", "ccw"),
]

_AXIS_OPTIONS = [
    ("Y (up)",   "Y"),
    ("X",        "X"),
    ("Z",        "Z"),
]

_HOVER_SCALE_OPTIONS = [
    ("1.5×", 1.5),
    ("2.0×", 2.0),
    ("2.5×", 2.5),
    ("3.0×", 3.0),
]

# Dome light texture-projection modes. Maps the UI label to the USD
# inputs:texture:format token. Some renderers (Karma latlong; Arnold,
# Redshift can default to mirror_ball / angular for legacy chrome-ball
# captures), so this needs to be configurable per project.
_HDRI_PROJECTION_OPTIONS = [
    ("Lat-Long / Equirectangular (default)", "latlong"),
    ("Mirror Ball / Spherical",              "mirrored_ball"),
    ("Angular Map / Fish-eye",               "angular_map"),
    ("Cube Map",                             "cube_map"),
]


class TurntableTab(QtWidgets.QWidget):
    """
    UI for configuring the turntable render + hover preview behavior.
    All values are persisted to the DB meta table (same store as Settings).
    """

    settings_changed = QtCore.Signal()

    def __init__(self, db: AssetDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._build_ui()
        self._load_settings()

    # ──────────────────────────────────────────────
    # UI construction
    # ──────────────────────────────────────────────

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        header = QtWidgets.QLabel("Turn Table")
        header.setObjectName("sectionTitle")
        header.setStyleSheet(
            f"font-size: 16px; font-weight: 700; "
            f"color: {COLORS['text_primary']};"
        )
        main_layout.addWidget(header)

        subtitle = QtWidgets.QLabel(
            "Configure camera-orbit animation rendered alongside each asset. "
            "Each HDRI in the list produces one full 360° rotation (one cycle)."
        )
        subtitle.setObjectName("dimLabel")
        subtitle.setWordWrap(True)
        main_layout.addWidget(subtitle)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(16)

        scroll_layout.addWidget(self._build_capture_group())
        scroll_layout.addWidget(self._build_hdri_group())
        scroll_layout.addWidget(self._build_calibration_group())
        scroll_layout.addWidget(self._build_render_group())
        scroll_layout.addWidget(self._build_output_group())
        scroll_layout.addWidget(self._build_hover_group())

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _build_capture_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Capture")
        form = QtWidgets.QFormLayout(group)

        # Frames per cycle (one full 360° rotation)
        self._frames_spin = QtWidgets.QSpinBox()
        self._frames_spin.setRange(6, 720)
        self._frames_spin.setValue(72)
        self._frames_spin.setSingleStep(6)
        self._frames_spin.setToolTip(
            "Number of frames in one full 360° rotation.\n"
            "Higher = smoother but slower to render and larger files.\n"
            "Common values: 24 (1s @ 24fps), 36, 72 (3s @ 24fps)."
        )
        self._frames_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_frames_per_cycle", v))
        self._frames_spin.valueChanged.connect(self._update_total_label)
        form.addRow("Frames per Cycle:", self._frames_spin)

        # Rotation axis
        self._axis_combo = QtWidgets.QComboBox()
        for label, _key in _AXIS_OPTIONS:
            self._axis_combo.addItem(label)
        self._axis_combo.setToolTip("Axis the camera orbits around.")
        self._axis_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_rotation_axis", _AXIS_OPTIONS[i][1]))
        form.addRow("Rotation Axis:", self._axis_combo)

        # Direction
        self._direction_combo = QtWidgets.QComboBox()
        for label, _key in _DIRECTION_OPTIONS:
            self._direction_combo.addItem(label)
        self._direction_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_direction", _DIRECTION_OPTIONS[i][1]))
        form.addRow("Direction:", self._direction_combo)

        # Camera pitch during orbit
        self._pitch_spin = QtWidgets.QDoubleSpinBox()
        self._pitch_spin.setRange(-89.0, 89.0)
        self._pitch_spin.setDecimals(1)
        self._pitch_spin.setSingleStep(5.0)
        self._pitch_spin.setSuffix("°")
        self._pitch_spin.setValue(15.0)
        self._pitch_spin.setToolTip(
            "Camera elevation during orbit (held constant).\n"
            "Positive = look slightly down at the asset."
        )
        self._pitch_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_camera_pitch", v))
        form.addRow("Camera Pitch:", self._pitch_spin)

        # Distance offset (added to auto-framed distance)
        self._dist_spin = QtWidgets.QDoubleSpinBox()
        self._dist_spin.setRange(-1000.0, 1000.0)
        self._dist_spin.setDecimals(3)
        self._dist_spin.setSingleStep(0.5)
        self._dist_spin.setValue(0.0)
        self._dist_spin.setToolTip(
            "Offset added to the auto-framed camera distance.\n"
            "Positive = zoom out, negative = zoom in."
        )
        self._dist_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_distance_offset", v))
        form.addRow("Zoom Offset:", self._dist_spin)

        # Starting yaw (so the first frame can match the thumbnail)
        self._start_yaw_spin = QtWidgets.QDoubleSpinBox()
        self._start_yaw_spin.setRange(-360.0, 360.0)
        self._start_yaw_spin.setDecimals(1)
        self._start_yaw_spin.setSingleStep(5.0)
        self._start_yaw_spin.setSuffix("°")
        self._start_yaw_spin.setValue(35.0)
        self._start_yaw_spin.setToolTip(
            "Starting yaw angle for the first frame of each cycle.\n"
            "Match the thumbnail yaw for a seamless static-to-animated handoff."
        )
        self._start_yaw_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_start_yaw", v))
        form.addRow("Starting Yaw:", self._start_yaw_spin)

        return group

    def _build_hdri_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("HDRI Cycles")
        vbox = QtWidgets.QVBoxLayout(group)

        info = QtWidgets.QLabel(
            "Each HDRI below produces one full 360° rotation lit by that "
            "environment. Reorder with the arrow buttons. Leave empty to "
            "fall back to the thumbnail HDRI from Settings."
        )
        info.setWordWrap(True)
        info.setObjectName("dimLabel")
        vbox.addWidget(info)

        # ── HDRI projection format ──
        proj_row = QtWidgets.QHBoxLayout()
        proj_row.addWidget(QtWidgets.QLabel("Projection:"))
        self._projection_combo = QtWidgets.QComboBox()
        for label, _key in _HDRI_PROJECTION_OPTIONS:
            self._projection_combo.addItem(label)
        self._projection_combo.setToolTip(
            "How the HDRI is wrapped onto the dome light.\n"
            "  • Lat-Long: standard equirectangular, the default for "
            "most HDRIs.\n"
            "  • Mirror Ball: spherical / mirrored-ball capture.\n"
            "  • Angular Map: fish-eye / Paul Debevec-style capture.\n"
            "  • Cube Map: pre-baked cube faces.\n\n"
            "Render engines vary in which projection they accept as "
            "default — match this to your HDRI source."
        )
        self._projection_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_hdri_projection",
                _HDRI_PROJECTION_OPTIONS[i][1]))
        proj_row.addWidget(self._projection_combo, 1)
        vbox.addLayout(proj_row)

        list_row = QtWidgets.QHBoxLayout()

        self._hdri_list = QtWidgets.QListWidget()
        self._hdri_list.setSelectionMode(
            QtWidgets.QAbstractItemView.ExtendedSelection)
        self._hdri_list.setMinimumHeight(120)
        self._hdri_list.setAlternatingRowColors(True)
        self._hdri_list.setToolTip(
            "HDRI environments, one per cycle.\n"
            "Total frames = (number of HDRIs) × (frames per cycle)."
        )
        list_row.addWidget(self._hdri_list, 1)

        btn_col = QtWidgets.QVBoxLayout()
        btn_col.setSpacing(4)

        add_btn = QtWidgets.QPushButton("Add…")
        add_btn.clicked.connect(self._on_add_hdri)
        btn_col.addWidget(add_btn)

        remove_btn = QtWidgets.QPushButton("Remove")
        remove_btn.clicked.connect(self._on_remove_hdri)
        btn_col.addWidget(remove_btn)

        up_btn = QtWidgets.QPushButton("Move Up")
        up_btn.clicked.connect(lambda: self._move_hdri(-1))
        btn_col.addWidget(up_btn)

        down_btn = QtWidgets.QPushButton("Move Down")
        down_btn.clicked.connect(lambda: self._move_hdri(1))
        btn_col.addWidget(down_btn)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self._on_clear_hdris)
        btn_col.addWidget(clear_btn)

        btn_col.addStretch()
        list_row.addLayout(btn_col)

        vbox.addLayout(list_row)

        self._total_label = QtWidgets.QLabel("")
        self._total_label.setObjectName("dimLabel")
        vbox.addWidget(self._total_label)

        return group

    def _build_calibration_group(self) -> QtWidgets.QGroupBox:
        """Calibration row: chrome ball + grey ball + Macbeth chart shown
        in the lower-left of every turntable frame, parented to the
        camera so they orbit with it. Lets the user verify HDRI
        reflection (chrome), diffuse response (grey), and color
        reproduction (Macbeth) across the rotation."""
        group = QtWidgets.QGroupBox("Calibration Row (Chrome • Grey • Macbeth)")
        vbox = QtWidgets.QVBoxLayout(group)

        info = QtWidgets.QLabel(
            "Adds reference spheres + ColorChecker chart in the lower-left "
            "corner of every frame. The chrome ball mirrors the HDRI so "
            "you can see lighting rotate; the grey ball shows the "
            "diffuse response; the Macbeth chart verifies color accuracy."
        )
        info.setWordWrap(True)
        info.setObjectName("dimLabel")
        vbox.addWidget(info)

        self._cal_enabled_chk = QtWidgets.QCheckBox(
            "Enable calibration row")
        self._cal_enabled_chk.toggled.connect(
            lambda v: self._save_meta("tt_cal_enabled", int(v)))
        self._cal_enabled_chk.toggled.connect(self._on_cal_enabled_toggled)
        vbox.addWidget(self._cal_enabled_chk)

        # Per-element toggles — let the user disable any of the three.
        elements_row = QtWidgets.QHBoxLayout()
        self._cal_chrome_chk = QtWidgets.QCheckBox("Chrome Ball")
        self._cal_chrome_chk.setChecked(True)
        self._cal_chrome_chk.toggled.connect(
            lambda v: self._save_meta("tt_cal_show_chrome", int(v)))
        elements_row.addWidget(self._cal_chrome_chk)

        self._cal_grey_chk = QtWidgets.QCheckBox("Grey Ball")
        self._cal_grey_chk.setChecked(True)
        self._cal_grey_chk.toggled.connect(
            lambda v: self._save_meta("tt_cal_show_grey", int(v)))
        elements_row.addWidget(self._cal_grey_chk)

        self._cal_macbeth_chk = QtWidgets.QCheckBox("Macbeth Chart")
        self._cal_macbeth_chk.setChecked(True)
        self._cal_macbeth_chk.toggled.connect(
            lambda v: self._save_meta("tt_cal_show_macbeth", int(v)))
        elements_row.addWidget(self._cal_macbeth_chk)

        elements_row.addStretch()
        vbox.addLayout(elements_row)

        # Position + scale (camera-local units).
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)

        self._cal_distance_spin = QtWidgets.QDoubleSpinBox()
        self._cal_distance_spin.setRange(0.1, 100.0)
        self._cal_distance_spin.setDecimals(2)
        self._cal_distance_spin.setSingleStep(0.1)
        self._cal_distance_spin.setValue(1.5)
        self._cal_distance_spin.setToolTip(
            "How far in front of the camera the row sits, in camera-local "
            "units. Smaller = closer / larger on screen."
        )
        self._cal_distance_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_cal_distance", v))
        form.addRow("Distance from Camera:", self._cal_distance_spin)

        self._cal_offset_x_spin = QtWidgets.QDoubleSpinBox()
        self._cal_offset_x_spin.setRange(-5.0, 5.0)
        self._cal_offset_x_spin.setDecimals(3)
        self._cal_offset_x_spin.setSingleStep(0.05)
        self._cal_offset_x_spin.setValue(-0.45)
        self._cal_offset_x_spin.setToolTip(
            "Horizontal offset in camera space. Negative = left."
        )
        self._cal_offset_x_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_cal_offset_x", v))
        form.addRow("X Offset (left/right):", self._cal_offset_x_spin)

        self._cal_offset_y_spin = QtWidgets.QDoubleSpinBox()
        self._cal_offset_y_spin.setRange(-5.0, 5.0)
        self._cal_offset_y_spin.setDecimals(3)
        self._cal_offset_y_spin.setSingleStep(0.05)
        self._cal_offset_y_spin.setValue(-0.22)
        self._cal_offset_y_spin.setToolTip(
            "Vertical offset in camera space. Negative = below center."
        )
        self._cal_offset_y_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_cal_offset_y", v))
        form.addRow("Y Offset (up/down):", self._cal_offset_y_spin)

        self._cal_scale_spin = QtWidgets.QDoubleSpinBox()
        self._cal_scale_spin.setRange(0.05, 10.0)
        self._cal_scale_spin.setDecimals(3)
        self._cal_scale_spin.setSingleStep(0.05)
        self._cal_scale_spin.setValue(0.75)
        self._cal_scale_spin.setToolTip(
            "Uniform scale on the whole row (spheres + chart)."
        )
        self._cal_scale_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_cal_scale", v))
        form.addRow("Row Scale:", self._cal_scale_spin)

        wrapper = QtWidgets.QWidget()
        wrapper.setLayout(form)
        self._cal_params_widget = wrapper  # for enable/disable on toggle
        vbox.addWidget(wrapper)

        return group

    def _on_cal_enabled_toggled(self, checked: bool):
        """Grey out the sub-controls when the master switch is off."""
        for w in (self._cal_chrome_chk, self._cal_grey_chk,
                  self._cal_macbeth_chk, self._cal_params_widget):
            w.setEnabled(checked)

    def _build_render_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Render")
        form = QtWidgets.QFormLayout(group)

        # Renderer
        self._renderer_combo = QtWidgets.QComboBox()
        self._renderer_options = [
            ("(inherit from Settings)", ""),
            ("Karma CPU",               "karma_cpu"),
            ("Karma XPU",               "karma_xpu"),
            ("Arnold",                  "arnold"),
            ("Redshift",                "redshift"),
        ]
        for label, _key in self._renderer_options:
            self._renderer_combo.addItem(label)
        self._renderer_combo.setToolTip(
            "Renderer for turntable frames. Leave on 'inherit' to use the "
            "same engine selected in Settings."
        )
        self._renderer_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_renderer", self._renderer_options[i][1]))
        form.addRow("Renderer:", self._renderer_combo)

        # Samples
        self._samples_spin = QtWidgets.QSpinBox()
        self._samples_spin.setRange(1, 4096)
        self._samples_spin.setValue(8)
        self._samples_spin.setToolTip(
            "Pixel samples per frame.\n"
            "Keep low (4–16) — many frames, less per-frame quality is fine."
        )
        self._samples_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_samples", v))
        form.addRow("Samples per Frame:", self._samples_spin)

        # Resolution
        res_row = QtWidgets.QHBoxLayout()
        self._width_spin = QtWidgets.QSpinBox()
        self._width_spin.setRange(64, 4096)
        self._width_spin.setValue(384)
        self._width_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_width", v))
        res_row.addWidget(self._width_spin)
        res_row.addWidget(QtWidgets.QLabel("×"))
        self._height_spin = QtWidgets.QSpinBox()
        self._height_spin.setRange(64, 4096)
        self._height_spin.setValue(384)
        self._height_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_height", v))
        res_row.addWidget(self._height_spin)
        res_row.addStretch()
        res_w = QtWidgets.QWidget()
        res_w.setLayout(res_row)
        form.addRow("Frame Resolution:", res_w)

        # Focal length + aperture inheritance toggle
        self._inherit_lens_chk = QtWidgets.QCheckBox(
            "Inherit lens (focal length + aperture) from Settings")
        self._inherit_lens_chk.setChecked(True)
        self._inherit_lens_chk.toggled.connect(self._on_inherit_lens_toggled)
        form.addRow(self._inherit_lens_chk)

        # Lens overrides (visible only when not inheriting)
        lens_row = QtWidgets.QHBoxLayout()
        self._focal_spin = QtWidgets.QDoubleSpinBox()
        self._focal_spin.setRange(1.0, 5000.0)
        self._focal_spin.setSuffix(" mm")
        self._focal_spin.setValue(50.0)
        self._focal_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_focal", v))
        lens_row.addWidget(QtWidgets.QLabel("Focal:"))
        lens_row.addWidget(self._focal_spin)
        self._aperture_spin = QtWidgets.QDoubleSpinBox()
        self._aperture_spin.setRange(0.1, 500.0)
        self._aperture_spin.setSuffix(" mm")
        self._aperture_spin.setValue(36.0)
        self._aperture_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_aperture", v))
        lens_row.addWidget(QtWidgets.QLabel("H. Aperture:"))
        lens_row.addWidget(self._aperture_spin)
        lens_row.addStretch()
        self._lens_widget = QtWidgets.QWidget()
        self._lens_widget.setLayout(lens_row)
        form.addRow(self._lens_widget)

        return group

    def _build_output_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Output")
        form = QtWidgets.QFormLayout(group)

        # Format
        self._format_combo = QtWidgets.QComboBox()
        for label, _key in _FORMAT_OPTIONS:
            self._format_combo.addItem(label)
        self._format_combo.setToolTip(
            "PNG Sequence is always written. The encoded outputs below are\n"
            "produced in addition, for sharing/export."
        )
        self._format_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_format", _FORMAT_OPTIONS[i][1]))
        form.addRow("Encoded Format:", self._format_combo)

        # FPS
        self._fps_spin = QtWidgets.QSpinBox()
        self._fps_spin.setRange(1, 120)
        self._fps_spin.setValue(24)
        self._fps_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_fps", v))
        form.addRow("Playback FPS:", self._fps_spin)

        # Loop mode
        self._loop_combo = QtWidgets.QComboBox()
        for label, _key in _LOOP_OPTIONS:
            self._loop_combo.addItem(label)
        self._loop_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_loop_mode", _LOOP_OPTIONS[i][1]))
        form.addRow("Loop Mode:", self._loop_combo)

        # Turntable rendering is on-demand only:
        # right-click an asset in the Gallery → "Process Turntable".
        info = QtWidgets.QLabel(
            "Turntables are rendered on demand from the Gallery: "
            "select one or more assets, right-click → "
            "<b>Process Turntable</b>."
        )
        info.setWordWrap(True)
        info.setObjectName("dimLabel")
        form.addRow(info)

        return group

    def _build_hover_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Gallery Hover Preview")
        form = QtWidgets.QFormLayout(group)

        self._hover_enabled_chk = QtWidgets.QCheckBox(
            "Show animated preview when hovering over gallery cards")
        self._hover_enabled_chk.setChecked(True)
        self._hover_enabled_chk.toggled.connect(
            lambda v: self._save_meta("tt_hover_enabled", int(v)))
        form.addRow(self._hover_enabled_chk)

        # Delay
        self._hover_delay_spin = QtWidgets.QSpinBox()
        self._hover_delay_spin.setRange(0, 5000)
        self._hover_delay_spin.setSingleStep(50)
        self._hover_delay_spin.setSuffix(" ms")
        self._hover_delay_spin.setValue(350)
        self._hover_delay_spin.setToolTip(
            "Delay before the preview opens.\n"
            "Higher values avoid flashing during quick mouse sweeps."
        )
        self._hover_delay_spin.valueChanged.connect(
            lambda v: self._save_meta("tt_hover_delay_ms", v))
        form.addRow("Hover Delay:", self._hover_delay_spin)

        # Scale
        self._hover_scale_combo = QtWidgets.QComboBox()
        for label, _val in _HOVER_SCALE_OPTIONS:
            self._hover_scale_combo.addItem(label)
        self._hover_scale_combo.setCurrentIndex(1)  # default 2.0x
        self._hover_scale_combo.currentIndexChanged.connect(
            lambda i: self._save_meta(
                "tt_hover_scale", _HOVER_SCALE_OPTIONS[i][1]))
        form.addRow("Popup Size:", self._hover_scale_combo)

        # Pin on click
        self._pin_chk = QtWidgets.QCheckBox(
            "Keep preview open after clicking the card")
        self._pin_chk.setToolTip(
            "When enabled, clicking a card while the hover preview is open\n"
            "'pins' the preview so it stays visible until you click "
            "elsewhere."
        )
        self._pin_chk.toggled.connect(
            lambda v: self._save_meta("tt_hover_pin_on_click", int(v)))
        form.addRow(self._pin_chk)

        return group

    # ──────────────────────────────────────────────
    # HDRI list actions
    # ──────────────────────────────────────────────

    def _on_add_hdri(self):
        files, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add HDRI(s)", "",
            "HDRI Images (*.hdr *.exr *.tx)"
        )
        if not files:
            return
        for f in files:
            self._hdri_list.addItem(f.replace("\\", "/"))
        self._save_hdri_list()
        self._update_total_label()

    def _on_remove_hdri(self):
        for item in self._hdri_list.selectedItems():
            self._hdri_list.takeItem(self._hdri_list.row(item))
        self._save_hdri_list()
        self._update_total_label()

    def _on_clear_hdris(self):
        if self._hdri_list.count() == 0:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Clear HDRIs",
            "Remove all HDRIs from the turntable?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._hdri_list.clear()
            self._save_hdri_list()
            self._update_total_label()

    def _move_hdri(self, offset: int):
        row = self._hdri_list.currentRow()
        if row < 0:
            return
        new_row = row + offset
        if new_row < 0 or new_row >= self._hdri_list.count():
            return
        item = self._hdri_list.takeItem(row)
        self._hdri_list.insertItem(new_row, item)
        self._hdri_list.setCurrentRow(new_row)
        self._save_hdri_list()

    def _save_hdri_list(self):
        paths = [self._hdri_list.item(i).text()
                 for i in range(self._hdri_list.count())]
        self._save_meta("tt_hdri_list", json.dumps(paths))

    def _update_total_label(self):
        n = self._hdri_list.count()
        frames = self._frames_spin.value()
        if n == 0:
            self._total_label.setText(
                f"No HDRIs → 1 cycle × {frames} frames = "
                f"<b>{frames}</b> frames (Settings HDRI fallback)"
            )
        else:
            total = n * frames
            secs = total / max(self._fps_spin.value(), 1)
            self._total_label.setText(
                f"{n} HDRIs × {frames} frames = "
                f"<b>{total}</b> frames "
                f"(~{secs:.1f}s @ {self._fps_spin.value()} fps)"
            )

    def _on_inherit_lens_toggled(self, checked: bool):
        self._lens_widget.setEnabled(not checked)
        self._save_meta("tt_inherit_lens", int(checked))

    # ──────────────────────────────────────────────
    # Persistence
    # ──────────────────────────────────────────────

    def _save_meta(self, key: str, value):
        try:
            self._db.set_meta(key, str(value))
        except Exception as e:
            print(f"[TurntableTab] Failed to save {key}: {e}")

    def _load_settings(self):
        """Load every persisted value from the DB meta table."""
        # Scalar spinboxes + combos
        scalars = [
            (self._frames_spin,        "tt_frames_per_cycle", int,   72),
            (self._pitch_spin,         "tt_camera_pitch",     float, 15.0),
            (self._dist_spin,          "tt_distance_offset",  float, 0.0),
            (self._start_yaw_spin,     "tt_start_yaw",        float, 35.0),
            (self._samples_spin,       "tt_samples",          int,   8),
            (self._width_spin,         "tt_width",            int,   384),
            (self._height_spin,        "tt_height",           int,   384),
            (self._focal_spin,         "tt_focal",            float, 50.0),
            (self._aperture_spin,      "tt_aperture",         float, 36.0),
            (self._fps_spin,           "tt_fps",              int,   24),
            (self._hover_delay_spin,   "tt_hover_delay_ms",   int,   350),
            (self._cal_distance_spin,  "tt_cal_distance",     float, 1.5),
            (self._cal_offset_x_spin,  "tt_cal_offset_x",     float, -0.45),
            (self._cal_offset_y_spin,  "tt_cal_offset_y",     float, -0.22),
            (self._cal_scale_spin,     "tt_cal_scale",        float, 0.75),
        ]
        for w, key, cast, default in scalars:
            raw = self._db.get_meta(key, "")
            try:
                val = cast(raw) if raw not in ("", None) else default
            except (TypeError, ValueError):
                val = default
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)

        # Combos: restore by key
        def _restore_combo(combo, options, meta_key, default_key):
            stored = self._db.get_meta(meta_key, default_key) or default_key
            for i, (_label, key) in enumerate(options):
                if key == stored:
                    combo.blockSignals(True)
                    combo.setCurrentIndex(i)
                    combo.blockSignals(False)
                    return
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

        _restore_combo(self._axis_combo,        _AXIS_OPTIONS,
                       "tt_rotation_axis", "Y")
        _restore_combo(self._direction_combo,   _DIRECTION_OPTIONS,
                       "tt_direction", "cw")
        _restore_combo(self._renderer_combo,    self._renderer_options,
                       "tt_renderer", "")
        _restore_combo(self._format_combo,      _FORMAT_OPTIONS,
                       "tt_format", "png_sequence")
        _restore_combo(self._loop_combo,        _LOOP_OPTIONS,
                       "tt_loop_mode", "loop")
        _restore_combo(self._projection_combo,  _HDRI_PROJECTION_OPTIONS,
                       "tt_hdri_projection", "latlong")

        # Hover scale (numeric key)
        raw_scale = self._db.get_meta("tt_hover_scale", "2.0") or "2.0"
        try:
            scale = float(raw_scale)
        except (TypeError, ValueError):
            scale = 2.0
        for i, (_label, val) in enumerate(_HOVER_SCALE_OPTIONS):
            if abs(val - scale) < 1e-6:
                self._hover_scale_combo.blockSignals(True)
                self._hover_scale_combo.setCurrentIndex(i)
                self._hover_scale_combo.blockSignals(False)
                break

        # Checkboxes (stored as 0/1 strings)
        for chk, key, default in (
            (self._inherit_lens_chk,    "tt_inherit_lens",      1),
            (self._hover_enabled_chk,   "tt_hover_enabled",     1),
            (self._pin_chk,             "tt_hover_pin_on_click", 0),
            (self._cal_enabled_chk,     "tt_cal_enabled",       0),
            (self._cal_chrome_chk,      "tt_cal_show_chrome",   1),
            (self._cal_grey_chk,        "tt_cal_show_grey",     1),
            (self._cal_macbeth_chk,     "tt_cal_show_macbeth",  1),
        ):
            raw = self._db.get_meta(key, str(default))
            try:
                checked = bool(int(raw)) if raw not in ("", None) \
                    else bool(default)
            except (TypeError, ValueError):
                checked = bool(default)
            chk.blockSignals(True)
            chk.setChecked(checked)
            chk.blockSignals(False)

        # Lens enable state must match the inherit toggle.
        self._lens_widget.setEnabled(not self._inherit_lens_chk.isChecked())

        # Calibration sub-controls follow the master toggle.
        self._on_cal_enabled_toggled(self._cal_enabled_chk.isChecked())

        # HDRI list (JSON-encoded)
        raw_list = self._db.get_meta("tt_hdri_list", "")
        if raw_list:
            try:
                paths = json.loads(raw_list)
                if isinstance(paths, list):
                    for p in paths:
                        if isinstance(p, str) and p:
                            self._hdri_list.addItem(p)
            except (json.JSONDecodeError, TypeError):
                pass

        self._update_total_label()

    # ──────────────────────────────────────────────
    # Public API — consumed by BatchProcessor / hover widget
    # ──────────────────────────────────────────────

    @staticmethod
    def read_turntable_settings(db: AssetDatabase) -> dict:
        """Collect every turntable-related meta value into a dict.

        Called by BatchProcessor (render side) and the hover preview
        widget (playback side). Centralizes meta-key names so callers
        don't have to know the storage layout.
        """
        def _f(key, default):
            try:
                v = db.get_meta(key, "")
                return float(v) if v not in (None, "") else default
            except Exception:
                return default

        def _i(key, default):
            try:
                v = db.get_meta(key, "")
                return int(v) if v not in (None, "") else default
            except Exception:
                return default

        def _s(key, default):
            try:
                v = db.get_meta(key, "")
                return v if v not in (None, "") else default
            except Exception:
                return default

        def _b(key, default):
            raw = _s(key, str(int(default)))
            try:
                return bool(int(raw))
            except (TypeError, ValueError):
                return bool(default)

        # HDRI list
        hdris = []
        raw_list = _s("tt_hdri_list", "")
        if raw_list:
            try:
                parsed = json.loads(raw_list)
                if isinstance(parsed, list):
                    hdris = [p for p in parsed if isinstance(p, str) and p]
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "frames_per_cycle": _i("tt_frames_per_cycle", 72),
            "rotation_axis":    _s("tt_rotation_axis", "Y"),
            "direction":        _s("tt_direction", "cw"),
            "camera_pitch":     _f("tt_camera_pitch", 15.0),
            "distance_offset":  _f("tt_distance_offset", 0.0),
            "start_yaw":        _f("tt_start_yaw", 35.0),
            "renderer":         _s("tt_renderer", ""),
            "samples":          _i("tt_samples", 8),
            "width":            _i("tt_width", 384),
            "height":           _i("tt_height", 384),
            "inherit_lens":     _b("tt_inherit_lens", True),
            "focal":            _f("tt_focal", 50.0),
            "aperture":         _f("tt_aperture", 36.0),
            "format":           _s("tt_format", "png_sequence"),
            "fps":              _i("tt_fps", 24),
            "loop_mode":        _s("tt_loop_mode", "loop"),
            "hover_enabled":    _b("tt_hover_enabled", True),
            "hover_delay_ms":   _i("tt_hover_delay_ms", 350),
            "hover_scale":      _f("tt_hover_scale", 2.0),
            "hover_pin_on_click": _b("tt_hover_pin_on_click", False),
            "hdris":             hdris,
            "hdri_projection":   _s("tt_hdri_projection", "latlong"),
            "cal_enabled":       _b("tt_cal_enabled", False),
            "cal_show_chrome":   _b("tt_cal_show_chrome", True),
            "cal_show_grey":     _b("tt_cal_show_grey", True),
            "cal_show_macbeth":  _b("tt_cal_show_macbeth", True),
            "cal_distance":      _f("tt_cal_distance", 1.5),
            "cal_offset_x":      _f("tt_cal_offset_x", -0.45),
            "cal_offset_y":      _f("tt_cal_offset_y", -0.22),
            "cal_scale":         _f("tt_cal_scale", 0.75),
        }
