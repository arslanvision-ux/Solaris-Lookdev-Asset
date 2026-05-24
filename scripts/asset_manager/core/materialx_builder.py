"""
MaterialX shading network builder for the Asset Manager.
Creates MaterialX-based materials in Houdini Solaris LOPs with
ACEScg color management and multi-renderer support.

Targets Houdini 20+ (PySide6, USD-native MaterialX).
"""

import os
from typing import Dict, Optional, List

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from ..database.models import TextureSet, MaterialInfo
from .usd_utils import (
    load_renderer_settings, get_colorspace_for_map,
    is_srgb_map, ensure_hou, ACES_COLORSPACES
)


# ────────────────────────────────────────────────────────
# MaterialX input name mapping
# Maps our internal map_type names to the actual
# mtlxstandard_surface input parameter names in Houdini 20+.
# ────────────────────────────────────────────────────────
MTLX_INPUT_MAP = {
    "base_color":   {"input": "base_color",          "signature": "color3",  "needs_normalmap": False},
    "roughness":    {"input": "specular_roughness",   "signature": "float",   "needs_normalmap": False},
    "metallic":     {"input": "metalness",            "signature": "float",   "needs_normalmap": False},
    "normal":       {"input": "normal",               "signature": "vector3", "needs_normalmap": True},
    "opacity":      {"input": "opacity",              "signature": "color3",  "needs_normalmap": False},
    "emissive":     {"input": "emission_color",       "signature": "color3",  "needs_normalmap": False},
    "displacement": {"input": None,                   "signature": "float",   "needs_normalmap": False},
    "ao":           {"input": None,                   "signature": "float",   "needs_normalmap": False},
}


