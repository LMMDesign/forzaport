"""Forza part animations: rig animated car parts and bake animations onto them.

The .carbin importer bakes each rigid part's skeleton transform straight into its mesh
vertices, so an imported car has no armature. This module adds one *on top* of an already
imported car:

* During import, every object is tagged with authored bind metadata (``forza_rigid_bone``,
  ``forza_carbin_bone``, ``forza_model_path``, ``forza_mesh_name``) and an effective
  ``forza_bone`` / ``forza_bone_rest`` for Child Of (carbin attach bone when present,
  else modelbin rigid bone). ``forza_car_root`` locates animation media on disk.
* **FH5:** ``Animations/*.gr2`` (Granny) → bundled ``tools/gr2dump`` local 4×4
  matrices → ``bake_action`` (Divine Collada product, no Divine.exe).
* **FH6:** ``Scene/animations/Mojo/*.clipd`` (+ ``.skeld``) → native ACL 2.1
  tracks → ``bake_mojo_action``. ACL is required; there is no mid fallback.
  Separate system — never mixed with FH5 GR2.
* **Legacy:** hand-picked Collada ``.dae`` when neither media type is present.

Pipelines are chosen by on-disk Autovista media (``detect_anim_pipeline``), not by
folder name heuristics or cross-game fallbacks.

Why a legacy .dae path still exists
-----------------------------------
Blender 5.x removed Collada import. Old workflows that already have ``.dae`` files can still
pick them; FH5 cars use gr2dump matrices; FH6 cars use Mojo only.

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
import xml.etree.ElementTree as ET

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Quaternion, Vector

from .development import development_enabled

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
    """Group tagged objects by their car root folder (prefers the current selection).

    Selecting any one part of a car expands to **all** scene objects that share
    that ``forza_car_root`` so Child Of attachment covers every door/hood mesh,
    not only the active selection.
    """
    selected = [o for o in context.selected_objects if o.get(PROP_CAR_ROOT)]
    if selected:
        root_keys = {o[PROP_CAR_ROOT] for o in selected}
        roots = {r: [] for r in root_keys}
        for o in context.scene.objects:
            r = o.get(PROP_CAR_ROOT)
            if r in roots:
                roots[r].append(o)
        return roots
    roots = {}
    for o in context.scene.objects:
        r = o.get(PROP_CAR_ROOT)
        if r:
            roots.setdefault(r, []).append(o)
    return roots


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


def discover_mojo_skeld(car_root):
    """Find ``skeleton.skeld`` (or any ``.skeld``) under the car's Mojo folder."""
    patterns = (
        os.path.join(car_root, "**", "animations", "Mojo", "**", "*.skeld"),
        os.path.join(car_root, "**", "Animations", "Mojo", "**", "*.skeld"),
        os.path.join(car_root, "**", "*.skeld"),
    )
    out = []
    for pat in patterns:
        out.extend(glob.glob(pat, recursive=True))
    # Prefer a file literally named skeleton.skeld
    named = [p for p in out if os.path.basename(p).lower() == "skeleton.skeld"]
    return (named or sorted(set(out)) or [None])[0] if out else None


def detect_anim_pipeline(car_root) -> str:
    """Which Autovista system this car uses: ``fh5_gr2``, ``fh6_mojo``, or ``none``.

    Content-based (not folder names). If both Mojo and GR2 somehow exist, Mojo
    wins and a warning is printed — pipelines are never merged.
    """
    mojo = discover_mojo_clips(car_root)
    gr2s = [
        p
        for p in discover_gr2(car_root)
        if "skel" not in os.path.basename(p).lower()
    ]
    if mojo and gr2s:
        print(
            "Forza anim: both Mojo .clipd and Animations/*.gr2 found; "
            "using FH6 Mojo pipeline only (no merge)."
        )
        return "fh6_mojo"
    if mojo:
        return "fh6_mojo"
    if gr2s:
        return "fh5_gr2"
    return "none"


def load_mojo_hinges(car_root, *, respect_mojo_config=True):
    """Parse the car's Mojo ``.clipd`` into ACL-backed ``HingeChannel`` list.

    FH6 requires ACL 2.1. Failures return ``([], error_message)`` — no mid fallback.
    When ``MojoConfig.xml`` is present, hinge events outside its
    ``AutovistaEvents`` list are dropped (catalog of declared mechanisms).
    """
    from .parsing.mojo_acl import MojoAclError
    from .parsing.mojo_clipd import parse_clipd
    from .parsing.mojo_config import filter_hinges_by_mojo_config, load_mojo_config

    clips = discover_mojo_clips(car_root)
    if not clips:
        return [], "no Mojo .clipd under this car"
    # One Autovista pack per car in practice; prefer the largest if several exist.
    clip_path = max(clips, key=lambda p: os.path.getsize(p))
    try:
        pack = parse_clipd(clip_path)
        hang_by_bone = {}
        nodes = load_mojo_skeld_nodes(car_root)
        if nodes:
            hang_by_bone = {n.name: n.pos for n in nodes if n.name}
        hinges = pack.hinge_channels(hang_by_bone=hang_by_bone)
    except MojoAclError as exc:
        return [], f"Mojo ACL required: {exc}"
    except (OSError, ValueError, RuntimeError) as exc:
        return [], f"Mojo clipd failed: {exc}"
    if not hinges:
        return [], f"no hinge channels in {os.path.basename(clip_path)}"
    if respect_mojo_config:
        cfg = load_mojo_config(car_root)
        if cfg is not None:
            before = len(hinges)
            hinges = filter_hinges_by_mojo_config(hinges, cfg)
            if not hinges:
                return (
                    [],
                    f"MojoConfig filtered all {before} clip event(s); "
                    f"declared: {', '.join(cfg.autovista_events) or '(none)'}",
                )
    return hinges, ""


