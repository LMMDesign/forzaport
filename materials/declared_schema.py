"""Dump declared MatI / shaderbin schema (existence ≠ DXIL sampling)."""

from __future__ import annotations

import hashlib
import os
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

from ..parsing.binary import BinaryStream, Bundle, Tag
from ..parsing.material import parse_register_map
from .name_hashes import name_for_hash

_TYPE_NAMES = {
    0: "float",
    1: "float4_color",
    2: "float_scalar",
    3: "bool",
    6: "texture_path",
    7: "sampler",
    11: "float2",
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _param_summary(p) -> dict[str, Any]:
    t = getattr(p, "type", None)
    row: dict[str, Any] = {
        "type": t,
        "type_name": _TYPE_NAMES.get(t, f"type_{t}"),
        "name": getattr(p, "name", None),
    }
    if t == 6:
        row["path"] = getattr(p, "path", "") or ""
    elif t == 3:
        row["value"] = bool(getattr(p, "value", False))
    elif t in (0, 1, 2, 11):
        row["value"] = getattr(p, "value", None)
    return row


def parse_shaderbin_xml(xml_bytes: bytes) -> dict[str, Any]:
    """Parse companion ``*.shaderbin.xml`` variant / texture / parameter export."""
    root = ET.fromstring(xml_bytes)
    variant_properties = []
    for vp in root.findall("./VariantProperties/VariantProperty"):
        variant_properties.append(
            {
                "name": vp.get("name"),
                "shortname": vp.get("shortname"),
                "parametername": vp.get("parametername"),
            }
        )
    variant_options = []
    for opt in root.findall("./VariantOptions/ExportVariantOption"):
        variants = [
            {"variant": v.get("variant"), "switch": v.get("switch")}
            for v in opt.findall("./Variant")
        ]
        variant_options.append(
            {
                "name": opt.get("name"),
                "exposed": opt.get("exposed"),
                "variants": variants,
            }
        )
    textures = []
    for tex in root.findall("./Textures/Texture"):
        textures.append(
            {
                "parametername": tex.get("parametername"),
                "referenced": tex.get("referenced"),
            }
        )
    parameters = []
    for p in root.findall("./Parameters/Parameter"):
        parameters.append(
            {
                "parametername": p.get("parametername"),
                "size": p.get("size"),
                "referenced": p.get("referenced"),
            }
        )
    vertex_usage_by_variant: list[dict[str, Any]] = []
    for ev in root.findall("./Variants/ExportVariant"):
        variant_name = ev.get("name") or ""
        for vi in ev.findall("./VertexInputUsage"):
            vertex_usage_by_variant.append(
                {
                    "export_variant": variant_name,
                    "scenarios": dict(vi.attrib),
                    "declaration_status": "PROVEN_FROM_GAME_FILES",
                }
            )
    # Backward-compat flat map: first variant only, marked provisional if multiple.
    vertex_usage = {}
    if vertex_usage_by_variant:
        vertex_usage = dict(vertex_usage_by_variant[0].get("scenarios") or {})
    tex_el = root.find("./Textures")
    return {
        "variant_properties": variant_properties,
        "variant_options": variant_options,
        "export_textures": textures,
        "export_parameters": parameters,
        "vertex_input_usage_by_scenario": vertex_usage,
        "vertex_input_usage_by_export_variant": vertex_usage_by_variant,
        "vertex_input_usage_status": (
            "PROVEN_FROM_GAME_FILES"
            if vertex_usage_by_variant
            else "NOT_DECLARED"
        ),
        "textures_count_attr": tex_el.get("count") if tex_el is not None else None,
        "textures_declaration_status": (
            "PROVEN_FROM_GAME_FILES"
            if tex_el is not None
            else "NOT_DECLARED"
        ),
    }


def dump_shaderbin_bytes(data: bytes, *, shader_name: str = "") -> dict[str, Any]:
    """Declared schema from a .shaderbin blob."""
    stream = BinaryStream(memoryview(data))
    bundle = Bundle()
    bundle.deserialize(stream)
    txmp = parse_register_map(bundle.blobs[Tag.TXMP][0]) if bundle.blobs[Tag.TXMP] else {}
    cbmp = parse_register_map(bundle.blobs[Tag.CBMP][0]) if bundle.blobs[Tag.CBMP] else {}
    spmp = parse_register_map(bundle.blobs[Tag.SPMP][0]) if bundle.blobs[Tag.SPMP] else {}

    # Load defaults via temporary MaterialSystemObject pattern
    from ..parsing.material import MaterialSystemObject

    mso = MaterialSystemObject()
    mso.shader_name = shader_name
    mso._load_shader_maps(bundle)
    blobs = bundle.blobs[Tag.DFPR] or bundle.blobs[Tag.MTPR]
    if blobs:
        mso.parameters_local.clear()
        mso._ingest_parameter_blob(
            blobs[0], into=mso.parameters_local, mark_overrides=False
        )
        mso._rebuild_merged()

    declared_txmp = []
    for h, reg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
        declared_txmp.append(
            {
                "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
                "name": name_for_hash(h),
                "texture_register": int(reg),
                "lifecycle": "DECLARED",
            }
        )
    declared_cbmp = [
        {
            "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
            "name": name_for_hash(h),
            "byte_offset": int(off),
            "cb_row": int(off) // 16,
            "cb_component": (int(off) % 16) // 4,
            "lifecycle": "DECLARED",
        }
        for h, off in sorted(cbmp.items(), key=lambda kv: int(kv[1]))
    ]
    declared_spmp = [
        {
            "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
            "name": name_for_hash(h),
            "sampler_register": int(reg),
            "lifecycle": "DECLARED",
        }
        for h, reg in sorted(spmp.items(), key=lambda kv: int(kv[1]))
    ]
    defaults = []
    for h, p in sorted(mso.parameters_local.items(), key=lambda kv: kv[0] & 0xFFFFFFFF):
        row = _param_summary(p)
        row["name_hash"] = f"0x{h & 0xFFFFFFFF:08X}"
        row["name"] = name_for_hash(h) or row.get("name")
        row["provenance"] = "SHADER_DEFAULT"
        row["lifecycle"] = "DECLARED"
        defaults.append(row)

    return {
        "shader_name": shader_name,
        "shaderbin_sha256": _sha256(data),
        "declared_txmp": declared_txmp,
        "declared_cbmp": declared_cbmp,
        "declared_spmp": declared_spmp,
        "shader_defaults": defaults,
        "lifecycle_legend": [
            "DECLARED",
            "BOUND_BY_INSTANCE",
            "SAMPLED_IN_PASS",
            "ACTIVE_IN_BRANCH",
            "USED_IN_FINAL_EXPRESSION",
        ],
    }


def dump_shader_archive(archive_path: str, shader_name: str) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path, "r") as zf:
        want = f"{shader_name}.shaderbin".lower()
        sb_member = None
        xml_member = None
        for n in zf.namelist():
            base = os.path.basename(n.replace("\\", "/")).lower()
            if base == want:
                sb_member = n
            if base == f"{shader_name}.shaderbin.xml".lower():
                xml_member = n
        if sb_member is None:
            raise FileNotFoundError(f"no shaderbin in {archive_path}")
        schema = dump_shaderbin_bytes(zf.read(sb_member), shader_name=shader_name)
        schema["archive"] = archive_path.replace("\\", "/")
        schema["shaderbin_member"] = sb_member.replace("\\", "/")
        if xml_member:
            schema["shaderbin_xml"] = parse_shaderbin_xml(zf.read(xml_member))
            schema["shaderbin_xml_member"] = xml_member.replace("\\", "/")
        else:
            schema["shaderbin_xml"] = None
        return schema


