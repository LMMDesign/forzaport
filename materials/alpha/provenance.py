"""AlphaTransparency parameter provenance from live MatI deserialize."""

from __future__ import annotations

from typing import Any

from ...parsing.material import ShaderParameterName as SPN
from . import (
    ALPHA_TRANSPARENCY_NAMEHASH,
    AlphaTransparencyProvenance,
)


def classify_alpha_transparency_provenance(mat) -> dict[str, Any]:
    """Return provenance for AlphaTransparencyBool on a MaterialSystemObject.

    Uses parameters_instance / override_hashes / parameters_local — not catalog.
    """
    h = int(SPN.AlphaTransparencyBool)
    assert h == ALPHA_TRANSPARENCY_NAMEHASH

    inst = getattr(mat, "parameters_instance", {}) or {}
    local = getattr(mat, "parameters_local", {}) or {}
    merged = getattr(mat, "parameters", {}) or {}
    overrides = getattr(mat, "override_hashes", set()) or set()
    parent_path = (getattr(mat, "parent_material_path", None) or "") or ""
    parent_lower = parent_path.lower().replace("/", "\\")

    present_merged = h in merged
    raw = merged.get(h)
    decoded: bool | None = None
    raw_type = None
    raw_value = None
    if raw is not None:
        raw_type = getattr(raw, "type", None)
        raw_value = getattr(raw, "value", None)
        if isinstance(raw_value, bool):
            decoded = raw_value
        elif raw_value is not None:
            decoded = bool(raw_value)

    if h in inst or h in overrides:
        provenance = AlphaTransparencyProvenance.MATI_EXPLICIT
    elif present_merged and h in local:
        if parent_lower.endswith(".shaderbin"):
            provenance = AlphaTransparencyProvenance.SHADER_DEFAULT
        elif parent_lower.endswith(".materialbin") or "material" in parent_lower:
            provenance = AlphaTransparencyProvenance.MATERIAL_TEMPLATE_INHERITED
        else:
            # Local-only without clear parent kind — still not instance-explicit.
            provenance = (
                AlphaTransparencyProvenance.SHADER_DEFAULT
                if not parent_path
                else AlphaTransparencyProvenance.MATERIAL_TEMPLATE_INHERITED
            )
    elif present_merged:
        # In merged but not local/instance (unexpected) — fail closed.
        provenance = AlphaTransparencyProvenance.UNRESOLVED
    else:
        provenance = AlphaTransparencyProvenance.UNRESOLVED

    return {
        "parameter_name": "AlphaTransparencyBool",
        "parameter_name_hash": h,
        "parameter_name_hash_hex": hex(h),
        "present_in_merged": present_merged,
        "present_in_instance": h in inst,
        "present_in_local": h in local,
        "in_override_hashes": h in overrides,
        "raw_type": raw_type,
        "raw_value": raw_value,
        "decoded_boolean": decoded,
        "provenance": provenance.value,
        "parent_path": parent_path or None,
        "cb_register": 32,
        "cb_component": "y",
        "do_not_treat_absent_as_false": provenance
        == AlphaTransparencyProvenance.UNRESOLVED
        and not present_merged,
    }
