"""Importer: orchestrates a single car import (replaces the old core.py top-level block).

Pipeline: optional GameDB lookup -> parse the carbin (CarScene) -> load each referenced modelbin
(geometry + embedded materials) -> assemble wheel/tire/brake/control-arm transforms -> build
Blender meshes (geometry layer) and materials (data-driven materials layer) -> place objects into
the nested collection hierarchy. All option inputs come from a single ImportOptions.
"""

import hashlib
import os
import pathlib
import sqlite3
from contextlib import closing

from .parsing.binary import BinaryStream
from .parsing.paths import GamePathResolver, resolve_tire_model_name, tire_modelbin_game_path
from .parsing.carbin import CarScene, ParseContext, Part, SharedCarModel, Upgrade
from .geometry import Modelbin, build_mesh_object
from .materials.builder import MaterialBuilder
from .materials.material_table import material_instance_key
from .materials import nodes
from .materials.manufacturer_colors import (
    find_manufacturer_colors,
    load_manufacturer_colors_json,
    stock_paint_rgba,
)
from . import assembly
from .collections import CollectionWrapper
from .contract import PROP_BONE, PROP_BONE_REST, PROP_CAR_ROOT

CARS_INTERNAL = r"GAME:\Media\Cars"
TIRES_INTERNAL = r"GAME:\Media\Cars\_library\scene\tires"


class Importer:
    def __init__(self, options):
        self.o = options
        self.resolver = GamePathResolver(
            options.game_path, options.cars_dir_override,
            options.tires_dir_override, options.materials_dir_override)
        self.image_cache = {}        # texture guid -> bpy image
        self.built_materials = {}    # material name -> bpy material
        self.material_specs = {}     # material name -> MaterialSpec (or None)
        self.root_collection = None
        self._builder = MaterialBuilder()
        self.media_name = options.media_name
        self._load_stock_paint()

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

        ctx = ParseContext()
        ctx.series = o.series
        scene = CarScene()
        scene.deserialize(BinaryStream.from_path(carbin_path), ctx)
        self.scene = scene

        skeleton_modelbin = None
        if o.suspension_transform_type == 0:
            skeleton_modelbin = Modelbin()
            skeleton_modelbin.deserialize(BinaryStream.from_path(self.resolver.resolve(scene.skeleton_path)))
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
        elif scene.part_wheels is not None and not o.TireModelName:
            print("Warning: no TireModelName — rubber tires will not be synthesized.")

        self._load_models(scene, skeleton_modelbin)

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

        self._build_scene(scene)

        if self.root_collection is not None:
            self.root_collection.sort()
        else:
            print("Warning: no meshes were imported (check LOD/draw group filters and file paths).")

    # ----------------------------------------------------------- model loading
    def _load_models(self, scene, skeleton_modelbin):
        o = self.o
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
                    continue
                if model.draw_groups & o.requested_draw_group == 0:
                    continue
                if model.levels_of_detail & o.requested_level_of_detail == 0:
                    continue

                p = self.resolver.resolve(model.path)
                if not os.path.isfile(p) and o.TireModelName and "tire_" in model.path.lower():
                    alt = tire_modelbin_game_path(o.TireModelName)
                    p = self.resolver.resolve(alt)
                if not os.path.isfile(p):
                    print(f"Warning: skipping missing model file: {model.path} -> {p}")
                    continue
                model.modelbin = Modelbin()
                model.modelbin.deserialize(
                    BinaryStream.from_path(p),
                    requested_level_of_detail=o.requested_level_of_detail,
                    resolver=self.resolver,
                    parse_materials=o.use_materials)

                sphere_cb = (lambda t, n: self._add_sphere(t, n)) if o.create_spheres else None
                assembly.apply_part_assignment(scene, o, part, model, skeleton_modelbin, sphere_cb)

    # ----------------------------------------------------------- scene build
    def _build_scene(self, scene):
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

                for mesh in modelbin.meshes:
                    if mesh.levels_of_detail & o.requested_level_of_detail == 0:
                        continue
                    if mesh.render_pass & 0x10 == 0:  # skip Shadow
                        continue
                    if o.hide_decal_transparent_pass and mesh.render_pass & 0x4 != 0:
                        continue
                    if part.type == 44 and mesh.render_pass & 0x4 != 0:
                        continue

                    md = modelbin.process_mesh(mesh)
                    _, obj = build_mesh_object(md, quadrangulate=o.quadrangulate_mesh)
                    self._tag_bone(obj, modelbin, mesh)
                    if o.car_root_dir:
                        obj[PROP_CAR_ROOT] = o.car_root_dir
                    self._assign_material(obj, modelbin, mesh)
                    self._place_object(obj, part, model, upgrade_ids)

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
        import bpy
        mid = mesh.material_id
        if not (0 <= mid < len(modelbin.materials)):
            return
        pm = modelbin.materials[mid]
        name = material_instance_key(pm)
        if not name:
            return

        if self.o.use_materials and pm.obj is not None:
            spec = self.material_specs.get(name, "missing")
            if spec == "missing":
                try:
                    spec = self._builder.build(name, pm.obj)
                except Exception as e:
                    print(f"Note: material build failed for '{name}': {e!r}")
                    spec = None
                self.material_specs[name] = spec
            if spec is not None and spec.valid:
                mat = self.built_materials.get(name)
                graph_v = getattr(nodes, "MATERIAL_GRAPH_VERSION", 0)
                if mat is None or mat.get("forza_graph_v", 0) != graph_v:
                    try:
                        mat = nodes.build_material(spec, self.resolver, self.image_cache)
                        self.built_materials[name] = mat
                    except Exception as e:
                        print(f"Note: material nodes failed for '{name}': {e!r}")
                        mat = None
                if mat is not None:
                    obj.data.materials.append(mat)
                    return

        if self.o.create_placeholder_materials:
            mat = self.built_materials.get(name)
            if mat is None:
                mat = bpy.data.materials.new(name)
                self.built_materials[name] = mat
                mat.use_nodes = True
                h = hashlib.md5(name.encode("utf-8")).digest()
                mat.diffuse_color = (h[0] / 255.0, h[1] / 255.0, h[2] / 255.0, 1.0)
            obj.data.materials.append(mat)

    # ----------------------------------------------------------- bone tagging
    def _tag_bone(self, obj, modelbin, mesh):
        skeleton = getattr(modelbin, "skeleton", None)
        if skeleton is not None and 0 <= mesh.bone_index < len(skeleton.bones):
            bone = skeleton.bones[mesh.bone_index]
            obj[PROP_BONE] = bone.name
            t = bone.transform  # row-vector convention: world = v . t
            obj[PROP_BONE_REST] = [
                -t[0][0], -t[1][0], -t[2][0], -t[3][0],
                -t[0][2], -t[1][2], -t[2][2], -t[3][2],
                 t[0][1],  t[1][1],  t[2][1],  t[3][1],
                 t[0][3],  t[1][3],  t[2][3],  t[3][3],
            ]

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
