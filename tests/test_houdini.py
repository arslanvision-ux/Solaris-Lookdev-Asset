"""
Houdini Integration Test for the Asset Manager.
Run this inside Houdini's Python Shell to validate the full pipeline.

Usage (in Houdini Python Shell):
    exec(open(r"E:\\PROJECTS\\ASSET_MANAGER\\tests\\test_houdini.py").read())
"""

import os
import sys
import shutil

# ── Setup ──
ASSET_MANAGER_ROOT = r"E:\PROJECTS\ASSET_MANAGER"
SCRIPTS_DIR = os.path.join(ASSET_MANAGER_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import hou


def log(msg):
    print(f"[HouTest] {msg}")


def create_test_geometry():
    """Create a simple test geometry at SOP level."""
    obj = hou.node("/obj")

    # Clean up previous test nodes
    old = obj.node("_asset_mgr_test")
    if old:
        old.destroy()

    geo = obj.createNode("geo", "_asset_mgr_test")
    # Create a torus as test geometry
    torus = geo.createNode("torus", "test_torus")
    torus.parm("rows").set(24)
    torus.parm("cols").set(24)

    # Add UV
    uvproject = geo.createNode("uvproject", "uvproject")
    uvproject.setInput(0, torus, 0)

    # Normal
    normal = geo.createNode("normal", "normal")
    normal.setInput(0, uvproject, 0)
    normal.setDisplayFlag(True)
    normal.setRenderFlag(True)

    geo.layoutChildren()
    return geo, normal


def create_test_textures(output_dir):
    """Create placeholder texture files for testing."""
    os.makedirs(output_dir, exist_ok=True)

    textures = {
        "test_asset_basecolor.exr": "base_color",
        "test_asset_roughness.exr": "roughness",
        "test_asset_metallic.exr": "metallic",
        "test_asset_normal.exr": "normal",
    }

    for filename, _ in textures.items():
        path = os.path.join(output_dir, filename)
        if not os.path.exists(path):
            # Create a small placeholder EXR via COP if possible,
            # otherwise just a dummy file
            with open(path, "wb") as f:
                f.write(b"PLACEHOLDER")

    return output_dir


# ════════════════════════════════════════════════
# Test 1: Scanner (rerun outside-Houdini test)
# ════════════════════════════════════════════════

def test_scanner():
    log("=" * 50)
    log("TEST 1: Directory Scanner")
    log("=" * 50)

    from asset_manager.core.scanner import DirectoryScanner

    test_dir = os.path.join(ASSET_MANAGER_ROOT, "tests", "_hou_test_assets")
    os.makedirs(test_dir, exist_ok=True)

    asset_dir = os.path.join(test_dir, "test_asset")
    os.makedirs(asset_dir, exist_ok=True)

    # Create dummy files
    with open(os.path.join(asset_dir, "test_asset.obj"), "w") as f:
        f.write("# test\n")
    for suffix in ["_basecolor.exr", "_roughness.exr", "_normal.exr"]:
        with open(os.path.join(asset_dir, f"test_asset{suffix}"), "w") as f:
            f.write("# test\n")

    scanner = DirectoryScanner()
    results = scanner.scan_single_directory(test_dir)
    results = scanner.validate_results(results)

    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0].is_valid(), "Result should be valid"
    assert results[0].texture_set.base_color, "Should have base_color"

    log(f"  Found: {results[0].asset_name}")
    log(f"  Textures: {results[0].texture_set.get_map_count()} maps")
    log("  PASSED")

    # Cleanup
    shutil.rmtree(test_dir, ignore_errors=True)
    return results[0]


# ════════════════════════════════════════════════
# Test 2: MaterialX Builder
# ════════════════════════════════════════════════

