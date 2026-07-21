"""Tests for effective material fingerprint / dedup policy (audit-only)."""

from __future__ import annotations

import os
import sys
import types
import unittest
from copy import deepcopy

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.effective_material import (
    CONTRACT_VERSION_BY_SHA256,
    EFFECTIVE_MATERIAL_SCHEMA_VERSION,
    DedupEligibility,
    EffectiveMaterialGroup,
    EffectiveMaterialShareCache,
    SourceInstanceRef,
    audit_group_graph_plans,
    canonicalize_effective_ir,
    effective_material_fingerprint,
    eligibility_for_shaderbin,
    normalize_graph_plan_for_dedup,
    production_sharing_enabled,
)
from io_import_forza_carbin.materials.eval_car_standard import CAR_STANDARD_SHADERBIN_SHA256
from io_import_forza_carbin.materials.forza_ir import (
    Channel,
    ConstantColor,
    ForzaMaterialIR,
    MeshUV,
    Multiply,
    NormalDecode,
    OffsetUV,
    RasterState,
    RotateUV,
    SamplerState,
    ScaleUV,
    ShaderIdentity,
    TextureSample,
    TextureSampleExpression,
)
from io_import_forza_carbin.materials.texture_source import (
    ResolvedTextureSource,
    TextureSourceKind,
)


def _src(path: str = "media\\cars\\_library\\textures\\a.swatchbin"):
    return ResolvedTextureSource(
        kind=TextureSourceKind.LOOSE_FILE,
        original_path="GAME:\\" + path.replace("/", "\\"),
        canonical_game_path=path.replace("/", "\\").lower(),
        filesystem_path="/tmp/" + path.replace("\\", "/"),
        archive_path=None,
        archive_member=None,
        exists=True,
        failure=None,
    )


def _sample(
    *,
    h: int = 1,
    path: str = "media\\cars\\_library\\textures\\a.swatchbin",
    uv=None,
    channels=("r", "g", "b"),
    color_space="sRGB",
):
    return TextureSample(
        sample=TextureSampleExpression(
            binding_name_hash=h,
            source=_src(path),
            uv=uv if uv is not None else MeshUV(index=0),
            channels=channels,
            color_space=color_space,
            sampler=SamplerState(),
        )
    )


def _ir(**kwargs) -> ForzaMaterialIR:
    base = kwargs.pop("base_color", _sample())
    return ForzaMaterialIR(
        shader=ShaderIdentity(
            shader_name="car_standard",
            archive_path="",
            shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
            permutation="CarLightScenario",
        ),
        base_color=base,
        normal=kwargs.pop("normal", None),
        roughness=kwargs.pop("roughness", None),
        metallic=kwargs.pop("metallic", None),
        ambient_occlusion=kwargs.pop("ambient_occlusion", None),
        opacity=kwargs.pop("opacity", None),
        raster_state=kwargs.pop("raster_state", None),
        rejection_reasons=kwargs.pop("rejection_reasons", ()),
    )


class FingerprintTests(unittest.TestCase):
    def test_identical_canonical_ir_shares_fingerprint(self):
        a = _ir()
        b = _ir()
        self.assertEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )
        self.assertEqual(
            canonicalize_effective_ir(a), canonicalize_effective_ir(b)
        )

    def test_different_uv_scale_never_shares(self):
        a = _ir(base_color=_sample(uv=ScaleUV(source=MeshUV(0), scale=(1.0, 1.0))))
        b = _ir(base_color=_sample(uv=ScaleUV(source=MeshUV(0), scale=(32.0, 32.0))))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_uv_rotation_never_shares(self):
        a = _ir(base_color=_sample(uv=RotateUV(source=MeshUV(1), degrees=0.0)))
        b = _ir(base_color=_sample(uv=RotateUV(source=MeshUV(1), degrees=90.0)))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_uv_pan_never_shares(self):
        a = _ir(base_color=_sample(uv=OffsetUV(source=MeshUV(0), offset=(0.0, 0.0))))
        b = _ir(base_color=_sample(uv=OffsetUV(source=MeshUV(0), offset=(0.5, 0.0))))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_texture_source_never_shares(self):
        a = _ir(base_color=_sample(path="media\\a.swatchbin"))
        b = _ir(base_color=_sample(path="media\\b.swatchbin"))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_channel_never_shares(self):
        a = _ir(
            opacity=Channel(source=_sample(channels=("x",), color_space="Non-Color"), channel="x")
        )
        b = _ir(
            opacity=Channel(source=_sample(channels=("y",), color_space="Non-Color"), channel="y")
        )
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_tint_constants_never_shares(self):
        a = _ir(base_color=ConstantColor(rgba=(0.1, 0.1, 0.1, 1.0)))
        b = _ir(base_color=ConstantColor(rgba=(0.2, 0.1, 0.1, 1.0)))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_inactive_outputs_omitted_do_not_prevent_sharing(self):
        """Two IRs with same active base and null other outputs share."""
        a = _ir(normal=None, roughness=None)
        b = _ir(normal=None, roughness=None)
        self.assertEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_proven_neutral_strength_default_deterministic(self):
        a = _ir(normal=NormalDecode(source=_sample(color_space="Non-Color"), strength=1.0))
        b = _ir(normal=NormalDecode(source=_sample(color_space="Non-Color")))
        self.assertEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )

    def test_different_blend_culling_never_shares(self):
        a = _ir(raster_state=RasterState(blend_enable=True, cull_mode="BACK"))
        b = _ir(raster_state=RasterState(blend_enable=False, cull_mode="BACK"))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(b)
        )
        c = _ir(raster_state=RasterState(blend_enable=True, cull_mode="NONE"))
        self.assertNotEqual(
            effective_material_fingerprint(a), effective_material_fingerprint(c)
        )

    def test_contract_version_change_invalidates_fingerprint(self):
        ir = _ir()
        fp1 = effective_material_fingerprint(ir)
        canon = canonicalize_effective_ir(ir)
        canon2 = deepcopy(canon)
        canon2["contract_version"] = "9.9.9-mutated"
        import hashlib
        import json

        payload = json.dumps(canon2, sort_keys=True, separators=(",", ":"))
        fp2 = hashlib.sha256(payload.encode()).hexdigest()
        self.assertNotEqual(fp1, fp2)
        self.assertEqual(
            CONTRACT_VERSION_BY_SHA256[CAR_STANDARD_SHADERBIN_SHA256], "1.0.0-b1"
        )
        self.assertEqual(EFFECTIVE_MATERIAL_SCHEMA_VERSION, "1")

    def test_unresolved_rejected_ir_cannot_fingerprint(self):
        ir = _ir(rejection_reasons=("unsupported",))
        with self.assertRaises(ValueError):
            effective_material_fingerprint(ir)

    def test_carbon_ineligible_for_dedup(self):
        sha = "f18954b13a8d117a6e442f153c2138cec6f31154d80430d0b86c458725a597b3"
        self.assertEqual(
            eligibility_for_shaderbin(sha), DedupEligibility.INELIGIBLE
        )
        self.assertFalse(production_sharing_enabled(sha))
        self.assertFalse(production_sharing_enabled(CAR_STANDARD_SHADERBIN_SHA256))


