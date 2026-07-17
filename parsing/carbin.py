"""CarScene (.carbin) parser: scene -> parts -> CarRenderModel11 references into modelbins.

Behavior-preserving port of the original core.py carbin classes. The one structural change:
the original threaded game `series` (Motorsport=1 / Horizon=2) and the CarScene version through
module globals that the model parser mutated mid-parse. Here that state lives in an explicit
ParseContext that is passed down, so the parser is pure and reentrant.
"""

import os

from .binary import BinaryStream


class ParseContext:
    """Mutable parse state shared down the carbin tree (replaces the old globals)."""
    def __init__(self):
        self.series = 0          # 0 unknown, 1 Forza Motorsport, 2 Forza Horizon
        self.series_is_weak = False
        self.scene_version = 0


class AOMapInfo:  # CarScene::ICarRenderModel::AOMapInfo
    def __init__(self):
        self.version = 0

    def deserialize(self, stream):
        self.version = stream.read_u16()
        if self.version > 3:
            print(f"Warning: Unsupported AOMapInfo version. Found: {self.version}. Max supported: 3")
        if self.version < 1:
            print(f"Warning: Unsupported AOMapInfo version. Found: {self.version}. Min supported: 1")
        stream.read_string()
        stream.seek(4 * 2, os.SEEK_CUR)
        if self.version >= 2:
            stream.seek(16, os.SEEK_CUR)
        else:
            stream.seek(2 + 1, os.SEEK_CUR)
        stream.seek(1, os.SEEK_CUR)
        if self.version >= 3:
            stream.seek(1 * 2, os.SEEK_CUR)


class CarRenderModel11:
    def __init__(self):
        self.version = 0
        self.modelbin = None
        self.type = None

    def deserialize(self, stream, ctx):
        self.version = stream.read_u16()
        if ctx.series == 0 or ctx.series_is_weak:
            known = False
            if self.version == 18:
                ctx.series = 2
                if ctx.scene_version == 6:
                    known = True
            elif self.version in [15, 16]:
                ctx.series = 2
                if ctx.scene_version == 5:
                    known = True
            elif self.version == 21 and ctx.scene_version == 7:
                ctx.series = 2
                known = True
            else:
                ctx.series = 1
                if self.version == 21:
                    if ctx.scene_version in [10, 11]:
                        known = True
                elif self.version in [14, 17] and ctx.scene_version == 5:
                    known = True
            if not known:
                print(f"Warning: Unknown CarScene (v{ctx.scene_version}) and CarRenderModel11 "
                      f"(v{self.version}) version combination.")
            print("Assumed game series: Forza Horizon" if ctx.series == 2
                  else "Assumed game series: Forza Motorsport")
            ctx.series_is_weak = False
        max_version = 21
        if self.version > max_version:
            print(f"Warning: Unsupported CarRenderModel11 version. Found: {self.version}. Max supported: {max_version}")
        if self.version < 1:
            print(f"Warning: Unsupported CarRenderModel11 version. Found: {self.version}. Min supported: 1")
        self.path = stream.read_string()
        self.transform = [[stream.read_f32() for _ in range(4)] for _ in range(4)]
        if self.version > 5:
            self.levels_of_detail = stream.read_u16()
        else:
            self.levels_of_detail = stream.read_u32()
        self.bone_name = stream.read_string()
        self.bone_index = stream.read_u16()
        stream.seek(1, os.SEEK_CUR)
        self.draw_groups = stream.read_s32()
        if self.version < 9:
            stream.read_string()
        if self.version >= 2:
            material_overrides_length = stream.read_u32()
            for _ in range(material_overrides_length):
                stream.read_string()
                stream.seek(stream.read_u32(), os.SEEK_CUR)  # Bundle
        if self.version >= 3:
            material_indices_length = stream.read_u32()
            for _ in range(material_indices_length):
                stream.read_string()
                if ctx.series == 1 and self.version >= 21 or ctx.series == 2 and self.version >= 20:
                    stream.seek(8, os.SEEK_CUR)
                else:
                    stream.seek(4, os.SEEK_CUR)
        if self.version >= 6:
            if stream.read_u8():
                stream.seek(4 + 4, os.SEEK_CUR)
        if self.version >= 8:
            stream.seek(4, os.SEEK_CUR)
        if self.version >= 9:
            ao_maps_info_length = stream.read_u32()
            for _ in range(ao_maps_info_length):
                AOMapInfo().deserialize(stream)
        if self.version >= 10:
            stream.seek(1, os.SEEK_CUR)
        if self.version >= 11:
            stream.seek(1 * 2 + 4 * 4, os.SEEK_CUR)
        if self.version >= 12:
            self.type = self.fix_type_case(stream.read_string())
        if self.version >= 13:
            stream.seek(16, os.SEEK_CUR)
        if self.version >= 14:
            stream.seek(16 + 4, os.SEEK_CUR)
        if ctx.series == 2 and self.version >= 15:
            stream.seek(4, os.SEEK_CUR)
        if ctx.series == 1 and self.version >= 15 or ctx.series == 2 and self.version >= 16:
            stream.seek(16 * stream.read_u32(), os.SEEK_CUR)
        if ctx.series == 1:
            if self.version >= 16:
                stream.seek(4, os.SEEK_CUR)
            if self.version >= 17:
                stream.seek(1, os.SEEK_CUR)
            if self.version >= 18:
                stream.read_string()
            if self.version >= 19:
                stream.read_string()
            if self.version >= 20:
                stream.seek(1 + 4 * 2 + 1 * 2, os.SEEK_CUR)
        elif ctx.series == 2:
            if self.version >= 17:
                stream.seek(1, os.SEEK_CUR)
            if self.version >= 18:
                stream.seek(4, os.SEEK_CUR)
            if self.version >= 19:
                stream.seek(4, os.SEEK_CUR)
                stream.read_string()

    def fix_type_case(self, name):
        table = {
            "bumperr": "BumperR", "centerconsole": "CenterConsole", "centerstack": "CenterStack",
            "chassis": "Chassis", "dash": "Dash", "details": "Details", "doors": "Doors",
            "floor": "Floor", "interiorlod": "InteriorLOD", "interiorwindows": "InteriorWindows",
            "pillar": "Pillar", "platform": "Platform", "primarylights": "PrimaryLights",
            "secondarylights": "SecondaryLights", "windows": "Windows",
        }
        if name in ("plate", "plates"):
            return name
        if name in table:
            return table[name]
        if not any(c.isupper() for c in name):
            print(f'Warning: Unknown lowercase CarRenderModel11 type name "{name}".')
        return name


