"""Forza part animations: rig animated car parts and bake .gr2 animations onto them.

The .carbin importer bakes each rigid part's skeleton transform straight into its mesh
vertices, so an imported car has no armature. This module adds one *on top* of an already
imported car:

* During import, every object is tagged with ``forza_bone`` (the Granny skeleton bone it
  belongs to), ``forza_car_root`` (the on-disk car folder) and ``forza_bone_rest`` (the
  bone's rest matrix in Blender space, derived the same way the vertices were baked).
* Here we take the car's ``Animations/*.gr2`` (Granny), convert them to ``.dae`` via LSLib
  (or accept user-converted ``.dae``), parse the Collada XML directly, build a small armature
  for the *animated* bones only, bone-attach the matching meshes with a Child Of constraint
  (so the rest pose is pixel-identical), and bake each animation to an Action / NLA track.

Why parse the .dae ourselves
----------------------------
Blender 5.x removed the Collada importer (``bpy.ops.wm.collada_import`` no longer exists), so
we cannot rely on it. A ``.dae`` is plain XML, so we read the joint hierarchy, rest matrices
and animation samplers directly. This is version-proof and works for both auto-converted and
hand-converted ``.dae`` files.

Coordinate alignment
--------------------
LSLib emits the skeleton in Granny's Y-up space - the *same* space the .carbin importer reads
before it converts ``(x, y, z) -> (-x, -z, y)`` while baking vertices. So a Collada world
matrix ``Mc`` maps into the importer's Blender space with a single left-multiply by that axis
matrix::

    C4 = [[-1, 0, 0, 0],
          [ 0, 0,-1, 0],
          [ 0, 1, 0, 0],
          [ 0, 0, 0, 1]]
    blender_world = C4 @ Mc

This is exactly how each bone's stored ``forza_bone_rest`` was produced, so the rig lands on
the geometry and the bake matches the game motion. Validated at run time against the stored
rest matrices.
"""

import functools
import glob
import math
import os
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Vector

from .contract import (
    COORD_ROWS, PROP_ANIM_RIG, PROP_BONE, PROP_BONE_REST, PROP_CAR_ROOT,
)

# Forza Y-up -> Blender, identical to the importer's vertex baking convention (shared contract).
C4 = Matrix(COORD_ROWS)


def _prefs():
    try:
        return bpy.context.preferences.addons[__package__].preferences
    except (KeyError, AttributeError):
        return None


def _flat_to_matrix(flat):
    f = list(flat)
    return Matrix((f[0:4], f[4:8], f[8:12], f[12:16]))


# ---------------------------------------------------------------------------
# Car / file discovery
# ---------------------------------------------------------------------------

def gather_cars(context):
    """Group tagged objects by their car root folder (prefers the current selection)."""
    selected = [o for o in context.selected_objects if o.get(PROP_CAR_ROOT)]
    pool = selected or [o for o in context.scene.objects if o.get(PROP_CAR_ROOT)]
    roots = {}
    for o in pool:
        roots.setdefault(o[PROP_CAR_ROOT], []).append(o)
    return roots


def find_skeleton_gr2(car_root):
    scene_dir = os.path.join(car_root, "scene")
    if os.path.isdir(scene_dir):
        cands = glob.glob(os.path.join(scene_dir, "*_skeleton.gr2")) or \
            glob.glob(os.path.join(scene_dir, "*skeleton*.gr2"))
        if cands:
            return cands[0]
    cands = glob.glob(os.path.join(car_root, "**", "*skeleton*.gr2"), recursive=True)
    return cands[0] if cands else None


def discover_gr2(car_root):
    anim_dir = os.path.join(car_root, "Animations")
    if os.path.isdir(anim_dir):
        return sorted(glob.glob(os.path.join(anim_dir, "*.gr2")))
    return sorted(glob.glob(os.path.join(car_root, "**", "Animations", "*.gr2"), recursive=True))


def discover_mojo_clips(car_root):
    """FH6+ stores part clips as Mojo .clipd (not Granny .gr2)."""
    patterns = (
        os.path.join(car_root, "**", "animations", "Mojo", "**", "*.clipd"),
        os.path.join(car_root, "**", "Animations", "Mojo", "**", "*.clipd"),
    )
    out = []
    for pat in patterns:
        out.extend(glob.glob(pat, recursive=True))
    return sorted(set(out))


