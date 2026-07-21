"""Effective Blender material identity from canonical ForzaMaterialIR.

Two identities are kept distinct:

1. **Source instance** — full MatI / modelbin / slot provenance (never used as a
   share key).
2. **Effective material** — canonical active IR outputs only; SHA-256 fingerprint
   is the sole Blender datablock sharing key when production sharing is enabled.

Production sharing is **off by default**. Eligibility is family-by-family and
requires explicit approval. Unresolved / rejected / diagnostic materials never
merge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .eval_car_standard import CAR_STANDARD_SHADERBIN_SHA256
from .forza_ir import (
    Add,
    Channel,
    Clamp,
    ComponentSwizzle,
    ConstantColor,
    ConstantScalar,
    ForzaMaterialIR,
    GeneratedCoordinate,
    MeshUV,
    Mix,
    Multiply,
    NormalBlend,
    NormalDecode,
    OffsetUV,
    RasterState,
    RotateUV,
    ScaleUV,
    Select,
    SelectUV,
    Subtract,
    TextureSample,
    TextureSampleExpression,
)
from .ir_compiler import graph_build_plan_from_ir

# Bump when the canonical effective schema changes (invalidates share cache).
EFFECTIVE_MATERIAL_SCHEMA_VERSION = "1"

# Contract version strings included in the fingerprint so a contract revision
# never silently merges with materials built under an older contract.
CONTRACT_VERSION_BY_SHA256 = {
    CAR_STANDARD_SHADERBIN_SHA256: "1.0.0-b1",
}


class DedupEligibility(Enum):
    """Whether an evaluated IR may enter audit / production share paths."""

    AUDIT_AND_PRODUCTION_CANDIDATE = "audit_and_production_candidate"
    AUDIT_ONLY = "audit_only"
    INELIGIBLE = "ineligible"


# Explicit policy — do not infer from shader name alone without SHA + rules.
_ELIGIBILITY: dict[str, DedupEligibility] = {
    CAR_STANDARD_SHADERBIN_SHA256: DedupEligibility.AUDIT_AND_PRODUCTION_CANDIDATE,
    # car_carbonfiber: wait for B1.5 GUI approval.
    "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3": (
        DedupEligibility.INELIGIBLE
    ),
}


PRODUCTION_SHARING_ENABLED_SHA256: frozenset[str] = frozenset()
"""Empty until explicit family cutover approval. Never enable globally."""


def eligibility_for_shaderbin(shaderbin_sha256: str | None) -> DedupEligibility:
    if not shaderbin_sha256:
        return DedupEligibility.INELIGIBLE
    return _ELIGIBILITY.get(shaderbin_sha256, DedupEligibility.INELIGIBLE)


def production_sharing_enabled(shaderbin_sha256: str | None) -> bool:
    return bool(
        shaderbin_sha256
        and shaderbin_sha256 in PRODUCTION_SHARING_ENABLED_SHA256
        and eligibility_for_shaderbin(shaderbin_sha256)
        is DedupEligibility.AUDIT_AND_PRODUCTION_CANDIDATE
    )


def _round_f(x: float) -> float:
    return round(float(x), 8)


def _canon_texture_source(src) -> dict[str, Any]:
    """Identity of resolved texture bytes — no filenames-as-rules."""
    return {
        "kind": getattr(src.kind, "value", str(src.kind)),
        "canonical_game_path": (src.canonical_game_path or "").replace("/", "\\").lower(),
        "archive_path": (src.archive_path or "").replace("/", "\\").lower() or None,
        "archive_member": (src.archive_member or "").replace("/", "\\").lower() or None,
        "exists": bool(src.exists),
    }


def _canon_uv(expr) -> Any:
    if expr is None:
        return None
    if isinstance(expr, MeshUV):
        return {"op": "MeshUV", "index": int(expr.index)}
    if isinstance(expr, ScaleUV):
        return {
            "op": "ScaleUV",
            "scale": [_round_f(expr.scale[0]), _round_f(expr.scale[1])],
            "source": _canon_uv(expr.source),
        }
    if isinstance(expr, OffsetUV):
        return {
            "op": "OffsetUV",
            "offset": [_round_f(expr.offset[0]), _round_f(expr.offset[1])],
            "source": _canon_uv(expr.source),
        }
    if isinstance(expr, RotateUV):
        return {
            "op": "RotateUV",
            "degrees": _round_f(expr.degrees),
            "source": _canon_uv(expr.source),
        }
    if isinstance(expr, SelectUV):
        return {
            "op": "SelectUV",
            "condition": _canon_expr(expr.condition),
            "a": _canon_uv(expr.a),
            "b": _canon_uv(expr.b),
        }
    if isinstance(expr, GeneratedCoordinate):
        return {"op": "GeneratedCoordinate", "kind": expr.kind}
    if isinstance(expr, ComponentSwizzle):
        return {
            "op": "ComponentSwizzle",
            "components": list(expr.components),
            "source": _canon_uv(expr.source),
        }
    return {"op": type(expr).__name__}


def _canon_sampler(s) -> dict[str, Any]:
    return {
        "address_u": s.address_u,
        "address_v": s.address_v,
        "filter": s.filter,
    }


def _canon_sample(sample: TextureSampleExpression) -> dict[str, Any]:
    return {
        "binding_name_hash": int(sample.binding_name_hash) & 0xFFFFFFFF,
        "source": _canon_texture_source(sample.source),
        "uv": _canon_uv(sample.uv),
        "channels": list(sample.channels),
        "color_space": sample.color_space,
        "sampler": _canon_sampler(sample.sampler),
    }


def _canon_expr(expr) -> Any:
    if expr is None:
        return None
    if isinstance(expr, ConstantColor):
        return {
            "op": "ConstantColor",
            "rgba": [_round_f(c) for c in expr.rgba],
        }
    if isinstance(expr, ConstantScalar):
        return {"op": "ConstantScalar", "value": _round_f(expr.value)}
    if isinstance(expr, TextureSample):
        return {"op": "TextureSample", "sample": _canon_sample(expr.sample)}
    if isinstance(expr, Channel):
        return {
            "op": "Channel",
            "channel": expr.channel.lower(),
            "source": _canon_expr(expr.source),
        }
    if isinstance(expr, Multiply):
        return {"op": "Multiply", "a": _canon_expr(expr.a), "b": _canon_expr(expr.b)}
    if isinstance(expr, Add):
        return {"op": "Add", "a": _canon_expr(expr.a), "b": _canon_expr(expr.b)}
    if isinstance(expr, Subtract):
        return {"op": "Subtract", "a": _canon_expr(expr.a), "b": _canon_expr(expr.b)}
    if isinstance(expr, Mix):
        return {
            "op": "Mix",
            "a": _canon_expr(expr.a),
            "b": _canon_expr(expr.b),
            "factor": _canon_expr(expr.factor),
        }
    if isinstance(expr, Clamp):
        return {
            "op": "Clamp",
            "lo": _round_f(expr.lo),
            "hi": _round_f(expr.hi),
            "source": _canon_expr(expr.source),
        }
    if isinstance(expr, NormalDecode):
        return {
            "op": "NormalDecode",
            "strength": _round_f(expr.strength),
            "source": _canon_expr(expr.source),
        }
    if isinstance(expr, NormalBlend):
        return {
            "op": "NormalBlend",
            "base": _canon_expr(expr.base),
            "detail": _canon_expr(expr.detail),
        }
    if isinstance(expr, Select):
        return {
            "op": "Select",
            "condition": _canon_expr(expr.condition),
            "if_true": _canon_expr(expr.if_true),
            "if_false": _canon_expr(expr.if_false),
        }
    return {"op": type(expr).__name__}


def _canon_raster(rs: RasterState | None) -> dict[str, Any] | None:
    if rs is None:
        return None
    return {
        "blend_enable": rs.blend_enable,
        "cull_mode": rs.cull_mode,
        "depth_write": rs.depth_write,
    }


def canonicalize_effective_ir(ir: ForzaMaterialIR) -> dict[str, Any]:
    """Deterministic JSON-able dict of **active** IR outputs only.

    Omits: rejection diagnostics noise, provenance evidence strings, archive
    hint paths that are not byte identity, inactive/null outputs.
    Includes: contract identity + schema/contract versions.
    """
    if ir.rejection_reasons:
        raise ValueError(
            "cannot canonicalize rejected IR: " + "; ".join(ir.rejection_reasons)
        )
    sha = ir.shader.shaderbin_sha256
    return {
        "schema_version": EFFECTIVE_MATERIAL_SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION_BY_SHA256.get(sha, "unknown"),
        "shader": {
            "shader_name": ir.shader.shader_name,
            "shaderbin_sha256": sha,
            "permutation": ir.shader.permutation,
        },
        "base_color": _canon_expr(ir.base_color),
        "normal": _canon_expr(ir.normal),
        "roughness": _canon_expr(ir.roughness),
        "metallic": _canon_expr(ir.metallic),
        "ambient_occlusion": _canon_expr(ir.ambient_occlusion),
        "opacity": _canon_expr(ir.opacity),
        "shading_attenuation": (
            _canon_expr(ir.shading_attenuation.expression)
            if ir.shading_attenuation is not None
            else None
        ),
        "alpha_semantics": (
            ir.alpha_semantics.to_fingerprint_dict()
            if ir.alpha_semantics is not None
            and hasattr(ir.alpha_semantics, "to_fingerprint_dict")
            else None
        ),
        "blender_alpha_plan": (
            ir.blender_alpha_plan.to_fingerprint_dict()
            if ir.blender_alpha_plan is not None
            and hasattr(ir.blender_alpha_plan, "to_fingerprint_dict")
            else None
        ),
        "emissive": _canon_expr(ir.emissive),
        "clearcoat": _canon_expr(ir.clearcoat),
        "clearcoat_roughness": _canon_expr(ir.clearcoat_roughness),
        "raster_state": _canon_raster(ir.raster_state),
    }


def effective_material_fingerprint(ir: ForzaMaterialIR) -> str:
    """SHA-256 hex of canonical JSON (sorted keys, compact separators)."""
    canon = canonicalize_effective_ir(ir)
    payload = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_graph_plan_for_dedup(plan: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Strip non-effective plan fields before intra-group equality checks."""
    out: list[dict[str, Any]] = []
    for step in plan:
        s = dict(step)
        if s.get("op") == "material_meta":
            s.pop("name", None)
            # pipeline label may differ (forza-ir-v1) but does not affect nodes
            s.pop("pipeline", None)
        out.append(s)
    return out


