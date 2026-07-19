"""Blender UI for per-car material import reports."""

from __future__ import annotations

import json

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup

from ..contract import PROP_MATERIAL_DIAG_KEY, PROP_MATERIAL_REPORT_TEXT
from .diagnostics import MaterialStatus, is_unresolved_family
from .report_store import (
    find_car_collections_with_reports,
    load_report_from_collection,
)


def _car_report_items(self, context):
    items = []
    for collection, report in find_car_collections_with_reports():
        items.append(
            (
                collection.name,
                f"{report.car_id} ({collection.name})",
                f"Material report for {report.car_id}",
            )
        )
    if not items:
        items.append(("", "(no imported car reports)", ""))
    return items


class ForzaMaterialReportSettings(PropertyGroup):
    car_collection: EnumProperty(
        name="Car",
        description="Imported car root collection that owns a material report",
        items=_car_report_items,
    )
    filter_status: EnumProperty(
        name="Status",
        items=[
            ("ALL", "All", ""),
            ("SUPPORTED", "Supported", ""),
            ("PARTIALLY_SUPPORTED", "Partial", ""),
            ("UNRESOLVED", "Unresolved family", ""),
            ("BUILDER_ERROR", "Builder errors", ""),
            ("UNRESOLVED_CAPABILITY", "Unresolved capability", ""),
            ("MISSING_PROVENANCE", "Missing provenance", ""),
            ("MISSING_TEXTURE", "Missing texture", ""),
            ("INVALID_BINDING", "Invalid binding", ""),
        ],
        default="ALL",
    )
    filter_capability: StringProperty(name="Capability", default="")
    filter_shader: StringProperty(name="Shader", default="")
    filter_semantic: StringProperty(
        name="Unresolved semantic",
        description="Hex NameHash substring, e.g. 0xA1B2C3D4",
        default="",
    )
    filter_name: StringProperty(name="Material name", default="")
    selected_key: StringProperty(name="Selected entry", default="")


def _active_report(context):
    settings = context.scene.forza_material_report
    name = settings.car_collection
    if not name:
        return None, None
    collection = bpy.data.collections.get(name)
    if collection is None:
        return None, None
    return collection, load_report_from_collection(collection)


def _filtered_entries(report, settings):
    if report is None:
        return []
    rows = report.sorted_entries()
    status = settings.filter_status
    if status == "UNRESOLVED":
        rows = [d for d in rows if is_unresolved_family(d.status)]
    elif status != "ALL":
        want = MaterialStatus(status)
        rows = [d for d in rows if d.status is want]
    cap = (settings.filter_capability or "").strip().lower()
    if cap:
        rows = [d for d in rows if (d.capability or "").lower().find(cap) >= 0]
    shader = (settings.filter_shader or "").strip().lower()
    if shader:
        rows = [d for d in rows if (d.shader_name or "").lower().find(shader) >= 0]
    name = (settings.filter_name or "").strip().lower()
    if name:
        rows = [d for d in rows if d.material_name.lower().find(name) >= 0]
    sem = (settings.filter_semantic or "").strip().lower().replace("0x", "")
    if sem:
        filtered = []
        for d in rows:
            for h in d.unresolved_semantics:
                if sem in f"{h & 0xFFFFFFFF:08x}":
                    filtered.append(d)
                    break
        rows = filtered
    return rows


class IMPORT_OT_forza_material_report_select(Operator):
    bl_idname = "import_scene.forza_material_report_select"
    bl_label = "Select Affected Objects"
    bl_options = {"REGISTER", "UNDO"}

    instance_key: StringProperty()

    def execute(self, context):
        collection, report = _active_report(context)
        if report is None:
            self.report({"WARNING"}, "No material report selected")
            return {"CANCELLED"}
        diag = report.entries.get(self.instance_key)
        if diag is None:
            self.report({"WARNING"}, "Entry not found")
            return {"CANCELLED"}
        names = set(diag.affected_object_names)
        # Also catch objects tagged with the diagnostic key.
        for obj in bpy.data.objects:
            if obj.get(PROP_MATERIAL_DIAG_KEY) == self.instance_key:
                names.add(obj.name)
        if not names:
            self.report({"WARNING"}, "No affected objects recorded")
            return {"CANCELLED"}
        bpy.ops.object.select_all(action="DESELECT")
        count = 0
        for name in sorted(names):
            obj = bpy.data.objects.get(name)
            if obj is None:
                continue
            obj.select_set(True)
            context.view_layer.objects.active = obj
            count += 1
        self.report({"INFO"}, f"Selected {count} object(s)")
        return {"FINISHED"}


class IMPORT_OT_forza_material_report_copy(Operator):
    bl_idname = "import_scene.forza_material_report_copy"
    bl_label = "Copy Diagnostic Details"
    bl_options = {"REGISTER"}

    instance_key: StringProperty()

    def execute(self, context):
        _, report = _active_report(context)
        if report is None:
            self.report({"WARNING"}, "No material report selected")
            return {"CANCELLED"}
        diag = report.entries.get(self.instance_key)
        if diag is None:
            self.report({"WARNING"}, "Entry not found")
            return {"CANCELLED"}
        from .diagnostics import diagnostic_to_dict

        text = json.dumps(diagnostic_to_dict(diag), indent=2, sort_keys=True)
        context.window_manager.clipboard = text
        self.report({"INFO"}, "Copied diagnostic JSON to clipboard")
        return {"FINISHED"}


