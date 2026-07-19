"""Unit tests for paint/glass SPN helpers and game install Media resolution."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from types import SimpleNamespace

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.registry import (  # noqa: E402
    params_have_glass_scalars,
    params_have_paint_scalars,
)
from io_import_forza_carbin.materials.translate import translator_for  # noqa: E402
from io_import_forza_carbin.materials.builder import MaterialTranslateError  # noqa: E402
from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN  # noqa: E402
from io_import_forza_carbin.parsing.paths import find_media_root  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_paint_via_spn_hash(self):
        params = {SPN.PaintColorColorParam: SimpleNamespace(type=0, value=(1, 0, 0, 1))}
        self.assertTrue(params_have_paint_scalars(params))
        self.assertFalse(params_have_paint_scalars({}))

    def test_glass_via_spn_hash(self):
        params = {SPN.GlassOpacityFloat: SimpleNamespace(type=2, value=0.3)}
        self.assertTrue(params_have_glass_scalars(params))
        self.assertFalse(params_have_glass_scalars({}))

    def test_translator_rejects_unknown(self):
        with self.assertRaises(MaterialTranslateError):
            translator_for("unknown")


class MediaRootTests(unittest.TestCase):
    def test_install_content_and_media_levels(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = os.path.join(tmp, "Content", "media")
            cars = os.path.join(media, "cars")
            os.makedirs(cars)
            install = tmp
            content = os.path.join(tmp, "Content")
            self.assertEqual(find_media_root(install), media)
            self.assertEqual(find_media_root(content), media)
            self.assertEqual(find_media_root(media), media)
            self.assertIsNone(find_media_root(cars))


if __name__ == "__main__":
    unittest.main()
