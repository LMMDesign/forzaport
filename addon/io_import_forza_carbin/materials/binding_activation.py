"""TXMP binding activation — presence ≠ use.

Compact, cached per-shader-family contracts. Runtime import must not re-disassemble
DXIL for every material instance; evidence strings reference the proven PSO once.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from ..parsing.material import ShaderParameterName as SPN
from .model import (
    BaseColorSourceKind,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedTextureSlot,
    ResolvedWeaveComposite,
    TextureBindingActivation,
    TextureBindingDecision,
)

# Proven CBMP / NameHash controls for car_carbonfiber CarLightScenario.
_CARBON_UNIQUE_LIVERY = int(SPN.UniqueLiverySwitchBool) & 0xFFFFFFFF
_CARBON_MASKED_LIVERY = int(SPN.MaskedLiveryBool) & 0xFFFFFFFF
_CARBON_WEAVE_A = int(SPN.WeaveColorTintA) & 0xFFFFFFFF
_CARBON_WEAVE_B = int(SPN.WeaveColorTintB) & 0xFFFFFFFF
_CARBON_WEAVE_MASK = int(SPN.WeaveMask) & 0xFFFFFFFF

_CARBON_WEAVE_DXIL = (
    "DXIL:car_carbonfiberCarLightScenario.pcdxil.pso:"
    "UniqueLiverySwitchBool(CB reg22.y / 0xF17A77BF)==0 forces lerp factor=0; "
    "albedo=lerp(WeaveColorTintA,WeaveColorTintB,WeaveMask.R); "
    "BaseColorAlpha t17 sample does not contribute"
)


@dataclass(frozen=True)
class ShaderBaseColorContract:
    """Cached activation contract keyed by shader archive / shaderbin identity."""

    shader_name: str
    shaderbin_hash: str
    scenario: str
    decide: Callable[..., tuple[ResolvedBaseColorSource, tuple[TextureBindingDecision, ...]]]


def _ev(*details: str, kind: str = "activation") -> tuple[ProvenanceDiagnostic, ...]:
    return tuple(
        ProvenanceDiagnostic(kind=kind, detail=d, source="materials.binding_activation")
        for d in details
        if d
    )


def _bool(params: dict, h: int) -> bool | None:
    p = params.get(h) or params.get(h & 0xFFFFFFFF)
    if p is None or getattr(p, "type", None) != 3:
        return None
    return bool(p.value)


def _color(params: dict, h: int):
    p = params.get(h) or params.get(h & 0xFFFFFFFF)
    if p is None or getattr(p, "type", None) not in (0, 1):
        return None
    value = getattr(p, "value", None)
    return value if isinstance(value, tuple) and len(value) >= 3 else None


def _paint_color(params: dict, stock):
    unique_color = _bool(params, SPN.UniqueBaseColorSwitchBool)
    unique_texture = _bool(params, SPN.UniqueBaseTextureSwitchBool)
    if unique_color is True and unique_texture is not True:
        value = _color(params, SPN.UniqueBaseColorColorParam)
        if value is not None:
            return (*value[:3], 1.0), "UniqueBaseColorColorParam"
    group = _bool(params, SPN.ColorGroupSwitchBool)
    if group is True:
        value = _color(params, SPN.PaintColorGroupColorParam)
        if value is not None:
            return (*value[:3], 1.0), "PaintColorGroupColorParam"
    value = _color(params, SPN.PaintColorColorParam)
    if value is not None:
        return (*value[:3], 1.0), "PaintColorColorParam"
    if stock is not None:
        return stock, "stock_paint"
    return None, None


def _decision(
    slot: ResolvedTextureSlot,
    activation: TextureBindingActivation,
    reason: str,
    evidence: tuple[ProvenanceDiagnostic, ...],
    controlling: tuple[int, ...] = (),
) -> TextureBindingDecision:
    return TextureBindingDecision(
        slot=slot,
        activation=activation,
        reason=reason,
        evidence=evidence,
        controlling_parameters=controlling,
    )


def _carbon_weave_composite(
    *,
    params: dict,
    weave_mask: ResolvedTextureSlot,
) -> ResolvedBaseColorSource | None:
    tint_a = _color(params, SPN.WeaveColorTintA)
    tint_b = _color(params, SPN.WeaveColorTintB)
    if tint_a is None or tint_b is None:
        return None
    a = (*tint_a[:3], 1.0)
    b = (*tint_b[:3], 1.0)
    weave = ResolvedWeaveComposite(
        tint_a=a,
        tint_b=b,
        mask=weave_mask,
        blend="lerp_a_b_mask_r",
        evidence=_ev(
            _CARBON_WEAVE_DXIL,
            "MatI:WeaveColorTintA+WeaveColorTintB+WeaveMask",
            "blend:rgb=A+mask.R*(B-A)",
        ),
    )
    return ResolvedBaseColorSource(
        kind=BaseColorSourceKind.WEAVE_COMPOSITE,
        weave=weave,
        evidence=weave.evidence,
    )


def decide_car_carbonfiber_base_color(
    *,
    params: dict,
    base_map: ResolvedTextureSlot | None,
    weave_mask: ResolvedTextureSlot | None,
    shaderbin_hash: str = "",
) -> tuple[ResolvedBaseColorSource, tuple[TextureBindingDecision, ...]]:
    """Proven Base Color selection for car_carbonfiber.

    UniqueLiverySwitchBool=false → WeaveMask composite; BaseColorAlpha inactive.
    UniqueLiverySwitchBool=true → conditional unresolved (livery mix not in clean).
    """
    decisions: list[TextureBindingDecision] = []
    unique_livery = _bool(params, SPN.UniqueLiverySwitchBool)
    masked_livery = _bool(params, SPN.MaskedLiveryBool)
    controls = (_CARBON_UNIQUE_LIVERY, _CARBON_MASKED_LIVERY, _CARBON_WEAVE_A, _CARBON_WEAVE_B)

    if base_map is not None and unique_livery is False:
        decisions.append(
            _decision(
                base_map,
                TextureBindingActivation.INACTIVE_PLACEHOLDER,
                "UniqueLiverySwitchBool=false zeroes BaseColorAlpha lerp factor",
                _ev(
                    _CARBON_WEAVE_DXIL,
                    f"MatI:UniqueLiverySwitchBool={unique_livery}",
                    f"MatI:MaskedLiveryBool={masked_livery}",
                    f"shaderbin_sha256={shaderbin_hash}" if shaderbin_hash else "",
                ),
                controls + (_CARBON_WEAVE_MASK,),
            )
        )
        if weave_mask is None:
            return (
                ResolvedBaseColorSource(
                    kind=BaseColorSourceKind.UNRESOLVED,
                    evidence=_ev(
                        "car_carbonfiber: WeaveMask required for WEAVE_COMPOSITE",
                        _CARBON_WEAVE_DXIL,
                    ),
                ),
                tuple(decisions),
            )
        decisions.append(
            _decision(
                weave_mask,
                TextureBindingActivation.ACTIVE,
                "WeaveMask.R blends WeaveColorTintA/B under UniqueLivery=false",
                _ev(_CARBON_WEAVE_DXIL, "role:weave_mask"),
                controls + (_CARBON_WEAVE_MASK,),
            )
        )
        source = _carbon_weave_composite(params=params, weave_mask=weave_mask)
        if source is None:
            return (
                ResolvedBaseColorSource(
                    kind=BaseColorSourceKind.UNRESOLVED,
                    evidence=_ev("car_carbonfiber: missing WeaveColorTintA/B"),
                ),
                tuple(decisions),
            )
        return source, tuple(decisions)

    if base_map is not None and unique_livery is True:
        decisions.append(
            _decision(
                base_map,
                TextureBindingActivation.CONDITIONAL_UNRESOLVED,
                "UniqueLiverySwitchBool=true enables BaseColorAlpha lerp; "
                "clean capability lacks proven livery/weave mix wiring",
                _ev(
                    _CARBON_WEAVE_DXIL,
                    f"MatI:UniqueLiverySwitchBool={unique_livery}",
                    f"MatI:MaskedLiveryBool={masked_livery}",
                ),
                controls,
            )
        )
        return (
            ResolvedBaseColorSource(
                kind=BaseColorSourceKind.UNRESOLVED,
                evidence=_ev(
                    "car_carbonfiber: UniqueLivery=true BaseColor path unsupported",
                    _CARBON_WEAVE_DXIL,
                ),
            ),
            tuple(decisions),
        )

    if base_map is not None:
        decisions.append(
            _decision(
                base_map,
                TextureBindingActivation.CONDITIONAL_UNRESOLVED,
                "UniqueLiverySwitchBool absent; cannot prove BaseColorAlpha activation",
                _ev(_CARBON_WEAVE_DXIL, "MatI:UniqueLiverySwitchBool missing"),
                controls,
            )
        )
        return (
            ResolvedBaseColorSource(
                kind=BaseColorSourceKind.UNRESOLVED,
                evidence=_ev("car_carbonfiber: UniqueLiverySwitchBool missing"),
            ),
            tuple(decisions),
        )

    # No BaseColorAlpha TXMP — still try weave if UniqueLivery false.
    if unique_livery is False and weave_mask is not None:
        decisions.append(
            _decision(
                weave_mask,
                TextureBindingActivation.ACTIVE,
                "WeaveMask composite without BaseColorAlpha TXMP",
                _ev(_CARBON_WEAVE_DXIL),
                controls + (_CARBON_WEAVE_MASK,),
            )
        )
        source = _carbon_weave_composite(params=params, weave_mask=weave_mask)
        if source is not None:
            return source, tuple(decisions)

    return (
        ResolvedBaseColorSource(
            kind=BaseColorSourceKind.UNRESOLVED,
            evidence=_ev("car_carbonfiber: no proven Base Color source"),
        ),
        tuple(decisions),
    )


def decide_paint_or_texture_base_color(
    *,
    params: dict,
    base_map: ResolvedTextureSlot | None,
    stock_paint,
) -> tuple[ResolvedBaseColorSource, tuple[TextureBindingDecision, ...]]:
    """Paint / UniqueBase / default albedo branch for non-carbon clean shaders."""
    decisions: list[TextureBindingDecision] = []
    paint, paint_name = _paint_color(params, stock_paint)
    unique_livery = _bool(params, SPN.UniqueLiverySwitchBool)
    unique_texture = _bool(params, SPN.UniqueBaseTextureSwitchBool)
    unique_color = _bool(params, SPN.UniqueBaseColorSwitchBool)

    uses_mat_paint = paint is not None and unique_livery is not True
    if uses_mat_paint:
        kind = (
            BaseColorSourceKind.INSTANCE_PAINT
            if paint_name in ("PaintColorColorParam", "PaintColorGroupColorParam", "stock_paint")
            else BaseColorSourceKind.MATERIAL_CONSTANT
        )
        if base_map is not None:
            decisions.append(
                _decision(
                    base_map,
                    TextureBindingActivation.INACTIVE_PLACEHOLDER,
                    f"paint/constant branch selected ({paint_name}); BaseColorAlpha bypassed",
                    _ev(
                        f"MatI:{paint_name}",
                        f"MatI:UniqueLiverySwitchBool={unique_livery}",
                        f"MatI:UniqueBaseColorSwitchBool={unique_color}",
                        f"MatI:UniqueBaseTextureSwitchBool={unique_texture}",
                    ),
                    (
                        int(SPN.UniqueBaseColorSwitchBool) & 0xFFFFFFFF,
                        int(SPN.UniqueBaseTextureSwitchBool) & 0xFFFFFFFF,
                        int(SPN.PaintColorColorParam) & 0xFFFFFFFF,
                        int(SPN.UniqueLiverySwitchBool) & 0xFFFFFFFF,
                    ),
                )
            )
        return (
            ResolvedBaseColorSource(
                kind=kind,
                color=paint,
                evidence=_ev(f"base_color:{kind.value}:{paint_name}"),
            ),
            tuple(decisions),
        )

    if unique_texture is False and unique_color is True:
        # Explicit unique colour with texture switch off and no paint — handled above
        # if UniqueBaseColorColorParam resolved into paint; otherwise unresolved.
        pass

    if base_map is not None:
        # Default albedo branch: DXIL samples BaseColorAlpha when paint/unique
        # livery do not select a competing constant path.
        decisions.append(
            _decision(
                base_map,
                TextureBindingActivation.ACTIVE,
                "BaseColorAlpha selected: no paint/unique-livery override branch",
                _ev(
                    "DXIL:CarLightScenario samples BaseColorAlpha as albedo",
                    f"MatI:UniqueLiverySwitchBool={unique_livery}",
                    f"MatI:UniqueBaseTextureSwitchBool={unique_texture}",
                    f"MatI:UniqueBaseColorSwitchBool={unique_color}",
                ),
                (
                    int(SPN.UniqueBaseTextureSwitchBool) & 0xFFFFFFFF,
                    int(SPN.UniqueLiverySwitchBool) & 0xFFFFFFFF,
                ),
            )
        )
        return (
            ResolvedBaseColorSource(
                kind=BaseColorSourceKind.TEXTURE,
                texture=base_map,
                evidence=_ev("base_color:texture:BaseColorAlpha"),
            ),
            tuple(decisions),
        )

    weave = _color(params, SPN.WeaveColorTintA) or _color(params, SPN.WeaveColorTintB)
    if weave is not None:
        color = (*weave[:3], 1.0)
        return (
            ResolvedBaseColorSource(
                kind=BaseColorSourceKind.MATERIAL_CONSTANT,
                color=color,
                evidence=_ev("base_color:material_constant:WeaveColorTint"),
            ),
            tuple(decisions),
        )

    return (
        ResolvedBaseColorSource(
            kind=BaseColorSourceKind.UNRESOLVED,
            evidence=_ev("no proven Base Color texture or constant"),
        ),
        tuple(decisions),
    )


@lru_cache(maxsize=64)
def contract_for_shader(shader_name: str, shaderbin_hash: str = "") -> str:
    """Return the contract family id (cached key for diagnostics / perf)."""
    name = (shader_name or "").lower()
    if name == "car_carbonfiber":
        return f"car_carbonfiber:{shaderbin_hash}:CarLightScenario"
    return f"default_paint_or_texture:{shaderbin_hash}:CarLightScenario"


def decide_base_color_source(
    *,
    shader_name: str,
    params: dict,
    base_map: ResolvedTextureSlot | None,
    weave_mask: ResolvedTextureSlot | None = None,
    stock_paint=None,
    shaderbin_hash: str = "",
) -> tuple[ResolvedBaseColorSource, tuple[TextureBindingDecision, ...]]:
    """Select Base Color using the cached family contract (no per-instance DXIL)."""
    family = (shader_name or "").lower()
    _ = contract_for_shader(family, shaderbin_hash)  # warm cache / identity
    if family == "car_carbonfiber":
        return decide_car_carbonfiber_base_color(
            params=params,
            base_map=base_map,
            weave_mask=weave_mask,
            shaderbin_hash=shaderbin_hash,
        )
    return decide_paint_or_texture_base_color(
        params=params,
        base_map=base_map,
        stock_paint=stock_paint,
    )
