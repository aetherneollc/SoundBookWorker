#!/usr/bin/env python3
"""Reusable semantic 3D scene layout solver for SoundBookWorker (godot3d).

Supports four modular scene templates out of the box:
  - ``observation_bay`` : lunar observation bay with wide viewport, console alcove, rover bay
  - ``corridor``        : ribbed sci-fi transit corridor with side terminal & clear central lane
  - ``med_lab``         : clinical medical & research bay with exam bed, bio-monitor, lab benches
  - ``command_deck``    : tactical command bridge with central holo-table, consoles & pillars

Every solved :class:`SceneLayout` declares:
  - room bounds, doors, windows, and safe ``camera_bounds``
  - semantic functional zones (``entry``, ``workstation``, ``window_view``,
    ``center_stage``, ``rover_bay``, ``exam_zone``, ``command_table``)
  - exact 3D obstacle AABBs (furniture, pillars, doorframe jambs)
  - validated standing anchors and prop surface anchors (with exact ``surface_y``)
  - wall-cleared camera candidate regions
  - direct export to the ``stage3d.json`` ``set`` block via :meth:`SceneLayout.to_stage3d_set`.
"""
from __future__ import annotations

import heapq
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KENNEY_DIR = ROOT / "media" / "assets3d" / "cc0" / "kenney_space_kit"
DEFAULT_TEXTURES_DIR = ROOT / "media" / "assets3d" / "textures"

SUPPORTED_SCENE_TYPES: tuple[str, ...] = (
    "observation_bay",
    "corridor",
    "med_lab",
    "command_deck",
)

SCENE_DEFAULT_LIGHTING: dict[str, str] = {
    "observation_bay": "day_bay",
    "corridor": "dim_corridor",
    "med_lab": "med_lab",
    "command_deck": "day_bay",
}

# Exact unscaled (width_x, height_y, depth_z) measured from media/assets3d/cc0/kenney_space_kit/*.glb
KENNEY_DIMENSIONS: dict[str, tuple[float, float, float]] = {
    "desk_computer.glb": (0.525, 0.375, 0.225),
    "desk_computerScreen.glb": (0.525, 0.637, 0.225),
    "desk_computerCorner.glb": (0.750, 0.375, 0.750),
    "desk_chair.glb": (0.150, 0.294, 0.150),
    "desk_chairArms.glb": (0.250, 0.344, 0.150),
    "desk_chairStool.glb": (0.150, 0.169, 0.150),
    "machine_wireless.glb": (0.750, 0.600, 0.500),
    "machine_wirelessCable.glb": (0.750, 0.800, 0.600),
    "machine_generator.glb": (0.700, 0.400, 0.525),
    "machine_generatorLarge.glb": (1.000, 0.683, 1.300),
    "machine_barrel.glb": (0.646, 0.600, 0.515),
    "machine_barrelLarge.glb": (0.754, 0.600, 0.754),
    "barrel.glb": (0.200, 0.250, 0.173),
    "barrels.glb": (0.550, 0.350, 0.496),
    "barrels_rail.glb": (0.550, 0.350, 0.496),
    "satelliteDish_large.glb": (0.849, 0.814, 0.737),
}

# Unscaled resting surface height (for props that host tabletop items).
KENNEY_SURFACE_Y: dict[str, float] = {
    "desk_computer.glb": 0.375,
    "desk_computerScreen.glb": 0.375,
    "desk_computerCorner.glb": 0.375,
}

# Default (width_x, height_y, depth_z) & resting surface_y for procedural bay.gd props.
PROCEDURAL_DEFAULTS: dict[str, tuple[tuple[float, float, float], float | None]] = {
    "crate": ((0.90, 0.90, 0.90), 0.90),
    "console": ((1.60, 0.92, 0.72), 0.92),
    "workstation": ((1.60, 0.92, 0.72), 0.92),
    "medical_bed": ((2.10, 0.88, 0.95), 0.88),
    "exam_bed": ((2.10, 0.88, 0.95), 0.88),
    "command_table": ((2.60, 0.95, 1.60), 0.95),
    "lab_bench": ((2.00, 0.90, 0.78), 0.90),
    "workbench": ((2.00, 0.90, 0.78), 0.90),
    "pillar": ((0.48, 3.40, 0.48), None),
    "column": ((0.48, 3.40, 0.48), None),
    "bio_monitor": ((1.10, 1.75, 0.26), None),
    "screen": ((1.10, 1.75, 0.26), None),
    "wall_screen": ((1.10, 1.75, 0.26), None),
    "cabinet": ((0.95, 2.00, 0.55), None),
    "locker": ((0.95, 2.00, 0.55), None),
    "bench": ((1.50, 0.48, 0.48), 0.48),
    "seat": ((1.50, 0.48, 0.48), 0.48),
}

WALL_THICKNESS = 0.20
DEFAULT_WALL_CLEARANCE = 0.35
DEFAULT_MIN_WALKWAY_M = 1.00
DEFAULT_PROP_MARGIN_M = 0.08
DEFAULT_ACTOR_RADIUS_M = 0.38
DEFAULT_DOORWAY_APRON_M = 1.50


def _r3(vals: tuple[float, ...] | list[float], digits: int = 3) -> list[float]:
    return [round(float(v), digits) for v in vals]


def _oriented_corners_2d(
    cx: float,
    cz: float,
    width: float,
    depth: float,
    yaw_deg: float,
) -> list[tuple[float, float]]:
    yaw = math.radians(yaw_deg)
    cos_y, sin_y = math.cos(yaw), math.sin(yaw)
    hw, hd = width * 0.5, depth * 0.5
    return [
        (cx + sx * hw * cos_y + sz * hd * sin_y, cz - sx * hw * sin_y + sz * hd * cos_y)
        for sx, sz in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))
    ]


def _polys_overlap_2d(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
    margin: float = 0.0,
) -> bool:
    for poly in (a, b):
        n = len(poly)
        for i in range(n):
            p, q = poly[i], poly[(i + 1) % n]
            ax, az = q[1] - p[1], p[0] - q[0]
            length = math.hypot(ax, az) or 1.0
            ax, az = ax / length, az / length
            pa = [c[0] * ax + c[1] * az for c in a]
            pb = [c[0] * ax + c[1] * az for c in b]
            if max(pa) + margin <= min(pb) or max(pb) + margin <= min(pa):
                return False
    return True


def _aabb_corners_2d(min_x: float, min_z: float, max_x: float, max_z: float) -> list[tuple[float, float]]:
    return [(min_x, min_z), (max_x, min_z), (max_x, max_z), (min_x, max_z)]


def _point_to_aabb_distance_2d(x: float, z: float, min_x: float, min_z: float, max_x: float, max_z: float) -> float:
    dx = max(min_x - x, 0.0, x - max_x)
    dz = max(min_z - z, 0.0, z - max_z)
    return math.hypot(dx, dz)


@dataclass
class ObstacleAABB:
    """Axis-aligned 3D bounding box for furniture, pillars, or doorframe jambs."""

    id: str
    kind: str
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]
    zone: str = ""
    footprint_2d: list[tuple[float, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.footprint_2d:
            self.footprint_2d = _aabb_corners_2d(
                self.min_xyz[0], self.min_xyz[2], self.max_xyz[0], self.max_xyz[2]
            )

    def distance_2d(self, x: float, z: float) -> float:
        return _point_to_aabb_distance_2d(x, z, self.min_xyz[0], self.min_xyz[2], self.max_xyz[0], self.max_xyz[2])

    def overlaps_2d(self, other: ObstacleAABB, margin: float = 0.0) -> bool:
        return _polys_overlap_2d(self.footprint_2d, other.footprint_2d, margin=margin)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "min": _r3(self.min_xyz),
            "max": _r3(self.max_xyz),
            "zone": self.zone,
        }


@dataclass
class Zone:
    """Semantic functional region inside a room."""

    name: str
    kind: str
    min_xz: tuple[float, float]
    max_xz: tuple[float, float]
    description: str = ""

    @property
    def center_xz(self) -> tuple[float, float]:
        return ((self.min_xz[0] + self.max_xz[0]) * 0.5, (self.min_xz[1] + self.max_xz[1]) * 0.5)

    def contains(self, x: float, z: float) -> bool:
        return self.min_xz[0] <= x <= self.max_xz[0] and self.min_xz[1] <= z <= self.max_xz[1]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "min": _r3(self.min_xz),
            "max": _r3(self.max_xz),
            "center": _r3(self.center_xz),
            "description": self.description,
        }


