"""Modelbin geometry: pure vertex/index decode -> MeshData, then MeshData -> Blender mesh.

The decode (process_mesh) is a behavior-preserving port of the original core.py routine - same
vertex-format handling, morph-weight blending, transform baking and Y-up/left-handed ->
Z-up/right-handed conversion - but it returns a plain MeshData instead of touching bpy, and it
takes its filter/skeleton/transform state explicitly instead of via injected globals.

build_mesh_object turns a MeshData into a bpy mesh. The one intended behavior change vs the old
importer: UV layers are named by their TRUE source slot (TEXCOORD0..4) instead of being compacted
into TEXCOORD0,1,..., so the material pipeline's exact per-texture UV channel (from the descriptor)
always lands on the right unwrap.
"""

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field

from .parsing.binary import BinaryStream, Bundle, Tag
from .parsing.model import Model, VertexLayout, ModelBuffer, Mesh
from .parsing.skeleton import Skeleton
from .parsing.material import MaterialParseError, MaterialSystemObject


class VertexLayout_Element:
    def __init__(self):
        self.stream = None
        self.advance = 0
        self.format = -1


class ParsedMaterial:
    """An embedded MatI material: its name plus the parsed MaterialSystemObject (or None)."""
    def __init__(self, name):
        self.name = name
        self.obj = None


@dataclass
class MeshData:
    name: str
    faces: list
    verts: list                       # local-indexed (vertex_id_min..max)
    norms: list = None                # None when the format carried no usable normal
    uvs: list = field(default_factory=list)   # 5 global-indexed channel arrays
    colors: list = field(default_factory=list)  # global-indexed
    vertex_id_min: int = 0
    vertex_id_max: int = 0
    material_id: int = -1
    bone_index: int = -1


