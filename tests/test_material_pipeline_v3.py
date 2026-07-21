"""Unit tests for the clean material contract."""

from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.instance_key import material_instance_key
from io_import_forza_carbin.materials.pipeline_v3 import (
    MaterialTranslateError,
    _alpha_mode,
    _binding_uv,
)
from io_import_forza_carbin.materials.translate import translator_for
from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN


def _param(type_, value=None, path=""):
    return SimpleNamespace(type=type_, value=value, path=path, samp=b"")


class CleanPipelineTests(unittest.TestCase):
    def test_ambiguous_uv_is_unsupported(self):
        binding = SimpleNamespace(
            uv_semantic=None,
            uv_semantics_all=[0, 1],
            gate_bool_hashes=[],
        )
        self.assertIsNone(_binding_uv(binding, {}))
        binding.uv_semantics_all = [1]
        self.assertEqual(_binding_uv(binding, {}), 1)

    def test_tiling_gate_does_not_invent_uv(self):
        """MAT006: false Override*Tiling must not pick min(uv)."""
        gate = 0xB8E61E16  # OverrideBaseColorTilingOnOff
        binding = SimpleNamespace(
            uv_semantic=None,
            uv_semantics_all=[0, 1],
            gate_bool_hashes=[gate],
        )
        params = {gate: _param(3, False)}
        self.assertIsNone(_binding_uv(binding, params, txmp_name="BaseColorAlpha"))

    def test_uvchoice_resolves_multi_uv(self):
        from io_import_forza_carbin.materials.capabilities import (
            UV_CHOICE_ON_CH1_OFF_CH2,
        )

        binding = SimpleNamespace(
            uv_semantic=None,
            uv_semantics_all=[0, 1],
            gate_bool_hashes=[],
        )
        on = {UV_CHOICE_ON_CH1_OFF_CH2: _param(3, True)}
        off = {UV_CHOICE_ON_CH1_OFF_CH2: _param(3, False)}
        self.assertEqual(
            _binding_uv(binding, on, txmp_name="BaseColorAlpha"), 0
        )
        self.assertEqual(
            _binding_uv(binding, off, txmp_name="BaseColorAlpha"), 1
        )

    def test_authored_alpha_modes(self):
        test = {SPN.UseAlphaTestBool: _param(3, True)}
        blend = {SPN.UseAlphaBlendBool: _param(3, True)}
        self.assertEqual(_alpha_mode(test, True), "CLIP")
        self.assertEqual(_alpha_mode(blend, True), "BLEND")
        # has_alpha alone must not invent CLIP (game-file alpha contracts).
        self.assertEqual(_alpha_mode({}, True), "OPAQUE")
        self.assertEqual(_alpha_mode({}, False), "OPAQUE")

    def test_fh5_has_no_cross_game_fallback(self):
        with self.assertRaises(MaterialTranslateError):
            translator_for("fh5")

    def test_instance_key_hashes_complete_material_state(self):
        def pm(path):
            obj = SimpleNamespace(
                shader_name="car_livery",
                parameters={0x698CA64F: _param(6, path=path)},
            )
            return SimpleNamespace(name="Label_CH1", obj=obj)

        a = material_instance_key(pm("badge_a.swatchbin"), game_key="fh6")
        b = material_instance_key(pm("badge_b.swatchbin"), game_key="fh6")
        self.assertNotEqual(a, b)
        self.assertIn("|v4-", a)


if __name__ == "__main__":
    unittest.main()
