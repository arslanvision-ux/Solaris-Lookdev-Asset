"""
Proxy geometry generator for the Asset Manager.
Creates PolyReduce proxies and convex hull sim meshes at the SOP level.
"""

import os
from typing import Optional

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from .usd_utils import ensure_hou


class ProxyGenerator:
    """
    Generates proxy (PolyReduce) and simulation (convex hull) geometry
    at the SOP level, then exports as USD layers.
    """

    def __init__(self, proxy_ratio: float = 0.1,
                 sim_method: str = "convex_hull"):
        """
        Args:
            proxy_ratio: Keep percentage for PolyReduce (0.0–1.0).
            sim_method:  "convex_hull", "vdb_remesh", or "decimated".
        """
        self._proxy_ratio = max(0.01, min(1.0, proxy_ratio))
        self._sim_method = sim_method

    def create_proxy_sop_network(self, parent_geo: "hou.Node",
                                 source_sop: "hou.Node",
                                 name: str = "proxy") -> "hou.Node":
        """
        Create a PolyReduce SOP chain for proxy geometry.

        Args:
            parent_geo:  The geometry container (e.g. /obj/geo1).
            source_sop:  The source SOP node to reduce.
            name:        Name prefix for created nodes.

        Returns:
            The final output SOP node of the proxy chain.
        """
        ensure_hou()

        # PolyReduce SOP
        polyreduce = parent_geo.createNode("polyreduce::2.0", f"{name}_polyreduce")
        polyreduce.setInput(0, source_sop, 0)
        polyreduce.parm("percentage").set(self._proxy_ratio * 100.0)
        polyreduce.parm("preservequality").set(1)  # Quality preservation
        polyreduce.parm("preserveboundary").set(1)  # Keep boundaries
        polyreduce.parm("preservegroups").set(1)    # Keep groups

        # Normal SOP to recompute normals after reduction
        normal = parent_geo.createNode("normal", f"{name}_normal")
        normal.setInput(0, polyreduce, 0)
        normal.parm("type").set(0)  # Point normals

        return normal

    def create_sim_sop_network(self, parent_geo: "hou.Node",
                               source_sop: "hou.Node",
                               name: str = "sim") -> "hou.Node":
        """
        Create simulation geometry (convex hull or VDB remesh).

        Args:
            parent_geo:  The geometry container.
            source_sop:  The source SOP node.
            name:        Name prefix for created nodes.

        Returns:
            The final output SOP node of the sim chain.
        """
        ensure_hou()

        if self._sim_method == "convex_hull":
            return self._create_convex_hull(parent_geo, source_sop, name)
        elif self._sim_method == "vdb_remesh":
            return self._create_vdb_remesh(parent_geo, source_sop, name)
        else:
            # Fallback: heavily decimated version
            return self._create_decimated(parent_geo, source_sop, name)

    def _create_convex_hull(self, parent_geo: "hou.Node",
                            source_sop: "hou.Node",
                            name: str) -> "hou.Node":
        """Create a convex hull using the Convex Decomposition SOP."""
        try:
            # Try the Convex Decomposition SOP (Houdini 20+)
            convex = parent_geo.createNode(
                "convexdecomposition", f"{name}_convex"
            )
            convex.setInput(0, source_sop, 0)
            return convex
        except hou.OperationFailed:
            # Fallback: use ConvexHull via VEX wrangle
            hull = parent_geo.createNode("convexhull", f"{name}_hull")
            hull.setInput(0, source_sop, 0)
            return hull

    def _create_vdb_remesh(self, parent_geo: "hou.Node",
                           source_sop: "hou.Node",
                           name: str) -> "hou.Node":
        """Create a VDB-based remeshed sim mesh."""
        # VDB from Polygons
        vdb_from = parent_geo.createNode("vdbfrompolygons", f"{name}_vdb")
        vdb_from.setInput(0, source_sop, 0)
        vdb_from.parm("voxelsize").set(0.02)

        # Convert VDB back to polygons
        convert = parent_geo.createNode("convertvdb", f"{name}_convert")
        convert.setInput(0, vdb_from, 0)

        return convert

    def _create_decimated(self, parent_geo: "hou.Node",
                          source_sop: "hou.Node",
                          name: str) -> "hou.Node":
        """Create a heavily decimated sim mesh."""
        polyreduce = parent_geo.createNode(
            "polyreduce::2.0", f"{name}_reduce"
        )
        polyreduce.setInput(0, source_sop, 0)
        polyreduce.parm("percentage").set(5.0)  # Keep only 5%
        return polyreduce

    def export_proxy_usd(self, proxy_sop: "hou.Node",
                         output_path: str,
                         prim_path: str = "/proxy"):
        """Export proxy geometry as a USD file via SOP-level USD ROP."""
        ensure_hou()
        parent = proxy_sop.parent()

        rop = parent.createNode("usdexport", "proxy_export")
        rop.setInput(0, proxy_sop, 0)
        rop.parm("lopoutput").set(output_path)
        rop.parm("primpath").set(prim_path)
        rop.parm("purpose").set("proxy")

        try:
            rop.parm("execute").pressButton()
        except Exception as e:
            print(f"[ProxyGenerator] Export failed: {e}")
        finally:
            rop.destroy()