class Modelbin:
    def __init__(self):
        self.weights = None
        self.scale_x = 1
        self.transform = [[1 if i == j else 0 for i in range(4)] for j in range(4)]
        self.post_bone_transform = None  # carbin instance (FTS layer B), applied after rigid bone
        self.skeleton = None
        self.materials = []

    def set_weights(self, weights, scale_x):
        self.weights = weights
        self.scale_x = scale_x

    def set_transform(self, transform):
        self.transform = transform

    def set_post_bone_transform(self, transform):
        self.post_bone_transform = transform

    def deserialize(self, stream, requested_level_of_detail=1 << 0, resolver=None, parse_materials=False):
        bundle = Bundle()
        bundle.deserialize(stream)

        model_blobs = bundle.blobs[Tag.Modl]
        if len(model_blobs) != 1:
            print("Warning: Read unexpected number of 'Modl' entries. Expected [1].")
        model = Model()
        model.deserialize(model_blobs[0])
        self.model = model
        if model.levels_of_detail & requested_level_of_detail == 0:
            print(f"Error: Model has no requested LOD. Requested 0x{requested_level_of_detail:x}, "
                  f"Contained 0x{model.levels_of_detail:x}.")

        skeleton_blobs = bundle.blobs[Tag.Skel]
        if len(skeleton_blobs) != 1:
            print("Warning: Read unexpected number of 'Skel' entries. Expected [1].")
        self.skeleton = Skeleton()
        self.skeleton.deserialize(skeleton_blobs[0])

        vertex_layout_blobs = bundle.blobs[Tag.VLay]
        if len(vertex_layout_blobs) != model.vertex_layouts_length:
            print(f"Warning: Read unexpected number of 'VLay' entries. Read [{len(vertex_layout_blobs)}]. "
                  f"Expected [{model.vertex_layouts_length}].")
        self.vertex_layouts = [VertexLayout() for _ in range(len(vertex_layout_blobs))]
        for vl, blob in zip(self.vertex_layouts, vertex_layout_blobs):
            vl.deserialize(blob)

        index_buffer_blobs = bundle.blobs[Tag.IndB]
        if len(index_buffer_blobs) != 1:
            print("Warning: Read unexpected number of 'IndB' entries. Expected [1].")
        self.index_buffer = ModelBuffer()
        if index_buffer_blobs:
            self.index_buffer.deserialize(index_buffer_blobs[0])

        self.vertex_buffers = defaultdict(ModelBuffer)
        for blob in bundle.blobs[Tag.VerB]:
            self.vertex_buffers[blob.metadata[Tag.Id].read_s32()].deserialize(blob)

        self.morph_data_buffers = defaultdict(ModelBuffer)
        for blob in bundle.blobs[Tag.MBuf]:
            self.morph_data_buffers[blob.metadata[Tag.Id].read_s32()].deserialize(blob)

        mesh_blobs = bundle.blobs[Tag.Mesh]
        if len(mesh_blobs) != model.meshes_length:
            print(f"Warning: Read unexpected number of 'Mesh' entries. Read [{len(mesh_blobs)}]. "
                  f"Expected [{model.meshes_length}].")
        self.meshes = [Mesh() for _ in range(len(mesh_blobs))]
        for mesh, blob in zip(self.meshes, mesh_blobs):
            mesh.deserialize(blob)

        material_blobs = bundle.blobs[Tag.MatI]
        if len(material_blobs) != model.materials_length:
            print(f"Warning: Read unexpected number of 'MatI' entries. Read [{len(material_blobs)}]. "
                  f"Expected [{model.materials_length}].")
        self.materials = [ParsedMaterial("") for _ in range(len(material_blobs))]
        for blob in material_blobs:
            idx = blob.metadata[Tag.Id].read_s32()
            pm = self.materials[idx]
            pm.name = blob.metadata[Tag.Name].read_string()
            if parse_materials and resolver is not None:
                try:
                    mso = MaterialSystemObject()
                    mso.deserialize(blob.stream, resolver)
                    pm.obj = mso
                except MaterialParseError as e:
                    print(f"Material parse error '{pm.name}': {e}")
                    fails = getattr(self, "_material_parse_failures", None)
                    if fails is None:
                        self._material_parse_failures = []
                        fails = self._material_parse_failures
                    fails.append((pm.name, str(e)))
                except Exception as e:
                    print(f"Material parse error '{pm.name}': {e!r}")
                    fails = getattr(self, "_material_parse_failures", None)
                    if fails is None:
                        self._material_parse_failures = []
                        fails = self._material_parse_failures
                    fails.append((pm.name, str(e)))

    def process_mesh(self, mesh):
        max_vertex_buffer_length = next(iter(self.vertex_buffers.values())).length
        draw_indices = [None] * self.index_buffer.length
        verts = [(0, 0, 0)] * max_vertex_buffer_length
        norms = [(0, 0, 0)] * max_vertex_buffer_length
        uvs = [[(0, 0)] * max_vertex_buffer_length for _ in range(5)]
        colors = [(1, 1, 1, 1)] * max_vertex_buffer_length

        vertex_id_min = 0xFFFFFFFF
        vertex_id_max = 0
        stride = self.index_buffer.stride
        stream = BinaryStream(self.index_buffer.stream[
            mesh.start_index_location * stride:(mesh.start_index_location + mesh.index_count) * stride])
        for i in range(mesh.index_count):
            vertex_id = stream.read_u32() if stride == 4 else stream.read_u16()
            vertex_id_max = max(vertex_id_max, vertex_id)
            vertex_id_min = min(vertex_id_min, vertex_id)
            draw_indices[i] = vertex_id

        faces = []
        for i in range(mesh.index_count // 3):
            j = i * 3
            faces.append((draw_indices[j] - vertex_id_min,
                          draw_indices[j + 2] - vertex_id_min,
                          draw_indices[j + 1] - vertex_id_min))  # LH -> RH winding

        vertex_buffer_offsets = [0 for _ in range(mesh.vertex_buffer_indices_length)]
        elements = defaultdict(VertexLayout_Element)
        for semantic_name, desc in self.vertex_layouts[mesh.vertex_layout_id].elements.items():
            vbi = mesh.vertex_buffer_indices[desc.input_slot]
            vb = self.vertex_buffers[vbi.id]
            element = elements[semantic_name]
            base = vbi.offset + vertex_buffer_offsets[desc.input_slot]
            element.stream = BinaryStream(vb.stream[
                base + (vertex_id_min + mesh.base_vertex_location) * vb.stride:
                base + (vertex_id_max + mesh.base_vertex_location + 1) * vb.stride])
            element.format = desc.format
            element.advance = vb.stride
            match desc.format:
                case 6:
                    vertex_buffer_offsets[desc.input_slot] += 12
                case 10 | 13:
                    vertex_buffer_offsets[desc.input_slot] += 8
                case 24 | 28 | 35 | 37:
                    vertex_buffer_offsets[desc.input_slot] += 4
                case _:
                    print(f"Error: Unexpected element format: {desc.format}.")

        position0 = elements["POSITION0"]
        if position0.format == 13:
            position0.advance -= 8
        elif position0.format == 6:
            position0.advance -= 12
        elif position0.format != -1:
            print("Error: Unexpected position format.")

        normal0 = elements["NORMAL0"]
        if normal0.format == 37:
            normal0.advance -= 4
        elif normal0.format == 10:
            normal0.advance -= 6
        elif normal0.format != -1:
            print("Error: Unexpected normal format.")

        color0 = elements["COLOR0"]
        if color0.format == 28:
            color0.advance -= 4
        elif color0.format != -1:
            print("Error: Unexpected color format.")

        texcoords = [None] * 5
        for i in range(5):
            texcoords[i] = elements["TEXCOORD" + str(i)]
            if texcoords[i].format == 35:
                texcoords[i].advance -= 4
            elif texcoords[i].format != -1:
                print("Error: Unexpected texcoord format.")

        morph_data = VertexLayout_Element()
        if mesh.morph_weights_count > 0 and self.weights:
            mdb = self.morph_data_buffers[mesh.morph_data_buffer_id]
            morph_data.stream = BinaryStream(mdb.stream[
                (vertex_id_min + mesh.base_vertex_location) * mdb.stride:
                (vertex_id_max + mesh.base_vertex_location + 1) * mdb.stride])
            morph_data.format = mdb.format
            morph_data.advance = mdb.stride
            if morph_data.format == 10:
                morph_data.advance -= 4
            else:
                print("Error: Unexpected morph data format.")

        n = [1, 0, 0]
        color_warning_printed = False
        for vertex_id in range(vertex_id_min, vertex_id_max + 1):
            for texcoord, uv, uv_transform in zip(texcoords, uvs, mesh.uv_transforms):
                if texcoord.format == 35:
                    t = [texcoord.stream.read_un16(), texcoord.stream.read_un16()]
                    t[0] = t[0] * uv_transform[0][1] + uv_transform[0][0]
                    t[1] = t[1] * uv_transform[1][1] + uv_transform[1][0]
                    uv[vertex_id] = (t[0], 1 - t[1])
                    texcoord.stream.seek(texcoord.advance, os.SEEK_CUR)

            if color0.format != -1:
                colors[vertex_id] = (color0.stream.read_un8(), color0.stream.read_un8(),
                                     color0.stream.read_un8(), color0.stream.read_un8())
                c = colors[vertex_id]
                if not color_warning_printed and c[2] != 0 and (c[0] != 1 or c[1] != 1 or c[2] != 1 or c[3] != 1):
                    color_warning_printed = True
                color0.stream.seek(color0.advance, os.SEEK_CUR)

            if position0.format == 13:
                v = [position0.stream.read_sn16() * mesh.scale[0] + mesh.translate[0],
                     position0.stream.read_sn16() * mesh.scale[1] + mesh.translate[1],
                     position0.stream.read_sn16() * mesh.scale[2] + mesh.translate[2]]
                v_w = position0.stream.read_sn16()
            else:
                v = [position0.stream.read_f32(), position0.stream.read_f32(), position0.stream.read_f32()]
            position0.stream.seek(position0.advance, os.SEEK_CUR)

            if normal0.format == 37:
                n = [v_w, normal0.stream.read_sn16(), normal0.stream.read_sn16()]
                normal0.stream.seek(normal0.advance, os.SEEK_CUR)
            elif normal0.format == 10:
                n = [normal0.stream.read_f16(), normal0.stream.read_f16(), normal0.stream.read_f16()]
                normal0.stream.seek(normal0.advance, os.SEEK_CUR)

            if morph_data.format == 10:
                for _ in range(mesh.morph_weights_count):
                    m = (morph_data.stream.read_f16(), morph_data.stream.read_f16(), morph_data.stream.read_f16())
                    weight = self.weights[int(morph_data.stream.read_f16())]
                    v[0] += m[0] * weight
                    v[1] += m[1] * weight
                    v[2] += m[2] * weight
                for _ in range(mesh.morph_weights_count):
                    m = (morph_data.stream.read_f16(), morph_data.stream.read_f16(), morph_data.stream.read_f16())
                    weight = self.weights[int(morph_data.stream.read_f16())]
                    n[0] += m[0] * weight
                    n[1] += m[1] * weight
                    n[2] += m[2] * weight
                n_length = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2])
                n[0] /= n_length
                n[1] /= n_length
                n[2] /= n_length
                v[0] *= self.scale_x
                n[0] /= self.scale_x

            v3 = [0, 0, 0]
            n3 = [0, 0, 0]
            for j in range(3):
                for k in range(4):
                    if k == 3:
                        v3[j] += self.transform[k][j]
                    else:
                        v3[j] += v[k] * self.transform[k][j]
                        n3[j] += n[k] * self.transform[k][j]
            v2 = [0, 0, 0]
            n2 = [0, 0, 0]
            bone_t = self.skeleton.bones[mesh.bone_index].transform
            for j in range(3):
                for k in range(4):
                    if k == 3:
                        v2[j] += bone_t[k][j]
                    else:
                        v2[j] += v3[k] * bone_t[k][j]
                        n2[j] += n3[k] * bone_t[k][j]

            post = self.post_bone_transform
            if post is not None:
                v4 = [0, 0, 0]
                n4 = [0, 0, 0]
                for j in range(3):
                    for k in range(4):
                        if k == 3:
                            v4[j] += post[k][j]
                        else:
                            v4[j] += v2[k] * post[k][j]
                            n4[j] += n2[k] * post[k][j]
                v2, n2 = v4, n4

            n_length = math.sqrt(n2[0] * n2[0] + n2[1] * n2[1] + n2[2] * n2[2])
            n2[0] /= n_length
            n2[1] /= n_length
            n2[2] /= n_length

            verts[vertex_id] = (-v2[0], -v2[2], v2[1])   # Y-up LH -> Z-up RH
            norms[vertex_id] = (-n2[0], -n2[2], n2[1])

        verts2 = verts[vertex_id_min:vertex_id_max + 1]
        norms2 = norms[vertex_id_min:vertex_id_max + 1] if normal0.format in (10, 37) else None

        name = mesh.name
        if mesh.material_id < 0:
            print(f"Warning: Mesh {mesh.name} material id {mesh.material_id} is not valid.")
        elif 0 <= mesh.material_id < len(self.materials):
            name += " " + self.materials[mesh.material_id].name

        return MeshData(name=name, faces=faces, verts=verts2, norms=norms2,
                        uvs=uvs, colors=colors, vertex_id_min=vertex_id_min,
                        vertex_id_max=vertex_id_max, material_id=mesh.material_id,
                        bone_index=mesh.bone_index)


