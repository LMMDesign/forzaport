"""Pure parsing layer for ForzaTech binary formats.

Modules here MUST NOT import bpy/mathutils-dependent code; they read bytes and return plain
data objects (dataclasses / dicts / lists). The Blender build layer consumes those. This keeps
parsing testable headlessly and removes the exec()-with-injected-globals coupling.
"""
