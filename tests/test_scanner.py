"""
Test script for verifying the Asset Manager outside of Houdini.
Tests the scanner, naming parser, database, and data models.

Run with: python test_scanner.py
"""

import os
import sys
import json
import shutil
import tempfile

# Add scripts to path
SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts"
)
sys.path.insert(0, SCRIPTS_DIR)

from asset_manager.database.models import TextureSet, AssetEntry, ScanResult
from asset_manager.database.asset_db import AssetDatabase
from asset_manager.core.scanner import DirectoryScanner


def create_test_assets(base_dir: str):
    """Create a mock directory structure with test assets."""
    assets = {
        "wooden_chair": {
            "geo": "wooden_chair.fbx",
            "textures": [
                "wooden_chair_basecolor.exr",
                "wooden_chair_roughness.exr",
                "wooden_chair_metallic.exr",
                "wooden_chair_normal.exr",
                "wooden_chair_displacement.exr",
            ]
        },
        "metal_table": {
            "geo": "metal_table.usd",
            "textures": [
                "metal_table_diffuse.png",
                "metal_table_rough.png",
                "metal_table_met.png",
                "metal_table_nml.png",
            ]
        },
        "stone_pillar": {
            "geo": "stone_pillar.obj",
            "textures": [
                "stone_pillar_albedo.1001.exr",
                "stone_pillar_albedo.1002.exr",
                "stone_pillar_roughness.1001.exr",
                "stone_pillar_roughness.1002.exr",
                "stone_pillar_normal.1001.exr",
                "stone_pillar_normal.1002.exr",
            ]
        },
        "geo_only_asset": {
            "geo": "geo_only_asset.abc",
            "textures": []
        },
    }

    for asset_name, data in assets.items():
        asset_dir = os.path.join(base_dir, asset_name)
        os.makedirs(asset_dir, exist_ok=True)

        # Create dummy geometry file
        geo_path = os.path.join(asset_dir, data["geo"])
        with open(geo_path, "w") as f:
            f.write(f"# Dummy geometry: {asset_name}\n")

        # Create dummy texture files
        for tex in data["textures"]:
            tex_path = os.path.join(asset_dir, tex)
            with open(tex_path, "w") as f:
                f.write(f"# Dummy texture: {tex}\n")

    return base_dir


def test_scanner():
    """Test the directory scanner."""
    print("=" * 60)
    print("TEST: Directory Scanner")
    print("=" * 60)

    test_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_test_assets"
    )

    try:
        # Create test assets
        create_test_assets(test_dir)
        print(f"Created test assets in: {test_dir}")

        # Scan
        scanner = DirectoryScanner()
        results = scanner.scan_single_directory(test_dir)
        results = scanner.validate_results(results)
        summary = scanner.get_scan_summary(results)

        print(f"\nScan Summary:")
        print(f"  Total found:     {summary['total_found']}")
        print(f"  Valid (geo+tex): {summary['valid']}")
        print(f"  Geometry only:   {summary['geo_only']}")

        print(f"\nDetailed Results:")
        for r in results:
            maps = r.texture_set.get_populated_maps()
            print(f"\n  Asset: {r.asset_name}")
            print(f"    Geo:      {os.path.basename(r.geo_file) if r.geo_file else 'NONE'}")
            print(f"    Textures: {len(maps)} maps")
            for k, v in maps.items():
                print(f"      {k:15s} -> {os.path.basename(v)}")
            if r.errors:
                print(f"    Errors:   {r.errors}")
            if r.warnings:
                print(f"    Warnings: {r.warnings}")

        assert summary["total_found"] == 4, f"Expected 4, got {summary['total_found']}"
        assert summary["valid"] == 3, f"Expected 3 valid, got {summary['valid']}"
        assert summary["geo_only"] == 1, f"Expected 1 geo-only, got {summary['geo_only']}"

        # Check UDIM handling
        pillar = next(r for r in results if r.asset_name == "stone_pillar")
        assert "<UDIM>" in pillar.texture_set.base_color, \
            f"Expected UDIM token in base_color: {pillar.texture_set.base_color}"

        print("\n[OK] Scanner tests PASSED")

    finally:
        if os.path.exists(test_dir):
            shutil.rmtree(test_dir)


