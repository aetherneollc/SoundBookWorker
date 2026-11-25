#!/usr/bin/env python3
"""Reusable, deterministic 3D CinematicDirector for SoundBookWorker (godot3d).

Plans cinematic camera coverage automatically from high-level beat semantics
(speaker, listener, emotion, action, prop_event, narrative_importance,
preferred_shot, actor positions/facings/tiers, room bounds, doors, obstacles)
across any characters, rooms, or episodes.

Supported canonical shot types (all 10):
  1. ``establishing``
  2. ``wide``
  3. ``medium``
  4. ``close_up``
  5. ``extreme_close_up``  (refused on TIER_B / non-BlendShape actors)
  6. ``over_the_shoulder``
  7. ``two_shot``
  8. ``insert``
  9. ``reaction``
 10. ``tracking``

(``pov`` is only permitted when explicitly marked ``pov_required=True``.)

Emits shot definitions directly compatible with ``stage3d.json`` ``shots[]``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

SHOT_TYPES: tuple[str, ...] = (
    "establishing",
    "wide",
    "medium",
    "close_up",
    "extreme_close_up",
    "over_the_shoulder",
    "two_shot",
    "insert",
    "reaction",
    "tracking",
)

SHOT_ALIASES: dict[str, str] = {
    "closeup": "close_up",
    "ecu": "extreme_close_up",
    "ots": "over_the_shoulder",
    "over_shoulder": "over_the_shoulder",
    "twoshot": "two_shot",
    "wide_follow": "tracking",
    "follow": "tracking",
    "detail": "insert",
    "cutaway": "reaction",
    "first_person": "pov",
}

WALL_CLEARANCE_MIN_M = 0.35
DOORFRAME_CLEARANCE_MIN_M = 0.35
OBSTACLE_CAMERA_PADDING_M = 0.20
DEFAULT_ACTOR_HEIGHT_M = 1.70
DEFAULT_FPS = 24
WALL_THICKNESS = 0.20

HIGH_EMOTIONS = frozenset({"angry", "fear", "sad", "surprised", "worried", "tense", "urgent", "shocked"})
MOVEMENT_ACTIONS = frozenset({"walk", "enter", "arrive", "run", "move", "patrol", "approach", "exit"})
PROP_INSERT_EVENTS = frozenset({"inspect", "pickup", "reveal", "use", "activate", "examine", "scan"})


def _r3(vals: tuple[float, ...] | list[float], digits: int = 3) -> list[float]:
    return [round(float(v), digits) for v in vals]


def _v3_sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v3_add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _v3_scale(a: tuple[float, float, float], s: float) -> tuple[float, float, float]:
    return (a[0] * s, a[1] * s, a[2] * s)


def _v3_len(a: tuple[float, float, float]) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _v3_dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return _v3_len(_v3_sub(a, b))


def _v2_norm(dx: float, dz: float) -> tuple[float, float]:
    length = math.hypot(dx, dz)
    if length < 1e-6:
        return (0.0, 1.0)
    return (dx / length, dz / length)


def _parse_pos3(raw: Any, default_y: float = 0.0) -> tuple[float, float, float]:
    if isinstance(raw, (list, tuple)):
        if len(raw) >= 3:
            return (float(raw[0]), float(raw[1]), float(raw[2]))
        if len(raw) == 2:
            return (float(raw[0]), default_y, float(raw[1]))
    return (0.0, default_y, 0.0)


def _parse_facing_2d(raw: Any) -> tuple[float, float]:
    """Convert yaw_deg (0=+Z, 90=+X) or 2D vector [dx, dz] into unit XZ vector."""
    if isinstance(raw, (int, float)):
        rad = math.radians(float(raw))
        return (math.sin(rad), math.cos(rad))
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return _v2_norm(float(raw[0]), float(raw[-1]))
    return (0.0, 1.0)


def _parse_importance(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    mapping = {"low": 0.25, "normal": 0.50, "medium": 0.55, "high": 0.80, "climax": 0.95}
    return mapping.get(str(value).strip().lower(), 0.50)


@dataclass
class AABB3:
    """3D axis-aligned bounding box used for occlusion & clearance checks."""

    id: str
    min_xyz: tuple[float, float, float]
    max_xyz: tuple[float, float, float]
    kind: str = "prop"

    def contains_point(self, p: tuple[float, float, float], padding: float = 0.0) -> bool:
        return (
            self.min_xyz[0] - padding <= p[0] <= self.max_xyz[0] + padding
            and self.min_xyz[1] - padding <= p[1] <= self.max_xyz[1] + padding
            and self.min_xyz[2] - padding <= p[2] <= self.max_xyz[2] + padding
        )

    def distance_to_point(self, p: tuple[float, float, float]) -> float:
        dx = max(self.min_xyz[0] - p[0], 0.0, p[0] - self.max_xyz[0])
        dy = max(self.min_xyz[1] - p[1], 0.0, p[1] - self.max_xyz[1])
        dz = max(self.min_xyz[2] - p[2], 0.0, p[2] - self.max_xyz[2])
        return math.sqrt(dx * dx + dy * dy + dz * dz)


def segment_intersects_aabb(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
    box: AABB3,
    end_trim_m: float = 0.0,
    padding: float = 0.0,
) -> bool:
    """Exact 3D slab test for line segment a->b against an AABB."""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    seg_len = math.sqrt(dx * dx + dy * dy + dz * dz)
    if seg_len < 1e-6:
        return box.contains_point(a, padding=padding)
    t_limit = max(0.0, min(1.0, (seg_len - end_trim_m) / seg_len))
    if t_limit <= 0.01:
        return False

    t_min = 0.0
    t_max = t_limit
    dirs = (dx, dy, dz)
    lo = (box.min_xyz[0] - padding, box.min_xyz[1] - padding, box.min_xyz[2] - padding)
    hi = (box.max_xyz[0] + padding, box.max_xyz[1] + padding, box.max_xyz[2] + padding)
    for axis in range(3):
        d = dirs[axis]
        orig = a[axis]
        if abs(d) < 1e-8:
            if orig < lo[axis] or orig > hi[axis]:
                return False
        else:
            inv_d = 1.0 / d
            t1 = (lo[axis] - orig) * inv_d
            t2 = (hi[axis] - orig) * inv_d
            if t1 > t2:
                t1, t2 = t2, t1
            t_min = max(t_min, t1)
            t_max = min(t_max, t2)
            if t_min > t_max:
                return False
    return t_max >= 0.0 and t_min <= t_limit


@dataclass
class BeatSpec:
    """High-level semantic input describing one story beat."""

    beat_id: str
    duration: float = 3.5
    frames: int | None = None
    speaker: str = ""
    listener: str = ""
    emotion: str = "neutral"
    action: str = "talk"
    prop_event: dict[str, Any] | None = None
    narrative_importance: float = 0.5
    preferred_shot: str | None = None
    pov_required: bool = False
    actor_positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    actor_end_positions: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    actor_facings: dict[str, tuple[float, float]] = field(default_factory=dict)
    actor_tiers: dict[str, Any] = field(default_factory=dict)
    actor_heights: dict[str, float] = field(default_factory=dict)
    room_bounds: dict[str, Any] = field(default_factory=dict)
    doors: list[dict[str, Any]] = field(default_factory=list)
    obstacles: list[AABB3] = field(default_factory=list)
    camera_regions: list[dict[str, Any]] = field(default_factory=list)
    scene_number: int = 1
    description: str = ""
    lines: list[dict[str, Any]] = field(default_factory=list)
    voice_lead_frames: int = 0

    @classmethod
    def from_input(
        cls,
        raw: BeatSpec | dict[str, Any],
        default_room_bounds: dict[str, Any] | None = None,
        default_obstacles: list[AABB3] | None = None,
        default_doors: list[dict[str, Any]] | None = None,
        default_camera_regions: list[dict[str, Any]] | None = None,
        default_actor_tiers: dict[str, Any] | None = None,
    ) -> BeatSpec:
        if isinstance(raw, BeatSpec):
            beat = raw
            if not beat.room_bounds and default_room_bounds:
                beat.room_bounds = dict(default_room_bounds)
            if not beat.obstacles and default_obstacles:
                beat.obstacles = list(default_obstacles)
            if not beat.doors and default_doors:
                beat.doors = list(default_doors)
            if not beat.camera_regions and default_camera_regions:
                beat.camera_regions = list(default_camera_regions)
            if default_actor_tiers:
                merged_tiers = dict(default_actor_tiers)
                merged_tiers.update(beat.actor_tiers)
                beat.actor_tiers = merged_tiers
            return beat

        pos_map = {str(k): _parse_pos3(v, 0.0) for k, v in (raw.get("actor_positions") or {}).items()}
        end_map = {str(k): _parse_pos3(v, 0.0) for k, v in (raw.get("actor_end_positions") or {}).items()}
        facing_map = {str(k): _parse_facing_2d(v) for k, v in (raw.get("actor_facings") or {}).items()}
        heights = {str(k): float(v) for k, v in (raw.get("actor_heights") or {}).items()}
        tiers = dict(default_actor_tiers or {})
        tiers.update(raw.get("actor_tiers") or {})

        room = raw.get("room_bounds") or raw.get("room") or default_room_bounds or {
            "min": [-6.0, 0.0, -4.5],
            "max": [6.0, 3.4, 4.5],
        }
        doors = list(raw.get("doors") or [])
        if not doors and isinstance(raw.get("door"), dict):
            doors = [dict(raw["door"])]
        if not doors and default_doors:
            doors = list(default_doors)

        obs_list = _parse_obstacles(raw.get("obstacles")) if "obstacles" in raw else list(default_obstacles or [])
        cam_regions = list(raw.get("camera_regions") or default_camera_regions or [])

        return cls(
            beat_id=str(raw.get("beat_id") or raw.get("id") or "beat"),
            duration=float(raw.get("duration", 3.5)),
            frames=int(raw["frames"]) if raw.get("frames") is not None else None,
            speaker=str(raw.get("speaker") or ""),
            listener=str(raw.get("listener") or ""),
            emotion=str(raw.get("emotion") or "neutral").strip().lower(),
            action=str(raw.get("action") or "talk").strip().lower(),
            prop_event=dict(raw["prop_event"]) if isinstance(raw.get("prop_event"), dict) else None,
            narrative_importance=_parse_importance(raw.get("narrative_importance", 0.5)),
            preferred_shot=str(raw["preferred_shot"]).strip().lower() if raw.get("preferred_shot") else None,
            pov_required=bool(raw.get("pov_required", False)),
            actor_positions=pos_map,
            actor_end_positions=end_map,
            actor_facings=facing_map,
            actor_tiers=tiers,
            actor_heights=heights,
            room_bounds=dict(room),
            doors=doors,
            obstacles=obs_list,
            camera_regions=cam_regions,
            scene_number=int(raw.get("scene_number", 1)),
            description=str(raw.get("description", "")),
            lines=list(raw.get("lines") or []),
            voice_lead_frames=int(raw.get("voice_lead_frames", 0)),
        )


def _parse_obstacles(raw_obstacles: Any) -> list[AABB3]:
    out: list[AABB3] = []
    if not raw_obstacles:
        return out
    for idx, item in enumerate(raw_obstacles):
        if isinstance(item, AABB3):
            out.append(item)
        elif hasattr(item, "min_xyz") and hasattr(item, "max_xyz"):
            out.append(AABB3(str(getattr(item, "id", f"obs_{idx}")), tuple(item.min_xyz), tuple(item.max_xyz), str(getattr(item, "kind", "prop"))))
        elif isinstance(item, dict) and "min" in item and "max" in item:
            out.append(AABB3(str(item.get("id", f"obs_{idx}")), _parse_pos3(item["min"]), _parse_pos3(item["max"]), str(item.get("kind", "prop"))))
    return out


def _doorframe_aabbs(
    room_min: tuple[float, float, float],
    room_max: tuple[float, float, float],
    doors: list[dict[str, Any]],
) -> list[AABB3]:
    out: list[AABB3] = []
    for idx, door in enumerate(doors):
        wall = str(door.get("wall", "back")).lower()
        dpos = float(door.get("pos", door.get("x", 0.0)))
        width = float(door.get("width", 1.6))
        height = float(door.get("height", 2.4))
        half = width * 0.5
        did = str(door.get("id", f"door_{idx}"))
        if wall == "back":
            z = room_max[2]
            out.append(AABB3(f"{did}_jamb_l", (dpos - half - 0.14, 0.0, z - 0.08), (dpos - half + 0.04, height + 0.14, z + WALL_THICKNESS + 0.04), "doorframe"))
            out.append(AABB3(f"{did}_jamb_r", (dpos + half - 0.04, 0.0, z - 0.08), (dpos + half + 0.14, height + 0.14, z + WALL_THICKNESS + 0.04), "doorframe"))
        elif wall == "front":
            z = room_min[2]
            out.append(AABB3(f"{did}_jamb_l", (dpos - half - 0.14, 0.0, z - WALL_THICKNESS - 0.04), (dpos - half + 0.04, height + 0.14, z + 0.08), "doorframe"))
            out.append(AABB3(f"{did}_jamb_r", (dpos + half - 0.04, 0.0, z - WALL_THICKNESS - 0.04), (dpos + half + 0.14, height + 0.14, z + 0.08), "doorframe"))
        elif wall == "left":
            x = room_min[0]
            out.append(AABB3(f"{did}_jamb_l", (x - WALL_THICKNESS - 0.04, 0.0, dpos - half - 0.14), (x + 0.08, height + 0.14, dpos - half + 0.04), "doorframe"))
            out.append(AABB3(f"{did}_jamb_r", (x - WALL_THICKNESS - 0.04, 0.0, dpos + half - 0.04), (x + 0.08, height + 0.14, dpos + half + 0.14), "doorframe"))
        elif wall == "right":
            x = room_max[0]
            out.append(AABB3(f"{did}_jamb_l", (x - 0.08, 0.0, dpos - half - 0.14), (x + WALL_THICKNESS + 0.04, height + 0.14, dpos - half + 0.04), "doorframe"))
            out.append(AABB3(f"{did}_jamb_r", (x - 0.08, 0.0, dpos + half - 0.04), (x + WALL_THICKNESS + 0.04, height + 0.14, dpos + half + 0.14), "doorframe"))
    return out


@dataclass
class CameraCandidate:
    """One geometric camera placement candidate evaluated by CinematicDirector."""

    candidate_id: str
    mode: str  # "dolly" | "static" | "pov"
    pos: tuple[float, float, float]
    pos_to: tuple[float, float, float]
    look_at: tuple[float, float, float]
    look_to: tuple[float, float, float]
    fov: float
    fov_to: float
    dof_enabled: bool = False
    dof_focus_distance: float = 2.5
    roll_deg: float = 0.0
    pov_actor: str = ""
    axis_side: int = 0
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    wall_clearance_m: float = 0.0
    doorframe_clearance_m: float = 99.0
    occluded: bool = False
    crosses_180_axis: bool = False
    frustum_edge_blocked: bool = False


class CinematicDirector:
    """Stateful, deterministic cinematic camera planner across a scene's beats.

    Maintains 180-degree line-of-action continuity and pacing history across
    consecutive beats while never hardcoding single-episode coordinates.
    """

    def __init__(
        self,
        fps: int = DEFAULT_FPS,
        wall_clearance_m: float = WALL_CLEARANCE_MIN_M,
        doorframe_clearance_m: float = DOORFRAME_CLEARANCE_MIN_M,
    ) -> None:
        self.fps = max(1, int(fps))
        self.wall_clearance_m = float(wall_clearance_m)
        self.doorframe_clearance_m = float(doorframe_clearance_m)
        self.reset_state()

    def reset_state(self) -> None:
        """Reset 180-degree axis and narrative pacing state for a new scene."""
        self._active_pair: tuple[str, str] | None = None
        self._axis_side: int = 0
        self._shot_history: list[str] = []
        self._consecutive_tracking: int = 0
        self._beats_since_reaction: int = 0
        self._beats_since_insert: int = 0

    @property
    def active_axis(self) -> tuple[tuple[str, str] | None, int]:
        """Current (canonical_actor_pair, side_sign) tracked for the 180-degree rule."""
        return (self._active_pair, self._axis_side)

    @staticmethod
    def normalize_shot_type(raw: str | None) -> str | None:
        if not raw:
            return None
        key = raw.strip().lower()
        key = SHOT_ALIASES.get(key, key)
        if key in SHOT_TYPES or key == "pov":
            return key
        return None

    @staticmethod
    def can_extreme_close_up(actor_id: str, beat: BeatSpec) -> bool:
        """Refuse extreme close-ups on TIER_B / non-BlendShape actors."""
        if not actor_id:
            return True
        spec = beat.actor_tiers.get(actor_id)
        if spec is None:
            return True
        if isinstance(spec, str):
            norm = spec.strip().upper().replace("-", "_")
            if norm in ("TIER_B", "B", "FALLBACK", "HEAD_ONLY", "NO_BLENDSHAPE"):
                return False
            return True
        if isinstance(spec, dict):
            tier = str(spec.get("tier", "TIER_A")).strip().upper().replace("-", "_")
            has_bs = spec.get("blendshapes")
            cap = str(spec.get("facial_capability", "")).strip().lower()
            if has_bs is False or cap in ("head_only", "none"):
                return False
            if tier in ("TIER_B", "B") and has_bs is not True:
                return False
            return True
        return True

    @staticmethod
    def axis_side_for_point(
        pos_a: tuple[float, float, float],
        pos_b: tuple[float, float, float],
        cam_pos: tuple[float, float, float],
    ) -> int:
        """Signed side (+1 / -1) of ``cam_pos`` relative to the directed line A->B in XZ."""
        ax, az = pos_b[0] - pos_a[0], pos_b[2] - pos_a[2]
        if math.hypot(ax, az) < 1e-4:
            return 0
        cross = ax * (cam_pos[2] - pos_a[2]) - az * (cam_pos[0] - pos_a[0])
        return 1 if cross >= 0.0 else -1

    # --------------------------------------------------------- public API

    def plan_sequence(
        self,
        beats: list[BeatSpec | dict[str, Any]],
        *,
        layout: Any | None = None,
        actor_tiers: dict[str, Any] | None = None,
        enable_cutaways: bool = True,
        reset: bool = True,
    ) -> list[dict[str, Any]]:
        """Plan contiguous stage3d-compatible shots for a full sequence of beats."""
        if reset:
            self.reset_state()

        default_room = None
        default_obstacles: list[AABB3] = []
        default_doors: list[dict[str, Any]] = []
        default_regions: list[dict[str, Any]] = []
        if layout is not None:
            if hasattr(layout, "to_stage3d_set"):
                set_doc = layout.to_stage3d_set(resolve_asset_paths=False)
            elif isinstance(layout, dict):
                set_doc = layout
            else:
                set_doc = {}
            default_room = set_doc.get("room")
            default_obstacles = _parse_obstacles(set_doc.get("obstacles"))
            default_doors = list(set_doc.get("doors") or ([set_doc["door"]] if isinstance(set_doc.get("door"), dict) else []))
            default_regions = list(set_doc.get("camera_regions") or [])

        planned: list[dict[str, Any]] = []
        cursor = 0
        for idx, raw_beat in enumerate(beats):
            beat = BeatSpec.from_input(
                raw_beat,
                default_room_bounds=default_room,
                default_obstacles=default_obstacles,
                default_doors=default_doors,
                default_camera_regions=default_regions,
                default_actor_tiers=actor_tiers,
            )
            total_frames = beat.frames if beat.frames is not None else max(12, int(round(beat.duration * self.fps)))

            # Optional narrative cutaway planning:
            # 1) Prop event insert on key dialogue/handoff beats (when no explicit shot override forces otherwise).
            # 2) Listener reaction cutaway on long high-importance emotional dialogue beats.
            if (
                enable_cutaways
                and beat.preferred_shot is None
                and total_frames >= int(self.fps * 3.2)
                and beat.prop_event is not None
                and beat.speaker
                and beat.action == "handoff"
            ):
                main_frames = int(round(total_frames * 0.62))
                insert_frames = total_frames - main_frames
                main_beat = self._clone_beat(beat, f"{beat.beat_id}_main", main_frames, preferred_shot="over_the_shoulder")
                insert_beat = self._clone_beat(beat, f"{beat.beat_id}_insert", insert_frames, preferred_shot="insert")
                for sub in (main_beat, insert_beat):
                    shot = self.plan_beat(sub, start_frame=cursor)
                    planned.append(shot)
                    cursor += shot["frames"]
                continue

            if (
                enable_cutaways
                and beat.preferred_shot is None
                and total_frames >= int(self.fps * 3.6)
                and beat.speaker
                and beat.listener
                and beat.listener in beat.actor_positions
                and beat.narrative_importance >= 0.72
                and (beat.emotion in HIGH_EMOTIONS or self._beats_since_reaction >= 2)
                and idx > 0
            ):
                main_frames = int(round(total_frames * 0.68))
                react_frames = total_frames - main_frames
                main_beat = self._clone_beat(beat, f"{beat.beat_id}_speak", main_frames)
                react_beat = self._clone_beat(beat, f"{beat.beat_id}_reaction", react_frames, preferred_shot="reaction")
                for sub in (main_beat, react_beat):
                    shot = self.plan_beat(sub, start_frame=cursor)
                    planned.append(shot)
                    cursor += shot["frames"]
                continue

            shot = self.plan_beat(beat, start_frame=cursor)
            planned.append(shot)
            cursor += shot["frames"]
        return planned

    def plan_beat(
        self,
        raw_beat: BeatSpec | dict[str, Any],
        *,
        start_frame: int = 0,
        layout: Any | None = None,
    ) -> dict[str, Any]:
        """Plan one cinematic shot from a beat specification."""
        default_room = None
        default_obstacles: list[AABB3] = []
        default_doors: list[dict[str, Any]] = []
        default_regions: list[dict[str, Any]] = []
        if layout is not None:
            set_doc = layout.to_stage3d_set(resolve_asset_paths=False) if hasattr(layout, "to_stage3d_set") else dict(layout)
            default_room = set_doc.get("room")
            default_obstacles = _parse_obstacles(set_doc.get("obstacles"))
            default_doors = list(set_doc.get("doors") or ([set_doc["door"]] if isinstance(set_doc.get("door"), dict) else []))
            default_regions = list(set_doc.get("camera_regions") or [])

        beat = BeatSpec.from_input(
            raw_beat,
            default_room_bounds=default_room,
            default_obstacles=default_obstacles,
            default_doors=default_doors,
            default_camera_regions=default_regions,
        )
        frames = beat.frames if beat.frames is not None else max(12, int(round(beat.duration * self.fps)))
        room_min = _parse_pos3(beat.room_bounds.get("min", [-6.0, 0.0, -4.5]))
        room_max = _parse_pos3(beat.room_bounds.get("max", [6.0, 3.4, 4.5]))
        jambs = _doorframe_aabbs(room_min, room_max, beat.doors)

        shot_type, primary_subject, secondary_subject, downgrade_reason = self._choose_shot_type(beat)
        pair_key, pair_positions = self._resolve_interaction_pair(beat, primary_subject, secondary_subject)

        # Reset 180-degree axis on establishing shot or when switching to a brand-new dialogue pair.
        if shot_type == "establishing":
            self._active_pair = None
            self._axis_side = 0
        elif pair_key is not None and self._active_pair is not None and pair_key != self._active_pair:
            self._active_pair = pair_key
            self._axis_side = 0

        candidates = self._generate_candidates(
            beat, shot_type, primary_subject, secondary_subject, room_min, room_max, pair_positions
        )

        best: CameraCandidate | None = None
        for cand in candidates:
            self._score_candidate(
                cand,
                beat,
                shot_type,
                primary_subject,
                secondary_subject,
                room_min,
                room_max,
                jambs,
                pair_key,
                pair_positions,
            )
            if best is None or cand.score > best.score:
                best = cand

        assert best is not None, "Candidate generator must always return at least one candidate"

        # Lock or preserve 180-degree axis side for dialogue/pair shots.
        if pair_key is not None and pair_positions is not None and shot_type in (
            "two_shot", "over_the_shoulder", "medium", "close_up", "extreme_close_up", "reaction"
        ):
            self._active_pair = pair_key
            if self._axis_side == 0 and best.axis_side != 0:
                self._axis_side = best.axis_side

        # Update pacing history.
        self._shot_history.append(shot_type)
        if shot_type == "tracking":
            self._consecutive_tracking += 1
        else:
            self._consecutive_tracking = 0
        self._beats_since_reaction = 0 if shot_type == "reaction" else self._beats_since_reaction + 1
        self._beats_since_insert = 0 if shot_type == "insert" else self._beats_since_insert + 1

        return self._emit_stage3d_shot(
            beat=beat,
            start_frame=start_frame,
            frames=frames,
            shot_type=shot_type,
            primary_subject=primary_subject,
            secondary_subject=secondary_subject,
            chosen=best,
            downgrade_reason=downgrade_reason,
            candidates_count=len(candidates),
        )

    # ------------------------------------------------------- shot selection

    def _clone_beat(
        self,
        beat: BeatSpec,
        new_id: str,
        frames: int,
        preferred_shot: str | None = None,
    ) -> BeatSpec:
        return BeatSpec(
            beat_id=new_id,
            duration=frames / self.fps,
            frames=frames,
            speaker=beat.speaker,
            listener=beat.listener,
            emotion=beat.emotion,
            action=beat.action,
            prop_event=dict(beat.prop_event) if beat.prop_event else None,
            narrative_importance=beat.narrative_importance,
            preferred_shot=preferred_shot,
            pov_required=beat.pov_required,
            actor_positions=dict(beat.actor_positions),
            actor_end_positions=dict(beat.actor_end_positions),
            actor_facings=dict(beat.actor_facings),
            actor_tiers=dict(beat.actor_tiers),
            actor_heights=dict(beat.actor_heights),
            room_bounds=dict(beat.room_bounds),
            doors=list(beat.doors),
            obstacles=list(beat.obstacles),
            camera_regions=list(beat.camera_regions),
            scene_number=beat.scene_number,
            description=beat.description,
            lines=list(beat.lines),
            voice_lead_frames=beat.voice_lead_frames,
        )

    def _choose_shot_type(self, beat: BeatSpec) -> tuple[str, str, str, str]:
        """Decide (shot_type, primary_subject, secondary_subject, downgrade_reason)."""
        downgrade_reason = ""
        actors = list(beat.actor_positions.keys())
        primary = beat.speaker or (actors[0] if actors else "")
        secondary = beat.listener or next((a for a in actors if a != primary), "")

        pref = self.normalize_shot_type(beat.preferred_shot)
        if pref == "pov":
            if beat.pov_required:
                return ("pov", primary, secondary, "")
            # Reject gratuitous 1st-person POV when not narratively required.
            downgrade_reason = "pov_not_narratively_required"
            pref = None

        if pref is not None:
            chosen = pref
        else:
            chosen = self._auto_select_shot_type(beat, primary, secondary)

        # Reaction shot always frames the listener as primary subject.
        if chosen == "reaction":
            if beat.listener and beat.listener in beat.actor_positions:
                primary, secondary = beat.listener, beat.speaker
            elif secondary:
                primary, secondary = secondary, primary
        elif chosen == "insert" and beat.prop_event:
            primary = str(beat.prop_event.get("prop_id") or "prop")

        # Hard capability check: refuse extreme_close_up on TIER_B / non-BlendShape actors!
        if chosen == "extreme_close_up" and not self.can_extreme_close_up(primary, beat):
            chosen = "medium"
            downgrade_reason = "tier_b_no_blendshapes"

        return (chosen, primary, secondary, downgrade_reason)

    def _auto_select_shot_type(self, beat: BeatSpec, primary: str, secondary: str) -> str:
        # 1. Opening beat establishes room geography.
        if not self._shot_history:
            if beat.prop_event and str(beat.prop_event.get("event", "")).lower() in PROP_INSERT_EVENTS and not beat.speaker:
                return "insert"
            return "establishing"

        # 2. Dedicated prop events trigger insert shots.
        if beat.prop_event is not None:
            ev = str(beat.prop_event.get("event", "")).lower()
            if ev in PROP_INSERT_EVENTS or (beat.narrative_importance >= 0.70 and self._beats_since_insert >= 1):
                return "insert"

        # 3. Movement beats use tracking, but never monotonously back-to-back!
        moving_actor = self._moving_actor(beat)
        if moving_actor or beat.action in MOVEMENT_ACTIONS:
            if self._consecutive_tracking == 0 and self._shot_history[-1] != "tracking":
                return "tracking"
            return "wide" if self._shot_history[-1] != "wide" else "medium"

        # 4. Explicit reaction beat.
        if beat.action in ("react", "listen", "nod") and (beat.listener or not beat.speaker):
            return "reaction"

        # 5. Dialogue & emotional pacing.
        last = self._shot_history[-1]
        has_pair = bool(primary and secondary and primary in beat.actor_positions and secondary in beat.actor_positions)

        if beat.narrative_importance >= 0.90 and beat.emotion in HIGH_EMOTIONS:
            if self.can_extreme_close_up(primary, beat) and last != "extreme_close_up":
                return "extreme_close_up"
            return "close_up" if last != "close_up" else "medium"

        if beat.narrative_importance >= 0.72 or beat.emotion in HIGH_EMOTIONS:
            if has_pair and self._beats_since_reaction >= 2 and last in ("close_up", "over_the_shoulder"):
                return "reaction"
            if last != "close_up":
                return "close_up"
            return "over_the_shoulder" if has_pair else "medium"

        if has_pair:
            if last in ("establishing", "wide", "tracking"):
                return "two_shot"
            if last == "two_shot":
                return "over_the_shoulder"
            if last == "over_the_shoulder":
                return "close_up" if beat.narrative_importance >= 0.55 else "reaction"
            if last == "reaction":
                return "medium"
            return "over_the_shoulder"

        return "medium" if last != "medium" else "wide"

    def _moving_actor(self, beat: BeatSpec) -> str:
        for actor_id, start_pos in beat.actor_positions.items():
            end_pos = beat.actor_end_positions.get(actor_id)
            if end_pos is not None and _v3_dist(start_pos, end_pos) >= 0.85:
                return actor_id
        return ""

    def _resolve_interaction_pair(
        self,
        beat: BeatSpec,
        primary: str,
        secondary: str,
    ) -> tuple[tuple[str, str] | None, tuple[tuple[float, float, float], tuple[float, float, float]] | None]:
        a = beat.speaker or primary
        b = beat.listener or secondary
        if a and b and a != b and a in beat.actor_positions and b in beat.actor_positions:
            pair = (a, b) if a < b else (b, a)
            return pair, (beat.actor_positions[pair[0]], beat.actor_positions[pair[1]])
        return None, None

    # --------------------------------------------------- candidate generation

    def _actor_target_height(self, beat: BeatSpec, actor_id: str, fraction: float) -> float:
        h = float(beat.actor_heights.get(actor_id, DEFAULT_ACTOR_HEIGHT_M))
        return h * fraction

    def _actor_facing(self, beat: BeatSpec, actor_id: str, fallback_toward: tuple[float, float, float] | None = None) -> tuple[float, float]:
        if actor_id in beat.actor_facings:
            return beat.actor_facings[actor_id]
        if fallback_toward is not None and actor_id in beat.actor_positions:
            p = beat.actor_positions[actor_id]
            return _v2_norm(fallback_toward[0] - p[0], fallback_toward[2] - p[2])
        return (0.0, 1.0)

    def _generate_candidates(
        self,
        beat: BeatSpec,
        shot_type: str,
        primary: str,
        secondary: str,
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        pair_positions: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    ) -> list[CameraCandidate]:
        room_center = (
            (room_min[0] + room_max[0]) * 0.5,
            1.25,
            (room_min[2] + room_max[2]) * 0.5,
        )
        p_pos = beat.actor_positions.get(primary, room_center)
        s_pos = beat.actor_positions.get(secondary) if secondary else None

        if shot_type == "pov":
            return self._candidates_pov(beat, primary, p_pos, s_pos)
        if shot_type == "establishing":
            return self._candidates_establishing(beat, room_min, room_max, room_center)
        if shot_type == "insert":
            return self._candidates_insert(beat, p_pos, s_pos)
        if shot_type == "two_shot":
            return self._candidates_two_shot(beat, p_pos, s_pos or room_center, room_center)
        if shot_type == "over_the_shoulder":
            return self._candidates_ots(beat, primary, secondary, p_pos, s_pos)
        if shot_type == "tracking":
            return self._candidates_tracking(beat, primary, p_pos)
        if shot_type == "extreme_close_up":
            return self._candidates_portrait(beat, primary, p_pos, s_pos, dists=(0.76, 0.88), fov=30.0, target_frac=0.91, eye_offset=0.0, dof=True, tag="ecu")
        if shot_type == "close_up":
            return self._candidates_portrait(beat, primary, p_pos, s_pos, dists=(1.18, 1.42), fov=35.0, target_frac=0.86, eye_offset=-0.04, dof=True, tag="cu")
        if shot_type == "reaction":
            return self._candidates_portrait(beat, primary, p_pos, s_pos, dists=(1.28, 1.58), fov=36.0, target_frac=0.86, eye_offset=-0.03, dof=True, tag="react")
        if shot_type == "medium":
            return self._candidates_portrait(beat, primary, p_pos, s_pos, dists=(2.15, 2.65), fov=42.0, target_frac=0.76, eye_offset=-0.10, dof=False, tag="med")
        # Default: "wide"
        return self._candidates_wide(beat, primary, p_pos, s_pos, room_center)

    def _candidates_pov(
        self,
        beat: BeatSpec,
        actor_id: str,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float] | None,
    ) -> list[CameraCandidate]:
        eye_y = self._actor_target_height(beat, actor_id, 0.93)
        fx, fz = self._actor_facing(beat, actor_id, s_pos)
        pos = (p_pos[0] + fx * 0.12, eye_y, p_pos[2] + fz * 0.12)
        look = (p_pos[0] + fx * 6.0, eye_y - 0.05, p_pos[2] + fz * 6.0)
        if s_pos is not None:
            look = (s_pos[0], eye_y - 0.08, s_pos[2])
        return [
            CameraCandidate(
                candidate_id="pov_0",
                mode="pov",
                pos=pos,
                pos_to=pos,
                look_at=look,
                look_to=look,
                fov=58.0,
                fov_to=58.0,
                dof_enabled=False,
                dof_focus_distance=max(1.5, _v3_dist(pos, look)),
                pov_actor=actor_id,
            )
        ]

    def _candidates_establishing(
        self,
        beat: BeatSpec,
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        room_center: tuple[float, float, float],
    ) -> list[CameraCandidate]:
        # Aim toward the centroid of actors if present, blended with room center.
        if beat.actor_positions:
            xs = [p[0] for p in beat.actor_positions.values()]
            zs = [p[2] for p in beat.actor_positions.values()]
            look = (sum(xs) / len(xs) * 0.65 + room_center[0] * 0.35, 1.22, sum(zs) / len(zs) * 0.65 + room_center[2] * 0.35)
        else:
            look = room_center

        cands: list[CameraCandidate] = []
        inset_x = max(self.wall_clearance_m + 0.35, (room_max[0] - room_min[0]) * 0.10)
        inset_z = max(self.wall_clearance_m + 0.35, (room_max[2] - room_min[2]) * 0.10)
        high_y = min(room_max[1] - 0.55, 2.45)
        mid_y = min(room_max[1] - 0.75, 1.95)
        vantages = [
            (room_min[0] + inset_x, high_y, room_max[2] - inset_z),
            (room_max[0] - inset_x, high_y, room_max[2] - inset_z),
            (room_min[0] + inset_x, high_y, room_min[2] + inset_z),
            (room_max[0] - inset_x, high_y, room_min[2] + inset_z),
            (room_center[0] - (room_max[0] - room_min[0]) * 0.22, mid_y, room_max[2] - inset_z - 0.4),
            (room_center[0] + (room_max[0] - room_min[0]) * 0.22, mid_y, room_max[2] - inset_z - 0.4),
            (room_center[0], mid_y, room_min[2] + inset_z + 0.4),
            (room_center[0], mid_y, room_max[2] - inset_z - 0.5),
        ]
        # Add layout-provided establishing regions.
        for idx, reg in enumerate(beat.camera_regions):
            if "establishing" in (reg.get("tags") or []) or "wide" in (reg.get("tags") or []):
                c = reg.get("center")
                if isinstance(c, (list, tuple)) and len(c) >= 3:
                    vantages.append((float(c[0]), float(c[1]), float(c[2])))

        for i, pos in enumerate(vantages):
            to_look = _v3_sub(look, pos)
            dist = max(_v3_len(to_look), 0.5)
            dolly_step = _v3_scale(to_look, min(0.35, dist * 0.08) / dist)
            pos_to = _v3_add(pos, dolly_step)
            cands.append(
                CameraCandidate(
                    candidate_id=f"est_{i}",
                    mode="dolly",
                    pos=pos,
                    pos_to=pos_to,
                    look_at=look,
                    look_to=look,
                    fov=52.0,
                    fov_to=50.5,
                    dof_enabled=False,
                    dof_focus_distance=round(dist, 2),
                )
            )
        return cands

    def _candidates_wide(
        self,
        beat: BeatSpec,
        primary: str,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float] | None,
        room_center: tuple[float, float, float],
    ) -> list[CameraCandidate]:
        center = (
            ((p_pos[0] + s_pos[0]) * 0.5, 1.15, (p_pos[2] + s_pos[2]) * 0.5)
            if s_pos is not None
            else (p_pos[0], 1.15, p_pos[2])
        )
        fx, fz = self._actor_facing(beat, primary, s_pos or room_center)
        base_yaw = math.atan2(fx, fz)
        cands: list[CameraCandidate] = []
        idx = 0
        for deg in (-65, -35, 0, 35, 65, 115, -115, 155, -155):
            for dist in (3.4, 4.4):
                ang = base_yaw + math.radians(deg)
                pos = (center[0] + math.sin(ang) * dist, 1.72, center[2] + math.cos(ang) * dist)
                pos_to = (center[0] + math.sin(ang) * (dist - 0.25), 1.68, center[2] + math.cos(ang) * (dist - 0.25))
                cands.append(
                    CameraCandidate(
                        candidate_id=f"wide_{idx}",
                        mode="dolly",
                        pos=pos,
                        pos_to=pos_to,
                        look_at=center,
                        look_to=center,
                        fov=48.0,
                        fov_to=47.0,
                        dof_enabled=False,
                        dof_focus_distance=round(dist, 2),
                    )
                )
                idx += 1
        return cands

    def _candidates_portrait(
        self,
        beat: BeatSpec,
        actor_id: str,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float] | None,
        *,
        dists: tuple[float, ...],
        fov: float,
        target_frac: float,
        eye_offset: float,
        dof: bool,
        tag: str,
    ) -> list[CameraCandidate]:
        target_y = self._actor_target_height(beat, actor_id, target_frac)
        cam_y = max(0.55, target_y + eye_offset)
        look = (p_pos[0], target_y, p_pos[2])
        fx, fz = self._actor_facing(beat, actor_id, s_pos)
        base_yaw = math.atan2(fx, fz)
        cands: list[CameraCandidate] = []
        idx = 0
        # Sample front & 3/4 angles first, plus wider fallbacks in case of tight obstacles.
        for deg in (-25, 25, -45, 45, 0, -70, 70, -105, 105, 145, -145):
            for dist in dists:
                ang = base_yaw + math.radians(deg)
                dx, dz = math.sin(ang), math.cos(ang)
                pos = (p_pos[0] + dx * dist, cam_y, p_pos[2] + dz * dist)
                # Subtle push-in for cinematic life.
                push = min(0.10, dist * 0.06)
                pos_to = (p_pos[0] + dx * (dist - push), cam_y, p_pos[2] + dz * (dist - push))
                cands.append(
                    CameraCandidate(
                        candidate_id=f"{tag}_{idx}",
                        mode="dolly",
                        pos=pos,
                        pos_to=pos_to,
                        look_at=look,
                        look_to=look,
                        fov=fov,
                        fov_to=max(26.0, fov - 0.8),
                        dof_enabled=dof,
                        dof_focus_distance=round(dist, 2),
                    )
                )
                idx += 1
        return cands

    def _candidates_two_shot(
        self,
        beat: BeatSpec,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float],
        room_center: tuple[float, float, float],
    ) -> list[CameraCandidate]:
        mid_x = (p_pos[0] + s_pos[0]) * 0.5
        mid_z = (p_pos[2] + s_pos[2]) * 0.5
        look = (mid_x, 1.32, mid_z)
        sep = max(0.8, math.hypot(s_pos[0] - p_pos[0], s_pos[2] - p_pos[2]))
        axis = _v2_norm(s_pos[0] - p_pos[0], s_pos[2] - p_pos[2])
        perp_pos = (-axis[1], axis[0])
        perp_neg = (axis[1], -axis[0])
        base_dist = max(2.35, min(4.2, sep * 1.55))

        cands: list[CameraCandidate] = []
        idx = 0
        for side_vec in (perp_pos, perp_neg):
            base_ang = math.atan2(side_vec[0], side_vec[1])
            for deg in (0, -18, 18, -35, 35, -55, 55, -72, 72):
                for dist_mult in (0.78, 1.0, 1.25):
                    ang = base_ang + math.radians(deg)
                    dist = max(1.85, base_dist * dist_mult)
                    dx, dz = math.sin(ang), math.cos(ang)
                    pos = (mid_x + dx * dist, 1.48, mid_z + dz * dist)
                    pos_to = (mid_x + dx * (dist - 0.18), 1.46, mid_z + dz * (dist - 0.18))
                    cands.append(
                        CameraCandidate(
                            candidate_id=f"two_{idx}",
                            mode="dolly",
                            pos=pos,
                            pos_to=pos_to,
                            look_at=look,
                            look_to=look,
                            fov=40.0,
                            fov_to=39.5,
                            dof_enabled=False,
                            dof_focus_distance=round(dist, 2),
                        )
                    )
                    idx += 1
        # Also include layout-provided camera regions as fallback vantages in tight spaces (e.g. corridors).
        for reg in beat.camera_regions:
            c = reg.get("center")
            if isinstance(c, (list, tuple)) and len(c) >= 3:
                pos = (float(c[0]), min(float(c[1]), 1.75), float(c[2]))
                dist = _v3_dist(pos, look)
                if 1.4 <= dist <= 7.0:
                    cands.append(
                        CameraCandidate(
                            candidate_id=f"two_{idx}",
                            mode="dolly",
                            pos=pos,
                            pos_to=pos,
                            look_at=look,
                            look_to=look,
                            fov=42.0,
                            fov_to=41.0,
                            dof_enabled=False,
                            dof_focus_distance=round(dist, 2),
                        )
                    )
                    idx += 1
        return cands

    def _candidates_ots(
        self,
        beat: BeatSpec,
        primary: str,
        secondary: str,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float] | None,
    ) -> list[CameraCandidate]:
        # Foreground shoulder is secondary (listener); camera looks over secondary toward primary (speaker).
        if s_pos is None:
            return self._candidates_portrait(beat, primary, p_pos, None, dists=(1.8, 2.2), fov=38.0, target_frac=0.82, eye_offset=-0.04, dof=True, tag="ots_fb")

        to_primary = _v2_norm(p_pos[0] - s_pos[0], p_pos[2] - s_pos[2])
        right_vec = (-to_primary[1], to_primary[0])
        p_eye = self._actor_target_height(beat, primary, 0.85)
        s_shoulder = self._actor_target_height(beat, secondary, 0.88)
        look = (p_pos[0] * 0.78 + s_pos[0] * 0.22, p_eye - 0.04, p_pos[2] * 0.78 + s_pos[2] * 0.22)

        cands: list[CameraCandidate] = []
        idx = 0
        for side_sign in (1.0, -1.0):
            for back_d in (1.10, 1.45, 1.85):
                for lat_d in (0.55, 0.82):
                    pos = (
                        s_pos[0] - to_primary[0] * back_d + right_vec[0] * lat_d * side_sign,
                        max(1.35, s_shoulder + 0.06),
                        s_pos[2] - to_primary[1] * back_d + right_vec[1] * lat_d * side_sign,
                    )
                    pos_to = (
                        pos[0] + to_primary[0] * 0.14,
                        pos[1] - 0.02,
                        pos[2] + to_primary[1] * 0.14,
                    )
                    focus = max(1.0, _v3_dist(pos, (p_pos[0], p_eye, p_pos[2])))
                    cands.append(
                        CameraCandidate(
                            candidate_id=f"ots_{idx}",
                            mode="dolly",
                            pos=pos,
                            pos_to=pos_to,
                            look_at=look,
                            look_to=look,
                            fov=35.0,
                            fov_to=34.0,
                            dof_enabled=True,
                            dof_focus_distance=round(focus, 2),
                        )
                    )
                    idx += 1
        return cands

    def _candidates_insert(
        self,
        beat: BeatSpec,
        p_pos: tuple[float, float, float],
        s_pos: tuple[float, float, float] | None,
    ) -> list[CameraCandidate]:
        if beat.prop_event and "position" in beat.prop_event:
            prop_pos = _parse_pos3(beat.prop_event["position"], 1.02)
        elif s_pos is not None:
            prop_pos = ((p_pos[0] + s_pos[0]) * 0.5, 1.06, (p_pos[2] + s_pos[2]) * 0.5)
        else:
            fx, fz = self._actor_facing(beat, beat.speaker or next(iter(beat.actor_positions), ""))
            prop_pos = (p_pos[0] + fx * 0.45, 1.05, p_pos[2] + fz * 0.45)

        cands: list[CameraCandidate] = []
        idx = 0
        for deg in (0, 45, 90, 135, 180, -135, -90, -45):
            for dist, elev in ((0.85, 0.28), (1.18, 0.42), (1.55, 0.62)):
                rad = math.radians(deg)
                pos = (prop_pos[0] + math.sin(rad) * dist, max(0.45, prop_pos[1] + elev), prop_pos[2] + math.cos(rad) * dist)
                pos_to = (prop_pos[0] + math.sin(rad) * (dist - 0.08), max(0.45, prop_pos[1] + elev - 0.02), prop_pos[2] + math.cos(rad) * (dist - 0.08))
                cands.append(
                    CameraCandidate(
                        candidate_id=f"ins_{idx}",
                        mode="dolly",
                        pos=pos,
                        pos_to=pos_to,
                        look_at=prop_pos,
                        look_to=prop_pos,
                        fov=32.0,
                        fov_to=31.0,
                        dof_enabled=True,
                        dof_focus_distance=round(_v3_dist(pos, prop_pos), 2),
                    )
                )
                idx += 1
        return cands

    def _candidates_tracking(
        self,
        beat: BeatSpec,
        primary: str,
        p_pos: tuple[float, float, float],
    ) -> list[CameraCandidate]:
        mover = self._moving_actor(beat) or primary
        start = beat.actor_positions.get(mover, p_pos)
        end = beat.actor_end_positions.get(mover)
        if end is None or _v3_dist(start, end) < 0.2:
            fx, fz = self._actor_facing(beat, mover)
            end = (start[0] + fx * 1.8, start[1], start[2] + fz * 1.8)

        move_dir = _v2_norm(end[0] - start[0], end[2] - start[2])
        right = (-move_dir[1], move_dir[0])
        target_y = self._actor_target_height(beat, mover, 0.74)
        look_start = (start[0], target_y, start[2])
        look_end = (end[0], target_y, end[2])

        cands: list[CameraCandidate] = []
        idx = 0
        # Prefer cinematic 3/4 front-side or lateral tracking dollies over boring rear follow!
        presets = [
            (1.0, 2.2, 1.3),    # right 3/4 lead
            (-1.0, 2.2, 1.3),   # left 3/4 lead
            (1.0, 2.6, 0.0),    # pure right lateral track
            (-1.0, 2.6, 0.0),   # pure left lateral track
            (1.0, 1.7, -1.6),   # right 3/4 follow
            (-1.0, 1.7, -1.6),  # left 3/4 follow
            (1.0, 1.2, 2.4),    # leading track
            (-1.0, 1.2, 2.4),
        ]
        for side_s, lat_d, fwd_d in presets:
            off_x = right[0] * lat_d * side_s + move_dir[0] * fwd_d
            off_z = right[1] * lat_d * side_s + move_dir[1] * fwd_d
            pos = (start[0] + off_x, 1.56, start[2] + off_z)
            pos_to = (end[0] + off_x * 0.92, 1.54, end[2] + off_z * 0.92)
            cands.append(
                CameraCandidate(
                    candidate_id=f"trk_{idx}",
                    mode="dolly",
                    pos=pos,
                    pos_to=pos_to,
                    look_at=look_start,
                    look_to=look_end,
                    fov=46.0,
                    fov_to=45.0,
                    dof_enabled=False,
                    dof_focus_distance=round(_v3_dist(pos, look_start), 2),
                )
            )
            idx += 1
        return cands

    # ------------------------------------------------------ candidate scoring

    def _wall_clearance(
        self,
        p: tuple[float, float, float],
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
    ) -> float:
        return min(
            p[0] - room_min[0],
            room_max[0] - p[0],
            p[2] - room_min[2],
            room_max[2] - p[2],
            p[1] - room_min[1],
            room_max[1] - p[1],
        )

    def _score_candidate(
        self,
        cand: CameraCandidate,
        beat: BeatSpec,
        shot_type: str,
        primary: str,
        secondary: str,
        room_min: tuple[float, float, float],
        room_max: tuple[float, float, float],
        jambs: list[AABB3],
        pair_key: tuple[str, str] | None,
        pair_positions: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    ) -> None:
        breakdown: dict[str, float] = {}
        mid_pos = _v3_scale(_v3_add(cand.pos, cand.pos_to), 0.5)
        mid_look = _v3_scale(_v3_add(cand.look_at, cand.look_to), 0.5)
        sample_pairs = ((cand.pos, cand.look_at), (mid_pos, mid_look), (cand.pos_to, cand.look_to))

        # 1. Room bounds & wall clearance (>= 0.35m hard requirement).
        wc = min(self._wall_clearance(p, room_min, room_max) for p, _ in sample_pairs)
        cand.wall_clearance_m = round(wc, 4)
        if wc < self.wall_clearance_m:
            breakdown["wall_clearance"] = -1000.0 - (self.wall_clearance_m - wc) * 500.0
        else:
            breakdown["wall_clearance"] = min(20.0, (wc - self.wall_clearance_m) * 18.0 + 10.0)

        # 2. Doorframe jamb clearance (>= 0.35m) + frustum edge check (no door jamb blocking frame!).
        dfc = 99.0
        jamb_hit = False
        for jamb in jambs:
            for p, look in sample_pairs:
                dfc = min(dfc, jamb.distance_to_point(p))
                if segment_intersects_aabb(p, look, jamb, end_trim_m=0.05, padding=0.04):
                    jamb_hit = True
                # Frustum left/right margin rays to catch door jambs intruding into frame edges.
                to_l = _v3_sub(look, p)
                horiz_len = math.hypot(to_l[0], to_l[2])
                if horiz_len > 0.2:
                    fwd = (to_l[0] / horiz_len, to_l[2] / horiz_len)
                    right = (-fwd[1], fwd[0])
                    half_hfov_rad = math.atan(math.tan(math.radians(cand.fov * 0.5)) * (16.0 / 9.0)) * 0.65
                    probe_d = min(horiz_len, 2.2)
                    for side_s in (-1.0, 1.0):
                        ex = p[0] + (fwd[0] * math.cos(half_hfov_rad) + right[0] * math.sin(half_hfov_rad) * side_s) * probe_d
                        ez = p[2] + (fwd[1] * math.cos(half_hfov_rad) + right[1] * math.sin(half_hfov_rad) * side_s) * probe_d
                        edge_pt = (ex, (p[1] + look[1]) * 0.5, ez)
                        if segment_intersects_aabb(p, edge_pt, jamb, end_trim_m=0.0, padding=0.02):
                            cand.frustum_edge_blocked = True
        cand.doorframe_clearance_m = round(dfc, 4)
        if dfc < self.doorframe_clearance_m:
            breakdown["doorframe_clearance"] = -1000.0 - (self.doorframe_clearance_m - dfc) * 400.0
        elif jamb_hit or cand.frustum_edge_blocked:
            breakdown["doorframe_clearance"] = -650.0
        else:
            breakdown["doorframe_clearance"] = 12.0

        # 3. Obstacle occlusion & camera-inside-obstacle check.
        occluded = False
        inside_obs = False
        trim = 0.28 if shot_type == "insert" else 0.35
        for obs in beat.obstacles:
            if obs.kind == "doorframe":
                continue
            for p, look in sample_pairs:
                if obs.contains_point(p, padding=OBSTACLE_CAMERA_PADDING_M):
                    inside_obs = True
                # For insert shots aimed at an item resting ON a host surface (table/bed/console),
                # ignore self-intersection with the host surface itself as long as camera is above & outside it.
                if shot_type == "insert" and obs.contains_point(look, padding=0.12) and p[1] >= look[1] + 0.10:
                    continue
                if segment_intersects_aabb(p, look, obs, end_trim_m=trim, padding=0.02):
                    occluded = True
        # Also verify secondary subject visibility in two_shot.
        if not occluded and shot_type == "two_shot" and secondary and secondary in beat.actor_positions:
            s_eye = (beat.actor_positions[secondary][0], self._actor_target_height(beat, secondary, 0.80), beat.actor_positions[secondary][2])
            for obs in beat.obstacles:
                if obs.kind != "doorframe" and segment_intersects_aabb(cand.pos, s_eye, obs, end_trim_m=0.35):
                    occluded = True
                    break

        cand.occluded = occluded or inside_obs
        if inside_obs:
            breakdown["occlusion"] = -950.0
        elif occluded:
            breakdown["occlusion"] = -800.0
        else:
            breakdown["occlusion"] = 25.0

        # 4. 180-degree line-of-action consistency.
        if pair_key is not None and pair_positions is not None:
            cand.axis_side = self.axis_side_for_point(pair_positions[0], pair_positions[1], cand.pos)
            if (
                self._active_pair == pair_key
                and self._axis_side != 0
                and shot_type not in ("establishing", "insert", "pov")
            ):
                if cand.axis_side == self._axis_side:
                    breakdown["axis_180"] = 35.0
                else:
                    cand.crosses_180_axis = True
                    breakdown["axis_180"] = -450.0
            else:
                breakdown["axis_180"] = 10.0
        else:
            breakdown["axis_180"] = 0.0

        # 5. Framing suitability & facing alignment.
        framing_score = 0.0
        if primary in beat.actor_positions and shot_type in ("medium", "close_up", "extreme_close_up", "reaction"):
            p_pos = beat.actor_positions[primary]
            to_cam = _v2_norm(cand.pos[0] - p_pos[0], cand.pos[2] - p_pos[2])
            facing = self._actor_facing(beat, primary, beat.actor_positions.get(secondary))
            dot_front = to_cam[0] * facing[0] + to_cam[1] * facing[1]
            # Reward front/3-quarter view (dot ~ 0.65..0.95), penalize back of head.
            if dot_front >= 0.45:
                framing_score += 22.0 - abs(dot_front - 0.82) * 14.0
            elif dot_front >= 0.0:
                framing_score += 4.0 * dot_front
            else:
                framing_score -= 30.0 * abs(dot_front)

        if shot_type == "two_shot" and primary in beat.actor_positions and secondary in beat.actor_positions:
            # Verify both actors comfortably fit inside horizontal FOV.
            p_a = beat.actor_positions[primary]
            p_b = beat.actor_positions[secondary]
            cam_fwd = _v2_norm(cand.look_at[0] - cand.pos[0], cand.look_at[2] - cand.pos[2])
            half_hfov = math.atan(math.tan(math.radians(cand.fov * 0.5)) * (16.0 / 9.0))
            max_ang = 0.0
            for pt in (p_a, p_b):
                v = _v2_norm(pt[0] - cand.pos[0], pt[2] - cand.pos[2])
                ang = math.acos(max(-1.0, min(1.0, cam_fwd[0] * v[0] + cam_fwd[1] * v[1])))
                max_ang = max(max_ang, ang)
            if max_ang < half_hfov * 0.78:
                framing_score += 24.0
            elif max_ang < half_hfov * 0.95:
                framing_score += 8.0
            else:
                framing_score -= 40.0

        if shot_type == "over_the_shoulder" and primary in beat.actor_positions and secondary in beat.actor_positions:
            # Ensure camera is behind secondary looking toward primary, not blocking primary.
            d_p = _v3_dist(cand.pos, beat.actor_positions[primary])
            d_s = _v3_dist(cand.pos, beat.actor_positions[secondary])
            if d_p > d_s + 0.35 and 1.8 <= d_p <= 4.5:
                framing_score += 20.0
            else:
                framing_score -= 15.0

        if shot_type == "establishing":
            dist = _v3_dist(cand.pos, cand.look_at)
            framing_score += min(24.0, dist * 3.5)

        if shot_type == "tracking":
            # Prefer front-3/4 or lateral tracks over monotonous rear follow.
            mover = self._moving_actor(beat) or primary
            start = beat.actor_positions.get(mover)
            end = beat.actor_end_positions.get(mover)
            if start is not None and end is not None and _v3_dist(start, end) > 0.2:
                mv = _v2_norm(end[0] - start[0], end[2] - start[2])
                to_cam = _v2_norm(cand.pos[0] - start[0], cand.pos[2] - start[2])
                lead_dot = mv[0] * to_cam[0] + mv[1] * to_cam[1]
                framing_score += 14.0 if lead_dot >= -0.1 else 4.0

        breakdown["framing"] = round(framing_score, 3)

        # 6. Bonus if candidate lies inside a layout-prevalidated camera region.
        region_bonus = 0.0
        for reg in beat.camera_regions:
            rmin = _parse_pos3(reg.get("min", [0, 0, 0]))
            rmax = _parse_pos3(reg.get("max", [0, 0, 0]))
            if (
                rmin[0] <= cand.pos[0] <= rmax[0]
                and rmin[1] <= cand.pos[1] <= rmax[1]
                and rmin[2] <= cand.pos[2] <= rmax[2]
            ):
                tags = reg.get("tags") or []
                region_bonus = 8.0 if shot_type in tags else 3.0
                break
        breakdown["camera_region"] = region_bonus

        cand.score_breakdown = {k: round(v, 3) for k, v in breakdown.items()}
        cand.score = round(sum(breakdown.values()), 3)

    # ------------------------------------------------------- stage3d export

    def _emit_stage3d_shot(
        self,
        *,
        beat: BeatSpec,
        start_frame: int,
        frames: int,
        shot_type: str,
        primary_subject: str,
        secondary_subject: str,
        chosen: CameraCandidate,
        downgrade_reason: str,
        candidates_count: int,
    ) -> dict[str, Any]:
        cam_entry: dict[str, Any] = {
            "from": 0,
            "to": frames,
            "mode": chosen.mode,
            "fov": round(chosen.fov, 2),
            "fov_to": round(chosen.fov_to, 2),
            "dof_enabled": chosen.dof_enabled,
            "dof_focus_distance": round(chosen.dof_focus_distance, 3),
        }
        if chosen.mode == "pov":
            cam_entry["actor"] = chosen.pov_actor
            cam_entry["eye_height"] = round(chosen.pos[1], 3)
            cam_entry["look_at"] = _r3(chosen.look_at)
            cam_entry["look_to"] = _r3(chosen.look_to)
            cam_entry["sway"] = 0.005
        else:
            cam_entry["pos"] = _r3(chosen.pos)
            cam_entry["pos_to"] = _r3(chosen.pos_to)
            cam_entry["look_at"] = _r3(chosen.look_at)
            cam_entry["look_to"] = _r3(chosen.look_to)
            if abs(chosen.roll_deg) > 1e-3:
                cam_entry["roll_deg"] = round(chosen.roll_deg, 2)

        end_focus = round(_v3_dist(chosen.pos_to, chosen.look_to), 3)
        camera_keyframes = {
            "mode": chosen.mode,
            "keyframes": [
                {
                    "frame": 0,
                    "pos": _r3(chosen.pos),
                    "look_at": _r3(chosen.look_at),
                    "fov": round(chosen.fov, 2),
                    "dof_focus_distance": round(chosen.dof_focus_distance, 3),
                },
                {
                    "frame": frames,
                    "pos": _r3(chosen.pos_to),
                    "look_at": _r3(chosen.look_to),
                    "fov": round(chosen.fov_to, 2),
                    "dof_focus_distance": end_focus,
                },
            ],
        }

        return {
            "id": f"{beat.beat_id}_{shot_type}",
            "beat_id": beat.beat_id,
            "type": shot_type,
            "shot_type": shot_type,
            "scene_number": beat.scene_number,
            "description": beat.description or f"{shot_type} on {primary_subject or 'scene'}",
            "from": start_frame,
            "frames": frames,
            "voice_lead_frames": beat.voice_lead_frames,
            "lines": beat.lines,
            "primary_subject": primary_subject,
            "secondary_subject": secondary_subject,
            "fov": round(chosen.fov, 2),
            "dof_enabled": chosen.dof_enabled,
            "dof_focus_distance": round(chosen.dof_focus_distance, 3),
            "axis_side": chosen.axis_side,
            "first_person": chosen.pov_actor if chosen.mode == "pov" else "",
            "cameras": [cam_entry],
            "camera": camera_keyframes,
            "director_meta": {
                "candidate_id": chosen.candidate_id,
                "score": chosen.score,
                "score_breakdown": chosen.score_breakdown,
                "wall_clearance_m": chosen.wall_clearance_m,
                "doorframe_clearance_m": chosen.doorframe_clearance_m,
                "occluded": chosen.occluded,
                "crosses_180_axis": chosen.crosses_180_axis,
                "frustum_edge_blocked": chosen.frustum_edge_blocked,
                "downgrade_reason": downgrade_reason,
                "candidates_evaluated": candidates_count,
            },
        }