@dataclass(frozen=True)
class SourceInstanceRef:
    """Recoverable source identity (never the share key)."""

    instance_key: str
    source_material_name: str
    car_id: str
    modelbin_game_path: str
    material_slot_index: int | None = None
    object_names: tuple[str, ...] = ()


@dataclass
class EffectiveMaterialGroup:
    fingerprint: str
    canonical_ir: dict[str, Any]
    sources: list[SourceInstanceRef] = field(default_factory=list)
    graph_plan_normalized: list[dict[str, Any]] | None = None
    graph_plan_consistent: bool = True
    inconsistency_notes: list[str] = field(default_factory=list)


def audit_group_graph_plans(
    group: EffectiveMaterialGroup,
    plans: list[tuple[dict[str, Any], ...]],
) -> EffectiveMaterialGroup:
    """Reject a share group if any deterministic graph plan field differs."""
    if not plans:
        group.graph_plan_consistent = False
        group.inconsistency_notes.append("no graph plans")
        return group
    norms = [normalize_graph_plan_for_dedup(p) for p in plans]
    group.graph_plan_normalized = norms[0]
    for i, n in enumerate(norms[1:], start=1):
        if n != norms[0]:
            group.graph_plan_consistent = False
            group.inconsistency_notes.append(
                f"graph_plan mismatch vs source[0] at member index {i}"
            )
    return group


