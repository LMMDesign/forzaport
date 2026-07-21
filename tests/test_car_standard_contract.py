"""Synthetic fixtures + unit tests for car_standard IR contract (B1)."""

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

from io_import_forza_carbin.materials.eval_car_standard import (
    CAR_STANDARD_SHADERBIN_SHA256,
    APPROVED_PRODUCTION_RULES,
    is_car_standard_contract_identity,
)
from io_import_forza_carbin.materials.forza_ir import (
    Channel,
    Clamp,
    MeshUV,
    Multiply,
    NormalDecode,
    TextureSample,
)
from io_import_forza_carbin.materials.ir_compiler import graph_build_plan_from_ir
from io_import_forza_carbin.materials.shader_contract_registry import (
    contract_status_for,
    load_contract,
)
from io_import_forza_carbin.materials.forza_ir import ContractStatus


class CarStandardIdentityTests(unittest.TestCase):
    def test_identity_requires_exact_sha(self):
        self.assertTrue(
            is_car_standard_contract_identity(
                "car_standard", CAR_STANDARD_SHADERBIN_SHA256
            )
        )
        self.assertFalse(
            is_car_standard_contract_identity("car_standard", "deadbeef")
        )
        self.assertFalse(
            is_car_standard_contract_identity(
                "car_tires_pg", CAR_STANDARD_SHADERBIN_SHA256
            )
        )

    def test_registry_loads_proven_contract(self):
        data = load_contract(CAR_STANDARD_SHADERBIN_SHA256)
        self.assertIsNotNone(data)
        self.assertEqual(data["identity"]["shader_name"], "car_standard")
        self.assertEqual(contract_status_for(CAR_STANDARD_SHADERBIN_SHA256), ContractStatus.PROVEN)
        self.assertIn("uvchoice_on_ch1_off_ch2", APPROVED_PRODUCTION_RULES)


class CarStandardIRCompilerTests(unittest.TestCase):
    def _fake_sample(self, *, channels=("r", "g", "b"), uv_index=0, path="GAME:\\t.swatchbin"):
        from io_import_forza_carbin.materials.forza_ir import (
            SamplerState,
            TextureSampleExpression,
        )
        from io_import_forza_carbin.materials.texture_source import (
            ResolvedTextureSource,
            TextureSourceKind,
        )

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
            binding_name_hash=0x12345678,
            source=src,
            uv=MeshUV(index=uv_index),
            channels=channels,
            color_space="sRGB",
            sampler=SamplerState(),
        )
        return TextureSample(sample=expr)

    def test_graph_plan_from_ir_emits_clean_ops(self):
        from io_import_forza_carbin.materials.forza_ir import ForzaMaterialIR, ShaderIdentity

        base = self._fake_sample()
        rmao = self._fake_sample(channels=("r", "g", "b"), path="GAME:\\rmao.swatchbin")
        rough = Channel(source=rmao, channel="r")
        metal = Channel(source=rmao, channel="g")
        ao = Channel(source=rmao, channel="b")
        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            base_color=Multiply(a=base, b=ao),
            normal=NormalDecode(source=self._fake_sample(path="GAME:\\n.swatchbin")),
            roughness=rough,
            metallic=metal,
            ambient_occlusion=ao,
            opacity=Clamp(
                source=Channel(
                    source=self._fake_sample(channels=("x",), path="GAME:\\a.swatchbin"),
                    channel="x",
                ),
                lo=0.5,
                hi=1.0,
            ),
        )
        plan = graph_build_plan_from_ir(ir)
        ops = [s["op"] for s in plan]
        self.assertIn("texture", ops)
        self.assertIn("rmao_separate_and_ao_multiply", ops)
        self.assertIn("alpha_clip", ops)
        alpha_slots = [
            s["slot"] for s in plan if s.get("op") == "texture" and s.get("binds") == "alpha"
        ]
        self.assertEqual(len(alpha_slots), 1)
        self.assertEqual(alpha_slots[0]["channel"], "x")

    def test_compiler_rejects_on_rejection_reasons(self):
        from io_import_forza_carbin.materials.forza_ir import ForzaMaterialIR, ShaderIdentity

        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            rejection_reasons=("synthetic reject",),
        )
        with self.assertRaises(RuntimeError):
            graph_build_plan_from_ir(ir)


