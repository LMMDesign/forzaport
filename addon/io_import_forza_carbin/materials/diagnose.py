"""Diagnose a parsed MatI through capability resolution (no bpy).

Runs the authoritative resolver once and classifies MaterialStatus for the
import report. Does not feed builder success into capability selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..parsing.material import ShaderParameterName as SPN
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
from .model import (
    InvalidMaterialBinding,
    MaterialResolution,
    MissingMaterialProvenance,
    UnsupportedMaterialCapability,
)
from .name_hashes import MaterialNameError, name_for_hash
from .pipeline_v3 import (
    CleanMaterialBuilder,
    MaterialSpec,
    _ALPHA_NAMES,
    _BASE_NAMES,
    _NORMAL_NAMES,
    _RMAO_NAMES,
    _binding_uv,
    material_spec_from_resolved,
)
from .resolver import path_exists
from .texture_source import resolve_texture_source
from .txmp_semantics import CLEAN_SURFACE_TXMP_NAMES, try_semantics_for_txmp_hash

# Instance parameters the clean-surface capability reads (NameHash ints).
_CONSUMED_PARAM_HASHES = frozenset(
    {
        int(SPN.UniqueBaseColorSwitchBool),
        int(SPN.UniqueBaseTextureSwitchBool),
        int(SPN.UniqueBaseColorColorParam),
        int(SPN.ColorGroupSwitchBool),
        int(SPN.PaintColorGroupColorParam),
        int(SPN.PaintColorColorParam),
        int(SPN.UniqueLiverySwitchBool),
        int(SPN.MaskedLiveryBool),
        int(SPN.WeaveColorTintA),
        int(SPN.WeaveColorTintB),
        int(SPN.WeaveMask),
        int(SPN.UseAlphaTestBool),
        int(SPN.UseAlphaBlendBool),
        int(SPN.AlphaTransparencyBool),
    }
)

_CONTRACT_TXMP = CLEAN_SURFACE_TXMP_NAMES | (
    _BASE_NAMES | _ALPHA_NAMES | _NORMAL_NAMES | _RMAO_NAMES
)


@dataclass(frozen=True)
class MaterialResolveResult:
    """Capability-resolution result before Blender node construction."""

    spec: MaterialSpec | None
    diagnostic: MaterialDiagnostic
    resolution: MaterialResolution | None = None


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
        interpreted = None
        if name and name in _CONTRACT_TXMP:
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
    binding_decisions=(),
    selected_base_color_source: str | None = None,
) -> tuple[tuple[TextureBindingDiagnostic, ...], tuple[int, ...], bool, bool]:
    """Inventory every TXMP; flag unresolved known semantics and missing files."""
    txmp = getattr(material, "txmp", None) or {}
    rows: list[TextureBindingDiagnostic] = []
    unresolved: list[int] = []
    missing_texture = False
    missing_provenance = False
    decisions_by_hash = {
        int(d.slot.param_hash) & 0xFFFFFFFF: d for d in (binding_decisions or ())
    }

    for h in sorted(txmp.keys(), key=lambda x: x & 0xFFFFFFFF):
        treg = txmp[h]
        p = params.get(h)
        path = getattr(p, "path", "") or "" if p is not None else ""
        src = resolve_texture_source(path, resolver) if path else None
        exists = bool(src and src.exists) if src is not None else (
            bool(path) and path_exists(path, resolver)
        )
        name = name_for_hash(h)
        if name is None:
            missing_provenance = True
        sem = try_semantics_for_txmp_hash(h)
        role = sem.role if sem is not None else None
        bind = bindings.textures.get(int(treg)) if bindings is not None else None
        sha = None
        if bindings is not None:
            sha = (getattr(bindings, "source_hashes", None) or {}).get(
                "shaderbin_sha256"
            )
        uv = (
            _binding_uv(
                bind, params, txmp_name=name, shaderbin_sha256=sha
            )
            if bind is not None
            else None
        )
        consumed = (h & 0xFFFFFFFF) in consumed_hashes
        decision = decisions_by_hash.get(h & 0xFFFFFFFF)
        activation = decision.activation.value if decision is not None else None
        activation_reason = decision.reason if decision is not None else None
        controlling = (
            tuple(int(x) & 0xFFFFFFFF for x in decision.controlling_parameters)
            if decision is not None
            else ()
        )
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

        source_failure = None
        if not path:
            unresolved_reason = "empty_path"
            if role is not None and not (sem and sem.is_fx_layer):
                unresolved.append(h & 0xFFFFFFFF)
        elif not exists:
            missing_texture = True
            source_failure = (
                src.failure.value if src is not None and src.failure is not None else None
            )
            unresolved_reason = source_failure or "SOURCE_TEXTURE_NOT_FOUND"
            if role is not None and not (sem and sem.is_fx_layer):
                unresolved.append(h & 0xFFFFFFFF)
        elif activation == "inactive_placeholder":
            unresolved_reason = "inactive_placeholder"
            # Not an error — retained for diagnostics only.
        elif activation == "conditional_unresolved":
            unresolved_reason = "conditional_unresolved_activation"
            unresolved.append(h & 0xFFFFFFFF)
        elif sem is not None and sem.is_fx_layer:
            unresolved_reason = "fx_layer_inactive"
        elif role is not None and not consumed:
            unresolved_reason = f"semantic_not_in_capability:{role}"
            unresolved.append(h & 0xFFFFFFFF)
        elif role is None and name is not None and not consumed:
            unresolved_reason = "unbound_unknown_or_inactive"
        elif uv is None and name in _CONTRACT_TXMP:
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
        if decision is not None:
            prov.append(
                ProvenanceDiagnostic(
                    kind="activation",
                    detail=f"{activation}: {activation_reason}",
                    source="materials.binding_activation",
                )
            )
        if src is not None:
            prov.extend(src.provenance)

        attempt_rows = tuple(
            f"{a.kind}:{a.location}:{'hit' if a.hit else 'miss'}"
            + (f":{a.detail}" if a.detail else "")
            for a in (src.attempts if src is not None else ())
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
                canonical_path=src.canonical_game_path if src else None,
                source_kind=src.kind.value if src else None,
                archive_path=src.archive_path if src else None,
                archive_member=src.archive_member if src else None,
                filesystem_path=src.filesystem_path if src else None,
                source_failure=source_failure,
                attempts=attempt_rows,
                activation=activation,
                activation_reason=activation_reason,
                controlling_parameters=controlling,
                selected_base_color_source=selected_base_color_source,
            )
        )

    return tuple(rows), tuple(sorted(set(unresolved))), missing_texture, missing_provenance


def _display_name(instance_key: str) -> str:
    parts = instance_key.split("|")
    if len(parts) >= 2:
        return parts[1]
    return instance_key


def _as_diag_evidence(evidence) -> tuple[ProvenanceDiagnostic, ...]:
    rows: list[ProvenanceDiagnostic] = []
    for ev in evidence or ():
        if isinstance(ev, ProvenanceDiagnostic):
            rows.append(ev)
        else:
            rows.append(
                ProvenanceDiagnostic(
                    kind=getattr(ev, "kind", "evidence"),
                    detail=getattr(ev, "detail", str(ev)),
                    source=getattr(ev, "source", ""),
                )
            )
    return tuple(rows)


def resolve_with_diagnostics(
    builder: CleanMaterialBuilder,
    instance_key: str,
    material,
    resolver=None,
) -> MaterialResolveResult:
    """Run one authoritative resolve; return MaterialSpec adapter + diagnostic."""
    shader_name = getattr(material, "shader_name", None)
    params = getattr(material, "parameters", None) or {}
    param_rows = _parameter_diagnostics(params)

    resolution: MaterialResolution | None = None
    resolve_error: str | None = None
    name_error: str | None = None
    binding_error: str | None = None

    try:
        resolution = builder.resolve(instance_key, material, resolver=resolver)
    except MaterialNameError as exc:
        name_error = str(exc)
    except Exception as exc:  # noqa: BLE001 — surface as unresolved capability
        resolve_error = f"{type(exc).__name__}: {exc}"

    spec: MaterialSpec | None = None
    consumed: set[int] = set()
    bindings = None
    probe_selected = False
    probe_evidence: tuple[ProvenanceDiagnostic, ...] = ()
    probe_reasons: tuple[str, ...] = ()
    capability_value: str | None = None
    failure_exc = None

    if resolution is not None:
        bindings = resolution.bindings
        consumed = set(resolution.consumed_txmp_hashes)
        probe = resolution.probe
        probe_selected = resolution.is_selected
        probe_evidence = _as_diag_evidence(probe.evidence)
        probe_reasons = probe.rejection_reasons
        if probe.kind is not None:
            capability_value = probe.kind.value
        failure_exc = resolution.failure_exception
        if isinstance(failure_exc, MissingMaterialProvenance):
            name_error = name_error or str(failure_exc)
        elif isinstance(failure_exc, InvalidMaterialBinding):
            binding_error = str(failure_exc)
        if resolution.resolved is not None and probe_selected:
            spec = material_spec_from_resolved(resolution.resolved)

    selected_base = None
    if (
        resolution is not None
        and resolution.resolved is not None
        and resolution.resolved.capability is not None
    ):
        selected_base = resolution.resolved.capability.base_color_source.kind.value

    tex_rows, unresolved, missing_tex, missing_prov = _texture_diagnostics(
        material=material,
        resolver=resolver,
        bindings=bindings,
        params=params,
        consumed_hashes=consumed,
        binding_decisions=(
            resolution.texture_binding_decisions if resolution is not None else ()
        ),
        selected_base_color_source=selected_base,
    )
    if name_error:
        missing_prov = True

    has_surface = spec is not None and spec.valid
    invalid_binding = binding_error is not None and not has_surface
    status = classify_capability_status(
        capability_selected=probe_selected,
        unresolved_semantics=unresolved,
        missing_texture=missing_tex and not has_surface,
        missing_provenance=missing_prov and not has_surface,
        invalid_binding=invalid_binding,
    )
    if not has_surface:
        if name_error:
            status = MaterialStatus.MISSING_PROVENANCE
        elif binding_error:
            status = MaterialStatus.INVALID_BINDING
        elif isinstance(failure_exc, UnsupportedMaterialCapability) or resolve_error:
            status = MaterialStatus.UNRESOLVED_CAPABILITY
        elif missing_tex:
            # Prefer the most common structured source failure among bindings.
            fail_codes = [
                tb.source_failure or tb.unresolved_reason
                for tb in tex_rows
                if not tb.path_exists and (tb.source_failure or tb.unresolved_reason)
            ]
            preferred = None
            for code in fail_codes:
                try:
                    preferred = MaterialStatus(code)
                    break
                except ValueError:
                    continue
            status = preferred or MaterialStatus.MISSING_TEXTURE
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
    if resolve_error:
        errors.append(resolve_error)
        failure_reason = failure_reason or resolve_error
    if failure_exc is not None and str(failure_exc) not in errors:
        errors.append(str(failure_exc))
        failure_reason = failure_reason or str(failure_exc)
    for reason in probe_reasons:
        if reason not in errors:
            (warnings if has_surface else errors).append(reason)
        failure_reason = failure_reason or reason
    if unresolved and has_surface:
        warnings.append(
            f"{len(unresolved)} TXMP NameHash(es) with known/unwired semantics"
        )

    capability_outcome = (
        StageOutcome.OK
        if probe_selected and status is MaterialStatus.SUPPORTED
        else StageOutcome.PARTIAL
        if probe_selected
        else StageOutcome.FAILED
    )

    diag = MaterialDiagnostic(
        material_name=_display_name(instance_key),
        instance_key=instance_key,
        shader_name=shader_name,
        material_name_hash=None,
        shader_name_hash=None,
        capability=capability_value,
        status=status,
        instance_parameters=param_rows,
        texture_bindings=tex_rows,
        unresolved_semantics=unresolved,
        evidence=probe_evidence,
        warnings=tuple(warnings),
        errors=tuple(errors),
        parsing_outcome=StageOutcome.OK,
        capability_outcome=capability_outcome,
        construction_outcome=StageOutcome.SKIPPED,
        assignment_outcome=AssignmentOutcome.SKIPPED,
        failure_reason=failure_reason,
    )
    return MaterialResolveResult(
        spec=spec if has_surface else None,
        diagnostic=diag,
        resolution=resolution,
    )
