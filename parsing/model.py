"""Model container blobs: Model header, VertexLayout, ModelBuffer, Mesh.

Pure module: no bpy. Behavior-preserving port of the CommonModel parsers from core.py. These
read the structural description of a modelbin; the actual vertex/index decode + Blender build
lives in the geometry layer.
"""

import os
from collections import defaultdict


class Model:
    def __init__(self):
        self.meshes_length = 0
        self.buffers_length = 0
        self.vertex_layouts_length = 0
        self.materials_length = 0
        self.levels_of_detail = 0
        self.decompress_flags = 0

    def deserialize(self, blob):
        if not blob.version.is_at_most(1, 4):
            print(f"Warning: Unsupported 'Modl' blob version. Found: {blob.version}. Max supported: 1.4")
        if not blob.version.is_at_least(1, 0):
            print(f"Warning: Unsupported 'Modl' blob version. Found: {blob.version}. Min supported: 1.0")
        stream = blob.stream
        self.meshes_length = stream.read_s16()
        self.buffers_length = stream.read_s16()
        self.vertex_layouts_length = stream.read_s16()
        self.materials_length = stream.read_s16()
        stream.seek(4, os.SEEK_CUR)
        self.levels_of_detail = stream.read_u16()
        if blob.version.is_at_least(1, 2):
            self.decompress_flags = stream.read_u8()


class D3D12_INPUT_ELEMENT_DESC:
    def __init__(self):
        self.semantic_name = ""
        self.semantic_index = 0
        self.input_slot = 0
        self.format = 0


class VertexLayout:
    def __init__(self):
        self.element_names_length = 0
        self.element_names = None
        self.elements_length = 0
        self.elements = defaultdict(D3D12_INPUT_ELEMENT_DESC)

    def deserialize(self, blob):
        if not blob.version.is_at_most(1, 1):
            print(f"Warning: Unsupported '{blob.get_tag()}' blob version. Found: {blob.version}. Max supported: 1.1")
        if not blob.version.is_at_least(1, 0):
            print(f"Warning: Unsupported '{blob.get_tag()}' blob version. Found: {blob.version}. Min supported: 1.0")
        self.element_names_length = blob.stream.read_u16()
        self.element_names = [None] * self.element_names_length
        for i in range(self.element_names_length):
            self.element_names[i] = blob.stream.read_string()
        self.elements_length = blob.stream.read_u16()
        for i in range(self.elements_length):
            self.semantic_name = self.element_names[blob.stream.read_u16()]
            self.semantic_index = blob.stream.read_u16()
            element = self.elements[self.semantic_name + str(self.semantic_index)]  # TEXCOORD0, TEXCOORD1, ...
            element.input_slot = blob.stream.read_u16()
            blob.stream.seek(2, os.SEEK_CUR)
            element.format = blob.stream.read_u32()
            blob.stream.seek(4 * 2, os.SEEK_CUR)


class ModelBuffer:
    def __init__(self):
        self.length = 0
        self.size = 0
        self.stride = 0
        self.format = 0

    def deserialize(self, blob):
        if not blob.version.is_at_most(1, 1):
            print(f"Warning: Unsupported '{blob.get_tag()}' blob version. Found: {blob.version}. Max supported: 1.1")
        if not blob.version.is_at_least(1, 0):
            print(f"Warning: Unsupported '{blob.get_tag()}' blob version. Found: {blob.version}. Min supported: 1.0")
        self.length = blob.stream.read_u32()
        self.size = blob.stream.read_u32()
        self.stride = blob.stream.read_u16()
        blob.stream.seek(1 + 1, os.SEEK_CUR)
        if blob.version.is_at_least(1, 0):
            self.format = blob.stream.read_u32()
            self.stream = blob.stream[0x10:0x10 + self.size]
        else:
            self.stream = blob.stream[0xC:0xC + self.size]


class Mesh_VertexBufferIndex:
    def __init__(self):
        self.id = 0
        self.stride = 0
        self.offset = 0


