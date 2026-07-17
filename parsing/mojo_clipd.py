"""Mojo .clipd reader (FH6 Autovista).

Car-agnostic: clip↔event↔bone wiring comes from file offsets + FNV bone
bindings. Production Autovista motion is ACL 2.1 only — never hang_pick /
mid invent / curve invent. Pass any car's ``.clipd`` (and optional bone-name
list from that car's modelbin / the shared Forza name catalog).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

FRAMES = 31
FPS = 30.0
DURATION = FRAMES / FPS  # ≈ 1.033 s

# Shared ForzaTech Autovista / Skel naming conventions (not per-car data).
# Used only to reverse FNV-1a64 hashes found in .clipd bindings / .skeld.
FORZA_BONE_CATALOG: tuple[str, ...] = (
    "boneDoorLF",
    "boneDoorRF",
    "boneDoorLR",
    "boneDoorRR",
    "root_boneDoorLF",
    "root_boneDoorRF",
    "root_boneDoorLR",
    "root_boneDoorRR",
    "boneHood",
    "root_boneHood",
    "boneTrunk",
    "root_boneTrunk",
    "boneRoof",
    "root_boneRoof",
    "boneWing",
    "boneWingL",
    "boneWingR",
    "boneWingPivot",
    "root_boneWing",
    "root_boneWingPivot",
    "boneDoorLFPistonLowerAimL",
    "boneDoorLFPistonUpperAimL",
    "boneDoorRFPistonLowerAimR",
    "boneDoorRFPistonUpperAimR",
    "boneDoorLRPistonLowerAimL",
    "boneDoorLRPistonUpperAimL",
    "boneDoorRRPistonLowerAimR",
    "boneDoorRRPistonUpperAimR",
    # AMG One / non-Aim piston helpers (ACL track ids)
    "boneDoorLFPistonUpperL",
    "boneDoorLFPistonUpperL001",
    "boneDoorLFPistonLowerL",
    "boneDoorRFPistonUpperR",
    "boneDoorRFPistonUpperR001",
    "boneDoorRFPistonLowerR",
    "boneDoorJambPistonLowerLF",
    "boneDoorJambPistonUpperLF",
    "boneDoorJambPistonLowerRF",
    "boneDoorJambPistonUpperRF",
    "boneDoorLFPistonLowerL001",
    "boneDoorRFPistonLowerR001",
    "boneHoodPistonAimL",
    "boneHoodPistonAimR",
    "boneTrunkPistonAimL",
    "boneTrunkPistonAimR",
    "boneTrunkLinerPistonUpperAimL",
    "boneTrunkLinerPistonUpperAimR",
    "boneTrunkLinerPistonLowerL",
    "boneTrunkLinerPistonLowerR",
    "boneWindowLF",
    "boneWindowRF",
    "boneWindowLR",
    "boneWindowRR",
    "boneMirrorL_001",
    "boneMirrorR_001",
    "boneMirrorC_001",
    # Unusual Autovista helper names seen on Countach / P1 / Jesko
    "boneDoorPartL",
    "boneDoorPartR",
    "boneAimStrutUpperLF",
    "boneAimStrutLowerLF",
    "boneAimStrutUpperRF",
    "boneAimStrutLowerRF",
    "bone_doorLFstrutLower_L",
    "bone_doorRFstrutLower_R",
    "bonehood",
    "bonewing",
    "bonewing_rotate",
)


def fnv1a64(text: str) -> int:
    h = 0xCBF29CE484222325
    for c in text.encode("utf-8"):
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def shared_payload_offset(data: bytes) -> int:
    if data[:4] != bytes.fromhex("cc14a4b1"):
        raise ValueError("bad Mojo magic")
    return struct.unpack_from("<I", data, 5)[0] + 9


def smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


@dataclass
class ChannelBlock:
    """Packed curve block after the float mid-section.

    ``table[4]`` is only the first slab (``count × 3`` u24 words). The live
    curve continues past that until the TLV schema before AudioEvent.
    """

    count_offset: int
    channel_counts: list[int]
    packed_offset: int
    packed: bytes
    lane_a_size: int
    lane_b_size: int

    @property
    def total_keys(self) -> int:
        return sum(self.channel_counts)


@dataclass
class CurveKnot:
    time: float  # seconds
    degrees: float
    tangent: float | None = None


@dataclass
class ClipDef:
    offset: int
    header: tuple[int, int, int, int]
    table: tuple[int, ...]
    payload: bytes
    notable_sins: list[tuple[int, float, float]] = field(default_factory=list)
    channels: ChannelBlock | None = None


@dataclass
class AudioEvent:
    offset: int
    name: str


@dataclass
class BoneBinding:
    offset: int
    hashes: list[int]
    names: list[str | None]



def _hermite(y0: float, y1: float, m0: float, m1: float, u: float) -> float:
    """Cubic Hermite on ``u∈[0,1]`` with end tangents ``m`` in value/unit-u."""
    u2 = u * u
    u3 = u2 * u
    h00 = 2.0 * u3 - 3.0 * u2 + 1.0
    h10 = u3 - 2.0 * u2 + u
    h01 = -2.0 * u3 + 3.0 * u2
    h11 = u3 - u2
    return h00 * y0 + h10 * m0 + h01 * y1 + h11 * m1


def _sample_knots(knots: list[CurveKnot], t: float) -> float:
    """Sample curve knots; Hermite when VT0 tangents are present, else linear."""
    if t <= knots[0].time:
        return knots[0].degrees
    if t >= knots[-1].time:
        return knots[-1].degrees
    for i in range(1, len(knots)):
        a, b = knots[i - 1], knots[i]
        if t > b.time:
            continue
        span = b.time - a.time
        u = 0.0 if span <= 0 else (t - a.time) / span
        if a.tangent is not None and b.tangent is not None:
            # Tangents stored as value-units (same scale as degrees); map to du.
            return _hermite(a.degrees, b.degrees, a.tangent * span, b.tangent * span, u)
        return _lerp(a.degrees, b.degrees, u)
    return knots[-1].degrees


@dataclass
class BoneDrive:
    """One bone's authored amp/axis/curve within an Autovista event."""

    bone: str
    amplitude_deg: float
    axis: tuple[float, float, float]
    sign: float = 1.0
    knots: list[CurveKnot] = field(default_factory=list)
    source: str = "synthetic_amp_ease"
    channel_index: int = 0
    euler_open: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis_from_mid: bool = False
    open_quat: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    quat_source: str = ""
    # Forza bone-local metres (nch=0 translation mids). None → rotation-only.
    open_loc: tuple[float, float, float] | None = None
    # Native ACL samples (game evaluation path). Empty → endpoint/ease fallback.
    acl_quats: list[tuple[float, float, float, float]] = field(default_factory=list)
    acl_locs: list[tuple[float, float, float]] = field(default_factory=list)
    acl_sample_rate: float = 0.0
    acl_duration: float = 0.0

    def sample_degrees(self, t: float, *, open_direction: bool, duration: float) -> float:
        if len(self.knots) >= 2:
            return _sample_knots(self.knots, t)
        u = 0.0 if duration <= 0 else max(0.0, min(1.0, t / duration))
        e = smoothstep(u)
        if open_direction:
            return self.amplitude_deg * e
        return self.amplitude_deg * (1.0 - e)


