"""Shared contract between the importer (build layer) and the animation module.

Both sides must agree on (a) the custom-property keys written onto imported objects and
(b) the Forza -> Blender coordinate convention. Centralizing them here removes the silent
coupling where animation.py's C4 matrix had to be kept bit-identical to the importer's
hand-rolled vertex baking. Kept dependency-light (mathutils only imported on demand) so it
is importable outside Blender for tests/tooling.
"""

# --- Custom property keys (written by the importer, read by animation.py) ---
PROP_CAR_ROOT = "forza_car_root"     # on-disk car folder, for locating .gr2/.dae later
PROP_BONE = "forza_bone"             # effective attach bone (carbin > rigid); Child Of target
PROP_BONE_REST = "forza_bone_rest"   # flat 16-float row-major rest for PROP_BONE
PROP_ANIM_RIG = "forza_anim_rig"     # tag on the generated armature
# Authored bind metadata (carbin + modelbin — source of truth for attachment)
PROP_RIGID_BONE = "forza_rigid_bone"           # Mesh.RigidBoneIndex → modelbin skeleton
PROP_CARBIN_BONE = "forza_carbin_bone"         # CarRenderModel.bone_name
PROP_CARBIN_BONE_INDEX = "forza_carbin_bone_index"  # CarRenderModel.bone_index (int)
PROP_MODEL_PATH = "forza_model_path"           # CarRenderModel.path (modelbin path)
PROP_MESH_NAME = "forza_mesh_name"             # modelbin mesh name (e.g. mirrorleft)

# Material import diagnostics (per-car report + unresolved slot tagging)
PROP_MATERIAL_REPORT_TEXT = "forza_material_report_text"  # bpy.data.texts name
PROP_MATERIAL_DIAG_KEY = "forza_material_diag_key"        # instance key on mesh object
PROP_MATERIAL_DIAG_STATUS = "forza_material_diag_status"

# --- Forza Y-up (left-handed) -> Blender Z-up (right-handed). Row-major 4x4. ---
COORD_ROWS = (
    (-1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)
