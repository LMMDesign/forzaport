"""FH6 Mojo ACL 2.1 decompress (game eval path for Autovista clips).

BSI ``ACLAnimationData`` embeds standard nfrechette ACL ``compressed_tracks``.
AMG live constellation proved OPEN last-sample rotations match evaluated poses
to ~0.0001°. There is no mid invent fallback — ACL is required.
"""
from __future__ import annotations

import ctypes
import functools
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

ACL_ANIMATION_DATA_HASH = 0xCBE977A85A54EB5E


class MojoAclError(RuntimeError):
    """Fatal: FH6 Autovista motion cannot be evaluated without ACL 2.1."""


def _dll_candidates() -> list[Path]:
    here = Path(__file__).resolve().parent
    tools = here.parent / "tools" / "acl"
    return [
        tools / "forza_acl.dll",
        tools / "libforza_acl.so",
        tools / "libforza_acl.dylib",
    ]


_dll = None
_dll_error: str | None = None


def load_acl_dll():
    """Load native ACL 2.1 helper. Returns ctypes CDLL or raises RuntimeError."""
    global _dll, _dll_error
    if _dll is not None:
        return _dll
    if _dll_error is not None:
        raise RuntimeError(_dll_error)
    last = "forza_acl.dll not found"
    for path in _dll_candidates():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
            lib.forza_acl_info.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_int),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_float),
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.forza_acl_info.restype = ctypes.c_int
            lib.forza_acl_decompress_sample.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_float),
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_int),
            ]
            lib.forza_acl_decompress_sample.restype = ctypes.c_int
            _dll = lib
            return _dll
        except OSError as exc:
            last = f"{path}: {exc}"
    _dll_error = last
    raise RuntimeError(last)


def _parse_object(data: bytes, offset: int) -> dict | None:
    if offset + 12 > len(data):
        return None
    body_size, type_hash = struct.unpack_from("<IQ", data, offset)
    body_start = offset + 12
    body_end = body_start + body_size
    if body_size == 0 or body_end > len(data):
        return None
    fields = []
    pos = body_start
    while pos < body_end:
        if pos + 4 > body_end:
            return None
        size = struct.unpack_from("<I", data, pos)[0]
        payload_start = pos + 4
        payload_end = payload_start + size
        if payload_end > body_end:
            return None
        fields.append(data[payload_start:payload_end])
        pos = payload_end
    if pos != body_end:
        return None
    return {"offset": offset, "type_hash": type_hash, "fields": fields}


def _read_vector(payload: bytes, stride: int) -> tuple[bytes, int] | None:
    if len(payload) < 4:
        return None
    count = struct.unpack_from("<I", payload, 0)[0]
    if count == 0 or count > 1_000_000:
        return None
    if len(payload) >= 8:
        capacity = struct.unpack_from("<I", payload, 4)[0]
        if count <= capacity and 8 + count * stride == len(payload):
            return payload[8:], count
    if 4 + count * stride == len(payload):
        return payload[4:], count
    return None


def _scalar_u32(fields: list[bytes], index: int) -> int:
    p = fields[index] if index < len(fields) else b""
    return struct.unpack_from("<I", p)[0] if len(p) >= 4 else 0


def _scalar_f32(fields: list[bytes], index: int) -> float:
    p = fields[index] if index < len(fields) else b""
    return struct.unpack_from("<f", p)[0] if len(p) >= 4 else 0.0


@dataclass
class AclClip:
    """One BSI ACLAnimationData transform buffer + resolved bone names."""

    offset: int
    transform: bytes
    bone_ids: list[int]
    bone_names: list[str | None]
    num_samples: int
    duration: float
    anim_type: int = 0
    version_number: int = 0

    def resolved_bones(self) -> list[str]:
        return [n for n in self.bone_names if n]


@dataclass
class AclSamplePose:
    bone: str
    rotation_xyzw: tuple[float, float, float, float]
    translation_xyz: tuple[float, float, float]
    scale_xyz: tuple[float, float, float]


