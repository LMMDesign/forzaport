"""TXMP parameter NameHash → Principled role, packing, and capability membership.

Authority is the FTS NameHashService string for each TXMP hash (game-authored
shader parameter names). Packing is encoded in those names (e.g. RoughMetalAO
→ R=roughness, G=metallic, B=AO). DXIL must not invent competing swizzles.

``supported_by`` is the sole allowlist for which capabilities consume a TXMP.
Broad ``role`` alone must never widen production support.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .model import MaterialCapabilityKind
from .name_hashes import MaterialNameError, require_name

_CLEAN = frozenset({MaterialCapabilityKind.CLEAN_SURFACE})


@dataclass(frozen=True)
class TxmpSemantics:
    """Role/packing for one TXMP slot. role=None means inactive (skip)."""

    role: str | None
    channel_roles: Mapping[str, str]
    evidence: str
    supported_by: frozenset[MaterialCapabilityKind] = frozenset()
    is_fx_layer: bool = False

    def supports(self, kind: MaterialCapabilityKind) -> bool:
        return kind in self.supported_by


# Exact NameHash strings → role + packing. Closed set from name_hashes.json.
_RMAO = {"roughness": "x", "metallic": "y", "ao": "z"}
_RM = {"roughness": "x", "metallic": "y"}
_AO_X = {"ao": "x"}
_OPACITY_X = {"opacity": "x"}


def _sem(
    role: str | None,
    channels: Mapping[str, str],
    evidence: str,
    *,
    clean: bool = False,
    fx: bool = False,
) -> TxmpSemantics:
    return TxmpSemantics(
        role,
        dict(channels),
        evidence,
        supported_by=_CLEAN if clean else frozenset(),
        is_fx_layer=fx,
    )


# Exact strings currently consumed by the clean-surface builder only.
_EXACT: dict[str, TxmpSemantics] = {
    "RoughMetalAO": _sem("rmao", _RMAO, "name_RoughMetalAO", clean=True),
    "BaseColorAlpha": _sem("diffuse", {}, "name_BaseColorAlpha", clean=True),
    "BaseColorAlpha_1": _sem("diffuse", {}, "name_BaseColorAlpha_1", clean=True),
    "DiffuseA": _sem("diffuse", {}, "name_DiffuseA"),
    "DiffuseATexture": _sem("diffuse", {}, "name_DiffuseATexture"),
    "DiffuseA_1": _sem("diffuse", {}, "name_DiffuseA_1"),
    "DiffuseA_2": _sem("diffuse", {}, "name_DiffuseA_2"),
    "DiffuseAndAlpha": _sem(
        "diffuse", {"opacity": "w"}, "name_DiffuseAndAlpha"
    ),
    "DiffuseAndGlossTexture": _sem(
        "diffuse", {}, "name_DiffuseAndGlossTexture"
    ),
    "CH1DiffuseTextureTexture": _sem(
        "diffuse", {}, "name_CH1DiffuseTextureTexture"
    ),
    "CH1DiffuseMap": _sem("diffuse", {}, "name_CH1DiffuseMap"),
    "CH1DiffuseMap_1": _sem("diffuse", {}, "name_CH1DiffuseMap_1"),
    "Normal": _sem("normal", {}, "name_Normal", clean=True),
    "NormalTexture": _sem("normal", {}, "name_NormalTexture"),
    "CH1NormalTexture": _sem("normal", {}, "name_CH1NormalTexture"),
    "CH2NormalTexture": _sem("normal", {}, "name_CH2NormalTexture"),
    "WeaveNormal": _sem("normal", {}, "name_WeaveNormal", clean=True),
    "ClearCoatNormal_Texture": _sem(
        "normal", {}, "name_ClearCoatNormal_Texture"
    ),
    "CH1ClearCoatNormalMap": _sem(
        "normal", {}, "name_CH1ClearCoatNormalMap"
    ),
    "CH2ClearCoatNormalMap": _sem(
        "normal", {}, "name_CH2ClearCoatNormalMap"
    ),
    "OrangePeelNormal": _sem("normal", {}, "name_OrangePeelNormal"),
    "FlakeNormalTexture": _sem("normal", {}, "name_FlakeNormalTexture"),
    "FlakeNormalTexture_1": _sem(
        "normal", {}, "name_FlakeNormalTexture_1"
    ),
    "Alpha": _sem("alpha", _OPACITY_X, "name_Alpha", clean=True),
    "LocalAO": _sem("lcao", _AO_X, "name_LocalAO"),
    "LocalAOTexture": _sem("lcao", _AO_X, "name_LocalAOTexture"),
    "DirectLocalAO": _sem("lcao", _AO_X, "name_DirectLocalAO"),
    "RoughnessMetalness": _sem("rmao", _RM, "name_RoughnessMetalness"),
    "RoughnessMetalness_1": _sem(
        "rmao", _RM, "name_RoughnessMetalness_1"
    ),
    "RoughnessMetalnessATexture": _sem(
        "rmao", _RM, "name_RoughnessMetalnessATexture"
    ),
    "NormRoughnessMetalness": _sem(
        "rmao", _RM, "name_NormRoughnessMetalness"
    ),
    "L1_NormRoughnessMetalness": _sem(
        "rmao", _RM, "name_L1_NormRoughnessMetalness"
    ),
    "L2_NormRoughnessMetalness": _sem(
        "rmao", _RM, "name_L2_NormRoughnessMetalness"
    ),
    "L4Pri_NormRoughnessMetalness": _sem(
        "rmao", _RM, "name_L4Pri_NormRoughnessMetalness"
    ),
    "GlassNormal": _sem("normal", {}, "name_GlassNormal"),
    "GlassRoughnessTexture": _sem(
        "gloss",
        {"roughness": "x", "smoothness": "0"},
        "name_GlassRoughnessTexture",
    ),
    # Present on carbon; not a Principled primary map — leave unbound.
    "WeaveMask": _sem(None, {}, "name_WeaveMask_unbound"),
    "radTexture": _sem(None, {}, "name_radTexture_unbound"),
    "radTexture_1": _sem(None, {}, "name_radTexture_1_unbound"),
    "radTextureTexture_pg_radiosity": _sem(
        None, {}, "name_radTexture_radiosity_unbound"
    ),
}

CLEAN_SURFACE_TXMP_NAMES = frozenset(
    name for name, sem in _EXACT.items() if sem.supports(MaterialCapabilityKind.CLEAN_SURFACE)
)

# Game-authored weather/damage layer markers in parameter names (FTS strings).
_FX_MARKERS = (
    "surfacefx",
    "rainstreak",
    "mudtile",
    "mudfx",
    "snowdiffuse",
    "snowhigh",
    "snowlow",
    "texlowfrequency",
    "texturewetness",
    "normalmapwithintensity",
    "normalmap00",
    "normalmap0texture",
)


def _is_fx_layer_name(name: str) -> bool:
    low = name.lower()
    return any(m in low for m in _FX_MARKERS)


def semantics_for_txmp_hash(h: int, *, context: str = "") -> TxmpSemantics:
    """Resolve TXMP hash → role/packing. FX layers are inactive (role=None)."""
    name = require_name(h, context=context or "TXMP")
    if name in _EXACT:
        return _EXACT[name]
    if _is_fx_layer_name(name):
        return TxmpSemantics(
            None, {}, f"fx_layer_inactive:{name}", is_fx_layer=True
        )
    # Unknown non-FX: leave unbound (do not invent role from DXIL).
    return TxmpSemantics(None, {}, f"unbound_unknown_txmp:{name}")


def try_semantics_for_txmp_hash(h: int) -> TxmpSemantics | None:
    """Like semantics_for_txmp_hash but returns None when NameHash is missing."""
    try:
        return semantics_for_txmp_hash(h)
    except MaterialNameError:
        return None