def load_mojo_skeld_nodes(car_root):
    """Parse ``.skeld`` with catalog + harvested bone names; ``None`` if missing."""
    from .parsing.mojo_clipd import resolve_bone_names
    from .parsing.mojo_skeld import parse_skeld

    skeld = discover_mojo_skeld(car_root)
    if not skeld:
        return None
    hints = resolve_bone_names(search_roots=[os.path.dirname(skeld), car_root])
    try:
        return parse_skeld(skeld, hints)
    except (OSError, ValueError) as exc:
        print(f"Forza anim: .skeld load failed ({exc})")
        return None


def _quat_xyz_to_matrix(qx, qy, qz, qw):
    """Mojo/skeld xyzw quaternion → 3×3 rotation (mathutils Matrix)."""
    return Quaternion((qw, qx, qy, qz)).to_matrix()


def skeld_blender_rests(nodes):
    """World rest matrices in Blender space for every named Mojo node."""
    by_index = {n.index: n for n in nodes}
    cache = {}

    def forza_world(idx):
        if idx in cache:
            return cache[idx]
        node = by_index[idx]
        local = _quat_xyz_to_matrix(*node.quat).to_4x4()
        local.translation = Vector(node.pos)
        if node.parent >= 0 and node.parent in by_index:
            world = forza_world(node.parent) @ local
        else:
            world = local
        cache[idx] = world
        return world

    out = {}
    for node in nodes:
        if not node.name:
            continue
        out[node.name] = C4 @ forza_world(node.index)
    return out


def apply_car_hinge_rests(skeld_rests, nodes, car_objs=None):
    """Return authored ``.skeld`` rests unchanged.

    Boundary: never invent pivots from mesh. Door ``*HingeUpper*`` bones are
    strut/aim joints (~0.5 m Forza Y on AMG), **not** the Autovista Mode A
    pivot — relocating ``root_*`` onto them drops the hinge into the sill.
    Cars without a second authored hinge field (F80 / P1 / AMG) keep the
    floating Autovista scaffold (Z≈1.71). ``nodes`` / ``car_objs`` kept for
    call-site compatibility only.
    """
    del nodes, car_objs
    return dict(skeld_rests) if skeld_rests else {}


def compute_parent_map_skeld(nodes, rigged):
    """Nearest kept ancestor from a parsed ``.skeld`` (same role as Collada parent map)."""
    rigged = set(rigged)
    by_index = {n.index: n for n in nodes}
    pmap = {}
    for bn in rigged:
        node = next((n for n in nodes if n.name == bn), None)
        parent_bone = None
        if node is not None:
            p = node.parent
            while p is not None and p >= 0:
                anc = by_index.get(p)
                if anc is None:
                    break
                if anc.name and anc.name in rigged:
                    parent_bone = anc.name
                    break
                p = anc.parent
        pmap[bn] = parent_bone
    return pmap


def skeld_parent_map(car_root, rigged, nodes=None):
    """Build a parent map from `.skeld` when available; otherwise flat (no parents)."""
    if nodes is None:
        nodes = load_mojo_skeld_nodes(car_root)
    if not nodes:
        return {bn: None for bn in rigged}
    return compute_parent_map_skeld(nodes, rigged)


# ---------------------------------------------------------------------------
# Collada (.dae) parsing - legacy path only (FH5 uses gr2dump)
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


def _panel_bone_for_strut_tag(tag: str, available: dict[str, str]) -> str | None:
    """Legacy FH5: unused strut/piston bind → nearest ``boneDoor*`` panel."""
    low = (tag or "").lower()
    if not any(tok in low for tok in ("strut", "aim", "piston", "hinge")):
        return None
    if (
        "doorrf" in low
        or low.endswith("rf")
        or low.endswith("rr")
        or "_r" in low
    ) and "bonedoorrf" in available:
        return available["bonedoorrf"]
    if (
        "doorlf" in low
        or low.endswith("lf")
        or low.endswith("lr")
        or "_l" in low
    ) and "bonedoorlf" in available:
        return available["bonedoorlf"]
    return None


def _remap_accessory_bone(obj, bn, available_bones, animated_bones=None):
    """Resolve Child Of target for accessory skins.

    FH6 ACL keys door piston/aim bones — keep modelbin ``forza_bone`` /
    ``forza_rigid_bone`` when that bone is in the animation rig.

    Root-rigid ``doorJamb*Strut*`` stays on authored ``<root>`` (game-static;
    AMG ships both sides in one mesh).

    Legacy FH5 path only: if the bind names a strut/piston that is *not*
    rigged, fall back to the door panel so the skin still swings with the door.
    """
    from .contract import PROP_RIGID_BONE

    animated = set(animated_bones or [])
    available = {b.lower(): b for b in available_bones}
    for tag in (obj.get(PROP_RIGID_BONE), bn):
        if not tag:
            continue
        low = str(tag).lower()
        if tag in animated and any(
            tok in low for tok in ("piston", "strut", "aim", "hinge")
        ):
            return tag
    for tag in (obj.get(PROP_RIGID_BONE), bn):
        hit = _panel_bone_for_strut_tag(tag, available)
        if hit:
            return hit
    return bn


