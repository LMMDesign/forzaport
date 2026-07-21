"""Pass identity, sample sites, contracts, fail-closed UVChoice."""

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

from io_import_forza_carbin.materials.dxil_sample_sites import (  # noqa: E402
    DxilSampleSite,
    register_summary,
)
from io_import_forza_carbin.materials.pass_contracts import (  # noqa: E402
    CAR_LIVERY_SHADERBIN_SHA256,
    additional_passes_for_sha,
    list_contracted_shas,
    load_shader_pass_contract,
)
from io_import_forza_carbin.materials.pass_identity import (  # noqa: E402
    classify_blender_relevance,
    parse_pass_identity,
    scenario_from_member,
    variant_from_member,
)
from io_import_forza_carbin.materials.uv.uv_choice_contracts import (  # noqa: E402
    CAR_STANDARD_SHADERBIN_SHA256,
    UV_CHOICE_ON_CH1_OFF_CH2,
    resolve_uv_choice_texcoord,
)


class PassIdentityTests(unittest.TestCase):
    def test_no_truncation_car_prefix(self):
        self.assertEqual(
            scenario_from_member(
                "car_liveryCarLightScenario.pcdxil.pso", "car_livery"
            ),
            "CarLightScenario",
        )
        self.assertEqual(
            scenario_from_member(
                "_Standard/car_standard_emissiveSimpleCarLightScenario.pcdxil.pso",
                "car_standard_emissive",
            ),
            "SimpleCarLightScenario",
        )

    def test_no_truncation_retro_prefix(self):
        self.assertEqual(
            scenario_from_member(
                "retro_licenseplate_atlasCarLightScenario.pcdxil.pso",
                "retro_licenseplate_atlas",
            ),
            "CarLightScenario",
        )

    def test_other_prefix(self):
        self.assertEqual(
            scenario_from_member("other_prefixFooScenario.pcdxil.pso", "other_prefix"),
            "FooScenario",
        )

    def test_variant_and_full_identity(self):
        self.assertEqual(
            variant_from_member(
                "_DXRSimpleHit_Base/car_automotive_paintCarLightScenario.pcdxil.pso"
            ),
            "_DXRSimpleHit_Base",
        )
        self.assertEqual(
            variant_from_member(
                "_Standard_L/car_tires_pgCarLightScenario.pcdxil.pso"
            ),
            "_Standard_L",
        )
        self.assertEqual(
            variant_from_member("car_liveryCarLightScenario.pcdxil.pso"), ""
        )
        ident = parse_pass_identity(
            member="_Standard/car_tires_pgCarLightScenario.pcdxil.pso",
            shader_name="car_tires_pg",
            shaderbin_sha256="ab" * 32,
            pso_sha256="cd" * 32,
        )
        self.assertEqual(ident.scenario, "CarLightScenario")
        self.assertEqual(ident.variant, "_Standard")
        self.assertEqual(ident.stage, "ps")
        self.assertIn("_Standard|CarLightScenario|ps|", ident.as_key())

    def test_duplicate_scenario_different_variants_are_distinct(self):
        a = parse_pass_identity(
            member="_Standard/car_automotive_paintCarLightScenario.pcdxil.pso",
            shader_name="car_automotive_paint",
            shaderbin_sha256="11" * 32,
            pso_sha256="aa" * 32,
        )
        b = parse_pass_identity(
            member="_DXRSimpleHit_Base/car_automotive_paintCarLightScenario.pcdxil.pso",
            shader_name="car_automotive_paint",
            shaderbin_sha256="11" * 32,
            pso_sha256="bb" * 32,
        )
        self.assertEqual(a.scenario, b.scenario)
        self.assertNotEqual(a.as_key(), b.as_key())
        self.assertNotEqual(a.variant, b.variant)

    def test_blender_relevance_classes(self):
        self.assertEqual(
            classify_blender_relevance("CarLightScenario", "_Standard"),
            "MAIN_SURFACE_SHADING",
        )
        self.assertEqual(
            classify_blender_relevance("CarDebugLightScenario", ""),
            "DEBUG_ONLY",
        )
        self.assertEqual(
            classify_blender_relevance("CarShadowDepthLightScenario", ""),
            "SHADOW_VISIBILITY",
        )
        self.assertEqual(
            classify_blender_relevance("CarLightScenario", "_DXRSimpleHit_Base"),
            "RAY_VISIBILITY",
        )


