"""Unique Blender material keys for modelbin MatI instances."""

from __future__ import annotations

import hashlib


def _parameter_fingerprint(mobj) -> str:
    """Hash the complete parsed material state; never truncate override identity."""
    digest = hashlib.sha256()
    digest.update((getattr(mobj, "shader_name", None) or "").encode("utf-8"))
    params = getattr(mobj, "parameters", None) or {}
    for h, p in sorted(params.items()):
        digest.update(int(h & 0xFFFFFFFF).to_bytes(4, "little"))
        digest.update(int(getattr(p, "type", -1)).to_bytes(2, "little", signed=True))
        path = getattr(p, "path", "") or ""
        if path:
            digest.update(path.encode("utf-8", errors="replace"))
        value = getattr(p, "value", None)
        if value is not None:
            digest.update(repr(value).encode("ascii", errors="replace"))
        samp = getattr(p, "samp", b"") or b""
        if samp:
            digest.update(bytes(samp))
    return digest.hexdigest()[:16]


def material_instance_key(pm, game_key: str | None = None):
    """Collision-resistant key for one complete parsed material instance."""
    mobj = getattr(pm, "obj", None)
    prefix = ""
    if game_key and game_key not in ("unknown",):
        prefix = f"{game_key}|"
    if mobj is None:
        base = getattr(pm, "name", None) or "material"
        return f"{prefix}{base}"
    base = getattr(pm, "name", None) or "material"
    if "|" in base and base.startswith(("fh5|", "fh6|", "fm|", "other|")):
        return base
    return f"{prefix}{base}|v4-{_parameter_fingerprint(mobj)}"
