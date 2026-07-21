"""Pass / variant identity — never truncate scenario names; never key by scenario alone.

A pass identity always includes exact shaderbin SHA, full archive member path,
variant directory, scenario name, stage, and PSO SHA-256.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass


_PSO_SUFFIX = re.compile(r"\.pcdxil\.(pso|vso)$", re.I)


@dataclass(frozen=True)
class PassIdentity:
    """Unique identity for one PSO/VSO archive member."""

    shaderbin_sha256: str
    archive_member: str  # full zip-internal path, forward slashes
    variant: str  # e.g. "_Standard", "_DXRSimpleHit_Base", "_Standard_L", ""
    scenario: str  # e.g. "CarLightScenario" (no shader-name prefix)
    stage: str  # "ps" | "vs"
    pso_sha256: str
    shader_name: str = ""

    def as_key(self) -> str:
        return (
            f"{self.shaderbin_sha256[:16]}|"
            f"{self.variant or 'root'}|"
            f"{self.scenario}|"
            f"{self.stage}|"
            f"{self.pso_sha256[:16]}"
        )


def variant_from_member(member: str) -> str:
    """Return technique/variant directory, or '' for root members."""
    norm = member.replace("\\", "/").strip("/")
    parts = norm.split("/")
    if len(parts) >= 2:
        return parts[0]
    return ""


def stage_from_member(member: str) -> str:
    low = member.replace("\\", "/").lower()
    if low.endswith(".pcdxil.vso"):
        return "vs"
    if low.endswith(".pcdxil.pso"):
        return "ps"
    return "unknown"


def scenario_from_member(member: str, shader_name: str) -> str:
    """Strip ``{shader_name}`` prefix from the basename stem.

    Must not chew the first character of the shader name into the scenario
    (historical bug: ``car_livery…`` → ``ar_livery…``).
    """
    base = os.path.basename(member.replace("\\", "/"))
    stem = _PSO_SUFFIX.sub("", base)
    prefix = shader_name or ""
    if prefix and stem.lower().startswith(prefix.lower()):
        rest = stem[len(prefix) :]
        return rest or "UNNAMED"
    # Fail closed on unexpected naming — return full stem rather than inventing.
    return stem


def classify_blender_relevance(scenario: str, variant: str) -> str:
    """Classify pass facts for Blender material construction."""
    s = (scenario or "").lower()
    v = (variant or "").lower()
    if "dxr" in v or "hit" in v:
        return "RAY_VISIBILITY"
    if "debug" in s:
        return "DEBUG_ONLY"
    if "shadowdepth" in s or "nops" in s:
        return "SHADOW_VISIBILITY"
    if "rtbuffer" in s:
        return "ENGINE_INTERNAL"
    if "proxylod" in s or "lod15" in s:
        return "LOD_ONLY"
    if "wheelblur" in s:
        return "ENGINE_INTERNAL"
    if "simplecarlight" in s or s.endswith("simplecarlightscenario"):
        return "VISIBILITY"
    if "fplusplus" in s:
        return "ENGINE_INTERNAL"
    if s.endswith("carlightscenario") or s == "carlightscenario":
        if v in ("", "_standard") or v.lower() == "_standard":
            return "MAIN_SURFACE_SHADING"
        if "_standard_l" in v.lower():
            return "UNRESOLVED"  # variant selection not yet proven
        return "UNRESOLVED"
    return "UNRESOLVED"


def parse_pass_identity(
    *,
    member: str,
    shader_name: str,
    shaderbin_sha256: str,
    pso_sha256: str,
) -> PassIdentity:
    return PassIdentity(
        shaderbin_sha256=shaderbin_sha256,
        archive_member=member.replace("\\", "/"),
        variant=variant_from_member(member),
        scenario=scenario_from_member(member, shader_name),
        stage=stage_from_member(member),
        pso_sha256=pso_sha256,
        shader_name=shader_name,
    )