class CarStandardPendingGateTests(unittest.TestCase):
    def test_production_rejects_glass_true(self):
        from io_import_forza_carbin.materials import eval_car_standard as mod
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        material = SimpleNamespace(
            shader_name="car_standard",
            parameters={
                SPN.GlassSwitchBool: SimpleNamespace(type=3, value=True, path="", samp=b""),
            },
            cbmp={},
        )
        with mock.patch.object(mod, "extract_bindings") as eb:
            eb.return_value = SimpleNamespace(
                source_hashes={"shaderbin_sha256": CAR_STANDARD_SHADERBIN_SHA256}
            )
            ir = mod.evaluate_car_standard(
                name="fixture|glass",
                material=material,
                resolver=None,
                media_root=".",
                production_mode=True,
            )
        self.assertTrue(ir.rejection_reasons)
        self.assertIn("GlassSwitch", ir.rejection_reasons[0])


class CarStandardB175TintAndUVTests(unittest.TestCase):
    """B1.75: BaseColor_Tint multiply + CB reg19 UV rotate/scale (no filename rules)."""

    def _fake_sample(self, *, channels=("r", "g", "b"), uv_index=0, path="GAME:\\t.swatchbin"):
        return CarStandardIRCompilerTests()._fake_sample(
            channels=channels, uv_index=uv_index, path=path
        )

    def _params(self, **overrides):
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        base = {
            SPN.UTiling: SimpleNamespace(type=2, value=3.0, path="", samp=b""),
            SPN.VTiling: SimpleNamespace(type=2, value=4.0, path="", samp=b""),
            SPN.UVOrientation: SimpleNamespace(type=2, value=15.0, path="", samp=b""),
            SPN.BaseColorTint: SimpleNamespace(
                type=1, value=(0.01, 0.02, 0.03, 1.0), path="", samp=b""
            ),
            SPN.BaseColorTintMode: SimpleNamespace(
                type=0, value=(1.0, 1.0, 0.0, 0.0), path="", samp=b""
            ),
            SPN.BaseColorTintMultiplier: SimpleNamespace(
                type=2, value=2.0, path="", samp=b""
            ),
            0xB8E61E16: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0xB99646E7: SimpleNamespace(type=11, value=(9.0, 9.0), path="", samp=b""),
            0xECCEB8F9: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0x8BAB96B3: SimpleNamespace(type=11, value=(1.0, 1.0), path="", samp=b""),
            0x1B003865: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0xF383EB56: SimpleNamespace(type=11, value=(1.0, 1.0), path="", samp=b""),
            0x402B8ED0: SimpleNamespace(type=3, value=False, path="", samp=b""),
        }
        base.update(overrides)
        return base

    def test_approved_rules_include_tint_and_uv(self):
        self.assertIn("base_color_tint_multiply", APPROVED_PRODUCTION_RULES)
        self.assertIn("uv_rotate_scale_cb_reg19", APPROVED_PRODUCTION_RULES)

    def test_uv_scale_independent_u_v_and_rotation_order(self):
        from io_import_forza_carbin.materials.eval_car_standard import _uv_expr_for_slot
        from io_import_forza_carbin.materials.model import ResolvedTextureSlot
        from io_import_forza_carbin.materials.forza_ir import RotateUV, ScaleUV, MeshUV

        slot = ResolvedTextureSlot(
            role="base_color",
            path="GAME:\\t.swatchbin",
            texcoord="TEXCOORD1",
            param_hash=1,
            param_name="BaseColorAlpha",
        )
        expr, reject = _uv_expr_for_slot(
            slot, self._params(), production_mode=True, revision="b1.75"
        )
        self.assertIsNone(reject)
        self.assertIsInstance(expr, ScaleUV)
        self.assertEqual(expr.scale, (3.0, 4.0))
        self.assertIsInstance(expr.source, RotateUV)
        self.assertEqual(expr.source.degrees, 15.0)
        self.assertIsInstance(expr.source.source, MeshUV)
        self.assertEqual(expr.source.source.index, 1)

    def test_missing_tiling_fails_closed(self):
        from io_import_forza_carbin.materials.eval_car_standard import _uv_expr_for_slot
        from io_import_forza_carbin.materials.model import ResolvedTextureSlot
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        slot = ResolvedTextureSlot(
            role="base_color",
            path="GAME:\\t.swatchbin",
            texcoord="TEXCOORD0",
            param_hash=1,
            param_name="BaseColorAlpha",
        )
        params = self._params()
        del params[SPN.UTiling]
        expr, reject = _uv_expr_for_slot(
            slot, params, production_mode=True, revision="b1.75"
        )
        self.assertIsNone(expr)
        self.assertIn("U_Tiling", reject or "")

    def test_different_scales_different_fingerprints(self):
        from io_import_forza_carbin.materials.eval_car_standard import _uv_expr_for_slot
        from io_import_forza_carbin.materials.model import ResolvedTextureSlot
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        slot = ResolvedTextureSlot(
            role="base_color",
            path="GAME:\\t.swatchbin",
            texcoord="TEXCOORD1",
            param_hash=1,
            param_name="BaseColorAlpha",
        )
        a, _ = _uv_expr_for_slot(
            slot, self._params(), production_mode=True, revision="b1.75"
        )
        params_b = self._params()
        params_b[SPN.UTiling] = SimpleNamespace(type=2, value=8.0, path="", samp=b"")
        b, _ = _uv_expr_for_slot(
            slot, params_b, production_mode=True, revision="b1.75"
        )
        self.assertNotEqual(a.scale, b.scale)

    def test_graph_plan_emits_tint_and_scale(self):
        from io_import_forza_carbin.materials.forza_ir import (
            ConstantColor,
            ForzaMaterialIR,
            Mix,
            RotateUV,
            ScaleUV,
            ShaderIdentity,
        )

        uv = ScaleUV(
            source=RotateUV(source=MeshUV(index=1), degrees=0.0),
            scale=(3.0, 3.0),
        )
        tex = self._fake_sample()
        # rebuild with UV
        from io_import_forza_carbin.materials.forza_ir import (
            SamplerState,
            TextureSampleExpression,
        )
        from io_import_forza_carbin.materials.texture_source import (
            ResolvedTextureSource,
            TextureSourceKind,
        )

        src = ResolvedTextureSource(
            kind=TextureSourceKind.LOOSE_FILE,
            original_path="GAME:\\t.swatchbin",
            canonical_game_path="media/t.swatchbin",
            filesystem_path="/tmp/t.swatchbin",
            archive_path=None,
            archive_member=None,
            exists=True,
            failure=None,
        )
        tex = TextureSample(
            sample=TextureSampleExpression(
                binding_name_hash=0x85E937A9,
                source=src,
                uv=uv,
                channels=("r", "g", "b"),
                color_space="sRGB",
                sampler=SamplerState(),
            )
        )
        tinted = Multiply(
            a=tex,
            b=ConstantColor(rgba=(0.007, 0.007, 0.007, 1.0)),
        )
        rmao = self._fake_sample(path="GAME:\\rmao.swatchbin")
        metal = Channel(source=rmao, channel="g")
        ao = Channel(source=rmao, channel="b")
        mixed = Mix(a=tinted, b=tex, factor=metal)
        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            base_color=Multiply(a=mixed, b=ao),
            roughness=Channel(source=rmao, channel="r"),
            metallic=metal,
            ambient_occlusion=ao,
        )
        plan = graph_build_plan_from_ir(ir)
        ops = [s["op"] for s in plan]
        self.assertIn("multiply_base_color_tint", ops)
        self.assertIn("tint_mode_metal_lerp", ops)
        tint_step = next(s for s in plan if s["op"] == "multiply_base_color_tint")
        self.assertAlmostEqual(tint_step["rgba"][0], 0.007, places=5)
        base_slot = next(
            s["slot"] for s in plan if s.get("op") == "texture" and s.get("binds") == "base_color"
        )
        self.assertEqual(base_slot["tiling"], [3.0, 3.0])
        self.assertEqual(base_slot["texcoord"], "TEXCOORD1")

    def test_no_filename_semantic_in_evaluator_source(self):
        import inspect
        from io_import_forza_carbin.materials import eval_car_standard as mod

        src = inspect.getsource(mod)
        self.assertNotIn("kwa430t", src.lower())
        self.assertNotIn("painted_metal_smooth", src.lower())

    def test_contract_json_documents_tint(self):
        data = load_contract(CAR_STANDARD_SHADERBIN_SHA256)
        self.assertIn("base_color_tint_multiply", data["production"]["approved_rules"])
        self.assertIn("tint", data["outputs"]["base_color"])
        self.assertIn("shared_u_v_tiling_branch", data["uv"])
        self.assertEqual(
            data["uv"]["shared_u_v_tiling_branch"]["status"], "corpus_proven"
        )
        self.assertEqual(
            data["uv"]["per_binding_override_true_branches"]["status"],
            "pending_review",
        )

    def test_override_tiling_true_rejected_in_production(self):
        from io_import_forza_carbin.materials.eval_car_standard import _uv_expr_for_slot
        from io_import_forza_carbin.materials.model import ResolvedTextureSlot

        slot = ResolvedTextureSlot(
            role="base_color",
            path="GAME:\\t.swatchbin",
            texcoord="TEXCOORD1",
            param_hash=1,
            param_name="BaseColorAlpha",
        )
        params = self._params()
        params[0xB8E61E16] = SimpleNamespace(type=3, value=True, path="", samp=b"")
        expr, reject = _uv_expr_for_slot(
            slot, params, production_mode=True, revision="b1.75"
        )
        self.assertIsNone(expr)
        self.assertIn("not production-approved", reject or "")

    def test_tint_mode_numerical_observed_branches(self):
        from io_import_forza_carbin.materials.eval_car_standard import (
            evaluate_tint_mode_rgb,
        )

        tex = (0.8, 0.6, 0.4)
        tint = (0.1, 0.2, 0.3)
        mult = 1.0

        def lerp(a, b, t):
            return tuple(ai + t * (bi - ai) for ai, bi in zip(a, b))

        def tinted():
            return tuple(max(0.0, min(1.0, t * c * mult)) for t, c in zip(tex, tint))

        # [1,1]: lerp(tinted, tex, metal)
        for metal in (0.0, 0.5, 1.0):
            got = evaluate_tint_mode_rgb(
                texture_rgb=tex,
                tint_rgb=tint,
                tint_multiplier=mult,
                tint_mode=(1.0, 1.0, 0.0, 0.0),
                metallic=metal,
            )
            exp = lerp(tinted(), tex, metal)
            for g, e in zip(got, exp):
                self.assertAlmostEqual(g, e, places=6, msg=f"[1,1] metal={metal}")

        # [1,0]: lerp(tex, tinted, metal) == lerp(tinted, tex, 1-metal)
        for metal in (0.0, 0.5, 1.0):
            got = evaluate_tint_mode_rgb(
                texture_rgb=tex,
                tint_rgb=tint,
                tint_multiplier=mult,
                tint_mode=(1.0, 0.0, 0.0, 0.0),
                metallic=metal,
            )
            exp = lerp(tex, tinted(), metal)
            for g, e in zip(got, exp):
                self.assertAlmostEqual(g, e, places=6, msg=f"[1,0] metal={metal}")
            # Guard against reversed lerp: at metal=0 must equal texture, not tinted
            if metal == 0.0:
                self.assertAlmostEqual(got[0], tex[0], places=6)
                self.assertNotAlmostEqual(got[0], tinted()[0], places=6)

        # [0,0]: tinted only
        for metal in (0.0, 0.5, 1.0):
            got = evaluate_tint_mode_rgb(
                texture_rgb=tex,
                tint_rgb=tint,
                tint_multiplier=mult,
                tint_mode=(0.0, 0.0, 0.0, 0.0),
                metallic=metal,
            )
            for g, e in zip(got, tinted()):
                self.assertAlmostEqual(g, e, places=6, msg=f"[0,0] metal={metal}")