class SampleSiteAggregateTests(unittest.TestCase):
    def test_register_summary_does_not_collapse_instruction_ids(self):
        sites = [
            DxilSampleSite(
                instruction_index=0,
                instruction_id="%10",
                operation="sample",
                texture_register=16,
                sampler_register=1,
                sampled_components=[0],
                coord_ssa=("%1", "%2"),
                texcoord_sources=[0],
                uv_expression="TEXCOORD0",
                branch_predicates=[],
                feeds_sv_target_alpha=True,
                feeds_discard=False,
            ),
            DxilSampleSite(
                instruction_index=1,
                instruction_id="%20",
                operation="sample",
                texture_register=16,
                sampler_register=1,
                sampled_components=[0, 1],
                coord_ssa=("%3", "%4"),
                texcoord_sources=[1],
                uv_expression="TEXCOORD1",
                branch_predicates=[],
                feeds_sv_target_alpha=False,
                feeds_discard=False,
            ),
        ]
        summary = register_summary(sites)
        self.assertEqual(summary["t16"]["sample_count"], 2)
        self.assertEqual(summary["t16"]["instruction_ids"], ["%10", "%20"])
        self.assertEqual(
            sorted(summary["t16"]["uv_expressions"]),
            ["TEXCOORD0", "TEXCOORD1"],
        )


class ContractRegistryTests(unittest.TestCase):
    def test_five_gap_contracts_present(self):
        shas = set(list_contracted_shas())
        self.assertGreaterEqual(len(shas), 5)
        self.assertIn(CAR_LIVERY_SHADERBIN_SHA256, shas)
        livery = load_shader_pass_contract(CAR_LIVERY_SHADERBIN_SHA256)
        self.assertIsNotNone(livery)
        merges = additional_passes_for_sha(CAR_LIVERY_SHADERBIN_SHA256)
        self.assertTrue(merges)
        self.assertEqual(merges[0].pass_name, "SimpleCarLightScenario")
        self.assertIn(16, merges[0].merge_texture_registers)

    def test_tinthack_debug_not_blender_imported(self):
        sha = "18a0c02486f35beec1a6974d108f26da52f5466923516bfe941f5d586b9dd888"
        merges = additional_passes_for_sha(sha)
        self.assertEqual(merges, ())
        c = load_shader_pass_contract(sha)
        sites = [
            s
            for p in c["relevant_passes"]
            for s in p["import_sample_sites"]
        ]
        self.assertTrue(sites)
        self.assertTrue(all(s.get("blender_import") is False for s in sites))

    def test_emissive_uvchoice_select_contracted(self):
        sha = "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178"
        merges = additional_passes_for_sha(sha)
        self.assertTrue(merges)
        c = load_shader_pass_contract(sha)
        alpha = c["relevant_passes"][1]["import_sample_sites"][0]
        self.assertEqual(alpha["uv_branch_status"], "PROVEN_SWITCH")
        self.assertTrue(alpha["blender_import"])
        self.assertEqual(alpha["uv_expression"]["kind"], "Select")

    def test_variant_selection_paint_and_tires(self):
        from io_import_forza_carbin.materials.variant_selection import (
            CAR_AUTOMOTIVE_PAINT_SHA256,
            CAR_TIRES_PG_SHA256,
            LEGACY_HASH,
            SIMPLE_HIT_HASH,
            resolve_shader_variant,
        )

        reject = resolve_shader_variant(
            shaderbin_sha256=CAR_AUTOMOTIVE_PAINT_SHA256, params={}
        )
        self.assertEqual(reject.status, "REJECTED")
        std = resolve_shader_variant(
            shaderbin_sha256=CAR_AUTOMOTIVE_PAINT_SHA256,
            params={SIMPLE_HIT_HASH: SimpleNamespace(type=2, value=0)},
        )
        self.assertEqual(std.decoded_variant, "_Standard")
        legacy = resolve_shader_variant(
            shaderbin_sha256=CAR_TIRES_PG_SHA256,
            params={LEGACY_HASH: SimpleNamespace(type=2, value=1)},
        )
        self.assertEqual(legacy.decoded_variant, "_Standard_L")

    def test_unknown_sha_fail_closed(self):
        self.assertIsNone(
            resolve_uv_choice_texcoord(
                {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=True)},
                shaderbin_sha256="ff" * 32,
            )
        )
        self.assertIsNone(resolve_uv_choice_texcoord({}))
        on = resolve_uv_choice_texcoord(
            {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=True)},
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertIsNotNone(on)
        self.assertEqual(on[0], 0)


if __name__ == "__main__":
    unittest.main()
