"""Deterministic comparison of ImportMaterialReport JSON payloads (no bpy).

Used for F80 baseline vs typed-resolver validation and future capability gates.
Match materials by normalized instance key (``|v3-|`` / ``|v4-|`` equivalent).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

_KEY_VERSION = re.compile(r"\|v[0-9]+-")

_ROLE_NORMALIZE = {
    "diffuse": "base_color",
    "base_color": "base_color",
    "normal": "normal",
    "rmao": "rmao",
    "alpha": "alpha",
}


def normalize_instance_key(key: str) -> str:
    """Collapse pipeline key version tags so v3/v4 fingerprints still match."""
    return _KEY_VERSION.sub("|vN-", key or "")


def _normalize_channel(channel: Any) -> str | None:
    if channel is None:
        return None
    text = str(channel)
    if text.startswith("channel:"):
        text = text.split(":", 1)[1]
    return text or None


def _sorted_counter(items) -> dict[str, int]:
    return dict(sorted(Counter(items).items(), key=lambda kv: kv[0]))


def binding_contract_from_material_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Comparable binding fingerprint from a report material entry.

    Prefers explicit ``binding_contract`` (typed resolve enrichment). Falls back
    to consumed texture_bindings rows (baseline schema).
    """
    explicit = entry.get("binding_contract")
    if isinstance(explicit, dict) and explicit:
        slots = list(explicit.get("slots") or [])
        return {
            "capability": entry.get("capability"),
            "status": entry.get("status"),
            "alpha_mode": explicit.get("alpha_mode"),
            "alpha_threshold": explicit.get("alpha_threshold"),
            "base_color": explicit.get("base_color"),
            "consumed_txmp_hashes": sorted(
                int(x) & 0xFFFFFFFF for x in (explicit.get("consumed_txmp_hashes") or [])
            ),
            "slots": sorted(slots, key=lambda s: (s.get("role") or "", s.get("param_hash") or 0)),
        }

    slots = []
    consumed = []
    for row in entry.get("texture_bindings") or ():
        if not row.get("consumed_by_builder"):
            continue
        h = int(row.get("name_hash") or 0) & 0xFFFFFFFF
        consumed.append(h)
        slots.append(
            {
                "role": row.get("semantic_role"),
                "path": row.get("path") or "",
                "texcoord": row.get("uv_role"),
                "uv_channel": row.get("uv_channel"),
                "channel": (row.get("alpha_interpretation") or None),
                "param_hash": h,
                "param_name": row.get("name"),
                "tiling": row.get("tiling"),
                "address": row.get("sampler_address"),
            }
        )
    return {
        "capability": entry.get("capability"),
        "status": entry.get("status"),
        "alpha_mode": entry.get("alpha_mode"),
        "alpha_threshold": entry.get("alpha_threshold"),
        "base_color": entry.get("base_color"),
        "consumed_txmp_hashes": sorted(set(consumed)),
        "slots": sorted(slots, key=lambda s: (s.get("role") or "", s.get("param_hash") or 0)),
    }


def _slot_core(slot: dict[str, Any]) -> dict[str, Any]:
    """Behavioural core of a slot (ignore null tiling/address when baseline lacked them)."""
    role = slot.get("role")
    role = _ROLE_NORMALIZE.get(role, role)
    out = {
        "role": role,
        "path": slot.get("path") or "",
        "param_hash": int(slot.get("param_hash") or 0) & 0xFFFFFFFF,
        "param_name": slot.get("param_name"),
    }
    tex = slot.get("texcoord") or (
        f"TEXCOORD{slot['uv_channel']}" if slot.get("uv_channel") is not None else None
    )
    out["texcoord"] = tex
    channel = _normalize_channel(slot.get("channel"))
    if channel is not None:
        out["channel"] = channel
    if slot.get("tiling") is not None:
        out["tiling"] = slot.get("tiling")
    if slot.get("address") is not None:
        out["address"] = slot.get("address")
    return out


