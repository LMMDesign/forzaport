"""Shader Pass Completeness v4 — sample-site identity, UV, provenance, fail-closed."""

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

from io_import_forza_carbin.materials.declared_schema import (  # noqa: E402
    parse_shaderbin_xml,
)
from io_import_forza_carbin.materials.forza_ir import (  # noqa: E402
    SamplerState,
    TextureSample,
    TextureSampleExpression,
)
from io_import_forza_carbin.materials.mati_parameter_provenance import (  # noqa: E402
    ParameterProvenanceCategory,
    classify_parameter_provenance,
    dump_instance_parameter_provenance,
)
from io_import_forza_carbin.materials.pass_contracts import (  # noqa: E402
    PassMergeSpec,
    blender_import_merge_specs,
    load_shader_pass_contract,
)
from io_import_forza_carbin.materials.pass_identity import (  # noqa: E402
    provisional_name_relevance,
)
from io_import_forza_carbin.materials.sample_site_eval import (  # noqa: E402
    evaluate_material_sample_sites,
)
from io_import_forza_carbin.materials.sample_site_identity import (  # noqa: E402
    ShaderSampleSiteIdentity,
)
from io_import_forza_carbin.materials.shader_bindings import (  # noqa: E402
    TextureBinding,
    _merge_pass_sites,
)
from io_import_forza_carbin.materials.uv.uv_choice_contracts import (  # noqa: E402
    resolve_uv_choice_texcoord,
)
from io_import_forza_carbin.materials.uv.uv_expr import (  # noqa: E402
    AddUVNode,
    AtlasOffsetUVNode,
    MeshUVNode,
    ScaleUVNode,
    SelectUVNode,
    evaluate_uv_expr,
    parse_uv_expr_json,
)
from io_import_forza_carbin.materials.variant_selection import (  # noqa: E402
    CAR_TIRES_PG_SHA256,
    LEGACY_HASH,
)


UV_CHOICE = 0x402B8ED0
EMISSIVE_SHA = (
    "8d4ef07a59378e6862a1e9318b8b247100e7fc5e05954a8fdbe6ae6ea2a57178"
)
LICENSE_SHA = (
    "d602d66d6c095568fdec20059f3f00f9cb3c3d53fdfa99acc50049d2330a957a"
)
TIRES_SHA = CAR_TIRES_PG_SHA256
LIVERY_SHA = (
    "f1617a600d251bc8acb78abf939ce6b1b223ea23afee8f4fb592094c135051bb"
)


def _bind(treg: int, *, uv: int, comps=(0,), sampler=1) -> TextureBinding:
    return TextureBinding(
        treg=treg,
        sampler_reg=sampler,
        comps=list(comps),
        uv_semantic=uv,
        uv_semantics_all=[uv],
        role="albedo",
        evidence=[f"feeds_sv_target_alpha t{treg}"],
        channel_roles={},
    )


