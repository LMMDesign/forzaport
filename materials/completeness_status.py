"""Separate completeness dimensions — architecture ≠ semantic coverage."""

from __future__ import annotations

from enum import Enum

# --- Runtime architecture (how the importer routes) -------------------------


class RuntimeArchitecture(str, Enum):
    SAMPLE_SITE_IR = "SAMPLE_SITE_IR"
    LEGACY_BINDING = "LEGACY_BINDING"
    FAIL_CLOSED = "FAIL_CLOSED"


# --- Semantic coverage (whether relevant DXIL sites are fully accounted) ---


class SemanticCoverage(str, Enum):
    COMPLETE_ACTIVE_BRANCH = "COMPLETE_ACTIVE_BRANCH"
    COMPLETE_SUPPORTED_SUBSET = "COMPLETE_SUPPORTED_SUBSET"
    PARTIAL_UNRESOLVED = "PARTIAL_UNRESOLVED"
    UNSUPPORTED = "UNSUPPORTED"


# --- Per-instance evaluation freshness -------------------------------------


class PerInstanceEvaluation(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_REGENERATED = "NOT_REGENERATED"


# --- Site dispositions (every relevant raw site must map to one) -----------


class SiteDisposition(str, Enum):
    IMPORTED_ACTIVE_SEMANTIC = "IMPORTED_ACTIVE_SEMANTIC"
    PROVEN_INACTIVE_BRANCH = "PROVEN_INACTIVE_BRANCH"
    PROVEN_DUPLICATE_SAMPLE = "PROVEN_DUPLICATE_SAMPLE"
    PROVEN_ENGINE_GLOBAL = "PROVEN_ENGINE_GLOBAL"
    PROVEN_PROCEDURAL_NON_MATERIAL_INPUT = "PROVEN_PROCEDURAL_NON_MATERIAL_INPUT"
    PROVEN_NO_FINAL_SURFACE_CONTRIBUTION = "PROVEN_NO_FINAL_SURFACE_CONTRIBUTION"
    EXPLICITLY_UNSUPPORTED_ACTIVE_SITE = "EXPLICITLY_UNSUPPORTED_ACTIVE_SITE"
    UNRESOLVED_SAMPLE_SITE = "UNRESOLVED_SAMPLE_SITE"


# Branch statuses after control-dependence recovery
PROVEN_UNCONDITIONAL = "PROVEN_UNCONDITIONAL"
EXECUTABLE_PREDICATE = "EXECUTABLE_PREDICATE"
COMPILE_TIME_VARIANT = "COMPILE_TIME_VARIANT"
PROVEN_INACTIVE = "PROVEN_INACTIVE"
UNRESOLVED_PREDICATE = "UNRESOLVED_PREDICATE"
NO_PREDICATE_RECOVERED = "NO_PREDICATE_RECOVERED"
PASS_SCOPED = "PASS_SCOPED"  # transitional — not a final disposition

# Re-export production IR sets from route_model for convenience
from .route_model import (  # noqa: E402
    PRODUCTION_IR_SHADERBIN_SHA256,
    PRODUCTION_IR_SHADER_NAMES,
    has_ir_evaluator,
)

# Current primary contracts: sample-site architecture is live, but semantic
# coverage of all MAIN_SURFACE_SHADING/VISIBILITY sites remains incomplete.
CURRENT_SEMANTIC_COVERAGE = SemanticCoverage.PARTIAL_UNRESOLVED
CURRENT_RUNTIME_ARCHITECTURE = RuntimeArchitecture.SAMPLE_SITE_IR
CURRENT_PER_INSTANCE_EVALUATION = PerInstanceEvaluation.NOT_REGENERATED
