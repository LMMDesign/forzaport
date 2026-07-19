"""TXMP parameter NameHash → Principled role and channel packing.

Authority is the FTS NameHashService string for each TXMP hash (game-authored
shader parameter names). Packing is encoded in those names (e.g. RoughMetalAO
→ R=roughness, G=metallic, B=AO). DXIL must not invent competing swizzles.
"""

from __future__ import annotations

from dataclasses import dataclass

from .name_hashes import MaterialNameError, require_name


@dataclass(frozen=True)
class TxmpSemantics:
    """Role/packing for one TXMP slot. role=None means inactive (skip)."""

    role: str | None
    channel_roles: dict[str, str]
    evidence: str
    is_fx_layer: bool = False


# Exact NameHash strings → role + packing. Closed set from name_hashes.json.
_RMAO = {"roughness": "x", "metallic": "y", "ao": "z"}
_RM = {"roughness": "x", "metallic": "y"}
_AO_X = {"ao": "x"}
_OPACITY_X = {"opacity": "x"}

_EXACT: dict[str, TxmpSemantics] = {
    "RoughMetalAO": TxmpSemantics("rmao", dict(_RMAO), "name_RoughMetalAO"),
    "BaseColorAlpha": TxmpSemantics("diffuse", {}, "name_BaseColorAlpha"),
    "BaseColorAlpha_1": TxmpSemantics("diffuse", {}, "name_BaseColorAlpha_1"),
    "DiffuseA": TxmpSemantics("diffuse", {}, "name_DiffuseA"),
    "DiffuseATexture": TxmpSemantics("diffuse", {}, "name_DiffuseATexture"),
    "DiffuseA_1": TxmpSemantics("diffuse", {}, "name_DiffuseA_1"),
    "DiffuseA_2": TxmpSemantics("diffuse", {}, "name_DiffuseA_2"),
    "DiffuseAndAlpha": TxmpSemantics("diffuse", {"opacity": "w"}, "name_DiffuseAndAlpha"),
    "DiffuseAndGlossTexture": TxmpSemantics(
        "diffuse", {}, "name_DiffuseAndGlossTexture"
    ),
    "CH1DiffuseTextureTexture": TxmpSemantics(
        "diffuse", {}, "name_CH1DiffuseTextureTexture"
    ),
    "CH1DiffuseMap": TxmpSemantics("diffuse", {}, "name_CH1DiffuseMap"),
    "CH1DiffuseMap_1": TxmpSemantics("diffuse", {}, "name_CH1DiffuseMap_1"),
    "Normal": TxmpSemantics("normal", {}, "name_Normal"),
    "NormalTexture": TxmpSemantics("normal", {}, "name_NormalTexture"),
    "CH1NormalTexture": TxmpSemantics("normal", {}, "name_CH1NormalTexture"),
    "CH2NormalTexture": TxmpSemantics("normal", {}, "name_CH2NormalTexture"),
    "WeaveNormal": TxmpSemantics("normal", {}, "name_WeaveNormal"),
    "ClearCoatNormal_Texture": TxmpSemantics(
        "normal", {}, "name_ClearCoatNormal_Texture"
    ),
    "CH1ClearCoatNormalMap": TxmpSemantics(
        "normal", {}, "name_CH1ClearCoatNormalMap"
    ),
    "CH2ClearCoatNormalMap": TxmpSemantics(
        "normal", {}, "name_CH2ClearCoatNormalMap"
    ),
    "OrangePeelNormal": TxmpSemantics("normal", {}, "name_OrangePeelNormal"),
    "FlakeNormalTexture": TxmpSemantics("normal", {}, "name_FlakeNormalTexture"),
    "FlakeNormalTexture_1": TxmpSemantics(
        "normal", {}, "name_FlakeNormalTexture_1"
    ),
    "Alpha": TxmpSemantics("alpha", dict(_OPACITY_X), "name_Alpha"),
    "LocalAO": TxmpSemantics("lcao", dict(_AO_X), "name_LocalAO"),
    "LocalAOTexture": TxmpSemantics("lcao", dict(_AO_X), "name_LocalAOTexture"),
    "DirectLocalAO": TxmpSemantics("lcao", dict(_AO_X), "name_DirectLocalAO"),
    "RoughnessMetalness": TxmpSemantics("rmao", dict(_RM), "name_RoughnessMetalness"),
    "RoughnessMetalness_1": TxmpSemantics(
        "rmao", dict(_RM), "name_RoughnessMetalness_1"
    ),
    "RoughnessMetalnessATexture": TxmpSemantics(
        "rmao", dict(_RM), "name_RoughnessMetalnessATexture"
    ),
    "NormRoughnessMetalness": TxmpSemantics(
        "rmao", dict(_RM), "name_NormRoughnessMetalness"
    ),
    "L1_NormRoughnessMetalness": TxmpSemantics(
        "rmao", dict(_RM), "name_L1_NormRoughnessMetalness"
    ),
    "L2_NormRoughnessMetalness": TxmpSemantics(
        "rmao", dict(_RM), "name_L2_NormRoughnessMetalness"
    ),
    "L4Pri_NormRoughnessMetalness": TxmpSemantics(
        "rmao", dict(_RM), "name_L4Pri_NormRoughnessMetalness"
    ),
    "GlassNormal": TxmpSemantics("normal", {}, "name_GlassNormal"),
    "GlassRoughnessTexture": TxmpSemantics(
        "gloss", {"roughness": "x", "smoothness": "0"}, "name_GlassRoughnessTexture"
    ),
    # Present on carbon; not a Principled primary map — leave unbound.
    "WeaveMask": TxmpSemantics(None, {}, "name_WeaveMask_unbound"),
    "radTexture": TxmpSemantics(None, {}, "name_radTexture_unbound"),
    "radTexture_1": TxmpSemantics(None, {}, "name_radTexture_1_unbound"),
    "radTextureTexture_pg_radiosity": TxmpSemantics(
        None, {}, "name_radTexture_radiosity_unbound"
    ),
}

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