class SameRegisterMultiSiteTests(unittest.TestCase):
    def test_secondary_site_on_existing_register_not_discarded(self):
        textures = {16: _bind(16, uv=0, comps=(0,), sampler=1)}
        sample_sites: list[dict] = []
        primary = SimpleNamespace(
            shader_name="demo",
            shaderbin_sha256="aa" * 32,
            pass_name="CarLightScenario",
            pso_member="demoCarLight.pcdxil.pso",
            pso_sha256="bb" * 32,
            textures=textures,
        )
        secondary = SimpleNamespace(
            pass_name="SimpleCarLightScenario",
            pso_member="demoSimple.pcdxil.pso",
            pso_sha256="cc" * 32,
            textures={16: _bind(16, uv=1, comps=(0, 1), sampler=2)},
        )
        spec = PassMergeSpec(
            pass_name="SimpleCarLightScenario",
            pso_basename="demoSimple.pcdxil.pso",
            merge_texture_registers=(16,),
            expected_uv_semantics=(1,),
            expected_comps=(0, 1),
            require_sv_target_alpha=True,
            evidence="test",
            blender_relevance="VISIBILITY",
        )
        _merge_pass_sites(
            textures=textures,
            sample_sites=sample_sites,
            primary=primary,
            secondary=secondary,
            spec=spec,
            site_identity_key="site_B",
            resolved_texcoord=1,
        )
        self.assertEqual(len(sample_sites), 1)
        self.assertTrue(sample_sites[0]["same_register_as_primary"])
        self.assertEqual(sample_sites[0]["uv_semantic"], 1)
        # Primary bridge binding retained (register still present).
        self.assertEqual(textures[16].uv_semantic, 0)

    def test_same_register_retains_different_uvs(self):
        a = ShaderSampleSiteIdentity(
            shaderbin_sha256="11" * 32,
            full_archive_member="a.pso",
            pso_sha256="22" * 32,
            variant="",
            scenario="SimpleCarLightScenario",
            stage="ps",
            instruction_id="%10",
            texture_register=16,
            sampler_register=1,
            sample_site_index=0,
        )
        b = ShaderSampleSiteIdentity(
            shaderbin_sha256="11" * 32,
            full_archive_member="b.pso",
            pso_sha256="33" * 32,
            variant="",
            scenario="SimpleCarLightScenario",
            stage="ps",
            instruction_id="%20",
            texture_register=16,
            sampler_register=1,
            sample_site_index=1,
        )
        self.assertNotEqual(a.as_key(), b.as_key())

    def test_same_register_different_channels_and_samplers(self):
        textures = {5: _bind(5, uv=0, comps=(0,), sampler=1)}
        sites: list[dict] = []
        primary = SimpleNamespace(
            shader_name="x",
            shaderbin_sha256="dd" * 32,
            pass_name="CarLightScenario",
            pso_member="x.pso",
            pso_sha256="ee" * 32,
            textures=textures,
        )
        secondary = SimpleNamespace(
            pass_name="Extra",
            pso_member="y.pso",
            pso_sha256="ff" * 32,
            textures={5: _bind(5, uv=2, comps=(1, 2), sampler=7)},
        )
        spec = PassMergeSpec(
            pass_name="Extra",
            pso_basename="y.pso",
            merge_texture_registers=(5,),
            expected_uv_semantics=(2,),
            expected_comps=(1, 2),
            require_sv_target_alpha=True,
            evidence="e",
            blender_relevance="VISIBILITY",
        )
        _merge_pass_sites(
            textures=textures,
            sample_sites=sites,
            primary=primary,
            secondary=secondary,
            spec=spec,
        )
        self.assertEqual(sites[0]["comps"], [1, 2])
        self.assertEqual(sites[0]["sampler_register"], 7)


class NoFirstSiteCollapseTests(unittest.TestCase):
    def test_merge_specs_are_one_per_site(self):
        merges = blender_import_merge_specs(LIVERY_SHA)
        self.assertTrue(merges)
        for m in merges:
            self.assertEqual(len(m.merge_texture_registers), 1)


class SelectPredicateTests(unittest.TestCase):
    def test_true_arm_texcoord3_false_texcoord0(self):
        node = SelectUVNode(
            predicate_hash=UV_CHOICE,
            predicate_type=3,
            true_when="nonzero",
            true_expr=MeshUVNode(3),
            false_expr=MeshUVNode(0),
        )
        on = evaluate_uv_expr(
            node, params={UV_CHOICE: SimpleNamespace(type=3, value=True)}
        )
        off = evaluate_uv_expr(
            node, params={UV_CHOICE: SimpleNamespace(type=3, value=False)}
        )
        self.assertEqual(on.status, "PROVEN")
        self.assertEqual(on.mesh_texcoord, 3)
        self.assertEqual(off.mesh_texcoord, 0)

    def test_true_composed_false_direct(self):
        node = SelectUVNode(
            predicate_hash=UV_CHOICE,
            predicate_type=3,
            true_when="nonzero",
            true_expr=ScaleUVNode(source=MeshUVNode(2), scale=(2.0, 2.0)),
            false_expr=MeshUVNode(1),
        )
        on = evaluate_uv_expr(
            node, params={UV_CHOICE: SimpleNamespace(type=3, value=True)}
        )
        off = evaluate_uv_expr(
            node, params={UV_CHOICE: SimpleNamespace(type=3, value=False)}
        )
        self.assertEqual(on.status, "PROVEN")
        self.assertIsInstance(on.node, ScaleUVNode)
        self.assertEqual(off.mesh_texcoord, 1)

    def test_missing_predicate_rejected(self):
        node = SelectUVNode(
            predicate_hash=UV_CHOICE,
            predicate_type=3,
            true_when="nonzero",
            true_expr=MeshUVNode(0),
            false_expr=MeshUVNode(1),
            missing_policy="reject",
        )
        r = evaluate_uv_expr(node, params={})
        self.assertEqual(r.status, "REJECTED")

    def test_emissive_select_eval(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256=EMISSIVE_SHA,
            params={UV_CHOICE: SimpleNamespace(type=3, value=True)},
        )
        active = [s for s in ev.sites if s.blender_import and s.status == "ACTIVE"]
        self.assertTrue(active)
        self.assertEqual(active[0].resolved_texcoord, 0)
        missing = evaluate_material_sample_sites(
            shaderbin_sha256=EMISSIVE_SHA, params={}
        )
        bad = [
            s
            for s in missing.sites
            if s.blender_import and s.status in ("REJECTED", "UNRESOLVED")
        ]
        self.assertTrue(bad)


