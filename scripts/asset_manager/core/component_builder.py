"""
USD Component Builder automation for the Asset Manager.

Uses Houdini's native Solaris Component Builder LOPs:
    componentgeometry -> componentmaterial -> componentoutput
                              ^
                              |
                       materiallibrary

`componentoutput` has built-in thumbnail rendering — we plug a
camera + HDRI-lit dome light into its *second* input and trigger the
`thumbmakeicon` button, so each asset writes its USD *and* its
thumbnail in one pass through the same network.
"""

import os
import shutil
from typing import Dict, Optional

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from ..database.models import AssetEntry, ScanResult, TextureSet
from .usd_utils import ensure_hou, load_renderer_settings
from .materialx_builder import MaterialXBuilder


# Hydra delegate identifiers used by componentoutput's `thumbrenderer` parm.
# componentoutput accepts the string label shown in its menu; these are
# the canonical names for the renderers we support.
_RENDERER_DELEGATE = {
    "karma":    "Karma CPU",
    "karmaxpu": "Karma XPU",
    "arnold":   "Arnold",
    "redshift": "Redshift",
}


def _set_if_exists(node, parm_name, value):
    """Set a parm only if it exists. Returns True on success."""
    try:
        p = node.parm(parm_name)
        if p is None:
            return False
        p.set(value)
        return True
    except Exception:
        return False


def _set_tuple_if_exists(node, parm_name, values):
    try:
        tup = node.parmTuple(parm_name)
        if tup is None:
            return False
        tup.set(values)
        return True
    except Exception:
        return False


def _create_first_available(parent, candidates, name):
    """Try each node type in `candidates`; return the first that creates."""
    last_err = None
    for type_name in candidates:
        if not type_name:
            continue
        try:
            return parent.createNode(type_name, name)
        except hou.OperationFailed as e:
            last_err = e
            continue
    if last_err:
        print(f"[ComponentBuilder] No node type matched {candidates}: {last_err}")
    return None


