bl_info = {
    "name": "Import Forza Car (.carbin)",
    "author": "Based on Doliman100 ForzaTech importers; FH5/FH6 Blender addon",
    "version": (2, 2, 1),
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

import os

import bpy

from . import animation
from . import preferences
from . import operators


def register():
    # Dev-only hot-reload when FORZA_ADDON_DEV=1 (keeps production installs stable).
    if os.environ.get("FORZA_ADDON_DEV") == "1":
        import importlib
        from .materials import builder, material_table, nodes
        from . import importer
        importlib.reload(material_table)
        importlib.reload(builder)
        importlib.reload(nodes)
        importlib.reload(importer)
        importlib.reload(operators)

    for cls in preferences.classes:
        bpy.utils.register_class(cls)
    for cls in operators.classes:
        bpy.utils.register_class(cls)
    bpy.utils.register_class(animation.IMPORT_SCENE_OT_forza_animations)
    bpy.types.TOPBAR_MT_file_import.append(operators.menu_func_import)
    animation.register_handlers()


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
