"""Authoritative FH6 material capability resolver (no bpy).

Owns MatI + NameHash TXMP semantics + DXIL binding -> typed CleanSurfaceCapability.
Blender node construction is a later stage and must not feed selection.
"""

from __future__ import annotations

import os
import struct
from types import MappingProxyType

from ..parsing.material import ShaderParameterName as SPN
from ..parsing.paths import find_media_root
from .capabilities import resolve_uv_choice_texcoord
from .binding_activation import decide_base_color_source
from .model import (
    BaseColorSourceKind,
    InvalidMaterialBinding,
    MaterialCapabilityKind,
    MaterialResolution,
    MaterialResolutionError,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedMaterial,
    ResolvedTextureSlot,
    UnsupportedMaterialCapability,
    make_clean_surface_capability,
)
from .name_hashes import MaterialNameError, require_name
from .registry import params_have_glass_scalars, params_have_paint_scalars
from .shader_bindings import extract_bindings, resolve_binding_uv
from .txmp_semantics import semantics_for_txmp_hash

_ADDR = {0: "REPEAT", 1: "REPEAT", 2: "MIRROR", 3: "EXTEND", 4: "CLIP"}
_KIND = MaterialCapabilityKind.CLEAN_SURFACE


def _bool(params: dict, h: int) -> bool | None:
    p = params.get(h)
    if p is None or getattr(p, "type", None) != 3:
        return None
    return bool(p.value)


def _color(params: dict, h: int):
    p = params.get(h)
    if p is None or getattr(p, "type", None) not in (0, 1):
        return None
    value = getattr(p, "value", None)
    return value if isinstance(value, tuple) and len(value) >= 3 else None


def _path_exists(path: str, resolver) -> bool:
    resolved = resolver.resolve(path) if resolver is not None else path
    return bool(resolved and os.path.isfile(resolved))


def binding_uv(bind, params: dict | None = None, *, txmp_name: str | None = None):
    """Return proven TEXCOORD index, or None when multi-UV lacks UVChoice."""
    if bind is None:
        return None
    if params is not None:
        uv = resolve_binding_uv(bind, params, txmp_name=txmp_name)
        if uv is not None:
            return int(uv)
    if bind.uv_semantic is not None:
        all_uv = tuple(dict.fromkeys(bind.uv_semantics_all or ()))
        if not all_uv or (
            len(all_uv) == 1 and int(all_uv[0]) == int(bind.uv_semantic)
        ):
            return int(bind.uv_semantic)
    all_uv = tuple(dict.fromkeys(bind.uv_semantics_all or ()))
    if len(all_uv) == 1:
        return int(all_uv[0])
    return None


def _tiling(params: dict, bind, *, param_name: str | None = None) -> tuple[float, float]:
    """Resolve U/V scale via UV Conformance Foundation precedence.

    Never lets an unswitched Override vec2 (1,1) win over MatI U/V_Tiling.
    """
    from .uv import resolve_uv_scale

    hashes = tuple(getattr(bind, "tiling_cb_hashes", None) or ()) if bind else ()
    resolved = resolve_uv_scale(
        params,
        param_name=param_name,
        tiling_cb_hashes=hashes,
        require_proven=True,
    )
    if resolved.scale is not None:
        return resolved.scale
    raise MaterialResolutionError(
        resolved.rejection
        or "UV scale unresolved (refusing accidental identity fallback)"
    )


def _address(params: dict, spmp: dict, bind) -> MappingProxyType | None:
    if bind is None or bind.sampler_reg is None:
        return None
    for h, reg in spmp.items():
        if int(reg) != int(bind.sampler_reg):
            continue
        p = params.get(h)
        raw = getattr(p, "samp", b"") if p is not None else b""
        if getattr(p, "type", None) != 7 or len(raw) < 8:
            continue
        u = struct.unpack_from("<I", raw, 0)[0]
        v = struct.unpack_from("<I", raw, 4)[0]
        return MappingProxyType(
            {"U": _ADDR.get(u, "REPEAT"), "V": _ADDR.get(v, "REPEAT")}
        )
    return None