def extract_acl_clips(data: bytes, known_hashes: dict[int, str] | None = None) -> list[AclClip]:
    """Scan clipd bytes for ACLAnimationData objects (FTS field map)."""
    known_hashes = known_hashes or {}
    needle = struct.pack("<Q", ACL_ANIMATION_DATA_HASH)
    out: list[AclClip] = []
    start = 0
    while True:
        i = data.find(needle, start)
        if i < 0:
            break
        start = i + 1
        header = i - 4
        if header < 0:
            continue
        obj = _parse_object(data, header)
        if not obj or obj["type_hash"] != ACL_ANIMATION_DATA_HASH:
            continue
        fields: list[bytes] = obj["fields"]
        if len(fields) < 11:
            continue
        vec = _read_vector(fields[0], 1)
        if not vec or not vec[0]:
            continue
        transform, _ = vec
        ids_raw = _read_vector(fields[6], 8) if len(fields) > 6 else None
        if ids_raw:
            bone_ids = list(struct.unpack("<" + "Q" * ids_raw[1], ids_raw[0]))
        else:
            ids32 = _read_vector(fields[6], 4) if len(fields) > 6 else None
            bone_ids = list(struct.unpack("<" + "I" * ids32[1], ids32[0])) if ids32 else []
        names = [known_hashes.get(int(h)) for h in bone_ids]
        out.append(
            AclClip(
                offset=obj["offset"],
                transform=transform,
                bone_ids=[int(h) for h in bone_ids],
                bone_names=names,
                num_samples=_scalar_u32(fields, 9),
                duration=_scalar_f32(fields, 10),
                anim_type=fields[11][0] if len(fields) > 11 and fields[11] else 0,
                version_number=fields[12][0] if len(fields) > 12 and fields[12] else 0,
            )
        )
    return out


def _quat_angle_deg(q: tuple[float, float, float, float]) -> float:
    qw = max(-1.0, min(1.0, abs(q[3])))
    return 2.0 * math.degrees(math.acos(qw))


def decompress_sample(transform: bytes, sample_index: int = -1) -> list[tuple]:
    """Return per-track (rotation_xyzw, translation_xyz, scale_xyz)."""
    lib = load_acl_dll()
    ntracks = ctypes.c_int()
    nsamples = ctypes.c_int()
    rate = ctypes.c_float()
    dur = ctypes.c_float()
    ver = ctypes.c_int()
    buf = (ctypes.c_uint8 * len(transform)).from_buffer_copy(transform)
    rc = lib.forza_acl_info(
        buf, len(transform), ctypes.byref(ntracks), ctypes.byref(nsamples),
        ctypes.byref(rate), ctypes.byref(dur), ctypes.byref(ver),
    )
    if rc != 0:
        raise RuntimeError(f"forza_acl_info failed ({rc})")
    nt = int(ntracks.value)
    out = (ctypes.c_float * (nt * 12))()
    wrote = ctypes.c_int()
    rc = lib.forza_acl_decompress_sample(
        buf, len(transform), int(sample_index), out, nt * 12, ctypes.byref(wrote),
    )
    if rc != 0:
        raise RuntimeError(f"forza_acl_decompress_sample failed ({rc})")
    poses = []
    for ti in range(nt):
        base = ti * 12
        rot = (float(out[base]), float(out[base + 1]), float(out[base + 2]), float(out[base + 3]))
        tr = (float(out[base + 4]), float(out[base + 5]), float(out[base + 6]))
        sc = (float(out[base + 8]), float(out[base + 9]), float(out[base + 10]))
        poses.append((rot, tr, sc))
    return poses


def clip_endpoint_angles(clip: AclClip) -> tuple[float, float]:
    """Max rotation angle at first and last sample (degrees)."""
    first = decompress_sample(clip.transform, 0)
    last = decompress_sample(clip.transform, -1)
    a0 = max((_quat_angle_deg(p[0]) for p in first), default=0.0)
    a1 = max((_quat_angle_deg(p[0]) for p in last), default=0.0)
    return a0, a1


