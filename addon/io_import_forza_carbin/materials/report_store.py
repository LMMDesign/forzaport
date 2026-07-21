"""Persist and recover per-car material import reports in Blender data."""

from __future__ import annotations

import json

from ..contract import PROP_MATERIAL_REPORT_TEXT
from .diagnostics import ImportMaterialReport, report_from_json


def text_block_name(car_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in car_id)[:80]
    return f"forza_material_report__{safe}"


def store_report_on_collection(collection, report: ImportMaterialReport) -> str:
    """Write JSON into a Text datablock and link it from the car root collection."""
    import bpy

    name = text_block_name(report.car_id)
    text = bpy.data.texts.get(name)
    if text is None:
        text = bpy.data.texts.new(name)
    text.clear()
    text.write(report.to_json())
    collection[PROP_MATERIAL_REPORT_TEXT] = name
    collection["forza_car_id"] = report.car_id
    collection["forza_material_report_summary"] = json.dumps(
        report.summary_counts(), sort_keys=True
    )
    return name


def load_report_from_collection(collection) -> ImportMaterialReport | None:
    import bpy

    name = collection.get(PROP_MATERIAL_REPORT_TEXT)
    if not name:
        return None
    text = bpy.data.texts.get(name)
    if text is None:
        return None
    body = text.as_string()
    if not body.strip():
        return None
    return report_from_json(json.loads(body))


def find_car_collections_with_reports():
    """Yield (collection, report) for every car root that stores a report."""
    import bpy

    for collection in bpy.data.collections:
        if PROP_MATERIAL_REPORT_TEXT not in collection:
            continue
        report = load_report_from_collection(collection)
        if report is not None:
            yield collection, report