class CarStandardB175Id39RadiatorRegressionTests(unittest.TestCase):
    """Permanent fixture: fh6|ID39_bumperFrame_radiator|v4-e49b0895bfda9099.

    Asserts proven MatI/DXIL shared UV for this fingerprint — no material-name
    or filename exception. Previous incorrect transform was ScaleUV(1,1).
    """

    TARGET_KEY = "fh6|ID39_bumperFrame_radiator|v4-e49b0895bfda9099"
    MEDIA = r"C:/XboxGames/Forza Horizon 6/Content/media"
    MODEL = (
        r"GAME:\Media\Cars\GMA_T50_22\Scene\Exterior\Trunk\trunkLR_a.modelbin"
    )
    EXPECT_BC = "ptn_grillhexagon_001_basecoloralpha_txy2a5b.swatchbin"
    EXPECT_RMAO = "ptn_grillhexagon_001_rmao_w5ki5pk.swatchbin"
    EXPECT_NRM = "ptn_grillhexagon_001_nrml_uzclgxw.swatchbin"

    def test_synthetic_scale_30_not_identity(self):
        """Numerical guard: MatI U/V=30 must not collapse to previous (1,1)."""
        from io_import_forza_carbin.materials.eval_car_standard import _uv_expr_for_slot
        from io_import_forza_carbin.materials.forza_ir import ScaleUV, MeshUV
        from io_import_forza_carbin.materials.model import ResolvedTextureSlot
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        params = {
            SPN.UTiling: SimpleNamespace(type=2, value=30.0, path="", samp=b""),
            SPN.VTiling: SimpleNamespace(type=2, value=30.0, path="", samp=b""),
            SPN.UVOrientation: SimpleNamespace(type=2, value=0.0, path="", samp=b""),
            0xB8E61E16: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0xECCEB8F9: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0x1B003865: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0x090ABF6B: SimpleNamespace(type=3, value=False, path="", samp=b""),
            0x402B8ED0: SimpleNamespace(type=3, value=False, path="", samp=b""),
        }
        for role, pname in (
            ("base_color", "BaseColorAlpha"),
            ("rmao", "RoughMetalAO"),
            ("normal", "Normal"),
        ):
            slot = ResolvedTextureSlot(
                role=role,
                path=f"GAME:\\{pname}.swatchbin",
                texcoord="TEXCOORD1",
                param_hash=1,
                param_name=pname,
            )
            expr, reject = _uv_expr_for_slot(
                slot, params, production_mode=True, revision="b1.75"
            )
            self.assertIsNone(reject, msg=pname)
            self.assertIsInstance(expr, ScaleUV)
            self.assertEqual(expr.scale, (30.0, 30.0), msg=pname)
            self.assertNotEqual(expr.scale, (1.0, 1.0), msg=pname)
            # Walk to MeshUV
            node = expr.source
            while not isinstance(node, MeshUV):
                node = node.source
            self.assertEqual(node.index, 1, msg=pname)

    def test_no_radiator_or_id39_name_rules_in_evaluator(self):
        import inspect
        from io_import_forza_carbin.materials import eval_car_standard as mod

        src = inspect.getsource(mod).lower()
        self.assertNotIn("id39_bumperframe_radiator", src)
        self.assertNotIn("radiator_basecoloralpha", src)
        self.assertNotIn("grillhexagon", src)
        self.assertNotIn("e49b0895bfda9099", src)

    @unittest.skipUnless(
        os.path.isdir(r"C:/XboxGames/Forza Horizon 6/Content/media/cars"),
        "FH6 media not mounted",
    )
    def test_live_mati_instance_fingerprint_and_bindings(self):
        from io_import_forza_carbin.geometry import Modelbin
        from io_import_forza_carbin.materials.eval_car_standard import (
            evaluate_car_standard,
        )
        from io_import_forza_carbin.materials.instance_key import material_instance_key
        from io_import_forza_carbin.materials.ir_compiler import graph_build_plan_from_ir
        from io_import_forza_carbin.parsing.binary import BinaryStream
        from io_import_forza_carbin.parsing.paths import GamePathResolver

        resolver = GamePathResolver(self.MEDIA)
        mb = Modelbin()
        mb.deserialize(
            BinaryStream.from_path(resolver.resolve(self.MODEL)),
            1,
            resolver,
            True,
        )
        target = None
        for pm in mb.materials:
            if pm.obj is None:
                continue
            if material_instance_key(pm, game_key="fh6") == self.TARGET_KEY:
                target = pm
                break
        self.assertIsNotNone(target, "ID39 fingerprint missing on trunkLR_a")
        key = material_instance_key(target, game_key="fh6")
        self.assertEqual(key, self.TARGET_KEY)

        mat = target.obj
        self.assertEqual((mat.shader_name or "").lower(), "car_standard")
        ir = evaluate_car_standard(
            name=target.name,
            material=mat,
            resolver=resolver,
            media_root=self.MEDIA,
            production_mode=True,
        )
        self.assertFalse(ir.rejection_reasons)
        self.assertEqual(ir.shader.shaderbin_sha256, CAR_STANDARD_SHADERBIN_SHA256)
        plan = graph_build_plan_from_ir(ir)
        by_role = {}
        for step in plan:
            if step.get("op") != "texture":
                continue
            slot = step["slot"]
            by_role[slot["role"]] = slot
        self.assertIn("base_color", by_role)
        self.assertIn("rmao", by_role)
        self.assertIn("normal", by_role)
        self.assertTrue(by_role["base_color"]["path"].lower().endswith(self.EXPECT_BC))
        self.assertTrue(by_role["rmao"]["path"].lower().endswith(self.EXPECT_RMAO))
        self.assertTrue(by_role["normal"]["path"].lower().endswith(self.EXPECT_NRM))
        for role in ("base_color", "rmao", "normal"):
            self.assertEqual(by_role[role]["texcoord"], "TEXCOORD1")
            self.assertEqual(by_role[role]["tiling"], [30.0, 30.0])
        # Radiator / zigzag must not be bound on this instance
        for slot in by_role.values():
            low = (slot.get("path") or "").lower()
            self.assertNotIn("radiator_basecoloralpha", low)
            self.assertNotIn("zigzag_002", low)

        # Coverage reopen: AlphaTransparency=false → opaque; product modulates
        # Base Color; Principled Alpha must stay unused (no packed-alpha cutout).
        self.assertIsNone(
            ir.opacity,
            "ID39 AlphaTransparency=false must not drive Principled Alpha",
        )
        self.assertIn("alpha", by_role)
        self.assertTrue(
            by_role["alpha"]["path"].lower().endswith(
                "ptn_grillhexagon_001_alpha_mszha21.swatchbin"
            )
        )
        cov = [
            e
            for e in ir.evidence
            if getattr(e, "kind", "") == "shading_attenuation"
        ]
        self.assertTrue(
            cov,
            "expected saturate(Alpha.r*BaseColorAlpha.a) attenuation evidence",
        )
        self.assertIsNotNone(ir.shading_attenuation)
        self.assertIsNone(ir.opacity)
        self.assertTrue(
            any(
                s.get("op") == "multiply_base_color_shading_attenuation"
                for s in plan
            ),
            "graph plan must include labelled BaseColor×attenuation approx",
        )
        # Must not enable CLIP/BLEND transparency for this MatI
        for step in plan:
            if step.get("op") == "configure_transparency":
                self.assertEqual(step.get("mode"), "OPAQUE")
            self.assertNotEqual(step.get("op"), "alpha_clip")
            self.assertNotEqual(step.get("op"), "link_alpha_and_transparent_mix")
        # Per-binding UV scale 30 on Alpha as well
        self.assertEqual(by_role["alpha"]["texcoord"], "TEXCOORD1")
        self.assertEqual(by_role["alpha"]["tiling"], [30.0, 30.0])

        # Mask orientation: product must darken cells (direct, not inverted)
        from io_import_forza_carbin.materials.forza_ir import (
            Clamp,
            Multiply,
            Channel,
            TextureSample,
            ScaleUV,
        )

        expr = ir.shading_attenuation.expression
        self.assertIsInstance(expr, Clamp)
        self.assertEqual(expr.lo, 0.0)
        self.assertEqual(expr.hi, 1.0)
        prod = expr.source
        self.assertIsInstance(prod, Multiply)
        chs = []
        for side in (prod.a, prod.b):
            self.assertIsInstance(side, Channel)
            chs.append(side.channel.lower())
        self.assertTrue(
            ({"x", "a"} <= set(chs)) or ({"r", "a"} <= set(chs)),
            f"product must be Alpha.r × BC.a, got channels {chs}",
        )

        # Structural UV reuse: Alpha / BC / Normal / RMAO share one ScaleUV.
        uv_ids = set()
        for role in ("base_color", "alpha", "normal", "rmao"):
            slot = by_role[role]
            self.assertEqual(slot["tiling"], [30.0, 30.0], msg=role)
            self.assertEqual(slot["texcoord"], "TEXCOORD1", msg=role)
        samples = []
        for side in (prod.a, prod.b):
            samples.append(side.source)
        # Walk IR texture samples for BC/Normal/RMAO via plan is enough;
        # prove attenuation product sides share UV with base color sample.
        from io_import_forza_carbin.materials.ir_compiler import _find_texture_sample

        bc_ts = _find_texture_sample(ir.base_color)
        self.assertIsNotNone(bc_ts)
        shared_uv = bc_ts.sample.uv
        self.assertIsInstance(shared_uv, ScaleUV)
        self.assertEqual(shared_uv.scale, (30.0, 30.0))
        for side in (prod.a, prod.b):
            ts = side.source
            self.assertIsInstance(ts, TextureSample)
            self.assertIs(
                ts.sample.uv,
                shared_uv,
                "Alpha/BC attenuation samples must reuse shared ScaleUV IR node",
            )
        for attr in (ir.normal, ir.roughness):
            ts = _find_texture_sample(attr)
            self.assertIsNotNone(ts)
            self.assertIs(
                ts.sample.uv,
                shared_uv,
                "Normal/RMAO must reuse shared ScaleUV IR node",
            )
        del uv_ids


