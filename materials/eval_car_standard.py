"""Evaluate car_standard MatI + proven contract → ForzaMaterialIR.

Blender-independent. Production cutover uses ``production_mode`` to enable only
reviewed rule groups; full evaluation always records complete evidence for diffs.
"""

from __future__ import annotations

from ..parsing.material import ShaderParameterName as SPN
from .capabilities import resolve_uv_choice_texcoord
from .forza_ir import (
    Channel,
    Clamp,
    ConstantColor,
    ConstantScalar,
    ForzaMaterialIR,
    MeshUV,
    Mix,
    Multiply,
    NormalDecode,
    RotateUV,
    ScaleUV,
    SelectUV,
    SamplerState,
    ShaderIdentity,
    ShadingAttenuation,
    TextureSample,
    TextureSampleExpression,
)
from .model import (
    BaseColorSourceKind,
    ProvenanceDiagnostic as PD,
    ResolvedTextureSlot,
)
from .resolver import MaterialCapabilityResolver, _bool, _color
from .shader_bindings import extract_bindings
from .texture_source import resolve_texture_source

CAR_STANDARD_SHADERBIN_SHA256 = (
    "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16"
)
CAR_STANDARD_PERMUTATION = "CarLightScenario"

APPROVED_PRODUCTION_RULES = frozenset(
    {
        "uvchoice_on_ch1_off_ch2",
        "base_color_txmp_or_paint_activation",
        "base_color_tint_multiply",
        "base_color_tint_mode_metal_lerp",
        "rmao_packing_rgb",
        "normal_sample",
        "alpha_mat_i_modes",
        "tiling_from_cb_when_override_false_uses_uv_policy",
        "uv_rotate_scale_cb_reg19",
        "bypass_sfx_fx_unbound",
        "glass_switch_false_only",
    }
)

PENDING_REVIEW_RULES = frozenset(
    {
        "base_color_override_switch",
        "override_tiling_true_branch",
        "bypass_sfx_true_surfacefx",
        "glass_switch_true",
    }
)

# Per-binding Override*TilingOnOff + *TilingOverride (CB reg29 / reg14-15).
_TILING_OVERRIDE_BY_PARAM: dict[str, tuple[int, int]] = {
    "BaseColorAlpha": (0xB8E61E16, 0xB99646E7),
    "BaseColorAlpha_1": (0xB8E61E16, 0xB99646E7),
    "RoughMetalAO": (0xECCEB8F9, 0x8BAB96B3),
    "Normal": (0x1B003865, 0xF383EB56),
    "Alpha": (0x090ABF6B, 0x4CCD7F85),
}


def _pd(*details: str, kind: str = "contract") -> tuple[PD, ...]:
    return tuple(
        PD(kind=kind, detail=d, source="materials.eval_car_standard")
        for d in details
        if d
    )


def is_car_standard_contract_identity(
    shader_name: str | None, shaderbin_sha256: str | None
) -> bool:
    return (shader_name or "").lower() == "car_standard" and (
        shaderbin_sha256 == CAR_STANDARD_SHADERBIN_SHA256
    )


def _mesh_uv(index: int, evidence: tuple[PD, ...] = ()) -> MeshUV:
    return MeshUV(index=int(index), evidence=evidence)


def _float(params: dict, h: int) -> float | None:
    p = params.get(h)
    if p is None or getattr(p, "type", None) != 2:
        return None
    value = getattr(p, "value", None)
    return float(value) if isinstance(value, (int, float)) else None


def _vec2(params: dict, h: int) -> tuple[float, float] | None:
    p = params.get(h)
    if p is None or getattr(p, "type", None) != 11:
        return None
    value = getattr(p, "value", None)
    if isinstance(value, tuple) and len(value) >= 2:
        return (float(value[0]), float(value[1]))
    return None


