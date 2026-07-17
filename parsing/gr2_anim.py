"""FH5 Granny .gr2 → matrix tracks via bundled tools/gr2dump.exe (LSLib).

gr2dump reads local 4×4 matrix samples (format ``gr2dump_v2_matrix``) and writes
JSON; Blender bakes with the same path as Divine Collada local matrices.
(Divine Collada export null-refs on Forza Autovista clips.)

Requires a legally obtained ``granny2.dll`` beside ``gr2dump.exe`` (not bundled).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path


def find_gr2dump_exe(_unused: str | None = None) -> str | None:
    """Locate bundled gr2dump.exe under addon/tools/gr2dump."""
    here = Path(__file__).resolve()
    addon_root = here.parents[1]
    candidates = [
        addon_root / "tools" / "gr2dump" / "gr2dump.exe",
        addon_root / "tools" / "gr2dump" / "gr2dump",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def granny2_dll_path(dump_exe: str | None = None) -> str | None:
    """Return path to granny2.dll next to gr2dump, or None if missing."""
    exe = dump_exe or find_gr2dump_exe()
    if not exe:
        return None
    cand = Path(exe).resolve().parent / "granny2.dll"
    return str(cand) if cand.is_file() else None


def require_gr2dump_runtime() -> tuple[str | None, str | None]:
    """Return ``(exe, error)``. Error explains missing tool or granny2.dll."""
    exe = find_gr2dump_exe()
    if not exe:
        return None, (
            "FH5 .gr2 bake needs tools/gr2dump/gr2dump.exe (bundled with the addon). "
            "Install .NET 8 runtime if the tool fails to start."
        )
    if granny2_dll_path(exe) is None:
        folder = Path(exe).resolve().parent
        return None, (
            "FH5 animation import needs granny2.dll beside gr2dump "
            f"({folder}). This proprietary Granny runtime is not shipped with "
            "the addon — place a legally obtained copy there, then retry. "
            "Also install .NET 8: https://dotnet.microsoft.com/download/dotnet/8.0"
        )
    return exe, None


def dump_gr2_animation(gr2_path: str, dump_exe: str) -> dict | None:
    """Run gr2dump → JSON dict, or None on failure."""
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="forza_gr2_")
    os.close(fd)
    try:
        proc = subprocess.run(
            [dump_exe, gr2_path, "-o", tmp],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.dirname(dump_exe) or None,
        )
        if proc.returncode != 0 or not os.path.isfile(tmp):
            return None
        with open(tmp, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _action_from_gr2_name(path: str) -> tuple[str, bool]:
    """NLA/action name + opening flag from ``doorLF_open.gr2``."""
    stem = Path(path).stem
    low = stem.lower()
    opening = "close" not in low

    if opening and re.fullmatch(r"doorlf_open", low):
        return "DOOROPEN_L", True
    if opening and re.fullmatch(r"doorrf_open", low):
        return "DOOROPEN_R", True
    if opening and re.fullmatch(r"hood_open", low):
        return "HOODOPEN", True
    if opening and re.fullmatch(r"trunk_open", low):
        return "TRUNKOPEN", True
    if opening and re.fullmatch(r"wing_open", low):
        return "AEROUP", True
    if (not opening) and re.fullmatch(r"doorlf_close", low):
        return "DOORCLOSE_L", False
    if (not opening) and re.fullmatch(r"doorrf_close", low):
        return "DOORCLOSE_R", False
    if (not opening) and re.fullmatch(r"hood_close", low):
        return "HOODCLOSE", False
    if (not opening) and re.fullmatch(r"trunk_close", low):
        return "TRUNKCLOSE", False
    if (not opening) and re.fullmatch(r"wing_close", low):
        return "AERODOWN", False

    return stem.upper().replace("-", "_"), opening


def find_skeleton_gr2_file(car_root: str) -> str | None:
    import glob as _g

    for pat in (
        os.path.join(car_root, "scene", "*_skeleton.gr2"),
        os.path.join(car_root, "Scene", "*_skeleton.gr2"),
        os.path.join(car_root, "**", "*_skeleton.gr2"),
    ):
        hits = sorted(_g.glob(pat, recursive=("**" in pat)))
        if hits:
            return hits[0]
    return None


def skeleton_bones_from_gr2(skel_gr2: str, dump_exe: str) -> list[dict] | None:
    """``[{name,parent,pos,quat}, ...]`` from skeleton .gr2 dump."""
    doc = dump_gr2_animation(skel_gr2, dump_exe)
    if not doc:
        return None
    skels = doc.get("skeletons") or []
    if not skels:
        return None
    return list(skels[0].get("bones") or [])


def find_animations_dir(car_root: str) -> str | None:
    for name in ("Animations", "animations"):
        p = os.path.join(car_root, name)
        if os.path.isdir(p):
            return p
    return None


def doc_has_matrix_tracks(doc: dict) -> bool:
    """True when gr2dump_v2_matrix emitted usable local 4×4 samples."""
    if (doc.get("format") or "") != "gr2dump_v2_matrix":
        return False
    for anim in doc.get("animations") or []:
        for tr in anim.get("tracks") or []:
            mats = tr.get("matrices") or []
            times = tr.get("times") or []
            if mats and times and len(mats) == len(times):
                return True
    return False


def dump_all_animation_gr2(
    car_root: str, dump_exe: str
) -> list[tuple[str, str, dict]]:
    """``[(action_name, gr2_path, doc), ...]`` — one entry per unique NLA name."""
    anim_dir = find_animations_dir(car_root)
    if not anim_dir:
        return []
    out: list[tuple[str, str, dict]] = []
    seen: set[str] = set()
    for gr2 in sorted(Path(anim_dir).glob("*.gr2")):
        if "skeleton" in gr2.name.lower() or "skel" in gr2.name.lower():
            continue
        doc = dump_gr2_animation(str(gr2), dump_exe)
        if not doc:
            continue
        anims = doc.get("animations") or []
        if not anims or not (anims[0].get("tracks") or []):
            continue
        action, _opening = _action_from_gr2_name(str(gr2))
        if action in seen:
            continue
        seen.add(action)
        out.append((action, str(gr2), doc))
    return out
