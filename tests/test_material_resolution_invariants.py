"""Unit tests for MaterialResolution invariants and graph plan equivalence."""

from __future__ import annotations

import os
import sys
import types
import unittest

_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "addon", "io_import_forza_carbin"))
_pkg = types.ModuleType("io_import_forza_carbin")
_pkg.__path__ = [_ROOT]
sys.modules.setdefault("io_import_forza_carbin", _pkg)

from io_import_forza_carbin.materials.model import (
    BaseColorSourceKind,
    CapabilityProbeResult,
    CleanSurfaceCapability,
    InconsistentMaterialResolution,
    MaterialCapabilityKind,
    MaterialResolution,
    ProvenanceDiagnostic,
    ResolvedBaseColorSource,
    ResolvedMaterial,
    ResolvedTextureSlot,
    make_clean_surface_capability,
)
from io_import_forza_carbin.materials.graph_plan import (
    ensure_resolved_material,
    graph_build_plan,
)
from io_import_forza_carbin.materials.pipeline_v3 import (
    material_spec_from_resolved,
    resolved_material_from_spec,
)
from io_import_forza_carbin.materials.report_compare import (
    compare_material_reports,
    normalize_instance_key,
)


def _slot(role: str = "base_color", **kwargs) -> ResolvedTextureSlot:
    return ResolvedTextureSlot(
        role=role,
        path=kwargs.get("path", "Game:\\x.swatchbin"),
        texcoord=kwargs.get("texcoord", "TEXCOORD0"),
        channel=kwargs.get("channel"),
        tiling=kwargs.get("tiling", (1.0, 1.0)),
        address=kwargs.get("address"),
        param_hash=kwargs.get("param_hash", 1),
        param_name=kwargs.get("param_name", role),
        evidence=(),
    )


def _cap(**kwargs) -> CleanSurfaceCapability:
    base_map = kwargs.get("base_color_map", _slot("base_color"))
    base_color = kwargs.get("base_color", (1.0, 1.0, 1.0, 1.0))
    if "base_color_source" in kwargs:
        source = kwargs["base_color_source"]
    elif base_map is not None:
        source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.TEXTURE,
            texture=base_map,
        )
    else:
        source = ResolvedBaseColorSource(
            kind=BaseColorSourceKind.MATERIAL_CONSTANT,
            color=base_color,
        )
    return make_clean_surface_capability(
        base_color_source=source,
        alpha_map=kwargs.get("alpha_map"),
        normal_map=kwargs.get("normal_map", _slot("normal", param_hash=2)),
        rmao_map=kwargs.get("rmao_map", _slot("rmao", param_hash=3)),
        alpha_mode=kwargs.get("alpha_mode", "OPAQUE"),
        alpha_threshold=kwargs.get("alpha_threshold", 0.5),
        evidence=(
            ProvenanceDiagnostic(
                kind="capability",
                detail=MaterialCapabilityKind.CLEAN_SURFACE.value,
                source="test",
            ),
        ),
    )


def _resolved(cap: CleanSurfaceCapability | None = None) -> ResolvedMaterial:
    capability = cap or _cap()
    return ResolvedMaterial(
        name="fh6|Plastic|v4-x",
        game_key="fh6",
        shader_name="car_standard",
        capability_kind=MaterialCapabilityKind.CLEAN_SURFACE,
        capability=capability,
    )


class MaterialResolutionInvariantTests(unittest.TestCase):
    def test_selected_factory_ok(self):
        resolved = _resolved()
        result = MaterialResolution.selected(resolved)
        self.assertTrue(result.is_selected)
        self.assertIs(result.probe.capability, resolved.capability)
        self.assertIs(result.probe.kind, resolved.capability_kind)

    def test_rejected_factory_ok(self):
        result = MaterialResolution.rejected(
            reasons=("unsupported",),
            evidence=(
                ProvenanceDiagnostic(kind="test", detail="x", source="t"),
            ),
        )
        self.assertFalse(result.is_selected)
        self.assertIsNone(result.resolved)
        self.assertIsNone(result.probe.capability)

    def test_rejected_requires_reason(self):
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution.rejected(reasons=())

    def test_invalid_resolved_without_kind(self):
        resolved = _resolved()
        probe = CapabilityProbeResult(
            kind=None,
            capability=resolved.capability,
            evidence=(),
        )
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution(resolved=resolved, probe=probe)

    def test_invalid_resolved_without_capability(self):
        resolved = _resolved()
        probe = CapabilityProbeResult(
            kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=None,
            evidence=(),
        )
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution(resolved=resolved, probe=probe)

    def test_invalid_kind_mismatch(self):
        resolved = _resolved()
        # Can't construct a different kind today; forge via object.__new__ path
        # by using a probe with same enum but different capability identity.
        other_cap = _cap(base_color=(0.5, 0.5, 0.5, 1.0))
        probe = CapabilityProbeResult(
            kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=other_cap,
            evidence=(),
        )
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution(resolved=resolved, probe=probe)

    def test_invalid_probe_capability_without_resolved(self):
        cap = _cap()
        probe = CapabilityProbeResult(
            kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=cap,
            evidence=(),
            rejection_reasons=("should not happen",),
        )
        # resolved None + capability set is illegal regardless of rejection.
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution(resolved=None, probe=probe)

    def test_invalid_kind_without_rejection_when_unresolved(self):
        probe = CapabilityProbeResult(
            kind=MaterialCapabilityKind.CLEAN_SURFACE,
            capability=None,
            evidence=(),
            rejection_reasons=(),
        )
        with self.assertRaises(InconsistentMaterialResolution):
            MaterialResolution(resolved=None, probe=probe)


