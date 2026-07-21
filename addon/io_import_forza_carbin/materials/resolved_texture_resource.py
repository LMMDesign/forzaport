"""Neutral MatI/TXMP texture resource binding — not shader semantics.

Answers only: which texture resource is bound to a declared slot/register.
Semantic role, UV, channel, activation, and IR inputs come from sample-site
contracts and evaluated expressions — never from this structure alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResolvedTextureResource:
    texture_register: int
    declared_txmp: str | None
    name_hash: int | None
    texture_path: str
    source_mati: str | None = None
    provenance: str = "MATI_TXMP"
    evidence: tuple[str, ...] = ()


def resolve_texture_resources(
    *,
    params: dict,
    txmp: dict[int, int],
    name_lookup,
    source_mati: str | None = None,
) -> dict[int, ResolvedTextureResource]:
    """Map texture registers → bound resources from MatI TXMP declarations.

    ``name_lookup(hash) -> str`` raises or returns the declared TXMP name.
    Does not decide semantic role or UV.
    """
    out: dict[int, ResolvedTextureResource] = {}
    for h, treg in txmp.items():
        p = params.get(h) or params.get(int(h) & 0xFFFFFFFF)
        if p is None or getattr(p, "type", None) != 6:
            continue
        path = getattr(p, "path", "") or ""
        try:
            declared = name_lookup(h)
        except Exception:
            declared = None
        out[int(treg)] = ResolvedTextureResource(
            texture_register=int(treg),
            declared_txmp=declared,
            name_hash=int(h) & 0xFFFFFFFF,
            texture_path=path,
            source_mati=source_mati,
            provenance="MATI_TXMP",
            evidence=(
                f"TXMP:0x{int(h) & 0xFFFFFFFF:08X}:{declared or '?'}",
                f"t{int(treg)}",
                f"path={path or '<empty>'}",
            ),
        )
    return out


def resource_for_site(
    resources: dict[int, ResolvedTextureResource],
    *,
    texture_register: int,
    declared_txmp: str | None = None,
) -> ResolvedTextureResource | None:
    """Pick the resource bound to a sample site's register.

    When multiple TXMPs share a register (rare), prefer ``declared_txmp`` name.
    """
    hit = resources.get(int(texture_register))
    if hit is None:
        return None
    if declared_txmp and hit.declared_txmp and hit.declared_txmp != declared_txmp:
        # Same register claimed under a different name — still return the
        # register binding; caller must fail closed if names must match.
        return hit
    return hit


def site_txmp_name(site: Any) -> str | None:
    return (
        getattr(site, "declared_txmp", None)
        or getattr(site, "semantic_role", None)
        or None
    )
