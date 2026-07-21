"""UV Conformance Foundation unit tests."""

from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

# Package shim (addon __init__ imports bpy).
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "io_import_forza_carbin"
_PKG_PARENT = _ADDON.parent  # …/addon
if str(_PKG_PARENT) not in sys.path:
    sys.path.insert(0, str(_PKG_PARENT))
if "io_import_forza_carbin" not in sys.modules:
    _pkg = types.ModuleType("io_import_forza_carbin")
    _pkg.__path__ = [str(_ADDON)]
    sys.modules["io_import_forza_carbin"] = _pkg


class UVFoundationPrecedenceTests(unittest.TestCase):
    def test_mati_uv_wins_over_unswitched_override_vec2(self):
        from io_import_forza_carbin.materials.uv import (
            UV_NAMEHASH_U_TILING,
            UV_NAMEHASH_V_TILING,
            UVProofStatus,
            resolve_uv_scale,
        )

        # Fabric-like: Override vec2 (1,1) appears first in DXIL hash list.
        params = {
            0x1144D400: SimpleNamespace(type=11, value=(1.0, 1.0)),
            UV_NAMEHASH_U_TILING: SimpleNamespace(type=2, value=3.0),
            UV_NAMEHASH_V_TILING: SimpleNamespace(type=2, value=3.0),
            0xB8E61E16: SimpleNamespace(type=3, value=False),
        }
        hashes = (0x1144D400, UV_NAMEHASH_U_TILING, UV_NAMEHASH_V_TILING)
        r = resolve_uv_scale(
            params,
            param_name="BaseColorAlpha",
            tiling_cb_hashes=hashes,
        )
        self.assertEqual(r.scale, (3.0, 3.0))
        self.assertEqual(r.proof_status, UVProofStatus.PROVEN_TRANSFORM)

    def test_missing_tiling_is_unresolved_not_identity(self):
        from io_import_forza_carbin.materials.uv import UVProofStatus, resolve_uv_scale

        r = resolve_uv_scale({}, param_name="BaseColorAlpha", tiling_cb_hashes=())
        self.assertIsNone(r.scale)
        self.assertEqual(r.proof_status, UVProofStatus.UNRESOLVED)

    def test_proven_identity_when_mati_is_one(self):
        from io_import_forza_carbin.materials.uv import (
            UV_NAMEHASH_U_TILING,
            UV_NAMEHASH_V_TILING,
            UVProofStatus,
            resolve_uv_scale,
        )

        params = {
            UV_NAMEHASH_U_TILING: SimpleNamespace(type=2, value=1.0),
            UV_NAMEHASH_V_TILING: SimpleNamespace(type=2, value=1.0),
        }
        r = resolve_uv_scale(params)
        self.assertEqual(r.scale, (1.0, 1.0))
        self.assertEqual(r.proof_status, UVProofStatus.PROVEN_IDENTITY)

    def test_nontrivial_scale_math_not_inverse(self):
        from io_import_forza_carbin.materials.uv import eval_uv_transform_point

        out = eval_uv_transform_point((0.2, 0.3), scale=(10.0, 20.0))
        self.assertEqual(out, (2.0, 6.0))

    def test_independent_u_v(self):
        from io_import_forza_carbin.materials.uv import eval_uv_transform_point

        out = eval_uv_transform_point((0.5, 0.25), scale=(4.0, 8.0))
        self.assertEqual(out, (2.0, 2.0))


class UVFoundationAlcantaraFixtureTests(unittest.TestCase):
    TARGET = "fh6|ID04_leather_embossed_alpha|v4-b8789c2fe616b01f"
    MEDIA = r"C:/XboxGames/Forza Horizon 6/Content/media"
    MODEL = (
        r"GAME:\Media\Cars\GMA_T50_22\Scene\Interior\Doors\doorCardLF_a.modelbin"
    )

    def test_no_alcantara_name_rule_in_uv_module(self):
        import inspect
        from io_import_forza_carbin.materials import uv as mod

        src = inspect.getsource(mod).lower()
        self.assertNotIn("alcantara", src)
        self.assertNotIn("leather_embossed", src)
        self.assertNotIn("b8789c2fe616b01f", src)

    @unittest.skipUnless(
        os.path.isdir(r"C:/XboxGames/Forza Horizon 6/Content/media/cars"),
        "FH6 media not mounted",
    )
    def test_live_alcantara_fabric_scale_3_not_identity(self):
        from io_import_forza_carbin.geometry import Modelbin
        from io_import_forza_carbin.materials.instance_key import material_instance_key
        from io_import_forza_carbin.materials.resolver import MaterialCapabilityResolver
        from io_import_forza_carbin.parsing.binary import BinaryStream
        from io_import_forza_carbin.parsing.paths import GamePathResolver

        resolver = GamePathResolver(self.MEDIA)
        mb = Modelbin()
        mb.deserialize(
            BinaryStream.from_path(resolver.resolve(self.MODEL)), 1, resolver, True
        )
        target = next(
            (
                pm
                for pm in mb.materials
                if pm.obj and material_instance_key(pm, "fh6") == self.TARGET
            ),
            None,
        )
        self.assertIsNotNone(target)
        self.assertEqual((target.obj.shader_name or "").lower(), "car_standard_fabric")
        res = MaterialCapabilityResolver(
            media_root=self.MEDIA, game_key="fh6"
        ).resolve(
            name=target.name,
            material=target.obj,
            resolver=resolver,
            derive_slots=True,
        )
        self.assertTrue(res.is_selected)
        cap = res.resolved.capability
        for slot in (
            cap.base_color_source.texture,
            cap.normal_map,
            cap.rmao_map,
            cap.alpha_map,
        ):
            self.assertIsNotNone(slot)
            self.assertEqual(slot.tiling, (3.0, 3.0), msg=slot.param_name)
            self.assertNotEqual(slot.tiling, (1.0, 1.0))
            self.assertEqual(slot.texcoord, "TEXCOORD1")


class UVFoundationId39StillScaledTests(unittest.TestCase):
    """ID39 grille must remain 30× after foundation changes."""

    TARGET = "fh6|ID39_bumperFrame_radiator|v4-e49b0895bfda9099"
    MEDIA = r"C:/XboxGames/Forza Horizon 6/Content/media"
    MODEL = (
        r"GAME:\Media\Cars\GMA_T50_22\Scene\Exterior\Trunk\trunkLR_a.modelbin"
    )

    @unittest.skipUnless(
        os.path.isdir(r"C:/XboxGames/Forza Horizon 6/Content/media/cars"),
        "FH6 media not mounted",
    )
    def test_id39_still_scale_30(self):
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
            BinaryStream.from_path(resolver.resolve(self.MODEL)), 1, resolver, True
        )
        target = next(
            pm
            for pm in mb.materials
            if pm.obj and material_instance_key(pm, "fh6") == self.TARGET
        )
        ir = evaluate_car_standard(
            name=target.name,
            material=target.obj,
            resolver=resolver,
            media_root=self.MEDIA,
            production_mode=True,
            revision="b1.75",
        )
        plan = graph_build_plan_from_ir(ir)
        for step in plan:
            if step.get("op") != "texture":
                continue
            self.assertEqual(step["slot"]["tiling"], [30.0, 30.0])


if __name__ == "__main__":
    unittest.main()
