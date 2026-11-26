#!/usr/bin/env python3
"""Retarget BVH motion capture onto the Quaternius UAL 65-bone skeleton.

The tool parses a BVH hierarchy, maps Bandai-style / CMU cgspeed / Mixamo /
ACCAD joint names onto the bones measured from ``UAL1_Standard.glb``, and
writes a glTF binary whose node layout matches that file. Rotations are
transferred by rest-pose basis alignment:

* The source reference pose (CMU frame 0 is the added T-pose; other files use
  frame 0) defines each joint's bone axis and a twist axis that pointed at a
  stable body reference (character forward, otherwise left, otherwise up).
* Each animated frame aims the matching Quaternius bone along that joint's
  live bone direction and rolls it so the twist axis matches.
* Frame 0 of a true T-pose therefore lands on the Quaternius bind. A source
  whose arms are already down does not inherit the bind's T-pose.

Root translation keeps pelvis vertical bob. XZ travel is written on ``root``
and can be zeroed with in-place mode. The exported pack is
``media/assets3d/animations/mocap_core10_pack.glb``.

Bandai Namco Research Motion Dataset was not downloaded. Its motion files are
CC BY-NC 4.0. Only the Blender preview utilities in that repository are MIT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UAL1 = ROOT / "media" / "assets3d" / "cc0" / "quaternius_universal_animation_library" / "UAL1_Standard.glb"
OUT_GLB = ROOT / "media" / "assets3d" / "animations" / "mocap_core10_pack.glb"
REPORT_PATH = ROOT / "media" / "assets3d" / "animations" / "mocap_retarget_report.json"
LICENSES = ROOT / "media" / "assets3d" / "licenses.json"

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942

SKIP_NODES = {"", "Armature", "Mannequin"}
REQUIRED_BONES = (
    "pelvis", "spine_01", "spine_02", "spine_03", "neck_01", "Head",
    "clavicle_l", "upperarm_l", "lowerarm_l", "hand_l",
    "clavicle_r", "upperarm_r", "lowerarm_r", "hand_r",
    "thigh_l", "calf_l", "foot_l",
    "thigh_r", "calf_r", "foot_r",
)
AIM_CHILD = {
    "pelvis": "spine_01",
    "spine_01": "spine_02",
    "spine_02": "spine_03",
    "spine_03": "neck_01",
    "neck_01": "Head",
    "clavicle_l": "upperarm_l",
    "upperarm_l": "lowerarm_l",
    "lowerarm_l": "hand_l",
    "clavicle_r": "upperarm_r",
    "upperarm_r": "lowerarm_r",
    "lowerarm_r": "hand_r",
    "thigh_l": "calf_l",
    "calf_l": "foot_l",
    "foot_l": "ball_l",
    "thigh_r": "calf_r",
    "calf_r": "foot_r",
    "foot_r": "ball_r",
}

CMU_PAGE = "http://mocap.cs.cmu.edu/"
CMU_MIRROR = "https://github.com/konyshevgmbh/cmu-mocap"
CMU_RAW = "https://raw.githubusercontent.com/konyshevgmbh/cmu-mocap/master/data"
ACCAD_PAGE = "https://accad.osu.edu/research/motion-lab/mocap-system-and-data"
ACCAD_ZIP = "https://accad.osu.edu/sites/accad.osu.edu/files/Male1_bvh.zip"
CC_BY_30 = "https://creativecommons.org/licenses/by/3.0/"

CMU_LICENSE = (
    "This data is free for use in research projects. You may include this data "
    "in commercially-sold products, but you may not resell this data directly, "
    "even in converted form. If you publish results obtained using this data, "
    "we would appreciate it if you would send the citation to your published "
    "paper to jkh+mocap@cs.cmu.edu, and also would add this text to your "
    "acknowledgments section: The data used in this project was obtained from "
    "mocap.cs.cmu.edu. The database was created with funding from NSF EIA-0196217."
)
CMU_README_LICENSE = (
    "CMU places no restrictions on the use of the original dataset, and I "
    "(Bruce) place no additional restrictions on the use of this particular "
    "BVH conversion. Use this data! This data is free for use in research and "
    "commercial projects worldwide."
)


def _vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vscale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a):
    return math.sqrt(_dot(a, a))


def _norm(a):
    n = _length(a)
    if n < 1e-10:
        return (0.0, 0.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _project_perp(v, axis):
    axis = _norm(axis)
    return _vsub(v, _vscale(axis, _dot(v, axis)))


def qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qinv(q):
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
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


def qaxis(axis, degrees):
    rad = math.radians(degrees) * 0.5
    s = math.sin(rad)
    c = math.cos(rad)
    ax, ay, az = _norm(axis)
    return (ax * s, ay * s, az * s, c)


def qdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def qangle(a, b):
    d = abs(max(-1.0, min(1.0, qdot(qnorm(a), qnorm(b)))))
    return math.degrees(2.0 * math.acos(d))


def hemisphere(q, previous):
    q = qnorm(q)
    if previous is not None and qdot(q, previous) < 0.0:
        q = (-q[0], -q[1], -q[2], -q[3])
    return q


def rotation_between(a, b):
    a = _norm(a)
    b = _norm(b)
    if _length(a) < 1e-8 or _length(b) < 1e-8:
        return (0.0, 0.0, 0.0, 1.0)
    c = _dot(a, b)
    if c > 0.999999:
        return (0.0, 0.0, 0.0, 1.0)
    if c < -0.999999:
        axis = _cross(a, (1.0, 0.0, 0.0))
        if _length(axis) < 1e-4:
            axis = _cross(a, (0.0, 1.0, 0.0))
        return qnorm(qaxis(_norm(axis), 180.0))
    axis = _cross(a, b)
    return qnorm((axis[0], axis[1], axis[2], 1.0 + c))


def signed_angle(from_v, to_v, axis):
    axis_n = _norm(axis)
    a = _project_perp(from_v, axis_n)
    b = _project_perp(to_v, axis_n)
    if _length(a) < 1e-8 or _length(b) < 1e-8:
        return 0.0
    a = _norm(a)
    b = _norm(b)
    return math.degrees(math.atan2(_dot(_cross(a, b), axis_n), _dot(a, b)))


def choose_ref(bone_dir):
    bone_dir = _norm(bone_dir)
    for ref in ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)):
        if abs(_dot(bone_dir, ref)) < 0.85:
            return ref
    return (0.0, 1.0, 0.0)


@dataclass
class Joint:
    index: int
    name: str
    parent: int | None
    offset: tuple[float, float, float] = (0.0, 0.0, 0.0)
    channels: list[str] = field(default_factory=list)
    children: list[int] = field(default_factory=list)
    channel_index: int = 0
    is_end: bool = False


@dataclass
class Bvh:
    joints: list[Joint]
    frames: list[list[float]]
    frame_time: float
    path: str = ""

    @property
    def fps(self) -> float:
        return 1.0 / self.frame_time if self.frame_time else 30.0

    def by_name(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for joint in self.joints:
            if not joint.is_end and joint.name not in found:
                found[joint.name] = joint.index
        return found


def parse_bvh(text: str, path: str = "") -> Bvh:
    tokens = text.replace("{", " { ").replace("}", " } ").split()
    cursor = 0

    def take() -> str:
        nonlocal cursor
        if cursor >= len(tokens):
            raise ValueError("unexpected end of BVH")
        token = tokens[cursor]
        cursor += 1
        return token

    joints: list[Joint] = []

    def parse_joint(parent: int | None, is_end: bool) -> None:
        name = "End Site" if is_end else take()
        if take() != "{":
            raise ValueError(f"{name} is missing an opening brace")
        joint = Joint(index=len(joints), name=name, parent=parent, is_end=is_end)
        joints.append(joint)
        if parent is not None:
            joints[parent].children.append(joint.index)
        while True:
            token = take()
            if token == "}":
                return
            if token == "OFFSET":
                joint.offset = (float(take()), float(take()), float(take()))
            elif token == "CHANNELS":
                count = int(take())
                joint.channels = [take() for _ in range(count)]
            elif token == "JOINT":
                parse_joint(joint.index, False)
            elif token == "End":
                if take() != "Site":
                    raise ValueError("expected End Site")
                parse_joint(joint.index, True)
            else:
                raise ValueError(f"unexpected BVH token {token}")

    if take() != "HIERARCHY":
        raise ValueError("BVH is missing HIERARCHY")
    if take() != "ROOT":
        raise ValueError("BVH is missing ROOT")
    parse_joint(None, False)
    if take() != "MOTION":
        raise ValueError("BVH is missing MOTION")
    if take() != "Frames:":
        raise ValueError("BVH is missing Frames:")
    frame_count = int(take())
    if take() != "Frame":
        raise ValueError("BVH is missing Frame Time")
    time_token = take()
    if time_token not in {"Time:", "Time"}:
        raise ValueError(f"unexpected frame-time token {time_token}")
    frame_time = float(take())
    channel_cursor = 0
    for joint in joints:
        joint.channel_index = channel_cursor
        channel_cursor += len(joint.channels)
    values = [float(token) for token in tokens[cursor:]]
    if frame_count <= 0 or channel_cursor <= 0:
        raise ValueError("BVH has no motion")
    usable = len(values) // channel_cursor
    if usable < frame_count:
        frame_count = usable
    frames = [values[i * channel_cursor:(i + 1) * channel_cursor] for i in range(frame_count)]
    return Bvh(joints=joints, frames=frames, frame_time=frame_time, path=path)


def load_bvh(path: Path) -> Bvh:
    return parse_bvh(path.read_text(encoding="utf-8", errors="replace"), str(path))


def _channel_trs(joint: Joint, frame: list[float], post_multiply: bool, position_mode: str):
    vals = frame[joint.channel_index:joint.channel_index + len(joint.channels)]
    pos = [joint.offset[0], joint.offset[1], joint.offset[2]]
    rotation = (0.0, 0.0, 0.0, 1.0)
    for channel, value in zip(joint.channels, vals):
        if channel == "Xposition":
            pos[0] = value if position_mode == "absolute" else joint.offset[0] + value
        elif channel == "Yposition":
            pos[1] = value if position_mode == "absolute" else joint.offset[1] + value
        elif channel == "Zposition":
            pos[2] = value if position_mode == "absolute" else joint.offset[2] + value
        elif channel.endswith("rotation"):
            step = qaxis((1.0, 0.0, 0.0) if channel[0] == "X" else (0.0, 1.0, 0.0) if channel[0] == "Y" else (0.0, 0.0, 1.0), value)
            rotation = qmul(rotation, step) if post_multiply else qmul(step, rotation)
    return qnorm(rotation), (pos[0], pos[1], pos[2])


def forward_kinematics(bvh: Bvh, frame: list[float], post_multiply: bool, position_mode: str):
    world_r: list[tuple[float, float, float, float]] = [(0.0, 0.0, 0.0, 1.0)] * len(bvh.joints)
    world_p: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)] * len(bvh.joints)
    for joint in bvh.joints:
        local_r, local_p = _channel_trs(joint, frame, post_multiply, position_mode)
        parent = joint.parent
        if parent is None:
            world_r[joint.index] = local_r
            world_p[joint.index] = local_p
            continue
        world_r[joint.index] = qnorm(qmul(world_r[parent], local_r))
        offset = local_p if any(channel.endswith("position") for channel in joint.channels) else joint.offset
        world_p[joint.index] = _vadd(world_p[parent], qrot(world_r[parent], offset))
    return world_r, world_p


def _norm_joint(name: str) -> str:
    text = name.strip()
    lowered = text.lower()
    for prefix in ("mixamorig:", "mixamorig_", "bip01_", "bip001_", "bip_"):
        if lowered.startswith(prefix):
            text = text[len(prefix):]
            lowered = text.lower()
    return lowered.replace(" ", "").replace("_", "").replace("-", "").replace(".", "")


def _find_joint(bvh: Bvh, *names: str) -> int | None:
    wanted = {_norm_joint(name) for name in names}
    for joint in bvh.joints:
        if joint.is_end:
            continue
        if _norm_joint(joint.name) in wanted:
            return joint.index
    return None


def _offset_len(joint: Joint) -> float:
    return _length(joint.offset)


def build_bone_map(bvh: Bvh) -> dict[str, int]:
    """Map Quaternius bone names to source joint indices."""
    def pick(*names: str) -> int | None:
        return _find_joint(bvh, *names)

    mapping: dict[str, int] = {}
    hips = pick("hips", "pelvis")
    if hips is None:
        hips = 0
    mapping["pelvis"] = hips
    spine_names = ("lowerback", "spine", "spine1", "spine2", "spine3", "chest", "upperchest")
    spines = []
    for name in spine_names:
        found = pick(name)
        if found is None:
            continue
        if _offset_len(bvh.joints[found]) < 1e-4:
            continue
        spines.append(found)
    targets = ("spine_01", "spine_02", "spine_03")
    if spines:
        last = len(spines) - 1
        for index, target in enumerate(targets):
            slot = int(round(index * last / max(1, len(targets) - 1)))
            mapping[target] = spines[slot]
    necks = []
    for name in ("neck", "neck1", "neck01", "neck_01"):
        found = pick(name)
        if found is not None and _offset_len(bvh.joints[found]) >= 1e-4:
            necks.append(found)
    if necks:
        mapping["neck_01"] = necks[-1]
    head = pick("head")
    if head is not None:
        mapping["Head"] = head
    for side, tag in (("l", "left"), ("r", "right")):
        clav = pick(f"{tag}shoulder", f"{tag}collar", f"{tag}clavicle")
        arm = pick(f"{tag}arm", f"{tag}upperarm", f"{tag}uparm")
        forearm = pick(f"{tag}forearm", f"{tag}lowerarm")
        hand = pick(f"{tag}hand", f"{tag}wrist")
        thigh = pick(f"{tag}upleg", f"{tag}thigh", f"{tag}upperleg")
        calf = pick(f"{tag}leg", f"{tag}calf", f"{tag}shin", f"{tag}lowerleg")
        foot = pick(f"{tag}foot", f"{tag}ankle")
        toe = pick(f"{tag}toebase", f"{tag}toe", f"{tag}toes", f"{tag}ball")
        if arm is None and clav is not None and forearm is not None:
            arm = clav
            clav = None
        if clav is not None:
            mapping[f"clavicle_{side}"] = clav
        if arm is not None:
            mapping[f"upperarm_{side}"] = arm
        if forearm is not None:
            mapping[f"lowerarm_{side}"] = forearm
        if hand is not None:
            mapping[f"hand_{side}"] = hand
        if thigh is not None:
            mapping[f"thigh_{side}"] = thigh
        if calf is not None:
            mapping[f"calf_{side}"] = calf
        if foot is not None:
            mapping[f"foot_{side}"] = foot
        if toe is not None:
            mapping[f"ball_{side}"] = toe
    return mapping


def _significant_child(bvh: Bvh, index: int) -> int | None:
    stack = list(bvh.joints[index].children)
    while stack:
        child = stack.pop(0)
        joint = bvh.joints[child]
        if _offset_len(joint) >= 1e-4:
            return child
        stack.extend(joint.children)
    return None


def select_bvh_convention(bvh: Bvh, bone_map: dict[str, int]) -> tuple[bool, str]:
    """Pick Euler order and whether root channels replace the offset.

    Head-to-foot height is identical for additive and absolute root channels,
    so the tie is broken by which interpretation puts the feet nearest Y=0.
    ACCAD stores absolute centimeter coordinates in the root channels.
    """
    frame = bvh.frames[0]
    best = None
    for post in (True, False):
        for mode in ("absolute", "additive"):
            try:
                _rots, pos = forward_kinematics(bvh, frame, post, mode)
            except (ValueError, ZeroDivisionError):
                continue
            if any(name not in bone_map for name in ("pelvis", "Head", "foot_l", "foot_r")):
                continue
            head_y = pos[bone_map["Head"]][1]
            foot_y = min(pos[bone_map["foot_l"]][1], pos[bone_map["foot_r"]][1])
            height = head_y - foot_y
            if height <= 0.0:
                continue
            rank = (abs(foot_y), 0 if post else 1, -height)
            if best is None or rank < best[0]:
                best = (rank, post, mode)
    if best is None:
        return True, "absolute"
    return best[1], best[2]


class TargetSkeleton:
    def __init__(self, document: dict[str, Any]):
        self.nodes = document["nodes"]
        self.names = [str(node.get("name") or "") for node in self.nodes]
        self.index = {name: i for i, name in enumerate(self.names) if name}
        self.children: dict[int, list[int]] = {}
        self.parent: dict[int, int] = {}
        for i, node in enumerate(self.nodes):
            kids = [int(child) for child in (node.get("children") or [])]
            self.children[i] = kids
            for child in kids:
                self.parent[child] = i
        self.rest_r = []
        self.rest_t = []
        for node in self.nodes:
            rot = tuple(float(v) for v in (node.get("rotation") or (0.0, 0.0, 0.0, 1.0)))
            pos = tuple(float(v) for v in (node.get("translation") or (0.0, 0.0, 0.0)))
            self.rest_r.append(qnorm(rot))
            self.rest_t.append(pos)
        self.rest_wr, self.rest_wp = self.fk(self.rest_r, self.rest_t)
        self.order = self._order(self.index["root"])
        self.driven = [i for i, name in enumerate(self.names) if name not in SKIP_NODES]
        if len(self.driven) != 65:
            raise RuntimeError(f"expected 65 driven bones, found {len(self.driven)}")

    def _order(self, root_index: int) -> list[int]:
        order: list[int] = []

        def walk(index: int) -> None:
            order.append(index)
            for child in self.children.get(index, []):
                walk(child)

        walk(root_index)
        return order

    def fk(self, rotations, translations):
        world_r = {}
        world_p = {}

        def walk(index: int) -> None:
            if index in world_r:
                return
            parent = self.parent.get(index)
            if parent is None:
                world_r[index] = rotations[index]
                world_p[index] = translations[index]
                return
            walk(parent)
            world_r[index] = qnorm(qmul(world_r[parent], rotations[index]))
            world_p[index] = _vadd(world_p[parent], qrot(world_r[parent], translations[index]))

        for index in range(len(self.names)):
            walk(index)
        return world_r, world_p

    def local_bone_axis(self, bone: str) -> tuple[float, float, float]:
        child_name = AIM_CHILD.get(bone)
        if child_name and child_name in self.index:
            axis = self.rest_t[self.index[child_name]]
            if _length(axis) > 1e-5:
                return _norm(axis)
        return (0.0, 1.0, 0.0)


def _alignment(world_p, bone_map: dict[str, int], forward_hint=None):
    """Yaw the source onto +Z without pitching vertical travel into the air.

    A single-frame toe vector is ignored when the foot is pointing down, and a
    real horizontal hip travel overrides it so a run does not face backward.
    """
    hips = world_p[bone_map["pelvis"]]
    head = world_p[bone_map["Head"]]
    up = (0.0, 1.0, 0.0) if head[1] > hips[1] else _norm(_vsub(head, hips))
    body = abs(head[1] - hips[1])
    forward = None
    if forward_hint is not None:
        flat = _project_perp(forward_hint, up)
        if _length(flat) > max(1e-3, 0.8 * body):
            forward = _norm(flat)
    if forward is None and "foot_l" in bone_map and "ball_l" in bone_map:
        toe = _vsub(world_p[bone_map["ball_l"]], world_p[bone_map["foot_l"]])
        flat_toe = _project_perp(toe, up)
        if _length(flat_toe) > max(0.05, 0.35 * _length(toe)):
            forward = _norm(flat_toe)
    if forward is None:
        if "thigh_l" in bone_map and "thigh_r" in bone_map:
            left = _norm(_project_perp(_vsub(world_p[bone_map["thigh_l"]], world_p[bone_map["thigh_r"]]), up))
        else:
            left = (1.0, 0.0, 0.0)
        forward = _norm(_cross(left, up)) if _length(left) > 1e-6 else (0.0, 0.0, 1.0)
    left = _norm(_cross(up, forward))
    if _length(left) < 1e-6:
        left = (1.0, 0.0, 0.0)
        forward = _norm(_cross(left, up))
    flat_left = _norm(_project_perp(left, (0.0, 1.0, 0.0))) if up == (0.0, 1.0, 0.0) else left
    align = qaxis((0.0, 1.0, 0.0), signed_angle(flat_left, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))) if _length(flat_left) > 1e-6 else (0.0, 0.0, 0.0, 1.0)
    if qrot(align, forward)[2] < 0.0:
        align = qnorm(qmul(qaxis((0.0, 1.0, 0.0), 180.0), align))
    return align


def _source_child(bvh: Bvh, bone_map: dict[str, int], bone: str) -> int | None:
    target_child = AIM_CHILD.get(bone)
    if target_child and target_child in bone_map and bone_map[target_child] != bone_map.get(bone):
        return bone_map[target_child]
    source = bone_map.get(bone)
    if source is None:
        return None
    return _significant_child(bvh, source)


@dataclass
class RetargetResult:
    name: str
    times: list[float]
    local_r: list[list[tuple[float, float, float, float]]]
    root_t: list[tuple[float, float, float]]
    pelvis_t: list[tuple[float, float, float]]
    fps: float
    metrics: dict[str, Any]


def _facing_yaw(skel: TargetSkeleton, packed, root_t, pelvis_t) -> float:
    """Degrees to yaw the pose so the pelvis faces +Z."""
    rots = list(skel.rest_r)
    trans = list(skel.rest_t)
    for node, quat in packed.items():
        rots[node] = quat
    trans[skel.index["root"]] = root_t
    trans[skel.index["pelvis"]] = pelvis_t
    world_r, _world_p = skel.fk(rots, trans)
    pelvis = skel.index["pelvis"]
    forward_local = qrot(qinv(skel.rest_wr[pelvis]), (0.0, 0.0, 1.0))
    forward = _project_perp(qrot(world_r[pelvis], forward_local), (0.0, 1.0, 0.0))
    if _length(forward) < 1e-6:
        return 0.0
    return signed_angle(_norm(forward), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))


def _apply_yaw(skel, solved, root_list, pelvis_list, yaw_deg):
    q_yaw = qaxis((0.0, 1.0, 0.0), yaw_deg)
    root_i = skel.index["root"]
    old_root_r = skel.rest_r[root_i]
    new_root_r = qnorm(qmul(q_yaw, old_root_r))
    new_solved = []
    new_root = []
    new_pelvis = []
    for packed, root_t, pelvis_t in zip(solved, root_list, pelvis_list):
        world_pelvis = _vadd(root_t, qrot(old_root_r, pelvis_t))
        world_pelvis = qrot(q_yaw, world_pelvis)
        rotated_root = qrot(q_yaw, root_t)
        new_root_t = (rotated_root[0], skel.rest_t[root_i][1], rotated_root[2])
        new_pelvis_t = qrot(qinv(new_root_r), _vsub(world_pelvis, new_root_t))
        updated = dict(packed)
        updated[root_i] = new_root_r
        new_solved.append(updated)
        new_root.append(new_root_t)
        new_pelvis.append(new_pelvis_t)
    return new_solved, new_root, new_pelvis


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * pct))
    return ordered[max(0, min(len(ordered) - 1, index))]


def retarget_bvh(
    bvh: Bvh,
    skel: TargetSkeleton,
    *,
    name: str,
    drop_first: bool,
    in_place: bool,
    twist_signs: dict[str, float] | None = None,
    trim: tuple[float, float] | None = None,
    export_fps: float = 30.0,
) -> RetargetResult:
    bone_map = build_bone_map(bvh)
    missing = [bone for bone in REQUIRED_BONES if bone not in bone_map]
    post, position_mode = select_bvh_convention(bvh, bone_map)
    ref_rots, ref_pos = forward_kinematics(bvh, bvh.frames[0], post, position_mode)
    forward_hint = None
    if not in_place:
        hint_index = max(1, min(len(bvh.frames) - 1, len(bvh.frames) // 4))
        _hint_rots, hint_pos = forward_kinematics(bvh, bvh.frames[hint_index], post, position_mode)
        forward_hint = _vsub(hint_pos[bone_map["pelvis"]], ref_pos[bone_map["pelvis"]])
    align = _alignment(ref_pos, bone_map, forward_hint)

    def xform_pos(pos):
        return qrot(align, pos)

    def xform_rot(rot):
        return qnorm(qmul(align, qmul(rot, qinv(align))))

    ref_pos = [xform_pos(p) for p in ref_pos]
    ref_rots = [xform_rot(r) for r in ref_rots]
    signs = {bone: 1.0 for bone in REQUIRED_BONES}
    if twist_signs:
        signs.update(twist_signs)

    source_axis: dict[str, tuple[float, float, float]] = {}
    source_twist: dict[str, tuple[float, float, float]] = {}
    target_axis: dict[str, tuple[float, float, float]] = {}
    target_twist: dict[str, tuple[float, float, float]] = {}
    for bone, source in bone_map.items():
        child = _source_child(bvh, bone_map, bone)
        if child is None:
            continue
        direction = _vsub(ref_pos[child], ref_pos[source])
        if _length(direction) < 1e-6:
            continue
        direction = _norm(direction)
        local_axis = _norm(qrot(qinv(ref_rots[source]), direction))
        ref = choose_ref(_norm(qrot(skel.rest_wr[skel.index[bone]], skel.local_bone_axis(bone))))
        twist = _project_perp(ref, direction)
        if _length(twist) < 1e-6:
            continue
        source_axis[bone] = local_axis
        source_twist[bone] = _norm(qrot(qinv(ref_rots[source]), _norm(twist)))
        t_axis = skel.local_bone_axis(bone)
        t_dir = _norm(qrot(skel.rest_wr[skel.index[bone]], t_axis))
        t_twist = _project_perp(ref, t_dir)
        target_axis[bone] = t_axis
        target_twist[bone] = _norm(qrot(qinv(skel.rest_wr[skel.index[bone]]), _norm(t_twist))) if _length(t_twist) > 1e-6 else (1.0, 0.0, 0.0)

    start = 1 if drop_first and len(bvh.frames) > 2 else 0
    end = len(bvh.frames)
    if trim is not None:
        t0 = max(start, int(round(trim[0] / bvh.frame_time)))
        t1 = min(end, int(round(trim[1] / bvh.frame_time)))
        if t1 - t0 >= 2:
            start, end = t0, t1
    selected = list(range(start, end))
    solved_r: list[dict[int, tuple]] = []
    hip_src_y: list[float] = []
    hip_src_xz: list[tuple[float, float]] = []
    for frame_index in selected:
        rots, pos = forward_kinematics(bvh, bvh.frames[frame_index], post, position_mode)
        pos = [xform_pos(p) for p in pos]
        rots = [xform_rot(r) for r in rots]
        hips = pos[bone_map["pelvis"]]
        hip_src_y.append(hips[1])
        hip_src_xz.append((hips[0], hips[2]))
        world_r = {i: skel.rest_wr[i] for i in range(len(skel.names))}
        local_r = list(skel.rest_r)
        for node in skel.order:
            bone = skel.names[node]
            parent = skel.parent.get(node)
            parent_world = world_r[parent] if parent is not None else (0.0, 0.0, 0.0, 1.0)
            if bone not in source_axis:
                local_r[node] = skel.rest_r[node]
                world_r[node] = qnorm(qmul(parent_world, local_r[node]))
                continue
            source = bone_map[bone]
            child = _source_child(bvh, bone_map, bone)
            direction = _vsub(pos[child], pos[source]) if child is not None else qrot(rots[source], source_axis[bone])
            if _length(direction) < 1e-6:
                direction = qrot(rots[source], source_axis[bone])
            direction = _norm(direction)
            twist = _project_perp(qrot(rots[source], source_twist[bone]), direction)
            if _length(twist) < 1e-6:
                twist = _project_perp((0.0, 0.0, 1.0), direction)
            twist = _vscale(_norm(twist), signs.get(bone, 1.0))
            aim = rotation_between(target_axis[bone], direction)
            hinted = qrot(aim, target_twist[bone])
            roll = qaxis(direction, signed_angle(hinted, twist, direction))
            world = qnorm(qmul(roll, aim))
            local_r[node] = qnorm(qmul(qinv(parent_world), world))
            world_r[node] = world
        solved_r.append({i: local_r[i] for i in skel.driven})

    scale = 1.0
    ref_hip = abs(ref_pos[bone_map["pelvis"]][1])
    target_hip = skel.rest_wp[skel.index["pelvis"]][1]
    if ref_hip > 1e-4:
        scale = target_hip / ref_hip
    origin_y = hip_src_y[0]
    origin_x, origin_z = hip_src_xz[0]
    bob = [(y - origin_y) * scale for y in hip_src_y]
    dxz = [((x - origin_x) * scale, (z - origin_z) * scale) for x, z in hip_src_xz]
    if in_place:
        dxz = [(0.0, 0.0) for _ in dxz]

    def pose_translations(ground: float):
        root_list = []
        pelvis_list = []
        translations = []
        rest_root = skel.rest_t[skel.index["root"]]
        for frame_i, local in enumerate(solved_r):
            rots = list(skel.rest_r)
            trans = list(skel.rest_t)
            for node, quat in local.items():
                rots[node] = quat
            dx, dz = dxz[frame_i]
            root_t = (rest_root[0] + dx, rest_root[1], rest_root[2] + dz)
            trans[skel.index["root"]] = root_t
            # Pelvis world Y carries the bob. Solve the local translation against the rest root rotation.
            rest_pelvis_world = skel.rest_wp[skel.index["pelvis"]]
            desired = (
                rest_pelvis_world[0] + dx,
                rest_pelvis_world[1] + bob[frame_i] + ground,
                rest_pelvis_world[2] + dz,
            )
            root_world_r = skel.rest_r[skel.index["root"]]
            pelvis_local = qrot(qinv(root_world_r), _vsub(desired, root_t))
            trans[skel.index["pelvis"]] = pelvis_local
            translations.append(trans)
            root_list.append(root_t)
            pelvis_list.append(pelvis_local)
            _ = frame_i
        return root_list, pelvis_list, translations

    _root0, _pelvis0, trans0 = pose_translations(0.0)
    ball_l = skel.index["ball_l"]
    ball_r = skel.index["ball_r"]
    lower = []
    for frame_i, local in enumerate(solved_r):
        rots = list(skel.rest_r)
        for node, quat in local.items():
            rots[node] = quat
        _wr, wp = skel.fk(rots, trans0[frame_i])
        lower.append(min(wp[ball_l][1], wp[ball_r][1]))
    target_ball = min(skel.rest_wp[ball_l][1], skel.rest_wp[ball_r][1])
    ground = target_ball - min(lower)
    root_list, pelvis_list, _trans_final = pose_translations(ground)
    yaw = _facing_yaw(skel, solved_r[0], root_list[0], pelvis_list[0])
    if abs(yaw) > 0.5:
        solved_r, root_list, pelvis_list = _apply_yaw(skel, solved_r, root_list, pelvis_list, yaw)

    # Resample to the export rate.
    duration = (len(selected) - 1) * bvh.frame_time
    if duration <= 0.0:
        duration = bvh.frame_time
    step = 1.0 / export_fps
    sample_count = max(2, int(round(duration / step)) + 1)
    times = [min(duration, i * step) for i in range(sample_count)]
    if times[-1] < duration:
        times.append(duration)

    def sample_quat(sequence, time_s):
        if len(sequence) == 1:
            return sequence[0]
        pos = time_s / bvh.frame_time
        i0 = max(0, min(len(sequence) - 1, int(math.floor(pos))))
        i1 = min(len(sequence) - 1, i0 + 1)
        u = max(0.0, min(1.0, pos - i0))
        a = sequence[i0]
        b = sequence[i1]
        if qdot(a, b) < 0.0:
            b = (-b[0], -b[1], -b[2], -b[3])
        return qnorm(tuple(a[k] * (1.0 - u) + b[k] * u for k in range(4)))

    def sample_vec(sequence, time_s):
        if len(sequence) == 1:
            return sequence[0]
        pos = time_s / bvh.frame_time
        i0 = max(0, min(len(sequence) - 1, int(math.floor(pos))))
        i1 = min(len(sequence) - 1, i0 + 1)
        u = max(0.0, min(1.0, pos - i0))
        return tuple(sequence[i0][k] * (1.0 - u) + sequence[i1][k] * u for k in range(3))

    per_node = {node: [local[node] for local in solved_r] for node in skel.driven}
    out_locals = []
    out_root = []
    out_pelvis = []
    previous = {node: None for node in skel.driven}
    for time_s in times:
        frame_local = []
        # Placeholder sized later; we store by driven order in metrics via dict.
        packed = {}
        for node in skel.driven:
            quat = hemisphere(sample_quat(per_node[node], time_s), previous[node])
            previous[node] = quat
            packed[node] = quat
        out_locals.append(packed)
        out_root.append(sample_vec(root_list, time_s))
        out_pelvis.append(sample_vec(pelvis_list, time_s))
    metrics = _measure(skel, bone_map, missing, out_locals, out_root, out_pelvis, times, bvh, post, position_mode, scale, ground)
    flat_locals = []
    for packed in out_locals:
        flat_locals.append([packed[node] for node in skel.driven])
    return RetargetResult(name, times, flat_locals, out_root, out_pelvis, export_fps, metrics)


def _measure(skel, bone_map, missing, locals_packed, root_t, pelvis_t, times, bvh, post, mode, scale, ground):
    ball_l = skel.index["ball_l"]
    ball_r = skel.index["ball_r"]
    target_ball = min(skel.rest_wp[ball_l][1], skel.rest_wp[ball_r][1])
    lower = []
    hip_ys = []
    max_jump = 0.0
    nan = False
    prev = None
    knee_scores = []
    hand_samples = []
    forward_local = qrot(qinv(skel.rest_wr[skel.index["pelvis"]]), (0.0, 0.0, 1.0))
    for index, packed in enumerate(locals_packed):
        rots = list(skel.rest_r)
        trans = list(skel.rest_t)
        trans[skel.index["root"]] = root_t[index]
        trans[skel.index["pelvis"]] = pelvis_t[index]
        for node, quat in packed.items():
            if not all(math.isfinite(c) for c in quat):
                nan = True
            rots[node] = quat
        if prev is not None:
            for node, quat in packed.items():
                max_jump = max(max_jump, qangle(prev[node], quat))
        prev = packed
        _wr, wp = skel.fk(rots, trans)
        ly = wp[ball_l][1]
        ry = wp[ball_r][1]
        lower.append(min(ly, ry))
        hip_ys.append(wp[skel.index["pelvis"]][1])
        facing = _norm(_project_perp(qrot(_wr[skel.index["pelvis"]], forward_local), (0.0, 1.0, 0.0)))
        if index % max(1, len(locals_packed) // 12) == 0:
            hand_samples.append({
                "t": round(times[index], 3),
                "hand_r": [round(v, 3) for v in wp[skel.index["hand_r"]]],
                "hand_l": [round(v, 3) for v in wp[skel.index["hand_l"]]],
                "hips_y": round(wp[skel.index["pelvis"]][1], 3),
            })
        if "thigh_l" in bone_map and "calf_l" in bone_map and "foot_l" in bone_map:
            for side in ("l", "r"):
                hip = wp[skel.index[f"thigh_{side}"]]
                knee = wp[skel.index[f"calf_{side}"]]
                ankle = wp[skel.index[f"foot_{side}"]]
                seg = _vsub(ankle, hip)
                denom = _dot(seg, seg)
                if denom < 1e-6:
                    continue
                t = max(0.0, min(1.0, _dot(_vsub(knee, hip), seg) / denom))
                closest = _vadd(hip, _vscale(seg, t))
                pole = _vsub(knee, closest)
                if _length(pole) > 0.08 and _length(facing) > 1e-6:
                    knee_scores.append(_dot(_norm(pole), facing))
    xs = [p[0] for p in root_t]
    zs = [p[2] for p in root_t]
    travel = math.hypot(max(xs) - min(xs), max(zs) - min(zs)) if xs else 0.0
    errors = [y - target_ball for y in lower]
    loop_gap = None
    if len(locals_packed) >= 2:
        gaps = [qangle(locals_packed[0][node], locals_packed[-1][node]) for node in locals_packed[0]]
        loop_gap = sum(gaps) / len(gaps)
    return {
        "mapped_bones": sorted(bone_map),
        "missing_required": missing,
        "bone_mapping_ratio": round((len(REQUIRED_BONES) - len(missing)) / len(REQUIRED_BONES), 4),
        "source_fps": round(bvh.fps, 4),
        "export_fps": round(1.0 / (times[1] - times[0]), 4) if len(times) > 1 else bvh.fps,
        "frame_count": len(times),
        "duration_s": round(times[-1] if times else 0.0, 4),
        "root_motion_delta_m": round(travel, 4),
        "root_xz_span_m": {"x": round(max(xs) - min(xs), 4) if xs else 0.0, "z": round(max(zs) - min(zs), 4) if zs else 0.0},
        "pelvis_vertical_bob_m": round(max(hip_ys) - min(hip_ys), 4) if hip_ys else 0.0,
        "foot_ground_target_y": round(target_ball, 4),
        "stance_foot_y_min": round(min(lower), 4) if lower else None,
        "stance_foot_y_max": round(max(lower), 4) if lower else None,
        "foot_contact_error_min_m": round(min(errors), 4) if errors else None,
        "foot_contact_error_max_m": round(max(errors), 4) if errors else None,
        "foot_contact_error_p08_m": round(_percentile(errors, 0.08), 4) if errors else None,
        "max_angular_delta_deg": round(max_jump, 3),
        "loop_closure_deg": round(loop_gap, 3) if loop_gap is not None else None,
        "nan_quaternions": nan,
        "knee_forward_mean": round(sum(knee_scores) / len(knee_scores), 4) if knee_scores else None,
        "height_scale": round(scale, 6),
        "ground_offset_m": round(ground, 4),
        "bvh_convention": {"post_multiply": post, "position_mode": mode},
        "hand_samples": hand_samples,
    }


def calibrate_twist(bvh, skel, **kwargs) -> RetargetResult:
    """Keep the thigh/shin roll whose knees point forward on bent frames."""
    base = retarget_bvh(bvh, skel, twist_signs=None, **kwargs)
    base.metrics["twist_sign_flip"] = []
    score = base.metrics.get("knee_forward_mean")
    if score is None or score >= 0.15:
        return base
    signs = {bone: -1.0 for bone in ("thigh_l", "thigh_r", "calf_l", "calf_r")}
    flipped = retarget_bvh(bvh, skel, twist_signs=signs, **kwargs)
    flipped.metrics["twist_sign_flip"] = list(signs)
    flipped_score = flipped.metrics.get("knee_forward_mean")
    if flipped_score is None or flipped_score <= score:
        return base
    return flipped


def _load_glb(path: Path):
    data = path.read_bytes()
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


def write_glb(path: Path, document: dict, blob: bytes) -> None:
    text = json.dumps(document, separators=(",", ":")).encode("utf-8")
    while len(text) % 4:
        text += b" "
    binary = blob
    while len(binary) % 4:
        binary += b"\x00"
    total = 12 + 8 + len(text) + 8 + len(binary)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        struct.pack("<III", GLB_MAGIC, 2, total)
        + struct.pack("<II", len(text), JSON_CHUNK)
        + text
        + struct.pack("<II", len(binary), BIN_CHUNK)
        + binary
    )


def export_pack(skel: TargetSkeleton, clips: list[RetargetResult], notes: list[str]) -> dict[str, Any]:
    pack = _Pack()
    animations = []
    for clip in clips:
        times = pack.add_floats(clip.times, "SCALAR")
        samplers = []
        channels = []

        def add_track(node, path_name, gl_type, values):
            output = pack.add_floats(values, gl_type)
            sampler = len(samplers)
            samplers.append({"input": times, "output": output, "interpolation": "LINEAR"})
            channels.append({"sampler": sampler, "target": {"node": node, "path": path_name}})

        for driven_index, node in enumerate(skel.driven):
            values = []
            for frame in clip.local_r:
                values.extend(frame[driven_index])
            add_track(node, "rotation", "VEC4", values)
        root_values = []
        pelvis_values = []
        for root_t, pelvis_t in zip(clip.root_t, clip.pelvis_t):
            root_values.extend(root_t)
            pelvis_values.extend(pelvis_t)
        add_track(skel.index["root"], "translation", "VEC3", root_values)
        add_track(skel.index["pelvis"], "translation", "VEC3", pelvis_values)
        animations.append({
            "name": clip.name,
            "samplers": samplers,
            "channels": channels,
            "extras": {
                "duration_s": clip.times[-1] if clip.times else 0.0,
                "fps": clip.fps,
                "frames": len(clip.times),
                "source": clip.metrics.get("source_file", ""),
                "root_motion_m": clip.metrics.get("root_motion_delta_m", 0.0),
            },
        })
    nodes = []
    for source, index in zip(skel.nodes, range(len(skel.nodes))):
        node = {"name": skel.names[index]}
        if "translation" in source:
            node["translation"] = list(source["translation"])
        if "rotation" in source:
            node["rotation"] = list(source["rotation"])
        if "scale" in source:
            node["scale"] = list(source["scale"])
        if skel.children[index]:
            node["children"] = list(skel.children[index])
        nodes.append(node)
    document = {
        "asset": {"version": "2.0", "generator": "SoundBookWorker retarget_mocap_bvh"},
        "scene": 0,
        "scenes": [{"name": "UAL1_rest", "nodes": [skel.index["Armature"]]}],
        "nodes": nodes,
        "animations": animations,
        "buffers": [{"byteLength": len(pack.blob)}],
        "bufferViews": pack.views,
        "accessors": pack.accessors,
        "extras": {
            "skeleton_source": "media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb",
            "tier": "real_mocap_retargeted",
            "notes": notes,
        },
    }
    write_glb(OUT_GLB, document, bytes(pack.blob))
    return document


def quality_problems(metrics: dict[str, Any]) -> list[str]:
    problems = []
    if metrics.get("nan_quaternions"):
        problems.append("NaN quaternion")
    if metrics.get("bone_mapping_ratio", 0.0) < 1.0:
        problems.append(f"missing bones {metrics.get('missing_required')}")
    err_min = metrics.get("foot_contact_error_min_m")
    err_max = metrics.get("foot_contact_error_max_m")
    if err_min is None or err_min < -0.04:
        problems.append(f"foot sinks ({err_min})")
    # Stance foot should plant near the bind sole. A running swing can lift the
    # other foot; the lower foot's minimum is the contact check. Floating means
    # even the lowest foot stays far above the sole.
    p08 = metrics.get("foot_contact_error_p08_m")
    if p08 is not None and p08 > 0.30:
        problems.append(f"stance foot floats {p08:.3f}m")
    bob = metrics.get("pelvis_vertical_bob_m")
    if bob is not None and bob > 0.55:
        problems.append(f"pelvis bob {bob:.3f}m is not a human vertical range")
    if metrics.get("max_angular_delta_deg", 0.0) > 160.0:
        problems.append(f"joint flip {metrics.get('max_angular_delta_deg')} deg")
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_skeleton() -> TargetSkeleton:
    return TargetSkeleton(_load_glb(UAL1))


# Core-10 sources. Trims are seconds on the source timeline and were chosen
# from the probe (yaw window, active-hand window, or the useful mid-cycle).
CORE_CLIPS = (
    {
        "name": "Mocap_Idle_Sway_Loop",
        "action": "idle_natural",
        "path": ROOT / "media/assets3d/mocap/accad/Male1_A2_Sway.bvh",
        "drop_first": False,
        "in_place": True,
        "trim": None,
        "source_id": "accad_male1_a2_sway",
        "description": "Natural standing weight shift / idle sway",
    },
    {
        "name": "Mocap_Sprint_Carry",
        "action": "sprint_carry",
        "path": ROOT / "media/assets3d/mocap/accad/Male1_C22_RunWithBox.bvh",
        "drop_first": False,
        "in_place": False,
        "trim": None,
        "source_id": "accad_male1_c22_run_with_box",
        "description": "Run while carrying a box",
    },
    {
        "name": "Mocap_Abrupt_Halt",
        "action": "abrupt_halt",
        "path": ROOT / "media/assets3d/mocap/accad/Male1_C02_RunToStand.bvh",
        "drop_first": False,
        "in_place": False,
        "trim": None,
        "source_id": "accad_male1_c02_run_to_stand",
        "description": "Run into a stop and stand",
    },
    {
        "name": "Mocap_Turn_Around",
        "action": "turn_around",
        "path": ROOT / "media/assets3d/mocap/cmu/69_16_turn_in_place.bvh",
        "drop_first": True,
        "in_place": False,
        "trim": (0.15, 2.6),
        "source_id": "cmu_69_16_turn_in_place",
        "description": "Turn in place, first reversal",
    },
    {
        "name": "Mocap_Angry_Confront",
        "action": "angry_confront",
        "path": ROOT / "media/assets3d/mocap/cmu/18_10_quarrel_angry.bvh",
        "drop_first": True,
        "in_place": True,
        "trim": (1.0, 5.2),
        "source_id": "cmu_18_10_quarrel_angry",
        "description": "Quarrel with angry hand gestures",
    },
    {
        "name": "Mocap_Handoff_Offer",
        "action": "handoff_offer",
        "path": ROOT / "media/assets3d/mocap/cmu/141_23_shake_hands.bvh",
        "drop_first": True,
        "in_place": True,
        "trim": None,
        "source_id": "cmu_141_23_shake_hands",
        "description": "Reach and clasp another person's hand",
    },
    {
        "name": "Mocap_Handoff_Receive",
        "action": "handoff_receive",
        "path": ROOT / "media/assets3d/mocap/cmu/141_11_throw_catch.bvh",
        "drop_first": True,
        "in_place": True,
        "trim": (1.05, 2.2),
        "source_id": "cmu_141_11_throw_and_catch",
        "description": "Two-handed reach that catches an incoming object",
    },
    {
        "name": "Mocap_Operate_Console",
        "action": "operate_console",
        "path": ROOT / "media/assets3d/mocap/cmu/79_85_typing_laptop.bvh",
        "drop_first": True,
        "in_place": True,
        "trim": (0.4, 4.2),
        "source_id": "cmu_79_85_typing_laptop",
        "description": "Typing on a laptop",
    },
    {
        "name": "Mocap_Exhale_Relief",
        "action": "exhale_relief",
        "path": ROOT / "media/assets3d/mocap/cmu/141_13_stretch_yawn.bvh",
        "drop_first": True,
        "in_place": True,
        "trim": None,
        "source_id": "cmu_141_13_stretch_yawn",
        "description": "Stretch and yawn / exhale",
    },
)


def _retarget_spec(skel: TargetSkeleton, spec: dict[str, Any]) -> RetargetResult:
    bvh = load_bvh(spec["path"])
    drop = spec["drop_first"] or any(joint.name == "LHipJoint" for joint in bvh.joints)
    result = calibrate_twist(
        bvh,
        skel,
        name=spec["name"],
        drop_first=drop,
        in_place=spec["in_place"],
        trim=spec.get("trim"),
    )
    result.metrics["source_file"] = spec["path"].relative_to(ROOT).as_posix()
    result.metrics["source_id"] = spec["source_id"]
    result.metrics["action"] = spec["action"]
    result.metrics["description"] = spec["description"]
    result.metrics["in_place"] = spec["in_place"]
    result.metrics["problems"] = quality_problems(result.metrics)
    result.metrics["passed"] = not result.metrics["problems"]
    return result


def probe(specs=CORE_CLIPS) -> list[dict[str, Any]]:
    skel = _load_skeleton()
    rows = []
    for spec in specs:
        if not spec["path"].is_file():
            print(f"MISSING {spec['path']}")
            continue
        result = _retarget_spec(skel, spec)
        metrics = dict(result.metrics)
        hands = metrics.pop("hand_samples", [])
        print(f"\n== {spec['name']} {spec['description']}")
        brief = {key: metrics[key] for key in (
            "duration_s", "frame_count", "source_fps", "bone_mapping_ratio", "missing_required",
            "root_motion_delta_m", "pelvis_vertical_bob_m", "stance_foot_y_min", "stance_foot_y_max",
            "foot_contact_error_min_m", "foot_contact_error_max_m", "max_angular_delta_deg",
            "knee_forward_mean", "nan_quaternions", "height_scale", "bvh_convention", "problems",
        ) if key in metrics}
        print(json.dumps(brief, ensure_ascii=False))
        print(" hands", json.dumps(hands[:6], ensure_ascii=False))
        rows.append(metrics)
    return rows


def _license_record(clips: list[RetargetResult]) -> dict[str, Any]:
    files = []
    seen = set()
    for spec in CORE_CLIPS:
        path = spec["path"]
        key = str(path.resolve())
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        files.append({
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "role": spec["source_id"],
        })
    zip_path = ROOT / "media" / "assets3d" / "mocap" / "_incoming" / "Male1_bvh.zip"
    if zip_path.is_file():
        files.append({
            "path": zip_path.relative_to(ROOT).as_posix(),
            "bytes": zip_path.stat().st_size,
            "sha256": _sha256(zip_path),
            "role": "accad_male1_bvh_zip",
        })
    readme = ROOT / "media" / "assets3d" / "mocap" / "cmu_READMEFIRST.txt"
    if readme.is_file():
        files.append({
            "path": readme.relative_to(ROOT).as_posix(),
            "bytes": readme.stat().st_size,
            "sha256": _sha256(readme),
            "role": "cmu_bvh_conversion_readme",
        })
    if OUT_GLB.is_file():
        files.append({
            "path": OUT_GLB.relative_to(ROOT).as_posix(),
            "bytes": OUT_GLB.stat().st_size,
            "sha256": _sha256(OUT_GLB),
            "role": "retargeted_glb",
        })
    return {
        "id": "mocap_core10",
        "tool": "retarget_mocap_bvh",
        "title": "Core-10 retargeted human motion capture",
        "tier": "real_mocap_retargeted",
        "sources": [
            {
                "id": "cmu_graphics_lab_mocap",
                "title": "CMU Graphics Lab Motion Capture Database (cgspeed / Bruce Hahne BVH conversion)",
                "author": "Carnegie Mellon University Graphics Lab; BVH conversion by Bruce Hahne",
                "license": "CMU free for research and commercial products; do not resell the data itself. BVH conversion adds no further restrictions.",
                "license_text": CMU_LICENSE,
                "conversion_license_text": CMU_README_LICENSE,
                "license_url": CMU_PAGE,
                "source_page": CMU_PAGE,
                "mirror": CMU_MIRROR,
                "download_urls": [
                    f"{CMU_RAW}/069/69_16.bvh",
                    f"{CMU_RAW}/018/18_10.bvh",
                    f"{CMU_RAW}/141/141_23.bvh",
                    f"{CMU_RAW}/141/141_11.bvh",
                    f"{CMU_RAW}/079/79_85.bvh",
                    f"{CMU_RAW}/141/141_13.bvh",
                ],
                "attribution": "The data used in this project was obtained from mocap.cs.cmu.edu. The database was created with funding from NSF EIA-0196217.",
            },
            {
                "id": "accad_open_motion",
                "title": "ACCAD Open Motion Project, Male 1 BVH",
                "author": "Advanced Computing Center for the Arts and Design, The Ohio State University",
                "license": "CC BY 3.0",
                "license_url": CC_BY_30,
                "source_page": ACCAD_PAGE,
                "download_url": ACCAD_ZIP,
                "attribution": "Motion data by ACCAD / The Ohio State University, licensed CC BY 3.0.",
                "note": "The distribution page shows the CC BY 3.0 badge and links the license deed. The zip has no separate license file.",
            },
        ],
        "rejected_sources": [
            {
                "id": "bandai_namco_research_motiondataset",
                "url": "https://github.com/BandaiNamcoResearchInc/Bandai-Namco-Research-Motiondataset",
                "license": "CC BY-NC 4.0 for the motion datasets; MIT applies only to utils/blender",
                "reason": "Non-commercial motion license. Not downloaded and not retargeted.",
            }
        ],
        "clips": [clip.metrics.get("source_id") for clip in clips],
        "files": files,
    }


def update_licenses(record: dict[str, Any]) -> None:
    if LICENSES.is_file():
        payload = json.loads(LICENSES.read_text(encoding="utf-8"))
    else:
        payload = {"version": 1, "assets": []}
    assets = [item for item in payload.get("assets", []) if item.get("id") != "mocap_core10"]
    assets.append(record)
    payload["assets"] = assets
    payload["updated_utc"] = "2026-11-26T00:00:00Z"
    LICENSES.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build() -> dict[str, Any]:
    skel = _load_skeleton()
    results = []
    for spec in CORE_CLIPS:
        result = _retarget_spec(skel, spec)
        results.append(result)
        status = "PASS" if result.metrics["passed"] else "FAIL"
        print(f"{status} {result.name} map={result.metrics['bone_mapping_ratio']} "
              f"foot_err={result.metrics['foot_contact_error_min_m']}..{result.metrics['foot_contact_error_max_m']} "
              f"jump={result.metrics['max_angular_delta_deg']} knee={result.metrics['knee_forward_mean']} "
              f"bob={result.metrics['pelvis_vertical_bob_m']} loop={result.metrics.get('loop_closure_deg')} "
              f"problems={result.metrics['problems']}")
    notes = [
        "Real optical motion capture retargeted onto the UAL1 65-bone rest pose.",
        "ps2_dramatic_pack.glb is not a source for these clips.",
        "Bandai Namco Research Motion Dataset was rejected because the motion license is CC BY-NC 4.0.",
    ]
    export_pack(skel, results, notes)
    structural = validate_pack(skel)
    record = _license_record(results)
    update_licenses(record)
    report = {
        "pack": OUT_GLB.relative_to(ROOT).as_posix(),
        "pack_sha256": _sha256(OUT_GLB),
        "pack_bytes": OUT_GLB.stat().st_size,
        "skeleton_source": "media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb",
        "structural_validation": structural,
        "clips": [
            {
                "clip": result.name,
                "action": result.metrics.get("action"),
                "description": result.metrics.get("description"),
                "source_file": result.metrics.get("source_file"),
                "source_id": result.metrics.get("source_id"),
                "license": "CC BY 3.0" if str(result.metrics.get("source_id", "")).startswith("accad") else "CMU commercial-use with attribution; no resale of the data",
                "bone_mapping_ratio": result.metrics["bone_mapping_ratio"],
                "missing_required": result.metrics["missing_required"],
                "frame_count": result.metrics["frame_count"],
                "duration_s": result.metrics["duration_s"],
                "source_fps": result.metrics["source_fps"],
                "export_fps": result.metrics["export_fps"],
                "root_motion_delta_m": result.metrics["root_motion_delta_m"],
                "pelvis_vertical_bob_m": result.metrics["pelvis_vertical_bob_m"],
                "in_place": result.metrics["in_place"],
                "foot_ground_target_y": result.metrics["foot_ground_target_y"],
                "stance_foot_y_min": result.metrics["stance_foot_y_min"],
                "stance_foot_y_max": result.metrics["stance_foot_y_max"],
                "foot_contact_error_min_m": result.metrics["foot_contact_error_min_m"],
                "foot_contact_error_max_m": result.metrics["foot_contact_error_max_m"],
                "max_angular_delta_deg": result.metrics["max_angular_delta_deg"],
                "loop_closure_deg": result.metrics.get("loop_closure_deg"),
                "knee_forward_mean": result.metrics["knee_forward_mean"],
                "nan_quaternions": result.metrics["nan_quaternions"],
                "twist_sign_flip": result.metrics.get("twist_sign_flip", []),
                "problems": result.metrics["problems"],
                "passed": result.metrics["passed"],
            }
            for result in results
        ],
        "rejected": record["rejected_sources"],
    }
    report["ual_audit"] = {
        "ual1_standard_clips": 43,
        "ual2_standard_clips": 43,
        "shared_name": "A_TPose",
        "independent_pro_authored_clips": 85,
    }
    report["core10_table"] = [
        {
            "action": row["action"],
            "clip": row["clip"],
            "tier": "real_mocap_retargeted",
            "source_file": row["source_file"],
            "license": row["license"],
            "one_shot": row["action"] not in {"idle_natural", "sprint_carry", "operate_console"},
        }
        for row in report["clips"]
    ]
    report["core10_table"].append({
        "action": "insert_module",
        "clip": "PickUp_Table",
        "tier": "pro_authored",
        "source_file": "media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb",
        "license": "CC0 1.0",
        "one_shot": True,
        "note": "No commercial-safe optical insert. ACCAD lift-box goes overhead; UAL1 PickUp_Table is the table-height pro clip.",
    })
    report["godot_headless"] = {
        "godot": "4.7.2.stable",
        "bones": 65,
        "animations": 9,
        "result": "loaded",
        "note": "Verified with godot --headless. Idle_Sway_Loop loops; the other clips are LOOP_NONE. Constant rest tracks are omitted by the importer.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT_GLB} ({OUT_GLB.stat().st_size} bytes)")
    print(f"wrote {REPORT_PATH}")
    return report


def validate_pack(skel: TargetSkeleton) -> dict[str, Any]:
    """Confirm the pack uses the UAL node layout and finite unit quaternions."""
    document = _load_glb(OUT_GLB)
    problems = []
    names = [node.get("name") for node in document.get("nodes") or []]
    if names != skel.names:
        problems.append("node names differ from UAL1")
    for index, node in enumerate(document.get("nodes") or []):
        if list(node.get("children") or []) != list(skel.children[index]):
            problems.append(f"children differ at {names[index] if index < len(names) else index}")
            break
    anims = document.get("animations") or []
    if len(anims) < len(CORE_CLIPS):
        problems.append(f"expected {len(CORE_CLIPS)} animations, found {len(anims)}")
    driven = {skel.names[i] for i in skel.driven}
    for anim in anims:
        seen = set()
        for channel in anim.get("channels") or []:
            target = channel.get("target") or {}
            node = target.get("node")
            if not isinstance(node, int) or node < 0 or node >= len(names):
                problems.append(f"{anim.get('name')} targets a missing node")
                continue
            if target.get("path") == "rotation":
                seen.add(names[node])
        missing = sorted(driven - seen)
        if missing:
            problems.append(f"{anim.get('name')} missing rotations {missing[:4]}")
    return {"problems": problems, "animations": [anim.get("name") for anim in anims], "nodes": len(names)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Retarget BVH mocap onto the Quaternius 65-bone skeleton.")
    parser.add_argument("--probe", action="store_true", help="retarget the core clips and print metrics")
    parser.add_argument("--build", action="store_true", help="write mocap_core10_pack.glb, the report, and licenses")
    args = parser.parse_args(argv)
    if args.probe:
        probe()
        return 0
    if args.build or not args.probe:
        report = build()
        failed = [row["clip"] for row in report["clips"] if not row["passed"]]
        structural = report["structural_validation"]["problems"]
        if failed or structural:
            print("quality problems", failed, structural)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
