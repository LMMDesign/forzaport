"""Import operators, car-library scanning, GameDB selection, search popup and menus.

The car import now calls Importer(ImportOptions(...)).run() directly - no exec() of a core
script, and no generic/handler material fork (the materials layer is a single data-driven
pipeline). Split out of the old monolithic __init__.py.
"""

import glob
import os
import pathlib
import re
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator
from bpy_extras.io_utils import ImportHelper

from .parsing.paths import (
    find_media_root,
    find_tires_dir,
    GamePathResolver,
    media_has_car_library,
    resolve_import_game_key,
)
from . import animation
from .options import ImportOptions, LOD_ITEMS, DRAW_ITEMS, SUSP_ITEMS
from .importer import Importer
from .preferences import get_prefs
from .contract import PROP_CAR_ROOT

# Same joins the importer uses, so "found" means the car is actually usable by it.
_DB_QUERY = """
SELECT Data_Car.MediaName
FROM Data_Car
    INNER JOIN List_UpgradeTireCompound ON List_UpgradeTireCompound.Ordinal = Data_Car.Id
    INNER JOIN List_UpgradeCarBody ON List_UpgradeCarBody.Ordinal = Data_Car.Id
    INNER JOIN Data_CarBody ON Data_CarBody.Id = List_UpgradeCarBody.CarBodyID
WHERE MediaName LIKE ?
    AND List_UpgradeTireCompound.IsStock = 1
"""


# ---------------------------------------------------------------------------
# Path / library helpers
# ---------------------------------------------------------------------------

def _resolve_paths(filepath):
    """Return (game_path, media_name, cars_dir_override, car_zip_path) for .carbin or .zip."""
    p = Path(filepath)
    media_name = p.stem
    car_zip = str(p.resolve()) if p.suffix.lower() == ".zip" else None
    parts = p.parts
    lower = [part.lower() for part in parts]
    # Xbox Media: .../Content/media/cars/NAME.zip  or extracted .../cars/NAME/NAME.carbin
    for i in range(len(parts) - 1):
        if lower[i] == "media" and lower[i + 1] == "cars":
            game_path = str(Path(*parts[: i + 1]))  # .../media
            cars_override = str(p.parent) if p.suffix.lower() == ".carbin" else None
            return game_path, media_name, cars_override, car_zip
    for i, part in enumerate(lower):
        if part == "content" and i + 1 < len(lower) and lower[i + 1] == "media":
            game_path = str(Path(*parts[: i + 2]))  # .../Content/media
            cars_override = str(p.parent) if p.suffix.lower() == ".carbin" else None
            return game_path, media_name, cars_override, car_zip
    # Loose zip / extracted car outside Media — register the zip; prefer a Media root.
    car_folder = os.path.dirname(os.path.abspath(filepath))
    root = os.path.dirname(car_folder) if p.suffix.lower() == ".carbin" else car_folder
    media = find_media_root(root) or find_media_root(car_folder)
    if media is None:
        cur = Path(car_folder)
        for _ in range(4):
            media = find_media_root(str(cur))
            if media:
                break
            if cur.parent == cur:
                break
            cur = cur.parent
    game_path = media or root
    cars_override = car_folder if p.suffix.lower() == ".carbin" else None
    return game_path, media_name, cars_override, car_zip


def _car_entry(label, name, cars_dir):
    """Prefer extracted carbin; fall back to Media car .zip (resolved at import)."""
    carbin = os.path.join(cars_dir, name, name + ".carbin")
    if os.path.isfile(carbin):
        return (label, name, carbin)
    zpath = os.path.join(cars_dir, name + ".zip")
    if os.path.isfile(zpath):
        return (label, name, zpath)
    return None


