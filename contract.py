"""Shared contract between the importer (build layer) and the animation module.

Both sides must agree on (a) the custom-property keys written onto imported objects and
(b) the Forza -> Blender coordinate convention. Centralizing them here removes the silent
coupling where animation.py's C4 matrix had to be kept bit-identical to the importer's
hand-rolled vertex baking. Kept dependency-light (mathutils only imported on demand) so it
is importable outside Blender for tests/tooling.
"""

# --- Custom property keys (written by the importer, read by animation.py) ---
PROP_CAR_ROOT = "forza_car_root"     # on-disk car folder, for locating .gr2/.dae later
PROP_BONE = "forza_bone"             # bone name a mesh is attached to
PROP_BONE_REST = "forza_bone_rest"   # flat 16-float row-major rest matrix
PROP_ANIM_RIG = "forza_anim_rig"     # tag on the generated armature

# --- Forza Y-up (left-handed) -> Blender Z-up (right-handed). Row-major 4x4. ---
COORD_ROWS = (
    (-1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def coord_matrix():
    """COORD_ROWS as a mathutils.Matrix (Blender runtime only)."""
    from mathutils import Matrix
    return Matrix(COORD_ROWS)
