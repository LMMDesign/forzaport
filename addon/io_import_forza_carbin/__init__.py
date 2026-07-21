bl_info = {
    "name": "Import Forza Car (.carbin)",
    "author": "Based on Doliman100 ForzaTech importers; FH5/FH6 Blender addon",
    "version": (3, 2, 1),
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
    from .parsing import mojo_clipd, mojo_skeld, texture
    from .materials import (
        builder,
        capabilities,
        diagnose,
        diagnostic_material,
        diagnostics,
        instance_key,
        name_hashes,
        nodes,
        nodes_v3,
        pipeline_v3,
        report_store,
        report_ui,
        shader_bindings,
        translate,
        txmp_semantics,
    )
    from . import importer

    importlib.reload(texture)
    importlib.reload(name_hashes)
    importlib.reload(txmp_semantics)
    importlib.reload(shader_bindings)
    importlib.reload(instance_key)
    importlib.reload(diagnostics)
    importlib.reload(capabilities)
    importlib.reload(pipeline_v3)
    importlib.reload(diagnose)
    importlib.reload(diagnostic_material)
    importlib.reload(report_store)
    importlib.reload(report_ui)
    importlib.reload(translate)
    importlib.reload(nodes_v3)
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
    from .materials import report_ui

    report_ui.register()
    bpy.types.TOPBAR_MT_file_import.append(operators.menu_func_import)
    animation.register_handlers()
    # Restore library / path prefs after (re)register — AddonPreferences alone
    # reset when the addon is disabled or scripts are reloaded.
    preferences.load_user_settings()


def unregister():
    animation.unregister_handlers()
    bpy.types.TOPBAR_MT_file_import.remove(operators.menu_func_import)
    from .materials import report_ui

    report_ui.unregister()
    bpy.utils.unregister_class(animation.IMPORT_SCENE_OT_forza_animations)
    for cls in reversed(operators.classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(preferences.classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