def _collect_cars(d):
    """Return car entries for a directory, or None if it's not a car directory."""
    label = os.path.basename(os.path.normpath(d))
    for cars_dir in (
        os.path.join(d, "Media", "Cars"),
        os.path.join(d, "media", "cars"),
        os.path.join(d, "Content", "media", "cars"),
        os.path.join(d, "cars"),
    ):
        if not os.path.isdir(cars_dir):
            continue
        out = []
        try:
            entries = sorted(os.listdir(cars_dir))
        except OSError:
            entries = []
        for name in entries:
            if name.startswith("_"):
                continue
            stem = name[:-4] if name.lower().endswith(".zip") else name
            if name.lower().endswith(".zip"):
                ent = _car_entry(label, stem, cars_dir)
            else:
                ent = _car_entry(label, name, cars_dir)
            if ent:
                out.append(ent)
        if out:
            return out

    raw = []
    try:
        entries = sorted(os.listdir(d))
    except OSError:
        entries = []
    for name in entries:
        if name.lower().endswith(".zip"):
            ent = _car_entry(label, name[:-4], d)
            if ent:
                raw.append(ent)
            continue
        carbin = os.path.join(d, name, name + ".carbin")
        if os.path.isfile(carbin):
            raw.append((label, name, carbin))
    return raw if raw else None


def _scan_car_library():
    """Return a sorted, de-duplicated list of (label, media_name, carbin_path)."""
    prefs = get_prefs()
    results = []
    seen = set()
    roots = [bpy.path.abspath(item.path) for item in prefs.library_roots] if prefs else []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        found = _collect_cars(root)
        if found is None:
            try:
                subs = sorted(os.listdir(root))
            except OSError:
                subs = []
            for sub in subs:
                p = os.path.join(root, sub)
                if os.path.isdir(p):
                    child = _collect_cars(p)
                    if child:
                        found = (found or []) + child
        if not found:
            continue
        for label, name, carbin in found:
            key = os.path.normcase(os.path.abspath(carbin))
            if key not in seen:
                seen.add(key)
                results.append((label, name, carbin))
    results.sort(key=lambda r: (r[0].lower(), r[1].lower()))
    return results


def _list_gamedbs(game_path, extra_dirs=()):
    dirs = [
        os.path.join(game_path, "Media", "Stripped"),
        os.path.join(game_path, "Media", "db"),
        game_path,
        os.path.dirname(game_path),
    ]
    dirs.extend(extra_dirs)
    seen = set()
    files = []
    for d in dirs:
        try:
            hits = glob.glob(os.path.join(d, "*.slt"))
        except OSError:
            hits = []
        for f in hits:
            key = os.path.normcase(os.path.abspath(f))
            if key not in seen and os.path.isfile(f):
                seen.add(key)
                files.append(f)
    return files


def _version_key(filename):
    m = re.search(r"v(\d+(?:\.\d+)+)", os.path.basename(filename))
    if not m:
        return (0,)
    return tuple(int(x) for x in m.group(1).split("."))


