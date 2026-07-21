"""Unit tests for FH5/FH6 shader archive candidate ordering."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
import zipfile

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials import shader_bindings as sb  # noqa: E402


class ShaderArchiveTests(unittest.TestCase):
    def test_fh6_prefers_per_shader_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            shaders = os.path.join(tmp, "cars", "_library", "shaders")
            os.makedirs(shaders)
            per = os.path.join(shaders, "car_standard.zip")
            mono = os.path.join(shaders, "Shaders.zip")
            with zipfile.ZipFile(per, "w") as zf:
                zf.writestr("car_standard.shaderbin", b"per")
            with zipfile.ZipFile(mono, "w") as zf:
                zf.writestr("car_standard.shaderbin", b"mono")
            path, names = sb._find_zip_and_members(tmp, "car_standard", game_key="fh6")
            self.assertEqual(os.path.basename(path).lower(), "car_standard.zip")
            self.assertTrue(any(n.endswith("car_standard.shaderbin") for n in names))

    def test_fh5_accepts_monolithic_shaders_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            shaders = os.path.join(tmp, "cars", "_library", "shaders")
            os.makedirs(shaders)
            mono = os.path.join(shaders, "Shaders.zip")
            with zipfile.ZipFile(mono, "w") as zf:
                zf.writestr("badge_ch1.shaderbin", b"mono")
            path, _names = sb._find_zip_and_members(tmp, "badge_ch1", game_key="fh5")
            self.assertEqual(os.path.basename(path).lower(), "shaders.zip")


if __name__ == "__main__":
    unittest.main()