def _uv_scale_for_slot(
    slot: ResolvedTextureSlot,
    params: dict,
    *,
    production_mode: bool,
) -> tuple[tuple[float, float], tuple[PD, ...], str | None]:
    """Shared U/V tiling from CB reg19 (corpus-proven when Override*=false).

    Override*=true branches are **pending** — reject in production_mode.
    Returns (scale_xy, evidence, reject_reason).
    """
    u = _float(params, SPN.UTiling)
    v = _float(params, SPN.VTiling)
    if u is None or v is None:
        return (
            (1.0, 1.0),
            (),
            "car_standard: U_Tiling/V_Tiling must be present in MatI "
            "(CB reg19.z/w; unproven (1,1) fallback removed)",
        )
    base_scale = (float(u), float(v))
    evidence = list(
        _pd(
            f"MatI:U_Tiling(0x19A7D8F1 / CB reg19.z)={u}",
            f"MatI:V_Tiling(0x4A3D8375 / CB reg19.w)={v}",
            "uv_branch=shared_U_V_tiling (Override*=false corpus-proven)",
        )
    )
    ov = _TILING_OVERRIDE_BY_PARAM.get(slot.param_name or "")
    if ov is None:
        return base_scale, tuple(evidence), None
    ov_bool_h, ov_vec_h = ov
    flag = _bool(params, ov_bool_h)
    evidence.extend(
        _pd(
            f"MatI:OverrideTilingOnOff({hex(ov_bool_h)})={flag} "
            f"param={slot.param_name} (true=pending_review)"
        )
    )
    if flag is True:
        if production_mode:
            return (
                base_scale,
                tuple(evidence),
                "car_standard: Override*TilingOnOff=true not production-approved "
                f"(pending independent DXIL slice) param={slot.param_name} "
                f"hash={hex(ov_bool_h)}",
            )
        ov_xy = _vec2(params, ov_vec_h)
        if ov_xy is None:
            return (
                base_scale,
                tuple(evidence),
                f"car_standard: Override tiling true but override vector absent "
                f"({hex(ov_vec_h)}) for {slot.param_name}",
            )
        evidence.extend(
            _pd(
                f"MatI:TilingOverride({hex(ov_vec_h)})={ov_xy} "
                "(non-production audit path only)"
            )
        )
        return ov_xy, tuple(evidence), None
    return base_scale, tuple(evidence), None


def _uv_expr_for_slot(
    slot: ResolvedTextureSlot,
    params: dict,
    *,
    production_mode: bool,
    revision: str,
    uv_cache: dict | None = None,
):
    """TEXCOORD → RotateUV(UV_Orientation°) → ScaleUV(U_Tiling, V_Tiling).

    B1.75 corpus-proven shared branch only. Override-true rejected in production.
    ``revision=\"b1\"`` emits MeshUV (+ resolver slot.tiling ScaleUV) without
    MatI rotate/scale — for B1→B1.75 conformance diffs only.

    When Override*=false and bindings share TEXCOORD + U/V tiling + orientation,
    reuse one UV expression object via ``uv_cache`` (IR structural sharing).
    Returns (expr, reject_reason).
    """
    uv_choice = resolve_uv_choice_texcoord(params)
    texcoord = int(str(slot.texcoord).replace("TEXCOORD", "") or "0")
    base = _mesh_uv(
        texcoord,
        evidence=_pd(
            f"slot:{slot.param_name}:TEXCOORD{texcoord}",
            uv_choice[1].detail if uv_choice else "",
        ),
    )
    if uv_choice is not None and slot.param_name in (
        "BaseColorAlpha",
        "BaseColorAlpha_1",
        "Alpha",
        "Normal",
        "RoughMetalAO",
    ):
        true_uv = _mesh_uv(0, evidence=_pd("UVChoice true → TEXCOORD0"))
        false_uv = _mesh_uv(1, evidence=_pd("UVChoice false → TEXCOORD1"))
        cond = ConstantScalar(
            value=1.0 if int(uv_choice[0]) == 0 else 0.0,
            evidence=(uv_choice[1],),
        )
        _ = SelectUV(
            condition=cond,
            a=true_uv,
            b=false_uv,
            evidence=(uv_choice[1],),
        )
        base = _mesh_uv(texcoord, evidence=_pd(uv_choice[1].detail))

    if revision == "b1":
        # Pre-B1.75 IR: MeshUV only; ScaleUV only when resolver slot.tiling ≠ (1,1).
        tiling = slot.tiling or (1.0, 1.0)
        if tiling != (1.0, 1.0):
            return (
                ScaleUV(
                    source=base,
                    scale=(float(tiling[0]), float(tiling[1])),
                    evidence=_pd(f"b1_baseline tiling:{tiling}"),
                ),
                None,
            )
        return base, None

    orient = _float(params, SPN.UVOrientation)
    if orient is None:
        return None, (
            "car_standard: UV_Orientation must be present in MatI "
            "(CB reg19.y; unproven 0° fallback removed)"
        )
    scale, scale_ev, scale_reject = _uv_scale_for_slot(
        slot, params, production_mode=production_mode
    )
    if scale_reject:
        return None, scale_reject

    # Shared-branch cache key: same MeshUV index + rotate + scale ⇒ one IR node.
    cache_key = (texcoord, float(orient), float(scale[0]), float(scale[1]), "shared")
    if uv_cache is not None and cache_key in uv_cache:
        return uv_cache[cache_key], None

    rotated = RotateUV(
        source=base,
        degrees=float(orient),
        evidence=_pd(
            f"rotate {float(orient)}deg (UV_Orientation / CB reg19.y)"
        ),
    )
    expr = ScaleUV(
        source=rotated,
        scale=scale,
        evidence=scale_ev
        + _pd(f"scale={scale} (shared U_Tiling,V_Tiling / CB reg19)"),
    )
    if uv_cache is not None:
        uv_cache[cache_key] = expr
    return expr, None


