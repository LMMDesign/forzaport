"""Memory accounting for material contexts without double-counting shared refs."""

from __future__ import annotations

import sys
from typing import Any, Iterable


def shallow_size(obj: Any) -> int:
    try:
        return sys.getsizeof(obj)
    except Exception:
        return 0


def deep_unique_size(obj: Any, *, seen: set[int] | None = None, budget: int = 50_000) -> int:
    """Approximate uniquely retained deep size (shared objects counted once)."""
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    if len(seen) > budget:
        return shallow_size(obj)
    total = shallow_size(obj)
    if isinstance(obj, dict):
        for k, v in obj.items():
            total += deep_unique_size(k, seen=seen, budget=budget)
            total += deep_unique_size(v, seen=seen, budget=budget)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for x in obj:
            total += deep_unique_size(x, seen=seen, budget=budget)
    elif hasattr(obj, "__dict__"):
        total += deep_unique_size(vars(obj), seen=seen, budget=budget)
    elif hasattr(obj, "__slots__"):
        for name in obj.__slots__:
            if hasattr(obj, name):
                total += deep_unique_size(getattr(obj, name), seen=seen, budget=budget)
    return total


def measure_context_memory(contexts: Iterable[Any], static_analyses: Iterable[Any] = ()) -> dict:
    contexts = list(contexts)
    static_analyses = list(static_analyses)
    shared_seen: set[int] = set()
    # Count static analyses first so instance deep sizes exclude shared static.
    static_bytes = 0
    for sa in static_analyses:
        static_bytes += deep_unique_size(sa, seen=shared_seen)

    per_ctx = []
    instance_bytes = 0
    for ctx in contexts:
        # Start from shared_seen copy so static is not double-counted into instance.
        seen = set(shared_seen)
        before = len(seen)
        sz = deep_unique_size(ctx, seen=seen)
        # Unique to this context ≈ growth beyond shared static
        unique = 0
        # Recompute: size of ctx tree excluding already-seen static ids
        seen2 = set(shared_seen)
        unique = deep_unique_size(ctx, seen=seen2)
        per_ctx.append(
            {
                "instance_key": getattr(getattr(ctx, "source", None), "instance_key", None),
                "shallow_bytes": shallow_size(ctx),
                "unique_deep_bytes": unique,
            }
        )
        instance_bytes += unique
        del before

    return {
        "context_count": len(contexts),
        "shallow_per_context_avg": (
            sum(r["shallow_bytes"] for r in per_ctx) / len(per_ctx) if per_ctx else 0
        ),
        "unique_deep_per_context_avg": (
            sum(r["unique_deep_bytes"] for r in per_ctx) / len(per_ctx) if per_ctx else 0
        ),
        "shared_static_analysis_bytes": static_bytes,
        "total_instance_context_bytes": instance_bytes,
        "per_context": per_ctx,
    }
