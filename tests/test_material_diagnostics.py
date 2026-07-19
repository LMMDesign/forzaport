"""Unit tests for material import diagnostics (no bpy)."""

from __future__ import annotations

import json
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
    UV_CHOICE_FALSE_TEXCOORD,
    UV_CHOICE_ON_CH1_OFF_CH2,
    UV_CHOICE_TRUE_TEXCOORD,
    probe_clean_v3_capability,
    resolve_uv_choice_texcoord,
)
from io_import_forza_carbin.materials.diagnostics import (
    AssignmentOutcome,
    ImportMaterialReport,
    MaterialCapability,
    MaterialDiagnostic,
    MaterialStatus,
    ParameterDiagnostic,
    ProvenanceDiagnostic,
    StageOutcome,
    TextureBindingDiagnostic,
    classify_capability_status,
    is_fully_supported,
    report_from_json,
)


def _diag(
    *,
    key: str,
    status: MaterialStatus,
    capability: str | None = MaterialCapability.CLEAN_V3_BASE_ALPHA_NORMAL_RMAO.value,
    unresolved: tuple[int, ...] = (),
    errors: tuple[str, ...] = (),
    assignment: AssignmentOutcome = AssignmentOutcome.SKIPPED,
    objects: tuple[str, ...] = (),
) -> MaterialDiagnostic:
    return MaterialDiagnostic(
        material_name=key.split("|")[1] if "|" in key else key,
        instance_key=key,
        shader_name="car_standard",
        material_name_hash=None,
        shader_name_hash=None,
        capability=capability,
        status=status,
        instance_parameters=(),
        texture_bindings=(),
        unresolved_semantics=unresolved,
        evidence=(
            ProvenanceDiagnostic(
                kind="test", detail="unit", source="test_material_diagnostics"
            ),
        ),
        warnings=(),
        errors=errors,
        parsing_outcome=StageOutcome.OK,
        capability_outcome=(
            StageOutcome.OK
            if status is MaterialStatus.SUPPORTED
            else StageOutcome.PARTIAL
            if status is MaterialStatus.PARTIALLY_SUPPORTED
            else StageOutcome.FAILED
        ),
        construction_outcome=StageOutcome.SKIPPED,
        assignment_outcome=assignment,
        failure_reason=errors[0] if errors else "",
        affected_object_names=objects,
    )


