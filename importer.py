"""Importer: orchestrates a single car import (replaces the old core.py top-level block).

Pipeline: optional GameDB lookup -> parse the carbin (CarScene) -> load each referenced modelbin
(geometry + embedded materials) -> assemble wheel/tire/brake/control-arm transforms -> build
Blender meshes (geometry layer) and materials (data-driven materials layer) -> place objects into
the nested collection hierarchy. All option inputs come from a single ImportOptions.
"""

import os
import pathlib
import sqlite3
from contextlib import closing

from .parsing.binary import BinaryStream
from .parsing.paths import (
    GamePathResolver,
    resolve_tire_model_name,
    tire_modelbin_game_path,
    find_media_root,
    resolve_import_game_key,
)
from .parsing.carbin import CarScene, ParseContext, Part, SharedCarModel, Upgrade
from .geometry import Modelbin, build_mesh_object
from .materials.translate import translator_for
from .materials.instance_key import material_instance_key
from .materials import nodes_v3 as nodes
from .materials.manufacturer_colors import (
    find_manufacturer_colors,
    load_manufacturer_colors_json,
    stock_paint_rgba,
)
from . import assembly
from .collections import CollectionWrapper
from .contract import (
    PROP_BONE,
    PROP_BONE_REST,
    PROP_CARBIN_BONE,
    PROP_CARBIN_BONE_INDEX,
    PROP_CAR_ROOT,
    PROP_MATERIAL_DIAG_KEY,
    PROP_MATERIAL_DIAG_STATUS,
    PROP_MESH_NAME,
    PROP_MODEL_PATH,
    PROP_RIGID_BONE,
)

CARS_INTERNAL = r"GAME:\Media\Cars"
TIRES_INTERNAL = r"GAME:\Media\Cars\_library\scene\tires"


