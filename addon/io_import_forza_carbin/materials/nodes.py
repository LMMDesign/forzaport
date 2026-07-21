"""Compatibility import for the clean Blender graph builder.

Production implementation lives in nodes_v3.py. No legacy graph remains.
"""

from .nodes_v3 import MATERIAL_GRAPH_VERSION, build_material

__all__ = ("MATERIAL_GRAPH_VERSION", "build_material")
