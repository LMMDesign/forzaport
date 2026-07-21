"""Per-material pipeline instrumentation (calls, timings, cache rates).

Fail-closed semantics are unchanged — this module only observes.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class SpanStatsSnapshot:
    name: str
    parent: str | None
    count: int = 0
    inclusive_s: float = 0.0
    self_s: float = 0.0
    min_s: float = 0.0
    max_s: float = 0.0
    parent_span_key: tuple[str | None, str] | None = field(default=None, repr=False)
    span_key: tuple[str | None, str] = field(default=(None, ""), repr=False)

    def avg_inclusive_s(self) -> float:
        if self.count <= 0:
            return 0.0
        return self.inclusive_s / self.count

    def avg_self_s(self) -> float:
        if self.count <= 0:
            return 0.0
        return self.self_s / self.count

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "parent": self.parent,
            "count": self.count,
            "inclusive_s": self.inclusive_s,
            "self_s": self.self_s,
            "avg_inclusive_s": self.avg_inclusive_s(),
            "avg_self_s": self.avg_self_s(),
            "min_s": self.min_s,
            "max_s": self.max_s,
        }


@dataclass
class PipelineMetricsSnapshot:
    calls: dict[str, int] = field(default_factory=dict)
    stage_seconds: dict[str, float] = field(default_factory=dict)
    cache_hits: dict[str, int] = field(default_factory=dict)
    cache_misses: dict[str, int] = field(default_factory=dict)
    peak_context_bytes: int = 0
    material_count: int = 0
    total_material_eval_seconds: float = 0.0
    spans: dict[str, SpanStatsSnapshot] = field(default_factory=dict)
    span_tree: list[dict[str, Any]] = field(default_factory=list)

    def avg_material_eval_seconds(self) -> float:
        if self.material_count <= 0:
            return 0.0
        return self.total_material_eval_seconds / self.material_count

    def cache_hit_rate(self, name: str) -> float:
        hits = self.cache_hits.get(name, 0)
        misses = self.cache_misses.get(name, 0)
        total = hits + misses
        return (hits / total) if total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": dict(self.calls),
            "stage_seconds": dict(self.stage_seconds),
            "cache_hits": dict(self.cache_hits),
            "cache_misses": dict(self.cache_misses),
            "cache_hit_rates": {
                k: self.cache_hit_rate(k)
                for k in sorted(set(self.cache_hits) | set(self.cache_misses))
            },
            "peak_context_bytes": self.peak_context_bytes,
            "material_count": self.material_count,
            "total_material_eval_seconds": self.total_material_eval_seconds,
            "avg_material_eval_seconds": self.avg_material_eval_seconds(),
            "spans": {k: v.as_dict() for k, v in sorted(self.spans.items())},
            "span_tree": list(self.span_tree),
        }


@dataclass
class _ActiveSpan:
    name: str
    parent: str | None
    span_key: tuple[str | None, str]
    start: float
    child_inclusive_s: float = 0.0


@dataclass
class _SpanAccumulator:
    name: str
    parent: str | None
    parent_span_key: tuple[str | None, str] | None = None
    count: int = 0
    inclusive_s: float = 0.0
    self_s: float = 0.0
    min_inclusive_s: float = field(default_factory=lambda: float("inf"))
    max_inclusive_s: float = 0.0

    def record(self, inclusive_s: float, self_s: float) -> None:
        self.count += 1
        self.inclusive_s += inclusive_s
        self.self_s += self_s
        if inclusive_s < self.min_inclusive_s:
            self.min_inclusive_s = inclusive_s
        if inclusive_s > self.max_inclusive_s:
            self.max_inclusive_s = inclusive_s

    def to_snapshot(self) -> SpanStatsSnapshot:
        min_s = 0.0 if self.count <= 0 else self.min_inclusive_s
        return SpanStatsSnapshot(
            name=self.name,
            parent=self.parent,
            count=self.count,
            inclusive_s=self.inclusive_s,
            self_s=self.self_s,
            min_s=min_s,
            max_s=self.max_inclusive_s if self.count > 0 else 0.0,
            parent_span_key=self.parent_span_key,
            span_key=(self.parent, self.name),
        )


class _PipelineMetrics:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tls = threading.local()
        self._enabled = True
        self.reset()

    @staticmethod
    def _span_dict_key(name: str, parent: str | None) -> str:
        if parent:
            return f"{parent}>{name}"
        return name

    def _span_stack(self) -> list[_ActiveSpan]:
        stack = getattr(self._tls, "span_stack", None)
        if stack is None:
            stack = []
            self._tls.span_stack = stack
        return stack

    def reset(self) -> None:
        with self._lock:
            self.calls: dict[str, int] = {}
            self.stage_seconds: dict[str, float] = {}
            self.cache_hits: dict[str, int] = {}
            self.cache_misses: dict[str, int] = {}
            self.peak_context_bytes = 0
            self.material_count = 0
            self.total_material_eval_seconds = 0.0
            self._spans: dict[tuple[str | None, str], _SpanAccumulator] = {}
            # Per-material call tallies (last material only when nested).
            self._material_stack: list[dict[str, int]] = []

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def record_call(self, name: str, n: int = 1) -> None:
        if not self._enabled:
            return
        with self._lock:
            self.calls[name] = self.calls.get(name, 0) + n
            if self._material_stack:
                cur = self._material_stack[-1]
                cur[name] = cur.get(name, 0) + n

    def record_cache(self, name: str, *, hit: bool) -> None:
        if not self._enabled:
            return
        with self._lock:
            bucket = self.cache_hits if hit else self.cache_misses
            bucket[name] = bucket.get(name, 0) + 1

    def record_stage(self, name: str, seconds: float) -> None:
        if not self._enabled:
            return
        with self._lock:
            self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + float(seconds)

    def _record_span_completion(
        self,
        name: str,
        parent: str | None,
        parent_span_key: tuple[str | None, str] | None,
        inclusive_s: float,
        self_s: float,
    ) -> None:
        with self._lock:
            self.stage_seconds[name] = self.stage_seconds.get(name, 0.0) + inclusive_s
            key = (parent, name)
            acc = self._spans.get(key)
            if acc is None:
                acc = _SpanAccumulator(
                    name=name,
                    parent=parent,
                    parent_span_key=parent_span_key,
                )
                self._spans[key] = acc
            acc.record(inclusive_s, self_s)

    def note_context_size(self, nbytes: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            if nbytes > self.peak_context_bytes:
                self.peak_context_bytes = int(nbytes)

    def _snapshot_spans(self) -> dict[str, SpanStatsSnapshot]:
        return {
            self._span_dict_key(acc.name, acc.parent): acc.to_snapshot()
            for acc in self._spans.values()
        }

    def get_span_tree(self) -> list[dict[str, Any]]:
        with self._lock:
            spans = self._snapshot_spans()
        return _build_span_tree(spans)

    def snapshot(self) -> PipelineMetricsSnapshot:
        with self._lock:
            spans = self._snapshot_spans()
            return PipelineMetricsSnapshot(
                calls=dict(self.calls),
                stage_seconds=dict(self.stage_seconds),
                cache_hits=dict(self.cache_hits),
                cache_misses=dict(self.cache_misses),
                peak_context_bytes=self.peak_context_bytes,
                material_count=self.material_count,
                total_material_eval_seconds=self.total_material_eval_seconds,
                spans=spans,
                span_tree=_build_span_tree(spans),
            )

    @contextmanager
    def material_scope(self) -> Iterator[dict[str, int]]:
        """Track call counts for one material evaluation."""
        tallies: dict[str, int] = {}
        t0 = time.perf_counter()
        with self._lock:
            self._material_stack.append(tallies)
        try:
            yield tallies
        finally:
            elapsed = time.perf_counter() - t0
            with self._lock:
                if self._material_stack and self._material_stack[-1] is tallies:
                    self._material_stack.pop()
                if self._enabled:
                    self.material_count += 1
                    self.total_material_eval_seconds += elapsed

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not self._enabled:
            yield
            return
        stack = self._span_stack()
        parent = stack[-1].name if stack else None
        parent_span_key = stack[-1].span_key if stack else None
        span_key = (parent, name)
        active = _ActiveSpan(
            name=name,
            parent=parent,
            span_key=span_key,
            start=time.perf_counter(),
        )
        stack.append(active)
        try:
            yield
        finally:
            inclusive_s = time.perf_counter() - active.start
            if stack and stack[-1] is active:
                stack.pop()
            else:
                for idx in range(len(stack) - 1, -1, -1):
                    if stack[idx] is active:
                        del stack[idx]
                        break
            self_s = max(0.0, inclusive_s - active.child_inclusive_s)
            if stack:
                stack[-1].child_inclusive_s += inclusive_s
            self._record_span_completion(
                name,
                parent,
                parent_span_key,
                inclusive_s,
                self_s,
            )


def _build_span_tree(spans: dict[str, SpanStatsSnapshot]) -> list[dict[str, Any]]:
    nodes: dict[tuple[str | None, str], dict[str, Any]] = {}
    for snap in spans.values():
        nodes[snap.span_key] = {
            "name": snap.name,
            "parent": snap.parent,
            "stats": snap.as_dict(),
            "children": [],
        }

    roots: list[dict[str, Any]] = []
    for snap in spans.values():
        node = nodes[snap.span_key]
        parent_key = snap.parent_span_key
        if parent_key is not None and parent_key in nodes:
            nodes[parent_key]["children"].append(node)
        else:
            roots.append(node)

    def _sort_tree(node: dict[str, Any]) -> None:
        node["children"].sort(key=lambda c: c["name"])
        for child in node["children"]:
            _sort_tree(child)

    for root in roots:
        _sort_tree(root)
    roots.sort(key=lambda n: n["name"])
    return roots


METRICS = _PipelineMetrics()


def reset_metrics() -> None:
    METRICS.reset()


def snapshot_metrics() -> PipelineMetricsSnapshot:
    return METRICS.snapshot()
