"""ManufacturerColors.bin / JSON: apply stock paint sets onto carpaint materials.

FH6 ships ManufacturerColors.bin inside each car zip. The CLI Toolkit also emits a JSON
mirror. Prefer parsing the JSON when present next to the dump; otherwise apply Preview_Color
/ PaintColorColorParam from the binary via the JSON already extracted by tools.

This module applies color set 0 (stock) PaintColorColorParam onto MaterialSpecs whose shader
looks like paint, when the material has no diffuse map.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass
class PaintSwatch:
    path: str
    preview_rgb: tuple[float, float, float]
    paint_rgba: tuple[float, float, float, float] | None
    masks: list[str]


def load_manufacturer_colors_json(path: str) -> list[PaintSwatch]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out: list[PaintSwatch] = []
    for color_set in data.get("Colors") or []:
        for entry in color_set.get("Data") or []:
            preview = entry.get("Preview_Color") or [0.5, 0.5, 0.5]
            paint = None
            for sp in entry.get("Shader_Parameters") or []:
                if (sp.get("Name") or "") == "PaintColorColorParam" or (
                    sp.get("Hash") or ""
                ).upper() == "C0CB2820":
                    d = sp.get("Data")
                    if isinstance(d, list) and len(d) >= 3:
                        paint = (float(d[0]), float(d[1]), float(d[2]), float(d[3] if len(d) > 3 else 1.0))
                    break
            out.append(
                PaintSwatch(
                    path=entry.get("Path") or "",
                    preview_rgb=(float(preview[0]), float(preview[1]), float(preview[2])),
                    paint_rgba=paint,
                    masks=list(entry.get("Masks") or []),
                )
            )
    return out


def find_manufacturer_colors(car_dir_or_zip_sibling: str) -> str | None:
    """Locate ManufacturerColors.json beside a dump, or next to a car folder."""
    candidates = [
        os.path.join(car_dir_or_zip_sibling, "ManufacturerColors.json"),
        os.path.join(os.path.dirname(car_dir_or_zip_sibling), "ManufacturerColors.json"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def stock_paint_rgba(swatches: list[PaintSwatch]) -> tuple[float, float, float, float] | None:
    """First swatch with PaintColorColorParam, else Preview_Color of first Body paint."""
    for s in swatches:
        if s.paint_rgba is not None:
            return s.paint_rgba
    for s in swatches:
        if "Body" in s.masks or not s.masks:
            r, g, b = s.preview_rgb
            return (r, g, b, 1.0)
    if swatches:
        r, g, b = swatches[0].preview_rgb
        return (r, g, b, 1.0)
    return None
