"""Reconcile raw relevant DXIL sites against contract dispositions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .completeness_status import (
    CURRENT_PER_INSTANCE_EVALUATION,
    CURRENT_RUNTIME_ARCHITECTURE,
    CURRENT_SEMANTIC_COVERAGE,
    SiteDisposition,
)


@dataclass
class ShaCoverageRow:
    shader_family: str
    shaderbin_sha256: str
    raw_relevant: int = 0
    contracted_relevant: int = 0
    imported: int = 0
    proven_inactive: int = 0
    proven_duplicate: int = 0
    engine_procedural: int = 0
    explicitly_unsupported: int = 0
    unresolved: int = 0
    dispositions: list[dict[str, Any]] = field(default_factory=list)

    def reconcile_ok(self) -> bool:
        accounted = (
            self.imported
            + self.proven_inactive
            + self.proven_duplicate
            + self.engine_procedural
            + self.explicitly_unsupported
            + self.unresolved
        )
        return accounted == self.raw_relevant and self.unresolved == 0

    def to_dict(self) -> dict[str, Any]:
        accounted = (
            self.imported
            + self.proven_inactive
            + self.proven_duplicate
            + self.engine_procedural
            + self.explicitly_unsupported
            + self.unresolved
        )
        return {
            "shader_family": self.shader_family,
            "shaderbin_sha256": self.shaderbin_sha256,
            "raw_relevant": self.raw_relevant,
            "contracted_relevant": self.contracted_relevant,
            "imported": self.imported,
            "proven_inactive": self.proven_inactive,
            "proven_duplicate": self.proven_duplicate,
            "engine_procedural": self.engine_procedural,
            "explicitly_unsupported": self.explicitly_unsupported,
            "unresolved": self.unresolved,
            "accounted": accounted,
            "reconcile_ok": self.reconcile_ok(),
            "missing_from_contracts": max(
                0, self.raw_relevant - self.contracted_relevant
            ),
            "runtime_architecture": CURRENT_RUNTIME_ARCHITECTURE.value,
            "semantic_coverage": CURRENT_SEMANTIC_COVERAGE.value,
            "per_instance_evaluation": CURRENT_PER_INSTANCE_EVALUATION.value,
        }


def classify_contract_site(site: dict[str, Any]) -> SiteDisposition:
    """Map one contract JSON row to a disposition (honest defaults)."""
    if site.get("blender_import") is True:
        return SiteDisposition.IMPORTED_ACTIVE_SEMANTIC
    disp = site.get("disposition")
    if disp:
        try:
            return SiteDisposition(str(disp))
        except ValueError:
            pass
    uv = site.get("uv_expression")
    role = str(site.get("semantic_role") or site.get("declared_txmp_name") or "")
    if isinstance(uv, dict) and uv.get("kind") == "UNRESOLVED_SAMPLE_SITE_CONTRACT":
        return SiteDisposition.UNRESOLVED_SAMPLE_SITE
    if role.lower() in ("", "unresolved", "none"):
        return SiteDisposition.UNRESOLVED_SAMPLE_SITE
    # Non-imported with a declared role but no proven disposition yet.
    return SiteDisposition.UNRESOLVED_SAMPLE_SITE


def build_sha_coverage(
    *,
    shader_family: str,
    shaderbin_sha256: str,
    raw_relevant_sites: list[dict[str, Any]],
    contract: dict[str, Any] | None,
) -> ShaCoverageRow:
    """Reconcile raw relevant inventory rows with contract dispositions.

    Sites present in raw but absent from contracts → UNRESOLVED.
    ``blender_import=false`` without an explicit disposition → UNRESOLVED.
    """
    row = ShaCoverageRow(
        shader_family=shader_family,
        shaderbin_sha256=shaderbin_sha256,
        raw_relevant=len(raw_relevant_sites),
    )
    contract_sites: list[dict[str, Any]] = []
    if contract:
        for p in contract.get("relevant_passes") or []:
            for s in p.get("import_sample_sites") or []:
                contract_sites.append({**s, "_scenario": p.get("scenario")})
    row.contracted_relevant = len(contract_sites)

    # Index contracts by (instruction_id, texture_register)
    by_key: dict[tuple[str, int], dict] = {}
    for s in contract_sites:
        key = (str(s.get("instruction_id") or ""), int(s.get("texture_register") or -1))
        by_key[key] = s

    seen_contract: set[tuple[str, int]] = set()
    for raw in raw_relevant_sites:
        instr = str(raw.get("instruction_id") or "")
        treg = int(raw.get("texture_register") or -1)
        key = (instr, treg)
        cs = by_key.get(key)
        if cs is None:
            # Try match on instruction alone
            cs = next(
                (
                    v
                    for (i, t), v in by_key.items()
                    if i == instr or (t == treg and not i)
                ),
                None,
            )
        if cs is None:
            row.unresolved += 1
            row.dispositions.append(
                {
                    "instruction_id": instr,
                    "texture_register": treg,
                    "disposition": SiteDisposition.UNRESOLVED_SAMPLE_SITE.value,
                    "reason": "raw relevant site absent from contract table",
                }
            )
            continue
        seen_contract.add(
            (
                str(cs.get("instruction_id") or instr),
                int(cs.get("texture_register") or treg),
            )
        )
        disp = classify_contract_site(cs)
        entry = {
            "instruction_id": instr,
            "texture_register": treg,
            "disposition": disp.value,
            "blender_import": bool(cs.get("blender_import")),
            "semantic_role": cs.get("semantic_role") or cs.get("declared_txmp_name"),
            "branch_status": cs.get("branch_status") or raw.get("branch_status"),
        }
        row.dispositions.append(entry)
        if disp is SiteDisposition.IMPORTED_ACTIVE_SEMANTIC:
            row.imported += 1
        elif disp is SiteDisposition.PROVEN_INACTIVE_BRANCH:
            row.proven_inactive += 1
        elif disp is SiteDisposition.PROVEN_DUPLICATE_SAMPLE:
            row.proven_duplicate += 1
        elif disp in (
            SiteDisposition.PROVEN_ENGINE_GLOBAL,
            SiteDisposition.PROVEN_PROCEDURAL_NON_MATERIAL_INPUT,
            SiteDisposition.PROVEN_NO_FINAL_SURFACE_CONTRIBUTION,
        ):
            row.engine_procedural += 1
        elif disp is SiteDisposition.EXPLICITLY_UNSUPPORTED_ACTIVE_SITE:
            row.explicitly_unsupported += 1
        else:
            row.unresolved += 1

    return row
