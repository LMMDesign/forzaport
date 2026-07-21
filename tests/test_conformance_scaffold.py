"""Smoke tests for conformance corpus helpers (no media required for unit parts)."""

from __future__ import annotations

import os
import sys
import types
import unittest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.conformance import FAILURE_CLASSES  # noqa: E402
from io_import_forza_carbin.materials.forza_ir import (  # noqa: E402
    ForzaMaterialIR,
    MeshUV,
    ShaderIdentity,
)
from io_import_forza_carbin.materials.shader_contract_registry import (  # noqa: E402
    contract_status_for,
)
from io_import_forza_carbin.materials.forza_ir import ContractStatus  # noqa: E402


class ConformanceScaffoldTests(unittest.TestCase):
    def test_failure_taxonomy_complete(self):
        self.assertIn("WRONG_ACTIVE_BINDING", FAILURE_CLASSES)
        self.assertIn("UNSUPPORTED_SHADER_FAMILY", FAILURE_CLASSES)
        self.assertEqual(len(FAILURE_CLASSES), 13)

    def test_ir_types_constructible(self):
        ir = ForzaMaterialIR(
            shader=ShaderIdentity(
                shader_name="car_standard",
                archive_path="x.zip",
                shaderbin_sha256="abc",
                permutation="CarLightScenario",
            ),
            evidence=(),
        )
        self.assertIsNone(ir.base_color)
        uv = MeshUV(index=0)
        self.assertEqual(uv.index, 0)

    def test_missing_contract_is_proposed(self):
        self.assertIs(contract_status_for("no_such_hash"), ContractStatus.PROPOSED)


if __name__ == "__main__":
    unittest.main()
