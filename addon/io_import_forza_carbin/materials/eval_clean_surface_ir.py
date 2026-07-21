"""Generic CleanSurface sample-site → ForzaMaterialIR evaluator.

Used for production-supported families that share the CleanSurface capability
shape. Family-specific overlays (car_standard tint, car_carbonfiber weave)
remain in dedicated modules; this path never builds semantics from
register-keyed TextureBinding.
"""

from __future__ import annotations

from .forza_ir import (
    Channel,
    ConstantColor,
    ForzaMaterialIR,
    MeshUV,
    Multiply,
    NormalDecode,
    RotateUV,
    ScaleUV,
    SamplerState,
    ShaderIdentity,
    TextureSample,
    TextureSampleExpression,
)
from .model import (
    BaseColorSourceKind,
    ProvenanceDiagnostic as PD,
)
from .resolver import MaterialCapabilityResolver
from .route_model import has_ir_evaluator
from .shader_bindings import extract_bindings
from .texture_source import resolve_texture_source

# SHA → family (mirrors route_model / catalog).
_SHA_TO_FAMILY: dict[str, str] = {
    "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16": "car_standard",
    "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515": "car_label",
    "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3": "car_carbonfiber",
    "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178": "car_standard_emissive",
    "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523": "car_standard_fabric",
    "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3": "car_automotive_paint",
    "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88": "car_standard_coated",
    "3f988df89a12b4a008777463a56eee840a5c3488c6af3ad53f69c2f4cb861d09": "car_glass_detailed",
    "47f92e42f2d1991ae07a36364216402e53801bc6be9efa765ee49fe64a51d0e9": "car_reflector",
    "384692abfe3daace9b29f2580c60c23a171192e8c5e9fd6b3be10989b255f106": "car_brakerotor",
    "831b4866240da29fa4bf6706b13ceab4f4259e2cb4f32eb7b10da687f7284f53": "car_livery_transmissive",
    "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb": "car_livery",
}


def _pd(*details: str, kind: str = "contract") -> tuple[PD, ...]:
    return tuple(
        PD(kind=kind, detail=d, source="materials.eval_clean_surface_ir")
        for d in details
        if d
    )


def is_clean_surface_ir_identity(shader_name: str | None, shaderbin_sha256: str | None) -> bool:
    if not shaderbin_sha256 or not has_ir_evaluator(shaderbin_sha256):
        return False
    # Dedicated evaluators own these overlays.
    if (shader_name or "").lower() in ("car_standard", "car_carbonfiber"):
        return False
    expected = _SHA_TO_FAMILY.get(shaderbin_sha256)
    if expected is None:
        return False
    return (shader_name or "").lower() == expected


_FAMILY_TO_SHA: dict[str, str] = {fam: sha for sha, fam in _SHA_TO_FAMILY.items()}


def _exact_family_identity(family: str, shader_name, sha) -> bool:
    return (shader_name or "").lower() == family and sha == _FAMILY_TO_SHA.get(family)


def _site_id_from_slot(slot) -> str | None:
    """Compatibility: recover site id only via exact evidence tag written from site."""
    for e in getattr(slot, "evidence", None) or ():
        detail = getattr(e, "detail", "") or ""
        if detail.startswith("sample_site:"):
            return detail.split(":", 1)[1]
    return None


def _uv_from_slot(slot, params: dict):
    """Fallback UV from derived slot — not the authoritative contracted path."""
    del params
    texcoord = int(str(slot.texcoord).replace("TEXCOORD", "") or "0")
    uv: object = MeshUV(
        index=texcoord,
        evidence=_pd(f"slot:{slot.param_name}:TEXCOORD{texcoord}"),
    )
    tiling = getattr(slot, "tiling", None)
    if tiling is not None and tiling != (1.0, 1.0):
        uv = ScaleUV(source=uv, scale=tuple(tiling), evidence=_pd(f"tiling={tiling}"))
    rot = float(getattr(slot, "rotation_degrees", 0.0) or 0.0)
    if rot != 0.0:
        uv = RotateUV(source=uv, degrees=rot, evidence=_pd(f"orient={rot}"))
    return uv


