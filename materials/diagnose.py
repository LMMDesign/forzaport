"""Diagnose a parsed MatI through capability resolution (no bpy).

Wraps CleanMaterialBuilder without changing shading output: collects source
TXMP/parameter inventories, records which inputs the current builder consumes,
and classifies MaterialStatus for the import report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..parsing.material import ShaderParameterName as SPN
from .capabilities import probe_all_capabilities
from .diagnostics import (
    AssignmentOutcome,
    MaterialDiagnostic,
    MaterialStatus,
    ParameterDiagnostic,
    ProvenanceDiagnostic,
    StageOutcome,
    TextureBindingDiagnostic,
    classify_capability_status,
)
from .name_hashes import MaterialNameError, name_for_hash
from .pipeline_v3 import (
    CleanMaterialBuilder,
    MaterialSpec,
    MaterialTranslateError,
    _ALPHA_NAMES,
    _BASE_NAMES,
    _NORMAL_NAMES,
    _RMAO_NAMES,
    _binding_uv,
    _path_exists,
)
from .shader_bindings import ShaderBindingError, extract_bindings
from .txmp_semantics import try_semantics_for_txmp_hash

# Instance parameters the clean v3 builder reads (NameHash ints).
_CONSUMED_PARAM_HASHES = frozenset(
    {
        int(SPN.UniqueBaseColorSwitchBool),
        int(SPN.UniqueBaseTextureSwitchBool),
        int(SPN.UniqueBaseColorColorParam),
        int(SPN.ColorGroupSwitchBool),
        int(SPN.PaintColorGroupColorParam),
        int(SPN.PaintColorColorParam),
        int(SPN.UniqueLiverySwitchBool),
        int(SPN.WeaveColorTintA),
        int(SPN.WeaveColorTintB),
        int(SPN.UseAlphaTestBool),
        int(SPN.UseAlphaBlendBool),
        int(SPN.AlphaTransparencyBool),
    }
)


@dataclass(frozen=True)
class MaterialResolveResult:
    """Capability-resolution result before Blender node construction."""

    spec: MaterialSpec | None
    diagnostic: MaterialDiagnostic


def _raw_value_summary(param) -> Any:
    if param is None:
        return None
    ptype = getattr(param, "type", None)
    if ptype == 6:
        return getattr(param, "path", "") or ""
    if ptype == 7:
        raw = getattr(param, "samp", b"") or b""
        return f"<sampler {len(raw)} bytes>"
    value = getattr(param, "value", None)
    if isinstance(value, tuple):
        return tuple(value)
    return value


def _parameter_diagnostics(params: dict) -> tuple[ParameterDiagnostic, ...]:
    rows: list[ParameterDiagnostic] = []
    for h in sorted(params.keys(), key=lambda x: x & 0xFFFFFFFF):
        p = params[h]
        name = name_for_hash(h)
        consumed = (h & 0xFFFFFFFF) in {x & 0xFFFFFFFF for x in _CONSUMED_PARAM_HASHES}
        # TXMP/SPMP consumed separately via texture_bindings.
        interpreted = None
        if name and name in (_BASE_NAMES | _ALPHA_NAMES | _NORMAL_NAMES | _RMAO_NAMES):
            interpreted = f"txmp_allowlist:{name}"
            consumed = True
        elif consumed and name:
            interpreted = f"builder_param:{name}"
        rows.append(
            ParameterDiagnostic(
                name_hash=h & 0xFFFFFFFF,
                name=name,
                raw_type=getattr(p, "type", None),
                raw_value=_raw_value_summary(p),
                interpreted=interpreted,
                consumed_by_builder=consumed,
                provenance=(
                    ProvenanceDiagnostic(
                        kind="NameHash",
                        detail=name or f"0x{h & 0xFFFFFFFF:08X}",
                        source="data/name_hashes.json",
                    ),
                ),
            )
        )
    return tuple(rows)


def _texture_diagnostics(
    *,
    material,
    resolver,
    bindings,
    params: dict,
    consumed_hashes: set[int],
) -> tuple[tuple[TextureBindingDiagnostic, ...], tuple[int, ...], bool, bool]:
    """Inventory every TXMP; flag unresolved known semantics and missing files."""
    txmp = getattr(material, "txmp", None) or {}
    rows: list[TextureBindingDiagnostic] = []
    unresolved: list[int] = []
    missing_texture = False
    missing_provenance = False

    for h in sorted(txmp.keys(), key=lambda x: x & 0xFFFFFFFF):
        treg = txmp[h]
        p = params.get(h)
        path = getattr(p, "path", "") or "" if p is not None else ""
        exists = bool(path) and _path_exists(path, resolver)
        name = name_for_hash(h)
        if name is None:
            missing_provenance = True
        sem = try_semantics_for_txmp_hash(h)
        role = sem.role if sem is not None else None
        bind = bindings.textures.get(int(treg)) if bindings is not None else None
        uv = (
            _binding_uv(bind, params, txmp_name=name) if bind is not None else None
        )
        consumed = (h & 0xFFFFFFFF) in consumed_hashes
        unresolved_reason = None
        alpha_interp = None
        color_space = None
        if role == "diffuse":
            color_space = "sRGB"
        elif role in ("normal", "rmao", "alpha", "lcao", "gloss"):
            color_space = "Non-Color"
        if role == "alpha" or (sem and "opacity" in (sem.channel_roles or {})):
            alpha_interp = "channel:" + (
                (sem.channel_roles or {}).get("opacity") or "x"
            )

        if not path:
            unresolved_reason = "empty_path"
            if role is not None and not (sem and sem.is_fx_layer):
                unresolved.append(h & 0xFFFFFFFF)
        elif not exists:
            missing_texture = True
            unresolved_reason = "missing_file"
            if role is not None and not (sem and sem.is_fx_layer):
                unresolved.append(h & 0xFFFFFFFF)
        elif sem is not None and sem.is_fx_layer:
            unresolved_reason = "fx_layer_inactive"
        elif role is not None and not consumed:
            unresolved_reason = f"semantic_not_in_capability:{role}"
            unresolved.append(h & 0xFFFFFFFF)
        elif role is None and name is not None and not consumed:
            unresolved_reason = "unbound_unknown_or_inactive"
        elif uv is None and name in (_BASE_NAMES | _NORMAL_NAMES | _RMAO_NAMES | _ALPHA_NAMES):
            if not consumed:
                unresolved_reason = unresolved_reason or "ambiguous_or_missing_uv"

        prov: list[ProvenanceDiagnostic] = []
        if name:
            prov.append(
                ProvenanceDiagnostic(
                    kind="TXMP NameHash",
                    detail=name,
                    source="data/name_hashes.json",
                )
            )
        if sem is not None:
            prov.append(
                ProvenanceDiagnostic(
                    kind="txmp_semantics",
                    detail=sem.evidence,
                    source="materials.txmp_semantics",
                )
            )
        if bind is not None and uv is not None:
            prov.append(
                ProvenanceDiagnostic(
                    kind="DXIL UV",
                    detail=f"t{treg}:TEXCOORD{uv}",
                    source="materials.shader_bindings",
                )
            )

        rows.append(
            TextureBindingDiagnostic(
                name_hash=h & 0xFFFFFFFF,
                name=name,
                texture_register=int(treg),
                semantic_role=role,
                path=path,
                path_exists=exists,
                uv_channel=uv,
                uv_role=f"TEXCOORD{uv}" if uv is not None else None,
                color_space=color_space,
                alpha_interpretation=alpha_interp,
                sampler_register=getattr(bind, "sampler_reg", None) if bind else None,
                sampler_address=None,
                consumed_by_builder=consumed,
                unresolved_reason=unresolved_reason,
                provenance=tuple(prov),
            )
        )

    return tuple(rows), tuple(sorted(set(unresolved))), missing_texture, missing_provenance


def _consumed_slot_hashes(spec: MaterialSpec | None) -> set[int]:
    if spec is None:
        return set()
    return {
        slot.param_hash & 0xFFFFFFFF
        for slot in spec.textures
        if slot is not None
    }


def _display_name(instance_key: str) -> str:
    # fh6|Plastic_Smooth|v4-... → Plastic_Smooth
    parts = instance_key.split("|")
    if len(parts) >= 2:
        return parts[1]
    return instance_key


def resolve_with_diagnostics(
    builder: CleanMaterialBuilder,
    instance_key: str,
    material,
    resolver=None,
) -> MaterialResolveResult:
    """Run clean v3 capability resolution and return spec + diagnostic."""
    shader_name = getattr(material, "shader_name", None)
    params = getattr(material, "parameters", None) or {}
    cbmp = getattr(material, "cbmp", None) or {}
    param_rows = _parameter_diagnostics(params)

    bindings = None
    binding_error: str | None = None
    name_error: str | None = None
    translate_error: str | None = None
    spec: MaterialSpec | None = None

    try:
        media = builder._media(resolver)
        bindings = extract_bindings(
            media_root=media,
            shader_name=shader_name or "",
            params=params,
            cbmp=cbmp,
            game_key="fh6",
        )
    except (ShaderBindingError, MaterialTranslateError, OSError, RuntimeError) as exc:
        binding_error = str(exc)

    try:
        spec = builder.build(instance_key, material, resolver=resolver)
    except MaterialNameError as exc:
        name_error = str(exc)
    except ShaderBindingError as exc:
        binding_error = binding_error or str(exc)
    except MaterialTranslateError as exc:
        translate_error = str(exc)
    except Exception as exc:  # noqa: BLE001 — surface as builder/capability failure
        translate_error = f"{type(exc).__name__}: {exc}"

    consumed = _consumed_slot_hashes(spec)
    tex_rows, unresolved, missing_tex, missing_prov = _texture_diagnostics(
        material=material,
        resolver=resolver,
        bindings=bindings,
        params=params,
        consumed_hashes=consumed,
    )
    if name_error:
        missing_prov = True

    has_surface = bool(spec is not None and spec.valid)
    evidence_lines: list[str] = []
    if spec is not None:
        for slot in spec.textures:
            evidence_lines.extend(slot.evidence)

    probe = probe_all_capabilities(
        shader_name=shader_name,
        has_resolvable_surface=has_surface,
        evidence_lines=tuple(evidence_lines),
    )

    invalid_binding = binding_error is not None and spec is None
    status = classify_capability_status(
        capability_selected=probe.selected,
        unresolved_semantics=unresolved,
        missing_texture=missing_tex and not has_surface,
        missing_provenance=missing_prov and not has_surface,
        invalid_binding=invalid_binding,
    )
    # Prefer more specific failure when build raised without a surface.
    if not has_surface:
        if name_error:
            status = MaterialStatus.MISSING_PROVENANCE
        elif binding_error and not translate_error:
            status = MaterialStatus.INVALID_BINDING
        elif translate_error:
            status = MaterialStatus.UNRESOLVED_CAPABILITY
        elif missing_tex:
            status = MaterialStatus.MISSING_TEXTURE
        else:
            status = MaterialStatus.UNRESOLVED_CAPABILITY

    errors: list[str] = []
    warnings: list[str] = []
    failure_reason = ""
    if name_error:
        errors.append(name_error)
        failure_reason = name_error
    if binding_error:
        errors.append(binding_error)
        failure_reason = failure_reason or binding_error
    if translate_error:
        errors.append(translate_error)
        failure_reason = failure_reason or translate_error
    for reason in probe.rejection_reasons:
        if reason not in errors:
            warnings.append(reason) if has_surface else errors.append(reason)
        failure_reason = failure_reason or reason
    if unresolved and has_surface:
        warnings.append(
            f"{len(unresolved)} TXMP NameHash(es) with known/unwired semantics"
        )

    capability_outcome = (
        StageOutcome.OK
        if probe.selected and status is MaterialStatus.SUPPORTED
        else StageOutcome.PARTIAL
        if probe.selected
        else StageOutcome.FAILED
    )

    diag = MaterialDiagnostic(
        material_name=_display_name(instance_key),
        instance_key=instance_key,
        shader_name=shader_name,
        material_name_hash=None,
        shader_name_hash=None,
        capability=(
            probe.capability.value
            if probe.capability is not None
            else None
        ),
        status=status,
        instance_parameters=param_rows,
        texture_bindings=tex_rows,
        unresolved_semantics=unresolved,
        evidence=probe.evidence,
        warnings=tuple(warnings),
        errors=tuple(errors),
        parsing_outcome=StageOutcome.OK,
        capability_outcome=capability_outcome,
        construction_outcome=StageOutcome.SKIPPED,
        assignment_outcome=AssignmentOutcome.SKIPPED,
        failure_reason=failure_reason,
    )
    return MaterialResolveResult(spec=spec if has_surface else None, diagnostic=diag)