class Mesh:
    def __init__(self):
        self.material_id = 0
        self.bone_index = 0
        self.levels_of_detail = 0
        self.render_pass = 0
        self.skinning_elements_count = 0
        self.morph_weights_count = 0
        self.index_buffer_id = 0
        self.start_index_location = 0
        self.base_vertex_location = 0
        self.index_count = 0
        self.uv_transforms = [None] * 5

    def deserialize(self, blob):
        from .binary import Tag
        if not blob.version.is_at_most(1, 12):
            print(f"Warning: Unsupported 'Mesh' blob version. Found: {blob.version}. Max supported: 1.12")
        if not blob.version.is_at_least(1, 0):
            print(f"Warning: Unsupported 'Mesh' blob version. Found: {blob.version}. Min supported: 1.0")
        self.name = blob.metadata[Tag.Name].read_string()
        if blob.version.is_at_least(1, 13):
            blob.stream.seek(4, os.SEEK_CUR)
        self.material_id = blob.stream.read_s16()
        if blob.version.is_at_least(1, 9):
            self.material_id = blob.stream.read_s16()
            blob.stream.seek(2 * 2, os.SEEK_CUR)
        self.bone_index = blob.stream.read_s16()
        self.levels_of_detail = blob.stream.read_u16()
        blob.stream.seek(2, os.SEEK_CUR)
        self.render_pass = blob.stream.read_u16()
        blob.stream.seek(1, os.SEEK_CUR)
        if blob.version.is_at_least(1, 2):
            self.skinning_elements_count = blob.stream.read_u8()
            if blob.version.is_at_least(1, 10):
                self.morph_weights_count = blob.stream.read_u32()
            else:
                self.morph_weights_count = blob.stream.read_u8()
        if blob.version.is_at_least(1, 3):
            blob.stream.seek(1, os.SEEK_CUR)
        blob.stream.seek(1 + 2, os.SEEK_CUR)
        self.index_buffer_id = blob.stream.read_s32()
        blob.stream.seek(4, os.SEEK_CUR)
        self.start_index_location = blob.stream.read_s32()
        self.base_vertex_location = blob.stream.read_s32()
        self.index_count = blob.stream.read_u32()
        blob.stream.seek(4, os.SEEK_CUR)
        if blob.version.is_at_least(1, 6):
            blob.stream.seek(4 + 4, os.SEEK_CUR)
            if blob.version.is_at_least(1, 11):
                length = blob.stream.read_u32()
                blob.stream.seek(4 * length, os.SEEK_CUR)
        self.vertex_layout_id = blob.stream.read_u32()
        self.vertex_buffer_indices_length = blob.stream.read_u32()
        self.vertex_buffer_indices = [None] * self.vertex_buffer_indices_length
        for i in range(self.vertex_buffer_indices_length):
            vertex_buffer_index = Mesh_VertexBufferIndex()
            vertex_buffer_index.id = blob.stream.read_s32()
            input_slot = blob.stream.read_s32()
            vertex_buffer_index.stride = blob.stream.read_s32()
            vertex_buffer_index.offset = blob.stream.read_s32()
            if blob.version.is_at_least(1, 12):
                blob.stream.seek(4, os.SEEK_CUR)
            self.vertex_buffer_indices[input_slot] = vertex_buffer_index
        if blob.version.is_at_least(1, 4):
            self.morph_data_buffer_id = blob.stream.read_s32()
            self.skinning_data_buffer_id = blob.stream.read_s32()
        self.constant_buffer_indices_length = blob.stream.read_u32()
        if self.constant_buffer_indices_length != 0:
            print("Warning: Mesh.constant_buffer_indices_length != 0. Please report it in GitHub issue.")
        if blob.version.is_at_least(1, 1):
            blob.stream.seek(4, os.SEEK_CUR)
        if blob.version.is_at_least(1, 5):
            for i in range(5):
                self.uv_transforms[i] = ((blob.stream.read_f32(), blob.stream.read_f32()),
                                         (blob.stream.read_f32(), blob.stream.read_f32()))
        if blob.version.is_at_least(1, 8):
            self.scale = [blob.stream.read_f32(), blob.stream.read_f32(),
                          blob.stream.read_f32(), blob.stream.read_f32()]
            self.translate = [blob.stream.read_f32(), blob.stream.read_f32(),
                              blob.stream.read_f32(), blob.stream.read_f32()]