# ---------------------------------------------------------------------------
# LSLib conversion (gr2 -> dae)
# ---------------------------------------------------------------------------

def convert_gr2_to_dae(divine_exe, gr2_path, skeleton_gr2, out_dae):
    """Convert one .gr2 to .dae with LSLib's divine.exe, conforming to the skeleton.

    Returns (ok, message). divine.exe flags vary between LSLib builds; the message surfaces
    stderr so the user can adjust. The manual-.dae path remains the guaranteed fallback.
    """
    # divine.exe (LSLib): single-file convert-model infers formats from the extensions. ``-g`` is
    # required; ``bg3`` selects the modern GR2 path that reads Forza's compressed Granny files
    # (needs granny2.dll next to divine.exe). The skeleton is conformed via the repeated
    # ``-e conform -e conform-copy`` options + ``--conform-path``. No mirror/flip options are set,
    # matching this module's C4 coordinate handling.
    cmd = [
        divine_exe,
        "-a", "convert-model",
        "-g", "bg3",
        "-s", gr2_path,
        "-d", out_dae,
    ]
    if skeleton_gr2 and os.path.isfile(skeleton_gr2):
        cmd += ["-e", "conform", "-e", "conform-copy", "--conform-path", skeleton_gr2]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        return False, f"divine.exe not found: {divine_exe}"
    except subprocess.TimeoutExpired:
        return False, f"Timed out converting {os.path.basename(gr2_path)}"
    if proc.returncode != 0 or not os.path.isfile(out_dae):
        err = (proc.stderr or proc.stdout or "").strip()
        if "granny2.dll" in err.lower():
            return False, ("granny2.dll is required for Forza's compressed GR2 files. Copy "
                           "granny2.dll into the same folder as divine.exe and try again.")
        return False, f"{os.path.basename(gr2_path)}: divine.exe failed ({proc.returncode}). {err[:400]}"
    return True, ""


# ---------------------------------------------------------------------------
# Collada (.dae) parsing - no dependency on Blender's removed Collada importer
# ---------------------------------------------------------------------------

def _ln(tag):
    return tag.rsplit("}", 1)[-1]


def _floats(text):
    return [float(x) for x in text.split()]


def _mat_from_16(vals):
    return Matrix((vals[0:4], vals[4:8], vals[8:12], vals[12:16]))


def _node_local_matrix(node_el):
    """Compose a Collada node's local transform from its <matrix>/translate/rotate/scale."""
    m = Matrix.Identity(4)
    found = False
    for child in node_el:
        tag = _ln(child.tag)
        if tag == "matrix":
            m = m @ _mat_from_16(_floats(child.text))
            found = True
        elif tag == "translate":
            v = _floats(child.text)
            m = m @ Matrix.Translation(v[:3])
            found = True
        elif tag == "rotate":
            v = _floats(child.text)
            if len(v) == 4 and any(v[:3]):
                m = m @ Matrix.Rotation(math.radians(v[3]), 4, Vector(v[:3]))
            found = True
        elif tag == "scale":
            v = _floats(child.text)
            m = m @ Matrix.Diagonal((v[0], v[1], v[2], 1.0))
            found = True
    return m if found else Matrix.Identity(4)


class ColladaAnim:
    """Parsed Collada animation: joint hierarchy + per-joint sampled local matrices."""

    def __init__(self):
        self.nodes = {}          # cid -> {"parent": cid|None, "local": Matrix, "ids": set}
        self.id_to_cid = {}      # any identifier (id/sid/name) -> cid
        self.anim = {}           # cid -> (times[list], locals[list[Matrix]])
        self.up_axis = "Y_UP"

    def resolve(self, identifier):
        return self.id_to_cid.get(identifier)

    def local_at(self, cid, t):
        if cid in self.anim:
            times, mats = self.anim[cid]
            return _lerp_samples(times, mats, t)
        return self.nodes[cid]["local"]

    def world_at(self, cid, t):
        node = self.nodes[cid]
        local = self.local_at(cid, t)
        parent = node["parent"]
        if parent is None:
            return local
        return self.world_at(parent, t) @ local

    def world_rest(self, cid):
        node = self.nodes[cid]
        parent = node["parent"]
        if parent is None:
            return node["local"]
        return self.world_rest(parent) @ node["local"]


