"""Transitional representations still present in the material pipeline.

Authoritative production path for contracted SHAs:
  MaterialEvaluationContext → evaluated sample sites → ForzaMaterialIR → Blender

Inventory only — not a roadmap.
"""

from __future__ import annotations

# Each entry: purpose, remaining callers, removal condition.
TRANSITIONAL_INVENTORY = (
    {
        "name": "CleanSurfaceCapability",
        "purpose": "Derived capability view for resolve/diagnostics/legacy nodes",
        "remaining_callers": "resolver.py, nodes_v3 legacy, diagnose.py, eval_* assembly",
        "removal_condition": "No consumer needs capability; diagnose uses site diagnostics",
    },
    {
        "name": "ResolvedTextureSlot",
        "purpose": "Slot tiling/path view derived from evaluated sites",
        "remaining_callers": "sample_site_slots.py, site_role_map, nodes_v3, UV tests (derive_slots)",
        "removal_condition": "No consumer needs slot.tiling/path views",
    },
    {
        "name": "MaterialSpec",
        "purpose": "Adapter over ResolvedMaterial for older pipeline call sites",
        "remaining_callers": "pipeline_v3.py, diagnose.py, some importer caches",
        "removal_condition": "Importer stores only ResolvedMaterial + context",
    },
    {
        "name": "AUTH_EVALUATED_SAMPLE_SITES",
        "purpose": "Legacy alias of EVALUATED_SAMPLE_SITES during cutover",
        "remaining_callers": "route_model.py, resolver auth_model checks, tests",
        "removal_condition": "All contracts emit FULL_SAMPLE_SITE_IR only; alias unused",
    },
    {
        "name": "PassMergeSpec / blender_import_merge_specs",
        "purpose": "Deprecated register-merge adapter for non-primary sites",
        "remaining_callers": "pass_contracts.py adapters, older completeness tests",
        "removal_condition": "No production path calls merge specs for contracted SHAs",
    },
    {
        "name": "ForzaMaterialIR.opacity",
        "purpose": "Dual field; prefer alpha_semantics.main_visibility",
        "remaining_callers": "eval_car_standard.py, eval_clean_surface_ir.py, ir_compiler.py",
        "removal_condition": "All consumers use alpha_semantics.main_visibility",
    },
)

TRANSITIONAL_CAPABILITY_VIEWS = (
    "CleanSurfaceCapability",
    "ResolvedTextureSlot",
    "MaterialSpec",
    "TextureSlot",
)

TRANSITIONAL_BINDING_PATHS = (
    "PRIMARY_PASS_TEXTURE_BINDINGS",
    "PARTIAL_SAMPLE_SITE_WITH_BINDING_SEMANTICS",
    "AUTH_EVALUATED_SAMPLE_SITES",
    "PassMergeSpec / blender_import_merge_specs",
)

TRANSITIONAL_ALPHA_FIELDS = (
    "ForzaMaterialIR.opacity",
)

REMOVED = (
    "ShaderInstanceEvaluator",  # deleted — no production consumers
)

REMOVED_FROM_PRODUCTION = (
    "nodes_v3._IR_CONTRACT_SHADERS name dispatch",
    "has_ir_evaluator(shader_name-only approval)",
    "ShaderInstanceEvaluator",
)

QUARANTINED_IMPORT_BLOCKLIST = frozenset(
    {
        "shader_instance_evaluator",
        "ShaderInstanceEvaluator",
    }
)
