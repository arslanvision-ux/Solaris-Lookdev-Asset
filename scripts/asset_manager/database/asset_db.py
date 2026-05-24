"""
SQLite-backed asset database for the Asset Manager.
Supports full CRUD, search/filter, and maintains backward-compatible
JSON export/import for portability.
"""

import os
import json
import sqlite3
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from contextlib import contextmanager

from .models import AssetEntry, TextureSet, MaterialInfo


# ────────────────────────────────────────────────────────
# Schema
# ────────────────────────────────────────────────────────
_SCHEMA_VERSION = 1

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    uid                 TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT DEFAULT '',
    description         TEXT DEFAULT '',
    tags                TEXT DEFAULT '[]',

    -- Source paths
    source_geo_path     TEXT DEFAULT '',
    source_texture_dir  TEXT DEFAULT '',

    -- Generated USD layers
    usd_output_path     TEXT DEFAULT '',
    render_geo_path     TEXT DEFAULT '',
    proxy_geo_path      TEXT DEFAULT '',
    sim_geo_path        TEXT DEFAULT '',
    material_layer_path TEXT DEFAULT '',

    -- Material (stored as JSON blob)
    material_info       TEXT DEFAULT '{}',

    -- Thumbnail
    thumbnail_path      TEXT DEFAULT '',

    -- Processing
    renderer            TEXT DEFAULT 'karma',
    status              TEXT DEFAULT 'pending',
    error_message       TEXT DEFAULT '',
    proxy_ratio         REAL DEFAULT 0.1,
    sim_method          TEXT DEFAULT 'convex_hull',

    -- Timestamps
    date_created        TEXT DEFAULT '',
    date_modified       TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_assets_name     ON assets(name);
CREATE INDEX IF NOT EXISTS idx_assets_status   ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_renderer ON assets(renderer);
CREATE INDEX IF NOT EXISTS idx_assets_category ON assets(category);

