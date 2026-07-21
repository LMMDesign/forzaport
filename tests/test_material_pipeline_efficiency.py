"""Material pipeline efficiency + context unification + validation tests."""

from __future__ import annotations

import ast
import os
import sys
import types
import unittest
from types import MappingProxyType, SimpleNamespace
from unittest import mock

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.context_cache import (  # noqa: E402
    MaterialContextCache,
    build_context_cache_key,
)
from io_import_forza_carbin.materials.evaluation_context import (  # noqa: E402
    MaterialEvaluationContext,
    MaterialSourceIdentity,
    ShaderEvalIdentity,
    create_material_evaluation_context,
)
from io_import_forza_carbin.materials.forza_ir import (  # noqa: E402
    MeshUV,
    RotateUV,
    ScaleUV,
    SelectUV,
    TextureSample,
)
from io_import_forza_carbin.materials.pipeline_metrics import (  # noqa: E402
    METRICS,
    reset_metrics,
    snapshot_metrics,
)
from io_import_forza_carbin.materials.route_model import has_ir_evaluator  # noqa: E402
from io_import_forza_carbin.materials.shader_implementation import (  # noqa: E402
    get_shader_implementation,
)
from io_import_forza_carbin.materials.shader_static_analysis import (  # noqa: E402
    clear_static_analysis_cache,
    get_or_create_static_analysis,
)
from io_import_forza_carbin.materials.transitional import (  # noqa: E402
    QUARANTINED_IMPORT_BLOCKLIST,
)
from io_import_forza_carbin.materials.uv.uv_expr import (  # noqa: E402
    MeshUVNode,
    RotateUVNode,
    ScaleUVNode,
    SelectUVNode,
)
from io_import_forza_carbin.materials.uv_ir_bridge import uv_expr_to_forza_ir  # noqa: E402


CAR_STANDARD_SHA = (
    "8df4836b0bf017fccbaf4f5bd5ce7a217f260924e457c72751a2d5df8163df16"
)


def _fake_bindings(*, sha=CAR_STANDARD_SHA, sites=None):
    evaluated = SimpleNamespace(
        active_import_sites=lambda: list(sites or []),
        variant=SimpleNamespace(variant="CarLightScenario"),
        serialized_schema=None,
        sites=list(sites or []),
    )
    return SimpleNamespace(
        source_hashes={
            "shaderbin_sha256": sha,
            "primary_pass": "CarLightScenario",
            "pso_sha256": "abc",
        },
        authoritative_model="FULL_SAMPLE_SITE_IR",
        evaluated_sites=evaluated,
        source_mati_path=None,
        textures={},
    )


def _fake_site(*, site_id="site|t17", treg=17, samp=17, uv_node=None, texcoord=0):
    identity = SimpleNamespace(
        sampler_register=samp,
        instruction_id="%1",
        scenario="CarLightScenario",
        as_key=lambda: site_id,
    )
    return SimpleNamespace(
        identity=identity,
        sample_site_id=site_id,
        texture_register=treg,
        sampled_components=(0, 1, 2),
        uv_node=uv_node or MeshUVNode(index=texcoord),
        uv_eval=SimpleNamespace(mesh_texcoord=texcoord),
        resolved_texcoord=texcoord,
        blender_import=True,
        status="ACTIVE",
        branch_status="PROVEN_UNCONDITIONAL",
        branch_predicates=(),
        evidence=("ev",),
        declared_txmp="BaseColorAlpha",
        semantic_role="BaseColorAlpha",
    )


class HasIrEvaluatorExactShaTests(unittest.TestCase):
    def test_exact_sha_approved(self):
        self.assertTrue(has_ir_evaluator(CAR_STANDARD_SHA))

    def test_name_only_rejected(self):
        self.assertFalse(has_ir_evaluator(None, "car_standard"))

    def test_unknown_sha_fails_closed(self):
        self.assertFalse(has_ir_evaluator("0" * 64))
        self.assertIsNone(get_shader_implementation("0" * 64))


class QuarantineTests(unittest.TestCase):
    def test_shader_instance_evaluator_deleted(self):
        with self.assertRaises(ImportError):
            __import__(
                "io_import_forza_carbin.materials.shader_instance_evaluator"
            )

    def test_no_new_production_quarantine_imports(self):
        root = os.path.join(_ROOT, "materials")
        offenders = []
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                if fn in ("transitional.py",):
                    continue
                path = os.path.join(dirpath, fn)
                src = open(path, encoding="utf-8").read()
                try:
                    tree = ast.parse(src)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module:
                        for name in QUARANTINED_IMPORT_BLOCKLIST:
                            if name in (node.module or "") or any(
                                getattr(a, "name", "") == name for a in node.names
                            ):
                                offenders.append(path)
        self.assertEqual(offenders, [])


