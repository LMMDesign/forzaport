"""Typed resource identity + per-context memo for resolve_texture_source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pipeline_metrics import METRICS
from .texture_source import ResolvedTextureSource, resolve_texture_source


@dataclass(frozen=True)
class TextureResourceIdentity:
    """Cache key — not filename alone."""

    shaderbin_sha256: str
    pass_name: str
    texture_register: int | None
    sampler_register: int | None
    txmp_path: str
    txmp_name_hash: int | None
    override_identity: str
    resource_kind: str = "TXMP"

    def as_tuple(self) -> tuple:
        return (
            self.shaderbin_sha256,
            self.pass_name,
            self.texture_register,
            self.sampler_register,
            self.txmp_path,
            self.txmp_name_hash,
            self.override_identity,
            self.resource_kind,
        )


class TextureSourceMemo:
    """Per-material-evaluation memo; not shared across imports."""

    def __init__(self) -> None:
        self._by_identity: dict[tuple, ResolvedTextureSource] = {}
        self.hits = 0
        self.misses = 0

    def resolve(
        self,
        identity: TextureResourceIdentity,
        resolver: Any,
        *,
        media_root: str | None = None,
    ) -> ResolvedTextureSource:
        key = identity.as_tuple()
        hit = self._by_identity.get(key)
        if hit is not None:
            self.hits += 1
            METRICS.record_cache("texture_source_memo", hit=True)
            return hit
        self.misses += 1
        METRICS.record_cache("texture_source_memo", hit=False)
        src = resolve_texture_source(
            identity.txmp_path, resolver, media_root=media_root
        )
        self._by_identity[key] = src
        return src

    def __len__(self) -> int:
        return len(self._by_identity)
