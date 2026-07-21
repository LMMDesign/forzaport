"""Mojo .skeld reader (FH6 Autovista).

Per-car body (after shared schema of length A+9):
  outer_size, type GUID, ..., then:
  ids:    count u32 + count * FNV-1a64(bone_name)
  parents: count u32 + count * s16 parent index (-1 = none)
  xforms: count u32 + count * 8 f32  as (px,py,pz,1, qx,qy,qz,qw)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


def fnv1a64(text: str) -> int:
    h = 0xCBF29CE484222325
    for c in text.encode("utf-8"):
        h ^= c
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


@dataclass
class MojoNode:
    index: int
    name_hash: int
    parent: int
    pos: tuple[float, float, float]
    quat: tuple[float, float, float, float]  # xyzw
    name: str | None = None


def shared_payload_offset(data: bytes) -> int:
    if data[:4] != bytes.fromhex("cc14a4b1"):
        raise ValueError("bad Mojo magic")
    a = struct.unpack_from("<I", data, 5)[0]
    return a + 9


def parse_skeld_bytes(data: bytes, name_hints: list[str] | None = None) -> list[MojoNode]:
    """Parse a .skeld from memory (zip member or file bytes)."""
    pay0 = shared_payload_offset(data)
    end = len(data)
    while end and data[end - 1] == 0:
        end -= 1
    body = data[pay0:end]

    id_off = None
    for o in range(48, min(200, len(body) - 8), 4):
        sz, n = struct.unpack_from("<II", body, o)
        if n >= 2 and sz == 4 + n * 8:
            id_off = o
            break
    if id_off is None:
        raise ValueError("ID table not found")

    sz, n = struct.unpack_from("<II", body, id_off)
    hashes = [struct.unpack_from("<Q", body, id_off + 8 + i * 8)[0] for i in range(n)]
    o = id_off + 4 + sz

    psz, pn = struct.unpack_from("<II", body, o)
    if pn != n or psz != 4 + n * 2:
        if not (pn == n and psz == 4 + n * 2):
            raise ValueError(f"parent table mismatch at {o}: {psz},{pn}")
    parents = list(struct.unpack_from("<" + "h" * n, body, o + 8))
    o = o + 4 + psz

    xsz, xn = struct.unpack_from("<II", body, o)
    if xn != n or xsz != 4 + n * 32:
        raise ValueError(f"xform table mismatch at {o}: {xsz},{xn}")
    nodes: list[MojoNode] = []
    for i in range(n):
        px, py, pz, _w, qx, qy, qz, qw = struct.unpack_from("<8f", body, o + 8 + i * 32)
        nodes.append(
            MojoNode(
                index=i,
                name_hash=hashes[i],
                parent=parents[i],
                pos=(px, py, pz),
                quat=(qx, qy, qz, qw),
            )
        )

    if name_hints:
        by_hash = {fnv1a64(name): name for name in name_hints}
        for node in nodes:
            node.name = by_hash.get(node.name_hash)

    return nodes


def parse_skeld(path: str, name_hints: list[str] | None = None) -> list[MojoNode]:
    data = open(path, "rb").read()
    return parse_skeld_bytes(data, name_hints)


def _panel_side(panel: str) -> str | None:
    """``l`` / ``r`` for ``boneDoorLF`` / ``boneDoorRF`` style panels."""
    low = (panel or "").lower()
    if low.endswith("lf") or low.endswith("lr"):
        return "l"
    if low.endswith("rf") or low.endswith("rr"):
        return "r"
    return None


def _mirror_bone_side(name: str) -> str | None:
    """Side letter for wing mirror bones; ``None`` for interior / center mirrors."""
    low = (name or "").lower()
    if "mirrorc" in low:
        return None
    if "mirrorl" in low:
        return "l"
    if "mirrorr" in low:
        return "r"
    return None


def _is_wing_mirror_bone(name: str) -> bool:
    low = (name or "").lower()
    return "mirror" in low and "mirrorc" not in low and _mirror_bone_side(name) is not None


def skeld_door_mounted_mirror_bones(nodes: list[MojoNode], panel: str) -> list[str]:
    """Mirror bones under a door panel (or its ``root_boneDoor*``) in ``.skeld``.

    Autovista door clips do not key ``boneMirror*``; the game relies on skeleton
    inheritance. Include these bones in the armature (unkeyed) so Child Of meshes
    on ``boneMirrorL_001`` / ``boneMirrorR_001`` follow the hinge.
    """
    if not nodes or not panel:
        return []
    by_index = {n.index: n for n in nodes}
    panel_node = next((n for n in nodes if n.name == panel), None)
    if panel_node is None:
        return []

    anchor_indices = {panel_node.index}
    p = panel_node.parent
    while p is not None and p >= 0:
        anc = by_index.get(p)
        if anc is None:
            break
        if anc.name and anc.name.startswith("root_boneDoor"):
            anchor_indices.add(anc.index)
            break
        p = anc.parent

    def under_door_anchor(node: MojoNode) -> bool:
        p = node.parent
        while p is not None and p >= 0:
            if p in anchor_indices:
                return True
            anc = by_index.get(p)
            if anc is None:
                break
            p = anc.parent
        return False

    want_side = _panel_side(panel)
    out: list[str] = []
    for node in nodes:
        if not node.name or not _is_wing_mirror_bone(node.name):
            continue
        if not under_door_anchor(node):
            continue
        side = _mirror_bone_side(node.name)
        if want_side and side and side != want_side:
            continue
        out.append(node.name)
    return sorted(set(out))


def skeld_subtree_bones(nodes: list[MojoNode], anchor_names: list[str]) -> list[str]:
    """All named descendants of ``anchor_names`` roots (unkeyed inheritance bones)."""
    if not nodes or not anchor_names:
        return []
    by_index = {n.index: n for n in nodes}
    by_name = {n.name: n for n in nodes if n.name}
    anchor_indices: set[int] = set()
    for name in anchor_names:
        node = by_name.get(name)
        if node is not None:
            anchor_indices.add(node.index)

    def under_anchor(node: MojoNode) -> bool:
        p = node.parent
        while p is not None and p >= 0:
            if p in anchor_indices:
                return True
            anc = by_index.get(p)
            if anc is None:
                break
            p = anc.parent
        return False

    out: list[str] = []
    for node in nodes:
        if not node.name:
            continue
        if node.name in anchor_names or under_anchor(node):
            out.append(node.name)
    return sorted(set(out))


def skeld_aero_mechanism_roots(nodes: list[MojoNode]) -> list[str]:
    """``root_bonewing`` / ``root_boneWingF`` style anchors for active aero."""
    if not nodes:
        return []
    roots: list[str] = []
    for node in nodes:
        if not node.name:
            continue
        low = node.name.lower()
        if low.startswith("root_bonewing"):
            roots.append(node.name)
    return sorted(set(roots))


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        raise SystemExit("usage: python mojo_skeld.py <skeld_path> [bone_names.txt]")
    path = sys.argv[1]
    hints: list[str] | None = None
    if len(sys.argv) > 2:
        hints = open(sys.argv[2], encoding="utf-8").read().splitlines()
    else:
        try:
            from .mojo_clipd import resolve_bone_names

            hints = resolve_bone_names(search_roots=[str(Path(path).parent)])
        except Exception:
            hints = None
    nodes = parse_skeld(path, hints)
    named = sum(1 for n in nodes if n.name)
    print(f"{len(nodes)} nodes, {named} named")
    for n in nodes:
        label = n.name or f"#{n.name_hash:016x}"
        print(
            f"{n.index:3d} p={n.parent:3d} {label:32s} "
            f"T=({n.pos[0]:.4f},{n.pos[1]:.4f},{n.pos[2]:.4f}) "
            f"Q=({n.quat[0]:.4f},{n.quat[1]:.4f},{n.quat[2]:.4f},{n.quat[3]:.4f})"
        )