def dump_material_instance(material) -> dict[str, Any]:
    """Declared vs instance-bound snapshot for one MatI object.

    Includes full provenance categories (not only in_local / in_instance).
    """
    from .mati_parameter_provenance import dump_instance_parameter_provenance

    params = getattr(material, "parameters", None) or {}
    local = getattr(material, "parameters_local", None) or {}
    inst = getattr(material, "parameters_instance", None) or {}
    txmp = getattr(material, "txmp", None) or {}
    provenance = dump_instance_parameter_provenance(material)
    rows = []
    for h in sorted(set(params) | set(local) | set(inst), key=lambda x: x & 0xFFFFFFFF):
        name = name_for_hash(h)
        in_local = h in local or (h & 0xFFFFFFFF) in {x & 0xFFFFFFFF for x in local}
        in_inst = h in inst or (h & 0xFFFFFFFF) in {x & 0xFFFFFFFF for x in inst}
        p = params.get(h) or params.get(h & 0xFFFFFFFF)
        lifecycle = "DECLARED"
        if in_inst:
            lifecycle = "BOUND_BY_INSTANCE"
        elif in_local:
            lifecycle = "DECLARED"
        row = {
            "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
            "name": name,
            "lifecycle": lifecycle,
            "in_shader_defaults": in_local,
            "in_instance": in_inst,
            "missing_from_merged": p is None,
        }
        if p is not None:
            row.update(_param_summary(p))
        # Attach authoritative provenance category.
        for pr in provenance.get("parameters") or []:
            if pr.get("name_hash") == row["name_hash"]:
                row["provenance_category"] = pr.get("category")
                row["declaration_status"] = pr.get("declaration_status")
                row["unresolved_conflicts"] = pr.get("unresolved_conflicts")
                break
        rows.append(row)
    bindings = []
    for h, reg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
        p = params.get(h) or params.get(h & 0xFFFFFFFF)
        path = getattr(p, "path", "") if p is not None else ""
        bindings.append(
            {
                "name_hash": f"0x{h & 0xFFFFFFFF:08X}",
                "name": name_for_hash(h),
                "texture_register": int(reg),
                "path": path or "",
                "lifecycle": "BOUND_BY_INSTANCE" if path else "DECLARED",
            }
        )
    return {
        "shader_name": getattr(material, "shader_name", None),
        "parent_material_path": getattr(material, "parent_material_path", None),
        "parameters": rows,
        "texture_bindings": bindings,
        "parameter_provenance": provenance,
    }