def attach_objects(arm_obj, car_objs, animated_bones, rest_pose, attach_target=None):
    """Bone-attach each animated car object with a Child Of constraint (rest unchanged).

    The imported meshes carry their world position in vertex data, so each object's own
    transform is identity. Child Of evaluates ``world = bone_world @ inverse_matrix @ owner``;
    with ``inverse_matrix = bone_rest^-1`` and ``owner = identity`` the object stays put at rest.

    ``attach_target`` maps mesh ``forza_bone`` tags → armature bone (Mode A: panel →
    ``root_bone*``) so the Child Of relationship line lands on the hinge, not the COM.
    """
    attach_target = attach_target or {}
    attached = 0
    for obj in car_objs:
        bn = _remap_accessory_bone(
            obj, obj.get(PROP_BONE), rest_pose, animated_bones=animated_bones
        )
        target = attach_target.get(bn, bn)
        if target not in animated_bones or target not in rest_pose:
            continue
        for con in [c for c in obj.constraints if c.name == "Forza Anim"]:
            obj.constraints.remove(con)
        con = obj.constraints.new("CHILD_OF")
        con.name = "Forza Anim"
        con.target = arm_obj
        con.subtarget = target
        con.inverse_matrix = rest_pose[target].inverted()
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


def collada_anim_from_gr2_doc(doc, skel_bones):
    """Rebuild a ``ColladaAnim`` from ``gr2dump_v2_matrix`` JSON (Divine DAE equivalent)."""
    ca = ColladaAnim()
    ca.up_axis = "Y_UP"
    bones = list(skel_bones or [])
    by_idx = {i: b for i, b in enumerate(bones)}

    for i, b in enumerate(bones):
        name = b.get("name") or ""
        if not name:
            continue
        p = int(b.get("parent", -1))
        parent = by_idx[p].get("name") if p >= 0 and p in by_idx else None
        pos = b.get("pos") or [0, 0, 0]
        quat = b.get("quat") or [0, 0, 0, 1]
        qx, qy, qz, qw = quat[:4]
        local = Quaternion((qw, qx, qy, qz)).to_matrix().to_4x4()
        local.translation = Vector(pos)
        ca.nodes[name] = {"parent": parent, "local": local, "ids": {name, f"Bone_{name}"}}
        ca.id_to_cid[name] = name
        ca.id_to_cid[f"Bone_{name}"] = name

    anims = doc.get("animations") or []
    if not anims:
        return ca
    for tr in anims[0].get("tracks") or []:
        bone = tr.get("bone") or ""
        times = [float(t) for t in (tr.get("times") or [])]
        raw = tr.get("matrices") or []
        if not bone or not times or len(raw) != len(times):
            continue
        if bone not in ca.nodes:
            ca.nodes[bone] = {
                "parent": None,
                "local": Matrix.Identity(4),
                "ids": {bone, f"Bone_{bone}"},
            }
            ca.id_to_cid[bone] = bone
            ca.id_to_cid[f"Bone_{bone}"] = bone
        mats = [_mat_from_16([float(x) for x in row]) for row in raw]
        ca.anim[bone] = (times, mats)
    return ca


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


