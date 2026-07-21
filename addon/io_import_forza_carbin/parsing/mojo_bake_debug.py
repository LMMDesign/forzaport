"""Gated Mojo bake diagnostics for door/mirror hinge QA.

Enable with ``FORZA_MOJO_DEBUG=1`` in the environment before launching Blender.
Writes ``forza_mojo_bake_debug.json`` under the car folder when writable, and
prints a short summary to the system console.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

from ..contract import (
    COORD_ROWS,
    PROP_BONE,
    PROP_CARBIN_BONE,
    PROP_MESH_NAME,
    PROP_MODEL_PATH,
    PROP_RIGID_BONE,
)

_C4 = None


def _c4():
    global _C4
    if _C4 is None:
        from mathutils import Matrix

        _C4 = Matrix(COORD_ROWS)
    return _C4


def mojo_debug_enabled() -> bool:
    v = os.environ.get("FORZA_MOJO_DEBUG", "").strip().lower()
    return v in ("1", "true", "on", "yes")


def _quat_axis_angle(
    q: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], float]:
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    w = max(-1.0, min(1.0, w))
    ang = 2.0 * math.degrees(math.acos(abs(w)))
    if ang < 0.5:
        return (0.0, 1.0, 0.0), 0.0
    if w < 0:
        x, y, z, w = -x, -y, -z, -w
    s = math.sin(math.radians(ang) * 0.5) or 1.0
    ax, ay, az = x / s, y / s, z / s
    ln = math.sqrt(ax * ax + ay * ay + az * az) or 1.0
    return (ax / ln, ay / ln, az / ln), ang


def local_aim_world_matrix(
    rest_pose: dict,
    rest_by_bone: dict,
    bone: str,
    oq: tuple[float, float, float, float],
    weight: float,
):
    """Same world pose as ``bake_mojo_action.local_aim_world`` at ``weight``."""
    from mathutils import Matrix, Quaternion

    C4 = _c4()
    rest = rest_pose[bone]
    r_b = rest_by_bone[bone]
    w_rest_f = C4.inverted() @ r_b
    w = max(0.0, min(1.0, weight))
    q_open = Quaternion((oq[3], oq[0], oq[1], oq[2])).normalized()
    q_id = Quaternion((1.0, 0.0, 0.0, 0.0))
    q = q_id.slerp(q_open, w)
    r_local = q.to_matrix().to_4x4()
    mc = w_rest_f @ r_local
    return (C4 @ mc @ r_b.inverted()) @ rest


def world_rotation_axis(
    rest_pose: dict,
    rest_by_bone: dict,
    bone: str,
    oq: tuple[float, float, float, float],
    weight: float = 1.0,
) -> dict[str, Any]:
    """Effective Blender-world rotation axis from rest → open at ``weight``."""
    rest = rest_pose[bone]
    world_open = local_aim_world_matrix(rest_pose, rest_by_bone, bone, oq, weight)
    delta = world_open.to_3x3() @ rest.to_3x3().inverted()
    q = delta.to_quaternion()
    axis = q.axis
    ang = math.degrees(q.angle)
    return {
        "axis": [round(axis.x, 6), round(axis.y, 6), round(axis.z, 6)],
        "angle_deg": round(ang, 3),
    }


def _is_door_event(event: str) -> bool:
    up = (event or "").upper()
    return "DOOROPEN" in up or "DOORCLOSE" in up


def _is_aero_event(event: str) -> bool:
    up = (event or "").upper()
    return "AEROUP" in up or "AERODOWN" in up


def _is_trunk_event(event: str) -> bool:
    up = (event or "").upper()
    return "TRUNKOPEN" in up or "TRUNKCLOSE" in up or "TRUNKUP" in up or "TRUNKDOWN" in up


def _mesh_is_anim_related(obj) -> bool:
    name = (obj.name or "").lower().replace(" ", "")
    bone = (obj.get(PROP_BONE) or "").lower()
    if any(tok in name for tok in ("door", "mirror", "wingmirror", "winglf", "wingrf", "wing_a", "winghinge", "wingstrut", "wingpiston")):
        return True
    if any(tok in bone for tok in ("door", "mirror", "wing", "bonewing")):
        return True
    return False


def _constraint_subtarget(obj, arm_obj) -> str | None:
    for con in getattr(obj, "constraints", ()) or ():
        if con.type != "CHILD_OF" or con.name != "Forza Anim":
            continue
        if getattr(con, "target", None) == arm_obj:
            return getattr(con, "subtarget", None) or None
    return None


def collect_mesh_attachments(
    car_objs,
    arm_obj,
    animated_bones: set[str],
    attach_target: dict[str, str],
) -> list[dict[str, Any]]:
    """Door/mirror mesh authored binds, attach targets, and rig constraint state."""
    animated = set(animated_bones)
    out: list[dict[str, Any]] = []
    for obj in car_objs or []:
        if not _mesh_is_anim_related(obj):
            continue
        tag = obj.get(PROP_BONE) or ""
        target = attach_target.get(tag, tag)
        rigged = target in animated
        sub = _constraint_subtarget(obj, arm_obj) if arm_obj else None
        out.append(
            {
                "object": obj.name,
                "forza_bone": tag or None,
                "forza_rigid_bone": obj.get(PROP_RIGID_BONE) or None,
                "forza_carbin_bone": obj.get(PROP_CARBIN_BONE) or None,
                "forza_model_path": obj.get(PROP_MODEL_PATH) or None,
                "forza_mesh_name": obj.get(PROP_MESH_NAME) or None,
                "attach_target": target,
                "rigged": rigged and sub is not None,
                "constraint_bone": sub,
                "skipped": not rigged,
            }
        )
    out.sort(key=lambda r: (r["object"] or "").lower())
    return out


def build_event_debug(
    hinge,
    *,
    drive: str,
    bone_quats: list[tuple[str, tuple[float, float, float, float]]],
    kind: str,
    rest_pose: dict,
    rest_by_bone: dict,
    attach_target: dict[str, str],
    mesh_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    panel = hinge.bone_hint or ""
    bound = list(getattr(hinge, "bound_bones", None) or [])
    keyed_names = [b for b, _ in bone_quats]
    unkeyed = [b for b in bound if b not in keyed_names]

    drive_oq = bone_quats[0][1] if bone_quats else tuple(
        getattr(hinge, "open_quat", (0.0, 0.0, 0.0, 1.0))
    )
    mid_axis, mid_ang = _quat_axis_angle(drive_oq)

    keyed_rows = []
    for bone, oq in bone_quats:
        ax, ang = _quat_axis_angle(oq)
        keyed_rows.append(
            {
                "bone": bone,
                "parent_attach": attach_target.get(bone, bone),
                "open_quat": list(round(c, 6) for c in oq),
                "axis_mid_space": [round(ax[0], 6), round(ax[1], 6), round(ax[2], 6)],
                "angle_mid_deg": round(ang, 3),
                "world_at_full_open": world_rotation_axis(
                    rest_pose, rest_by_bone, bone, oq, 1.0
                ),
            }
        )

    # Panel-tagged meshes only for this event side.
    side_tokens = []
    low = panel.lower()
    if low.endswith("lf") or low.endswith("_l"):
        side_tokens = ("lf", "l", "left")
    elif low.endswith("rf") or low.endswith("_r"):
        side_tokens = ("rf", "r", "right")

    meshes = mesh_rows
    if side_tokens:
        meshes = [
            m
            for m in mesh_rows
            if any(
                tok in (m.get("object") or "").lower()
                or tok in (m.get("forza_bone") or "").lower()
                for tok in side_tokens
            )
        ]

    short_event = (hinge.event or "").rsplit("/", 1)[-1]
    return {
        "event": short_event,
        "panel": panel,
        "drive": drive,
        "quat_source": kind,
        "amplitude_deg": round(float(hinge.amplitude_deg or 0.0), 3),
        "open_loc": (
            list(round(float(c), 6) for c in hinge.open_loc)
            if getattr(hinge, "open_loc", None)
            else None
        ),
        "drive_open_quat": list(round(c, 6) for c in drive_oq),
        "drive_axis_mid_space": [round(mid_axis[0], 6), round(mid_axis[1], 6), round(mid_axis[2], 6)],
        "drive_angle_mid_deg": round(mid_ang, 3),
        "hinge_world_at_full_open": world_rotation_axis(
            rest_pose, rest_by_bone, drive, drive_oq, 1.0
        ),
        "bound_bones": bound,
        "keyed_bones": keyed_rows,
        "unkeyed_bound": unkeyed,
        "meshes": meshes,
    }


def emit_mojo_bake_debug(
    car_root: str,
    hinges,
    *,
    drive_for: dict,
    quat_for: dict,
    attach_target: dict[str, str],
    rest_pose: dict,
    rest_by_bone: dict,
    animated: set[str],
    car_objs,
    arm_obj,
    skeld_info: dict[str, Any] | None = None,
) -> str | None:
    """Print + write JSON report; returns output path when written."""
    car = os.path.basename(os.path.normpath(car_root))
    mesh_rows = collect_mesh_attachments(
        car_objs,
        arm_obj,
        animated,
        attach_target,
    )

    events: list[dict[str, Any]] = []
    for h in hinges:
        ev_name = h.event or ""
        if not (
            _is_door_event(ev_name)
            or _is_aero_event(ev_name)
            or _is_trunk_event(ev_name)
        ):
            continue
        drive = drive_for.get(id(h), h.bone_hint)
        packed = quat_for.get(
            id(h),
            ([(drive, getattr(h, "open_quat", (0, 0, 0, 1)))], "", {}),
        )
        if len(packed) == 2:
            bone_quats, kind = packed
        else:
            bone_quats, kind, _locs = packed
        events.append(
            build_event_debug(
                h,
                drive=drive,
                bone_quats=bone_quats,
                kind=kind,
                rest_pose=rest_pose,
                rest_by_bone=rest_by_bone,
                attach_target=attach_target,
                mesh_rows=mesh_rows,
            )
        )

    animated_sorted = sorted(animated)
    report: dict[str, Any] = {
        "car": car,
        "car_root": os.path.normpath(car_root),
        "skeld": skeld_info or {},
        "animated_bones": animated_sorted,
        "animated_mirror_bones": [b for b in animated_sorted if "mirror" in b.lower()],
        "events": events,
        "all_anim_meshes": mesh_rows,
    }

    out_path = os.path.join(car_root, "forza_mojo_bake_debug.json")
    written: str | None = None
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
        written = out_path
    except OSError:
        try:
            import bpy

            tmp = os.path.join(bpy.app.tempdir or "", f"forza_mojo_bake_debug_{car}.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
                f.write("\n")
            written = tmp
        except (OSError, ImportError, TypeError, ValueError):
            written = None

    print(f"Forza Mojo debug ({car}): {len(events)} door/aero event(s)")
    for ev in events:
        w = ev["hinge_world_at_full_open"]
        mid = ev["drive_axis_mid_space"]
        unrigged = [m["object"] for m in ev["meshes"] if not m["rigged"]]
        print(
            f"  {ev['event']} drive={ev['drive']} src={ev['quat_source']} "
            f"mid_axis={mid} world_axis={w['axis']} ({w['angle_deg']:.1f}°) "
            f"keyed={[k['bone'] for k in ev['keyed_bones']]} "
            f"unkeyed_bound={ev['unkeyed_bound']}"
        )
        if unrigged:
            print(f"    NOT rigged: {', '.join(unrigged)}")
    if written:
        print(f"Forza Mojo debug: wrote {written}")
    return written