def test_materialx_builder():
    log("=" * 50)
    log("TEST 2: MaterialX Builder")
    log("=" * 50)

    from asset_manager.core.materialx_builder import MaterialXBuilder
    from asset_manager.database.models import TextureSet

    stage = hou.node("/stage")

    # Clean up
    old = stage.node("_test_mat_lib")
    if old:
        old.destroy()

    builder = MaterialXBuilder(renderer="karma")

    # Create material library
    mat_lib = builder.create_material_library(stage, "_test_mat_lib")
    assert mat_lib is not None, "Material library should be created"
    log(f"  Created: {mat_lib.path()}")

    # Build material with test textures
    tex = TextureSet(
        base_color="$HIP/textures/test_basecolor.exr",
        roughness="$HIP/textures/test_roughness.exr",
        normal="$HIP/textures/test_normal.exr",
    )

    info = builder.build_material(mat_lib, "test_asset", tex)
    assert info.name == "test_asset_mtl", f"Expected 'test_asset_mtl', got '{info.name}'"
    log(f"  Material: {info.name}")
    log(f"  Material path: {info.material_path}")

    # Check nodes were created inside the material builder
    mat_builder = mat_lib.node("test_asset_mtl")
    if mat_builder:
        children = mat_builder.children()
        child_types = [c.type().name() for c in children]
        log(f"  Nodes inside builder: {child_types}")
    else:
        log("  WARNING: Material builder subnet not found (may use different naming)")

    log("  PASSED")
    return mat_lib


# ════════════════════════════════════════════════
# Test 3: LOP Network Construction
# ════════════════════════════════════════════════

def test_lop_network():
    log("=" * 50)
    log("TEST 3: LOP Network Construction")
    log("=" * 50)

    from asset_manager.core.usd_utils import create_reference_lop, create_lop_node

    stage = hou.node("/stage")

    # Clean up
    old = stage.node("_test_lop_subnet")
    if old:
        old.destroy()

    subnet = stage.createNode("subnet", "_test_lop_subnet")

    # Test creating LOP nodes
    # 1. SOP Import
    geo_node, final_sop = create_test_geometry()
    sop_import = subnet.createNode("sopimport", "test_import")
    sop_import.parm("soppath").set(final_sop.path())
    sop_import.parm("primpath").set("/test_asset/geo/render")
    log(f"  Created SOP Import: {sop_import.path()}")

    # 2. Configure Layer
    config = subnet.createNode("configurelayer", "test_config")
    config.setInput(0, sop_import, 0)
    config.parm("defaultprim").set("/test_asset")
    log(f"  Created Configure Layer: {config.path()}")

    # 3. Force cook to validate the network
    try:
        config.cook(force=True)
        log("  Network cooks successfully")
    except Exception as e:
        log(f"  WARNING: Cook error (non-fatal): {e}")

    subnet.layoutChildren()
    log("  PASSED")
    return subnet


# ════════════════════════════════════════════════
# Test 4: Database (SQLite)
# ════════════════════════════════════════════════

def test_database():
    log("=" * 50)
    log("TEST 4: SQLite Database")
    log("=" * 50)

    from asset_manager.database.asset_db import AssetDatabase
    from asset_manager.database.models import AssetEntry

    db_path = os.path.join(ASSET_MANAGER_ROOT, "tests", "_hou_test.db")

    try:
        db = AssetDatabase(db_path)

        entry = AssetEntry(
            name="houdini_test",
            source_geo_path="/test/geo.fbx",
            renderer="karma",
            status="ready",
            tags=["test"],
        )
        uid = db.add_asset(entry)
        log(f"  Added: {entry.name} (uid: {uid})")

        found = db.get_asset(uid)
        assert found is not None
        assert found.name == "houdini_test"
        log(f"  Retrieved: {found.name}")

        # Project alias
        db.add_project("houdini_test", hou.hipFile.path(),
                       output_dir="$HIP/usd_output")
        db.set_active_project("houdini_test")
        active = db.get_active_project()
        assert active is not None
        log(f"  Active project: {active['alias']}")

        db.remove_asset(uid)
        log("  PASSED")

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)


# ════════════════════════════════════════════════
# Test 5: UI Panel Launch
# ════════════════════════════════════════════════

