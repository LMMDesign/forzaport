"""Skeleton ('Skel') blob parser: bones with parent-accumulated world transforms.

Pure module: no bpy. Bone.transform ends up as a plain 4x4 row list (world space). The matrix
accumulation is kept as the original explicit loops to stay dependency-free and bit-identical;
the Blender build layer wraps these into mathutils.Matrix.
"""


class Bone:
    def __init__(self):
        self.name = ""
        self.transform = [[1 if i == j else 0 for i in range(4)] for j in range(4)]

    def deserialize(self, blob):
        self.name_length = blob.stream.read_u32()
        self.name = blob.stream.read(self.name_length).decode("utf-8")
        self.parent_index = blob.stream.read_s16()
        self.child_index = blob.stream.read_s16()
        self.next_index = blob.stream.read_s16()
        for j in range(4):
            for i in range(4):
                self.transform[j][i] = blob.stream.read_f32()


class Skeleton:
    def __init__(self):
        self.bones_length = 0
        self.bones = []

    def deserialize(self, blob):
        if not blob.version.is_at_most(1, 0):
            print(f"Warning: Unsupported 'Skel' blob version. Found: {blob.version}. Max supported: 1.0")
        self.bones_length = blob.stream.read_u16()
        self.bones = [Bone() for _ in range(self.bones_length)]
        transform = [[0 for _ in range(4)] for _ in range(4)]
        for bone in self.bones:
            bone.deserialize(blob)
            if bone.parent_index != -1:
                tr = self.bones[bone.parent_index].transform
                for j in range(4):
                    for i in range(4):
                        transform[j][i] = 0
                for i in range(4):
                    for j in range(4):
                        for k in range(4):
                            transform[i][j] += bone.transform[i][k] * tr[k][j]
                bone.transform = [row[:] for row in transform]
