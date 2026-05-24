"""
USD and Houdini utility functions for the Asset Manager.
Provides helpers for LOP node creation, stage inspection,
project alias resolution, and ACEScg color management.
"""

import os
import json
from typing import Optional, Tuple, List, Dict

# Houdini imports – guarded so the module can be imported outside Houdini
# for testing / data-model work.
try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

try:
    from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf
    HAS_USD = True
except ImportError:
    HAS_USD = False


# ────────────────────────────────────────────────────────
# Path / Config helpers
# ────────────────────────────────────────────────────────

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PACKAGE_ROOT))
_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")


def get_config_dir() -> str:
    """Return the path to the config directory."""
    return _CONFIG_DIR


def get_project_root() -> str:
    """Return the root directory of the ASSET_MANAGER project."""
    return _PROJECT_ROOT


def load_json_config(filename: str) -> dict:
    """Load a JSON config file from the config directory."""
    path = os.path.join(_CONFIG_DIR, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_naming_conventions() -> dict:
    """Load the naming conventions configuration."""
    return load_json_config("naming_conventions.json")


def load_renderer_settings() -> dict:
    """Load the renderer settings configuration."""
    return load_json_config("renderer_settings.json")


# ────────────────────────────────────────────────────────
# ACEScg Color Management
# ────────────────────────────────────────────────────────

# OCIO color-space names used in the ACEScg workflow.
# These follow the ACES 1.2+ naming convention used by
# Houdini 20+ and the standard OCIO ACES config.
ACES_COLORSPACES = {
    # sRGB textures (base_color, emissive, etc.)
    "srgb_texture": "Utility - sRGB - Texture",
    # Linear / raw data textures (roughness, metallic, normal, displacement, AO)
    "raw":          "Utility - Raw",
    # ACEScg working space (render space)
    "acescg":       "ACES - ACEScg",
    # Output: sRGB display
    "srgb_display": "Output - sRGB",
    # Linear sRGB (for when ACES isn't available)
    "linear_srgb":  "Utility - Linear - sRGB",
}

# Map each texture map type to the correct input colorspace.
TEXTURE_COLORSPACE_MAP = {
    "base_color":   "srgb_texture",
    "emissive":     "srgb_texture",
    "roughness":    "raw",
    "metallic":     "raw",
    "normal":       "raw",
    "displacement": "raw",
    "opacity":      "raw",
    "ao":           "raw",
}


def get_colorspace_for_map(map_type: str) -> str:
    """
    Return the OCIO colorspace name for a given texture map type
    in an ACEScg pipeline.

    Args:
        map_type: One of base_color, roughness, metallic, normal,
                  displacement, opacity, emissive, ao.

    Returns:
        The OCIO colorspace string (e.g. "Utility - sRGB - Texture").
    """
    cs_key = TEXTURE_COLORSPACE_MAP.get(map_type, "raw")
    return ACES_COLORSPACES.get(cs_key, "Utility - Raw")


def is_srgb_map(map_type: str) -> bool:
    """Return True if this map type should be treated as sRGB input."""
    return TEXTURE_COLORSPACE_MAP.get(map_type) == "srgb_texture"


# ────────────────────────────────────────────────────────
# Houdini / LOP helpers  (require hou)
# ────────────────────────────────────────────────────────

def ensure_hou():
    """Raise if hou module is not available."""
    if not HAS_HOU:
        raise RuntimeError("This function requires the Houdini (hou) module.")


def get_stage_root() -> "hou.LopNode":
    """Return the /stage context node."""
    ensure_hou()
    stage = hou.node("/stage")
    if stage is None:
        raise RuntimeError("No /stage context found. Are you in a Solaris session?")
    return stage


def get_or_create_subnet(parent: "hou.Node", name: str) -> "hou.Node":
    """Get an existing child subnet or create a new one."""
    ensure_hou()
    child = parent.node(name)
    if child is None:
        child = parent.createNode("subnet", name)
    return child


def create_lop_node(parent: "hou.Node", node_type: str,
                    name: str = "", **parms) -> "hou.Node":
    """
    Create a LOP node under *parent* and set parameters.

    Args:
        parent:    The parent node.
        node_type: Houdini node type name (e.g. "reference", "materiallibrary").
        name:      Optional node name.
        **parms:   Parameter name=value pairs to set on the node.

    Returns:
        The newly created node.
    """
    ensure_hou()
    node = parent.createNode(node_type, name or node_type)
    for parm_name, parm_value in parms.items():
        p = node.parm(parm_name)
        if p is not None:
            p.set(parm_value)
    return node


def get_active_network_editor() -> Optional["hou.NetworkEditor"]:
    """Return the first visible Network Editor pane, or None."""
    ensure_hou()
    for pane in hou.ui.paneTabs():
        if isinstance(pane, hou.NetworkEditor) and pane.isCurrentTab():
            return pane
    # Fallback: any network editor
    editors = [p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)]
    return editors[0] if editors else None


