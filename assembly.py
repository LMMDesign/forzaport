"""Wheel / tire / brake / control-arm placement (pure: no bpy).

Behavior-preserving port of the suspension-assembly logic from the old core.py main block.
The three suspension_transform_type modes:
  0 (skeleton) - each rigid part is positioned by its skeleton bone's rest transform
  1 (carbin)   - positioned by the carbin's per-model transform
  2 (gamedb)   - wheels/tires/brakes positioned from GameDB measurements (most accurate)

These helpers operate purely on parsed objects (CarRenderModel11 with .modelbin attached,
Modelbin transforms via set_transform/set_weights). The importer loads the modelbins and calls
into here; nothing here touches Blender.
"""

from .parsing.carbin import Part, CarRenderModel11


def identity():
    return [[1 if i == j else 0 for i in range(4)] for j in range(4)]


def is_identity_matrix(m, eps=1e-6):
    for i in range(4):
        for j in range(4):
            want = 1.0 if i == j else 0.0
            if abs(m[i][j] - want) > eps:
                return False
    return True


def is_root_carbin_bone(bone_name):
    """FTS ``IsRootCarbinBoneName`` — no carbin instance transform for these."""
    if not bone_name or not str(bone_name).strip():
        return False
    n = str(bone_name).strip().lower()
    return n in ("root", "<root>")


def _find_bone_index_by_name(skeleton, bone_name):
    if skeleton is None or not bone_name:
        return None
    want = str(bone_name).strip().lower()
    if not want:
        return None
    for i, b in enumerate(skeleton.bones):
        if (b.name or "").strip().lower() == want:
            return i
    return None


def _find_bone_index(skeleton, bone_name, bone_index):
    """Name match first; numeric index only when name is missing/empty."""
    by_name = _find_bone_index_by_name(skeleton, bone_name)
    if by_name is not None:
        return by_name
    if bone_name and str(bone_name).strip():
        # Named carbin bones must not fall through to a part-skeleton index —
        # controlArmLF_a.modelbin reuses short local indices that collide with
        # unrelated names like boneWingPivot (F80 suspension → wing attach bug).
        return None
    if skeleton is None:
        return None
    bones = skeleton.bones
    if bone_index is not None and 0 <= int(bone_index) < len(bones):
        return int(bone_index)
    return None


def skeleton_bone_world(skeleton, bone_name=None, bone_index=-1):
    """World rest row-matrix for a named/indexed bone (skeleton deserializer accumulates parents)."""
    idx = _find_bone_index(skeleton, bone_name, bone_index)
    if idx is None:
        return identity()
    return [row[:] for row in skeleton.bones[idx].transform]


def resolve_carbin_bone_world(part_skeleton, scene_skeleton, bone_name, bone_index):
    """Carbin bone world: part modelbin skeleton first, then scene ``_skeleton``.

    Carbin ``bone_index`` indexes the **scene** skeleton. Part modelbins often
    have tiny local skeletons with recycled names — never apply the carbin index
    to the part skeleton when a bone name is present.
    """
    idx = _find_bone_index_by_name(part_skeleton, bone_name)
    if idx is not None:
        return part_skeleton.bones[idx].transform, part_skeleton.bones[idx].name
    idx = _find_bone_index(scene_skeleton, bone_name, bone_index)
    if idx is not None:
        return scene_skeleton.bones[idx].transform, scene_skeleton.bones[idx].name
    return identity(), None


def carbin_instance_transform(model, part_modelbin, scene_skeleton_modelbin):
    """FTS ``TryResolveCarbinInstanceTransform``: ``carbin.T @ boneWorld(carbin.BoneName)``.

    Returns a row-matrix, or ``None`` when root-bound or identity (no layer-B apply).
    """
    if is_root_carbin_bone(getattr(model, "bone_name", None)):
        return None
    part_sk = getattr(part_modelbin, "skeleton", None) if part_modelbin else None
    scene_sk = (
        getattr(scene_skeleton_modelbin, "skeleton", None)
        if scene_skeleton_modelbin
        else None
    )
    bone_world, _ = resolve_carbin_bone_world(
        part_sk,
        scene_sk,
        getattr(model, "bone_name", None),
        getattr(model, "bone_index", -1),
    )
    carbin_t = getattr(model, "transform", None) or identity()
    inst = matmul4(carbin_t, bone_world)
    if is_identity_matrix(inst):
        return None
    return inst


def carbin_attach_bone_name(model, part_modelbin, scene_skeleton_modelbin):
    """Scene/part bone name used for layer-B attachment tagging (``None`` if root / unresolved)."""
    if is_root_carbin_bone(getattr(model, "bone_name", None)):
        return None
    part_sk = getattr(part_modelbin, "skeleton", None) if part_modelbin else None
    scene_sk = (
        getattr(scene_skeleton_modelbin, "skeleton", None)
        if scene_skeleton_modelbin
        else None
    )
    _bw, name = resolve_carbin_bone_world(
        part_sk,
        scene_sk,
        getattr(model, "bone_name", None),
        getattr(model, "bone_index", -1),
    )
    return name