def _ev(*details: str, kind: str = "contract", source: str = "materials.resolver"):
    return tuple(
        ProvenanceDiagnostic(kind=kind, detail=d, source=source) for d in details if d
    )


def _slot(
    *,
    role: str,
    h: int,
    name: str,
    path: str,
    bind,
    params: dict,
    spmp: dict,
    uv: int,
    channel: str | None = None,
    evidence: tuple[ProvenanceDiagnostic, ...] = (),
    resolver=None,
) -> ResolvedTextureSlot:
    from .texture_source import resolve_texture_source
    from .uv import UV_NAMEHASH_UV_ORIENTATION, resolve_uv_scale

    src = resolve_texture_source(path, resolver) if path else None
    hashes = tuple(getattr(bind, "tiling_cb_hashes", None) or ()) if bind else ()
    scale_res = resolve_uv_scale(
        params,
        param_name=name,
        tiling_cb_hashes=hashes,
        require_proven=True,
    )
    ev = evidence
    if scale_res.scale is not None:
        tiling = scale_res.scale
        ev = ev + _ev(
            f"UV_SCALE:{tiling[0]:g},{tiling[1]:g}:{scale_res.proof_status.value}",
            kind="uv_scale",
        )
    else:
        # Compatibility path: do not invent a silent identity. Mark unresolved
        # and keep numeric (1,1) only as a labelled placeholder for PARTIAL
        # diagnostics — production IR evaluators must fail closed themselves.
        tiling = (1.0, 1.0)
        ev = ev + _ev(
            f"UV_UNRESOLVED:{scale_res.rejection}",
            kind="uv_unresolved",
        )
    orient_p = params.get(int(UV_NAMEHASH_UV_ORIENTATION))
    rotation_degrees = 0.0
    if orient_p is not None and getattr(orient_p, "type", None) == 2:
        oval = getattr(orient_p, "value", None)
        if isinstance(oval, (int, float)):
            rotation_degrees = float(oval)
    return ResolvedTextureSlot(
        role=role,
        path=path,
        texcoord=f"TEXCOORD{uv}",
        channel=channel,
        tiling=tiling,
        address=_address(params, spmp, bind),
        param_hash=h & 0xFFFFFFFF,
        param_name=name,
        evidence=ev,
        source_kind=src.kind.value if src else None,
        canonical_path=src.canonical_game_path if src else None,
        filesystem_path=src.filesystem_path if src else None,
        archive_path=src.archive_path if src else None,
        archive_member=src.archive_member if src else None,
        rotation_degrees=rotation_degrees,
    )


def _prefer(current, candidate, *, is_override: bool):
    if current is None or is_override:
        return candidate
    return current


def _paint_color(params: dict, stock):
    from .binding_activation import _paint_color as _activate_paint

    color, _name = _activate_paint(params, stock)
    return color


def _alpha_mode(params: dict, has_alpha: bool) -> str:
    """Visibility mode from MatI switches only — never from TXMP presence.

    ``has_alpha`` is retained for call-site compatibility / diagnostics but must
    **not** select CLIP. Game-file alpha contracts (materials.alpha) decide
    cutout from AlphaTransparency / UseAlpha* with exact SHA evidence.
    """
    del has_alpha  # intentional: presence of Alpha TXMP is not a visibility signal
    use_test = _bool(params, SPN.UseAlphaTestBool)
    use_blend = _bool(params, SPN.UseAlphaBlendBool)
    transparency = _bool(params, SPN.AlphaTransparencyBool)
    # Explicit AlphaTransparency=false → opaque cutout off (car_standard DXIL).
    if transparency is False:
        return "OPAQUE"
    if use_blend is True and use_test is not True:
        return "BLEND"
    if use_test is True or transparency is True:
        return "CLIP"
    # Absent AlphaTransparency + no UseAlpha* → opaque (fail closed; do not
    # invent CLIP from an Alpha TXMP binding name or channel payload).
    return "OPAQUE"