class IPart:
    @staticmethod
    def type_v1_to_latest(type):
        if type >= 42:
            type += 1
        return type

    _TYPE_NAMES = {
        0: "Engine", 1: "Drivetrain", 2: "CarBody", 3: "Motor", 4: "Brakes", 5: "SpringDamper",
        6: "AntiSwayFront", 7: "AntiSwayRear", 8: "TireCompound", 9: "RearWing", 10: "RimSizeFront",
        11: "RimSizeRear", 12: "Camshaft", 13: "Valves", 14: "Displacement", 15: "PistonsCompression",
        16: "FuelSystem", 17: "Ignition", 18: "Exhaust", 19: "Intake", 20: "Flywheel", 21: "Manifold",
        22: "RestrictorPlate", 23: "OilCooling", 24: "SingleTurbo", 25: "TwinTurbo", 26: "QuadTurbo",
        27: "SuperchargerCSC", 28: "SuperchargerDSC", 29: "Intercooler", 30: "Clutch",
        31: "Transmission", 32: "Driveline", 33: "Differential", 34: "FrontBumper", 35: "RearBumper",
        36: "Hood", 37: "SideSkirts", 38: "TireWidthFront", 39: "TireWidthRear", 40: "WeightReduction",
        41: "ChassisStiffness", 42: "Ballast", 43: "MotorParts", 44: "Wheels", 45: "Aspiration",
    }

    def get_type_name(self):
        return IPart._TYPE_NAMES.get(self.type)


class Part(IPart):
    def __init__(self):
        self.version = 0

    def deserialize(self, stream, ctx):
        self.version = stream.read_u16()
        max_version = 2 if ctx.series == 2 else 3
        if self.version > max_version:
            print(f"Warning: Unsupported CarPart version. Found: {self.version}. Max supported: {max_version}")
        if self.version < 1:
            print(f"Warning: Unsupported CarPart version. Found: {self.version}. Min supported: 1")
        self.type = stream.read_u32()
        if ctx.series != 1 or self.version < 3:
            self.type = self.type_v1_to_latest(self.type)
        self.models = stream.read_list(CarRenderModel11)
        for model in self.models:
            model.deserialize(stream, ctx)
        if self.version >= 2:
            stream.seek(32, os.SEEK_CUR)