def uses_carbin_layer_b(part, model):
    """Body parts: assembly already owns wheel/tire/brake/control-arm placement."""
    if getattr(part, "type", None) in (44, 8, 4):
        return False
    bn = getattr(model, "bone_name", None) or ""
    if bn.startswith("controlArm"):
        return False
    return True


def matmul4(a, b):
    r = [[0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            s = 0
            for k in range(4):
                s += a[i][k] * b[k][j]
            r[i][j] = s
    return r


ROTATE_Y_180 = ((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1))


def bone_name_to_wheel_index(bone_name):
    if bone_name.endswith("LF"):
        return 0
    if bone_name.endswith("RF"):
        return 1
    if bone_name.endswith("RR"):
        return 2
    if bone_name.endswith("LR"):
        return 3
    if bone_name.endswith("LM"):
        return 4
    if bone_name.endswith("RM"):
        return 5
    print("Error: Unknown wheel bone name.")


def wheel_index_to_name(wheel_index):
    return {0: "LF", 1: "RF", 2: "RR", 3: "LR", 4: "LM", 5: "RM"}.get(wheel_index)


def wheel_index_is_right(wheel_index):
    return wheel_index in (1, 2, 5)  # RF, RR, RM


def synthesize_tire_part(scene, tire_internal_path):
    """Build a synthetic TireCompound part mirroring the (already LOD-filtered) wheel models."""
    part = Part()
    part.type = 8  # TireCompound
    part.models = [CarRenderModel11() for _ in range(len(scene.part_wheels.models))]
    scene.parts.append(part)
    for model, wheel_model in zip(part.models, scene.part_wheels.models):
        model.path = tire_internal_path
        model.levels_of_detail = 127
        model.bone_index = wheel_model.bone_index
        model.type = "Tires"
        model.bone_name = wheel_model.bone_name
        model.transform = identity()
        # Match wheel visibility + allow Exterior/Cockpit/etc. import filters.
        model.draw_groups = getattr(wheel_model, "draw_groups", None) or 127
        if model.draw_groups < 127:
            model.draw_groups = 127
    scene.part_tires = part
    return part


def apply_part_assignment(scene, o, part, model, skeleton_modelbin, sphere_cb=None):
    """Classify a freshly-loaded model into the wheel/tire/brake/control-arm registries, set its
    morph weights, and (for the wheel GameDB mode) compute its placement transform.

    Mirrors core.py lines ~2509-2587. model.modelbin must already be deserialized.
    """
    st = o.suspension_transform_type

    if part.type == 44:  # WheelStyle
        wheel_index = bone_name_to_wheel_index(model.bone_name)
        scene.part_wheels.wheel_models[wheel_index] = model
        is_front = wheel_index < 2
        is_right = wheel_index_is_right(wheel_index)
        if is_front:
            tire_width_mm, original_tire_aspect, wheel_diameter_in = (
                o.FrontTireWidthMM, o.OriginalFrontTireAspect, o.FrontWheelDiameterIN)
        else:
            tire_width_mm, original_tire_aspect, wheel_diameter_in = (
                o.RearTireWidthMM, o.OriginalRearTireAspect, o.RearWheelDiameterIN)
        model.modelbin.set_weights(
            ((wheel_diameter_in - 10) / 14, (1 - tire_width_mm / 1000) / 0.9), 1)  # rim

        if st == 2:
            if is_front:
                model_track_outer, model_ride_height = o.ModelFrontTrackOuter, o.ModelFrontStockRideHeight
            else:
                model_track_outer, model_ride_height = o.ModelRearTrackOuter, o.ModelRearStockRideHeight
            half_wheel_outer_diameter_m = ((original_tire_aspect * 0.01) * (tire_width_mm * 0.001)
                                           + wheel_diameter_in * 0.0254 / 2)
            model.transform = identity()
            if is_right:
                model.transform[0][0] = -model.transform[0][0]
                model.transform[2][2] = -model.transform[2][2]
            translate = model.transform[3]
            translate[0] = model_track_outer / 2
            translate[1] = half_wheel_outer_diameter_m - model_ride_height
            translate[2] = o.ModelWheelbase / 2
            if not is_right:
                translate[0] = -translate[0]
            if not is_front:
                translate[2] = -translate[2]
            translate[0] += o.BottomCenterWheelbasePosX
            translate[1] += o.BottomCenterWheelbasePosY
            translate[2] -= o.BottomCenterWheelbasePosZ
            if sphere_cb is not None:
                sphere_cb(translate, model.bone_name)

    elif part.type == 8:  # TireCompound
        wheel_index = bone_name_to_wheel_index(model.bone_name)
        scene.part_tires.tire_models[wheel_index] = model
        if wheel_index < 2:
            tire_width_mm, original_tire_aspect, original_wheel_diameter_in, wheel_diameter_in = (
                o.FrontTireWidthMM, o.OriginalFrontTireAspect,
                o.OriginalFrontWheelDiameterIN, o.FrontWheelDiameterIN)
        else:
            tire_width_mm, original_tire_aspect, original_wheel_diameter_in, wheel_diameter_in = (
                o.RearTireWidthMM, o.OriginalRearTireAspect,
                o.OriginalRearWheelDiameterIN, o.RearWheelDiameterIN)
        model.modelbin.set_weights(
            ((tire_width_mm * original_tire_aspect / 100 - 225 + original_wheel_diameter_in * 12.7) / 275,
             (wheel_diameter_in - 10) / 14, 0, 0, 0), tire_width_mm / 1000)  # tire

    elif part.type == 4:  # Brakes
        wheel_index = bone_name_to_wheel_index(model.bone_name)
        if model.bone_name.startswith("spindle"):
            scene.part_brakes.rotor_models[wheel_index] = model
        elif model.bone_name.startswith("hub"):
            scene.part_brakes.caliper_models[wheel_index] = model
        else:
            print("Warning: Unknown BrakePart bone name.")
    elif model.bone_name.startswith("controlArm"):
        wheel_index = bone_name_to_wheel_index(model.bone_name)
        scene.control_arm_models[wheel_index] = model

    if part.type != 8:
        if st == 0:
            model.modelbin.set_transform(skeleton_modelbin.skeleton.bones[model.bone_index].transform)
        elif st == 1:
            model.modelbin.set_transform(model.transform)


def init_wheel_brake_transforms(scene, o):
    """Second pass: derive wheel/brake/caliper/control-arm world transforms (core.py 2600-2694)."""
    st = o.suspension_transform_type
    for wheel_index in range(6):
        wheel_model = scene.part_wheels.wheel_models[wheel_index]
        if wheel_model is None:
            continue
        is_right = wheel_index_is_right(wheel_index)
        spindle_offset = None
        for bone in wheel_model.modelbin.skeleton.bones:
            if bone.name == "spindle":
                spindle_offset = bone.transform[3][0]
                break
        control_arm_offset = 0.30480003  # 12 inch
        rotor_model = scene.part_brakes.rotor_models[wheel_index] if scene.part_brakes is not None else None
        caliper_local_transform = None
        if rotor_model is not None:
            for bone in rotor_model.modelbin.skeleton.bones:
                if bone.name == "controlArm":
                    control_arm_offset = bone.transform[3][0]
                    break
            rotor_center_offset = 0
            caliper_model = scene.part_brakes.caliper_models[wheel_index]
            if caliper_model is not None:
                for bone in rotor_model.modelbin.skeleton.bones:
                    if (bone.name == f"rotor{wheel_index_to_name(wheel_index)}_center"
                            or bone.name == "rotor_center" or bone.name == "rotorLF_center"):
                        rotor_center_offset = bone.transform[3][0]
                        break
                caliper_local_transform = [[0 for _ in range(4)] for _ in range(4)]
                clt = caliper_local_transform[3]
                clt[0] = rotor_center_offset
                clt[1] = caliper_model.transform[3][1] - rotor_model.transform[3][1]
                clt[2] = caliper_model.transform[3][2] - rotor_model.transform[3][2]
                clt[3] = 1
                if is_right:
                    for i in range(3):  # rotate Y 180 (hub bone has its own Y rotation)
                        for j in range(3):
                            for k in range(3):
                                caliper_local_transform[i][j] += caliper_model.transform[i][k] * ROTATE_Y_180[k][j]
                    clt[2] = -clt[2]
                else:
                    for j in range(3):
                        for i in range(3):
                            caliper_local_transform[j][i] = caliper_model.transform[j][i]

        if st == 2:
            spindle_transform = wheel_model.transform
            wheel_model.modelbin.set_transform(spindle_transform)
            translate_x = identity()
            translate_x[3][0] = spindle_offset

            if rotor_model is not None:
                brake_transform = matmul4(translate_x, spindle_transform)
                rotor_model.modelbin.set_transform(brake_transform)
                caliper_model = scene.part_brakes.caliper_models[wheel_index]
                if caliper_model is not None and caliper_local_transform is not None:
                    caliper_transform = matmul4(caliper_local_transform, brake_transform)
                    caliper_model.modelbin.set_transform(caliper_transform)

            control_arm_model = scene.control_arm_models[wheel_index]
            if control_arm_model is not None:
                transform = [row[:] for row in translate_x]  # starts at spindle_offset on X
                transform[3][0] += control_arm_offset
                if is_right:
                    transform[3][0] = -transform[3][0]
                for i in range(3):
                    transform[3][i] += spindle_transform[3][i]
                control_arm_model.modelbin.set_transform(transform)

        tire_model = None
        if scene.part_tires is not None:
            tire_model = scene.part_tires.tire_models[wheel_index]
        if tire_model is not None and tire_model.modelbin is not None:
            tire_model.modelbin.set_transform(wheel_model.modelbin.transform)