@dataclass
class HingeChannel:
    """One Autovista event → one NLA track (may drive several bones)."""

    event: str
    bone_hint: str | None
    amplitude_deg: float
    duration: float
    open_direction: bool  # True → 0→amp, False → amp→0
    source: str
    clip_index: int
    key_count: int | None = None
    packed_hex: str | None = None
    knots: list[CurveKnot] = field(default_factory=list)
    bound_bones: list[str] = field(default_factory=list)
    nch: int = 0
    mid_axes: list[tuple[float, float, float]] = field(default_factory=list)
    mechanism: str = ""
    channel_index: int = 0
    euler_open: tuple[float, float, float] = (0.0, 0.0, 0.0)
    axis: tuple[float, float, float] = (0.0, 1.0, 0.0)
    axis_from_mid: bool = False
    drives: list[BoneDrive] = field(default_factory=list)
    open_quat: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    quat_source: str = ""
    # Forza bone-local metres from nch=0 translation mids (trunk etc.).
    open_loc: tuple[float, float, float] | None = None

    def sample_degrees(self, t: float) -> float:
        """Scalar angle for the primary drive (event panel)."""
        if self.drives:
            return self.drives[0].sample_degrees(
                t, open_direction=self.open_direction, duration=self.duration
            )
        if len(self.knots) >= 2:
            return _sample_knots(self.knots, t)
        u = 0.0 if self.duration <= 0 else max(0.0, min(1.0, t / self.duration))
        e = smoothstep(u)
        if self.open_direction:
            return self.amplitude_deg * e
        return self.amplitude_deg * (1.0 - e)

    def keys(self, steps: int | None = None) -> list[tuple[float, float]]:
        if self.knots and steps is None:
            return [(k.time, k.degrees) for k in self.knots]
        n = steps if steps is not None else FRAMES
        out = []
        for i in range(n + 1):
            t = self.duration * i / n
            out.append((t, self.sample_degrees(t)))
        return out


