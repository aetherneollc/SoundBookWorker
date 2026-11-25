#!/usr/bin/env python3
"""PS2-era cutscene camera grammar on top of ``CinematicDirector``.

Shots are motivated by an entrance, a physical impact, a listener reaction,
a prop, or the room itself. A change of speaker is not a reason to cut.
Dialogue coverage stays in the 2–5 second range and action or impact coverage
stays in the 1–3 second range, unless the beat explicitly holds for a climax.

Wall clearance, the 180-degree line, and dynamic actor occlusion stay with
the parent scorer. Angle and push adjustments are kept only when that scorer
still accepts the camera.
"""
from __future__ import annotations

import math
from typing import Any

try:
    from cinematic_director import (
        WALL_CLEARANCE_MIN_M,
        BeatSpec,
        CameraCandidate,
        CinematicDirector,
        _doorframe_aabbs,
        _parse_pos3,
        _r3,
    )
except ImportError:
    from renderer.cinematic_director import (
        WALL_CLEARANCE_MIN_M,
        BeatSpec,
        CameraCandidate,
        CinematicDirector,
        _doorframe_aabbs,
        _parse_pos3,
        _r3,
    )

SHOT_INTENTS: tuple[str, ...] = (
    "impact_opening",
    "entrance_exit",
    "action_track",
    "low_angle",
    "high_angle",
    "over_the_shoulder",
    "reaction",
    "prop_mech_closeup",
    "environment_insert",
    "climax_push",
)
INTENT_TO_BASE = {
    "impact_opening": "wide",
    "entrance_exit": "tracking",
    "action_track": "tracking",
    "low_angle": "medium",
    "high_angle": "medium",
    "over_the_shoulder": "over_the_shoulder",
    "reaction": "reaction",
    "prop_mech_closeup": "insert",
    "environment_insert": "insert",
    "climax_push": "close_up",
}
DIALOGUE_INTENTS = frozenset({"over_the_shoulder", "low_angle", "high_angle", "reaction"})
ACTION_INTENTS = frozenset({
    "impact_opening", "entrance_exit", "action_track", "prop_mech_closeup",
    "environment_insert", "climax_push",
})
TALK_ACTIONS = frozenset({
    "talk", "speak", "dialogue", "converse", "hold", "listen", "idle", "stand", "",
})
IMPACT_ACTIONS = frozenset({
    "slam_table", "abrupt_halt", "flinch_dodge", "startle_recoil", "brace_impact",
    "land_heavy", "collapse_down", "table_lean_in", "lunge_strike", "sidearm_fire",
})
MOVE_ACTIONS = frozenset({
    "walk", "jog", "sprint", "enter", "exit", "move", "run", "approach", "patrol", "pace",
})
ACTION_INTENT = {
    "slam_table": "climax_push",
    "table_lean_in": "low_angle",
    "operate_console": "prop_mech_closeup",
    "press_button": "prop_mech_closeup",
    "install_module": "prop_mech_closeup",
    "mount_sensor": "prop_mech_closeup",
    "detach_module": "prop_mech_closeup",
    "repair_kneel": "high_angle",
    "repair_stand": "prop_mech_closeup",
    "inspect_gauge": "prop_mech_closeup",
    "check_storm_window": "environment_insert",
    "idle_window_watch": "environment_insert",
    "rush_enter": "entrance_exit",
    "turn_walk_away": "entrance_exit",
    "door_enter": "entrance_exit",
    "door_exit": "entrance_exit",
    "trigger_door": "entrance_exit",
    "door_close_pull": "entrance_exit",
    "pace_tense": "action_track",
    "confront_step": "low_angle",
    "accuse_point": "over_the_shoulder",
    "fear_brace": "high_angle",
    "startle_recoil": "reaction",
    "step_back_shock": "reaction",
    "flinch_dodge": "action_track",
    "snap_look_back": "reaction",
    "rage_clench": "low_angle",
    "walk": "action_track",
    "enter": "entrance_exit",
    "run": "action_track",
    "exit": "entrance_exit",
}
EVENT_INTENT = {
    "storm_approaching": "environment_insert",
    "red_alert": "environment_insert",
    "signal_lost": "environment_insert",
    "power_flicker": "environment_insert",
    "sensor_error": "prop_mech_closeup",
    "device_operating": "prop_mech_closeup",
    "module_install": "prop_mech_closeup",
    "table_impact": "climax_push",
    "door_open": "entrance_exit",
    "door_close": "entrance_exit",
    "array_restore": "prop_mech_closeup",
    "robot_speaking": "reaction",
}
PUNCH_KIND = {
    "impact": "impact",
    "alarm": "alarm",
    "door": "door",
    "push": "push",
}


