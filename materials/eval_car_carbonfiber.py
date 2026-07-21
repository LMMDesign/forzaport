"""Evaluate car_carbonfiber MatI + proven contract → ForzaMaterialIR.

Blender-independent. Exact-SHA contract only (Milestone B1.5), mirrors
``eval_car_standard`` structure. Fixes two proven legacy bugs:

1. Weave UV (WeaveMask / WeaveNormal, TEXCOORD1) uses MatI ``UV_Orientation`` /
   ``U_Tiling`` / ``V_Tiling`` (CB reg14). The B1.5 first pass wrongly queried
   absent ``*UVtransformationRef`` hashes and defaulted to identity scale — that
   unproven ``(1,1)`` made the weave physically enormous. Missing U/V tiling now
   fails closed.
2. Normal uses ``WeaveNormal`` (t36) exclusively when present, not the flat
   ``Normal`` (t21) TXMP. The legacy resolver's ``_prefer`` keeps whichever of
   Normal/WeaveNormal is iterated first (ascending texture register — Normal
   t21 before WeaveNormal t36), so it silently picks the flat map.

UniqueLiverySwitchBool must be proven False; any other value (True or absent)
is livery-mix territory with no proven clean-IR expression and is rejected.
"""

from __future__ import annotations

from ..parsing.material import ShaderParameterName as SPN
from .forza_ir import (
    Channel,
    ConstantColor,
    ForzaMaterialIR,
    MeshUV,
    Mix,
    Multiply,
    NormalDecode,
    RotateUV,
    SamplerState,
    ScaleUV,
    ShaderIdentity,
    TextureSample,
    TextureSampleExpression,
)
from .model import ProvenanceDiagnostic as PD
from .name_hashes import MaterialNameError, require_name
from .shader_bindings import extract_bindings, resolve_binding_uv
from .texture_source import resolve_texture_source

CAR_CARBONFIBER_SHADERBIN_SHA256 = (
    "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3"
)
CAR_CARBONFIBER_PERMUTATION = "CarLightScenario"

APPROVED_PRODUCTION_RULES = frozenset(
    {
        "unique_livery_false_only",
        "weave_mask_lerp_tint_a_b",
        "weave_uv_orientation_u_v_tiling_cb_reg14",
        "weave_normal_preferred_over_flat_normal",
        "rmao_packing_rgb_texcoord0",
    }
)

PENDING_REVIEW_RULES = frozenset(
    {
        "unique_livery_true_or_absent",
        "flat_normal_only_no_weave_normal",
        "weavemask_anisotropy_tangent_blend",
        "base_plus_weave_normal_blend_full_ssa",
    }
)

_ADDR = {0: "REPEAT", 1: "REPEAT", 2: "MIRROR", 3: "EXTEND", 4: "CLIP"}


def _pd(*details: str, kind: str = "contract") -> tuple[PD, ...]:
    return tuple(
        PD(kind=kind, detail=d, source="materials.eval_car_carbonfiber")
        for d in details
        if d
    )


