"""Evaluate exact-SHA sample-site contracts into per-site results.

Authoritative production path — not register merges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .pass_contracts import load_shader_pass_contract
from .pass_identity import classify_blender_relevance
from .sample_site_identity import ShaderSampleSiteIdentity
from .uv.uv_expr import (
    UvEvalResult,
    UvExprNode,
    evaluate_uv_expr,
    parse_uv_expr_json,
)
from .variant_selection import VariantResolution, resolve_shader_variant


@dataclass(frozen=True)
class BranchPredicate:
    param_hash: int
    param_type: int
    operator: str
    expected: Any
    missing_policy: str = "reject"
    provenance: str = ""


@dataclass
class EvaluatedSampleSite:
    identity: ShaderSampleSiteIdentity
    sample_site_id: str
    sampled_components: tuple[int, ...]
    uv_node: UvExprNode | None
    uv_eval: UvEvalResult | None
    semantic_role: str | None
    final_use: str | None
    blender_relevance: str
    relevance_evidence_status: str  # PROVISIONAL_NAME_CLASSIFICATION | CONTRACT_EVIDENCE | …
    blender_import: bool
    status: str  # ACTIVE | INACTIVE | REJECTED | UNRESOLVED
    branch_status: str = "UNCONDITIONAL"
    # UNCONDITIONAL | EXECUTABLE_PREDICATE | COMPILE_TIME_VARIANT | PASS_ONLY |
    # INACTIVE_OR_OPTIMISED | UNRESOLVED_PREDICATE | NOT_RELEVANT_TO_BLENDER
    branch_predicates: tuple[BranchPredicate, ...] = ()
    evidence: tuple[str, ...] = ()
    declared_txmp: str | None = None

    @property
    def scenario(self) -> str:
        return self.identity.scenario

    @property
    def texture_register(self) -> int:
        return self.identity.texture_register

    @property
    def resolved_texcoord(self) -> int | None:
        if self.uv_eval is None:
            return None
        return self.uv_eval.mesh_texcoord


@dataclass
class EvaluatedMaterialSampleSites:
    shaderbin_sha256: str
    variant: VariantResolution
    sites: list[EvaluatedSampleSite] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)
    compatibility_bridge_used: bool = False

    def by_identity(self) -> dict[str, EvaluatedSampleSite]:
        return {s.identity.as_key(): s for s in self.sites}

    def active_import_sites(self) -> list[EvaluatedSampleSite]:
        return [s for s in self.sites if s.blender_import and s.status == "ACTIVE"]


def _parse_branch(raw: Any) -> BranchPredicate | None:
    if not isinstance(raw, dict):
        return None
    ph = raw.get("param_hash") or raw.get("hash")
    if ph is None:
        return None
    if isinstance(ph, str):
        ph = int(ph, 16) if ph.startswith("0x") else int(ph)
    return BranchPredicate(
        param_hash=int(ph),
        param_type=int(raw.get("type") or raw.get("param_type") or 3),
        operator=str(raw.get("operator") or raw.get("true_when") or "nonzero"),
        expected=raw.get("expected"),
        missing_policy=str(raw.get("missing_policy") or "reject"),
        provenance=str(raw.get("provenance") or ""),
    )


def _eval_branch(pred: BranchPredicate, params: dict) -> tuple[bool | None, str]:
    from .uv.uv_expr import evaluate_predicate

    return evaluate_predicate(
        params=params,
        param_hash=pred.param_hash,
        param_type=pred.param_type,
        true_when=pred.operator
        if pred.operator
        not in ("eq",)
        else f"eq:{pred.expected}",
    )


def evaluate_material_sample_sites(
    *,
    shaderbin_sha256: str | None,
    params: dict,
    pso_members: dict[str, str] | None = None,
    pso_shas: dict[str, str] | None = None,
) -> EvaluatedMaterialSampleSites:
    """Evaluate JSON contracts → one EvaluatedSampleSite per contract site.

    ``pso_members`` / ``pso_shas`` map scenario → archive member / sha when known.
    """
    pso_members = pso_members or {}
    pso_shas = pso_shas or {}

    if not shaderbin_sha256:
        return EvaluatedMaterialSampleSites(
            shaderbin_sha256="",
            variant=resolve_shader_variant(shaderbin_sha256=None, params=params),
            rejection_reasons=["missing shaderbin_sha256 — fail closed"],
        )

    variant = resolve_shader_variant(
        shaderbin_sha256=shaderbin_sha256, params=params
    )
    out = EvaluatedMaterialSampleSites(
        shaderbin_sha256=shaderbin_sha256,
        variant=variant,
    )
    if variant.status == "REJECTED":
        out.rejection_reasons.append(f"variant rejected: {variant.provenance}")

    data = load_shader_pass_contract(shaderbin_sha256)
    if data is None:
        return out

    site_index = 0
    for pass_row in data.get("relevant_passes") or []:
        req = pass_row.get("requires_variant")
        if req and variant.status == "PROVEN" and variant.decoded_variant != req:
            continue
        if req and variant.status == "REJECTED":
            continue

        scenario = str(pass_row.get("scenario") or "")
        pass_variant = str(
            pass_row.get("variant")
            or (variant.decoded_variant if variant.status == "PROVEN" else "")
            or ""
        )
        member = str(
            pass_row.get("archive_member")
            or pso_members.get(scenario)
            or ""
        )
        pso_sha = str(pso_shas.get(scenario) or pso_shas.get(member) or "")
        stage = str(pass_row.get("stage") or "ps")

        name_relevance = classify_blender_relevance(scenario, pass_variant)
        # Filename/scenario heuristics are provisional only.
        if pass_row.get("blender_relevance") and (
            pass_row.get("reason")
            or pass_row.get("evidence")
            or pass_row.get("relevance_evidence")
        ):
            contract_relevance = str(pass_row.get("blender_relevance"))
            evidence_status = "CONTRACT_EVIDENCE"
        elif pass_row.get("blender_relevance"):
            # Declared in contract JSON without game-file proof → still provisional.
            contract_relevance = str(pass_row.get("blender_relevance"))
            evidence_status = "PROVISIONAL_NAME_CLASSIFICATION"
        else:
            contract_relevance = name_relevance or "UNRESOLVED"
            evidence_status = "PROVISIONAL_NAME_CLASSIFICATION"

        for s in pass_row.get("import_sample_sites") or []:
            regs: list[int] = []
            if "texture_register" in s:
                regs.append(int(s["texture_register"]))
            # Expand ranges into distinct placeholder sites (tyres) — each reg
            # gets its own identity; mark UNRESOLVED until per-instruction IDs exist.
            range_expanded = False
            if "texture_register_range" in s:
                lo, hi = s["texture_register_range"]
                regs.extend(range(int(lo), int(hi) + 1))
                range_expanded = True
            if not regs:
                continue

            uv_raw = s.get("uv_expression")
            if uv_raw is None:
                uv_raw = s.get("expected_uv_expression")
            uv_node = parse_uv_expr_json(uv_raw)

            branches: list[BranchPredicate] = []
            for b in s.get("branch_predicates") or []:
                bp = _parse_branch(b)
                if bp:
                    branches.append(bp)
            # Optional single predicate on site
            if s.get("branch_predicate"):
                bp = _parse_branch(s["branch_predicate"])
                if bp:
                    branches.append(bp)

            blender_import = bool(s.get("blender_import"))
            comps = tuple(s.get("expected_comps") or ())

            for treg in regs:
                instr = str(
                    s.get("instruction_id")
                    or s.get("sample_site_id")
                    or f"contract|{scenario}|t{treg}|i{site_index}"
                )
                if range_expanded:
                    instr = f"{instr}|t{treg}"
                identity = ShaderSampleSiteIdentity(
                    shaderbin_sha256=shaderbin_sha256,
                    full_archive_member=member,
                    pso_sha256=pso_sha,
                    variant=pass_variant,
                    scenario=scenario,
                    stage=stage,
                    instruction_id=instr,
                    texture_register=treg,
                    sampler_register=s.get("sampler_register"),
                    sample_site_index=site_index,
                )
                site_index += 1

                status = "ACTIVE"
                evidence: list[str] = list(s.get("evidence") or [])
                uv_eval: UvEvalResult | None = None
                rejection = None
                branch_status = "UNCONDITIONAL"

                # Branch predicates
                if branches:
                    branch_status = "EXECUTABLE_PREDICATE"
                elif pass_row.get("requires_variant"):
                    branch_status = "COMPILE_TIME_VARIANT"
                elif not blender_import:
                    branch_status = "NOT_RELEVANT_TO_BLENDER"

                for bp in branches:
                    ok, ev = _eval_branch(bp, params)
                    evidence.append(f"branch:{ev}")
                    if ok is None:
                        branch_status = "UNRESOLVED_PREDICATE"
                        if bp.missing_policy == "reject":
                            status = "REJECTED"
                            rejection = f"missing branch predicate: {ev}"
                        else:
                            status = "UNRESOLVED"
                            rejection = ev
                        break
                    if not ok:
                        status = "INACTIVE"
                        branch_status = "INACTIVE_OR_OPTIMISED"
                        evidence.append("branch_false→inactive")
                        break

                # Always evaluate UV when a node is present (evidence), even for
                # non-import / inactive sites.
                if uv_node is not None and status in ("ACTIVE", "INACTIVE"):
                    uv_eval = evaluate_uv_expr(uv_node, params=params)
                    evidence.extend(uv_eval.evidence)

                if status == "ACTIVE" and blender_import and uv_eval is not None:
                    if uv_eval.status == "REJECTED":
                        status = "REJECTED"
                        rejection = uv_eval.rejection
                    elif uv_eval.status != "PROVEN":
                        status = "UNRESOLVED"
                        rejection = uv_eval.rejection or "UV unresolved"
                    elif uv_eval.mesh_texcoord is None:
                        from .uv.uv_expr import (
                            MeshUVNode as _M,
                            OffsetUVNode,
                            RotateUVNode,
                            ScaleUVNode,
                        )

                        n = uv_eval.node
                        if isinstance(n, _M):
                            pass
                        elif isinstance(n, (ScaleUVNode, OffsetUVNode, RotateUVNode)):
                            if not isinstance(n.source, _M):
                                status = "UNRESOLVED"
                                rejection = (
                                    "composed UV not MeshUV-rooted for Blender yet"
                                )
                        else:
                            status = "UNRESOLVED"
                            rejection = (
                                "non-MeshUV UV tree not Blender-importable yet"
                            )

                if range_expanded:
                    status = "UNRESOLVED"
                    rejection = (
                        "register-range placeholder — need per-instruction "
                        "UNRESOLVED_SAMPLE_SITE_CONTRACT or exact ID"
                    )
                    evidence.append("UNRESOLVED_SAMPLE_SITE_CONTRACT:range")

                if not blender_import and status == "ACTIVE":
                    status = "INACTIVE"

                if rejection and status in ("REJECTED", "UNRESOLVED") and blender_import:
                    out.rejection_reasons.append(
                        f"{identity.as_key()}: {rejection}"
                    )

                if (
                    blender_import
                    and status == "ACTIVE"
                    and branch_status == "UNRESOLVED_PREDICATE"
                ):
                    status = "REJECTED"
                    out.rejection_reasons.append(
                        f"{identity.as_key()}: active site with UNRESOLVED_PREDICATE"
                    )

                out.sites.append(
                    EvaluatedSampleSite(
                        identity=identity,
                        sample_site_id=str(
                            s.get("sample_site_id") or identity.instruction_id
                        ),
                        sampled_components=comps,
                        uv_node=uv_node,
                        uv_eval=uv_eval,
                        semantic_role=s.get("semantic_role"),
                        final_use=s.get("final_use"),
                        blender_relevance=str(
                            s.get("blender_relevance") or contract_relevance
                        ),
                        relevance_evidence_status=evidence_status,
                        blender_import=blender_import,
                        status=status,
                        branch_status=branch_status,
                        branch_predicates=tuple(branches),
                        evidence=tuple(evidence),
                        declared_txmp=s.get("declared_txmp_name"),
                    )
                )
    return out
