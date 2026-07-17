"""Skeld mirror-bone attachment for root-rigid wing mirror housings.

Side markers in ``wingmirror*_a.modelbin`` already bind ``boneMirrorL_001`` /
``boneMirrorR_001`` via ``RigidBoneIndex``. Main housings are often index 0
(``<root>``) while the game inherits motion from the door-mounted mirror bones
in ``.skeld``. Resolve effective attach from model path + modelbin mesh name —
not Blender object names.
"""
from __future__ import annotations

from ..assembly import is_root_carbin_bone


def is_root_rigid_bone(name: str | None) -> bool:
    if not name or not str(name).strip():
        return True
    n = str(name).strip().lower()
    return n in ("root", "<root>")


def is_wing_mirror_model_path(path: str | None) -> bool:
    if not path:
        return False
    return "wingmirror" in path.replace("\\", "/").lower()


def mesh_mirror_side(mesh_name: str | None) -> str | None:
    """``l`` / ``r`` from modelbin mesh name (handles shared ``wingmirrorl_a`` bins)."""
    if not mesh_name:
        return None
    n = mesh_name.lower()
    if "mirrorc" in n:
        return None
    if n.startswith("wingmirrorl") or n.startswith("glasswingmirrorl"):
        return "l"
    if n.startswith("wingmirrorr") or n.startswith("glasswingmirrorr"):
        return "r"
    if "wingmirrorsidemarkerl" in n:
        return "l"
    if "wingmirrorsidemarkerr" in n:
        return "r"
    return None


def model_path_mirror_side(model_path: str | None) -> str | None:
    if not model_path:
        return None
    p = model_path.replace("\\", "/").lower()
    if "wingmirrorl" in p:
        return "l"
    if "wingmirrorr" in p:
        return "r"
    return None


def resolve_mirror_bone_name(part_skeleton, scene_skeleton, side: str) -> str | None:
    """Prefer ``boneMirror{L|R}_001`` on part skel, then scene skel."""
    side_l = side.lower()
    token = f"bonemirror{side_l}"

    def _pick(skeleton) -> str | None:
        if skeleton is None:
            return None
        hits: list[str] = []
        for bone in getattr(skeleton, "bones", None) or []:
            name = getattr(bone, "name", None) or ""
            low = name.lower()
            if token in low and "mirrorc" not in low:
                hits.append(name)
        if not hits:
            return None
        hits.sort(key=lambda n: (0 if n.endswith("_001") else 1, n.lower()))
        return hits[0]

    return _pick(part_skeleton) or _pick(scene_skeleton)


def bone_row_rest(part_skeleton, scene_skeleton, bone_name: str):
    """Row-major 4×4 rest for ``bone_name`` (part skel first, then scene)."""
    want = (bone_name or "").strip()
    if not want:
        return None
    for skeleton in (part_skeleton, scene_skeleton):
        if skeleton is None:
            continue
        for bone in getattr(skeleton, "bones", None) or []:
            if (getattr(bone, "name", None) or "").strip() == want:
                return bone.transform
    return None


def resolve_skeld_mirror_attach(
    *,
    model_path: str | None,
    mesh_name: str | None,
    rigid_name: str | None,
    carbin_bone: str | None,
    part_skeleton,
    scene_skeleton,
) -> tuple[str, object] | None:
    """When rigid bind is root on a wing-mirror modelbin, return mirror bone + rest."""
    if not is_wing_mirror_model_path(model_path):
        return None
    if not is_root_rigid_bone(rigid_name):
        return None
    if not is_root_carbin_bone(carbin_bone):
        return None

    side = mesh_mirror_side(mesh_name) or model_path_mirror_side(model_path)
    if side is None:
        return None

    mirror_bone = resolve_mirror_bone_name(part_skeleton, scene_skeleton, side)
    if not mirror_bone:
        return None

    rest = bone_row_rest(part_skeleton, scene_skeleton, mirror_bone)
    if rest is None:
        return None
    return mirror_bone, rest
