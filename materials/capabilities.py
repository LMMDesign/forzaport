"""Material capability registry and probe framework (no bpy).

Capabilities are explicit contracts the rewrite will expand. Probing records
evidence for selection or rejection without inventing shading behaviour.

UV resolution production contract (DXIL-proven):
    UVChoice_OnCh1_OffCh2 (0x402B8ED0) on car_standard CarLightScenario selects
    TEXCOORD0 when true and TEXCOORD1 when false for BaseColor/Normal/RMAO/Alpha.
    Multi-UV without this (or another proven) policy stays unresolved — no min(uv).
    See MATERIAL_BOUNDARY.md and ``UV_CHOICE_ON_CH1_OFF_CH2``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .diagnostics import MaterialCapability, ProvenanceDiagnostic

# FTS NameHash — proven in car_standard CarLightScenario DXIL (not a heuristic).
UV_CHOICE_ON_CH1_OFF_CH2 = 0x402B8ED0

# Ch1 = first mesh UV set (TEXCOORD0); Ch2 = second (TEXCOORD1).
UV_CHOICE_TRUE_TEXCOORD = 0
UV_CHOICE_FALSE_TEXCOORD = 1


@dataclass(frozen=True)
class UvPolicyEvidence:
    """DXIL-proven MatI UV policy for rewrite-stage UV resolution."""

    param_hash: int
    param_name: str
    true_texcoord: int
    false_texcoord: int
    applies_to_txmp: tuple[str, ...]
    pso: str
    evidence: str


# Locked rewrite facts. Expand only when a new shader/PSO is traced the same way.
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
    """If MatI carries proven UVChoice, return (texcoord_index, evidence).

    Used by the rewrite UV path. Returns None when the param is absent so callers
    can fall through to other DXIL-traced policies (never invent a default).
    """
    p = params.get(UV_CHOICE_ON_CH1_OFF_CH2)
    if p is None:
        # unsigned/signed key variants
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


@dataclass(frozen=True)
class CapabilityProbeResult:
    capability: MaterialCapability | None
    selected: bool
    evidence: tuple[ProvenanceDiagnostic, ...]
    rejection_reasons: tuple[str, ...] = ()


def probe_clean_v3_capability(
    *,
    shader_name: str | None,
    has_resolvable_surface: bool,
    evidence_lines: tuple[str, ...] = (),
) -> CapabilityProbeResult:
    """Probe the only production capability: clean v3 Base/Alpha/Normal/RMAO.

    Selection requires a validated surface under that contract (maps and/or
    proven paint/weave constants). Shader display names are not used as
    family allowlists — only as report metadata.
    """
    evidence = [
        ProvenanceDiagnostic(
            kind="capability",
            detail=MaterialCapability.CLEAN_V3_BASE_ALPHA_NORMAL_RMAO.value,
            source="materials.pipeline_v3",
        )
    ]
    for line in evidence_lines:
        evidence.append(
            ProvenanceDiagnostic(kind="contract", detail=line, source="pipeline_v3")
        )
    if not shader_name:
        return CapabilityProbeResult(
            capability=None,
            selected=False,
            evidence=tuple(evidence),
            rejection_reasons=("material has no shader",),
        )
    if not has_resolvable_surface:
        return CapabilityProbeResult(
            capability=None,
            selected=False,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"{shader_name}: unsupported by clean Base/Alpha/Normal/RMAO contract",
            ),
        )
    return CapabilityProbeResult(
        capability=MaterialCapability.CLEAN_V3_BASE_ALPHA_NORMAL_RMAO,
        selected=True,
        evidence=tuple(evidence),
    )


def probe_all_capabilities(
    *,
    shader_name: str | None,
    has_resolvable_surface: bool,
    evidence_lines: tuple[str, ...] = (),
) -> CapabilityProbeResult:
    """Run registered capability probes; return the first selected result.

    Future rewrite stages append probes here. Order is intentional and stable.
    """
    return probe_clean_v3_capability(
        shader_name=shader_name,
        has_resolvable_surface=has_resolvable_surface,
        evidence_lines=evidence_lines,
    )
