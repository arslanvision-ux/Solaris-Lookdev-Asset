"""
Thumbnail renderer for the Asset Manager.
Renders preview thumbnails for processed USD assets using the
selected render engine (Karma, Arnold, or Redshift).
"""

import os
import math
from typing import Optional, Tuple

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

try:
    from pxr import Usd, UsdGeom, Gf
    HAS_USD = True
except ImportError:
    HAS_USD = False

from .usd_utils import ensure_hou, load_renderer_settings
from ..database.models import ScanResult, TextureSet


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


def _create_first_available(parent, candidates, name):
    """Try each node type in `candidates` and return the first node that
    creates successfully. Returns None if none of them are registered."""
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
        print(f"[ThumbnailRenderer] No node type matched {candidates}: {last_err}")
    return None


class ThumbnailRenderer:
    """
    Creates and renders thumbnail images for USD assets.
    Sets up a temporary LOP network with camera, lighting, and
    render settings, then renders a preview image.
    """

    def __init__(self, renderer: str = "karma",
                 resolution: Tuple[int, int] = (512, 512),
                 hdri_path: str = ""):
        self._renderer = renderer
        self._resolution = resolution
        self._hdri_path = hdri_path or ""
        self._settings = load_renderer_settings()
        self._renderer_config = self._settings["renderers"].get(
            renderer, self._settings["renderers"]["karma"]
        )

    def render_thumbnail(self, source,
                         output_path: str,
                         parent_node: Optional["hou.Node"] = None) -> bool:
        """
        Render a thumbnail. `source` may be either a `ScanResult` (in which
        case the thumbnail is rendered from the scanned geo + textures, with
        a full material network) or a USD file path (legacy: just references
        the existing USD). HDRI lighting is applied when configured.

        Falls back to a placeholder PNG so the gallery is never empty.
        """
        ensure_hou()

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        if parent_node is None:
            parent_node = hou.node("/stage")

        # Coerce a plain path to a minimal ScanResult so the same pipeline
        # can drive both flows.
        if isinstance(source, str):
            label = os.path.splitext(os.path.basename(source))[0]
            scan_result = ScanResult(
                asset_name=label,
                geo_file=source,
                texture_set=TextureSet(),
                source_dir=os.path.dirname(source),
            )
        else:
            scan_result = source

        thumb_subnet = parent_node.createNode("subnet", "_thumb_render_tmp")

        rendered_ok = False
        try:
            rendered_ok = self._build_and_render(
                thumb_subnet, scan_result, output_path
            )
        except Exception as e:
            print(f"[ThumbnailRenderer] Error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            try:
                thumb_subnet.destroy()
            except Exception:
                pass

        if rendered_ok and os.path.exists(output_path):
            return True

        # Hydra render didn't produce a file — write a placeholder so the
        # gallery doesn't end up empty.
        label = scan_result.asset_name or os.path.splitext(
            os.path.basename(output_path))[0]
        if self._write_placeholder(output_path, label):
            print(f"[ThumbnailRenderer] Wrote placeholder thumbnail: {output_path}")
            return True
        return False

    @staticmethod
    def _write_placeholder(output_path: str, label: str,
                           size: int = 512) -> bool:
        """Generate a simple PNG placeholder using Qt so we always have a
        thumbnail file. Independent of any render engine."""
        try:
            from asset_manager.qt_compat import QtCore, QtGui
        except Exception:
            return False
        try:
            pixmap = QtGui.QPixmap(size, size)
            pixmap.fill(QtGui.QColor("#1a1a2e"))
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            pen = QtGui.QPen(QtGui.QColor("#3a4a65"))
            pen.setWidth(4)
            painter.setPen(pen)
            cx, cy = size // 2, size // 2
            s = size // 4
            painter.drawRect(cx - s, cy - s, s * 2, s * 2)
            painter.drawLine(cx - s, cy - s, cx - s + s // 2, cy - s - s // 2)
            painter.drawLine(cx + s, cy - s, cx + s + s // 2, cy - s - s // 2)
            painter.drawLine(cx + s, cy + s, cx + s + s // 2, cy + s - s // 2)
            painter.drawLine(cx - s + s // 2, cy - s - s // 2,
                             cx + s + s // 2, cy - s - s // 2)
            painter.drawLine(cx + s + s // 2, cy - s - s // 2,
                             cx + s + s // 2, cy + s - s // 2)

            painter.setPen(QtGui.QColor("#8a9bb5"))
            font = painter.font()
            font.setPixelSize(max(18, size // 14))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                pixmap.rect().adjusted(0, size // 3, 0, 0),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop,
                label,
            )
            painter.end()
            return pixmap.save(output_path, "PNG")
        except Exception as e:
            print(f"[ThumbnailRenderer] Placeholder failed: {e}")
            return False

    def _build_and_render(self, subnet: "hou.Node",
                          scan_result: "ScanResult",
                          output_path: str) -> bool:
        """Build the render network and execute it.

        Pipeline:
            1. Load the scanned asset (reference if USD, sopcreate for
               obj/fbx/glb/etc.).
            2. Build a Material Library + render-engine material network
               from the scanned texture set, then AssignMaterial.
            3. Camera.
            4. Dome light + HDRI (when configured).
            5. Karma ROP — resolution, picture, camera, samples written
               directly on the ROP.
        """
        # 1. Geometry from the scanned source path (not via SOP Import).
        geo_node = self._import_scanned_asset(subnet, scan_result)
        if geo_node is None:
            print("[ThumbnailRenderer] Could not import scanned geometry.")
            return False
        upstream = geo_node

        # 2. Material network from textures folder (when present).
        if scan_result.texture_set and scan_result.texture_set.has_textures():
            with_mtl = self._build_thumbnail_materials(
                subnet, upstream, scan_result
            )
            if with_mtl is not None:
                upstream = with_mtl

        # 3. Camera (auto-framed via Python LOP when available).
        camera_node = self._create_camera(subnet, upstream)
        upstream = camera_node or upstream

        # 4. Dome light + HDRI hookup.
        light_node = self._create_dome_light(subnet, upstream)
        upstream = light_node or upstream

        # 5. Render settings LOP — optional, only when configured.
        if self._renderer_config.get("render_settings_type"):
            render_settings = self._create_render_settings(
                subnet, upstream, output_path
            )
            if render_settings is not None:
                upstream = render_settings

        # 6. Render ROP.
        rop = self._create_render_rop(subnet, upstream, output_path)
        if rop is None:
            print("[ThumbnailRenderer] Could not create render ROP.")
            return False

        try:
            subnet.layoutChildren()
        except Exception:
            pass

        try:
            # render() is synchronous and writes the picture before returning.
            try:
                rop.render()
            except AttributeError:
                rop.parm("execute").pressButton()
            return os.path.exists(output_path)
        except Exception as e:
            print(f"[ThumbnailRenderer] Render failed: {e}")
            return False

    # ──────────────────────────────────────────────
    # Scanned-asset import (Reference for USD, sopcreate for everything else)
    # ──────────────────────────────────────────────

    def _import_scanned_asset(self, parent: "hou.Node",
                              scan_result: "ScanResult"):
        """Bring the scanned geometry into LOPs at `/<asset_name>`."""
        geo_path = (scan_result.geo_file or "").replace("\\", "/")
        if not geo_path or not os.path.exists(geo_path):
            print(f"[ThumbnailRenderer] Asset path missing on disk: {geo_path}")
            return None

        asset_name = scan_result.asset_name or "asset"
        prim_path = f"/{asset_name}"
        ext = os.path.splitext(geo_path)[1].lower()

        if ext in (".usd", ".usda", ".usdc", ".usdz"):
            ref = _create_first_available(
                parent, ["reference", "reference::2.0"], f"{asset_name}_ref"
            )
            if ref is None:
                return None
            _set_if_exists(ref, "filepath1", geo_path)
            _set_if_exists(ref, "primpath1", prim_path)
            return ref

        # Non-USD: wrap a File SOP inside a sopcreate LOP so we never touch
        # /obj. Reference LOP can't load .obj/.fbx directly.
        sopc = _create_first_available(
            parent, ["sopcreate"], f"{asset_name}_geo"
        )
        if sopc is None:
            return None
        for parm_name in ("pathprefix", "primpath", "path"):
            if _set_if_exists(sopc, parm_name, prim_path):
                break

        file_sop = sopc.createNode("file", "file_in")
        _set_if_exists(file_sop, "file", geo_path)

        uv = sopc.createNode("uvproject", "uv_check")
        uv.setInput(0, file_sop, 0)
        _set_if_exists(uv, "projtype", 5)

        normal = sopc.createNode("normal", "normal_compute")
        normal.setInput(0, uv, 0)
        _set_if_exists(normal, "type", 0)

        try:
            normal.setDisplayFlag(True)
        except Exception:
            pass
        try:
            normal.setRenderFlag(True)
        except Exception:
            pass
        try:
            sopc.layoutChildren()
        except Exception:
            pass
        return sopc

    # ──────────────────────────────────────────────
    # Material network for the thumbnail
    # ──────────────────────────────────────────────

    def _build_thumbnail_materials(self, parent: "hou.Node",
                                   geo_node: "hou.Node",
                                   scan_result: "ScanResult"):
        """Material Library + render-engine material builder + MaterialX
        shading network wired to the scanned textures, then AssignMaterial
        binding to the asset's primpath."""
        try:
            from .materialx_builder import MaterialXBuilder
        except Exception as e:
            print(f"[ThumbnailRenderer] MaterialXBuilder unavailable: {e}")
            return None

        asset_name = scan_result.asset_name or "asset"
        mtlx = MaterialXBuilder(self._renderer)

        mat_lib = _create_first_available(
            parent, ["materiallibrary"], f"{asset_name}_materials"
        )
        if mat_lib is None:
            return None
        try:
            mat_lib.setInput(0, geo_node, 0)
        except Exception:
            pass

        # Force a predictable matpathprefix so AssignMaterial can find the
        # shader. Trailing slash is required for the prefix semantics.
        mat_prefix = f"/{asset_name}/mtl/"
        for parm_name in ("matpathprefix", "matprefix"):
            if _set_if_exists(mat_lib, parm_name, mat_prefix):
                break

        try:
            mat_info = mtlx.build_material(
                mat_lib, asset_name, scan_result.texture_set
            )
        except Exception as e:
            print(f"[ThumbnailRenderer] Material build failed: {e}")
            return mat_lib  # still pass geometry downstream

        material_prim = mat_prefix.rstrip("/") + "/" + mat_info.name

        assign = _create_first_available(
            parent, ["assignmaterial"], f"{asset_name}_assign"
        )
        if assign is None:
            return mat_lib
        try:
            assign.setInput(0, mat_lib, 0)
        except Exception:
            pass

        _set_if_exists(assign, "nummaterials", 1)
        # Bind on the asset root — USD material bindings inherit to
        # descendants unless they have their own binding.
        _set_if_exists(assign, "primpattern1", f"/{asset_name}")
        _set_if_exists(assign, "matspecpath1", material_prim)
        # Fallback for older AssignMaterial parm naming.
        _set_if_exists(assign, "matspecpath_1", material_prim)
        _set_if_exists(assign, "geopath1", f"/{asset_name}")

        return assign

    def _create_camera(self, parent: "hou.Node",
                       input_node: "hou.Node"):
        """Create a camera that frames the asset."""
        cam = _create_first_available(
            parent, ["camera", "lopcamera"], "thumb_camera"
        )
        if cam is None:
            return None
        try:
            cam.setInput(0, input_node, 0)
        except Exception:
            pass

        for parm_name, value in (
            ("primpath", "/cameras/thumb_cam"),
            ("tx", 2.0), ("ty", 1.5), ("tz", 2.0),
            ("rx", -25.0), ("ry", 45.0), ("rz", 0.0),
        ):
            p = cam.parm(parm_name)
            if p is not None:
                try:
                    p.set(value)
                except Exception:
                    pass

        # Auto-frame: optional Python Script LOP. Skip if not registered.
        frame_script = _create_first_available(
            parent, ["pythonscript", "python"], "auto_frame"
        )
        if frame_script is None:
            return cam
        try:
            frame_script.setInput(0, cam, 0)
        except Exception:
            return cam

        script = '''
import math
from pxr import UsdGeom, Gf

node = hou.pwd()
stage = node.editableStage()

bbox_cache = UsdGeom.BBoxCache(
    0, [UsdGeom.Tokens.default_, UsdGeom.Tokens.render]
)
root = stage.GetPseudoRoot()
bbox = bbox_cache.ComputeWorldBound(root)
rng = bbox.GetRange()

if not rng.IsEmpty():
    center = (rng.GetMin() + rng.GetMax()) / 2.0
    size = rng.GetSize()
    max_dim = max(size[0], size[1], size[2])
    dist = max_dim * 2.5

    cam_prim = stage.GetPrimAtPath("/cameras/thumb_cam")
    if cam_prim:
        xform = UsdGeom.Xformable(cam_prim)
        xform.ClearXformOpOrder()

        angle_h = math.radians(35)
        angle_v = math.radians(25)

        cam_x = center[0] + dist * math.cos(angle_v) * math.sin(angle_h)
        cam_y = center[1] + dist * math.sin(angle_v)
        cam_z = center[2] + dist * math.cos(angle_v) * math.cos(angle_h)

        xform.AddTranslateOp().Set(Gf.Vec3d(cam_x, cam_y, cam_z))
'''
        for parm_name in ("python", "script", "code"):
            p = frame_script.parm(parm_name)
            if p is not None:
                try:
                    p.set(script)
                    break
                except Exception:
                    pass

        return frame_script

    def _create_dome_light(self, parent: "hou.Node",
                           input_node: "hou.Node"):
        """Create a dome light for neutral lighting."""
        dome = _create_first_available(
            parent, ["domelight::3.0", "domelight::2.0", "domelight"],
            "thumb_light",
        )
        if dome is None:
            return None
        try:
            dome.setInput(0, input_node, 0)
        except Exception:
            pass

        for parm_name, value in (
            ("primpath", "/lights/dome_light"),
            ("intensity", 1.0),
        ):
            p = dome.parm(parm_name)
            if p is not None:
                try:
                    p.set(value)
                except Exception:
                    pass

        hdri = self._hdri_path or self._settings.get("thumbnail_hdri", "")
        if hdri and os.path.exists(hdri):
            for parm_name in ("texturefile", "xn__inputstexturefile_r3ah",
                              "inputs:texture:file"):
                p = dome.parm(parm_name)
                if p is not None:
                    try:
                        p.set(hdri)
                        break
                    except Exception:
                        pass

        return dome

    def _create_render_settings(self, parent: "hou.Node",
                                input_node: "hou.Node",
                                output_path: str):
        """Create render settings for the thumbnail."""
        configured = self._renderer_config.get("render_settings_type", "")
        candidates = [
            configured,
            "usdrendersettings",
            "karmarendersettings",
            "karmarenderproperties",
        ]
        settings = _create_first_available(parent, candidates, "thumb_settings")
        if settings is None:
            return None
        try:
            settings.setInput(0, input_node, 0)
        except Exception:
            pass

        res_x, res_y = self._resolution
        samples = max(16, self._renderer_config.get("default_samples", 64) // 4)

        def _unlock(ctrl_name):
            """Flip a `_control` companion to 'set' so the value parm is
            writable on lock-pattern nodes (e.g. karmarenderproperties)."""
            ctrl = settings.parm(ctrl_name)
            if ctrl is None:
                return
            for value in ("set", 1):
                try:
                    ctrl.set(value)
                    return
                except Exception:
                    continue

        def _override(parm_name, value):
            _unlock(f"{parm_name}_control")
            parm = settings.parm(parm_name)
            if parm is None:
                return
            try:
                parm.set(value)
            except Exception as e:
                print(f"[ThumbnailRenderer] Skipping {parm_name}: {e}")

        # Resolution is a 2-channel parmTuple on most LOPs — set both
        # channels atomically so the lock is lifted for the whole vector.
        _unlock("resolution_control")
        _unlock("resolutionx_control")
        _unlock("resolutiony_control")
        res_tuple = settings.parmTuple("resolution")
        if res_tuple is not None:
            try:
                res_tuple.set((int(res_x), int(res_y)))
            except Exception as e:
                print(f"[ThumbnailRenderer] Skipping resolution tuple: {e}")
        else:
            _override("resolutionx", res_x)
            _override("resolutiony", res_y)

        for samples_name in ("samples", "camera_samples",
                             "samplesperpixel", "pathtracedsamples"):
            if settings.parm(samples_name) is not None:
                _override(samples_name, samples)
                break

        _override("camera", "/cameras/thumb_cam")

        return settings

    def _create_render_rop(self, parent: "hou.Node",
                           input_node: "hou.Node",
                           output_path: str):
        """Create the render ROP and configure output/resolution/camera."""
        configured = self._renderer_config.get("thumbnail_rop_type", "")
        candidates = [configured, "karma", "usdrender_rop", "usdrender"]
        rop = _create_first_available(parent, candidates, "thumb_rop")
        if rop is None:
            return None
        try:
            rop.setInput(0, input_node, 0)
        except Exception:
            pass

        # Output image — the parm name varies between karma / usdrender_rop.
        for parm_name in ("picture", "outputimage", "filename", "lopoutput"):
            p = rop.parm(parm_name)
            if p is not None:
                try:
                    p.set(output_path)
                    break
                except Exception:
                    pass

        # Resolution — on karma ROP this is `res1`/`res2` and writable.
        res_x, res_y = self._resolution
        res_tuple = rop.parmTuple("res")
        if res_tuple is not None:
            try:
                res_tuple.set((int(res_x), int(res_y)))
            except Exception as e:
                print(f"[ThumbnailRenderer] Skipping ROP res tuple: {e}")
        else:
            for name, val in (("res1", res_x), ("res2", res_y),
                              ("resolutionx", res_x), ("resolutiony", res_y)):
                p = rop.parm(name)
                if p is not None:
                    try:
                        p.set(int(val))
                    except Exception:
                        pass

        # Camera primpath
        for name in ("camera", "rendercamera"):
            p = rop.parm(name)
            if p is not None:
                try:
                    p.set("/cameras/thumb_cam")
                    break
                except Exception:
                    pass

        # Samples — pick first writable name.
        samples = max(16, self._renderer_config.get("default_samples", 64) // 4)
        for name in ("samples", "pathtracedsamples", "camera_samples",
                     "samplesperpixel"):
            p = rop.parm(name)
            if p is not None:
                try:
                    p.set(int(samples))
                    break
                except Exception:
                    pass

        # Render the current frame only.
        for name in ("trange", "frange"):
            p = rop.parm(name)
            if p is not None:
                try:
                    p.set(0)
                except Exception:
                    pass
                break

        return rop

    def render_batch_thumbnails(self, assets: list,
                                thumbnail_dir: str,
                                parent_node: Optional["hou.Node"] = None) -> dict:
        """
        Render thumbnails for multiple assets.

        Args:
            assets:        List of (usd_path, asset_name) tuples.
            thumbnail_dir: Directory to save thumbnails.
            parent_node:   Parent LOP node.

        Returns:
            Dict mapping asset_name → thumbnail_path (or empty on failure).
        """
        os.makedirs(thumbnail_dir, exist_ok=True)
        results = {}

        for usd_path, asset_name in assets:
            thumb_path = os.path.join(
                thumbnail_dir, f"{asset_name}.png"
            ).replace("\\", "/")

            success = self.render_thumbnail(
                usd_path, thumb_path, parent_node
            )
            results[asset_name] = thumb_path if success else ""

        return results