def quat_angle_deg(q: tuple[float, float, float, float]) -> float:
    qw = max(-1.0, min(1.0, q[3]))
    return 2.0 * math.degrees(math.acos(abs(qw)))


@dataclass
class ClipEventLink:
    """One Autovista action: event + owning clipdef + bound bones (from file)."""

    event: AudioEvent
    clip: ClipDef
    clip_index: int
    binding: BoneBinding | None
    bone_names: list[str]


@dataclass
class MojoClipPack:
    clipdefs: list[ClipDef]
    events: list[AudioEvent]
    bindings: list[BoneBinding]
    acl_clips: list = field(default_factory=list)

    def link_events(self) -> list[ClipEventLink]:
        """Pair each AudioEvent to its clipdef and bone binding by file offset.

        Clipdef: nearest record with ``offset < event.offset``.
        Binding: nearest table with ``offset`` in ``[event-2500, event+200]``
        (Autovista wiring sits beside the event string — works for 2–12 event cars
        including Integra Type R where front/rear doors share ``_L``/``_R`` labels).
        """
        out: list[ClipEventLink] = []
        for ev in self.events:
            prior = [d for d in self.clipdefs if d.offset < ev.offset]
            if not prior:
                continue
            clip = prior[-1]
            clip_index = self.clipdefs.index(clip)
            near = [
                b
                for b in self.bindings
                if (ev.offset - 2500) <= b.offset <= (ev.offset + 200)
            ]
            binding = near[-1] if near else None
            names = [n for n in (binding.names if binding else []) if n]
            out.append(
                ClipEventLink(
                    event=ev,
                    clip=clip,
                    clip_index=clip_index,
                    binding=binding,
                    bone_names=names,
                )
            )
        return out

    def hinge_channels(
        self, hang_by_bone: dict[str, tuple[float, float, float]] | None = None
    ) -> list[HingeChannel]:
        """One NLA-ready track per Autovista event from ACL 2.1 samples.

        FH6 Mojo has no mid fallback. Missing buffers, DLL, or clip match raises
        ``MojoAclError``. ``hang_by_bone`` is accepted for call-site compatibility
        and ignored (ACL supplies bone-local poses).
        """
        from .mojo_acl import MojoAclError, apply_acl_to_drives, load_acl_dll

        _ = hang_by_bone
        if not self.acl_clips:
            raise MojoAclError(
                "FH6 Mojo clipd has no ACLAnimationData — ACL 2.1 is required"
            )
        try:
            load_acl_dll()
        except RuntimeError as exc:
            raise MojoAclError(str(exc)) from exc

        out: list[HingeChannel] = []
        for link in self.link_events():
            d = link.clip
            upper = link.event.name.upper()
            if "OPEN" in upper or "UP" in upper:
                opening = True
            elif "CLOSE" in upper or "DOWN" in upper:
                opening = False
            else:
                opening = True
            nch = int(d.header[2]) if d.header else 0
            bones = list(link.bone_names)
            if not bones:
                continue
            # Scaffold drives from the clip binding; ACL expands helpers and fills Q/T.
            drives: list[BoneDrive] = []
            for i, bone in enumerate(bones):
                if not bone:
                    continue
                drives.append(
                    BoneDrive(
                        bone=bone,
                        amplitude_deg=0.0,
                        axis=(0.0, 1.0, 0.0),
                        knots=[],
                        source="acl_2.1",
                        channel_index=i,
                        axis_from_mid=False,
                        open_quat=(0.0, 0.0, 0.0, 1.0),
                        quat_source="",
                    )
                )
            if not drives:
                continue
            event_leaf = link.event.name.rsplit("/", 1)[-1]
            try:
                acl_tag = apply_acl_to_drives(
                    drives, bones, self.acl_clips, opening=opening
                )
            except MojoAclError as exc:
                raise MojoAclError(f"{event_leaf}: {exc}") from exc

            prim_name = primary_bound_bone([d.bone for d in drives if d.bone]) or (
                drives[0].bone if drives else None
            )
            primary = next((d for d in drives if d.bone == prim_name), drives[0])
            ordered = [primary] + [d for d in drives if d is not primary]
            packed = d.channels.packed.hex() if d.channels else None
            out.append(
                HingeChannel(
                    event=link.event.name,
                    bone_hint=primary.bone,
                    amplitude_deg=primary.amplitude_deg,
                    duration=primary.acl_duration or DURATION,
                    open_direction=opening,
                    source=primary.source,
                    clip_index=link.clip_index,
                    key_count=len(primary.acl_quats) or None,
                    packed_hex=packed,
                    knots=[],
                    bound_bones=list(bones),
                    nch=nch,
                    mid_axes=[],
                    channel_index=primary.channel_index,
                    euler_open=primary.euler_open,
                    axis=primary.axis,
                    axis_from_mid=primary.axis_from_mid,
                    drives=ordered,
                    open_quat=primary.open_quat,
                    quat_source=acl_tag,
                    open_loc=primary.open_loc,
                )
            )
        if not out:
            raise MojoAclError(
                "no Autovista events produced ACL hinge channels "
                f"(acl_clips={len(self.acl_clips)}, bindings={len(self.bindings)})"
            )
        return out


