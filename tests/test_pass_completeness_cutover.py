"""Bridge cutover tests — contracted SHAs must not use legacy merge."""

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

from io_import_forza_carbin.materials.forza_ir import (  # noqa: E402
    MeshUV,
    SamplerState,
    TextureSample,
    TextureSampleExpression,
)
from io_import_forza_carbin.materials.legacy_binding_bridge import (  # noqa: E402
    LegacyCompatibilityBridgeError,
    assert_legacy_bridge_allowed,
    is_contracted_shaderbin_sha,
    legacy_bridge_metrics,
    reset_legacy_bridge_metrics,
)
from io_import_forza_carbin.materials.pass_contracts import (  # noqa: E402
    CAR_LIVERY_SHADERBIN_SHA256,
    additional_passes_for_sha,
)
from io_import_forza_carbin.materials.sample_site_eval import (  # noqa: E402
    evaluate_material_sample_sites,
)
from io_import_forza_carbin.materials.sample_site_identity import (  # noqa: E402
    ShaderSampleSiteIdentity,
)
from io_import_forza_carbin.materials.shader_bindings import (  # noqa: E402
    ShaderBindings,
)
from io_import_forza_carbin.materials.uv.uv_choice_contracts import (  # noqa: E402
    CAR_STANDARD_FABRIC_SHADERBIN_SHA256,
    UV_CHOICE_ON_CH1_OFF_CH2,
    resolve_uv_choice_texcoord,
)
from io_import_forza_carbin.materials.variant_selection import (  # noqa: E402
    CAR_TIRES_PG_SHA256,
    LEGACY_HASH,
)

LICENSE_SHA = (
    "d602d66d6c095568fdec20059f3f00f9cb3c3d53fdfa99acc50049d2330a957a"
)


