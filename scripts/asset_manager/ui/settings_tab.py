"""
Settings Tab for the Asset Manager UI.
Allows users to configure project paths, renderers, and database settings.
"""

import os
import json
from asset_manager.qt_compat import QtWidgets, QtCore, QtGui

from ..database.asset_db import AssetDatabase
from .styles import COLORS

# Constants for common settings
ACES_COLORSPACES = {
    "srgb": "Utility - sRGB - Texture",
    "linear": "Utility - Linear - sRGB",
    "raw": "Utility - Raw",
    "noncolor": "Utility - Raw"
}

TEXTURE_COLORSPACE_MAP = {
    "Base Color": "srgb",
    "Emissive": "srgb",
    "Roughness": "raw",
    "Metallic": "raw",
    "Normal": "raw",
    "Height/Displacement": "raw",
    "AO": "raw"
}


class SettingsTab(QtWidgets.QWidget):
    """
    The Settings tab manages global and project-specific configurations.
    Data is stored in the database and persisted across sessions.
    """
    
    settings_changed = QtCore.Signal()

    def __init__(self, db: AssetDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._build_ui()
        self._load_settings()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Header
        header = QtWidgets.QLabel("Settings")
        header.setObjectName("sectionTitle")
        header.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {COLORS['text_primary']};"
        )
        main_layout.addWidget(header)

        # Scroll area for all settings
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)

        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(16)

        # ── Project Configuration ──
        proj_group = QtWidgets.QGroupBox("Project Configuration")
        proj_layout = QtWidgets.QVBoxLayout(proj_group)
        proj_layout.setSpacing(10)

        proj_form = QtWidgets.QFormLayout()
        proj_form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        proj_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        proj_form.setHorizontalSpacing(10)
        proj_form.setVerticalSpacing(8)

        self._alias_edit = QtWidgets.QLineEdit()
        self._alias_edit.setPlaceholderText("e.g. my_film_project")
        proj_form.addRow("Alias:", self._alias_edit)

        def _browse_row(placeholder, browse_slot):
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText(placeholder)
            btn = QtWidgets.QPushButton("Browse")
            btn.setFixedWidth(85)
            btn.clicked.connect(browse_slot)
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(edit)
            row.addWidget(btn)
            return edit, row

        self._project_path_edit, _prow = _browse_row(
            "Root directory of the project", self._browse_project_path)
        proj_form.addRow("Project Path:", _prow)

        self._output_edit, _orow = _browse_row(
            "Where processed USD assets go", self._browse_output_dir)
        proj_form.addRow("USD Output Dir:", _orow)

        self._thumb_edit, _trow = _browse_row(
            "Where thumbnails are saved", self._browse_thumb_dir)
        proj_form.addRow("Thumbnails Dir:", _trow)

        proj_layout.addLayout(proj_form)

        proj_btn_row = QtWidgets.QHBoxLayout()
        proj_btn_row.setSpacing(8)
        self._save_proj_btn = QtWidgets.QPushButton("Save Project")
        self._save_proj_btn.setObjectName("primaryButton")
        self._save_proj_btn.setFixedWidth(110)
        self._save_proj_btn.clicked.connect(self._save_project)
        proj_btn_row.addWidget(self._save_proj_btn)

        self._load_proj_combo = QtWidgets.QComboBox()
        self._load_proj_combo.setPlaceholderText("Load existing project…")
        self._load_proj_combo.currentTextChanged.connect(self._load_project)
        proj_btn_row.addWidget(self._load_proj_combo)

        self._set_active_btn = QtWidgets.QPushButton("Set Active")
        self._set_active_btn.setFixedWidth(100)
        self._set_active_btn.clicked.connect(self._set_active_project)
        proj_btn_row.addWidget(self._set_active_btn)

        proj_layout.addLayout(proj_btn_row)
        scroll_layout.addWidget(proj_group)

        # ── Renderer Settings ──
        render_group = QtWidgets.QGroupBox("Render Engine")
        render_form = QtWidgets.QFormLayout(render_group)
        render_form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        render_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        render_form.setHorizontalSpacing(10)
        render_form.setVerticalSpacing(8)

        self._renderer_combo = QtWidgets.QComboBox()
        self._renderer_options = [
            ("Karma CPU", "karma_cpu"),
            ("Karma XPU", "karma_xpu"),
            ("Arnold",    "arnold"),
            ("Redshift",  "redshift"),
        ]
        for label, _key in self._renderer_options:
            self._renderer_combo.addItem(label)
        self._renderer_combo.currentIndexChanged.connect(self._on_renderer_changed)
        render_form.addRow("Default Renderer:", self._renderer_combo)

        self._samples_spin = QtWidgets.QSpinBox()
        self._samples_spin.setRange(1, 4096)
        self._samples_spin.setValue(64)
        self._samples_spin.valueChanged.connect(
            lambda v: self._save_meta("karma_samples", v))
        render_form.addRow("Karma Samples:", self._samples_spin)

        self._res_preset_combo = QtWidgets.QComboBox()
        self._res_presets = [
            ("Standard 4:3",                    640,  480),
            ("HDTV 16:9",                       1280, 720),
            ("Academy 1.375:1",                 550,  400),
            ("Widescreen 1.85:1",               740,  400),
            ("IMAX 1.90:1",                     760,  400),
            ("CinemaScope 2.39:1",              956,  400),
            ("DCI 2K/4K",                       2048, 1080),
            ("DCI 2K/4K (flat cropped)",        1998, 1080),
            ("DCI 2K/4K (CinemaScope cropped)", 2048, 858),
            ("Wide 3:2",                        600,  400),
            ("Portrait 2:3",                    400,  600),
            ("Square 1:1",                      512,  512),
            ("Custom",                          0,    0),
        ]
        for label, _w, _h in self._res_presets:
            self._res_preset_combo.addItem(label)
        self._res_preset_combo.currentIndexChanged.connect(self._on_res_preset_changed)
        render_form.addRow("Resolution Preset:", self._res_preset_combo)

        res_size_row = QtWidgets.QHBoxLayout()
        res_size_row.setSpacing(4)
        self._res_x_spin = QtWidgets.QSpinBox()
        self._res_x_spin.setRange(128, 4096)
        self._res_x_spin.setValue(640)
        self._res_x_spin.valueChanged.connect(self._on_res_spin_changed)
        res_size_row.addWidget(self._res_x_spin)
        _x_sep = QtWidgets.QLabel("×")
        _x_sep.setFixedWidth(12)
        res_size_row.addWidget(_x_sep)
        self._res_y_spin = QtWidgets.QSpinBox()
        self._res_y_spin.setRange(128, 4096)
        self._res_y_spin.setValue(480)
        self._res_y_spin.valueChanged.connect(self._on_res_spin_changed)
        res_size_row.addWidget(self._res_y_spin)
        res_size_row.addStretch()
        render_form.addRow("Thumbnail Size:", res_size_row)

        self._hdri_edit = QtWidgets.QLineEdit()
        self._hdri_edit.setPlaceholderText("Optional HDRI for thumbnail lighting")
        self._hdri_edit.editingFinished.connect(self._save_hdri)
        browse_hdri = QtWidgets.QPushButton("Browse")
        browse_hdri.setFixedWidth(85)
        browse_hdri.clicked.connect(self._browse_hdri)
        hdri_row = QtWidgets.QHBoxLayout()
        hdri_row.setSpacing(6)
        hdri_row.addWidget(self._hdri_edit)
        hdri_row.addWidget(browse_hdri)
        render_form.addRow("Thumbnail HDRI:", hdri_row)

        scroll_layout.addWidget(render_group)

        # ── Thumbnail Camera ──
        cam_group = QtWidgets.QGroupBox("Thumbnail Camera")
        cam_layout = QtWidgets.QFormLayout(cam_group)
        cam_layout.setRowWrapPolicy(QtWidgets.QFormLayout.DontWrapRows)

        # ── Transform sub-section ──
        _transform_sep = QtWidgets.QLabel("Transform")
        _transform_sep.setStyleSheet(
            "font-weight: bold; font-size: 11px; "
            f"color: {COLORS.get('accent', '#0f9b8e')}; "
            "margin-top: 4px;"
        )
        cam_layout.addRow(_transform_sep)

        # Asset scale — uniform xform applied before componentoutput writes.
        self._scale_spin = QtWidgets.QDoubleSpinBox()
        self._scale_spin.setRange(0.0001, 10000.0)
        self._scale_spin.setDecimals(4)
        self._scale_spin.setSingleStep(0.1)
        self._scale_spin.setValue(1.0)
        self._scale_spin.valueChanged.connect(
            lambda v: self._save_meta("asset_scale", v))
        cam_layout.addRow("Asset Scale:", self._scale_spin)

        self._yaw_spin = QtWidgets.QDoubleSpinBox()
        self._yaw_spin.setRange(-360.0, 360.0)
        self._yaw_spin.setDecimals(1)
        self._yaw_spin.setSingleStep(5.0)
        self._yaw_spin.setSuffix("°")
        self._yaw_spin.setValue(35.0)
        self._yaw_spin.valueChanged.connect(
            lambda v: self._save_meta("camera_yaw", v))
        cam_layout.addRow("Camera Yaw:", self._yaw_spin)

        self._pitch_spin = QtWidgets.QDoubleSpinBox()
        self._pitch_spin.setRange(-89.0, 89.0)
        self._pitch_spin.setDecimals(1)
        self._pitch_spin.setSingleStep(5.0)
        self._pitch_spin.setSuffix("°")
        self._pitch_spin.setValue(20.0)
        self._pitch_spin.valueChanged.connect(
            lambda v: self._save_meta("camera_pitch", v))
        cam_layout.addRow("Camera Pitch:", self._pitch_spin)

        self._dist_spin = QtWidgets.QDoubleSpinBox()
        self._dist_spin.setRange(-1000.0, 1000.0)
        self._dist_spin.setDecimals(3)
        self._dist_spin.setSingleStep(0.5)
        self._dist_spin.setValue(0.0)
        self._dist_spin.setToolTip(
            "Camera zoom offset.\n"
            "Positive = zoom out (move camera away).\n"
            "Negative = zoom in (move camera closer)."
        )
        self._dist_spin.valueChanged.connect(
            lambda v: self._save_meta("thumb_distance", v))
        cam_layout.addRow("Zoom Offset:", self._dist_spin)

        # ── Lens sub-section ──
        _lens_sep = QtWidgets.QLabel("Lens")
        _lens_sep.setStyleSheet(
            "font-weight: bold; font-size: 11px; "
            f"color: {COLORS.get('accent', '#0f9b8e')}; "
            "margin-top: 8px;"
        )
        cam_layout.addRow(_lens_sep)

        self._focal_spin = QtWidgets.QDoubleSpinBox()
        self._focal_spin.setRange(1.0, 5000.0)
        self._focal_spin.setDecimals(3)
        self._focal_spin.setSingleStep(1.0)
        self._focal_spin.setSuffix(" mm")
        self._focal_spin.setValue(40.0)
        self._focal_spin.valueChanged.connect(
            lambda v: self._save_meta("cam_focal", v))
        cam_layout.addRow("Focal Length:", self._focal_spin)

        self._aperture_spin = QtWidgets.QDoubleSpinBox()
        self._aperture_spin.setRange(0.1, 500.0)
        self._aperture_spin.setDecimals(3)
        self._aperture_spin.setSingleStep(1.0)
        self._aperture_spin.setSuffix(" mm")
        self._aperture_spin.setValue(25.0)
        self._aperture_spin.valueChanged.connect(
            lambda v: self._save_meta("cam_aperture", v))
        cam_layout.addRow("Horiz. Aperture:", self._aperture_spin)

        # ── Clipping sub-section ──
        _clip_sep = QtWidgets.QLabel("Clipping")
        _clip_sep.setStyleSheet(
            "font-weight: bold; font-size: 11px; "
            f"color: {COLORS.get('accent', '#0f9b8e')}; "
            "margin-top: 8px;"
        )
        cam_layout.addRow(_clip_sep)

        self._clip_near_spin = QtWidgets.QDoubleSpinBox()
        self._clip_near_spin.setRange(0.0001, 100000.0)
        self._clip_near_spin.setDecimals(4)
        self._clip_near_spin.setSingleStep(0.1)
        self._clip_near_spin.setValue(0.1)
        self._clip_near_spin.valueChanged.connect(
            lambda v: self._save_meta("cam_near", v))
        cam_layout.addRow("Near Clip:", self._clip_near_spin)

        self._clip_far_spin = QtWidgets.QDoubleSpinBox()
        self._clip_far_spin.setRange(1.0, 1e10)
        self._clip_far_spin.setDecimals(0)
        self._clip_far_spin.setSingleStep(10000.0)
        self._clip_far_spin.setValue(1000000.0)
        self._clip_far_spin.valueChanged.connect(
            lambda v: self._save_meta("cam_far", v))
        cam_layout.addRow("Far Clip:", self._clip_far_spin)

        cam_info = QtWidgets.QLabel(
            "Asset is centered at origin, then scaled. The camera "
            "auto-frames the bbox; Yaw/Pitch orbit around the asset. "
            "FOV is computed from Focal Length + Horiz. Aperture."
        )
        cam_info.setWordWrap(True)
        cam_info.setObjectName("dimLabel")
        cam_layout.addRow(cam_info)

        scroll_layout.addWidget(cam_group)

        # ── Proxy / Sim Settings ──
        proxy_group = QtWidgets.QGroupBox("Proxy & Simulation Mesh")
        proxy_layout = QtWidgets.QVBoxLayout(proxy_group)

        proxy_row = QtWidgets.QHBoxLayout()
        proxy_row.addWidget(QtWidgets.QLabel("Proxy Keep Ratio:"))
        self._proxy_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._proxy_slider.setRange(1, 100)
        self._proxy_slider.setValue(10)
        self._proxy_slider.valueChanged.connect(self._on_proxy_slider)
        proxy_row.addWidget(self._proxy_slider)
        self._proxy_label = QtWidgets.QLabel("10%")
        self._proxy_label.setFixedWidth(40)
        proxy_row.addWidget(self._proxy_label)
        proxy_layout.addLayout(proxy_row)

        sim_row = QtWidgets.QHBoxLayout()
        sim_row.addWidget(QtWidgets.QLabel("Sim Mesh Method:"))
        self._sim_combo = QtWidgets.QComboBox()
        self._sim_combo.addItems(["Convex Hull", "VDB Remesh", "Decimated"])
        sim_row.addWidget(self._sim_combo)
        sim_row.addStretch()
        proxy_layout.addLayout(sim_row)

        scroll_layout.addWidget(proxy_group)

        # ── Color Management ──
        color_group = QtWidgets.QGroupBox("Color Management (ACEScg)")
        color_layout = QtWidgets.QVBoxLayout(color_group)

        info_label = QtWidgets.QLabel(
            "Using ACEScg working space. "
            "Base color & emissive textures are treated as sRGB input, "
            "all data textures (roughness, normal, etc.) as Raw/Linear."
        )
        info_label.setWordWrap(True)
        info_label.setObjectName("dimLabel")
        color_layout.addWidget(info_label)

        # Colorspace display table
        cs_table = QtWidgets.QTableWidget()
        cs_table.setColumnCount(2)
        cs_table.setHorizontalHeaderLabels(["Map Type", "OCIO Colorspace"])
        cs_table.horizontalHeader().setStretchLastSection(True)
        cs_table.verticalHeader().setVisible(False)
        cs_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        cs_table.setMaximumHeight(200)

        cs_table.setRowCount(len(TEXTURE_COLORSPACE_MAP))
        for row, (map_type, cs_key) in enumerate(TEXTURE_COLORSPACE_MAP.items()):
            cs_table.setItem(row, 0, QtWidgets.QTableWidgetItem(map_type))
            cs_name = ACES_COLORSPACES.get(cs_key, cs_key)
            cs_table.setItem(row, 1, QtWidgets.QTableWidgetItem(cs_name))
        cs_table.resizeColumnsToContents()
        color_layout.addWidget(cs_table)

        scroll_layout.addWidget(color_group)

        # ── Database Info ──
        db_group = QtWidgets.QGroupBox("Database")
        db_layout = QtWidgets.QVBoxLayout(db_group)

        self._db_path_label = QtWidgets.QLabel(
            f"Database: {self._db.db_path}"
        )
        self._db_path_label.setObjectName("dimLabel")
        db_layout.addWidget(self._db_path_label)

        db_btn_row = QtWidgets.QHBoxLayout()
        export_btn = QtWidgets.QPushButton("Export to JSON")
        export_btn.clicked.connect(self._export_db)
        db_btn_row.addWidget(export_btn)

        import_btn = QtWidgets.QPushButton("Import from JSON")
        import_btn.clicked.connect(self._import_db)
        db_btn_row.addWidget(import_btn)

        db_btn_row.addStretch()
        db_layout.addLayout(db_btn_row)

        scroll_layout.addWidget(db_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    # ──────────────────────────────────────────────
    # Load / Save
    # ──────────────────────────────────────────────

    def _load_settings(self):
        """Fetch settings from the database and populate UI."""
        projects = self._db.get_all_projects()
        self._load_proj_combo.clear()
        self._load_proj_combo.addItems([p["alias"] for p in projects])

        active = self._db.get_active_project()
        if active:
            self._alias_edit.setText(active.get("alias", ""))
            self._project_path_edit.setText(active.get("project_path", ""))
            self._output_edit.setText(active.get("output_dir", ""))
            self._thumb_edit.setText(active.get("thumbnail_dir", ""))

        self._hdri_edit.setText(self._db.get_meta("thumbnail_hdri", ""))

        # Block signals while populating spinboxes so they don't write
        # back the loaded value on every setValue() call.
        for w, key, cast, default in (
            (self._res_x_spin,      "thumb_res_x",    int,   640),
            (self._res_y_spin,      "thumb_res_y",    int,   480),
            (self._samples_spin,    "karma_samples",  int,   64),
            (self._scale_spin,      "asset_scale",    float, 1.0),
            (self._yaw_spin,        "camera_yaw",     float, 35.0),
            (self._pitch_spin,      "camera_pitch",   float, 20.0),
            (self._dist_spin,       "thumb_distance", float, 0.0),
            (self._focal_spin,      "cam_focal",      float, 40.0),
            (self._aperture_spin,   "cam_aperture",   float, 25.0),
            (self._clip_near_spin,  "cam_near",       float, 0.1),
            (self._clip_far_spin,   "cam_far",        float, 1000000.0),
        ):
            raw = self._db.get_meta(key, "")
            try:
                val = cast(raw) if raw not in (None, "") else default
            except (TypeError, ValueError):
                val = default
            w.blockSignals(True)
            w.setValue(val)
            w.blockSignals(False)

        # Sync resolution preset combo to match the loaded X/Y values.
        lx = self._res_x_spin.value()
        ly = self._res_y_spin.value()
        self._res_preset_combo.blockSignals(True)
        matched = False
        for i, (label, pw, ph) in enumerate(self._res_presets):
            if label != "Custom" and pw == lx and ph == ly:
                self._res_preset_combo.setCurrentIndex(i)
                matched = True
                break
        if not matched:
            custom_idx = next(
                (i for i, (l, _, _) in enumerate(self._res_presets)
                 if l == "Custom"), -1
            )
            if custom_idx >= 0:
                self._res_preset_combo.setCurrentIndex(custom_idx)
        self._res_preset_combo.blockSignals(False)

        # Restore renderer dropdown selection from meta (default: karma_cpu).
        stored_key = self._db.get_meta("renderer", "karma_cpu") or "karma_cpu"
        for i, (_label, key) in enumerate(self._renderer_options):
            if key == stored_key:
                self._renderer_combo.blockSignals(True)
                self._renderer_combo.setCurrentIndex(i)
                self._renderer_combo.blockSignals(False)
                break

    def _on_renderer_changed(self, idx):
        if 0 <= idx < len(self._renderer_options):
            _label, key = self._renderer_options[idx]
            self._save_meta("renderer", key)

    def _on_res_preset_changed(self, idx):
        """Apply a resolution preset to both spinboxes."""
        if not hasattr(self, "_res_presets"):
            return
        if idx < 0 or idx >= len(self._res_presets):
            return
        label, w, h = self._res_presets[idx]
        if label == "Custom" or w == 0:
            return
        for spin in (self._res_x_spin, self._res_y_spin):
            spin.blockSignals(True)
        self._res_x_spin.setValue(w)
        self._res_y_spin.setValue(h)
        for spin in (self._res_x_spin, self._res_y_spin):
            spin.blockSignals(False)
        self._save_meta("thumb_res_x", w)
        self._save_meta("thumb_res_y", h)

    def _on_res_spin_changed(self, _v):
        """When the user edits width/height manually, switch preset to Custom."""
        if not hasattr(self, "_res_presets"):
            return
        x = self._res_x_spin.value()
        y = self._res_y_spin.value()
        # Check if this matches a known preset; if so reflect it.
        for i, (label, pw, ph) in enumerate(self._res_presets):
            if label != "Custom" and pw == x and ph == y:
                self._res_preset_combo.blockSignals(True)
                self._res_preset_combo.setCurrentIndex(i)
                self._res_preset_combo.blockSignals(False)
                break
        else:
            custom_idx = next(
                (i for i, (l, _, _) in enumerate(self._res_presets)
                 if l == "Custom"), -1
            )
            if custom_idx >= 0:
                self._res_preset_combo.blockSignals(True)
                self._res_preset_combo.setCurrentIndex(custom_idx)
                self._res_preset_combo.blockSignals(False)
        self._save_meta("thumb_res_x", x)
        self._save_meta("thumb_res_y", y)

    def _save_meta(self, key, value):
        """Persist a single setting to the DB meta table."""
        try:
            self._db.set_meta(key, str(value))
        except Exception as e:
            print(f"[SettingsTab] Failed to save {key}: {e}")

    def _save_project(self):
        alias = self._alias_edit.text()
        path = self._project_path_edit.text().strip()
        output = self._output_edit.text().strip()
        thumb = self._thumb_edit.text().strip()

        if not alias or not path:
            QtWidgets.QMessageBox.warning(self, "Missing Info", "Alias and Path are required.")
            return

        # Default subfolders if not specified
        if not output:
            output = os.path.join(path, "usd")
            self._output_edit.setText(output)
        if not thumb:
            thumb = os.path.join(path, "icons")
            self._thumb_edit.setText(thumb)

        # Always create all required directories
        self._create_project_dirs(thumb, output)

        self._db.add_project(alias, path, output, thumb)
        self._db.set_meta("thumbnail_hdri", self._hdri_edit.text().strip())
        self._load_settings()
        self.settings_changed.emit()

    def _create_project_dirs(self, thumb_dir: str, usd_dir: str = ""):
        """Create icons/, icons/turntable/, and usd/ under the project."""
        dirs_to_create = []
        if usd_dir:
            dirs_to_create.append(usd_dir)
        if thumb_dir:
            dirs_to_create.append(thumb_dir)
            dirs_to_create.append(os.path.join(thumb_dir, "turntable"))
        for d in dirs_to_create:
            try:
                os.makedirs(d, exist_ok=True)
                print(f"[SettingsTab] Created: {d}")
            except Exception as e:
                print(f"[SettingsTab] Failed to create {d}: {e}")

    def _load_project(self, alias):
        if not alias: return
        proj = self._db.get_project(alias)
        if proj:
            self._alias_edit.setText(proj.get("alias", ""))
            self._project_path_edit.setText(proj.get("project_path", ""))
            self._output_edit.setText(proj.get("output_dir", ""))
            self._thumb_edit.setText(proj.get("thumbnail_dir", ""))

    def _set_active_project(self):
        alias = self._alias_edit.text()
        if alias:
            self._db.set_active_project(alias)
            self.settings_changed.emit()

    def _on_proxy_slider(self, value):
        self._proxy_label.setText(f"{value}%")

    def _browse_project_path(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Project Path")
        if not path:
            return
        self._project_path_edit.setText(path)
        # Auto-fill subdirs if fields are empty
        if not self._output_edit.text().strip():
            self._output_edit.setText(os.path.join(path, "usd"))
        if not self._thumb_edit.text().strip():
            self._thumb_edit.setText(os.path.join(path, "icons"))

    def _browse_output_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Output Path")
        if path: self._output_edit.setText(path)

    def _browse_thumb_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Thumbnails Path")
        if path: self._thumb_edit.setText(path)

    def _browse_hdri(self):
        file, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select HDRI", "", "Images (*.hdr *.exr)")
        if file:
            self._hdri_edit.setText(file)
            self._save_hdri()

    def _save_hdri(self):
        """Persist the HDRI path to the database's meta table."""
        try:
            self._db.set_meta("thumbnail_hdri", self._hdri_edit.text().strip())
        except Exception as e:
            print(f"[SettingsTab] Failed to save HDRI path: {e}")

    def _export_db(self):
        file, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export DB", "", "JSON (*.json)")
        if file:
            self._db.export_to_json(file)

    def _import_db(self):
        file, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import DB", "", "JSON (*.json)")
        if file:
            self._db.import_from_json(file)
            self.settings_changed.emit()
            self._load_settings()