def _is_panel_bone(name: str) -> bool:
    low = (name or "").lower()
    if any(tok in low for tok in ("piston", "aim", "strut", "spindle", "jamb")):
        return False
    return any(
        tok in low
        for tok in ("door", "hood", "trunk", "wing", "roof", "spoiler")
    )


def _is_door_bone(name: str) -> bool:
    """True for left/right door panels only (not wing/hood/aero)."""
    low = (name or "").lower()
    if any(tok in low for tok in ("piston", "aim", "strut", "window", "mirror")):
        return False
    return "door" in low


def primary_bound_bone(names: list[str]) -> str | None:
    """Pick the main panel bone from a binding list (skip pistons / aims)."""
    if not names:
        return None
    skip = ("piston", "aim", "strut", "spindle", "hinge", "blade", "jamb", "pivot")
    mains = [n for n in names if not any(s in n.lower() for s in skip)]
    # Prefer a plain panel over a ``*_rotate`` helper when both are bound (P1).
    plain = [n for n in mains if "rotate" not in n.lower()]
    return (plain or mains or names)[0]


def harvest_bone_names_from_binary(path: str) -> list[str]:
    """Pull ``bone…`` ASCII tokens from a modelbin (or any car binary)."""
    import re

    data = open(path, "rb").read()
    found = re.findall(rb"bone[A-Za-z0-9_]{2,64}", data)
    return sorted({m.decode("ascii") for m in found})


def resolve_bone_names(
    explicit: list[str] | None = None,
    search_roots: list[str] | None = None,
) -> list[str]:
    """Union of catalog + fleet harvest file + optional per-car names + modelbin."""
    from pathlib import Path

    names: set[str] = set(FORZA_BONE_CATALOG)
    # Fleet-wide harvest (XboxGames FH5+FH6) shipped beside this module.
    cat = Path(__file__).with_name("bone_name_catalog.txt")
    if cat.is_file():
        names.update(
            n.strip()
            for n in cat.read_text(encoding="utf-8").splitlines()
            if n.strip() and not n.startswith("#")
        )
    if explicit:
        names.update(explicit)
    for root in search_roots or []:
        p = Path(root)
        if p.is_file() and p.suffix.lower() == ".txt":
            names.update(p.read_text(encoding="utf-8").splitlines())
            continue
        if not p.is_dir():
            continue
        # Shallow only — never walk the whole Media tree
        bins = list(p.glob("*.modelbin"))
        bins += list(p.glob("*/*_skeleton.modelbin"))
        bins += list(p.glob("Scene/*_skeleton.modelbin"))
        bins += list(p.glob("Scene/*.modelbin"))
        for bin_path in bins[:8]:
            names.update(harvest_bone_names_from_binary(str(bin_path)))
        for txt in list(p.glob("*bones*.txt")) + list(p.glob("*/*bones*.txt")):
            names.update(txt.read_text(encoding="utf-8").splitlines())
    return sorted(n for n in names if n and not n.startswith("#"))