def _event_type(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip().lower()
    if isinstance(raw, dict):
        return str(raw.get("type") or raw.get("event") or "").strip().lower()
    return ""


def _chunk_frames(total: int, min_frames: int, max_frames: int) -> list[int]:
    """Split ``total`` into pieces inside ``[min_frames, max_frames]`` when it is long enough.

    A beat that is already shorter than the pacing minimum is left intact.
    Stretching it would move every later caption.
    """
    total = max(1, int(total))
    min_frames = max(1, int(min_frames))
    max_frames = max(min_frames, int(max_frames))
    if total <= max_frames:
        return [total]
    chunks: list[int] = []
    left = total
    while left > max_frames:
        take = max_frames
        if left - take < min_frames:
            take = left - min_frames
            if take > max_frames:
                take = max_frames
            if take < min_frames:
                take = max(min_frames, left // 2)
        take = max(1, min(int(take), left - 1))
        chunks.append(take)
        left -= take
    if left:
        chunks.append(int(left))
    if len(chunks) >= 2 and chunks[-1] < min_frames <= chunks[-2] - (min_frames - chunks[-1]):
        need = min_frames - chunks[-1]
        chunks[-2] -= need
        chunks[-1] += need
    if sum(chunks) != total and chunks:
        chunks[-1] += total - sum(chunks)
    return chunks


class DramaticDirector(CinematicDirector):
    """CinematicDirector plus PS2 cutscene intents, punches, and motivated cuts."""

    def __init__(self, fps: int = 24, wall_clearance_m: float = WALL_CLEARANCE_MIN_M, doorframe_clearance_m: float = 0.35) -> None:
        super().__init__(fps=fps, wall_clearance_m=wall_clearance_m, doorframe_clearance_m=doorframe_clearance_m)
        self._hints: dict[str, dict[str, Any]] = {}

    def reset_state(self) -> None:
        super().reset_state()
        self._hints = {}

    def plan_sequence(
        self,
        beats: list[BeatSpec | dict[str, Any]],
        *,
        layout: Any | None = None,
        actor_tiers: dict[str, Any] | None = None,
        enable_cutaways: bool = True,
        reset: bool = True,
    ) -> list[dict[str, Any]]:
        """Plan shots that cut on impacts, reactions, and environment events.

        Parent mechanical cutaways are replaced by this grammar. ``enable_cutaways``
        is accepted so existing callers keep working; story cuts still happen.
        """
        del enable_cutaways
        if reset:
            self.reset_state()
        prepared: list[dict[str, Any]] = []
        previous: dict[str, Any] | None = None
        for index, raw in enumerate(beats):
            source = self._coerce_beat(raw)
            pieces = self._partition_beat(source, index, previous)
            prepared.extend(pieces)
            previous = pieces[-1]
        shots: list[dict[str, Any]] = []
        cursor = 0
        for piece in prepared:
            if actor_tiers:
                merged_tiers = dict(actor_tiers)
                merged_tiers.update(piece.get("actor_tiers") or {})
                piece["actor_tiers"] = merged_tiers
            # Keep the dict (camera_intent, cut_reason) instead of letting the
            # parent sequence rebuild a BeatSpec and drop those fields.
            shot = self.plan_beat(piece, start_frame=cursor, layout=layout)
            shots.append(shot)
            cursor += int(shot["frames"])
        return shots

    def plan_beat(
        self,
        raw_beat: BeatSpec | dict[str, Any],
        *,
        start_frame: int = 0,
        layout: Any | None = None,
    ) -> dict[str, Any]:
        """Plan one geometrically legal shot and stamp PS2 camera feel onto it."""
        source = self._coerce_beat(raw_beat)
        intent = self._resolve_intent(source, index=0, previous=None)
        if str(source.get("camera_intent") or source.get("shot_intent") or "") in SHOT_INTENTS:
            intent = str(source.get("camera_intent") or source.get("shot_intent"))
        source["camera_intent"] = intent
        source["preferred_shot"] = INTENT_TO_BASE[intent]
        if not source.get("cut_reason"):
            source["cut_reason"] = self._single_cut_reason(source, intent)
        beat_id = str(source.get("beat_id") or source.get("id") or "beat")
        source["beat_id"] = beat_id
        self._hints[beat_id] = {
            "shot_intent": intent,
            "cut_reason": str(source["cut_reason"]),
            "action": str(source.get("action") or ""),
            "environment_event": source.get("environment_event"),
            "intensity": source.get("intensity", source.get("narrative_importance", source.get("emotional_intensity", 0.6))),
        }
        shot = super().plan_beat(source, start_frame=start_frame, layout=layout)
        decorated_source = self._with_layout_bounds(source, layout)
        return self._decorate(shot, decorated_source, self._hints.get(str(shot.get("beat_id")), self._hints[beat_id]))

    def _coerce_beat(self, raw: BeatSpec | dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw, BeatSpec):
            return {
                "beat_id": raw.beat_id,
                "duration": raw.duration,
                "frames": raw.frames,
                "speaker": raw.speaker,
                "listener": raw.listener,
                "emotion": raw.emotion,
                "action": raw.action,
                "prop_event": raw.prop_event,
                "narrative_importance": raw.narrative_importance,
                "preferred_shot": raw.preferred_shot,
                "pov_required": raw.pov_required,
                "actor_positions": {key: list(value) for key, value in raw.actor_positions.items()},
                "actor_end_positions": {key: list(value) for key, value in raw.actor_end_positions.items()},
                "actor_facings": {key: list(value) for key, value in raw.actor_facings.items()},
                "actor_tiers": dict(raw.actor_tiers),
                "actor_heights": dict(raw.actor_heights),
                "room_bounds": dict(raw.room_bounds),
                "doors": list(raw.doors),
                "obstacles": list(raw.obstacles),
                "camera_regions": list(raw.camera_regions),
                "scene_number": raw.scene_number,
                "description": raw.description,
                "lines": list(raw.lines),
                "voice_lead_frames": raw.voice_lead_frames,
            }
        return dict(raw)

    def _beat_frames(self, beat: dict[str, Any]) -> int:
        if beat.get("frames") is not None:
            return max(1, int(beat["frames"]))
        return max(12, int(round(float(beat.get("duration", 3.5)) * self.fps)))

    def _hold_allowed(self, beat: dict[str, Any], intent: str) -> bool:
        if beat.get("hold") or beat.get("allow_hold"):
            return True
        importance = float(beat.get("narrative_importance") or beat.get("importance") or 0.0)
        intensity = float(beat.get("intensity") or 0.0)
        return intent == "climax_push" and (importance >= 0.9 or intensity >= 0.9)

    def _pacing_bounds(self, intent: str, hold: bool) -> tuple[float, float]:
        if intent in ACTION_INTENTS:
            high = 6.5 if hold and intent == "climax_push" else (5.0 if hold else 3.0)
            return (1.0, high)
        high = 7.0 if hold else 5.0
        return (2.0, high)

    def _partition_beat(self, beat: dict[str, Any], index: int, previous: dict[str, Any] | None) -> list[dict[str, Any]]:
        explicit = str(beat.get("camera_intent") or beat.get("shot_intent") or "") in SHOT_INTENTS
        intent = self._resolve_intent(beat, index=index, previous=previous)
        reason = self._sequence_cut_reason(beat, intent, index, previous)
        action = str(beat.get("action") or "").strip().lower()
        if (not explicit) and reason == "reaction" and action in TALK_ACTIONS:
            intent = "reaction"
        frames = self._beat_frames(beat)
        hold = self._hold_allowed(beat, intent)
        low, high = self._pacing_bounds(intent, hold)
        parts = _chunk_frames(frames, int(round(low * self.fps)), int(round(high * self.fps)))
        base_id = str(beat.get("beat_id") or beat.get("id") or f"beat{index}")
        pieces: list[dict[str, Any]] = []
        for part_index, part_frames in enumerate(parts):
            piece = dict(beat)
            piece["frames"] = int(part_frames)
            piece["duration"] = part_frames / self.fps
            piece["beat_id"] = base_id if len(parts) == 1 else f"{base_id}_p{part_index}"
            if part_index == 0:
                piece["camera_intent"] = intent
                piece["cut_reason"] = reason
            elif beat.get("listener") and intent in DIALOGUE_INTENTS | {"impact_opening"}:
                piece["camera_intent"] = "reaction"
                piece["cut_reason"] = "reaction"
            else:
                piece["camera_intent"] = intent
                piece["cut_reason"] = "pacing"
            piece["preferred_shot"] = INTENT_TO_BASE[str(piece["camera_intent"])]
            pieces.append(piece)
        return pieces

    def _resolve_intent(self, beat: dict[str, Any], index: int, previous: dict[str, Any] | None) -> str:
        explicit = str(beat.get("camera_intent") or beat.get("shot_intent") or "").strip()
        if explicit in SHOT_INTENTS:
            return explicit
        event = _event_type(beat.get("environment_event"))
        if event in EVENT_INTENT:
            return EVENT_INTENT[event]
        action = str(beat.get("action") or "").strip().lower()
        if action in ACTION_INTENT:
            return ACTION_INTENT[action]
        emotion = str(beat.get("emotion") or "").strip().lower()
        level = float(beat.get("intensity") or beat.get("narrative_importance") or beat.get("importance") or 0.0)
        if emotion in ("fear", "scared", "worried") and level >= 0.6:
            return "high_angle"
        if emotion in ("angry", "rage") and level >= 0.75:
            return "low_angle"
        if action in MOVE_ACTIONS:
            return "action_track"
        if index == 0 and previous is None:
            return "impact_opening"
        return "over_the_shoulder"

    def _sequence_cut_reason(
        self,
        beat: dict[str, Any],
        intent: str,
        index: int,
        previous: dict[str, Any] | None,
    ) -> str:
        event = _event_type(beat.get("environment_event"))
        action = str(beat.get("action") or "").strip().lower()
        # A physical hit is the cut even when the same beat also raises an event.
        if action in IMPACT_ACTIONS or event == "table_impact":
            return "impact"
        if event and event not in ("none", "null"):
            return "environment_event"
        if intent in ("entrance_exit", "action_track", "prop_mech_closeup", "environment_insert"):
            return "action"
        if intent == "reaction" or action in ("react", "listen"):
            return "reaction"
        if previous is not None:
            previous_speaker = str(previous.get("speaker") or "")
            speaker = str(beat.get("speaker") or "")
            previous_action = str(previous.get("action") or "").strip().lower()
            if speaker and previous_speaker and speaker != previous_speaker and action in TALK_ACTIONS and previous_action in TALK_ACTIONS:
                return "reaction"
            return "continuity"
        if index == 0:
            return "scene_open"
        return "continuity"

    def _single_cut_reason(self, beat: dict[str, Any], intent: str) -> str:
        return self._sequence_cut_reason(beat, intent, 0, None)

    def _decorate(self, shot: dict[str, Any], source: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
        intent = str(hint.get("shot_intent") or source.get("camera_intent") or "over_the_shoulder")
        if intent not in SHOT_INTENTS:
            intent = "over_the_shoulder"
        cut_reason = str(hint.get("cut_reason") or source.get("cut_reason") or "continuity")
        if cut_reason == "speaker_change":
            cut_reason = "reaction"
        shot["shot_intent"] = intent
        shot["cut_reason"] = cut_reason
        shot["push_curve"] = self._push_curve(intent)
        self._apply_camera_feel(shot, source, intent)
        punch = self._punch(shot, source, hint, intent)
        if punch is not None:
            shot["camera_punch"] = punch
        meta = shot.setdefault("director_meta", {})
        meta["shot_intent"] = intent
        meta["cut_reason"] = cut_reason
        meta["grammar"] = "ps2_cutscene"
        return shot

    def _push_curve(self, intent: str) -> dict[str, Any]:
        if intent == "climax_push":
            keys = [(0.0, 0.0, 1.0), (0.28, 0.45, 0.86), (1.0, 1.0, 0.72)]
            kind = "ease_in_impact"
        elif intent == "impact_opening":
            keys = [(0.0, 0.0, 1.08), (0.35, 0.40, 0.94), (1.0, 0.85, 0.82)]
            kind = "dynamic_push"
        elif intent in ("action_track", "entrance_exit"):
            keys = [(0.0, 0.0, 1.0), (1.0, 0.70, 0.96)]
            kind = "track"
        elif intent == "low_angle":
            keys = [(0.0, 0.0, 1.0), (1.0, 0.20, 0.94)]
            kind = "low_hold"
        else:
            keys = [(0.0, 0.0, 1.0), (1.0, 0.12, 0.97)]
            kind = "hold"
        return {
            "kind": kind,
            "keys": [{"u": u, "dolly": dolly, "fov": fov} for u, dolly, fov in keys],
        }

    def _punch(self, shot: dict[str, Any], source: dict[str, Any], hint: dict[str, Any], intent: str) -> dict[str, Any] | None:
        event = _event_type(hint.get("environment_event") if hint.get("environment_event") is not None else source.get("environment_event"))
        action = str(hint.get("action") or source.get("action") or "").strip().lower()
        kind = ""
        if action in IMPACT_ACTIONS or event == "table_impact" or action in ("slam_table",):
            kind = "impact"
        elif event in ("door_open", "door_close") or action in ("trigger_door", "door_close_pull", "door_enter", "door_exit", "rush_enter"):
            kind = "door"
        elif event in ("red_alert", "signal_lost", "sensor_error", "power_flicker") or action in ("error_recoil", "console_error_tap"):
            kind = "alarm"
        elif intent in ("climax_push", "impact_opening", "action_track"):
            kind = "push"
        if not kind:
            return None
        frames = max(1, int(shot.get("frames") or 1))
        try:
            intensity = float(hint.get("intensity") or 0.7)
        except (TypeError, ValueError):
            intensity = 0.7
        strength = {
            "impact": 0.55 + 0.40 * min(1.0, intensity),
            "alarm": 0.45 + 0.35 * min(1.0, intensity),
            "door": 0.40 + 0.30 * min(1.0, intensity),
            "push": 0.22 + 0.25 * min(1.0, intensity),
        }[kind]
        return {
            "frame": int(min(frames - 1, max(0, round(frames * 0.28)))),
            "space": "shot",
            "strength": round(strength, 3),
            "duration_frames": int(max(3, min(8, round(0.16 * self.fps)))),
            "kind": PUNCH_KIND[kind],
        }

    def _apply_camera_feel(self, shot: dict[str, Any], source: dict[str, Any], intent: str) -> None:
        cameras = shot.get("cameras") or []
        if not cameras or cameras[0].get("mode") == "pov" or "pos" not in cameras[0]:
            return
        camera = cameras[0]
        original = {
            "pos": list(camera["pos"]),
            "pos_to": list(camera["pos_to"]),
            "look_at": list(camera["look_at"]),
            "look_to": list(camera["look_to"]),
            "fov": camera.get("fov"),
            "fov_to": camera.get("fov_to"),
        }
        proposal = self._propose_feel(original, intent)
        if proposal is None:
            return
        if self._proposal_is_legal(shot, source, proposal):
            self._write_camera(shot, proposal)
        # An illegal bias keeps the parent camera, which already cleared the walls.

    def _propose_feel(self, original: dict[str, Any], intent: str) -> dict[str, Any] | None:
        pos = [float(value) for value in original["pos"]]
        pos_to = [float(value) for value in original["pos_to"]]
        look = [float(value) for value in original["look_at"]]
        look_to = [float(value) for value in original["look_to"]]
        fov = float(original["fov"])
        fov_to = float(original["fov_to"])
        if intent == "low_angle":
            pos[1] = min(pos[1], 0.78)
            pos_to[1] = min(pos_to[1], 0.90)
            look[1] = max(look[1], 1.58)
            look_to[1] = max(look_to[1], 1.52)
        elif intent == "high_angle":
            pos[1] = max(pos[1], 2.42)
            pos_to[1] = max(pos_to[1], 2.30)
            look[1] = min(look[1], 1.22)
            look_to[1] = min(look_to[1], 1.18)
        elif intent == "climax_push":
            fov = max(28.0, fov - 4.0)
            fov_to = max(26.0, fov_to - 6.0)
            pos, pos_to = self._dolly_toward(pos, look, pos_to, look_to, 0.10)
        elif intent == "impact_opening":
            fov = min(64.0, fov + 6.0)
            fov_to = max(30.0, fov_to - 4.0)
            pos, pos_to = self._dolly_toward(pos, look, pos_to, look_to, 0.12)
        elif intent == "prop_mech_closeup":
            fov = min(fov, 34.0)
            fov_to = min(fov_to, 30.0)
        elif intent == "environment_insert":
            fov = min(58.0, max(fov, 42.0))
            fov_to = min(54.0, max(fov_to, 40.0))
        else:
            return {
                "pos": pos,
                "pos_to": pos_to,
                "look_at": look,
                "look_to": look_to,
                "fov": fov,
                "fov_to": fov_to,
            }
        return {
            "pos": pos,
            "pos_to": pos_to,
            "look_at": look,
            "look_to": look_to,
            "fov": fov,
            "fov_to": fov_to,
        }

    @staticmethod
    def _dolly_toward(
        pos: list[float],
        look: list[float],
        pos_to: list[float],
        look_to: list[float],
        distance: float,
    ) -> tuple[list[float], list[float]]:
        def _step(src: list[float], dst: list[float]) -> list[float]:
            delta = (dst[0] - src[0], dst[1] - src[1], dst[2] - src[2])
            length = math.sqrt(delta[0] ** 2 + delta[1] ** 2 + delta[2] ** 2) or 1.0
            scale = min(distance, length * 0.25) / length
            return [src[0] + delta[0] * scale, src[1] + delta[1] * scale, src[2] + delta[2] * scale]

        return _step(pos, look), _step(pos_to, look_to)

    def _proposal_is_legal(self, shot: dict[str, Any], source: dict[str, Any], proposal: dict[str, Any]) -> bool:
        beat = BeatSpec.from_input(source)
        room_min = _parse_pos3(beat.room_bounds.get("min", [-6.0, 0.0, -4.5]))
        room_max = _parse_pos3(beat.room_bounds.get("max", [6.0, 3.4, 4.5]))
        # Keep the biased camera inside the same clearance box the parent enforces.
        for key in ("pos", "pos_to"):
            point = proposal[key]
            point[0] = min(room_max[0] - self.wall_clearance_m, max(room_min[0] + self.wall_clearance_m, point[0]))
            point[1] = min(room_max[1] - self.wall_clearance_m, max(room_min[1] + self.wall_clearance_m, point[1]))
            point[2] = min(room_max[2] - self.wall_clearance_m, max(room_min[2] + self.wall_clearance_m, point[2]))
        primary = str(shot.get("primary_subject") or "")
        secondary = str(shot.get("secondary_subject") or "")
        shot_type = str(shot.get("type") or "medium")
        candidate = CameraCandidate(
            candidate_id="dramatic_bias",
            mode="dolly",
            pos=(proposal["pos"][0], proposal["pos"][1], proposal["pos"][2]),
            pos_to=(proposal["pos_to"][0], proposal["pos_to"][1], proposal["pos_to"][2]),
            look_at=(proposal["look_at"][0], proposal["look_at"][1], proposal["look_at"][2]),
            look_to=(proposal["look_to"][0], proposal["look_to"][1], proposal["look_to"][2]),
            fov=float(proposal["fov"]),
            fov_to=float(proposal["fov_to"]),
        )
        jambs = _doorframe_aabbs(room_min, room_max, beat.doors)
        pair_key, pair_positions = self._resolve_interaction_pair(beat, primary, secondary)
        self._score_candidate(
            candidate, beat, shot_type, primary, secondary, room_min, room_max, jambs, pair_key, pair_positions,
        )
        legal = (
            candidate.wall_clearance_m + 1e-6 >= self.wall_clearance_m
            and candidate.doorframe_clearance_m + 1e-6 >= self.doorframe_clearance_m
            and not candidate.dynamic_occluded
            and not candidate.camera_inside_actor
            and not candidate.occluded
            and not candidate.crosses_180_axis
            and not candidate.frustum_edge_blocked
        )
        if legal:
            shot.setdefault("director_meta", {})
            shot["director_meta"]["wall_clearance_m"] = candidate.wall_clearance_m
            shot["director_meta"]["doorframe_clearance_m"] = candidate.doorframe_clearance_m
            shot["director_meta"]["dynamic_occluded"] = False
            shot["director_meta"]["occluded"] = False
            shot["director_meta"]["crosses_180_axis"] = False
            shot["director_meta"]["camera_inside_actor"] = False
        return legal

    def _write_camera(self, shot: dict[str, Any], proposal: dict[str, Any]) -> None:
        camera = shot["cameras"][0]
        camera["pos"] = _r3(proposal["pos"])
        camera["pos_to"] = _r3(proposal["pos_to"])
        camera["look_at"] = _r3(proposal["look_at"])
        camera["look_to"] = _r3(proposal["look_to"])
        camera["fov"] = round(float(proposal["fov"]), 2)
        camera["fov_to"] = round(float(proposal["fov_to"]), 2)
        shot["fov"] = camera["fov"]
        keyframes = (shot.get("camera") or {}).get("keyframes") or []
        if len(keyframes) >= 2:
            keyframes[0]["pos"] = list(camera["pos"])
            keyframes[0]["look_at"] = list(camera["look_at"])
            keyframes[0]["fov"] = camera["fov"]
        keyframes[1]["pos"] = list(camera["pos_to"])
        keyframes[1]["look_at"] = list(camera["look_to"])
        keyframes[1]["fov"] = camera["fov_to"]

    def _with_layout_bounds(self, source: dict[str, Any], layout: Any | None) -> dict[str, Any]:
        """Copy solved room, doors, and obstacles onto a beat that did not carry them."""
        if layout is None or source.get("room_bounds") or source.get("room"):
            return source
        if hasattr(layout, "to_stage3d_set"):
            set_doc = layout.to_stage3d_set(resolve_asset_paths=False)
        elif isinstance(layout, dict):
            set_doc = layout
        else:
            return source
        enriched = dict(source)
        if set_doc.get("room"):
            enriched["room_bounds"] = set_doc["room"]
        if set_doc.get("doors") and not enriched.get("doors"):
            enriched["doors"] = list(set_doc["doors"])
        elif isinstance(set_doc.get("door"), dict) and not enriched.get("doors"):
            enriched["doors"] = [dict(set_doc["door"])]
        if set_doc.get("obstacles") and not enriched.get("obstacles"):
            enriched["obstacles"] = list(set_doc["obstacles"])
        return enriched
