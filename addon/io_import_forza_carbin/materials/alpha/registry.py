"""AlphaContractRegistry — data-driven evaluation by exact shader SHA."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..forza_ir import Clamp, MaterialExpression, Multiply
from ..model import ProvenanceDiagnostic as PD
from .blender_plan import blender_plan_from_alpha_ir
from .ir import (
    AlphaSemanticsIR,
    AuthoredMaskIR,
    EvaluatedAlphaSemantics,
    OutputAlphaIR,
    PassVisibilityIR,
    ShadingAttenuationIR,
    SurfaceVisibilityIR,
)
from .types import (
    CAR_STANDARD_SHADERBIN_SHA256,
    SCHEMA_VERSION,
    SourceVisibilitySemantic,
    ThresholdStatus,
)

_CONTRACTS_DIR = Path(__file__).resolve().parent / "contracts"


class AlphaContractError(Exception):
    """Fail-closed contract load / evaluation error."""


@dataclass(frozen=True)
class ShaderIdentityView:
    shader_name: str | None
    shaderbin_sha256: str | None
    permutation: str | None = None


def _pd(detail: str, *, kind: str = "alpha_contract") -> PD:
    return PD(kind=kind, detail=detail, source="materials.alpha.registry")


def load_index(path: Path | None = None) -> dict[str, Any]:
    p = path or (_CONTRACTS_DIR / "index.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    if int(data.get("schema_version", -1)) != SCHEMA_VERSION:
        raise AlphaContractError(
            f"unknown alpha contracts index schema_version={data.get('schema_version')!r}"
        )
    return data


def load_contract(sha256: str, *, contracts_dir: Path | None = None) -> dict[str, Any]:
    root = contracts_dir or _CONTRACTS_DIR
    index = load_index(root / "index.json")
    entry = (index.get("contracts") or {}).get(sha256)
    if not entry:
        raise AlphaContractError(f"no alpha contract registered for sha={sha256}")
    rel = entry["path"]
    data = json.loads((root / rel).read_text(encoding="utf-8"))
    if int(data.get("schema_version", -1)) != SCHEMA_VERSION:
        raise AlphaContractError(
            f"unknown contract schema_version for sha={sha256}: "
            f"{data.get('schema_version')!r}"
        )
    if data.get("shader_sha256") != sha256:
        raise AlphaContractError("contract shader_sha256 mismatch vs lookup key")
    return data


class AlphaContractRegistry:
    def __init__(self, contracts_dir: Path | None = None) -> None:
        self.contracts_dir = contracts_dir or _CONTRACTS_DIR
        self._cache: dict[str, dict[str, Any]] = {}

    def get(self, sha256: str) -> dict[str, Any]:
        if sha256 not in self._cache:
            self._cache[sha256] = load_contract(
                sha256, contracts_dir=self.contracts_dir
            )
        return self._cache[sha256]

    def has(self, sha256: str) -> bool:
        try:
            load_index(self.contracts_dir / "index.json")
        except AlphaContractError:
            return False
        index = load_index(self.contracts_dir / "index.json")
        return sha256 in (index.get("contracts") or {})


_DEFAULT_REGISTRY = AlphaContractRegistry()


def _branch_key_for_transparency(value: bool | None) -> str:
    if value is True:
        return "AlphaTransparency=true"
    if value is False:
        return "AlphaTransparency=false"
    return "AlphaTransparency=absent"


def _bind_authored_mask_expression(
    *,
    mask_expr: MaterialExpression | None,
) -> MaterialExpression | None:
    return mask_expr


def evaluate_alpha_semantics(
    shader_identity: ShaderIdentityView | Mapping[str, Any],
    permutation: str | None,
    mati_parameters: Mapping[str, Any] | None,
    resolved_bindings: Mapping[str, Any] | None = None,
    *,
    authored_mask_expression: MaterialExpression | None = None,
    alpha_transparency: bool | None = None,
    registry: AlphaContractRegistry | None = None,
) -> EvaluatedAlphaSemantics:
    """Production entry: exact-SHA contract → AlphaSemanticsIR + BlenderAlphaPlan.

    Family-specific Python evaluators must not be called from production.
    ``authored_mask_expression`` is the already-built IR expression for the
    contract's authored mask (binding resolution happens upstream).
    """
    reg = registry or _DEFAULT_REGISTRY
    if isinstance(shader_identity, Mapping):
        sha = shader_identity.get("shaderbin_sha256")
        name = shader_identity.get("shader_name")
        perm = permutation or shader_identity.get("permutation")
    else:
        sha = shader_identity.shaderbin_sha256
        name = shader_identity.shader_name
        perm = permutation or shader_identity.permutation

    if not sha:
        raise AlphaContractError("shaderbin_sha256 required")

    try:
        contract = reg.get(sha)
    except AlphaContractError as exc:
        alpha_ir = AlphaSemanticsIR(
            source_visibility=SourceVisibilitySemantic.REJECTED_UNSUPPORTED_BRANCH,
            shader_sha256=sha,
            unresolved=(str(exc),),
            evidence=(_pd(str(exc)),),
        )
        plan = blender_plan_from_alpha_ir(alpha_ir)
        return EvaluatedAlphaSemantics(
            source_visibility=alpha_ir.source_visibility,
            alpha_ir=alpha_ir,
            blender_plan=plan,
            branch_key="unknown_sha",
            contract_id="",
            unresolved=(str(exc),),
        )

    # Resolve AlphaTransparency from explicit arg or MatI map (hash or name).
    transparency = alpha_transparency
    if transparency is None and mati_parameters is not None:
        from ..parsing.material import ShaderParameterName as SPN

        h = int(SPN.AlphaTransparencyBool)
        raw = mati_parameters.get(h) or mati_parameters.get(str(h))
        if raw is not None:
            val = getattr(raw, "value", raw)
            if isinstance(val, bool):
                transparency = val

    branch_key = _branch_key_for_transparency(transparency)
    branches = contract.get("branches") or {}
    rejected = contract.get("rejected_branches") or {}

    if branch_key in rejected:
        reason = rejected[branch_key].get("reason") or "rejected branch"
        alpha_ir = AlphaSemanticsIR(
            source_visibility=SourceVisibilitySemantic.REJECTED_UNSUPPORTED_BRANCH,
            contract_id=contract.get("contract_id"),
            shader_sha256=sha,
            branch_key=branch_key,
            unresolved=(reason,),
            evidence=(_pd(reason),),
        )
        return EvaluatedAlphaSemantics(
            source_visibility=alpha_ir.source_visibility,
            alpha_ir=alpha_ir,
            blender_plan=blender_plan_from_alpha_ir(alpha_ir),
            branch_key=branch_key,
            contract_id=str(contract.get("contract_id") or ""),
            unresolved=(reason,),
        )

    if branch_key not in branches:
        raise AlphaContractError(
            f"missing branch {branch_key!r} in contract {contract.get('contract_id')}"
        )

    branch = branches[branch_key]
    try:
        src_vis = SourceVisibilitySemantic(branch["source_visibility"])
    except Exception as exc:  # noqa: BLE001
        raise AlphaContractError(f"malformed source_visibility: {exc}") from exc

    mask_expr = _bind_authored_mask_expression(
        mask_expr=authored_mask_expression
    )
    masks_meta = contract.get("authored_masks") or []
    authored = tuple(
        AuthoredMaskIR(
            mask_id=m["mask_id"],
            equation=m["equation"],
            expression=mask_expr if m.get("mask_id") == "authored_mask" else None,
            evidence=tuple(_pd(e) for e in (m.get("evidence") or ())),
        )
        for m in masks_meta
    )

    mv_spec = branch.get("main_visibility") or {}
    mv_sem = SourceVisibilitySemantic(
        mv_spec.get("semantic") or branch["source_visibility"]
    )
    vis_expr = None
    if mv_spec.get("expression") == "Constant(1.0)":
        vis_expr = None
    elif mv_spec.get("expression_ref") == "authored_mask":
        vis_expr = mask_expr

    main_vis = SurfaceVisibilityIR(
        semantic=mv_sem,
        expression=vis_expr,
        source_threshold=mv_spec.get("source_threshold"),
        source_threshold_provenance=mv_spec.get("source_threshold_provenance"),
        evidence=tuple(_pd(e) for e in (branch.get("evidence") or ())),
    )

    shading = None
    sh_spec = branch.get("shading_attenuation")
    if sh_spec:
        shading = ShadingAttenuationIR(
            expression=mask_expr if sh_spec.get("expression_ref") == "authored_mask" else None,
            equation=sh_spec.get("equation"),
            evidence=(_pd("shading_attenuation from contract branch"),),
        )

    unresolved = tuple(contract.get("unresolved") or ())
    if contract.get("fixed_function_status", "").startswith("UNRESOLVED"):
        unresolved = unresolved + ("fixed_function_status_unresolved",)

    alpha_ir = AlphaSemanticsIR(
        source_visibility=src_vis,
        authored_masks=authored,
        main_visibility=main_vis,
        shadow_visibility=None,
        ray_visibility=None,
        depth_visibility=None,
        output_alpha=OutputAlphaIR(
            expression=None,
            equation="see contract passes",
        ),
        shading_attenuation=shading,
        contract_id=str(contract.get("contract_id") or ""),
        shader_sha256=sha,
        branch_key=branch_key,
        evidence=tuple(_pd(e) for e in (branch.get("evidence") or ())),
        unresolved=unresolved,
    )

    # Prefer contract-declared blender block when present; still go through translator.
    plan = blender_plan_from_alpha_ir(alpha_ir)
    bspec = branch.get("blender") or {}
    if bspec:
        # Overlay explicit contract blender fields (authoritative for this SHA).
        from .ir import BlenderAlphaPlan
        from .types import BlenderRenderMode

        plan = BlenderAlphaPlan(
            render_mode=str(
                bspec.get("render_mode") or plan.render_mode
            ),
            alpha_expression=(
                mask_expr
                if str(bspec.get("render_mode")) in ("CLIP", "HASHED", "BLEND")
                else None
            ),
            alpha_threshold=bspec.get("alpha_threshold", plan.alpha_threshold),
            threshold_status=ThresholdStatus(
                bspec.get("threshold_status") or plan.threshold_status.value
            ),
            approximation_reason=bspec.get(
                "approximation_reason", plan.approximation_reason
            ),
            shading_attenuation_expression=(
                mask_expr
                if bspec.get("apply_shading_attenuation_to_base_color")
                else plan.shading_attenuation_expression
            ),
            apply_shading_attenuation_to_base_color=bool(
                bspec.get("apply_shading_attenuation_to_base_color")
            ),
            evidence=plan.evidence + ("contract.blender overlay",),
            unresolved=plan.unresolved,
        )

    return EvaluatedAlphaSemantics(
        source_visibility=src_vis,
        alpha_ir=alpha_ir,
        blender_plan=plan,
        branch_key=branch_key,
        contract_id=str(contract.get("contract_id") or ""),
        evidence=tuple(branch.get("evidence") or ()),
        unresolved=unresolved,
    )


# --- Deprecated compatibility wrappers (call generic system only) -------------


def evaluate_car_standard_alpha(
    *,
    alpha_transparency: bool | None,
    shaderbin_sha256: str | None,
) -> EvaluatedAlphaSemantics:
    """DEPRECATED: use ``evaluate_alpha_semantics``. Thin wrapper."""
    return evaluate_alpha_semantics(
        ShaderIdentityView(
            shader_name="car_standard",
            shaderbin_sha256=shaderbin_sha256,
            permutation="CarLightScenario",
        ),
        "CarLightScenario",
        None,
        None,
        alpha_transparency=alpha_transparency,
        authored_mask_expression=None,
    )


def car_standard_alpha_contract() -> dict[str, Any]:
    """DEPRECATED: load data contract for exact car_standard SHA."""
    return load_contract(CAR_STANDARD_SHADERBIN_SHA256)
