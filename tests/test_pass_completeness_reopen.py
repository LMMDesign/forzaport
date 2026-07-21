"""Reopen gate: semantic coverage ≠ sample-site architecture."""

from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.completeness_status import (  # noqa: E402
    CURRENT_SEMANTIC_COVERAGE,
    SemanticCoverage,
    SiteDisposition,
)
from io_import_forza_carbin.materials.site_coverage import (  # noqa: E402
    build_sha_coverage,
    classify_contract_site,
)

WS = Path(__file__).resolve().parents[3]
BRANCH = (
    WS
    / "reports/archive/2026-07-21-conformance-runs/runs"
    / "2026-07-21_1447_shader-pass-completeness-post-cutover-provenance_950c1d2"
    / "data/shader_sample_site_table_branch_status.json"
)

CAR_STANDARD = "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16"


class ReopenCoverageTests(unittest.TestCase):
    def test_semantic_coverage_is_partial(self):
        self.assertEqual(
            CURRENT_SEMANTIC_COVERAGE, SemanticCoverage.PARTIAL_UNRESOLVED
        )

    def test_blender_import_false_is_not_a_disposition(self):
        d = classify_contract_site(
            {
                "blender_import": False,
                "semantic_role": "unresolved",
                "uv_expression": {
                    "kind": "UNRESOLVED_SAMPLE_SITE_CONTRACT",
                    "note": "x",
                },
            }
        )
        self.assertEqual(d, SiteDisposition.UNRESOLVED_SAMPLE_SITE)

    def test_car_standard_raw_vs_imported_not_complete(self):
        if not BRANCH.is_file():
            self.skipTest("branch inventory missing")
        data = json.loads(BRANCH.read_text(encoding="utf-8"))
        raw = [
            s
            for s in data.get("sample_sites") or []
            if s.get("shaderbin_sha256") == CAR_STANDARD
            and s.get("blender_relevance")
            in ("MAIN_SURFACE_SHADING", "VISIBILITY")
        ]
        from io_import_forza_carbin.materials.pass_contracts import (
            load_shader_pass_contract,
        )

        cov = build_sha_coverage(
            shader_family="car_standard",
            shaderbin_sha256=CAR_STANDARD,
            raw_relevant_sites=raw,
            contract=load_shader_pass_contract(CAR_STANDARD),
        )
        self.assertEqual(cov.raw_relevant, 39)
        self.assertEqual(cov.imported, 4)
        self.assertFalse(cov.reconcile_ok())
        self.assertGreater(cov.unresolved, 0)

    def test_supported_relevant_nopredicate_is_412(self):
        if not BRANCH.is_file():
            self.skipTest("branch inventory missing")
        data = json.loads(BRANCH.read_text(encoding="utf-8"))
        supported = {
            "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16",
            "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515",
            "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3",
            "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178",
            "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523",
            "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3",
            "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88",
            "3f988df89a12b4a008777463a56eee840a5c3488c6af3ad53f69c2f4cb861d09",
            "47f92e42f2d1991ae07a36364216402e53801bc6be9efa765ee49fe64a51d0e9",
            "384692abfe3daace9b29f2580c60c23a171192e8c5e9fd6b3be10989b255f106",
            "831b4866240da29fa4bf6706b13ceab4f4259e2cb4f32eb7b10da687f7284f53",
            "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb",
        }
        nopred = [
            s
            for s in data.get("sample_sites") or []
            if s.get("shaderbin_sha256") in supported
            and s.get("blender_relevance")
            in ("MAIN_SURFACE_SHADING", "VISIBILITY")
            and s.get("branch_status")
            in ("NO_PREDICATE_RECOVERED", "UNCONDITIONAL")
        ]
        self.assertEqual(len(nopred), 412)


if __name__ == "__main__":
    unittest.main()
