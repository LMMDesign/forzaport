"""Material capability registry (no bpy).

Capability lifecycle (B): constructing a complete typed ``CleanSurfaceCapability``
*is* selection. ``select_clean_surface_capability`` / ``probe_*`` helpers only
mirror that payload into ``CapabilityProbeResult`` for diagnostics — they must
not re-approve or disagree with an already-complete capability.

UVChoice is **not** a global policy. It is keyed by exact shaderbin SHA via
``materials.uv.uv_choice_contracts`` (car_standard SHA only today).
"""

from __future__ import annotations

from .model import (
    CapabilityProbeResult,
    CleanSurfaceCapability,
    MaterialCapabilityKind,
    ProvenanceDiagnostic,
)
from .uv.uv_choice_contracts import (
    CAR_STANDARD_SHADERBIN_SHA256,
    UV_CHOICE_BY_SHA,
    UV_CHOICE_FALSE_TEXCOORD,
    UV_CHOICE_ON_CH1_OFF_CH2,
    UV_CHOICE_TRUE_TEXCOORD,
    UvChoiceContract,
    resolve_uv_choice_texcoord,
)

# Back-compat evidence listing for docs/tests (SHA-gated at resolve time).
PROVEN_UV_POLICIES: tuple[UvChoiceContract, ...] = tuple(UV_CHOICE_BY_SHA.values())


def select_clean_surface_capability(
    *,
    shader_name: str | None,
    capability: CleanSurfaceCapability | None,
    evidence: tuple[ProvenanceDiagnostic, ...] = (),
    rejection_reasons: tuple[str, ...] = (),
) -> CapabilityProbeResult:
    """Finalize clean-surface selection from a complete typed payload.

    Does not take builder success. Incomplete payloads are never selected.
    """
    base_evidence = list(evidence) or [
        ProvenanceDiagnostic(
            kind="capability",
            detail=MaterialCapabilityKind.CLEAN_SURFACE.value,
            source="materials.capabilities",
        )
    ]
    if not shader_name:
        return CapabilityProbeResult(
            kind=None,
            capability=None,
            evidence=tuple(base_evidence),
            rejection_reasons=("material has no shader",),
        )
    if capability is None:
        reasons = rejection_reasons or (
            f"{shader_name}: unsupported by clean Base/Alpha/Normal/RMAO contract",
        )
        return CapabilityProbeResult(
            kind=None,
            capability=None,
            evidence=tuple(base_evidence),
            rejection_reasons=tuple(reasons),
        )
    return CapabilityProbeResult(
        kind=MaterialCapabilityKind.CLEAN_SURFACE,
        capability=capability,
        evidence=tuple(base_evidence),
    )


def probe_clean_v3_capability(
    *,
    shader_name: str | None,
    capability: CleanSurfaceCapability | None = None,
    evidence: tuple[ProvenanceDiagnostic, ...] = (),
    rejection_reasons: tuple[str, ...] = (),
    has_resolvable_surface: bool | None = None,
) -> CapabilityProbeResult:
    """Select clean surface from a typed payload (no builder-success input)."""
    if has_resolvable_surface is not None:
        raise TypeError(
            "has_resolvable_surface was removed; pass a CleanSurfaceCapability "
            "payload (or None) instead"
        )
    return select_clean_surface_capability(
        shader_name=shader_name,
        capability=capability,
        evidence=evidence,
        rejection_reasons=rejection_reasons,
    )


def probe_all_capabilities(
    *,
    shader_name: str | None,
    capability: CleanSurfaceCapability | None = None,
    evidence: tuple[ProvenanceDiagnostic, ...] = (),
    rejection_reasons: tuple[str, ...] = (),
) -> CapabilityProbeResult:
    """Run registered capability probes; return the first selected result."""
    return select_clean_surface_capability(
        shader_name=shader_name,
        capability=capability,
        evidence=evidence,
        rejection_reasons=rejection_reasons,
    )


__all__ = [
    "CAR_STANDARD_SHADERBIN_SHA256",
    "PROVEN_UV_POLICIES",
    "UV_CHOICE_BY_SHA",
    "UV_CHOICE_FALSE_TEXCOORD",
    "UV_CHOICE_ON_CH1_OFF_CH2",
    "UV_CHOICE_TRUE_TEXCOORD",
    "UvChoiceContract",
    "probe_all_capabilities",
    "probe_clean_v3_capability",
    "resolve_uv_choice_texcoord",
    "select_clean_surface_capability",
]