class CarStandardOpaqueCoverageGateTests(unittest.TestCase):
    """AlphaTransparency=false must not CLIP; no Alpha TXMP ⇒ no BC.a cutout."""

    def test_alpha_mode_explicit_false_is_opaque_even_with_alpha_map(self):
        from io_import_forza_carbin.materials.resolver import _alpha_mode
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        params = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=False, path="", samp=b""
            ),
        }
        self.assertEqual(_alpha_mode(params, has_alpha=True), "OPAQUE")

    def test_alpha_mode_true_is_clip(self):
        from io_import_forza_carbin.materials.resolver import _alpha_mode
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        params = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=True, path="", samp=b""
            ),
        }
        self.assertEqual(_alpha_mode(params, has_alpha=True), "CLIP")

    def test_mask_orientation_product_darkens_cells_not_bars(self):
        """Numerical: low product at hex cell, high at solid bar — not inverted."""
        import json
        from pathlib import Path

        p = Path(
            r"H:\Documents\Forza Import\reports\material-conformance\runs"
            r"\2026-07-21_id39-grille-coverage_reopen\data"
            r"\mask_orientation_samples.json"
        )
        if not p.is_file():
            self.skipTest("mask orientation artifact missing")
        data = json.loads(p.read_text(encoding="utf-8"))
        by_label = {s["label"]: s for s in data["samples"]}
        cell = by_label["dark_hex_cell_center"]
        bar = by_label["solid_bar_center"]
        self.assertLess(cell["product"], 0.05)
        self.assertGreater(bar["product"], 0.95)
        self.assertLess(cell["product"], bar["product"])
        self.assertIn("DIRECTLY", data.get("orientation_verdict", ""))
        self.assertIn("not_inverted", data.get("orientation_verdict", ""))


