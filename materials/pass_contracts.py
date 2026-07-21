"""Exact-SHA shader pass contracts (data-driven).

Contracts live under ``shader_pass_contracts/<sha256>.json``.
Production may import sample sites only when ``blender_import`` is true and
UV is uniquely proven. CarLightScenario is never treated as a complete schema.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

PRIMARY_RASTER_PASS = "CarLightScenario"

CAR_LIVERY_SHADERBIN_SHA256 = (
    "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb"
)


def contracts_dir() -> str:
    return os.path.join(os.path.dirname(__file__), "shader_pass_contracts")


@dataclass(frozen=True)
class PassMergeSpec:
    """Legacy-compatible merge descriptor derived from a JSON contract site."""

    pass_name: str
    pso_basename: str
    merge_texture_registers: tuple[int, ...]
    expected_uv_semantics: tuple[int, ...] | None = None
    expected_comps: tuple[int, ...] | None = None
    require_sv_target_alpha: bool = False
    evidence: str = ""
    blender_relevance: str = "UNRESOLVED"
    expected_uv_expression: str | None = None


@dataclass(frozen=True)
class SampleSiteContract:
    shaderbin_sha256: str
    pass_name: str
    shader_stage: str
    texture_register: int
    sampler_register: int | None
    instruction_id: str
    sampled_components: tuple[int, ...]
    uv_expression: str
    tiling_expression: str
    branch_conditions: tuple[str, ...]
    final_uses: tuple[str, ...]
    declared_txmp_binding: str | None
    evidence: tuple[str, ...] = ()
    blender_import: bool = False
    blender_relevance: str = "UNRESOLVED"


@lru_cache(maxsize=1)
def load_contract_index() -> dict[str, Any]:
    path = os.path.join(contracts_dir(), "index.json")
    if not os.path.isfile(path):
        return {"schema_version": 1, "contracts": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=64)
def load_shader_pass_contract(shaderbin_sha256: str | None) -> dict[str, Any] | None:
    if not shaderbin_sha256:
        return None
    index = load_contract_index()
    entry = next(
        (
            c
            for c in index.get("contracts") or []
            if c.get("shaderbin_sha256") == shaderbin_sha256
        ),
        None,
    )
    fname = entry.get("file") if entry else f"{shaderbin_sha256}.json"
    path = os.path.join(contracts_dir(), fname)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("shaderbin_sha256") != shaderbin_sha256:
        raise RuntimeError(
            f"contract file SHA mismatch: {path} vs key {shaderbin_sha256}"
        )
    return data


def list_contracted_shas() -> tuple[str, ...]:
    index = load_contract_index()
    shas = [
        c["shaderbin_sha256"]
        for c in index.get("contracts") or []
        if c.get("shaderbin_sha256")
    ]
    if shas:
        return tuple(sorted(set(shas)))
    d = contracts_dir()
    if not os.path.isdir(d):
        return ()
    return tuple(
        sorted(
            n[: -len(".json")]
            for n in os.listdir(d)
            if n.endswith(".json") and n != "index.json" and len(n) == 64 + 5
        )
    )


def _uv_semantics_from_expression(expr) -> tuple[int, ...] | None:
    if not expr:
        return None
    if isinstance(expr, dict):
        kind = expr.get("kind")
        if kind == "Select":
            # Multi-UV; resolved at MatI evaluation via UVChoice — do not lock one semantic.
            return None
        if kind == "Compose":
            return None
        summary = expr.get("summary") or expr.get("value")
        return _uv_semantics_from_expression(summary)
    if isinstance(expr, str):
        if expr.startswith("TEXCOORD") and expr[8:].isdigit():
            return (int(expr[8:]),)
    return None


def blender_import_merge_specs(
    shaderbin_sha256: str | None,
) -> tuple[PassMergeSpec, ...]:
    """Deprecated compatibility adapter: **one PassMergeSpec per site**.

    Do not collapse a pass's sites into a single multi-register merge descriptor.
    Authoritative evaluation uses ``sample_site_eval.evaluate_material_sample_sites``.
    """
    data = load_shader_pass_contract(shaderbin_sha256)
    if not data:
        return ()
    out: list[PassMergeSpec] = []
    for pass_row in data.get("relevant_passes") or []:
        member = pass_row.get("archive_member") or ""
        basename = os.path.basename(member.replace("\\", "/"))
        for s in pass_row.get("import_sample_sites") or []:
            if s.get("blender_import") is not True:
                continue
            if "texture_register" not in s:
                # Ranges are not mergeable as a single site.
                continue
            treg = int(s["texture_register"])
            uv_expr = s.get("uv_expression")
            if uv_expr is None:
                uv_expr = s.get("expected_uv_expression")
            out.append(
                PassMergeSpec(
                    pass_name=str(pass_row.get("scenario") or ""),
                    pso_basename=basename,
                    merge_texture_registers=(treg,),
                    expected_uv_semantics=_uv_semantics_from_expression(uv_expr),
                    expected_comps=tuple(s.get("expected_comps") or ()) or None,
                    require_sv_target_alpha=(
                        "alpha" in str(s.get("semantic_role") or "").lower()
                        or "SV_Target.a" in str(s.get("final_use") or "")
                    ),
                    evidence="; ".join(
                        e if isinstance(e, str) else str(e)
                        for e in (s.get("evidence") or [])
                    )
                    or str(pass_row.get("reason") or ""),
                    blender_relevance=str(
                        s.get("blender_relevance")
                        or pass_row.get("blender_relevance")
                        or "UNRESOLVED"
                    ),
                    expected_uv_expression=(
                        str(uv_expr) if isinstance(uv_expr, str) else None
                    ),
                )
            )
    return tuple(out)


def additional_passes_for_sha(shaderbin_sha256: str | None) -> tuple[PassMergeSpec, ...]:
    """Back-compat name used by extract_bindings — blender_import sites only."""
    return blender_import_merge_specs(shaderbin_sha256)


def sample_sites_for_sha(shaderbin_sha256: str | None) -> tuple[SampleSiteContract, ...]:
    data = load_shader_pass_contract(shaderbin_sha256)
    if not data:
        return ()
    sha = data["shaderbin_sha256"]
    rows: list[SampleSiteContract] = []
    for pass_row in data.get("relevant_passes") or []:
        for s in pass_row.get("import_sample_sites") or []:
            regs: list[int] = []
            if "texture_register" in s:
                regs.append(int(s["texture_register"]))
            if "texture_register_range" in s:
                lo, hi = s["texture_register_range"]
                regs.extend(range(int(lo), int(hi) + 1))
            if not regs:
                continue
            for treg in regs:
                rows.append(
                    SampleSiteContract(
                        shaderbin_sha256=sha,
                        pass_name=str(pass_row.get("scenario") or ""),
                        shader_stage=str(pass_row.get("stage") or "ps"),
                        texture_register=treg,
                        sampler_register=s.get("sampler_register"),
                        instruction_id=str(
                            s.get("instruction_id")
                            or s.get("sample_site_id")
                            or f"contract|{pass_row.get('scenario')}|t{treg}"
                        ),
                        sampled_components=tuple(s.get("expected_comps") or ()),
                        uv_expression=str(
                            (s.get("uv_expression") or {}).get("summary")
                            if isinstance(s.get("uv_expression"), dict)
                            else (
                                s.get("uv_expression")
                                or s.get("expected_uv_expression")
                                or "UNRESOLVED"
                            )
                        ),
                        tiling_expression=str(s.get("tiling_expression") or "UNRESOLVED"),
                        branch_conditions=(str(s.get("active_branch") or ""),),
                        final_uses=(str(s.get("final_use") or ""),),
                        declared_txmp_binding=s.get("declared_txmp_name"),
                        evidence=tuple(s.get("evidence") or ()),
                        blender_import=bool(s.get("blender_import")),
                        blender_relevance=str(
                            pass_row.get("blender_relevance") or "UNRESOLVED"
                        ),
                    )
                )
    return tuple(rows)


# Back-compat empty module-level (tests may import).
ADDITIONAL_PASS_SOURCES: dict[str, tuple[PassMergeSpec, ...]] = {}
SAMPLE_SITE_CONTRACTS: tuple[SampleSiteContract, ...] = ()


def _refresh_compat_maps() -> None:
    global ADDITIONAL_PASS_SOURCES, SAMPLE_SITE_CONTRACTS
    merges: dict[str, tuple[PassMergeSpec, ...]] = {}
    sites: list[SampleSiteContract] = []
    for sha in list_contracted_shas():
        merges[sha] = blender_import_merge_specs(sha)
        sites.extend(sample_sites_for_sha(sha))
    ADDITIONAL_PASS_SOURCES = merges
    SAMPLE_SITE_CONTRACTS = tuple(sites)


_refresh_compat_maps()