def _car_in_db(db_path, media_name):
    try:
        uri = pathlib.Path(db_path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as con:
            rows = con.execute(_DB_QUERY, (media_name,)).fetchall()
        return (len(rows) > 0, None)
    except sqlite3.DatabaseError as e:
        if getattr(e, "sqlite_errorcode", None) == sqlite3.SQLITE_NOTADB:
            return (False, "the file is not a valid SQLite DB (still encrypted?)")
        return (False, str(e))
    except OSError as e:
        return (False, str(e))


def _select_gamedb(game_path, media_name, explicit_db, extra_dirs=()):
    if explicit_db:
        candidates = [explicit_db]
    else:
        candidates = _list_gamedbs(game_path, extra_dirs)
        token = os.path.basename(os.path.normpath(game_path)).lower()
        candidates.sort(key=lambda f: (
            0 if token and token in os.path.basename(f).lower() else 1,
            tuple(-x for x in _version_key(f)),
        ))
    readable = 0
    last_err = None
    for db in candidates:
        found, err = _car_in_db(db, media_name)
        if err:
            last_err = err
            continue
        readable += 1
        if found:
            return (db, readable, None)
    return (None, readable, last_err)


def _configured_game_media(*, filepath=None, game_path=None, car_root=None):
    """Return the explicit per-game Media root selected in addon preferences."""
    prefs = get_prefs()
    game_key = resolve_import_game_key(
        filepath=filepath, game_path=game_path, car_root=car_root
    )
    if not prefs:
        return None
    accepted = {game_key} if game_key != "unknown" else {"other"}
    for item in prefs.game_roots:
        if item.game not in accepted or not item.path:
            continue
        media = find_media_root(bpy.path.abspath(item.path))
        if media:
            return media
    return None


def _resolve_tires_dir(game_media):
    """Derive tires only from the selected game's Media tree."""
    return find_tires_dir(game_media)


# ---------------------------------------------------------------------------
# Import core (calls the new Importer directly)
# ---------------------------------------------------------------------------

def _resolve_car_root(filepath, game_path, media_name, car_zip_path=None):
    """On-disk folder used to locate Animations / skeleton next to the car.

    Xbox Media cars ship as .zip — extract the carbin via ZipAssetStore and use that cache
    folder so Animations\\*.gr2 (FH5) or Scene\\animations\\Mojo\\... (FH6) are visible.
    """
    parent = os.path.dirname(os.path.abspath(filepath))
    has_scene = os.path.isdir(os.path.join(parent, "Scene")) or os.path.isdir(
        os.path.join(parent, "scene")
    )
    zip_path = car_zip_path or (filepath if filepath.lower().endswith(".zip") else None)
    if zip_path or not has_scene:
        media = find_media_root(game_path) or game_path
        resolver = GamePathResolver(media, car_zip_path=zip_path)
        if zip_path and resolver._zipfs is not None:
            resolver._zipfs.register_car_zip(zip_path, media_name)
        carbin = resolver.resolve(fr"GAME:\Media\Cars\{media_name}\{media_name}.carbin")
        if carbin and os.path.isfile(carbin):
            root = os.path.dirname(carbin)
            if resolver._zipfs is not None:
                resolver._zipfs.build()
                prefix = f"media\\cars\\{media_name.lower()}\\"
                for key in list(resolver._zipfs._index):
                    if not key.startswith(prefix):
                        continue
                    if (
                        "animation" in key
                        or key.endswith((".gr2", ".clipd", ".skeld"))
                    ):
                        resolver._zipfs.resolve_to_cache("GAME:\\" + key)
            return root
    return parent


def _resolve_game_media(filepath, source_path, car_root=None):
    """Use source Media when complete, otherwise the explicit matching game setting."""
    source_media = find_media_root(source_path)
    if media_has_car_library(source_media):
        return source_media
    configured = _configured_game_media(
        filepath=filepath, game_path=source_path, car_root=car_root
    )
    if configured:
        print(f"Forza: using configured game Media: {configured}")
        return configured
    return None


def _import_carbin(filepath, *, use_db, db_path, level_of_detail, draw_group,
                   suspension_transform_type, use_materials, quadrangulate_mesh,
                   hide_decal_transparent_pass, create_placeholder_materials=False):
    game_path, media_name, cars_override, car_zip = _resolve_paths(filepath)
    car_root = _resolve_car_root(filepath, game_path, media_name, car_zip_path=car_zip)
    # Shared assets always come from one explicit game Media root.
    media_path = _resolve_game_media(filepath, game_path, car_root=car_root)
    if use_materials and not media_path:
        game_key = resolve_import_game_key(
            filepath=filepath, game_path=game_path, car_root=car_root
        )
        raise RuntimeError(
            f"No game install folder is configured for {game_key.upper()}. "
            "Set it under Preferences > Add-ons > Import Forza Car > Game Installations. "
            "Choose the game install folder (the one that contains Content). "
            "Content or Content\\media also work — the folder must resolve to Content\\media\\cars."
        )
    if media_path and os.path.normcase(media_path) != os.path.normcase(game_path):
        # Keep resolving this car from its own folder/zip when Media is elsewhere.
        if cars_override is None and not car_zip:
            car_folder = os.path.dirname(os.path.abspath(filepath))
            if filepath.lower().endswith(".carbin"):
                cars_override = car_folder
            elif os.path.isdir(car_folder):
                cars_override = car_folder
        game_path = media_path
    o = ImportOptions(
        game_path=game_path,
        media_name=media_name,
        cars_dir_override=cars_override,
        car_zip_path=car_zip,
        car_root_dir=car_root,
        tires_dir_override=_resolve_tires_dir(game_path),
        materials_dir_override=None,
        db_path=db_path or "",
        use_db=use_db,
        car_body_id=None,
        requested_level_of_detail=int(level_of_detail),
        requested_draw_group=int(draw_group),
        suspension_transform_type=int(suspension_transform_type),
        hide_decal_transparent_pass=hide_decal_transparent_pass,
        suspension_only=False,
        create_spheres=False,
        use_materials=use_materials,
        create_placeholder_materials=create_placeholder_materials,
        quadrangulate_mesh=quadrangulate_mesh,
        series=0,
    )
    Importer(o).run()
    return game_path, media_name


def _build_animations_for(op, new_objects):
    cars = [o for o in new_objects if o.get(PROP_CAR_ROOT)]
    if not cars:
        op.report({"WARNING"}, "Import Animations was enabled, but no car objects were tagged for rigging.")
        return
    view_objs = bpy.context.view_layer.objects
    for o in view_objs:
        o.select_set(False)
    for o in cars:
        if o.name in view_objs:
            o.select_set(True)
    view_objs.active = cars[0]
    try:
        bpy.ops.import_scene.forza_animations("EXEC_DEFAULT")
    except RuntimeError as exc:
        op.report({"WARNING"}, f"Mesh imported, but the animation step failed: {exc}")


def _guarded_import(op, filepath, *, use_db, db_path, level_of_detail, draw_group,
                    suspension_transform_type, use_materials, quadrangulate_mesh,
                    hide_decal_transparent_pass, create_placeholder_materials=False,
                    import_animations=False):
    game_path, media_name, _, car_zip = _resolve_paths(filepath)
    if not game_path:
        op.report({"ERROR"}, "Could not determine the car folder from the .carbin/.zip path.")
        return {"CANCELLED"}
    if filepath.lower().endswith(".zip") and not (car_zip and os.path.isfile(car_zip)):
        op.report({"ERROR"}, f"Car zip not found: {filepath}")
        return {"CANCELLED"}

    prefs = get_prefs()
    extra_dirs = []
    if prefs and prefs.gamedb_dir:
        extra_dirs.append(bpy.path.abspath(prefs.gamedb_dir))

    resolved_db = ""
    if use_db:
        explicit = bpy.path.abspath(db_path) if db_path else None
        if explicit and not os.path.isfile(explicit):
            op.report({"ERROR"}, f"GameDB Path does not exist: {explicit}")
            return {"CANCELLED"}

        # GameDB must be a decrypted SQLite .slt. The live game Media tree only has
        # encrypted gamedbRC.slt — do not search Game Installations for it.
        # Prefer Preferences > GameDB Folder / Path; also scan near the car folder.
        resolved_db, readable, err = _select_gamedb(game_path, media_name, explicit, extra_dirs)
        if not resolved_db:
            if readable == 0:
                detail = f" Last error: {err}" if err else ""
                op.report({"ERROR"},
                          "Use GameDB is enabled but no readable (decrypted) GameDB .slt was found. "
                          "Set Preferences > GameDB Folder / Path, or disable Use GameDB."
                          + detail)
                return {"CANCELLED"}
            bpy.ops.import_scene.forza_carbin_db_fallback(
                "INVOKE_DEFAULT",
                filepath=filepath,
                media_name=media_name,
                searched=readable,
                level_of_detail=str(level_of_detail),
                draw_group=str(draw_group),
                use_materials=use_materials,
                quadrangulate_mesh=quadrangulate_mesh,
                hide_decal_transparent_pass=hide_decal_transparent_pass,
                create_placeholder_materials=create_placeholder_materials,
                import_animations=import_animations,
            )
            return {"FINISHED"}
        print(f"Forza: using GameDB '{resolved_db}' for {media_name}")

    before = set(bpy.data.objects)
    try:
        _import_carbin(
            filepath,
            use_db=use_db,
            db_path=resolved_db,
            level_of_detail=level_of_detail,
            draw_group=draw_group,
            suspension_transform_type=suspension_transform_type,
            use_materials=use_materials,
            quadrangulate_mesh=quadrangulate_mesh,
            hide_decal_transparent_pass=hide_decal_transparent_pass,
            create_placeholder_materials=create_placeholder_materials,
        )
    except Exception as exc:
        traceback.print_exc()
        op.report({"ERROR"}, f"Forza import failed: {exc}")
        return {"CANCELLED"}

    op.report({"INFO"}, f"Imported Forza car: {media_name}")
    if import_animations:
        _build_animations_for(op, [o for o in bpy.data.objects if o not in before])
    return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class IMPORT_SCENE_OT_forza_carbin(Operator, ImportHelper):
    """Browse to a car .zip from the game (or an extracted .carbin) and import it"""

    bl_idname = "import_scene.forza_carbin"
    bl_label = "Import Forza Car"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".carbin"
    filter_glob: StringProperty(default="*.carbin;*.zip", options={"HIDDEN"})

    level_of_detail: EnumProperty(name="Level of Detail", items=LOD_ITEMS, default="1")
    draw_group: EnumProperty(name="Draw Group", items=DRAW_ITEMS, default="1")
    suspension_transform_type: EnumProperty(name="Wheel Positioning", items=SUSP_ITEMS, default="2")
    use_db: BoolProperty(
        name="Use GameDB",
        description="Auto-detect a decrypted GameDB containing this car for accurate scaling",
        default=True,
    )
    db_path: StringProperty(
        name="GameDB Path",
        description="Force a specific decrypted GameDB .slt. Leave empty to auto-detect "
                    "from Preferences > GameDB Folder (or near the car)",
        subtype="FILE_PATH",
        default="",
    )
    use_materials: BoolProperty(name="Import Materials", default=True)
    create_placeholder_materials: BoolProperty(
        name="Placeholder Materials",
        description="Deprecated: unresolved materials always receive the shared "
                    "FORZAPORT_UNRESOLVED_MATERIAL diagnostic slot (never left empty)",
        default=False,
    )
    quadrangulate_mesh: BoolProperty(name="Quadrangulate", default=False)
    hide_decal_transparent_pass: BoolProperty(name="Hide Transparent Decals", default=False)
    import_animations: BoolProperty(
        name="Import Animations",
        description="After importing the mesh, build the rig and bake the car's part animations "
                    "(doors, hood, windows, wipers, …). FH5: bundled gr2dump (.NET 8); FH6: Mojo .clipd",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Pick a car .zip from the game, or an extracted .carbin.", icon="INFO")
        box.label(text="Or add your cars folder in Preferences for the Import menu.")
        layout.prop(self, "level_of_detail")
        layout.prop(self, "draw_group")
        layout.prop(self, "suspension_transform_type")
        col = layout.column()
        col.prop(self, "use_db")
        sub = col.column()
        sub.enabled = self.use_db
        sub.prop(self, "db_path")
        layout.prop(self, "use_materials")
        layout.prop(self, "create_placeholder_materials")
        layout.prop(self, "quadrangulate_mesh")
        layout.prop(self, "hide_decal_transparent_pass")
        layout.prop(self, "import_animations")

    def execute(self, context):
        return _guarded_import(
            self, self.filepath,
            use_db=self.use_db,
            db_path=self.db_path,
            level_of_detail=self.level_of_detail,
            draw_group=self.draw_group,
            suspension_transform_type=self.suspension_transform_type,
            use_materials=self.use_materials,
            quadrangulate_mesh=self.quadrangulate_mesh,
            hide_decal_transparent_pass=self.hide_decal_transparent_pass,
            create_placeholder_materials=self.create_placeholder_materials,
            import_animations=self.import_animations,
        )


class IMPORT_SCENE_OT_forza_carbin_quick(Operator):
    """Import this Forza car using the default options from the addon preferences"""

    bl_idname = "import_scene.forza_carbin_quick"
    bl_label = "Import Forza Car"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}

    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})

    def execute(self, context):
        prefs = get_prefs()
        if prefs is None:
            self.report({"ERROR"}, "Addon preferences unavailable.")
            return {"CANCELLED"}
        self.report({"INFO"}, "Importing Forza car… (status bar progress; System Console for DXIL details)")
        return _guarded_import(
            self, self.filepath,
            use_db=prefs.use_db,
            db_path=prefs.db_path,
            level_of_detail=prefs.level_of_detail,
            draw_group=prefs.draw_group,
            suspension_transform_type=prefs.suspension_transform_type,
            use_materials=prefs.use_materials,
            quadrangulate_mesh=prefs.quadrangulate_mesh,
            hide_decal_transparent_pass=prefs.hide_decal_transparent_pass,
            create_placeholder_materials=prefs.create_placeholder_materials,
            import_animations=prefs.import_animations,
        )


