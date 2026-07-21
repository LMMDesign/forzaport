"""Proposed Forza Material IR (Milestone A scaffolding).

Blender-independent expression types for Phase 3+. Not wired into production
resolve or node construction yet — Milestone A change-control.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from .model import ProvenanceDiagnostic
from .texture_source import ResolvedTextureSource


@dataclass(frozen=True)
class ShaderIdentity:
    shader_name: str
    archive_path: str
    shaderbin_sha256: str
    permutation: str  # e.g. CarLightScenario


# --- UV expressions ---------------------------------------------------------


@dataclass(frozen=True)
class MeshUV:
    index: int
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ScaleUV:
    source: "UVExpression"
    scale: tuple[float, float]
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class OffsetUV:
    source: "UVExpression"
    offset: tuple[float, float]
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class RotateUV:
    """Rotate UV about origin by ``degrees`` (car_carbonfiber weave transform)."""

    source: "UVExpression"
    degrees: float
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class SelectUV:
    condition: "MaterialExpression"
    a: "UVExpression"
    b: "UVExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class GeneratedCoordinate:
    kind: str
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ComponentSwizzle:
    source: "UVExpression"
    components: tuple[str, ...]
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


UVExpression = Union[
    MeshUV,
    ScaleUV,
    OffsetUV,
    RotateUV,
    SelectUV,
    GeneratedCoordinate,
    ComponentSwizzle,
]


@dataclass(frozen=True)
class SamplerState:
    address_u: str = "REPEAT"
    address_v: str = "REPEAT"
    filter: str = "LINEAR"


@dataclass(frozen=True)
class TextureSampleExpression:
    binding_name_hash: int
    source: ResolvedTextureSource
    uv: UVExpression
    channels: tuple[str, ...]
    color_space: str
    sampler: SamplerState
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    # Exact sample-site identity — do not collapse same-register sites.
    sample_site_id: str | None = None
    sample_site_key: str | None = None
    shaderbin_sha256: str | None = None
    archive_member: str | None = None
    pso_sha256: str | None = None
    pass_name: str | None = None
    variant: str | None = None
    texture_register: int | None = None
    sampler_register: int | None = None


# --- Material expression DAG ------------------------------------------------


@dataclass(frozen=True)
class ConstantColor:
    rgba: tuple[float, float, float, float]
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ConstantScalar:
    value: float
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class TextureSample:
    sample: TextureSampleExpression
    sample_site_id: str | None = None


@dataclass(frozen=True)
class Channel:
    source: "MaterialExpression"
    channel: str
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Multiply:
    a: "MaterialExpression"
    b: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Add:
    a: "MaterialExpression"
    b: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Subtract:
    a: "MaterialExpression"
    b: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Mix:
    a: "MaterialExpression"
    b: "MaterialExpression"
    factor: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Clamp:
    source: "MaterialExpression"
    lo: float
    hi: float
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ShadingAttenuation:
    """Lighting/surface energy modulation — NOT visibility / Principled Alpha.

    For car_standard AlphaTransparency=false, DXIL uses
    saturate(Alpha.r * BaseColorAlpha.a) on selected lighting terms.
    Blender may approximate this via Base Color multiply; that is a labelled
    backend approximation, not exact BRDF equivalence.
    """

    expression: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class NormalDecode:
    source: "MaterialExpression"
    # Principled Normal Map "Strength" (car_carbonfiber WeaveNormal_Intensity,
    # CB reg15.w). Default 1.0 preserves prior car_standard behaviour exactly.
    strength: float = 1.0
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class NormalBlend:
    base: "MaterialExpression"
    detail: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class Select:
    condition: "MaterialExpression"
    if_true: "MaterialExpression"
    if_false: "MaterialExpression"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


MaterialExpression = Union[
    ConstantColor,
    ConstantScalar,
    TextureSample,
    Channel,
    Multiply,
    Add,
    Subtract,
    Mix,
    Clamp,
    NormalDecode,
    NormalBlend,
    Select,
]


@dataclass(frozen=True)
class RasterState:
    blend_enable: bool | None = None
    cull_mode: str | None = None
    depth_write: bool | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ForzaMaterialIR:
    """Canonical evaluated material — Blender compiles this only (Phase 8)."""

    shader: ShaderIdentity
    base_color: MaterialExpression | None = None
    normal: MaterialExpression | None = None
    roughness: MaterialExpression | None = None
    metallic: MaterialExpression | None = None
    ambient_occlusion: MaterialExpression | None = None
    # Deprecated dual field: prefer alpha_semantics.main_visibility.expression.
    # Retained as the authored visibility/mask scalar expression only (no
    # threshold Clamp encoding).
    opacity: MaterialExpression | None = None
    # Lighting attenuation (opaque); never auto-wires to Principled Alpha.
    shading_attenuation: ShadingAttenuation | None = None
    # Explicit game-file alpha IR + Blender plan (Alpha Closure).
    alpha_semantics: object | None = None  # AlphaSemanticsIR | None
    blender_alpha_plan: object | None = None  # BlenderAlphaPlan | None
    emissive: MaterialExpression | None = None
    clearcoat: MaterialExpression | None = None
    clearcoat_roughness: MaterialExpression | None = None
    raster_state: RasterState | None = None
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    rejection_reasons: tuple[str, ...] = ()


class ContractStatus(Enum):
    PROPOSED = "proposed"
    PARTIAL = "partial"
    PROVEN = "proven"
    BLOCKED = "blocked"