def test_database():
    """Test the SQLite database."""
    print("\n" + "=" * 60)
    print("TEST: SQLite Database")
    print("=" * 60)

    db_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "_test_db", "test.db"
    )

    try:
        db = AssetDatabase(db_path)

        # Add assets
        entry1 = AssetEntry(
            name="test_chair",
            source_geo_path="/models/chair.fbx",
            renderer="karma",
            status="ready",
            tags=["furniture", "wood"],
            category="props",
        )
        uid1 = db.add_asset(entry1)
        print(f"  Added asset: {entry1.name} (uid: {uid1})")

        entry2 = AssetEntry(
            name="test_table",
            source_geo_path="/models/table.usd",
            renderer="arnold",
            status="pending",
            tags=["furniture", "metal"],
            category="props",
        )
        uid2 = db.add_asset(entry2)
        print(f"  Added asset: {entry2.name} (uid: {uid2})")

        # Query
        assert db.count == 2, f"Expected 2, got {db.count}"

        found = db.get_asset(uid1)
        assert found.name == "test_chair"
        print(f"  Retrieved: {found.name}")

        # Search
        results = db.search("chair")
        assert len(results) == 1
        print(f"  Search 'chair': {len(results)} result(s)")

        # Filter
        ready = db.filter_by_status("ready")
        assert len(ready) == 1
        print(f"  Filter 'ready': {len(ready)} result(s)")

        # Tags
        tags = db.get_all_tags()
        print(f"  All tags: {tags}")
        assert "furniture" in tags

        # Project alias
        db.add_project("test_proj", "/projects/test",
                       output_dir="/projects/test/usd_output",
                       thumbnail_dir="/projects/test/thumbnails")
        db.set_active_project("test_proj")
        active = db.get_active_project()
        assert active["alias"] == "test_proj"
        print(f"  Active project: {active['alias']}")

        # Stats
        stats = db.get_stats()
        print(f"  Stats: {json.dumps(stats, indent=2)}")

        # Export/Import
        export_path = os.path.join(
            os.path.dirname(db_path), "export.json"
        )
        db.export_to_json(export_path)
        print(f"  Exported to: {export_path}")

        # Remove
        db.remove_asset(uid1)
        assert db.count == 1
        print(f"  Removed uid1, count: {db.count}")

        print("\n[OK] Database tests PASSED")

    finally:
        test_db_dir = os.path.dirname(db_path)
        if os.path.exists(test_db_dir):
            shutil.rmtree(test_db_dir)


def test_data_models():
    """Test data model serialization."""
    print("\n" + "=" * 60)
    print("TEST: Data Models")
    print("=" * 60)

    tex = TextureSet(
        base_color="/tex/chair_basecolor.exr",
        roughness="/tex/chair_roughness.exr",
        normal="/tex/chair_normal.exr",
    )
    assert tex.has_textures()
    assert tex.get_map_count() == 3
    print(f"  TextureSet: {tex.get_map_count()} maps, has_textures={tex.has_textures()}")

    # Serialize/deserialize
    d = tex.to_dict()
    tex2 = TextureSet.from_dict(d)
    assert tex2.base_color == tex.base_color
    print(f"  TextureSet round-trip: OK")

    entry = AssetEntry(name="test", source_geo_path="/geo.fbx")
    d = entry.to_dict()
    entry2 = AssetEntry.from_dict(d)
    assert entry2.name == entry.name
    assert entry2.uid == entry.uid
    print(f"  AssetEntry round-trip: OK (uid={entry.uid})")

    print("\n[OK] Data model tests PASSED")


if __name__ == "__main__":
    print("Houdini Solaris LOP Asset Manager - Test Suite")
    print("=" * 60)
    test_data_models()
    test_database()
    test_scanner()
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED [OK]")
    print("=" * 60)
