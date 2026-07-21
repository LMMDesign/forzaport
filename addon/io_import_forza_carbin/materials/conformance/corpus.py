"""Material conformance corpus: inventory only (no production resolver changes).

Scans car archives / modelbins for MatI clusters and optional capability status.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# Failure taxonomy (Milestone A reports).
FAILURE_CLASSES = (
    "WRONG_CAPABILITY",
    "WRONG_ACTIVE_BINDING",
    "WRONG_BASE_COLOR_SOURCE",
    "WRONG_CHANNEL",
    "WRONG_UV_STREAM",
    "WRONG_UV_TRANSFORM",
    "WRONG_COLOR_SPACE",
    "WRONG_TEXTURE_DECODE",
    "WRONG_CONSTANT",
    "UNSUPPORTED_EXPRESSION",
    "UNSUPPORTED_SHADER_FAMILY",
    "MISSING_SOURCE",
    "BUILDER_ERROR",
)


@dataclass(frozen=True)
class CorpusCar:
    car_id: str
    zip_path: str
    role: str  # painted_body | carbon_heavy | tyre_complex | interior | audit


DEFAULT_CORPUS: tuple[CorpusCar, ...] = (
    CorpusCar("GMA_T50_22", "cars/GMA_T50_22.zip", "audit+carbon"),
    CorpusCar("FER_F80_25", "cars/FER_F80_25.zip", "audit"),
    CorpusCar("POR_911GT3RS_23", "cars/POR_911GT3RS_23.zip", "painted_body"),
    CorpusCar("CHE_CorvetteZ06_23", "cars/CHE_CorvetteZ06_23.zip", "painted_body"),
    CorpusCar("AST_Valkyrie_23", "cars/AST_Valkyrie_23.zip", "carbon_heavy"),
    CorpusCar("TOY_LandCruiser_25", "cars/TOY_LandCruiser_25.zip", "interior"),
)


@dataclass
class MaterialInstanceRecord:
    car_id: str
    modelbin_game_path: str
    source_material_name: str
    instance_key: str
    shader_name: str | None
    shader_archive_path: str | None
    shaderbin_sha256: str | None
    available_psos: tuple[str, ...]
    txmp_signature: str
    param_switch_signature: str
    txmp_bindings: list[dict[str, Any]]
    switches: dict[str, Any]
    colors: dict[str, Any]
    scalars: dict[str, Any]
    texture_paths: list[str]
    status: str | None = None
    capability: str | None = None
    base_color_source: str | None = None
    rejection_reasons: tuple[str, ...] = ()
    cluster_id: str = ""


@dataclass
class FamilyCluster:
    cluster_id: str
    shader_name: str | None
    shaderbin_sha256: str | None
    txmp_signature: str
    param_switch_signature: str
    instance_count: int = 0
    cars: set[str] = field(default_factory=set)
    status_counts: Counter = field(default_factory=Counter)
    sample_materials: list[str] = field(default_factory=list)
    diagnostic_or_incorrect: int = 0


def _sha256_file(path: str) -> str | None:
    if not path or not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _bool_val(p) -> Any:
    if p is None:
        return None
    if getattr(p, "type", None) == 3:
        return bool(p.value)
    return None


def _color_val(p) -> Any:
    if p is None or getattr(p, "type", None) not in (0, 1):
        return None
    v = getattr(p, "value", None)
    if isinstance(v, tuple) and len(v) >= 3:
        return [round(float(x), 6) for x in v[:4]]
    return None


def _scalar_val(p) -> Any:
    if p is None:
        return None
    t = getattr(p, "type", None)
    if t in (2, 11):  # float / floatN
        v = getattr(p, "value", None)
        if isinstance(v, tuple):
            return [round(float(x), 6) for x in v]
        if isinstance(v, (int, float)):
            return float(v)
    return None


def modelbin_game_paths(media_root: str, car: CorpusCar) -> list[str]:
    zip_path = os.path.join(media_root, car.zip_path.replace("/", os.sep))
    paths: list[str] = []
    if not os.path.isfile(zip_path):
        return paths
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            nl = n.replace("\\", "/").lower()
            if not nl.endswith(".modelbin"):
                continue
            if "/scene/" not in nl and not nl.startswith("scene/"):
                continue
            parts = n.replace("\\", "/").split("/")
            if parts and parts[0].lower() == car.car_id.lower():
                rel = "/".join(parts[1:])
            else:
                rel = "/".join(parts)
            paths.append(
                f"GAME:\\Media\\Cars\\{car.car_id}\\" + rel.replace("/", "\\")
            )
    return list(dict.fromkeys(paths))


def tire_modelbin_game_paths(media_root: str, compound: str = "tire_c") -> list[str]:
    """Library tire modelbins used by stock compounds (complex tread case)."""
    tire_zip = os.path.join(
        media_root, "cars", "_library", "scene", "tires", f"{compound}.zip"
    )
    if not os.path.isfile(tire_zip):
        return []
    paths: list[str] = []
    with zipfile.ZipFile(tire_zip) as z:
        for n in z.namelist():
            nl = n.replace("\\", "/").lower()
            if not nl.endswith(".modelbin"):
                continue
            # Prefer GAME path under cars/_library/scene/tires/
            member = n.replace("\\", "/")
            paths.append(
                "GAME:\\Media\\Cars\\_library\\scene\\tires\\"
                + compound
                + "\\"
                + member.replace("/", "\\")
            )
    return list(dict.fromkeys(paths))


def shader_archive_info(media_root: str, shader_name: str | None) -> tuple[str | None, str | None, tuple[str, ...]]:
    if not shader_name:
        return None, None, ()
    archive = os.path.join(
        media_root, "cars", "_library", "shaders", f"{shader_name}.zip"
    )
    if not os.path.isfile(archive):
        return None, None, ()
    psos: list[str] = []
    shaderbin_member = None
    with zipfile.ZipFile(archive) as z:
        for n in z.namelist():
            low = n.lower()
            if low.endswith(".pcdxil.pso"):
                psos.append(n)
            if low.endswith(".shaderbin") and shaderbin_member is None:
                shaderbin_member = n
        sha = None
        if shaderbin_member:
            sha = hashlib.sha256(z.read(shaderbin_member)).hexdigest()
    return archive.replace("\\", "/"), sha, tuple(sorted(psos))


def _txmp_signature(txmp: dict, params: dict, name_for_hash) -> str:
    parts = []
    for h, treg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
        name = name_for_hash(h) or f"0x{h & 0xFFFFFFFF:08X}"
        p = params.get(h)
        path = (getattr(p, "path", "") or "") if p else ""
        leaf = os.path.basename(path.replace("\\", "/")).lower() if path else ""
        parts.append(f"t{int(treg)}:{name}:{leaf}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _switch_signature(params: dict, switch_hashes: Iterable[int], name_for_hash) -> str:
    parts = []
    for h in sorted(set(int(x) & 0xFFFFFFFF for x in switch_hashes)):
        p = params.get(h) or params.get(h & 0xFFFFFFFF)
        name = name_for_hash(h) or f"0x{h:08X}"
        if p is None:
            parts.append(f"{name}=absent")
        elif getattr(p, "type", None) == 3:
            parts.append(f"{name}={bool(p.value)}")
        else:
            parts.append(f"{name}=type{getattr(p, 'type', None)}")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _important_switch_hashes(SPN) -> tuple[int, ...]:
    return (
        int(SPN.UniqueBaseColorSwitchBool),
        int(SPN.UniqueBaseTextureSwitchBool),
        int(SPN.ColorGroupSwitchBool),
        int(SPN.UniqueLiverySwitchBool),
        int(SPN.MaskedLiveryBool),
        int(SPN.UseAlphaTestBool),
        int(SPN.UseAlphaBlendBool),
        int(SPN.AlphaTransparencyBool),
        0x402B8ED0,  # UVChoice_OnCh1_OffCh2
    )


def scan_corpus(
    *,
    media_root: str,
    cars: tuple[CorpusCar, ...] | None = None,
    include_stock_tire: bool = True,
    resolve_status: bool = True,
    limit_modelbins_per_car: int | None = None,
) -> dict[str, Any]:
    """Scan corpus; returns serialisable catalog payload.

    Does not mutate production resolver behaviour — read-only observation.
    """
    # Late import so conformance tooling stays optional outside addon load.
    import sys
    import types

    addon = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    if "io_import_forza_carbin" not in sys.modules:
        pkg = types.ModuleType("io_import_forza_carbin")
        pkg.__path__ = [addon]
        sys.modules["io_import_forza_carbin"] = pkg

    from io_import_forza_carbin.geometry import Modelbin
    from io_import_forza_carbin.materials.diagnose import resolve_with_diagnostics
    from io_import_forza_carbin.materials.instance_key import material_instance_key
    from io_import_forza_carbin.materials.name_hashes import name_for_hash
    from io_import_forza_carbin.materials.translate import translator_for
    from io_import_forza_carbin.parsing.binary import BinaryStream
    from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN
    from io_import_forza_carbin.parsing.paths import GamePathResolver
    from io_import_forza_carbin.materials.texture_source import resolve_texture_source

    cars = cars or DEFAULT_CORPUS
    resolver = GamePathResolver(media_root)
    builder = translator_for("fh6", media_root) if resolve_status else None
    switch_hashes = _important_switch_hashes(SPN)

    records: list[MaterialInstanceRecord] = []
    seen_keys: set[str] = set()
    shader_cache: dict[str, tuple[str | None, str | None, tuple[str, ...]]] = {}

    jobs: list[tuple[str, str]] = []
    for car in cars:
        paths = modelbin_game_paths(media_root, car)
        if limit_modelbins_per_car is not None:
            paths = paths[:limit_modelbins_per_car]
        for gp in paths:
            jobs.append((car.car_id, gp))
    if include_stock_tire:
        for gp in tire_modelbin_game_paths(media_root, "tire_c"):
            jobs.append(("STOCK_TIRE_C", gp))

    for car_id, gp in jobs:
        disk = resolver.resolve(gp)
        if not disk or not os.path.isfile(disk):
            continue
        try:
            mb = Modelbin()
            mb.deserialize(BinaryStream.from_path(disk), 1, resolver, True)
        except Exception:
            continue
        for pm in mb.materials:
            if pm.obj is None:
                continue
            key = material_instance_key(pm, game_key="fh6")
            if not key:
                continue
            dedupe = f"{car_id}|{key}"
            if dedupe in seen_keys:
                continue
            seen_keys.add(dedupe)
            m = pm.obj
            params = getattr(m, "parameters", None) or {}
            txmp = getattr(m, "txmp", None) or {}
            shader_name = getattr(m, "shader_name", None)
            if shader_name not in shader_cache:
                shader_cache[shader_name or ""] = shader_archive_info(
                    media_root, shader_name
                )
            archive, sha, psos = shader_cache[shader_name or ""]

            bindings: list[dict[str, Any]] = []
            texture_paths: list[str] = []
            for h, treg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
                p = params.get(h)
                path = (getattr(p, "path", "") or "") if p else ""
                name = name_for_hash(h)
                src = None
                if path:
                    try:
                        src = resolve_texture_source(path, resolver)
                    except Exception:
                        src = None
                    texture_paths.append(path)
                bindings.append(
                    {
                        "name_hash": h & 0xFFFFFFFF,
                        "name": name,
                        "treg": int(treg),
                        "path": path,
                        "source_exists": bool(src and src.exists),
                        "archive_member": getattr(src, "archive_member", None)
                        if src
                        else None,
                    }
                )

            switches: dict[str, Any] = {}
            colors: dict[str, Any] = {}
            scalars: dict[str, Any] = {}
            for h, p in params.items():
                name = name_for_hash(h)
                if name is None:
                    continue
                b = _bool_val(p)
                if b is not None:
                    switches[name] = b
                    continue
                c = _color_val(p)
                if c is not None and ("Color" in name or "Tint" in name or "Paint" in name):
                    colors[name] = c
                    continue
                s = _scalar_val(p)
                if s is not None and len(scalars) < 40:
                    scalars[name] = s

            tx_sig = _txmp_signature(txmp, params, name_for_hash)
            sw_sig = _switch_signature(params, switch_hashes, name_for_hash)
            cluster_id = f"{sha or 'nosha'}|{tx_sig}|{sw_sig}"

            status = capability = base_src = None
            reasons: tuple[str, ...] = ()
            if resolve_status and builder is not None:
                try:
                    result = resolve_with_diagnostics(
                        builder, key, m, resolver=resolver
                    )
                    status = result.diagnostic.status.value
                    capability = result.diagnostic.capability
                    if result.resolution and result.resolution.resolved:
                        base_src = (
                            result.resolution.resolved.capability.base_color_source.kind.value
                        )
                    if result.resolution and result.resolution.probe.rejection_reasons:
                        reasons = tuple(result.resolution.probe.rejection_reasons)
                except Exception as exc:  # noqa: BLE001
                    status = "BUILDER_ERROR"
                    reasons = (f"{type(exc).__name__}: {exc}",)

            records.append(
                MaterialInstanceRecord(
                    car_id=car_id,
                    modelbin_game_path=gp,
                    source_material_name=pm.name or "",
                    instance_key=key,
                    shader_name=shader_name,
                    shader_archive_path=archive,
                    shaderbin_sha256=sha,
                    available_psos=psos,
                    txmp_signature=tx_sig,
                    param_switch_signature=sw_sig,
                    txmp_bindings=bindings,
                    switches=switches,
                    colors=colors,
                    scalars=scalars,
                    texture_paths=texture_paths,
                    status=status,
                    capability=capability,
                    base_color_source=base_src,
                    rejection_reasons=reasons,
                    cluster_id=cluster_id,
                )
            )

    clusters: dict[str, FamilyCluster] = {}
    for rec in records:
        cl = clusters.get(rec.cluster_id)
        if cl is None:
            cl = FamilyCluster(
                cluster_id=rec.cluster_id,
                shader_name=rec.shader_name,
                shaderbin_sha256=rec.shaderbin_sha256,
                txmp_signature=rec.txmp_signature,
                param_switch_signature=rec.param_switch_signature,
            )
            clusters[rec.cluster_id] = cl
        cl.instance_count += 1
        cl.cars.add(rec.car_id)
        if rec.status:
            cl.status_counts[rec.status] += 1
        if rec.status and rec.status not in ("SUPPORTED",):
            # PARTIAL is still often visually ok; count unresolved / errors heavier.
            if rec.status in (
                "UNRESOLVED_CAPABILITY",
                "MISSING_TEXTURE",
                "MISSING_PROVENANCE",
                "BUILDER_ERROR",
                "INVALID_BINDING",
            ):
                cl.diagnostic_or_incorrect += 1
            elif rec.status == "PARTIALLY_SUPPORTED":
                cl.diagnostic_or_incorrect += 0  # not auto-incorrect
        if len(cl.sample_materials) < 5:
            cl.sample_materials.append(rec.source_material_name)

    # Rank by instances * cars + diagnostic weight
    family_by_shader: dict[str, dict[str, Any]] = {}
    for rec in records:
        sh = rec.shader_name or "(none)"
        fam = family_by_shader.setdefault(
            sh,
            {
                "shader_name": sh,
                "instances": 0,
                "cars": set(),
                "clusters": set(),
                "status": Counter(),
                "diagnostic_unresolved": 0,
                "shaderbin_sha256": rec.shaderbin_sha256,
                "archive": rec.shader_archive_path,
                "pso_count": len(rec.available_psos),
            },
        )
        fam["instances"] += 1
        fam["cars"].add(rec.car_id)
        fam["clusters"].add(rec.cluster_id)
        if rec.status:
            fam["status"][rec.status] += 1
        if rec.status == "UNRESOLVED_CAPABILITY":
            fam["diagnostic_unresolved"] += 1

    ranking = []
    for sh, fam in family_by_shader.items():
        cars_n = len(fam["cars"])
        inst = fam["instances"]
        diag = fam["diagnostic_unresolved"]
        # Importance heuristic for exterior: weight unresolved + volume
        score = inst * 10 + cars_n * 50 + diag * 25
        if any(x in sh for x in ("paint", "standard", "carbon", "tire", "glass", "window")):
            score += 100
        ranking.append(
            {
                "shader_name": sh,
                "instance_count": inst,
                "car_count": cars_n,
                "cars": sorted(fam["cars"]),
                "cluster_count": len(fam["clusters"]),
                "status_counts": dict(fam["status"]),
                "diagnostic_unresolved": diag,
                "shaderbin_sha256": fam["shaderbin_sha256"],
                "archive": fam["archive"],
                "pso_count": fam["pso_count"],
                "rank_score": score,
            }
        )
    ranking.sort(key=lambda r: (-r["rank_score"], -r["instance_count"], r["shader_name"]))

    payload = {
        "media_root": media_root,
        "cars": [asdict(c) for c in cars]
        + (
            [{"car_id": "STOCK_TIRE_C", "zip_path": "cars/_library/scene/tires/tire_c.zip", "role": "tyre_complex"}]
            if include_stock_tire
            else []
        ),
        "totals": {
            "material_instances": len(records),
            "unique_shaders": len(family_by_shader),
            "clusters": len(clusters),
        },
        "shader_family_ranking": ranking,
        "clusters": [
            {
                "cluster_id": c.cluster_id,
                "shader_name": c.shader_name,
                "shaderbin_sha256": c.shaderbin_sha256,
                "txmp_signature": c.txmp_signature,
                "param_switch_signature": c.param_switch_signature,
                "instance_count": c.instance_count,
                "cars": sorted(c.cars),
                "status_counts": dict(c.status_counts),
                "diagnostic_or_incorrect": c.diagnostic_or_incorrect,
                "sample_materials": c.sample_materials,
            }
            for c in sorted(
                clusters.values(), key=lambda x: (-x.instance_count, x.shader_name or "")
            )
        ],
        "instances": [
            {
                **{k: getattr(rec, k) for k in (
                    "car_id",
                    "modelbin_game_path",
                    "source_material_name",
                    "instance_key",
                    "shader_name",
                    "shader_archive_path",
                    "shaderbin_sha256",
                    "txmp_signature",
                    "param_switch_signature",
                    "status",
                    "capability",
                    "base_color_source",
                    "cluster_id",
                )},
                "available_psos": list(rec.available_psos),
                "txmp_bindings": rec.txmp_bindings,
                "switches": rec.switches,
                "colors": rec.colors,
                "rejection_reasons": list(rec.rejection_reasons),
                "texture_path_count": len(rec.texture_paths),
            }
            for rec in records
        ],
    }
    return payload


def write_family_catalog_markdown(payload: dict[str, Any], path: str) -> None:
    ranking = payload["shader_family_ranking"]
    totals = payload["totals"]
    lines = [
        "# Material shader family catalog",
        "",
        "Milestone A inventory — **read-only**; production resolver unchanged.",
        "",
        f"- Material instances: **{totals['material_instances']}**",
        f"- Unique shader families: **{totals['unique_shaders']}**",
        f"- Parameter/TXMP clusters: **{totals['clusters']}**",
        "",
        "## Cars scanned",
        "",
        "| Car | Role |",
        "|-----|------|",
    ]
    for c in payload["cars"]:
        lines.append(f"| `{c['car_id']}` | {c['role']} |")
    lines += [
        "",
        "## Family ranking (by coverage + diagnostic weight)",
        "",
        "| Rank | Shader | Instances | Cars | Unresolved | Status | Score |",
        "|-----:|--------|----------:|-----:|-----------:|--------|------:|",
    ]
    for i, r in enumerate(ranking, 1):
        st = ", ".join(f"{k}:{v}" for k, v in sorted(r["status_counts"].items()))
        lines.append(
            f"| {i} | `{r['shader_name']}` | {r['instance_count']} | "
            f"{r['car_count']} | {r['diagnostic_unresolved']} | {st} | {r['rank_score']} |"
        )
    lines += [
        "",
        "## Ranking notes",
        "",
        "- Score = `instances*10 + cars*50 + unresolved*25` plus bonuses for paint/standard/carbon/tire/glass/window names.",
        "- Viewport surface area is **not** measured in Milestone A (no Blender session); exterior importance uses name heuristics + volume.",
        "- Instance coverage is the primary metric for later milestones.",
        "",
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