def bake_mojo_action(
    context,
    arm_obj,
    channel,
    action_name,
    *,
    drive_bone=None,
    open_quat=None,
    rest_by_bone=None,
    rest_pose=None,
    bone_quats=None,
    bone_locs=None,
    parent_map=None,
):
    """Bake one Mojo event as a single Action / NLA track.

    ``bone_quats`` is ``[(bone_name, xyzw_quat), ...]`` — primary panel/hinge plus
    any piston/aim helpers that share the event curve. Each bone is keyed with its
    own bone-local open pose; armature parenting composes nested helpers.

    ``bone_locs`` maps bone → Forza **bone-local** open translation (metres) from
    nch=0 translation mids in this car's ``.clipd`` — not from FH5 / live capture.
    """
    if bone_quats is None:
        bone = drive_bone or channel.bone_hint
        oq = open_quat if open_quat is not None else getattr(channel, "open_quat", None)
        if oq is None:
            oq = (0.0, 0.0, 0.0, 1.0)
        bone_quats = [(bone, oq)] if bone else []

    bone_quats = [(b, q) for b, q in bone_quats if b and arm_obj.pose.bones.get(b)]
    if not bone_quats:
        return None, "no bone"

    rest_by_bone = rest_by_bone or {}
    rest_pose = rest_pose or {}
    parent_map = parent_map or {}
    bone_locs = bone_locs or {}
    for bone, _oq in bone_quats:
        if rest_pose.get(bone) is None or rest_by_bone.get(bone) is None:
            return None, f"missing rest for '{bone}'"

    scene = context.scene
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    duration = channel.duration if channel.duration > 0 else (31.0 / 30.0)
    steps = max(2, int(round(duration * fps)))
    prim_oq = bone_quats[0][1]
    q_prim = Quaternion((prim_oq[3], prim_oq[0], prim_oq[1], prim_oq[2])).normalized()
    amp = channel.amplitude_deg or math.degrees(q_prim.angle) or 1.0
    q_id = Quaternion((1.0, 0.0, 0.0, 0.0))
    # parent_map retained for call-site compatibility; bone-local ACL/mid keys
    # rely on armature parenting instead of bake-time parent compensation.
    _ = parent_map

    def local_aim_world(bone: str, oq, weight: float, direct_loc=None):
        """World pose of ``bone`` if its parent stay at rest (Forza R/T on own rest)."""
        rest = rest_pose[bone]
        r_b = rest_by_bone[bone]
        w_rest_f = C4.inverted() @ r_b
        w = max(0.0, min(1.0, weight))
        q_open = Quaternion((oq[3], oq[0], oq[1], oq[2])).normalized()
        q = q_id.slerp(q_open, w)
        r_local = q.to_matrix().to_4x4()
        mc = w_rest_f @ r_local
        loc = direct_loc if direct_loc is not None else bone_locs.get(bone)
        if loc is not None and w > 1e-8:
            # Authored mid ΔT is bone-local (skeld axes); lift into Forza world.
            delta_f = w_rest_f.to_3x3() @ Vector(
                (float(loc[0]), float(loc[1]), float(loc[2]))
            )
            mc = mc.copy()
            loc_weight = 1.0 if direct_loc is not None else w
            mc.translation = Vector(w_rest_f.translation) + loc_weight * delta_f
        return (C4 @ mc @ r_b.inverted()) @ rest

    acl_drive_by_bone = {
        drv.bone: drv
        for drv in (getattr(channel, "drives", None) or [])
        if drv.bone and getattr(drv, "acl_quats", None)
    }

    def sample_acl_drive(drv, t):
        """Interpolate the native ACL local Q/T sample at event time."""
        quats = list(getattr(drv, "acl_quats", None) or [])
        locs = list(getattr(drv, "acl_locs", None) or [])
        if not quats:
            return None
        rate = float(getattr(drv, "acl_sample_rate", 0.0) or 0.0)
        pos = max(0.0, min(float(len(quats) - 1), t * rate)) if rate > 0 else 0.0
        i0 = int(math.floor(pos))
        i1 = min(i0 + 1, len(quats) - 1)
        alpha = pos - i0
        q0 = Quaternion((quats[i0][3], quats[i0][0], quats[i0][1], quats[i0][2])).normalized()
        q1 = Quaternion((quats[i1][3], quats[i1][0], quats[i1][1], quats[i1][2])).normalized()
        q = q0.slerp(q1, alpha)
        q_xyzw = (q.x, q.y, q.z, q.w)
        loc = None
        if i0 < len(locs):
            l0 = locs[i0]
            l1 = locs[i1] if i1 < len(locs) else l0
            loc = tuple(float(l0[j]) + alpha * (float(l1[j]) - float(l0[j])) for j in range(3))
        return q_xyzw, loc

    for pbone in arm_obj.pose.bones:
        pbone.rotation_mode = "QUATERNION"
        pbone.matrix_basis = Matrix.Identity(4)

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    action = bpy.data.actions.new(action_name)
    arm_obj.animation_data.action = action

    edit_prefs = context.preferences.edit
    prev_interp = edit_prefs.keyframe_new_interpolation_type
    edit_prefs.keyframe_new_interpolation_type = "LINEAR"
    try:
        frame_max = 1
        for i in range(steps + 1):
            t = duration * i / steps
            frame = 1 + int(round(t * fps))
            frame_max = max(frame_max, frame)
            deg = channel.sample_degrees(t)
            weight = 0.0 if amp < 1e-6 else (deg / amp)

            # ACL / mid open quats are bone-local. Build a "parent at rest" world
            # pose per bone and key that as matrix_basis; Blender parenting composes
            # keyed ancestors. Do NOT compensate against other keyed parents here —
            # that fights nested mechanisms (combined AEROUP: hinge=boneWingRF while
            # rear struts parent under bonewing) and causes raise→drop→tilt artifacts.
            worlds = {}
            for bone, oq in bone_quats:
                acl_sample = sample_acl_drive(acl_drive_by_bone.get(bone), t)
                if acl_sample is not None:
                    sample_q, sample_loc = acl_sample
                    worlds[bone] = local_aim_world(
                        bone, sample_q, 1.0, direct_loc=sample_loc
                    )
                else:
                    worlds[bone] = local_aim_world(bone, oq, weight)

            for pbone in arm_obj.pose.bones:
                pbone.matrix_basis = Matrix.Identity(4)

            for bone, _oq in bone_quats:
                pb = arm_obj.pose.bones[bone]
                pb.rotation_mode = "QUATERNION"
                rest = rest_pose[bone]
                pb.matrix_basis = rest.inverted() @ worlds[bone]
                pb.keyframe_insert("location", frame=frame)
                pb.keyframe_insert("rotation_quaternion", frame=frame)
    finally:
        edit_prefs.keyframe_new_interpolation_type = prev_interp

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