class NestedUvTests(unittest.TestCase):
    def test_nested_add_scale(self):
        tree = ScaleUVNode(
            source=AddUVNode(a=MeshUVNode(3), b=MeshUVNode(0)),
            scale=(1.0, 1.0),
        )
        r = evaluate_uv_expr(tree, params={})
        self.assertEqual(r.status, "PROVEN")
        self.assertIsNone(r.mesh_texcoord)

    def test_license_plate_compose_rejected_until_atlas_operands(self):
        c = load_shader_pass_contract(LICENSE_SHA)
        site = c["relevant_passes"][1]["import_sample_sites"][0]
        node = parse_uv_expr_json(site["uv_expression"])
        self.assertIsInstance(node, ScaleUVNode)
        self.assertIsInstance(node.source, AddUVNode)
        self.assertIsInstance(node.source.a, AtlasOffsetUVNode)
        self.assertFalse(node.source.a.operands_proven)
        r = evaluate_uv_expr(node, params={})
        self.assertEqual(r.status, "UNRESOLVED")
        self.assertIn("AtlasOffset", r.rejection or "")
        self.assertFalse(site.get("blender_import"))


class BranchActivityTests(unittest.TestCase):
    def test_per_site_branch_and_missing_fail_closed(self):
        # Synthetic contract evaluation via Select on emissive SHA.
        missing = evaluate_material_sample_sites(
            shaderbin_sha256=EMISSIVE_SHA, params={}
        )
        self.assertTrue(
            any(s.status == "REJECTED" for s in missing.sites if s.blender_import)
            or missing.rejection_reasons
        )


class RelevanceEvidenceTests(unittest.TestCase):
    def test_provisional_vs_contract_evidence(self):
        prov = provisional_name_relevance("CarDebugLightScenario", "")
        self.assertEqual(prov["evidence_status"], "PROVISIONAL_NAME_CLASSIFICATION")
        self.assertEqual(prov["relevance"], "DEBUG_ONLY")
        ev = evaluate_material_sample_sites(
            shaderbin_sha256=LIVERY_SHA, params={}
        )
        self.assertTrue(ev.sites)
        # Livery SimpleCar has reason → CONTRACT_EVIDENCE when reason+relevance_evidence
        # present; at least not silent game-file claim from filename alone.
        for s in ev.sites:
            self.assertIn(
                s.relevance_evidence_status,
                (
                    "CONTRACT_EVIDENCE",
                    "PROVISIONAL_NAME_CLASSIFICATION",
                ),
            )


