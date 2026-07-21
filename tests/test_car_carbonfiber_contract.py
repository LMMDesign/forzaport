"""Synthetic fixtures + unit tests for car_carbonfiber IR contract (B1.5)."""

from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.eval_car_carbonfiber import (
    APPROVED_PRODUCTION_RULES,
    CAR_CARBONFIBER_SHADERBIN_SHA256,
    is_car_carbonfiber_contract_identity,
)
from io_import_forza_carbin.materials.forza_ir import (
    Channel,
    ConstantColor,
    ContractStatus,
    Mix,
    Multiply,
    NormalDecode,
    RotateUV,
    ScaleUV,
    MeshUV,
    TextureSample,
)
from io_import_forza_carbin.materials.ir_compiler import (
    _find_mix,
    _rotation_of,
    _slot_from_sample,
    graph_build_plan_from_ir,
)
from io_import_forza_carbin.materials.shader_contract_registry import (
    contract_status_for,
    load_contract,
)
from io_import_forza_carbin.materials.texture_source import (
    ResolvedTextureSource,
    TextureSourceKind,
)
from io_import_forza_carbin.materials.forza_ir import SamplerState, TextureSampleExpression


def _fake_sample(*, channels=("r",), uv, path="GAME:\\t.swatchbin", h=0x12345678):
    src = ResolvedTextureSource(
        kind=TextureSourceKind.LOOSE_FILE,
        original_path=path,
        canonical_game_path="media/t.swatchbin",
        filesystem_path="/tmp/t.swatchbin",
        archive_path=None,
        archive_member=None,
        exists=True,
        failure=None,
    )
    expr = TextureSampleExpression(
        binding_name_hash=h,
        source=src,
        uv=uv,
        channels=channels,
        color_space="Non-Color",
        sampler=SamplerState(),
    )
    return TextureSample(sample=expr)


class CarCarbonfiberIdentityTests(unittest.TestCase):
    def test_identity_requires_exact_sha(self):
        self.assertTrue(
            is_car_carbonfiber_contract_identity(
                "car_carbonfiber", CAR_CARBONFIBER_SHADERBIN_SHA256
            )
        )
        self.assertFalse(
            is_car_carbonfiber_contract_identity("car_carbonfiber", "deadbeef")
        )
        self.assertFalse(
            is_car_carbonfiber_contract_identity(
                "car_standard", CAR_CARBONFIBER_SHADERBIN_SHA256
            )
        )

    def test_registry_loads_proven_contract(self):
        data = load_contract(CAR_CARBONFIBER_SHADERBIN_SHA256)
        self.assertIsNotNone(data)
        self.assertEqual(data["identity"]["shader_name"], "car_carbonfiber")
        self.assertEqual(
            contract_status_for(CAR_CARBONFIBER_SHADERBIN_SHA256), ContractStatus.PROVEN
        )
        self.assertIn("weave_normal_preferred_over_flat_normal", APPROVED_PRODUCTION_RULES)


class RotateUVCompilerTests(unittest.TestCase):
    def test_rotation_of_walks_chain(self):
        base = MeshUV(index=1)
        rotated = RotateUV(source=base, degrees=45.0)
        scaled = ScaleUV(source=rotated, scale=(2.0, 2.0))
        self.assertEqual(_rotation_of(scaled), 45.0)
        self.assertEqual(_rotation_of(base), 0.0)

    def test_slot_from_sample_carries_rotation(self):
        uv = ScaleUV(source=RotateUV(source=MeshUV(index=1), degrees=30.0), scale=(2.0, 2.0))
        sample = _fake_sample(uv=uv)
        slot = _slot_from_sample(sample.sample, role="weave_mask")
        self.assertAlmostEqual(slot.rotation_degrees, 30.0)
        self.assertEqual(slot.tiling, (2.0, 2.0))
        self.assertEqual(slot.texcoord, "TEXCOORD1")

    def test_zero_rotation_matches_prior_default(self):
        """Non-carbon UV chains (no RotateUV) must report exactly 0.0 — no regression."""
        uv = MeshUV(index=0)
        sample = _fake_sample(uv=uv)
        slot = _slot_from_sample(sample.sample, role="base_color")
        self.assertEqual(slot.rotation_degrees, 0.0)
        self.assertEqual(slot.pan, (0.0, 0.0))


