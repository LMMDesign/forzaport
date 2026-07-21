"""Shared static shader analysis — keyed by exact SHA + pass/PSO identity.

Multiple MatI instances with the same exact shader/pass share one object.
Instance evaluation (params, active sites, alpha) lives on MaterialEvaluationContext.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Mapping

from .pipeline_metrics import METRICS


@dataclass(frozen=True)
class ShaderStaticAnalysisKey:
    shaderbin_sha256: str
    pass_name: str
    pso_sha256: str
    archive_path: str
    platform: str = "fh6"

    def as_tuple(self) -> tuple[str, str, str, str, str]:
        return (
            self.shaderbin_sha256,
            self.pass_name,
            self.pso_sha256,
            self.archive_path,
            self.platform,
        )


@dataclass(frozen=True)
class ShaderStaticAnalysis:
    """Immutable static layer shared across MatIs with the same exact shader/pass."""

    key: ShaderStaticAnalysisKey
    pass_contract: Mapping[str, Any] | None
    # Primary-pass DXIL / ShaderBindings static carrier (textures template, evidence).
    static_bindings_template: Any = None
    static_sample_site_definitions: tuple[Any, ...] = ()
    resource_declarations: tuple[Any, ...] = ()
    evidence: tuple[str, ...] = ()


_LOCK = RLock()
_CACHE: dict[tuple[str, str, str, str, str], ShaderStaticAnalysis] = {}


def clear_static_analysis_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def static_analysis_cache_size() -> int:
    with _LOCK:
        return len(_CACHE)


def get_cached_static_analyses() -> tuple[ShaderStaticAnalysis, ...]:
    with _LOCK:
        return tuple(_CACHE.values())


def get_or_create_static_analysis(
    *,
    shaderbin_sha256: str,
    pass_name: str,
    pso_sha256: str,
    archive_path: str,
    platform: str = "fh6",
    pass_contract: Mapping[str, Any] | None = None,
    static_bindings_template: Any = None,
    static_sample_site_definitions: tuple[Any, ...] = (),
    resource_declarations: tuple[Any, ...] = (),
    evidence: tuple[str, ...] = (),
) -> ShaderStaticAnalysis:
    """Return shared static analysis; create once per exact key."""
    key = ShaderStaticAnalysisKey(
        shaderbin_sha256=shaderbin_sha256 or "",
        pass_name=pass_name or "CarLightScenario",
        pso_sha256=pso_sha256 or "",
        archive_path=archive_path or "",
        platform=platform or "fh6",
    )
    tup = key.as_tuple()
    with _LOCK:
        hit = _CACHE.get(tup)
        if hit is not None:
            METRICS.record_cache("shader_static_analysis", hit=True)
            return hit
        METRICS.record_cache("shader_static_analysis", hit=False)
        # Load contract once if not provided.
        contract = pass_contract
        if contract is None and key.shaderbin_sha256:
            from .pass_contracts import load_shader_pass_contract

            with METRICS.stage("contract_lookup"):
                contract = load_shader_pass_contract(key.shaderbin_sha256)
        obj = ShaderStaticAnalysis(
            key=key,
            pass_contract=contract,
            static_bindings_template=static_bindings_template,
            static_sample_site_definitions=tuple(static_sample_site_definitions),
            resource_declarations=tuple(resource_declarations),
            evidence=tuple(evidence),
        )
        _CACHE[tup] = obj
        return obj
