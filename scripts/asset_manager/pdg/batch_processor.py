"""
PDG (TOPs) batch processor for the Asset Manager.

Builds a TOP network where:
    - a Generic Generator emits one work item per scanned asset
    - a Python Processor recursively builds the Solaris Component Builder
      network for that asset and presses both the USD export and the
      `thumbmakeicon` button on the `componentoutput` LOP
    - a final Python Processor records the produced USD + thumbnail paths
      back into the SQLite database

A synchronous fallback (`process_sequential`) is kept for callers that
want to run inline without PDG (used by the Scanner tab today).
"""

import json
import os
from typing import Callable, List, Optional

try:
    import hou
    HAS_HOU = True
except ImportError:
    HAS_HOU = False

from ..core.component_builder import ComponentBuilder
from ..core.usd_utils import ensure_hou
from ..database.asset_db import AssetDatabase
from ..database.models import AssetEntry, ScanResult
from ..ui.turntable_tab import TurntableTab


class BatchProcessor:
    """Builds + cooks a PDG network that processes every scanned asset
    through the Solaris Component Builder, with thumbnails rendered by
    `componentoutput`."""

    def __init__(self, db: AssetDatabase, renderer: str = "",
                 proxy_ratio: float = 0.1, sim_method: str = "convex_hull"):
        self._db = db
        # If no renderer passed explicitly, pick whatever the user selected
        # in the Settings tab (stored under the "renderer" meta key).
        if not renderer:
            try:
                renderer = db.get_meta("renderer", "karma_cpu") or "karma_cpu"
            except Exception:
                renderer = "karma_cpu"
        self._renderer = renderer
        self._proxy_ratio = proxy_ratio
        self._sim_method = sim_method

    def _read_thumb_settings(self):
        """Pull thumbnail/scale settings from the DB meta table. Falls back
        to sensible defaults if a key isn't set."""
        def _f(key, default):
            try:
                v = self._db.get_meta(key, "")
                return float(v) if v not in (None, "") else default
            except Exception:
                return default
        def _i(key, default):
            try:
                v = self._db.get_meta(key, "")
                return int(v) if v not in (None, "") else default
            except Exception:
                return default
        return {
            "asset_scale":    _f("asset_scale",    1.0),
            "camera_yaw":     _f("camera_yaw",     35.0),
            "camera_pitch":   _f("camera_pitch",   20.0),
            "thumb_distance": _f("thumb_distance", 0.0),
            "thumb_res_x":    _i("thumb_res_x",    640),
            "thumb_res_y":    _i("thumb_res_y",    480),
            "karma_samples":  _i("karma_samples",  64),
            "cam_focal":      _f("cam_focal",      40.0),
            "cam_aperture":   _f("cam_aperture",   25.0),
            "cam_near":       _f("cam_near",       0.1),
            "cam_far":        _f("cam_far",        1_000_000.0),
        }

    # ──────────────────────────────────────────────
    # PDG-driven batch
    # ──────────────────────────────────────────────

    def cook_batch(self, scan_results: List[ScanResult],
                   output_dir: str,
                   thumbnail_dir: str,
                   block: bool = True,
                   progress_callback: Optional[Callable] = None) -> "hou.Node":
        """Build and cook a TOP network that processes every asset.

        Args:
            scan_results: assets to process.
            output_dir:   root for USD output (componentoutput cache location).
            thumbnail_dir: where the gallery PNGs are copied.
            block:        when True, the call blocks until cooking finishes.
            progress_callback: optional `fn(index, total, name)` driver hook.

        Returns:
            The TOP network node (left in /obj so the user can inspect it).
        """
        ensure_hou()

        topnet = self._create_top_network(scan_results, output_dir,
                                          thumbnail_dir)
        if progress_callback:
            progress_callback(0, len(scan_results), "Submitting to PDG…")

        # Cook the TOP graph. PDG handles recursion across work items.
        try:
            self._cook_topnet(topnet, block=block)
        except Exception as e:
            print(f"[BatchProcessor] PDG cook failed, falling back to "
                  f"sequential: {e}")
            return self.process_sequential(
                scan_results, output_dir, thumbnail_dir,
                progress_callback=progress_callback,
            )

        if progress_callback:
            progress_callback(len(scan_results), len(scan_results), "Done")
        return topnet

    def _create_top_network(self, scan_results, output_dir, thumbnail_dir):
        """Build the TOP graph: generator -> build/render -> register-in-db.
        The wedge/generator is what gives us recursion across all assets."""
        ensure_hou()
        parent = hou.node("/obj")
        # Place each batch in its own topnet so re-runs don't pile up.
        topnet = parent.createNode("topnet", "asset_manager_batch")

        scan_payload = [
            {
                "asset_name": sr.asset_name,
                "geo_file": sr.geo_file.replace("\\", "/"),
                "texture_set": sr.texture_set.to_dict(),
                "source_dir": sr.source_dir.replace("\\", "/"),
            }
            for sr in scan_results
        ]
        hdri_path = ""
        try:
            hdri_path = self._db.get_meta("thumbnail_hdri", "")
        except Exception:
            pass

        # 1. Generator — one work item per asset (recurses across all).
        generator = topnet.createNode("genericgenerator", "asset_items")
        self._configure_generator(generator, scan_payload, output_dir,
                                  thumbnail_dir, hdri_path)

        # 2. Python Processor — build the Component Builder network for
        # each work item and cook the componentoutput.
        builder = topnet.createNode("pythonprocessor", "build_and_render")
        builder.setInput(0, generator, 0)
        self._configure_builder_processor(builder)

        # 3. Python Processor — write the resulting AssetEntry to SQLite.
        registrar = topnet.createNode("pythonprocessor", "register_in_db")
        registrar.setInput(0, builder, 0)
        self._configure_registrar(registrar)

        topnet.layoutChildren()
        return topnet

    # ── PDG node scripts ──────────────────────────────────────────────

    def _configure_generator(self, node, scan_payload, output_dir,
                             thumbnail_dir, hdri_path):
        """Emit one work item per scan result. The data is serialized as
        a per-item JSON attribute so the downstream processor recurses
        through the batch with PDG's normal partitioning."""
        try:
            node.parm("itemcount").set(len(scan_payload))
        except Exception:
            pass

        scan_json = json.dumps(scan_payload)
        script = f"""
import json

SCAN_DATA = {scan_json!r}
OUTPUT_DIR = {output_dir.replace(os.sep, '/')!r}
THUMB_DIR = {thumbnail_dir.replace(os.sep, '/')!r}
HDRI_PATH = {hdri_path.replace(os.sep, '/')!r}
RENDERER = {self._renderer!r}
PROXY_RATIO = {self._proxy_ratio!r}
SIM_METHOD = {self._sim_method!r}

data = json.loads(SCAN_DATA)
i = work_item.index
if i < len(data):
    a = data[i]
    work_item.setStringAttrib("asset_name", a["asset_name"])
    work_item.setStringAttrib("geo_file", a["geo_file"])
    work_item.setStringAttrib("texture_json", json.dumps(a["texture_set"]))
    work_item.setStringAttrib("source_dir", a["source_dir"])
    work_item.setStringAttrib("output_dir", OUTPUT_DIR)
    work_item.setStringAttrib("thumbnail_dir", THUMB_DIR)
    work_item.setStringAttrib("hdri_path", HDRI_PATH)
    work_item.setStringAttrib("renderer", RENDERER)
    work_item.setFloatAttrib("proxy_ratio", PROXY_RATIO)
    work_item.setStringAttrib("sim_method", SIM_METHOD)
"""
        _set_first(node, ("generatescript", "script", "python"), script)

    def _configure_builder_processor(self, node):
        """For each work item, build the Component Builder network and
        cook the componentoutput. Returns the produced USD + thumbnail
        paths as work item attributes for the next stage."""
        scripts_dir = self._scripts_dir()
        script = f"""
import json, os, sys

scripts_dir = {scripts_dir!r}
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from asset_manager.core.component_builder import ComponentBuilder
from asset_manager.database.models import ScanResult, TextureSet

asset_name   = work_item.attribValue("asset_name")
geo_file     = work_item.attribValue("geo_file")
tex_json     = work_item.attribValue("texture_json")
source_dir   = work_item.attribValue("source_dir")
output_dir   = work_item.attribValue("output_dir")
thumb_dir    = work_item.attribValue("thumbnail_dir")
hdri_path    = work_item.attribValue("hdri_path")
renderer     = work_item.attribValue("renderer")
proxy_ratio  = work_item.attribValue("proxy_ratio")
sim_method   = work_item.attribValue("sim_method")

scan = ScanResult(
    asset_name=asset_name,
    geo_file=geo_file,
    texture_set=TextureSet.from_dict(json.loads(tex_json)),
    source_dir=source_dir,
)
builder = ComponentBuilder(
    renderer=renderer,
    proxy_ratio=proxy_ratio,
    sim_method=sim_method,
    hdri_path=hdri_path,
)
entry = builder.build_asset(scan, output_dir, thumbnail_dir=thumb_dir)

work_item.setStringAttrib("entry_json", json.dumps(entry.to_dict()))
work_item.setStringAttrib("usd_path", entry.usd_output_path)
work_item.setStringAttrib("thumb_path", entry.thumbnail_path)
work_item.setStringAttrib("status", entry.status)
"""
        _set_first(node, ("script", "python", "code"), script)

    def _configure_registrar(self, node):
        """Write the produced AssetEntry into the database."""
        db_path = self._db.db_path.replace("\\", "/")
        scripts_dir = self._scripts_dir()
        script = f"""
import json, sys

scripts_dir = {scripts_dir!r}
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

from asset_manager.database.asset_db import AssetDatabase
from asset_manager.database.models import AssetEntry

entry_json = work_item.attribValue("entry_json")
entry = AssetEntry.from_dict(json.loads(entry_json))
db = AssetDatabase({db_path!r})
db.add_asset(entry)
"""
        _set_first(node, ("script", "python", "code"), script)

    @staticmethod
    def _cook_topnet(topnet, block=True):
        """Trigger a cook, blocking until done when requested. Uses the
        PDG GraphContext when available; falls back to the cook button."""
        try:
            import pdg
            ctx = topnet.getPDGGraphContext()
            if ctx is not None:
                ctx.cook(blocking=block)
                return
        except Exception:
            pass

        # Fallback: press the cook button. This is asynchronous in some
        # versions, so when blocking is requested, fall back to the inline
        # callable surface by raising — the caller catches and runs the
        # sequential fallback path.
        dirty = topnet.parm("dirtybutton")
        if dirty is not None:
            try:
                dirty.pressButton()
            except Exception:
                pass
        btn = topnet.parm("cookbutton")
        if btn is None:
            raise RuntimeError("topnet has no cookbutton parm")
        btn.pressButton()
        if block:
            raise RuntimeError(
                "Cannot block on cook without pdg.GraphContext support"
            )

    @staticmethod
    def _scripts_dir():
        return os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ).replace("\\", "/")

    # ──────────────────────────────────────────────
    # Synchronous fallback (no PDG)
    # ──────────────────────────────────────────────

    def process_sequential(self, scan_results: List[ScanResult],
                           output_dir: str,
                           thumbnail_dir: str,
                           progress_callback: Optional[Callable] = None,
                           ) -> List[AssetEntry]:
        """Inline loop driver — used by the Scanner tab and as the PDG
        fallback when no scheduler is available."""
        ensure_hou()

        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(thumbnail_dir, exist_ok=True)

        hdri_path = ""
        try:
            hdri_path = self._db.get_meta("thumbnail_hdri", "")
        except Exception:
            pass

        ts = self._read_thumb_settings()
        turntable_settings = TurntableTab.read_turntable_settings(self._db)
        builder = ComponentBuilder(
            renderer=self._renderer,
            proxy_ratio=self._proxy_ratio,
            sim_method=self._sim_method,
            hdri_path=hdri_path,
            thumbnail_resolution=(ts["thumb_res_x"], ts["thumb_res_y"]),
            asset_scale=ts["asset_scale"],
            camera_yaw=ts["camera_yaw"],
            camera_pitch=ts["camera_pitch"],
            thumb_distance=ts["thumb_distance"],
            karma_samples=ts["karma_samples"],
            camera_focal=ts["cam_focal"],
            camera_aperture=ts["cam_aperture"],
            camera_near=ts["cam_near"],
            camera_far=ts["cam_far"],
            turntable_settings=turntable_settings,
        )

        entries = []
        total = len(scan_results)
        for i, result in enumerate(scan_results):
            if progress_callback:
                progress_callback(i, total, result.asset_name)
            try:
                entry = builder.build_asset(
                    result, output_dir, thumbnail_dir=thumbnail_dir
                )
                self._db.add_asset(entry)
                entries.append(entry)
                builder.cleanup_sop_nodes(result.asset_name)
            except Exception as e:
                print(f"[BatchProcessor] Error processing "
                      f"{result.asset_name}: {e}")
                err = AssetEntry(
                    name=result.asset_name,
                    source_geo_path=result.geo_file,
                    status="error",
                    error_message=str(e),
                )
                self._db.add_asset(err)
                entries.append(err)

        if progress_callback:
            progress_callback(total, total, "Complete")
        return entries


def _set_first(node, candidate_parm_names, value):
    """Set the first matching parm on `node`. Returns True on success."""
    for n in candidate_parm_names:
        try:
            p = node.parm(n)
            if p is None:
                continue
            p.set(value)
            return True
        except Exception:
            continue
    return False