def _quadrangulate(faces):
    """Original cheap quad reconstruction: pair triangles that share a reversed edge."""
    polys = []
    used = [False] * len(faces)
    for i, f0 in enumerate(faces):
        if used[i]:
            continue
        r = next(((j, f1) for j, f1 in enumerate(faces[i + 1:], i + 1)
                  if f0[0] == f1[2] and f0[2] == f1[0]), None)
        if r is None:
            polys.append([f0[0], f0[1], f0[2]])
        else:
            j, f1 = r
            polys.append([f0[0], f0[1], f0[2], f1[1]])
            used[j] = True
    return polys


def build_mesh_object(md, quadrangulate=False):
    """MeshData -> (bpy mesh, object). UV layers are named by true source slot (TEXCOORD{i})."""
    import bpy
    import bmesh

    polys = _quadrangulate(md.faces) if quadrangulate else md.faces

    mesh = bpy.data.meshes.new(md.name)
    mesh.from_pydata(md.verts, [], polys, False)
    mesh.validate()
    if md.norms is not None:
        mesh.normals_split_custom_set_from_vertices(md.norms)
    obj = bpy.data.objects.new(md.name, mesh)

    bm = bmesh.new()
    bm.from_mesh(mesh)

    # populated source channels: a channel whose per-vertex value isn't constant
    populated = []
    for i in range(5):
        channel = md.uvs[i]
        first = None
        for vert in bm.verts:
            value = channel[vert.index + md.vertex_id_min]
            if first is None:
                first = value
            elif value != first:
                populated.append(i)
                break
    if not populated:
        populated = [0]
    uv_layers = {i: bm.loops.layers.uv.new("TEXCOORD" + str(i)) for i in populated}
    for face in bm.faces:
        for loop in face.loops:
            gid = loop.vert.index + md.vertex_id_min
            for i in populated:
                loop[uv_layers[i]].uv = md.uvs[i][gid]

    color_layer = bm.verts.layers.color.new("COLOR0")
    for vert in bm.verts:
        vert[color_layer] = md.colors[vert.index + md.vertex_id_min]

    bm.to_mesh(mesh)
    bm.free()
    return mesh, obj
