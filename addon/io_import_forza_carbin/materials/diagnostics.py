"""Immutable material import diagnostics (no bpy).

Infrastructure for the material rewrite: every MatI encountered during import
produces a structured record of source data, capability selection, evidence,
resolved/unresolved inputs, and final status. Shading behaviour is not decided
here — only observed and classified under MATERIAL_BOUNDARY.md.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable

from .model import MaterialCapabilityKind, ProvenanceDiagnostic


class MaterialStatus(Enum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNRESOLVED_CAPABILITY = "UNRESOLVED_CAPABILITY"
    MISSING_PROVENANCE = "MISSING_PROVENANCE"
    MISSING_TEXTURE = "MISSING_TEXTURE"
    SOURCE_TEXTURE_NOT_FOUND = "SOURCE_TEXTURE_NOT_FOUND"
    SOURCE_TEXTURE_MEMBER_NOT_FOUND = "SOURCE_TEXTURE_MEMBER_NOT_FOUND"
    SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED = "SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED"
    TEXTURE_READ_FAILED = "TEXTURE_READ_FAILED"
    TEXTURE_DECODE_FAILED = "TEXTURE_DECODE_FAILED"
    BLENDER_IMAGE_CREATION_FAILED = "BLENDER_IMAGE_CREATION_FAILED"
    INVALID_BINDING = "INVALID_BINDING"
    BUILDER_ERROR = "BUILDER_ERROR"


class StageOutcome(Enum):
    """Per-stage result; independent of whether a bpy material object exists."""

    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AssignmentOutcome(Enum):
    ASSIGNED_RESOLVED = "ASSIGNED_RESOLVED"
    ASSIGNED_DIAGNOSTIC = "ASSIGNED_DIAGNOSTIC"
    SKIPPED = "SKIPPED"


class MaterialCapability(Enum):
    """Registered material capabilities (aligned with MaterialCapabilityKind)."""

    CLEAN_V3_BASE_ALPHA_NORMAL_RMAO = MaterialCapabilityKind.CLEAN_SURFACE.value


DIAGNOSTIC_MATERIAL_NAME = "FORZAPORT_UNRESOLVED_MATERIAL"


@dataclass(frozen=True)
class ParameterDiagnostic:
    name_hash: int
    name: str | None
    raw_type: int | None
    raw_value: Any
    interpreted: str | None
    consumed_by_builder: bool
    provenance: tuple[ProvenanceDiagnostic, ...] = ()


@dataclass(frozen=True)
class TextureBindingDiagnostic:
    name_hash: int
    name: str | None
    texture_register: int | None
    semantic_role: str | None
    path: str
    path_exists: bool
    uv_channel: int | None
    uv_role: str | None
    color_space: str | None
    alpha_interpretation: str | None
    sampler_register: int | None
    sampler_address: dict[str, str] | None
    consumed_by_builder: bool
    unresolved_reason: str | None
    provenance: tuple[ProvenanceDiagnostic, ...] = ()
    # Typed texture-source layer (optional; empty when unresolved at path stage)
    canonical_path: str | None = None
    source_kind: str | None = None
    archive_path: str | None = None
    archive_member: str | None = None
    filesystem_path: str | None = None
    source_failure: str | None = None
    attempts: tuple[str, ...] = ()
    # Binding activation (presence ≠ use)
    activation: str | None = None
    activation_reason: str | None = None
    controlling_parameters: tuple[int, ...] = ()
    selected_base_color_source: str | None = None


@dataclass(frozen=True)
class MaterialDiagnostic:
    """One source material instance after capability resolution (+ later stages)."""

    material_name: str
    instance_key: str
    shader_name: str | None
    material_name_hash: int | None
    shader_name_hash: int | None
    capability: str | None
    status: MaterialStatus
    instance_parameters: tuple[ParameterDiagnostic, ...]
    texture_bindings: tuple[TextureBindingDiagnostic, ...]
    unresolved_semantics: tuple[int, ...]
    evidence: tuple[ProvenanceDiagnostic, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    parsing_outcome: StageOutcome = StageOutcome.OK
    capability_outcome: StageOutcome = StageOutcome.FAILED
    construction_outcome: StageOutcome = StageOutcome.SKIPPED
    assignment_outcome: AssignmentOutcome = AssignmentOutcome.SKIPPED
    failure_reason: str = ""
    affected_object_names: tuple[str, ...] = ()

    def with_construction(
        self,
        *,
        outcome: StageOutcome,
        status: MaterialStatus | None = None,
        error: str | None = None,
    ) -> MaterialDiagnostic:
        errors = self.errors
        if error:
            errors = self.errors + (error,)
        return MaterialDiagnostic(
            material_name=self.material_name,
            instance_key=self.instance_key,
            shader_name=self.shader_name,
            material_name_hash=self.material_name_hash,
            shader_name_hash=self.shader_name_hash,
            capability=self.capability,
            status=status or self.status,
            instance_parameters=self.instance_parameters,
            texture_bindings=self.texture_bindings,
            unresolved_semantics=self.unresolved_semantics,
            evidence=self.evidence,
            warnings=self.warnings,
            errors=errors,
            parsing_outcome=self.parsing_outcome,
            capability_outcome=self.capability_outcome,
            construction_outcome=outcome,
            assignment_outcome=self.assignment_outcome,
            failure_reason=error or self.failure_reason,
            affected_object_names=self.affected_object_names,
        )

    def with_assignment(
        self,
        *,
        outcome: AssignmentOutcome,
        object_name: str | None = None,
    ) -> MaterialDiagnostic:
        names = self.affected_object_names
        if object_name and object_name not in names:
            names = tuple(sorted(names + (object_name,)))
        return MaterialDiagnostic(
            material_name=self.material_name,
            instance_key=self.instance_key,
            shader_name=self.shader_name,
            material_name_hash=self.material_name_hash,
            shader_name_hash=self.shader_name_hash,
            capability=self.capability,
            status=self.status,
            instance_parameters=self.instance_parameters,
            texture_bindings=self.texture_bindings,
            unresolved_semantics=self.unresolved_semantics,
            evidence=self.evidence,
            warnings=self.warnings,
            errors=self.errors,
            parsing_outcome=self.parsing_outcome,
            capability_outcome=self.capability_outcome,
            construction_outcome=self.construction_outcome,
            assignment_outcome=outcome,
            failure_reason=self.failure_reason,
            affected_object_names=names,
        )

    def with_affected_object(self, object_name: str) -> MaterialDiagnostic:
        if object_name in self.affected_object_names:
            return self
        names = tuple(sorted(self.affected_object_names + (object_name,)))
        return MaterialDiagnostic(
            material_name=self.material_name,
            instance_key=self.instance_key,
            shader_name=self.shader_name,
            material_name_hash=self.material_name_hash,
            shader_name_hash=self.shader_name_hash,
            capability=self.capability,
            status=self.status,
            instance_parameters=self.instance_parameters,
            texture_bindings=self.texture_bindings,
            unresolved_semantics=self.unresolved_semantics,
            evidence=self.evidence,
            warnings=self.warnings,
            errors=self.errors,
            parsing_outcome=self.parsing_outcome,
            capability_outcome=self.capability_outcome,
            construction_outcome=self.construction_outcome,
            assignment_outcome=self.assignment_outcome,
            failure_reason=self.failure_reason,
            affected_object_names=names,
        )


def is_fully_supported(status: MaterialStatus) -> bool:
    return status is MaterialStatus.SUPPORTED


def is_unresolved_family(status: MaterialStatus) -> bool:
    return status in (
        MaterialStatus.UNRESOLVED_CAPABILITY,
        MaterialStatus.MISSING_PROVENANCE,
        MaterialStatus.MISSING_TEXTURE,
        MaterialStatus.SOURCE_TEXTURE_NOT_FOUND,
        MaterialStatus.SOURCE_TEXTURE_MEMBER_NOT_FOUND,
        MaterialStatus.SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED,
        MaterialStatus.TEXTURE_READ_FAILED,
        MaterialStatus.TEXTURE_DECODE_FAILED,
        MaterialStatus.BLENDER_IMAGE_CREATION_FAILED,
        MaterialStatus.INVALID_BINDING,
        MaterialStatus.BUILDER_ERROR,
    )


def is_missing_texture_family(status: MaterialStatus) -> bool:
    return status in (
        MaterialStatus.MISSING_TEXTURE,
        MaterialStatus.SOURCE_TEXTURE_NOT_FOUND,
        MaterialStatus.SOURCE_TEXTURE_MEMBER_NOT_FOUND,
        MaterialStatus.SOURCE_TEXTURE_ARCHIVE_NOT_INDEXED,
        MaterialStatus.TEXTURE_READ_FAILED,
        MaterialStatus.TEXTURE_DECODE_FAILED,
        MaterialStatus.BLENDER_IMAGE_CREATION_FAILED,
    )


def classify_capability_status(
    *,
    capability_selected: bool,
    unresolved_semantics: Iterable[int],
    missing_texture: bool,
    missing_provenance: bool,
    invalid_binding: bool,
) -> MaterialStatus:
    """Classify after capability resolution, before Blender node construction.

    A half-Principled that later builds successfully must still be PARTIAL when
    unresolved_semantics is non-empty — never silently SUPPORTED.
    """
    if missing_provenance:
        return MaterialStatus.MISSING_PROVENANCE
    if invalid_binding:
        return MaterialStatus.INVALID_BINDING
    if missing_texture and not capability_selected:
        return MaterialStatus.MISSING_TEXTURE
    if not capability_selected:
        return MaterialStatus.UNRESOLVED_CAPABILITY
    if tuple(unresolved_semantics) or missing_texture:
        return MaterialStatus.PARTIALLY_SUPPORTED
    return MaterialStatus.SUPPORTED


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple):
        return [_json_ready(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, float):
        return round(value, 9)
    return value


def diagnostic_to_dict(diag: MaterialDiagnostic) -> dict[str, Any]:
    raw = asdict(diag)
    return {k: _json_ready(v) for k, v in raw.items()}


@dataclass
class ImportMaterialReport:
    """Per-car aggregated material diagnostics (mutable builder during import)."""

    forza_version: str
    blender_version: str
    game_key: str
    pipeline: str
    car_id: str
    entries: dict[str, MaterialDiagnostic] = field(default_factory=dict)

    def upsert(self, diag: MaterialDiagnostic) -> None:
        existing = self.entries.get(diag.instance_key)
        if existing is None:
            self.entries[diag.instance_key] = diag
            return
        # Merge affected objects; prefer richer later-stage outcomes.
        merged = diag
        if existing.affected_object_names:
            names = tuple(
                sorted(set(existing.affected_object_names) | set(diag.affected_object_names))
            )
            merged = MaterialDiagnostic(
                material_name=diag.material_name,
                instance_key=diag.instance_key,
                shader_name=diag.shader_name,
                material_name_hash=diag.material_name_hash,
                shader_name_hash=diag.shader_name_hash,
                capability=diag.capability,
                status=diag.status,
                instance_parameters=diag.instance_parameters,
                texture_bindings=diag.texture_bindings,
                unresolved_semantics=diag.unresolved_semantics,
                evidence=diag.evidence,
                warnings=diag.warnings,
                errors=diag.errors,
                parsing_outcome=diag.parsing_outcome,
                capability_outcome=diag.capability_outcome,
                construction_outcome=diag.construction_outcome,
                assignment_outcome=diag.assignment_outcome,
                failure_reason=diag.failure_reason or existing.failure_reason,
                affected_object_names=names,
            )
        self.entries[diag.instance_key] = merged

    def record_object(self, instance_key: str, object_name: str) -> None:
        diag = self.entries.get(instance_key)
        if diag is None:
            return
        self.entries[instance_key] = diag.with_affected_object(object_name)

    def sorted_entries(self) -> list[MaterialDiagnostic]:
        return [
            self.entries[k]
            for k in sorted(self.entries.keys(), key=lambda s: s.lower())
        ]

    def summary_counts(self) -> dict[str, int]:
        counts = {
            "materials_encountered": len(self.entries),
            "fully_supported": 0,
            "partially_supported": 0,
            "unresolved": 0,
            "builder_errors": 0,
            "objects_with_diagnostic_materials": 0,
            "texture_bindings_encountered": 0,
            "resolved_loose_textures": 0,
            "resolved_archive_textures": 0,
            "source_textures_not_found": 0,
            "archive_members_not_found": 0,
            "texture_read_failures": 0,
            "texture_decode_failures": 0,
            "blender_image_creation_failures": 0,
        }
        for diag in self.entries.values():
            if diag.status is MaterialStatus.SUPPORTED:
                counts["fully_supported"] += 1
            elif diag.status is MaterialStatus.PARTIALLY_SUPPORTED:
                counts["partially_supported"] += 1
            elif diag.status is MaterialStatus.BUILDER_ERROR:
                counts["builder_errors"] += 1
                counts["unresolved"] += 1
            else:
                counts["unresolved"] += 1
            if diag.assignment_outcome is AssignmentOutcome.ASSIGNED_DIAGNOSTIC:
                counts["objects_with_diagnostic_materials"] += len(
                    diag.affected_object_names
                )
            for tb in diag.texture_bindings:
                counts["texture_bindings_encountered"] += 1
                kind = (tb.source_kind or "").lower()
                if tb.path_exists and kind == "loose_file":
                    counts["resolved_loose_textures"] += 1
                elif tb.path_exists and kind == "zip_member":
                    counts["resolved_archive_textures"] += 1
                fail = tb.source_failure or tb.unresolved_reason or ""
                if fail in ("SOURCE_TEXTURE_NOT_FOUND", "missing_file"):
                    counts["source_textures_not_found"] += 1
                elif fail == "SOURCE_TEXTURE_MEMBER_NOT_FOUND":
                    counts["archive_members_not_found"] += 1
                elif fail == "TEXTURE_READ_FAILED":
                    counts["texture_read_failures"] += 1
                elif fail == "TEXTURE_DECODE_FAILED":
                    counts["texture_decode_failures"] += 1
                elif fail == "BLENDER_IMAGE_CREATION_FAILED":
                    counts["blender_image_creation_failures"] += 1
        return counts

    def count_by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for diag in self.entries.values():
            key = diag.status.value
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    def count_by_capability(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for diag in self.entries.values():
            key = diag.capability or "(none)"
            out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    def top_unresolved_name_hashes(self, limit: int = 20) -> list[tuple[str, int]]:
        tallies: dict[int, int] = {}
        for diag in self.entries.values():
            for h in diag.unresolved_semantics:
                tallies[h & 0xFFFFFFFF] = tallies.get(h & 0xFFFFFFFF, 0) + 1
        ranked = sorted(tallies.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(f"0x{h:08X}", n) for h, n in ranked[:limit]]

    def top_unresolved_shaders(self, limit: int = 20) -> list[tuple[str, int]]:
        tallies: dict[str, int] = {}
        for diag in self.entries.values():
            if not is_unresolved_family(diag.status):
                continue
            key = diag.shader_name or "(no shader)"
            tallies[key] = tallies.get(key, 0) + 1
        ranked = sorted(tallies.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        return ranked[:limit]

    def to_json_dict(self) -> dict[str, Any]:
        entries = [diagnostic_to_dict(d) for d in self.sorted_entries()]
        return {
            "forza_port_version": self.forza_version,
            "blender_version": self.blender_version,
            "game_key": self.game_key,
            "pipeline": self.pipeline,
            "car_id": self.car_id,
            "summary": self.summary_counts(),
            "count_by_status": self.count_by_status(),
            "count_by_capability": self.count_by_capability(),
            "top_unresolved_name_hashes": [
                {"name_hash": h, "count": n}
                for h, n in self.top_unresolved_name_hashes()
            ],
            "top_unresolved_shaders": [
                {"shader": s, "count": n} for s, n in self.top_unresolved_shaders()
            ],
            "materials": entries,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_json_dict(), indent=indent, sort_keys=True) + "\n"


def report_from_json(data: dict[str, Any]) -> ImportMaterialReport:
    """Rebuild a report from exported JSON (tests / refresh helpers)."""
    report = ImportMaterialReport(
        forza_version=str(data.get("forza_port_version") or ""),
        blender_version=str(data.get("blender_version") or ""),
        game_key=str(data.get("game_key") or ""),
        pipeline=str(data.get("pipeline") or ""),
        car_id=str(data.get("car_id") or ""),
    )
    for row in data.get("materials") or []:
        status = MaterialStatus(row["status"])
        diag = MaterialDiagnostic(
            material_name=row["material_name"],
            instance_key=row["instance_key"],
            shader_name=row.get("shader_name"),
            material_name_hash=row.get("material_name_hash"),
            shader_name_hash=row.get("shader_name_hash"),
            capability=row.get("capability"),
            status=status,
            instance_parameters=tuple(
                ParameterDiagnostic(
                    name_hash=int(p["name_hash"]),
                    name=p.get("name"),
                    raw_type=p.get("raw_type"),
                    raw_value=p.get("raw_value"),
                    interpreted=p.get("interpreted"),
                    consumed_by_builder=bool(p.get("consumed_by_builder")),
                    provenance=tuple(
                        ProvenanceDiagnostic(**ev) for ev in (p.get("provenance") or ())
                    ),
                )
                for p in (row.get("instance_parameters") or ())
            ),
            texture_bindings=tuple(
                TextureBindingDiagnostic(
                    name_hash=int(t["name_hash"]),
                    name=t.get("name"),
                    texture_register=t.get("texture_register"),
                    semantic_role=t.get("semantic_role"),
                    path=t.get("path") or "",
                    path_exists=bool(t.get("path_exists")),
                    uv_channel=t.get("uv_channel"),
                    uv_role=t.get("uv_role"),
                    color_space=t.get("color_space"),
                    alpha_interpretation=t.get("alpha_interpretation"),
                    sampler_register=t.get("sampler_register"),
                    sampler_address=t.get("sampler_address"),
                    consumed_by_builder=bool(t.get("consumed_by_builder")),
                    unresolved_reason=t.get("unresolved_reason"),
                    provenance=tuple(
                        ProvenanceDiagnostic(**ev) for ev in (t.get("provenance") or ())
                    ),
                    canonical_path=t.get("canonical_path"),
                    source_kind=t.get("source_kind"),
                    archive_path=t.get("archive_path"),
                    archive_member=t.get("archive_member"),
                    filesystem_path=t.get("filesystem_path"),
                    source_failure=t.get("source_failure"),
                    attempts=tuple(t.get("attempts") or ()),
                )
                for t in (row.get("texture_bindings") or ())
            ),
            unresolved_semantics=tuple(int(x) for x in (row.get("unresolved_semantics") or ())),
            evidence=tuple(
                ProvenanceDiagnostic(**ev) for ev in (row.get("evidence") or ())
            ),
            warnings=tuple(row.get("warnings") or ()),
            errors=tuple(row.get("errors") or ()),
            parsing_outcome=StageOutcome(row.get("parsing_outcome") or "OK"),
            capability_outcome=StageOutcome(row.get("capability_outcome") or "FAILED"),
            construction_outcome=StageOutcome(
                row.get("construction_outcome") or "SKIPPED"
            ),
            assignment_outcome=AssignmentOutcome(
                row.get("assignment_outcome") or "SKIPPED"
            ),
            failure_reason=row.get("failure_reason") or "",
            affected_object_names=tuple(row.get("affected_object_names") or ()),
        )
        report.upsert(diag)
    return report