def clip_endpoint_translation(clip: AclClip) -> tuple[float, float]:
    """Max ‖translation‖ at first and last sample (metres)."""
    first = decompress_sample(clip.transform, 0)
    last = decompress_sample(clip.transform, -1)

    def _max_t(poses) -> float:
        best = 0.0
        for p in poses:
            tr = p[1]
            best = max(best, math.sqrt(tr[0] * tr[0] + tr[1] * tr[1] + tr[2] * tr[2]))
        return best

    return _max_t(first), _max_t(last)


@functools.lru_cache(maxsize=64)
def decompress_all_samples(transform: bytes) -> tuple[tuple[tuple, ...], ...]:
    """Decode every ACL sample once; cached for duplicate Autovista events."""
    lib = load_acl_dll()
    ntracks = ctypes.c_int()
    nsamples = ctypes.c_int()
    rate = ctypes.c_float()
    dur = ctypes.c_float()
    ver = ctypes.c_int()
    buf = (ctypes.c_uint8 * len(transform)).from_buffer_copy(transform)
    rc = lib.forza_acl_info(
        buf, len(transform), ctypes.byref(ntracks), ctypes.byref(nsamples),
        ctypes.byref(rate), ctypes.byref(dur), ctypes.byref(ver),
    )
    if rc != 0:
        raise RuntimeError(f"forza_acl_info failed ({rc})")
    return tuple(
        tuple(decompress_sample(transform, sample_index))
        for sample_index in range(int(nsamples.value))
    )


def open_pose_by_bone(clip: AclClip, *, opening: bool) -> dict[str, tuple]:
    """Map bone name → endpoint (rotation, translation, scale).

    OPEN clips: identity → open (use last sample).
    CLOSE clips: open → identity (use first sample).
    """
    sample = -1 if opening else 0
    # Event verb selects the endpoint. Do not flip from first/last amplitude —
    # that heuristic can pick the wrong twin polarity on non-door clips.
    poses = decompress_sample(clip.transform, sample)
    out: dict[str, tuple] = {}
    for i, name in enumerate(clip.bone_names):
        if not name or i >= len(poses):
            continue
        out[name] = poses[i]
    return out


