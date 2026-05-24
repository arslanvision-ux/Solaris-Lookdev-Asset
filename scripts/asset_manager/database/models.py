"""
Data models for the Asset Manager.
Defines the core data structures used throughout the pipeline.
"""

import os
import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
from datetime import datetime


@dataclass
class TextureSet:
    """Represents a complete set of PBR textures for a single material."""
    base_color: str = ""
    roughness: str = ""
    metallic: str = ""
    normal: str = ""
    displacement: str = ""
    opacity: str = ""
    emissive: str = ""
    ao: str = ""

    def has_textures(self) -> bool:
        """Returns True if at least one texture slot is populated."""
        return any([
            self.base_color, self.roughness, self.metallic,
            self.normal, self.displacement, self.opacity,
            self.emissive, self.ao
        ])

    def get_populated_maps(self) -> Dict[str, str]:
        """Returns a dictionary of only the populated texture maps."""
        result = {}
        for map_type in [
            "base_color", "roughness", "metallic", "normal",
            "displacement", "opacity", "emissive", "ao"
        ]:
            path = getattr(self, map_type, "")
            if path:
                result[map_type] = path
        return result

    def get_map_count(self) -> int:
        """Returns the number of populated texture maps."""
        return len(self.get_populated_maps())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TextureSet":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MaterialInfo:
    """Information about a generated material for an asset."""
    name: str = ""
    material_path: str = ""         # USD prim path of the material
    material_layer_path: str = ""   # File path to the material USD layer
    renderer: str = "karma"
    texture_set: TextureSet = field(default_factory=TextureSet)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["texture_set"] = self.texture_set.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "MaterialInfo":
        tex_data = data.pop("texture_set", {})
        info = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        info.texture_set = TextureSet.from_dict(tex_data) if tex_data else TextureSet()
        return info


@dataclass
class AssetEntry:
    """
    Represents a single asset in the database.
    Contains all metadata about the asset's files, materials, and processing state.
    """
    # Identity
    name: str = ""
    uid: str = ""

    # Source files
    source_geo_path: str = ""
    source_texture_dir: str = ""

    # Generated USD files
    usd_output_path: str = ""       # Final composed asset USD
    render_geo_path: str = ""       # Render-purpose geometry layer
    proxy_geo_path: str = ""        # Proxy-purpose geometry layer
    sim_geo_path: str = ""          # Simulation geometry layer
    material_layer_path: str = ""   # MaterialX material layer

    # Material info
    material_info: MaterialInfo = field(default_factory=MaterialInfo)

    # Thumbnail
    thumbnail_path: str = ""

    # Metadata
    tags: List[str] = field(default_factory=list)
    category: str = ""
    description: str = ""
    date_created: str = ""
    date_modified: str = ""
    renderer: str = "karma"

    # Processing state
    status: str = "pending"         # pending | scanning | processing | ready | error
    error_message: str = ""

    # Proxy settings used
    proxy_ratio: float = 0.1        # PolyReduce keep ratio
    sim_method: str = "convex_hull" # convex_hull | vdb_remesh | decimated

    def __post_init__(self):
        if not self.uid:
            import hashlib
            # UID is derived from name only — no timestamp — so the same
            # asset always maps to the same UID across re-process runs.
            # INSERT OR REPLACE in add_asset therefore updates in place
            # instead of creating duplicate rows.
            self.uid = hashlib.md5(self.name.lower().strip().encode()).hexdigest()[:12]
        if not self.date_created:
            self.date_created = datetime.now().isoformat()
        if not self.date_modified:
            self.date_modified = self.date_created

    def update_modified(self):
        """Update the modification timestamp."""
        self.date_modified = datetime.now().isoformat()

    def is_ready(self) -> bool:
        return self.status == "ready"

    def has_proxy(self) -> bool:
        return bool(self.proxy_geo_path) and os.path.exists(self.proxy_geo_path)

    def has_sim(self) -> bool:
        return bool(self.sim_geo_path) and os.path.exists(self.sim_geo_path)

    def has_thumbnail(self) -> bool:
        return bool(self.thumbnail_path) and os.path.exists(self.thumbnail_path)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["material_info"] = self.material_info.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "AssetEntry":
        mat_data = data.pop("material_info", {})
        entry = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        entry.material_info = MaterialInfo.from_dict(mat_data) if mat_data else MaterialInfo()
        return entry


@dataclass
class ScanResult:
    """Result from scanning a directory for a single asset."""
    asset_name: str = ""
    geo_file: str = ""
    texture_set: TextureSet = field(default_factory=TextureSet)
    source_dir: str = ""
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def is_valid(self) -> bool:
        """A scan result is valid if it has geometry and at least one texture."""
        return bool(self.geo_file) and self.texture_set.has_textures()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["texture_set"] = self.texture_set.to_dict()
        return d


@dataclass
class ProcessingJob:
    """Represents a batch processing job configuration."""
    job_id: str = ""
    source_directories: List[str] = field(default_factory=list)
    output_directory: str = ""
    thumbnail_directory: str = ""
    renderer: str = "karma"
    proxy_ratio: float = 0.1
    sim_method: str = "convex_hull"
    scan_results: List[ScanResult] = field(default_factory=list)
    status: str = "pending"         # pending | running | completed | failed
    progress: float = 0.0          # 0.0 to 1.0
    total_assets: int = 0
    processed_assets: int = 0
    failed_assets: int = 0
    date_started: str = ""
    date_completed: str = ""

    def __post_init__(self):
        if not self.job_id:
            import hashlib
            raw = f"job_{datetime.now().isoformat()}"
            self.job_id = hashlib.md5(raw.encode()).hexdigest()[:8]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scan_results"] = [sr.to_dict() if isinstance(sr, ScanResult) else sr
                             for sr in self.scan_results]
        return d