def discover_clipd(path: str) -> str:
    """Resolve a ``.clipd`` file or a car / Mojo folder containing one."""
    from pathlib import Path

    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".clipd":
        return str(p)
    if p.is_dir():
        hits = sorted(p.rglob("*.clipd"))
        if not hits:
            raise FileNotFoundError(f"no .clipd under {p}")
        return str(hits[0])
    raise FileNotFoundError(path)


def skeld_panel_root_name(nodes: list, panel_name: str) -> str | None:
    """Return nearest ``root_bone…`` ancestor of a panel (Mode A hinge).

    F80 wing: ``boneWingPivot → boneWing → root_boneWing``. Door panels are
    direct children of ``root_boneDoor*``. Walking ancestors covers both.
    """
    by_name = {n.name: n for n in nodes if n.name}
    by_index = {n.index: n for n in nodes}
    panel = by_name.get(panel_name)
    if panel is None:
        return None
    p = panel.parent
    while p is not None and p >= 0:
        parent = by_index.get(p)
        if parent is None:
            break
        if parent.name and parent.name.startswith("root_"):
            return parent.name
        p = parent.parent
    return None


def action_name_from_event(event_path: str, bone_hint: str | None = None) -> str:
    """Short Action/NLA name from an ``AudioEvent:/…/AV_DOOROPEN_L`` path.

    When ``bone_hint`` is set, append it so multiple clips that share the same
    AV_* leaf (AMG ``AEROUP`` for fender + rear wing) do not overwrite each other.
    ``bone_hint`` comes from the clip binding's primary bound bone in the car files.
    """
    base = event_path.rsplit("/", 1)[-1]
    if base.startswith("AV_"):
        base = base[3:]
    if bone_hint:
        return f"{base}__{bone_hint}"
    return base


