"""
Gallery Tab for the Asset Manager UI.
Displays processed assets in a grid of thumbnail cards.
Supports search, filtering, drag-and-drop to scene, and context menus.
"""

import os

from asset_manager.qt_compat import QtWidgets, QtCore, QtGui

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from ..database.asset_db import AssetDatabase
from ..database.models import AssetEntry
from .thumbnail_widget import ThumbnailWidget
from .styles import COLORS
from .hover_preview import (
    HoverPreviewPopup, find_turntable_dir, list_turntable_frames,
)
from .turntable_tab import TurntableTab


class GalleryTab(QtWidgets.QWidget):
    """
    The Gallery tab shows all processed assets as thumbnail cards in a
    scrollable grid. Assets can be dragged into the Solaris scene or
    inserted via context menu / double-click.
    """

    def __init__(self, db: AssetDatabase, parent=None):
        super().__init__(parent)
        self._db = db
        self._thumbnail_widgets = {}  # uid -> ThumbnailWidget
        self._selected_uids: set = set()
        self._anchor_uid: str | None = None

        # Hover preview popup — shared singleton.
        self._hover_popup = HoverPreviewPopup(self)
        self._hover_pending_uid: str | None = None
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._on_hover_timer)

        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ── Header & Search ──
        header_row = QtWidgets.QHBoxLayout()

        title = QtWidgets.QLabel("Asset Gallery")
        title.setObjectName("sectionTitle")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {COLORS['text_primary']};"
        )
        header_row.addWidget(title)

        header_row.addStretch()

        # Search
        self._search_edit = QtWidgets.QLineEdit()
        self._search_edit.setPlaceholderText("Search assets...")
        self._search_edit.setFixedWidth(250)
        self._search_edit.textChanged.connect(self._on_search)
        header_row.addWidget(self._search_edit)

        # Filter combo
        self._filter_combo = QtWidgets.QComboBox()
        self._filter_combo.addItems(["All", "Ready", "Pending", "Error"])
        self._filter_combo.setFixedWidth(100)
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        header_row.addWidget(self._filter_combo)

        # Icon size picker
        self._size_combo = QtWidgets.QComboBox()
        self._size_options = [
            ("Small",   "small"),
            ("Medium",  "medium"),
            ("Large",   "large"),
            ("Largest", "largest"),
        ]
        for label, _key in self._size_options:
            self._size_combo.addItem(label)
        # Restore persisted choice (default = medium).
        stored_size = self._db.get_meta("gallery_icon_size", "medium")
        for i, (_l, k) in enumerate(self._size_options):
            if k == stored_size:
                self._size_combo.setCurrentIndex(i)
                break
        self._size_combo.setFixedWidth(100)
        self._size_combo.setToolTip("Thumbnail card size in the gallery grid.")
        self._size_combo.currentIndexChanged.connect(self._on_size_changed)
        header_row.addWidget(QtWidgets.QLabel("Icon Size:"))
        header_row.addWidget(self._size_combo)

        # Refresh
        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self.refresh_gallery)
        header_row.addWidget(self._refresh_btn)

        # Delete all
        self._delete_all_btn = QtWidgets.QPushButton("Delete All")
        self._delete_all_btn.setToolTip(
            "Remove every asset from the database. USD files on disk are kept."
        )
        self._delete_all_btn.clicked.connect(self._on_delete_all)
        header_row.addWidget(self._delete_all_btn)

        main_layout.addLayout(header_row)

        # ── Stats Bar ──
        self._stats_label = QtWidgets.QLabel("")
        self._stats_label.setObjectName("dimLabel")
        main_layout.addWidget(self._stats_label)

        # ── Scrollable Grid ──
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        self._grid_container = QtWidgets.QWidget()
        self._grid_layout = FlowLayout(self._grid_container)
        self._grid_container.setLayout(self._grid_layout)
        self._grid_container.mousePressEvent = self._on_background_click
        self._scroll.setWidget(self._grid_container)

        main_layout.addWidget(self._scroll)

        # ── Bottom Actions ──
        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setSpacing(8)

        self._rerender_btn = QtWidgets.QPushButton("Re-render Thumbnails")
        self._rerender_btn.setEnabled(False)
        self._rerender_btn.setToolTip("Re-render still thumbnails for selected assets")
        self._rerender_btn.clicked.connect(self._on_rerender_selected)
        bottom_row.addWidget(self._rerender_btn)

        self._turntable_btn = QtWidgets.QPushButton("Render Turntable")
        self._turntable_btn.setEnabled(False)
        self._turntable_btn.setToolTip("Render turntable sequences for selected assets")
        self._turntable_btn.clicked.connect(self._on_turntable_selected)
        bottom_row.addWidget(self._turntable_btn)

        bottom_row.addStretch()

        self._insert_ref_btn = QtWidgets.QPushButton("Insert as Reference")
        self._insert_ref_btn.setObjectName("primaryButton")
        self._insert_ref_btn.setEnabled(False)
        self._insert_ref_btn.clicked.connect(self._on_insert_reference)
        bottom_row.addWidget(self._insert_ref_btn)

        self._insert_payload_btn = QtWidgets.QPushButton("Insert as Payload")
        self._insert_payload_btn.setEnabled(False)
        self._insert_payload_btn.clicked.connect(self._on_insert_payload)
        bottom_row.addWidget(self._insert_payload_btn)

        main_layout.addLayout(bottom_row)

    # ──────────────────────────────────────────────
    # Gallery population
    # ──────────────────────────────────────────────

    def refresh_gallery(self):
        """Reload assets from the database and rebuild the grid.

        Assets whose thumbnail image no longer exists on disk (deleted
        manually, drive disconnected, etc.) are pruned from the database
        so the gallery stays in sync with reality. The pipeline can
        re-create them by re-processing the source asset."""
        self._clear_grid()

        query = self._search_edit.text().strip()
        status_filter = self._filter_combo.currentText().lower()

        if query:
            assets = self._db.search(query)
        elif status_filter != "all":
            assets = self._db.filter_by_status(status_filter)
        else:
            assets = self._db.get_all_assets()

        pruned = []
        for asset in assets:
            thumb_path = self._resolve_thumbnail_path(asset)
            if not thumb_path:
                # No PNG resolves anywhere — treat as orphaned and remove
                # from the DB so it won't reappear on the next refresh.
                pruned.append(asset)
                continue
            # Cache the resolved path so _add_thumbnail_card doesn't have
            # to re-walk the candidate list.
            asset.thumbnail_path = thumb_path
            self._add_thumbnail_card(asset)

        for asset in pruned:
            try:
                self._db.remove_asset(asset.uid)
                print(f"[Gallery] Pruned '{asset.name}' "
                      f"(uid={asset.uid}) — thumbnail missing")
            except Exception as e:
                print(f"[Gallery] Failed to prune '{asset.name}': {e}")

        self._update_stats()

    def _add_thumbnail_card(self, asset: AssetEntry):
        """Add a single thumbnail card to the grid."""
        # Resolve thumbnail path with fallbacks — the DB entry may have an
        # empty/stale path while the actual PNG exists in the project's
        # thumbnails dir under <asset_name>.png.
        thumb_path = self._resolve_thumbnail_path(asset)
        card = ThumbnailWidget(
            uid=asset.uid,
            name=asset.name,
            thumbnail_path=thumb_path,
            status=asset.status,
            usd_path=asset.usd_output_path,
            size_preset=self._current_size_preset(),
        )
        card.clicked.connect(self._on_card_clicked)
        card.double_clicked.connect(self._on_card_double_clicked)
        card.context_menu_requested.connect(self._on_card_context_menu)
        card.drag_started.connect(self._on_card_drag_started)
        card.hover_entered.connect(self._on_card_hover_entered)
        card.hover_left.connect(self._on_card_hover_left)

        self._grid_layout.addWidget(card)
        self._thumbnail_widgets[asset.uid] = card

    def _resolve_thumbnail_path(self, asset: AssetEntry) -> str:
        """Find an existing PNG for the asset. Checks the DB-stored path
        first, then the active project's thumbnail dir, then any sibling
        directory of the USD output. Returns "" if nothing exists."""
        # 1. DB-stored path
        if asset.thumbnail_path and os.path.exists(asset.thumbnail_path):
            return asset.thumbnail_path
        # 2. Active project's thumbnail dir
        active = (self._db.get_active_project() or {})
        thumb_dir = active.get("thumbnail_dir", "")
        candidates = []
        if thumb_dir:
            candidates.append(os.path.join(thumb_dir, f"{asset.name}.png"))
        # 3. Houdini default dir
        candidates.append(os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager",
            "thumbnails", f"{asset.name}.png"))
        # 4. Adjacent to the USD output (asset_dir/thumbnails/, asset_dir/)
        if asset.usd_output_path:
            asset_dir = os.path.dirname(asset.usd_output_path)
            candidates.append(os.path.join(asset_dir, f"{asset.name}.png"))
            candidates.append(os.path.join(asset_dir, "thumbnail.png"))
            candidates.append(os.path.join(
                os.path.dirname(asset_dir), "thumbnails",
                f"{asset.name}.png"))
            candidates.append(os.path.join(
                os.path.dirname(asset_dir), "icons",
                f"{asset.name}.png"))
        for c in candidates:
            c = c.replace("\\", "/")
            if os.path.exists(c):
                return c
        return ""

    def _clear_grid(self):
        """Remove all cards from the grid."""
        for uid, widget in self._thumbnail_widgets.items():
            widget.setParent(None)
            widget.deleteLater()
        self._thumbnail_widgets.clear()
        self._selected_uids.clear()
        self._anchor_uid = None
        self._update_insert_buttons()

    def _update_stats(self):
        stats = self._db.get_stats()
        total = stats["total"]
        ready = stats["statuses"].get("ready", 0)
        pending = stats["statuses"].get("pending", 0)
        self._stats_label.setText(
            f"{total} assets — {ready} ready, {pending} pending"
        )

    # ──────────────────────────────────────────────
    # Event handlers
    # ──────────────────────────────────────────────

    def _on_search(self, text):
        self.refresh_gallery()

    def _on_filter_changed(self, text):
        self.refresh_gallery()

    def _on_size_changed(self, idx):
        if 0 <= idx < len(self._size_options):
            _label, key = self._size_options[idx]
            try:
                self._db.set_meta("gallery_icon_size", key)
            except Exception as e:
                print(f"[Gallery] failed to persist icon size: {e}")
        self.refresh_gallery()

    def _current_size_preset(self) -> str:
        idx = self._size_combo.currentIndex()
        if 0 <= idx < len(self._size_options):
            return self._size_options[idx][1]
        return "medium"

    def _update_insert_buttons(self):
        n = len(self._selected_uids)
        enabled = n > 0
        suffix = f" ({n})" if n > 1 else ""
        self._rerender_btn.setEnabled(enabled)
        self._turntable_btn.setEnabled(enabled)
        self._insert_ref_btn.setEnabled(enabled)
        self._insert_ref_btn.setText(f"Insert as Reference{suffix}")
        self._insert_payload_btn.setEnabled(enabled)
        self._insert_payload_btn.setText(f"Insert as Payload{suffix}")

    def _on_background_click(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            if self._grid_container.childAt(event.position().toPoint()) is not None:
                return
            for u in list(self._selected_uids):
                if u in self._thumbnail_widgets:
                    self._thumbnail_widgets[u].set_selected(False)
            self._selected_uids.clear()
            self._anchor_uid = None
            self._update_insert_buttons()
            # Background click also dismisses a pinned hover preview.
            if self._hover_popup.isVisible():
                self._hover_popup.hide_preview()

    def _on_card_hover_entered(self, uid: str):
        """Schedule the hover preview to open after the configured delay."""
        tt = TurntableTab.read_turntable_settings(self._db)
        if not tt["hover_enabled"]:
            return
        # If popup is pinned and showing another asset, leave it alone.
        if self._hover_popup.is_pinned and self._hover_popup.isVisible():
            return
        self._hover_pending_uid = uid
        self._hover_timer.start(max(0, int(tt["hover_delay_ms"])))

    def _on_card_hover_left(self, uid: str):
        """Cancel a pending open; hide the popup unless pinned."""
        if self._hover_pending_uid == uid:
            self._hover_pending_uid = None
            self._hover_timer.stop()
        if (self._hover_popup.isVisible()
                and self._hover_popup.current_uid == uid
                and not self._hover_popup.is_pinned):
            self._hover_popup.hide_preview()

    def _on_hover_timer(self):
        """Fired after hover delay — actually open the popup."""
        uid = self._hover_pending_uid
        self._hover_pending_uid = None
        if not uid:
            return
        card = self._thumbnail_widgets.get(uid)
        if card is None:
            return
        asset = self._db.get_asset(uid)
        if not asset:
            return

        tt = TurntableTab.read_turntable_settings(self._db)
        thumb_path = self._resolve_thumbnail_path(asset)
        tt_dir = find_turntable_dir(
            asset.name, thumb_path, asset.usd_output_path
        )
        frames = list_turntable_frames(tt_dir)

        # Fallback static pixmap if no frames yet.
        fallback = None
        if thumb_path and os.path.exists(thumb_path):
            fallback = QtGui.QPixmap(thumb_path)

        # Target popup size from settings (square, scaled from card size).
        scale = float(tt.get("hover_scale", 2.0))
        target = int(ThumbnailWidget.THUMB_SIZE * scale)

        num_hdris = max(1, len(tt.get("hdris", [])) or 1)
        self._hover_popup.show_for(
            uid=uid,
            name=asset.name,
            frames=frames,
            fallback_pixmap=fallback,
            fps=int(tt["fps"]),
            loop_mode=tt["loop_mode"],
            target_size=target,
            frames_per_cycle=int(tt["frames_per_cycle"]),
            num_hdris=num_hdris,
            anchor_global_pos=card.thumbnail_global_pos(),
        )

    def _on_card_drag_started(self, dragged_uid: str):
        """Build drag MIME data for all selected assets (or just the dragged
        card if nothing else is selected) and execute the drag."""
        drag_uids = (
            list(self._selected_uids)
            if dragged_uid in self._selected_uids
            else [dragged_uid]
        )
        usd_paths = {}
        for uid in drag_uids:
            w = self._thumbnail_widgets.get(uid)
            if w and w.usd_path:
                usd_paths[uid] = w.usd_path

        card = self._thumbnail_widgets.get(dragged_uid)
        if card:
            card.start_drag(usd_paths)

    def _on_card_clicked(self, uid: str, modifiers: QtCore.Qt.KeyboardModifiers):
        ctrl = bool(modifiers & QtCore.Qt.ControlModifier)
        shift = bool(modifiers & QtCore.Qt.ShiftModifier)

        # Click-to-pin: if the hover popup is showing this asset and the
        # user enables pin-on-click in TurntableTab, pin it.
        tt = TurntableTab.read_turntable_settings(self._db)
        if (tt.get("hover_pin_on_click")
                and self._hover_popup.isVisible()
                and self._hover_popup.current_uid == uid):
            self._hover_popup.set_pinned(True)

        if ctrl:
            if uid in self._selected_uids:
                self._selected_uids.discard(uid)
                if uid in self._thumbnail_widgets:
                    self._thumbnail_widgets[uid].set_selected(False)
            else:
                self._selected_uids.add(uid)
                if uid in self._thumbnail_widgets:
                    self._thumbnail_widgets[uid].set_selected(True)
            self._anchor_uid = uid
        elif shift and self._anchor_uid:
            keys = list(self._thumbnail_widgets.keys())
            try:
                a = keys.index(self._anchor_uid)
                b = keys.index(uid)
            except ValueError:
                a, b = 0, len(keys) - 1
            lo, hi = min(a, b), max(a, b)
            for u, w in self._thumbnail_widgets.items():
                w.set_selected(False)
            self._selected_uids.clear()
            for u in keys[lo:hi + 1]:
                self._selected_uids.add(u)
                self._thumbnail_widgets[u].set_selected(True)
        else:
            if uid in self._selected_uids and len(self._selected_uids) == 1:
                self._selected_uids.discard(uid)
                if uid in self._thumbnail_widgets:
                    self._thumbnail_widgets[uid].set_selected(False)
                self._anchor_uid = None
            else:
                for u in list(self._selected_uids):
                    if u in self._thumbnail_widgets:
                        self._thumbnail_widgets[u].set_selected(False)
                self._selected_uids.clear()
                self._selected_uids.add(uid)
                if uid in self._thumbnail_widgets:
                    self._thumbnail_widgets[uid].set_selected(True)
                self._anchor_uid = uid

        self._update_insert_buttons()

    def _on_card_double_clicked(self, uid: str):
        """Double-click opens turntable sequence if available, else static preview."""
        asset = self._db.get_asset(uid)
        if not asset:
            return
        thumb_path = self._resolve_thumbnail_path(asset)
        tt_dir = find_turntable_dir(asset.name, thumb_path, asset.usd_output_path)
        frames = list_turntable_frames(tt_dir)

        if frames:
            # Open full turntable sequence viewer with metadata
            from .hover_preview import TurntableSequenceDialog
            tt = TurntableTab.read_turntable_settings(self._db)
            dlg = TurntableSequenceDialog(
                asset_name=asset.name,
                frames=frames,
                fps=int(tt.get("fps", 24)),
                loop_mode=tt.get("loop_mode", "loop"),
                frames_per_cycle=int(tt.get("frames_per_cycle", 72)),
                num_hdris=max(1, len(tt.get("hdris", [])) or 1),
                thumb_path=thumb_path,
                usd_path=asset.usd_output_path or "",
                status=asset.status or "",
                parent=self,
            )
            dlg.exec()
        else:
            # Fallback to static preview
            self._open_preview(uid)

    def _on_card_context_menu(self, uid: str, global_pos: QtCore.QPoint):
        """Show context menu for an asset card."""
        menu = QtWidgets.QMenu(self)

        if uid in self._selected_uids and len(self._selected_uids) > 1:
            target_uids = list(self._selected_uids)
            n = len(target_uids)
            multi = True
        else:
            target_uids = [uid]
            n = 1
            multi = False

        if not multi:
            menu.addAction("Preview", lambda: self._open_preview(uid))
            menu.addSeparator()

        ref_label = f"Insert {n} as Reference" if multi else "Insert as Reference"
        pay_label = f"Insert {n} as Payload" if multi else "Insert as Payload"
        menu.addAction(ref_label,
                       lambda t=target_uids: self._insert_assets_to_scene(t, False))
        menu.addAction(pay_label,
                       lambda t=target_uids: self._insert_assets_to_scene(t, True))
        menu.addSeparator()

        # Turntable processing — works for single OR multi selection.
        tt_label = (f"Process Turntable ({n})"
                    if multi else "Process Turntable")
        menu.addAction(tt_label,
                       lambda t=target_uids: self._process_turntables(t))
        menu.addSeparator()

        if not multi:
            menu.addAction("Re-render Thumbnail",
                           lambda: self._rerender_thumbnail(uid))
            menu.addAction("Open Source Directory",
                           lambda: self._open_source_dir(uid))
            menu.addSeparator()
            menu.addAction("Remove from Database",
                           lambda: self._remove_asset(uid))

        menu.exec(global_pos)

    def _open_preview(self, uid: str):
        """Open a larger preview window with the thumbnail + render info."""
        asset = self._db.get_asset(uid)
        if not asset:
            return
        thumb_path = self._resolve_thumbnail_path(asset)
        dlg = ThumbnailPreviewDialog(asset, thumb_path, parent=self)
        dlg.exec()

    # ──────────────────────────────────────────────
    # Scene integration
    # ──────────────────────────────────────────────

    def _insert_assets_to_scene(self, uids, as_payload: bool = False):
        """Create Reference or Payload LOPs in the active Solaris network."""
        if not HAS_HOU:
            QtWidgets.QMessageBox.warning(
                self, "Not in Houdini",
                "Scene insertion requires running inside Houdini."
            )
            return

        net_editor = None
        for pane in hou.ui.paneTabs():
            if (pane.type() == hou.paneTabType.NetworkEditor
                    and pane.isCurrentTab()):
                net_editor = pane
                break
        if net_editor is None:
            for pane in hou.ui.paneTabs():
                if pane.type() == hou.paneTabType.NetworkEditor:
                    net_editor = pane
                    break
        if net_editor is None:
            QtWidgets.QMessageBox.warning(
                self, "No Network Editor",
                "Cannot find a Network Editor pane."
            )
            return

        parent = net_editor.pwd()
        if parent.childTypeCategory() != hou.lopNodeTypeCategory():
            stage = hou.node("/stage")
            if stage is None:
                QtWidgets.QMessageBox.warning(
                    self, "Not in LOPs",
                    "Open a Solaris (/stage) network before inserting assets."
                )
                return
            parent = stage

        try:
            cursor_pos = net_editor.cursorPosition()
        except Exception:
            cursor_pos = hou.Vector2(0, 0)

        created = []
        for i, uid in enumerate(uids):
            asset = self._db.get_asset(uid)
            if not asset or not asset.usd_output_path:
                continue

            safe_name = "".join(c if c.isalnum() or c == "_" else "_"
                                for c in asset.name) or "asset"
            try:
                node = parent.createNode("reference", safe_name)
            except hou.OperationFailed as e:
                QtWidgets.QMessageBox.critical(
                    self, "Error",
                    f"Failed to create reference LOP:\n{e}"
                )
                continue

            for parm_name, value in (
                ("filepath1", asset.usd_output_path),
                ("primpath1", f"/{safe_name}"),
            ):
                p = node.parm(parm_name)
                if p is not None:
                    try:
                        p.set(value)
                    except Exception:
                        pass

            if as_payload:
                for parm_name in ("reftype1", "reftype"):
                    p = node.parm(parm_name)
                    if p is None:
                        continue
                    try:
                        p.set("payload")
                    except hou.TypeError:
                        try:
                            p.set(1)
                        except Exception:
                            pass
                    break

            node.setPosition(hou.Vector2(cursor_pos[0] + i * 2.5, cursor_pos[1]))
            created.append(node)

        if not created:
            return
        try:
            created[-1].setDisplayFlag(True)
        except Exception:
            pass
        try:
            created[-1].setCurrent(True, clear_all_selected=True)
            for node in created[:-1]:
                node.setCurrent(True, clear_all_selected=False)
        except Exception:
            pass

    def _on_insert_reference(self):
        if self._selected_uids:
            self._insert_assets_to_scene(list(self._selected_uids), False)

    def _on_insert_payload(self):
        if self._selected_uids:
            self._insert_assets_to_scene(list(self._selected_uids), True)

    def _on_turntable_selected(self):
        if self._selected_uids:
            self._process_turntables(list(self._selected_uids))

    def _on_rerender_selected(self):
        """Re-render thumbnails for all selected assets."""
        if not self._selected_uids:
            return
        uids = list(self._selected_uids)
        n = len(uids)
        reply = QtWidgets.QMessageBox.question(
            self, "Re-render Thumbnails",
            f"Re-render {n} thumbnail(s)?\n\n"
            f"Current render settings (renderer, samples, "
            f"resolution, etc.) will be applied.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        succeeded = 0
        failed = []
        for asset_uid in uids:
            try:
                self._rerender_thumbnail(asset_uid)
                succeeded += 1
            except Exception as e:
                failed.append(self._db.get_asset(asset_uid).name if self._db.get_asset(asset_uid) else asset_uid)
                print(f"[Gallery] rerender failed for {asset_uid}: {e}")

        summary = f"Re-rendered {succeeded}/{n} thumbnail(s)."
        if failed:
            summary += f"\nFailed: {', '.join(failed)}"
        QtWidgets.QMessageBox.information(self, "Complete", summary)

    def _rerender_thumbnail(self, uid: str):
        """Re-render the thumbnail using the SAME pipeline as the initial
        build: ComponentBuilder._render_thumbnail_karma against the
        already-exported USD. Reads render settings (renderer/samples/
        yaw/pitch/distance/resolution/HDRI) from the DB meta keys so the
        UI's current values are respected."""
        asset = self._db.get_asset(uid)
        if not asset or not asset.usd_output_path:
            print(f"[Gallery] cannot re-render: asset has no USD path")
            return
        if not os.path.exists(asset.usd_output_path):
            print(f"[Gallery] cannot re-render: USD missing at "
                  f"{asset.usd_output_path}")
            return

        from ..core.component_builder import ComponentBuilder

        def _f(key, default):
            try:
                v = self._db.get_meta(key, "")
                return float(v) if v not in (None, "") else default
            except Exception:
                return default
        def _i(key, default):
            try:
                v = self._db.get_meta(key, "")
                return int(v) if v not in (None, "") else default
            except Exception:
                return default
        renderer_key = (self._db.get_meta("renderer", "karma_cpu")
                        or "karma_cpu")
        hdri_path = ""
        try:
            hdri_path = self._db.get_meta("thumbnail_hdri", "") or ""
        except Exception:
            pass

        builder = ComponentBuilder(
            renderer=renderer_key,
            hdri_path=hdri_path,
            thumbnail_resolution=(_i("thumb_res_x", 640),
                                  _i("thumb_res_y", 480)),
            asset_scale=_f("asset_scale", 1.0),
            camera_yaw=_f("camera_yaw", 35.0),
            camera_pitch=_f("camera_pitch", 20.0),
            thumb_distance=_f("thumb_distance", 0.0),
            karma_samples=_i("karma_samples", 64),
            camera_focal=_f("cam_focal", 40.0),
            camera_aperture=_f("cam_aperture", 25.0),
            camera_near=_f("cam_near", 0.1),
            camera_far=_f("cam_far", 1_000_000.0),
        )

        # Always use the active project's configured thumbnail directory
        active = self._db.get_active_project() or {}
        thumb_dir = active.get("thumbnail_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager",
            "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)

        # Recover texture info from the asset's material_info so the
        # render metadata sidecar can carry it forward to the gallery
        # overlay/tooltip.
        tex_info = {}
        try:
            ts = asset.material_info.texture_set
            if ts:
                tex_info = ts.get_populated_maps()
        except Exception:
            tex_info = {}

        try:
            new_thumb = builder.render_thumbnail_only(
                asset.usd_output_path, asset.name, thumb_dir,
                texture_info=tex_info,
                source_dir=asset.source_texture_dir,
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[Gallery] re-render failed: {e}")
            return

        if new_thumb and os.path.exists(new_thumb):
            asset.thumbnail_path = new_thumb
            self._db.update_asset(asset)
            if uid in self._thumbnail_widgets:
                self._thumbnail_widgets[uid].update_thumbnail(new_thumb)
            print(f"[Gallery] re-rendered {asset.name} → {new_thumb}")
        else:
            print(f"[Gallery] re-render produced no PNG for {asset.name}")

    def _process_turntables(self, uids: "list[str]"):
        """Render turntables for one or more selected assets.

        Builds the ComponentBuilder once (settings are global) and
        iterates over each asset sequentially. Logs per-asset progress;
        failures on one asset don't stop the rest.
        """
        if not uids:
            return

        tt = TurntableTab.read_turntable_settings(self._db)
        hdri_path = self._db.get_meta("thumbnail_hdri", "") or ""
        if not tt.get("hdris") and not hdri_path:
            QtWidgets.QMessageBox.warning(
                self, "No HDRI Configured",
                "Add at least one HDRI in the Turn Table tab "
                "(or set a Thumbnail HDRI in Settings) before "
                "processing a turntable.",
            )
            return

        # Collect valid (asset, thumbnail_dir) pairs upfront so we can
        # confirm the batch size and skip broken entries.
        # Always use the active project's configured thumbnail directory
        active = self._db.get_active_project() or {}
        thumb_dir = active.get("thumbnail_dir") or os.path.join(
            os.path.expanduser("~"), "houdini", "asset_manager",
            "thumbnails")
        os.makedirs(thumb_dir, exist_ok=True)
        # Turntables render into turntable/ subfolder
        turntable_dir = os.path.join(thumb_dir, "turntable")
        os.makedirs(turntable_dir, exist_ok=True)

        targets = []
        for uid in uids:
            asset = self._db.get_asset(uid)
            if not asset or not asset.usd_output_path:
                print(f"[Gallery] skip {uid}: no USD path")
                continue
            if not os.path.exists(asset.usd_output_path):
                print(f"[Gallery] skip {asset.name}: USD missing at "
                      f"{asset.usd_output_path}")
                continue
            targets.append((asset, turntable_dir))

        if not targets:
            QtWidgets.QMessageBox.information(
                self, "No Assets to Process",
                "None of the selected assets have a built USD ready "
                "for turntable rendering.",
            )
            return

        # Confirm — turntables can take a long time per asset.
        n = len(targets)
        n_cycles = max(1, len(tt.get("hdris") or [hdri_path]))
        per_asset_frames = n_cycles * int(tt.get("frames_per_cycle", 72))
        reply = QtWidgets.QMessageBox.question(
            self, "Process Turntables",
            f"Render turntables for {n} asset(s)?\n\n"
            f"Each asset: {n_cycles} HDRI cycle(s) × "
            f"{tt.get('frames_per_cycle', 72)} frames = "
            f"{per_asset_frames} frames.\n"
            f"Total frames to render: {n * per_asset_frames}.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if reply != QtWidgets.QMessageBox.Yes:
            return

        from ..core.component_builder import ComponentBuilder

        renderer_key = (self._db.get_meta("renderer", "karma_cpu")
                        or "karma_cpu")

        def _f(key, default):
            try:
                v = self._db.get_meta(key, "")
                return float(v) if v not in (None, "") else default
            except Exception:
                return default

        def _i(key, default):
            try:
                v = self._db.get_meta(key, "")
                return int(v) if v not in (None, "") else default
            except Exception:
                return default

        builder = ComponentBuilder(
            renderer=renderer_key,
            hdri_path=hdri_path,
            thumbnail_resolution=(_i("thumb_res_x", 640),
                                  _i("thumb_res_y", 480)),
            asset_scale=_f("asset_scale", 1.0),
            camera_focal=_f("cam_focal", 40.0),
            camera_aperture=_f("cam_aperture", 25.0),
            camera_near=_f("cam_near", 0.1),
            camera_far=_f("cam_far", 1_000_000.0),
            turntable_settings=tt,
        )

        try:
            import hou
            _OperationInterrupted = hou.OperationInterrupted
        except Exception:
            class _OperationInterrupted(Exception):
                pass

        succeeded = 0
        failed = []
        interrupted = False
        for i, (asset, thumb_dir) in enumerate(targets, start=1):
            print(f"[Gallery] turntable {i}/{n}: {asset.name}")
            try:
                tt_dir = builder.render_turntable_only(
                    asset.usd_output_path, asset.name, thumb_dir,
                )
                if tt_dir and os.path.isdir(tt_dir):
                    succeeded += 1
                    print(f"[Gallery] {asset.name} → {tt_dir}")
                else:
                    failed.append(asset.name)
                    print(f"[Gallery] {asset.name}: produced no frames")
            except _OperationInterrupted as ie:
                # User clicked Interrupt — stop the whole queue.
                interrupted = True
                print(f"[Gallery] interrupted at {asset.name}: {ie}")
                break
            except Exception as e:
                import traceback
                traceback.print_exc()
                failed.append(asset.name)
                print(f"[Gallery] {asset.name}: failed — {e}")

        summary = f"Processed {succeeded}/{n} turntable(s)."
        if interrupted:
            summary += "\nQueue interrupted by user."
        if failed:
            summary += f"\nFailed: {', '.join(failed)}"
        QtWidgets.QMessageBox.information(
            self, "Turntables Complete", summary,
        )

    def _open_source_dir(self, uid: str):
        """Open the source directory in the file explorer."""
        asset = self._db.get_asset(uid)
        if asset and asset.source_texture_dir:
            path = os.path.normpath(asset.source_texture_dir)
            if os.path.isdir(path):
                os.startfile(path)

    def _on_delete_all(self):
        """Clear every asset from the database (USD files on disk are kept)."""
        count = self._db.count
        if count == 0:
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Delete All Assets",
            f"Remove all {count} assets from the database?\n"
            "(USD files on disk will not be deleted.)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._db.clear_assets()
            self.refresh_gallery()

    def _remove_asset(self, uid: str):
        """Remove an asset from the database."""
        reply = QtWidgets.QMessageBox.question(
            self, "Remove Asset",
            "Remove this asset from the database?\n"
            "(USD files on disk will not be deleted.)",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        if reply == QtWidgets.QMessageBox.Yes:
            self._db.remove_asset(uid)
            self.refresh_gallery()


# ────────────────────────────────────────────────────────
# Flow Layout (grid that wraps to fit container width)
# ────────────────────────────────────────────────────────

class FlowLayout(QtWidgets.QLayout):
    """
    A Qt layout that arranges widgets in a flow (left-to-right, wrapping).
    Used for the thumbnail grid so cards reflow when the panel is resized.
    """

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setContentsMargins(margin, margin, margin, margin)
        if spacing >= 0:
            self._spacing = spacing
        else:
            self._spacing = 8
        self._items = []

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect, test_only=False):
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is None or widget.isHidden():
                continue
            space_x = self._spacing
            space_y = self._spacing
            item_size = item.sizeHint()

            next_x = x + item_size.width() + space_x
            if next_x - space_x > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QtCore.QRect(
                    QtCore.QPoint(x, y), item_size
                ))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + m.bottom()


class ThumbnailPreviewDialog(QtWidgets.QDialog):
    """Larger preview window showing the thumbnail at native size plus
    the render metadata sidecar's contents (engine, samples, resolution,
    HDRI, etc.) — opened by double-click or right-click → Preview."""

    def __init__(self, asset: AssetEntry, thumb_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Preview — {asset.name}")
        # Sized to fit a typical 1080p screen with room to grow.
        # The dialog is freely resizable and the splitter inside lets
        # the user trade image area for metadata area.
        self.resize(960, 880)
        self.setSizeGripEnabled(True)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Image ──
        # Source pixmap kept around so resizeEvent can rescale on demand
        # without losing quality from repeated downscales.
        self._source_pixmap = None
        if thumb_path and os.path.exists(thumb_path):
            pm = QtGui.QPixmap(thumb_path)
            if not pm.isNull():
                self._source_pixmap = pm

        self._img_label = QtWidgets.QLabel()
        self._img_label.setAlignment(QtCore.Qt.AlignCenter)
        self._img_label.setSizePolicy(
            QtWidgets.QSizePolicy.Ignored,
            QtWidgets.QSizePolicy.Ignored,
        )
        self._img_label.setMinimumSize(200, 200)
        self._img_label.setStyleSheet(
            f"background-color: {COLORS['bg_darkest']};"
        )
        if self._source_pixmap is None:
            self._img_label.setText(
                "(no thumbnail available)" if not thumb_path
                else "(failed to load image)"
            )

        # ── Render Info ──
        info = self._load_render_info(thumb_path, asset)
        info_text = QtWidgets.QPlainTextEdit()
        info_text.setReadOnly(True)
        info_text.setMinimumHeight(80)
        info_text.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        info_text.setPlainText(self._format_info(info))
        info_text.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {COLORS['bg_darkest']}; "
            f"color: {COLORS['text_secondary']}; font-family: Consolas, monospace; "
            f"font-size: 11px; }}"
        )

        # ── Splitter: image on top, info on bottom, draggable handle ──
        self._splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._splitter.addWidget(self._img_label)
        self._splitter.addWidget(info_text)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(6)
        self._splitter.setSizes([620, 200])
        self._splitter.splitterMoved.connect(
            lambda *_: self._rescale_image())
        layout.addWidget(self._splitter, 1)

        # ── Close ──
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # Initial render at current geometry.
        QtCore.QTimer.singleShot(0, self._rescale_image)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale_image()

    def _rescale_image(self):
        """Re-fit the source pixmap into the current image area."""
        if not self._source_pixmap or self._source_pixmap.isNull():
            return
        target = self._img_label.size()
        if target.width() <= 1 or target.height() <= 1:
            return
        scaled = self._source_pixmap.scaled(
            target,
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self._img_label.setPixmap(scaled)

    @staticmethod
    def _load_render_info(thumb_path: str, asset: AssetEntry) -> dict:
        """Read the JSON sidecar next to the thumbnail if present, else
        fall back to whatever's on the AssetEntry."""
        info = {}
        if thumb_path:
            sidecar = os.path.splitext(thumb_path)[0] + ".json"
            if os.path.exists(sidecar):
                try:
                    import json as _json
                    with open(sidecar, "r", encoding="utf-8") as f:
                        info = _json.load(f)
                except Exception as e:
                    info["sidecar_error"] = str(e)
        # Augment with AssetEntry fields the sidecar may not have.
        info.setdefault("asset_name", asset.name)
        info.setdefault("usd_output_path", asset.usd_output_path)
        info.setdefault("source_geo_path", asset.source_geo_path)
        info.setdefault("status", asset.status)
        return info

    @staticmethod
    def _format_info(info: dict) -> str:
        # Stable display order — most-relevant fields first.
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
        seen = set()
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
        # Texture map list gets its own block.
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