class MaterialDiagnosticsTests(unittest.TestCase):
    def test_status_classification(self):
        self.assertEqual(
            classify_capability_status(
                capability_selected=True,
                unresolved_semantics=(),
                missing_texture=False,
                missing_provenance=False,
                invalid_binding=False,
            ),
            MaterialStatus.SUPPORTED,
        )
        self.assertEqual(
            classify_capability_status(
                capability_selected=True,
                unresolved_semantics=(0x1111,),
                missing_texture=False,
                missing_provenance=False,
                invalid_binding=False,
            ),
            MaterialStatus.PARTIALLY_SUPPORTED,
        )
        self.assertEqual(
            classify_capability_status(
                capability_selected=False,
                unresolved_semantics=(),
                missing_texture=False,
                missing_provenance=False,
                invalid_binding=False,
            ),
            MaterialStatus.UNRESOLVED_CAPABILITY,
        )
        self.assertEqual(
            classify_capability_status(
                capability_selected=False,
                unresolved_semantics=(),
                missing_texture=False,
                missing_provenance=True,
                invalid_binding=False,
            ),
            MaterialStatus.MISSING_PROVENANCE,
        )
        self.assertEqual(
            classify_capability_status(
                capability_selected=False,
                unresolved_semantics=(),
                missing_texture=True,
                missing_provenance=False,
                invalid_binding=False,
            ),
            MaterialStatus.MISSING_TEXTURE,
        )

    def test_unsupported_never_classified_supported(self):
        for status in (
            MaterialStatus.UNRESOLVED_CAPABILITY,
            MaterialStatus.MISSING_PROVENANCE,
            MaterialStatus.MISSING_TEXTURE,
            MaterialStatus.INVALID_BINDING,
            MaterialStatus.BUILDER_ERROR,
            MaterialStatus.PARTIALLY_SUPPORTED,
        ):
            self.assertFalse(is_fully_supported(status))

    def test_capability_probe_rejects_without_surface(self):
        probe = probe_clean_v3_capability(
            shader_name="car_blackhole", has_resolvable_surface=False
        )
        self.assertFalse(probe.selected)
        self.assertIsNone(probe.capability)

    def test_uv_choice_contract_true_ch1_false_ch2(self):
        missing = resolve_uv_choice_texcoord({})
        self.assertIsNone(missing)
        on = resolve_uv_choice_texcoord(
            {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=True)}
        )
        off = resolve_uv_choice_texcoord(
            {UV_CHOICE_ON_CH1_OFF_CH2: SimpleNamespace(type=3, value=False)}
        )
        self.assertEqual(on[0], UV_CHOICE_TRUE_TEXCOORD)
        self.assertEqual(off[0], UV_CHOICE_FALSE_TEXCOORD)
        self.assertEqual(on[0], 0)
        self.assertEqual(off[0], 1)
        self.assertIn("TEXCOORD0", on[1].detail)
        self.assertIn("TEXCOORD1", off[1].detail)

    def test_partial_and_missing_provenance_reporting(self):
        report = ImportMaterialReport(
            forza_version="3.1.0",
            blender_version="5.1.1",
            game_key="fh6",
            pipeline="clean_v3",
            car_id="FER_F80_25",
        )
        report.upsert(
            _diag(
                key="fh6|A|v3-1",
                status=MaterialStatus.PARTIALLY_SUPPORTED,
                unresolved=(0xAABBCCDD,),
                assignment=AssignmentOutcome.ASSIGNED_RESOLVED,
                objects=("Body.001",),
            )
        )
        report.upsert(
            _diag(
                key="fh6|B|v3-2",
                status=MaterialStatus.MISSING_PROVENANCE,
                capability=None,
                errors=("no NameHash entry",),
                assignment=AssignmentOutcome.ASSIGNED_DIAGNOSTIC,
                objects=("Glass.001",),
            )
        )
        counts = report.summary_counts()
        self.assertEqual(counts["materials_encountered"], 2)
        self.assertEqual(counts["partially_supported"], 1)
        self.assertEqual(counts["unresolved"], 1)
        self.assertEqual(counts["objects_with_diagnostic_materials"], 1)
        self.assertEqual(
            report.top_unresolved_name_hashes(1),
            [("0xAABBCCDD", 1)],
        )

    def test_builder_error_status(self):
        diag = _diag(key="fh6|C|v3-3", status=MaterialStatus.SUPPORTED)
        failed = diag.with_construction(
            outcome=StageOutcome.FAILED,
            status=MaterialStatus.BUILDER_ERROR,
            error="node boom",
        )
        self.assertEqual(failed.status, MaterialStatus.BUILDER_ERROR)
        self.assertEqual(failed.construction_outcome, StageOutcome.FAILED)
        self.assertIn("node boom", failed.errors)

    def test_deterministic_json_ordering(self):
        report = ImportMaterialReport(
            forza_version="3.1.0",
            blender_version="5.1.1",
            game_key="fh6",
            pipeline="clean_v3",
            car_id="FER_F80_25",
        )
        report.upsert(_diag(key="fh6|Zed|v3-z", status=MaterialStatus.SUPPORTED))
        report.upsert(
            _diag(
                key="fh6|Alpha|v3-a",
                status=MaterialStatus.UNRESOLVED_CAPABILITY,
                capability=None,
                errors=("unsupported",),
                assignment=AssignmentOutcome.ASSIGNED_DIAGNOSTIC,
                objects=("ObjB", "ObjA"),
            )
        )
        first = report.to_json()
        second = report.to_json()
        self.assertEqual(first, second)
        data = json.loads(first)
        keys = [row["instance_key"] for row in data["materials"]]
        self.assertEqual(keys, sorted(keys, key=str.lower))
        restored = report_from_json(data)
        self.assertEqual(restored.to_json(), first)

    def test_parameter_and_texture_models_serialize(self):
        diag = MaterialDiagnostic(
            material_name="Plastic_Smooth",
            instance_key="fh6|Plastic_Smooth|v3-x",
            shader_name="car_standard",
            material_name_hash=None,
            shader_name_hash=None,
            capability=MaterialCapability.CLEAN_V3_BASE_ALPHA_NORMAL_RMAO.value,
            status=MaterialStatus.PARTIALLY_SUPPORTED,
            instance_parameters=(
                ParameterDiagnostic(
                    name_hash=0x1,
                    name="Roughness_Shift",
                    raw_type=2,
                    raw_value=-1.0,
                    interpreted=None,
                    consumed_by_builder=False,
                    provenance=(),
                ),
            ),
            texture_bindings=(
                TextureBindingDiagnostic(
                    name_hash=0x2,
                    name="Alpha",
                    texture_register=16,
                    semantic_role="alpha",
                    path="Game:\\a.swatchbin",
                    path_exists=True,
                    uv_channel=0,
                    uv_role="TEXCOORD0",
                    color_space="Non-Color",
                    alpha_interpretation="channel:x",
                    sampler_register=3,
                    sampler_address={"U": "REPEAT", "V": "REPEAT"},
                    consumed_by_builder=False,
                    unresolved_reason="semantic_not_in_capability:alpha",
                    provenance=(),
                ),
            ),
            unresolved_semantics=(0x2,),
            evidence=(),
            warnings=("1 TXMP",),
            errors=(),
        )
        report = ImportMaterialReport(
            forza_version="3.1.0",
            blender_version="5.1.1",
            game_key="fh6",
            pipeline="clean_v3",
            car_id="t",
        )
        report.upsert(diag)
        blob = report.to_json_dict()
        self.assertEqual(blob["materials"][0]["texture_bindings"][0]["name"], "Alpha")


if __name__ == "__main__":
    unittest.main()
