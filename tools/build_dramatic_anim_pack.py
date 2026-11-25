#!/usr/bin/env python3
"""Author the PS2 dramatic animation pack on the Quaternius UAL skeleton.

The pack is original keyframed motion. It copies the rest skeleton from
``UAL1_Standard.glb`` and does not reuse UAL clip curves.

Bone names in that file are not the generic labels in the brief:

- hips  -> ``pelvis``
- neck  -> ``neck_01``
- head  -> ``Head``

Everything else matches (``spine_01/02/03``, ``clavicle_*``, ``upperarm_*``,
``lowerarm_*``, ``hand_*``, ``thigh_*``, ``calf_*``, ``foot_*``).

Axis convention is measured from the file before any clip is written.
Positive semantic pitch leans the chest toward character-forward.
Positive semantic yaw turns toward the character's right.
Positive semantic roll lifts the character's left side.
Limb targets are world-space meters on the bind (Y-up, facing +Z, left +X)
and are reached with two-bone IK so knees and elbows bend toward a forward
or outward pole instead of twisting backward.

Run: ``python tools/build_dramatic_anim_pack.py``
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UAL1 = ROOT / "media" / "assets3d" / "cc0" / "quaternius_universal_animation_library" / "UAL1_Standard.glb"
OUT = ROOT / "media" / "assets3d" / "animations" / "ps2_dramatic_pack.glb"
LICENSES = ROOT / "media" / "assets3d" / "licenses.json"
CC0_URL = "https://creativecommons.org/publicdomain/zero/1.0/"

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

CLIP_NAMES = (
    "Sprint_Urgent_Upright",
    "Abrupt_Halt_Skid",
    "Slam_Table_Right",
    "Slam_Table_Both",
    "Accuse_Point_Hard",
    "Confront_Lean_In",
    "Refuse_Wave_Off",
    "Interrupt_Hand_Stop",
    "Argue_Emphatic_Both",
    "Shrug_Frustrated",
    "Arms_Crossed_Stern",
    "Hands_On_Hips_Command",
    "Pace_Anxious_Turn",
    "Snap_Look_Back",
    "Startle_Recoil_Back",
    "Flinch_Explosion_Flash",
    "Turn_Walk_Away_Dismiss",
    "Lean_Console_Inspect",
    "Console_Operate_Urgent",
    "Button_Press_Hard",
    "Panel_Open_Pull",
    "Module_Detach_Pull",
    "Module_Install_Lock",
    "Repair_Bend_Work",
    "Handoff_Offer_Firm",
    "Handoff_Grab_Pull",
    "Inspect_Prop_Close",
    "Check_Window_Urgent",
    "Look_Up_Array_Track",
    "Exhale_Relief_Slump",
    "Nod_Firm_Confirm",
    "Catch_Breath_Brace",
)

DRIVEN_SKIP = {"", "Armature", "Mannequin"}
FEATURE_BONES = (
    "root", "pelvis", "spine_02", "spine_03", "neck_01", "Head",
    "upperarm_l", "lowerarm_l", "hand_l", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
)
BODY_BONES = (
    "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head",
    "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l", "thigh_r", "calf_r", "foot_r",
)


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qconj(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def qrot(q, v):
    x, y, z = v
    qx, qy, qz, qw = qnorm(q)
    ix = qw * x + qy * z - qz * y
    iy = qw * y + qz * x - qx * z
    iz = qw * z + qx * y - qy * x
    iw = -qx * x - qy * y - qz * z
    return (
        ix * qw + iw * -qx + iy * -qz - iz * -qy,
        iy * qw + iw * -qy + iz * -qx - ix * -qz,
        iz * qw + iw * -qz + ix * -qy - iy * -qx,
    )


def qaa(axis, degrees):
    rad = math.radians(degrees) * 0.5
    s = math.sin(rad)
    n = math.sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]) or 1.0
    return (axis[0] / n * s, axis[1] / n * s, axis[2] / n * s, math.cos(rad))


def quat_from_to(a, b):
    a = vnorm(a)
    b = vnorm(b)
    d = vdot(a, b)
    if d > 0.9999:
        return (0.0, 0.0, 0.0, 1.0)
    if d < -0.9999:
        axis = vcross(a, (1.0, 0.0, 0.0))
        if vlen(axis) < 1e-4:
            axis = vcross(a, (0.0, 1.0, 0.0))
        return qaa(axis, 180.0)
    axis = vcross(a, b)
    return qnorm((axis[0], axis[1], axis[2], 1.0 + d))


def axis_angle(q):
    q = qnorm(q)
    if q[3] < 0.0:
        q = (-q[0], -q[1], -q[2], -q[3])
    w = max(-1.0, min(1.0, q[3]))
    angle = 2.0 * math.acos(w)
    s = math.sqrt(max(0.0, 1.0 - w * w))
    if s < 1e-8 or angle < 1e-8:
        return (1.0, 0.0, 0.0), 0.0
    return (q[0] / s, q[1] / s, q[2] / s), math.degrees(angle)


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vlen(a):
    return math.sqrt(vdot(a, a))


def vnorm(a):
    n = vlen(a) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def vdist(a, b):
    return vlen(vsub(a, b))


def euler_xyz(rx, ry, rz):
    return qmul(qmul(qaa((1.0, 0.0, 0.0), rx), qaa((0.0, 1.0, 0.0), ry)), qaa((0.0, 0.0, 1.0), rz))


def load_glb(path: Path):
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError(f"{path} is too small to be a glb")
    magic, version, length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != 2 or length != len(data):
        raise ValueError(f"{path} is not a valid GLB header")
    offset = 12
    document = None
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + chunk_len]
        offset += 8 + chunk_len
        if chunk_type == JSON_CHUNK:
            document = json.loads(chunk.decode("utf-8").rstrip("\x00").strip())
    if document is None:
        raise ValueError(f"{path} has no JSON chunk")
    return document


def fk(skel, rotations, translations):
    world_r = {}
    world_p = {}

    def walk(index):
        if index in world_r:
            return
        parent = skel.parent.get(index)
        if parent is None:
            world_r[index] = rotations[index]
            world_p[index] = translations[index]
            return
        walk(parent)
        pw = world_r[parent]
        pp = world_p[parent]
        world_r[index] = qnorm(qmul(pw, rotations[index]))
        offset = qrot(pw, translations[index])
        world_p[index] = vadd(pp, offset)

    for index in range(len(skel.names)):
        walk(index)
    return world_r, world_p


class Skeleton:
    def __init__(self, document):
        self.nodes = document["nodes"]
        self.names = [str(node.get("name") or "") for node in self.nodes]
        self.index = {name: i for i, name in enumerate(self.names) if name}
        self.parent = {}
        self.children = {}
        for i, node in enumerate(self.nodes):
            kids = list(node.get("children") or [])
            self.children[i] = kids
            for child in kids:
                self.parent[child] = i
        self.rest_r = []
        self.rest_t = []
        for node in self.nodes:
            if "matrix" in node:
                raise RuntimeError(f"{node.get('name')} uses a matrix; UAL rest is TRS")
            self.rest_r.append(qnorm(tuple(node.get("rotation") or (0.0, 0.0, 0.0, 1.0))))
            self.rest_t.append(tuple(float(v) for v in (node.get("translation") or (0.0, 0.0, 0.0))))
        self.driven = [i for i, name in enumerate(self.names) if name not in DRIVEN_SKIP]
        if len(self.driven) != 65:
            raise RuntimeError(f"expected 65 driven bones, found {len(self.driven)}")
        missing = [name for name in BODY_BONES + ("root", "ball_l", "ball_r") if name not in self.index]
        if missing:
            raise RuntimeError(f"UAL skeleton is missing {missing}")
        self.rest_wr, self.rest_wp = fk(self, self.rest_r, self.rest_t)
        self.length = {}
        pairs = (
            ("upperarm_l", "lowerarm_l"), ("lowerarm_l", "hand_l"),
            ("upperarm_r", "lowerarm_r"), ("lowerarm_r", "hand_r"),
            ("thigh_l", "calf_l"), ("calf_l", "foot_l"), ("foot_l", "ball_l"),
            ("thigh_r", "calf_r"), ("calf_r", "foot_r"), ("foot_r", "ball_r"),
        )
        for a, b in pairs:
            self.length[(a, b)] = vdist(self.rest_wp[self.index[a]], self.rest_wp[self.index[b]])
            if self.length[(a, b)] < 0.02:
                raise RuntimeError(f"bone length {a}->{b} is {self.length[(a, b)]}")


class Axes:
    """Semantic degrees mapped onto the measured local axes."""

    def __init__(self):
        self.euler = {}
        self.pelvis_local = {}
        self.root_local = {}
        self.root_yaw = (2, 1.0)
        self.local_forward = (0.0, -1.0, 0.0)
        self.clavicle = {}
        self.notes = []


def _point(wr, wp, index, local):
    return vadd(wp[index], qrot(wr[index], local))


def _pose_with_euler(skel, bone, index, degrees):
    rotations = list(skel.rest_r)
    axis = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[index]
    i = skel.index[bone]
    rotations[i] = qnorm(qmul(rotations[i], qaa(axis, degrees)))
    return fk(skel, rotations, skel.rest_t)


def _discover_euler(skel, bone, axes: Axes):
    i = skel.index[bone]
    rest_up = _point(skel.rest_wr, skel.rest_wp, i, (0.0, 0.28, 0.0))
    rest_front = _point(skel.rest_wr, skel.rest_wp, i, (0.0, 0.0, 0.28))
    rest_left = _point(skel.rest_wr, skel.rest_wp, i, (0.28, 0.0, 0.0))
    scores = {"pitch": [], "yaw": [], "roll": []}
    for index in range(3):
        wr, wp = _pose_with_euler(skel, bone, index, 25.0)
        up = vsub(_point(wr, wp, i, (0.0, 0.28, 0.0)), rest_up)
        front = vsub(_point(wr, wp, i, (0.0, 0.0, 0.28)), rest_front)
        left = vsub(_point(wr, wp, i, (0.28, 0.0, 0.0)), rest_left)
        scores["pitch"].append(up[2])
        scores["yaw"].append(-front[0])
        scores["roll"].append(left[1])
    mapping = {}
    used = set()
    for name in ("pitch", "yaw", "roll"):
        best_i = max(range(3), key=lambda n: abs(scores[name][n]))
        value = scores[name][best_i]
        if abs(value) < 0.012:
            raise RuntimeError(f"{bone} {name} probe moved only {value:.4f}")
        sign = 1.0 if value > 0.0 else -1.0
        mapping[name] = (best_i, sign)
        used.add(best_i)
    if len(used) < 3:
        raise RuntimeError(f"{bone} pitch/yaw/roll collapsed onto axes {mapping}")
    axes.euler[bone] = mapping
    labels = "XYZ"
    axes.notes.append(
        f"{bone}: pitch {labels[mapping['pitch'][0]]}{'+' if mapping['pitch'][1] > 0 else '-'} "
        f"yaw {labels[mapping['yaw'][0]]}{'+' if mapping['yaw'][1] > 0 else '-'} "
        f"roll {labels[mapping['roll'][0]]}{'+' if mapping['roll'][1] > 0 else '-'}"
    )


def _discover_translation(skel, bone, target_name, store, axes: Axes):
    i = skel.index[bone]
    j = skel.index[target_name]
    origin = skel.rest_wp[j]
    best = {}
    for world_axis, goal in enumerate(((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))):
        winner = None
        for local in range(3):
            for sign in (1.0, -1.0):
                translations = list(skel.rest_t)
                delta = [0.0, 0.0, 0.0]
                delta[local] = 0.1 * sign
                translations[i] = vadd(translations[i], tuple(delta))
                _wr, wp = fk(skel, skel.rest_r, translations)
                moved = vsub(wp[j], origin)
                score = vdot(moved, goal)
                if winner is None or score > winner[0]:
                    winner = (score, local, sign)
        if winner[0] < 0.06:
            raise RuntimeError(f"{bone} does not translate along world axis {world_axis}")
        best["xyz"[world_axis]] = (winner[1], winner[2])
    store.update(best)
    axes.notes.append(
        f"{bone} translation world XYZ -> local "
        + " ".join(f"{k}[{v[0]}{'+' if v[1] > 0 else '-'}]" for k, v in best.items())
    )


def _discover_root_yaw(skel, axes: Axes):
    i = skel.index["root"]
    rest_q = skel.rest_r[i]
    candidates = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))
    axes.local_forward = max(candidates, key=lambda vec: qrot(rest_q, vec)[2])
    rest_face = qrot(rest_q, axes.local_forward)
    winner = None
    for index in range(3):
        for sign in (1.0, -1.0):
            axis = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[index]
            q = qnorm(qmul(rest_q, qaa(axis, 25.0 * sign)))
            face = qrot(q, axes.local_forward)
            score = rest_face[0] - face[0]
            if winner is None or score > winner[0]:
                winner = (score, index, sign, face)
    if winner[0] < 0.05:
        raise RuntimeError("root yaw does not turn the character to their right")
    axes.root_yaw = (winner[1], winner[2])
    axes.notes.append(
        f"root forward local {tuple(round(c, 3) for c in axes.local_forward)}; "
        f"yaw-right is local {'XYZ'[winner[1]]}{'+' if winner[2] > 0 else '-'}"
    )


def _discover_clavicle(skel, axes: Axes):
    for side, bone, child in (("l", "clavicle_l", "upperarm_l"), ("r", "clavicle_r", "upperarm_r")):
        i = skel.index[bone]
        j = skel.index[child]
        origin = skel.rest_wp[j]
        lift = None
        reach = None
        for index in range(3):
            for sign in (1.0, -1.0):
                rotations = list(skel.rest_r)
                axis = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[index]
                rotations[i] = qnorm(qmul(rotations[i], qaa(axis, 15.0 * sign)))
                _wr, wp = fk(skel, rotations, skel.rest_t)
                delta = vsub(wp[j], origin)
                if lift is None or delta[1] > lift[0]:
                    lift = (delta[1], index, sign)
                if reach is None or delta[2] > reach[0]:
                    reach = (delta[2], index, sign)
        if lift[0] < 0.004 or reach[0] < 0.004:
            raise RuntimeError(f"{bone} shrug/reach probe was too small")
        axes.clavicle[side] = {"lift": (lift[1], lift[2]), "reach": (reach[1], reach[2])}
        axes.notes.append(
            f"{bone}: lift {'XYZ'[lift[1]]}{'+' if lift[2] > 0 else '-'} "
            f"reach {'XYZ'[reach[1]]}{'+' if reach[2] > 0 else '-'}"
        )


def discover_axes(skel: Skeleton) -> Axes:
    axes = Axes()
    # Root rest rotation must turn local +Z into world up. This is the UAL
    # bind that puts a Z-up joint frame into glTF Y-up.
    up = qrot(skel.rest_r[skel.index["root"]], (0.0, 0.0, 1.0))
    if up[1] < 0.9:
        raise RuntimeError(f"root rest +Z is {tuple(round(c, 3) for c in up)}, expected world up")
    left = skel.rest_wp[skel.index["hand_l"]][0]
    right = skel.rest_wp[skel.index["hand_r"]][0]
    if not (left > 0.4 and right < -0.4):
        raise RuntimeError("character left is not world +X on the UAL bind")
    forward = qrot(skel.rest_wr[skel.index["spine_03"]], (0.0, 0.0, 1.0))
    if forward[2] < 0.8:
        raise RuntimeError("spine_03 local +Z is not character forward")
    for bone in ("pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head"):
        _discover_euler(skel, bone, axes)
    _discover_translation(skel, "pelvis", "pelvis", axes.pelvis_local, axes)
    _discover_translation(skel, "root", "pelvis", axes.root_local, axes)
    _discover_root_yaw(skel, axes)
    _discover_clavicle(skel, axes)
    # Feet start on the ground and the head is above the pelvis.
    if skel.rest_wp[skel.index["ball_l"]][1] > 0.08:
        raise RuntimeError("rest ball is not near the ground")
    if skel.rest_wp[skel.index["Head"]][1] < skel.rest_wp[skel.index["pelvis"]][1] + 0.4:
        raise RuntimeError("rest head is not above the pelvis")
    return axes


def _apply_euler(rest_q, mapping, pitch, yaw, roll):
    euler = [0.0, 0.0, 0.0]
    for name, value in (("pitch", pitch), ("yaw", yaw), ("roll", roll)):
        index, sign = mapping[name]
        euler[index] += sign * float(value)
    return qnorm(qmul(rest_q, euler_xyz(*euler)))


def _map_translation(mapping, world_xyz):
    local = [0.0, 0.0, 0.0]
    for axis, value in zip("xyz", world_xyz):
        index, sign = mapping[axis]
        local[index] += sign * float(value)
    return tuple(local)


def _semantic_euler_quat(axes: Axes, bone, pitch, yaw, roll, rest_q):
    return _apply_euler(rest_q, axes.euler[bone], pitch, yaw, roll)


class Pose:
    def __init__(self, skel: Skeleton):
        self.r = list(skel.rest_r)
        self.t = list(skel.rest_t)


def _pole(kind, shoulder, side):
    sign = 1.0 if side == "l" else -1.0
    offsets = {
        "down": (sign * 0.34, -0.85, -0.08),
        "out": (sign * 0.8, 0.05, -0.2),
        "forward": (sign * 0.16, -0.35, 0.55),
        "up": (sign * 0.42, 0.55, 0.05),
        "back": (sign * 0.35, -0.15, -0.65),
        "cross": (sign * 0.05, -0.2, 0.65),
        "hips": (sign * 0.72, -0.05, -0.28),
    }
    off = offsets[kind]
    return vadd(shoulder, off)


def _solve_two_bone(origin, target, len_a, len_b, pole):
    raw = vsub(target, origin)
    dist = vlen(raw)
    if dist < 1e-5:
        raw = (0.0, -1.0, 0.0)
        dist = 1.0
    max_d = max(1e-3, len_a + len_b - 0.012)
    min_d = abs(len_a - len_b) + 0.012
    clamped = min(max(dist, min_d), max_d)
    unit = vscale(raw, 1.0 / dist)
    reached = vadd(origin, vscale(unit, clamped))
    cos_a = (len_a * len_a + clamped * clamped - len_b * len_b) / (2.0 * len_a * clamped)
    cos_a = max(-1.0, min(1.0, cos_a))
    along = cos_a * len_a
    height = math.sqrt(max(0.0, len_a * len_a - along * along))
    pole_v = vsub(pole, origin)
    side = vsub(pole_v, vscale(unit, vdot(pole_v, unit)))
    if vlen(side) < 1e-4:
        side = vcross(unit, (0.0, 1.0, 0.0))
        if vlen(side) < 1e-4:
            side = vcross(unit, (1.0, 0.0, 0.0))
    joint = vadd(origin, vadd(vscale(unit, along), vscale(vnorm(side), height)))
    return joint, reached


def _aim_y(parent_world, rest_local, direction):
    base = qnorm(qmul(parent_world, rest_local))
    current = qrot(base, (0.0, 1.0, 0.0))
    aimed = qnorm(qmul(quat_from_to(current, direction), base))
    return qnorm(qmul(qconj(parent_world), aimed))


def _twist_align(parent_world, local_q, desired, hinge_local=(0.0, 1.0, 0.0), normal_local=(1.0, 0.0, 0.0)):
    world = qnorm(qmul(parent_world, local_q))
    hinge = qrot(world, hinge_local)
    current = qrot(world, normal_local)
    c = vsub(current, vscale(hinge, vdot(current, hinge)))
    d = vsub(desired, vscale(hinge, vdot(desired, hinge)))
    if vlen(c) < 1e-4 or vlen(d) < 1e-4:
        return local_q
    aligned = qnorm(qmul(quat_from_to(c, d), world))
    return qnorm(qmul(qconj(parent_world), aligned))


def _orient_palm(parent_world, rest_local, desired):
    """Twist the hand about its finger axis using whichever local axis already faces the palm.

    Searching ±X and ±Z keeps the correction under a half-turn. A single fixed axis
    was landing on 180 degree flips when that axis pointed the wrong way.
    """
    best_q = rest_local
    best_cost = 1e9
    desired_n = vnorm(desired)
    for normal in ((1.0, 0.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)):
        quat = _twist_align(parent_world, rest_local, desired_n, normal_local=normal)
        _axis, deg = axis_angle(qmul(qconj(rest_local), quat))
        world = qnorm(qmul(parent_world, quat))
        facing = vnorm(qrot(world, normal))
        cost = deg + (1.0 - vdot(facing, desired_n)) * 50.0
        if cost < best_cost:
            best_cost = cost
            best_q = quat
    return best_q


def _set_chain(skel, pose, wr, wp, bone, child, target_dir):
    parent = skel.parent[skel.index[bone]]
    pose.r[skel.index[bone]] = _aim_y(wr[parent], skel.rest_r[skel.index[bone]], target_dir)
    wr, wp = fk(skel, pose.r, pose.t)
    return wr, wp


def solve_spec(skel: Skeleton, axes: Axes, spec: dict) -> Pose:
    spec = copy.deepcopy(spec)
    for _ in range(5):
        pose = _apply_spec(skel, axes, spec)
        wr, wp = _character_fk(skel, pose)
        lows = []
        for side in ("l", "r"):
            if spec["leg_" + side].get("plant", True):
                lows.append(wp[skel.index["ball_" + side]][1])
        if not lows:
            return pose
        err = min(lows) - 0.02
        if abs(err) < 0.012:
            return pose
        spec["shift"] = (
            spec["shift"][0],
            spec["shift"][1],
            max(-0.2, min(0.06, spec["shift"][2] - err)),
        )
    return pose


def _character_fk(skel, pose):
    rotations = list(pose.r)
    translations = list(pose.t)
    root = skel.index["root"]
    rotations[root] = skel.rest_r[root]
    translations[root] = skel.rest_t[root]
    return fk(skel, rotations, translations)


def _apply_root(skel: Skeleton, axes: Axes, pose: Pose, spec: dict) -> None:
    """Stamp root yaw and travel after the body is solved in place."""
    root = skel.index["root"]
    yaw = float(spec.get("root_yaw", 0.0))
    index, sign = axes.root_yaw
    axis = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))[index]
    pose.r[root] = qnorm(qmul(skel.rest_r[root], qaa(axis, yaw * sign)))
    face = qrot(pose.r[root], axes.local_forward)
    face = vnorm((face[0], 0.0, face[2]))
    left = (face[2], 0.0, -face[0])
    forward = float(spec.get("root_forward", 0.0))
    strafe = float(spec.get("root_left", 0.0))
    pose.t[root] = (
        face[0] * forward + left[0] * strafe,
        0.0,
        face[2] * forward + left[2] * strafe,
    )


def _apply_spec(skel: Skeleton, axes: Axes, spec: dict) -> Pose:
    pose = Pose(skel)
    pelvis = skel.index["pelvis"]
    shift = spec.get("shift", (0.0, 0.0, 0.0))
    # semantic shift is (left, forward, up) = world (+X, +Z, +Y)
    local_shift = _map_translation(axes.pelvis_local, (shift[0], shift[2], shift[1]))
    pose.t[pelvis] = vadd(skel.rest_t[pelvis], local_shift)
    pe, se, ne, he = spec["pelvis"], spec["spine"], spec["neck"], spec["head"]
    pose.r[pelvis] = _semantic_euler_quat(axes, "pelvis", *pe, skel.rest_r[pelvis])
    scales = ((0.34, "spine_01"), (0.40, "spine_02"), (0.26, "spine_03"))
    for scale, name in scales:
        i = skel.index[name]
        pose.r[i] = _semantic_euler_quat(
            axes, name, se[0] * scale, se[1] * scale, se[2] * scale, skel.rest_r[i]
        )
    pose.r[skel.index["neck_01"]] = _semantic_euler_quat(axes, "neck_01", *ne, skel.rest_r[skel.index["neck_01"]])
    pose.r[skel.index["Head"]] = _semantic_euler_quat(axes, "Head", *he, skel.rest_r[skel.index["Head"]])
    for side in ("l", "r"):
        bone = "clavicle_" + side
        i = skel.index[bone]
        lift, reach = spec.get("clav_" + side, (0.0, 0.0))
        euler = [0.0, 0.0, 0.0]
        for key, value in (("lift", lift), ("reach", reach)):
            index, sign = axes.clavicle[side][key]
            euler[index] += max(-14.0, min(14.0, sign * float(value)))
        pose.r[i] = qnorm(qmul(skel.rest_r[i], euler_xyz(*euler)))
    wr, wp = fk(skel, pose.r, pose.t)
    for side in ("l", "r"):
        arm = spec["arm_" + side]
        shoulder_n = "upperarm_" + side
        elbow_n = "lowerarm_" + side
        hand_n = "hand_" + side
        shoulder = wp[skel.index[shoulder_n]]
        pole = _pole(arm.get("pole", "down"), shoulder, side)
        joint, reached = _solve_two_bone(
            shoulder,
            tuple(arm["target"]),
            skel.length[(shoulder_n, elbow_n)],
            skel.length[(elbow_n, hand_n)],
            pole,
        )
        parent = skel.parent[skel.index[shoulder_n]]
        pose.r[skel.index[shoulder_n]] = _aim_y(wr[parent], skel.rest_r[skel.index[shoulder_n]], vsub(joint, shoulder))
        wr, wp = fk(skel, pose.r, pose.t)
        elbow = wp[skel.index[elbow_n]]
        pose.r[skel.index[elbow_n]] = _aim_y(
            wr[skel.index[shoulder_n]], skel.rest_r[skel.index[elbow_n]], vsub(reached, elbow)
        )
        wr, wp = fk(skel, pose.r, pose.t)
        palm = tuple(arm.get("palm", (0.0, -1.0, 0.0)))
        pose.r[skel.index[hand_n]] = _orient_palm(
            wr[skel.index[elbow_n]], skel.rest_r[skel.index[hand_n]], palm
        )
        wr, wp = fk(skel, pose.r, pose.t)
    for side in ("l", "r"):
        leg = spec["leg_" + side]
        hip_n = "thigh_" + side
        knee_n = "calf_" + side
        foot_n = "foot_" + side
        hip = wp[skel.index[hip_n]]
        pole = vadd(hip, (0.12 if side == "l" else -0.12, -0.25, 0.75))
        joint, reached = _solve_two_bone(
            hip,
            tuple(leg["ankle"]),
            skel.length[(hip_n, knee_n)],
            skel.length[(knee_n, foot_n)],
            pole,
        )
        parent = skel.parent[skel.index[hip_n]]
        pose.r[skel.index[hip_n]] = _aim_y(wr[parent], skel.rest_r[skel.index[hip_n]], vsub(joint, hip))
        wr, wp = fk(skel, pose.r, pose.t)
        knee = wp[skel.index[knee_n]]
        pose.r[skel.index[knee_n]] = _aim_y(
            wr[skel.index[hip_n]], skel.rest_r[skel.index[knee_n]], vsub(reached, knee)
        )
        wr, wp = fk(skel, pose.r, pose.t)
        ankle = wp[skel.index[foot_n]]
        pose.r[skel.index[foot_n]] = _aim_y(
            wr[skel.index[knee_n]], skel.rest_r[skel.index[foot_n]], tuple(leg["foot"])
        )
        wr, wp = fk(skel, pose.r, pose.t)
        _ = ankle
    _apply_root(skel, axes, pose, spec)
    return pose


def neutral_spec(skel: Skeleton) -> dict:
    def ankle(side):
        return tuple(skel.rest_wp[skel.index["foot_" + side]])

    def foot_dir(side):
        return tuple(qrot(skel.rest_wr[skel.index["foot_" + side]], (0.0, 1.0, 0.0)))

    return {
        "shift": (0.0, 0.0, 0.0),
        "pelvis": (0.0, 0.0, 0.0),
        "spine": (1.5, 0.0, 0.0),
        "neck": (-1.0, 0.0, 0.0),
        "head": (0.0, 0.0, 0.0),
        "clav_l": (0.0, 0.0),
        "clav_r": (0.0, 0.0),
        "root_yaw": 0.0,
        "root_forward": 0.0,
        "root_left": 0.0,
        "arm_l": {"target": (0.30, 0.98, 0.10), "pole": "down", "palm": (0.25, -0.55, 0.15)},
        "arm_r": {"target": (-0.30, 0.98, 0.10), "pole": "down", "palm": (-0.25, -0.55, 0.15)},
        "leg_l": {"ankle": ankle("l"), "foot": foot_dir("l"), "plant": True},
        "leg_r": {"ankle": ankle("r"), "foot": foot_dir("r"), "plant": True},
    }


def edit(spec, **kwargs):
    out = copy.deepcopy(spec)
    for key, value in kwargs.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            merged = dict(out[key])
            merged.update(value)
            out[key] = merged
        else:
            out[key] = value
    return out


def _gait(skel, phase, step, lift, arm, lean, chest):
    spec = neutral_spec(skel)
    rest_l = skel.rest_wp[skel.index["foot_l"]]
    rest_r = skel.rest_wp[skel.index["foot_r"]]
    foot_y = qrot(skel.rest_wr[skel.index["foot_l"]], (0.0, 1.0, 0.0))

    def foot(p, rest):
        if p < 0.56:
            u = p / 0.56
            z = rest[2] + step * (1.0 - 2.0 * u)
            y = rest[1]
            direction = foot_y
            plant = u < 0.86
            if u > 0.84:
                w = (u - 0.84) / 0.16
                y = rest[1] + 0.03 * w
                direction = vnorm((0.0, -0.78, 0.63))
                plant = False
        else:
            u = (p - 0.56) / 0.44
            z = rest[2] - step + 2.0 * step * u
            y = rest[1] + math.sin(u * math.pi) * lift
            direction = vnorm((0.0, -0.2, 0.98))
            plant = False
        return {"ankle": (rest[0], y, z), "foot": direction, "plant": plant}

    def arm_target(p, side):
        sign = 1.0 if side == "l" else -1.0
        z = 0.06 + math.cos(p * math.tau) * arm
        y = 1.02 + 0.2 * max(0.0, math.sin(p * math.tau))
        x = sign * (0.32 + 0.03 * math.sin(p * math.tau))
        return {"target": (x, y, z), "pole": "down", "palm": (sign * 0.2, -0.35, 0.25)}

    roll = math.sin(phase * math.tau) * 5.0
    spec["pelvis"] = (lean, 0.0, -roll * 0.35)
    spec["spine"] = (chest, -math.sin(phase * math.tau) * 4.0, roll)
    spec["head"] = (-4.0, math.sin(phase * math.tau) * 3.0, 0.0)
    spec["neck"] = (-2.0, 0.0, 0.0)
    spec["shift"] = (math.sin(phase * math.tau) * 0.035, 0.0, 0.03 * abs(math.sin(phase * math.tau)))
    spec["leg_l"] = foot(phase % 1.0, rest_l)
    spec["leg_r"] = foot((phase + 0.5) % 1.0, rest_r)
    spec["arm_l"] = arm_target((phase + 0.5) % 1.0, "l")
    spec["arm_r"] = arm_target(phase % 1.0, "r")
    return spec


def _keys(duration, pairs):
    keys = []
    for t, spec in pairs:
        keys.append((float(t), copy.deepcopy(spec)))
    if len(keys) < 8 or len(keys) > 18:
        raise RuntimeError(f"key count {len(keys)} is outside 8..18")
    return {"duration": duration, "keys": keys, "loop": False}


def build_clips(skel: Skeleton) -> list[dict]:
    n = neutral_spec(skel)
    clips = []

    def add(name, duration, pairs, loop=False):
        clip = _keys(duration, pairs)
        clip["name"] = name
        clip["loop"] = loop
        clips.append(clip)

    sprint = []
    for i in range(9):
        t = i / 8.0
        phase = 0.0 if i == 8 else t
        spec = _gait(skel, phase, 0.34, 0.2, 0.3, 5.0, -1.0)
        spec["root_forward"] = 3.05 * t
        spec["head"] = (-6.0, spec["head"][1], 0.0)
        sprint.append((t, spec))
    add("Sprint_Urgent_Upright", 0.72, sprint, loop=True)

    def run_at(phase, travel):
        spec = _gait(skel, phase, 0.32, 0.16, 0.26, 6.0, 0.0)
        spec["root_forward"] = travel
        return spec

    skid = edit(
        n,
        pelvis=(-18.0, 0.0, 0.0),
        spine=(-6.0, 0.0, 0.0),
        head=(8.0, 0.0, 0.0),
        shift=(0.02, -0.06, -0.06),
        leg_l={"ankle": (0.11, 0.1, 0.3), "foot": (0.0, 0.42, 0.91), "plant": True},
        leg_r={"ankle": (-0.13, 0.11, -0.2), "foot": (0.0, -0.55, 0.84), "plant": True},
        arm_l={"target": (0.24, 1.28, 0.32), "pole": "forward", "palm": (0.1, -0.2, 1.0)},
        arm_r={"target": (-0.2, 1.32, 0.34), "pole": "forward", "palm": (-0.1, -0.2, 1.0)},
    )
    settle = edit(
        n,
        pelvis=(-2.0, 0.0, 0.0),
        spine=(2.0, 0.0, 0.0),
        head=(2.0, 0.0, 0.0),
        shift=(0.0, 0.0, 0.0),
        root_forward=1.34,
    )
    add("Abrupt_Halt_Skid", 1.16, [
        (0.00, run_at(0.08, 0.0)),
        (0.12, run_at(0.28, 0.42)),
        (0.24, run_at(0.48, 0.78)),
        (0.36, edit(skid, root_forward=1.05, pelvis=(-12.0, 0.0, 0.0))),
        (0.48, edit(skid, root_forward=1.22)),
        (0.60, edit(skid, root_forward=1.3, pelvis=(-20.0, 0.0, 0.0), spine=(-8.0, 0.0, 0.0))),
        (0.74, edit(settle, pelvis=(-8.0, 0.0, 0.0), spine=(-2.0, 0.0, 0.0), root_forward=1.33)),
        (0.88, settle),
        (1.00, edit(settle, head=(0.0, 0.0, 0.0))),
    ])

    def slam_right(t_pose):
        return t_pose

    wind = edit(
        n, pelvis=(-8.0, 0.0, 0.0), spine=(-10.0, -6.0, 0.0), head=(-4.0, -4.0, 0.0),
        shift=(-0.02, -0.04, 0.02),
        arm_r={"target": (-0.2, 1.66, -0.08), "pole": "up", "palm": (0.0, 0.2, -0.6)},
        arm_l={"target": (0.32, 1.05, 0.02), "pole": "down", "palm": (0.2, -0.4, 0.2)},
    )
    impact = edit(
        n, pelvis=(8.0, 4.0, 0.0), spine=(24.0, 6.0, 2.0), neck=(6.0, 2.0, 0.0), head=(10.0, 2.0, 0.0),
        shift=(0.0, 0.08, -0.05),
        arm_r={"target": (-0.08, 0.98, 0.3), "pole": "up", "palm": (0.0, -1.0, 0.15)},
        arm_l={"target": (0.36, 1.12, -0.02), "pole": "out", "palm": (0.3, -0.4, 0.1)},
    )
    add("Slam_Table_Right", 1.12, [
        (0.00, n),
        (0.14, edit(wind, spine=(-4.0, -2.0, 0.0), arm_r={"target": (-0.28, 1.25, 0.02), "pole": "back"})),
        (0.28, wind),
        (0.40, edit(n, spine=(10.0, 4.0, 0.0), arm_r={"target": (-0.12, 1.32, 0.2), "pole": "forward", "palm": (0.0, -0.4, 0.8)})),
        (0.50, impact),
        (0.62, edit(impact, spine=(28.0, 4.0, 0.0), arm_r={"target": (-0.04, 0.92, 0.34), "pole": "up", "palm": (0.0, -1.0, 0.1)}, shift=(0.0, 0.1, -0.07))),
        (0.76, edit(n, spine=(8.0, 2.0, 0.0), pelvis=(-4.0, 0.0, 0.0), arm_r={"target": (-0.18, 1.16, 0.12), "pole": "forward"})),
        (0.88, edit(n, spine=(6.0, 0.0, 0.0), head=(4.0, 0.0, 0.0), arm_r={"target": (-0.22, 1.06, 0.16), "pole": "down"})),
        (1.00, edit(n, spine=(5.0, 0.0, 0.0), head=(3.0, 0.0, 0.0), arm_r={"target": (-0.24, 1.02, 0.14), "pole": "down"})),
    ])
    _ = slam_right

    both = edit(
        n, pelvis=(6.0, 0.0, 0.0), spine=(22.0, 0.0, 0.0), head=(8.0, 0.0, 0.0), shift=(0.0, 0.1, -0.08),
        arm_l={"target": (0.16, 0.96, 0.32), "pole": "up", "palm": (0.15, -1.0, 0.1)},
        arm_r={"target": (-0.16, 0.96, 0.32), "pole": "up", "palm": (-0.15, -1.0, 0.1)},
    )
    add("Slam_Table_Both", 1.18, [
        (0.00, n),
        (0.16, edit(n, spine=(-8.0, 0.0, 0.0), pelvis=(-6.0, 0.0, 0.0), shift=(0.0, -0.04, 0.02),
                    arm_l={"target": (0.22, 1.58, -0.02), "pole": "up"},
                    arm_r={"target": (-0.22, 1.58, -0.02), "pole": "up"})),
        (0.30, edit(n, spine=(-12.0, 0.0, 0.0),
                    arm_l={"target": (0.18, 1.7, 0.02), "pole": "up", "palm": (0.0, 0.4, -0.4)},
                    arm_r={"target": (-0.18, 1.7, 0.02), "pole": "up", "palm": (0.0, 0.4, -0.4)})),
        (0.42, edit(n, spine=(8.0, 0.0, 0.0),
                    arm_l={"target": (0.16, 1.28, 0.22), "pole": "forward"},
                    arm_r={"target": (-0.16, 1.28, 0.22), "pole": "forward"})),
        (0.52, both),
        (0.64, edit(both, spine=(30.0, 0.0, 0.0), shift=(0.0, 0.12, -0.1),
                    arm_l={"target": (0.14, 0.9, 0.36), "pole": "up", "palm": (0.1, -1.0, 0.05)},
                    arm_r={"target": (-0.14, 0.9, 0.36), "pole": "up", "palm": (-0.1, -1.0, 0.05)})),
        (0.78, edit(n, spine=(10.0, 0.0, 0.0), pelvis=(-6.0, 0.0, 0.0),
                    arm_l={"target": (0.2, 1.12, 0.16), "pole": "forward"},
                    arm_r={"target": (-0.2, 1.12, 0.16), "pole": "forward"})),
        (1.00, edit(n, spine=(6.0, 0.0, 0.0), head=(4.0, 0.0, 0.0),
                    arm_l={"target": (0.24, 1.04, 0.16), "pole": "down"},
                    arm_r={"target": (-0.24, 1.04, 0.16), "pole": "down"})),
    ])

    point = edit(
        n, pelvis=(6.0, 8.0, 0.0), spine=(12.0, 10.0, 0.0), neck=(2.0, 6.0, 0.0), head=(4.0, 8.0, 0.0),
        shift=(-0.02, 0.1, -0.02),
        arm_r={"target": (-0.02, 1.46, 0.46), "pole": "down", "palm": (-0.15, -0.15, 1.0)},
        arm_l={"target": (0.22, 1.02, 0.08), "pole": "hips", "palm": (0.2, -0.5, 0.2)},
        leg_l={"ankle": (0.12, 0.108, 0.2), "foot": (0.0, -0.5, 0.86), "plant": True},
        leg_r={"ankle": (-0.12, 0.108, -0.06), "foot": (0.0, -0.5, 0.86), "plant": True},
    )
    add("Accuse_Point_Hard", 1.05, [
        (0.00, n),
        (0.16, edit(n, spine=(-6.0, -8.0, 0.0), head=(-2.0, -6.0, 0.0), shift=(0.0, -0.04, 0.0),
                    arm_r={"target": (-0.28, 1.2, -0.02), "pole": "back"})),
        (0.32, edit(n, spine=(4.0, 4.0, 0.0), shift=(0.0, 0.04, 0.0),
                    arm_r={"target": (-0.1, 1.4, 0.22), "pole": "forward", "palm": (0.0, -0.2, 1.0)},
                    leg_l={"ankle": (0.12, 0.108, 0.1), "foot": (0.0, -0.5, 0.86), "plant": True})),
        (0.48, point),
        (0.62, edit(point, spine=(16.0, 14.0, 0.0), head=(8.0, 12.0, 0.0),
                    arm_r={"target": (0.0, 1.48, 0.5), "pole": "down", "palm": (0.0, -0.1, 1.0)})),
        (0.76, edit(point, spine=(10.0, 8.0, 0.0), arm_r={"target": (-0.04, 1.42, 0.42), "pole": "down"})),
        (0.88, edit(point, head=(6.0, 6.0, 0.0))),
        (1.00, edit(point, spine=(8.0, 6.0, 0.0), head=(4.0, 4.0, 0.0))),
    ])

    confront = edit(
        n, pelvis=(4.0, 0.0, 0.0), spine=(8.0, 0.0, 0.0), head=(0.0, 0.0, 0.0),
        shift=(0.0, 0.08, -0.02), clav_l=(2.0, 6.0), clav_r=(2.0, 6.0),
        arm_l={"target": (0.28, 1.16, 0.2), "pole": "out", "palm": (-0.6, -0.3, 0.4)},
        arm_r={"target": (-0.28, 1.16, 0.2), "pole": "out", "palm": (0.6, -0.3, 0.4)},
        leg_l={"ankle": (0.12, 0.108, 0.18), "foot": (0.0, -0.5, 0.86), "plant": True},
        leg_r={"ankle": (-0.11, 0.108, -0.02), "foot": (0.0, -0.5, 0.86), "plant": True},
    )
    add("Confront_Lean_In", 1.2, [
        (0.00, n),
        (0.18, edit(n, spine=(-4.0, 0.0, 0.0), shift=(0.0, -0.02, 0.0))),
        (0.36, edit(confront, spine=(2.0, 0.0, 0.0), shift=(0.0, 0.04, 0.0))),
        (0.52, confront),
        (0.68, edit(confront, spine=(12.0, 0.0, 0.0), shift=(0.0, 0.12, -0.03), head=(2.0, 0.0, 0.0))),
        (0.82, confront),
        (0.92, edit(confront, spine=(6.0, 0.0, 0.0))),
        (1.00, edit(confront, spine=(6.0, 0.0, 0.0), head=(1.0, 0.0, 0.0))),
    ])

    add("Refuse_Wave_Off", 0.98, [
        (0.00, n),
        (0.14, edit(n, head=(0.0, -8.0, 0.0), spine=(0.0, -6.0, 0.0),
                    arm_r={"target": (-0.05, 1.2, 0.16), "pole": "forward"})),
        (0.28, edit(n, spine=(2.0, 4.0, 0.0),
                    arm_r={"target": (-0.02, 1.38, 0.28), "pole": "forward", "palm": (0.0, -0.2, 1.0)})),
        (0.42, edit(n, spine=(0.0, 16.0, 0.0), head=(2.0, 18.0, 4.0),
                    arm_r={"target": (-0.28, 1.3, 0.12), "pole": "out", "palm": (0.2, -0.2, 0.6)})),
        (0.56, edit(n, spine=(0.0, 22.0, 0.0), head=(4.0, 28.0, 6.0),
                    arm_r={"target": (-0.5, 1.22, 0.0), "pole": "out", "palm": (0.4, -0.2, 0.2)})),
        (0.70, edit(n, spine=(2.0, 18.0, 0.0), head=(2.0, 24.0, 4.0),
                    arm_r={"target": (-0.42, 1.16, 0.04), "pole": "out"})),
        (0.84, edit(n, head=(0.0, 16.0, 0.0), spine=(0.0, 10.0, 0.0),
                    arm_r={"target": (-0.36, 1.08, 0.06), "pole": "down"})),
        (1.00, edit(n, head=(0.0, 12.0, 0.0), arm_r={"target": (-0.32, 1.02, 0.08), "pole": "down"})),
    ])

    stop = edit(
        n, pelvis=(-2.0, 0.0, 0.0), spine=(-6.0, 0.0, 0.0), head=(-2.0, 0.0, 0.0),
        arm_r={"target": (-0.06, 1.42, 0.4), "pole": "down", "palm": (0.0, -0.05, 1.0)},
        arm_l={"target": (0.22, 1.16, 0.16), "pole": "forward", "palm": (0.1, -0.3, 0.7)},
    )
    add("Interrupt_Hand_Stop", 0.92, [
        (0.00, edit(n, spine=(4.0, 0.0, 0.0))),
        (0.16, edit(n, spine=(6.0, 0.0, 0.0), head=(4.0, 0.0, 0.0),
                    arm_r={"target": (-0.2, 1.05, 0.08), "pole": "down"})),
        (0.32, edit(n, spine=(-2.0, 0.0, 0.0),
                    arm_r={"target": (-0.1, 1.28, 0.24), "pole": "forward", "palm": (0.0, 0.0, 1.0)})),
        (0.48, stop),
        (0.62, edit(stop, spine=(-8.0, 0.0, 0.0), arm_r={"target": (-0.04, 1.44, 0.44), "pole": "down", "palm": (0.0, 0.0, 1.0)})),
        (0.76, stop),
        (0.88, edit(stop, arm_r={"target": (-0.08, 1.38, 0.36), "pole": "down", "palm": (0.0, -0.1, 1.0)})),
        (1.00, edit(stop, spine=(-4.0, 0.0, 0.0))),
    ])

    def argue(yaw, lx, ly, lz, rx, ry, rz, shift_x):
        return edit(
            n, pelvis=(6.0, yaw * 0.3, 0.0), spine=(10.0, yaw, 0.0), head=(2.0, yaw * 0.6, 0.0),
            shift=(shift_x, 0.04, 0.0),
            arm_l={"target": (lx, ly, lz), "pole": "forward", "palm": (-0.2, -0.2, 1.0)},
            arm_r={"target": (rx, ry, rz), "pole": "forward", "palm": (0.2, -0.2, 1.0)},
        )

    add("Argue_Emphatic_Both", 1.35, [
        (0.00, n),
        (0.12, edit(n, spine=(-4.0, -6.0, 0.0),
                    arm_l={"target": (0.16, 1.05, 0.08), "pole": "down"},
                    arm_r={"target": (-0.16, 1.05, 0.08), "pole": "down"})),
        (0.28, argue(12, 0.12, 1.32, 0.28, -0.2, 1.18, 0.22, -0.03)),
        (0.42, argue(-14, 0.22, 1.2, 0.2, -0.08, 1.4, 0.32, 0.04)),
        (0.56, argue(16, 0.08, 1.42, 0.34, -0.22, 1.16, 0.18, -0.04)),
        (0.70, argue(-10, 0.2, 1.22, 0.26, -0.1, 1.36, 0.3, 0.03)),
        (0.84, argue(8, 0.14, 1.28, 0.24, -0.16, 1.26, 0.24, 0.0)),
        (1.00, argue(4, 0.16, 1.22, 0.2, -0.16, 1.22, 0.2, 0.0)),
    ])

    shrug = edit(
        n, clav_l=(12.0, 2.0), clav_r=(12.0, 2.0), head=(-6.0, 0.0, 10.0), spine=(-2.0, 0.0, 4.0),
        arm_l={"target": (0.36, 1.24, 0.14), "pole": "out", "palm": (0.15, 1.0, 0.2)},
        arm_r={"target": (-0.34, 1.22, 0.12), "pole": "out", "palm": (-0.15, 1.0, 0.2)},
    )
    add("Shrug_Frustrated", 1.15, [
        (0.00, n),
        (0.18, edit(n, head=(4.0, 0.0, 0.0), spine=(4.0, 0.0, 0.0))),
        (0.36, edit(shrug, clav_l=(6.0, 0.0), clav_r=(6.0, 0.0),
                    arm_l={"target": (0.32, 1.08, 0.1), "pole": "out", "palm": (0.1, 0.4, 0.2)},
                    arm_r={"target": (-0.32, 1.08, 0.1), "pole": "out", "palm": (-0.1, 0.4, 0.2)})),
        (0.52, shrug),
        (0.68, edit(shrug, head=(-8.0, 0.0, 14.0), clav_l=(14.0, 2.0), clav_r=(14.0, 2.0))),
        (0.82, edit(shrug, head=(-2.0, 0.0, 6.0), clav_l=(8.0, 0.0), clav_r=(8.0, 0.0))),
        (0.92, edit(n, head=(2.0, 0.0, 3.0), clav_l=(3.0, 0.0), clav_r=(3.0, 0.0),
                    arm_l={"target": (0.32, 1.05, 0.1), "pole": "down", "palm": (0.2, 0.3, 0.2)},
                    arm_r={"target": (-0.32, 1.02, 0.1), "pole": "down", "palm": (-0.2, 0.3, 0.2)})),
        (1.00, edit(n, head=(0.0, 0.0, 2.0))),
    ])

    crossed = edit(
        n, spine=(3.0, 0.0, 0.0), head=(6.0, 0.0, 0.0),
        arm_l={"target": (-0.06, 1.24, 0.22), "pole": "cross", "palm": (0.2, -0.7, 0.4)},
        arm_r={"target": (0.08, 1.16, 0.2), "pole": "cross", "palm": (-0.2, -0.7, 0.4)},
    )
    add("Arms_Crossed_Stern", 1.25, [
        (0.00, n),
        (0.16, edit(n, arm_l={"target": (0.2, 1.2, 0.16), "pole": "forward"},
                    arm_r={"target": (-0.2, 1.18, 0.14), "pole": "forward"})),
        (0.34, edit(n, arm_l={"target": (0.02, 1.28, 0.24), "pole": "cross"},
                    arm_r={"target": (-0.04, 1.22, 0.22), "pole": "cross"})),
        (0.50, crossed),
        (0.66, edit(crossed, head=(8.0, 0.0, 0.0), spine=(4.0, 0.0, 0.0))),
        (0.80, crossed),
        (0.90, edit(crossed, head=(5.0, 0.0, 0.0))),
        (1.00, edit(crossed, head=(4.0, 0.0, 0.0))),
    ])

    hips = edit(
        n, spine=(-6.0, 0.0, 0.0), pelvis=(-2.0, 0.0, 0.0), head=(-1.0, 0.0, 0.0),
        clav_l=(0.0, 4.0), clav_r=(0.0, 4.0),
        arm_l={"target": (0.2, 1.02, 0.06), "pole": "hips", "palm": (-0.85, -0.2, 0.15)},
        arm_r={"target": (-0.2, 1.02, 0.06), "pole": "hips", "palm": (0.85, -0.2, 0.15)},
    )
    add("Hands_On_Hips_Command", 1.1, [
        (0.00, n),
        (0.16, edit(n, spine=(-2.0, 0.0, 0.0),
                    arm_l={"target": (0.36, 0.92, -0.02), "pole": "down"},
                    arm_r={"target": (-0.36, 0.92, -0.02), "pole": "down"})),
        (0.32, edit(n, spine=(-4.0, 0.0, 0.0),
                    arm_l={"target": (0.34, 1.2, 0.16), "pole": "out"},
                    arm_r={"target": (-0.34, 1.2, 0.16), "pole": "out"})),
        (0.48, hips),
        (0.62, edit(hips, spine=(-10.0, 0.0, 0.0), head=(-3.0, 0.0, 0.0))),
        (0.76, hips),
        (0.88, edit(n, spine=(-4.0, 0.0, 0.0),
                    arm_l={"target": (0.26, 1.08, 0.1), "pole": "out", "palm": (-0.7, -0.3, 0.2)},
                    arm_r={"target": (-0.26, 1.08, 0.1), "pole": "out", "palm": (0.7, -0.3, 0.2)})),
        (1.00, edit(hips, spine=(-3.0, 0.0, 0.0), head=(0.0, 0.0, 0.0))),
    ])

    pace = []
    yaws = (0, 16, -8, 20, -14, 10, -6, 4, 0)
    for i, yaw in enumerate(yaws):
        t = i / (len(yaws) - 1)
        phase = t
        spec = _gait(skel, phase * 0.5, 0.12, 0.05, 0.08, 2.0, 3.0)
        spec["root_yaw"] = yaw
        spec["root_forward"] = 0.08 * math.sin(t * math.pi)
        spec["root_left"] = 0.06 * math.sin(t * math.tau)
        spec["head"] = (2.0, yaw * 0.4, 0.0)
        if i % 2 == 0:
            spec["arm_r"] = {"target": (-0.12, 1.28, 0.2), "pole": "forward", "palm": (0.2, -0.2, 0.8)}
        else:
            spec["arm_l"] = {"target": (0.12, 1.26, 0.18), "pole": "forward", "palm": (-0.2, -0.2, 0.8)}
        pace.append((t, spec))
    add("Pace_Anxious_Turn", 2.4, pace)

    add("Snap_Look_Back", 0.86, [
        (0.00, n),
        (0.12, edit(n, head=(0.0, -10.0, 0.0), spine=(0.0, -6.0, 0.0))),
        (0.26, edit(n, head=(0.0, 20.0, 0.0), spine=(0.0, 8.0, 0.0))),
        (0.40, edit(n, head=(-2.0, 55.0, 0.0), spine=(2.0, 24.0, 4.0), neck=(0.0, 10.0, 0.0),
                    clav_r=(4.0, 0.0))),
        (0.52, edit(n, head=(-4.0, 78.0, 2.0), spine=(4.0, 32.0, 6.0), neck=(0.0, 14.0, 0.0),
                    arm_r={"target": (-0.18, 1.15, -0.08), "pole": "back"})),
        (0.66, edit(n, head=(0.0, 62.0, 0.0), spine=(2.0, 22.0, 3.0))),
        (0.82, edit(n, head=(2.0, 36.0, 0.0), spine=(0.0, 12.0, 0.0))),
        (1.00, edit(n, head=(0.0, 18.0, 0.0), spine=(0.0, 6.0, 0.0))),
    ])

    startle = edit(
        n, pelvis=(-14.0, 0.0, 0.0), spine=(-8.0, 0.0, 0.0), head=(-10.0, 0.0, 0.0),
        shift=(0.0, -0.1, -0.02),
        arm_l={"target": (0.16, 1.5, 0.18), "pole": "up", "palm": (-0.2, 0.2, 1.0)},
        arm_r={"target": (-0.14, 1.52, 0.16), "pole": "up", "palm": (0.2, 0.2, 1.0)},
        leg_l={"ankle": (0.12, 0.108, 0.02), "foot": (0.0, -0.5, 0.86), "plant": True},
        leg_r={"ankle": (-0.14, 0.108, -0.22), "foot": (0.0, -0.5, 0.86), "plant": True},
    )
    add("Startle_Recoil_Back", 0.9, [
        (0.00, n),
        (0.14, edit(n, head=(2.0, 0.0, 0.0), spine=(2.0, 0.0, 0.0))),
        (0.28, edit(startle, pelvis=(-6.0, 0.0, 0.0), shift=(0.0, -0.04, 0.0),
                    arm_l={"target": (0.18, 1.25, 0.16), "pole": "forward"},
                    arm_r={"target": (-0.16, 1.28, 0.14), "pole": "forward"})),
        (0.44, startle),
        (0.58, edit(startle, pelvis=(-18.0, 0.0, 0.0), head=(-14.0, 0.0, 0.0), shift=(0.0, -0.14, 0.0))),
        (0.74, edit(startle, pelvis=(-8.0, 0.0, 0.0), head=(-4.0, 0.0, 0.0))),
        (0.88, edit(n, pelvis=(-3.0, 0.0, 0.0), head=(-2.0, 0.0, 0.0),
                    arm_l={"target": (0.24, 1.2, 0.12), "pole": "forward"},
                    arm_r={"target": (-0.22, 1.18, 0.1), "pole": "forward"})),
        (1.00, edit(n, head=(-1.0, 0.0, 0.0))),
    ])

    flinch = edit(
        n, pelvis=(8.0, 0.0, 0.0), spine=(22.0, 0.0, 0.0), neck=(8.0, 0.0, 0.0), head=(16.0, 0.0, 0.0),
        shift=(0.0, 0.02, -0.1),
        arm_l={"target": (0.12, 1.5, 0.1), "pole": "up", "palm": (0.0, -0.2, 0.8)},
        arm_r={"target": (-0.1, 1.52, 0.08), "pole": "up", "palm": (0.0, -0.2, 0.8)},
        clav_l=(10.0, 0.0), clav_r=(10.0, 0.0),
    )
    add("Flinch_Explosion_Flash", 0.7, [
        (0.00, n),
        (0.14, edit(n, head=(-4.0, 0.0, 0.0), spine=(-2.0, 0.0, 0.0))),
        (0.30, flinch),
        (0.44, edit(flinch, spine=(28.0, 4.0, 0.0), head=(20.0, 0.0, 0.0), shift=(0.0, 0.0, -0.12))),
        (0.58, edit(flinch, spine=(16.0, 0.0, 0.0), head=(10.0, 0.0, 0.0), shift=(0.0, 0.0, -0.06))),
        (0.74, edit(n, spine=(8.0, 0.0, 0.0), head=(6.0, 0.0, 0.0),
                    arm_l={"target": (0.2, 1.25, 0.12), "pole": "up"},
                    arm_r={"target": (-0.18, 1.28, 0.1), "pole": "up"})),
        (0.88, edit(n, spine=(4.0, 0.0, 0.0), head=(3.0, 0.0, 0.0))),
        (1.00, edit(n, head=(2.0, 0.0, 0.0))),
    ])

    turn = []
    yaws = (0, -12, -35, -60, -90, -115, -135, -148, -155, -158)
    for i, yaw in enumerate(yaws):
        t = i / (len(yaws) - 1)
        spec = _gait(skel, t, 0.22, 0.08, 0.12, 4.0, 6.0)
        spec["root_yaw"] = yaw
        spec["root_forward"] = 1.15 * t
        spec["head"] = (8.0, 6.0, 0.0)
        spec["spine"] = (8.0, spec["spine"][1], spec["spine"][2])
        spec["clav_l"] = (-4.0, 0.0)
        spec["clav_r"] = (-4.0, 0.0)
        turn.append((t, spec))
    add("Turn_Walk_Away_Dismiss", 1.7, turn)

    lean = edit(
        n, pelvis=(4.0, 0.0, 0.0), spine=(22.0, 0.0, 0.0), neck=(4.0, 0.0, 0.0), head=(6.0, 0.0, 0.0),
        shift=(0.0, 0.1, -0.02),
        arm_l={"target": (0.18, 1.08, 0.34), "pole": "down", "palm": (0.1, -1.0, 0.15)},
        arm_r={"target": (-0.16, 1.06, 0.34), "pole": "down", "palm": (-0.1, -1.0, 0.15)},
    )
    add("Lean_Console_Inspect", 1.8, [
        (0.00, n),
        (0.12, edit(n, spine=(6.0, 0.0, 0.0), shift=(0.0, 0.04, 0.0))),
        (0.28, lean),
        (0.42, edit(lean, head=(4.0, -14.0, 0.0), spine=(22.0, -6.0, 0.0))),
        (0.56, edit(lean, head=(8.0, 10.0, 0.0), spine=(24.0, 6.0, 0.0))),
        (0.70, edit(lean, head=(2.0, -8.0, 0.0))),
        (0.84, edit(lean, head=(6.0, 12.0, 0.0), spine=(20.0, 4.0, 0.0))),
        (1.00, edit(lean, head=(4.0, 0.0, 0.0), spine=(16.0, 0.0, 0.0))),
    ])

    back = edit(n, spine=(-5.0, 0.0, 0.0), head=(-2.0, 0.0, 0.0),
                arm_l={"target": (0.24, 1.04, -0.04), "pole": "back"},
                arm_r={"target": (-0.24, 1.04, -0.04), "pole": "back"})
    typing = edit(
        n, spine=(14.0, 0.0, 0.0), head=(10.0, 0.0, 0.0), shift=(0.0, 0.08, -0.02),
        arm_l={"target": (0.1, 1.14, 0.38), "pole": "down", "palm": (0.1, -0.9, 0.25)},
        arm_r={"target": (-0.1, 1.12, 0.4), "pole": "down", "palm": (-0.1, -0.9, 0.25)},
    )
    operate = [
        (0.00, n),
        (0.08, back),
        (0.16, edit(back, spine=(-8.0, 0.0, 0.0))),
        (0.24, edit(n, spine=(2.0, 0.0, 0.0),
                    arm_l={"target": (0.18, 1.12, 0.14), "pole": "forward"},
                    arm_r={"target": (-0.18, 1.1, 0.14), "pole": "forward"})),
        (0.34, typing),
        (0.44, edit(typing, arm_l={"target": (0.16, 1.08, 0.44), "pole": "down", "palm": (0.1, -0.9, 0.2)},
                    arm_r={"target": (-0.02, 1.18, 0.32), "pole": "down"})),
        (0.54, edit(typing, arm_l={"target": (0.02, 1.2, 0.32), "pole": "down"},
                    arm_r={"target": (-0.16, 1.06, 0.46), "pole": "down"})),
        (0.64, edit(typing, arm_l={"target": (0.14, 1.06, 0.46), "pole": "down"},
                    arm_r={"target": (-0.02, 1.18, 0.34), "pole": "down"})),
        (0.74, edit(typing, arm_l={"target": (0.02, 1.2, 0.34), "pole": "down"},
                    arm_r={"target": (-0.14, 1.06, 0.42), "pole": "down"})),
        (0.84, edit(typing, spine=(8.0, 0.0, 0.0),
                    arm_l={"target": (0.12, 1.12, 0.26), "pole": "down"},
                    arm_r={"target": (-0.12, 1.1, 0.24), "pole": "down"})),
        (0.92, edit(n, spine=(6.0, 0.0, 0.0), head=(4.0, 0.0, 0.0),
                    arm_l={"target": (0.16, 1.08, 0.18), "pole": "down"},
                    arm_r={"target": (-0.14, 1.06, 0.16), "pole": "down"})),
        (1.00, edit(n, spine=(3.0, 0.0, 0.0), head=(2.0, 0.0, 0.0))),
    ]
    add("Console_Operate_Urgent", 1.15, operate)

    press = edit(
        n, spine=(8.0, 2.0, 0.0), head=(6.0, 0.0, 0.0), shift=(0.0, 0.05, 0.0),
        arm_r={"target": (-0.02, 1.18, 0.42), "pole": "down", "palm": (0.0, -0.35, 1.0)},
        arm_l={"target": (0.28, 1.0, 0.08), "pole": "down", "palm": (0.2, -0.5, 0.2)},
    )
    add("Button_Press_Hard", 1.25, [
        (0.00, n),
        (0.14, edit(n, spine=(-3.0, 0.0, 0.0), arm_r={"target": (-0.22, 1.08, 0.06), "pole": "down"})),
        (0.30, edit(n, spine=(4.0, 0.0, 0.0), arm_r={"target": (-0.08, 1.28, 0.24), "pole": "forward", "palm": (0.0, -0.2, 1.0)})),
        (0.46, press),
        (0.60, edit(press, arm_r={"target": (-0.01, 1.16, 0.46), "pole": "down", "palm": (0.0, -0.45, 0.9)}, spine=(12.0, 2.0, 0.0))),
        (0.74, edit(press, arm_r={"target": (-0.02, 1.16, 0.44), "pole": "down", "palm": (0.0, -0.4, 0.95)})),
        (0.88, edit(press, spine=(6.0, 0.0, 0.0), arm_r={"target": (-0.06, 1.2, 0.32), "pole": "forward"})),
        (1.00, edit(n, spine=(4.0, 0.0, 0.0), arm_r={"target": (-0.16, 1.08, 0.18), "pole": "down"})),
    ])

    add("Panel_Open_Pull", 1.3, [
        (0.00, n),
        (0.14, edit(n, spine=(6.0, 0.0, 0.0),
                    arm_l={"target": (0.1, 1.2, 0.22), "pole": "forward"},
                    arm_r={"target": (-0.02, 1.22, 0.24), "pole": "forward"})),
        (0.28, edit(n, spine=(8.0, -4.0, 0.0), head=(4.0, -4.0, 0.0),
                    arm_l={"target": (0.06, 1.24, 0.34), "pole": "down", "palm": (0.0, -0.3, 1.0)},
                    arm_r={"target": (-0.02, 1.26, 0.36), "pole": "down", "palm": (0.0, -0.3, 1.0)})),
        (0.44, edit(n, spine=(6.0, -8.0, 0.0),
                    arm_l={"target": (0.16, 1.2, 0.28), "pole": "out"},
                    arm_r={"target": (0.02, 1.22, 0.3), "pole": "forward"})),
        (0.58, edit(n, spine=(4.0, -14.0, 0.0), shift=(0.04, -0.02, 0.0),
                    arm_l={"target": (0.32, 1.16, 0.14), "pole": "out", "palm": (-0.2, -0.4, 0.6)},
                    arm_r={"target": (0.18, 1.18, 0.16), "pole": "out"})),
        (0.72, edit(n, spine=(2.0, -10.0, 0.0),
                    arm_l={"target": (0.38, 1.14, 0.08), "pole": "out"},
                    arm_r={"target": (0.22, 1.16, 0.1), "pole": "out"})),
        (0.86, edit(n, head=(2.0, -6.0, 0.0),
                    arm_l={"target": (0.3, 1.1, 0.12), "pole": "down"},
                    arm_r={"target": (0.08, 1.12, 0.16), "pole": "down"})),
        (1.00, edit(n, arm_l={"target": (0.28, 1.04, 0.12), "pole": "down"})),
    ])

    grip = edit(
        n, spine=(12.0, 0.0, 0.0), head=(8.0, 0.0, 0.0), shift=(0.0, 0.08, 0.0),
        arm_l={"target": (0.1, 1.2, 0.36), "pole": "down", "palm": (0.15, -0.2, 1.0)},
        arm_r={"target": (-0.1, 1.18, 0.38), "pole": "down", "palm": (-0.15, -0.2, 1.0)},
    )
    yank = edit(
        n, pelvis=(-10.0, 0.0, 0.0), spine=(-6.0, 0.0, 0.0), head=(-2.0, 0.0, 0.0),
        shift=(0.0, -0.06, 0.0),
        arm_l={"target": (0.12, 1.22, 0.14), "pole": "down", "palm": (0.2, -0.3, 0.7)},
        arm_r={"target": (-0.12, 1.2, 0.12), "pole": "down", "palm": (-0.2, -0.3, 0.7)},
    )
    add("Module_Detach_Pull", 1.22, [
        (0.00, n),
        (0.16, edit(n, spine=(-4.0, 0.0, 0.0))),
        (0.32, grip),
        (0.46, edit(grip, spine=(16.0, 0.0, 0.0),
                    arm_l={"target": (0.08, 1.18, 0.42), "pole": "down"},
                    arm_r={"target": (-0.08, 1.16, 0.44), "pole": "down"})),
        (0.60, yank),
        (0.74, edit(yank, pelvis=(-14.0, 0.0, 0.0), shift=(0.0, -0.1, 0.0),
                    arm_l={"target": (0.14, 1.24, 0.08), "pole": "down"},
                    arm_r={"target": (-0.14, 1.22, 0.06), "pole": "down"})),
        (0.88, edit(n, spine=(-2.0, 0.0, 0.0),
                    arm_l={"target": (0.16, 1.16, 0.16), "pole": "down"},
                    arm_r={"target": (-0.16, 1.14, 0.14), "pole": "down"})),
        (1.00, edit(n, arm_l={"target": (0.22, 1.08, 0.14), "pole": "down"},
                    arm_r={"target": (-0.2, 1.06, 0.12), "pole": "down"})),
    ])

    push = edit(
        n, pelvis=(6.0, 0.0, 0.0), spine=(16.0, 0.0, 0.0), head=(6.0, 0.0, 0.0), shift=(0.0, 0.1, -0.03),
        arm_l={"target": (0.1, 1.16, 0.44), "pole": "down", "palm": (0.1, -0.25, 1.0)},
        arm_r={"target": (-0.1, 1.14, 0.46), "pole": "down", "palm": (-0.1, -0.25, 1.0)},
    )
    add("Module_Install_Lock", 1.28, [
        (0.00, n),
        (0.14, edit(n, spine=(-3.0, 0.0, 0.0))),
        (0.30, edit(grip, arm_l={"target": (0.1, 1.22, 0.28), "pole": "down"},
                    arm_r={"target": (-0.1, 1.2, 0.3), "pole": "down"})),
        (0.46, push),
        (0.60, edit(push, spine=(22.0, 0.0, 0.0), shift=(0.0, 0.14, -0.04),
                    arm_l={"target": (0.08, 1.14, 0.5), "pole": "down", "palm": (0.05, -0.3, 1.0)},
                    arm_r={"target": (-0.08, 1.12, 0.52), "pole": "down", "palm": (-0.05, -0.3, 1.0)})),
        (0.74, edit(n, pelvis=(-8.0, 0.0, 0.0), spine=(-4.0, 0.0, 0.0), shift=(0.0, -0.02, 0.0),
                    arm_l={"target": (0.12, 1.2, 0.22), "pole": "down"},
                    arm_r={"target": (-0.12, 1.18, 0.2), "pole": "down"})),
        (0.88, edit(n, spine=(4.0, 0.0, 0.0), head=(2.0, 0.0, 0.0),
                    arm_l={"target": (0.14, 1.16, 0.26), "pole": "down"},
                    arm_r={"target": (-0.12, 1.14, 0.24), "pole": "down"})),
        (1.00, edit(n, head=(1.0, 0.0, 0.0))),
    ])

    repair = edit(
        n, pelvis=(6.0, 0.0, 0.0), spine=(30.0, 4.0, 0.0), neck=(6.0, 0.0, 0.0), head=(12.0, 2.0, 0.0),
        shift=(0.0, 0.06, -0.08),
        arm_l={"target": (0.16, 0.98, 0.3), "pole": "down", "palm": (0.1, -0.7, 0.4)},
        arm_r={"target": (-0.1, 0.96, 0.32), "pole": "down", "palm": (-0.1, -0.7, 0.4)},
    )
    add("Repair_Bend_Work", 1.7, [
        (0.00, n),
        (0.14, edit(n, spine=(8.0, 0.0, 0.0))),
        (0.30, repair),
        (0.44, edit(repair, head=(14.0, -6.0, 0.0),
                    arm_r={"target": (-0.04, 0.94, 0.34), "pole": "down"})),
        (0.58, edit(repair, spine=(26.0, -4.0, 0.0), head=(10.0, 8.0, 0.0),
                    arm_l={"target": (0.2, 1.0, 0.26), "pole": "down"})),
        (0.72, edit(repair, arm_r={"target": (-0.14, 0.92, 0.3), "pole": "down"},
                    arm_l={"target": (0.12, 0.98, 0.34), "pole": "down"})),
        (0.86, edit(repair, spine=(22.0, 0.0, 0.0), head=(8.0, 0.0, 0.0))),
        (1.00, edit(n, spine=(12.0, 0.0, 0.0), head=(6.0, 0.0, 0.0), shift=(0.0, 0.04, -0.03))),
    ])

    offer = edit(
        n, spine=(4.0, -6.0, 0.0), head=(0.0, -4.0, 0.0), shift=(0.02, 0.08, 0.0),
        arm_r={"target": (0.02, 1.32, 0.46), "pole": "down", "palm": (0.1, 0.85, 0.35)},
        arm_l={"target": (0.26, 1.02, 0.02), "pole": "back", "palm": (0.2, -0.4, 0.1)},
        leg_l={"ankle": (0.12, 0.108, 0.16), "foot": (0.0, -0.5, 0.86), "plant": True},
    )
    add("Handoff_Offer_Firm", 1.15, [
        (0.00, n),
        (0.16, edit(n, spine=(-3.0, 0.0, 0.0), arm_r={"target": (-0.2, 1.08, 0.08), "pole": "down"})),
        (0.34, edit(n, arm_r={"target": (-0.08, 1.24, 0.26), "pole": "forward", "palm": (0.0, 0.4, 0.8)})),
        (0.50, offer),
        (0.66, edit(offer, arm_r={"target": (0.04, 1.34, 0.5), "pole": "down", "palm": (0.05, 1.0, 0.25)}, spine=(6.0, -8.0, 0.0))),
        (0.80, offer),
        (0.90, edit(offer, arm_r={"target": (0.0, 1.3, 0.42), "pole": "down", "palm": (0.1, 0.7, 0.4)})),
        (1.00, edit(offer, spine=(3.0, -4.0, 0.0))),
    ])

    add("Handoff_Grab_Pull", 1.12, [
        (0.00, n),
        (0.14, edit(n, spine=(-4.0, 0.0, 0.0), shift=(0.0, -0.02, 0.0))),
        (0.30, edit(n, spine=(8.0, 0.0, 0.0), shift=(0.0, 0.08, 0.0),
                    arm_l={"target": (0.08, 1.28, 0.4), "pole": "forward", "palm": (0.0, -0.2, 1.0)},
                    arm_r={"target": (-0.06, 1.26, 0.42), "pole": "forward", "palm": (0.0, -0.2, 1.0)},
                    leg_l={"ankle": (0.12, 0.108, 0.14), "foot": (0.0, -0.5, 0.86), "plant": True})),
        (0.46, edit(n, spine=(12.0, 0.0, 0.0),
                    arm_l={"target": (0.06, 1.24, 0.48), "pole": "down"},
                    arm_r={"target": (-0.04, 1.22, 0.5), "pole": "down"})),
        (0.60, edit(n, pelvis=(-8.0, 0.0, 0.0), spine=(-4.0, 0.0, 0.0), shift=(0.0, -0.04, 0.0),
                    arm_l={"target": (0.1, 1.26, 0.2), "pole": "down", "palm": (0.3, -0.2, 0.6)},
                    arm_r={"target": (-0.08, 1.24, 0.18), "pole": "down", "palm": (-0.3, -0.2, 0.6)})),
        (0.74, edit(n, pelvis=(-12.0, 0.0, 0.0), shift=(0.0, -0.08, 0.0),
                    arm_l={"target": (0.12, 1.28, 0.12), "pole": "down"},
                    arm_r={"target": (-0.1, 1.26, 0.1), "pole": "down"})),
        (0.88, edit(n, spine=(-2.0, 0.0, 0.0),
                    arm_l={"target": (0.14, 1.22, 0.16), "pole": "down"},
                    arm_r={"target": (-0.12, 1.2, 0.14), "pole": "down"})),
        (1.00, edit(n, arm_l={"target": (0.16, 1.16, 0.16), "pole": "down"},
                    arm_r={"target": (-0.14, 1.14, 0.14), "pole": "down"})),
    ])

    add("Inspect_Prop_Close", 1.4, [
        (0.00, n),
        (0.14, edit(n, arm_r={"target": (-0.1, 1.12, 0.2), "pole": "forward"},
                    arm_l={"target": (0.12, 1.1, 0.18), "pole": "forward"})),
        (0.30, edit(n, spine=(6.0, 0.0, 0.0), head=(10.0, 0.0, 0.0),
                    arm_l={"target": (0.04, 1.28, 0.28), "pole": "down", "palm": (0.2, 0.4, 0.6)},
                    arm_r={"target": (-0.02, 1.26, 0.3), "pole": "down", "palm": (-0.2, 0.4, 0.6)})),
        (0.46, edit(n, head=(8.0, 0.0, 0.0),
                    arm_l={"target": (0.03, 1.46, 0.22), "pole": "up", "palm": (0.1, 0.2, 0.8)},
                    arm_r={"target": (-0.03, 1.42, 0.24), "pole": "up", "palm": (-0.1, 0.2, 0.8)})),
        (0.60, edit(n, head=(12.0, -8.0, 6.0),
                    arm_l={"target": (0.02, 1.5, 0.18), "pole": "up"},
                    arm_r={"target": (0.02, 1.4, 0.2), "pole": "up"})),
        (0.74, edit(n, head=(6.0, 10.0, -4.0),
                    arm_l={"target": (0.06, 1.44, 0.2), "pole": "up"},
                    arm_r={"target": (-0.06, 1.5, 0.16), "pole": "up"})),
        (0.88, edit(n, head=(4.0, 0.0, 0.0),
                    arm_l={"target": (0.05, 1.36, 0.22), "pole": "forward"},
                    arm_r={"target": (-0.04, 1.34, 0.22), "pole": "forward"})),
        (1.00, edit(n, head=(2.0, 0.0, 0.0),
                    arm_l={"target": (0.08, 1.24, 0.2), "pole": "down"},
                    arm_r={"target": (-0.06, 1.22, 0.2), "pole": "down"})),
    ])

    add("Check_Window_Urgent", 1.35, [
        (0.00, n),
        (0.16, edit(n, shift=(0.0, 0.06, 0.0),
                    leg_l={"ankle": (0.12, 0.108, 0.16), "foot": (0.0, -0.5, 0.86), "plant": True})),
        (0.32, edit(n, spine=(12.0, 0.0, 0.0), head=(2.0, -8.0, 0.0), shift=(0.0, 0.1, 0.0),
                    arm_r={"target": (-0.06, 1.55, 0.2), "pole": "up", "palm": (0.0, -0.7, 0.4)},
                    arm_l={"target": (0.24, 1.22, 0.3), "pole": "forward", "palm": (0.1, -0.5, 0.7)})),
        (0.48, edit(n, spine=(14.0, -6.0, 0.0), head=(0.0, -16.0, 0.0),
                    arm_r={"target": (-0.04, 1.58, 0.22), "pole": "up", "palm": (0.0, -0.85, 0.3)},
                    arm_l={"target": (0.26, 1.2, 0.32), "pole": "down"})),
        (0.64, edit(n, spine=(12.0, 8.0, 0.0), head=(2.0, 14.0, 0.0),
                    arm_r={"target": (-0.1, 1.52, 0.18), "pole": "up"})),
        (0.78, edit(n, head=(0.0, -6.0, 0.0), spine=(10.0, 0.0, 0.0),
                    arm_r={"target": (-0.08, 1.5, 0.2), "pole": "up"})),
        (0.90, edit(n, spine=(8.0, 0.0, 0.0), head=(2.0, 0.0, 0.0),
                    arm_r={"target": (-0.12, 1.36, 0.16), "pole": "forward"},
                    arm_l={"target": (0.22, 1.1, 0.18), "pole": "down"})),
        (1.00, edit(n, head=(0.0, 0.0, 0.0), spine=(4.0, 0.0, 0.0))),
    ])

    add("Look_Up_Array_Track", 1.8, [
        (0.00, n),
        (0.14, edit(n, head=(-6.0, 0.0, 0.0), spine=(-2.0, 0.0, 0.0))),
        (0.30, edit(n, head=(-18.0, -8.0, 0.0), neck=(-6.0, 0.0, 0.0), spine=(-8.0, 0.0, 0.0),
                    arm_l={"target": (0.32, 1.08, 0.08), "pole": "down"},
                    arm_r={"target": (-0.3, 1.12, 0.06), "pole": "down"})),
        (0.46, edit(n, head=(-28.0, 6.0, 0.0), neck=(-8.0, 2.0, 0.0), spine=(-12.0, 2.0, 0.0))),
        (0.62, edit(n, head=(-26.0, 16.0, 0.0), neck=(-8.0, 4.0, 0.0), spine=(-10.0, 4.0, 0.0))),
        (0.76, edit(n, head=(-30.0, -10.0, 0.0), neck=(-10.0, -2.0, 0.0), spine=(-14.0, -2.0, 0.0))),
        (0.88, edit(n, head=(-22.0, 0.0, 0.0), neck=(-6.0, 0.0, 0.0), spine=(-8.0, 0.0, 0.0))),
        (1.00, edit(n, head=(-12.0, 0.0, 0.0), spine=(-4.0, 0.0, 0.0))),
    ])

    tense = edit(
        n, clav_l=(8.0, 0.0), clav_r=(8.0, 0.0), spine=(-2.0, 0.0, 0.0), head=(-2.0, 0.0, 0.0),
        arm_l={"target": (0.28, 1.08, 0.12), "pole": "out", "palm": (0.2, -0.2, 0.3)},
        arm_r={"target": (-0.28, 1.08, 0.12), "pole": "out", "palm": (-0.2, -0.2, 0.3)},
    )
    release = edit(
        n, clav_l=(-2.0, 0.0), clav_r=(-2.0, 0.0), spine=(10.0, 0.0, 0.0), neck=(4.0, 0.0, 0.0),
        head=(12.0, 0.0, 0.0), shift=(0.0, 0.0, -0.03),
        arm_l={"target": (0.34, 0.9, 0.08), "pole": "down", "palm": (0.2, -0.7, 0.1)},
        arm_r={"target": (-0.34, 0.9, 0.08), "pole": "down", "palm": (-0.2, -0.7, 0.1)},
    )
    add("Exhale_Relief_Slump", 1.45, [
        (0.00, tense),
        (0.16, edit(tense, clav_l=(10.0, 0.0), clav_r=(10.0, 0.0), spine=(-4.0, 0.0, 0.0))),
        (0.34, edit(tense, head=(2.0, 0.0, 0.0))),
        (0.50, release),
        (0.66, edit(release, spine=(14.0, 0.0, 0.0), head=(16.0, 0.0, 0.0), shift=(0.0, 0.0, -0.04))),
        (0.80, edit(n, spine=(6.0, 0.0, 0.0), head=(6.0, 0.0, 0.0),
                    arm_l={"target": (0.32, 0.96, 0.1), "pole": "down"},
                    arm_r={"target": (-0.32, 0.96, 0.1), "pole": "down"})),
        (0.90, edit(n, spine=(4.0, 0.0, 0.0), head=(3.0, 0.0, 0.0))),
        (1.00, edit(n, head=(2.0, 0.0, 0.0), spine=(3.0, 0.0, 0.0))),
    ])

    def nod(pitch, chest=-4.0):
        return edit(n, head=(pitch, 0.0, 0.0), neck=(pitch * 0.35, 0.0, 0.0), spine=(chest, 0.0, 0.0),
                    pelvis=(-1.0, 0.0, 0.0))

    add("Nod_Firm_Confirm", 1.2, [
        (0.00, nod(0.0, 0.0)),
        (0.14, nod(-8.0, -2.0)),
        (0.28, nod(8.0)),
        (0.40, nod(4.0)),
        (0.52, nod(20.0, -6.0)),
        (0.64, nod(20.0, -6.0)),
        (0.76, nod(2.0, -2.0)),
        (0.88, nod(12.0, -4.0)),
        (1.00, nod(5.0, -3.0)),
    ])

    breath = edit(
        n, pelvis=(4.0, 0.0, 0.0), spine=(26.0, 0.0, 0.0), neck=(6.0, 0.0, 0.0), head=(14.0, 0.0, 0.0),
        shift=(0.04, 0.04, -0.1),
        arm_l={"target": (0.22, 0.78, 0.26), "pole": "down", "palm": (0.2, -0.8, 0.2)},
        arm_r={"target": (-0.22, 0.78, 0.26), "pole": "down", "palm": (-0.2, -0.8, 0.2)},
        leg_l={"ankle": (0.16, 0.108, 0.04), "foot": (0.0, -0.5, 0.86), "plant": True},
        leg_r={"ankle": (-0.16, 0.108, 0.04), "foot": (0.0, -0.5, 0.86), "plant": True},
    )
    add("Catch_Breath_Brace", 1.6, [
        (0.00, edit(n, spine=(8.0, 0.0, 0.0), head=(4.0, 0.0, 0.0))),
        (0.16, breath),
        (0.30, edit(breath, spine=(32.0, 0.0, 0.0), head=(18.0, 0.0, 0.0), shift=(0.04, 0.04, -0.12))),
        (0.44, edit(breath, spine=(18.0, 0.0, 0.0), head=(8.0, 0.0, 0.0), shift=(0.04, 0.04, -0.06))),
        (0.58, edit(breath, spine=(30.0, 0.0, 0.0), head=(16.0, 0.0, 0.0))),
        (0.72, edit(breath, spine=(20.0, 0.0, 0.0), head=(10.0, 0.0, 0.0))),
        (0.86, edit(n, spine=(14.0, 0.0, 0.0), head=(8.0, 0.0, 0.0), shift=(0.0, 0.02, -0.04),
                    arm_l={"target": (0.24, 0.9, 0.18), "pole": "down"},
                    arm_r={"target": (-0.24, 0.9, 0.18), "pole": "down"})),
        (1.00, edit(n, spine=(6.0, 0.0, 0.0), head=(4.0, 0.0, 0.0))),
    ])

    names = [clip["name"] for clip in clips]
    if names != list(CLIP_NAMES):
        raise RuntimeError(f"clip list mismatch: {names}")
    return clips


def _feature(skel, pose):
    values = []
    for name in FEATURE_BONES:
        i = skel.index[name]
        delta = qmul(qconj(skel.rest_r[i]), pose.r[i])
        axis, deg = axis_angle(delta)
        values.extend((axis[0] * deg / 40.0, axis[1] * deg / 40.0, axis[2] * deg / 40.0))
    i = skel.index["pelvis"]
    delta_t = vsub(pose.t[i], skel.rest_t[i])
    values.extend(component / 0.15 for component in delta_t)
    return values


def _lerp_list(a, b, u):
    return [a[i] + (b[i] - a[i]) * u for i in range(len(a))]


def _sample_feature(skel, axes, clip, t):
    keys = clip["keys"]
    if t <= keys[0][0]:
        return _feature(skel, solve_spec(skel, axes, keys[0][1]))
    if t >= keys[-1][0]:
        return _feature(skel, solve_spec(skel, axes, keys[-1][1]))
    for (t0, s0), (t1, s1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            # Sample the nearest authored pose. Interpolation of specs is not
            # required for the distinctness test; the authored knots are the keys.
            spec = s0 if u < 0.5 else s1
            return _feature(skel, solve_spec(skel, axes, spec))
    return _feature(skel, solve_spec(skel, axes, keys[-1][1]))


def _dist(a, b):
    return math.sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


def _cosine(a, b):
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (da * db)


def validate_poses(skel: Skeleton, axes: Axes, clips: list[dict]) -> list[str]:
    problems = []
    curves = []
    solved = []
    for clip in clips:
        poses = []
        features = []
        for t, spec in clip["keys"]:
            pose = solve_spec(skel, axes, spec)
            poses.append(pose)
            features.append(_feature(skel, pose))
            problems.extend(_pose_problems(skel, clip["name"], t, pose, spec))
        if clip["name"] == "Sprint_Urgent_Upright":
            problems.extend(_sprint_upright(skel, axes, clip, poses))
        if clip["loop"]:
            if _dist(features[0], features[-1]) > 0.25:
                problems.append(f"{clip['name']} loop pose does not close")
            if _dist(features[0], features[len(features) // 2]) < 0.35:
                problems.append(f"{clip['name']} loop has no cycle")
        else:
            count = len(features)
            wind = features[max(1, count // 4)]
            accent = features[min(count - 2, max(2, int(round(count * 0.55))))]
            end = features[-1]
            if _dist(features[0], accent) < 0.18:
                problems.append(f"{clip['name']} accent is too close to the start pose")
            if _dist(wind, accent) < 0.1:
                problems.append(f"{clip['name']} has no separation between anticipation and accent")
            if _dist(accent, end) < 0.08:
                problems.append(f"{clip['name']} does not settle off the accent")
        flat = []
        for i in range(8):
            t = i / 7.0
            # piecewise constant on authored keys is enough; use linear blend of features
            flat.extend(_resample(features, [k[0] for k in clip["keys"]], t))
        curves.append(flat)
        solved.append(poses)
        _ = solved
    for i in range(len(clips)):
        for j in range(i + 1, len(clips)):
            sim = _cosine(curves[i], curves[j])
            if sim > 0.975:
                problems.append(f"{clips[i]['name']} collapses into {clips[j]['name']} (cosine {sim:.3f})")
    return problems


def _resample(features, times, t):
    if t <= times[0]:
        return features[0]
    if t >= times[-1]:
        return features[-1]
    for index in range(len(times) - 1):
        if times[index] <= t <= times[index + 1]:
            span = times[index + 1] - times[index] or 1.0
            u = (t - times[index]) / span
            return _lerp_list(features[index], features[index + 1], u)
    return features[-1]


def _pose_problems(skel, name, t, pose, spec):
    problems = []
    wr, wp = _character_fk(skel, pose)
    facing = (0.0, 0.0, 1.0)
    for side in ("l", "r"):
        ball = wp[skel.index["ball_" + side]]
        if ball[1] < -0.03:
            problems.append(f"{name}@{t:.2f} {side} foot penetrates y={ball[1]:.3f}")
        hip = wp[skel.index["thigh_" + side]]
        knee = wp[skel.index["calf_" + side]]
        ankle = wp[skel.index["foot_" + side]]
        mid = vscale(vadd(hip, ankle), 0.5)
        offset = vsub(knee, mid)
        if vlen(offset) > 0.025 and vdot(offset, facing) < -0.03:
            problems.append(f"{name}@{t:.2f} {side} knee bends backward")
        shoulder = wp[skel.index["upperarm_" + side]]
        elbow = wp[skel.index["lowerarm_" + side]]
        hand = wp[skel.index["hand_" + side]]
        target = tuple(spec["arm_" + side]["target"])
        miss = vdist(hand, target)
        if miss > 0.2:
            problems.append(f"{name}@{t:.2f} {side} hand misses target by {miss:.2f}m")
        if hand[1] < 0.35 or hand[1] > 1.95:
            problems.append(f"{name}@{t:.2f} {side} hand height {hand[1]:.2f}")
        forearm = qrot(wr[skel.index["lowerarm_" + side]], (0.0, 1.0, 0.0))
        fingers = qrot(wr[skel.index["hand_" + side]], (0.0, 1.0, 0.0))
        if vdot(vnorm(forearm), vnorm(fingers)) < 0.75:
            problems.append(f"{name}@{t:.2f} {side} fingers peeled off the forearm")
        pole = _pole(spec["arm_" + side].get("pole", "down"), shoulder, side)
        line = vsub(hand, shoulder)
        if vlen(line) > 1e-4:
            elbow_off = vsub(elbow, shoulder)
            pole_off = vsub(pole, shoulder)
            elbow_side = vsub(elbow_off, vscale(vnorm(line), vdot(elbow_off, vnorm(line))))
            pole_side = vsub(pole_off, vscale(vnorm(line), vdot(pole_off, vnorm(line))))
            if vlen(elbow_side) > 0.04 and vlen(pole_side) > 0.04 and vdot(vnorm(elbow_side), vnorm(pole_side)) < -0.2:
                problems.append(f"{name}@{t:.2f} {side} elbow flipped away from its pole")
        i = skel.index["lowerarm_" + side]
        _axis, deg = axis_angle(qmul(qconj(skel.rest_r[i]), pose.r[i]))
        if deg > 170.0:
            problems.append(f"{name}@{t:.2f} {side} elbow twist {deg:.0f} deg")
    head = wp[skel.index["Head"]]
    pelvis = wp[skel.index["pelvis"]]
    if head[1] < 1.25 or head[1] > 1.9:
        problems.append(f"{name}@{t:.2f} head height {head[1]:.2f}")
    if pelvis[1] < 0.55 or pelvis[1] > 1.08:
        problems.append(f"{name}@{t:.2f} pelvis height {pelvis[1]:.2f}")
    for bone in BODY_BONES:
        i = skel.index[bone]
        _axis, deg = axis_angle(qmul(qconj(skel.rest_r[i]), pose.r[i]))
        limit = 168.0 if "lowerarm" in bone or "hand" in bone else 150.0
        if "calf" in bone or "thigh" in bone:
            limit = 155.0
        if "upperarm" in bone:
            limit = 145.0
        if deg > limit:
            problems.append(f"{name}@{t:.2f} {bone} leaves rest by {deg:.0f} deg")
    return problems


def _sprint_upright(skel, axes, clip, poses):
    problems = []
    for (t, spec), pose in zip(clip["keys"], poses):
        if abs(spec["pelvis"][0]) > 10 or spec["spine"][0] > 6:
            problems.append(f"sprint torso hunches at {t:.2f}")
        wr, wp = _character_fk(skel, pose)
        _ = wr
        for side in ("l", "r"):
            hand = wp[skel.index["hand_" + side]]
            if abs(hand[0]) < 0.16:
                problems.append(f"sprint {side} hand collapses in front of the chest")
            if hand[2] > 0.45:
                problems.append(f"sprint {side} hand reaches forward like a carry")
    travel = clip["keys"][-1][1]["root_forward"] - clip["keys"][0][1]["root_forward"]
    if travel < 1.5:
        problems.append("sprint has no root travel")
    return problems


def _times(clip):
    times = [round(t * clip["duration"], 4) for t, _spec in clip["keys"]]
    times[0] = 0.0
    times[-1] = round(clip["duration"], 4)
    for index in range(1, len(times)):
        if times[index] <= times[index - 1]:
            times[index] = round(times[index - 1] + 0.02, 4)
    return times


def _hemisphere(quat, previous):
    quat = qnorm(quat)
    if previous is not None and vdot(quat, previous) < 0.0:
        quat = (-quat[0], -quat[1], -quat[2], -quat[3])
    return quat


class _Pack:
    def __init__(self):
        self.blob = bytearray()
        self.views = []
        self.accessors = []

    def _align(self):
        while len(self.blob) % 4:
            self.blob.append(0)

    def add_floats(self, values, gl_type):
        self._align()
        start = len(self.blob)
        raw = struct.pack("<" + "f" * len(values), *values)
        self.blob += raw
        comps = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[gl_type]
        mins, maxs = [], []
        for comp in range(comps):
            column = values[comp::comps]
            mins.append(min(column))
            maxs.append(max(column))
        view = len(self.views)
        self.views.append({"buffer": 0, "byteOffset": start, "byteLength": len(raw)})
        self.accessors.append({
            "bufferView": view,
            "componentType": 5126,
            "count": len(values) // comps,
            "type": gl_type,
            "min": mins if comps > 1 else [mins[0]],
            "max": maxs if comps > 1 else [maxs[0]],
        })
        return len(self.accessors) - 1


def build_document(skel: Skeleton, axes: Axes, clips: list[dict]) -> dict:
    pack = _Pack()
    animations = []
    solved_cache = []
    for clip in clips:
        times = _times(clip)
        time_acc = pack.add_floats(times, "SCALAR")
        poses = [solve_spec(skel, axes, spec) for _t, spec in clip["keys"]]
        solved_cache.append(poses)
        samplers = []
        channels = []

        def add_track(node, path, gl_type, values):
            output = pack.add_floats(values, gl_type)
            sampler = len(samplers)
            samplers.append({"input": time_acc, "output": output, "interpolation": "LINEAR"})
            channels.append({"sampler": sampler, "target": {"node": node, "path": path}})

        for node in skel.driven:
            quats = []
            previous = None
            for pose in poses:
                quat = _hemisphere(pose.r[node], previous)
                previous = quat
                quats.extend(quat)
            add_track(node, "rotation", "VEC4", quats)
        for name in ("root", "pelvis"):
            node = skel.index[name]
            values = []
            for pose in poses:
                values.extend(pose.t[node])
            add_track(node, "translation", "VEC3", values)
        animations.append({
            "name": clip["name"],
            "samplers": samplers,
            "channels": channels,
            "extras": {
                "duration_s": clip["duration"],
                "keyframes": len(times),
                "loop_pose": clip["loop"],
            },
        })
    nodes = []
    for source, index in zip(skel.nodes, range(len(skel.nodes))):
        node = {"name": skel.names[index]}
        if skel.rest_t[index] != (0.0, 0.0, 0.0):
            node["translation"] = [round(v, 6) for v in skel.rest_t[index]]
        if skel.rest_r[index] != (0.0, 0.0, 0.0, 1.0):
            node["rotation"] = [round(v, 6) for v in skel.rest_r[index]]
        if source.get("scale"):
            node["scale"] = list(source["scale"])
        if skel.children[index]:
            node["children"] = list(skel.children[index])
        nodes.append(node)
    document = {
        "asset": {
            "version": "2.0",
            "generator": "SoundBookWorker build_dramatic_anim_pack",
        },
        "scene": 0,
        "scenes": [{"name": "UAL1_rest", "nodes": [skel.index["Armature"]]}],
        "nodes": nodes,
        "animations": animations,
        "buffers": [{"byteLength": len(pack.blob)}],
        "bufferViews": pack.views,
        "accessors": pack.accessors,
        "extras": {
            "skeleton_source": "media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb",
            "bone_aliases": {"hips": "pelvis", "neck": "neck_01", "head": "Head"},
            "axis_notes": axes.notes,
            "license": "CC0 1.0",
            "original": True,
        },
    }
    return document, bytes(pack.blob), solved_cache


def write_glb(path: Path, document: dict, blob: bytes) -> None:
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    while len(text) % 4:
        text += b" "
    binary = blob
    while len(binary) % 4:
        binary += b"\x00"
    total = 12 + 8 + len(text) + 8 + len(binary)
    header = struct.pack("<III", GLB_MAGIC, 2, total)
    json_header = struct.pack("<II", len(text), JSON_CHUNK)
    bin_header = struct.pack("<II", len(binary), BIN_CHUNK)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + json_header + text + bin_header + binary)


def validate_glb(path: Path) -> dict:
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<III", data, 0)
    problems = []
    if magic != GLB_MAGIC or version != 2 or length != len(data):
        problems.append("GLB header does not match the file")
    offset = 12
    document = None
    blob = b""
    chunks = 0
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        chunk = data[offset + 8:offset + 8 + chunk_len]
        if chunk_len % 4:
            problems.append("chunk length is not aligned")
        offset += 8 + chunk_len
        chunks += 1
        if chunk_type == JSON_CHUNK:
            if chunk.endswith(b" ") or len(chunk) % 4 == 0:
                document = json.loads(chunk.decode("utf-8").strip())
            else:
                problems.append("JSON chunk padding is not spaces")
        elif chunk_type == BIN_CHUNK:
            blob = chunk
    if document is None or chunks < 2:
        problems.append("GLB is missing JSON or BIN")
        return {"problems": problems}
    if document.get("asset", {}).get("version") != "2.0":
        problems.append("asset.version is not 2.0")
    if int(document["buffers"][0]["byteLength"]) != len(blob):
        problems.append("buffer byteLength does not match BIN chunk")
    nodes = document["nodes"]
    names = [node.get("name") for node in nodes]
    anims = document.get("animations") or []
    if len(anims) < 32:
        problems.append(f"only {len(anims)} animations")
    seen = set()
    root_motion = []
    for anim in anims:
        name = anim.get("name")
        if name in seen:
            problems.append(f"duplicate animation {name}")
        seen.add(name)
        driven = set()
        key_count = None
        duration = 0.0
        root_samples = []
        for channel in anim["channels"]:
            target = channel["target"]
            node = target["node"]
            if not isinstance(node, int) or node < 0 or node >= len(nodes):
                problems.append(f"{name} targets a missing node")
                continue
            driven.add(nodes[node].get("name"))
            sampler = anim["samplers"][channel["sampler"]]
            if sampler.get("interpolation") != "LINEAR":
                problems.append(f"{name} is not LINEAR")
            times = _read_accessor(document, blob, sampler["input"])
            values = _read_accessor(document, blob, sampler["output"])
            if len(times) != len(values):
                problems.append(f"{name} input/output counts differ")
            if len(times) < 8 or len(times) > 18:
                problems.append(f"{name} has {len(times)} keys")
            flat_times = [row[0] for row in times]
            if flat_times != sorted(flat_times) or len(set(round(t, 5) for t in flat_times)) != len(flat_times):
                problems.append(f"{name} times are not strictly increasing")
            key_count = len(flat_times)
            duration = max(duration, flat_times[-1] if flat_times else 0.0)
            if target["path"] == "rotation":
                for quat in values:
                    n = sum(c * c for c in quat)
                    if abs(n - 1.0) > 1e-3:
                        problems.append(f"{name} has a non-unit quaternion")
                        break
            if nodes[node].get("name") == "root" and target["path"] == "translation":
                root_samples = values
        for bone in BODY_BONES:
            if bone not in driven:
                problems.append(f"{name} does not drive {bone}")
                break
        if "root" not in driven or "pelvis" not in driven:
            problems.append(f"{name} does not drive root and pelvis")
        travel = 0.0
        if root_samples:
            xs = [v[0] for v in root_samples]
            zs = [v[2] for v in root_samples]
            travel = math.hypot(max(xs) - min(xs), max(zs) - min(zs))
        if travel > 0.12:
            root_motion.append(name)
        _ = (key_count, duration)
    missing = [name for name in CLIP_NAMES if name not in seen]
    if missing:
        problems.append(f"missing clips {missing}")
    # accessor bounds
    for index, accessor in enumerate(document["accessors"]):
        view = document["bufferViews"][accessor["bufferView"]]
        start = int(view.get("byteOffset", 0))
        end = start + int(view["byteLength"])
        if start < 0 or end > len(blob):
            problems.append(f"accessor {index} is outside the buffer")
    return {
        "problems": problems,
        "animations": len(anims),
        "root_motion": sorted(root_motion),
        "bytes": len(data),
    }


def _read_accessor(document, blob, index):
    accessor = document["accessors"][index]
    comps = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
    view = document["bufferViews"][accessor["bufferView"]]
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    rows = []
    for i in range(int(accessor["count"])):
        rows.append(struct.unpack_from("<" + "f" * comps, blob, start + i * comps * 4))
    return rows


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def update_license(path: Path, clip_count: int, root_motion: list[str]) -> None:
    if LICENSES.is_file():
        licenses = json.loads(LICENSES.read_text(encoding="utf-8"))
    else:
        licenses = {"version": 1, "assets": []}
    licenses.setdefault("version", 1)
    licenses.setdefault("assets", [])
    digest = _sha256(path)
    record = {
        "id": "ps2_dramatic_pack",
        "tool": "build_dramatic_anim_pack",
        "title": "PS2 Dramatic Animation Pack",
        "author": "SoundBookWorker (original project-authored)",
        "license": "CC0 1.0",
        "license_url": CC0_URL,
        "source_page": "",
        "note": (
            "Original full-body keyframed clips authored on the Quaternius UAL1 "
            "65-bone rest skeleton. Not a copy of UAL1 or UAL2 animation curves. "
            "hips=pelvis, neck=neck_01, head=Head."
        ),
        "skeleton_source": "media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb",
        "clip_count": clip_count,
        "root_motion_clips": root_motion,
        "files": [{
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": digest,
        }],
    }
    assets = [item for item in licenses["assets"] if not (isinstance(item, dict) and item.get("id") == record["id"])]
    assets.append(record)
    licenses["assets"] = assets
    licenses["updated_utc"] = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    LICENSES.write_text(json.dumps(licenses, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    if not UAL1.is_file():
        print(f"missing skeleton {UAL1}", file=sys.stderr)
        return 1
    document = load_glb(UAL1)
    skel = Skeleton(document)
    axes = discover_axes(skel)
    print("UAL axis check passed")
    for note in axes.notes:
        print(" ", note)
    clips = build_clips(skel)
    problems = validate_poses(skel, axes, clips)
    if problems:
        print(f"pose QC failed ({len(problems)})")
        log = ROOT / ".tmp" / "dramatic_pose_problems.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(problems) + "\n", encoding="utf-8")
        for line in problems[:24]:
            print(" ", line)
        return 1
    gltf, blob, _solved = build_document(skel, axes, clips)
    write_glb(OUT, gltf, blob)
    report = validate_glb(OUT)
    if report["problems"]:
        print(f"glTF validation failed ({len(report['problems'])})")
        for line in report["problems"][:40]:
            print(" ", line)
        return 1
    update_license(OUT, report["animations"], report["root_motion"])
    print(f"wrote {OUT.relative_to(ROOT).as_posix()} ({report['bytes']} bytes)")
    print(f"animations {report['animations']}")
    print("root motion:", ", ".join(report["root_motion"]) or "(none)")
    for clip in clips:
        print(f"  {clip['name']}: {len(clip['keys'])} keys, {clip['duration']:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
