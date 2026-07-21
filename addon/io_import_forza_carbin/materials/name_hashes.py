"""FTS NameHashService dictionary (hash -> parameter name).

Required for the direct material translator: sampled texture / gate params must
resolve to a name. Missing names raise MaterialNameError (fail closed).
"""

from __future__ import annotations

import json
import os

_HASHES: dict[int, str] | None = None


class MaterialNameError(RuntimeError):
    """A parameter hash required for material build has no NameHash entry."""


def _data_path() -> str:
    return os.path.join(os.path.dirname(__file__), "..", "data", "name_hashes.json")


def _load() -> dict[int, str]:
    global _HASHES
    if _HASHES is not None:
        return _HASHES
    path = os.path.normpath(_data_path())
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out: dict[int, str] = {}
    for key, name in (raw.get("hashes") or {}).items():
        out[int(key, 16)] = name
    _HASHES = out
    return _HASHES


def name_for_hash(h: int) -> str | None:
    return _load().get(h & 0xFFFFFFFF)


def require_name(h: int, *, context: str = "") -> str:
    name = name_for_hash(h)
    if not name:
        ctx = f" ({context})" if context else ""
        raise MaterialNameError(
            f"no NameHash entry for 0x{h & 0xFFFFFFFF:08X}{ctx}"
        )
    return name