def create_reference_lop(parent: "hou.Node", usd_path: str,
                         name: str = "") -> "hou.Node":
    """
    Create a Reference LOP that references a USD file.

    Args:
        parent:   The parent LOP network.
        usd_path: Path to the USD file.
        name:     Optional node name.

    Returns:
        The reference LOP node.
    """
    ensure_hou()
    ref_name = name or os.path.splitext(os.path.basename(usd_path))[0]
    node = parent.createNode("reference", ref_name)
    node.parm("filepath1").set(usd_path)
    return node


def create_sublayer_lop(parent: "hou.Node", usd_path: str,
                        name: str = "") -> "hou.Node":
    """Create a Sublayer LOP that sublayers a USD file."""
    ensure_hou()
    node = parent.createNode("sublayer", name or "sublayer")
    node.parm("filepath1").set(usd_path)
    return node


def auto_layout_children(parent: "hou.Node"):
    """Auto-layout all children of a node."""
    ensure_hou()
    parent.layoutChildren()


# ────────────────────────────────────────────────────────
# USD Stage helpers  (require pxr)
# ────────────────────────────────────────────────────────

def get_stage_bounds(stage: "Usd.Stage") -> Optional[Tuple]:
    """
    Compute the world-space bounding box of the entire stage.

    Returns:
        (min_corner, max_corner) as Gf.Vec3d tuples, or None.
    """
    if not HAS_USD:
        return None

    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    )
    root = stage.GetPseudoRoot()
    bbox = bbox_cache.ComputeWorldBound(root)
    if bbox.GetRange().IsEmpty():
        return None
    rng = bbox.GetRange()
    return (rng.GetMin(), rng.GetMax())


def get_prim_bounds(stage: "Usd.Stage", prim_path: str) -> Optional[Tuple]:
    """Compute the bounding box of a specific prim."""
    if not HAS_USD:
        return None

    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        return None

    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
    )
    bbox = bbox_cache.ComputeWorldBound(prim)
    if bbox.GetRange().IsEmpty():
        return None
    rng = bbox.GetRange()
    return (rng.GetMin(), rng.GetMax())


def set_prim_purpose(stage: "Usd.Stage", prim_path: str, purpose: str):
    """
    Set the USD purpose attribute on a prim.

    Args:
        stage:     The USD stage.
        prim_path: Sdf path to the prim.
        purpose:   One of "default", "render", "proxy", "guide".
    """
    if not HAS_USD:
        return

    prim = stage.GetPrimAtPath(prim_path)
    if prim and prim.IsValid():
        imageable = UsdGeom.Imageable(prim)
        purpose_tokens = {
            "default": UsdGeom.Tokens.default_,
            "render":  UsdGeom.Tokens.render,
            "proxy":   UsdGeom.Tokens.proxy,
            "guide":   UsdGeom.Tokens.guide,
        }
        token = purpose_tokens.get(purpose, UsdGeom.Tokens.default_)
        imageable.CreatePurposeAttr(token)