class CarStandardNegativeAlphaRegressionTests(unittest.TestCase):
    """Packed alpha / Alpha.r alone must not invent Principled transparency."""

    def test_alpha_transparency_false_never_clip_from_has_alpha(self):
        from io_import_forza_carbin.materials.resolver import _alpha_mode
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        params = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=False, path="", samp=b""
            ),
        }
        # Even if an Alpha TXMP is present, explicit false stays OPAQUE.
        self.assertEqual(_alpha_mode(params, has_alpha=True), "OPAQUE")
        self.assertEqual(_alpha_mode(params, has_alpha=False), "OPAQUE")

    def test_shading_attenuation_field_is_not_opacity(self):
        from io_import_forza_carbin.materials.forza_ir import (
            ConstantScalar,
            ForzaMaterialIR,
            ShaderIdentity,
            ShadingAttenuation,
        )

        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            shading_attenuation=ShadingAttenuation(
                expression=ConstantScalar(value=0.25),
            ),
            opacity=None,
        )
        self.assertIsNone(ir.opacity)
        self.assertIsNotNone(ir.shading_attenuation)
        self.assertEqual(ir.shading_attenuation.expression.value, 0.25)

    def test_has_alpha_alone_without_transparency_bool_is_opaque_fail_closed(self):
        """Alpha TXMP presence alone must not invent CLIP (game-file contracts)."""
        from io_import_forza_carbin.materials.resolver import _alpha_mode
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        # No AlphaTransparency param + has_alpha → OPAQUE (not heuristic CLIP).
        self.assertEqual(_alpha_mode({}, has_alpha=True), "OPAQUE")
        # Explicit true → CLIP (car_standard contract true branch).
        params_true = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=True, path="", samp=b""
            ),
        }
        self.assertEqual(_alpha_mode(params_true, has_alpha=True), "CLIP")
        self.assertEqual(_alpha_mode(params_true, has_alpha=False), "CLIP")

    def test_basecolor_alpha_channel_alone_does_not_imply_transparency_mode(self):
        """Presence of BC.a texture data is not an alpha-mode signal by itself."""
        from io_import_forza_carbin.materials.resolver import _alpha_mode
        from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN

        # has_alpha=False mimics "no Alpha TXMP" — BC.a alone must stay OPAQUE
        # when AlphaTransparency is false or absent.
        params_false = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=False, path="", samp=b""
            ),
        }
        self.assertEqual(_alpha_mode(params_false, has_alpha=False), "OPAQUE")
        self.assertEqual(_alpha_mode({}, has_alpha=False), "OPAQUE")

    def test_graph_plan_opaque_painted_has_no_alpha_link(self):
        """Unrelated opaque painted path: no Principled Alpha wiring ops."""
        from io_import_forza_carbin.materials.forza_ir import (
            ConstantColor,
            ForzaMaterialIR,
            ShaderIdentity,
        )
        from io_import_forza_carbin.materials.ir_compiler import graph_build_plan_from_ir

        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                permutation="CarLightScenario",
            ),
            base_color=ConstantColor(rgba=(0.4, 0.4, 0.4, 1.0)),
            opacity=None,
            shading_attenuation=None,
        )
        plan = graph_build_plan_from_ir(ir)
        ops = [s.get("op") for s in plan]
        self.assertNotIn("link_alpha_and_transparent_mix", ops)
        self.assertNotIn("alpha_clip", ops)
        self.assertNotIn("multiply_base_color_shading_attenuation", ops)
        for step in plan:
            if step.get("op") == "configure_transparency":
                self.assertEqual(step.get("mode"), "OPAQUE")

    def test_carbon_evaluator_source_has_no_id39_alpha_exception(self):
        import inspect
        from io_import_forza_carbin.materials import eval_car_carbonfiber as mod

        src = inspect.getsource(mod).lower()
        self.assertNotIn("id39", src)
        self.assertNotIn("grillhexagon", src)
        self.assertNotIn("bumperframe_radiator", src)