class IMPORT_SCENE_OT_forza_carbin_db_fallback(Operator):
    """Car not found in GameDB - confirm importing with approximate wheel sizes"""

    bl_idname = "import_scene.forza_carbin_db_fallback"
    bl_label = "Car not in GameDB"
    bl_options = {"REGISTER", "INTERNAL"}

    filepath: StringProperty(subtype="FILE_PATH", options={"HIDDEN"})
    media_name: StringProperty(options={"HIDDEN"})
    searched: IntProperty(default=0, options={"HIDDEN"})
    level_of_detail: StringProperty(default="1", options={"HIDDEN"})
    draw_group: StringProperty(default="1", options={"HIDDEN"})
    use_materials: BoolProperty(default=True, options={"HIDDEN"})
    create_placeholder_materials: BoolProperty(default=False, options={"HIDDEN"})
    quadrangulate_mesh: BoolProperty(default=False, options={"HIDDEN"})
    hide_decal_transparent_pass: BoolProperty(default=False, options={"HIDDEN"})
    import_animations: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        where = f"any of the {self.searched} GameDB(s) found" if self.searched else "the GameDB"
        col.label(text=f"'{self.media_name}' was not found in {where}.", icon="ERROR")
        col.label(text="The car may belong to a different game/version than your DBs.")
        col.separator()
        col.label(text="Import anyway with approximate wheel/tire dimensions?")
        col.label(text="(Body, interior and brakes are unaffected; wheel positions")
        col.label(text="come from the carbin. Rim/tire scaling may be slightly off.)")

    def execute(self, context):
        before = set(bpy.data.objects)
        try:
            _import_carbin(
                self.filepath,
                use_db=False,
                db_path="",
                level_of_detail=self.level_of_detail,
                draw_group=self.draw_group,
                suspension_transform_type=1,   # carbin positions (correct without DB)
                use_materials=self.use_materials,
                quadrangulate_mesh=self.quadrangulate_mesh,
                hide_decal_transparent_pass=self.hide_decal_transparent_pass,
                create_placeholder_materials=self.create_placeholder_materials,
            )
        except Exception as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"Forza import failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Imported (approximate wheels): {self.media_name}")
        if self.import_animations:
            _build_animations_for(self, [o for o in bpy.data.objects if o not in before])
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Car list: search + manufacturer grouping
# ---------------------------------------------------------------------------