def _sample_from_slot(
    slot: ResolvedTextureSlot,
    *,
    params: dict,
    channels: tuple[str, ...],
    color_space: str,
    resolver,
    production_mode: bool,
    revision: str,
    uv_cache: dict | None = None,
) -> tuple[TextureSample | None, str | None]:
    src = resolve_texture_source(slot.path, resolver) if slot.path else None
    if src is None or not src.exists:
        return None, f"texture source missing for IR: {slot.param_name}"
    uv_expr, uv_reject = _uv_expr_for_slot(
        slot,
        params,
        production_mode=production_mode,
        revision=revision,
        uv_cache=uv_cache,
    )
    if uv_reject:
        return None, uv_reject
    assert uv_expr is not None
    address = dict(slot.address) if slot.address else {}
    sample = TextureSampleExpression(
        binding_name_hash=int(slot.param_hash) & 0xFFFFFFFF,
        source=src,
        uv=uv_expr,
        channels=channels,
        color_space=color_space,
        sampler=SamplerState(
            address_u=address.get("U", "REPEAT"),
            address_v=address.get("V", "REPEAT"),
        ),
        evidence=slot.evidence,
    )
    return TextureSample(sample=sample), None


def evaluate_tint_mode_rgb(
    *,
    texture_rgb: tuple[float, float, float],
    tint_rgb: tuple[float, float, float],
    tint_multiplier: float,
    tint_mode: tuple[float, float, float, float] | tuple[float, float],
    metallic: float,
) -> tuple[float, float, float]:
    """Numerical Base Color tint (DXIL-proven), linear RGB, with saturate.

    factor = TintMode.x * (1 - lerp(metallic, 1-metallic, TintMode.y))
    output = lerp(saturate(tex * tint * mult), tex, factor)
    """
    mode_x = float(tint_mode[0])
    mode_y = float(tint_mode[1]) if len(tint_mode) > 1 else 0.0
    m = float(metallic)
    w = m + (1.0 - 2.0 * m) * mode_y  # lerp(m, 1-m, mode_y)
    factor = mode_x * (1.0 - w)
    out = []
    for t, c in zip(texture_rgb, tint_rgb):
        tinted = max(0.0, min(1.0, float(t) * float(c) * float(tint_multiplier)))
        out.append(tinted + factor * (float(t) - tinted))
    return (out[0], out[1], out[2])


