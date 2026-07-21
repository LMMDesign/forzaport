"""UV Conformance Foundation — typed contracts, proof status, effective values.

Milestone: UV Conformance Foundation (blocks further visual one-offs / B2).

Invariant: every TextureSample must carry an explicit UV expression whose scale
and TEXCOORD are positively proven, proven-identity, or unresolved. Accidental
identity fallbacks are forbidden on production paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UVProofStatus(str, Enum):
    PROVEN_TRANSFORM = "PROVEN_TRANSFORM"
    PROVEN_IDENTITY = "PROVEN_IDENTITY"
    UNRESOLVED = "UNRESOLVED"


class UVValueSourceCategory(str, Enum):
    MATI_EXPLICIT = "MATI_EXPLICIT"
    MATERIAL_TEMPLATE_INHERITED = "MATERIAL_TEMPLATE_INHERITED"
    SHADER_DEFAULT = "SHADER_DEFAULT"
    CONTRACT_CONSTANT = "CONTRACT_CONSTANT"
    DERIVED_EXPRESSION = "DERIVED_EXPRESSION"
    UNRESOLVED = "UNRESOLVED"


# Well-known NameHashes shared across car_standard / car_standard_fabric /
# car_carbonfiber MatI blobs (not filename rules).
UV_NAMEHASH_U_TILING = 0x19A7D8F1
UV_NAMEHASH_V_TILING = 0x4A3D8375
UV_NAMEHASH_UV_ORIENTATION = 0x8B7343AB

# Per-binding Override*TilingOnOff + *TilingOverride (corpus-proven hashes).
UV_TILING_OVERRIDE_BY_PARAM: dict[str, tuple[int, int]] = {
    "BaseColorAlpha": (0xB8E61E16, 0xB99646E7),
    "BaseColorAlpha_1": (0xB8E61E16, 0xB99646E7),
    "RoughMetalAO": (0xECCEB8F9, 0x8BAB96B3),
    "Normal": (0x1B003865, 0xF383EB56),
    "Alpha": (0x090ABF6B, 0x4CCD7F85),
}


@dataclass(frozen=True)
class SampleSiteId:
    """Identity for one texture sample instruction site (not merely t-reg)."""

    shader_sha256: str
    permutation: str
    texture_register: int
    sampler_register: int | None
    sample_site_index: int = 0
    pass_name: str = ""

    def as_key(self) -> str:
        smp = "none" if self.sampler_register is None else str(self.sampler_register)
        return (
            f"{self.shader_sha256[:12]}|{self.permutation}|t{self.texture_register}"
            f"|s{smp}|i{self.sample_site_index}"
        )


@dataclass(frozen=True)
class EffectiveUVValue:
    """One resolved UV scalar/vector with full provenance."""

    semantic_name: str
    name_hash: int | None
    raw_type: int | None
    raw_value: Any
    decoded_value: Any
    source_category: UVValueSourceCategory
    source_file: str = ""
    cb_register: str = ""
    controlling_switch: str | None = None
    fallback_reason: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextureSampleUVContract:
    """Source-independent UV contract for one sample site."""

    shader_sha256: str
    shader_family: str
    permutation: str
    pass_name: str
    sample_site_id: str
    texture_register: int
    sampler_register: int | None
    binding_name_hash: int | None
    semantic_role: str
    texcoord_index: int | None
    texcoord_components: tuple[str, ...]
    transform_operations: tuple[str, ...]
    transform_order: tuple[str, ...]
    parameter_sources: tuple[EffectiveUVValue, ...]
    defaults: tuple[EffectiveUVValue, ...] = ()
    switches: tuple[EffectiveUVValue, ...] = ()
    proof_status: UVProofStatus = UVProofStatus.UNRESOLVED
    evidence: tuple[str, ...] = ()
    u_scale: float | None = None
    v_scale: float | None = None
    rotation_degrees: float | None = None
    pan_u: float | None = None
    pan_v: float | None = None
    wrap_u: str | None = None
    wrap_v: str | None = None


@dataclass(frozen=True)
class ResolvedUVScale:
    """Result of central UV scale precedence resolution."""

    scale: tuple[float, float] | None
    proof_status: UVProofStatus
    u: EffectiveUVValue | None = None
    v: EffectiveUVValue | None = None
    rejection: str | None = None
    evidence: tuple[str, ...] = ()


def _param(params: dict, h: int):
    return params.get(int(h))


def _bool_param(params: dict, h: int) -> bool | None:
    p = _param(params, h)
    if p is None or getattr(p, "type", None) != 3:
        return None
    value = getattr(p, "value", None)
    if isinstance(value, bool):
        return value
    return None


def _float_param(params: dict, h: int) -> float | None:
    p = _param(params, h)
    if p is None or getattr(p, "type", None) != 2:
        return None
    value = getattr(p, "value", None)
    return float(value) if isinstance(value, (int, float)) else None


def _vec2_param(params: dict, h: int) -> tuple[float, float] | None:
    p = _param(params, h)
    if p is None or getattr(p, "type", None) != 11:
        return None
    value = getattr(p, "value", None)
    if isinstance(value, tuple) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return None


def resolve_uv_scale(
    params: dict,
    *,
    param_name: str | None = None,
    tiling_cb_hashes: tuple[int, ...] | list[int] | None = None,
    require_proven: bool = True,
) -> ResolvedUVScale:
    """Central UV U/V scale resolver (effective-value precedence).

    Proven order for Override*=false shared branch (car_standard / fabric DXIL):

        1. Explicit MatI U_Tiling / V_Tiling (NameHashes)
        2. Per-binding Override vec2 **only when** Override*TilingOnOff is true
        3. Otherwise UNRESOLVED (never invent identity)

    Critical bug this replaces: treating the first type-11 vec2 in
    ``tiling_cb_hashes`` as scale even when Override*=false (those vec2s are
    often authored as (1,1) and were returning accidental identity while MatI
    U/V_Tiling held the real scale, e.g. fabric microfibre @ 3×3).
    """
    evidence: list[str] = []
    hashes = tuple(int(h) for h in (tiling_cb_hashes or ()))

    # --- Override-true branch (pending for production IR; resolve when flagged)
    ov = UV_TILING_OVERRIDE_BY_PARAM.get(param_name or "")
    if ov is not None:
        ov_bool_h, ov_vec_h = ov
        flag = _bool_param(params, ov_bool_h)
        evidence.append(f"OverrideTilingOnOff({hex(ov_bool_h)})={flag} param={param_name}")
        if flag is True:
            xy = _vec2_param(params, ov_vec_h)
            if xy is None:
                return ResolvedUVScale(
                    scale=None,
                    proof_status=UVProofStatus.UNRESOLVED,
                    rejection=(
                        f"Override*TilingOnOff=true but override vec2 absent "
                        f"({hex(ov_vec_h)}) for {param_name}"
                    ),
                    evidence=tuple(evidence),
                )
            # True-branch is pending independent DXIL for some families; still
            # surface the authored override values with provenance.
            u_ev = EffectiveUVValue(
                semantic_name="OverrideTiling.x",
                name_hash=ov_vec_h,
                raw_type=11,
                raw_value=xy,
                decoded_value=xy[0],
                source_category=UVValueSourceCategory.MATI_EXPLICIT,
                controlling_switch=f"Override*TilingOnOff={flag}",
                evidence=("override_true_branch",),
            )
            v_ev = EffectiveUVValue(
                semantic_name="OverrideTiling.y",
                name_hash=ov_vec_h,
                raw_type=11,
                raw_value=xy,
                decoded_value=xy[1],
                source_category=UVValueSourceCategory.MATI_EXPLICIT,
                controlling_switch=f"Override*TilingOnOff={flag}",
                evidence=("override_true_branch",),
            )
            return ResolvedUVScale(
                scale=xy,
                proof_status=UVProofStatus.PROVEN_TRANSFORM
                if xy != (1.0, 1.0)
                else UVProofStatus.PROVEN_IDENTITY,
                u=u_ev,
                v=v_ev,
                evidence=tuple(evidence)
                + ("scale_from_override_vec2_when_switch_true",),
            )

    # --- Shared U_Tiling / V_Tiling (MatI explicit)
    u = _float_param(params, UV_NAMEHASH_U_TILING)
    v = _float_param(params, UV_NAMEHASH_V_TILING)
    if u is not None and v is not None:
        # Prefer when DXIL listed these hashes OR MatI carries them explicitly.
        in_dxil = (
            UV_NAMEHASH_U_TILING in hashes and UV_NAMEHASH_V_TILING in hashes
        )
        evidence.append(
            f"MatI:U_Tiling({hex(UV_NAMEHASH_U_TILING)})={u} "
            f"V_Tiling({hex(UV_NAMEHASH_V_TILING)})={v} "
            f"dxil_hash_hit={in_dxil}"
        )
        # Ignore type-11 (1,1) override vectors when switch is false / absent —
        # they must not win over MatI U/V.
        ignored = []
        for h in hashes:
            if h in (UV_NAMEHASH_U_TILING, UV_NAMEHASH_V_TILING, UV_NAMEHASH_UV_ORIENTATION):
                continue
            if _vec2_param(params, h) is not None:
                ignored.append(hex(h))
        if ignored:
            evidence.append(
                "ignored_unswitched_override_vec2_hashes=" + ",".join(ignored)
            )
        scale = (float(u), float(v))
        status = (
            UVProofStatus.PROVEN_IDENTITY
            if scale == (1.0, 1.0)
            else UVProofStatus.PROVEN_TRANSFORM
        )
        # Without DXIL hash hit, MatI is still explicit but sample-site link is
        # weaker — keep PROVEN when NameHashes are the universal tiling pair
        # present in MatI (same hashes car_standard / fabric / carbon use).
        u_ev = EffectiveUVValue(
            semantic_name="U_Tiling",
            name_hash=UV_NAMEHASH_U_TILING,
            raw_type=2,
            raw_value=u,
            decoded_value=float(u),
            source_category=UVValueSourceCategory.MATI_EXPLICIT,
            evidence=("shared_U_V_tiling",),
        )
        v_ev = EffectiveUVValue(
            semantic_name="V_Tiling",
            name_hash=UV_NAMEHASH_V_TILING,
            raw_type=2,
            raw_value=v,
            decoded_value=float(v),
            source_category=UVValueSourceCategory.MATI_EXPLICIT,
            evidence=("shared_U_V_tiling",),
        )
        return ResolvedUVScale(
            scale=scale,
            proof_status=status,
            u=u_ev,
            v=v_ev,
            evidence=tuple(evidence),
        )

    if require_proven:
        return ResolvedUVScale(
            scale=None,
            proof_status=UVProofStatus.UNRESOLVED,
            rejection=(
                "U_Tiling/V_Tiling absent from MatI and no active Override "
                "vec2; refusing accidental identity (1,1)"
            ),
            evidence=tuple(evidence),
        )
    return ResolvedUVScale(
        scale=None,
        proof_status=UVProofStatus.UNRESOLVED,
        rejection="uv_scale_unresolved",
        evidence=tuple(evidence),
    )


def eval_uv_transform_point(
    uv: tuple[float, float],
    *,
    scale: tuple[float, float],
    rotation_degrees: float = 0.0,
    pan: tuple[float, float] = (0.0, 0.0),
    order: tuple[str, ...] = ("rotate", "scale", "offset"),
) -> tuple[float, float]:
    """Numerical UV transform for regression tests (nontrivial fixtures).

    Proven car_standard / carbon order: RotateUV → ScaleUV → OffsetUV.
    ``transformed = scale * rotate(uv) + pan`` for the default order.
    """
    import math

    u, v = float(uv[0]), float(uv[1])
    for op in order:
        if op == "rotate":
            rad = math.radians(float(rotation_degrees))
            c, s = math.cos(rad), math.sin(rad)
            u, v = (u * c - v * s), (u * s + v * c)
        elif op == "scale":
            u, v = u * float(scale[0]), v * float(scale[1])
        elif op == "offset":
            u, v = u + float(pan[0]), v + float(pan[1])
        else:
            raise ValueError(f"unknown UV op {op!r}")
    return (u, v)
