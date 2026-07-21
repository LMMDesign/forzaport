"""Per-parameter MatI provenance categories for all material instances."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ParameterProvenanceCategory(str, Enum):
    MATI_EXPLICIT = "MATI_EXPLICIT"
    PARENT_MATERIAL_INHERITED = "PARENT_MATERIAL_INHERITED"
    TEMPLATE_INHERITED = "TEMPLATE_INHERITED"
    SHADER_DEFAULT = "SHADER_DEFAULT"
    CONTRACT_CONSTANT = "CONTRACT_CONSTANT"
    UNRESOLVED = "UNRESOLVED"


DECLARATION_STATUS = (
    "NOT_DECLARED",
    "DECLARED_BUT_UNRESOLVED",
    "PROVEN_FROM_GAME_FILES",
)


def _norm_parent(path: str | None) -> str:
    return (path or "").lower().replace("/", "\\")


def classify_parameter_provenance(
    *,
    name_hash: int,
    material: Any,
    contract_constant: bool = False,
) -> dict[str, Any]:
    """Classify one parameter's final effective value provenance.

    Categories follow the Shader Pass Completeness v4 contract — not merely
    ``in_local`` / ``in_instance`` booleans.
    """
    h = int(name_hash) & 0xFFFFFFFF
    inst = getattr(material, "parameters_instance", None) or {}
    local = getattr(material, "parameters_local", None) or {}
    merged = getattr(material, "parameters", None) or {}
    overrides = getattr(material, "override_hashes", None) or set()
    parent_path = getattr(material, "parent_material_path", None) or ""
    parent_chain = list(getattr(material, "parent_chain", None) or [])
    template_path = getattr(material, "template_material_path", None) or ""
    shaderbin_sha = getattr(material, "shaderbin_sha256", None) or ""
    source_mati = getattr(material, "source_mati_path", None) or getattr(
        material, "path", None
    )

    def _get(d: dict, key: int):
        return d.get(key) or d.get(key & 0xFFFFFFFF) or d.get(h)

    def _has(d: dict, key: int) -> bool:
        return key in d or (key & 0xFFFFFFFF) in d or h in {x & 0xFFFFFFFF for x in d}

    p = _get(merged, h)
    in_inst = _has(inst, h) or h in {x & 0xFFFFFFFF for x in overrides}
    in_local = _has(local, h)
    parent_l = _norm_parent(parent_path)
    template_l = _norm_parent(template_path)

    if contract_constant:
        category = ParameterProvenanceCategory.CONTRACT_CONSTANT
    elif in_inst:
        category = ParameterProvenanceCategory.MATI_EXPLICIT
    elif in_local and parent_l.endswith(".shaderbin"):
        category = ParameterProvenanceCategory.SHADER_DEFAULT
    elif in_local and (
        template_l.endswith(".materialbin")
        or "template" in template_l
        or parent_l.endswith(".materialbin")
    ):
        # Distinguishes parent materialbin vs named template when available.
        if template_path and template_l != parent_l:
            category = ParameterProvenanceCategory.TEMPLATE_INHERITED
        elif parent_l.endswith(".materialbin"):
            category = ParameterProvenanceCategory.PARENT_MATERIAL_INHERITED
        else:
            category = ParameterProvenanceCategory.TEMPLATE_INHERITED
    elif in_local:
        category = ParameterProvenanceCategory.SHADER_DEFAULT
    elif p is not None:
        category = ParameterProvenanceCategory.UNRESOLVED
    else:
        category = ParameterProvenanceCategory.UNRESOLVED

    cb_reg = getattr(p, "cb_register", None) if p is not None else None
    cb_comp = getattr(p, "cb_component", None) if p is not None else None
    ptype = getattr(p, "type", None) if p is not None else None
    value = getattr(p, "value", None) if p is not None else None

    conflicts: list[str] = []
    if in_inst and in_local:
        # Instance wins; record both for audit.
        pass
    if category == ParameterProvenanceCategory.UNRESOLVED and p is not None and not in_local and not in_inst:
        conflicts.append("merged_without_local_or_instance")

    from .name_hashes import name_for_hash  # local import — avoids cycle at module load

    return {
        "name_hash": f"0x{h:08X}",
        "name": name_for_hash(h),
        "type": ptype,
        "value": value,
        "cb_register": cb_reg,
        "cb_component": cb_comp,
        "category": category.value,
        "source_mati_path": source_mati,
        "parent_material_path": parent_path or None,
        "parent_chain": parent_chain,
        "template_material_path": template_path or None,
        "shaderbin_sha256": shaderbin_sha or None,
        "in_instance": bool(in_inst),
        "in_local": bool(in_local),
        "in_merged": p is not None,
        "unresolved_conflicts": conflicts,
        "declaration_status": (
            "PROVEN_FROM_GAME_FILES"
            if p is not None
            else ("DECLARED_BUT_UNRESOLVED" if in_local or in_inst else "NOT_DECLARED")
        ),
    }


def material_content_key(
    *,
    shaderbin_sha256: str | None,
    source_mati_path: str | None,
    parameter_fingerprint: str | None = None,
) -> str:
    """Content identity — may repeat across corpus occurrences."""
    return "|".join(
        [
            (shaderbin_sha256 or "")[:64],
            (source_mati_path or "").replace("\\", "/").lower(),
            parameter_fingerprint or "",
        ]
    )


def corpus_occurrence_key(
    *,
    game: str = "fh6",
    vehicle_or_archive: str | None = None,
    source_catalog: str | None = None,
    source_mati_path: str | None = None,
    mesh_or_object: str | None = None,
    material_slot: str | None = None,
    occurrence_index: int = 0,
) -> str:
    """Unique corpus occurrence identity — must be unique across 2317 rows."""
    return "|".join(
        [
            game or "fh6",
            (vehicle_or_archive or "").replace("\\", "/").lower(),
            (source_catalog or "").replace("\\", "/").lower(),
            (source_mati_path or "").replace("\\", "/").lower(),
            (mesh_or_object or "").replace("\\", "/").lower(),
            (material_slot or "").replace("\\", "/").lower(),
            str(int(occurrence_index)),
        ]
    )


def dump_instance_parameter_provenance(material: Any, **occurrence_ctx: Any) -> dict[str, Any]:
    """Emit provenance rows for every parameter on one MatI instance."""
    inst = getattr(material, "parameters_instance", None) or {}
    local = getattr(material, "parameters_local", None) or {}
    merged = getattr(material, "parameters", None) or {}
    hashes = sorted(
        {int(h) & 0xFFFFFFFF for h in (set(inst) | set(local) | set(merged))},
    )
    rows = [classify_parameter_provenance(name_hash=h, material=material) for h in hashes]
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    source_mati = getattr(material, "source_mati_path", None) or getattr(
        material, "path", None
    )
    sha = getattr(material, "shaderbin_sha256", None)
    content = material_content_key(
        shaderbin_sha256=sha,
        source_mati_path=source_mati,
    )
    occurrence = corpus_occurrence_key(
        game=str(occurrence_ctx.get("game") or "fh6"),
        vehicle_or_archive=occurrence_ctx.get("vehicle_or_archive"),
        source_catalog=occurrence_ctx.get("source_catalog"),
        source_mati_path=source_mati,
        mesh_or_object=occurrence_ctx.get("mesh_or_object"),
        material_slot=occurrence_ctx.get("material_slot")
        or getattr(material, "name", None),
        occurrence_index=int(occurrence_ctx.get("occurrence_index") or 0),
    )
    return {
        "shader_name": getattr(material, "shader_name", None),
        "source_mati_path": source_mati,
        "parent_material_path": getattr(material, "parent_material_path", None),
        "shaderbin_sha256": sha,
        "material_content_key": content,
        "corpus_occurrence_key": occurrence,
        "parameter_count": len(rows),
        "by_category": by_cat,
        "parameters": rows,
    }
