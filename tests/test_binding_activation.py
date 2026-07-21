"""Pure-Python tests for texture binding activation vs presence."""

from __future__ import annotations

import os
import sys
import types
import unittest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.binding_activation import (  # noqa: E402
    decide_base_color_source,
    decide_car_carbonfiber_base_color,
    decide_paint_or_texture_base_color,
)
from io_import_forza_carbin.materials.graph_plan import graph_build_plan  # noqa: E402
from io_import_forza_carbin.materials.model import (  # noqa: E402
    BaseColorSourceKind,
    MaterialCapabilityKind,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedMaterial,
    ResolvedTextureSlot,
    TextureBindingActivation,
    make_clean_surface_capability,
)
from io_import_forza_carbin.materials.texture_source import (  # noqa: E402
    canonicalize_game_path,
)
from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _slot(role="base_color", path="Game:\\Media\\_library\\textures\\x.swatchbin", name="BaseColorAlpha"):
    return ResolvedTextureSlot(
        role=role,
        path=path,
        texcoord="TEXCOORD0",
        param_hash=1,
        param_name=name,
        channel="r" if role == "weave_mask" else None,
        evidence=(ProvenanceDiagnostic(kind="test", detail="slot", source="test"),),
    )


def _bool(v: bool):
    return SimpleNamespace(type=3, value=v)


def _color(rgb=(0.1, 0.2, 0.3, 1.0)):
    return SimpleNamespace(type=1, value=rgb)