class GraphPlanEquivalenceTests(unittest.TestCase):
    def test_spec_and_resolved_same_plan(self):
        resolved = _resolved(
            _cap(
                alpha_map=_slot("alpha", channel="x", param_hash=4),
                alpha_mode="CLIP",
            )
        )
        spec = material_spec_from_resolved(resolved)
        via_spec = ensure_resolved_material(spec)
        self.assertEqual(graph_build_plan(resolved), graph_build_plan(via_spec))

    def test_round_trip_spec_preserves_slot_fields(self):
        resolved = _resolved(
            _cap(
                base_color_map=_slot(
                    "base_color",
                    path="Game:\\a.swatchbin",
                    texcoord="TEXCOORD1",
                    tiling=(2.0, 3.0),
                    address={"U": "MIRROR", "V": "REPEAT"},
                    param_hash=0x85E937A9,
                    param_name="BaseColorAlpha",
                )
            )
        )
        again = resolved_material_from_spec(material_spec_from_resolved(resolved))
        a = resolved.capability.base_color_map
        b = again.capability.base_color_map
        self.assertEqual(a.path, b.path)
        self.assertEqual(a.texcoord, b.texcoord)
        self.assertEqual(a.tiling, b.tiling)
        self.assertEqual(dict(a.address or {}), dict(b.address or {}))
        self.assertEqual(a.param_hash, b.param_hash)


class ReportCompareTests(unittest.TestCase):
    def test_normalize_instance_key(self):
        self.assertEqual(
            normalize_instance_key("fh6|BlackFrame|v3-abc"),
            normalize_instance_key("fh6|BlackFrame|v4-abc"),
        )

    def test_identical_reports_pass(self):
        entry = {
            "instance_key": "fh6|A|v3-1",
            "material_name": "A",
            "shader_name": "car_standard",
            "capability": "clean_v3.base_alpha_normal_rmao",
            "status": "SUPPORTED",
            "assignment_outcome": "ASSIGNED_RESOLVED",
            "affected_object_names": ["synthetic:A"],
            "unresolved_semantics": [],
            "texture_bindings": [
                {
                    "name_hash": 1,
                    "name": "Normal",
                    "semantic_role": "normal",
                    "path": "Game:\\n.swatchbin",
                    "uv_channel": 0,
                    "uv_role": "TEXCOORD0",
                    "consumed_by_builder": True,
                    "alpha_interpretation": None,
                    "sampler_address": None,
                }
            ],
            "evidence": [],
        }
        cur = dict(entry)
        cur["instance_key"] = "fh6|A|v4-1"
        cur["binding_contract"] = {
            "alpha_mode": "OPAQUE",
            "alpha_threshold": 0.5,
            "base_color": [1, 1, 1, 1],
            "consumed_txmp_hashes": [1],
            "slots": [
                {
                    "role": "normal",
                    "path": "Game:\\n.swatchbin",
                    "texcoord": "TEXCOORD0",
                    "channel": None,
                    "param_hash": 1,
                    "param_name": "Normal",
                }
            ],
        }
        diff = compare_material_reports(
            {"materials": [entry], "count_by_status": {"SUPPORTED": 1}},
            {"materials": [cur], "count_by_status": {"SUPPORTED": 1}},
        )
        self.assertTrue(diff["pass"])
        self.assertTrue(diff["behavioural_identical"])


if __name__ == "__main__":
    unittest.main()