class CarStandardId40DoorTagsCutoutTests(unittest.TestCase):
    """fh6|ID40_INT_doorTags|v4-ce6e4a28ffaee1c0 — AlphaTransparency=true cutout.

    Flat white Alpha TXMP × BaseColorAlpha.a must drive CLIP; Alpha.r alone
    leaves a solid quad (the regression that hid door-tag decals).
    """

    TARGET_KEY = "fh6|ID40_INT_doorTags|v4-ce6e4a28ffaee1c0"
    MEDIA = r"C:/XboxGames/Forza Horizon 6/Content/media"
    MODEL = (
        r"GAME:\Media\Cars\GMA_T50_22\Scene\Exterior\Doors\doorLF_a.modelbin"
    )

    def test_no_doortags_name_exception_in_evaluator(self):
        import inspect
        from io_import_forza_carbin.materials import eval_car_standard as mod

        src = inspect.getsource(mod).lower()
        self.assertNotIn("id40_int_doortags", src)
        self.assertNotIn("doortags", src)
        self.assertNotIn("ce6e4a28ffaee1c0", src)

    @unittest.skipUnless(
        os.path.isdir(r"C:/XboxGames/Forza Horizon 6/Content/media/cars"),
        "FH6 media not mounted",
    )
    def test_live_doortags_cutout_uses_alpha_times_bc_a(self):
        from io_import_forza_carbin.geometry import Modelbin
        from io_import_forza_carbin.materials.eval_car_standard import (
            evaluate_car_standard,
        )
        from io_import_forza_carbin.materials.instance_key import material_instance_key
        from io_import_forza_carbin.materials.ir_compiler import (
            _opacity_is_alpha_times_bc_a,
            graph_build_plan_from_ir,
        )
        from io_import_forza_carbin.materials.forza_ir import Channel, Clamp, Multiply
        from io_import_forza_carbin.parsing.binary import BinaryStream
        from io_import_forza_carbin.parsing.paths import GamePathResolver

        resolver = GamePathResolver(self.MEDIA)
        mb = Modelbin()
        mb.deserialize(
            BinaryStream.from_path(resolver.resolve(self.MODEL)),
            1,
            resolver,
            True,
        )
        target = next(
            (
                pm
                for pm in mb.materials
                if pm.obj
                and material_instance_key(pm, game_key="fh6") == self.TARGET_KEY
            ),
            None,
        )
        self.assertIsNotNone(target)
        ir = evaluate_car_standard(
            name=target.name,
            material=target.obj,
            resolver=resolver,
            media_root=self.MEDIA,
            production_mode=True,
            revision="b1.75",
        )
        self.assertFalse(ir.rejection_reasons)
        self.assertIsNotNone(ir.opacity)
        self.assertIsNone(ir.shading_attenuation)
        self.assertTrue(
            _opacity_is_alpha_times_bc_a(ir.opacity),
            "door-tag cutout must be Alpha.r × BC.a product",
        )
        # Outer CLIP clamp threshold 0.5 (ShadowDepth contract)
        self.assertIsInstance(ir.opacity, Clamp)
        self.assertAlmostEqual(float(ir.opacity.lo), 0.5)
        plan = graph_build_plan_from_ir(ir)
        ops = [s.get("op") for s in plan]
        self.assertIn("multiply_alpha_by_basecolor_a", ops)
        self.assertIn("alpha_clip", ops)
        self.assertIn("link_alpha_and_transparent_mix", ops)
        for step in plan:
            if step.get("op") == "configure_transparency":
                self.assertEqual(step.get("mode"), "CLIP")
            if step.get("op") == "texture" and step.get("binds") == "alpha":
                path = (step["slot"].get("path") or "").lower()
                self.assertIn("flat_texture_white", path)
            if step.get("op") == "texture" and step.get("binds") == "base_color":
                path = (step["slot"].get("path") or "").lower()
                self.assertIn("doortags_basecoloralpha", path)


if __name__ == "__main__":
    unittest.main()