@dataclass
class EffectiveMaterialShareCache:
    """Production bpy.types.Material cache — disabled until family approval.

    Retains source provenance mapping for recovery after sharing.
    """

    enabled_shaderbin_sha256: frozenset[str] = field(
        default_factory=lambda: PRODUCTION_SHARING_ENABLED_SHA256
    )
    # fingerprint -> blender material name (string only in non-bpy tests)
    materials_by_fingerprint: dict[str, Any] = field(default_factory=dict)
    # fingerprint -> list of source refs that share it
    sources_by_fingerprint: dict[str, list[SourceInstanceRef]] = field(
        default_factory=dict
    )

    def lookup(self, fingerprint: str):
        if not self.enabled_shaderbin_sha256:
            return None
        return self.materials_by_fingerprint.get(fingerprint)

    def store(
        self,
        *,
        fingerprint: str,
        shaderbin_sha256: str,
        material,
        source: SourceInstanceRef,
    ) -> None:
        if not production_sharing_enabled(shaderbin_sha256):
            raise RuntimeError(
                f"production sharing not enabled for sha={shaderbin_sha256!r}"
            )
        self.materials_by_fingerprint[fingerprint] = material
        self.sources_by_fingerprint.setdefault(fingerprint, []).append(source)

    def provenance_for(self, fingerprint: str) -> list[SourceInstanceRef]:
        return list(self.sources_by_fingerprint.get(fingerprint) or ())