def is_car_carbonfiber_contract_identity(
    shader_name: str | None, shaderbin_sha256: str | None
) -> bool:
    return (shader_name or "").lower() == "car_carbonfiber" and (
        shaderbin_sha256 == CAR_CARBONFIBER_SHADERBIN_SHA256
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


def _float(params: dict, h: int) -> float | None:
    p = params.get(h) or params.get(h & 0xFFFFFFFF)
    if p is None:
        return None
    t = getattr(p, "type", None)
    if t == 2:
        return float(p.value)
    if t in (0, 1, 12) and isinstance(getattr(p, "value", None), tuple):
        return float(p.value[0])
    if t == 11 and isinstance(getattr(p, "value", None), tuple):
        return float(p.value[0])
    return None


def _path_exists(path: str, resolver) -> bool:
    import os

    resolved = resolver.resolve(path) if resolver is not None else path
    return bool(resolved and os.path.isfile(resolved))


def _address_for_bind(params: dict, spmp: dict, bind):
    import struct

    if bind is None or bind.sampler_reg is None:
        return None
    for h, reg in (spmp or {}).items():
        if int(reg) != int(bind.sampler_reg):
            continue
        p = params.get(h)
        raw = getattr(p, "samp", b"") if p is not None else b""
        if getattr(p, "type", None) != 7 or len(raw) < 8:
            continue
        u = struct.unpack_from("<I", raw, 0)[0]
        v = struct.unpack_from("<I", raw, 4)[0]
        return {"U": _ADDR.get(u, "REPEAT"), "V": _ADDR.get(v, "REPEAT")}
    return None


def _uv_index_for(bind, params: dict, txmp_name: str) -> int | None:
    if bind is None:
        return None
    uv = resolve_binding_uv(
        bind,
        params,
        txmp_name=txmp_name,
        shaderbin_sha256=CAR_CARBONFIBER_SHADERBIN_SHA256,
    )
    return int(uv) if uv is not None else None


def _weave_uv(params: dict) -> tuple[object | None, tuple[PD, ...], str | None]:
    """TEXCOORD1 → RotateUV(UV_Orientation°) → ScaleUV(U_Tiling, V_Tiling).

    Proven CB reg14 on car_carbonfiberCarLightScenario: .x=UV_Orientation (deg),
    .y=U_Tiling, .z=V_Tiling. DXIL applies rotate then anisotropic multiply.
    No post-scale pan is present in the SSA sample-coord chain.

    Returns (expr, evidence, reject_reason). ``expr`` is None when U/V tiling
    cannot be proven — callers must fail closed (never invent (1,1)).
    """
    angle = _float(params, SPN.UVOrientation)
    u_tile = _float(params, SPN.UTiling)
    v_tile = _float(params, SPN.VTiling)

    ev = _pd(
        "MatI:UV_Orientation(0x8B7343AB / CB reg14.x)="
        f"{angle if angle is not None else 'ABSENT'}",
        "MatI:U_Tiling(0x19A7D8F1 / CB reg14.y)="
        f"{u_tile if u_tile is not None else 'ABSENT'}",
        "MatI:V_Tiling(0x4A3D8375 / CB reg14.z)="
        f"{v_tile if v_tile is not None else 'ABSENT'}",
        "DXIL:TEXCOORD1→rotate(reg14.x°·π/180)→*(reg14.y,reg14.z); no pan in sample coords",
    )

    if u_tile is None or v_tile is None:
        return (
            None,
            ev,
            "car_carbonfiber: U_Tiling/V_Tiling must be present in MatI "
            "(unproven (1,1) fallback forbidden)",
        )
    if angle is None:
        return (
            None,
            ev,
            "car_carbonfiber: UV_Orientation must be present in MatI "
            "(unproven 0° default forbidden)",
        )

    angle_deg = float(angle)
    scale_u = float(u_tile)
    scale_v = float(v_tile)

    expr: object = MeshUV(
        index=1, evidence=_pd("slot:WeaveUV:TEXCOORD1 (fixed; not UVChoice)")
    )
    if angle_deg != 0.0:
        expr = RotateUV(
            source=expr,
            degrees=angle_deg,
            evidence=_pd(f"rotate {angle_deg}deg (UV_Orientation / CB reg14.x)"),
        )
    # Always emit ScaleUV when tiling is proven — including when both are 1.0
    # (rare; still an authored value). Graph compiler may omit identity nodes
    # only when both components are exactly 1.0.
    expr = ScaleUV(
        source=expr,
        scale=(scale_u, scale_v),
        evidence=_pd(f"scale=({scale_u},{scale_v}) (U_Tiling,V_Tiling / CB reg14.y/z)"),
    )
    return expr, ev, None


def _sample(
    *,
    h: int,
    path: str,
    uv,
    channels: tuple[str, ...],
    color_space: str,
    resolver,
    address=None,
    evidence: tuple[PD, ...] = (),
) -> TextureSample:
    src = resolve_texture_source(path, resolver) if path else None
    if src is None or not src.exists:
        raise RuntimeError(f"texture source missing for IR: 0x{h & 0xFFFFFFFF:08X}")
    expr = TextureSampleExpression(
        binding_name_hash=int(h) & 0xFFFFFFFF,
        source=src,
        uv=uv,
        channels=channels,
        color_space=color_space,
        sampler=SamplerState(
            address_u=(address or {}).get("U", "REPEAT"),
            address_v=(address or {}).get("V", "REPEAT"),
        ),
        evidence=evidence,
    )
    return TextureSample(sample=expr)


def evaluate_car_carbonfiber(
    *,
    name: str,
    material,
    resolver,
    media_root: str,
    production_mode: bool = True,
) -> ForzaMaterialIR:
    """Evaluate one car_carbonfiber instance into ForzaMaterialIR."""
    shader_name = getattr(material, "shader_name", None)
    params = getattr(material, "parameters", None) or {}
    cbmp = getattr(material, "cbmp", None) or {}
    txmp = getattr(material, "txmp", None) or {}
    spmp = getattr(material, "spmp", None) or {}

    bindings = extract_bindings(
        media_root=media_root,
        shader_name="car_carbonfiber",
        params=params,
        cbmp=cbmp,
        game_key="fh6",
    )
    sha = (bindings.source_hashes or {}).get("shaderbin_sha256")
    if not is_car_carbonfiber_contract_identity(shader_name, sha):
        raise RuntimeError(
            f"car_carbonfiber contract identity mismatch: shader={shader_name!r} sha={sha!r}"
        )

    identity = ShaderIdentity(
        shader_name="car_carbonfiber",
        archive_path=f"{media_root}/cars/_library/shaders/car_carbonfiber.zip".replace(
            "\\", "/"
        ),
        shaderbin_sha256=CAR_CARBONFIBER_SHADERBIN_SHA256,
        permutation=CAR_CARBONFIBER_PERMUTATION,
    )

    evidence: list[PD] = list(
        _pd(
            f"shaderbin_sha256={CAR_CARBONFIBER_SHADERBIN_SHA256}",
            f"permutation={CAR_CARBONFIBER_PERMUTATION}",
            f"production_mode={production_mode}",
            f"approved_rules={sorted(APPROVED_PRODUCTION_RULES)}",
            f"pending_review_rules={sorted(PENDING_REVIEW_RULES)}",
        )
    )

    unique_livery = _bool(params, SPN.UniqueLiverySwitchBool)
    evidence.extend(
        _pd(f"MatI:UniqueLiverySwitchBool={unique_livery} (CB reg22.y / 0xF17A77BF)")
    )
    if unique_livery is not False:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_carbonfiber: UniqueLiverySwitchBool must be proven False for "
                f"the weave contract (got {unique_livery!r}); unique-livery base "
                "color mix has no proven clean-IR expression (pending_review)",
            ),
        )

    # Locate WeaveMask / WeaveNormal / flat Normal / RoughMetalAO by proven NameHash
    # (never by filename/register heuristics — registers vary by shaderbin build).
    weave_mask = weave_normal = flat_normal = rmao = None
    for h, treg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
        p = params.get(h)
        if p is None or getattr(p, "type", None) != 6:
            continue
        try:
            pname = require_name(h, context=f"car_carbonfiber TXMP t{treg}")
        except MaterialNameError:
            continue
        path = getattr(p, "path", "") or ""
        row = (h, int(treg), path)
        if pname == "WeaveMask":
            weave_mask = row
        elif pname == "WeaveNormal":
            weave_normal = row
        elif pname == "Normal":
            flat_normal = row
        elif pname == "RoughMetalAO":
            rmao = row

    tint_a = _color(params, SPN.WeaveColorTintA)
    tint_b = _color(params, SPN.WeaveColorTintB)
    if tint_a is None or tint_b is None:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_carbonfiber: missing WeaveColorTintA/B constants",
            ),
        )

    if weave_mask is None or not weave_mask[2] or not _path_exists(weave_mask[2], resolver):
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_carbonfiber: WeaveMask texture missing/unresolved",
            ),
        )
    wm_hash, wm_treg, wm_path = weave_mask
    wm_bind = bindings.textures.get(int(wm_treg))
    wm_uv = _uv_index_for(wm_bind, params, "WeaveMask")
    if wm_uv is None:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"car_carbonfiber: WeaveMask t{wm_treg} has no proven UV in DXIL",
            ),
        )
    if wm_uv != 1:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"car_carbonfiber: WeaveMask t{wm_treg} DXIL UV=TEXCOORD{wm_uv}, "
                "expected TEXCOORD1 (contract drift)",
            ),
        )

    weave_uv_expr, weave_uv_evidence, weave_uv_reject = _weave_uv(params)
    evidence.extend(weave_uv_evidence)
    if weave_uv_reject or weave_uv_expr is None:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(weave_uv_reject or "car_carbonfiber: weave UV unproven",),
        )

    mask_sample = _sample(
        h=wm_hash,
        path=wm_path,
        uv=weave_uv_expr,
        channels=("r",),
        color_space="Non-Color",
        resolver=resolver,
        address=_address_for_bind(params, spmp, wm_bind),
        evidence=_pd(
            f"TXMP:0x{wm_hash & 0xFFFFFFFF:08X}:WeaveMask",
            f"DXIL:t{wm_treg}:TEXCOORD1",
        )
        + weave_uv_evidence,
    )
    mask_channel = Channel(
        source=mask_sample,
        channel="r",
        evidence=_pd("WeaveMask.R blend factor"),
    )
    base_color = Mix(
        a=ConstantColor(rgba=(*tint_a[:3], 1.0)),
        b=ConstantColor(rgba=(*tint_b[:3], 1.0)),
        factor=mask_channel,
        evidence=_pd(
            "albedo=lerp(WeaveColorTintA,WeaveColorTintB,WeaveMask.R)",
            "DXIL:CarLightScenario UniqueLiverySwitchBool=false path; "
            "BaseColorAlpha t17 does not contribute",
        ),
    )

    # Normal: prefer WeaveNormal (t36) over flat Normal (t21) when both are
    # present — the legacy resolver's `_prefer` keeps whichever is iterated
    # first by ascending texture register (flat Normal), which is the bug.
    normal = None
    if weave_normal is not None and weave_normal[2] and _path_exists(weave_normal[2], resolver):
        wn_hash, wn_treg, wn_path = weave_normal
        wn_bind = bindings.textures.get(int(wn_treg))
        wn_uv = _uv_index_for(wn_bind, params, "WeaveNormal")
        if wn_uv is None:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(
                    f"car_carbonfiber: WeaveNormal t{wn_treg} has no proven UV in DXIL",
                ),
            )
        if wn_uv != 1:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(
                    f"car_carbonfiber: WeaveNormal t{wn_treg} DXIL UV=TEXCOORD{wn_uv}, "
                    "expected TEXCOORD1 (contract drift)",
                ),
            )
        intensity = _float(params, SPN.WeaveNormalIntensity)
        intensity_val = float(intensity) if intensity is not None else 1.0
        wn_sample = _sample(
            h=wn_hash,
            path=wn_path,
            uv=weave_uv_expr,
            channels=("r", "g", "b"),
            color_space="Non-Color",
            resolver=resolver,
            address=_address_for_bind(params, spmp, wn_bind),
            evidence=_pd(
                f"TXMP:0x{wn_hash & 0xFFFFFFFF:08X}:WeaveNormal",
                f"DXIL:t{wn_treg}:TEXCOORD1 (SAME weave UV as WeaveMask)",
            ),
        )
        normal = NormalDecode(
            source=wn_sample,
            strength=intensity_val,
            evidence=_pd(
                "normal_unpack:xy=sample*2-1; z=sqrt(saturate(1-x^2-y^2)); "
                f"xy*=intensity({intensity_val}) CB reg15.w / WeaveNormal_Intensity",
                "preferred over flat Normal t21 when UniqueLivery=false "
                "(fixes legacy _prefer-keeps-first-Normal bug)",
                f"flat_normal_also_sampled_in_dxil={flat_normal is not None}",
            ),
        )
    else:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_carbonfiber: WeaveNormal TXMP missing; flat-Normal-only "
                "fallback is not an approved production rule (pending_review; "
                "do not invent bump from mask)",
            ),
        )

    if rmao is None or not rmao[2] or not _path_exists(rmao[2], resolver):
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=("car_carbonfiber: RoughMetalAO texture missing",),
        )
    rmao_hash, rmao_treg, rmao_path = rmao
    rmao_bind = bindings.textures.get(int(rmao_treg))
    rmao_uv = _uv_index_for(rmao_bind, params, "RoughMetalAO")
    if rmao_uv is None:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"car_carbonfiber: RoughMetalAO t{rmao_treg} has no proven UV in DXIL",
            ),
        )
    if rmao_uv != 0:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"car_carbonfiber: RoughMetalAO t{rmao_treg} DXIL UV=TEXCOORD{rmao_uv}, "
                "expected TEXCOORD0 (contract drift)",
            ),
        )
    rmao_sample = _sample(
        h=rmao_hash,
        path=rmao_path,
        uv=MeshUV(index=0, evidence=_pd("slot:RoughMetalAO:TEXCOORD0")),
        channels=("r", "g", "b"),
        color_space="Non-Color",
        resolver=resolver,
        address=_address_for_bind(params, spmp, rmao_bind),
        evidence=_pd(
            f"TXMP:0x{rmao_hash & 0xFFFFFFFF:08X}:RoughMetalAO",
            f"DXIL:t{rmao_treg}:TEXCOORD0",
            "packing:R=roughness,G=metallic,B=AO",
        ),
    )
    roughness = Channel(source=rmao_sample, channel="r", evidence=_pd("RMAO.R=roughness"))
    metallic = Channel(source=rmao_sample, channel="g", evidence=_pd("RMAO.G=metallic"))
    ao = Channel(source=rmao_sample, channel="b", evidence=_pd("RMAO.B=AO"))
    base_color = Multiply(
        a=base_color,
        b=ao,
        evidence=_pd("base_color *= AO (Principled prep)"),
    )

    return ForzaMaterialIR(
        shader=identity,
        base_color=base_color,
        normal=normal,
        roughness=roughness,
        metallic=metallic,
        ambient_occlusion=ao,
        opacity=None,
        evidence=tuple(evidence),
        rejection_reasons=(),
    )
