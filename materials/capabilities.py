"""Material capability registry and UV policies (no bpy).

Capability lifecycle (B): constructing a complete typed ``CleanSurfaceCapability``
*is* selection. ``select_clean_surface_capability`` / ``probe_*`` helpers only
mirror that payload into ``CapabilityProbeResult`` for diagnostics — they must
not re-approve or disagree with an already-complete capability.

UVChoice remains a proven MatI→TEXCOORD policy used by the authoritative resolver.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    CapabilityProbeResult,
    CleanSurfaceCapability,
    MaterialCapabilityKind,
    ProvenanceDiagnostic,
)

# FTS NameHash — proven in car_standard CarLightScenario DXIL (not a heuristic).
UV_CHOICE_ON_CH1_OFF_CH2 = 0x402B8ED0

# Ch1 = first mesh UV set (TEXCOORD0); Ch2 = second (TEXCOORD1).
UV_CHOICE_TRUE_TEXCOORD = 0
UV_CHOICE_FALSE_TEXCOORD = 1


@dataclass(frozen=True)
class UvPolicyEvidence:
    """DXIL-proven MatI UV policy for production UV resolution."""

    param_hash: int
    param_name: str
    true_texcoord: int
    false_texcoord: int
    applies_to_txmp: tuple[str, ...]
    pso: str
    evidence: str


PROVEN_UV_POLICIES: tuple[UvPolicyEvidence, ...] = (
    UvPolicyEvidence(
        param_hash=UV_CHOICE_ON_CH1_OFF_CH2,
        param_name="UVChoice_OnCh1_OffCh2",
        true_texcoord=UV_CHOICE_TRUE_TEXCOORD,
        false_texcoord=UV_CHOICE_FALSE_TEXCOORD,
        applies_to_txmp=(
            "BaseColorAlpha",
            "BaseColorAlpha_1",
            "Alpha",
            "Normal",
            "RoughMetalAO",
        ),
        pso="car_standardCarLightScenario.pcdxil.pso",
        evidence=(
            "DXIL: CB load -> icmp eq 0 -> phi loadInput sigId 1 (false) "
            "vs sigId 0 (true); feeds t16/t17/t20/t26 sample coords"
        ),
    ),
)


def resolve_uv_choice_texcoord(params: dict) -> tuple[int, ProvenanceDiagnostic] | None:
    """If MatI carries proven UVChoice, return (texcoord_index, evidence)."""
    p = params.get(UV_CHOICE_ON_CH1_OFF_CH2)
    if p is None:
        p = params.get(UV_CHOICE_ON_CH1_OFF_CH2 & 0xFFFFFFFF)
    if p is None or getattr(p, "type", None) != 3:
        return None
    texcoord = (
        UV_CHOICE_TRUE_TEXCOORD if bool(p.value) else UV_CHOICE_FALSE_TEXCOORD
    )
    return (
        texcoord,
        ProvenanceDiagnostic(
            kind="UVChoice_OnCh1_OffCh2",
            detail=(
                f"MatI bool={bool(p.value)} -> TEXCOORD{texcoord} "
                f"({PROVEN_UV_POLICIES[0].evidence})"
            ),
            source="materials.capabilities",
        ),
    )


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


# Back-compat name used by older tests / docs; forwards to typed selector.
def probe_clean_v3_capability(
    *,
    shader_name: str | None,
    capability: CleanSurfaceCapability | None = None,
    evidence: tuple[ProvenanceDiagnostic, ...] = (),
    rejection_reasons: tuple[str, ...] = (),
    # Deprecated — ignored if present; kept only to fail loud if callers pass it.
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