CREATE TABLE IF NOT EXISTS projects (
    alias           TEXT PRIMARY KEY,
    project_path    TEXT NOT NULL,
    output_dir      TEXT DEFAULT '',
    thumbnail_dir   TEXT DEFAULT '',
    renderer        TEXT DEFAULT 'karma',
    date_created    TEXT DEFAULT '',
    date_modified   TEXT DEFAULT '',
    is_active       INTEGER DEFAULT 0
);
"""


class AssetDatabase:
    """
    SQLite-based asset database with thread-safe operations.
    Each project gets its own database file stored in the project output directory.
    """

    def __init__(self, db_path: str):
        """
        Initialize the asset database.

        Args:
            db_path: Path to the SQLite database file (.db).
        """
        self._db_path = db_path
        self._lock = threading.Lock()
        self._ensure_db_directory()
        self._init_db()

    # ──────────────────────────────────────────────
    # Connection Management
    # ──────────────────────────────────────────────

    def _ensure_db_directory(self):
        """Create the database directory if it doesn't exist."""
        db_dir = os.path.dirname(self._db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

    @contextmanager
    def _connect(self):
        """Thread-safe database connection context manager."""
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._lock:
            with self._connect() as conn:
                conn.executescript(_CREATE_TABLES)
                # Set schema version
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    ("schema_version", str(_SCHEMA_VERSION))
                )

    # ──────────────────────────────────────────────
    # Serialization helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _asset_to_row(asset: AssetEntry) -> dict:
        """Convert an AssetEntry to a dict suitable for SQLite insertion."""
        return {
            "uid": asset.uid,
            "name": asset.name,
            "category": asset.category,
            "description": asset.description,
            "tags": json.dumps(asset.tags),
            "source_geo_path": asset.source_geo_path,
            "source_texture_dir": asset.source_texture_dir,
            "usd_output_path": asset.usd_output_path,
            "render_geo_path": asset.render_geo_path,
            "proxy_geo_path": asset.proxy_geo_path,
            "sim_geo_path": asset.sim_geo_path,
            "material_layer_path": asset.material_layer_path,
            "material_info": json.dumps(asset.material_info.to_dict()),
            "thumbnail_path": asset.thumbnail_path,
            "renderer": asset.renderer,
            "status": asset.status,
            "error_message": asset.error_message,
            "proxy_ratio": asset.proxy_ratio,
            "sim_method": asset.sim_method,
            "date_created": asset.date_created,
            "date_modified": asset.date_modified,
        }

    @staticmethod
    def _row_to_asset(row: sqlite3.Row) -> AssetEntry:
        """Convert a SQLite Row to an AssetEntry."""
        d = dict(row)
        # Deserialize JSON fields
        d["tags"] = json.loads(d.get("tags", "[]"))
        mat_data = json.loads(d.get("material_info", "{}"))
        d.pop("material_info", None)

        asset = AssetEntry(**{k: v for k, v in d.items()
                              if k in AssetEntry.__dataclass_fields__})
        asset.material_info = MaterialInfo.from_dict(mat_data) if mat_data else MaterialInfo()
        return asset

    # ──────────────────────────────────────────────
    # CRUD – Assets
    # ──────────────────────────────────────────────

    def add_asset(self, asset: AssetEntry) -> str:
        """
        Upsert an asset by name. If an entry with the same name already
        exists (including ones with old timestamp-based UIDs from prior
        runs), it is replaced and the original date_created is preserved.

        Returns:
            The UID of the upserted asset.
        """
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT uid, date_created FROM assets WHERE name = ?",
                    (asset.name,),
                ).fetchone()
                if existing:
                    # Preserve the original creation date across re-processes.
                    asset.date_created = existing["date_created"]
                    # If the stored UID differs (old timestamp-based UID),
                    # remove the stale row so we don't end up with duplicates
                    # after the INSERT OR REPLACE below.
                    if existing["uid"] != asset.uid:
                        conn.execute(
                            "DELETE FROM assets WHERE uid = ?",
                            (existing["uid"],),
                        )
                asset.date_modified = datetime.now().isoformat()
                row = self._asset_to_row(asset)
                cols = ", ".join(row.keys())
                placeholders = ", ".join(["?"] * len(row))
                conn.execute(
                    f"INSERT OR REPLACE INTO assets ({cols}) VALUES ({placeholders})",
                    list(row.values()),
                )
        return asset.uid

    def get_asset(self, uid: str) -> Optional[AssetEntry]:
        """Get an asset by its UID."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM assets WHERE uid = ?", (uid,))
            row = cursor.fetchone()
            return self._row_to_asset(row) if row else None

    def get_asset_by_name(self, name: str) -> Optional[AssetEntry]:
        """Get the first asset matching the given name."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE name = ? LIMIT 1", (name,)
            )
            row = cursor.fetchone()
            return self._row_to_asset(row) if row else None

    def update_asset(self, asset: AssetEntry):
        """Update an existing asset in the database."""
        asset.update_modified()
        row = self._asset_to_row(asset)
        sets = ", ".join([f"{k} = ?" for k in row.keys() if k != "uid"])
        vals = [v for k, v in row.items() if k != "uid"]
        vals.append(asset.uid)

        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE assets SET {sets} WHERE uid = ?", vals
                )

    def remove_asset(self, uid: str) -> bool:
        """
        Remove an asset from the database by UID.

        Returns:
            True if the asset was found and removed.
        """
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute("DELETE FROM assets WHERE uid = ?", (uid,))
                return cursor.rowcount > 0

    def clear_assets(self):
        """Remove all assets from the database."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("DELETE FROM assets")

    # ──────────────────────────────────────────────
    # CRUD – Projects
    # ──────────────────────────────────────────────

    def add_project(self, alias: str, project_path: str, output_dir: str = "",
                    thumbnail_dir: str = "", renderer: str = "karma"):
        """Register a new project alias."""
        now = datetime.now().isoformat()
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO projects
                       (alias, project_path, output_dir, thumbnail_dir,
                        renderer, date_created, date_modified, is_active)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
                    (alias, project_path, output_dir, thumbnail_dir,
                     renderer, now, now)
                )

    def get_project(self, alias: str) -> Optional[dict]:
        """Get a project by its alias."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM projects WHERE alias = ?", (alias,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_active_project(self) -> Optional[dict]:
        """Get the currently active project."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM projects WHERE is_active = 1 LIMIT 1"
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def set_active_project(self, alias: str):
        """Set a project as the active one (deactivates others)."""
        with self._lock:
            with self._connect() as conn:
                conn.execute("UPDATE projects SET is_active = 0")
                conn.execute(
                    "UPDATE projects SET is_active = 1 WHERE alias = ?",
                    (alias,)
                )

    def get_all_projects(self) -> List[dict]:
        """Return all registered projects."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM projects ORDER BY alias")
            return [dict(row) for row in cursor.fetchall()]

    def remove_project(self, alias: str) -> bool:
        """Remove a project by alias."""
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM projects WHERE alias = ?", (alias,)
                )
                return cursor.rowcount > 0

    # ──────────────────────────────────────────────
    # Query / Filter
    # ──────────────────────────────────────────────

    def get_all_assets(self) -> List[AssetEntry]:
        """Return a list of all assets."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT * FROM assets ORDER BY name")
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def get_ready_assets(self) -> List[AssetEntry]:
        """Return only assets with status 'ready'."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE status = 'ready' ORDER BY name"
            )
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def search(self, query: str) -> List[AssetEntry]:
        """
        Search assets by name, tags, or category (case-insensitive).

        Args:
            query: Search string.

        Returns:
            List of matching AssetEntry objects.
        """
        pattern = f"%{query}%"
        with self._connect() as conn:
            cursor = conn.execute(
                """SELECT * FROM assets
                   WHERE name LIKE ? OR category LIKE ? OR tags LIKE ?
                   ORDER BY name""",
                (pattern, pattern, pattern)
            )
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def filter_by_status(self, status: str) -> List[AssetEntry]:
        """Filter assets by processing status."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE status = ? ORDER BY name",
                (status,)
            )
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def filter_by_renderer(self, renderer: str) -> List[AssetEntry]:
        """Filter assets by renderer type."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE renderer = ? ORDER BY name",
                (renderer,)
            )
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def filter_by_category(self, category: str) -> List[AssetEntry]:
        """Filter assets by category."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT * FROM assets WHERE category = ? ORDER BY name",
                (category,)
            )
            return [self._row_to_asset(row) for row in cursor.fetchall()]

    def get_categories(self) -> List[str]:
        """Return a list of unique categories."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT DISTINCT category FROM assets WHERE category != '' ORDER BY category"
            )
            return [row["category"] for row in cursor.fetchall()]

    def get_all_tags(self) -> List[str]:
        """Return a sorted list of unique tags across all assets."""
        tags = set()
        with self._connect() as conn:
            cursor = conn.execute("SELECT tags FROM assets")
            for row in cursor.fetchall():
                try:
                    tag_list = json.loads(row["tags"])
                    tags.update(tag_list)
                except (json.JSONDecodeError, TypeError):
                    pass
        return sorted(tags)

    # ──────────────────────────────────────────────
    # Statistics
    # ──────────────────────────────────────────────

    @property
    def count(self) -> int:
        """Total number of assets in the database."""
        with self._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM assets")
            return cursor.fetchone()["cnt"]

    @property
    def db_path(self) -> str:
        """Path to the database file."""
        return self._db_path

    def get_stats(self) -> dict:
        """Return statistics about the database."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) as c FROM assets").fetchone()["c"]

            status_rows = conn.execute(
                "SELECT status, COUNT(*) as c FROM assets GROUP BY status"
            ).fetchall()
            statuses = {r["status"]: r["c"] for r in status_rows}

            renderer_rows = conn.execute(
                "SELECT renderer, COUNT(*) as c FROM assets GROUP BY renderer"
            ).fetchall()
            renderers = {r["renderer"]: r["c"] for r in renderer_rows}

            cat_count = conn.execute(
                "SELECT COUNT(DISTINCT category) as c FROM assets WHERE category != ''"
            ).fetchone()["c"]

        return {
            "total": total,
            "statuses": statuses,
            "renderers": renderers,
            "categories": cat_count,
            "tags": len(self.get_all_tags()),
        }

    # ──────────────────────────────────────────────
    # Import / Export (JSON compatibility)
    # ──────────────────────────────────────────────

    def export_to_json(self, export_path: str):
        """Export the full database to a JSON file."""
        assets = self.get_all_assets()
        data = {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "asset_count": len(assets),
            "assets": {a.uid: a.to_dict() for a in assets}
        }
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_from_json(self, json_path: str) -> int:
        """
        Import assets from a JSON file (merges into existing DB).

        Returns:
            Number of assets imported.
        """
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        count = 0
        for uid, entry_data in data.get("assets", {}).items():
            try:
                asset = AssetEntry.from_dict(entry_data)
                self.add_asset(asset)
                count += 1
            except Exception as e:
                print(f"[AssetDB] Warning: Failed to import asset '{uid}': {e}")
        return count

    def reload(self):
        """No-op for SQLite (always reads from disk). Kept for API compat."""
        pass

    # ──────────────────────────────────────────────
    # Generic key/value settings (uses `meta` table)
    # ──────────────────────────────────────────────

    def set_meta(self, key: str, value: str):
        """Persist an arbitrary string setting (e.g. thumbnail HDRI path)."""
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                    (key, value),
                )

    def get_meta(self, key: str, default: str = "") -> str:
        """Read a setting written by `set_meta`."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            )
            row = cursor.fetchone()
            return row["value"] if row else default