@dataclass
class StandingAnchor:
    """Validated floor position where an actor can stand or walk to."""

    id: str
    position: tuple[float, float, float]
    facing_yaw_deg: float
    zone: str
    role: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pos": _r3(self.position),
            "pos_xz": _r3((self.position[0], self.position[2])),
            "facing_yaw_deg": round(float(self.facing_yaw_deg), 2),
            "zone": self.zone,
            "role": self.role,
        }


@dataclass
class SurfaceAnchor:
    """Exact tabletop/surface location for resting interactive props without floating/sinking."""

    id: str
    position: tuple[float, float, float]
    surface_y: float
    width: float
    depth: float
    host_prop_id: str
    zone: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pos": _r3(self.position),
            "surface_y": round(float(self.surface_y), 4),
            "width": round(float(self.width), 3),
            "depth": round(float(self.depth), 3),
            "host_prop_id": self.host_prop_id,
            "zone": self.zone,
        }


@dataclass
class CameraRegion:
    """Pre-validated camera placement volume with wall and doorframe clearance."""

    id: str
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]
    zone: str
    preferred_look_xz: tuple[float, float] | None = None
    tags: tuple[str, ...] = ()

    @property
    def center_xyz(self) -> tuple[float, float, float]:
        return (
            (self.min_xyz[0] + self.max_xyz[0]) * 0.5,
            (self.min_xyz[1] + self.max_xyz[1]) * 0.5,
            (self.min_xyz[2] + self.max_xyz[2]) * 0.5,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "min": _r3(self.min_xyz),
            "max": _r3(self.max_xyz),
            "center": _r3(self.center_xyz),
            "zone": self.zone,
            "tags": list(self.tags),
        }
        if self.preferred_look_xz is not None:
            out["preferred_look_xz"] = _r3(self.preferred_look_xz)
        return out


@dataclass
class DoorSpec:
    """Doorway opening along a room wall."""

    id: str
    wall: str  # "back" (+Z), "front" (-Z), "left" (-X), "right" (+X)
    pos: float
    width: float = 1.6
    height: float = 2.4
    corridor_depth: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "wall": self.wall,
            "x": round(float(self.pos), 3),
            "pos": round(float(self.pos), 3),
            "width": round(float(self.width), 3),
            "height": round(float(self.height), 3),
            "corridor_depth": round(float(self.corridor_depth), 3),
        }


@dataclass
class WindowSpec:
    """Viewport window opening along a room wall."""

    id: str
    wall: str
    span_min: float
    span_max: float
    y_min: float = 0.95
    y_max: float = 3.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "wall": self.wall,
            "x_min": round(float(self.span_min), 3),
            "x_max": round(float(self.span_max), 3),
            "y_min": round(float(self.y_min), 3),
            "y_max": round(float(self.y_max), 3),
        }


@dataclass
class PlacedProp:
    """Placed furniture or architectural prop in a solved scene layout."""

    id: str
    kind: str  # "model" or procedural bay.gd kind ("console", "medical_bed", "command_table", "crate", ...)
    pos: tuple[float, float]
    yaw_deg: float = 0.0
    scale: float = 1.0
    size: tuple[float, float, float] | None = None
    model: str = ""
    color: str = ""
    zone: str = ""
    surface_y: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def resolved_dimensions(self, room_height: float = 3.4) -> tuple[float, float, float]:
        if self.size is not None:
            sx, sy, sz = self.size
            if self.kind in ("console", "workstation"):
                return (sx, sy + 0.56, sz)
            if self.kind == "command_table":
                return (sx, sy + 0.35, sz)
            if self.kind in ("medical_bed", "exam_bed"):
                return (sx, sy + 0.10, sz)
            return (sx, sy, sz)
        if self.model:
            base = KENNEY_DIMENSIONS.get(Path(self.model).name, (0.8, 0.8, 0.8))
            return (base[0] * self.scale, base[1] * self.scale, base[2] * self.scale)
        default_size, _ = PROCEDURAL_DEFAULTS.get(self.kind, ((0.8, 0.8, 0.8), None))
        if self.kind in ("pillar", "column"):
            return (default_size[0], room_height, default_size[2])
        return default_size

    def resolved_surface_y(self) -> float | None:
        if self.surface_y is not None:
            return self.surface_y
        if self.model:
            base_sy = KENNEY_SURFACE_Y.get(Path(self.model).name)
            return round(base_sy * self.scale, 4) if base_sy is not None else None
        if self.size is not None and self.kind in (
            "crate", "console", "workstation", "medical_bed", "exam_bed", "command_table", "lab_bench", "workbench", "bench", "seat"
        ):
            return round(float(self.size[1]), 4)
        _, default_sy = PROCEDURAL_DEFAULTS.get(self.kind, ((0.8, 0.8, 0.8), None))
        return default_sy

    def build_obstacle_aabb(self, room_height: float = 3.4) -> ObstacleAABB:
        w, h, d = self.resolved_dimensions(room_height)
        corners = _oriented_corners_2d(self.pos[0], self.pos[1], w, d, self.yaw_deg)
        xs = [c[0] for c in corners]
        zs = [c[1] for c in corners]
        return ObstacleAABB(
            id=self.id,
            kind=self.kind,
            min_xyz=(min(xs), 0.0, min(zs)),
            max_xyz=(max(xs), h, max(zs)),
            zone=self.zone,
            footprint_2d=corners,
        )

    def to_stage3d_dict(self, kenney_dir: Path | None = DEFAULT_KENNEY_DIR) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "pos": _r3(self.pos),
            "yaw_deg": round(float(self.yaw_deg), 2),
        }
        if self.model:
            model_path = Path(self.model)
            if not model_path.is_absolute() and kenney_dir is not None:
                model_path = kenney_dir / self.model
            out["model"] = model_path.as_posix()
            out["scale"] = round(float(self.scale), 3)
        else:
            out["kind"] = self.kind
            if self.size is not None:
                out["size"] = _r3(self.size)
            if self.color:
                out["color"] = self.color
        if self.zone:
            out["zone"] = self.zone
        if self.extra:
            out.update(self.extra)
        return out


@dataclass
class LayoutValidationReport:
    """Structured result of geometric and reachability validation."""

    valid: bool
    issues: list[str]
    min_walkway_width_m: float
    unreachable_anchors: list[str] = field(default_factory=list)
    overlapping_pairs: list[tuple[str, str]] = field(default_factory=list)
    doorway_violations: list[str] = field(default_factory=list)
    out_of_bounds_props: list[str] = field(default_factory=list)
    invalid_anchors: list[str] = field(default_factory=list)


