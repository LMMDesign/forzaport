"""Blender-independent material capability models (authoritative types).

Dependency sink: capabilities / txmp_semantics / resolver / diagnostics import from
here. Does not import bpy or diagnostic report UI types.

Capability lifecycle (B): attempting construction of a complete typed capability
payload constitutes selection. A successful ``CleanSurfaceCapability`` is the
selected capability; there is no second approval stage that can disagree with it.
``CapabilityProbeResult`` mirrors that payload for diagnostics (evidence / rejection).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class MaterialCapabilityKind(Enum):
    """Registered material capabilities. Expand only with full typed payloads."""

    CLEAN_SURFACE = "clean_v3.base_alpha_normal_rmao"


class MaterialResolutionError(RuntimeError):
    """Base error for capability resolution (not Blender construction)."""


class UnsupportedMaterialCapability(MaterialResolutionError):
    """No complete typed capability could be selected."""


class MissingMaterialProvenance(MaterialResolutionError):
    """Required NameHash / TXMP provenance is absent."""


class InvalidMaterialBinding(MaterialResolutionError):
    """DXIL / PSO binding extraction failed."""


class InconsistentMaterialResolution(MaterialResolutionError):
    """Illegal MaterialResolution combination (invariant violation)."""


@dataclass(frozen=True)
class ProvenanceDiagnostic:
    """Evidence record attached to resolved inputs or capability selection."""

    kind: str
    detail: str
    source: str = ""


@dataclass(frozen=True)
class ResolvedTextureSlot:
    """One proven texture sample binding for a capability payload.

    ``path`` remains the original MatI/TXMP GAME path. Optional source-identity
    fields (filled by the texture source layer) identify exact bytes without
    the node builder re-searching the game tree.
    """

    role: str
    path: str
    texcoord: str
    channel: str | None = None
    tiling: tuple[float, float] = (1.0, 1.0)
    address: Mapping[str, str] | None = None
    param_hash: int = 0
    param_name: str = ""
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    # car_carbonfiber weave UV transform (rotate about origin, then pan/offset).
    # Defaults preserve exact prior behaviour for every other family.
    rotation_degrees: float = 0.0
    pan: tuple[float, float] = (0.0, 0.0)
    # Typed source identity (optional; filled at resolve/diagnose time)
    source_kind: str | None = None
    canonical_path: str | None = None
    filesystem_path: str | None = None
    archive_path: str | None = None
    archive_member: str | None = None


class TextureBindingActivation(Enum):
    """Whether a physically resolved TXMP contributes to shading output."""

    ACTIVE = "active"
    INACTIVE_PLACEHOLDER = "inactive_placeholder"
    CONDITIONAL_UNRESOLVED = "conditional_unresolved"
    UNSUPPORTED_SEMANTIC = "unsupported_semantic"


@dataclass(frozen=True)
class TextureBindingDecision:
    """Presence vs use for one recognised TXMP binding."""

    slot: ResolvedTextureSlot
    activation: TextureBindingActivation
    reason: str
    evidence: tuple[ProvenanceDiagnostic, ...] = ()
    controlling_parameters: tuple[int, ...] = ()


class BaseColorSourceKind(Enum):
    """Authoritative Base Color provenance for clean-surface materials."""

    TEXTURE = "texture"
    MATERIAL_CONSTANT = "material_constant"
    INSTANCE_PAINT = "instance_paint"
    LIVERY_COMPOSITE = "livery_composite"
    WEAVE_COMPOSITE = "weave_composite"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ResolvedWeaveComposite:
    """Proven WeaveMask lerp of WeaveColorTintA/B (car_carbonfiber family)."""

    tint_a: tuple[float, float, float, float]
    tint_b: tuple[float, float, float, float]
    mask: ResolvedTextureSlot
    # DXIL: rgb = A + WeaveMask.R * (B - A)
    blend: str = "lerp_a_b_mask_r"
    evidence: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class ResolvedBaseColorSource:
    """Explicit Base Color selection — not an optional texture + fallback colour."""

    kind: BaseColorSourceKind
    texture: ResolvedTextureSlot | None = None
    color: tuple[float, float, float, float] | None = None
    weave: ResolvedWeaveComposite | None = None
    # Optional BaseColor_Tint×Multiplier (car_standard DXIL). None = no tint multiply.
    multiply_tint: tuple[float, float, float, float] | None = None
    # When set with RMAO: "lerp_tinted_tex_metal" (TintMode.y≈1) or
    # "lerp_tex_tinted_metal" (TintMode.y≈0). None = tinted path only.
    tint_metal_blend: str | None = None
    # Opaque AlphaTransparency=false: multiply Base Color by Alpha.r*BC.a
    # (lighting-coverage stand-in). Never enables Principled cutout by itself.
    multiply_coverage: bool = False
    evidence: tuple[ProvenanceDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        kind = self.kind
        if kind is BaseColorSourceKind.TEXTURE:
            if self.texture is None or self.color is not None or self.weave is not None:
                raise ValueError("TEXTURE requires texture only")
        elif kind is BaseColorSourceKind.MATERIAL_CONSTANT:
            if self.color is None or self.texture is not None or self.weave is not None:
                raise ValueError("MATERIAL_CONSTANT requires color only")
            if (
                self.multiply_tint is not None
                or self.tint_metal_blend is not None
                or self.multiply_coverage
            ):
                raise ValueError("MATERIAL_CONSTANT cannot carry texture tint fields")
        elif kind is BaseColorSourceKind.INSTANCE_PAINT:
            if self.color is None or self.texture is not None or self.weave is not None:
                raise ValueError("INSTANCE_PAINT requires color only")
            if (
                self.multiply_tint is not None
                or self.tint_metal_blend is not None
                or self.multiply_coverage
            ):
                raise ValueError("INSTANCE_PAINT cannot carry texture tint fields")
        elif kind is BaseColorSourceKind.LIVERY_COMPOSITE:
            raise ValueError("LIVERY_COMPOSITE is not implemented for clean surface")
        elif kind is BaseColorSourceKind.WEAVE_COMPOSITE:
            if self.weave is None or self.texture is not None:
                raise ValueError("WEAVE_COMPOSITE requires weave only")
            if (
                self.multiply_tint is not None
                or self.tint_metal_blend is not None
                or self.multiply_coverage
            ):
                raise ValueError("WEAVE_COMPOSITE cannot carry texture tint fields")
        elif kind is BaseColorSourceKind.UNRESOLVED:
            if self.texture is not None or self.color is not None or self.weave is not None:
                raise ValueError("UNRESOLVED must not invent texture or color")
            if (
                self.multiply_tint is not None
                or self.tint_metal_blend is not None
                or self.multiply_coverage
            ):
                raise ValueError("UNRESOLVED must not invent tint fields")


def base_color_mirrors(
    source: ResolvedBaseColorSource,
) -> tuple[tuple[float, float, float, float], ResolvedTextureSlot | None]:
    """Compatibility mirrors for graph helpers that still read flat fields."""
    if source.kind is BaseColorSourceKind.TEXTURE:
        assert source.texture is not None
        return (1.0, 1.0, 1.0, 1.0), source.texture
    if source.kind in (
        BaseColorSourceKind.MATERIAL_CONSTANT,
        BaseColorSourceKind.INSTANCE_PAINT,
    ):
        assert source.color is not None
        return source.color, None
    if source.kind is BaseColorSourceKind.WEAVE_COMPOSITE:
        assert source.weave is not None
        # When A==B the constant equals the composite; otherwise tint_a is
        # only a diagnostic mirror — nodes must consume ``weave``.
        return source.weave.tint_a, None
    return (1.0, 1.0, 1.0, 1.0), None


@dataclass(frozen=True)
class CleanSurfaceCapability:
    """Complete clean v3 Base/Alpha/Normal/RMAO (+ paint/weave) contract payload.

    ``base_color_source`` is authoritative. ``base_color`` / ``base_color_map``
    are mirrors derived from that source for back-compat (TEXTURE map only).
    """

    base_color_source: ResolvedBaseColorSource
    base_color: tuple[float, float, float, float]
    base_color_map: ResolvedTextureSlot | None
    alpha_map: ResolvedTextureSlot | None
    normal_map: ResolvedTextureSlot | None
    rmao_map: ResolvedTextureSlot | None
    alpha_mode: str
    alpha_threshold: float
    evidence: tuple[ProvenanceDiagnostic, ...]
    texture_binding_decisions: tuple[TextureBindingDecision, ...] = ()
    # Principled Normal Map "Strength" (car_carbonfiber WeaveNormal_Intensity).
    # Default 1.0 preserves prior behaviour for every other family.
    normal_strength: float = 1.0
    # AlphaTransparency=true: cutout/blend factor is saturate(Alpha.r×BC.a),
    # not Alpha.r alone (door-tag decals use flat white Alpha + BC.a mask).
    alpha_cutout_uses_bc_a_product: bool = False

    def __post_init__(self) -> None:
        mirrored_color, mirrored_map = base_color_mirrors(self.base_color_source)
        if self.base_color_source.kind is BaseColorSourceKind.TEXTURE:
            if self.base_color_map is not self.base_color_source.texture:
                raise ValueError("base_color_map must be the ACTIVE texture source")
        elif self.base_color_map is not None:
            raise ValueError("non-TEXTURE base source must not set base_color_map")
        if self.base_color_source.kind in (
            BaseColorSourceKind.MATERIAL_CONSTANT,
            BaseColorSourceKind.INSTANCE_PAINT,
        ):
            if self.base_color != mirrored_color:
                raise ValueError("base_color must mirror constant/paint source")
        if self.base_color_source.kind is BaseColorSourceKind.UNRESOLVED:
            raise ValueError("CleanSurfaceCapability cannot carry UNRESOLVED base source")


def make_clean_surface_capability(
    *,
    base_color_source: ResolvedBaseColorSource,
    alpha_map: ResolvedTextureSlot | None,
    normal_map: ResolvedTextureSlot | None,
    rmao_map: ResolvedTextureSlot | None,
    alpha_mode: str,
    alpha_threshold: float,
    evidence: tuple[ProvenanceDiagnostic, ...],
    texture_binding_decisions: tuple[TextureBindingDecision, ...] = (),
    normal_strength: float = 1.0,
    alpha_cutout_uses_bc_a_product: bool = False,
) -> CleanSurfaceCapability:
    color, mapa = base_color_mirrors(base_color_source)
    return CleanSurfaceCapability(
        base_color_source=base_color_source,
        base_color=color,
        base_color_map=mapa,
        alpha_map=alpha_map,
        normal_map=normal_map,
        rmao_map=rmao_map,
        alpha_mode=alpha_mode,
        alpha_threshold=alpha_threshold,
        evidence=evidence,
        texture_binding_decisions=texture_binding_decisions,
        normal_strength=normal_strength,
        alpha_cutout_uses_bc_a_product=alpha_cutout_uses_bc_a_product,
    )

@dataclass(frozen=True)
class ResolvedMaterial:
    """Authoritative resolution product before Blender node construction."""

    name: str
    game_key: str
    shader_name: str
    capability_kind: MaterialCapabilityKind
    capability: CleanSurfaceCapability


@dataclass(frozen=True)
class CapabilityProbeResult:
    """Diagnostic mirror of selection (payload required when selected).

    When selected, ``kind`` and ``capability`` must be the same objects/
    values as on ``ResolvedMaterial`` — never an independent approval.
    """

    kind: MaterialCapabilityKind | None
    capability: CleanSurfaceCapability | None
    evidence: tuple[ProvenanceDiagnostic, ...]
    rejection_reasons: tuple[str, ...] = ()

    @property
    def selected(self) -> bool:
        return (
            self.kind is not None
            and self.capability is not None
            and not self.rejection_reasons
        )


def _validate_resolution(
    *,
    resolved: ResolvedMaterial | None,
    probe: CapabilityProbeResult,
) -> None:
    if resolved is not None:
        if probe.kind is None:
            raise InconsistentMaterialResolution(
                "resolved is set but probe.kind is None"
            )
        if probe.capability is None:
            raise InconsistentMaterialResolution(
                "resolved is set but probe.capability is None"
            )
        if probe.kind is not resolved.capability_kind:
            raise InconsistentMaterialResolution(
                "probe.kind != resolved.capability_kind"
            )
        if probe.capability is not resolved.capability:
            raise InconsistentMaterialResolution(
                "probe.capability is not resolved.capability"
            )
        if probe.rejection_reasons:
            raise InconsistentMaterialResolution(
                "resolved is set but probe has rejection_reasons"
            )
    else:
        if probe.capability is not None:
            raise InconsistentMaterialResolution(
                "resolved is None but probe.capability is set"
            )
        if probe.kind is not None and not probe.rejection_reasons:
            # Kind without payload and without rejection is incomplete.
            raise InconsistentMaterialResolution(
                "resolved is None but probe.kind is set without rejection"
            )


@dataclass(frozen=True)
class MaterialResolution:
    """Single resolve operation: typed material or structured unresolved state.

    Prefer ``selected`` / ``rejected`` factories. Direct construction still
    validates invariants in ``__post_init__``.

    ``evaluation_context`` carries the once-built game-file evaluation used by
    IR evaluators (bindings / sample sites). Capability fields remain derived
    compatibility views for contracted SHAs.
    """

    resolved: ResolvedMaterial | None
    probe: CapabilityProbeResult
    contract_errors: tuple[str, ...] = ()
    consumed_txmp_hashes: frozenset[int] = frozenset()
    bindings: object | None = None
    failure_exception: BaseException | None = None
    texture_binding_decisions: tuple[TextureBindingDecision, ...] = ()
    evaluation_context: object | None = None

    def __post_init__(self) -> None:
        _validate_resolution(resolved=self.resolved, probe=self.probe)

    @property
    def is_selected(self) -> bool:
        return self.resolved is not None and self.probe.selected

    @classmethod
    def selected(
        cls,
        resolved: ResolvedMaterial,
        *,
        evidence: tuple[ProvenanceDiagnostic, ...] | None = None,
        contract_errors: tuple[str, ...] = (),
        consumed_txmp_hashes: frozenset[int] = frozenset(),
        bindings: object | None = None,
        texture_binding_decisions: tuple[TextureBindingDecision, ...] = (),
        evaluation_context: object | None = None,
    ) -> MaterialResolution:
        """Successful construction of a typed capability = selection."""
        ev = (
            evidence
            if evidence is not None
            else resolved.capability.evidence
        )
        probe = CapabilityProbeResult(
            kind=resolved.capability_kind,
            capability=resolved.capability,
            evidence=tuple(ev),
            rejection_reasons=(),
        )
        decisions = texture_binding_decisions or getattr(
            resolved.capability, "texture_binding_decisions", ()
        )
        return cls(
            resolved=resolved,
            probe=probe,
            contract_errors=tuple(contract_errors),
            consumed_txmp_hashes=frozenset(consumed_txmp_hashes),
            bindings=bindings,
            failure_exception=None,
            texture_binding_decisions=tuple(decisions),
            evaluation_context=evaluation_context,
        )

    @classmethod
    def rejected(
        cls,
        *,
        reasons: tuple[str, ...] | list[str],
        evidence: tuple[ProvenanceDiagnostic, ...] = (),
        contract_errors: tuple[str, ...] = (),
        consumed_txmp_hashes: frozenset[int] = frozenset(),
        bindings: object | None = None,
        failure_exception: BaseException | None = None,
        texture_binding_decisions: tuple[TextureBindingDecision, ...] = (),
        evaluation_context: object | None = None,
    ) -> MaterialResolution:
        """No complete typed capability — unresolved for diagnostics."""
        reason_tuple = tuple(reasons)
        if not reason_tuple:
            raise InconsistentMaterialResolution(
                "rejected() requires at least one rejection reason"
            )
        probe = CapabilityProbeResult(
            kind=None,
            capability=None,
            evidence=tuple(evidence),
            rejection_reasons=reason_tuple,
        )
        return cls(
            resolved=None,
            probe=probe,
            contract_errors=tuple(contract_errors),
            consumed_txmp_hashes=frozenset(consumed_txmp_hashes),
            bindings=bindings,
            failure_exception=failure_exception,
            texture_binding_decisions=tuple(texture_binding_decisions),
            evaluation_context=evaluation_context,
        )