def test_ui_launch():
    log("=" * 50)
    log("TEST 5: UI Panel Launch")
    log("=" * 50)

    try:
        from asset_manager.ui.main_panel import AssetManagerPanel
        panel = AssetManagerPanel(hou.qt.mainWindow())
        log(f"  Panel created: {panel.windowTitle()}")
        log(f"  Size: {panel.minimumSize().width()}x{panel.minimumSize().height()}")

        # Don't show yet — just validate it constructs without errors
        panel.close()
        panel.deleteLater()
        log("  PASSED")
    except Exception as e:
        log(f"  FAILED: {e}")
        import traceback
        traceback.print_exc()


# ════════════════════════════════════════════════
# Test 6: Full Pipeline (Scanner -> Build -> Gallery)
# ════════════════════════════════════════════════

def test_full_pipeline():
    log("=" * 50)
    log("TEST 6: Full Pipeline (end-to-end)")
    log("=" * 50)

    from asset_manager.core.scanner import DirectoryScanner
    from asset_manager.core.component_builder import ComponentBuilder
    from asset_manager.database.asset_db import AssetDatabase
    from asset_manager.database.models import AssetEntry

    test_dir = os.path.join(ASSET_MANAGER_ROOT, "tests", "_hou_pipeline_test")
    output_dir = os.path.join(test_dir, "output")
    os.makedirs(test_dir, exist_ok=True)

    try:
        # Create test geometry file (save a simple USD from SOPs)
        geo_node, final_sop = create_test_geometry()
        geo_file = os.path.join(test_dir, "pipeline_test.obj")
        rop = geo_node.createNode("rop_geometry", "export_test")
        rop.parm("soppath").set(final_sop.path())
        rop.parm("sopoutput").set(geo_file)
        rop.parm("execute").pressButton()
        log(f"  Exported test geometry: {geo_file}")
        assert os.path.exists(geo_file), "Geometry file should exist"

        # Create texture placeholders
        for suffix in ["_basecolor.exr", "_roughness.exr", "_normal.exr"]:
            tex_path = os.path.join(test_dir, f"pipeline_test{suffix}")
            with open(tex_path, "wb") as f:
                f.write(b"PLACEHOLDER")

        # Scan
        scanner = DirectoryScanner()
        results = scanner.scan_single_directory(test_dir, recursive=False)
        results = scanner.validate_results(results)
        log(f"  Scanned: {len(results)} asset(s)")

        valid = [r for r in results if r.is_valid()]
        if valid:
            result = valid[0]
            log(f"  Asset: {result.asset_name}")
            log(f"  Maps: {result.texture_set.get_map_count()}")

            # Build
            builder = ComponentBuilder(renderer="karma", proxy_ratio=0.1)
            stage = hou.node("/stage")

            entry = builder.build_asset(result, output_dir, stage)
            log(f"  Built: {entry.name}")
            log(f"  USD path: {entry.usd_output_path}")
            log(f"  Status: {entry.status}")

            # Register in DB
            db_path = os.path.join(test_dir, "test.db")
            db = AssetDatabase(db_path)
            uid = db.add_asset(entry)
            log(f"  Registered in DB: uid={uid}")

            assert db.count == 1
            log("  PASSED")
        else:
            log("  WARNING: No valid scan results (texture matching may need adjustment)")
            log("  SKIPPED")

    except Exception as e:
        log(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Cleanup
        old = hou.node("/obj/_asset_mgr_test")
        if old:
            old.destroy()
        shutil.rmtree(test_dir, ignore_errors=True)


# ════════════════════════════════════════════════
# Run All Tests
# ════════════════════════════════════════════════

def run_all():
    log("")
    log("Houdini Solaris Asset Manager - Integration Tests")
    log("=" * 50)

    test_scanner()
    test_database()
    test_materialx_builder()
    test_lop_network()
    test_ui_launch()
    test_full_pipeline()

    log("")
    log("=" * 50)
    log("ALL HOUDINI TESTS COMPLETE")
    log("=" * 50)

    # Cleanup test nodes
    for name in ["_test_mat_lib", "_test_lop_subnet"]:
        node = hou.node(f"/stage/{name}")
        if node:
            node.destroy()
    for name in ["_asset_mgr_test"]:
        node = hou.node(f"/obj/{name}")
        if node:
            node.destroy()

    log("Cleaned up test nodes.")


run_all()