def parse_channel_block(
    payload: bytes, header: tuple[int, int, int, int], table: tuple[int, ...]
) -> ChannelBlock | None:
    """Decode counts + first packed slab after ``table[7]``."""
    if len(table) < 8:
        return None
    t4, t5, t6, t7 = table[4], table[5], table[6], table[7]
    if t4 != t5 + t6 or t7 < 68 or t7 >= len(payload):
        return None
    nch = header[2]
    if nch > 16:
        return None

    if nch == 0 and t6 and t5 == 0:
        count = struct.unpack_from("<I", payload, t7)[0]
        if count > 500 or 3 * count != t4:
            return None
        po = t7 + 4
        packed = payload[po : po + t4]
        if len(packed) != t4:
            return None
        return ChannelBlock(t7, [count], po, packed, t5, t6)

    if nch < 1:
        return None

    if nch == 1 and t6 == 0:
        count = struct.unpack_from("<I", payload, t7)[0]
        if count > 500 or 3 * count != t4:
            return None
        po = t7 + 4
        packed = payload[po : po + t4]
        if len(packed) != t4:
            return None
        return ChannelBlock(t7, [count], po, packed, t5, t6)

    if nch == 1 and t6 != 0:
        count_a = struct.unpack_from("<I", payload, t7)[0]
        if count_a > 500 or 3 * count_a != t5:
            return None
        po = t7 + 4
        packed = payload[po : po + t4]
        if len(packed) != t4:
            return None
        return ChannelBlock(t7, [count_a, t6 // 3], po, packed, t5, t6)

    counts = list(payload[t7 : t7 + nch])
    after = t7 + nch
    if after % 4:
        after += 4 - (after % 4)
    if t6 == 0:
        if 3 * sum(counts) != t4:
            return None
        packed = payload[after : after + t4]
        if len(packed) != t4:
            return None
        return ChannelBlock(t7, counts, after, packed, t5, t6)

    count_b = struct.unpack_from("<I", payload, after)[0]
    if 3 * sum(counts) != t5 or 3 * count_b != t6:
        return None
    po = after + 4
    packed = payload[po : po + t4]
    if len(packed) != t4:
        return None
    return ChannelBlock(t7, counts + [count_b], po, packed, t5, t6)



def enrich_clipdef(d: ClipDef) -> ClipDef:
    """Attach packed channel slab metadata used for diagnostics / ACL wiring."""
    d.channels = parse_channel_block(d.payload, d.header, d.table)
    return d


def _notable_sins(payload: bytes) -> list[tuple[int, float, float]]:
    """Compact sin(θ/2) hits for diagnostics (not used for bake)."""
    out: list[tuple[int, float, float]] = []
    for o in range(0, len(payload) - 3, 4):
        v = struct.unpack_from("<f", payload, o)[0]
        if 0.15 < abs(v) < 0.999:
            try:
                deg = 2.0 * math.degrees(math.asin(max(-1.0, min(1.0, v))))
            except ValueError:
                continue
            if abs(deg) >= 5.0:
                out.append((o, v, deg))
    out.sort(key=lambda t: -abs(t[2]))
    return out[:12]


def find_clipdefs(data: bytes) -> list[ClipDef]:
    """Non-overlapping scan for (31, 30.0f, size) records."""
    out: list[ClipDef] = []
    needle = struct.pack("<If", 31, 30.0)
    start = 0
    while True:
        j = data.find(needle, start)
        if j < 0:
            break
        size = struct.unpack_from("<I", data, j + 8)[0]
        if size < 32 or size > 10_000 or j + 12 + size > len(data):
            start = j + 4
            continue
        payload = data[j + 12 : j + 12 + size]
        if len(payload) < 36 or payload[32:36] != b"\xff\xff\xff\xff":
            start = j + 4
            continue
        header = struct.unpack_from("<4I", payload, 0)
        table = struct.unpack_from("<8I", payload, 36)
        d = ClipDef(
            offset=j,
            header=header,
            table=table,
            payload=payload,
            notable_sins=_notable_sins(payload),
        )
        out.append(enrich_clipdef(d))
        start = j + 12 + size
    return out


def find_audio_events(data: bytes) -> list[AudioEvent]:
    out: list[AudioEvent] = []
    i = 0
    while True:
        j = data.find(b"AudioEvent:", i)
        if j < 0:
            break
        ln = struct.unpack_from("<I", data, j - 4)[0]
        out.append(AudioEvent(offset=j - 4, name=data[j : j + ln].decode("ascii", "replace")))
        i = j + 1
    return out


def find_bone_bindings(data: bytes, known: dict[int, str] | None = None) -> list[BoneBinding]:
    """Find bone-hash lists.

    Prefer locating known FNV hashes, then reading backward for ``u32 size, u32 n``
    (size == 4+n*8). Tables are not always 4-aligned relative to file start.
    """
    known = known or {}
    out: list[BoneBinding] = []
    seen: set[int] = set()

    def try_at(o: int) -> BoneBinding | None:
        if o < 0 or o + 8 > len(data):
            return None
        size, n = struct.unpack_from("<II", data, o)
        if n < 1 or n > 32 or size != 4 + n * 8:
            return None
        if o + 4 + size > len(data):
            return None
        hashes = [struct.unpack_from("<Q", data, o + 8 + i * 8)[0] for i in range(n)]
        names = [known.get(h) for h in hashes]
        if known and not any(names):
            return None
        return BoneBinding(offset=o, hashes=hashes, names=names)

    for h, _name in known.items():
        raw = struct.pack("<Q", h)
        start = 0
        while True:
            j = data.find(raw, start)
            if j < 0:
                break
            for cand in (j - 8, j - 8 - ((j - 8) % 4)):
                b = try_at(cand)
                if b and b.offset not in seen:
                    seen.add(b.offset)
                    out.append(b)
                    break
            start = j + 1

    for o in range(0, len(data) - 12, 4):
        if o in seen:
            continue
        b = try_at(o)
        if b:
            seen.add(b.offset)
            out.append(b)

    out.sort(key=lambda b: b.offset)
    return out


def parse_clipd_bytes(data: bytes, bone_names: list[str] | None = None) -> MojoClipPack:
    """Parse a ``.clipd`` from memory (zip member or file)."""
    bones = list(bone_names or [])
    known = {fnv1a64(n): n for n in bones}
    # Always include catalog so ACL track ids resolve even without modelbin harvest.
    for n in FORZA_BONE_CATALOG:
        known.setdefault(fnv1a64(n), n)
    acl_clips = []
    try:
        from .mojo_acl import MojoAclError, extract_acl_clips

        acl_clips = extract_acl_clips(data, known)
    except MojoAclError:
        raise
    except Exception as exc:
        from .mojo_acl import MojoAclError

        raise MojoAclError(f"ACLAnimationData extract failed: {exc}") from exc
    return MojoClipPack(
        clipdefs=find_clipdefs(data),
        events=find_audio_events(data),
        bindings=find_bone_bindings(data, known),
        acl_clips=acl_clips,
    )


def parse_clipd(path: str, bone_names: list[str] | None = None) -> MojoClipPack:
    """Parse a ``.clipd`` or a car/Mojo folder. Bone names resolve FNV bindings."""
    from pathlib import Path

    clip_path = discover_clipd(path)
    # Mojo dir, animations/, Scene/, car root — stop before Media/
    roots: list[str] = []
    cur = Path(clip_path).parent
    for _ in range(4):
        roots.append(str(cur))
        if cur.name.lower() in ("cars", "media", "content") or cur.parent == cur:
            break
        cur = cur.parent
    bones = resolve_bone_names(explicit=bone_names, search_roots=roots)
    data = open(clip_path, "rb").read()
    return parse_clipd_bytes(data, bones)



def decode_clip_channels(
    car_or_clip: str,
    *,
    hang_by_bone: dict[str, tuple[float, float, float]] | None = None,
) -> list[HingeChannel]:
    """Public decode API: clipd → ACL hinge channels."""
    pack = parse_clipd(car_or_clip)
    return pack.hinge_channels(hang_by_bone=hang_by_bone)


def _print_pack(pack: MojoClipPack) -> None:
    links = pack.link_events()
    print(
        f"{len(pack.clipdefs)} clipdefs, {len(pack.events)} events, "
        f"{len(pack.bindings)} bone bindings, {len(links)} linked actions, "
        f"{len(pack.acl_clips)} ACL clips"
    )
    used = {lnk.clip_index for lnk in links}
    for i, d in enumerate(pack.clipdefs):
        ch = d.channels
        ch_s = (
            f"counts={ch.channel_counts} packed={len(ch.packed)}"
            if ch
            else "channels=?"
        )
        orphan = "" if i in used else " [unlinked]"
        print(
            f"[{i}] @{d.offset} hdr={d.header} t4-7={d.table[4:8]} {ch_s}{orphan}"
        )

    print("\nLinked ACL hinge actions:")
    for h in pack.hinge_channels():
        short = h.event.rsplit("/", 1)[-1]
        print(
            f"  clip[{h.clip_index}] {short} bone={h.bone_hint} "
            f"src={h.source} samples={h.key_count}"
        )


def validate_clipd_trees(roots: list[str]) -> int:
    """Return number of cars that fail wiring; 0 = all good."""
    from pathlib import Path

    clipds: list[Path] = []
    for r in roots:
        p = Path(r)
        if p.exists():
            clipds.extend(sorted(p.rglob("*.clipd")))
    clipds = sorted(set(clipds))
    if not clipds:
        print("no .clipd found")
        return 1
    fails = 0
    for clip in clipds:
        pack = parse_clipd(str(clip))
        links = pack.link_events()
        hinges = pack.hinge_channels()
        named = sum(1 for h in hinges if h.bone_hint)
        label = clip.parent.name
        if clip.parent.name == "clip":
            label = clip.parents[2].name
        print(
            f"{label}: {len(links)} actions, {named}/{len(hinges)} with bone, "
            f"sources={[h.source for h in hinges]}"
        )
        ok = len(links) == len(pack.events) and named == len(hinges) and bool(hinges)
        if not ok:
            fails += 1
            print(
                f"  FAIL links={len(links)}/{len(pack.events)} "
                f"bones={named}/{len(hinges)}"
            )
    print(f"\n{len(clipds) - fails}/{len(clipds)} cars fully wired")
    return fails


if __name__ == "__main__":
    import sys

    args = sys.argv[1:]
    if not args:
        raise SystemExit(
            "usage: python -m io_import_forza_carbin.parsing.mojo_clipd "
            "<clipd|car_root> | --validate <root> [<root>...]"
        )
    if args[0] == "--validate":
        roots = args[1:]
        if not roots:
            raise SystemExit("--validate requires one or more search roots")
        raise SystemExit(validate_clipd_trees(roots))

    pack = parse_clipd(args[0])
    _print_pack(pack)