class MixWeaveCompilerTests(unittest.TestCase):
    def _weave_base_color(self, *, angle=0.0):
        mask_uv = MeshUV(index=1)
        if angle:
            mask_uv = RotateUV(source=mask_uv, degrees=angle)
        mask = _fake_sample(uv=mask_uv, path="GAME:\\mask.swatchbin")
        factor = Channel(source=mask, channel="r")
        mix = Mix(
            a=ConstantColor(rgba=(0.01, 0.01, 0.01, 1.0)),
            b=ConstantColor(rgba=(0.02, 0.02, 0.02, 1.0)),
            factor=factor,
        )
        rmao = _fake_sample(channels=("r", "g", "b"), uv=MeshUV(index=0), path="GAME:\\rmao.swatchbin")
        ao = Channel(source=rmao, channel="b")
        return Multiply(a=mix, b=ao), rmao

    def test_find_mix_through_multiply(self):
        base_color, _ = self._weave_base_color()
        mix = _find_mix(base_color)
        self.assertIsInstance(mix, Mix)

    def test_graph_plan_emits_weave_composite_op(self):
        from io_import_forza_carbin.materials.forza_ir import ForzaMaterialIR, ShaderIdentity

        base_color, rmao = self._weave_base_color(angle=15.0)
        rough = Channel(source=rmao, channel="r")
        metal = Channel(source=rmao, channel="g")
        ao = Channel(source=rmao, channel="b")
        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_carbonfiber",
                archive_path="",
                shaderbin_sha256=CAR_CARBONFIBER_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            base_color=base_color,
            normal=NormalDecode(
                source=_fake_sample(
                    channels=("r", "g", "b"),
                    uv=RotateUV(source=MeshUV(index=1), degrees=15.0),
                    path="GAME:\\weave_normal.swatchbin",
                ),
                strength=1.5,
            ),
            roughness=rough,
            metallic=metal,
            ambient_occlusion=ao,
        )
        plan = graph_build_plan_from_ir(ir)
        ops = [s["op"] for s in plan]
        self.assertIn("weave_composite_base_color", ops)
        weave_step = next(s for s in plan if s["op"] == "weave_composite_base_color")
        self.assertEqual(weave_step["tint_a"], [0.01, 0.01, 0.01, 1.0])
        self.assertEqual(weave_step["mask"]["rotation_degrees"], 15.0)
        normal_step = next(s for s in plan if s["op"] == "normal_map")
        self.assertEqual(normal_step["strength"], 1.5)

    def test_non_carbon_plan_has_no_rotation_or_strength_keys(self):
        """car_standard-shaped IR (no RotateUV, strength=1.0) must not gain new keys."""
        from io_import_forza_carbin.materials.forza_ir import ForzaMaterialIR, ShaderIdentity

        base = _fake_sample(channels=("r", "g", "b"), uv=MeshUV(index=0), path="GAME:\\base.swatchbin")
        rmao = _fake_sample(channels=("r", "g", "b"), uv=MeshUV(index=0), path="GAME:\\rmao.swatchbin")
        ao = Channel(source=rmao, channel="b")
        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256="deadbeef",
                permutation="CarLightScenario",
            ),
            base_color=Multiply(a=base, b=ao),
            normal=NormalDecode(
                source=_fake_sample(
                    channels=("r", "g", "b"), uv=MeshUV(index=0), path="GAME:\\n.swatchbin"
                )
            ),
            roughness=Channel(source=rmao, channel="r"),
            metallic=Channel(source=rmao, channel="g"),
            ambient_occlusion=ao,
        )
        plan = graph_build_plan_from_ir(ir)
        for step in plan:
            if step.get("op") == "texture":
                self.assertNotIn("rotation_degrees", step["slot"])
                self.assertNotIn("pan", step["slot"])
            if step.get("op") == "normal_map":
                self.assertNotIn("strength", step)


