"""Data-driven material pipeline.

material_table.py loads/looks up data/material_table.json (per-shader, per-paramHash UV +
channel packing + normal encoding + sampler address + role token + cbuffer defaults, mined
from the FH5 shader library). builder.py turns a parsed material into a MaterialSpec; nodes.py
turns a MaterialSpec into a Blender node tree. No fail-first fallback fork.
"""