def _apply_base_color_tint(
    tex: TextureSample,
    *,
    params: dict,
    metallic: Channel | None,
) -> tuple[object, tuple[PD, ...], str | None]:
    """DXIL: tinted=saturate(tex×Tint×Mult); albedo=lerp(tinted,tex, mode.x×(1-w)).

    Observed corpus TintMode collapses (verified numerically in tests):
      [1,1,0,0] → lerp(tinted, tex, metal)   # Mix(tinted, tex, metal)
      [1,0,0,0] → lerp(tex, tinted, metal)   # Mix(tex, tinted, metal)
      [0,0,0,0] → tinted
    Metallic = RoughMetalAO.g (channel g/y). Applied before AO multiply.
    """
    tint = _color(params, SPN.BaseColorTint)
    if tint is None:
        return None, (), "car_standard: BaseColor_Tint missing for textured base"
    mult = _float(params, SPN.BaseColorTintMultiplier)
    if mult is None:
        return None, (), "car_standard: BaseColor_TintMultiplier missing"
    mode = _color(params, SPN.BaseColorTintMode)
    if mode is None:
        return None, (), "car_standard: BaseColor_TintMode missing"
    mode_x = float(mode[0])
    mode_y = float(mode[1]) if len(mode) > 1 else 0.0
    tint_eff = (
        float(tint[0]) * mult,
        float(tint[1]) * mult,
        float(tint[2]) * mult,
        float(tint[3]) if len(tint) > 3 else 1.0,
    )
    evidence = _pd(
        f"MatI:BaseColor_Tint(0x53A946B6 / CB reg1)={tint}",
        f"MatI:BaseColor_TintMultiplier(0x6B242133 / CB reg19.x)={mult}",
        f"MatI:BaseColor_TintMode(0x5EA395A8 / CB reg2)={mode}",
        "DXIL: saturate(tex * Tint * Mult); TintMode.x/y metal lerp; metal=RMAO.g",
        "AO multiply is applied after tint/TintMode (Principled prep)",
    )
    tinted = Multiply(
        a=tex,
        b=ConstantColor(rgba=tint_eff, evidence=_pd("BaseColor_Tint*Multiplier")),
        evidence=_pd("tinted = tex * (Tint * Multiplier) [saturate in DXIL/evaluator]"),
    )
    if mode_x < 0.5:
        return tinted, evidence + _pd("TintMode.x≈0 → tinted only"), None
    if metallic is None:
        # No RMAO → metal treated as 0: [1,1]→tinted, [1,0]→tex.
        if mode_y >= 0.5:
            return tinted, evidence + _pd("no RMAO: metal=0 → TintMode.y≈1 → tinted"), None
        return tex, evidence + _pd("no RMAO: metal=0 → TintMode.y≈0 → untinted tex"), None
    if mode_y >= 0.5:
        mixed = Mix(
            a=tinted,
            b=tex,
            factor=metallic,
            evidence=_pd("TintMode≈[1,1]: lerp(tinted, tex, metal=RMAO.g)"),
        )
    else:
        mixed = Mix(
            a=tex,
            b=tinted,
            factor=metallic,
            evidence=_pd("TintMode≈[1,0]: lerp(tex, tinted, metal=RMAO.g)"),
        )
    return mixed, evidence, None