class ProvenanceTests(unittest.TestCase):
    def test_categories(self):
        mat = SimpleNamespace(
            parameters={1: SimpleNamespace(type=3, value=True)},
            parameters_instance={1: SimpleNamespace(type=3, value=True)},
            parameters_local={},
            override_hashes=set(),
            parent_material_path=r"media\foo.shaderbin",
            parent_chain=[],
            template_material_path="",
            shaderbin_sha256="aa" * 32,
            source_mati_path="car.mati",
            shader_name="car_standard",
        )
        row = classify_parameter_provenance(name_hash=1, material=mat)
        self.assertEqual(row["category"], ParameterProvenanceCategory.MATI_EXPLICIT.value)

        mat2 = SimpleNamespace(
            parameters={2: SimpleNamespace(type=2, value=1.0)},
            parameters_instance={},
            parameters_local={2: SimpleNamespace(type=2, value=1.0)},
            override_hashes=set(),
            parent_material_path=r"media\parent.materialbin",
            parent_chain=["parent.materialbin"],
            template_material_path="",
            shaderbin_sha256="bb" * 32,
            source_mati_path="x.mati",
            shader_name="x",
        )
        row2 = classify_parameter_provenance(name_hash=2, material=mat2)
        self.assertEqual(
            row2["category"],
            ParameterProvenanceCategory.PARENT_MATERIAL_INHERITED.value,
        )
        dump = dump_instance_parameter_provenance(mat)
        self.assertEqual(dump["parameter_count"], 1)


class VertexInputXmlTests(unittest.TestCase):
    def test_vertex_input_usage_not_overwritten(self):
        xml = b"""<?xml version="1.0"?>
        <Shader>
          <Variants>
            <ExportVariant name="A">
              <VertexInputUsage TEXCOORD0="1" TEXCOORD1="0"/>
            </ExportVariant>
            <ExportVariant name="B">
              <VertexInputUsage TEXCOORD0="0" TEXCOORD3="1"/>
            </ExportVariant>
          </Variants>
        </Shader>
        """
        parsed = parse_shaderbin_xml(xml)
        by_v = parsed["vertex_input_usage_by_export_variant"]
        self.assertEqual(len(by_v), 2)
        self.assertEqual(by_v[0]["export_variant"], "A")
        self.assertEqual(by_v[0]["scenarios"]["TEXCOORD0"], "1")
        self.assertEqual(by_v[1]["export_variant"], "B")
        self.assertEqual(by_v[1]["scenarios"]["TEXCOORD3"], "1")
        self.assertEqual(parsed["vertex_input_usage_status"], "PROVEN_FROM_GAME_FILES")


class TyreDistinctSitesTests(unittest.TestCase):
    def test_t19_t25_distinct(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256=TIRES_SHA,
            params={LEGACY_HASH: SimpleNamespace(type=2, value=1)},
        )
        regs = sorted(
            s.texture_register
            for s in ev.sites
            if s.scenario == "SimpleCarLightScenario"
        )
        self.assertEqual(regs, list(range(19, 26)))
        self.assertTrue(all(s.blender_import is False for s in ev.sites if s.texture_register >= 19))
        keys = {s.identity.as_key() for s in ev.sites if s.texture_register >= 19}
        self.assertEqual(len(keys), 7)


class FailClosedTests(unittest.TestCase):
    def test_unknown_sha_uvchoice(self):
        self.assertIsNone(
            resolve_uv_choice_texcoord(
                {UV_CHOICE: SimpleNamespace(type=3, value=True)},
                shaderbin_sha256="00" * 32,
            )
        )

    def test_unknown_sha_eval_empty_sites(self):
        ev = evaluate_material_sample_sites(
            shaderbin_sha256="ff" * 32, params={}
        )
        self.assertEqual(ev.sites, [])


class IrSampleSiteFieldTests(unittest.TestCase):
    def test_texture_sample_carries_site_id(self):
        from io_import_forza_carbin.materials.forza_ir import MeshUV
        from io_import_forza_carbin.materials.texture_source import (
            ResolvedTextureSource,
        )

        src = mock.Mock(spec=ResolvedTextureSource)
        expr = TextureSampleExpression(
            binding_name_hash=1,
            source=src,
            uv=MeshUV(index=0),
            channels=("r",),
            color_space="sRGB",
            sampler=SamplerState(),
            sample_site_id="site_A",
            sample_site_key="key_A",
        )
        tex = TextureSample(sample=expr, sample_site_id="site_A")
        self.assertEqual(tex.sample_site_id, "site_A")
        self.assertEqual(tex.sample.sample_site_id, "site_A")


if __name__ == "__main__":
    unittest.main()
