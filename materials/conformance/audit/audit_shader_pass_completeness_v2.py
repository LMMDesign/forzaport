"""Shader Pass Completeness — full inventory, schema dump, sample sites, contracts.

Usage (repo root):
  python tools/material_conformance/audit_shader_pass_completeness_v2.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
import zipfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path = [p for p in sys.path if "io_import_forza_carbin" not in p.replace("\\", "/")]

from tools.material_conformance.common.run_context import ConformanceRun
from tools.material_conformance.common.workspace_paths import (
    ADDON_ROOT,
    ensure_addon_on_sys_path,
)

ensure_addon_on_sys_path()
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [str(ADDON_ROOT)]
sys.modules["io_import_forza_carbin"] = _pkg

from io_import_forza_carbin.materials.declared_schema import (  # noqa: E402
    dump_shader_archive,
)
from io_import_forza_carbin.materials.dxil_sample_sites import (  # noqa: E402
    extract_sample_sites,
    register_summary,
)
from io_import_forza_carbin.materials.serialized_material_shader_schema import (  # noqa: E402
    build_serialized_schema_from_bytes,
)
from io_import_forza_carbin.parsing.material import MaterialSystemObject  # noqa: E402
from io_import_forza_carbin.parsing.binary import BinaryStream  # noqa: E402
from io_import_forza_carbin.materials.pass_contracts import (  # noqa: E402
    list_contracted_shas,
    load_shader_pass_contract,
)
from io_import_forza_carbin.materials.pass_identity import (  # noqa: E402
    classify_blender_relevance,
    parse_pass_identity,
    scenario_from_member,
    variant_from_member,
)
from io_import_forza_carbin.materials.shader_bindings import (  # noqa: E402
    _addon_dxc,
    _disasm,
)
from io_import_forza_carbin.materials.uv.uv_choice_contracts import (  # noqa: E402
    resolve_uv_choice_texcoord,
)

MEDIA = r"C:/XboxGames/Forza Horizon 6/Content/media"
CATALOG = (
    ROOT
    / "reports/material-conformance/runs/legacy-import_milestone-a/data"
    / "material_shader_family_catalog.json"
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_diff(run: ConformanceRun) -> None:
    addon = ADDON_ROOT
    try:
        diff = subprocess.check_output(
            ["git", "-C", str(addon), "diff", "--", "materials/", "README.md", "tests/"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        diff = f"(diff unavailable: {exc})\n"
    run.write_text("diffs/shader_pass_completeness_source.diff", diff)


def _corpus_families(catalog: dict) -> list[dict]:
    """One row per family name — include NO_SHA families."""
    by_name: dict[str, dict] = {}
    for inst in catalog.get("instances") or []:
        name = inst.get("shader_name") or "UNKNOWN"
        row = by_name.setdefault(
            name,
            {
                "shader_name": name,
                "shaderbin_sha256": inst.get("shaderbin_sha256"),
                "shader_archive_path": inst.get("shader_archive_path"),
                "instance_count": 0,
                "status_counts": defaultdict(int),
            },
        )
        row["instance_count"] += 1
        st = inst.get("status") or "UNKNOWN"
        row["status_counts"][st] += 1
        if not row.get("shaderbin_sha256") and inst.get("shaderbin_sha256"):
            row["shaderbin_sha256"] = inst["shaderbin_sha256"]
            row["shader_archive_path"] = inst.get("shader_archive_path")
    # ranking may list families not in instance loop edge cases
    for fam in catalog.get("shader_family_ranking") or []:
        name = fam.get("shader_name")
        if name and name not in by_name:
            by_name[name] = {
                "shader_name": name,
                "shaderbin_sha256": fam.get("shaderbin_sha256"),
                "shader_archive_path": fam.get("archive"),
                "instance_count": fam.get("instance_count") or 0,
                "status_counts": defaultdict(int, fam.get("status_counts") or {}),
            }
    out = []
    for name, row in sorted(by_name.items()):
        row["status_counts"] = dict(row["status_counts"])
        if not row.get("shaderbin_sha256"):
            row["classification"] = "NO_SHADER_ARCHIVE_OR_SHA"
            row["capability"] = "UNRESOLVED_CAPABILITY"
        else:
            row["classification"] = "EXACT_SHA_AVAILABLE"
            row["capability"] = None
        out.append(row)
    return out


def _cbmp_from_shaderbin(sb_bytes: bytes) -> dict[int, int]:
    """Build hash→cbuffer-offset map from serialized CBMP (FTS-parity)."""
    if not sb_bytes:
        return {}
    try:
        mso = MaterialSystemObject()
        mso.deserialize(BinaryStream(memoryview(sb_bytes)))
        if mso.cbmp:
            return {int(k) & 0xFFFFFFFF: int(v) for k, v in mso.cbmp.items()}
    except Exception:
        pass
    try:
        schema = build_serialized_schema_from_bytes(sb_bytes)
        entries = (schema.cbmp or {}).get("entries") or []
        out: dict[int, int] = {}
        for e in entries:
            nh = e.get("name_hash")
            if not nh:
                continue
            h = int(str(nh), 16) if isinstance(nh, str) else int(nh)
            out[h & 0xFFFFFFFF] = int(
                e.get("effective_byte_offset") or e.get("id_or_offset") or 0
            )
        return out
    except Exception:
        return {}


def _analyze_all_psos(
    *,
    shader_name: str,
    sha: str,
    archive: str,
    dxc: str,
) -> dict:
    result = {
        "shader_name": shader_name,
        "shaderbin_sha256": sha,
        "archive": archive,
        "pso_members": [],
        "sample_sites": [],
        "variants": set(),
        "errors": [],
        "declared_schema": None,
    }
    if not archive or not os.path.isfile(archive):
        result["errors"].append(f"archive missing: {archive!r}")
        return result
    try:
        result["declared_schema"] = dump_shader_archive(archive, shader_name)
    except Exception as exc:
        result["errors"].append(f"schema dump failed: {exc}")

    with zipfile.ZipFile(archive, "r") as zf:
        sb_member = None
        want = f"{shader_name}.shaderbin".lower()
        for n in zf.namelist():
            if os.path.basename(n.replace("\\", "/")).lower() == want:
                sb_member = n
                break
        sb_bytes = zf.read(sb_member) if sb_member else b""
        live_sha = _sha(sb_bytes) if sb_bytes else ""
        if live_sha and live_sha != sha:
            result["errors"].append(f"SHA drift catalog={sha} live={live_sha}")
        cbmp = _cbmp_from_shaderbin(sb_bytes)
        result["cbmp_entry_count"] = len(cbmp)

        pso_names = [
            n.replace("\\", "/")
            for n in zf.namelist()
            if n.lower().endswith(".pcdxil.pso")
        ]
        for member in sorted(pso_names):
            raw = zf.read(member)
            pso_sha = _sha(raw)
            identity = parse_pass_identity(
                member=member,
                shader_name=shader_name,
                shaderbin_sha256=live_sha or sha,
                pso_sha256=pso_sha,
            )
            result["variants"].add(identity.variant or "root")
            relevance = classify_blender_relevance(identity.scenario, identity.variant)
            entry = {
                "pass_identity": {
                    "shaderbin_sha256": identity.shaderbin_sha256,
                    "archive_member": identity.archive_member,
                    "variant": identity.variant or "root",
                    "scenario": identity.scenario,
                    "stage": identity.stage,
                    "pso_sha256": identity.pso_sha256,
                    "key": identity.as_key(),
                },
                "blender_relevance": relevance,
                "sample_site_count": 0,
                "error": None,
            }
            try:
                sites = extract_sample_sites(_disasm(dxc, raw), cbmp=cbmp)
                entry["sample_site_count"] = len(sites)
                entry["register_summary"] = register_summary(sites)
                for s in sites:
                    result["sample_sites"].append(
                        {
                            **s.to_dict(),
                            "shader_name": shader_name,
                            "shaderbin_sha256": identity.shaderbin_sha256,
                            "variant": identity.variant or "root",
                            "scenario": identity.scenario,
                            "stage": identity.stage,
                            "archive_member": identity.archive_member,
                            "pso_sha256": identity.pso_sha256,
                            "blender_relevance": relevance,
                            "pass_identity_key": identity.as_key(),
                        }
                    )
            except Exception as exc:
                entry["error"] = str(exc)
                result["errors"].append(f"{member}: {exc}")
            result["pso_members"].append(entry)

    result["variants"] = sorted(result["variants"])
    result["pso_member_count"] = len(result["pso_members"])
    result["sample_site_count"] = len(result["sample_sites"])
    return result


def _carlight_vs_sites(analysis: dict) -> dict:
    """Compare CarLightScenario sample sites vs other passes — per instruction."""
    sites = analysis.get("sample_sites") or []
    carlight = [
        s
        for s in sites
        if s.get("scenario") == "CarLightScenario"
        and (s.get("variant") in ("root", "_Standard", ""))
    ]
    # Prefer _Standard when both exist
    std = [s for s in carlight if s.get("variant") == "_Standard"]
    if std:
        carlight = std
    else:
        root = [s for s in carlight if s.get("variant") == "root"]
        if root:
            carlight = root

    def site_key(s: dict) -> str:
        return (
            f"t{s['texture_register']}|{s['instruction_id']}|"
            f"{s.get('uv_expression')}|comps={s.get('sampled_components')}"
        )

    carlight_tregs = {s["texture_register"] for s in carlight}
    elsewhere = [
        s
        for s in sites
        if s.get("scenario") != "CarLightScenario"
        or s.get("variant") not in ("root", "_Standard", "")
    ]
    absent_elsewhere = []
    best_by_t: dict[int, dict] = {}
    _rank = {
        "VISIBILITY": 0,
        "MAIN_SURFACE_SHADING": 1,
        "SHADOW_VISIBILITY": 2,
        "DEPTH_VISIBILITY": 3,
        "RAY_VISIBILITY": 4,
        "LOD_ONLY": 5,
        "ENGINE_INTERNAL": 6,
        "DEBUG_ONLY": 7,
        "UNRESOLVED": 8,
    }
    for s in elsewhere:
        t = s["texture_register"]
        if t in carlight_tregs:
            continue
        cand = {
            "texture_register": t,
            "example_scenario": s.get("scenario"),
            "example_variant": s.get("variant"),
            "example_uv": s.get("uv_expression"),
            "example_comps": s.get("sampled_components"),
            "blender_relevance": s.get("blender_relevance"),
            "archive_member": s.get("archive_member"),
        }
        prev = best_by_t.get(t)
        if prev is None or _rank.get(cand["blender_relevance"], 9) < _rank.get(
            prev["blender_relevance"], 9
        ):
            best_by_t[t] = cand
    absent_elsewhere = [best_by_t[t] for t in sorted(best_by_t)]

    # Same register, different UV expression across passes
    by_treg: dict[int, set[str]] = defaultdict(set)
    for s in sites:
        by_treg[s["texture_register"]].add(
            f"{s.get('scenario')}|{s.get('variant')}|{s.get('uv_expression')}"
        )
    multi_uv = {
        f"t{t}": sorted(v)
        for t, v in by_treg.items()
        if len({x.split('|', 2)[-1] for x in v}) > 1
    }

    return {
        "carlight_sample_site_count": len(carlight),
        "absent_treg_from_carlight_present_elsewhere": absent_elsewhere,
        "multi_uv_expression_by_treg": multi_uv,
    }


def _variant_audit(analysis: dict) -> list[dict]:
    rows = []
    schema = analysis.get("declared_schema") or {}
    xml = schema.get("shaderbin_xml") or {}
    props = xml.get("variant_properties") or []
    opts = xml.get("variant_options") or []
    variants = analysis.get("variants") or []
    for v in variants:
        members = [
            p
            for p in analysis.get("pso_members") or []
            if (p.get("pass_identity") or {}).get("variant") == v
        ]
        selection = "UNRESOLVED"
        meta = None
        if v == "root" and len(variants) == 1:
            selection = "SOLE_VARIANT"
        if v == "_Standard":
            # Prefer when SimpleHit/Legacy options exist — production uses _Standard
            # until MatI VariantConstant_* proven.
            selection = "PRODUCTION_DEFAULT_UNPROVEN_SWITCH"
            meta = {"variant_properties": props, "variant_options": opts}
        if v == "_DXRSimpleHit_Base":
            selection = "UNRESOLVED"
            meta = {
                "hint": "VariantProperty SimpleHit / VariantConstant_SimpleHit",
                "variant_properties": props,
            }
        if v == "_Standard_L":
            selection = "UNRESOLVED"
            meta = {
                "hint": "ExportVariantOption Legacy → _Standard_L vs _Standard",
                "variant_options": opts,
            }
        rows.append(
            {
                "variant": v,
                "pso_member_count": len(members),
                "archive_members": [
                    (p.get("pass_identity") or {}).get("archive_member") for p in members
                ],
                "pso_shas": [
                    (p.get("pass_identity") or {}).get("pso_sha256") for p in members
                ],
                "declared_selection_metadata": meta,
                "mati_parameters_affecting_selection": [
                    p.get("parametername") for p in props
                ],
                "resolution_status": selection,
            }
        )
    return rows


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    families = _corpus_families(catalog)
    dxc = _addon_dxc()
    log_lines: list[str] = []

    with ConformanceRun(
        milestone="shader-pass-completeness-v2",
        tool="audit_shader_pass_completeness_v2.py",
        command="python tools/material_conformance/audit_shader_pass_completeness_v2.py",
        corpus="2317-instance FH6 family catalog (25 families)",
    ) as run:
        _git_diff(run)
        analyses = []
        sample_table = []
        schema_rows = []
        variant_rows = []
        completeness = []

        for i, fam in enumerate(families, 1):
            name = fam["shader_name"]
            sha = fam.get("shaderbin_sha256")
            msg = f"[{i}/{len(families)}] {name} sha={str(sha)[:12] if sha else 'NONE'}…"
            print(msg, flush=True)
            log_lines.append(msg)
            if not sha:
                analyses.append(
                    {
                        "shader_name": name,
                        "shaderbin_sha256": None,
                        "classification": fam["classification"],
                        "capability": fam["capability"],
                        "instance_count": fam["instance_count"],
                        "pso_member_count": 0,
                        "sample_site_count": 0,
                        "variants": [],
                        "errors": ["NO_SHADER_ARCHIVE_OR_SHA"],
                    }
                )
                continue
            analysis = _analyze_all_psos(
                shader_name=name,
                sha=sha,
                archive=fam.get("shader_archive_path") or "",
                dxc=dxc,
            )
            analysis["classification"] = fam["classification"]
            analysis["instance_count"] = fam["instance_count"]
            cmp_ = _carlight_vs_sites(analysis)
            analysis["completeness"] = cmp_
            completeness.append({"shader_name": name, "shaderbin_sha256": sha, **cmp_})
            vrows = _variant_audit(analysis)
            analysis["variant_selection"] = vrows
            variant_rows.extend(
                [{"shader_name": name, "shaderbin_sha256": sha, **r} for r in vrows]
            )
            sample_table.extend(analysis.get("sample_sites") or [])
            if analysis.get("declared_schema"):
                schema_rows.append(analysis["declared_schema"])
            analyses.append(analysis)
            log_lines.append(
                f"  pso={analysis.get('pso_member_count')} "
                f"sites={analysis.get('sample_site_count')} "
                f"variants={analysis.get('variants')} "
                f"errors={len(analysis.get('errors') or [])}"
            )

        # Contracts validation
        contract_logs = []
        for sha in list_contracted_shas():
            c = load_shader_pass_contract(sha)
            contract_logs.append(
                {
                    "shaderbin_sha256": sha,
                    "shader_name": c.get("shader_name") if c else None,
                    "relevant_pass_count": len((c or {}).get("relevant_passes") or []),
                    "blender_import_sites": sum(
                        1
                        for p in (c or {}).get("relevant_passes") or []
                        for s in p.get("import_sample_sites") or []
                        if s.get("blender_import")
                    ),
                    "unresolved": (c or {}).get("unresolved") or [],
                }
            )

        # Unknown SHA fail-closed check
        fail_closed = resolve_uv_choice_texcoord(
            {0x402B8ED0: type("P", (), {"type": 3, "value": True})()},
            shaderbin_sha256="00" * 32,
        )
        run.record_test_result(
            "unknown_sha_uvchoice_fail_closed",
            fail_closed is None,
            detail="UVChoice returns None for unknown SHA",
        )

        totals = {
            "corpus_families": len(families),
            "exact_shas_available": sum(
                1 for f in families if f.get("shaderbin_sha256")
            ),
            "families_missing_exact_sha": sum(
                1 for f in families if not f.get("shaderbin_sha256")
            ),
            "pso_archive_members": sum(
                a.get("pso_member_count") or 0 for a in analyses
            ),
            "shader_variants": len(
                {
                    (a.get("shader_name"), v)
                    for a in analyses
                    for v in (a.get("variants") or [])
                }
            ),
            "sample_sites": len(sample_table),
            "declared_txmp_bindings": sum(
                len(s.get("declared_txmp") or []) for s in schema_rows
            ),
            "relevant_pass_contracts": len(list_contracted_shas()),
            "unresolved_variants": sum(
                1
                for r in variant_rows
                if r.get("resolution_status") == "UNRESOLVED"
            ),
            "unresolved_families": sum(
                1 for f in families if f.get("classification") == "NO_SHADER_ARCHIVE_OR_SHA"
            ),
        }

        payload = {
            "totals": totals,
            "families": families,
            "analyses": [],
            "contract_validation": contract_logs,
        }
        for a in analyses:
            slim = {k: v for k, v in a.items() if k != "sample_sites"}
            slim["sample_site_count"] = a.get("sample_site_count")
            # Drop nested declared_schema from slim analyses (lives in schema audit).
            if "declared_schema" in slim:
                slim["declared_schema_txmp_count"] = len(
                    (slim.get("declared_schema") or {}).get("declared_txmp") or []
                )
                slim.pop("declared_schema", None)
            payload["analyses"].append(slim)

        run.write_json("data/shader_pass_completeness_audit.json", payload)
        run.write_json("shader_pass_completeness_audit.json", payload)
        run.write_json(
            "data/shader_sample_site_table.json",
            {"count": len(sample_table), "sample_sites": sample_table},
        )
        run.write_json(
            "shader_sample_site_table.json",
            {"count": len(sample_table), "sample_sites": sample_table},
        )
        run.write_json(
            "data/declared_shader_schema_audit.json",
            {"shader_count": len(schema_rows), "shaders": schema_rows},
        )
        run.write_json(
            "declared_shader_schema_audit.json",
            {"shader_count": len(schema_rows), "shaders": schema_rows},
        )
        run.write_json(
            "data/variant_selection_audit.json",
            {"rows": variant_rows},
        )
        # Material instance schema from catalog TXMP signatures (no silent omission).
        instance_rows = []
        for inst in catalog.get("instances") or []:
            instance_rows.append(
                {
                    "instance_key": inst.get("instance_key"),
                    "shader_name": inst.get("shader_name"),
                    "shaderbin_sha256": inst.get("shaderbin_sha256"),
                    "classification": (
                        "EXACT_SHA_AVAILABLE"
                        if inst.get("shaderbin_sha256")
                        else "NO_SHADER_ARCHIVE_OR_SHA"
                    ),
                    "txmp_bindings": inst.get("txmp_bindings"),
                    "lifecycle": "BOUND_BY_INSTANCE"
                    if inst.get("txmp_bindings")
                    else "DECLARED",
                    "status": inst.get("status"),
                }
            )
        run.write_json(
            "data/material_instance_schema_audit.json",
            {
                "instance_count": len(instance_rows),
                "instances": instance_rows,
                "note": (
                    "Catalog-derived instance TXMP bindings. Full MatI parameter "
                    "dumps available via declared_schema.dump_material_instance."
                ),
            },
        )
        run.write_json(
            "material_instance_schema_audit.json",
            {"instance_count": len(instance_rows), "instances": instance_rows},
        )

        # Markdown reports
        md = [
            "# Shader pass completeness audit (v2)",
            "",
            "## Totals",
            "",
        ]
        for k, v in totals.items():
            md.append(f"- **{k}**: {v}")
        md += [
            "",
            "## Families (25)",
            "",
        ]
        for f in families:
            md.append(
                f"- `{f['shader_name']}` — {f['classification']} "
                f"(instances={f['instance_count']}, "
                f"sha={str(f.get('shaderbin_sha256') or 'null')[:16]})"
            )
        md += ["", "## CarLight gaps (sample-site based)", ""]
        for c in completeness:
            absent = c.get("absent_treg_from_carlight_present_elsewhere") or []
            if not absent:
                continue
            md.append(f"### `{c['shader_name']}`")
            for a in absent:
                md.append(
                    f"- t{a['texture_register']} on `{a['example_scenario']}` "
                    f"({a['blender_relevance']}) uv=`{a['example_uv']}`"
                )
            md.append("")
        md += [
            "## Contracts",
            "",
        ]
        for c in contract_logs:
            md.append(
                f"- `{c['shader_name']}` (`{c['shaderbin_sha256'][:16]}…`) "
                f"passes={c['relevant_pass_count']} "
                f"blender_import_sites={c['blender_import_sites']}"
            )
            for u in c.get("unresolved") or []:
                md.append(f"  - unresolved: {u}")
        run.write_text("SHADER_PASS_COMPLETENESS_AUDIT.md", "\n".join(md) + "\n")
        run.write_text(
            "audits/SHADER_PASS_COMPLETENESS_AUDIT.md", "\n".join(md) + "\n"
        )

        site_md = [
            "# Shader sample-site table",
            "",
            f"Total sample sites: **{len(sample_table)}** "
            "(one row per DXIL sample instruction).",
            "",
            "Register aggregates are derived views only — see JSON for full rows.",
            "",
        ]
        by_shader: dict[str, int] = defaultdict(int)
        for s in sample_table:
            by_shader[s["shader_name"]] += 1
        for name, n in sorted(by_shader.items()):
            site_md.append(f"- `{name}`: {n} sites")
        run.write_text("SHADER_SAMPLE_SITE_TABLE.md", "\n".join(site_md) + "\n")
        run.write_text(
            "audits/SHADER_SAMPLE_SITE_TABLE.md", "\n".join(site_md) + "\n"
        )

        schema_md = [
            "# Declared shader schema audit",
            "",
            f"Exact SHAs with schema dumps: **{len(schema_rows)}**",
            "",
            "Lifecycle labels: DECLARED / BOUND_BY_INSTANCE / SAMPLED_IN_PASS / "
            "ACTIVE_IN_BRANCH / USED_IN_FINAL_EXPRESSION",
            "",
        ]
        for s in schema_rows:
            schema_md.append(
                f"- `{s['shader_name']}` TXMP={len(s.get('declared_txmp') or [])} "
                f"CBMP={len(s.get('declared_cbmp') or [])} "
                f"defaults={len(s.get('shader_defaults') or [])} "
                f"variants_xml={len((s.get('shaderbin_xml') or {}).get('variant_properties') or [])}"
            )
        run.write_text("DECLARED_SHADER_SCHEMA_AUDIT.md", "\n".join(schema_md) + "\n")
        run.write_text(
            "MATERIAL_INSTANCE_SCHEMA_AUDIT.md",
            "# Material instance schema audit\n\n"
            "Corpus family coverage recorded; use "
            "`declared_schema.dump_material_instance` for per-MatI dumps.\n"
            f"Instances in catalog: {sum(f['instance_count'] for f in families)}\n",
        )

        run.write_text("logs/pass_analysis.log", "\n".join(log_lines) + "\n")
        run.write_text(
            "logs/contract_validation.log",
            json.dumps(contract_logs, indent=2) + "\n",
        )
        run.write_text(
            "logs/schema_dump.log",
            f"dumped {len(schema_rows)} shader schemas\n",
        )

        # Identity smoke assertions as recorded tests
        from io_import_forza_carbin.materials.pass_identity import scenario_from_member

        trunc_ok = (
            scenario_from_member(
                "car_liveryCarLightScenario.pcdxil.pso", "car_livery"
            )
            == "CarLightScenario"
            and scenario_from_member(
                "retro_licenseplate_atlasCarLightScenario.pcdxil.pso",
                "retro_licenseplate_atlas",
            )
            == "CarLightScenario"
            and scenario_from_member(
                "other_prefixFooScenario.pcdxil.pso", "other_prefix"
            )
            == "FooScenario"
        )
        run.record_test_result("no_pass_name_truncation", trunc_ok)

        paint = next(
            (a for a in analyses if a.get("shader_name") == "car_automotive_paint"),
            None,
        )
        tires = next(
            (a for a in analyses if a.get("shader_name") == "car_tires_pg"), None
        )
        run.record_test_result(
            "no_variant_collapse_paint",
            bool(paint)
            and paint.get("pso_member_count") == 22
            and set(paint.get("variants") or [])
            >= {"_Standard", "_DXRSimpleHit_Base"},
            detail=str(paint.get("variants") if paint else None),
        )
        run.record_test_result(
            "no_variant_collapse_tires",
            bool(tires)
            and tires.get("pso_member_count") == 22
            and set(tires.get("variants") or []) >= {"_Standard", "_Standard_L"},
            detail=str(tires.get("variants") if tires else None),
        )
        run.record_test_result(
            "all_25_families_represented",
            len(families) == 25,
            detail=str(len(families)),
        )
        run.record_test_result(
            "alphafadelivery_explicit_unresolved",
            any(
                f["shader_name"] == "alphafadelivery_ch1norm"
                and f["classification"] == "NO_SHADER_ARCHIVE_OR_SHA"
                for f in families
            ),
        )
        gap_names = {
            "car_livery",
            "car_reflector",
            "car_standard_emissive",
            "car_tinthack",
            "retro_licenseplate_atlas",
        }
        run.record_test_result(
            "five_known_gaps_contracted_or_documented",
            all(
                load_shader_pass_contract(
                    next(
                        (
                            f["shaderbin_sha256"]
                            for f in families
                            if f["shader_name"] == n
                        ),
                        None,
                    )
                )
                for n in gap_names
            ),
        )

        run.finalize(ok=True)
        print("TOTALS", json.dumps(totals, indent=2), flush=True)
        print(f"Wrote {run.run_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