def _sample_slot(slot, *, channels, color_space, resolver, params, evaluated_sites=None):
    from .site_ir_sample import find_site_for_slot, sample_from_evaluated_site

    site = find_site_for_slot(evaluated_sites, slot) if evaluated_sites is not None else None
    if site is not None:
        addr = getattr(slot, "address", None)
        address = None
        if addr is not None:
            try:
                address = {"U": addr["U"], "V": addr["V"]}
            except Exception:
                address = {
                    "U": getattr(addr, "get", lambda *_: "REPEAT")("U", "REPEAT"),
                    "V": getattr(addr, "get", lambda *_: "REPEAT")("V", "REPEAT"),
                }
        return sample_from_evaluated_site(
            site,
            path=slot.path,
            binding_name_hash=int(slot.param_hash or 0),
            channels=tuple(channels),
            color_space=color_space,
            resolver=resolver,
            params=params,
            address=address,
            extra_evidence=tuple(slot.evidence or ()),
        )

    # Derived-slot fallback (tests without evaluated sites).
    src = resolve_texture_source(slot.path, resolver)
    if src is None:
        return None, f"texture unresolved: {slot.path}"
    addr = getattr(slot, "address", None)
    u = "REPEAT"
    v = "REPEAT"
    if addr is not None:
        try:
            u = addr["U"]
            v = addr["V"]
        except Exception:
            u = getattr(addr, "get", lambda *_: "REPEAT")("U", "REPEAT")
            v = getattr(addr, "get", lambda *_: "REPEAT")("V", "REPEAT")
    samp = SamplerState(address_u=str(u), address_v=str(v))
    site_id = _site_id_from_slot(slot)
    expr = TextureSampleExpression(
        binding_name_hash=int(slot.param_hash or 0),
        source=src,
        uv=_uv_from_slot(slot, params),
        channels=tuple(channels),
        color_space=color_space,
        sampler=samp,
        evidence=tuple(slot.evidence or ()) + _pd(f"sample_site:{site_id}"),
        sample_site_id=site_id,
        texture_register=None,
    )
    return TextureSample(sample=expr, sample_site_id=site_id), None