_MANUFACTURER_NAMES = {
    "ALF": "Alfa Romeo", "AST": "Aston Martin", "AUD": "Audi", "BEN": "Bentley",
    "BMW": "BMW", "BUG": "Bugatti", "CAD": "Cadillac", "CHE": "Chevrolet",
    "DOD": "Dodge", "FER": "Ferrari", "FOR": "Ford", "HON": "Honda", "HYU": "Hyundai",
    "JAG": "Jaguar", "JEE": "Jeep", "KOE": "Koenigsegg", "LAM": "Lamborghini",
    "LEX": "Lexus", "LOT": "Lotus", "MAS": "Maserati", "MAZ": "Mazda", "MCL": "McLaren",
    "MER": "Mercedes-Benz", "MIN": "Mini", "MIT": "Mitsubishi", "NIS": "Nissan",
    "PAG": "Pagani", "POR": "Porsche", "SUB": "Subaru", "TOY": "Toyota",
    "VOL": "Volvo", "VW": "Volkswagen",
}


def _manufacturer_of(media_name):
    code = media_name.split("_", 1)[0]
    return code.upper() if code else "?"


def _manufacturer_label(media_name):
    code = _manufacturer_of(media_name)
    return _MANUFACTURER_NAMES.get(code, code)


_search_enum_items = []
_search_cache = []


