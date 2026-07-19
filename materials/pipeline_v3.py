"""Direct FH6 material translator (game-data only).

Contract:
    TXMP exact NameHash -> BaseColor / Alpha / Normal / RoughMetalAO
    DXIL -> UV (unique semantic or proven UVChoice_OnCh1_OffCh2), sampler, tiling
    MatI -> paint / weave / authored alpha mode

No filename heuristics, no min(UV) guesses, no invented fill-ins.
Ambiguous or missing provenance raises MaterialTranslateError (printed).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from ..parsing.material import ShaderParameterName as SPN
from ..parsing.paths import find_media_root
from .capabilities import resolve_uv_choice_texcoord
from .name_hashes import require_name
from .shader_bindings import extract_bindings, resolve_binding_uv

PIPELINE_VERSION = 4

_ADDR = {0: "REPEAT", 1: "REPEAT", 2: "MIRROR", 3: "EXTEND", 4: "CLIP"}
_BASE_NAMES = frozenset({"BaseColorAlpha", "BaseColorAlpha_1"})
_ALPHA_NAMES = frozenset({"Alpha"})
# Primary normals only — clearcoat/flake/orange-peel need their own proven stacks.
_NORMAL_NAMES = frozenset({"Normal", "WeaveNormal"})
_RMAO_NAMES = frozenset({"RoughMetalAO"})
_CONTRACT_NAMES = _BASE_NAMES | _ALPHA_NAMES | _NORMAL_NAMES | _RMAO_NAMES


class MaterialTranslateError(RuntimeError):
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


def _bool(params: dict, h: int) -> bool | None:
    p = params.get(h)
    if p is None or getattr(p, "type", None) != 3:
        return None
    return bool(p.value)


def _color(params: dict, h: int):
    p = params.get(h)
    if p is None or getattr(p, "type", None) not in (0, 1):
        return None
    value = getattr(p, "value", None)
    return value if isinstance(value, tuple) and len(value) >= 3 else None


def _path_exists(path: str, resolver) -> bool:
    resolved = resolver.resolve(path) if resolver is not None else path
    return bool(resolved and os.path.isfile(resolved))


def _binding_uv(bind, params: dict | None = None, *, txmp_name: str | None = None):
    """Return proven TEXCOORD index, or None when multi-UV lacks UVChoice."""
    if bind is None:
        return None
    if params is not None:
        uv = resolve_binding_uv(bind, params, txmp_name=txmp_name)
        if uv is not None:
            return int(uv)
    if bind.uv_semantic is not None:
        all_uv = tuple(dict.fromkeys(bind.uv_semantics_all or ()))
        if not all_uv or (
            len(all_uv) == 1 and int(all_uv[0]) == int(bind.uv_semantic)
        ):
            return int(bind.uv_semantic)
    all_uv = tuple(dict.fromkeys(bind.uv_semantics_all or ()))
    if len(all_uv) == 1:
        return int(all_uv[0])
    return None


def _tiling(params: dict, bind) -> tuple[float, float]:
    if bind is None:
        return (1.0, 1.0)
    values: list[float] = []
    for h in bind.tiling_cb_hashes or ():
        p = params.get(h)
        if p is None:
            continue
        value = getattr(p, "value", None)
        if getattr(p, "type", None) == 11 and isinstance(value, tuple):
            if len(value) >= 2:
                return (float(value[0]), float(value[1]))
        elif isinstance(value, (int, float)):
            values.append(float(value))
    if len(values) >= 2:
        return (values[0], values[1])
    if len(values) == 1:
        return (values[0], values[0])
    return (1.0, 1.0)


def _address(params: dict, spmp: dict, bind):
    if bind is None or bind.sampler_reg is None:
        return None
    for h, reg in spmp.items():
        if int(reg) != int(bind.sampler_reg):
            continue
        p = params.get(h)
        raw = getattr(p, "samp", b"") if p is not None else b""
        if getattr(p, "type", None) != 7 or len(raw) < 8:
            continue
        u = struct.unpack_from("<I", raw, 0)[0]
        v = struct.unpack_from("<I", raw, 4)[0]
        return {"U": _ADDR.get(u, "REPEAT"), "V": _ADDR.get(v, "REPEAT")}
    return None


def _slot(
    *,
    role: str,
    h: int,
    name: str,
    path: str,
    bind,
    params: dict,
    spmp: dict,
    uv: int,
    channel: str | None = None,
    evidence: tuple[str, ...] = (),
) -> TextureSlot:
    return TextureSlot(
        role=role,
        path=path,
        texcoord=f"TEXCOORD{uv}",
        channel=channel,
        tiling=_tiling(params, bind),
        address=_address(params, spmp, bind),
        param_hash=h & 0xFFFFFFFF,
        param_name=name,
        evidence=evidence,
    )


def _prefer(current, candidate, *, is_override: bool):
    if current is None or is_override:
        return candidate
    return current


def _paint_color(params: dict, stock):
    """MatI paint switches first; ManufacturerColors stock only if MatI has none."""
    unique_color = _bool(params, SPN.UniqueBaseColorSwitchBool)
    unique_texture = _bool(params, SPN.UniqueBaseTextureSwitchBool)
    if unique_color is True and unique_texture is not True:
        value = _color(params, SPN.UniqueBaseColorColorParam)
        if value is not None:
            return (*value[:3], 1.0)
    group = _bool(params, SPN.ColorGroupSwitchBool)
    if group is True:
        value = _color(params, SPN.PaintColorGroupColorParam)
        if value is not None:
            return (*value[:3], 1.0)
    value = _color(params, SPN.PaintColorColorParam)
    if value is not None:
        return (*value[:3], 1.0)
    return stock


def _alpha_mode(params: dict, has_alpha: bool) -> str:
    use_test = _bool(params, SPN.UseAlphaTestBool)
    use_blend = _bool(params, SPN.UseAlphaBlendBool)
    transparency = _bool(params, SPN.AlphaTransparencyBool)
    if use_blend is True and use_test is not True:
        return "BLEND"
    if use_test is True or transparency is True or has_alpha:
        return "CLIP"
    return "OPAQUE"


class CleanMaterialBuilder:
    """Build MaterialSpec strictly from MatI + shaderbin + CarLightScenario DXIL."""

    def __init__(self, media_root: str | None = None, game_key: str = "fh6"):
        if (game_key or "").lower() != "fh6":
            raise MaterialTranslateError(
                f"direct material pipeline supports FH6 only, got {game_key!r}"
            )
        self.game_key = "fh6"
        self.media_root = media_root
        self.stock_paint_rgba = None

    def _media(self, resolver) -> str:
        if self.media_root and os.path.isdir(self.media_root):
            return self.media_root
        root = getattr(resolver, "root", None)
        media = find_media_root(root) if root else None
        if media:
            return media
        raise MaterialTranslateError("FH6 Media root is required for DXIL bindings")

    def build(self, name, material, resolver=None) -> MaterialSpec:
        shader_name = getattr(material, "shader_name", None)
        if not shader_name:
            raise MaterialTranslateError("material has no shader")
        params = getattr(material, "parameters", None) or {}
        txmp = getattr(material, "txmp", None) or {}
        cbmp = getattr(material, "cbmp", None) or {}
        spmp = getattr(material, "spmp", None) or {}
        overrides = getattr(material, "override_hashes", None) or set()

        try:
            bindings = extract_bindings(
                media_root=self._media(resolver),
                shader_name=shader_name,
                params=params,
                cbmp=cbmp,
                game_key="fh6",
            )
        except Exception as exc:
            msg = f"{name} ({shader_name}): DXIL/bindings failed: {exc}"
            print(f"Forza material ERROR: {msg}", flush=True)
            raise MaterialTranslateError(msg) from exc

        spec = MaterialSpec(name=name, game_key="fh6", shader_name=shader_name)
        pending_alpha: tuple[int, str, str, object, int] | None = None
        errors: list[str] = []

        paint = _paint_color(params, self.stock_paint_rgba)
        unique_livery = _bool(params, SPN.UniqueLiverySwitchBool)
        uses_mat_paint = paint is not None and unique_livery is not True

        uv_choice = resolve_uv_choice_texcoord(params)
        if uv_choice is not None:
            print(
                f"Forza material UV: {name}: {uv_choice[1].detail}",
                flush=True,
            )

        for h, treg in sorted(txmp.items(), key=lambda kv: int(kv[1])):
            p = params.get(h)
            if p is None or getattr(p, "type", None) != 6:
                continue
            path = getattr(p, "path", "") or ""
            try:
                param_name = require_name(h, context=f"{shader_name} TXMP t{treg}")
            except Exception as exc:
                errors.append(str(exc))
                continue

            if param_name not in _CONTRACT_NAMES:
                continue

            # MatI paint replaces albedo — do not require BaseColorAlpha swatch files.
            if uses_mat_paint and param_name in _BASE_NAMES:
                continue

            if not path:
                errors.append(f"{param_name} t{treg}: empty TXMP path")
                continue
            if not _path_exists(path, resolver):
                errors.append(f"{param_name} t{treg}: texture missing: {path}")
                continue

            bind = bindings.textures.get(int(treg))
            if param_name in _ALPHA_NAMES:
                pending_alpha = (h, param_name, path, bind, int(treg))
                continue

            uv = _binding_uv(bind, params, txmp_name=param_name)
            if uv is None:
                all_uv = (
                    list(getattr(bind, "uv_semantics_all", None) or []) if bind else []
                )
                if bind is None:
                    errors.append(
                        f"{param_name} t{treg}: not sampled in CarLightScenario DXIL"
                    )
                else:
                    errors.append(
                        f"{param_name} t{treg}: no proven UV "
                        f"(DXIL candidates={all_uv or None}; "
                        f"need unique TEXCOORD or UVChoice)"
                    )
                continue

            evidence = (
                f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}",
                f"DXIL:t{treg}:TEXCOORD{uv}",
            )
            if param_name in _BASE_NAMES:
                candidate = _slot(
                    role="base_color",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence,
                )
                spec.base_color_map = _prefer(
                    spec.base_color_map, candidate, is_override=h in overrides
                )
            elif param_name in _NORMAL_NAMES:
                candidate = _slot(
                    role="normal",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence,
                )
                spec.normal_map = _prefer(
                    spec.normal_map, candidate, is_override=h in overrides
                )
            elif param_name in _RMAO_NAMES:
                candidate = _slot(
                    role="rmao",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    evidence=evidence + ("packing:R=roughness,G=metallic,B=AO",),
                )
                spec.rmao_map = _prefer(
                    spec.rmao_map, candidate, is_override=h in overrides
                )

        if uses_mat_paint:
            spec.base_color = paint
            spec.base_color_map = None
            pending_alpha = None
        elif spec.base_color_map is None:
            weave = _color(params, SPN.WeaveColorTintA) or _color(
                params, SPN.WeaveColorTintB
            )
            if weave is not None:
                spec.base_color = (*weave[:3], 1.0)

        if pending_alpha is not None:
            h, param_name, path, bind, treg = pending_alpha
            uv = _binding_uv(bind, params, txmp_name=param_name)
            evidence = [f"TXMP:0x{h & 0xFFFFFFFF:08X}:{param_name}"]
            use_test = _bool(params, SPN.UseAlphaTestBool)
            use_blend = _bool(params, SPN.UseAlphaBlendBool)
            transparency = _bool(params, SPN.AlphaTransparencyBool)
            authored_on = (
                use_test is True or use_blend is True or transparency is True
            )
            # Explicit MatI off (e.g. AlphaTransparency=False) → leave Alpha unused.
            authored_off = transparency is False or (
                use_test is False and use_blend is not True
            )
            dxil_opacity = False
            opacity_ch = "x"
            if bind is not None:
                roles = getattr(bind, "channel_roles", None) or {}
                if roles.get("opacity"):
                    dxil_opacity = True
                    opacity_ch = str(roles["opacity"])
                elif getattr(bind, "opacity_from_w", False):
                    dxil_opacity = True
                    opacity_ch = "w"
                elif any(
                    "feeds_sv_target_alpha" in e
                    for e in (getattr(bind, "evidence", None) or ())
                ):
                    dxil_opacity = True
                for e in getattr(bind, "evidence", None) or ():
                    if "alpha_supplement_pso=" in e:
                        evidence.append(e)
            if bind is None:
                errors.append(
                    f"{param_name} t{treg}: TXMP present but not sampled in "
                    f"CarLightScenario DXIL (no invented UV)"
                )
            elif uv is None:
                all_uv = list(getattr(bind, "uv_semantics_all", None) or [])
                errors.append(
                    f"{param_name} t{treg}: no proven UV "
                    f"(DXIL candidates={all_uv or None}; need unique TEXCOORD or UVChoice)"
                )
            elif authored_on or dxil_opacity:
                if authored_on:
                    evidence.append("MatI:authored alpha mode")
                if dxil_opacity:
                    evidence.append(f"DXIL:opacity_channel={opacity_ch}")
                evidence.append(f"DXIL:t{treg}:TEXCOORD{uv}")
                spec.alpha_map = _slot(
                    role="alpha",
                    h=h,
                    name=param_name,
                    path=path,
                    bind=bind,
                    params=params,
                    spmp=spmp,
                    uv=uv,
                    channel=opacity_ch,
                    evidence=tuple(evidence),
                )
            elif authored_off:
                pass
            else:
                errors.append(
                    f"{param_name} t{treg}: Tex+UV TEXCOORD{uv} but no MatI alpha "
                    f"mode and no DXIL SV_Target-alpha evidence"
                )

        spec.alpha_mode = _alpha_mode(params, spec.alpha_map is not None)
        spec.valid = bool(
            spec.base_color_map
            or spec.alpha_map
            or spec.normal_map
            or spec.rmao_map
            or spec.base_color != (1.0, 1.0, 1.0, 1.0)
        )

        if errors:
            for err in errors:
                print(f"Forza material ERROR: {name} ({shader_name}): {err}", flush=True)

        if not spec.valid:
            detail = "; ".join(errors) if errors else "no proven Base/Alpha/Normal/RMAO or paint/weave"
            msg = f"{shader_name}: unsupported — {detail}"
            spec.error = msg
            raise MaterialTranslateError(msg)

        if errors:
            # Built a surface but some contract maps failed provenance — keep build,
            # leave PARTIAL diagnostics to the report layer; do not invent fill-ins.
            print(
                f"Forza material WARN: {name} ({shader_name}): built with "
                f"{len(errors)} unresolved contract map issue(s)",
                flush=True,
            )
        return spec