class BindingActivationTests(unittest.TestCase):
    def test_presence_does_not_imply_activity_without_proven_branch(self):
        # Carbon with UniqueLivery=false: BaseColorAlpha present but inactive.
        base = _slot(
            path="Game:\\Media\\_library\\textures\\swatches\\defaultshader_diff_x.swatchbin"
        )
        mask = _slot(role="weave_mask", name="WeaveMask", path="Game:\\Media\\cars\\_library\\textures\\mask.swatchbin")
        params = {
            SPN.UniqueLiverySwitchBool: _bool(False),
            SPN.MaskedLiveryBool: _bool(True),
            SPN.WeaveColorTintA: _color((0.01, 0.01, 0.01, 1.0)),
            SPN.WeaveColorTintB: _color((0.02, 0.02, 0.02, 1.0)),
        }
        source, decisions = decide_car_carbonfiber_base_color(
            params=params, base_map=base, weave_mask=mask
        )
        self.assertEqual(source.kind, BaseColorSourceKind.WEAVE_COMPOSITE)
        self.assertIsNone(source.texture)
        base_dec = next(d for d in decisions if d.slot.param_name == "BaseColorAlpha")
        self.assertEqual(base_dec.activation, TextureBindingActivation.INACTIVE_PLACEHOLDER)

    def test_proven_active_texture_default_branch(self):
        base = _slot(path="Game:\\Media\\cars\\_library\\textures\\authored_diff.swatchbin")
        source, decisions = decide_paint_or_texture_base_color(
            params={}, base_map=base, stock_paint=None
        )
        self.assertEqual(source.kind, BaseColorSourceKind.TEXTURE)
        self.assertEqual(decisions[0].activation, TextureBindingActivation.ACTIVE)

    def test_proven_inactive_placeholder_via_paint(self):
        base = _slot()
        params = {SPN.PaintColorColorParam: _color((0.5, 0.1, 0.0, 1.0))}
        source, decisions = decide_paint_or_texture_base_color(
            params=params, base_map=base, stock_paint=None
        )
        self.assertEqual(source.kind, BaseColorSourceKind.INSTANCE_PAINT)
        self.assertEqual(decisions[0].activation, TextureBindingActivation.INACTIVE_PLACEHOLDER)
        self.assertEqual(source.color[:3], (0.5, 0.1, 0.0))

    def test_filename_independence_active_defaultshader_name(self):
        # Name containing defaultshader_diff can still be ACTIVE outside carbon gate.
        base = _slot(
            path="Game:\\Media\\_library\\textures\\swatches\\defaultshader_diff_x.swatchbin"
        )
        source, decisions = decide_paint_or_texture_base_color(
            params={}, base_map=base, stock_paint=None
        )
        self.assertEqual(source.kind, BaseColorSourceKind.TEXTURE)
        self.assertEqual(decisions[0].activation, TextureBindingActivation.ACTIVE)

    def test_filename_independence_inactive_other_name(self):
        base = _slot(path="Game:\\Media\\cars\\_library\\textures\\fancy_albedo.swatchbin")
        mask = _slot(role="weave_mask", name="WeaveMask")
        params = {
            SPN.UniqueLiverySwitchBool: _bool(False),
            SPN.WeaveColorTintA: _color(),
            SPN.WeaveColorTintB: _color((0.2, 0.2, 0.2, 1.0)),
        }
        source, decisions = decide_car_carbonfiber_base_color(
            params=params, base_map=base, weave_mask=mask
        )
        self.assertEqual(source.kind, BaseColorSourceKind.WEAVE_COMPOSITE)
        self.assertEqual(
            decisions[0].activation, TextureBindingActivation.INACTIVE_PLACEHOLDER
        )

    def test_constant_override_via_unique_base_color(self):
        base = _slot()
        params = {
            SPN.UniqueBaseColorSwitchBool: _bool(True),
            SPN.UniqueBaseTextureSwitchBool: _bool(False),
            SPN.UniqueBaseColorColorParam: _color((0.8, 0.1, 0.1, 1.0)),
        }
        source, decisions = decide_paint_or_texture_base_color(
            params=params, base_map=base, stock_paint=None
        )
        self.assertEqual(source.kind, BaseColorSourceKind.MATERIAL_CONSTANT)
        self.assertEqual(decisions[0].activation, TextureBindingActivation.INACTIVE_PLACEHOLDER)

    def test_instance_paint_selected(self):
        params = {SPN.PaintColorColorParam: _color((0.2, 0.3, 0.9, 1.0))}
        source, _ = decide_paint_or_texture_base_color(
            params=params, base_map=_slot(), stock_paint=None
        )
        self.assertEqual(source.kind, BaseColorSourceKind.INSTANCE_PAINT)

    def test_conditional_unresolved_unique_livery_true(self):
        base = _slot()
        mask = _slot(role="weave_mask", name="WeaveMask")
        params = {
            SPN.UniqueLiverySwitchBool: _bool(True),
            SPN.WeaveColorTintA: _color(),
            SPN.WeaveColorTintB: _color(),
        }
        source, decisions = decide_car_carbonfiber_base_color(
            params=params, base_map=base, weave_mask=mask
        )
        self.assertEqual(source.kind, BaseColorSourceKind.UNRESOLVED)
        self.assertEqual(
            decisions[0].activation, TextureBindingActivation.CONDITIONAL_UNRESOLVED
        )

    def test_graph_isolation_consumes_only_resolved_source(self):
        weave_mask = _slot(role="weave_mask", name="WeaveMask", path="Game:\\m.swatchbin")
        from io_import_forza_carbin.materials.model import ResolvedWeaveComposite

        weave = ResolvedWeaveComposite(
            tint_a=(0.01, 0.01, 0.01, 1.0),
            tint_b=(0.02, 0.02, 0.02, 1.0),
            mask=weave_mask,
        )
        cap = make_clean_surface_capability(
            base_color_source=ResolvedBaseColorSource(
                kind=BaseColorSourceKind.WEAVE_COMPOSITE,
                weave=weave,
            ),
            alpha_map=None,
            normal_map=None,
            rmao_map=None,
            alpha_mode="OPAQUE",
            alpha_threshold=0.5,
            evidence=(),
        )
        resolved = ResolvedMaterial(
            name="t",
            game_key="fh6",
            shader_name="car_carbonfiber",
            capability_kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=cap,
        )
        plan = graph_build_plan(resolved)
        ops = [s["op"] for s in plan]
        self.assertIn("weave_composite_base_color", ops)
        self.assertNotIn("constant_base_color", ops)
        # Plan must not mention raw MatI / defaultshader / filename heuristics.
        blob = str(plan)
        self.assertNotIn("UniqueLivery", blob)
        self.assertNotIn("defaultshader", blob.lower())

    def test_archive_path_canonical_unchanged(self):
        self.assertTrue(
            canonicalize_game_path(
                "GAME:\\Media\\_library\\textures\\swatches\\defaultshader_diff_x.swatchbin"
            ).startswith("media\\_library\\textures\\")
        )

    def test_decide_routes_carbon_family(self):
        base = _slot()
        mask = _slot(role="weave_mask", name="WeaveMask")
        params = {
            SPN.UniqueLiverySwitchBool: _bool(False),
            SPN.WeaveColorTintA: _color(),
            SPN.WeaveColorTintB: _color((0.2, 0.2, 0.2, 1.0)),
        }
        source, _ = decide_base_color_source(
            shader_name="car_carbonfiber",
            params=params,
            base_map=base,
            weave_mask=mask,
        )
        self.assertEqual(source.kind, BaseColorSourceKind.WEAVE_COMPOSITE)


if __name__ == "__main__":
    unittest.main()