def _drive_has_motion(drv) -> bool:
    """True when a drive has usable rotation or translation to key."""
    from .parsing.mojo_clipd import quat_angle_deg

    if getattr(drv, "axis_from_mid", False):
        return True
    if quat_angle_deg(tuple(getattr(drv, "open_quat", (0, 0, 0, 1)))) >= 0.5:
        return True
    if str(getattr(drv, "quat_source", "") or "").startswith("acl_"):
        locs = list(getattr(drv, "acl_locs", None) or [])
        if any(max(abs(float(c)) for c in loc) > 1e-5 for loc in locs):
            return True
    loc = getattr(drv, "open_loc", None)
    if loc is not None and max(abs(float(c)) for c in loc) > 1e-5:
        return True
    return False


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

    _SKIP_TOKENS = ("piston", "strut", "aim", "spindle", "blade")

    @classmethod
    def _skip_helper_bone(cls, bone_name: str) -> bool:
        """Skip door strut/piston helpers; never skip active-aero ``boneWingHinge*``."""
        low = (bone_name or "").lower()
        if "winghinge" in low or low.startswith("bonewing"):
            return False
        if low in ("bonewing", "bonewinglf", "bonewingrf"):
            return False
        return any(tok in low for tok in cls._SKIP_TOKENS)

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

        # FH5 .gr2 / FH6 Mojo: no file picker. Legacy .dae picker only if neither exists.
        if detect_anim_pipeline(self._car_root) != "none":
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def _collect_dae(self, context):
        """Legacy Collada picker only — FH5 GR2 uses gr2dump, not Divine."""
        if self.files:
            dae_list = [os.path.join(self.directory, f.name)
                        for f in self.files if f.name.lower().endswith(".dae")]
        elif self.directory:
            dae_list = sorted(glob.glob(os.path.join(self.directory, "*.dae")))
        else:
            dae_list = []
        if not dae_list:
            if detect_anim_pipeline(self._car_root) != "none":
                return [], None
            self.report(
                {"ERROR"},
                "No Animations\\*.gr2 or Mojo .clipd on this car. "
                "Pick legacy .dae files if you already converted them.",
            )
            return None, None
        return dae_list, None

    def _execute_mojo(self, context, rest_by_bone):
        """FH6 Autovista: Mojo ACL 2.1 only (never FH5 GR2, never mid fallback)."""
        from .parsing.mojo_clipd import (
            _is_door_bone,
            _is_panel_bone,
            action_name_from_event,
            quat_angle_deg,
            skeld_panel_root_name,
        )
        from .parsing.mojo_skeld import (
            skeld_aero_mechanism_roots,
            skeld_door_mounted_mirror_bones,
            skeld_subtree_bones,
        )

        hinges, err = load_mojo_hinges(self._car_root)
        if err:
            self.report({"ERROR"}, f"Mojo animation: {err}")
            return {"CANCELLED"}

        # Explicit development build only; never part of normal addon evaluation.
        oracle_hits = 0
        oracle_src = ""
        if development_enabled():
            try:
                from .parsing.mojo_pose_oracle import (
                    apply_pose_oracle_to_hinges,
                    discover_pose_oracle,
                    load_pose_oracle,
                    pose_oracle_enabled,
                )
            except ImportError:
                pass
            else:
                try:
                    if pose_oracle_enabled():
                        opath = discover_pose_oracle(self._car_root)
                        if opath is not None:
                            oracle = load_pose_oracle(opath)
                            if oracle:
                                oracle_hits = apply_pose_oracle_to_hinges(hinges, oracle)
                                oracle_src = str(opath)
                                print(
                                    f"Forza Mojo POSE ORACLE: {oracle_hits} bone(s) from "
                                    f"{opath.name} (source={oracle.get('source')}) — DEBUG only"
                                )
                except Exception as exc:
                    print(f"Forza Mojo pose oracle skipped ({exc})")

        nodes = load_mojo_skeld_nodes(self._car_root)
        skeld_raw = skeld_blender_rests(nodes) if nodes else {}
        # Authored .skeld rests only (scaffold). *HingeUpper* is strut joint, not Mode A pivot.
        skeld_rests = apply_car_hinge_rests(
            skeld_raw, nodes, car_objs=self._car_objs
        )

        animated = set()
        drive_for = {}
        quat_for = {}
        attach_target = {}
        for h in hinges:
            panel = h.bone_hint
            if not panel:
                continue
            if self.only_default_parts and self._skip_helper_bone(panel):
                continue
            # FH5 GR2 OrientationCurves key the **panel** (boneDoorLF), not root_*.
            # Mojo mid is authored for that same binding bone. Keying root_* applied
            # the panel quat in the scaffold frame — doors never looked right on FH6.
            # Match GR2: drive the panel; keep root at authored rest (hierarchy parent).
            # Mode B (multi-sibling aero): still key each panel, not a shared root.
            root = skeld_panel_root_name(nodes, panel) if nodes else None
            real_mid_drives = [
                drv
                for drv in (getattr(h, "drives", None) or [])
                if drv.bone
                and getattr(drv, "axis_from_mid", False)
                and quat_angle_deg(tuple(getattr(drv, "open_quat", (0, 0, 0, 1)))) > 0.5
            ]
            # Multiple *panel* siblings (boneWingLF + boneWingRF), not hinge helpers.
            panel_mid_drives = [
                drv
                for drv in real_mid_drives
                if _is_panel_bone(drv.bone) and "hinge" not in (drv.bone or "").lower()
            ]
            multi_panel = len(panel_mid_drives) > 1 and not _is_door_bone(panel)
            door_panel = _is_door_bone(panel)
            acl_channel = str(getattr(h, "quat_source", "") or "").startswith(
                "acl_"
            ) or any(
                str(getattr(drv, "quat_source", "") or "").startswith("acl_")
                for drv in (getattr(h, "drives", None) or [])
            )
            # Never redirect an ACL panel pose onto root_bone*.
            # Also: only Mode-A-key root_* when that root appears in the clip
            # binding (file-authored). Skeld root_* is hierarchy scaffold —
            # AMG root_bonewing is not bound and must stay at rest.
            bound_names = {
                b for b in (getattr(h, "bound_bones", None) or []) if b
            }
            for drv in getattr(h, "drives", None) or []:
                bn = getattr(drv, "bone", None)
                if bn:
                    bound_names.add(bn)
            root_is_bound = bool(root and root in bound_names)
            drive = (
                panel
                if (
                    acl_channel
                    or multi_panel
                    or door_panel
                    or not root
                    or not root_is_bound
                )
                else root
            )
            if panel in skeld_rests:
                rest_by_bone[panel] = skeld_rests[panel]
            if drive in skeld_rests:
                rest_by_bone[drive] = skeld_rests[drive]
            if root and root in skeld_rests:
                rest_by_bone[root] = skeld_rests[root]
            if panel not in rest_by_bone and drive not in rest_by_bone:
                continue
            animated.add(panel)
            if drive != panel:
                animated.add(drive)
            if root and root != drive:
                animated.add(root)
            # Door/GR2: meshes follow keyed panel. Multi-panel aero: own bone.
            # Non-door Mode A (trunk etc.): still follow keyed root when drive is root.
            attach_target[panel] = (
                panel
                if (acl_channel or multi_panel or door_panel or not root_is_bound)
                else drive
            )
            if root and multi_panel:
                attach_target[root] = root
            # Filter helpers to this mechanism root (not the Mode-B panel alone).
            mech_anchor = root or drive
            mech_bones = None
            if nodes and mech_anchor:
                mech_bones = set(skeld_subtree_bones(nodes, [mech_anchor]))
                mech_bones.add(mech_anchor)
                mech_bones.add(panel)
            oq = tuple(getattr(h, "open_quat", (0.0, 0.0, 0.0, 1.0)))
            # Boundary: clip mid sense only — never hang_pick conjugate rewrite.
            kind = getattr(h, "quat_source", "") or getattr(h, "mechanism", "") or "mid"
            open_loc = getattr(h, "open_loc", None)
            h.mechanism = kind
            h.open_quat = oq
            acl_helpers = str(kind).startswith("acl_") or any(
                str(getattr(drv, "quat_source", "") or "").startswith("acl_")
                for drv in (getattr(h, "drives", None) or [])
            )
            if multi_panel:
                bone_quats = []
                for drv in panel_mid_drives:
                    bn = drv.bone
                    if bn not in rest_by_bone and bn in skeld_rests:
                        rest_by_bone[bn] = skeld_rests[bn]
                    if bn not in rest_by_bone:
                        continue
                    animated.add(bn)
                    attach_target[bn] = bn
                    qo = tuple(getattr(drv, "open_quat", (0.0, 0.0, 0.0, 1.0)))
                    bone_quats.append((bn, qo))
                for drv in real_mid_drives:
                    bn = drv.bone
                    if any(bn == b for b, _ in bone_quats):
                        continue
                    if (
                        not acl_helpers
                        and mech_bones is not None
                        and bn not in mech_bones
                    ):
                        continue
                    if bn in skeld_rests:
                        rest_by_bone[bn] = skeld_rests[bn]
                    if bn not in rest_by_bone:
                        continue
                    animated.add(bn)
                    attach_target[bn] = bn
                    qo = tuple(getattr(drv, "open_quat", (0.0, 0.0, 0.0, 1.0)))
                    bone_quats.append((bn, qo))
                # ACL may add hinge/piston tracks that mid never created as panel drives.
                if acl_helpers:
                    for drv in getattr(h, "drives", None) or []:
                        bn = drv.bone
                        if not bn or any(bn == b for b, _ in bone_quats):
                            continue
                        if not _drive_has_motion(drv):
                            continue
                        if bn in skeld_rests:
                            rest_by_bone[bn] = skeld_rests[bn]
                        if bn not in rest_by_bone:
                            continue
                        animated.add(bn)
                        attach_target[bn] = bn
                        bone_quats.append(
                            (bn, tuple(getattr(drv, "open_quat", (0.0, 0.0, 0.0, 1.0))))
                        )
                # Hoodvent panel→hinge quat copy retired: invents motion on bones
                # that have no authored mid/ACL track. Only key file-backed drives.
                if not bone_quats:
                    continue
            else:
                bone_quats = [(drive, oq)]
                # Rear wing mid-only: helper mids under a keyed root compounded into
                # strut fly-aways. ACL 2.1 supplies real per-helper open poses — key them.
                panel_l = (panel or "").lower()
                drive_l = (drive or "").lower()
                acl_helpers = any(
                    str(getattr(drv, "quat_source", "") or "").startswith("acl_")
                    for drv in (getattr(h, "drives", None) or [])
                )
                skip_helper_mids = (
                    panel_l == "bonewing"
                    or drive_l in ("bonewing", "root_bonewing")
                ) and not acl_helpers
                if not skip_helper_mids:
                    for drv in getattr(h, "drives", None) or []:
                        bn = drv.bone
                        if not bn:
                            continue
                        if bn == panel:
                            continue
                        # ACL tracks are authoritative; do not clip to skeld subtree.
                        if (
                            not acl_helpers
                            and mech_bones is not None
                            and bn not in mech_bones
                        ):
                            continue
                        if self.only_default_parts and self._skip_helper_bone(bn):
                            continue
                        # Skip empty helper mids (already filtered in hinge_channels).
                        # Keep translation-only ACL aims (e.g. boneWingHingeLowerAim).
                        if not _drive_has_motion(drv):
                            continue
                        if bn in skeld_rests:
                            rest_by_bone[bn] = skeld_rests[bn]
                        if bn not in rest_by_bone:
                            continue
                        animated.add(bn)
                        attach_target[bn] = bn
                        qo = tuple(getattr(drv, "open_quat", (0.0, 0.0, 0.0, 1.0)))
                        bone_quats.append((bn, qo))
            # Production: ACL / authored mid bone-local quats only. Do not remap
            # door rests onto Local +Y (that rewrote skeld orientation).
            acl_pose = str(kind).startswith("acl_") or any(
                str(getattr(drv, "quat_source", "") or "").startswith("acl_")
                for drv in (getattr(h, "drives", None) or [])
            )
            if door_panel and bone_quats and oracle_hits > 0:
                kind = f"{kind}+pose_oracle" if kind else "pose_oracle"
            elif door_panel and bone_quats and acl_pose:
                kind = f"{kind}+acl" if kind and "+acl" not in kind else (kind or "acl_open")
            drive_for[id(h)] = drive
            locs = {}
            # Mid open_loc is bone-local ΔT; ACL per-sample locs are applied in bake.
            if open_loc is not None and bone_quats:
                locs[bone_quats[0][0]] = tuple(open_loc)
            for drv in getattr(h, "drives", None) or []:
                bn = getattr(drv, "bone", None)
                dloc = getattr(drv, "open_loc", None)
                if (
                    bn
                    and dloc is not None
                    and any(bn == b for b, _ in bone_quats)
                    and max(abs(float(c)) for c in dloc) > 1e-5
                ):
                    locs[bn] = tuple(dloc)
            quat_for[id(h)] = (bone_quats, kind, locs)

            if nodes and _is_door_bone(panel):
                for mb in skeld_door_mounted_mirror_bones(nodes, panel):
                    if mb in skeld_rests:
                        rest_by_bone[mb] = skeld_rests[mb]
                        animated.add(mb)

        if nodes:
            for anchor in skeld_aero_mechanism_roots(nodes):
                for bone in skeld_subtree_bones(nodes, [anchor]):
                    if bone in skeld_rests:
                        rest_by_bone[bone] = skeld_rests[bone]
                        animated.add(bone)

        hinges = [h for h in hinges if id(h) in drive_for]
        if not animated or not hinges:
            self.report(
                {"ERROR"},
                "Mojo clips found, but no bound bones matched this car's tagged parts. "
                "Re-import the .carbin so objects keep forza_bone tags.",
            )
            return {"CANCELLED"}

        self._remove_existing_rig(self._car_objs)
        parent_map = skeld_parent_map(self._car_root, animated, nodes=nodes)
        car_name = os.path.basename(os.path.normpath(self._car_root))
        arm_obj = build_rig(
            context, f"{car_name}_anim", sorted(animated), rest_by_bone, parent_map
        )
        context.view_layer.update()
        rest_pose = read_rest_pose(arm_obj)
        attached = attach_objects(
            arm_obj, self._car_objs, animated, rest_pose, attach_target=attach_target
        )

        baked = 0
        span_end = context.scene.frame_start
        src_note = {}
        for h in hinges:
            name = action_name_from_event(h.event, h.bone_hint)
            if any(
                t.name == name
                for t in (
                    arm_obj.animation_data.nla_tracks if arm_obj.animation_data else []
                )
            ):
                continue
            drive = drive_for.get(id(h), h.bone_hint)
            packed = quat_for.get(
                id(h),
                ([(drive, getattr(h, "open_quat", (0, 0, 0, 1)))], "", {}),
            )
            if len(packed) == 2:
                bone_quats, kind = packed
                bone_locs = {}
            else:
                bone_quats, kind, bone_locs = packed
            action, info = bake_mojo_action(
                context,
                arm_obj,
                h,
                name,
                drive_bone=drive,
                open_quat=bone_quats[0][1] if bone_quats else None,
                bone_quats=bone_quats,
                bone_locs=bone_locs,
                rest_by_bone=rest_by_bone,
                rest_pose=rest_pose,
                parent_map=parent_map,
            )
            if action is not None:
                baked += 1
                src_note[h.source] = src_note.get(h.source, 0) + 1
                if kind:
                    src_note[kind] = src_note.get(kind, 0) + 1
                helpers = max(0, len(bone_quats) - 1)
                if helpers:
                    src_note["helpers"] = src_note.get("helpers", 0) + helpers
                if isinstance(info, int):
                    span_end = max(span_end, info)

        if span_end > context.scene.frame_end:
            context.scene.frame_end = span_end

        for pb in arm_obj.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
        context.view_layer.update()

        try:
            bpy.app.timers.register(
                functools.partial(_deferred_rebind, arm_obj.name), first_interval=0.0
            )
        except (RuntimeError, ValueError, TypeError):
            rebind_strip_slots(arm_obj)

        if development_enabled():
            try:
                from .parsing.mojo_bake_debug import (
                    emit_mojo_bake_debug,
                    mojo_debug_enabled,
                )

                if mojo_debug_enabled():
                    skeld_path = discover_mojo_skeld(self._car_root)
                    skeld_mirror: list[str] = []
                    if nodes:
                        for h in hinges:
                            panel = h.bone_hint
                            if panel and _is_door_bone(panel):
                                skeld_mirror.extend(
                                    skeld_door_mounted_mirror_bones(nodes, panel)
                                )
                        skeld_mirror = sorted(set(skeld_mirror))
                    emit_mojo_bake_debug(
                        self._car_root,
                        hinges,
                        drive_for=drive_for,
                        quat_for=quat_for,
                        attach_target=attach_target,
                        rest_pose=rest_pose,
                        rest_by_bone=rest_by_bone,
                        animated=animated,
                        car_objs=self._car_objs,
                        arm_obj=arm_obj,
                        skeld_info={
                            "path": skeld_path,
                            "loaded": nodes is not None,
                            "node_count": len(nodes) if nodes else 0,
                            "named_count": sum(1 for n in nodes if n.name) if nodes else 0,
                            "door_mounted_mirror_bones": skeld_mirror,
                            "mirror_on_armature": [
                                b for b in skeld_mirror if b in animated
                            ],
                        },
                    )
            except Exception as exc:
                print(f"Forza Mojo debug failed ({exc})")

        cfg_note = ""
        try:
            from .parsing.mojo_config import load_mojo_config

            cfg = load_mojo_config(self._car_root)
            if cfg is not None and cfg.autovista_events:
                cfg_note = f", MojoConfig={'+'.join(cfg.autovista_events)}"
        except Exception:
            cfg_note = ""
        detail = ", ".join(f"{n}x{s}" for s, n in sorted(src_note.items()))
        oracle_note = ""
        if oracle_hits > 0:
            oracle_note = f", POSE_ORACLE={oracle_hits}b"
        self.report(
            {"INFO"},
            f"Mojo v2.25.0: rigged {attached} parts, baked {baked} clip(s) onto '{arm_obj.name}' "
            f"({detail or 'no samples'}{cfg_note}{oracle_note}). "
            f"Unmute DOOROPEN_*/DOORCLOSE_* and scrub strip range.",
        )
        return {"FINISHED"}

    def _execute_gr2(self, context, rest_by_bone):
        """FH5 Autovista: gr2dump local 4×4 matrices → bake_action only."""
        from .parsing.gr2_anim import (
            doc_has_matrix_tracks,
            dump_all_animation_gr2,
            find_skeleton_gr2_file,
            require_gr2dump_runtime,
            skeleton_bones_from_gr2,
        )

        dump_exe, runtime_err = require_gr2dump_runtime()
        if runtime_err:
            self.report({"ERROR"}, runtime_err)
            return {"CANCELLED"}

        skel_path = find_skeleton_gr2_file(self._car_root)
        skel_bones = (
            skeleton_bones_from_gr2(skel_path, dump_exe) if skel_path else None
        )
        if not skel_bones:
            self.report(
                {"ERROR"},
                "FH5 GR2 matrix bake needs a skeleton .gr2 under this car "
                "(e.g. scene/*_skeleton.gr2).",
            )
            return {"CANCELLED"}

        dumps = dump_all_animation_gr2(self._car_root, dump_exe)
        matrix_clips = [
            (action, doc) for action, _path, doc in dumps if doc_has_matrix_tracks(doc)
        ]
        if not matrix_clips:
            self.report(
                {"ERROR"},
                "gr2dump produced no matrix tracks. Rebuild tools/gr2dump "
                "(format gr2dump_v2_matrix) and ensure .NET 8 is installed.",
            )
            return {"CANCELLED"}

        return self._execute_gr2_matrix(
            context, rest_by_bone, skel_bones, matrix_clips
        )

    def _execute_gr2_matrix(self, context, rest_by_bone, skel_bones, matrix_clips):
        """Divine-parity bake: gr2dump local 4×4 samples → bake_action (same as .dae)."""
        parsed = []
        animated = set()
        for action_name, doc in matrix_clips:
            ca = collada_anim_from_gr2_doc(doc, skel_bones)
            parsed.append((action_name, ca))
            for bn in rest_by_bone:
                cid = ca.resolve(bn)
                if cid is not None and _affected(ca, cid):
                    animated.add(bn)

        if self.only_default_parts:
            animated = {
                b
                for b in animated
                if not any(tok in b.lower() for tok in self._SKIP_TOKENS)
            }
        if not animated:
            self.report(
                {"ERROR"},
                "No GR2 matrix tracks matched this car's tagged parts.",
            )
            return {"CANCELLED"}

        self._remove_existing_rig(self._car_objs)
        parent_map = compute_parent_map(parsed[0][1], animated) if parsed else {}
        car_name = os.path.basename(os.path.normpath(self._car_root))
        arm_obj = build_rig(
            context, f"{car_name}_anim", sorted(animated), rest_by_bone, parent_map
        )
        context.view_layer.update()
        rest_pose = read_rest_pose(arm_obj)
        attached = attach_objects(arm_obj, self._car_objs, animated, rest_pose)

        baked = 0
        span_end = context.scene.frame_start
        for name, ca in parsed:
            action, info = bake_action(
                context,
                arm_obj,
                ca,
                animated,
                rest_by_bone,
                rest_pose,
                parent_map,
                name,
            )
            if action is not None:
                baked += 1
                if isinstance(info, int):
                    span_end = max(span_end, info)

        if span_end > context.scene.frame_end:
            context.scene.frame_end = span_end
        for pb in arm_obj.pose.bones:
            pb.matrix_basis = Matrix.Identity(4)
        context.view_layer.update()
        try:
            bpy.app.timers.register(
                functools.partial(_deferred_rebind, arm_obj.name), first_interval=0.0
            )
        except (RuntimeError, ValueError, TypeError):
            rebind_strip_slots(arm_obj)

        self.report(
            {"INFO"},
            f"GR2 v2.25.0: rigged {attached} parts, baked {baked} matrix clip(s) onto "
            f"'{arm_obj.name}' (FH5 matrix). Unmute DOOROPEN_* and scrub.",
        )
        return {"FINISHED"}


    def execute(self, context):
        if not getattr(self, "_car_root", None):
            resolved = self._resolve_car(context)
            if resolved is None:
                return {"CANCELLED"}
            self._car_root, self._car_objs = resolved

        # Exact rest matrices captured at import time (our Blender space).
        rest_by_bone = {}
        for o in self._car_objs:
            bn = o.get(PROP_BONE)
            rest = o.get(PROP_BONE_REST)
            if bn and rest and bn not in rest_by_bone:
                rest_by_bone[bn] = _flat_to_matrix(rest)

        pipeline = detect_anim_pipeline(self._car_root)
        if pipeline == "fh5_gr2":
            return self._execute_gr2(context, rest_by_bone)
        if pipeline == "fh6_mojo":
            return self._execute_mojo(context, rest_by_bone)

        dae_list, tmpdir = self._collect_dae(context)
        if dae_list is None:
            return {"CANCELLED"}
        if not dae_list:
            self.report({"ERROR"}, "No animations found for this car.")
            return {"CANCELLED"}

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