def _car_search_items(self, context):
    global _search_enum_items, _search_cache
    _search_cache = _scan_car_library()
    multi_game = len({c[0] for c in _search_cache}) > 1
    items = []
    for i, (label, name, carbin) in enumerate(_search_cache):
        display = f"{name}  [{label}]" if multi_game else name
        items.append((str(i), display, carbin))
    if not items:
        items = [("", "No cars found - add a library folder in Preferences", "")]
    _search_enum_items = items
    return _search_enum_items


class IMPORT_SCENE_OT_forza_carbin_search(Operator):
    """Search your Forza car library and import the chosen car"""

    bl_idname = "import_scene.forza_carbin_search"
    bl_label = "Search Forza Car"
    bl_options = {"REGISTER", "UNDO", "INTERNAL"}
    bl_property = "car"

    car: EnumProperty(name="Car", items=_car_search_items)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        prefs = get_prefs()
        if prefs is None:
            self.report({"ERROR"}, "Addon preferences unavailable.")
            return {"CANCELLED"}
        if not self.car:
            return {"CANCELLED"}
        try:
            idx = int(self.car)
        except ValueError:
            return {"CANCELLED"}
        if idx < 0 or idx >= len(_search_cache):
            self.report({"ERROR"}, "Car selection is stale; reopen the search.")
            return {"CANCELLED"}
        carbin = _search_cache[idx][2]
        return _guarded_import(
            self, carbin,
            use_db=prefs.use_db,
            db_path=prefs.db_path,
            level_of_detail=prefs.level_of_detail,
            draw_group=prefs.draw_group,
            suspension_transform_type=prefs.suspension_transform_type,
            use_materials=prefs.use_materials,
            quadrangulate_mesh=prefs.quadrangulate_mesh,
            hide_decal_transparent_pass=prefs.hide_decal_transparent_pass,
            create_placeholder_materials=prefs.create_placeholder_materials,
            import_animations=prefs.import_animations,
        )