@dataclass
class SceneLayout:
    """Complete solved layout for a modular 3D scene."""

    scene_type: str
    room_min: tuple[float, float, float]
    room_max: tuple[float, float, float]
    camera_bounds: dict[str, Any]
    doors: list[DoorSpec]
    windows: list[WindowSpec]
    lighting_profile: str
    zones: dict[str, Zone]
    props: list[PlacedProp]
    outside_props: list[dict[str, Any]]
    obstacles: list[ObstacleAABB]
    doorframe_obstacles: list[ObstacleAABB]
    standing_anchors: dict[str, StandingAnchor]
    surface_anchors: dict[str, SurfaceAnchor]
    camera_regions: list[CameraRegion]
    surfaces: dict[str, Any] = field(default_factory=dict)
    backdrop: dict[str, Any] = field(default_factory=dict)
    lighting_overrides: dict[str, Any] = field(default_factory=dict)
    kenney_dir: Path = DEFAULT_KENNEY_DIR

    def get_anchor(self, anchor_id: str) -> StandingAnchor | None:
        return self.standing_anchors.get(anchor_id)

    def get_surface(self, surface_id: str) -> SurfaceAnchor | None:
        return self.surface_anchors.get(surface_id)

    def all_obstacles(self) -> list[ObstacleAABB]:
        return list(self.obstacles) + list(self.doorframe_obstacles)

    def is_point_clear(self, x: float, z: float, radius: float = DEFAULT_ACTOR_RADIUS_M) -> bool:
        if not (
            self.room_min[0] + radius <= x <= self.room_max[0] - radius
            and self.room_min[2] + radius <= z <= self.room_max[2] - radius
        ):
            return False
        for obs in self.obstacles:
            if obs.distance_2d(x, z) < radius:
                return False
        return True

    def doorway_aprons(self, apron_depth: float = DEFAULT_DOORWAY_APRON_M) -> list[tuple[str, list[tuple[float, float]]]]:
        aprons: list[tuple[str, list[tuple[float, float]]]] = []
        for door in self.doors:
            half_w = door.width * 0.5 + 0.20
            if door.wall == "back":
                poly = _aabb_corners_2d(
                    door.pos - half_w,
                    self.room_max[2] - apron_depth,
                    door.pos + half_w,
                    self.room_max[2],
                )
            elif door.wall == "front":
                poly = _aabb_corners_2d(
                    door.pos - half_w,
                    self.room_min[2],
                    door.pos + half_w,
                    self.room_min[2] + apron_depth,
                )
            elif door.wall == "left":
                poly = _aabb_corners_2d(
                    self.room_min[0],
                    door.pos - half_w,
                    self.room_min[0] + apron_depth,
                    door.pos + half_w,
                )
            else:
                poly = _aabb_corners_2d(
                    self.room_max[0] - apron_depth,
                    door.pos - half_w,
                    self.room_max[0],
                    door.pos + half_w,
                )
            aprons.append((door.id, poly))
        return aprons

    def find_path(
        self,
        start_xz: tuple[float, float],
        goal_xz: tuple[float, float],
        clearance_radius: float = 0.50,
        cell_size: float = 0.20,
    ) -> list[tuple[float, float]] | None:
        """Grid A* pathfinder guaranteeing clear lane width >= 2 * clearance_radius."""
        nx = max(2, int(math.floor((self.room_max[0] - self.room_min[0]) / cell_size)))
        nz = max(2, int(math.floor((self.room_max[2] - self.room_min[2]) / cell_size)))

        def to_cell(x: float, z: float) -> tuple[int, int]:
            ix = max(0, min(nx - 1, int((x - self.room_min[0]) / cell_size)))
            iz = max(0, min(nz - 1, int((z - self.room_min[2]) / cell_size)))
            return (ix, iz)

        def to_world(ix: int, iz: int) -> tuple[float, float]:
            return (
                self.room_min[0] + (ix + 0.5) * cell_size,
                self.room_min[2] + (iz + 0.5) * cell_size,
            )

        free = [[False] * nz for _ in range(nx)]
        for ix in range(nx):
            for iz in range(nz):
                wx, wz = to_world(ix, iz)
                free[ix][iz] = self.is_point_clear(wx, wz, radius=clearance_radius)

        def nearest_free(target: tuple[int, int]) -> tuple[int, int] | None:
            if free[target[0]][target[1]]:
                return target
            best: tuple[int, int] | None = None
            best_d = 999999.0
            for dx in range(-4, 5):
                for dz in range(-4, 5):
                    cx, cz = target[0] + dx, target[1] + dz
                    if 0 <= cx < nx and 0 <= cz < nz and free[cx][cz]:
                        d = math.hypot(dx, dz)
                        if d < best_d:
                            best_d = d
                            best = (cx, cz)
            return best

        s_cell = nearest_free(to_cell(*start_xz))
        g_cell = nearest_free(to_cell(*goal_xz))
        if s_cell is None or g_cell is None:
            return None

        open_heap: list[tuple[float, tuple[int, int]]] = [(0.0, s_cell)]
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], float] = {s_cell: 0.0}
        neighbors = (
            (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
            (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414),
        )
        while open_heap:
            _, current = heapq.heappop(open_heap)
            if current == g_cell:
                rev = [to_world(*current)]
                while current in came_from:
                    current = came_from[current]
                    rev.append(to_world(*current))
                rev.reverse()
                return rev
            cur_g = g_score[current]
            for dx, dz, step_cost in neighbors:
                nxt = (current[0] + dx, current[1] + dz)
                if not (0 <= nxt[0] < nx and 0 <= nxt[1] < nz) or not free[nxt[0]][nxt[1]]:
                    continue
                tentative = cur_g + step_cost
                if tentative < g_score.get(nxt, math.inf):
                    came_from[nxt] = current
                    g_score[nxt] = tentative
                    h = math.hypot(nxt[0] - g_cell[0], nxt[1] - g_cell[1])
                    heapq.heappush(open_heap, (tentative + h, nxt))
        return None

    def validate(
        self,
        min_walkway_m: float = DEFAULT_MIN_WALKWAY_M,
        prop_margin_m: float = DEFAULT_PROP_MARGIN_M,
        actor_radius_m: float = DEFAULT_ACTOR_RADIUS_M,
        doorway_apron_m: float = DEFAULT_DOORWAY_APRON_M,
    ) -> LayoutValidationReport:
        """Validate zero overlap, room containment, doorway clearance, walkway width >= 1.0m, and anchors."""
        issues: list[str] = []
        overlapping_pairs: list[tuple[str, str]] = []
        doorway_violations: list[str] = []
        out_of_bounds_props: list[str] = []
        invalid_anchors: list[str] = []
        unreachable_anchors: list[str] = []

        # 1. Room containment & prop-prop overlap checks.
        for i, obs in enumerate(self.obstacles):
            if (
                obs.min_xyz[0] < self.room_min[0] - 1e-4
                or obs.max_xyz[0] > self.room_max[0] + 1e-4
                or obs.min_xyz[2] < self.room_min[2] - 1e-4
                or obs.max_xyz[2] > self.room_max[2] + 1e-4
                or obs.max_xyz[1] > self.room_max[1] + 0.05
            ):
                out_of_bounds_props.append(obs.id)
                issues.append(f"prop '{obs.id}' extends outside room bounds")

            for other in self.obstacles[i + 1 :]:
                if obs.overlaps_2d(other, margin=prop_margin_m):
                    overlapping_pairs.append((obs.id, other.id))
                    issues.append(f"props '{obs.id}' and '{other.id}' overlap (margin {prop_margin_m:.2f}m)")

        # 2. Doorway apron obstruction checks.
        for door_id, apron_poly in self.doorway_aprons(doorway_apron_m):
            for obs in self.obstacles:
                if _polys_overlap_2d(obs.footprint_2d, apron_poly, margin=0.0):
                    doorway_violations.append(f"{door_id}:{obs.id}")
                    issues.append(f"prop '{obs.id}' obstructs doorway '{door_id}' clearance apron")

        # 3. Standing anchors clearance & reachability with walkway width >= min_walkway_m.
        lane_radius = min_walkway_m * 0.5
        entry_anchor = self.standing_anchors.get("entry_threshold") or (
            next(iter(self.standing_anchors.values())) if self.standing_anchors else None
        )

        # Build BFS reachability grid from entry_anchor at clearance = lane_radius (0.5m => 1.0m wide lane).
        cell_size = 0.20
        nx = max(2, int(math.floor((self.room_max[0] - self.room_min[0]) / cell_size)))
        nz = max(2, int(math.floor((self.room_max[2] - self.room_min[2]) / cell_size)))
        free = [[False] * nz for _ in range(nx)]
        for ix in range(nx):
            wx = self.room_min[0] + (ix + 0.5) * cell_size
            for iz in range(nz):
                wz = self.room_min[2] + (iz + 0.5) * cell_size
                free[ix][iz] = self.is_point_clear(wx, wz, radius=lane_radius)

        def to_cell(x: float, z: float) -> tuple[int, int]:
            return (
                max(0, min(nx - 1, int((x - self.room_min[0]) / cell_size))),
                max(0, min(nz - 1, int((z - self.room_min[2]) / cell_size))),
            )

        visited: set[tuple[int, int]] = set()
        if entry_anchor is not None:
            sc = to_cell(entry_anchor.position[0], entry_anchor.position[2])
            if not free[sc[0]][sc[1]]:
                # Find closest free cell within 0.6m of entry threshold.
                found = None
                for r in range(1, 4):
                    for dx in range(-r, r + 1):
                        for dz in range(-r, r + 1):
                            cx, cz = sc[0] + dx, sc[1] + dz
                            if 0 <= cx < nx and 0 <= cz < nz and free[cx][cz]:
                                found = (cx, cz)
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    sc = found
            if free[sc[0]][sc[1]]:
                q: deque[tuple[int, int]] = deque([sc])
                visited.add(sc)
                while q:
                    cx, cz = q.popleft()
                    for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                        nx_i, nz_i = cx + dx, cz + dz
                        if 0 <= nx_i < nx and 0 <= nz_i < nz and free[nx_i][nz_i] and (nx_i, nz_i) not in visited:
                            visited.add((nx_i, nz_i))
                            q.append((nx_i, nz_i))

        min_measured_lane = math.inf
        for anchor_id, anchor in self.standing_anchors.items():
            ax, ay, az = anchor.position
            if abs(ay) > 0.05:
                invalid_anchors.append(anchor_id)
                issues.append(f"standing anchor '{anchor_id}' y={ay:.3f} is not on floor")
            if not self.is_point_clear(ax, az, radius=actor_radius_m):
                invalid_anchors.append(anchor_id)
                issues.append(f"standing anchor '{anchor_id}' violates actor clearance ({actor_radius_m:.2f}m)")
            # Measure local clear diameter at anchor.
            wall_d = min(
                ax - self.room_min[0],
                self.room_max[0] - ax,
                az - self.room_min[2],
                self.room_max[2] - az,
            )
            obs_d = min((obs.distance_2d(ax, az) for obs in self.obstacles), default=wall_d)
            min_measured_lane = min(min_measured_lane, 2.0 * min(wall_d, obs_d))

            # Check reachability on the 1.0m-wide clear lane grid.
            ac = to_cell(ax, az)
            reachable = ac in visited
            if not reachable:
                # Standing right next to a console (0.45m from desk) is still reachable if adjacent cell is on 1.0m grid.
                for dx in range(-2, 3):
                    for dz in range(-2, 3):
                        if (ac[0] + dx, ac[1] + dz) in visited:
                            reachable = True
                            break
                    if reachable:
                        break
            if not reachable:
                unreachable_anchors.append(anchor_id)
                issues.append(
                    f"standing anchor '{anchor_id}' is not reachable via a >= {min_walkway_m:.2f}m clear walkway"
                )

        # 4. Surface anchors must have positive surface_y and valid host prop.
        prop_ids = {p.id for p in self.props}
        for sid, surf in self.surface_anchors.items():
            if surf.host_prop_id not in prop_ids:
                invalid_anchors.append(sid)
                issues.append(f"surface anchor '{sid}' references missing host prop '{surf.host_prop_id}'")
            if not (0.25 <= surf.surface_y <= self.room_max[1] - 0.5):
                invalid_anchors.append(sid)
                issues.append(f"surface anchor '{sid}' has out-of-range surface_y={surf.surface_y}")
            if abs(surf.position[1] - surf.surface_y) > 1e-3:
                invalid_anchors.append(sid)
                issues.append(f"surface anchor '{sid}' position.y != surface_y")

        # 5. Camera candidate regions must stay >= 0.35m from walls & doorframes.
        for region in self.camera_regions:
            if (
                region.min_xyz[0] < self.room_min[0] + DEFAULT_WALL_CLEARANCE - 1e-4
                or region.max_xyz[0] > self.room_max[0] - DEFAULT_WALL_CLEARANCE + 1e-4
                or region.min_xyz[2] < self.room_min[2] + DEFAULT_WALL_CLEARANCE - 1e-4
                or region.max_xyz[2] > self.room_max[2] - DEFAULT_WALL_CLEARANCE + 1e-4
            ):
                issues.append(f"camera region '{region.id}' violates {DEFAULT_WALL_CLEARANCE:.2f}m wall clearance")

        return LayoutValidationReport(
            valid=len(issues) == 0,
            issues=issues,
            min_walkway_width_m=round(min_measured_lane if min_measured_lane < math.inf else min_walkway_m, 3),
            unreachable_anchors=unreachable_anchors,
            overlapping_pairs=overlapping_pairs,
            doorway_violations=doorway_violations,
            out_of_bounds_props=out_of_bounds_props,
            invalid_anchors=invalid_anchors,
        )

    def to_stage3d_set(self, resolve_asset_paths: bool = True) -> dict[str, Any]:
        """Export directly into stage3d.json's ``set`` dictionary format."""
        kenney = self.kenney_dir if resolve_asset_paths else None
        primary_door = next((d for d in self.doors if d.wall == "back"), self.doors[0])
        front_win = next((w for w in self.windows if w.wall == "front"), None)

        outside = []
        for item in self.outside_props:
            copy = dict(item)
            if "model" in copy and kenney is not None and not Path(str(copy["model"])).is_absolute():
                copy["model"] = (kenney / str(copy["model"])).as_posix()
            outside.append(copy)

        marks = {
            aid: _r3((a.position[0], a.position[2]))
            for aid, a in self.standing_anchors.items()
        }
        kind_map = {
            "observation_bay": "lunar_observation_bay",
            "corridor": "station_corridor",
            "med_lab": "medical_laboratory",
            "command_deck": "tactical_command_deck",
        }
        set_doc: dict[str, Any] = {
            "scene_type": self.scene_type,
            "kind": kind_map.get(self.scene_type, self.scene_type),
            "room": {"min": _r3(self.room_min), "max": _r3(self.room_max)},
            "door": primary_door.to_dict(),
            "doors": [d.to_dict() for d in self.doors],
            "window": front_win.to_dict() if front_win is not None else {"enabled": False},
            "windows": [w.to_dict() for w in self.windows],
            "camera_bounds": self.camera_bounds,
            "lighting_profile": self.lighting_profile,
            "lighting": dict(self.lighting_overrides),
            "backdrop": dict(self.backdrop),
            "surfaces": dict(self.surfaces),
            "props": [p.to_stage3d_dict(kenney) for p in self.props],
            "outside_props": outside,
            "marks": marks,
            "zones": {k: z.to_dict() for k, z in self.zones.items()},
            "obstacles": [o.to_dict() for o in self.obstacles],
            "doorframe_obstacles": [o.to_dict() for o in self.doorframe_obstacles],
            "standing_anchors": {k: a.to_dict() for k, a in self.standing_anchors.items()},
            "surface_anchors": {k: s.to_dict() for k, s in self.surface_anchors.items()},
            "camera_regions": [r.to_dict() for r in self.camera_regions],
        }
        return set_doc


class SceneLayoutSystem:
    """Reusable parametric scene layout solver for modular 3D rooms."""

    def __init__(
        self,
        kenney_dir: Path = DEFAULT_KENNEY_DIR,
        textures_dir: Path = DEFAULT_TEXTURES_DIR,
    ) -> None:
        self.kenney_dir = Path(kenney_dir)
        self.textures_dir = Path(textures_dir)

    @staticmethod
    def supported_scene_types() -> tuple[str, ...]:
        return SUPPORTED_SCENE_TYPES

    def solve(
        self,
        scene_type: str,
        *,
        room_size: tuple[float, float, float] | None = None,
        lighting_profile: str | None = None,
        extra_props: list[dict[str, Any]] | None = None,
        validate: bool = True,
    ) -> SceneLayout:
        """Build and validate a complete semantic layout for ``scene_type``."""
        stype = scene_type.strip().lower()
        if stype not in SUPPORTED_SCENE_TYPES:
            raise ValueError(f"Unsupported scene_type {scene_type!r}; expected one of {SUPPORTED_SCENE_TYPES}")

        profile = lighting_profile or SCENE_DEFAULT_LIGHTING[stype]
        if stype == "observation_bay":
            layout = self._build_observation_bay(room_size, profile)
        elif stype == "corridor":
            layout = self._build_corridor(room_size, profile)
        elif stype == "med_lab":
            layout = self._build_med_lab(room_size, profile)
        else:
            layout = self._build_command_deck(room_size, profile)

        if extra_props:
            self._append_extra_props(layout, extra_props)

        if validate:
            report = layout.validate()
            if not report.valid:
                raise ValueError(f"SceneLayout '{stype}' failed validation: " + "; ".join(report.issues))
        return layout

    # ------------------------------------------------------- shared builders

    def _room_bounds(
        self,
        default_size: tuple[float, float, float],
        room_size: tuple[float, float, float] | None,
        z_offset: float = 0.0,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        w, h, d = room_size if room_size is not None else default_size
        return (
            (-w * 0.5, 0.0, -d * 0.5 + z_offset),
            (w * 0.5, h, d * 0.5 + z_offset),
        )

    def _camera_bounds(
        self,
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        doors: list[DoorSpec],
    ) -> dict[str, Any]:
        margin = DEFAULT_WALL_CLEARANCE
        back_door = next((d for d in doors if d.wall == "back"), None)
        corridor_max_z = (
            room_max[2] + back_door.corridor_depth - 0.35
            if back_door and back_door.corridor_depth > 0.2
            else room_max[2] - margin
        )
        return {
            "min": _r3((room_min[0] + margin, 0.30, room_min[2] + margin)),
            "max": _r3((room_max[0] - margin, room_max[1] - 0.30, room_max[2] - margin)),
            "corridor_max_z": round(corridor_max_z, 3),
        }

    def _doorframe_obstacles(
        self,
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        doors: list[DoorSpec],
    ) -> list[ObstacleAABB]:
        out: list[ObstacleAABB] = []
        for door in doors:
            half = door.width * 0.5
            if door.wall == "back":
                z = room_max[2]
                out.append(ObstacleAABB(f"{door.id}_jamb_l", "doorframe", (door.pos - half - 0.14, 0.0, z - 0.08), (door.pos - half + 0.04, door.height + 0.14, z + WALL_THICKNESS + 0.04), "entry"))
                out.append(ObstacleAABB(f"{door.id}_jamb_r", "doorframe", (door.pos + half - 0.04, 0.0, z - 0.08), (door.pos + half + 0.14, door.height + 0.14, z + WALL_THICKNESS + 0.04), "entry"))
            elif door.wall == "front":
                z = room_min[2]
                out.append(ObstacleAABB(f"{door.id}_jamb_l", "doorframe", (door.pos - half - 0.14, 0.0, z - WALL_THICKNESS - 0.04), (door.pos - half + 0.04, door.height + 0.14, z + 0.08), "entry"))
                out.append(ObstacleAABB(f"{door.id}_jamb_r", "doorframe", (door.pos + half - 0.04, 0.0, z - WALL_THICKNESS - 0.04), (door.pos + half + 0.14, door.height + 0.14, z + 0.08), "entry"))
        return out

    def _place_props_safely(
        self,
        candidates: list[PlacedProp],
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        aprons: list[tuple[str, list[tuple[float, float]]]],
        margin: float = DEFAULT_PROP_MARGIN_M,
    ) -> tuple[list[PlacedProp], list[ObstacleAABB]]:
        placed: list[PlacedProp] = []
        obstacles: list[ObstacleAABB] = []
        room_h = room_max[1]
        for prop in candidates:
            obs = prop.build_obstacle_aabb(room_h)
            in_room = (
                obs.min_xyz[0] >= room_min[0] + 0.02
                and obs.max_xyz[0] <= room_max[0] - 0.02
                and obs.min_xyz[2] >= room_min[2] + 0.02
                and obs.max_xyz[2] <= room_max[2] - 0.02
            )
            if not in_room:
                continue
            if any(_polys_overlap_2d(obs.footprint_2d, apron, margin=0.02) for _, apron in aprons):
                continue
            if any(obs.overlaps_2d(existing, margin=margin) for existing in obstacles):
                continue
            placed.append(prop)
            obstacles.append(obs)
        return placed, obstacles

    def _append_extra_props(self, layout: SceneLayout, extra_props: list[dict[str, Any]]) -> None:
        aprons = layout.doorway_aprons()
        room_h = layout.room_max[1]
        for item in extra_props:
            pos_raw = item.get("pos", [0.0, 0.0])
            size_raw = item.get("size")
            prop = PlacedProp(
                id=str(item.get("id", f"extra_{len(layout.props)}")),
                kind=str(item.get("kind", "model" if "model" in item else "crate")),
                pos=(float(pos_raw[0]), float(pos_raw[-1])),
                yaw_deg=float(item.get("yaw_deg", 0.0)),
                scale=float(item.get("scale", 1.0)),
                size=tuple(float(v) for v in size_raw) if isinstance(size_raw, (list, tuple)) and len(size_raw) >= 3 else None,
                model=str(item.get("model", "")),
                color=str(item.get("color", "")),
                zone=str(item.get("zone", "center_stage")),
            )
            obs = prop.build_obstacle_aabb(room_h)
            if (
                obs.min_xyz[0] < layout.room_min[0] + 0.02
                or obs.max_xyz[0] > layout.room_max[0] - 0.02
                or obs.min_xyz[2] < layout.room_min[2] + 0.02
                or obs.max_xyz[2] > layout.room_max[2] - 0.02
            ):
                raise ValueError(f"extra prop '{prop.id}' is outside room bounds")
            if any(_polys_overlap_2d(obs.footprint_2d, apron, margin=0.0) for _, apron in aprons):
                raise ValueError(f"extra prop '{prop.id}' blocks a doorway apron")
            if any(obs.overlaps_2d(existing, margin=DEFAULT_PROP_MARGIN_M) for existing in layout.obstacles):
                raise ValueError(f"extra prop '{prop.id}' overlaps existing furniture")
            layout.props.append(prop)
            layout.obstacles.append(obs)

    def _surface_anchor_for(self, prop: PlacedProp, anchor_id: str) -> SurfaceAnchor | None:
        sy = prop.resolved_surface_y()
        if sy is None:
            return None
        w, _, d = prop.resolved_dimensions()
        return SurfaceAnchor(
            id=anchor_id,
            position=(prop.pos[0], sy, prop.pos[1]),
            surface_y=sy,
            width=w * 0.75,
            depth=d * 0.75,
            host_prop_id=prop.id,
            zone=prop.zone,
        )

    def _bay_surfaces(self, include_console_screen: bool = False, console_pos: tuple[float, float] = (-3.5, -4.0)) -> dict[str, Any]:
        surfaces: dict[str, Any] = {}
        floor_tex = self.textures_dir / "bay_floor.png"
        wall_tex = self.textures_dir / "bay_wall.png"
        screen_tex = self.textures_dir / "console_screen.png"
        if floor_tex.is_file():
            surfaces["floor"] = {
                "texture": floor_tex.as_posix(),
                "tile_m": 2.5,
                "roughness": 0.65,
                "metallic": 0.1,
                "specular": 0.5,
                "tint": "#e6eaf0",
            }
        if wall_tex.is_file():
            surfaces["wall"] = {
                "texture": wall_tex.as_posix(),
                "tile_m": 3.0,
                "roughness": 0.78,
                "specular": 0.35,
                "tint": "#ffffff",
                "low_tint": "#b8c0cc",
            }
        if include_console_screen and screen_tex.is_file():
            surfaces["console_screen"] = {
                "texture": screen_tex.as_posix(),
                "pos": _r3((console_pos[0], 1.36, console_pos[1])),
                "size": [0.94, 0.53],
                "tilt_deg": 8.0,
                "emission": 0.65,
                "stand_bottom_y": 0.97,
            }
        return surfaces

    def _lunar_backdrop(self) -> dict[str, Any]:
        for name in ("lunar_panorama_clean.png", "lunar_panorama.png"):
            image = self.textures_dir / name
            if image.is_file():
                return {
                    "mode": "cyclorama",
                    "image": image.as_posix(),
                    "radius": 100.0,
                    "arc_deg": 140.0,
                    "center": [0.0, 0.0],
                    "horizon_v": 0.852,
                    "horizon_y": 1.45,
                    "ground_y": -2.4,
                    "near_ground": "#34486b",
                }
        return {
            "mode": "procedural",
            "ground_y": -2.4,
            "seed": 7,
            "planet": {"pos": [-34.0, 26.0, -330.0], "radius": 62.0},
        }

    # ------------------------------------------------------ template 1: observation_bay

    def _build_observation_bay(
        self,
        room_size: tuple[float, float, float] | None,
        lighting_profile: str,
    ) -> SceneLayout:
        room_min, room_max = self._room_bounds((14.0, 3.4, 8.5), room_size, z_offset=-0.25)
        w = room_max[0] - room_min[0]
        d = room_max[2] - room_min[2]
        doors = [DoorSpec("main_door", "back", 0.0, width=1.6, height=2.4, corridor_depth=3.0)]
        win_margin = min(1.5, w * 0.14)
        windows = [WindowSpec("viewport", "front", room_min[0] + win_margin, room_max[0] - win_margin, 0.95, min(room_max[1] - 0.4, 3.0))]
        cam_bounds = self._camera_bounds(room_min, room_max, doors)
        jambs = self._doorframe_obstacles(room_min, room_max, doors)

        zones = {
            "entry": Zone("entry", "entry", (-1.5, room_max[2] - 2.0), (1.5, room_max[2]), "Main bay entrance doorway apron"),
            "workstation": Zone("workstation", "workstation", (room_min[0] + 0.4, room_min[2] + 0.2), (-w * 0.12, room_min[2] + d * 0.42), "Telemetry console workstation"),
            "window_view": Zone("window_view", "window_view", (-w * 0.22, room_min[2] + 0.4), (w * 0.22, room_min[2] + d * 0.32), "Panoramic lunar viewport lookout"),
            "center_stage": Zone("center_stage", "center_stage", (-w * 0.26, -d * 0.16), (w * 0.22, d * 0.20), "Central briefing and dialogue floor"),
            "rover_bay": Zone("rover_bay", "rover_bay", (w * 0.14, room_min[2] + 0.6), (room_max[0] - 1.4, room_max[2] - 1.8), "Rover parking and cargo staging"),
        }

        ws_x = room_min[0] + w * 0.26
        ws_z = room_min[2] + 0.72
        corner_x = room_min[0] + 1.50
        corner_z = room_min[2] + 0.96
        candidates = [
            PlacedProp("console_main", "model", (ws_x, ws_z), 0.0, 2.6, model="desk_computer.glb", zone="workstation"),
            PlacedProp("console_corner", "model", (corner_x, corner_z), 0.0, 1.9, model="desk_computerCorner.glb", zone="workstation"),
            PlacedProp("chair", "model", (ws_x - 1.05, ws_z + 1.35), 160.0, 2.6, model="desk_chair.glb", zone="workstation"),
            PlacedProp("generator", "model", (room_max[0] - 1.25, room_max[2] - 1.25), -90.0, 1.4, model="machine_generatorLarge.glb", zone="rover_bay"),
            PlacedProp("wireless", "model", (room_max[0] - 0.82, room_max[2] - 2.95), -90.0, 1.7, model="machine_wireless.glb", zone="rover_bay"),
            PlacedProp("barrel_a", "model", (room_min[0] + 0.95, room_max[2] - 1.10), 90.0, 1.6, model="machine_barrel.glb", zone="entry"),
            PlacedProp("barrel_b", "model", (room_min[0] + 1.05, room_max[2] - 2.55), 0.0, 1.4, model="machine_barrelLarge.glb", zone="entry"),
            PlacedProp("barrels", "model", (room_max[0] - 3.35, room_max[2] - 0.75), 0.0, 1.8, model="barrels.glb", zone="rover_bay"),
            PlacedProp("crate_a", "crate", (room_max[0] - 2.00, -0.35), 6.0, size=(0.9, 0.9, 0.9), color="orange", zone="rover_bay"),
            PlacedProp("crate_b", "crate", (room_max[0] - 0.92, -0.15), -10.0, size=(0.75, 0.6, 0.75), color="dark", zone="rover_bay"),
        ]
        temp_layout = SceneLayout(
            "observation_bay", room_min, room_max, cam_bounds, doors, windows,
            lighting_profile, zones, [], [], [], jambs, {}, {}, [], kenney_dir=self.kenney_dir
        )
        props, obstacles = self._place_props_safely(candidates, room_min, room_max, temp_layout.doorway_aprons())

        standing_anchors = {
            "entry_threshold": StandingAnchor("entry_threshold", (0.0, 0.0, room_max[2] - 0.85), 180.0, "entry", "entry"),
            "workstation_operator": StandingAnchor("workstation_operator", (ws_x + 0.35, 0.0, ws_z + 0.78), 180.0, "workstation", "operator"),
            "workstation_partner": StandingAnchor("workstation_partner", (ws_x + 0.98, 0.0, ws_z + 1.25), -125.0, "workstation", "partner"),
            "center_a": StandingAnchor("center_a", (-0.85, 0.0, 0.25), 90.0, "center_stage", "speaker"),
            "center_b": StandingAnchor("center_b", (0.85, 0.0, 0.25), -90.0, "center_stage", "listener"),
            "window_observer": StandingAnchor("window_observer", (0.0, 0.0, room_min[2] + 1.25), 180.0, "window_view", "observer"),
            "rover_bay_spot": StandingAnchor("rover_bay_spot", (w * 0.19, 0.0, room_min[2] + 1.35), -60.0, "rover_bay", "rover"),
        }

        surface_anchors: dict[str, SurfaceAnchor] = {}
        for p in props:
            if p.id in ("console_main", "console_corner", "crate_a"):
                sa = self._surface_anchor_for(p, f"{p.id}_top")
                if sa is not None:
                    surface_anchors[sa.id] = sa

        camera_regions = [
            CameraRegion("cam_establishing_corner", (room_max[0] - 2.6, 1.9, room_max[2] - 2.2), (room_max[0] - 0.6, 2.7, room_max[2] - 0.6), "entry", (-1.5, -1.8), ("establishing", "wide")),
            CameraRegion("cam_workstation_front", (ws_x - 0.6, 1.25, ws_z + 2.2), (ws_x + 2.6, 1.85, room_max[2] - 1.2), "workstation", (ws_x, ws_z + 0.5), ("two_shot", "medium", "close_up")),
            CameraRegion("cam_center_south", (-2.2, 1.25, 1.0), (2.2, 1.95, room_max[2] - 0.8), "center_stage", (0.0, -1.0), ("wide", "two_shot", "medium")),
            CameraRegion("cam_rover_angle", (0.4, 0.95, -1.2), (2.6, 1.65, 1.4), "rover_bay", (w * 0.22, room_min[2] + 1.4), ("medium", "wide")),
        ]

        outside_props = [
            {"id": "dish", "model": "satelliteDish_large.glb", "pos": [-8.8, -12.5], "yaw_deg": 30.0, "scale": 4.2},
            {"id": "catwalk", "kind": "catwalk", "pos": [1.5, -12.0], "length": 24.0, "depth": 1.3, "deck_y": -0.15},
        ]
        lighting_overrides = {
            "fills": [
                {"id": "ConsoleGlow", "pos": _r3((ws_x + 0.15, 1.5, ws_z + 0.3)), "range": 3.0, "energy": 0.48, "color": "#8fe6ff"},
                {"id": "FaceFill", "pos": [-1.2, 1.9, -1.2], "range": 4.2, "energy": 0.38, "color": "#fff0dc"},
                {"id": "WindowSpill", "pos": [0.0, 2.2, room_min[2] + 0.6], "range": 6.5, "energy": 0.35, "color": "#b9d0ff"},
            ]
        }
        return SceneLayout(
            scene_type="observation_bay",
            room_min=room_min,
            room_max=room_max,
            camera_bounds=cam_bounds,
            doors=doors,
            windows=windows,
            lighting_profile=lighting_profile,
            zones=zones,
            props=props,
            outside_props=outside_props,
            obstacles=obstacles,
            doorframe_obstacles=jambs,
            standing_anchors=standing_anchors,
            surface_anchors=surface_anchors,
            camera_regions=camera_regions,
            surfaces=self._bay_surfaces(include_console_screen=True, console_pos=(ws_x - 0.25, ws_z - 0.20)),
            backdrop=self._lunar_backdrop(),
            lighting_overrides=lighting_overrides,
            kenney_dir=self.kenney_dir,
        )

    # ------------------------------------------------------------- template 2: corridor

    def _build_corridor(
        self,
        room_size: tuple[float, float, float] | None,
        lighting_profile: str,
    ) -> SceneLayout:
        room_min, room_max = self._room_bounds((4.8, 3.0, 15.0), room_size)
        doors = [
            DoorSpec("south_door", "back", 0.0, width=1.6, height=2.4, corridor_depth=2.0),
            DoorSpec("north_door", "front", 0.0, width=1.6, height=2.4, corridor_depth=0.0),
        ]
        windows: list[WindowSpec] = []
        cam_bounds = self._camera_bounds(room_min, room_max, doors)
        jambs = self._doorframe_obstacles(room_min, room_max, doors)

        zones = {
            "entry": Zone("entry", "entry", (-1.4, room_max[2] - 2.4), (1.4, room_max[2]), "South bulkhead entrance"),
            "center_stage": Zone("center_stage", "center_stage", (-1.5, -2.5), (1.5, 2.5), "Central transit intersection"),
            "workstation": Zone("workstation", "workstation", (room_min[0] + 0.1, -3.5), (room_min[0] + 1.4, -0.5), "Wall security terminal alcove"),
            "window_view": Zone("window_view", "window_view", (room_max[0] - 1.4, -1.5), (room_max[0] - 0.1, 2.0), "Side diagnostic bulkhead"),
        }

        left_wall = room_min[0]
        right_wall = room_max[0]
        # Every prop hugs a side wall <= 0.65m deep so the center transit lane stays >= 3.2m wide!
        candidates = [
            PlacedProp("corridor_terminal", "model", (left_wall + 0.34, -2.0), 90.0, 2.1, model="desk_computerScreen.glb", zone="workstation"),
            PlacedProp("corridor_cabinet_l", "cabinet", (left_wall + 0.34, 2.6), 90.0, size=(1.1, 2.0, 0.52), zone="center_stage"),
            PlacedProp("corridor_relay_l", "model", (left_wall + 0.42, -4.6), 90.0, 0.95, model="machine_wireless.glb", zone="workstation"),
            PlacedProp("corridor_screen_r", "wall_screen", (right_wall - 0.22, -0.8), -90.0, size=(1.2, 1.75, 0.24), zone="window_view"),
            PlacedProp("corridor_bench_r", "bench", (right_wall - 0.32, 1.8), -90.0, size=(1.6, 0.48, 0.46), zone="center_stage"),
            PlacedProp("corridor_crate_r", "crate", (right_wall - 0.40, -4.2), -85.0, size=(0.65, 0.65, 0.65), color="orange", zone="center_stage"),
        ]
        temp_layout = SceneLayout(
            "corridor", room_min, room_max, cam_bounds, doors, windows,
            lighting_profile, zones, [], [], [], jambs, {}, {}, [], kenney_dir=self.kenney_dir
        )
        props, obstacles = self._place_props_safely(candidates, room_min, room_max, temp_layout.doorway_aprons())

        standing_anchors = {
            "entry_threshold": StandingAnchor("entry_threshold", (0.0, 0.0, room_max[2] - 1.0), 180.0, "entry", "entry"),
            "corridor_south": StandingAnchor("corridor_south", (0.0, 0.0, 3.2), 180.0, "center_stage", "walk_mid"),
            "center_a": StandingAnchor("center_a", (-0.55, 0.0, 0.4), 90.0, "center_stage", "speaker"),
            "center_b": StandingAnchor("center_b", (0.55, 0.0, -0.5), -90.0, "center_stage", "listener"),
            "workstation_operator": StandingAnchor("workstation_operator", (left_wall + 1.15, 0.0, -2.0), -90.0, "workstation", "operator"),
            "corridor_north": StandingAnchor("corridor_north", (0.0, 0.0, room_min[2] + 1.4), 0.0, "center_stage", "exit"),
        }

        surface_anchors: dict[str, SurfaceAnchor] = {}
        for p in props:
            if p.id in ("corridor_terminal", "corridor_crate_r", "corridor_bench_r"):
                sa = self._surface_anchor_for(p, f"{p.id}_top")
                if sa is not None:
                    surface_anchors[sa.id] = sa

        camera_regions = [
            CameraRegion("cam_corridor_south_wide", (-0.9, 1.45, 3.6), (0.9, 2.25, room_max[2] - 1.0), "entry", (0.0, -2.0), ("establishing", "wide", "tracking")),
            CameraRegion("cam_corridor_mid", (-1.0, 1.30, -1.8), (1.0, 1.90, 2.2), "center_stage", (0.0, 0.0), ("medium", "two_shot", "over_the_shoulder")),
            CameraRegion("cam_corridor_north_wide", (-0.9, 1.45, room_min[2] + 1.0), (0.9, 2.25, -3.6), "center_stage", (0.0, 1.5), ("wide", "tracking")),
        ]

        return SceneLayout(
            scene_type="corridor",
            room_min=room_min,
            room_max=room_max,
            camera_bounds=cam_bounds,
            doors=doors,
            windows=windows,
            lighting_profile=lighting_profile,
            zones=zones,
            props=props,
            outside_props=[],
            obstacles=obstacles,
            doorframe_obstacles=jambs,
            standing_anchors=standing_anchors,
            surface_anchors=surface_anchors,
            camera_regions=camera_regions,
            surfaces={},
            backdrop={"mode": "none"},
            lighting_overrides={
                "fills": [
                    {"id": "TerminalGlow", "pos": _r3((left_wall + 0.75, 1.45, -2.0)), "range": 3.0, "energy": 0.45, "color": "#72d8ff"},
                ]
            },
            kenney_dir=self.kenney_dir,
        )

    # -------------------------------------------------------------- template 3: med_lab

    def _build_med_lab(
        self,
        room_size: tuple[float, float, float] | None,
        lighting_profile: str,
    ) -> SceneLayout:
        room_min, room_max = self._room_bounds((11.0, 3.2, 9.0), room_size)
        doors = [DoorSpec("lab_door", "back", 0.0, width=1.6, height=2.4, corridor_depth=2.4)]
        windows: list[WindowSpec] = []
        cam_bounds = self._camera_bounds(room_min, room_max, doors)
        jambs = self._doorframe_obstacles(room_min, room_max, doors)

        zones = {
            "entry": Zone("entry", "entry", (-1.5, room_max[2] - 2.0), (1.5, room_max[2]), "Sterile lab airlock entry"),
            "exam_zone": Zone("exam_zone", "exam_zone", (0.8, room_min[2] + 0.5), (room_max[0] - 0.4, 1.5), "Patient examination & bio-scan bay"),
            "workstation": Zone("workstation", "workstation", (room_min[0] + 0.4, room_min[2] + 0.3), (-0.8, 1.5), "Analytical workbench and diagnostic console"),
            "center_stage": Zone("center_stage", "center_stage", (-1.8, -1.5), (1.6, 2.2), "Central consultation area"),
        }

        exam_x = room_max[0] - 2.15
        exam_z = room_min[2] + 2.35
        bench_x = room_min[0] + 1.95
        bench_z = room_min[2] + 0.56
        candidates = [
            PlacedProp("exam_bed_main", "medical_bed", (exam_x, exam_z), 0.0, size=(2.1, 0.88, 0.95), zone="exam_zone"),
            PlacedProp("bio_monitor_main", "bio_monitor", (exam_x - 0.25, room_min[2] + 0.32), 0.0, size=(1.1, 1.75, 0.26), zone="exam_zone"),
            PlacedProp("lab_bench_main", "lab_bench", (bench_x, bench_z), 0.0, size=(2.1, 0.90, 0.78), zone="workstation"),
            PlacedProp("lab_bench_side", "lab_bench", (room_min[0] + 0.54, 0.25), 90.0, size=(2.0, 0.90, 0.74), zone="workstation"),
            PlacedProp("diag_console", "model", (bench_x + 2.05, room_min[2] + 0.48), 0.0, 2.2, model="desk_computerScreen.glb", zone="workstation"),
            PlacedProp("lab_stool", "model", (bench_x, bench_z + 0.92), 180.0, 2.4, model="desk_chairStool.glb", zone="workstation"),
            PlacedProp("med_cabinet_l", "cabinet", (room_min[0] + 0.45, room_max[2] - 1.35), 90.0, size=(1.05, 2.0, 0.55), zone="entry"),
            PlacedProp("med_cabinet_r", "cabinet", (room_max[0] - 0.45, room_max[2] - 1.35), -90.0, size=(1.05, 2.0, 0.55), zone="exam_zone"),
            PlacedProp("med_supply_case", "crate", (room_max[0] - 1.10, 1.15), -15.0, size=(0.72, 0.62, 0.62), color="dark", zone="exam_zone"),
        ]
        temp_layout = SceneLayout(
            "med_lab", room_min, room_max, cam_bounds, doors, windows,
            lighting_profile, zones, [], [], [], jambs, {}, {}, [], kenney_dir=self.kenney_dir
        )
        props, obstacles = self._place_props_safely(candidates, room_min, room_max, temp_layout.doorway_aprons())

        standing_anchors = {
            "entry_threshold": StandingAnchor("entry_threshold", (0.0, 0.0, room_max[2] - 0.9), 180.0, "entry", "entry"),
            "exam_doctor": StandingAnchor("exam_doctor", (exam_x - 0.35, 0.0, exam_z + 1.08), 180.0, "exam_zone", "doctor"),
            "exam_assistant": StandingAnchor("exam_assistant", (exam_x - 1.70, 0.0, exam_z - 0.15), 90.0, "exam_zone", "assistant"),
            "workstation_operator": StandingAnchor("workstation_operator", (bench_x + 1.95, 0.0, room_min[2] + 1.25), 180.0, "workstation", "operator"),
            "center_a": StandingAnchor("center_a", (-0.95, 0.0, 0.55), 90.0, "center_stage", "speaker"),
            "center_b": StandingAnchor("center_b", (0.65, 0.0, 0.55), -90.0, "center_stage", "listener"),
        }

        surface_anchors: dict[str, SurfaceAnchor] = {}
        for p in props:
            if p.id in ("exam_bed_main", "lab_bench_main", "lab_bench_side", "diag_console", "med_supply_case"):
                sa = self._surface_anchor_for(p, f"{p.id}_top")
                if sa is not None:
                    surface_anchors[sa.id] = sa

        camera_regions = [
            CameraRegion("cam_lab_establishing", (-2.6, 1.85, room_max[2] - 2.0), (2.4, 2.55, room_max[2] - 0.6), "entry", (0.0, -1.5), ("establishing", "wide")),
            CameraRegion("cam_exam_view", (-0.8, 1.25, 0.2), (1.8, 1.85, 2.6), "exam_zone", (exam_x, exam_z), ("medium", "two_shot", "close_up")),
            CameraRegion("cam_workstation_view", (-2.2, 1.25, -0.8), (1.0, 1.85, 2.2), "workstation", (bench_x, bench_z), ("medium", "over_the_shoulder", "insert")),
        ]

        return SceneLayout(
            scene_type="med_lab",
            room_min=room_min,
            room_max=room_max,
            camera_bounds=cam_bounds,
            doors=doors,
            windows=windows,
            lighting_profile=lighting_profile,
            zones=zones,
            props=props,
            outside_props=[],
            obstacles=obstacles,
            doorframe_obstacles=jambs,
            standing_anchors=standing_anchors,
            surface_anchors=surface_anchors,
            camera_regions=camera_regions,
            surfaces={},
            backdrop={"mode": "none"},
            lighting_overrides={
                "fills": [
                    {"id": "BioMonitorGlow", "pos": _r3((exam_x - 0.2, 1.6, room_min[2] + 0.8)), "range": 3.5, "energy": 0.45, "color": "#4ce6d8"},
                    {"id": "BenchTaskLight", "pos": _r3((bench_x, 1.9, bench_z + 0.4)), "range": 4.2, "energy": 0.50, "color": "#f4fbff"},
                ]
            },
            kenney_dir=self.kenney_dir,
        )

    # --------------------------------------------------------- template 4: command_deck

    def _build_command_deck(
        self,
        room_size: tuple[float, float, float] | None,
        lighting_profile: str,
    ) -> SceneLayout:
        room_min, room_max = self._room_bounds((14.0, 3.8, 10.5), room_size, z_offset=-0.25)
        w = room_max[0] - room_min[0]
        doors = [DoorSpec("bridge_door", "back", 0.0, width=1.8, height=2.5, corridor_depth=3.0)]
        win_margin = min(1.8, w * 0.15)
        windows = [WindowSpec("bridge_viewport", "front", room_min[0] + win_margin, room_max[0] - win_margin, 0.95, min(room_max[1] - 0.45, 3.2))]
        cam_bounds = self._camera_bounds(room_min, room_max, doors)
        jambs = self._doorframe_obstacles(room_min, room_max, doors)

        zones = {
            "entry": Zone("entry", "entry", (-1.8, room_max[2] - 2.2), (1.8, room_max[2]), "Bridge blast-door entry"),
            "command_table": Zone("command_table", "command_table", (-2.6, -2.0), (2.6, 1.6), "Central holographic command table"),
            "workstation": Zone("workstation", "workstation", (room_min[0] + 0.4, room_min[2] + 0.4), (room_max[0] - 0.4, room_min[2] + 2.4), "Forward helm & tactical consoles"),
            "window_view": Zone("window_view", "window_view", (-2.8, room_min[2] + 0.3), (2.8, room_min[2] + 2.2), "Forward command viewport"),
            "center_stage": Zone("center_stage", "center_stage", (-3.4, -2.6), (3.4, 2.4), "Command table briefing perimeter"),
        }

        table_z = -0.30
        candidates = [
            PlacedProp("command_table_main", "command_table", (0.0, table_z), 0.0, size=(2.6, 0.95, 1.6), zone="command_table"),
            PlacedProp("helm_console", "console", (-3.1, room_min[2] + 0.72), 0.0, size=(1.8, 0.92, 0.72), zone="workstation"),
            PlacedProp("tactical_console", "console", (3.1, room_min[2] + 0.72), 0.0, size=(1.8, 0.92, 0.72), zone="workstation"),
            PlacedProp("helm_chair", "model", (-3.1, room_min[2] + 1.68), 180.0, 2.5, model="desk_chairArms.glb", zone="workstation"),
            PlacedProp("tactical_chair", "model", (3.1, room_min[2] + 1.68), 180.0, 2.5, model="desk_chairArms.glb", zone="workstation"),
            PlacedProp("side_console_l", "model", (room_min[0] + 0.55, -0.30), 90.0, 2.5, model="desk_computer.glb", zone="workstation"),
            PlacedProp("side_console_r", "model", (room_max[0] - 0.55, -0.30), -90.0, 2.5, model="desk_computer.glb", zone="workstation"),
            PlacedProp("wall_screen_l", "wall_screen", (room_min[0] + 0.35, 2.10), 90.0, size=(1.2, 1.85, 0.26), zone="center_stage"),
            PlacedProp("wall_screen_r", "wall_screen", (room_max[0] - 0.35, 2.10), -90.0, size=(1.2, 1.85, 0.26), zone="center_stage"),
            PlacedProp("pillar_nw", "pillar", (room_min[0] + 0.95, room_min[2] + 1.15), 0.0, size=(0.48, room_max[1], 0.48), zone="window_view"),
            PlacedProp("pillar_ne", "pillar", (room_max[0] - 0.95, room_min[2] + 1.15), 0.0, size=(0.48, room_max[1], 0.48), zone="window_view"),
            PlacedProp("pillar_sw", "pillar", (room_min[0] + 0.95, room_max[2] - 1.15), 0.0, size=(0.48, room_max[1], 0.48), zone="entry"),
            PlacedProp("pillar_se", "pillar", (room_max[0] - 0.95, room_max[2] - 1.15), 0.0, size=(0.48, room_max[1], 0.48), zone="entry"),
            PlacedProp("comms_relay", "model", (room_max[0] - 2.35, room_max[2] - 0.85), 180.0, 1.35, model="machine_wirelessCable.glb", zone="entry"),
        ]
        temp_layout = SceneLayout(
            "command_deck", room_min, room_max, cam_bounds, doors, windows,
            lighting_profile, zones, [], [], [], jambs, {}, {}, [], kenney_dir=self.kenney_dir
        )
        props, obstacles = self._place_props_safely(candidates, room_min, room_max, temp_layout.doorway_aprons())

        standing_anchors = {
            "entry_threshold": StandingAnchor("entry_threshold", (0.0, 0.0, room_max[2] - 1.0), 180.0, "entry", "entry"),
            "command_north": StandingAnchor("command_north", (0.0, 0.0, table_z - 1.38), 0.0, "command_table", "commander"),
            "command_south": StandingAnchor("command_south", (0.0, 0.0, table_z + 1.38), 180.0, "command_table", "officer"),
            "command_west": StandingAnchor("command_west", (-1.92, 0.0, table_z), 90.0, "command_table", "specialist"),
            "command_east": StandingAnchor("command_east", (1.92, 0.0, table_z), -90.0, "command_table", "specialist"),
            "helm_operator": StandingAnchor("helm_operator", (-3.1, 0.0, room_min[2] + 2.40), 180.0, "workstation", "operator"),
            "tactical_operator": StandingAnchor("tactical_operator", (3.1, 0.0, room_min[2] + 2.40), 180.0, "workstation", "operator"),
            "window_observer": StandingAnchor("window_observer", (0.0, 0.0, room_min[2] + 1.15), 180.0, "window_view", "observer"),
        }

        surface_anchors: dict[str, SurfaceAnchor] = {}
        for p in props:
            if p.id in ("command_table_main", "helm_console", "tactical_console", "side_console_l", "side_console_r"):
                sa = self._surface_anchor_for(p, f"{p.id}_top")
                if sa is not None:
                    surface_anchors[sa.id] = sa

        camera_regions = [
            CameraRegion("cam_deck_establishing", (-3.2, 2.1, room_max[2] - 2.2), (3.2, 3.0, room_max[2] - 0.7), "entry", (0.0, table_z), ("establishing", "wide")),
            CameraRegion("cam_table_southwest", (-4.4, 1.35, 0.8), (-1.4, 2.05, 3.2), "center_stage", (0.0, table_z), ("two_shot", "medium", "over_the_shoulder")),
            CameraRegion("cam_table_southeast", (1.4, 1.35, 0.8), (4.4, 2.05, 3.2), "center_stage", (0.0, table_z), ("two_shot", "medium", "over_the_shoulder")),
            CameraRegion("cam_viewport_reverse", (-2.4, 1.35, room_min[2] + 0.9), (2.4, 2.0, room_min[2] + 2.3), "window_view", (0.0, table_z), ("medium", "close_up", "reaction")),
        ]

        return SceneLayout(
            scene_type="command_deck",
            room_min=room_min,
            room_max=room_max,
            camera_bounds=cam_bounds,
            doors=doors,
            windows=windows,
            lighting_profile=lighting_profile,
            zones=zones,
            props=props,
            outside_props=[],
            obstacles=obstacles,
            doorframe_obstacles=jambs,
            standing_anchors=standing_anchors,
            surface_anchors=surface_anchors,
            camera_regions=camera_regions,
            surfaces=self._bay_surfaces(include_console_screen=False),
            backdrop=self._lunar_backdrop(),
            lighting_overrides={
                "fills": [
                    {"id": "HoloTableGlow", "pos": [0.0, 1.35, table_z], "range": 4.2, "energy": 0.65, "color": "#58d4ff"},
                    {"id": "HelmGlow", "pos": _r3((-3.1, 1.45, room_min[2] + 1.0)), "range": 3.0, "energy": 0.40, "color": "#7be2ff"},
                    {"id": "TacticalGlow", "pos": _r3((3.1, 1.45, room_min[2] + 1.0)), "range": 3.0, "energy": 0.40, "color": "#7be2ff"},
                ]
            },
            kenney_dir=self.kenney_dir,
        )
