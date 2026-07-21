"""Offline / debug pose oracle for Mojo Autovista closed-loop RE.

NOT production decode. Enable with::

    FORZA_MOJO_POSE_ORACLE=1

Optional path override::

    FORZA_MOJO_POSE_ORACLE=H:\\path\\to\\pose_oracle.json

When set, door (and listed) bone ``open_quat`` values are replaced from the
oracle JSON before bake. Used to prove Blender Mode A can look correct when
fed game/FH5 deltas, then reverse those deltas into a file-backed rule.

Schema ``forza_pose_oracle_v1``::

    {
      "schema": "forza_pose_oracle_v1",
      "media": "MER_AMGOne_21",
      "source": "fh5_gr2+live_panel",
      "bones": {
        "boneDoorLF": {"open_quat_xyzw": [qx,qy,qz,qw], "amplitude_deg": 82.3},
        ...
      },
      "events": { "AV_DOOROPEN_L": ["boneDoorLF", ...], ... }  # optional filter
    }
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ORACLE_SCHEMA = "forza_pose_oracle_v1"


def pose_oracle_enabled() -> bool:
    v = os.environ.get("FORZA_MOJO_POSE_ORACLE", "").strip()
    if not v:
        return False
    return v.lower() not in ("0", "false", "no", "off")


def _oracle_path_from_env() -> Path | None:
    v = os.environ.get("FORZA_MOJO_POSE_ORACLE", "").strip()
    if not v or v.lower() in ("1", "true", "yes", "on"):
        return None
    p = Path(v)
    return p if p.is_file() else None


def discover_pose_oracle(car_root: str | Path) -> Path | None:
    """Resolve oracle JSON: env file, then Mojo folder, then RE captures."""
    env_path = _oracle_path_from_env()
    if env_path is not None:
        return env_path
    if not pose_oracle_enabled():
        return None
    root = Path(car_root)
    media = root.name
    candidates = [
        root / "Scene" / "animations" / "Mojo" / "pose_oracle.json",
        root / "pose_oracle.json",
        Path(__file__).resolve().parents[3]
        / "archive"
        / "reverse-engineering"
        / "fh6-rip-legacy"
        / "_mojo_samples"
        / "_re_captures"
        / f"pose_oracle_{media}.json",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_pose_oracle(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != ORACLE_SCHEMA:
        return None
    bones = data.get("bones")
    if not isinstance(bones, dict) or not bones:
        return None
    return data


def oracle_quat_for_bone(
    oracle: dict[str, Any],
    bone: str,
    *,
    event_leaf: str | None = None,
) -> tuple[float, float, float, float] | None:
    """Return xyzw open delta quat for ``bone``, or None."""
    events = oracle.get("events")
    if isinstance(events, dict) and event_leaf:
        allow = events.get(event_leaf)
        if allow is not None and bone not in allow:
            return None
    entry = (oracle.get("bones") or {}).get(bone)
    if not isinstance(entry, dict):
        return None
    q = entry.get("open_quat_xyzw")
    if not (isinstance(q, (list, tuple)) and len(q) == 4):
        return None
    return (float(q[0]), float(q[1]), float(q[2]), float(q[3]))


def apply_pose_oracle_to_hinges(hinges: list, oracle: dict[str, Any]) -> int:
    """Rewrite hinge drive open quats from oracle. Returns number of bones hit."""
    from .mojo_clipd import quat_angle_deg

    n = 0
    for h in hinges:
        leaf = (getattr(h, "event", "") or "").rsplit("/", 1)[-1]
        drives = getattr(h, "drives", None) or []
        for drv in drives:
            bn = getattr(drv, "bone", None)
            if not bn:
                continue
            q = oracle_quat_for_bone(oracle, bn, event_leaf=leaf)
            if q is None:
                continue
            drv.open_quat = q
            drv.quat_source = f"pose_oracle:{oracle.get('source', 'oracle')}"
            drv.amplitude_deg = quat_angle_deg(q)
            drv.axis_from_mid = quat_angle_deg(q) > 0.5
            n += 1
            # Keep channel primary in sync when this is the panel drive.
            if bn == getattr(h, "bone_hint", None) or (
                drives and drv is drives[0]
            ):
                h.open_quat = q
                h.quat_source = drv.quat_source
                h.amplitude_deg = drv.amplitude_deg
                h.axis_from_mid = drv.axis_from_mid
    return n
