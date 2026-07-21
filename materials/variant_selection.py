"""Variant selection from MatI / shader defaults (exact SHA).

Do not assume ``_Standard`` from the directory name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Exact corpus SHAs.
CAR_AUTOMOTIVE_PAINT_SHA256 = (
    "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3"
)
CAR_TIRES_PG_SHA256 = (
    "0b4530398a6cd3409819a3a9289532bf3c52d18689224121dcad986dd6870270"
)

# CBMP / NameHash: SimpleHit / Legacy (decoded names; VariantConstant_* in XML).
SIMPLE_HIT_HASH = 0xC0AA4C88
LEGACY_HASH = 0xDA0D7DC4


@dataclass(frozen=True)
class VariantResolution:
    shaderbin_sha256: str
    parameter_hash: int | None
    parameter_name: str | None
    raw_value: Any
    decoded_variant: str | None
    archive_directory: str | None
    status: str  # PROVEN | REJECTED | NOT_APPLICABLE
    provenance: str
    evidence: tuple[str, ...] = ()


def _param(params: dict, h: int):
    p = params.get(h)
    if p is None:
        p = params.get(h & 0xFFFFFFFF)
    return p


def _as_int(p) -> int | None:
    if p is None:
        return None
    t = getattr(p, "type", None)
    v = getattr(p, "value", None)
    if t == 3:
        return 1 if bool(v) else 0
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return int(v)
    return None


def resolve_shader_variant(
    *,
    shaderbin_sha256: str | None,
    params: dict,
) -> VariantResolution:
    """Select archive technique directory from proven MatI/CB constants."""
    if not shaderbin_sha256:
        return VariantResolution(
            shaderbin_sha256="",
            parameter_hash=None,
            parameter_name=None,
            raw_value=None,
            decoded_variant=None,
            archive_directory=None,
            status="REJECTED",
            provenance="missing_shaderbin_sha",
            evidence=("unknown SHA — fail closed",),
        )

    if shaderbin_sha256 == CAR_AUTOMOTIVE_PAINT_SHA256:
        p = _param(params, SIMPLE_HIT_HASH)
        val = _as_int(p)
        if val is None:
            return VariantResolution(
                shaderbin_sha256=shaderbin_sha256,
                parameter_hash=SIMPLE_HIT_HASH,
                parameter_name="SimpleHit",
                raw_value=None,
                decoded_variant=None,
                archive_directory=None,
                status="REJECTED",
                provenance="SimpleHit absent from MatI/defaults",
                evidence=(
                    "shaderbin.xml VariantProperty SimpleHit → "
                    "_Standard (Off) / _DXRSimpleHit_Base (On); "
                    "refusing to assume _Standard without MatI value",
                ),
            )
        if val == 0:
            return VariantResolution(
                shaderbin_sha256=shaderbin_sha256,
                parameter_hash=SIMPLE_HIT_HASH,
                parameter_name="SimpleHit",
                raw_value=val,
                decoded_variant="_Standard",
                archive_directory="_Standard",
                status="PROVEN",
                provenance="MatI/default SimpleHit=0 → ExportVariant SimpleHit=Off",
                evidence=(
                    "DXIL/XML: VariantConstant_SimpleHit Off → _Standard/",
                    f"0x{SIMPLE_HIT_HASH:08X}=0",
                ),
            )
        return VariantResolution(
            shaderbin_sha256=shaderbin_sha256,
            parameter_hash=SIMPLE_HIT_HASH,
            parameter_name="SimpleHit",
            raw_value=val,
            decoded_variant="_DXRSimpleHit_Base",
            archive_directory="_DXRSimpleHit_Base",
            status="PROVEN",
            provenance="MatI/default SimpleHit!=0 → ExportVariant SimpleHit=On",
            evidence=(
                "DXIL/XML: VariantConstant_SimpleHit On → _DXRSimpleHit_Base/",
                f"0x{SIMPLE_HIT_HASH:08X}={val}",
            ),
        )

    if shaderbin_sha256 == CAR_TIRES_PG_SHA256:
        p = _param(params, LEGACY_HASH)
        val = _as_int(p)
        if val is None:
            return VariantResolution(
                shaderbin_sha256=shaderbin_sha256,
                parameter_hash=LEGACY_HASH,
                parameter_name="Legacy",
                raw_value=None,
                decoded_variant=None,
                archive_directory=None,
                status="REJECTED",
                provenance="Legacy absent from MatI/defaults",
                evidence=(
                    "shaderbin.xml ExportVariantOption Legacy → "
                    "_Standard (Off) / _Standard_L (On); refuse default guess",
                ),
            )
        if val == 0:
            return VariantResolution(
                shaderbin_sha256=shaderbin_sha256,
                parameter_hash=LEGACY_HASH,
                parameter_name="Legacy",
                raw_value=val,
                decoded_variant="_Standard",
                archive_directory="_Standard",
                status="PROVEN",
                provenance="MatI Legacy=0 → _Standard/",
                evidence=(f"0x{LEGACY_HASH:08X}=0",),
            )
        return VariantResolution(
            shaderbin_sha256=shaderbin_sha256,
            parameter_hash=LEGACY_HASH,
            parameter_name="Legacy",
            raw_value=val,
            decoded_variant="_Standard_L",
            archive_directory="_Standard_L",
            status="PROVEN",
            provenance="MatI Legacy=1 → _Standard_L/",
            evidence=(
                f"0x{LEGACY_HASH:08X}={val}",
                "SimpleCar t19–t25 Legacy* maps appear on _Standard_L only",
            ),
        )

    return VariantResolution(
        shaderbin_sha256=shaderbin_sha256,
        parameter_hash=None,
        parameter_name=None,
        raw_value=None,
        decoded_variant="root_or_single",
        archive_directory="",
        status="NOT_APPLICABLE",
        provenance="no multi-variant contract for this SHA",
        evidence=(),
    )
