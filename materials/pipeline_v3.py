"""Blender-facing facade over the authoritative capability resolver.

Owns MaterialSpec / TextureSlot adapters for node construction and the importer.
Selection / contract resolution lives in ``resolver``; this module must not
duplicate TXMP allowlists or rebuild capability logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    BaseColorSourceKind,
    CleanSurfaceCapability,
    InvalidMaterialBinding,
    MaterialCapabilityKind,
    MaterialResolution,
    MaterialResolutionError,
    MissingMaterialProvenance,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedMaterial,
    ResolvedTextureSlot,
    UnsupportedMaterialCapability,
    make_clean_surface_capability,
)
from .resolver import MaterialCapabilityResolver, binding_uv, _alpha_mode
from .txmp_semantics import CLEAN_SURFACE_TXMP_NAMES

PIPELINE_VERSION = 4

# Back-compat aliases for diagnose / tests (semantic names, not duplicate logic).
_BASE_NAMES = frozenset({"BaseColorAlpha", "BaseColorAlpha_1"})
_ALPHA_NAMES = frozenset({"Alpha"})
_NORMAL_NAMES = frozenset({"Normal", "WeaveNormal"})
_RMAO_NAMES = frozenset({"RoughMetalAO"})
_CONTRACT_NAMES = CLEAN_SURFACE_TXMP_NAMES
_binding_uv = binding_uv


class MaterialTranslateError(MaterialResolutionError):
    """Material cannot be built from proven game data alone."""


@dataclass(frozen=True)
class TextureSlot:
    role: str
    path: str
    texcoord: str
    channel: str | None = None
    tiling: tuple[float, float] = (1.0, 1.0)
    address: dict[str, str] | None = None
    param_hash: int = 0
    param_name: str = ""
    evidence: tuple[str, ...] = ()


@dataclass
class MaterialSpec:
    """Compatibility adapter for importer caches and older call sites.

    Authoritative type is ``ResolvedMaterial``. Prefer converting at the
    ``build_material`` boundary via ``ensure_resolved_material``; this class
    must not own a second interpretation of textures / UV / alpha.
    """

    name: str
    valid: bool = False
    game_key: str = "fh6"
    shader_name: str = ""
    base_color: tuple[float, float, float, float] = (1.0, 1.0, 1.0, 1.0)
    base_color_map: TextureSlot | None = None
    alpha_map: TextureSlot | None = None
    normal_map: TextureSlot | None = None
    rmao_map: TextureSlot | None = None
    alpha_mode: str = "OPAQUE"
    alpha_threshold: float = 0.5
    error: str | None = None
    capability_kind: str | None = None

    @property
    def textures(self) -> list[TextureSlot]:
        return [
            slot
            for slot in (
                self.base_color_map,
                self.alpha_map,
                self.normal_map,
                self.rmao_map,
            )
            if slot is not None
        ]


def texture_slot_from_resolved(slot: ResolvedTextureSlot) -> TextureSlot:
    return TextureSlot(
        role=slot.role,
        path=slot.path,
        texcoord=slot.texcoord,
        channel=slot.channel,
        tiling=slot.tiling,
        address=dict(slot.address) if slot.address else None,
        param_hash=slot.param_hash,
        param_name=slot.param_name,
        evidence=tuple(ev.detail for ev in slot.evidence),
    )


def resolved_slot_from_texture_slot(slot: TextureSlot) -> ResolvedTextureSlot:
    """Lossless adapter reverse: no UV / path / tiling reinterpretation."""
    return ResolvedTextureSlot(
        role=slot.role,
        path=slot.path,
        texcoord=slot.texcoord,
        channel=slot.channel,
        tiling=slot.tiling,
        address=dict(slot.address) if slot.address else None,
        param_hash=slot.param_hash,
        param_name=slot.param_name,
        evidence=tuple(
            ProvenanceDiagnostic(kind="adapter", detail=d, source="MaterialSpec")
            for d in slot.evidence
        ),
    )


def material_spec_from_capability(
    *,
    name: str,
    game_key: str,
    shader_name: str,
    capability: CleanSurfaceCapability,
    capability_kind: str | None = None,
) -> MaterialSpec:
    return MaterialSpec(
        name=name,
        valid=True,
        game_key=game_key,
        shader_name=shader_name,
        base_color=capability.base_color,
        base_color_map=(
            texture_slot_from_resolved(capability.base_color_map)
            if capability.base_color_map is not None
            else None
        ),
        alpha_map=(
            texture_slot_from_resolved(capability.alpha_map)
            if capability.alpha_map is not None
            else None
        ),
        normal_map=(
            texture_slot_from_resolved(capability.normal_map)
            if capability.normal_map is not None
            else None
        ),
        rmao_map=(
            texture_slot_from_resolved(capability.rmao_map)
            if capability.rmao_map is not None
            else None
        ),
        alpha_mode=capability.alpha_mode,
        alpha_threshold=capability.alpha_threshold,
        capability_kind=capability_kind,
    )


def material_spec_from_resolved(resolved: ResolvedMaterial) -> MaterialSpec:
    return material_spec_from_capability(
        name=resolved.name,
        game_key=resolved.game_key,
        shader_name=resolved.shader_name,
        capability=resolved.capability,
        capability_kind=resolved.capability_kind.value,
    )


def resolved_material_from_spec(spec: MaterialSpec) -> ResolvedMaterial:
    """Convert compatibility MaterialSpec → ResolvedMaterial without reinterpretation."""
    if not spec.valid:
        raise MaterialTranslateError(spec.error or "invalid MaterialSpec adapter")
    kind_value = spec.capability_kind or MaterialCapabilityKind.CLEAN_SURFACE.value
    try:
        kind = MaterialCapabilityKind(kind_value)
    except ValueError as exc:
        raise MaterialTranslateError(
            f"unknown capability kind on MaterialSpec: {kind_value!r}"
        ) from exc
    if spec.base_color_map is not None:
        texture = resolved_slot_from_texture_slot(spec.base_color_map)
        base_source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.TEXTURE,
            texture=texture,
            evidence=(),
        )
    else:
        base_source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.MATERIAL_CONSTANT,
            color=spec.base_color,
            evidence=(),
        )
    capability = make_clean_surface_capability(
        base_color_source=base_source,
        alpha_map=(
            resolved_slot_from_texture_slot(spec.alpha_map)
            if spec.alpha_map is not None
            else None
        ),
        normal_map=(
            resolved_slot_from_texture_slot(spec.normal_map)
            if spec.normal_map is not None
            else None
        ),
        rmao_map=(
            resolved_slot_from_texture_slot(spec.rmao_map)
            if spec.rmao_map is not None
            else None
        ),
        alpha_mode=spec.alpha_mode,
        alpha_threshold=spec.alpha_threshold,
        evidence=(),
    )
    return ResolvedMaterial(
        name=spec.name,
        game_key=spec.game_key,
        shader_name=spec.shader_name,
        capability_kind=kind,
        capability=capability,
    )


def _raise_from_resolution(result: MaterialResolution) -> None:
    exc = result.failure_exception
    if isinstance(exc, MaterialTranslateError):
        raise exc
    if isinstance(
        exc,
        (
            UnsupportedMaterialCapability,
            InvalidMaterialBinding,
            MissingMaterialProvenance,
            MaterialResolutionError,
        ),
    ):
        raise MaterialTranslateError(str(exc)) from exc
    reasons = result.probe.rejection_reasons
    msg = reasons[0] if reasons else "unsupported material capability"
    raise MaterialTranslateError(msg)


class CleanMaterialBuilder:
    """Facade: resolve via MaterialCapabilityResolver, return MaterialSpec."""

    def __init__(self, media_root: str | None = None, game_key: str = "fh6"):
        try:
            self._resolver = MaterialCapabilityResolver(
                media_root=media_root, game_key=game_key
            )
        except MaterialResolutionError as exc:
            raise MaterialTranslateError(str(exc)) from exc
        self.game_key = self._resolver.game_key
        self.media_root = self._resolver.media_root
        self.stock_paint_rgba = None

    @property
    def stock_paint_rgba(self):
        return self._resolver.stock_paint_rgba

    @stock_paint_rgba.setter
    def stock_paint_rgba(self, value):
        self._resolver.stock_paint_rgba = value

    def _media(self, resolver) -> str:
        return self._resolver._media(resolver)

    def resolve(self, name, material, resolver=None) -> MaterialResolution:
        return self._resolver.resolve(name=name, material=material, resolver=resolver)

    def build(self, name, material, resolver=None) -> MaterialSpec:
        result = self.resolve(name, material, resolver=resolver)
        if not result.is_selected or result.resolved is None:
            _raise_from_resolution(result)
        return material_spec_from_resolved(result.resolved)