# ---------------------------------------------------------------------------
# Menus
# ---------------------------------------------------------------------------

class IMPORT_MT_forza_cars(Menu):
    bl_idname = "IMPORT_MT_forza_cars"
    bl_label = "Forza Car"

    def draw(self, context):
        layout = self.layout
        layout.operator("import_scene.forza_carbin_search", text="Search...", icon="VIEWZOOM")
        cars = _scan_car_library()
        if not cars:
            layout.separator()
            layout.label(text="No cars found", icon="INFO")
            layout.label(text="Copy game car .zips into a folder you own,")
            layout.label(text="then add that folder in Preferences > Add-ons")
            return
        multi_game = len({c[0] for c in cars}) > 1
        groups = {}
        for label, name, carbin in cars:
            groups.setdefault(_manufacturer_label(name), []).append((label, name, carbin))
        col = layout.column()
        for mfr in sorted(groups):
            col.separator()
            col.label(text=mfr)
            for label, name, carbin in groups[mfr]:
                text = f"{name}  [{label}]" if multi_game else name
                col.operator("import_scene.forza_carbin_quick", text=text).filepath = carbin


def menu_func_import(self, context):
    self.layout.menu(IMPORT_MT_forza_cars.bl_idname, text="Forza Car")
    self.layout.operator(IMPORT_SCENE_OT_forza_carbin.bl_idname, text="Forza Car (.carbin/.zip)...")
    self.layout.operator(
        animation.IMPORT_SCENE_OT_forza_animations.bl_idname,
        text="Forza Car Animations...",
    )


classes = (
    IMPORT_SCENE_OT_forza_carbin,
    IMPORT_SCENE_OT_forza_carbin_quick,
    IMPORT_SCENE_OT_forza_carbin_search,
    IMPORT_SCENE_OT_forza_carbin_db_fallback,
    IMPORT_MT_forza_cars,
)