def compare_material_reports(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    ignore_evidence_source: bool = True,
) -> dict[str, Any]:
    """Field-level deterministic compare. Returns a structured diff document."""
    base_mats = {
        normalize_instance_key(m["instance_key"]): m for m in baseline.get("materials") or []
    }
    cur_mats = {
        normalize_instance_key(m["instance_key"]): m for m in current.get("materials") or []
    }

    only_baseline = sorted(set(base_mats) - set(cur_mats))
    only_current = sorted(set(cur_mats) - set(base_mats))
    shared = sorted(set(base_mats) & set(cur_mats))

    status_diffs: list[dict[str, Any]] = []
    capability_diffs: list[dict[str, Any]] = []
    binding_diffs: list[dict[str, Any]] = []
    assignment_diffs: list[dict[str, Any]] = []
    shader_diffs: list[dict[str, Any]] = []
    name_diffs: list[dict[str, Any]] = []
    unresolved_hash_diffs: list[dict[str, Any]] = []
    affected_diffs: list[dict[str, Any]] = []
    intentional: list[dict[str, Any]] = []

    for key in shared:
        b = base_mats[key]
        c = cur_mats[key]
        label = b.get("material_name") or key

        if b.get("status") != c.get("status"):
            status_diffs.append(
                {"key": key, "name": label, "baseline": b.get("status"), "current": c.get("status")}
            )
        if b.get("capability") != c.get("capability"):
            capability_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": b.get("capability"),
                    "current": c.get("capability"),
                }
            )
        if b.get("shader_name") != c.get("shader_name"):
            shader_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": b.get("shader_name"),
                    "current": c.get("shader_name"),
                }
            )
        if b.get("material_name") != c.get("material_name"):
            name_diffs.append(
                {
                    "key": key,
                    "baseline": b.get("material_name"),
                    "current": c.get("material_name"),
                }
            )
        if tuple(b.get("unresolved_semantics") or ()) != tuple(
            c.get("unresolved_semantics") or ()
        ):
            unresolved_hash_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": list(b.get("unresolved_semantics") or ()),
                    "current": list(c.get("unresolved_semantics") or ()),
                }
            )
        if b.get("assignment_outcome") != c.get("assignment_outcome"):
            assignment_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": b.get("assignment_outcome"),
                    "current": c.get("assignment_outcome"),
                }
            )
        if tuple(b.get("affected_object_names") or ()) != tuple(
            c.get("affected_object_names") or ()
        ):
            affected_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": list(b.get("affected_object_names") or ()),
                    "current": list(c.get("affected_object_names") or ()),
                }
            )

        # Instance key version tag alone is intentional (v3→v4).
        if b.get("instance_key") != c.get("instance_key"):
            intentional.append(
                {
                    "kind": "instance_key_version_tag",
                    "key": key,
                    "baseline": b.get("instance_key"),
                    "current": c.get("instance_key"),
                    "note": "Fingerprint suffix unchanged; |v3-| vs |v4-| is pipeline tag only.",
                }
            )

        bb = binding_contract_from_material_entry(b)
        cb = binding_contract_from_material_entry(c)
        base_slots = [_slot_core(s) for s in bb["slots"]]
        cur_slots = [_slot_core(s) for s in cb["slots"]]
        # Drop tiling/address from current when baseline omitted them (schema gap).
        for i, bs in enumerate(base_slots):
            if i < len(cur_slots):
                if "tiling" not in bs:
                    cur_slots[i].pop("tiling", None)
                if "address" not in bs:
                    cur_slots[i].pop("address", None)
                if "channel" not in bs:
                    cur_slots[i].pop("channel", None)

        slot_mismatch = base_slots != cur_slots
        hash_mismatch = bb["consumed_txmp_hashes"] != cb["consumed_txmp_hashes"]
        alpha_mode_mismatch = (
            bb.get("alpha_mode") is not None
            and cb.get("alpha_mode") is not None
            and bb.get("alpha_mode") != cb.get("alpha_mode")
        )
        alpha_thr_mismatch = (
            bb.get("alpha_threshold") is not None
            and cb.get("alpha_threshold") is not None
            and bb.get("alpha_threshold") != cb.get("alpha_threshold")
        )
        if (
            slot_mismatch
            or hash_mismatch
            or alpha_mode_mismatch
            or alpha_thr_mismatch
        ):
            binding_diffs.append(
                {
                    "key": key,
                    "name": label,
                    "baseline": {
                        "consumed_txmp_hashes": bb["consumed_txmp_hashes"],
                        "slots": base_slots,
                        "alpha_mode": bb.get("alpha_mode"),
                        "alpha_threshold": bb.get("alpha_threshold"),
                    },
                    "current": {
                        "consumed_txmp_hashes": cb["consumed_txmp_hashes"],
                        "slots": cur_slots,
                        "alpha_mode": cb.get("alpha_mode"),
                        "alpha_threshold": cb.get("alpha_threshold"),
                    },
                }
            )

        # Evidence source string drift is intentional architecture rename.
        if ignore_evidence_source:
            b_ev = [
                {"kind": e.get("kind"), "detail": e.get("detail")}
                for e in (b.get("evidence") or ())
            ]
            c_ev = [
                {"kind": e.get("kind"), "detail": e.get("detail")}
                for e in (c.get("evidence") or ())
            ]
            if b_ev != c_ev:
                # Detail set compare (order-insensitive for contract lines).
                b_set = sorted((e["kind"], e["detail"]) for e in b_ev)
                c_set = sorted((e["kind"], e["detail"]) for e in c_ev)
                if b_set != c_set:
                    intentional.append(
                        {
                            "kind": "evidence_detail_set",
                            "key": key,
                            "name": label,
                            "baseline_only": sorted(set(b_set) - set(c_set)),
                            "current_only": sorted(set(c_set) - set(b_set)),
                            "note": "Inspect — may be intentional provenance wording.",
                        }
                    )
                else:
                    intentional.append(
                        {
                            "kind": "evidence_order_or_source",
                            "key": key,
                            "name": label,
                            "note": "Same kind/detail pairs; ordering or source string differs.",
                        }
                    )

    base_status = baseline.get("count_by_status") or _sorted_counter(
        m.get("status") for m in baseline.get("materials") or []
    )
    cur_status = current.get("count_by_status") or _sorted_counter(
        m.get("status") for m in current.get("materials") or []
    )
    base_cap = baseline.get("count_by_capability") or _sorted_counter(
        m.get("capability") or "(none)" for m in baseline.get("materials") or []
    )
    cur_cap = current.get("count_by_capability") or _sorted_counter(
        m.get("capability") or "(none)" for m in current.get("materials") or []
    )

    def _assign_counts(report: dict[str, Any]) -> dict[str, int]:
        return _sorted_counter(
            m.get("assignment_outcome") for m in report.get("materials") or []
        )

    base_assign = _assign_counts(baseline)
    cur_assign = _assign_counts(current)
    base_diag = sum(
        1
        for m in baseline.get("materials") or []
        if m.get("assignment_outcome") == "ASSIGNED_DIAGNOSTIC"
    )
    cur_diag = sum(
        1
        for m in current.get("materials") or []
        if m.get("assignment_outcome") == "ASSIGNED_DIAGNOSTIC"
    )
    base_affected = sum(
        len(m.get("affected_object_names") or ())
        for m in baseline.get("materials") or []
    )
    cur_affected = sum(
        len(m.get("affected_object_names") or ())
        for m in current.get("materials") or []
    )

    behavioural_nonzero = (
        only_baseline
        or only_current
        or status_diffs
        or capability_diffs
        or binding_diffs
        or assignment_diffs
        or shader_diffs
        or name_diffs
        or unresolved_hash_diffs
        or affected_diffs
        or base_status != cur_status
        or base_cap != cur_cap
        or base_assign != cur_assign
        or base_diag != cur_diag
        or base_affected != cur_affected
        or len(baseline.get("materials") or []) != len(current.get("materials") or [])
    )

    product_deltas = _classify_known_product_deltas(
        status_diffs=status_diffs,
        binding_diffs=binding_diffs,
        intentional=intentional,
        base_mats=base_mats,
        cur_mats=cur_mats,
    )
    unexplained = {
        "status_diffs": [
            d
            for d in status_diffs
            if d["key"] not in product_deltas["explained_keys"]
        ],
        "binding_diffs": [
            d
            for d in binding_diffs
            if d["key"] not in product_deltas["explained_keys"]
        ],
        "capability_diffs": capability_diffs,
        "assignment_diffs": assignment_diffs,
        "shader_diffs": shader_diffs,
        "name_diffs": name_diffs,
        "unresolved_hash_diffs": [
            d
            for d in unresolved_hash_diffs
            if d["key"] not in product_deltas["explained_keys"]
        ],
        "affected_diffs": affected_diffs,
        "only_baseline_keys": only_baseline,
        "only_current_keys": only_current,
    }
    unexplained_nonzero = any(unexplained[k] for k in unexplained)

    return {
        "totals": {
            "baseline_materials": len(baseline.get("materials") or []),
            "current_materials": len(current.get("materials") or []),
            "shared": len(shared),
            "only_baseline": len(only_baseline),
            "only_current": len(only_current),
        },
        "count_by_status": {"baseline": base_status, "current": cur_status},
        "count_by_capability": {"baseline": base_cap, "current": cur_cap},
        "assignment_counts": {"baseline": base_assign, "current": cur_assign},
        "diagnostic_assignment_count": {
            "baseline": base_diag,
            "current": cur_diag,
        },
        "affected_object_count": {
            "baseline": base_affected,
            "current": cur_affected,
        },
        "only_baseline_keys": only_baseline,
        "only_current_keys": only_current,
        "status_diffs": status_diffs,
        "capability_diffs": capability_diffs,
        "shader_diffs": shader_diffs,
        "name_diffs": name_diffs,
        "unresolved_hash_diffs": unresolved_hash_diffs,
        "binding_diffs": binding_diffs,
        "assignment_diffs": assignment_diffs,
        "affected_diffs": affected_diffs,
        "intentional_or_wording": intentional,
        "known_product_deltas": product_deltas["explanations"],
        "unexplained_diffs": unexplained,
        "behavioural_identical": not behavioural_nonzero,
        "architecture_clean": not unexplained_nonzero
        and base_diag == cur_diag
        and base_assign == cur_assign
        and base_cap == cur_cap
        and not capability_diffs
        and not assignment_diffs
        and not only_baseline
        and not only_current,
        "pass": not behavioural_nonzero,
    }