def match_acl_clip(
    clips: Iterable[AclClip],
    bound_bones: list[str],
    *,
    opening: bool,
) -> AclClip | None:
    """Pick the ACL buffer whose bone set matches the event binding.

    Prefer an exact bone-set match (AMG fender+wing vs rear-only aero clips)
    over subset/superset fuzzy matches.
    """
    want = {b for b in bound_bones if b}
    if not want:
        return None
    candidates: list[tuple[float, AclClip]] = []
    for clip in clips:
        have = set(clip.resolved_bones())
        if not have:
            continue
        exact = have == want
        subset = want.issubset(have) or have.issubset(want)
        if not exact and not subset:
            panel_want = {
                b
                for b in want
                if "door" in b.lower()
                and not any(t in b.lower() for t in ("piston", "aim", "strut", "jamb"))
            }
            panel_have = {
                b
                for b in have
                if "door" in b.lower()
                and not any(t in b.lower() for t in ("piston", "aim", "strut", "jamb"))
            }
            if not panel_want or panel_want != panel_have:
                continue
        try:
            a0, a1 = clip_endpoint_angles(clip)
            t0, t1 = clip_endpoint_translation(clip)
        except RuntimeError:
            continue
        motion_r = max(a0, a1)
        motion_t = max(t0, t1)
        # Trunk/etc. can be translation-only (identity quats, metres of ΔT).
        if motion_r < 0.5 and motion_t < 1e-4:
            continue
        if opening:
            polarity = (a1 - a0) + 10.0 * (t1 - t0)
        else:
            polarity = (a0 - a1) + 10.0 * (t0 - t1)
        overlap = len(have & want) / max(len(want), 1)
        # Exact set >> overlap; keep polarity for OPEN/CLOSE twin buffers.
        exact_bonus = 1000.0 if exact else 0.0
        size_pen = -0.001 * abs(len(have) - len(want))
        score = (
            exact_bonus
            + polarity
            + 10.0 * overlap
            + 0.01 * motion_r
            + 1.0 * motion_t
            + size_pen
        )
        candidates.append((score, clip))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def apply_acl_to_drives(drives, bound_bones: list[str], clips: list[AclClip], opening: bool) -> str:
    """Apply matching ACL samples to drives, expanding to all ACL tracks.

    Raises ``MojoAclError`` when the DLL, clip match, or decompress fails.
    Mid decode is not a fallback.
    """
    if not clips:
        raise MojoAclError("clipd has no ACLAnimationData buffers")
    try:
        load_acl_dll()
    except RuntimeError as exc:
        raise MojoAclError(str(exc)) from exc
    bones = list(bound_bones) if bound_bones else [getattr(d, "bone", None) for d in (drives or [])]
    bones = [b for b in bones if b]
    if not bones:
        raise MojoAclError("ACL apply requires bound bone names")
    clip = match_acl_clip(clips, bones, opening=opening)
    if clip is None:
        raise MojoAclError(
            f"no ACL clip matches bound bones {bones} "
            f"({'open' if opening else 'close'})"
        )
    try:
        by_bone = open_pose_by_bone(clip, opening=opening)
        all_samples = decompress_all_samples(clip.transform)
    except RuntimeError as exc:
        raise MojoAclError(str(exc)) from exc
    if not by_bone:
        raise MojoAclError(f"ACL decompress returned no tracks for bones={bones}")

    # Lazy import avoids circular import at module load.
    from .mojo_clipd import BoneDrive

    by_name = {getattr(d, "bone", None): d for d in (drives or []) if getattr(d, "bone", None)}
    tag = "acl_open" if opening else "acl_close"
    primary_source = "acl_2.1"

    applied = 0
    # Prefer ACL track order so panel/helpers stay stable for primary selection.
    ordered_bones = [n for n in clip.bone_names if n and n in by_bone]
    for bone in ordered_bones:
        track_index = clip.bone_names.index(bone)
        q, tr, _sc = by_bone[bone]
        ang = _quat_angle_deg(q)
        axis = (0.0, 1.0, 0.0)
        if ang > 0.5:
            s = math.sin(math.radians(ang) * 0.5) or 1.0
            axis = (q[0] / s, q[1] / s, q[2] / s)
        drv = by_name.get(bone)
        if drv is None:
            drv = BoneDrive(
                bone=bone,
                amplitude_deg=ang,
                axis=axis,
                knots=[],
                source=primary_source,
                channel_index=len(drives),
                axis_from_mid=ang > 0.5,
                open_quat=q,
                quat_source=tag,
                open_loc=tr if max(abs(v) for v in tr) > 1e-7 else None,
            )
            drives.append(drv)
            by_name[bone] = drv
        else:
            drv.open_quat = q
            drv.quat_source = tag
            drv.amplitude_deg = ang
            drv.axis_from_mid = ang > 0.5
            drv.axis = axis
            drv.source = primary_source
            drv.open_loc = tr if max(abs(v) for v in tr) > 1e-7 else None
        drv.acl_quats = [sample[track_index][0] for sample in all_samples]
        drv.acl_locs = [sample[track_index][1] for sample in all_samples]
        drv.acl_sample_rate = (
            float(clip.num_samples - 1) / float(clip.duration)
            if clip.num_samples > 1 and clip.duration > 0.0
            else 0.0
        )
        drv.acl_duration = float(clip.duration)
        applied += 1

    if applied == 0:
        raise MojoAclError(f"ACL matched but applied 0 tracks for bones={bones}")
    return tag