class ComponentBuilder:
    """
    Build a complete component asset using the standard Houdini Solaris
    Component Builder workflow, with HDRI-lit thumbnail rendering driven
    by `componentoutput`'s built-in thumbnail generator.
    """

    def __init__(self, renderer: str = "karma_cpu",
                 proxy_ratio: float = 0.1,
                 sim_method: str = "convex_hull",
                 hdri_path: str = "",
                 thumbnail_resolution=(640, 480),
                 asset_scale: float = 1.0,
                 camera_yaw: float = 35.0,
                 camera_pitch: float = 20.0,
                 thumb_distance: float = 0.0,
                 karma_samples: int = 64,
                 camera_focal: float = 40.0,
                 camera_aperture: float = 25.0,
                 camera_near: float = 0.1,
                 camera_far: float = 1_000_000.0,
                 turntable_settings: Optional[dict] = None):
        self._renderer = renderer
        self._proxy_ratio = max(0.01, min(1.0, proxy_ratio))
        self._sim_method = sim_method
        self._hdri_path = hdri_path or ""
        self._thumb_res = thumbnail_resolution
        self._asset_scale = float(asset_scale)
        self._camera_yaw = float(camera_yaw)
        self._camera_pitch = float(camera_pitch)
        self._thumb_distance = float(thumb_distance)
        self._karma_samples = int(max(1, karma_samples))
        self._camera_focal = float(camera_focal)
        self._camera_aperture = float(camera_aperture)
        self._camera_near = float(camera_near)
        self._camera_far = float(camera_far)
        self._turntable_settings = turntable_settings or {}
        # MaterialX builder doesn't care about CPU/XPU split — both use Karma.
        mtlx_renderer = "karma" if renderer.startswith("karma") else renderer
        self._mtlx_builder = MaterialXBuilder(mtlx_renderer)
        self._settings = load_renderer_settings()
        self._renderer_config = self._settings["renderers"].get(
            renderer,
            self._settings["renderers"].get(
                "karma_cpu", self._settings["renderers"]["karma"]
            ),
        )

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def build_asset(self, scan_result: ScanResult,
                    output_dir: str,
                    thumbnail_dir: str = "",
                    parent_node: Optional["hou.Node"] = None) -> AssetEntry:
        """
        Build a component asset and render its thumbnail in one network.

        Returns:
            AssetEntry with usd_output_path + thumbnail_path populated.
        """
        ensure_hou()

        name = scan_result.asset_name
        asset_dir = os.path.join(output_dir, "assets", name).replace("\\", "/")
        os.makedirs(asset_dir, exist_ok=True)
        if not thumbnail_dir:
            thumbnail_dir = os.path.join(output_dir, "thumbnails")
        thumbnail_dir = thumbnail_dir.replace("\\", "/")
        os.makedirs(thumbnail_dir, exist_ok=True)

        if parent_node is None:
            parent_node = hou.node("/stage")

        # Wipe any stale build subnet for this asset so re-runs are clean.
        self._purge_stale_build_state(parent_node, name)
        build_subnet = parent_node.createNode("subnet", f"build_{name}")

        try:
            cgeo = self._make_component_geometry(build_subnet, scan_result)
            matlib, material_prim = self._make_material_library(
                build_subnet, name, scan_result.texture_set
            )
            cmat = self._make_component_material(
                build_subnet, cgeo, matlib, material_prim
            )
            cam_chain = self._make_thumbnail_camera_and_light(build_subnet, name)
            cout = self._make_component_output(
                build_subnet, cmat, cam_chain, name, asset_dir, thumbnail_dir
            )
            build_subnet.layoutChildren()

            # Texture info → JSON sidecar / gallery overlay/tooltip.
            tex_info = {}
            if scan_result.texture_set:
                tex_info = scan_result.texture_set.get_populated_maps()
            usd_path, thumb_path = self._execute(
                cout, name, asset_dir, thumbnail_dir,
                texture_info=tex_info,
                source_dir=scan_result.source_dir,
            )

            # Turntable rendering is opt-in — driven by the Gallery's
            # right-click → "Process Turntable" action, not the build.

            return AssetEntry(
                name=name,
                source_geo_path=scan_result.geo_file,
                source_texture_dir=scan_result.source_dir,
                usd_output_path=usd_path,
                thumbnail_path=thumb_path,
                material_layer_path="",
                renderer=self._renderer,
                proxy_ratio=self._proxy_ratio,
                sim_method=self._sim_method,
                status="ready" if (usd_path and os.path.exists(usd_path)) else "error",
                error_message="" if usd_path else "componentoutput did not produce USD",
            )
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[ComponentBuilder] Error building {name}: {e}")
            return AssetEntry(
                name=name,
                source_geo_path=scan_result.geo_file,
                source_texture_dir=scan_result.source_dir,
                status="error",
                error_message=str(e),
            )

    # ──────────────────────────────────────────────
    # 1. componentgeometry  (default / proxy / simproxy)
    # ──────────────────────────────────────────────

    def _make_component_geometry(self, parent, scan_result: ScanResult):
        cgeo = _create_first_available(
            parent, ["componentgeometry"], f"{scan_result.asset_name}_geo"
        )
        if cgeo is None:
            raise RuntimeError("componentgeometry LOP is not available")

        # H21 componentgeometry topology:
        #   cgeo  →  sopnet (fixed plumbing: purpose attribs, input switches,
        #            material-bind fixers — DO NOT add user SOPs here)
        #     └─ geo (subnet)  ← user SOPs go in this inner subnet
        #          ├─ default  (output node, index 0)
        #          ├─ proxy    (output node, index 1)
        #          └─ simproxy (output node, index 2)
        # `sourceinput` parm (default 0 = "Internal SOP Network") picks this
        # branch; the H20-and-earlier parm `sourcemode` no longer exists.
        try:
            cgeo.allowEditingOfContents(propagate=True)
        except Exception as e:
            print(f"[ComponentBuilder] allowEditingOfContents failed: {e}")

        _set_if_exists(cgeo, "geovariantname", scan_result.asset_name)
        _set_if_exists(cgeo, "sourceinput", 0)  # Internal SOP Network

        sopnet = cgeo.node("sopnet")
        if sopnet is None:
            for child in cgeo.children():
                if child.childTypeCategory() == hou.sopNodeTypeCategory():
                    sopnet = child
                    break
        if sopnet is None:
            raise RuntimeError(
                "componentgeometry has no editable SOP subnet inside"
            )
        try:
            sopnet.allowEditingOfContents(propagate=True)
        except Exception:
            pass

        geo_sub = sopnet.node("geo")
        if geo_sub is None:
            raise RuntimeError(
                "componentgeometry's sopnet has no inner 'geo' subnet"
            )
        try:
            geo_sub.allowEditingOfContents(propagate=True)
        except Exception:
            pass

        default_out = geo_sub.node("default")
        proxy_out   = geo_sub.node("proxy")
        sim_out     = geo_sub.node("simproxy")

        geo_path = scan_result.geo_file.replace("\\", "/")

        # ─ Default (render) chain: Loader → matchsize → xform → output.
        # matchsize re-centers the asset at world origin so the thumbnail
        # camera's bbox-fit math works against a known pivot. Then xform
        # applies the user's Asset Scale around that origin.
        loader = self._make_geo_loader(geo_sub, geo_path,
                                        scan_result.asset_name)

        # SOP xform sits before matchsize so any source-space transform
        # (kept at identity here — scale is applied at LOP level via the
        # transform LOPs in _make_component_output and _render_thumbnail)
        # is followed by a re-centering pass.
        xform = geo_sub.createNode("xform", "source_xform")
        xform.setInput(0, loader, 0)

        match = geo_sub.createNode("matchsize", "center_at_origin")
        match.setInput(0, xform, 0)
        # Justify menu: 0=Min, 1=Center, 2=Max — center on every axis.
        _set_if_exists(match, "justify_x", 1)
        _set_if_exists(match, "justify_y", 1)
        _set_if_exists(match, "justify_z", 1)
        # Translate only — scaling happens at LOP level.
        _set_if_exists(match, "dotranslate", 1)
        _set_if_exists(match, "doscale", 0)

        if default_out is not None:
            default_out.setInput(0, match, 0)

        # ─ Proxy chain: PolyReduce off the centered geo so the
        # proxy/sim layers stay aligned with the render layer.
        if proxy_out is not None:
            try:
                try:
                    polyreduce = geo_sub.createNode("polyreduce::2.0", "proxy_reduce")
                except hou.OperationFailed:
                    polyreduce = geo_sub.createNode("polyreduce", "proxy_reduce")
                polyreduce.setInput(0, match, 0)
                _set_if_exists(polyreduce, "percentage", self._proxy_ratio * 100.0)
                _set_if_exists(polyreduce, "quality", 1)
                proxy_out.setInput(0, polyreduce, 0)
            except Exception as e:
                print(f"[ComponentBuilder] Proxy generation skipped: {e}")

        # ─ Sim chain: convex / vdb / decimated based on chosen method
        if sim_out is not None:
            try:
                sim_node = self._make_sim_sop(geo_sub, match)
                if sim_node is not None:
                    sim_out.setInput(0, sim_node, 0)
            except Exception as e:
                print(f"[ComponentBuilder] Sim mesh generation skipped: {e}")

        try:
            geo_sub.layoutChildren()
        except Exception:
            pass

        # Sanity: confirm the default-output stage cooks with real geometry
        # AND that the source's UVs / normals survived the chain. If has_uv
        # is False the textures won't map correctly.
        try:
            g = default_out.geometry() if default_out is not None else None
            if g is None:
                print(f"[ComponentBuilder] {scan_result.asset_name}: "
                      f"default output produced no geometry")
            else:
                def _has(name):
                    for getter in ("vertexAttribs", "pointAttribs"):
                        try:
                            if any(a.name() == name
                                   for a in getattr(g, getter)()):
                                return True
                        except Exception:
                            pass
                    return False
                print(f"[ComponentBuilder] {scan_result.asset_name}: "
                      f"default points={len(g.points())} "
                      f"prims={len(g.prims())} "
                      f"has_uv={_has('uv')} has_N={_has('N')}")
        except Exception as e:
            print(f"[ComponentBuilder] {scan_result.asset_name}: "
                  f"default cook failed: {e}")

        return cgeo

    def _make_geo_loader(self, geo_sub, geo_path: str, asset_name: str):
        """Pick the right SOP to load `geo_path`.

        Alembic (.abc) routes through the dedicated Alembic SOP so any
        object hierarchy / animation / packed-primitive metadata survives
        instead of being flattened by the generic File SOP. The Alembic
        SOP is forced into 'Unpacked Houdini Geometry' mode so the rest
        of the chain (matchsize, polyreduce, materialbinds) operates on
        plain polygons.

        Anything else falls back to the File SOP.
        """
        lower = geo_path.lower()

        if lower.endswith(".abc"):
            abc = _create_first_available(
                geo_sub, ["alembic"], "abc_in",
            )
            if abc is None:
                # No Alembic SOP available in this build — fall back to
                # the File SOP, which can still read static .abc files.
                print(f"[ComponentBuilder] {asset_name}: alembic SOP "
                      f"unavailable, falling back to File SOP")
                file_sop = geo_sub.createNode("file", "file_in")
                _set_if_exists(file_sop, "file", geo_path)
                return file_sop

            # File path — H17→H21 parm name candidates.
            for pn in ("fileName", "filename", "abcfile", "file"):
                if _set_if_exists(abc, pn, geo_path):
                    break

            # Load as plain Houdini geometry rather than packed primitives.
            # Parm name is "loadmode" in H20/H21 (0 = Houdini geometry,
            # 1 = packed). Older releases used "loadasprim" / "viewportlod".
            loadmode_set = False
            for pn in ("loadmode", "loadAs", "load_as"):
                if _set_if_exists(abc, pn, 0):
                    loadmode_set = True
                    break
            if not loadmode_set:
                # Last-ditch: viewportlod=Full (some builds) so downstream
                # SOPs can still operate on the data.
                _set_if_exists(abc, "viewportlod", "full")

            # Don't restrict to a sub-object — load the whole hierarchy
            # so multi-mesh Alembics come through merged.
            for pn in ("objectPath", "objectpath"):
                _set_if_exists(abc, pn, "/")

            # Polygons (not subdiv) so the matchsize / polyreduce path
            # behaves like it does for OBJ/FBX/BGEO inputs.
            for pn in ("polysoup", "polyssoup"):
                _set_if_exists(abc, pn, 0)

            # If the Alembic carries packed prims even with loadmode=0
            # (some H21 builds), an explicit unpack guarantees plain geo.
            try:
                unpack = geo_sub.createNode("unpack", "abc_unpack")
                unpack.setInput(0, abc, 0)
                return unpack
            except hou.OperationFailed:
                return abc

        # Default: File SOP handles OBJ / FBX / BGEO / BGEO.SC / USD.
        file_sop = geo_sub.createNode("file", "file_in")
        _set_if_exists(file_sop, "file", geo_path)
        return file_sop

    def _make_sim_sop(self, sop_parent, source_sop):
        method = self._sim_method
        if method == "convex_hull":
            try:
                node = sop_parent.createNode("convexdecomposition", "sim_convex")
                node.setInput(0, source_sop, 0)
                return node
            except hou.OperationFailed:
                method = "vdb_remesh"
        if method == "vdb_remesh":
            try:
                vdb_from = sop_parent.createNode("vdbfrompolygons", "sim_vdb_from")
                vdb_from.setInput(0, source_sop, 0)
                _set_if_exists(vdb_from, "voxelsize", 0.03)
                convert = sop_parent.createNode("convertvdb", "sim_vdb")
                convert.setInput(0, vdb_from, 0)
                return convert
            except Exception:
                method = "decimated"
        try:
            try:
                pr = sop_parent.createNode("polyreduce::2.0", "sim_decimate")
            except hou.OperationFailed:
                pr = sop_parent.createNode("polyreduce", "sim_decimate")
            pr.setInput(0, source_sop, 0)
            _set_if_exists(pr, "percentage", 5.0)
            return pr
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # 2. materiallibrary  (MaterialXBuilder fills it)
    # ──────────────────────────────────────────────

    def _make_material_library(self, parent, name, texture_set: TextureSet):
        matlib = _create_first_available(
            parent, ["materiallibrary"], f"{name}_materials"
        )
        if matlib is None:
            raise RuntimeError("materiallibrary LOP is not available")

        # Component Builder convention: all materials live at /ASSET/mtl/.
        # componentgeometry creates /ASSET; componentmaterial binds against
        # this same path; so the three nodes line up.
        _set_if_exists(matlib, "matpathprefix", "/ASSET/mtl/")

        material_prim = ""
        if texture_set and texture_set.has_textures():
            maps = texture_set.get_populated_maps()
            print(f"[ComponentBuilder] {name}: building material with "
                  f"{len(maps)} maps: {list(maps.keys())}")
            try:
                mat_info = self._mtlx_builder.build_material(
                    matlib, name, texture_set
                )
                material_prim = "/ASSET/mtl/" + mat_info.name
                # In flat mode the shader nodes are siblings inside the
                # materiallibrary itself, so count its children directly.
                mat_node = matlib.node(mat_info.name)
                shader_kids = [c.name() for c in matlib.children()]
                print(f"[ComponentBuilder] {name}: material '{mat_info.name}' "
                      f"target={material_prim} | "
                      f"surface node {'FOUND' if mat_node else 'MISSING'} "
                      f"| matlib children: {shader_kids}")
            except Exception as e:
                import traceback
                traceback.print_exc()
                print(f"[ComponentBuilder] {name}: Material build failed: {e}")
        else:
            n = texture_set.get_map_count() if texture_set else 0
            print(f"[ComponentBuilder] {name}: skipping material build "
                  f"(texture_set has {n} maps)")
        return matlib, material_prim

    # ──────────────────────────────────────────────
    # 3. componentmaterial  (binds materiallibrary to componentgeometry)
    # ──────────────────────────────────────────────

    def _make_component_material(self, parent, cgeo, matlib, material_prim):
        cmat = _create_first_available(
            parent, ["componentmaterial"], f"{cgeo.name()}_cmat"
        )
        if cmat is None:
            raise RuntimeError("componentmaterial LOP is not available")
        cmat.setInput(0, cgeo, 0)
        cmat.setInput(1, matlib, 0)

        if material_prim:
            # Add one binding rule via the standard multiparm. componentmaterial
            # uses `nummaterials` + `primpattern#` + `matspecpath#`.
            _set_if_exists(cmat, "nummaterials", 1)
            # Bind the material to everything under the asset's geo prim.
            _set_if_exists(cmat, "primpattern1", "%type:Mesh")
            _set_if_exists(cmat, "matspecpath1", material_prim)
        return cmat

    # ──────────────────────────────────────────────
    # 4. Camera + HDRI dome for componentoutput's second input
    # ──────────────────────────────────────────────

    def _make_thumbnail_camera_and_light(self, parent, name):
        """Build the camera + dome light chain that componentoutput uses
        for thumbnail rendering. Returns the last node in the chain (or
        None if neither could be created)."""
        cam = _create_first_available(parent, ["camera"], f"{name}_thumb_cam")
        last = None
        if cam is not None:
            _set_if_exists(cam, "primpath", f"/cameras/{name}_thumb_cam")
            # Reasonable framing — componentoutput's auto-cam mode is the
            # fallback if you'd rather skip this.
            for p, v in (("tx", 2.2), ("ty", 1.6), ("tz", 2.2),
                         ("rx", -25.0), ("ry", 45.0), ("rz", 0.0)):
                _set_if_exists(cam, p, v)
            last = cam

        dome = _create_first_available(
            parent,
            ["domelight::3.0", "domelight::2.0", "domelight"],
            f"{name}_thumb_dome",
        )
        if dome is not None:
            if last is not None:
                dome.setInput(0, last, 0)
            _set_if_exists(dome, "primpath", "/lights/thumb_dome")
            _set_if_exists(dome, "intensity", 1.0)
            if self._hdri_path and os.path.exists(self._hdri_path):
                for parm_name in ("texturefile",
                                  "xn__inputstexturefile_r3ah",
                                  "inputs:texture:file"):
                    if _set_if_exists(dome, parm_name, self._hdri_path):
                        break
            last = dome

        return last

    # ──────────────────────────────────────────────
    # 5. componentoutput  (writes USD + thumbnail)
    # ──────────────────────────────────────────────

    def _make_component_output(self, parent, cmat, cam_chain,
                               name, asset_dir, thumbnail_dir):
        cout = _create_first_available(
            parent, ["componentoutput"], f"{name}_output"
        )
        if cout is None:
            raise RuntimeError("componentoutput LOP is not available")

        # Apply asset_scale via a transform LOP targeting /ASSET, placed
        # between componentmaterial and componentoutput.
        upstream = cmat
        if abs(self._asset_scale - 1.0) > 1e-9:
            xf_lop = parent.createNode("xform", f"{name}_scale")
            xf_lop.setInput(0, cmat, 0)
            _set_if_exists(xf_lop, "primpattern", "/ASSET")
            if not _set_tuple_if_exists(
                xf_lop, "s",
                (self._asset_scale, self._asset_scale, self._asset_scale),
            ):
                _set_if_exists(xf_lop, "sx", self._asset_scale)
                _set_if_exists(xf_lop, "sy", self._asset_scale)
                _set_if_exists(xf_lop, "sz", self._asset_scale)
            ms_lop = parent.createNode("matchsize", f"{name}_scale_match")
            ms_lop.setInput(0, xf_lop, 0)
            _set_if_exists(ms_lop, "primpattern", "/ASSET")
            # CRITICAL: matchsize LOP defaults to "scale to fit unit bbox",
            # which would override our explicit asset_scale and make every
            # thumbnail render at the same apparent size. Configure it to
            # ONLY re-center the asset, leaving the scale untouched.
            self._configure_matchsize_center_only(ms_lop, name)
            upstream = ms_lop

        cout.setInput(0, upstream, 0)
        if cam_chain is not None:
            cout.setInput(1, cam_chain, 0)

        # H21 componentoutput honors `lopoutput` regardless of `mode`, so we
        # just set the explicit path. Setting `location` / `componentname`
        # via parm.set() is silently ignored in this version.
        explicit_path = os.path.join(asset_dir, f"{name}.usd").replace("\\", "/")
        _set_if_exists(cout, "lopoutput", explicit_path)
        # componentgeometry outputs prims under /ASSET — match that.
        _set_if_exists(cout, "rootprim", "/ASSET")
        # Source = "Input Primitives": export whatever arrives on input 0
        # rather than walking the internal SOP network again. The parm name
        # and accepted value form drift across Houdini versions — try the
        # known candidates and dump the menu options if none stuck.
        self._set_componentoutput_source_input(cout, name)
        # `flattenstage` = "Collapse All Sublayers and References" — produces
        # a single self-contained USD.
        _set_if_exists(cout, "savestyle", "flattenstage")

        # Built-in thumbnail config — Karma + automatic camera + user spin/
        # pitch/distance. Using componentoutput's own thumbnail (not an
        # external Karma ROP) so the camera auto-frames the asset instead
        # of using a fixed position that misses huge/scaled meshes.
        self._configure_thumbnail(cout, name, thumbnail_dir)
        return cout

    def _bbox_from_lop_stage(self, lop_node, name):
        """Query the world-space bbox of /ASSET directly from the LOP
        node's live output stage. Returns `(center_tuple, radius)`.

        This is the authoritative source for camera framing because the
        stage held by the LOP node is exactly what its downstream
        consumers (the camera, the Karma ROP) will render. Reading from
        the LOP stage avoids file-on-disk staleness and reference
        resolution issues that bite the pxr-open-file path.
        """
        import math
        center = (0.0, 0.0, 0.0)
        radius = 1.0
        try:
            # Force the node to cook so .stage() reflects the latest
            # upstream changes (the reference may have just been wired).
            try:
                lop_node.cook(force=True)
            except Exception:
                pass
            stage = lop_node.stage()
            if stage is None:
                print(f"[Thumb] {name}: LOP stage is None on {lop_node.path()}")
                return center, radius
            from pxr import Usd, UsdGeom
            prim = stage.GetPrimAtPath("/ASSET")
            prim_path = "/ASSET"
            if not prim or not prim.IsValid():
                prim = stage.GetDefaultPrim() or stage.GetPseudoRoot()
                prim_path = prim.GetPath().pathString if prim else "<none>"
            if not prim or not prim.IsValid():
                print(f"[Thumb] {name}: no valid prim in LOP stage")
                return center, radius
            bcache = UsdGeom.BBoxCache(
                Usd.TimeCode.Default(),
                includedPurposes=[
                    UsdGeom.Tokens.default_, UsdGeom.Tokens.render,
                ],
            )
            rng = bcache.ComputeWorldBound(prim).GetRange()
            if rng.IsEmpty():
                print(
                    f"[Thumb] {name}: LOP bbox empty on prim {prim_path}; "
                    f"using defaults"
                )
                return center, radius
            mn, mx = rng.GetMin(), rng.GetMax()
            center = (
                (mn[0] + mx[0]) / 2.0,
                (mn[1] + mx[1]) / 2.0,
                (mn[2] + mx[2]) / 2.0,
            )
            size = (mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2])
            radius = math.sqrt(size[0]**2 + size[1]**2 + size[2]**2) / 2
            print(
                f"[Thumb] {name}: LOP bbox on {prim_path}: "
                f"min={tuple(mn)} max={tuple(mx)} size={size}"
            )
        except Exception as e:
            print(f"[Thumb] {name}: LOP bbox query failed ({e})")
        return center, radius

    def _configure_matchsize_center_only(self, ms_lop, name):
        """Set the matchsize LOP to re-center the asset WITHOUT rescaling.

        The H21 matchsize LOP defaults to "scale to fit a unit bounding
        box" which silently overrides any upstream scale. We don't want
        that — we want the explicit asset_scale to be preserved and only
        the position to be normalized to origin.

        Parm names drift across versions, so probe by menu label:
          • Size matching parm → set to the menu entry containing "off"
            / "none" / "no scale".
          • Position matching parm → set to "centroid" / "center".
        Falls back to integer 0 / 1 if no label match is found.
        """
        def _menu(p):
            try:
                items = list(p.menuItems()) if hasattr(p, "menuItems") else []
            except Exception:
                items = []
            try:
                labels = list(p.menuLabels()) if hasattr(p, "menuLabels") else []
            except Exception:
                labels = []
            return items, labels

        def _set_menu_by_keywords(parm_keywords, label_keywords,
                                  fallback_index, label_for_log):
            """Find a parm whose name contains any of `parm_keywords` and
            whose menu has a label containing any of `label_keywords`;
            set it to that menu entry."""
            for p in ms_lop.parms():
                pname = p.name().lower()
                if not any(k in pname for k in parm_keywords):
                    continue
                items, labels = _menu(p)
                if not labels:
                    # Boolean / int parm with no menu — set the fallback.
                    try:
                        p.set(fallback_index)
                        print(
                            f"[MatchSize] {name}: {p.name()}={fallback_index} "
                            f"({label_for_log}, no menu)"
                        )
                        return True
                    except Exception:
                        continue
                for i, lbl in enumerate(labels):
                    if any(kw in lbl.lower() for kw in label_keywords):
                        val = items[i] if i < len(items) else i
                        try:
                            p.set(val)
                            print(
                                f"[MatchSize] {name}: {p.name()}={val!r} → "
                                f"{lbl!r} ({label_for_log})"
                            )
                            return True
                        except Exception:
                            try:
                                p.set(i)
                                print(
                                    f"[MatchSize] {name}: {p.name()}={i} → "
                                    f"{lbl!r} ({label_for_log})"
                                )
                                return True
                            except Exception:
                                pass
            return False

        # Disable size matching.
        if not _set_menu_by_keywords(
            parm_keywords=("size", "scale"),
            label_keywords=("off", "none", "no scal"),
            fallback_index=0,
            label_for_log="disable scaling",
        ):
            # Last resort: any boolean parm starting with 'doscale' /
            # 'scaletofit' → 0.
            for cand in ("doscale", "scaletofit", "applyscale"):
                if _set_if_exists(ms_lop, cand, 0):
                    print(f"[MatchSize] {name}: {cand}=0 (disable scale, hard fallback)")
                    break
            else:
                print(f"[MatchSize] {name}: WARNING — no scale-disable parm found")

        # Enable centroid-based position matching.
        if not _set_menu_by_keywords(
            parm_keywords=("position", "translate", "center"),
            label_keywords=("centroid", "center", "origin"),
            fallback_index=1,
            label_for_log="center to origin",
        ):
            for cand in ("dotranslate", "applytranslate", "matchposition"):
                if _set_if_exists(ms_lop, cand, 1):
                    print(f"[MatchSize] {name}: {cand}=1 (enable center, hard fallback)")
                    break
            else:
                print(f"[MatchSize] {name}: WARNING — no position parm found")

    def _set_componentoutput_source_input(self, cout, name):
        """Set componentoutput's Source dropdown to 'Input Primitives'.

        Strategy: walk every parm on `cout` whose menu labels include
        'Input Primitives', then set it with the corresponding menu *token*
        (and fall back to the integer index). Reading back uses both
        `evalAsString` and integer eval so we can verify against either a
        string-valued ordered menu or an integer menu.
        """
        target_label = "Input Primitives"

        def _menu(parm):
            try:
                items = list(parm.menuItems()) if hasattr(parm, "menuItems") else []
            except Exception:
                items = []
            try:
                labels = list(parm.menuLabels()) if hasattr(parm, "menuLabels") else []
            except Exception:
                labels = []
            return items, labels

        def _current_label(parm, items, labels):
            try:
                tok = parm.evalAsString() if hasattr(parm, "evalAsString") else None
            except Exception:
                tok = None
            if tok and items:
                for i, t in enumerate(items):
                    if t == tok and i < len(labels):
                        return labels[i]
            try:
                idx = parm.eval()
            except Exception:
                idx = None
            if isinstance(idx, int) and 0 <= idx < len(labels):
                return labels[idx]
            return None

        # Scan every parm; pick those whose menu actually contains the label.
        candidates = []
        for p in cout.parms():
            items, labels = _menu(p)
            if target_label in labels:
                candidates.append((p, items, labels))

        if not candidates:
            # Dump every menu parm so we can identify the right one.
            menu_parms = []
            for p in cout.parms():
                items, labels = _menu(p)
                if labels:
                    menu_parms.append(
                        f"  {p.name()} (label={p.description()!r}, "
                        f"items={items}, labels={labels})"
                    )
            print(
                f"[ComponentOutput] {name}: NO parm on this componentoutput "
                f"has a menu label 'Input Primitives'. All menu parms:\n"
                + "\n".join(menu_parms)
            )
            return

        for p, items, labels in candidates:
            idx = labels.index(target_label)
            attempts = []
            if idx < len(items):
                attempts.append(items[idx])  # string token (most reliable)
            attempts.append(idx)             # integer index

            for val in attempts:
                try:
                    p.set(val)
                except Exception as err:
                    print(
                        f"[ComponentOutput] {name}: {p.name()}.set({val!r}) "
                        f"raised {err}"
                    )
                    continue
                lbl = _current_label(p, items, labels)
                if lbl == target_label:
                    print(
                        f"[ComponentOutput] {name}: Source set via "
                        f"{p.name()}={val!r} → {target_label!r}"
                    )
                    return
                print(
                    f"[ComponentOutput] {name}: {p.name()}.set({val!r}) "
                    f"left value at {p.eval()!r} (label {lbl!r})"
                )

        print(
            f"[ComponentOutput] {name}: found menu parms with "
            f"'Input Primitives' but none accepted the value: "
            f"{[p.name() for p, _, _ in candidates]}"
        )

    def _configure_thumbnail(self, cout, name, thumbnail_dir):
        """Disable componentoutput's built-in thumbnail. We render via a
        separate Karma ROP with auto-framed camera (see
        `_render_thumbnail_karma`) — the built-in path didn't fire the
        actual render even with `autothumbnail=1` and `thumbnailfile` set
        on this H21 build."""
        _set_if_exists(cout, "autothumbnail", 0)

    # ──────────────────────────────────────────────
    # 6. Execute: write USD + render thumbnail
    # ──────────────────────────────────────────────

    def _execute(self, cout, name, asset_dir, thumbnail_dir,
                 texture_info: Optional[dict] = None,
                 source_dir: str = ""):
        """Write the USD via componentoutput's `execute` button, then press
        its built-in `thumbmakeicon` (or equivalent) to render the thumbnail
        with the Automatic camera + Karma. componentoutput's built-in
        thumbnail handles bbox auto-framing internally — no separate ROP."""
        usd_path = ""
        thumb_path = ""

        try:
            expanded_loc = cout.evalParm("lopoutput") or ""
            actual_savestyle = cout.evalParm("savestyle")
        except Exception:
            expanded_loc = ""
            actual_savestyle = "?"
        print(f"[ComponentBuilder] {name}: lopoutput='{expanded_loc}' "
              f"savestyle={actual_savestyle!r}")

        # Wipe stale duplicate USDs from prior broken runs.
        try:
            for stale in os.listdir(asset_dir):
                if "_duplicate" in stale and stale.lower().endswith(
                    (".usd", ".usda", ".usdc")
                ):
                    try:
                        os.remove(os.path.join(asset_dir, stale))
                    except OSError:
                        pass
        except OSError:
            pass

        # USD export.
        for btn_name in ("execute", "savetodisk", "executebackground"):
            btn = cout.parm(btn_name)
            if btn is None:
                continue
            try:
                btn.pressButton()
                print(f"[ComponentBuilder] Pressed {btn_name} for {name}")
                break
            except Exception as e:
                print(f"[ComponentBuilder] {btn_name} press failed: {e}")

        # Find the USD that was just written.
        if expanded_loc and os.path.exists(expanded_loc):
            usd_path = expanded_loc.replace("\\", "/")
        else:
            for cp in (os.path.join(asset_dir, f"{name}.usd"),
                       os.path.join(asset_dir, name, f"{name}.usd")):
                cp = cp.replace("\\", "/")
                if os.path.exists(cp):
                    usd_path = cp
                    break
        if not usd_path:
            for root, _d, files in os.walk(asset_dir):
                for f in files:
                    if f.lower().endswith((".usd", ".usda", ".usdc")):
                        usd_path = os.path.join(root, f).replace("\\", "/")
                        break
                if usd_path:
                    break
        print(f"[ComponentBuilder] {name}: USD = {usd_path or '<not found>'}")
        if not usd_path:
            return "", ""

        # Render the thumbnail via a standalone Karma ROP with auto-framed
        # camera. componentoutput's built-in thumbnail doesn't fire its
        # render in this H21 build, so we drive Karma ourselves.
        thumb_path = self._render_thumbnail_karma(
            cout.parent(), name, usd_path, thumbnail_dir,
            texture_info=texture_info, source_dir=source_dir,
        )
        return usd_path, thumb_path

    def _render_thumbnail_karma(self, parent, name, usd_path, thumbnail_dir,
                                 texture_info: Optional[dict] = None,
                                 source_dir: str = ""):
        """Render the thumbnail via a Karma ROP. The asset is already
        centered at origin by `matchsize` upstream, so the camera orbit
        around (0,0,0) at FOV-matched distance frames the full bbox.
        Resolution, samples, engine (CPU/XPU), and HDRI all come from
        the UI/settings.

        Args:
            texture_info: dict of {map_type: path} for the populated
                texture maps (recorded in the JSON sidecar so the
                gallery overlay/tooltip can display it).
            source_dir: the asset's source directory on disk.
        """
        import math, time
        os.makedirs(thumbnail_dir, exist_ok=True)
        thumb_target = os.path.join(thumbnail_dir, f"{name}.png").replace("\\", "/")

        # Camera placement — all values come from the Settings UI.
        focal    = self._camera_focal
        aperture = self._camera_aperture          # Horizontal Aperture
        near     = self._camera_near
        far      = self._camera_far
        # Vertical aperture derived from horizontal × render aspect ratio
        # so the film gate matches the output resolution exactly.
        aperture_y = aperture * (int(self._thumb_res[1]) /
                                  max(int(self._thumb_res[0]), 1))
        fov_deg = math.degrees(2.0 * math.atan((aperture / 2.0) / focal))
        margin = 1.1
        rx, ry = int(self._thumb_res[0]), int(self._thumb_res[1])
        samples = self._karma_samples
        engine = self._renderer_config.get("karma_engine", "CPU")

        # Destroy any prior thumbnail render subnet for this asset so we
        # don't pile up duplicates, but DON'T auto-destroy at the end —
        # leaving it in place lets the user inspect the network.
        existing = parent.node(f"{name}_thumb_render")
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass
        thumb_subnet = parent.createNode("subnet", f"{name}_thumb_render")
        try:
            ref = thumb_subnet.createNode("reference", f"{name}_ref")
            _set_if_exists(ref, "filepath1", usd_path)

            # Scale + centering are already baked into the USD by the build
            # chain (xform + matchsize between cmat and cout), so the
            # reference can feed the camera directly here.
            cam = thumb_subnet.createNode("camera", f"{name}_thumb_cam")
            cam.setInput(0, ref, 0)

            # Read the world-space bbox of /ASSET FROM THE LIVE LOP STAGE
            # at the camera's input (the ref node). This is exactly what
            # the camera will see, so the framing math is authoritative:
            # no file I/O staleness, no pxr-on-disk reference issues.
            center, radius = self._bbox_from_lop_stage(ref, name)
            distance = max(radius, 0.001) / math.sin(
                math.radians(fov_deg) / 2
            ) * margin + float(self._thumb_distance)
            yaw_deg = float(self._camera_yaw)
            pitch_deg = float(self._camera_pitch)
            yaw_r = math.radians(yaw_deg)
            pitch_r = math.radians(pitch_deg)
            cam_x = center[0] + distance * math.cos(pitch_r) * math.sin(yaw_r)
            cam_y = center[1] + distance * math.sin(pitch_r)
            cam_z = center[2] + distance * math.cos(pitch_r) * math.cos(yaw_r)
            print(
                f"[Thumb] {name}: LOP-stage center={center} radius={radius:.4f}"
                f" asset_scale={self._asset_scale:.4f} → distance={distance:.4f}"
                f" cam=({cam_x:.3f}, {cam_y:.3f}, {cam_z:.3f})"
            )

            # ── Diagnostic: dump ALL camera parm names so we can identify
            # the real H21 Solaris camera LOP parm names for focal/clip.
            try:
                print(f"[Thumb] {name}: CAMERA NODE TYPE = {cam.type().name()}")
                all_cam_parms = {}
                for _p in cam.parms():
                    try:
                        all_cam_parms[_p.name()] = _p.eval()
                    except Exception:
                        all_cam_parms[_p.name()] = "<eval-err>"
                print(f"[Thumb] {name}: ALL CAMERA PARMS = {all_cam_parms}")
            except Exception as _cam_diag_err:
                print(f"[Thumb] {name}: cam parm diag failed: {_cam_diag_err}")

            # primpath — several candidate names used across H19–H21.
            for _pp in ("primpath", "campath", "usdprimpath", "path"):
                if _set_if_exists(cam, _pp, "/cameras/thumb_cam"):
                    break

            if not _set_tuple_if_exists(
                cam, "t",
                (float(cam_x), float(cam_y), float(cam_z)),
            ):
                _set_if_exists(cam, "tx", float(cam_x))
                _set_if_exists(cam, "ty", float(cam_y))
                _set_if_exists(cam, "tz", float(cam_z))
            if not _set_tuple_if_exists(
                cam, "r", (-pitch_deg, yaw_deg, 0.0),
            ):
                _set_if_exists(cam, "rx", -pitch_deg)
                _set_if_exists(cam, "ry", yaw_deg)
                _set_if_exists(cam, "rz", 0.0)

            # Focal length — H21 LOP camera parm name candidates.
            focal_set = False
            for _fn in ("focal", "focallength", "focallen",
                        "xn__inputsfocalLength_4hag"):
                if _set_if_exists(cam, _fn, focal):
                    focal_set = True
                    print(f"[Thumb] {name}: focal set via parm '{_fn}'={focal}")
                    break
            if not focal_set:
                print(f"[Thumb] {name}: WARNING — could not set focal length")

            # Horizontal aperture = 50.
            for _an in ("aperturex", "aperture",
                        "xn__inputshorizontalAperture_j3ag"):
                if _set_if_exists(cam, _an, aperture):
                    break

            # Vertical aperture = 37.5 (horizontal × 3/4 for 4:3 aspect).
            for _ayn in ("aperturey", "vaperture", "vertaperture",
                         "xn__inputsverticalAperture_j3ag"):
                if _set_if_exists(cam, _ayn, aperture_y):
                    break

            # Aspect ratio — derived from render resolution.
            _aspect_float = rx / max(ry, 1)
            _set_if_exists(cam, "aspect", _aspect_float)
            _set_tuple_if_exists(cam, "aspectratio", (rx, ry))

            _set_if_exists(cam, "resx", rx)
            _set_if_exists(cam, "resy", ry)

            # Projection = Perspective.
            for pname, val in (("projection", "perspective"),
                               ("projection", 0),
                               ("projmode", "perspective"),
                               ("projmode", 0)):
                if _set_if_exists(cam, pname, val):
                    break

            # Clipping range — values from Settings UI.
            # Try tuple parms first, then individual scalar parms.
            clip_set = False
            for _ct in ("clip", "clipping", "clippingrange",
                        "xn__inputsclippingRange_v2ah"):
                if _set_tuple_if_exists(cam, _ct, (near, far)):
                    clip_set = True
                    print(f"[Thumb] {name}: clip set via tuple parm '{_ct}'")
                    break
            if not clip_set:
                for pn_near, pn_far in (("near", "far"),
                                        ("clipnear", "clipfar"),
                                        ("clip1", "clip2"),
                                        ("clippingnear", "clippingfar"),
                                        ("nearclip", "farclip"),
                                        ("znear", "zfar")):
                    if (_set_if_exists(cam, pn_near, near)
                            and _set_if_exists(cam, pn_far, far)):
                        clip_set = True
                        print(f"[Thumb] {name}: clip set via parms "
                              f"'{pn_near}'={near} '{pn_far}'={far}")
                        break
            if not clip_set:
                print(f"[Thumb] {name}: WARNING — could not set clip range")

            # ── Definitive USD-level binding ───────────────────────────
            # The camera LOP parm names drift between Houdini versions,
            # so we add a Python LOP that sets the USD Camera attributes
            # directly on the stage. This guarantees the render camera
            # receives focal / aperture / clipping range exactly as
            # configured in the Settings tab, regardless of which parm
            # names happened to take above.
            # H21 type name is "pythonscript"; older was "python".
            cam_override = None
            for _type in ("pythonscript", "python"):
                try:
                    cam_override = thumb_subnet.createNode(
                        _type, f"{name}_cam_attrs"
                    )
                    if cam_override is not None:
                        break
                except hou.OperationFailed:
                    cam_override = None
                    continue

            if cam_override is not None:
                cam_override.setInput(0, cam, 0)
                override_script = (
                    "from pxr import UsdGeom, Gf\n"
                    "stage = hou.pwd().editableStage()\n"
                    f"cam_path = '/cameras/thumb_cam'\n"
                    "prim = stage.GetPrimAtPath(cam_path)\n"
                    "if not prim or not prim.IsValid():\n"
                    "    cam = UsdGeom.Camera.Define(stage, cam_path)\n"
                    "else:\n"
                    "    cam = UsdGeom.Camera(prim)\n"
                    f"cam.CreateFocalLengthAttr().Set({focal})\n"
                    f"cam.CreateHorizontalApertureAttr().Set({aperture})\n"
                    f"cam.CreateVerticalApertureAttr().Set({aperture_y})\n"
                    f"cam.CreateClippingRangeAttr().Set("
                    f"Gf.Vec2f({near}, {far}))\n"
                    "cam.CreateProjectionAttr().Set(UsdGeom.Tokens.perspective)\n"
                    "print('[CamOverride] focal=' + "
                    "str(cam.GetFocalLengthAttr().Get()))\n"
                    "print('[CamOverride] aperture_h=' + "
                    "str(cam.GetHorizontalApertureAttr().Get()))\n"
                    "print('[CamOverride] aperture_v=' + "
                    "str(cam.GetVerticalApertureAttr().Get()))\n"
                    "print('[CamOverride] clip=' + "
                    "str(cam.GetClippingRangeAttr().Get()))\n"
                )
                _override_parm_set = False
                for _pn in ("python", "code", "script", "pythoncode"):
                    if _set_if_exists(cam_override, _pn, override_script):
                        _override_parm_set = True
                        print(f"[Thumb] {name}: cam override script set via "
                              f"parm '{_pn}' on '{cam_override.type().name()}'")
                        break
                if not _override_parm_set:
                    print(f"[Thumb] {name}: WARNING — could not set override "
                          f"script on '{cam_override.type().name()}'")
                cam_tail = cam_override
            else:
                print(f"[Thumb] {name}: no Python LOP type available — "
                      f"relying on parm-based camera binding only")
                cam_tail = cam

            dome = _create_first_available(
                thumb_subnet,
                ["domelight::3.0", "domelight::2.0", "domelight"],
                f"{name}_thumb_dome",
            )
            hdri_applied = False
            if dome is not None:
                dome.setInput(0, cam_tail, 0)
                _set_if_exists(dome, "primpath", "/lights/thumb_dome")
                _set_if_exists(dome, "intensity", 1.0)
                if self._hdri_path and os.path.exists(self._hdri_path):
                    for pn in ("xn__inputstexturefile_r3ah",
                               "inputs:texture:file",
                               "texturefile", "envmap", "file"):
                        if _set_if_exists(dome, pn, self._hdri_path):
                            hdri_applied = True
                            break
                last_lop = dome
            else:
                last_lop = cam_tail

            rop = _create_first_available(
                thumb_subnet, ["karma", "usdrender_rop"], f"{name}_thumb_rop"
            )
            if rop is None:
                print(f"[Thumb] {name}: no Karma ROP type available")
                return ""
            rop.setInput(0, last_lop, 0)

            # --- Output path ---
            for pn in ("picture", "outputimage", "outputname"):
                _set_if_exists(rop, pn, thumb_target)

            # --- Resolution override (must force override flag on too,
            # otherwise Karma uses the camera's resolution and ignores
            # these parms, which was producing the 1280x720 results). ---
            for pn in ("override_camerares", "override_resolution",
                       "overrideres", "overridecamerares"):
                _set_if_exists(rop, pn, 1)
            res_set = False
            if _set_tuple_if_exists(rop, "res", (rx, ry)):
                res_set = True
            elif _set_tuple_if_exists(rop, "resolution", (rx, ry)):
                res_set = True
            elif _set_tuple_if_exists(rop, "res_override", (rx, ry)):
                res_set = True
            else:
                for px, py in (("resx", "resy"), ("res1", "res2"),
                               ("resolutionx", "resolutiony"),
                               ("res_overridex", "res_overridey")):
                    if (_set_if_exists(rop, px, rx)
                            and _set_if_exists(rop, py, ry)):
                        res_set = True
                        break

            # --- Camera selection ---
            _set_if_exists(rop, "camera", "/cameras/thumb_cam")

            # --- Engine: CPU vs XPU ---
            for pn in ("engine", "render_engine", "karmaengine"):
                if _set_if_exists(rop, pn, engine):
                    break

            # --- Samples from UI ---
            samples_set = False
            for pn in ("pathtracedsamples", "samplesperpixel",
                       "pixelsamples", "camera_samples", "samples"):
                if _set_if_exists(rop, pn, samples):
                    samples_set = True
                    break

            thumb_subnet.layoutChildren()

            # Diagnostic: print what actually landed on the ROP so we can
            # confirm resolution + samples + engine took.
            try:
                snap = {}
                for p in rop.parms():
                    nl = p.name().lower()
                    if any(k in nl for k in (
                        "res", "sample", "engine", "picture", "output",
                        "camera", "override",
                    )):
                        try:
                            snap[p.name()] = p.eval()
                        except Exception:
                            pass
                print(f"[Thumb] {name}: rop parms = {snap}")
            except Exception:
                pass

            print(f"[Thumb] {name}: cam pos=({cam_x:.2f},{cam_y:.2f},"
                  f"{cam_z:.2f}) yaw={yaw_deg:.1f} pitch={pitch_deg:.1f} "
                  f"focal={focal:.1f} aperture={aperture}(h)/{aperture_y}(v) "
                  f"fov={fov_deg:.1f}° dist={distance:.2f} "
                  f"engine={engine} samples={samples} "
                  f"res=({rx},{ry}) res_set={res_set} "
                  f"hdri_applied={hdri_applied}")

            render_start = time.time()
            pressed = False
            for btn_name in ("execute", "executerender", "render"):
                btn = rop.parm(btn_name)
                if btn is None:
                    continue
                try:
                    btn.pressButton()
                    print(f"[Thumb] {name}: pressed Karma ROP {btn_name}")
                    pressed = True
                    break
                except Exception as e:
                    print(f"[Thumb] {name}: {btn_name} failed: {e}")
            if not pressed:
                print(f"[Thumb] {name}: no Karma ROP button matched")

            # Karma renders asynchronously in some H21 configurations —
            # pressButton() can return before the PNG hits disk. Block until
            # the file appears (up to 2 min) so the JSON sidecar is always
            # written on the *first* render, not just the second.
            if pressed and not os.path.exists(thumb_target):
                try:
                    hou.ui.waitForCook()
                except Exception:
                    pass
                if not os.path.exists(thumb_target):
                    _deadline = time.time() + 120
                    while time.time() < _deadline:
                        if os.path.exists(thumb_target):
                            break
                        time.sleep(0.5)

            render_elapsed = time.time() - render_start

            if os.path.exists(thumb_target):
                # Normalize texture info for JSON sidecar.
                tex_payload = {}
                if texture_info:
                    for k, v in texture_info.items():
                        if v:
                            tex_payload[k] = str(v).replace("\\", "/")
                # Write a JSON sidecar with render metadata.
                self._write_render_metadata(
                    thumb_target, name=name, usd_path=usd_path,
                    engine=engine, samples=samples,
                    resolution=(rx, ry),
                    yaw=yaw_deg, pitch=pitch_deg, distance=distance,
                    fov=fov_deg, focal=focal,
                    aperture_h=aperture, aperture_v=aperture_y,
                    clip_near=near, clip_far=far,
                    hdri=self._hdri_path if hdri_applied else "",
                    renderer_key=self._renderer,
                    render_time_seconds=round(render_elapsed, 2),
                    textures=tex_payload,
                    source_dir=source_dir.replace("\\", "/") if source_dir else "",
                )
                print(f"[Thumb] {name}: thumbnail = {thumb_target} "
                      f"(render {render_elapsed:.2f}s)")
                return thumb_target
            print(f"[Thumb] {name}: no PNG at {thumb_target} "
                  f"(elapsed {render_elapsed:.2f}s)")
            return ""
        except Exception:
            # On error: still destroy the partial subnet so the next run
            # is clean. On success we leave it for inspection.
            try:
                thumb_subnet.destroy()
            except Exception:
                pass
            raise

    @staticmethod
    def _write_render_metadata(thumb_path: str, **info):
        """Write a `.json` sidecar next to the PNG with the render config
        so the UI can display it and downstream tools can read it."""
        import json as _json
        try:
            sidecar = os.path.splitext(thumb_path)[0] + ".json"
            from datetime import datetime
            payload = {
                "rendered_at": datetime.now().isoformat(),
                "thumbnail": thumb_path,
                **info,
            }
            with open(sidecar, "w", encoding="utf-8") as f:
                _json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            print(f"[Thumb] metadata write failed: {e}")

    # ──────────────────────────────────────────────
    # Turntable rendering
    # ──────────────────────────────────────────────

    def _render_turntable(self, parent: "hou.Node", name: str,
                          usd_path: str, thumbnail_dir: str) -> str:
        """Render a multi-HDRI turntable for the asset.

        For each HDRI in the configured list, render one full 360° camera
        orbit. Frames are written to:
            <thumbnail_dir>/<name>_turntable/frame_NNNN.png

        Returns the turntable directory on success, "" on failure. The
        global frame range is set per cycle so frames are numbered
        contiguously across all cycles.
        """
        import math
        ensure_hou()

        tt = self._turntable_settings or {}
        hdris = list(tt.get("hdris") or [])
        if not hdris:
            # Fall back to the Settings HDRI (single cycle).
            if self._hdri_path:
                hdris = [self._hdri_path]
            else:
                print(f"[Turntable] {name}: no HDRIs configured — skipping")
                return ""

        frames_per_cycle = int(tt.get("frames_per_cycle", 72) or 72)
        if frames_per_cycle < 2:
            frames_per_cycle = 2
        direction = -1 if str(tt.get("direction", "cw")).lower() == "ccw" else 1
        axis = str(tt.get("rotation_axis", "Y")).upper()
        start_yaw = float(tt.get("start_yaw", 35.0))
        pitch_deg = float(tt.get("camera_pitch", 15.0))
        distance_offset = float(tt.get("distance_offset", 0.0))
        samples = max(1, int(tt.get("samples", 8) or 8))
        rx = max(64, int(tt.get("width", 384) or 384))
        ry = max(64, int(tt.get("height", 384) or 384))

        hdri_projection = str(tt.get("hdri_projection", "latlong")).lower()
        cal_enabled = bool(tt.get("cal_enabled", False))
        cal_show_chrome = bool(tt.get("cal_show_chrome", True))
        cal_show_grey = bool(tt.get("cal_show_grey", True))
        cal_show_macbeth = bool(tt.get("cal_show_macbeth", True))
        cal_distance = float(tt.get("cal_distance", 1.5))
        cal_offset_x = float(tt.get("cal_offset_x", -0.45))
        cal_offset_y = float(tt.get("cal_offset_y", -0.28))
        cal_scale = float(tt.get("cal_scale", 1.0))

        # Lens — inherit from Settings unless explicitly overridden.
        if tt.get("inherit_lens", True):
            focal = self._camera_focal
            aperture = self._camera_aperture
        else:
            focal = float(tt.get("focal", 50.0))
            aperture = float(tt.get("aperture", 36.0))
        aperture_y = aperture * (ry / max(rx, 1))
        fov_deg = math.degrees(2.0 * math.atan((aperture / 2.0) / focal))
        margin = 1.1

        # Engine: inherit Karma CPU/XPU split from active renderer config.
        engine = self._renderer_config.get("karma_engine", "CPU")

        turntable_dir = os.path.join(
            thumbnail_dir, f"{name}_turntable"
        ).replace("\\", "/")
        os.makedirs(turntable_dir, exist_ok=True)

        # One subnet per cycle so each render is isolated and the user can
        # inspect any cycle's network after the fact.
        existing = parent.node(f"{name}_turntable_render")
        if existing is not None:
            try:
                existing.destroy()
            except Exception:
                pass
        tt_subnet = parent.createNode("subnet", f"{name}_turntable_render")

        # Save the user's current playbar range so we can restore it after
        # all cycles render — we widen it per cycle as a safety net for
        # any ROP that reads $FSTART/$FEND from the playbar.
        saved_playback_range = None
        saved_current_frame = None
        try:
            saved_playback_range = hou.playbar.playbackRange()
            saved_current_frame = hou.frame()
        except Exception:
            pass

        total_rendered = 0
        try:
            for cycle_idx, hdri in enumerate(hdris):
                cycle_start = cycle_idx * frames_per_cycle + 1
                cycle_end = (cycle_idx + 1) * frames_per_cycle

                ref = tt_subnet.createNode(
                    "reference", f"{name}_tt_ref_c{cycle_idx}",
                )
                _set_if_exists(ref, "filepath1", usd_path)

                cam = tt_subnet.createNode(
                    "camera", f"{name}_tt_cam_c{cycle_idx}",
                )
                cam.setInput(0, ref, 0)

                # Bbox center+radius — same approach as thumbnail render.
                center, radius = self._bbox_from_lop_stage(ref, name)
                distance = max(radius, 0.001) / math.sin(
                    math.radians(fov_deg) / 2
                ) * margin + distance_offset

                # Camera transform is driven per frame by a Python LOP that
                # reads hou.frame() and rewrites the camera xform on cook.
                cam_override = _create_first_available(
                    tt_subnet, ["pythonscript", "python"],
                    f"{name}_tt_cam_override_c{cycle_idx}",
                )
                if cam_override is not None:
                    cam_override.setInput(0, cam, 0)
                    # Y-axis orbit (the common case): camera position
                    # orbits around the bbox center on a horizontal ring at
                    # the configured pitch elevation; rotation is
                    # rx=-pitch, ry=yaw, rz=0 — same convention as the
                    # static thumbnail render so the first frame matches
                    # the still image.
                    # X/Z axes also supported by swapping which coordinate
                    # the orbit lies in.
                    override_script = (
                        "import math\n"
                        "from pxr import UsdGeom, Gf\n"
                        "stage = hou.pwd().editableStage()\n"
                        "cam_path = '/cameras/thumb_cam'\n"
                        "prim = stage.GetPrimAtPath(cam_path)\n"
                        "if not prim or not prim.IsValid():\n"
                        "    cam = UsdGeom.Camera.Define(stage, cam_path)\n"
                        "    prim = cam.GetPrim()\n"
                        f"frames_per_cycle = {frames_per_cycle}\n"
                        f"cycle_start = {cycle_start}\n"
                        f"start_yaw = {start_yaw}\n"
                        f"direction = {direction}\n"
                        f"axis = {axis!r}\n"
                        f"pitch_deg = {pitch_deg}\n"
                        f"distance = {distance}\n"
                        f"center = (Gf.Vec3d({float(center[0])}, "
                        f"{float(center[1])}, {float(center[2])}))\n"
                        "frame = int(hou.frame())\n"
                        "local = (frame - cycle_start) % frames_per_cycle\n"
                        "t = local / float(frames_per_cycle)\n"
                        "yaw_deg = start_yaw + direction * t * 360.0\n"
                        "yaw_r = math.radians(yaw_deg)\n"
                        "pitch_r = math.radians(pitch_deg)\n"
                        "if axis == 'Y':\n"
                        "    cx = center[0] + distance * math.cos(pitch_r) * math.sin(yaw_r)\n"
                        "    cy = center[1] + distance * math.sin(pitch_r)\n"
                        "    cz = center[2] + distance * math.cos(pitch_r) * math.cos(yaw_r)\n"
                        "    rot = Gf.Vec3d(-pitch_deg, yaw_deg, 0.0)\n"
                        "elif axis == 'X':\n"
                        "    cx = center[0] + distance * math.sin(pitch_r)\n"
                        "    cy = center[1] + distance * math.cos(pitch_r) * math.sin(yaw_r)\n"
                        "    cz = center[2] + distance * math.cos(pitch_r) * math.cos(yaw_r)\n"
                        "    rot = Gf.Vec3d(0.0, yaw_deg, -pitch_deg)\n"
                        "else:\n"
                        "    cx = center[0] + distance * math.cos(pitch_r) * math.sin(yaw_r)\n"
                        "    cy = center[1] + distance * math.cos(pitch_r) * math.cos(yaw_r)\n"
                        "    cz = center[2] + distance * math.sin(pitch_r)\n"
                        "    rot = Gf.Vec3d(-pitch_deg, 0.0, yaw_deg)\n"
                        "xform = UsdGeom.Xformable(prim)\n"
                        "xform.ClearXformOpOrder()\n"
                        "xform.AddTranslateOp().Set(Gf.Vec3d(cx, cy, cz))\n"
                        "xform.AddRotateXYZOp().Set(rot)\n"
                        "cam = UsdGeom.Camera(prim)\n"
                        f"cam.CreateFocalLengthAttr().Set({focal})\n"
                        f"cam.CreateHorizontalApertureAttr().Set({aperture})\n"
                        f"cam.CreateVerticalApertureAttr().Set({aperture_y})\n"
                        f"cam.CreateClippingRangeAttr().Set("
                        f"Gf.Vec2f({self._camera_near}, "
                        f"{self._camera_far}))\n"
                        "cam.CreateProjectionAttr()."
                        "Set(UsdGeom.Tokens.perspective)\n"
                        "# Dome-light attributes are written by a\n"
                        "# separate Python LOP downstream of the dome —\n"
                        "# at this point in the chain the dome prim\n"
                        "# doesn't exist yet.\n"
                        "print('[TurntableCam] frame=' + str(frame) + "
                        "' yaw=' + str(yaw_deg) + ' pos=(' + str(cx) "
                        "+ ',' + str(cy) + ',' + str(cz) + ')')\n"
                    )
                    for pn in ("python", "code", "script", "pythoncode"):
                        if _set_if_exists(cam_override, pn, override_script):
                            break
                    cam_tail = cam_override
                else:
                    cam_tail = cam

                _set_if_exists(cam, "primpath", "/cameras/thumb_cam")

                # ── Calibration row (optional) ──
                # Inserted between cam_tail and dome so the prims are
                # lit by the same HDRI as the asset. The script defines
                # spheres + a textured quad parented under the camera,
                # so they orbit with it and stay screen-locked to the
                # lower-left of the frame.
                if cal_enabled and (
                    cal_show_chrome or cal_show_grey or cal_show_macbeth
                ):
                    cal_lop = _create_first_available(
                        tt_subnet, ["pythonscript", "python"],
                        f"{name}_tt_cal_c{cycle_idx}",
                    )
                    if cal_lop is not None:
                        cal_lop.setInput(0, cam_tail, 0)
                        cal_script = self._build_calibration_script(
                            distance=cal_distance,
                            offset_x=cal_offset_x,
                            offset_y=cal_offset_y,
                            scale=cal_scale,
                            show_chrome=cal_show_chrome,
                            show_grey=cal_show_grey,
                            show_macbeth=cal_show_macbeth,
                        )
                        for pn in ("python", "code", "script",
                                   "pythoncode"):
                            if _set_if_exists(cal_lop, pn, cal_script):
                                break
                        cam_tail = cal_lop

                dome = _create_first_available(
                    tt_subnet,
                    ["domelight::3.0", "domelight::2.0", "domelight"],
                    f"{name}_tt_dome_c{cycle_idx}",
                )
                if dome is not None:
                    dome.setInput(0, cam_tail, 0)
                    _set_if_exists(dome, "primpath", "/lights/turntable_dome")
                    _set_if_exists(dome, "intensity", 1.0)
                    for pn in ("xn__inputstexturefile_r3ah",
                               "inputs:texture:file",
                               "texturefile", "envmap", "file"):
                        if _set_if_exists(dome, pn, hdri):
                            break
                    # Projection / texture format. Dome-light LOPs in H21
                    # expose this under several encoded parm names; the
                    # token strings (latlong / mirrored_ball / etc.) are
                    # the USD-standard inputs:texture:format values.
                    for pn in ("xn__inputstextureformat_r3ah",
                               "inputs:texture:format",
                               "textureformat", "format",
                               "projection"):
                        if _set_if_exists(dome, pn, hdri_projection):
                            break
                    # Make the HDRI visible to the camera (background).
                    # Try Karma's parm name first; the USD-attr fallback
                    # written by the post-dome Python LOP below covers
                    # Arnold and Redshift as well as cases where this
                    # parm name doesn't exist in the current H21 build.
                    for pn in ("xn__karmalightrenderlightgeo_4tbjlcf",
                               "karma:light:renderlightgeo",
                               "renderlightgeo",
                               "lightcamerageo",
                               "rendergeo"):
                        if _set_if_exists(dome, pn, 1):
                            break

                    # Post-dome Python LOP: write USD attributes on the
                    # dome prim now that it exists in the stage. This
                    # forces projection + camera-visibility regardless
                    # of LOP parm-name drift between H21 builds.
                    dome_post = _create_first_available(
                        tt_subnet, ["pythonscript", "python"],
                        f"{name}_tt_dome_attrs_c{cycle_idx}",
                    )
                    if dome_post is not None:
                        dome_post.setInput(0, dome, 0)
                        dome_script = (
                            "from pxr import Sdf\n"
                            "stage = hou.pwd().editableStage()\n"
                            "dome_prim = stage.GetPrimAtPath("
                            "'/lights/turntable_dome')\n"
                            "if dome_prim and dome_prim.IsValid():\n"
                            "    # Texture projection / wrap mode.\n"
                            f"    fmt_attr = dome_prim.CreateAttribute("
                            f"'inputs:texture:format', "
                            f"Sdf.ValueTypeNames.Token)\n"
                            f"    fmt_attr.Set({hdri_projection!r})\n"
                            "    # ── HDRI visible to the camera "
                            "(background) ──\n"
                            "    # Karma:\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'karma:light:renderlightgeo',\n"
                            "        Sdf.ValueTypeNames.Bool)\n"
                            "    a.Set(True)\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'inputs:karma:light:renderlightgeo',\n"
                            "        Sdf.ValueTypeNames.Bool)\n"
                            "    a.Set(True)\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'primvars:karma:light:"
                            "renderlightgeo',\n"
                            "        Sdf.ValueTypeNames.Bool)\n"
                            "    a.Set(True)\n"
                            "    # Arnold:\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'inputs:arnold:camera',\n"
                            "        Sdf.ValueTypeNames.Float)\n"
                            "    a.Set(1.0)\n"
                            "    # Redshift:\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'inputs:redshift:"
                            "DomeLight:backPlateEnable',\n"
                            "        Sdf.ValueTypeNames.Bool)\n"
                            "    a.Set(True)\n"
                            "    a = dome_prim.CreateAttribute(\n"
                            "        'primvars:redshift:"
                            "DomeLight:backPlateEnable',\n"
                            "        Sdf.ValueTypeNames.Bool)\n"
                            "    a.Set(True)\n"
                            "    print('[TurntableDome] projection=' "
                            f"+ {hdri_projection!r} "
                            "+ ' camvis=True')\n"
                            "else:\n"
                            "    print('[TurntableDome] WARNING — "
                            "dome prim not found')\n"
                        )
                        for pn in ("python", "code", "script",
                                   "pythoncode"):
                            if _set_if_exists(dome_post, pn,
                                              dome_script):
                                break
                        last_lop = dome_post
                    else:
                        last_lop = dome
                else:
                    last_lop = cam_tail

                rop = _create_first_available(
                    tt_subnet, ["karma", "usdrender_rop"],
                    f"{name}_tt_rop_c{cycle_idx}",
                )
                if rop is None:
                    print(f"[Turntable] {name} cycle {cycle_idx}: "
                          f"no Karma ROP available")
                    continue
                rop.setInput(0, last_lop, 0)

                # Output: PNG sequence under the per-asset turntable dir.
                # $F4 expands to a 4-digit zero-padded global frame number,
                # so frames across cycles land in one contiguous sequence.
                out_pattern = (
                    f"{turntable_dir}/frame_$F4.png"
                )
                for pn in ("picture", "outputimage", "outputname"):
                    _set_if_exists(rop, pn, out_pattern)

                for pn in ("override_camerares", "override_resolution",
                           "overrideres", "overridecamerares"):
                    _set_if_exists(rop, pn, 1)
                if not _set_tuple_if_exists(rop, "res", (rx, ry)):
                    for px, py in (("resx", "resy"), ("res1", "res2"),
                                   ("resolutionx", "resolutiony")):
                        if (_set_if_exists(rop, px, rx)
                                and _set_if_exists(rop, py, ry)):
                            break

                _set_if_exists(rop, "camera", "/cameras/thumb_cam")
                for pn in ("engine", "render_engine", "karmaengine"):
                    if _set_if_exists(rop, pn, engine):
                        break
                for pn in ("pathtracedsamples", "samplesperpixel",
                           "pixelsamples", "camera_samples", "samples"):
                    if _set_if_exists(rop, pn, samples):
                        break

                # Frame range — force-override.
                # H21 USD Render ROP defaults f1/f2/f3 to channel
                # expressions ($FSTART, $FEND, $FINC). A plain parm.set()
                # is supposed to replace the expression, but on
                # `usdrender_rop` it sometimes doesn't — so clear keys
                # first, then set the explicit value.
                def _force_set(p_node, parm_name, value):
                    p = p_node.parm(parm_name)
                    if p is None:
                        return False
                    try:
                        p.deleteAllKeyframes()
                    except Exception:
                        pass
                    try:
                        p.set(value)
                        return True
                    except Exception:
                        return False

                _force_set(rop, "trange", 1)  # 1 = render frame range
                f1_set = _force_set(rop, "f1", cycle_start)
                f2_set = _force_set(rop, "f2", cycle_end)
                f3_set = _force_set(rop, "f3", 1)

                # Fallback: some H21 ROP variants expose the range as the
                # tuple parm "f" rather than three scalar parms.
                if not (f1_set and f2_set and f3_set):
                    try:
                        f_tuple = rop.parmTuple("f")
                        if f_tuple is not None and len(f_tuple) >= 2:
                            for tp in f_tuple:
                                try:
                                    tp.deleteAllKeyframes()
                                except Exception:
                                    pass
                            if len(f_tuple) >= 3:
                                f_tuple.set((cycle_start, cycle_end, 1))
                            else:
                                f_tuple.set((cycle_start, cycle_end))
                    except Exception as _e:
                        print(f"[Turntable] {name}: frame range tuple "
                              f"fallback failed: {_e}")

                # Belt-and-suspenders: also widen the playbar so any ROP
                # that bypasses its own trange parm (or reads $FSTART/
                # $FEND from the playbar) still picks up the cycle range.
                try:
                    hou.playbar.setFrameRange(cycle_start, cycle_end)
                    hou.playbar.setPlaybackRange(cycle_start, cycle_end)
                    hou.setFrame(cycle_start)
                except Exception:
                    pass

                tt_subnet.layoutChildren()

                # Diagnostic: read back the values that actually landed so
                # a wrong frame count is visible in the log.
                try:
                    diag = {}
                    for pn in ("trange", "f1", "f2", "f3"):
                        p = rop.parm(pn)
                        if p is not None:
                            try:
                                diag[pn] = p.eval()
                            except Exception:
                                diag[pn] = "<eval-err>"
                    print(f"[Turntable] {name} cycle {cycle_idx+1}: "
                          f"ROP frame-range parms = {diag}")
                except Exception:
                    pass

                print(f"[Turntable] {name} cycle {cycle_idx+1}/{len(hdris)}: "
                      f"frames {cycle_start}-{cycle_end} HDRI={hdri}")

                pressed = False
                interrupted = False
                # Prefer RopNode.render() — it takes an explicit
                # frame_range and ignores the ROP's parm-level defaults.
                # It also raises hou.OperationInterrupted when the user
                # clicks Interrupt on the Karma render dialog, which we
                # use to bail out of the remaining cycles cleanly.
                try:
                    rop.render(
                        frame_range=(cycle_start, cycle_end, 1),
                        verbose=False,
                    )
                    pressed = True
                except hou.OperationInterrupted:
                    interrupted = True
                    print(f"[Turntable] {name} cycle {cycle_idx+1}: "
                          f"interrupted by user")
                except Exception as e:
                    print(f"[Turntable] {name} cycle {cycle_idx+1}: "
                          f"RopNode.render() failed ({e}) — falling back "
                          f"to button press")

                if not pressed and not interrupted:
                    for btn_name in ("executerender", "execute", "render"):
                        btn = rop.parm(btn_name)
                        if btn is None:
                            continue
                        try:
                            btn.pressButton()
                            pressed = True
                            break
                        except hou.OperationInterrupted:
                            interrupted = True
                            print(f"[Turntable] {name} cycle "
                                  f"{cycle_idx+1}: interrupted (button)")
                            break
                        except Exception as e:
                            print(f"[Turntable] {name}: {btn_name} "
                                  f"failed: {e}")

                if not pressed and not interrupted:
                    print(f"[Turntable] {name} cycle {cycle_idx+1}: "
                          f"no render button matched")
                    continue

                # Count produced frames. rop.render() is synchronous so
                # frames are on disk by the time it returns — no polling
                # needed.
                produced = sum(
                    1 for f in range(cycle_start, cycle_end + 1)
                    if os.path.exists(
                        os.path.join(turntable_dir, f"frame_{f:04d}.png")
                    )
                )
                total_rendered += produced
                print(f"[Turntable] {name} cycle {cycle_idx+1}: "
                      f"{produced}/{frames_per_cycle} frames produced")

                # Yield to the Qt event loop so the UI repaints between
                # cycles. Without this the Asset Manager panel appears
                # frozen during a multi-HDRI run.
                try:
                    from asset_manager.qt_compat import QtWidgets as _QtW
                    _QtW.QApplication.processEvents()
                except Exception:
                    pass

                # User hit Interrupt — stop processing remaining cycles
                # AND propagate the interrupt to the caller (gallery
                # queue loop) so it stops any pending assets too.
                if interrupted:
                    print(f"[Turntable] {name}: aborting remaining cycles")
                    # Write metadata for what we've got before re-raising
                    # so the partial sequence is still playable.
                    try:
                        self._write_turntable_metadata(
                            turntable_dir, name=name, usd_path=usd_path,
                            hdris=hdris[:cycle_idx + 1],
                            frames_per_cycle=frames_per_cycle,
                            total_frames=total_rendered,
                            fps=int(tt.get("fps", 24)),
                            loop_mode=tt.get("loop_mode", "loop"),
                            direction="ccw" if direction < 0 else "cw",
                            axis=axis, pitch=pitch_deg, focal=focal,
                            aperture_h=aperture, samples=samples,
                            engine=engine, resolution=(rx, ry),
                            interrupted=True,
                        )
                    except Exception:
                        pass
                    raise hou.OperationInterrupted(
                        f"Turntable interrupted at {name} cycle "
                        f"{cycle_idx + 1}/{len(hdris)}"
                    )

            # Write a sidecar with playback metadata so the gallery hover
            # widget can render the proper caption / FPS / loop mode.
            self._write_turntable_metadata(
                turntable_dir, name=name, usd_path=usd_path,
                hdris=hdris, frames_per_cycle=frames_per_cycle,
                total_frames=total_rendered, fps=int(tt.get("fps", 24)),
                loop_mode=tt.get("loop_mode", "loop"),
                direction="ccw" if direction < 0 else "cw",
                axis=axis, pitch=pitch_deg, focal=focal,
                aperture_h=aperture, samples=samples, engine=engine,
                resolution=(rx, ry),
            )

            # Optional encoded outputs (WebP / APNG / MP4).
            fmt = str(tt.get("format", "png_sequence"))
            if fmt and fmt != "png_sequence" and total_rendered > 0:
                try:
                    self._encode_turntable(
                        turntable_dir, name, fmt,
                        fps=int(tt.get("fps", 24)),
                    )
                except Exception as enc_err:
                    print(f"[Turntable] {name}: encode '{fmt}' failed: "
                          f"{enc_err}")

            return turntable_dir

        except Exception:
            try:
                tt_subnet.destroy()
            except Exception:
                pass
            raise
        finally:
            # Restore the user's playbar range/current frame so the scene
            # isn't left at the last cycle's render range.
            try:
                if saved_playback_range is not None:
                    hou.playbar.setFrameRange(
                        saved_playback_range[0], saved_playback_range[1])
                    hou.playbar.setPlaybackRange(
                        saved_playback_range[0], saved_playback_range[1])
                if saved_current_frame is not None:
                    hou.setFrame(saved_current_frame)
            except Exception:
                pass

    @staticmethod
    def _build_calibration_script(*, distance: float,
                                  offset_x: float, offset_y: float,
                                  scale: float,
                                  show_chrome: bool, show_grey: bool,
                                  show_macbeth: bool) -> str:
        """Generate the Python LOP script that constructs the calibration
        row in USD.

        The script defines:
          • /materials/chrome_mat, grey_mat, macbeth_mat (UsdPreviewSurface)
          • /cameras/thumb_cam/calibration_row Xform (parented to the
            camera, so the row orbits with it)
          • Chrome sphere, grey sphere, Macbeth quad as children of the
            row xform — positioned along its local +X, centered vertically

        Positioning convention (camera-local axes):
          +X right, +Y up, -Z forward.
        The row sits at (offset_x, offset_y, -distance) so it floats in
        front of the camera, off to the lower-left.
        """
        from .macbeth import get_macbeth_chart_path
        try:
            chart_path = get_macbeth_chart_path()
        except Exception as e:
            print(f"[Turntable] Macbeth chart generation failed: {e}")
            chart_path = ""

        # Element local positions (before row-level scale).
        # Layout (matches the reference photo the user provided):
        #
        #       [grey]   [chrome]
        #       [    macbeth     ]
        #
        # Two balls side-by-side on top, Macbeth chart centered below.
        # Coordinates are in row-local space; the row origin (0,0)
        # corresponds to the LEFT edge / vertical center of the
        # composition, so the row's negative X offset controls how far
        # the assembly sits from screen center.
        ball_radius = 0.045
        ball_spacing = 0.105       # center-to-center along X
        chart_w, chart_h = 0.30, 0.18
        vertical_gap = 0.020       # gap between ball bottoms and chart top

        # Chart: centered horizontally within the composition.
        macbeth_x = chart_w * 0.5
        macbeth_y = -(ball_radius + vertical_gap + chart_h * 0.5)

        # Balls: centered above the chart's midline, separated by
        # ball_spacing.
        balls_center_x = macbeth_x
        balls_y = ball_radius + vertical_gap + chart_h * 0.5
        grey_x = balls_center_x - ball_spacing * 0.5
        chrome_x = balls_center_x + ball_spacing * 0.5

        return f"""
from pxr import Usd, UsdGeom, UsdShade, Sdf, Gf

stage = hou.pwd().editableStage()

CAM_PATH = '/cameras/thumb_cam'
ROW_PATH = CAM_PATH + '/calibration_row'

# ── Ensure the camera prim exists (the override LOP defines it, but
#    in some LOP orderings this script may cook first). ──
cam_prim = stage.GetPrimAtPath(CAM_PATH)
if not cam_prim or not cam_prim.IsValid():
    UsdGeom.Camera.Define(stage, CAM_PATH)

# ── Materials ──
# Built as MaterialX standard_surface shaders so Karma renders them
# faithfully. Karma's UsdPreviewSurface translator in H21 has been
# observed to fall back to a default grey on non-trivial input
# bindings, so we go straight to mtlx. We also wire a
# UsdPreviewSurface output for Hydra Storm / viewport preview.

def _bind(prim, material):
    \"\"\"Apply MaterialBindingAPI then bind. The Apply step is what
    makes the relationship visible to render delegates that are strict
    about applied-schema membership (Karma in H21 is one).\"\"\"
    UsdShade.MaterialBindingAPI.Apply(prim)
    UsdShade.MaterialBindingAPI(prim).Bind(material)

def _make_mtlx_material(path, base_color, metalness, roughness):
    mat = UsdShade.Material.Define(stage, path)

    # MaterialX standard_surface for Karma + Arnold MtlX paths.
    mtlx = UsdShade.Shader.Define(stage, path + '/standard_surface')
    mtlx.CreateIdAttr('ND_standard_surface_surfaceshader')
    mtlx.CreateInput('base',
        Sdf.ValueTypeNames.Float).Set(1.0)
    mtlx.CreateInput('base_color',
        Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*base_color))
    mtlx.CreateInput('metalness',
        Sdf.ValueTypeNames.Float).Set(metalness)
    mtlx.CreateInput('specular_roughness',
        Sdf.ValueTypeNames.Float).Set(roughness)
    mtlx.CreateInput('specular_IOR',
        Sdf.ValueTypeNames.Float).Set(1.5)
    mtlx_out = mtlx.CreateOutput('out',
        Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput('mtlx').ConnectToSource(mtlx_out)

    # UsdPreviewSurface for viewport / Hydra Storm fallback.
    prev = UsdShade.Shader.Define(stage, path + '/PreviewSurface')
    prev.CreateIdAttr('UsdPreviewSurface')
    prev.CreateInput('diffuseColor',
        Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*base_color))
    prev.CreateInput('metallic',
        Sdf.ValueTypeNames.Float).Set(metalness)
    prev.CreateInput('roughness',
        Sdf.ValueTypeNames.Float).Set(roughness)
    prev.CreateInput('useSpecularWorkflow',
        Sdf.ValueTypeNames.Int).Set(0)
    prev_out = prev.CreateOutput('surface',
        Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput().ConnectToSource(prev_out)

    return mat, mtlx, prev

chrome_mat, _cs, _cp = _make_mtlx_material(
    '/materials/calibration_chrome', (0.95, 0.95, 0.95),
    metalness=1.0, roughness=0.02,
)
grey_mat, _gs, _gp = _make_mtlx_material(
    '/materials/calibration_grey', (0.18, 0.18, 0.18),
    metalness=0.0, roughness=0.5,
)

# Macbeth chart: MaterialX with an image node driving base_color.
macbeth_mat, mb_mtlx, mb_prev = _make_mtlx_material(
    '/materials/calibration_macbeth', (0.5, 0.5, 0.5),
    metalness=0.0, roughness=0.5,
)
if {chart_path!r}:
    # MaterialX image node — outputs color3 from a file texture.
    img = UsdShade.Shader.Define(
        stage, '/materials/calibration_macbeth/ImageRead')
    img.CreateIdAttr('ND_image_color3')
    img.CreateInput('file',
        Sdf.ValueTypeNames.Asset).Set({chart_path!r})
    img_out = img.CreateOutput('out',
        Sdf.ValueTypeNames.Color3f)
    # Re-wire base_color from constant to texture output.
    base_in = mb_mtlx.GetInput('base_color')
    if base_in:
        base_in.DisconnectSource()
        base_in.ConnectToSource(img_out)
    else:
        mb_mtlx.CreateInput('base_color',
            Sdf.ValueTypeNames.Color3f).ConnectToSource(img_out)

    # UsdPreviewSurface texture path (for viewport).
    uv_reader = UsdShade.Shader.Define(
        stage, '/materials/calibration_macbeth/UVReader')
    uv_reader.CreateIdAttr('UsdPrimvarReader_float2')
    uv_reader.CreateInput('varname',
        Sdf.ValueTypeNames.Token).Set('st')
    uv_out = uv_reader.CreateOutput('result',
        Sdf.ValueTypeNames.Float2)
    tex = UsdShade.Shader.Define(
        stage, '/materials/calibration_macbeth/PreviewTexture')
    tex.CreateIdAttr('UsdUVTexture')
    tex.CreateInput('file',
        Sdf.ValueTypeNames.Asset).Set({chart_path!r})
    tex.CreateInput('sourceColorSpace',
        Sdf.ValueTypeNames.Token).Set('auto')
    tex.CreateInput('st',
        Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
    tex_rgb_out = tex.CreateOutput('rgb',
        Sdf.ValueTypeNames.Float3)
    prev_diff = mb_prev.GetInput('diffuseColor')
    if prev_diff:
        prev_diff.DisconnectSource()
        prev_diff.ConnectToSource(tex_rgb_out)
    else:
        mb_prev.CreateInput('diffuseColor',
            Sdf.ValueTypeNames.Color3f).ConnectToSource(tex_rgb_out)

    import os as _os
    _exists = _os.path.exists({chart_path!r})
    print('[CalibrationRow] macbeth chart=' + {chart_path!r}
          + ' exists=' + str(_exists))

# ── Row xform — child of the camera so it orbits with it. ──
row_xform = UsdGeom.Xform.Define(stage, ROW_PATH)
row_xformable = UsdGeom.Xformable(row_xform.GetPrim())
row_xformable.ClearXformOpOrder()
row_xformable.AddTranslateOp().Set(
    Gf.Vec3d({offset_x}, {offset_y}, {-distance}))
row_xformable.AddScaleOp().Set(
    Gf.Vec3f({scale}, {scale}, {scale}))

# ── Chrome ball ──
if {bool(show_chrome)}:
    sp_path = ROW_PATH + '/chrome_ball'
    sp = UsdGeom.Sphere.Define(stage, sp_path)
    sp.CreateRadiusAttr({ball_radius})
    sp.CreateExtentAttr([
        (-{ball_radius}, -{ball_radius}, -{ball_radius}),
        ({ball_radius}, {ball_radius}, {ball_radius})])
    sp_xf = UsdGeom.Xformable(sp.GetPrim())
    sp_xf.ClearXformOpOrder()
    sp_xf.AddTranslateOp().Set(Gf.Vec3d({chrome_x}, 0.0, 0.0))
    _bind(sp.GetPrim(), chrome_mat)

# ── Grey ball ──
if {bool(show_grey)}:
    sp_path = ROW_PATH + '/grey_ball'
    sp = UsdGeom.Sphere.Define(stage, sp_path)
    sp.CreateRadiusAttr({ball_radius})
    sp.CreateExtentAttr([
        (-{ball_radius}, -{ball_radius}, -{ball_radius}),
        ({ball_radius}, {ball_radius}, {ball_radius})])
    sp_xf = UsdGeom.Xformable(sp.GetPrim())
    sp_xf.ClearXformOpOrder()
    sp_xf.AddTranslateOp().Set(Gf.Vec3d({grey_x}, 0.0, 0.0))
    _bind(sp.GetPrim(), grey_mat)

# ── Macbeth chart (textured quad) ──
if {bool(show_macbeth)}:
    chart_path_prim = ROW_PATH + '/macbeth_chart'
    mesh = UsdGeom.Mesh.Define(stage, chart_path_prim)
    hw = {chart_w} / 2.0
    hh = {chart_h} / 2.0
    mesh.CreatePointsAttr([
        Gf.Vec3f(-hw, -hh, 0),
        Gf.Vec3f( hw, -hh, 0),
        Gf.Vec3f( hw,  hh, 0),
        Gf.Vec3f(-hw,  hh, 0),
    ])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateExtentAttr([(-hw, -hh, 0), (hw, hh, 0)])
    mesh.CreateDoubleSidedAttr(True)
    # UVs (st primvar) so the texture maps across the quad.
    primvars = UsdGeom.PrimvarsAPI(mesh.GetPrim())
    st_pv = primvars.CreatePrimvar(
        'st',
        Sdf.ValueTypeNames.TexCoord2fArray,
        UsdGeom.Tokens.varying,
    )
    st_pv.Set([(0, 0), (1, 0), (1, 1), (0, 1)])
    chart_xf = UsdGeom.Xformable(mesh.GetPrim())
    chart_xf.ClearXformOpOrder()
    chart_xf.AddTranslateOp().Set(
        Gf.Vec3d({macbeth_x}, {macbeth_y}, 0.0))
    _bind(mesh.GetPrim(), macbeth_mat)

print('[CalibrationRow] built at ' + ROW_PATH
      + ' (chrome={show_chrome} grey={show_grey} macbeth={show_macbeth})')
"""

    @staticmethod
    def _write_turntable_metadata(turntable_dir: str, **info):
        """Write a `turntable.json` sidecar with the render config."""
        import json as _json
        try:
            from datetime import datetime as _dt
            sidecar = os.path.join(turntable_dir, "turntable.json")
            payload = {"rendered_at": _dt.now().isoformat(), **info}
            with open(sidecar, "w", encoding="utf-8") as f:
                _json.dump(payload, f, indent=2, default=str)
        except Exception as e:
            print(f"[Turntable] metadata write failed: {e}")

    @staticmethod
    def _encode_turntable(turntable_dir: str, name: str,
                          fmt: str, fps: int = 24) -> str:
        """Encode the PNG sequence to the requested format.

        Tries Pillow for WebP/APNG, and imageio-ffmpeg (or system ffmpeg)
        for MP4. Failures are non-fatal — the PNG sequence is the source
        of truth for hover playback.
        """
        import glob
        frame_paths = sorted(glob.glob(
            os.path.join(turntable_dir, "frame_*.png")))
        if not frame_paths:
            return ""

        out_path = ""
        if fmt in ("webp", "apng"):
            try:
                from PIL import Image
            except ImportError:
                print(f"[Turntable] {name}: Pillow not available — "
                      f"skipping {fmt} encode")
                return ""
            imgs = [Image.open(p) for p in frame_paths]
            duration_ms = int(1000.0 / max(fps, 1))
            if fmt == "webp":
                out_path = os.path.join(turntable_dir, f"{name}.webp")
                imgs[0].save(
                    out_path, format="WEBP",
                    save_all=True, append_images=imgs[1:],
                    duration=duration_ms, loop=0, lossless=False,
                    quality=80,
                )
            else:
                out_path = os.path.join(turntable_dir, f"{name}.apng")
                imgs[0].save(
                    out_path, format="PNG",
                    save_all=True, append_images=imgs[1:],
                    duration=duration_ms, loop=0,
                )
            print(f"[Turntable] {name}: encoded {fmt} -> {out_path}")
        elif fmt == "mp4":
            ffmpeg_cmd = None
            try:
                import imageio_ffmpeg
                ffmpeg_cmd = imageio_ffmpeg.get_ffmpeg_exe()
            except ImportError:
                from shutil import which
                ffmpeg_cmd = which("ffmpeg")
            if not ffmpeg_cmd:
                print(f"[Turntable] {name}: ffmpeg not available — "
                      f"skipping mp4 encode")
                return ""
            import subprocess
            out_path = os.path.join(turntable_dir, f"{name}.mp4")
            cmd = [
                ffmpeg_cmd, "-y",
                "-framerate", str(fps),
                "-i", os.path.join(turntable_dir, "frame_%04d.png"),
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-crf", "20",
                out_path,
            ]
            subprocess.run(cmd, check=True,
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            print(f"[Turntable] {name}: encoded mp4 -> {out_path}")

        return out_path

    def render_turntable_only(self, usd_path: str, name: str,
                              thumbnail_dir: str) -> str:
        """Re-render only the turntable for an already-built asset. Used
        by the Gallery's "Re-render Turntable" action."""
        ensure_hou()
        stage = hou.node("/stage")
        if stage is None:
            raise RuntimeError("No /stage context")
        parent = stage.node(f"tt_only_{name}")
        if parent is None:
            parent = stage.createNode("subnet", f"tt_only_{name}")
        return self._render_turntable(
            parent, name, usd_path, thumbnail_dir,
        )

    def render_thumbnail_only(self, usd_path: str, name: str,
                              thumbnail_dir: str,
                              texture_info: Optional[dict] = None,
                              source_dir: str = "") -> str:
        """Re-render only the thumbnail for an already-exported USD. Used
        by the Gallery's "Re-render Thumbnail" action — doesn't rebuild
        the asset, just creates a throwaway parent subnet and calls the
        same Karma ROP path the main build uses."""
        ensure_hou()
        stage = hou.node("/stage")
        if stage is None:
            raise RuntimeError("No /stage context")
        parent = stage.node(f"thumb_only_{name}")
        if parent is None:
            parent = stage.createNode("subnet", f"thumb_only_{name}")
        return self._render_thumbnail_karma(
            parent, name, usd_path, thumbnail_dir,
            texture_info=texture_info, source_dir=source_dir,
        )

    # ──────────────────────────────────────────────
    # Compatibility / cleanup helpers
    # ──────────────────────────────────────────────

    def execute_export(self, build_subnet):
        """Kept for backward compatibility — build_asset already triggers
        the export internally. This re-presses the componentoutput button
        on an existing build subnet."""
        ensure_hou()
        for child in build_subnet.children():
            if child.type().name() == "componentoutput":
                try:
                    btn = child.parm("execute") or child.parm("savetodisk")
                    if btn is not None:
                        btn.pressButton()
                    return True
                except Exception as e:
                    print(f"[ComponentBuilder] Re-export failed: {e}")
                    return False
        return False

    def cleanup_sop_nodes(self, name: str):
        """No-op. All SOPs live inside the componentgeometry's internal
        SOP network now, so there's nothing to clean up in /obj."""
        return

    @staticmethod
    def _purge_stale_build_state(stage_parent, name):
        """Destroy any leftover build subnet (and legacy /obj container)
        for this asset before re-building."""
        existing = stage_parent.node(f"build_{name}")
        if existing is not None:
            try:
                existing.destroy()
            except Exception as e:
                print(f"[ComponentBuilder] Could not destroy stale "
                      f"build_{name}: {e}")
        obj = hou.node("/obj")
        if obj is not None:
            legacy = obj.node(f"_am_{name}_geo")
            if legacy is not None:
                try:
                    legacy.destroy()
                except Exception:
                    pass