def _classify_known_product_deltas(
    *,
    status_diffs,
    binding_diffs,
    intentional,
    base_mats,
    cur_mats,
) -> dict[str, Any]:
    """Classify diffs already shipped before typed-architecture (baseline 3.1.0).

    Does not waive unexplained regressions. Baseline is not updated here.
    """
    explained: set[str] = set()
    explanations: list[dict[str, Any]] = []

    for diff in binding_diffs:
        key = diff["key"]
        cur = cur_mats.get(key) or {}
        evidence = cur.get("evidence") or []
        has_uvchoice = any(
            (e.get("kind") or "").startswith("UVChoice") for e in evidence
        )
        base_slots = {s.get("param_name"): s for s in diff["baseline"]["slots"]}
        cur_slots = {s.get("param_name"): s for s in diff["current"]["slots"]}
        # UVChoice False → TEXCOORD1 vs baseline min/first TEXCOORD0.
        uv_only = (
            has_uvchoice
            and diff["baseline"]["consumed_txmp_hashes"]
            == diff["current"]["consumed_txmp_hashes"]
            and set(base_slots) == set(cur_slots)
            and all(
                base_slots[n].get("path") == cur_slots[n].get("path")
                and base_slots[n].get("role") == cur_slots[n].get("role")
                for n in base_slots
            )
            and any(
                base_slots[n].get("texcoord") != cur_slots[n].get("texcoord")
                for n in base_slots
            )
        )
        if uv_only:
            explained.add(key)
            explanations.append(
                {
                    "key": key,
                    "name": diff["name"],
                    "kind": "uvchoice_texcoord_fix",
                    "note": (
                        "Baseline 3.1.0 predated proven UVChoice_OnCh1_OffCh2 "
                        "(False->TEXCOORD1). Paths/hashes unchanged."
                    ),
                }
            )
            continue

        # OrangePeelNormal dropped from clean contract (Label_CH1).
        base_names = set(base_slots)
        cur_names = set(cur_slots)
        if "OrangePeelNormal" in (base_names - cur_names) and cur_names <= base_names:
            explained.add(key)
            explanations.append(
                {
                    "key": key,
                    "name": diff["name"],
                    "kind": "orangepeel_not_in_clean_contract",
                    "note": (
                        "OrangePeelNormal is not a CLEAN_SURFACE TXMP; baseline "
                        "incorrectly consumed it. Current leaves it unresolved -> PARTIAL."
                    ),
                }
            )

    for diff in status_diffs:
        key = diff["key"]
        if key in explained:
            explanations.append(
                {
                    "key": key,
                    "name": diff["name"],
                    "kind": "status_follow_on",
                    "baseline": diff["baseline"],
                    "current": diff["current"],
                    "note": "Status shift follows explained binding/contract change.",
                }
            )

    # Unresolved-hash follow-on for OrangePeelNormal NameHash 0x8C7FDE22.
    for key in list(explained):
        b = base_mats.get(key) or {}
        c = cur_mats.get(key) or {}
        if tuple(b.get("unresolved_semantics") or ()) != tuple(
            c.get("unresolved_semantics") or ()
        ):
            explanations.append(
                {
                    "key": key,
                    "name": b.get("material_name") or key,
                    "kind": "unresolved_hash_follow_on",
                    "baseline": list(b.get("unresolved_semantics") or ()),
                    "current": list(c.get("unresolved_semantics") or ()),
                    "note": "Unresolved NameHash set follows explained contract narrowing.",
                }
            )

    return {"explained_keys": explained, "explanations": explanations}


def load_report(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def compare_report_files(baseline_path: str, current_path: str) -> dict[str, Any]:
    return compare_material_reports(
        load_report(baseline_path), load_report(current_path)
    )