def evaluate_clean_surface_ir(
    *,
    name: str,
    material,
    resolver,
    media_root: str,
    production_mode: bool = True,
    evaluation_context=None,
) -> ForzaMaterialIR:
    """Evaluate a CleanSurface family from sample-site-backed capability → IR."""
    del production_mode  # reserved for future pending-review gates
    shader_name = getattr(material, "shader_name", None) or ""
    params = getattr(material, "parameters", None) or {}
    cbmp = getattr(material, "cbmp", None) or {}

    if evaluation_context is not None:
        bindings = evaluation_context.bindings
        sha = evaluation_context.shader.shaderbin_sha256 or ""
    else:
        bindings = extract_bindings(
            media_root=media_root,
            shader_name=shader_name,
            params=params,
            cbmp=cbmp,
            game_key="fh6",
        )
        sha = (bindings.source_hashes or {}).get("shaderbin_sha256") or ""
    if not is_clean_surface_ir_identity(shader_name, sha):
        raise RuntimeError(
            f"clean_surface_ir identity mismatch: shader={shader_name!r} sha={sha!r}"
        )
    expected_family = _SHA_TO_FAMILY.get(sha)
    if expected_family and expected_family != shader_name.lower():
        raise RuntimeError(
            f"clean_surface_ir family/SHA mismatch: {shader_name} vs {expected_family}"
        )

    identity = ShaderIdentity(
        shader_name=shader_name,
        archive_path=f"{media_root}/cars/_library/shaders/{shader_name}.zip".replace(
            "\\", "/"
        ),
        shaderbin_sha256=sha,
        permutation=str(
            (bindings.source_hashes or {}).get("primary_pass") or "CarLightScenario"
        ),
    )
    evidence: list[PD] = list(
        _pd(
            f"shaderbin_sha256={sha}",
            f"authoritative_model={bindings.authoritative_model}",
            "route=FULL_SAMPLE_SITE_IR",
            f"active_sites={len((bindings.evaluated_sites.active_import_sites() if bindings.evaluated_sites else []))}",
        )
    )

    if bindings.authoritative_model not in (
        "FULL_SAMPLE_SITE_IR",
        "EVALUATED_SAMPLE_SITES",
    ):
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"{shader_name}: authoritative_model={bindings.authoritative_model} "
                "is not FULL_SAMPLE_SITE_IR",
            ),
        )

    def _cap_from_sites(ctx):
        from .binding_activation import decide_base_color_source
        from .evaluation_context import IR_ROUTE_SLOTS_DEFERRED
        from .model import make_clean_surface_capability
        from .resolver import _alpha_mode
        from .site_role_map import collect_site_role_bindings, slot_view_from_binding

        roles = collect_site_role_bindings(ctx)
        base_slot = slot_view_from_binding(roles["base"]) if "base" in roles else None
        weave_slot = (
            slot_view_from_binding(roles["weave_mask"]) if "weave_mask" in roles else None
        )
        normal_slot = (
            slot_view_from_binding(roles["normal"]) if "normal" in roles else None
        )
        rmao_slot = slot_view_from_binding(roles["rmao"]) if "rmao" in roles else None
        alpha_slot = slot_view_from_binding(roles["alpha"]) if "alpha" in roles else None
        base_source, decisions = decide_base_color_source(
            shader_name=shader_name,
            params=params,
            base_map=base_slot,
            weave_mask=weave_slot,
            stock_paint=None,
            shaderbin_hash=sha,
        )
        return make_clean_surface_capability(
            base_color_source=base_source,
            alpha_map=alpha_slot,
            normal_map=normal_slot,
            rmao_map=rmao_slot,
            alpha_mode=_alpha_mode(params, alpha_slot is not None),
            alpha_threshold=0.5,
            evidence=tuple(evidence)
            + (
                PD(
                    kind="route",
                    detail=IR_ROUTE_SLOTS_DEFERRED,
                    source="materials.eval_clean_surface_ir",
                ),
            ),
            texture_binding_decisions=decisions,
        )

    if evaluation_context is not None:
        cap = _cap_from_sites(evaluation_context)
        evidence.extend(list(cap.evidence))
    else:
        cap_resolver = MaterialCapabilityResolver(media_root=media_root, game_key="fh6")
        resolution = cap_resolver.resolve(
            name=name,
            material=material,
            resolver=resolver,
            evaluation_context=None,
        )
        if not resolution.is_selected or resolution.resolved is None:
            reasons = resolution.probe.rejection_reasons or (f"{shader_name} unsupported",)
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence) + tuple(resolution.probe.evidence),
                rejection_reasons=tuple(reasons),
            )
        from .evaluation_context import capability_is_ir_deferred

        if capability_is_ir_deferred(resolution.resolved.capability):
            ctx = resolution.evaluation_context
            if ctx is None:
                return ForzaMaterialIR(
                    shader=identity,
                    evidence=tuple(evidence),
                    rejection_reasons=(f"{shader_name}: IR deferred without context",),
                )
            cap = _cap_from_sites(ctx)
        else:
            cap = resolution.resolved.capability
        evidence.extend(list(cap.evidence))

    evaluated_sites = getattr(bindings, "evaluated_sites", None)

    base_color = None
    src = cap.base_color_source
    if src.kind is BaseColorSourceKind.TEXTURE and src.texture is not None:
        samp, reject = _sample_slot(
            src.texture,
            channels=("r", "g", "b"),
            color_space="sRGB",
            resolver=resolver,
            params=params,
            evaluated_sites=evaluated_sites,
        )
        if reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(reject,),
            )
        base_color = samp
    elif src.kind in (
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
    ) and src.color is not None:
        base_color = ConstantColor(rgba=src.color, evidence=tuple(src.evidence))
    elif src.kind is BaseColorSourceKind.WEAVE_COMPOSITE:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(
                f"{shader_name}: weave composite must use eval_car_carbonfiber",
            ),
        )
    else:
        return ForzaMaterialIR(
            shader=identity,
            evidence=tuple(evidence),
            rejection_reasons=(f"{shader_name}: unsupported base source {src.kind}",),
        )

    normal = None
    if cap.normal_map is not None:
        samp, reject = _sample_slot(
            cap.normal_map,
            channels=("r", "g", "b"),
            color_space="Non-Color",
            resolver=resolver,
            params=params,
            evaluated_sites=evaluated_sites,
        )
        if reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(reject,),
            )
        normal = NormalDecode(source=samp, evidence=_pd("normal_unpack:mad2_minus1"))

    roughness = metallic = ao = None
    if cap.rmao_map is not None:
        samp, reject = _sample_slot(
            cap.rmao_map,
            channels=("r", "g", "b"),
            color_space="Non-Color",
            resolver=resolver,
            params=params,
            evaluated_sites=evaluated_sites,
        )
        if reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(reject,),
            )
        roughness = Channel(source=samp, channel="r", evidence=_pd("RMAO.R=roughness"))
        metallic = Channel(source=samp, channel="g", evidence=_pd("RMAO.G=metallic"))
        ao = Channel(source=samp, channel="b", evidence=_pd("RMAO.B=AO"))

    if ao is not None and base_color is not None:
        base_color = Multiply(
            a=base_color,
            b=ao,
            evidence=_pd("base_color *= AO (Principled prep)"),
        )

    opacity = None
    if cap.alpha_map is not None:
        alpha_ch = (cap.alpha_map.channel or "x").lower()
        if alpha_ch in ("r", "red"):
            alpha_ch = "x"
        samp, reject = _sample_slot(
            cap.alpha_map,
            channels=(alpha_ch,),
            color_space="Non-Color",
            resolver=resolver,
            params=params,
            evaluated_sites=evaluated_sites,
        )
        if reject:
            return ForzaMaterialIR(
                shader=identity,
                evidence=tuple(evidence),
                rejection_reasons=(reject,),
            )
        opacity = Channel(
            source=samp,
            channel=alpha_ch,
            evidence=_pd(f"Alpha.{alpha_ch}"),
        )

    return ForzaMaterialIR(
        shader=identity,
        base_color=base_color,
        normal=normal,
        roughness=roughness,
        metallic=metallic,
        ambient_occlusion=ao,
        opacity=opacity,
        evidence=tuple(evidence),
    )


