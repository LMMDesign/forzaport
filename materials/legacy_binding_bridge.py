"""Legacy TextureBinding merge bridge — not for contracted production SHAs.

``PassMergeSpec`` / ``_merge_pass_sites`` / register-skip semantics live here
only. Contracted shaderbin SHAs must never enter this module for production
routing; tests assert ``compatibility_bridge_usage_for_contracted_shas == 0``.
"""

from __future__ import annotations

from typing import Callable

from .pass_contracts import load_shader_pass_contract

# Call counters for cutover metrics / tests.
_legacy_calls_total = 0
_legacy_calls_contracted = 0
_legacy_call_log: list[dict] = []


def reset_legacy_bridge_metrics() -> None:
    global _legacy_calls_total, _legacy_calls_contracted, _legacy_call_log
    _legacy_calls_total = 0
    _legacy_calls_contracted = 0
    _legacy_call_log = []


def legacy_bridge_metrics() -> dict:
    return {
        "legacy_calls_total": _legacy_calls_total,
        "compatibility_bridge_usage_for_contracted_shas": _legacy_calls_contracted,
        "call_log": list(_legacy_call_log),
    }


def is_contracted_shaderbin_sha(shaderbin_sha256: str | None) -> bool:
    if not shaderbin_sha256:
        return False
    return load_shader_pass_contract(shaderbin_sha256) is not None


class LegacyCompatibilityBridgeError(RuntimeError):
    """Contracted SHA attempted the legacy TextureBinding merge path."""

    def __init__(
        self,
        *,
        shaderbin_sha256: str,
        material_instance_key: str | None,
        entry_point: str,
        detail: str = "",
    ):
        self.shaderbin_sha256 = shaderbin_sha256
        self.material_instance_key = material_instance_key
        self.entry_point = entry_point
        msg = (
            f"LEGACY_COMPATIBILITY_VIEW forbidden for contracted SHA "
            f"{shaderbin_sha256[:16]}… entry={entry_point}"
        )
        if material_instance_key:
            msg += f" instance={material_instance_key}"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg)


def assert_legacy_bridge_allowed(
    *,
    shaderbin_sha256: str | None,
    entry_point: str,
    material_instance_key: str | None = None,
) -> None:
    """Fail closed when a contracted SHA reaches the legacy merge bridge."""
    global _legacy_calls_total, _legacy_calls_contracted, _legacy_call_log
    _legacy_calls_total += 1
    _legacy_call_log.append(
        {
            "shaderbin_sha256": shaderbin_sha256,
            "entry_point": entry_point,
            "material_instance_key": material_instance_key,
            "contracted": is_contracted_shaderbin_sha(shaderbin_sha256),
        }
    )
    if is_contracted_shaderbin_sha(shaderbin_sha256):
        _legacy_calls_contracted += 1
        raise LegacyCompatibilityBridgeError(
            shaderbin_sha256=shaderbin_sha256 or "",
            material_instance_key=material_instance_key,
            entry_point=entry_point,
            detail="missing contract/evaluation state — use EvaluatedMaterialSampleSites",
        )


def legacy_merge_pass_sites(
    merge_fn: Callable[..., None],
    *,
    shaderbin_sha256: str | None,
    entry_point: str = "legacy_merge_pass_sites",
    material_instance_key: str | None = None,
    **kwargs,
) -> None:
    """Invoke ``_merge_pass_sites`` only for non-contracted / diagnostic paths."""
    assert_legacy_bridge_allowed(
        shaderbin_sha256=shaderbin_sha256,
        entry_point=entry_point,
        material_instance_key=material_instance_key,
    )
    merge_fn(**kwargs)