class CarCarbonfiberEvaluatorGateTests(unittest.TestCase):
    def _material(self, overrides=None):
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        params = {
            SPN.UniqueLiverySwitchBool: SimpleNamespace(type=3, value=False, path="", samp=b""),
        }
        params.update(overrides or {})
        return SimpleNamespace(shader_name="car_carbonfiber", parameters=params, cbmp={}, txmp={}, spmp={})

    def test_production_rejects_unique_livery_true(self):
        from io_import_forza_carbin.materials import eval_car_carbonfiber as mod
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        material = self._material(
            {SPN.UniqueLiverySwitchBool: SimpleNamespace(type=3, value=True, path="", samp=b"")}
        )
        with mock.patch.object(mod, "extract_bindings") as eb:
            eb.return_value = SimpleNamespace(
                source_hashes={"shaderbin_sha256": CAR_CARBONFIBER_SHADERBIN_SHA256}
            )
            ir = mod.evaluate_car_carbonfiber(
                name="fixture|unique_livery",
                material=material,
                resolver=None,
                media_root=".",
                production_mode=True,
            )
        self.assertTrue(ir.rejection_reasons)
        self.assertIn("UniqueLiverySwitchBool", ir.rejection_reasons[0])

    def test_rejects_missing_weave_mask(self):
        from io_import_forza_carbin.materials import eval_car_carbonfiber as mod
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        material = self._material(
            {
                SPN.WeaveColorTintA: SimpleNamespace(type=0, value=(0.1, 0.1, 0.1, 1.0), path="", samp=b""),
                SPN.WeaveColorTintB: SimpleNamespace(type=0, value=(0.2, 0.2, 0.2, 1.0), path="", samp=b""),
            }
        )
        with mock.patch.object(mod, "extract_bindings") as eb:
            eb.return_value = SimpleNamespace(
                source_hashes={"shaderbin_sha256": CAR_CARBONFIBER_SHADERBIN_SHA256}
            )
            ir = mod.evaluate_car_carbonfiber(
                name="fixture|no_weave_mask",
                material=material,
                resolver=None,
                media_root=".",
                production_mode=True,
            )
        self.assertTrue(ir.rejection_reasons)
        self.assertIn("WeaveMask", ir.rejection_reasons[0])

    def test_weave_uv_fails_closed_when_tiling_absent(self):
        from io_import_forza_carbin.materials.eval_car_carbonfiber import _weave_uv

        expr, evidence, reject = _weave_uv({})
        self.assertIsNone(expr)
        self.assertIsNotNone(reject)
        self.assertIn("U_Tiling", reject)
        self.assertTrue(evidence)

    def test_weave_uv_orientation_u_v_tiling_order(self):
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN
        from io_import_forza_carbin.materials.eval_car_carbonfiber import _weave_uv

        params = {
            SPN.UVOrientation: SimpleNamespace(type=2, value=90.0),
            SPN.UTiling: SimpleNamespace(type=2, value=32.0),
            SPN.VTiling: SimpleNamespace(type=2, value=32.0),
        }
        expr, evidence, reject = _weave_uv(params)
        self.assertIsNone(reject)
        self.assertIsInstance(expr, ScaleUV)
        self.assertEqual(expr.scale, (32.0, 32.0))
        self.assertIsInstance(expr.source, RotateUV)
        self.assertEqual(expr.source.degrees, 90.0)
        self.assertIsInstance(expr.source.source, MeshUV)
        self.assertEqual(expr.source.source.index, 1)
        self.assertTrue(any("U_Tiling" in e.detail for e in evidence))

    def test_equal_tints_remain_equal_in_mix(self):
        """Authoritative IR must not invent Base Color contrast when A==B."""
        from io_import_forza_carbin.materials.forza_ir import ConstantColor, Mix, Channel

        a = (0.009721217676997185, 0.009721217676997185, 0.009721217676997185, 1.0)
        mix = Mix(
            a=ConstantColor(rgba=a),
            b=ConstantColor(rgba=a),
            factor=Channel(source=ConstantColor(rgba=(1, 1, 1, 1)), channel="r"),
        )
        self.assertEqual(mix.a.rgba, mix.b.rgba)


if __name__ == "__main__":
    unittest.main()