# nodes_v3 dispatch helpers
def evaluate_car_label(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_standard_emissive(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_standard_fabric(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_automotive_paint(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_standard_coated(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_glass_detailed(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_reflector(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_brakerotor(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_livery_transmissive(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def evaluate_car_livery(**kwargs):
    return evaluate_clean_surface_ir(**kwargs)


def is_car_label_contract_identity(shader_name, sha):
    return _exact_family_identity("car_label", shader_name, sha)


def is_car_standard_emissive_contract_identity(shader_name, sha):
    return _exact_family_identity("car_standard_emissive", shader_name, sha)


def is_car_standard_fabric_contract_identity(shader_name, sha):
    return _exact_family_identity("car_standard_fabric", shader_name, sha)


def is_car_automotive_paint_contract_identity(shader_name, sha):
    return _exact_family_identity("car_automotive_paint", shader_name, sha)


def is_car_standard_coated_contract_identity(shader_name, sha):
    return _exact_family_identity("car_standard_coated", shader_name, sha)


def is_car_glass_detailed_contract_identity(shader_name, sha):
    return _exact_family_identity("car_glass_detailed", shader_name, sha)


def is_car_reflector_contract_identity(shader_name, sha):
    return _exact_family_identity("car_reflector", shader_name, sha)


def is_car_brakerotor_contract_identity(shader_name, sha):
    return _exact_family_identity("car_brakerotor", shader_name, sha)


def is_car_livery_transmissive_contract_identity(shader_name, sha):
    return _exact_family_identity("car_livery_transmissive", shader_name, sha)


def is_car_livery_contract_identity(shader_name, sha):
    return _exact_family_identity("car_livery", shader_name, sha)


# SHA constants expected by nodes_v3 _build_via_ir_contract
CAR_LABEL_SHADERBIN_SHA256 = (
    "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515"
)
CAR_STANDARD_EMISSIVE_SHADERBIN_SHA256 = (
    "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178"
)
CAR_STANDARD_FABRIC_SHADERBIN_SHA256 = (
    "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523"
)
CAR_AUTOMOTIVE_PAINT_SHADERBIN_SHA256 = (
    "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3"
)
CAR_STANDARD_COATED_SHADERBIN_SHA256 = (
    "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88"
)
CAR_GLASS_DETAILED_SHADERBIN_SHA256 = (
    "3f988df89a12b4a008777463a56eee840a5c3488c6af3ad53f69c2f4cb861d09"
)
CAR_REFLECTOR_SHADERBIN_SHA256 = (
    "47f92e42f2d1991ae07a36364216402e53801bc6be9efa765ee49fe64a51d0e9"
)
CAR_BRAKEROTOR_SHADERBIN_SHA256 = (
    "384692abfe3daace9b29f2580c60c23a171192e8c5e9fd6b3be10989b255f106"
)
CAR_LIVERY_TRANSMISSIVE_SHADERBIN_SHA256 = (
    "831b4866240da29fa4bf6706b13ceab4f4259e2cb4f32eb7b10da687f7284f53"
)
CAR_LIVERY_SHADERBIN_SHA256 = (
    "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb"
)