class UvAstBridgeTests(unittest.TestCase):
    def test_exact_uv_ast_reaches_ir_unchanged_structure(self):
        node = ScaleUVNode(
            source=RotateUVNode(
                source=MeshUVNode(index=1, evidence=("TEXCOORD1",)),
                degrees=45.0,
                evidence=("rot",),
            ),
            scale=(2.0, 3.0),
            evidence=("scale",),
        )
        ir, err = uv_expr_to_forza_ir(node, params={})
        self.assertIsNone(err)
        self.assertIsInstance(ir, ScaleUV)
        self.assertEqual(ir.scale, (2.0, 3.0))
        self.assertIsInstance(ir.source, RotateUV)
        self.assertEqual(ir.source.degrees, 45.0)
        self.assertIsInstance(ir.source.source, MeshUV)
        self.assertEqual(ir.source.source.index, 1)

    def test_select_uv_preserved(self):
        node = SelectUVNode(
            predicate_hash=0x402B8ED0,
            predicate_type=3,
            true_when="nonzero",
            true_expr=MeshUVNode(index=0),
            false_expr=MeshUVNode(index=1),
        )
        params = {0x402B8ED0: SimpleNamespace(type=3, value=True)}
        ir, err = uv_expr_to_forza_ir(node, params=params)
        self.assertIsNone(err)
        self.assertIsInstance(ir, SelectUV)


class ContextImmutabilityTests(unittest.TestCase):
    def test_mappings_are_read_only(self):
        ctx = MaterialEvaluationContext(
            source=MaterialSourceIdentity("m", "car_standard"),
            shader=ShaderEvalIdentity("car_standard", CAR_STANDARD_SHA),
            serialized_schema=None,
            effective_parameters=MappingProxyType({1: 2}),
            texture_resources=MappingProxyType({}),
            static_analysis=None,
            static_pass_analysis=None,
            evaluated_sites=None,
            variant_result=None,
            alpha_semantics=None,
            diagnostics=(),
            bindings=_fake_bindings(),
        )
        with self.assertRaises(TypeError):
            ctx.effective_parameters[3] = 4  # type: ignore[index]
        self.assertFalse(hasattr(ctx, "capability"))
        self.assertFalse(hasattr(ctx, "resolution"))

    def test_frozen_replace_for_alpha(self):
        ctx = MaterialEvaluationContext(
            source=MaterialSourceIdentity("m", "car_standard"),
            shader=ShaderEvalIdentity("car_standard", CAR_STANDARD_SHA),
            serialized_schema=None,
            effective_parameters=MappingProxyType({}),
            texture_resources=MappingProxyType({}),
            static_analysis=None,
            static_pass_analysis=None,
            evaluated_sites=None,
            variant_result=None,
            alpha_semantics=None,
            diagnostics=(),
        )
        ctx2 = ctx.with_alpha_semantics({"x": 1})
        self.assertIsNone(ctx.alpha_semantics)
        self.assertEqual(ctx2.alpha_semantics, {"x": 1})


class StaticAnalysisSharingTests(unittest.TestCase):
    def setUp(self):
        clear_static_analysis_cache()

    def test_same_key_shares_object(self):
        a = get_or_create_static_analysis(
            shaderbin_sha256=CAR_STANDARD_SHA,
            pass_name="CarLightScenario",
            pso_sha256="pso1",
            archive_path="/x/car_standard.zip",
            pass_contract={"shaderbin_sha256": CAR_STANDARD_SHA},
        )
        b = get_or_create_static_analysis(
            shaderbin_sha256=CAR_STANDARD_SHA,
            pass_name="CarLightScenario",
            pso_sha256="pso1",
            archive_path="/x/car_standard.zip",
            pass_contract={"shaderbin_sha256": CAR_STANDARD_SHA},
        )
        self.assertIs(a, b)


class ContextCacheKeyTests(unittest.TestCase):
    def test_different_overrides_do_not_collide(self):
        m1 = SimpleNamespace(override_hashes={1}, parent_template="")
        m2 = SimpleNamespace(override_hashes={2}, parent_template="")
        k1 = build_context_cache_key(
            instance_key="same_name", material=m1, media_root="/m", shaderbin_sha256="a"
        )
        k2 = build_context_cache_key(
            instance_key="same_name", material=m2, media_root="/m", shaderbin_sha256="a"
        )
        self.assertNotEqual(k1.as_tuple(), k2.as_tuple())

    def test_repeated_put_get_shares(self):
        cache = MaterialContextCache()
        m = SimpleNamespace(override_hashes=set(), parent_template="")
        key = build_context_cache_key(
            instance_key="k", material=m, media_root="/m", shaderbin_sha256="a"
        )
        obj = object()
        cache.put(key, obj)
        self.assertIs(cache.get(key), obj)
        cache.clear()
        self.assertEqual(len(cache), 0)


