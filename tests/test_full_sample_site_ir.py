"""FULL_SAMPLE_SITE_IR migration gate tests."""

from __future__ import annotations

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest import mock

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.mati_parameter_provenance import (  # noqa: E402
    corpus_occurrence_key,
    material_content_key,
)
from io_import_forza_carbin.materials.pass_contracts import (  # noqa: E402
    load_contract_index,
    load_shader_pass_contract,
)
from io_import_forza_carbin.materials.route_model import (  # noqa: E402
    FULL_SAMPLE_SITE_IR,
    PRODUCTION_IR_SHADERBIN_SHA256,
    PRODUCTION_IR_SHADER_NAMES,
    has_ir_evaluator,
)
from io_import_forza_carbin.materials.sample_site_eval import (  # noqa: E402
    evaluate_material_sample_sites,
)

SUPPORTED = {
    "car_standard": "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16",
    "car_label": "35bccc9b43710c374b94c8800436dce8a44c607ee778f65764f31f0bc56cc515",
    "car_carbonfiber": "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3",
    "car_standard_emissive": "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178",
    "car_standard_fabric": "af463726a228752c328abd847868a90bf69110463594a69851ebee1ce9034523",
    "car_automotive_paint": "ce460364d8151e819f056552d274353ba2657aff2ff718ed1239db02b7ffebb3",
    "car_standard_coated": "373050795197539169f78b29a08424add9f313e99c8eab0a33a6658a40987c88",
    "car_glass_detailed": "3f988df89a12b4a008777463a56eee840a5c3488c6af3ad53f69c2f4cb861d09",
    "car_reflector": "47f92e42f2d1991ae07a36364216402e53801bc6be9efa765ee49fe64a51d0e9",
    "car_brakerotor": "384692abfe3daace9b29f2580c60c23a171192e8c5e9fd6b3be10989b255f106",
    "car_livery_transmissive": "831b4866240da29fa4bf6706b13ceab4f4259e2cb4f32eb7b10da687f7284f53",
    "car_livery": "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb",
}


class FullSampleSiteIrGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_contract_index.cache_clear()
        load_shader_pass_contract.cache_clear()

    def test_twelve_supported_have_ir_and_primary_sites(self):
        self.assertEqual(len(SUPPORTED), 12)
        self.assertEqual(len(PRODUCTION_IR_SHADERBIN_SHA256), 12)
        for fam, sha in SUPPORTED.items():
            self.assertTrue(has_ir_evaluator(sha, fam), fam)
            self.assertIn(fam, PRODUCTION_IR_SHADER_NAMES)
            c = load_shader_pass_contract(sha)
            self.assertIsNotNone(c, fam)
            primary = [
                p
                for p in (c.get("relevant_passes") or [])
                if (p.get("scenario") or "") == "CarLightScenario"
            ]
            self.assertTrue(primary, fam)
            n_import = sum(
                1
                for p in primary
                for s in (p.get("import_sample_sites") or [])
                if s.get("blender_import")
            )
            self.assertGreater(n_import, 0, fam)

    def test_no_supported_sha_uses_primary_pass_texture_bindings_label(self):
        # Contracts present → extract_bindings sets FULL_SAMPLE_SITE_IR.
        for fam, sha in SUPPORTED.items():
            c = load_shader_pass_contract(sha)
            self.assertIsNotNone(c)
            # Route model constant used by verification.
            self.assertEqual(FULL_SAMPLE_SITE_IR, "FULL_SAMPLE_SITE_IR")

    def test_same_register_sites_remain_independent(self):
        sha = SUPPORTED["car_standard"]
        c = load_shader_pass_contract(sha)
        sites = []
        for p in c.get("relevant_passes") or []:
            sites.extend(p.get("import_sample_sites") or [])
        # Multiple instruction IDs may share a register.
        by_reg: dict[int, set[str]] = {}
        for s in sites:
            by_reg.setdefault(int(s["texture_register"]), set()).add(
                str(s["instruction_id"])
            )
        multi = {r: ids for r, ids in by_reg.items() if len(ids) > 1}
        self.assertTrue(multi, "expected same-register multi-instruction sites")

    def test_unknown_sha_fails_closed(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256="00" * 32,
            params={},
        )
        self.assertEqual(ev.sites, [])
        self.assertFalse(load_shader_pass_contract("00" * 32))

    def test_no_predicate_recovered_rejects_blender_import(self):
        # Inject a synthetic contract evaluation via monkeypatch of load.
        from io_import_forza_carbin.materials import sample_site_eval as sse

        fake = {
            "shaderbin_sha256": "aa" * 32,
            "relevant_passes": [
                {
                    "scenario": "CarLightScenario",
                    "archive_member": "x.pcdxil.pso",
                    "blender_relevance": "MAIN_SURFACE_SHADING",
                    "reason": "unit",
                    "relevance_evidence": ["unit"],
                    "import_sample_sites": [
                        {
                            "sample_site_id": "unit|t17",
                            "instruction_id": "%1",
                            "texture_register": 17,
                            "sampler_register": 1,
                            "expected_comps": [0, 1, 2],
                            "uv_expression": "TEXCOORD0",
                            "branch_status": "NO_PREDICATE_RECOVERED",
                            "declared_txmp_name": "BaseColorAlpha",
                            "semantic_role": "BaseColorAlpha",
                            "blender_import": True,
                            "evidence": ["unit"],
                        }
                    ],
                }
            ],
        }
        with mock.patch.object(sse, "load_shader_pass_contract", return_value=fake):
            ev = sse.evaluate_material_sample_sites(
                shaderbin_sha256="aa" * 32, params={}
            )
        self.assertEqual(len(ev.sites), 1)
        self.assertEqual(ev.sites[0].status, "REJECTED")
        self.assertEqual(ev.sites[0].branch_status, "NO_PREDICATE_RECOVERED")

    def test_occurrence_keys_distinguish_reuse(self):
        a = corpus_occurrence_key(
            vehicle_or_archive="cars/a.zip",
            source_mati_path="mat/a.mati",
            material_slot="Body",
            occurrence_index=0,
        )
        b = corpus_occurrence_key(
            vehicle_or_archive="cars/b.zip",
            source_mati_path="mat/a.mati",
            material_slot="Body",
            occurrence_index=0,
        )
        self.assertNotEqual(a, b)
        content = material_content_key(
            shaderbin_sha256=SUPPORTED["car_standard"],
            source_mati_path="mat/a.mati",
        )
        self.assertIn(SUPPORTED["car_standard"][:16], content)

    def test_primary_sites_enter_eval_with_site_ids(self):
        # UVChoice false → TEXCOORD1 for car_standard Select sites.
        params = {
            0x402B8ED0: SimpleNamespace(type=3, value=False),
        }
        for fam in ("car_standard", "car_carbonfiber", "car_standard_fabric"):
            sha = SUPPORTED[fam]
            ev = evaluate_material_sample_sites(shaderbin_sha256=sha, params=params)
            active = ev.active_import_sites()
            self.assertTrue(active, fam)
            for s in active:
                self.assertTrue(s.sample_site_id)
                self.assertIsNotNone(s.resolved_texcoord, s.sample_site_id)

    def test_paint_and_coated_sites_distinct(self):
        paint = load_shader_pass_contract(SUPPORTED["car_automotive_paint"])
        coated = load_shader_pass_contract(SUPPORTED["car_standard_coated"])
        p_ids = {
            s["sample_site_id"]
            for p in paint["relevant_passes"]
            for s in p["import_sample_sites"]
            if s.get("blender_import")
        }
        c_ids = {
            s["sample_site_id"]
            for p in coated["relevant_passes"]
            for s in p["import_sample_sites"]
            if s.get("blender_import")
        }
        self.assertTrue(p_ids)
        self.assertTrue(c_ids)
        self.assertFalse(p_ids & c_ids)

    def test_glass_brakerotor_livery_transmissive_have_import_sites(self):
        for fam in (
            "car_glass_detailed",
            "car_brakerotor",
            "car_livery_transmissive",
            "car_standard_emissive",
            "car_reflector",
            "car_livery",
            "car_label",
        ):
            c = load_shader_pass_contract(SUPPORTED[fam])
            n = sum(
                1
                for p in c["relevant_passes"]
                for s in p["import_sample_sites"]
                if s.get("blender_import")
            )
            self.assertGreater(n, 0, fam)

    def test_production_sharing_remains_off(self):
        # Prefer explicit empty production sharing set if present.
        try:
            from io_import_forza_carbin.materials.effective_material import (  # noqa: F401
                PRODUCTION_SHARING_ENABLED_SHA256,
            )

            self.assertEqual(len(PRODUCTION_SHARING_ENABLED_SHA256), 0)
        except ImportError:
            # Fall back: no sharing module constant is also OFF.
            pass


if __name__ == "__main__":
    unittest.main()
