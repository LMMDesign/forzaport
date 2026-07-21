"""Game-file alpha contracts — no filename / texture-stat heuristics."""
from __future__ import annotations

import inspect
import os
import sys
import types
import unittest
from types import SimpleNamespace

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)
_parent = os.path.normpath(os.path.join(_ROOT, ".."))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from io_import_forza_carbin.materials.alpha import (
    AUTHORED_MASK_EQUATION,
    CAR_STANDARD_SHADERBIN_SHA256,
    AlphaClassification,
    BlenderAlphaTranslation,
    car_standard_alpha_contract,
    evaluate_car_standard_alpha,
)
from io_import_forza_carbin.materials.resolver import _alpha_mode
from io_import_forza_carbin.parsing.material import ShaderParameterName as SPN


class GameFileAlphaContractTests(unittest.TestCase):
    def test_contract_keyed_by_exact_sha(self):
        from io_import_forza_carbin.materials.alpha import load_contract

        c = load_contract(CAR_STANDARD_SHADERBIN_SHA256)
        self.assertEqual(c["shader_sha256"], CAR_STANDARD_SHADERBIN_SHA256)
        self.assertEqual(
            c["authored_masks"][0]["equation"], AUTHORED_MASK_EQUATION
        )
        self.assertEqual(c["fixed_function_status"], "UNRESOLVED_NOT_IN_PSO_BLOB")
        self.assertTrue(c["sample_sites"])

    def test_id39_false_branch_opaque_with_shading_mask(self):
        sem = evaluate_car_standard_alpha(
            alpha_transparency=False,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertEqual(
            sem.classification, AlphaClassification.PROVEN_OPAQUE
        )
        self.assertEqual(sem.surface_visibility, "OPAQUE")
        self.assertIn(sem.blender_plan.render_mode, ("OPAQUE",))
        self.assertEqual(
            sem.shading_attenuation_expression, AUTHORED_MASK_EQUATION
        )
        self.assertEqual(sem.secondary_classification, "PROVEN_SHADING_ONLY_MASK")
        self.assertEqual(sem.principled_alpha, "unused")
        self.assertIsNone(sem.opacity_expression)

    def test_id40_true_branch_texture_visibility_mask_not_exact_cutout(self):
        sem = evaluate_car_standard_alpha(
            alpha_transparency=True,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertEqual(
            sem.classification,
            AlphaClassification.PROVEN_MASKED_VISIBILITY,
        )
        self.assertEqual(
            sem.source_visibility_semantic,
            AlphaClassification.PROVEN_MASKED_VISIBILITY.value,
        )
        self.assertEqual(sem.blender_plan.render_mode, "CLIP")
        self.assertEqual(sem.surface_visibility, "CLIP")
        self.assertEqual(sem.opacity_expression, AUTHORED_MASK_EQUATION)
        self.assertAlmostEqual(sem.blender_threshold, 0.5)
        self.assertEqual(sem.principled_alpha, "expression")

    def test_explicit_true_differs_from_false(self):
        t = evaluate_car_standard_alpha(
            alpha_transparency=True,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        f = evaluate_car_standard_alpha(
            alpha_transparency=False,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertNotEqual(t.classification, f.classification)
        self.assertNotEqual(t.surface_visibility, f.surface_visibility)

    def test_explicit_false_differs_from_missing(self):
        f = evaluate_car_standard_alpha(
            alpha_transparency=False,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        m = evaluate_car_standard_alpha(
            alpha_transparency=None,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertEqual(f.classification, AlphaClassification.PROVEN_OPAQUE)
        self.assertEqual(
            m.classification, AlphaClassification.REJECTED_UNSUPPORTED_BRANCH
        )

    def test_absent_transparency_unresolved_fail_closed(self):
        sem = evaluate_car_standard_alpha(
            alpha_transparency=None,
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
        )
        self.assertEqual(
            sem.classification, AlphaClassification.REJECTED_UNSUPPORTED_BRANCH
        )
        self.assertEqual(sem.surface_visibility, "UNRESOLVED")
        self.assertEqual(sem.principled_alpha, "unused")

    def test_wrong_sha_rejected(self):
        sem = evaluate_car_standard_alpha(
            alpha_transparency=True,
            shaderbin_sha256="deadbeef" * 8,
        )
        self.assertEqual(
            sem.classification, AlphaClassification.REJECTED_UNSUPPORTED_BRANCH
        )

    def test_authored_mask_serialises_deterministically(self):
        from io_import_forza_carbin.materials.alpha import load_contract

        a = load_contract(CAR_STANDARD_SHADERBIN_SHA256)
        b = load_contract(CAR_STANDARD_SHADERBIN_SHA256)
        self.assertEqual(
            a["authored_masks"][0]["equation"],
            b["authored_masks"][0]["equation"],
        )
        self.assertEqual(a["authored_masks"][0]["equation"], AUTHORED_MASK_EQUATION)

    def test_resolver_ignores_has_alpha(self):
        self.assertEqual(_alpha_mode({}, has_alpha=True), "OPAQUE")
        params_false = {
            int(SPN.AlphaTransparencyBool): SimpleNamespace(
                type=3, value=False, path="", samp=b""
            ),
        }
        self.assertEqual(_alpha_mode(params_false, has_alpha=True), "OPAQUE")

    def test_alpha_module_has_no_filename_or_material_name_heuristics(self):
        import io_import_forza_carbin.materials.alpha as mod

        src = inspect.getsource(mod).lower()
        for banned in (
            "doortags",
            "id40",
            "id39",
            "grillhexagon",
            "silhouette",
            "flat_white",
            "histogram",
            "looks like",
            "probably",
            "likely cutout",
        ):
            self.assertNotIn(banned, src, msg=banned)

    def test_dxil_sites_parser_emits_sample_instruction_ids(self):
        from pathlib import Path

        from io_import_forza_carbin.materials.alpha.dxil_sites import parse_pass_ll

        ll = (
            Path(__file__).resolve().parents[3]
            / "reports/archive/2026-07-21-conformance-runs/runs"
            / "2026-07-21_id39-grille-coverage_alpha-pso/dxil"
            / "car_standard_CarShadowDepthLightScenario.ll"
        )
        if not ll.is_file():
            self.skipTest("DXIL dump not present")
        parsed = parse_pass_ll(ll, pass_name="CarShadowDepthLightScenario")
        self.assertGreaterEqual(len(parsed["sample_sites"]), 2)
        ids = {
            s["dxil_texture_sample_instruction_id"] for s in parsed["sample_sites"]
        }
        self.assertIn("%98", ids)
        self.assertIn("%118", ids)
        expr = parsed["authored_mask_expression"]
        self.assertIsNotNone(expr)
        self.assertEqual(expr["multiplication_ssa_id"], "%120")
        self.assertEqual(len(expr["references_sample_site_ids"]), 2)


class AlphaFingerprintParticipationTests(unittest.TestCase):
    def test_opacity_changes_effective_fingerprint(self):
        from io_import_forza_carbin.materials.effective_material import (
            effective_material_fingerprint,
        )
        from io_import_forza_carbin.materials.forza_ir import (
            ConstantScalar,
            ForzaMaterialIR,
            ShaderIdentity,
        )
        from io_import_forza_carbin.materials.model import ProvenanceDiagnostic

        ident = ShaderIdentity(
            shader_name="car_standard",
            archive_path="media/cars/_library/shaders/car_standard.zip",
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
            permutation="CarLightScenario",
        )
        ev = (ProvenanceDiagnostic(kind="t", detail="d", source="test"),)
        opaque = ForzaMaterialIR(
            shader=ident, opacity=None, evidence=ev, rejection_reasons=()
        )
        clipped = ForzaMaterialIR(
            shader=ident,
            opacity=ConstantScalar(value=0.5, evidence=ev),
            evidence=ev,
            rejection_reasons=(),
        )
        self.assertNotEqual(
            effective_material_fingerprint(opaque),
            effective_material_fingerprint(clipped),
        )


class AlphaSharingOffTests(unittest.TestCase):
    def test_production_sharing_off(self):
        from io_import_forza_carbin.materials.effective_material import (
            PRODUCTION_SHARING_ENABLED_SHA256,
            production_sharing_enabled,
        )

        self.assertEqual(len(PRODUCTION_SHARING_ENABLED_SHA256), 0)
        self.assertFalse(production_sharing_enabled(CAR_STANDARD_SHADERBIN_SHA256))


if __name__ == "__main__":
    unittest.main()