class MaterialXBuilder:
    """
    Programmatically builds MaterialX shading networks inside
    a Material Library LOP in Houdini Solaris.

    Supports Karma, Arnold, and Redshift through the standard
    mtlxstandard_surface shader with ACEScg color management.
    """

    def __init__(self, renderer: str = "karma"):
        self._renderer = renderer
        self._settings = load_renderer_settings()
        self._renderer_config = self._settings["renderers"].get(
            renderer, self._settings["renderers"]["karma"]
        )

    @property
    def renderer(self) -> str:
        return self._renderer

    @renderer.setter
    def renderer(self, value: str):
        self._renderer = value
        self._renderer_config = self._settings["renderers"].get(
            value, self._settings["renderers"]["karma"]
        )

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def build_material(self, parent_node: "hou.Node",
                       asset_name: str,
                       texture_set: TextureSet,
                       material_name: str = "") -> MaterialInfo:
        """
        Build a complete MaterialX material network for an asset.

        Args:
            parent_node:   The parent LOP node (typically a Material Library
                           or a subnet inside one).
            asset_name:    Name of the asset (used for naming).
            texture_set:   TextureSet with paths to texture files.
            material_name: Optional override for the material name.

        Returns:
            MaterialInfo with the created material's metadata.
        """
        ensure_hou()

        mat_name = material_name or f"{asset_name}_mtl"
        mat_builder, flat_mode = self._create_material_builder(
            parent_node, mat_name
        )

        # In flat mode, mat_builder IS the materiallibrary — the surface
        # shader's own name becomes the USD material prim name, so create
        # it with `mat_name`. In wrapped mode, the wrapper's name is the
        # material name, so the inner shader can use any internal name.
        surface_node = self._get_surface_shader(mat_builder)
        if surface_node is None:
            surface_node = self._create_surface_shader(
                mat_builder,
                shader_name=mat_name if flat_mode else "standard_surface",
            )

        # Find output/collect node for displacement wiring
        output_node = self._get_output_node(mat_builder)

        # Connect each populated texture map
        populated = texture_set.get_populated_maps()
        created_nodes = []

        for map_type, tex_path in populated.items():
            nodes = self._connect_texture_map(
                mat_builder, surface_node, output_node,
                map_type, tex_path
            )
            created_nodes.extend(nodes)

        # Handle AO multiply if both AO and base_color exist
        if texture_set.ao and texture_set.base_color:
            self._wire_ao_multiply(mat_builder, surface_node)

        # Layout everything nicely
        mat_builder.layoutChildren()

        info = MaterialInfo(
            name=mat_name,
            material_path=f"/mtl/{mat_name}",
            renderer=self._renderer,
            texture_set=texture_set,
        )
        return info

    def create_material_library(self, parent_node: "hou.Node",
                                name: str = "material_library") -> "hou.Node":
        """Create a Material Library LOP node."""
        ensure_hou()
        mat_lib = parent_node.createNode("materiallibrary", name)
        return mat_lib

    def _create_material_builder(self, parent_node: "hou.Node",
                                 mat_name: str):
        """
        Pick the right "container" for the material's shader graph.

        H20 renderer-specific builders like `karmamaterialbuilder` were
        removed in H21. A plain VOP `subnet` works as a container, but the
        materiallibrary doesn't auto-recognize subnets as USD materials —
        so componentmaterial would later fail to find the material prim.

        H21's idiom: place the surface shader (`mtlxstandard_surface`)
        directly inside the materiallibrary. The materiallibrary auto-
        detects known shader types and creates a USD material prim named
        after the shader. We use this "flat mode" as the fallback.

        Returns:
            (container_node, flat_mode) — flat_mode=True means the caller
            should name the surface shader with the material name so the
            auto-generated USD material prim has the right path.
        """
        configured = self._renderer_config.get("material_builder_type", "")
        wrapper_candidates = [
            configured,
            "karmamaterialbuilder",   # H20-era Karma builder
            "karma::mtl",              # alt H20 name
        ]
        for type_name in wrapper_candidates:
            if not type_name:
                continue
            try:
                node = parent_node.createNode(type_name, mat_name)
                if type_name != configured:
                    print(f"[MtlXBuilder] material_builder_type "
                          f"'{configured}' unavailable; using '{type_name}'")
                return node, False
            except hou.OperationFailed:
                continue
        # H21 fallback: flat mode — shaders go directly into the matlib.
        print(f"[MtlXBuilder] Using flat mode (shader nodes directly inside "
              f"{parent_node.path()}); material name = '{mat_name}'")
        return parent_node, True

    # ──────────────────────────────────────────────
    # Internal: find existing shader nodes
    # ──────────────────────────────────────────────

    def _get_surface_shader(self, mat_builder: "hou.Node") -> "hou.Node":
        """
        Find the existing surface shader inside a material builder.
        Karma Material Builder creates one automatically.
        """
        surface_type = self._renderer_config["surface_shader_type"]

        for child in mat_builder.children():
            ctype = child.type().name()
            if ctype == surface_type:
                return child
            # Also check for generic surface outputs
            if "standard_surface" in ctype or "surface" in child.name():
                if "output" not in ctype and "collect" not in ctype:
                    return child
        return None

    def _create_surface_shader(self, mat_builder: "hou.Node",
                               shader_name: str = "standard_surface") -> "hou.Node":
        """Create a new mtlxstandard_surface node and wire it to the
        builder's output (if there is one — flat mode has no output node)."""
        surface_type = self._renderer_config["surface_shader_type"]
        surface = mat_builder.createNode(surface_type, shader_name)

        output = self._get_output_node(mat_builder)
        if output:
            try:
                output.setNamedInput("surface", surface, "out")
            except Exception:
                try:
                    output.setInput(0, surface, 0)
                except Exception:
                    pass
        return surface

    def _get_output_node(self, mat_builder: "hou.Node") -> "hou.Node":
        """Find the output/collect node inside the material builder.
        Handles renderer-specific builders (which create explicit "output"
        nodes) and plain VOP subnets (which have a `subnetconnector` named
        `suboutput`)."""
        for child in mat_builder.children():
            ctype = child.type().name().lower()
            cname = child.name().lower()
            if ("output" in ctype or "collect" in ctype
                    or "suboutput" in ctype):
                return child
            # Generic VOP subnet output connector.
            if ctype == "subnetconnector":
                kind = ""
                try:
                    kp = child.parm("connectorkind")
                    if kp is not None:
                        kind = kp.evalAsString().lower()
                except Exception:
                    pass
                if kind == "output" or "output" in cname or cname.startswith("subout"):
                    return child
        return None

    def _ensure_surface_output_connector(self, mat_builder: "hou.Node"):
        """If mat_builder is a generic `subnet`, configure its suboutput
        connector so it exposes a "surface" output typed as `surfaceshader`.
        Without this, the materiallibrary doesn't recognize the subnet as a
        MaterialX surface material and binds nothing."""
        if mat_builder.type().name() != "subnet":
            return  # renderer-specific builders pre-configure this
        out_conn = None
        for child in mat_builder.children():
            if child.type().name() != "subnetconnector":
                continue
            kind = ""
            try:
                kp = child.parm("connectorkind")
                if kp is not None:
                    kind = kp.evalAsString().lower()
            except Exception:
                pass
            if kind == "output" or "output" in child.name().lower():
                out_conn = child
                break
        if out_conn is None:
            try:
                out_conn = mat_builder.createNode("subnetconnector", "suboutput")
                _ = out_conn.parm("connectorkind") and \
                    out_conn.parm("connectorkind").set("output")
            except Exception as e:
                print(f"[MtlXBuilder] Could not create suboutput: {e}")
                return
        # Name + type the connector so it surfaces as a MaterialX surface.
        for pname, val in (("parmname", "surface"),
                           ("parmlabel", "Surface"),
                           ("parmtype", "surface")):
            p = out_conn.parm(pname)
            if p is not None:
                try:
                    p.set(val)
                except Exception:
                    pass

    # ──────────────────────────────────────────────
    # Internal: texture wiring
    # ──────────────────────────────────────────────

    def _connect_texture_map(self, mat_builder: "hou.Node",
                             surface_node: "hou.Node",
                             output_node: "hou.Node",
                             map_type: str,
                             tex_path: str) -> List["hou.Node"]:
        """
        Create texture node(s) and wire to the surface shader.
        Returns list of created nodes.
        """
        mapping = MTLX_INPUT_MAP.get(map_type)
        if mapping is None:
            return []

        img_type = self._renderer_config["image_node_type"]
        created = []

        # ── Create mtlximage node ──
        img_node = mat_builder.createNode(img_type, f"{map_type}_tex")
        created.append(img_node)
        self._set_image_params(img_node, tex_path, map_type, mapping["signature"])

        # ── Route through normalmap if needed ──
        if mapping["needs_normalmap"]:
            nmap_type = self._renderer_config["normalmap_node_type"]
            nmap_node = mat_builder.createNode(nmap_type, f"{map_type}_nmap")
            created.append(nmap_node)

            # Connect image -> normalmap
            try:
                nmap_node.setNamedInput("in", img_node, "out")
            except Exception:
                nmap_node.setInput(0, img_node, 0)

            # Connect normalmap -> surface input
            self._wire_to_surface(surface_node, mapping["input"], nmap_node)

        elif mapping["input"] is not None:
            # Direct connection: image -> surface input
            self._wire_to_surface(surface_node, mapping["input"], img_node)

        elif map_type == "displacement":
            self._wire_displacement(mat_builder, output_node, img_node)

        # AO is handled separately via _wire_ao_multiply
        return created

    def _set_image_params(self, img_node: "hou.Node", tex_path: str,
                          map_type: str, signature: str):
        """Set file path, colorspace, and signature on a mtlximage node."""
        # File path
        for pname in ("file", "filename", "tex0"):
            p = img_node.parm(pname)
            if p is not None:
                p.set(tex_path)
                break

        # Signature (data type: color3, float, vector3)
        sig_parm = img_node.parm("signature")
        if sig_parm is not None:
            try:
                sig_parm.set(signature)
            except Exception:
                pass

        # OCIO Colorspace (ACEScg pipeline)
        colorspace = get_colorspace_for_map(map_type)
        for pname in ("ocio:colorspace", "colorspace", "ocio_colorspace"):
            p = img_node.parm(pname)
            if p is not None:
                try:
                    p.set(colorspace)
                except Exception:
                    pass
                break

        # For Houdini 20+, the filecolorspace parm controls OCIO
        fcs = img_node.parm("filecolorspace")
        if fcs is not None:
            try:
                fcs.set(colorspace)
            except Exception:
                pass

    def _wire_to_surface(self, surface_node: "hou.Node",
                         input_name: str,
                         source_node: "hou.Node"):
        """
        Wire a source node to a named input on the surface shader.
        Uses setNamedInput() which is the robust approach for Houdini 20+.
        """
        # Attempt 1: setNamedInput with output name
        try:
            surface_node.setNamedInput(input_name, source_node, "out")
            return
        except (hou.OperationFailed, hou.InvalidInput):
            pass

        # Attempt 2: setNamedInput with output index
        try:
            surface_node.setNamedInput(input_name, source_node, 0)
            return
        except (hou.OperationFailed, hou.InvalidInput):
            pass

        # Attempt 3: search input connectors by label
        try:
            for i, name in enumerate(surface_node.inputNames()):
                if input_name == name or input_name in name:
                    surface_node.setInput(i, source_node, 0)
                    return
        except Exception:
            pass

        # Attempt 4: search by label text
        try:
            for i, label in enumerate(surface_node.inputLabels()):
                if input_name.replace("_", " ").lower() in label.lower():
                    surface_node.setInput(i, source_node, 0)
                    return
        except Exception:
            pass

        print(f"[MtlXBuilder] Warning: Could not wire '{input_name}' "
              f"on {surface_node.path()}")

    def _wire_displacement(self, mat_builder: "hou.Node",
                           output_node: "hou.Node",
                           img_node: "hou.Node"):
        """Wire displacement texture to the material output."""
        # Create mtlxdisplacement node
        try:
            disp = mat_builder.createNode("mtlxdisplacement", "displacement_out")
            # Connect image -> displacement node
            try:
                disp.setNamedInput("displacement", img_node, "out")
            except Exception:
                disp.setInput(0, img_node, 0)

            # Connect displacement -> material output
            if output_node:
                try:
                    output_node.setNamedInput("displacement", disp, "out")
                except Exception:
                    # Try second input (surface is usually input 0)
                    try:
                        output_node.setInput(1, disp, 0)
                    except Exception:
                        pass
        except hou.OperationFailed as e:
            print(f"[MtlXBuilder] Displacement setup skipped: {e}")

    def _wire_ao_multiply(self, mat_builder: "hou.Node",
                          surface_node: "hou.Node"):
        """
        Multiply AO into the base_color input by inserting a
        mtlxmultiply node between the base_color texture and
        the surface shader.
        """
        # Find the AO texture node
        ao_node = mat_builder.node("ao_tex")
        if ao_node is None:
            return

        # Find what's currently connected to base_color
        bc_source = None
        try:
            for conn in surface_node.inputConnections():
                in_name = surface_node.inputNames()[conn.inputIndex()]
                if "base_color" in in_name:
                    bc_source = conn.inputNode()
                    bc_out_idx = conn.outputIndex()
                    bc_in_idx = conn.inputIndex()
                    break
        except Exception:
            return

        if bc_source is None:
            return

        try:
            # Create multiply node
            multiply = mat_builder.createNode("mtlxmultiply", "ao_multiply")
            multiply.parm("signature").set("color3")

            # Wire: base_color_tex -> multiply.in1
            try:
                multiply.setNamedInput("in1", bc_source, "out")
            except Exception:
                multiply.setInput(0, bc_source, bc_out_idx)

            # Wire: ao_tex -> multiply.in2
            try:
                multiply.setNamedInput("in2", ao_node, "out")
            except Exception:
                multiply.setInput(1, ao_node, 0)

            # Wire: multiply -> surface.base_color
            surface_node.setInput(bc_in_idx, multiply, 0)

        except Exception as e:
            print(f"[MtlXBuilder] AO multiply setup failed: {e}")