class GraphPlanConsistencyTests(unittest.TestCase):
    def test_inconsistent_plans_reject_group(self):
        g = EffectiveMaterialGroup(fingerprint="abc", canonical_ir={})
        p1 = ({"op": "texture", "slot": {"tiling": [1.0, 1.0]}},)
        p2 = ({"op": "texture", "slot": {"tiling": [32.0, 32.0]}},)
        audit_group_graph_plans(g, [p1, p2])
        self.assertFalse(g.graph_plan_consistent)

    def test_normalize_strips_material_name(self):
        plan = (
            {"op": "material_meta", "name": "a", "pipeline": "forza-ir-v1"},
            {"op": "link_base_color"},
        )
        n = normalize_graph_plan_for_dedup(plan)
        self.assertNotIn("name", n[0])
        self.assertNotIn("pipeline", n[0])


class ShareCacheProvenanceTests(unittest.TestCase):
    def test_source_provenance_recoverable_after_store(self):
        cache = EffectiveMaterialShareCache(
            enabled_shaderbin_sha256=frozenset({CAR_STANDARD_SHADERBIN_SHA256})
        )
        # Force-enable for this unit test only via monkeypatch of membership.
        import io_import_forza_carbin.materials.effective_material as em

        old = em.PRODUCTION_SHARING_ENABLED_SHA256
        try:
            em.PRODUCTION_SHARING_ENABLED_SHA256 = frozenset(
                {CAR_STANDARD_SHADERBIN_SHA256}
            )
            cache.enabled_shaderbin_sha256 = frozenset({CAR_STANDARD_SHADERBIN_SHA256})
            src_a = SourceInstanceRef(
                instance_key="k1",
                source_material_name="matA",
                car_id="GMA_T50_22",
                modelbin_game_path="GAME:\\a.modelbin",
                material_slot_index=0,
            )
            src_b = SourceInstanceRef(
                instance_key="k2",
                source_material_name="matB",
                car_id="FER_F80_25",
                modelbin_game_path="GAME:\\b.modelbin",
                material_slot_index=3,
            )
            cache.store(
                fingerprint="fp1",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                material="Material_Datablock",
                source=src_a,
            )
            cache.store(
                fingerprint="fp1",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                material="Material_Datablock",
                source=src_b,
            )
            prov = cache.provenance_for("fp1")
            self.assertEqual(len(prov), 2)
            self.assertEqual({p.instance_key for p in prov}, {"k1", "k2"})
            self.assertEqual(cache.lookup("fp1"), "Material_Datablock")
        finally:
            em.PRODUCTION_SHARING_ENABLED_SHA256 = old

    def test_store_refuses_when_sharing_disabled(self):
        cache = EffectiveMaterialShareCache()
        with self.assertRaises(RuntimeError):
            cache.store(
                fingerprint="fp",
                shaderbin_sha256=CAR_STANDARD_SHADERBIN_SHA256,
                material=object(),
                source=SourceInstanceRef(
                    instance_key="k",
                    source_material_name="m",
                    car_id="c",
                    modelbin_game_path="g",
                ),
            )


if __name__ == "__main__":
    unittest.main()
