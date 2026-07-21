"""Context-cache key audit + importer-scoped MaterialEvaluationContext cache."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from .pipeline_metrics import METRICS


@dataclass(frozen=True)
class MaterialContextCacheKey:
    """Distinguishes every input that may change evaluation."""

    mati_identity: str
    parent_template_identity: str
    effective_overrides_fingerprint: str
    shaderbin_sha256: str
    pass_pso_scenario: str
    platform_archive_identity: str
    media_root: str
    game_key: str = "fh6"

    def as_tuple(self) -> tuple[str, ...]:
        return (
            self.mati_identity,
            self.parent_template_identity,
            self.effective_overrides_fingerprint,
            self.shaderbin_sha256,
            self.pass_pso_scenario,
            self.platform_archive_identity,
            self.media_root,
            self.game_key,
        )


def overrides_fingerprint(overrides: Any) -> str:
    if not overrides:
        return ""
    try:
        return ",".join(f"{int(h) & 0xFFFFFFFF:08X}" for h in sorted(int(x) for x in overrides))
    except Exception:
        return repr(sorted(str(x) for x in overrides))


def build_context_cache_key(
    *,
    instance_key: str,
    material,
    media_root: str,
    game_key: str = "fh6",
    shaderbin_sha256: str = "",
    pass_name: str = "CarLightScenario",
    pso_sha256: str = "",
    archive_path: str = "",
) -> MaterialContextCacheKey:
    parent = str(
        getattr(material, "parent_template", None)
        or getattr(material, "template_name", None)
        or ""
    )
    overrides = getattr(material, "override_hashes", None) or set()
    return MaterialContextCacheKey(
        mati_identity=str(instance_key or ""),
        parent_template_identity=parent,
        effective_overrides_fingerprint=overrides_fingerprint(overrides),
        shaderbin_sha256=str(shaderbin_sha256 or ""),
        pass_pso_scenario=f"{pass_name}|{pso_sha256}",
        platform_archive_identity=f"{game_key}|{archive_path}",
        media_root=str(media_root or ""),
        game_key=str(game_key or "fh6"),
    )


class MaterialContextCache:
    """Per-import cache: repeated mesh use shares one context; cleared on cleanup."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._store: dict[tuple[str, ...], Any] = {}
        self.hits = 0
        self.misses = 0
        self.peak_alive = 0

    def get(self, key: MaterialContextCacheKey):
        with self._lock:
            hit = self._store.get(key.as_tuple())
            if hit is not None:
                self.hits += 1
                METRICS.record_cache("material_context_cache", hit=True)
                return hit
            self.misses += 1
            METRICS.record_cache("material_context_cache", hit=False)
            return None

    def put(self, key: MaterialContextCacheKey, ctx) -> None:
        with self._lock:
            self._store[key.as_tuple()] = ctx
            n = len(self._store)
            if n > self.peak_alive:
                self.peak_alive = n

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def values(self):
        with self._lock:
            return tuple(self._store.values())


# Process-wide default for tests; importer owns its own instance.
_DEFAULT_CACHE = MaterialContextCache()


def default_context_cache() -> MaterialContextCache:
    return _DEFAULT_CACHE