class ContractedBridgeCutoverTests(unittest.TestCase):
    def setUp(self):
        reset_legacy_bridge_metrics()

    def test_contracted_shas_detected(self):
        self.assertTrue(is_contracted_shaderbin_sha(CAR_LIVERY_SHADERBIN_SHA256))
        self.assertFalse(is_contracted_shaderbin_sha("00" * 32))

    def test_legacy_bridge_forbidden_for_contracted(self):
        with self.assertRaises(LegacyCompatibilityBridgeError) as ctx:
            assert_legacy_bridge_allowed(
                shaderbin_sha256=CAR_LIVERY_SHADERBIN_SHA256,
                entry_point="unit_test",
                material_instance_key="fh6|test",
            )
        self.assertIn("LEGACY_COMPATIBILITY_VIEW", str(ctx.exception))
        m = legacy_bridge_metrics()
        self.assertEqual(m["compatibility_bridge_usage_for_contracted_shas"], 1)

    def test_legacy_textures_view_fails_for_contracted_bindings(self):
        b = ShaderBindings(
            shader_name="car_livery",
            source_hashes={"shaderbin_sha256": CAR_LIVERY_SHADERBIN_SHA256},
            authoritative_model="EVALUATED_SAMPLE_SITES",
        )
        with self.assertRaises(LegacyCompatibilityBridgeError):
            b.legacy_textures_view(material_instance_key="fh6|x")

    def test_pass_merge_specs_exist_but_are_not_production_authority(self):
        # Specs may remain as JSON adapters / diagnostics; cutover forbids
        # extract_bindings from invoking the merge bridge for contracted SHAs.
        specs = additional_passes_for_sha(CAR_LIVERY_SHADERBIN_SHA256)
        self.assertTrue(specs)

    def test_same_register_distinct_ir_samples(self):
        from io_import_forza_carbin.materials.texture_source import (
            ResolvedTextureSource,
        )

        src = mock.Mock(spec=ResolvedTextureSource)
        a = TextureSampleExpression(
            binding_name_hash=1,
            source=src,
            uv=MeshUV(index=0),
            channels=("r",),
            color_space="sRGB",
            sampler=SamplerState(),
            sample_site_id="site_A",
            sample_site_key="key_A",
            texture_register=16,
            sampler_register=1,
            shaderbin_sha256="aa" * 32,
            pass_name="SimpleCarLightScenario",
        )
        b = TextureSampleExpression(
            binding_name_hash=1,
            source=src,
            uv=MeshUV(index=1),
            channels=("g", "b"),
            color_space="sRGB",
            sampler=SamplerState(address_u="CLAMP"),
            sample_site_id="site_B",
            sample_site_key="key_B",
            texture_register=16,
            sampler_register=2,
            shaderbin_sha256="aa" * 32,
            pass_name="SimpleCarLightScenario",
        )
        self.assertNotEqual(a.sample_site_id, b.sample_site_id)
        self.assertEqual(a.texture_register, b.texture_register)
        self.assertNotEqual(a.uv, b.uv)
        self.assertNotEqual(a.channels, b.channels)
        self.assertNotEqual(a.sampler_register, b.sampler_register)
        ta = TextureSample(sample=a, sample_site_id="site_A")
        tb = TextureSample(sample=b, sample_site_id="site_B")
        self.assertNotEqual(ta.sample_site_id, tb.sample_site_id)

    def test_license_plate_rejected_cleanly(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256=LICENSE_SHA, params={}
        )
        sites = [s for s in ev.sites if s.texture_register == 16]
        self.assertTrue(sites)
        self.assertFalse(sites[0].blender_import)
        self.assertIn(sites[0].status, ("INACTIVE", "UNRESOLVED"))

    def test_tyres_seven_sites_non_import(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256=CAR_TIRES_PG_SHA256,
            params={LEGACY_HASH: SimpleNamespace(type=2, value=1)},
        )
        regs = sorted(
            s.texture_register
            for s in ev.sites
            if 19 <= s.texture_register <= 25
        )
        self.assertEqual(regs, list(range(19, 26)))
        self.assertTrue(
            all(not s.blender_import for s in ev.sites if s.texture_register >= 19)
        )

    def test_unknown_sha_fail_closed(self):
        self.assertIsNone(
            resolve_uv_choice_texcoord(
                {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=True)},
                shaderbin_sha256="ff" * 32,
            )
        )
        ev = evaluate_material_sample_sites(shaderbin_sha256="ee" * 32, params={})
        self.assertEqual(ev.sites, [])

    def test_fabric_uvchoice_resolves_texcoord1_when_false(self):
        uv, _ = resolve_uv_choice_texcoord(
            {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=False)},
            shaderbin_sha256=CAR_STANDARD_FABRIC_SHADERBIN_SHA256,
        )
        self.assertEqual(uv, 1)

    def test_identity_keys_distinct(self):
        a = ShaderSampleSiteIdentity(
            shaderbin_sha256="11" * 32,
            full_archive_member="a.pso",
            pso_sha256="22" * 32,
            variant="",
            scenario="S",
            stage="ps",
            instruction_id="%1",
            texture_register=16,
            sampler_register=1,
        )
        b = ShaderSampleSiteIdentity(
            shaderbin_sha256="11" * 32,
            full_archive_member="b.pso",
            pso_sha256="33" * 32,
            variant="",
            scenario="S",
            stage="ps",
            instruction_id="%2",
            texture_register=16,
            sampler_register=1,
        )
        self.assertNotEqual(a.as_key(), b.as_key())

    def test_production_sharing_off(self):
        from io_import_forza_carbin.materials.effective_material import (
            PRODUCTION_SHARING_ENABLED_SHA256,
            production_sharing_enabled,
        )

        self.assertEqual(len(PRODUCTION_SHARING_ENABLED_SHA256), 0)
        self.assertFalse(production_sharing_enabled("aa" * 32))
        self.assertFalse(production_sharing_enabled(CAR_LIVERY_SHADERBIN_SHA256))


if __name__ == "__main__":
    unittest.main()