def evaluate_car_standard(
    *,
    name: str,
    material,
    resolver,
    media_root: str,
    production_mode: bool = True,
    revision: str = "b1.75",
) -> ForzaMaterialIR:
    """Evaluate one car_standard instance into ForzaMaterialIR.

    ``revision``: ``\"b1.75\"`` (production) or ``\"b1\"`` (baseline for diffs only).
    """
    if revision not in ("b1", "b1.75"):
        raise ValueError(f"unsupported car_standard revision: {revision!r}")
    shader_name = getattr(material, "shader_name", None)
    params = getattr(material, "parameters", None) or {}
    cbmp = getattr(material, "cbmp", None) or {}

    bindings = extract_bindings(
        media_root=media_root,
        shader_name="car_standard",
        params=params,
        cbmp=cbmp,
        game_key="fh6",
    )
    sha = (bindings.source_hashes or {}).get("shaderbin_sha256")
    if not is_car_standard_contract_identity(shader_name, sha):
        raise RuntimeError(
            f"car_standard contract identity mismatch: shader={shader_name!r} sha={sha!r}"
        )

    identity = ShaderIdentity(
        shader_name="car_standard",
        archive_path=f"{media_root}/cars/_library/shaders/car_standard.zip".replace(
            "\\", "/"
        ),
        shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        permutation=CAR_STANDARD_PERMUTATION,
    )

    evidence: list[PD] = list(
        _pd(
            f"shaderbin_sha256={CAR_STANDARD_SHADERBIN_SHA256}",
            f"permutation={CAR_STANDARD_PERMUTATION}",
            f"production_mode={production_mode}",
            f"revision={revision}",
            f"approved_rules={sorted(APPROVED_PRODUCTION_RULES)}",
            f"pending_review_rules={sorted(PENDING_REVIEW_RULES)}",
        )
    )

    bco = _bool(params, 0x71134963)
    evidence.extend(
        _pd(
            "MatI:BaseColorOverrideSwitch="
            f"{'absent' if bco is None else bco} "
            "(0x71134963; pending_review; corpus_all_absent)"
        )
    )
    glass = _bool(params, SPN.GlassSwitchBool)
    evidence.extend(_pd(f"MatI:GlassSwitch={glass} CB=reg30.w"))
    bypass = _bool(params, 0xA14CA0B8)
    evidence.extend(
        _pd(
            f"MatI:BypassSFXOnOff={bypass} CB=reg30.z "
            "DXIL:icmp ne 0 gates SurfaceFX; clean IR leaves FX unbound"
        )
    )
    ov_bc = _bool(params, 0xB8E61E16)
    evidence.extend(
        _pd(
            f"MatI:OverrideBaseColorTilingOnOff={ov_bc} CB=reg29.y "
            "(corpus always false; true=pending_review)"
        )
    )

    if bco is True and production_mode:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_standard: BaseColorOverrideSwitch=true not production-approved",
            ),
        )
    if glass is True and production_mode:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                "car_standard: GlassSwitch=true not production-approved for clean IR",
            ),
        )

    cap_resolver = MaterialCapabilityResolver(media_root=media_root, game_key="fh6")
    resolution = cap_resolver.resolve(name=name, material=material, resolver=resolver)
    if not resolution.is_selected or resolution.resolved is None:
        reasons = resolution.probe.rejection_reasons or ("car_standard unsupported",)
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence) + tuple(resolution.probe.evidence),
            rejection_reasons=tuple(reasons),
        )

    cap = resolution.resolved.capability
    evidence.extend(list(cap.evidence))

    # Shared Override*=false UV transform: one ScaleUV tree for all bindings
    # that share TEXCOORD + U/V tiling + orientation (IR structural reuse).
    uv_cache: dict = {}
    sample_kw = dict(
        params=params,
        resolver=resolver,
        production_mode=production_mode,
        revision=revision,
        uv_cache=uv_cache,
    )

    base_color = None
    src = cap.base_color_source
    if src.kind is BaseColorSourceKind.TEXTURE and src.texture is not None:
        base_tex, sample_reject = _sample_from_slot(
            src.texture,
            channels=("r", "g", "b"),
            color_space="sRGB",
            **sample_kw,
        )
        if sample_reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(sample_reject,),
            )
        base_color = base_tex
    elif src.kind in (
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
    ) and src.color is not None:
        base_color = ConstantColor(rgba=src.color, evidence=tuple(src.evidence))
    else:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(f"car_standard: unsupported base source {src.kind}",),
        )

    normal = None
    if cap.normal_map is not None:
        samp, sample_reject = _sample_from_slot(
            cap.normal_map,
            channels=("r", "g", "b"),
            color_space="Non-Color",
            **sample_kw,
        )
        if sample_reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(sample_reject,),
            )
        normal = NormalDecode(source=samp, evidence=_pd("normal_unpack:mad2_minus1"))

    roughness = metallic = ao = None
    metal_channel = None
    if cap.rmao_map is not None:
        samp, sample_reject = _sample_from_slot(
            cap.rmao_map,
            channels=("r", "g", "b"),
            color_space="Non-Color",
            **sample_kw,
        )
        if sample_reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(sample_reject,),
            )
        roughness = Channel(source=samp, channel="r", evidence=_pd("RMAO.R=roughness"))
        metallic = Channel(source=samp, channel="g", evidence=_pd("RMAO.G=metallic"))
        metal_channel = metallic
        ao = Channel(source=samp, channel="b", evidence=_pd("RMAO.B=AO"))

    # Base Color tint (DXIL) before AO Principled prep — B1.75 only.
    if revision == "b1.75" and isinstance(base_color, TextureSample):
        tinted, tint_ev, tint_reject = _apply_base_color_tint(
            base_color, params=params, metallic=metal_channel
        )
        if tint_reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(tint_reject,),
            )
        evidence.extend(list(tint_ev))
        base_color = tinted

    if ao is not None and base_color is not None:
        base_color = Multiply(
            a=base_color,
            b=ao,
            evidence=_pd("base_color *= AO (Principled prep; after tint)"),
        )

    opacity = None
    shading_attenuation = None
    if cap.alpha_map is not None:
        # Opacity channel uses DXIL/HLSL .x labeling (resolver/legacy graph plans).
        alpha_ch = (cap.alpha_map.channel or "x").lower()
        if alpha_ch in ("r", "red"):
            alpha_ch = "x"
        samp, sample_reject = _sample_from_slot(
            cap.alpha_map,
            channels=(alpha_ch,),
            color_space="Non-Color",
            **sample_kw,
        )
        if sample_reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(sample_reject,),
            )
        alpha_r = Channel(
            source=samp,
            channel=alpha_ch,
            evidence=_pd(f"Alpha.{alpha_ch} shading factor"),
        )
        # DXIL: product = Alpha.r * BaseColorAlpha.a (exact SHA alpha contract).
        from .alpha import evaluate_car_standard_alpha

        atten = alpha_r
        if (
            src.kind is BaseColorSourceKind.TEXTURE
            and src.texture is not None
            and revision == "b1.75"
        ):
            bc_a_samp, bc_a_reject = _sample_from_slot(
                src.texture,
                channels=("a",),
                color_space="Non-Color",
                **sample_kw,
            )
            if bc_a_reject:
                return ForzaMaterialIR(
                    shader=identity,
                    evidence=tuple(evidence),
                    rejection_reasons=(bc_a_reject,),
                )
            bc_a = Channel(
                source=bc_a_samp,
                channel="a",
                evidence=_pd("BaseColorAlpha.a shading factor"),
            )
            atten = Multiply(
                a=alpha_r,
                b=bc_a,
                evidence=_pd(
                    "authored_mask=Alpha.r*BaseColorAlpha.a "
                    "(game-file alpha contract / DXIL)"
                ),
            )
        # Saturate(product) as in DXIL %2467.
        atten = Clamp(
            source=atten,
            lo=0.0,
            hi=1.0,
            evidence=_pd("saturate(Alpha.r*BaseColorAlpha.a)"),
        )

        transparency = _bool(params, SPN.AlphaTransparencyBool)
        sem = evaluate_car_standard_alpha(
            alpha_transparency=transparency,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        evidence.append(
            PD(
                kind="alpha_contract",
                detail=(
                    f"classification={sem.classification.value} "
                    f"source_visibility={sem.source_visibility_semantic} "
                    f"blender={sem.blender_translation}"
                ),
                source="materials.alpha",
            )
        )
        for u in sem.unresolved:
            evidence.append(
                PD(kind="alpha_unresolved", detail=u, source="materials.alpha")
            )

        if sem.surface_visibility == "OPAQUE":
            evidence.append(
                PD(
                    kind="shading_attenuation",
                    detail="saturate(Alpha.r*BaseColorAlpha.a); NOT Principled Alpha",
                    source="materials.eval_car_standard",
                )
            )
            shading_attenuation = ShadingAttenuation(
                expression=atten,
                evidence=_pd(
                    "opaque lighting attenuation; Blender backend may approx "
                    "via BaseColor×attenuation (not exact BRDF)"
                ),
            )
            opacity = None
        elif sem.surface_visibility == "CLIP":
            evidence.append(
                PD(
                    kind="alpha_texture_visibility_mask",
                    detail=(
                        "source: TEXTURE_VISIBILITY_MASK; "
                        f"blender={sem.blender_translation} "
                        f"thr={sem.blender_threshold} "
                        f"({sem.threshold_provenance})"
                    ),
                    source="materials.eval_car_standard",
                )
            )
            opacity = atten
            thr = float(
                sem.blender_threshold if sem.blender_threshold is not None else 0.5
            )
            opacity = Clamp(
                source=opacity,
                lo=thr,
                hi=1.0,
                evidence=_pd(
                    f"blender {sem.blender_translation} threshold={thr} "
                    "(not exact Forza FF)"
                ),
            )
        else:
            # UNRESOLVED: keep bindings for diagnostics; do not invent Principled Alpha.
            opacity = None
            shading_attenuation = None
            if transparency is None and cap.alpha_map is not None:
                return ForzaMaterialIR(
                    shader=identity,
                    evidence=tuple(evidence),
                    rejection_reasons=(
                        "car_standard: AlphaTransparency absent — "
                        "UNRESOLVED_ALPHA (refuse has_alpha CLIP heuristic)",
                    ),
                )

    return ForzaMaterialIR(
        shader=identity,
        base_color=base_color,
        normal=normal,
        roughness=roughness,
        metallic=metallic,
        ambient_occlusion=ao,
        opacity=opacity,
        shading_attenuation=shading_attenuation,
        evidence=tuple(evidence),
        rejection_reasons=(),
    )