class Upgrade:
    def __init__(self):
        self.version = 0

    def deserialize(self, stream, ctx):
        self.version = stream.read_u16()
        max_version = 3 if ctx.series == 2 else 4
        if self.version > max_version:
            print(f"Warning: Unsupported Upgrade version. Found: {self.version}. Max supported: {max_version}")
        if self.version < 1:
            print(f"Warning: Unsupported Upgrade version. Found: {self.version}. Min supported: 1")
        self.level = stream.read_u8()
        self.is_stock = stream.read_u8()
        self.id = stream.read_s32()
        self.car_body_id = stream.read_s32()
        self.parent_is_stock = stream.read_u8()
        if self.version < 3:
            print("Error: Upgrade less than v3 is not supported.")
        if self.version >= 2:
            stream.seek(32, os.SEEK_CUR)


class SharedCarModel:
    def __init__(self):
        self.upgrade_ids = None

    def deserialize(self, stream, ctx):
        self.upgrade_ids = stream.read_list(int)
        for i in range(len(self.upgrade_ids)):
            self.upgrade_ids[i] = stream.read_u32()
        self.model = CarRenderModel11()
        self.model.deserialize(stream, ctx)


class UpgradablePart(IPart):
    def __init__(self):
        self.version = 0

    def deserialize(self, stream, ctx):
        self.version = stream.read_u16()
        max_version = 3 if ctx.series == 2 else 4
        if self.version > max_version:
            print(f"Warning: Unsupported UpgradablePart version. Found: {self.version}. Max supported: {max_version}")
        if self.version < 1:
            print(f"Warning: Unsupported UpgradablePart version. Found: {self.version}. Min supported: 1")
        self.type = stream.read_u32()
        if ctx.series != 1 or self.version < 4:
            self.type = self.type_v1_to_latest(self.type)
        upgrades_length = stream.read_u32()
        self.upgrades = {}
        for _ in range(upgrades_length):
            upgrade = Upgrade()
            upgrade.deserialize(stream, ctx)
            self.upgrades[upgrade.id] = upgrade
        self.models = []
        if self.version >= 3:
            self.models = stream.read_list(SharedCarModel)
            for model in self.models:
                model.deserialize(stream, ctx)


class CarScene:
    def __init__(self):
        self.version = 0
        self.parts = []
        self.part_wheels = None
        self.part_brakes = None
        self.part_tires = None
        self.upgradable_parts = []
        # Always present so wheel assembly can run without tire synthesis.
        self.control_arm_models = [None] * 6

    def deserialize(self, stream, ctx):
        self.version = stream.read_u16()
        if ctx.series == 0 and self.version in [10, 11]:
            ctx.series = 1
            ctx.series_is_weak = True
        ctx.scene_version = self.version
        max_version = 7 if ctx.series == 2 else 11
        if self.version > max_version:
            print(f"Warning: Unsupported CarScene version. Found: {self.version}. Max supported: {max_version}")
        if self.version < 1:
            print(f"Warning: Unsupported CarScene version. Found: {self.version}. Min supported: 1")
        if self.version >= 3:
            stream.seek(16, os.SEEK_CUR)
        if self.version >= 5:
            stream.seek(1, os.SEEK_CUR)
        self.ordinal = stream.read_u32()
        self.media_name = stream.read_string()
        self.skeleton_path = stream.read_string()
        if self.version >= 2:
            stream.seek(2, os.SEEK_CUR)
        if self.version < 5:
            print("Warning: CarScene v4 or below. Please, create an issue and upload this file.")

        self.parts = stream.read_list(Part)
        for part in self.parts:
            if self.version >= 4:
                type = stream.read_u8()
                if ctx.series != 1 or self.version < 6:
                    type = IPart.type_v1_to_latest(type)
                if type == 4:
                    self.part_brakes = part
                elif type == 44:
                    self.part_wheels = part
            part.deserialize(stream, ctx)

        self.upgradable_parts = stream.read_list(UpgradablePart)
        for upgradable_part in self.upgradable_parts:
            upgradable_part.deserialize(stream, ctx)