class Importer:
    def __init__(self, options):
        self.o = options
        self.resolver = GamePathResolver(
            options.game_path, options.cars_dir_override,
            options.tires_dir_override, options.materials_dir_override,
            car_zip_path=getattr(options, "car_zip_path", None),
        )
        self.image_cache = {}        # texture guid -> bpy image
        self.built_materials = {}    # material name -> bpy material
        self.material_specs = {}     # material name -> MaterialSpec (or None)
        self.root_collection = None
        media = find_media_root(options.game_path) or options.game_path
        self.game_key = resolve_import_game_key(
            filepath=getattr(options, "filepath", None),
            game_path=options.game_path,
            car_root=getattr(options, "car_root_dir", None),
        )
        if self.game_key == "unknown":
            raise RuntimeError(
                "Cannot determine game (FH5/FH6/FM) for materials. "
                "Set Preferences > Game Installations for this title, or import from a "
                "path that identifies the game."
            )
        self._builder = translator_for(self.game_key, media_root=media)
        self._material_failures = []
        self._progress = None
        self.media_name = options.media_name
        self._scene_skeleton_mb = None
        self.material_report = None
        self._load_stock_paint()

    def _progress_begin(self, total, message="Importing Forza car"):
        self._progress = {"i": 0, "total": max(1, int(total)), "message": message}
        try:
            import bpy
            wm = bpy.context.window_manager
            wm.progress_begin(0, self._progress["total"])
        except Exception:
            pass
        print(f"Forza: {message} (0/{self._progress['total']})", flush=True)

    def _progress_step(self, label=""):
        if not self._progress:
            return
        self._progress["i"] += 1
        i = self._progress["i"]
        total = self._progress["total"]
        try:
            import bpy
            bpy.context.window_manager.progress_update(min(i, total))
            # Force a UI refresh during blocking execute so the status bar moves.
            try:
                bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)
            except Exception:
                pass
        except Exception:
            pass
        if label and (i == 1 or i == total or i % 3 == 0):
            print(f"Forza: {label} ({i}/{total})", flush=True)

    def _progress_end(self):
        if not self._progress:
            return
        try:
            import bpy
            bpy.context.window_manager.progress_end()
        except Exception:
            pass
        print(
            f"Forza: {self._progress['message']} done "
            f"({self._progress['i']}/{self._progress['total']})",
            flush=True,
        )
        self._progress = None

    def _count_loadable_models(self, scene):
        o = self.o
        n = 0
        for part in [*scene.parts, *scene.upgradable_parts]:
            if o.suspension_only and part.type not in (44, 4, 2, 8):
                continue
            for model in part.models:
                n += 1
        return n

    def _load_stock_paint(self):
        """Optional ManufacturerColors.json (CLI dump) stock paint for carpaint without instance color."""
        o = self.o
        search = o.car_root_dir or o.cars_dir_override or o.game_path
        path = find_manufacturer_colors(search) if search else None
        if path is None and o.cars_dir_override:
            # dump layout: .../FH6_FER_F80_25/ManufacturerColors.json beside Materials/
            parent = os.path.dirname(o.cars_dir_override.rstrip("\\/"))
            path = find_manufacturer_colors(parent)
        if not path:
            return
        try:
            rgba = stock_paint_rgba(load_manufacturer_colors_json(path))
        except (OSError, ValueError, KeyError) as e:
            print(f"Forza: ManufacturerColors load skipped ({e})")
            return
        if rgba:
            self._builder.stock_paint_rgba = rgba
            print(f"Forza: applied stock paint from {os.path.basename(path)}")

    # ------------------------------------------------------------------ entry
    def run(self):
        import bpy
        self._check_blender_version(bpy)
        o = self.o
        self._material_parse_failures = []
        self._init_material_report(bpy)

        if o.use_materials:
            media = find_media_root(o.game_path) or o.game_path
            from .parsing.paths import media_has_car_library
            if not media_has_car_library(media):
                print(
                    "Forza: WARNING — game_path has no cars\\_library "
                    f"({media!r}). Materials cannot be built without the shared library. "
                    "Set Preferences > Game Installations to the game install folder "
                    "(the folder that contains Content; Content or Content\\media also work)."
                )

        if o.use_db:
            self._query_gamedb()

        if not o.TireModelName:
            o.TireModelName = resolve_tire_model_name(
                o.game_path, o.tires_dir_override, o.TireModelName
            )
        else:
            o.TireModelName = resolve_tire_model_name(
                o.game_path, o.tires_dir_override, o.TireModelName
            )
        if o.TireModelName:
            print(f"Resolved stock tire compound: tire_{o.TireModelName}")

        media_name = self.media_name
        carbin_internal = fr"{CARS_INTERNAL}\{media_name}\{media_name}.carbin"
        tire_internal = tire_modelbin_game_path(o.TireModelName) if o.TireModelName else ""
        carbin_path = self.resolver.resolve(carbin_internal)
        if not carbin_path or not os.path.isfile(carbin_path):
            hint = o.car_zip_path or "(no zip)"
            raise FileNotFoundError(
                f"Could not find {media_name}.carbin (looked up {carbin_internal}). "
                f"Zip={hint}. Put the car .zip under Media\\Cars or pick the .zip / "
                f"extracted .carbin via File → Import."
            )

        ctx = ParseContext()
        ctx.series = o.series
        scene = CarScene()
        scene.deserialize(BinaryStream.from_path(carbin_path), ctx)
        self.scene = scene

        skeleton_modelbin = None
        scene_skeleton_mb = self._load_scene_skeleton(scene)
        if o.suspension_transform_type == 0 and scene_skeleton_mb is not None:
            skeleton_modelbin = scene_skeleton_mb
            if o.create_spheres:
                for bone in skeleton_modelbin.skeleton.bones:
                    self._add_sphere(bone.transform[3], bone.name)

        if scene.part_wheels is not None and o.TireModelName and tire_internal:
            scene.part_wheels.models = [m for m in scene.part_wheels.models
                                        if m.levels_of_detail & o.requested_level_of_detail]
            assembly.synthesize_tire_part(scene, tire_internal)
            scene.part_tires.tire_models = [None] * 6
            scene.part_wheels.wheel_models = [None] * 6
            if scene.part_brakes is not None:
                scene.part_brakes.rotor_models = [None] * 6
                scene.part_brakes.caliper_models = [None] * 6
            scene.control_arm_models = [None] * 6
        elif scene.part_wheels is not None:
            # Wheel/brake assembly still classifies models when tires are skipped
            # (no TireModelName / DB off / missing tire files).
            if not hasattr(scene.part_wheels, "wheel_models"):
                scene.part_wheels.wheel_models = [None] * 6
            if scene.part_brakes is not None:
                if not hasattr(scene.part_brakes, "rotor_models"):
                    scene.part_brakes.rotor_models = [None] * 6
                if not hasattr(scene.part_brakes, "caliper_models"):
                    scene.part_brakes.caliper_models = [None] * 6
            if not o.TireModelName:
                print("Warning: no TireModelName — rubber tires will not be synthesized.")

        self._load_models(scene, skeleton_modelbin)
        if self._material_parse_failures:
            n = len(self._material_parse_failures)
            sample = self._material_parse_failures[0][1]
            print(
                f"Forza: {n} material(s) failed to parse (parent materialbin/shader missing). "
                f"First: {sample}. Meshes get magenta placeholders until Media cars\\_library resolves."
            )

        car_bodies = {}
        for upgradable_part in scene.upgradable_parts:
            for upgrade in upgradable_part.upgrades.values():
                if upgrade.car_body_id == -1:
                    continue
                if upgrade.car_body_id in car_bodies:
                    if car_bodies[upgrade.car_body_id] != upgrade.parent_is_stock:
                        print(f"Warning: CarBody {upgrade.car_body_id} is marked as both stock and non-stock.")
                else:
                    car_bodies[upgrade.car_body_id] = upgrade.parent_is_stock
        self.car_bodies = car_bodies

        if scene.part_wheels is not None:
            assembly.init_wheel_brake_transforms(scene, o)

        try:
            self._build_scene(scene, scene_skeleton_mb)
        finally:
            self._progress_end()

        if self.root_collection is not None:
            self.root_collection.sort()
            self._finalize_material_report()
        else:
            print("Warning: no meshes were imported (check LOD/draw group filters and file paths).")

    def _init_material_report(self, bpy):
        import importlib

        from .materials.diagnostics import ImportMaterialReport

        bl_ver = ".".join(str(x) for x in bpy.app.version)
        forza_ver = "unknown"
        try:
            pkg = importlib.import_module(__package__)
            forza_ver = ".".join(str(x) for x in pkg.bl_info["version"])
        except Exception:
            pass
        self.material_report = ImportMaterialReport(
            forza_version=forza_ver,
            blender_version=bl_ver,
            game_key=self.game_key,
            pipeline="clean_v3",
            car_id=self.media_name,
        )

    def _finalize_material_report(self):
        if self.material_report is None or self.root_collection is None:
            return
        from .materials.report_store import store_report_on_collection

        collection = self.root_collection.layer_collection.collection
        name = store_report_on_collection(collection, self.material_report)
        summary = self.material_report.summary_counts()
        print(
            "Forza material report: "
            f"encountered={summary['materials_encountered']} "
            f"supported={summary['fully_supported']} "
            f"partial={summary['partially_supported']} "
            f"unresolved={summary['unresolved']} "
            f"builder_errors={summary['builder_errors']} "
            f"diagnostic_objects={summary['objects_with_diagnostic_materials']} "
            f"(text={name})",
            flush=True,
        )

    # ----------------------------------------------------------- model loading
    def _load_models(self, scene, skeleton_modelbin):
        o = self.o
        total = self._count_loadable_models(scene) * 2  # load + build phases
        self._progress_begin(total, f"Importing {self.media_name}")
        for part in [*scene.parts, *scene.upgradable_parts]:
            if o.suspension_only and part.type not in (44, 4, 2, 8):
                continue
            for model in part.models:
                if type(model) is SharedCarModel:
                    upgrade_ids = part.upgrades.keys() & model.upgrade_ids
                    if not upgrade_ids:
                        print("Warning: Model is not attached to any upgrade")
                        for upgrade_id in model.upgrade_ids:
                            upgrade = Upgrade()
                            upgrade.is_stock = 0
                            upgrade.id = upgrade_id
                            upgrade.car_body_id = -1
                            upgrade.parent_is_stock = 1
                            part.upgrades[upgrade_id] = upgrade
                    elif len(upgrade_ids) != len(model.upgrade_ids):
                        model.upgrade_ids = list(upgrade_ids)
                    model = model.model
                if o.suspension_only and part.type == 2 and not model.bone_name.startswith("controlArm"):
                    self._progress_step("skip model")
                    continue
                if model.draw_groups & o.requested_draw_group == 0:
                    self._progress_step("skip model")
                    continue
                mpath_early = (getattr(model, "path", None) or "").lower().replace("\\", "/")
                # Wing-mirror modelbins often advertise a different LOD mask than
                # the meshes inside (AMG L housings are LOD1-only). Never skip
                # the whole model on LOD — mesh-tier filter handles duplicates.
                if "wingmirror" not in mpath_early:
                    if model.levels_of_detail & o.requested_level_of_detail == 0:
                        self._progress_step("skip model")
                        continue

                p = self.resolver.resolve(model.path)
                if not os.path.isfile(p) and o.TireModelName and "tire_" in model.path.lower():
                    alt = tire_modelbin_game_path(o.TireModelName)
                    p = self.resolver.resolve(alt)
                if not os.path.isfile(p):
                    print(f"Warning: skipping missing model file: {model.path} -> {p}")
                    self._progress_step("missing model")
                    continue
                mpath_load = (getattr(model, "path", None) or "").lower().replace("\\", "/")
                lod_for_deserialize = o.requested_level_of_detail
                if "wingmirror" in mpath_load:
                    # Need every mesh tier present so the single-tier picker can
                    # fall back (AMG L housings are LOD1-only).
                    lod_for_deserialize = 0xFF
                model.modelbin = Modelbin()
                model.modelbin.deserialize(
                    BinaryStream.from_path(p),
                    requested_level_of_detail=lod_for_deserialize,
                    resolver=self.resolver,
                    parse_materials=o.use_materials)
                fails = getattr(model.modelbin, "_material_parse_failures", None)
                if fails:
                    self._material_parse_failures.extend(fails)

                sphere_cb = (lambda t, n: self._add_sphere(t, n)) if o.create_spheres else None
                assembly.apply_part_assignment(scene, o, part, model, skeleton_modelbin, sphere_cb)
                leaf = os.path.basename((getattr(model, "path", None) or "").replace("\\", "/"))
                self._progress_step(f"loaded {leaf}")

    def _load_scene_skeleton(self, scene):
        """Parse ``scene/_skeleton.modelbin`` once (carbin layer B + suspension mode 0)."""
        if self._scene_skeleton_mb is not None:
            return self._scene_skeleton_mb if self._scene_skeleton_mb is not False else None
        self._scene_skeleton_mb = False
        path = getattr(scene, "skeleton_path", None)
        if not path:
            return None
        p = self.resolver.resolve(path)
        if not os.path.isfile(p):
            print(f"Warning: scene skeleton not found: {path}")
            return None
        try:
            mb = Modelbin()
            mb.deserialize(
                BinaryStream.from_path(p),
                requested_level_of_detail=self.o.requested_level_of_detail,
                resolver=self.resolver,
                parse_materials=False,
            )
            self._scene_skeleton_mb = mb
            return mb
        except (OSError, ValueError, KeyError) as exc:
            print(f"Warning: could not load scene skeleton ({exc})")
            return None

    # ----------------------------------------------------------- scene build
    def _build_scene(self, scene, scene_skeleton_mb=None):
        import bpy
        o = self.o
        for part in [*scene.parts, *scene.upgradable_parts]:
            if o.suspension_only and part.type not in (44, 4, 2, 8):
                continue
            for model in part.models:
                upgrade_ids = None
                if type(model) is SharedCarModel:
                    upgrade_ids = model.upgrade_ids
                    model = model.model
                modelbin = model.modelbin
                if modelbin is None:
                    continue

                if o.suspension_transform_type == 1 and o.create_spheres:
                    self._add_sphere(model.transform[3], model.bone_name)

                carbin_attach_bone = None
                if assembly.uses_carbin_layer_b(part, model):
                    inst = assembly.carbin_instance_transform(
                        model, modelbin, scene_skeleton_mb
                    )
                    if inst is not None:
                        modelbin.set_post_bone_transform(inst)
                        carbin_attach_bone = assembly.carbin_attach_bone_name(
                            model, modelbin, scene_skeleton_mb
                        )

                mpath = (getattr(model, "path", None) or "").lower()
                lod_mask = int(o.requested_level_of_detail)
                # Wing mirrors: pick one LOD tier (AMG L is LOD1-only). Never import
                # LODS0 and LOD1 of the same housing together.
                mirror_tier = None
                if "wingmirror" in mpath.replace("\\", "/"):
                    present = 0
                    for m in modelbin.meshes:
                        present |= int(getattr(m, "levels_of_detail", 0) or 0)
                    path_l = "wingmirrorl" in mpath.replace("\\", "/")
                    path_r = "wingmirrorr" in mpath.replace("\\", "/")

                    def _primary_lod_bit(flags: int) -> int:
                        for bit in (1, 2, 4, 8, 16, 32, 64):
                            if flags & bit:
                                return bit
                        return 0

                    def _tier_has_side_housing(tier: int) -> bool:
                        """AMG L bin stores misnamed wingMirrorR_* at LODS0; real L is LOD1."""
                        for m in modelbin.meshes:
                            fl = int(getattr(m, "levels_of_detail", 0) or 0)
                            if _primary_lod_bit(fl) != tier:
                                continue
                            nm = (getattr(m, "name", None) or "").lower()
                            if "sidemarker" in nm:
                                continue
                            if path_l and "wingmirrorl" in nm:
                                return True
                            if path_r and "wingmirrorr" in nm:
                                return True
                        return False

                    if present:
                        # Prefer requested LOD bits, then other present tiers.
                        # AMG L: LODS0 has only misnamed wingMirrorR_* (+ SideMarker);
                        # real wingMirrorL_* housings are LOD1 — must fall through.
                        preferred, fallback = [], []
                        for bit in (1, 2, 4, 8, 16, 32, 64):
                            if not (present & bit):
                                continue
                            (preferred if (lod_mask & bit) else fallback).append(bit)
                        candidates = preferred + fallback
                        mirror_tier = None
                        for bit in candidates:
                            if _tier_has_side_housing(bit):
                                mirror_tier = bit
                                break
                        if mirror_tier is None and candidates:
                            mirror_tier = candidates[0]
                        if mirror_tier is not None:
                            lod_mask = mirror_tier
                for mesh in modelbin.meshes:
                    flags = int(getattr(mesh, "levels_of_detail", 0) or 0)
                    if flags & lod_mask == 0:
                        continue
                    if mirror_tier is not None:
                        primary = 0
                        for bit in (1, 2, 4, 8, 16, 32, 64):
                            if flags & bit:
                                primary = bit
                                break
                        if primary != mirror_tier:
                            continue
                    # Drop cross-named housings inside the wrong-side bin (AMG L@LODS0).
                    if "wingmirror" in mpath.replace("\\", "/"):
                        nm = (getattr(mesh, "name", None) or "").lower()
                        if "sidemarker" not in nm:
                            if "wingmirrorl" in mpath.replace("\\", "/") and "wingmirrorr" in nm:
                                continue
                            if "wingmirrorr" in mpath.replace("\\", "/") and "wingmirrorl" in nm:
                                continue
                    if mesh.render_pass & 0x10 == 0:  # skip Shadow
                        continue
                    if o.hide_decal_transparent_pass and mesh.render_pass & 0x4 != 0:
                        continue
                    if part.type == 44 and mesh.render_pass & 0x4 != 0:
                        continue

                    md = modelbin.process_mesh(mesh)
                    _, obj = build_mesh_object(md, quadrangulate=o.quadrangulate_mesh)
                    self._tag_bone(
                        obj,
                        modelbin,
                        mesh,
                        model,
                        carbin_attach_bone=carbin_attach_bone,
                        scene_skeleton_mb=scene_skeleton_mb,
                    )
                    if o.car_root_dir:
                        obj[PROP_CAR_ROOT] = o.car_root_dir
                    self._assign_material(obj, modelbin, mesh)
                    self._place_object(obj, part, model, upgrade_ids)
                leaf = os.path.basename((getattr(model, "path", None) or "").replace("\\", "/"))
                self._progress_step(f"built {leaf}")

    # ----------------------------------------------------------- placement
    def _place_object(self, obj, part, model, upgrade_ids):
        if self.root_collection is None:
            name = self.scene.media_name
            if self.media_name.lower() == name:
                name = self.media_name
            self.root_collection = CollectionWrapper(name)

        leaf_type = model.type or "Model"
        if type(part) is Part:
            bodies = self.car_bodies.items() if self.car_bodies else [(None, None)]
            for (car_body_id, is_stock) in bodies:
                collection = self.root_collection
                if car_body_id is not None:
                    collection = collection.open(str(car_body_id), is_stock)
                collection = collection.open(part.get_type_name())
                name = leaf_type
                if part.type in (4, 8, 44):
                    name += " " + model.bone_name[-2:]
                collection = collection.open(name, name != "InteriorWindows")
                collection.add(obj)
        else:
            for upgrade_id in (upgrade_ids or ()):
                if upgrade_id not in part.upgrades:
                    continue
                upgrade = part.upgrades[upgrade_id]
                bodies = (self.car_bodies.items() if upgrade.car_body_id == -1
                          else [(upgrade.car_body_id, upgrade.parent_is_stock)])
                for (car_body_id, is_stock) in bodies:
                    collection = self.root_collection.open(str(car_body_id), is_stock)
                    collection = collection.open(part.get_type_name())
                    name = str(upgrade_id)
                    if upgrade.is_stock:
                        name += " [stock]"
                    collection = collection.open(name, upgrade.is_stock)
                    collection = collection.open(leaf_type, leaf_type != "InteriorWindows")
                    collection.add(obj)

    # ----------------------------------------------------------- materials
    def _assign_material(self, obj, modelbin, mesh):
        mid = mesh.material_id
        if not (0 <= mid < len(modelbin.materials)):
            return
        pm = modelbin.materials[mid]
        name = material_instance_key(pm, game_key=self.game_key)
        if not name:
            return

        if not (self.o.use_materials and pm.obj is not None):
            return

        from .materials.diagnose import resolve_with_diagnostics
        from .materials.diagnostic_material import get_unresolved_material
        from .materials.diagnostics import (
            AssignmentOutcome,
            MaterialStatus,
            StageOutcome,
        )
        from .materials.pipeline_v3 import MaterialTranslateError
        from .materials.shader_bindings import ShaderBindingError

        cached = self.material_specs.get(name, "missing")
        if cached == "missing":
            result = resolve_with_diagnostics(
                self._builder, name, pm.obj, resolver=self.resolver
            )
            diag = result.diagnostic
            spec = result.spec
            self.material_specs[name] = spec
            if self.material_report is not None:
                self.material_report.upsert(diag)
            if spec is None or not spec.valid:
                print(
                    f"Material unresolved '{name}': "
                    f"{diag.status.value}: {diag.failure_reason or diag.errors}"
                )
                self._material_failures.append(
                    (name, diag.failure_reason or diag.status.value)
                )
        else:
            spec = cached
            diag = (
                self.material_report.entries.get(name)
                if self.material_report is not None
                else None
            )

        mat = None
        assign_diagnostic = False
        if spec is not None and spec.valid:
            mat = self.built_materials.get(name)
            graph_v = getattr(nodes, "MATERIAL_GRAPH_VERSION", 0)
            if mat is None or mat.get("forza_graph_v", 0) != graph_v:
                try:
                    mat = nodes.build_material(spec, self.resolver, self.image_cache)
                    self.built_materials[name] = mat
                    if self.material_report is not None and name in self.material_report.entries:
                        prev = self.material_report.entries[name]
                        # Construction succeeded; keep PARTIAL if capability was partial.
                        self.material_report.upsert(
                            prev.with_construction(outcome=StageOutcome.OK)
                        )
                except (MaterialTranslateError, ShaderBindingError, RuntimeError) as e:
                    print(f"Material unresolved '{name}': node graph: {e}")
                    self._material_failures.append((name, f"node graph: {e}"))
                    assign_diagnostic = True
                    if self.material_report is not None and name in self.material_report.entries:
                        prev = self.material_report.entries[name]
                        self.material_report.upsert(
                            prev.with_construction(
                                outcome=StageOutcome.FAILED,
                                status=MaterialStatus.BUILDER_ERROR,
                                error=str(e),
                            )
                        )
                    mat = None
            if mat is None:
                assign_diagnostic = True
        else:
            assign_diagnostic = True

        if assign_diagnostic:
            mat = get_unresolved_material()
            obj[PROP_MATERIAL_DIAG_KEY] = name
            status_val = (
                diag.status.value
                if diag is not None
                else MaterialStatus.UNRESOLVED_CAPABILITY.value
            )
            obj[PROP_MATERIAL_DIAG_STATUS] = status_val
            if self.material_report is not None:
                if name not in self.material_report.entries and diag is not None:
                    self.material_report.upsert(diag)
                if name in self.material_report.entries:
                    self.material_report.upsert(
                        self.material_report.entries[name].with_assignment(
                            outcome=AssignmentOutcome.ASSIGNED_DIAGNOSTIC,
                            object_name=obj.name,
                        )
                    )
        else:
            if PROP_MATERIAL_DIAG_KEY in obj:
                del obj[PROP_MATERIAL_DIAG_KEY]
            if PROP_MATERIAL_DIAG_STATUS in obj:
                del obj[PROP_MATERIAL_DIAG_STATUS]
            if self.material_report is not None and name in self.material_report.entries:
                self.material_report.upsert(
                    self.material_report.entries[name].with_assignment(
                        outcome=AssignmentOutcome.ASSIGNED_RESOLVED,
                        object_name=obj.name,
                    )
                )

        if mat is not None:
            obj.data.materials.append(mat)

    # ----------------------------------------------------------- bone tagging
    @staticmethod
    def _bone_rest_props(bone_transform_row):
        t = bone_transform_row
        return [
            -t[0][0], -t[1][0], -t[2][0], -t[3][0],
            -t[0][2], -t[1][2], -t[2][2], -t[3][2],
             t[0][1],  t[1][1],  t[2][1],  t[3][1],
             t[0][3],  t[1][3],  t[2][3],  t[3][3],
        ]

    @staticmethod
    def _scene_bone_row_rest(scene_skeleton_mb, bone_name):
        skel = getattr(scene_skeleton_mb, "skeleton", None) if scene_skeleton_mb else None
        if not bone_name or skel is None:
            return None
        want = bone_name.strip()
        for bone in skel.bones:
            if bone.name == want:
                return bone.transform
        return None

    def _tag_bone(
        self,
        obj,
        modelbin,
        mesh,
        model,
        *,
        carbin_attach_bone=None,
        scene_skeleton_mb=None,
    ):
        """Stamp carbin/modelbin bind metadata and effective ``forza_bone`` for animation."""
        obj[PROP_MODEL_PATH] = getattr(model, "path", None) or ""
        obj[PROP_MESH_NAME] = getattr(mesh, "name", None) or ""
        obj[PROP_CARBIN_BONE] = getattr(model, "bone_name", None) or ""
        obj[PROP_CARBIN_BONE_INDEX] = int(getattr(model, "bone_index", -1))

        rigid_name = None
        rigid_rest = None
        part_sk = getattr(modelbin, "skeleton", None)
        if part_sk is not None and 0 <= mesh.bone_index < len(part_sk.bones):
            rb = part_sk.bones[mesh.bone_index]
            rigid_name = rb.name
            rigid_rest = self._bone_rest_props(rb.transform)
            obj[PROP_RIGID_BONE] = rigid_name

        attach_name = None
        attach_rest = None

        if carbin_attach_bone:
            row = self._scene_bone_row_rest(scene_skeleton_mb, carbin_attach_bone)
            if row is not None:
                attach_name = carbin_attach_bone
                attach_rest = self._bone_rest_props(row)

        if attach_name is None and not assembly.is_root_carbin_bone(
            getattr(model, "bone_name", None)
        ):
            _bw, resolved = assembly.resolve_carbin_bone_world(
                part_sk,
                getattr(scene_skeleton_mb, "skeleton", None)
                if scene_skeleton_mb
                else None,
                getattr(model, "bone_name", None),
                getattr(model, "bone_index", -1),
            )
            if resolved:
                row = self._scene_bone_row_rest(scene_skeleton_mb, resolved)
                if row is not None:
                    attach_name = resolved
                    attach_rest = self._bone_rest_props(row)

        if attach_name is None and rigid_name:
            attach_name = rigid_name
            attach_rest = rigid_rest

        from .parsing.mojo_mirror_bind import resolve_skeld_mirror_attach

        scene_sk = (
            getattr(scene_skeleton_mb, "skeleton", None)
            if scene_skeleton_mb
            else None
        )
        mirror_hit = resolve_skeld_mirror_attach(
            model_path=obj[PROP_MODEL_PATH],
            mesh_name=obj[PROP_MESH_NAME],
            rigid_name=rigid_name,
            carbin_bone=getattr(model, "bone_name", None),
            part_skeleton=part_sk,
            scene_skeleton=scene_sk,
        )
        if mirror_hit is not None:
            attach_name, attach_rest = mirror_hit
            attach_rest = self._bone_rest_props(attach_rest)

        # Aero / active-wing meshes: attach from modelbin RigidBoneIndex only
        # (PROP_RIGID_BONE above). No mesh-name→bone invent table.

        if attach_name and attach_rest is not None:
            obj[PROP_BONE] = attach_name
            obj[PROP_BONE_REST] = attach_rest

    # ----------------------------------------------------------- helpers
    def _add_sphere(self, translate, name):
        import bpy
        v = translate
        bpy.ops.mesh.primitive_uv_sphere_add(location=(-v[0], -v[2], v[1]), radius=0.05)
        bpy.context.active_object.name = name

    @staticmethod
    def _check_blender_version(bpy):
        if bpy.app.version < (4, 1, 0):
            raise RuntimeError(
                f"Blender 4.1.0 or later required, but found: {bpy.app.version_string}"
            )
        if bpy.app.version >= (5, 2, 0):
            print(
                f"Forza: Blender {bpy.app.version_string} is newer than the last "
                "actively tested series (4.1–5.1); import usually works."
            )

    # ----------------------------------------------------------- GameDB
    def _query_gamedb(self):
        o = self.o
        try:
            with closing(sqlite3.connect(pathlib.Path(o.db_path).as_uri() + "?mode=ro", uri=True)) as connection:
                cursor = connection.execute(
                    """
                SELECT Data_Car.MediaName, Data_Car.Id, List_UpgradeCarBody.CarBodyID,
                    List_UpgradeTireCompound.TireModelName, Data_Car.FrontTireWidthMM,
                    Data_Car.FrontTireAspect, Data_Car.FrontWheelDiameterIN, Data_Car.RearTireWidthMM,
                    Data_Car.RearTireAspect, Data_Car.RearWheelDiameterIN, Data_CarBody.ModelWheelbase,
                    Data_CarBody.ModelFrontTrackOuter, Data_CarBody.ModelRearTrackOuter,
                    Data_CarBody.ModelFrontStockRideHeight, Data_CarBody.ModelRearStockRideHeight,
                    Data_CarBody.BottomCenterWheelbasePosx, Data_CarBody.BottomCenterWheelbasePosy,
                    Data_CarBody.BottomCenterWheelbasePosZ
                FROM Data_Car
                    INNER JOIN List_UpgradeTireCompound ON List_UpgradeTireCompound.Ordinal = Data_Car.Id
                    INNER JOIN List_UpgradeCarBody ON List_UpgradeCarBody.Ordinal = Data_Car.Id
                    INNER JOIN Data_CarBody ON Data_CarBody.Id = List_UpgradeCarBody.CarBodyID
                WHERE MediaName LIKE ? AND List_UpgradeTireCompound.IsStock = 1
                ORDER BY List_UpgradeCarBody.CarBodyID
                """,
                    (o.media_name,),
                )
                rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            if e.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN:
                e.args = (f'The database file was not found. db_path = "{o.db_path}"',)
            elif e.sqlite_errorcode == sqlite3.SQLITE_CANTOPEN_ISDIR:
                e.args = (f'db_path is a folder, but a file was expected. db_path = "{o.db_path}"',)
            raise
        except sqlite3.DatabaseError as e:
            if e.sqlite_errorcode == sqlite3.SQLITE_NOTADB:
                e.args = (f'The database file is not SQLite3, it\'s probably encrypted. db_path = "{o.db_path}"',)
            raise

        if not rows:
            raise RuntimeError(f'The database file doesn\'t contain the requested MediaName '
                               f'"{o.media_name}", it\'s probably outdated. db_path = "{o.db_path}"')

        row = rows[0]
        if o.car_body_id is not None:
            for row in reversed(rows):
                if row[2] == o.car_body_id:
                    break

        self.media_name = row[0]
        o.media_name = row[0]
        o.TireModelName = row[3]
        o.FrontTireWidthMM = row[4]
        o.OriginalFrontTireAspect = row[5]
        o.OriginalFrontWheelDiameterIN = row[6]
        o.FrontWheelDiameterIN = row[6]
        o.RearTireWidthMM = row[7]
        o.OriginalRearTireAspect = row[8]
        o.OriginalRearWheelDiameterIN = row[9]
        o.RearWheelDiameterIN = row[9]
        o.ModelWheelbase = row[10]
        o.ModelFrontTrackOuter = row[11]
        o.ModelRearTrackOuter = row[12]
        o.ModelFrontStockRideHeight = row[13]
        o.ModelRearStockRideHeight = row[14]
        o.BottomCenterWheelbasePosX = row[15]
        o.BottomCenterWheelbasePosY = row[16]
        o.BottomCenterWheelbasePosZ = row[17]
