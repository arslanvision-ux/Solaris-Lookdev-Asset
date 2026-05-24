"""
Recursive directory scanner for the Asset Manager.
Walks specified directories, identifies geometry files and groups
them with their texture sets based on naming conventions.
"""

import os
import re
from typing import List, Dict, Optional

from ..database.models import ScanResult, TextureSet
from .usd_utils import load_naming_conventions


class DirectoryScanner:
    """
    Scans directories recursively to discover 3D model assets and
    their associated texture files.
    """

    def __init__(self, conventions: Optional[dict] = None):
        config = conventions or load_naming_conventions()
        self._convention = config.get("conventions", {}).get("default", {})
        self._image_formats = set(
            config.get("supported_image_formats",
                       [".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff",
                        ".hdr", ".tex", ".rat"])
        )
        self._geo_formats = set(
            config.get("supported_geo_formats",
                       [".usd", ".usda", ".usdc", ".usdz", ".obj",
                        ".fbx", ".abc", ".bgeo", ".bgeo.sc"])
        )
        self._suffix_map: Dict[str, str] = {}
        for map_type, suffixes in self._convention.items():
            for suffix in suffixes:
                self._suffix_map[suffix.lower()] = map_type
        self._sorted_suffixes = sorted(
            self._suffix_map.keys(), key=len, reverse=True
        )
        self._udim_re = re.compile(r"[\._](\d{4})[\._]?")
        # Trailing tags that texture/geo filenames pick up beyond the map
        # suffix. Stripped iteratively so e.g. `Drill_01_nor_gl_2k` →
        # `Drill_01_nor` (peel `_2k`, then `_gl`) before suffix matching.
        self._trail_tag_re = re.compile(
            r"[_\-]("
            r"\d+k|\d{3,5}"            # resolution: _2k, _4k, _1024, _2048
            r"|gl|dx|opengl|directx"   # normal-map convention
            r"|srgb|linear|raw|aces|acescg"  # colorspace hints
            r"|mdl|mat|tex|map"        # DCC batch-export layer tags
            r")$",
            re.IGNORECASE,
        )

    def scan_directories(self, directories: List[str],
                         recursive: bool = True) -> List[ScanResult]:
        all_results: Dict[str, ScanResult] = {}
        for directory in directories:
            directory = os.path.normpath(directory)
            if not os.path.isdir(directory):
                continue
            self._scan_directory(directory, recursive, all_results)
        return list(all_results.values())

    def scan_single_directory(self, directory: str,
                              recursive: bool = True) -> List[ScanResult]:
        return self.scan_directories([directory], recursive)

    def _scan_directory(self, directory: str, recursive: bool,
                        results: Dict[str, ScanResult]):
        if recursive:
            for root, dirs, files in os.walk(directory):
                self._process_file_list(root, files, results)
        else:
            files = os.listdir(directory)
            self._process_file_list(directory, files, results)

    def _process_file_list(self, directory: str, files: List[str],
                           results: Dict[str, ScanResult]):
        for filename in files:
            filepath = os.path.join(directory, filename)
            if not os.path.isfile(filepath):
                continue
            ext = self._get_extension(filename)
            if ext in self._geo_formats:
                asset_name = self._get_stem(filename, ext)
                result = self._get_or_create_result(results, asset_name, directory)
                # Geo file is the canonical source of the asset's display
                # name — overwrite anything a texture pre-registered.
                result.asset_name = asset_name
                if not result.geo_file or ext in (".usd", ".usda", ".usdc"):
                    result.geo_file = filepath
            elif ext in self._image_formats:
                self._classify_texture(filename, filepath, directory, results)

    def _classify_texture(self, filename: str, filepath: str,
                          directory: str, results: Dict[str, ScanResult]):
        ext = self._get_extension(filename)
        stem = self._get_stem(filename, ext)
        stem_normalized = self._normalize_stem(stem)
        for suffix in self._sorted_suffixes:
            if stem_normalized.lower().endswith(suffix):
                map_type = self._suffix_map[suffix]
                asset_name = stem_normalized[
                    :len(stem_normalized) - len(suffix)
                ].rstrip("_-")
                if not asset_name:
                    continue
                result = self._get_or_create_result(results, asset_name, directory)
                if self._udim_re.search(filename):
                    # Replace the 4-digit tile with <UDIM>
                    filepath = self._udim_re.sub(lambda m: m.group(0).replace(m.group(1), "<UDIM>"), filepath, count=1)
                self._set_texture_slot(result.texture_set, map_type, filepath)
                result.source_dir = directory
                return

    def _strip_udim(self, stem: str) -> str:
        # Match .1001 or _1001 at the end or middle
        match = re.search(r"[\._](\d{4})", stem)
        if match:
            tile = int(match.group(1))
            if 1001 <= tile <= 1999:
                # Remove the UDIM part from the stem for naming
                return stem.replace(match.group(0), "")
        return stem

    def _normalize_stem(self, stem: str) -> str:
        """Strip UDIM + all trailing tags (resolution / normal-convention /
        colorspace) iteratively so `Drill_01_nor_gl_2k` becomes `Drill_01_nor`
        and the map-suffix matcher can see the real ending."""
        stem = self._strip_udim(stem)
        while True:
            m = self._trail_tag_re.search(stem)
            if not m:
                return stem
            stem = stem[:m.start()]
            if not stem:
                return stem

    def _get_extension(self, filename: str) -> str:
        lower = filename.lower()
        if lower.endswith(".bgeo.sc"):
            return ".bgeo.sc"
        _, ext = os.path.splitext(filename)
        return ext.lower()

    def _get_stem(self, filename: str, ext: str) -> str:
        if ext == ".bgeo.sc":
            return filename[:-len(".bgeo.sc")]
        return os.path.splitext(filename)[0]

    def _get_or_create_result(self, results: Dict[str, ScanResult],
                              asset_name: str, directory: str) -> ScanResult:
        # Use a normalized key so the geo (`Drill_01_2k`) and the
        # texture-derived name (`Drill_01`) collapse to the same entry.
        key = self._normalize_stem(asset_name).lower()
        if key not in results:
            results[key] = ScanResult(
                asset_name=asset_name, source_dir=directory,
                texture_set=TextureSet(),
            )
        return results[key]

    @staticmethod
    def _set_texture_slot(texture_set: TextureSet, map_type: str, path: str):
        if hasattr(texture_set, map_type) and not getattr(texture_set, map_type):
            setattr(texture_set, map_type, path)

    def validate_results(self, results: List[ScanResult]) -> List[ScanResult]:
        for result in results:
            if not result.geo_file:
                result.errors.append("No geometry file found")
            if not result.texture_set.has_textures():
                result.warnings.append("No textures found")
            if not result.texture_set.base_color:
                result.warnings.append("Missing base color texture")
        return results

    def get_scan_summary(self, results: List[ScanResult]) -> dict:
        valid = [r for r in results if r.is_valid()]
        geo_only = [r for r in results if r.geo_file and not r.texture_set.has_textures()]
        tex_only = [r for r in results if not r.geo_file and r.texture_set.has_textures()]
        return {
            "total_found": len(results),
            "valid": len(valid),
            "geo_only": len(geo_only),
            "tex_only": len(tex_only),
            "valid_assets": [r.asset_name for r in valid],
        }