class IMPORT_OT_forza_material_report_export(Operator):
    bl_idname = "import_scene.forza_material_report_export"
    bl_label = "Export Material Report JSON"
    bl_options = {"REGISTER"}

    filepath: StringProperty(subtype="FILE_PATH")
    filename_ext = ".json"

    def invoke(self, context, event):
        _, report = _active_report(context)
        if report is None:
            self.report({"WARNING"}, "No material report selected")
            return {"CANCELLED"}
        self.filepath = f"forza_material_report_{report.car_id}.json"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        _, report = _active_report(context)
        if report is None:
            self.report({"WARNING"}, "No material report selected")
            return {"CANCELLED"}
        path = bpy.path.abspath(self.filepath)
        with open(path, "w", encoding="utf-8") as f:
            f.write(report.to_json())
        self.report({"INFO"}, f"Wrote {path}")
        return {"FINISHED"}


class IMPORT_OT_forza_material_report_refresh(Operator):
    bl_idname = "import_scene.forza_material_report_refresh"
    bl_label = "Refresh Report From Scene"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # Re-bind object names from current scene tags into the stored report.
        collection, report = _active_report(context)
        if report is None:
            self.report({"WARNING"}, "No material report selected")
            return {"CANCELLED"}
        for obj in bpy.data.objects:
            key = obj.get(PROP_MATERIAL_DIAG_KEY)
            if not key:
                continue
            report.record_object(key, obj.name)
        from .report_store import store_report_on_collection

        store_report_on_collection(collection, report)
        self.report({"INFO"}, "Report refreshed from scene object tags")
        return {"FINISHED"}


class IMPORT_PT_forza_material_report(Panel):
    bl_label = "Forza Material Import Report"
    bl_idname = "IMPORT_PT_forza_material_report"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Forza"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.forza_material_report
        layout.prop(settings, "car_collection", text="Car")
        collection, report = _active_report(context)
        if report is None:
            layout.label(text="Import a car with materials to populate this report.")
            return

        summary = report.summary_counts()
        box = layout.box()
        box.label(text=f"Materials encountered: {summary['materials_encountered']}")
        box.label(text=f"Fully supported: {summary['fully_supported']}")
        box.label(text=f"Partially supported: {summary['partially_supported']}")
        box.label(text=f"Unresolved: {summary['unresolved']}")
        box.label(text=f"Builder errors: {summary['builder_errors']}")
        box.label(
            text=f"Objects with diagnostic materials: "
            f"{summary['objects_with_diagnostic_materials']}"
        )

        filt = layout.box()
        filt.label(text="Filters")
        filt.prop(settings, "filter_status")
        filt.prop(settings, "filter_capability")
        filt.prop(settings, "filter_shader")
        filt.prop(settings, "filter_semantic")
        filt.prop(settings, "filter_name")

        row = layout.row()
        row.operator(
            IMPORT_OT_forza_material_report_export.bl_idname, icon="EXPORT"
        )
        row.operator(
            IMPORT_OT_forza_material_report_refresh.bl_idname, icon="FILE_REFRESH"
        )

        entries = _filtered_entries(report, settings)
        layout.label(text=f"Entries: {len(entries)}")
        for diag in entries[:80]:
            entry = layout.box()
            entry.label(text=diag.material_name)
            entry.label(text=f"Shader: {diag.shader_name or '—'}")
            entry.label(text=f"Status: {diag.status.value}")
            entry.label(text=f"Capability: {diag.capability or '—'}")
            reason = diag.failure_reason or (
                diag.warnings[0] if diag.warnings else ""
            )
            if reason:
                entry.label(text=f"Reason: {reason[:96]}")
            entry.label(text=f"Affected objects: {len(diag.affected_object_names)}")
            ops = entry.row()
            op = ops.operator(
                IMPORT_OT_forza_material_report_select.bl_idname, text="Select"
            )
            op.instance_key = diag.instance_key
            op = ops.operator(
                IMPORT_OT_forza_material_report_copy.bl_idname, text="Copy"
            )
            op.instance_key = diag.instance_key
        if len(entries) > 80:
            layout.label(text=f"… {len(entries) - 80} more (narrow filters)")


classes = (
    ForzaMaterialReportSettings,
    IMPORT_OT_forza_material_report_select,
    IMPORT_OT_forza_material_report_copy,
    IMPORT_OT_forza_material_report_export,
    IMPORT_OT_forza_material_report_refresh,
    IMPORT_PT_forza_material_report,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.forza_material_report = bpy.props.PointerProperty(
        type=ForzaMaterialReportSettings
    )


def unregister():
    if hasattr(bpy.types.Scene, "forza_material_report"):
        del bpy.types.Scene.forza_material_report
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