def _lerp_samples(times, mats, t):
    if not times:
        return Matrix.Identity(4)
    if t <= times[0]:
        return mats[0]
    if t >= times[-1]:
        return mats[-1]
    for i in range(len(times) - 1):
        if times[i] <= t <= times[i + 1]:
            span = times[i + 1] - times[i]
            f = 0.0 if span == 0 else (t - times[i]) / span
            return mats[i].lerp(mats[i + 1], f)
    return mats[-1]


def parse_dae(path):
    """Parse a Collada .dae into a ColladaAnim, or return None on failure."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        print(f"Forza anim: cannot parse {path}: {exc}")
        return None

    ca = ColladaAnim()
    for asset in root.iter():
        if _ln(asset.tag) == "up_axis" and asset.text:
            ca.up_axis = asset.text.strip()
            break

    # 1) Global source + sampler tables (sources can be referenced library-wide by id).
    sources = {}
    for src in root.iter():
        if _ln(src.tag) != "source":
            continue
        sid = src.get("id")
        for arr in src:
            t = _ln(arr.tag)
            if t == "float_array" and arr.text:
                sources[sid] = ("f", _floats(arr.text))
            elif t == "Name_array" and arr.text:
                sources[sid] = ("n", arr.text.split())

    samplers = {}
    for smp in root.iter():
        if _ln(smp.tag) != "sampler":
            continue
        inputs = {}
        for inp in smp:
            if _ln(inp.tag) == "input":
                inputs[inp.get("semantic")] = (inp.get("source") or "").lstrip("#")
        samplers[smp.get("id")] = inputs

    # 2) Visual-scene node hierarchy.
    counter = [0]

    def walk(node_el, parent_cid):
        cid = f"n{counter[0]}"
        counter[0] += 1
        ids = set()
        for key in ("id", "sid", "name"):
            v = node_el.get(key)
            if v:
                ids.add(v)
        ca.nodes[cid] = {
            "parent": parent_cid,
            "local": _node_local_matrix(node_el),
            "ids": ids,
        }
        for ident in ids:
            ca.id_to_cid.setdefault(ident, cid)
        for child in node_el:
            if _ln(child.tag) == "node":
                walk(child, cid)

    for vs in root.iter():
        if _ln(vs.tag) == "visual_scene":
            for child in vs:
                if _ln(child.tag) == "node":
                    walk(child, None)

    # 3) Animation channels -> per-joint sampled local matrices.
    for chan in root.iter():
        if _ln(chan.tag) != "channel":
            continue
        target = chan.get("target") or ""
        sampler_id = (chan.get("source") or "").lstrip("#")
        node_ident = target.split("/")[0]
        cid = ca.resolve(node_ident)
        if cid is None or sampler_id not in samplers:
            continue
        inputs = samplers[sampler_id]
        in_src = sources.get(inputs.get("INPUT"))
        out_src = sources.get(inputs.get("OUTPUT"))
        if not in_src or not out_src:
            continue
        times = in_src[1]
        out_vals = out_src[1]
        # Only whole-matrix (float4x4) channels are supported for rigid parts.
        if len(out_vals) != len(times) * 16 or not times:
            continue
        mats = [_mat_from_16(out_vals[i * 16:(i + 1) * 16]) for i in range(len(times))]
        ca.anim[cid] = (times, mats)

    return ca


# ---------------------------------------------------------------------------
# Rig building
# ---------------------------------------------------------------------------

def build_rig(context, name, animated_bones, rest_by_bone, parent_map=None):
    """Create an armature with one bone per animated part (placed at its true rest).

    Bones are re-parented to their nearest kept ancestor (``parent_map``) so child parts inherit
    a parent's motion - the basis for composing independent animations (door swing + window roll).
    """
    parent_map = parent_map or {}
    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    arm_obj[PROP_ANIM_RIG] = True  # picked up by the frame_change_pre slot-rebind handler
    context.scene.collection.objects.link(arm_obj)

    prev_active = context.view_layer.objects.active
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    for bn in animated_bones:
        rest = rest_by_bone.get(bn)
        if rest is None:
            continue
        eb = arm_data.edit_bones.new(bn)
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, 0.0, 0.1)
        eb.matrix = rest
        eb.length = 0.12
    # Parent after every bone exists; Child Of-style edit-bone parenting (connect off) leaves each
    # bone's rest world transform untouched, so the rig still lands exactly on the geometry.
    for bn in animated_bones:
        parent = parent_map.get(bn)
        if parent and bn in arm_data.edit_bones and parent in arm_data.edit_bones:
            arm_data.edit_bones[bn].parent = arm_data.edit_bones[parent]
    bpy.ops.object.mode_set(mode="OBJECT")
    if prev_active is not None:
        context.view_layer.objects.active = prev_active
    return arm_obj


def rebind_strip_slots(arm_obj):
    """(Re)bind every NLA strip to its action's slot so the strip actually drives the bones.

    Blender 5.x slotted Actions are fragile when bound programmatically: a strip's ``action_slot``
    binding silently lapses after intervening depsgraph evaluations (the strip still *reports* its
    slot, yet evaluates to nothing). Re-assigning the slot makes it drive again, so this is run
    from a persistent ``frame_change_pre`` handler (see ``forza_anim_frame_pre``) to keep every
    rigged car animating no matter what else happened in the session.
    """
    ad = getattr(arm_obj, "animation_data", None)
    if not ad:
        return
    for track in ad.nla_tracks:
        # Only (re)bind strips that actually evaluate. Rebinding muted strips too makes the
        # binding of several simultaneously-active strips unreliable (only one ends up driving),
        # which breaks combining animations (e.g. door open + window down).
        if track.mute:
            continue
        for strip in track.strips:
            slots = getattr(strip.action, "slots", None) if strip.action else None
            if slots:
                try:
                    strip.action_slot = slots[0]
                except (AttributeError, TypeError):
                    pass


def _deferred_rebind(arm_name):
    obj = bpy.data.objects.get(arm_name)
    if obj is not None:
        rebind_strip_slots(obj)
    return None  # one-shot timer


@persistent
def forza_anim_frame_pre(scene, depsgraph=None):
    """Refresh NLA strip slot bindings for every Forza animation rig before each frame is drawn.

    See ``rebind_strip_slots`` for why this is necessary on Blender 5.x.
    """
    for obj in bpy.data.objects:
        if obj.get(PROP_ANIM_RIG):
            rebind_strip_slots(obj)


def register_handlers():
    if forza_anim_frame_pre not in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.append(forza_anim_frame_pre)


def unregister_handlers():
    if forza_anim_frame_pre in bpy.app.handlers.frame_change_pre:
        bpy.app.handlers.frame_change_pre.remove(forza_anim_frame_pre)


def read_rest_pose(arm_obj):
    """Read each bone's actual rest world matrix as Blender stored it (armature at identity).

    The importer's rest matrices contain a reflection (handedness flip, det -1) which Blender
    cannot store in an edit-bone, so the bone Blender actually builds differs from the requested
    matrix. We read it back here and drive everything (Child Of inverse + bake) off this real
    rest, so parts stay exactly in place.
    """
    return {pb.name: pb.matrix.copy() for pb in arm_obj.pose.bones}


def _remap_accessory_bone(obj, bn, available_bones):
    """Re-home door-mounted accessories that the mesh data binds to the wrong bone.

    The side ('wing') mirrors are modelled as their own part and the extracted mesh binds
    them to the car root ('<root>'), not to the door - even though the game skeleton has
    boneMirrorL/R under boneDoorLF. Left as-is they never move when the door opens. They are
    rigidly mounted on the door, so re-home wingMirrorL/R onto boneDoorLF/RF (whose motion is
    identical to the mirror bones in the door-open animations). Only applied when that door
    bone actually exists for this car; otherwise the original tag is kept.
    """
    name = obj.name.lower()
    if "wingmirror" in name:
        side = name.split("wingmirror", 1)[1][:1]
        cand = {"l": "boneDoorLF", "r": "boneDoorRF"}.get(side)
        if cand and cand in available_bones:
            return cand
    return bn


def attach_objects(arm_obj, car_objs, animated_bones, rest_pose):
    """Bone-attach each animated car object with a Child Of constraint (rest unchanged).

    The imported meshes carry their world position in vertex data, so each object's own
    transform is identity. Child Of evaluates ``world = bone_world @ inverse_matrix @ owner``;
    with ``inverse_matrix = bone_rest^-1`` and ``owner = identity`` the object stays put at rest.
    """
    attached = 0
    for obj in car_objs:
        bn = _remap_accessory_bone(obj, obj.get(PROP_BONE), rest_pose)
        if bn not in animated_bones or bn not in rest_pose:
            continue
        for con in [c for c in obj.constraints if c.name == "Forza Anim"]:
            obj.constraints.remove(con)
        con = obj.constraints.new("CHILD_OF")
        con.name = "Forza Anim"
        con.target = arm_obj
        con.subtarget = bn
        con.inverse_matrix = rest_pose[bn].inverted()
        attached += 1
    return attached


# ---------------------------------------------------------------------------
# Baking
# ---------------------------------------------------------------------------

def _affected(ca, cid):
    """True if this node or any of its ancestors is animated in ``ca``.

    A door window, for example, has no channel of its own in ``doorLF_open`` - only the parent
    ``boneDoorLF`` is keyframed - but it must still follow the door because it is a child bone.
    """
    while cid is not None:
        if cid in ca.anim:
            return True
        cid = ca.nodes[cid]["parent"]
    return False


def compute_parent_map(ca, rigged):
    """Map each rigged bone to its nearest rigged ancestor (or None) using the skeleton tree.

    The armature mirrors the Granny hierarchy *for the bones we keep*: e.g. ``boneWindowLF`` is
    re-parented to ``boneDoorLF`` (its nearest kept ancestor), skipping the intermediate
    ``root_boneWindowLF`` helper. This is what lets independent animations compose - the door
    swing (on ``boneDoorLF``) and the window roll-down (local on ``boneWindowLF``) add up instead
    of one overwriting the other.
    """
    rigged = set(rigged)
    pmap = {}
    for bn in rigged:
        cid = ca.resolve(bn)
        parent_bone = None
        if cid is not None:
            anc = ca.nodes[cid]["parent"]
            while anc is not None:
                hit = next((i for i in ca.nodes[anc]["ids"] if i in rigged), None)
                if hit is not None:
                    parent_bone = hit
                    break
                anc = ca.nodes[anc]["parent"]
        pmap[bn] = parent_bone
    return pmap


def bake_action(context, arm_obj, ca, animated_bones, rest_by_bone, rest_pose, parent_map,
                action_name):
    """Bake a parsed Collada animation onto ``arm_obj`` as an Action + (muted) NLA strip.

    Only bones with their *own* channel in this animation are keyed; child parts without a channel
    (e.g. the door window in ``doorLF_open``) follow through the rig's bone parenting instead of
    being baked in absolutely. This is what makes independent animations compose: ``doorLF_open``
    drives ``boneDoorLF`` and ``windowLF_open`` drives ``boneWindowLF`` *locally*, so opening the
    door and rolling the window down add up instead of the window snapping back.

    Each keyed bone is written as a parent-relative ``matrix_basis`` solved analytically from the
    desired world pose and the parent's pose in this same animation, so baking is deterministic and
    independent of depsgraph/evaluation order. The world pose uses ``M = C4 @ Mc(t) @ R_b^-1`` (a
    proper rigid transform, det +1) applied to ``rest_pose[bone]``, so frame-at-rest reproduces the
    bone's real rest exactly and no negative scale enters the bones.
    """
    scene = context.scene
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    parent_map = parent_map or {}

    # Time origin + the union of every channel's sample times.
    all_times = set()
    t0 = None
    for times, _mats in ca.anim.values():
        if times:
            all_times.update(times)
            t0 = times[0] if t0 is None else min(t0, times[0])
    if not all_times:
        return None, "no animation samples"
    times_sorted = sorted(all_times)
    if t0 is None:
        t0 = 0.0

    # Only bones with their own animation channel in this .dae are keyed; descendants follow via
    # bone parenting (their basis stays at rest).
    bone_cid = {}
    for bn in animated_bones:
        cid = ca.resolve(bn)
        if cid is not None and cid in ca.anim:
            bone_cid[bn] = cid
    if not bone_cid:
        return None, "no matching animated bones"

    def world_pose(bn, cid, t):
        """Desired armature-space world pose of a bone at time t (incl. animated ancestors)."""
        return (C4 @ ca.world_at(cid, t) @ rest_by_bone[bn].inverted()) @ rest_pose[bn]

    # Reset the whole pose to rest so un-keyed parents stay put while we solve child bases.
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        pb.matrix_basis = Matrix.Identity(4)

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    action = bpy.data.actions.new(action_name)
    arm_obj.animation_data.action = action

    # Make inserted keyframes LINEAR (matches Collada's baked samples) without touching the
    # Action's f-curves directly - Blender 5.x's slotted Actions removed ``Action.fcurves``.
    edit_prefs = context.preferences.edit
    prev_interp = edit_prefs.keyframe_new_interpolation_type
    edit_prefs.keyframe_new_interpolation_type = "LINEAR"
    try:
        frame_max = 1
        for bn, cid in bone_cid.items():
            pb = arm_obj.pose.bones.get(bn)
            if pb is None:
                continue
            pb.rotation_mode = "QUATERNION"
            rest = rest_pose.get(bn)
            if rest is None or bn not in rest_by_bone:
                continue
            parent = parent_map.get(bn)
            p_cid = ca.resolve(parent) if parent else None
            p_ok = (parent in rest_pose and parent in rest_by_bone and p_cid is not None)
            for t in times_sorted:
                frame = 1 + int(round((t - t0) * fps))
                frame_max = max(frame_max, frame)
                w_bone = world_pose(bn, cid, t)
                if p_ok:
                    # basis = L[bn]^-1 . L[p] . parent_pose^-1 . world_pose  (Blender bone formula)
                    p_pose = world_pose(parent, p_cid, t)
                    basis = rest.inverted() @ rest_pose[parent] @ p_pose.inverted() @ w_bone
                else:
                    basis = rest.inverted() @ w_bone
                pb.matrix_basis = basis
                pb.keyframe_insert("location", frame=frame)
                pb.keyframe_insert("rotation_quaternion", frame=frame)
    finally:
        edit_prefs.keyframe_new_interpolation_type = prev_interp

    # Park on its own muted NLA track so several animations coexist. Blender 5.x slotted Actions
    # require the strip's action_slot to be bound explicitly, otherwise the strip evaluates to
    # nothing.
    arm_obj.animation_data.action = None
    track = arm_obj.animation_data.nla_tracks.new()
    track.name = action_name
    strip = track.strips.new(action_name, 1, action)
    slots = getattr(action, "slots", None)
    if slots:
        try:
            strip.action_slot = slots[0]
        except (AttributeError, TypeError):
            pass
    track.mute = True
    return action, frame_max


def validate_alignment(ca, animated_bones, rest_by_bone):
    """Compare C4 @ (collada rest) vs the stored Forza rest; returns (max_dev_m, bone)."""
    worst, worst_bone = None, None
    for bn in animated_bones:
        cid = ca.resolve(bn)
        if cid is None or bn not in rest_by_bone:
            continue
        ours = C4 @ ca.world_rest(cid)
        d = (ours.translation - rest_by_bone[bn].translation).length
        if worst is None or d > worst:
            worst, worst_bone = d, bn
    return worst, worst_bone


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class IMPORT_SCENE_OT_forza_animations(Operator, ImportHelper):
    """Rig an imported Forza car and bake its part animations (doors, hood, trunk, ...)"""

    bl_idname = "import_scene.forza_animations"
    bl_label = "Import Forza Animations"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".dae"
    filter_glob: StringProperty(default="*.dae", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    only_default_parts: BoolProperty(
        name="Skip Pistons/Struts/Aim Helpers",
        description="Only rig the main moving panels (doors, hood, trunk, roof, windows, wing, "
                    "wipers, gauges). Leave off to rig every animated bone",
        default=False,
    )

    _SKIP_TOKENS = ("piston", "strut", "aim", "hinge", "spindle", "blade")

    def _resolve_car(self, context):
        roots = gather_cars(context)
        if not roots:
            self.report({"ERROR"}, "No imported Forza car found. Import a .carbin first.")
            return None
        if len(roots) > 1:
            self.report({"ERROR"}, "Several cars in the scene. Select a part of the one to animate.")
            return None
        return next(iter(roots.items()))

    def invoke(self, context, event):
        resolved = self._resolve_car(context)
        if resolved is None:
            return {"CANCELLED"}
        self._car_root, self._car_objs = resolved

        prefs = _prefs()
        divine = bpy.path.abspath(prefs.lslib_path) if prefs and prefs.lslib_path else ""
        if divine and os.path.isfile(divine):
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _collect_dae(self, context):
        prefs = _prefs()
        divine = bpy.path.abspath(prefs.lslib_path) if prefs and prefs.lslib_path else ""
        if divine and os.path.isfile(divine):
            skel = find_skeleton_gr2(self._car_root)
            gr2s = discover_gr2(self._car_root)
            if not gr2s:
                mojo = discover_mojo_clips(self._car_root)
                if mojo:
                    self.report(
                        {"ERROR"},
                        "This car uses FH6 Mojo animations (.clipd under Scene/animations/Mojo/), "
                        "not Granny .gr2. LSLib/divine cannot convert Mojo — animation import is "
                        f"not supported yet ({len(mojo)} clip(s) found).",
                    )
                else:
                    self.report(
                        {"ERROR"},
                        f"No Animations\\*.gr2 found under {self._car_root}.",
                    )
                return None, None
            tmpdir = tempfile.mkdtemp(prefix="forza_anim_")
            dae_list = []
            for g in gr2s:
                out = os.path.join(tmpdir, os.path.splitext(os.path.basename(g))[0] + ".dae")
                ok, msg = convert_gr2_to_dae(divine, g, skel, out)
                if ok:
                    dae_list.append(out)
                else:
                    self.report({"WARNING"}, msg)
            if not dae_list:
                self.report({"ERROR"}, "LSLib produced no .dae files. Check divine.exe / flags.")
                return None, None
            return dae_list, tmpdir

        if self.files:
            dae_list = [os.path.join(self.directory, f.name)
                        for f in self.files if f.name.lower().endswith(".dae")]
        elif self.directory:
            dae_list = sorted(glob.glob(os.path.join(self.directory, "*.dae")))
        else:
            dae_list = []
        if not dae_list:
            self.report({"ERROR"},
                        "Set the LSLib divine.exe path in addon preferences to auto-convert, "
                        "or choose .dae files you converted with LSLib.")
            return None, None
        return dae_list, None

    def execute(self, context):
        if not getattr(self, "_car_root", None):
            resolved = self._resolve_car(context)
            if resolved is None:
                return {"CANCELLED"}
            self._car_root, self._car_objs = resolved

        dae_list, tmpdir = self._collect_dae(context)
        if dae_list is None:
            return {"CANCELLED"}

        # Exact rest matrices captured at import time (our Blender space).
        rest_by_bone = {}
        for o in self._car_objs:
            bn = o.get(PROP_BONE)
            rest = o.get(PROP_BONE_REST)
            if bn and rest and bn not in rest_by_bone:
                rest_by_bone[bn] = _flat_to_matrix(rest)

        # Parse every .dae once and find which tagged bones are actually animated.
        parsed = []
        animated = set()
        for dae in dae_list:
            ca = parse_dae(dae)
            if ca is None:
                self.report({"WARNING"}, f"Skipped unreadable .dae: {os.path.basename(dae)}")
                continue
            if ca.up_axis.upper() != "Y_UP":
                self.report({"WARNING"},
                            f"{os.path.basename(dae)} is {ca.up_axis}; expected Y_UP. "
                            "Animation orientation may be wrong.")
            name = os.path.splitext(os.path.basename(dae))[0]
            parsed.append((name, ca))
            for bn in rest_by_bone:
                cid = ca.resolve(bn)
                if cid is not None and _affected(ca, cid):
                    animated.add(bn)

        if self.only_default_parts:
            animated = {b for b in animated
                        if not any(tok in b.lower() for tok in self._SKIP_TOKENS)}
        if not animated:
            self.report({"ERROR"}, "No animated bones in the .dae matched this car's parts.")
            self._cleanup_tmp(tmpdir)
            return {"CANCELLED"}

        # Coordinate sanity check (milestone 1): stored rest vs parsed rest.
        if parsed:
            dev, dev_bone = validate_alignment(parsed[0][1], animated, rest_by_bone)
            if dev is not None:
                print(f"Forza anim: rest alignment max deviation {dev:.4f} m on '{dev_bone}'")
                if dev > 0.05:
                    self.report({"WARNING"},
                                f"Rig/animation axis check off by {dev:.3f} m ({dev_bone}); "
                                "animation direction may be wrong.")

        # Re-running should be clean: drop any previous rig/constraints/actions for this car so
        # we never accumulate stale armatures or out-of-date baked actions.
        self._remove_existing_rig(self._car_objs)

        # Re-parent kept bones to their nearest kept ancestor so child parts (windows, mirrors)
        # inherit a parent's motion and independent animations compose. Any parsed .dae carries
        # the full conformed skeleton, so the first one is enough to read the hierarchy.
        parent_map = compute_parent_map(parsed[0][1], animated) if parsed else {}

        car_name = os.path.basename(os.path.normpath(self._car_root))
        arm_obj = build_rig(context, f"{car_name}_anim", sorted(animated), rest_by_bone, parent_map)
        context.view_layer.update()
        rest_pose = read_rest_pose(arm_obj)
        attached = attach_objects(arm_obj, self._car_objs, animated, rest_pose)

        baked = 0
        span_end = context.scene.frame_start
        for name, ca in parsed:
            action, info = bake_action(context, arm_obj, ca, animated, rest_by_bone, rest_pose,
                                       parent_map, name)
            if action is not None:
                baked += 1
                if isinstance(info, int):
                    span_end = max(span_end, info)

        if span_end > context.scene.frame_end:
            context.scene.frame_end = span_end

        # Baking leaves the live pose at the last sampled frame. Reset every bone to its rest
        # basis so the car sits exactly like the static import until the user enables an NLA
        # track (all tracks are muted by default; enabling one drives only its bones, the rest
        # stay at rest).
        from mathutils import Matrix as _M
        for pb in arm_obj.pose.bones:
            pb.matrix_basis = _M.Identity(4)
        context.view_layer.update()

        # Slotted-Action strip bindings only commit once execute() has returned, so defer the
        # re-bind to a one-shot timer (see rebind_strip_slots). Without this the NLA tracks
        # exist but play nothing when enabled.
        try:
            bpy.app.timers.register(
                functools.partial(_deferred_rebind, arm_obj.name), first_interval=0.0)
        except (RuntimeError, ValueError, TypeError):
            rebind_strip_slots(arm_obj)

        self._cleanup_tmp(tmpdir)
        self.report({"INFO"},
                    f"Rigged {attached} parts, baked {baked} animation(s) onto '{arm_obj.name}'. "
                    "Enable an NLA track to play.")
        return {"FINISHED"}

    @staticmethod
    def _remove_existing_rig(car_objs):
        """Delete any previous Forza animation rig (armature + Child Of + baked actions) for
        this car, so re-importing animations rebuilds from scratch instead of layering on top."""
        rigs = set()
        for obj in car_objs:
            for con in list(obj.constraints):
                if con.name == "Forza Anim":
                    if con.target is not None and con.target.get(PROP_ANIM_RIG):
                        rigs.add(con.target)
                    obj.constraints.remove(con)
        for arm in rigs:
            actions = []
            if arm.animation_data:
                for tr in arm.animation_data.nla_tracks:
                    for st in tr.strips:
                        if st.action:
                            actions.append(st.action)
            data = arm.data
            bpy.data.objects.remove(arm, do_unlink=True)
            if data is not None and data.users == 0:
                bpy.data.armatures.remove(data)
            for act in actions:
                if act.users == 0:
                    bpy.data.actions.remove(act)

    @staticmethod
    def _cleanup_tmp(tmpdir):
        if not tmpdir:
            return
        try:
            for f in os.listdir(tmpdir):
                os.remove(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except OSError:
            pass