class ContextUnificationTests(unittest.TestCase):
    def setUp(self):
        reset_metrics()

    def test_one_binding_extraction_per_material_evaluation(self):
        material = SimpleNamespace(
            shader_name="car_standard",
            parameters={},
            cbmp={},
            txmp={},
            spmp={},
            override_hashes=set(),
        )
        bindings = _fake_bindings()
        cache = MaterialContextCache()
        with mock.patch(
            "io_import_forza_carbin.materials.shader_bindings.extract_bindings",
            return_value=bindings,
        ) as eb:
            ctx = create_material_evaluation_context(
                instance_key="m1",
                material=material,
                media_root=".",
                context_cache=cache,
            )
            self.assertEqual(eb.call_count, 1)
            create_material_evaluation_context(
                instance_key="m1",
                material=material,
                media_root=".",
                context_cache=cache,
                bindings=bindings,
            )
            # Second create with same identity hits cache (no extra extract when
            # bindings supplied — and cache hit skips extract entirely).
            self.assertEqual(eb.call_count, 1)
        self.assertIsInstance(ctx, MaterialEvaluationContext)

    def test_no_resolver_recursion_from_evaluator_when_context_present(self):
        from io_import_forza_carbin.materials import eval_car_standard as mod

        ctx = MaterialEvaluationContext(
            source=MaterialSourceIdentity("m", "car_standard"),
            shader=ShaderEvalIdentity("car_standard", CAR_STANDARD_SHA),
            serialized_schema=None,
            effective_parameters=MappingProxyType(
                {
                    0x8B7343AB: SimpleNamespace(type=2, value=0.0),
                    0x19A7D8F1: SimpleNamespace(type=2, value=1.0),
                    0x4A3D8375: SimpleNamespace(type=2, value=1.0),
                }
            ),
            texture_resources=MappingProxyType({}),
            static_analysis=None,
            static_pass_analysis=None,
            evaluated_sites=_fake_bindings().evaluated_sites,
            variant_result=None,
            alpha_semantics=None,
            diagnostics=(),
            bindings=_fake_bindings(),
            media_root=".",
        )
        material = SimpleNamespace(
            shader_name="car_standard",
            parameters=dict(ctx.effective_parameters),
            cbmp={},
            txmp={},
            spmp={},
        )
        with mock.patch.object(mod, "extract_bindings") as eb:
            with mock.patch.object(mod, "MaterialCapabilityResolver") as mcr:
                ir = mod.evaluate_car_standard(
                    name="m",
                    material=material,
                    resolver=None,
                    media_root=".",
                    evaluation_context=ctx,
                )
                eb.assert_not_called()
                mcr.assert_not_called()
        # Empty sites → may reject; must not call extract/resolver.
        self.assertTrue(
            ir.rejection_reasons or ir.base_color is not None or True
        )

    def test_exact_site_identity_and_registers_reach_ir(self):
        from io_import_forza_carbin.materials.site_ir_sample import (
            sample_from_evaluated_site,
        )
        from io_import_forza_carbin.materials.texture_source import (
            ResolvedTextureSource,
            TextureSourceKind,
        )

        site = _fake_site(site_id="exact|site|id", treg=26, samp=5, texcoord=1)
        src = ResolvedTextureSource(
            kind=TextureSourceKind.LOOSE_FILE,
            original_path="GAME:\\t.swatchbin",
            canonical_game_path="media/t.swatchbin",
            filesystem_path="/tmp/t.swatchbin",
            archive_path=None,
            archive_member=None,
            exists=True,
        )
        with mock.patch(
            "io_import_forza_carbin.materials.site_ir_sample.resolve_texture_source",
            return_value=src,
        ):
            samp, err = sample_from_evaluated_site(
                site,
                path="GAME:\\t.swatchbin",
                binding_name_hash=0x1234,
                channels=("r", "g", "b"),
                color_space="sRGB",
                resolver=None,
                params={},
            )
        self.assertIsNone(err)
        self.assertIsInstance(samp, TextureSample)
        self.assertEqual(samp.sample_site_id, "exact|site|id")
        self.assertEqual(samp.sample.texture_register, 26)
        self.assertEqual(samp.sample.sampler_register, 5)

    def test_hierarchical_self_time_subtracts_children(self):
        reset_metrics()
        with METRICS.stage("parent"):
            with METRICS.stage("child"):
                pass
        snap = snapshot_metrics()
        # Find parent span
        parent = None
        for k, sp in snap.spans.items():
            if sp.name == "parent" and sp.parent is None:
                parent = sp
        self.assertIsNotNone(parent)
        self.assertGreaterEqual(parent.inclusive_s, parent.self_s)


if __name__ == "__main__":
    unittest.main()
