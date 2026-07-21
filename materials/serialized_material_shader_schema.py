"""SerializedMaterialShaderSchema — FTS-parity declaration layer.

This is the authoritative source for *declared* material/shader facts
(existence, types, defaults, bindings, scenarios, maps). DXIL must not be
required merely to discover that a resource is declared.

Architecture:
  SerializedMaterialShaderSchema  ← this module (from materialbin/shaderbin)
  StaticDxilSemantics             ← sample sites / arithmetic (separate)
  EvaluatedMaterialInstance       ← defaults + overrides + variants
  ForzaMaterialIR                 ← importer IR

Adapted field rules come from ForzaTools.Bundles (MIT, Copyright (c) 2023 Nenkai).
Viewport heuristics from ForzaTechStudio UI are excluded.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from ..parsing.binary import BinaryStream, Bundle, Tag, Version
from ..parsing.fts_mapping import parse_shader_parameter_mapping
from ..parsing.fts_schema_blobs import (
    collect_blob_metadata,
    parse_light_scenario_blob,
    parse_render_target_blob,
    parse_vars_blob,
    parse_vers_blob,
)
from .name_hashes import name_for_hash

_TYPE_NAMES = {
    0: "Vector4",
    1: "Color",
    2: "Float",
    3: "Bool",
    4: "Int",
    5: "Swizzle",
    6: "Texture2D",
    7: "Sampler",
    8: "ColorGradient",
    9: "FunctionRange",
    11: "Vector2",
    12: "Vector4_FH6",  # ForzaPort extension
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _param_row(h: int, p) -> dict[str, Any]:
    t = getattr(p, "type", None)
    row: dict[str, Any] = {
        "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
        "name": name_for_hash(h) or getattr(p, "name", None),
        "type": t,
        "type_name": _TYPE_NAMES.get(t, f"type_{t}"),
        "param_version": str(getattr(p, "param_version", "") or ""),
        "has_guid": getattr(p, "guid", None) is not None,
    }
    if t == 6:
        row["path"] = getattr(p, "path", "") or ""
        ph = getattr(p, "path_hash", None)
        row["path_hash"] = f"0x{ph & 0xFFFFFFFF:08X}" if ph is not None else None
    elif t == 7:
        row["address_u"] = getattr(p, "address_u", None)
        row["address_v"] = getattr(p, "address_v", None)
        row["filter_or_unk_type"] = getattr(p, "filter_or_unk_type", None)
    elif t == 8:
        row["gradient_stop_count"] = getattr(p, "gradient_stops", None)
        row["value"] = getattr(p, "value", None)
    elif t == 3:
        row["value"] = bool(getattr(p, "value", False))
    else:
        row["value"] = getattr(p, "value", None)
    return row


@dataclass
class SerializedMaterialShaderSchema:
    """Normalized declaration schema from FTS-parity parsers."""

    shader_name: str = ""
    shaderbin_sha256: str = ""
    source_attribution: str = (
        "ForzaTools.Bundles parsers (MIT/Nenkai) adapted into ForzaPort"
    )
    matl_paths: dict[str, Any] = field(default_factory=dict)
    txmp: dict[str, Any] = field(default_factory=dict)
    cbmp: dict[str, Any] = field(default_factory=dict)
    spmp: dict[str, Any] = field(default_factory=dict)
    shader_defaults: list[dict[str, Any]] = field(default_factory=list)
    mtpr_trailer: tuple[int, int, int] | None = None
    light_scenarios: dict[str, Any] | None = None
    render_targets: dict[str, Any] | None = None
    vers: dict[str, Any] | None = None
    vars: dict[str, Any] | None = None
    blob_metadata: dict[str, Any] = field(default_factory=dict)
    dxil_required_for: list[str] = field(
        default_factory=lambda: [
            "actual texture sample instructions",
            "sampled channels",
            "complete UV expression per sample",
            "branch and contribution predicates",
            "arithmetic / tint / mask composition",
            "discard / IgnoreHit / output alpha / MRT writes",
        ]
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "SerializedMaterialShaderSchema",
            "shader_name": self.shader_name,
            "shaderbin_sha256": self.shaderbin_sha256,
            "source_attribution": self.source_attribution,
            "matl_paths": self.matl_paths,
            "txmp": self.txmp,
            "cbmp": self.cbmp,
            "spmp": self.spmp,
            "shader_defaults": self.shader_defaults,
            "mtpr_trailer": (
                [f"0x{x & 0xFFFFFFFF:08X}" for x in self.mtpr_trailer]
                if self.mtpr_trailer
                else None
            ),
            "light_scenarios": self.light_scenarios,
            "render_targets": self.render_targets,
            "vers": self.vers,
            "vars": self.vars,
            "blob_metadata": self.blob_metadata,
            "dxil_required_for": self.dxil_required_for,
            "lifecycle_legend": [
                "DECLARED",
                "BOUND_BY_INSTANCE",
                "SAMPLED_IN_PASS",
                "ACTIVE_IN_BRANCH",
                "USED_IN_FINAL_EXPRESSION",
            ],
        }


def build_serialized_schema_from_bundle(
    bundle: Bundle, *, data: bytes | None = None, shader_name: str = ""
) -> SerializedMaterialShaderSchema:
    """Build schema from an already-deserialized shaderbin/materialbin Bundle."""
    from ..parsing.material import MaterialSystemObject

    schema = SerializedMaterialShaderSchema(shader_name=shader_name)
    if data is not None:
        schema.shaderbin_sha256 = _sha256(data)

    # MATL paths if present on this bundle
    matl = bundle.blobs[Tag.MATL]
    if matl:
        b = matl[0]
        b.stream.seek(0)
        paths = {"path": b.stream.read_7bit_string() or ""}
        ver = b.version or Version()
        if ver.is_at_least(1, 1):
            paths["path_v1_1"] = b.stream.read_7bit_string() or ""
        if ver.is_at_least(1, 2):
            paths["path_v1_2"] = b.stream.read_7bit_string() or ""
        paths["version"] = str(ver)
        schema.matl_paths = paths

    mso = MaterialSystemObject()
    mso.shader_name = shader_name
    mso._load_shader_maps(bundle)
    if mso.txmp_mapping:
        schema.txmp = mso.txmp_mapping.to_dict()
    if mso.cbmp_mapping:
        schema.cbmp = mso.cbmp_mapping.to_dict()
    if mso.spmp_mapping:
        schema.spmp = mso.spmp_mapping.to_dict()

    blobs = bundle.blobs[Tag.DFPR] or bundle.blobs[Tag.MTPR]
    if blobs:
        mso.parameters_local.clear()
        mso._ingest_parameter_blob(
            blobs[0], into=mso.parameters_local, mark_overrides=False
        )
        mso._rebuild_merged()
        schema.mtpr_trailer = mso.mtpr_trailer
        for h, p in sorted(mso.parameters_local.items(), key=lambda kv: kv[0] & 0xFFFFFFFF):
            row = _param_row(h, p)
            row["provenance"] = "SHADER_DEFAULT"
            row["lifecycle"] = "DECLARED"
            schema.shader_defaults.append(row)

    lsce = bundle.blobs[Tag.LSCE] or bundle.blobs[Tag.DBLS]
    if lsce:
        try:
            parsed = parse_light_scenario_blob(lsce[0])
            if parsed:
                d = parsed.to_dict()
                d["ok"] = True
                schema.light_scenarios = d
        except Exception as exc:
            schema.light_scenarios = {
                "parse_error": str(exc),
                "scenario_count": None,
                "ok": False,
                "parity_status": "UNRESOLVED",
                "note": (
                    "LSCE parse failed — asset must not be reported as fully passed "
                    "for LSCE. Other schema fields may still be valid."
                ),
            }

    trgt = bundle.blobs[Tag.TRGT]
    if trgt:
        parsed = parse_render_target_blob(trgt[0])
        if parsed:
            schema.render_targets = parsed.to_dict()

    vers = bundle.blobs[Tag.VERS]
    if vers:
        parsed = parse_vers_blob(vers[0])
        if parsed:
            schema.vers = parsed.to_dict()

    vars_b = bundle.blobs[Tag.VARS]
    if vars_b:
        parsed = parse_vars_blob(vars_b[0])
        if parsed:
            schema.vars = parsed.to_dict()

    # Metadata on DFPR/MTPR/LSCE/TRGT blobs
    meta_out: dict[str, Any] = {}
    for tag_name, tag in (
        ("dfpr", Tag.DFPR),
        ("mtpr", Tag.MTPR),
        ("lsce", Tag.LSCE),
        ("trgt", Tag.TRGT),
        ("mati", Tag.MATI),
        ("matl", Tag.MATL),
    ):
        blist = bundle.blobs[tag]
        if blist:
            collected = collect_blob_metadata(blist[0])
            if collected:
                meta_out[tag_name] = collected
    schema.blob_metadata = meta_out
    return schema


def build_serialized_schema_from_bytes(
    data: bytes, *, shader_name: str = ""
) -> SerializedMaterialShaderSchema:
    stream = BinaryStream(memoryview(data))
    bundle = Bundle()
    bundle.deserialize(stream)
    return build_serialized_schema_from_bundle(
        bundle, data=data, shader_name=shader_name
    )


@dataclass
class EvaluatedMaterialInstance:
    """Resolved defaults + instance overrides (declaration layer only)."""

    schema: SerializedMaterialShaderSchema
    instance_parameters: list[dict[str, Any]] = field(default_factory=list)
    override_hashes: list[str] = field(default_factory=list)
    parent_material_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": "EvaluatedMaterialInstance",
            "parent_material_path": self.parent_material_path,
            "override_hashes": self.override_hashes,
            "instance_parameters": self.instance_parameters,
            "serialized_schema": self.schema.to_dict(),
        }


def evaluate_material_instance(material) -> EvaluatedMaterialInstance:
    """Build EvaluatedMaterialInstance from a parsed MaterialSystemObject."""
    schema = SerializedMaterialShaderSchema(
        shader_name=getattr(material, "shader_name", "") or ""
    )
    if getattr(material, "txmp_mapping", None):
        schema.txmp = material.txmp_mapping.to_dict()
    elif getattr(material, "txmp", None):
        schema.txmp = {
            "entries": [
                {
                    "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
                    "effective_byte_offset": int(v),
                }
                for h, v in sorted(material.txmp.items(), key=lambda kv: int(kv[1]))
            ]
        }
    if getattr(material, "cbmp_mapping", None):
        schema.cbmp = material.cbmp_mapping.to_dict()
    if getattr(material, "spmp_mapping", None):
        schema.spmp = material.spmp_mapping.to_dict()
    schema.mtpr_trailer = getattr(material, "mtpr_trailer", None)
    schema.matl_paths = {
        "path": getattr(material, "parent_material_path", None),
        "path_v1_1": getattr(material, "parent_path_v1_1", None),
        "path_v1_2": getattr(material, "parent_path_v1_2", None),
    }
    for h, p in sorted(
        (getattr(material, "parameters_local", None) or {}).items(),
        key=lambda kv: kv[0] & 0xFFFFFFFF,
    ):
        row = _param_row(h, p)
        row["provenance"] = "SHADER_DEFAULT"
        row["lifecycle"] = "DECLARED"
        schema.shader_defaults.append(row)

    inst_rows = []
    for h, p in sorted(
        (getattr(material, "parameters_instance", None) or {}).items(),
        key=lambda kv: kv[0] & 0xFFFFFFFF,
    ):
        row = _param_row(h, p)
        row["provenance"] = "INSTANCE_OVERRIDE"
        row["lifecycle"] = "BOUND_BY_INSTANCE"
        inst_rows.append(row)

    return EvaluatedMaterialInstance(
        schema=schema,
        instance_parameters=inst_rows,
        override_hashes=[
            f"0x{h & 0xFFFFFFFF:08X}"
            for h in sorted(
                getattr(material, "override_hashes", None) or set(),
                key=lambda x: x & 0xFFFFFFFF,
            )
        ],
        parent_material_path=getattr(material, "parent_material_path", None),
    )
