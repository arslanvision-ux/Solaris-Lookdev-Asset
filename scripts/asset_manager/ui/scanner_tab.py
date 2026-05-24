"""
Scanner Tab for the Asset Manager UI.
Handles directory scanning, path filtering, and batch processing logic.
"""

import os
import traceback
from asset_manager.qt_compat import QtWidgets, QtCore, QtGui

from ..database.asset_db import AssetDatabase
from ..database.models import ScanResult, TextureSet, AssetEntry
from ..pdg.batch_processor import BatchProcessor
from .styles import COLORS


class ScannerTab(QtWidgets.QWidget):
    """
    The Scanner tab allows users to select a root directory,
    filter for specific file types, and trigger the USD conversion pipeline.
    """

    assets_processed = QtCore.Signal()  # Emitted when a batch finish

    def __init__(self, db: AssetDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._processor = BatchProcessor(db)
        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ── Path Selection ──
        path_group = QtWidgets.QGroupBox("Source Directory")
        path_layout = QtWidgets.QHBoxLayout(path_group)
        
        self._path_edit = QtWidgets.QLineEdit()
        self._path_edit.setPlaceholderText("Select root directory to scan...")
        path_layout.addWidget(self._path_edit)
        
        browse_btn = QtWidgets.QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_directory)
        path_layout.addWidget(browse_btn)
        
        main_layout.addWidget(path_group)

        # ── Filters & Options ──
        options_layout = QtWidgets.QHBoxLayout()
        
        # Extensions
        ext_group = QtWidgets.QGroupBox("File Types")
        ext_layout = QtWidgets.QHBoxLayout(ext_group)
        self._ext_edit = QtWidgets.QLineEdit(
            ".obj, .fbx, .abc, .bgeo, .bgeo.sc, .usd, .usda, .usdc, .usdz"
        )
        ext_layout.addWidget(self._ext_edit)
        options_layout.addWidget(ext_group)
        
        # Recursive toggle
        self._recursive_check = QtWidgets.QCheckBox("Scan Subdirectories")
        self._recursive_check.setChecked(True)
        options_layout.addWidget(self._recursive_check)
        
        options_layout.addStretch()
        main_layout.addLayout(options_layout)

        # ── File List (Scan Results) ──
        list_group = QtWidgets.QGroupBox("Detected Assets")
        list_layout = QtWidgets.QVBoxLayout(list_group)
        
        self._asset_tree = QtWidgets.QTreeWidget()
        self._asset_tree.setHeaderLabels(["Asset Name", "Format", "Path"])
        self._asset_tree.setColumnCount(3)
        self._asset_tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self._asset_tree.setAlternatingRowColors(True)
        list_layout.addWidget(self._asset_tree)
        
        btn_row = QtWidgets.QHBoxLayout()
        self._scan_btn = QtWidgets.QPushButton("Scan Directory")
        self._scan_btn.setObjectName("primaryButton")
        self._scan_btn.setMinimumHeight(32)
        self._scan_btn.clicked.connect(self._scan_directory)
        btn_row.addWidget(self._scan_btn)
        
        self._clear_btn = QtWidgets.QPushButton("Clear List")
        self._clear_btn.clicked.connect(self._clear_list)
        btn_row.addWidget(self._clear_btn)
        
        btn_row.addStretch()
        list_layout.addLayout(btn_row)
        
        main_layout.addWidget(list_group)

        # ── Processing ──
        proc_group = QtWidgets.QGroupBox("Processing")
        proc_layout = QtWidgets.QVBoxLayout(proc_group)
        
        self._process_btn = QtWidgets.QPushButton("Process Selected Assets (Create USD)")
        self._process_btn.setObjectName("accentButton")
        self._process_btn.setMinimumHeight(44)
        self._process_btn.setEnabled(False)
        self._process_btn.clicked.connect(self._process_assets)
        proc_layout.addWidget(self._process_btn)
        
        self._progress_bar = QtWidgets.QProgressBar()
        self._progress_bar.setVisible(False)
        proc_layout.addWidget(self._progress_bar)
        
        self._status_label = QtWidgets.QLabel("Ready")
        self._status_label.setObjectName("dimLabel")
        proc_layout.addWidget(self._status_label)
        
        main_layout.addWidget(proc_group)

    # ──────────────────────────────────────────────
    # Logic
    # ──────────────────────────────────────────────

    def _browse_directory(self):
        dir_path = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Source Directory"
        )
        if dir_path:
            self._path_edit.setText(dir_path)

    def _scan_directory(self):
        root = self._path_edit.text()
        if not os.path.isdir(root):
            QtWidgets.QMessageBox.warning(self, "Invalid Path", "Please select a valid directory.")
            return

        self._clear_list()
        self._status_label.setText("Scanning...")
        
        extensions = [e.strip().lower() for e in self._ext_edit.text().split(",")]
        recursive = self._recursive_check.isChecked()
        
        found_files = []
        if recursive:
            for r, d, f in os.walk(root):
                for file in f:
                    if any(file.lower().endswith(ext) for ext in extensions):
                        found_files.append(os.path.join(r, file))
        else:
            for file in os.listdir(root):
                if any(file.lower().endswith(ext) for ext in extensions):
                    found_files.append(os.path.join(root, file))
        
        # Populate tree
        for f in found_files:
            name = os.path.basename(f)
            ext = os.path.splitext(f)[1].upper()
            item = QtWidgets.QTreeWidgetItem([name, ext, f])
            # Store full path in data
            item.setData(0, QtCore.Qt.UserRole, f)
            self._asset_tree.addTopLevelItem(item)
            
        self._asset_tree.resizeColumnToContents(0)
        self._asset_tree.resizeColumnToContents(1)
        
        count = len(found_files)
        self._status_label.setText(f"Found {count} candidate assets.")
        self._process_btn.setEnabled(count > 0)

    def _clear_list(self):
        self._asset_tree.clear()
        self._process_btn.setEnabled(False)
        self._status_label.setText("Ready")

    def _process_assets(self):
        selected_items = self._asset_tree.selectedItems()
        if not selected_items:
            # If nothing selected, process all
            selected_items = [self._asset_tree.topLevelItem(i) for i in range(self._asset_tree.topLevelItemCount())]

        if not selected_items:
            return

        paths = [item.data(0, QtCore.Qt.UserRole) for item in selected_items]

        self._scan_btn.setEnabled(False)
        self._process_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(paths))
        self._progress_bar.setValue(0)

        self._status_label.setText(f"Processing {len(paths)} assets...")
        QtWidgets.QApplication.processEvents()

        results = self._run_sequential(paths)
        self._on_batch_finished(results)

    def _prepare_scan_results(self, paths):
        """Re-scan each selected file's directory to get its texture set,
        then bind the selected geo path back onto the ScanResult."""
        from ..core.scanner import DirectoryScanner
        scanner = DirectoryScanner()
        scan_results = []
        for p in paths:
            asset_name = os.path.splitext(os.path.basename(p))[0]
            directory = os.path.dirname(p)
            # recursive=True so the scanner descends into `<asset>/textures/`
            # subfolders. Without this, textures one level below the geo file
            # are invisible to the scanner and assets get built with no
            # materials → black thumbnails.
            dir_scans = {sr.asset_name: sr for sr in
                         scanner.scan_single_directory(directory, recursive=True)}
            sr = dir_scans.get(asset_name) or ScanResult(
                asset_name=asset_name, geo_file=p,
                texture_set=TextureSet(), source_dir=directory,
            )
            sr.geo_file = p
            scan_results.append(sr)

        active = self._db.get_active_project() or {}
        output_dir = active.get("output_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager", "usd")
        thumb_dir = active.get("thumbnail_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager", "thumbnails")
        return scan_results, output_dir, thumb_dir

    def _run_sequential(self, paths):
        """
        Build minimal ScanResults from the selected geo paths and run them
        through the BatchProcessor sequentially. Returns a list of
        {"name", "success", "error"} dicts for UI feedback.
        """
        # Build ScanResult per geo path. Textures in the same directory are
        # auto-matched via the existing scanner conventions.
        from ..core.scanner import DirectoryScanner
        scanner = DirectoryScanner()
        scan_results = []
        for p in paths:
            asset_name = os.path.splitext(os.path.basename(p))[0]
            directory = os.path.dirname(p)
            # recursive=True picks up textures in `<asset>/textures/`
            # subfolders, which is the conventional layout for PBR assets.
            dir_scans = {sr.asset_name: sr for sr in
                         scanner.scan_single_directory(directory, recursive=True)}
            sr = dir_scans.get(asset_name) or ScanResult(
                asset_name=asset_name, geo_file=p,
                texture_set=TextureSet(), source_dir=directory,
            )
            # Ensure geo_file points to the selected file
            sr.geo_file = p
            tex_count = sr.texture_set.get_map_count()
            print(f"[Scanner] {asset_name}: found {tex_count} texture maps "
                  f"({list(sr.texture_set.get_populated_maps().keys())})")
            scan_results.append(sr)

        active = self._db.get_active_project() or {}
        output_dir = active.get("output_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager", "usd")
        thumb_dir = active.get("thumbnail_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager", "thumbnails")

        total = len(scan_results)

        def _progress(i, n, name):
            self._progress_bar.setValue(i)
            self._status_label.setText(f"[{i}/{n}] {name}")
            QtWidgets.QApplication.processEvents()

        results = []
        try:
            entries = self._processor.process_sequential(
                scan_results, output_dir, thumb_dir,
                progress_callback=_progress,
            )
            for entry in entries:
                results.append({
                    "name": entry.name,
                    "success": entry.status == "ready",
                    "error": entry.error_message,
                })
        except Exception as e:
            traceback.print_exc()
            for sr in scan_results:
                results.append({"name": sr.asset_name, "success": False, "error": str(e)})

        self._progress_bar.setValue(total)
        return results

    def _on_batch_finished(self, results):
        self._scan_btn.setEnabled(True)
        self._process_btn.setEnabled(True)
        self._progress_bar.setVisible(False)

        success_count = sum(1 for r in results if r["success"])
        self._status_label.setText(f"Batch complete. {success_count}/{len(results)} successful.")

        # Notify main panel to refresh gallery
        self.assets_processed.emit()

        if success_count < len(results):
            QtWidgets.QMessageBox.warning(
                self, "Processing Complete",
                f"Batch finished with {len(results) - success_count} errors. Check console for details."
            )
