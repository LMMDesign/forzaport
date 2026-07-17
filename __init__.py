bl_info = {
    "name": "Import Forza Car (.carbin)",
    "author": "Based on Doliman100 ForzaTech importers; FH5/FH6 Blender addon",
    "version": (2, 25, 0),
    "blender": (4, 1, 0),
    "location": "File > Import > Forza Car (.carbin/.zip)",
    "description": "Import ForzaTech carbins (Forza Horizon / Motorsport). "
                   "Copy car .zips from the game into your own folder (zipped or extracted), "
                   "then point the addon at that folder.",
    "doc_url": "https://github.com/LMMDesign/forzaport",
    "tracker_url": "https://github.com/LMMDesign/forzaport/issues",
    "support": "COMMUNITY",
    "category": "Import-Export",
}

import sys

import bpy

from . import animation
from .development import development_enabled
from . import preferences
from . import operators


def _reload_submodules():
    """Reload parse/bake modules so File>Reload Scripts picks up Mojo fixes."""
    import importlib
    from . import parsing
    from .parsing import mojo_clipd, mojo_skeld
    from .materials import builder, material_table, nodes
    from . import importer

    importlib.reload(material_table)
    importlib.reload(builder)
    importlib.reload(nodes)
    importlib.reload(mojo_skeld)
    importlib.reload(mojo_clipd)
    try:
        from .parsing import gr2_anim
        importlib.reload(gr2_anim)
    except Exception:
        pass
    importlib.reload(importer)
    importlib.reload(operators)
    importlib.reload(preferences)
    importlib.reload(animation)


def register():
    # Hot reload is a workspace convenience, not production addon behavior.
    if development_enabled() and "io_import_forza_carbin.animation" in sys.modules:
        try:
            _reload_submodules()
        except Exception as exc:  # noqa: BLE001 — never block enable
            print(f"Forza addon reload warning: {exc}")

    for cls in preferences.classes:
        bpy.utils.register_class(cls)
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(animation.IMPORT_SCENE_OT_forza_animations)
    bpy.types.TOPBAR_MT_file_import.append(operators.menu_func_import)
    animation.register_handlers()
    # Restore library / path prefs after (re)register — AddonPreferences alone
    # reset when the addon is disabled or scripts are reloaded.
    preferences.load_user_settings()


def unregister():
    animation.unregister_handlers()
    bpy.types.TOPBAR_MT_file_import.remove(operators.menu_func_import)
    bpy.utils.unregister_class(animation.IMPORT_SCENE_OT_forza_animations)
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(preferences.classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