def _clean_surface_complete(cap) -> bool:
    src = cap.base_color_source
    has_base = src.kind in (
        BaseColorSourceKind.TEXTURE,
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
        BaseColorSourceKind.WEAVE_COMPOSITE,
    )
    return bool(
        has_base
        or cap.alpha_map
        or cap.normal_map
        or cap.rmao_map
    )


class MaterialCapabilityResolver:
    """Resolve MatI + DXIL into a typed capability or structured rejection."""

    def __init__(self, media_root: str | None = None, game_key: str = "fh6"):
        if (game_key or "").lower() != "fh6":
            raise MaterialResolutionError(
                f"direct material pipeline supports FH6 only, got {game_key!r}"
            )
        self.game_key = "fh6"
        self.media_root = media_root
        self.stock_paint_rgba = None

    def _media(self, resolver) -> str:
        if self.media_root and os.path.isdir(self.media_root):
            return self.media_root
        root = getattr(resolver, "root", None)
        media = find_media_root(root) if root else None
        if media:
            return media
        raise MaterialResolutionError("FH6 Media root is required for DXIL bindings")

    def resolve(self, *, name: str, material, resolver=None) -> MaterialResolution:
        shader_name = getattr(material, "shader_name", None)
        params = getattr(material, "parameters", None) or {}
        txmp = getattr(material, "txmp", None) or {}
        cbmp = getattr(material, "cbmp", None) or {}
        spmp = getattr(material, "spmp", None) or {}
        overrides = getattr(material, "override_hashes", None) or set()

        observation_ev: list[ProvenanceDiagnostic] = [
            ProvenanceDiagnostic(
                kind="capability",
                detail=_KIND.value,
                source="materials.resolver",
            )
        ]
        if params_have_paint_scalars(params):
            observation_ev.append(
                ProvenanceDiagnostic(
                    kind="observation",
                    detail="paint parameter family present",
                    source="materials.registry",
                )
            )
        if params_have_glass_scalars(params):
            observation_ev.append(
                ProvenanceDiagnostic(
                    kind="observation",
                    detail="glass parameter family present",
                    source="materials.registry",
                )
            )

        if not shader_name:
            return MaterialResolution.rejected(
                reasons=("material has no shader",),
                evidence=tuple(observation_ev),
            )

        try:
            bindings = extract_bindings(
                media_root=self._media(resolver),
                shader_name=shader_name,
                params=params,
                cbmp=cbmp,
                game_key="fh6",
            )
        except Exception as exc:
            msg = f"{name} ({shader_name}): DXIL/bindings failed: {exc}"
            print(f"Forza material ERROR: {msg}", flush=True)
            fail = InvalidMaterialBinding(msg)
            return MaterialResolution.rejected(
                reasons=(msg,),
                evidence=tuple(observation_ev),
                failure_exception=fail,
            )

        errors: list[str] = []
        base_map = None
        weave_mask = None
        normal_map = None
        rmao_map = None
        alpha_map = None
        pending_alpha = None
        consumed: set[int] = set()

        uv_choice = resolve_uv_choice_texcoord(params)
        if uv_choice is not None:
            print(f"Forza material UV: {name}: {uv_choice[1].detail}", flush=True)
            observation_ev.append(uv_choice[1])

        for h, treg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
            p = params.get(h)
            if p is None or getattr(p, "type", None) != 6:
                continue
            path = getattr(p, "path", "") or ""
            try:
                param_name = require_name(h, context=f"{shader_name} TXMP t{treg}")
            except MaterialNameError as exc:
                errors.append(str(exc))
                continue

            # WeaveMask is activation-owned (not a Principled primary map).
            if param_name == "WeaveMask":
                if not path:
                    errors.append(f"{param_name} t{treg}: empty TXMP path")
                    continue
                if not _path_exists(path, resolver):
                    errors.append(f"{param_name} t{treg}: texture missing: {path}")
                    continue
                bind = bindings.textures.get(int(treg))
                uv = binding_uv(bind, params, txmp_name=param_name)
                if uv is None:
                    errors.append(
                        f"{param_name} t{treg}: no proven UV for weave composite"
                    )
                    continue
                weave_mask = _slot(
                    role="weave_mask",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    channel="r",
                    evidence=_ev(
                        f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}",
                        f"DXIL:t{treg}:TEXCOORD{uv}",
                        "activation:weave_mask",
                    ),
                    resolver=resolver,
                )
                continue

            try:
                sem = semantics_for_txmp_hash(
                    h, context=f"{shader_name} TXMP t{treg}"
                )
            except MaterialNameError as exc:
                errors.append(str(exc))
                continue

            if not sem.supports(_KIND):
                continue

            if not path:
                errors.append(f"{param_name} t{treg}: empty TXMP path")
                continue
            if not _path_exists(path, resolver):
                errors.append(f"{param_name} t{treg}: texture missing: {path}")
                continue

            bind = bindings.textures.get(int(treg))
            if param_name == "Alpha":
                pending_alpha = (h, param_name, path, bind, int(treg))
                continue

            uv = binding_uv(bind, params, txmp_name=param_name)
            if uv is None:
                all_uv = (
                    list(getattr(bind, "uv_semantics_all", None) or []) if bind else []
                )
                if bind is None:
                    errors.append(
                        f"{param_name} t{treg}: not sampled in CarLightScenario DXIL"
                    )
                else:
                    errors.append(
                        f"{param_name} t{treg}: no proven UV "
                        f"(DXIL candidates={all_uv or None}; "
                        f"need unique TEXCOORD or UVChoice)"
                    )
                continue

            evidence = _ev(
                f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}",
                f"DXIL:t{treg}:TEXCOORD{uv}",
            )
            if param_name in ("BaseColorAlpha", "BaseColorAlpha_1"):
                candidate = _slot(
                    role="base_color",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence,
                    resolver=resolver,
                )
                base_map = _prefer(base_map, candidate, is_override=h in overrides)
            elif param_name in ("Normal", "WeaveNormal"):
                candidate = _slot(
                    role="normal",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence,
                    resolver=resolver,
                )
                normal_map = _prefer(normal_map, candidate, is_override=h in overrides)
            elif param_name == "RoughMetalAO":
                candidate = _slot(
                    role="rmao",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence + _ev("packing:R=roughness,G=metallic,B=AO"),
                    resolver=resolver,
                )
                rmao_map = _prefer(rmao_map, candidate, is_override=h in overrides)

        shaderbin_hash = ""
        if bindings is not None:
            shaderbin_hash = str(
                (getattr(bindings, "source_hashes", None) or {}).get(
                    "shaderbin_sha256", ""
                )
            )

        base_source, binding_decisions = decide_base_color_source(
            shader_name=shader_name,
            params=params,
            base_map=base_map,
            weave_mask=weave_mask,
            stock_paint=self.stock_paint_rgba,
            shaderbin_hash=shaderbin_hash,
        )
        observation_ev.extend(base_source.evidence)
        for dec in binding_decisions:
            observation_ev.extend(dec.evidence)

        active_base_map = (
            base_source.texture
            if base_source.kind is BaseColorSourceKind.TEXTURE
            else None
        )

        paint_clears_alpha = base_source.kind in (
            BaseColorSourceKind.INSTANCE_PAINT,
            BaseColorSourceKind.MATERIAL_CONSTANT,
        ) and any(
            d.activation.value == "inactive_placeholder"
            and d.slot.param_name in ("BaseColorAlpha", "BaseColorAlpha_1")
            for d in binding_decisions
        )
        if paint_clears_alpha:
            pending_alpha = None

        if pending_alpha is not None:
            h, param_name, path, bind, treg = pending_alpha
            uv = binding_uv(bind, params, txmp_name=param_name)
            evidence_details = [f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}"]
            use_test = _bool(params, SPN.UseAlphaTestBool)
            use_blend = _bool(params, SPN.UseAlphaBlendBool)
            transparency = _bool(params, SPN.AlphaTransparencyBool)
            authored_on = (
                use_test is True or use_blend is True or transparency is True
            )
            authored_off = transparency is False or (
                use_test is False and use_blend is not True
            )
            dxil_opacity = False
            opacity_ch = "x"
            if bind is not None:
                roles = getattr(bind, "channel_roles", None) or {}
                if roles.get("opacity"):
                    dxil_opacity = True
                    opacity_ch = str(roles["opacity"])
                elif getattr(bind, "opacity_from_w", False):
                    dxil_opacity = True
                    opacity_ch = "w"
                elif any(
                    "feeds_sv_target_alpha" in e
                    for e in (getattr(bind, "evidence", None) or ())
                ):
                    dxil_opacity = True
                for e in getattr(bind, "evidence", None) or ():
                    if "alpha_supplement_pso=" in e:
                        evidence_details.append(e)
            if bind is None:
                errors.append(
                    f"{param_name} t{treg}: TXMP present but not sampled in "
                    f"CarLightScenario DXIL (no invented UV)"
                )
            elif uv is None:
                all_uv = list(getattr(bind, "uv_semantics_all", None) or [])
                errors.append(
                    f"{param_name} t{treg}: no proven UV "
                    f"(DXIL candidates={all_uv or None}; need unique TEXCOORD or UVChoice)"
                )
            elif authored_on or dxil_opacity:
                if authored_on:
                    evidence_details.append("MatI:authored alpha mode")
                if dxil_opacity:
                    evidence_details.append(f"DXIL:opacity_channel={opacity_ch}")
                evidence_details.append(f"DXIL:t{treg}:TEXCOORD{uv}")
                alpha_map = _slot(
                    role="alpha",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    channel=opacity_ch,
                    evidence=_ev(*evidence_details),
                    resolver=resolver,
                )
            elif authored_off and transparency is False:
                # Alpha TXMP still sampled; AlphaTransparency=false forces
                # discard/SV_Target.a paths to 1.0, but CarLightScenario still
                # uses Alpha.r*BaseColorAlpha.a for lighting. Keep the slot for
                # opaque coverage modulation (not Principled cutout).
                evidence_details.append(
                    "MatI:AlphaTransparency=false → no discard/cutout"
                )
                evidence_details.append(
                    "DXIL:coverage=Alpha.r*BaseColorAlpha.a (lighting)"
                )
                evidence_details.append(f"DXIL:t{treg}:TEXCOORD{uv}")
                alpha_map = _slot(
                    role="alpha",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    channel=opacity_ch,
                    evidence=_ev(*evidence_details),
                    resolver=resolver,
                )
            elif authored_off:
                pass
            else:
                errors.append(
                    f"{param_name} t{treg}: Tex+UV TEXCOORD{uv} but no MatI alpha "
                    f"mode and no DXIL SV_Target-alpha evidence"
                )

        if base_source.kind is BaseColorSourceKind.UNRESOLVED:
            # Fail closed when a BaseColorAlpha candidate exists but activation
            # cannot be proven. If there is no BaseColor TXMP at all, a white
            # constant remains valid when other clean maps are proven (legacy).
            if base_map is not None:
                detail = (
                    "; ".join(errors)
                    if errors
                    else "Base Color activation unresolved (fail closed)"
                )
                for d in binding_decisions:
                    detail = f"{detail}; {d.reason}" if detail else d.reason
                msg = f"{shader_name}: unsupported — {detail}"
                print(f"Forza material ERROR: {name} ({shader_name}): {msg}", flush=True)
                return MaterialResolution.rejected(
                    reasons=(msg,),
                    evidence=tuple(observation_ev),
                    contract_errors=tuple(errors),
                    consumed_txmp_hashes=frozenset(consumed),
                    bindings=bindings,
                    failure_exception=UnsupportedMaterialCapability(msg),
                    texture_binding_decisions=binding_decisions,
                )
            if normal_map or rmao_map or alpha_map:
                base_source = ResolvedBaseColorSource(
                    kind=BaseColorSourceKind.MATERIAL_CONSTANT,
                    color=(1.0, 1.0, 1.0, 1.0),
                    evidence=_ev(
                        "no BaseColor TXMP; white constant with other clean maps"
                    ),
                )
                observation_ev.extend(base_source.evidence)
            else:
                detail = (
                    "; ".join(errors)
                    if errors
                    else "no proven Base/Alpha/Normal/RMAO or paint/weave"
                )
                msg = f"{shader_name}: unsupported — {detail}"
                return MaterialResolution.rejected(
                    reasons=(msg,),
                    evidence=tuple(observation_ev),
                    contract_errors=tuple(errors),
                    consumed_txmp_hashes=frozenset(consumed),
                    bindings=bindings,
                    failure_exception=UnsupportedMaterialCapability(msg),
                    texture_binding_decisions=binding_decisions,
                )

        for slot in (
            active_base_map,
            alpha_map,
            normal_map,
            rmao_map,
            weave_mask if base_source.kind is BaseColorSourceKind.WEAVE_COMPOSITE else None,
        ):
            if slot is not None:
                consumed.add(slot.param_hash & 0xFFFFFFFF)

        alpha_mode = _alpha_mode(params, alpha_map is not None)
        slot_evidence: list[ProvenanceDiagnostic] = list(observation_ev)
        for slot in (active_base_map, alpha_map, normal_map, rmao_map):
            if slot is not None:
                slot_evidence.extend(slot.evidence)
        if base_source.kind is BaseColorSourceKind.WEAVE_COMPOSITE and base_source.weave:
            slot_evidence.extend(base_source.weave.evidence)

        draft = make_clean_surface_capability(
            base_color_source=base_source,
            alpha_map=alpha_map,
            normal_map=normal_map,
            rmao_map=rmao_map,
            alpha_mode=alpha_mode,
            alpha_threshold=0.5,
            evidence=tuple(slot_evidence),
            texture_binding_decisions=binding_decisions,
        )

        if errors:
            for err in errors:
                print(f"Forza material ERROR: {name} ({shader_name}): {err}", flush=True)

        if not _clean_surface_complete(draft):
            detail = (
                "; ".join(errors)
                if errors
                else "no proven Base/Alpha/Normal/RMAO or paint/weave"
            )
            msg = f"{shader_name}: unsupported — {detail}"
            return MaterialResolution.rejected(
                reasons=(msg,),
                evidence=draft.evidence,
                contract_errors=tuple(errors),
                consumed_txmp_hashes=frozenset(consumed),
                bindings=bindings,
                failure_exception=UnsupportedMaterialCapability(msg),
                texture_binding_decisions=binding_decisions,
            )

        if errors:
            print(
                f"Forza material WARN: {name} ({shader_name}): built with "
                f"{len(errors)} unresolved contract map issue(s)",
                flush=True,
            )

        resolved = ResolvedMaterial(
            name=name,
            game_key="fh6",
            shader_name=shader_name,
            capability_kind=_KIND,
            capability=draft,
        )
        return MaterialResolution.selected(
            resolved,
            evidence=draft.evidence,
            contract_errors=tuple(errors),
            consumed_txmp_hashes=frozenset(consumed),
            bindings=bindings,
            texture_binding_decisions=binding_decisions,
        )


path_exists = _path_exists
