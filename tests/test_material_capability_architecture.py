"""Architecture tests for typed capability resolution (no bpy)."""

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

from io_import_forza_carbin.materials.capabilities import (
    probe_all_capabilities,
    select_clean_surface_capability,
)
from io_import_forza_carbin.materials.model import (
    BaseColorSourceKind,
    MaterialCapabilityKind,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedMaterial,
    ResolvedTextureSlot,
    make_clean_surface_capability,
)
from io_import_forza_carbin.materials.pipeline_v3 import (
    CleanMaterialBuilder,
    material_spec_from_resolved,
)
from io_import_forza_carbin.materials.txmp_semantics import (
    CLEAN_SURFACE_TXMP_NAMES,
    semantics_for_txmp_hash,
)


def _slot(role: str = "base_color") -> ResolvedTextureSlot:
    return ResolvedTextureSlot(
        role=role,
        path="Game:\\x.swatchbin",
        texcoord="TEXCOORD0",
        evidence=(
            ProvenanceDiagnostic(kind="test", detail=f"{role}:ok", source="test"),
        ),
    )


def _capability():
    return make_clean_surface_capability(
        base_color_source=ResolvedBaseColorSource(
            kind=BaseColorSourceKind.TEXTURE,
            texture=_slot("base_color"),
        ),
        alpha_map=None,
        normal_map=_slot("normal"),
        rmao_map=_slot("rmao"),
        alpha_mode="OPAQUE",
        alpha_threshold=0.5,
        evidence=(
            ProvenanceDiagnostic(
                kind="capability",
                detail=MaterialCapabilityKind.CLEAN_SURFACE.value,
                source="test",
            ),
        ),
    )


class TypedCapabilityArchitectureTests(unittest.TestCase):
    def test_clean_txmp_allowlist_is_exact_six(self):
        self.assertEqual(
            CLEAN_SURFACE_TXMP_NAMES,
            {
                "BaseColorAlpha",
                "BaseColorAlpha_1",
                "Alpha",
                "Normal",
                "WeaveNormal",
                "RoughMetalAO",
            },
        )

    def test_broad_diffuse_role_not_clean_supported(self):
        # DiffuseA shares role=diffuse with BaseColorAlpha but must not widen support.
        from io_import_forza_carbin.materials.name_hashes import _load

        self.assertNotIn("DiffuseA", CLEAN_SURFACE_TXMP_NAMES)
        h = next(hash_ for hash_, name in _load().items() if name == "DiffuseA")
        sem = semantics_for_txmp_hash(h)
        self.assertEqual(sem.role, "diffuse")
        self.assertFalse(sem.supports(MaterialCapabilityKind.CLEAN_SURFACE))

    def test_probe_requires_typed_payload(self):
        incomplete = select_clean_surface_capability(
            shader_name="car_standard",
            capability=None,
        )
        self.assertFalse(incomplete.selected)
        complete = select_clean_surface_capability(
            shader_name="car_standard",
            capability=_capability(),
        )
        self.assertTrue(complete.selected)
        self.assertIs(complete.kind, MaterialCapabilityKind.CLEAN_SURFACE)

    def test_probe_all_does_not_accept_builder_success_flag(self):
        with self.assertRaises(TypeError):
            probe_all_capabilities(
                shader_name="car_standard",
                has_resolvable_surface=True,  # type: ignore[call-arg]
            )

    def test_material_spec_adapter_preserves_slots(self):
        resolved = ResolvedMaterial(
            name="fh6|Plastic|v4-x",
            game_key="fh6",
            shader_name="car_standard",
            capability_kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=_capability(),
        )
        spec = material_spec_from_resolved(resolved)
        self.assertTrue(spec.valid)
        self.assertEqual(spec.shader_name, "car_standard")
        self.assertIsNotNone(spec.base_color_map)
        self.assertEqual(spec.base_color_map.texcoord, "TEXCOORD0")
        self.assertEqual(spec.capability_kind, MaterialCapabilityKind.CLEAN_SURFACE.value)
        self.assertIn("base_color:ok", spec.base_color_map.evidence)

    def test_builder_facade_delegates_to_resolver(self):
        builder = CleanMaterialBuilder(media_root="C:\\does-not-exist-media")
        self.assertEqual(builder.game_key, "fh6")
        # No shader → structured rejection, not a circular "surface" probe.
        material = SimpleNamespace(
            shader_name=None,
            parameters={},
            txmp={},
            cbmp={},
            spmp={},
        )
        result = builder.resolve("fh6|Empty|v4-0", material, resolver=None)
        self.assertFalse(result.is_selected)
        self.assertIsNone(result.resolved)
        self.assertTrue(result.probe.rejection_reasons)


if __name__ == "__main__":
    unittest.main()
