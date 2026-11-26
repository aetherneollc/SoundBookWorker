#!/usr/bin/env python3
"""Turn a dramatic scene spec into performance beats with real UAL actions.

A beat is not a static face-to-face talking slot. Each ``PerformanceBeat``
carries an objective, a conflict, an emotion, an intensity, a physical
action, the other person's reaction, a target, blocking, an environment
event, a camera intent, and a sound event. The planner enforces a
setup -> escalate -> climax -> release intensity curve and breaks up runs of
two or more standing talk beats by assigning work the actors can do while
the line is spoken (console, table slam, storm window, module install, pace).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from semantic_animation_library import PHASE_NAMES, SemanticAnimationLibrary
except ImportError:
    from renderer.semantic_animation_library import PHASE_NAMES, SemanticAnimationLibrary

TENSION_PHASES: tuple[str, ...] = ("setup", "escalate", "climax", "release")
REQUIRED_BEAT_FIELDS: tuple[str, ...] = (
    "objective",
    "conflict",
    "emotion",
    "intensity",
    "action",
    "reaction",
    "target",
    "blocking",
    "environment_event",
    "camera_intent",
    "sound_event",
)
TALKISH_ACTIONS = frozenset({
    "talk", "speak", "dialogue", "converse", "hold", "listen", "stand", "idle",
    "face_to_face", "none", "",
})
STATIC_BLOCKING = frozenset({
    "", "stand", "standing", "face_to_face", "face to face", "talk", "idle", "neutral", "dialogue",
})
PURPOSEFUL_ACTIONS: tuple[str, ...] = (
    "operate_console",
    "slam_table",
    "check_storm_window",
    "install_module",
    "pace_tense",
)
PURPOSEFUL_BLOCKING = {
    "operate_console": "crosses to the console and works the board while speaking",
    "slam_table": "steps in and slams the table on the line",
    "check_storm_window": "turns to the storm window without dropping the argument",
    "install_module": "installs the sensor module while the line continues",
    "pace_tense": "paces the floor instead of standing face to face",
}
EMOTION_REACTION = {
    "angry": "head_shake_no",
    "rage": "step_back_shock",
    "fear": "fear_brace",
    "scared": "fear_brace",
    "surprise": "startle_recoil",
    "surprised": "startle_recoil",
    "shocked": "startle_recoil",
    "tense": "impatient_shift",
    "worried": "step_back_shock",
    "urgent": "abrupt_halt",
    "relieved": "exhale_relax",
    "sad": "exhale_relax",
    "weary": "catch_breath",
    "confident": "firm_nod",
    "neutral": "firm_nod",
    "focused": "firm_nod",
}
ACTION_EVENT = {
    "slam_table": "table_impact",
    "table_lean_in": "table_impact",
    "operate_console": "device_operating",
    "press_button": "device_operating",
    "flip_switch": "device_operating",
    "pull_lever": "device_operating",
    "open_panel": "device_operating",
    "close_panel": "device_operating",
    "device_reboot": "array_restore",
    "device_scan": "device_operating",
    "console_error_tap": "sensor_error",
    "error_recoil": "sensor_error",
    "signal_pulse": "signal_lost",
    "install_module": "module_install",
    "mount_sensor": "module_install",
    "seat_module": "module_install",
    "lock_clamp": "module_install",
    "detach_module": "device_operating",
    "unlatch_cover": "device_operating",
    "repair_kneel": "device_operating",
    "repair_stand": "device_operating",
    "inspect_gauge": "device_operating",
    "trigger_door": "door_open",
    "door_enter": "door_open",
    "rush_enter": "door_open",
    "door_exit": "door_close",
    "door_close_pull": "door_close",
    "turn_walk_away": "door_close",
    "check_storm_window": "storm_approaching",
    "idle_window_watch": "storm_approaching",
}
ACTION_SOUND = {
    "slam_table": "table_slam",
    "table_lean_in": "table_slam",
    "operate_console": "console_beep",
    "press_button": "console_beep",
    "flip_switch": "console_beep",
    "console_error_tap": "console_error",
    "error_recoil": "console_error",
    "signal_pulse": "console_beep",
    "device_reboot": "power_restore",
    "install_module": "module_install",
    "mount_sensor": "module_install",
    "seat_module": "module_install",
    "lock_clamp": "metal_clang",
    "detach_module": "module_detach",
    "unlatch_cover": "module_detach",
    "repair_kneel": "metal_clang",
    "repair_stand": "metal_clang",
    "trigger_door": "door_open_hiss",
    "door_enter": "door_open_hiss",
    "rush_enter": "footstep_heavy",
    "door_exit": "door_close_thud",
    "door_close_pull": "door_close_thud",
    "check_storm_window": "wind_gust",
    "pace_tense": "footstep_heavy",
    "confront_step": "footstep_heavy",
    "walk_casual": "footstep_light",
    "walk_urgent": "footstep_heavy",
    "sprint_burst": "footstep_heavy",
    "flinch_dodge": "cloth_rustle",
    "startle_recoil": "cloth_rustle",
    "pass_prop": "cloth_rustle",
    "receive_prop": "cloth_rustle",
}
EVENT_SOUND = {
    "table_impact": "table_slam",
    "device_operating": "console_beep",
    "module_install": "module_install",
    "door_open": "door_open_hiss",
    "door_close": "door_close_thud",
    "storm_approaching": "wind_gust",
    "sensor_error": "console_error",
    "signal_lost": "console_error",
    "red_alert": "alarm_klaxon",
    "power_flicker": "power_down",
    "array_restore": "power_restore",
    "robot_speaking": "console_beep",
}
ACTION_CAMERA = {
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
    "rush_enter": "entrance_exit",
    "turn_walk_away": "entrance_exit",
    "door_enter": "entrance_exit",
    "door_exit": "entrance_exit",
    "trigger_door": "entrance_exit",
    "pace_tense": "action_track",
    "confront_step": "low_angle",
    "accuse_point": "over_the_shoulder",
    "fear_brace": "high_angle",
    "startle_recoil": "reaction",
    "step_back_shock": "reaction",
    "flinch_dodge": "action_track",
    "snap_look_back": "reaction",
    "rage_clench": "low_angle",
    "head_shake_no": "reaction",
}
EVENT_CAMERA = {
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
CAMERA_INTENTS = frozenset({
    "impact_opening", "entrance_exit", "action_track", "low_angle", "high_angle",
    "over_the_shoulder", "reaction", "prop_mech_closeup", "environment_insert", "climax_push",
})
INTENSITY_BANDS = {
    "setup": (0.30, 0.50),
    "escalate": (0.52, 0.72),
    "climax": (0.78, 1.00),
    "release": (0.28, 0.48),
}
SUPPORTED_EVENTS = frozenset({
    "signal_lost", "sensor_error", "red_alert", "storm_approaching", "device_operating",
    "robot_speaking", "door_open", "door_close", "power_flicker", "array_restore",
    "table_impact", "module_install", "none",
})


@dataclass
class PerformanceBeat:
    """One dramatic beat. The first eleven fields are required on every beat."""

    objective: str
    conflict: str
    emotion: str
    intensity: float
    action: str
    reaction: str
    target: str
    blocking: str
    environment_event: dict[str, Any]
    camera_intent: str
    sound_event: str
    beat_id: str = ""
    actor: str = ""
    partner: str = ""
    duration: float = 3.0
    from_frame: int = 0
    to_frame: int = 0
    tension_phase: str = "setup"
    scene_type: str = ""
    text: str = ""
    hold: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "objective": self.objective,
            "conflict": self.conflict,
            "emotion": self.emotion,
            "intensity": self.intensity,
            "action": self.action,
            "reaction": self.reaction,
            "target": self.target,
            "blocking": self.blocking,
            "environment_event": dict(self.environment_event),
            "camera_intent": self.camera_intent,
            "sound_event": self.sound_event,
            "beat_id": self.beat_id,
            "actor": self.actor,
            "partner": self.partner,
            "duration": self.duration,
            "from_frame": self.from_frame,
            "to_frame": self.to_frame,
            "from": self.from_frame,
            "to": self.to_frame,
            "tension_phase": self.tension_phase,
            "scene_type": self.scene_type,
            "text": self.text,
            "hold": self.hold,
        }
        return payload


def _as_mapping(raw: PerformanceBeat | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, PerformanceBeat):
        return raw.to_dict()
    if isinstance(raw, dict):
        return dict(raw)
    raise TypeError(f"beat must be a dict or PerformanceBeat, got {type(raw).__name__}")


def is_static_talk(raw: PerformanceBeat | dict[str, Any]) -> bool:
    """True when the beat is standing face-to-face talk with no physical task."""
    data = _as_mapping(raw)
    action = str(data.get("action") or "").strip().lower()
    blocking = str(data.get("blocking") or "").strip().lower().replace("-", "_")
    if action not in TALKISH_ACTIONS:
        return False
    if not blocking or blocking in STATIC_BLOCKING or "face_to_face" in blocking:
        return True
    return False


def phase_list(count: int) -> list[str]:
    """Non-decreasing tension phases. Four or more beats include every phase."""
    if count <= 0:
        return []
    if count == 1:
        return ["climax"]
    if count == 2:
        return ["setup", "climax"]
    if count == 3:
        return ["setup", "climax", "release"]
    buckets = [1, 1, 1, 1]
    extras = count - 4
    for index in range(extras):
        buckets[(1, 2, 0, 3)[index % 4]] += 1
    phases: list[str] = []
    for name, copies in zip(TENSION_PHASES, buckets):
        phases.extend([name] * copies)
    return phases


def _event_type(raw: Any) -> str:
    if isinstance(raw, str):
        return raw.strip().lower()
    if isinstance(raw, dict):
        return str(raw.get("type") or raw.get("event") or "").strip().lower()
    return ""


def _normalize_event(raw: Any, start: int, end: int, intensity: float, target: str) -> dict[str, Any]:
    kind = _event_type(raw)
    if kind in ("", "none", "null"):
        return {"type": "none", "from": int(start), "to": int(end), "intensity": 0.0, "target": str(target or "")}
    if isinstance(raw, dict):
        return {
            "type": kind,
            "from": int(raw.get("from", start)),
            "to": int(raw.get("to", end)),
            "intensity": float(raw.get("intensity", intensity)),
            "target": str(raw.get("target") or target or ""),
        }
    return {
        "type": kind,
        "from": int(start),
        "to": int(end),
        "intensity": float(intensity),
        "target": str(target or ""),
    }


def _curve_is_valid(beats: list[dict[str, Any]]) -> bool:
    if not beats:
        return False
    expected = phase_list(len(beats))
    phases = [str(beat.get("tension_phase") or "") for beat in beats]
    if phases != expected:
        return False
    for beat, phase in zip(beats, phases):
        lo, hi = INTENSITY_BANDS[phase]
        value = float(beat.get("intensity", -1))
        if not lo - 1e-6 <= value <= hi + 1e-6:
            return False
    return True


class PerformancePlanner:
    """Compile high-level scene specs into ranked, library-backed beats."""

    def __init__(self, library: SemanticAnimationLibrary | None = None) -> None:
        self.library = library if library is not None else SemanticAnimationLibrary()

    def plan(self, scene: dict[str, Any] | list[Any]) -> list[PerformanceBeat]:
        """Validate, remediate, and return production-ready beats."""
        if isinstance(scene, dict):
            raw_beats = list(scene.get("beats") or [])
            scene_type = str(scene.get("scene_type") or "")
            fps = int(scene.get("fps") or 24)
        else:
            raw_beats = list(scene)
            scene_type = ""
            fps = 24
        report = self.validate_scene_beats(raw_beats, scene_type=scene_type, fps=fps)
        return list(report["beats"])

    def validate_scene_beats(
        self,
        beats: list[Any],
        scene_type: str = "",
        fps: int = 24,
    ) -> dict[str, Any]:
        """Flag static talk runs and a flat curve, then return the fixed beats.

        A second call on the returned beats reports ``ok`` when the scene
        already climbs through the tension curve and no longer contains two
        standing talk beats in a row.
        """
        rows = [_as_mapping(beat) for beat in beats]
        issues: list[str] = []
        static_runs = self._static_runs(rows)
        for start, end in static_runs:
            issues.append(
                f"static_talking_heads: beats {start}-{end - 1} are consecutive face-to-face standing talk"
            )
            for index in range(start, end):
                self._assign_purposeful(rows[index], index - start)
        if not _curve_is_valid(rows):
            if rows:
                issues.append("tension_curve: scene does not follow setup -> escalate -> climax -> release")
        self._apply_tension(rows)
        self._assign_frames(rows, fps)
        planned = [self._finalize(row, scene_type) for row in rows]
        return {
            "ok": not issues,
            "issues": issues,
            "static_runs": static_runs,
            "tension_ok": True,
            "beats": planned,
        }

    def compile_dramatic_actions(self, beats: list[PerformanceBeat], fps: int = 24) -> dict[str, list[dict[str, Any]]]:
        """Map each beat's action and reaction onto phase-structured timeline entries."""
        del fps  # frames are already absolute on the beat
        timeline: dict[str, list[dict[str, Any]]] = {}
        for beat in beats:
            if beat.actor:
                timeline.setdefault(beat.actor, []).append(
                    self._stage_action(beat.action, beat.from_frame, beat.to_frame, beat.intensity, beat.target)
                )
            if beat.partner and beat.partner != beat.actor and beat.reaction:
                span = max(1, beat.to_frame - beat.from_frame)
                react_from = beat.from_frame + int(span * 0.28)
                react_to = max(react_from + 1, beat.to_frame)
                timeline.setdefault(beat.partner, []).append(
                    self._stage_action(beat.reaction, react_from, react_to, max(0.35, beat.intensity * 0.85), beat.actor or beat.target)
                )
        for entries in timeline.values():
            entries.sort(key=lambda item: (item["from"], item["action_id"]))
        return timeline

    def environment_events(self, beats: list[PerformanceBeat]) -> list[dict[str, Any]]:
        """Concrete environment events authored on the beats, skipping empty ones."""
        events: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for beat in beats:
            event = beat.environment_event
            if not isinstance(event, dict) or event.get("type") in (None, "", "none"):
                continue
            key = (event.get("type"), int(event.get("from", beat.from_frame)), str(event.get("target") or ""))
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "type": str(event["type"]),
                "from": int(event.get("from", beat.from_frame)),
                "to": int(event.get("to", beat.to_frame)),
                "intensity": float(event.get("intensity", beat.intensity)),
                "target": str(event.get("target") or beat.target or ""),
            })
        return events

    def _stage_action(self, action_id: str, start: int, end: int, intensity: float, target: str) -> dict[str, Any]:
        resolved = action_id if action_id in self.library.by_id else self._canonicalize(action_id, "", "idle_neutral")
        spec = self.library.export_godot_spec(resolved, intensity=intensity, target=target)
        phases = spec["phases"]
        if set(phases) != set(PHASE_NAMES):
            raise RuntimeError(f"{resolved} is missing dramatic phases")
        return {
            "from": int(start),
            "to": int(max(end, start + 1)),
            "action_id": spec["action_id"],
            "base_clip": spec["base_clip"],
            "dedicated_clip": spec.get("dedicated_clip") or "",
            "clip_source": spec.get("clip_source") or "",
            "intensity": float(spec["intensity"]),
            "target": str(target or spec["target"]),
            "root_motion": bool(spec["root_motion"]),
            "one_shot": bool(spec.get("one_shot", False)),
            "phases": phases,
        }

    def _static_runs(self, rows: list[dict[str, Any]]) -> list[tuple[int, int]]:
        runs: list[tuple[int, int]] = []
        index = 0
        count = len(rows)
        while index < count:
            if is_static_talk(rows[index]) and index + 1 < count and is_static_talk(rows[index + 1]):
                end = index + 2
                while end < count and is_static_talk(rows[end]):
                    end += 1
                runs.append((index, end))
                index = end
            else:
                index += 1
        return runs

    def _assign_purposeful(self, row: dict[str, Any], offset: int) -> None:
        action_id = PURPOSEFUL_ACTIONS[offset % len(PURPOSEFUL_ACTIONS)]
        row["action"] = action_id
        row["blocking"] = PURPOSEFUL_BLOCKING[action_id]
        if str(row.get("camera_intent") or "") not in CAMERA_INTENTS:
            row["camera_intent"] = ACTION_CAMERA.get(action_id, "over_the_shoulder")

    def _apply_tension(self, rows: list[dict[str, Any]]) -> None:
        phases = phase_list(len(rows))
        for row, phase in zip(rows, phases):
            lo, hi = INTENSITY_BANDS[phase]
            authored = row.get("intensity", row.get("importance", (lo + hi) * 0.5))
            try:
                authored_f = float(authored)
            except (TypeError, ValueError):
                authored_f = (lo + hi) * 0.5
            mid = (lo + hi) * 0.5
            value = (0.55 * mid) + (0.45 * authored_f)
            row["tension_phase"] = phase
            row["intensity"] = round(min(hi, max(lo, value)), 3)

    def _assign_frames(self, rows: list[dict[str, Any]], fps: int) -> None:
        cursor = 0
        fps = max(1, int(fps))
        for row in rows:
            duration = max(0.25, float(row.get("duration") or 3.0))
            if row.get("from") is not None or row.get("from_frame") is not None:
                start = int(row.get("from", row.get("from_frame")))
            else:
                start = cursor
            if row.get("to") is not None or row.get("to_frame") is not None:
                end = int(row.get("to", row.get("to_frame")))
            else:
                end = start + max(1, int(round(duration * fps)))
            if end <= start:
                end = start + 1
            row["from"] = start
            row["to"] = end
            row["from_frame"] = start
            row["to_frame"] = end
            row["duration"] = (end - start) / fps
            cursor = max(cursor, end)

    def _finalize(self, row: dict[str, Any], scene_type: str) -> PerformanceBeat:
        emotion = str(row.get("emotion") or "neutral").strip().lower() or "neutral"
        actor = str(row.get("actor") or row.get("speaker") or "")
        partner = str(row.get("partner") or row.get("listener") or "")
        target = str(row.get("target") or partner or row.get("prop") or "scene")
        action = self._canonicalize(str(row.get("action") or ""), emotion, self._talk_fallback(emotion))
        reaction = self._canonicalize(
            str(row.get("reaction") or ""),
            emotion,
            EMOTION_REACTION.get(emotion, "firm_nod"),
        )
        start = int(row.get("from_frame", row.get("from", 0)))
        end = int(row.get("to_frame", row.get("to", start + 1)))
        intensity = float(row.get("intensity") or 0.5)
        event = row.get("environment_event")
        if _event_type(event) in ("", "none", "null"):
            inferred = ACTION_EVENT.get(action, "")
            event = inferred or "none"
        event_doc = _normalize_event(event, start, end, intensity, target)
        if event_doc["type"] not in SUPPORTED_EVENTS:
            event_doc["type"] = "none"
            event_doc["intensity"] = 0.0
        camera = str(row.get("camera_intent") or "")
        if camera not in CAMERA_INTENTS:
            camera = ACTION_CAMERA.get(action) or EVENT_CAMERA.get(event_doc["type"]) or "over_the_shoulder"
        sound = str(row.get("sound_event") or "").strip()
        if not sound or sound == "none":
            sound = ACTION_SOUND.get(action) or EVENT_SOUND.get(event_doc["type"]) or "cloth_rustle"
        objective = str(row.get("objective") or "").strip()
        if not objective:
            objective = f"{actor or 'The actor'} uses {action} to press {target}"
        conflict = str(row.get("conflict") or "").strip()
        if not conflict:
            other = partner or "the room"
            conflict = f"{other} resists {action}"
        blocking = str(row.get("blocking") or "").strip()
        blocking_key = blocking.lower().replace("-", "_")
        if (
            not blocking
            or blocking_key in STATIC_BLOCKING
            or "face_to_face" in blocking_key
            or is_static_talk({"action": row.get("action"), "blocking": blocking})
        ):
            blocking = PURPOSEFUL_BLOCKING.get(action, f"performs {action} while the line plays")
        phase = str(row.get("tension_phase") or "setup")
        if phase not in TENSION_PHASES:
            phase = "setup"
        return PerformanceBeat(
            objective=objective,
            conflict=conflict,
            emotion=emotion,
            intensity=intensity,
            action=action,
            reaction=reaction,
            target=target,
            blocking=blocking,
            environment_event=event_doc,
            camera_intent=camera,
            sound_event=sound,
            beat_id=str(row.get("beat_id") or row.get("id") or ""),
            actor=actor,
            partner=partner,
            duration=float(row.get("duration") or 3.0),
            from_frame=start,
            to_frame=end,
            tension_phase=phase,
            scene_type=str(row.get("scene_type") or scene_type or ""),
            text=str(row.get("text") or ""),
            hold=bool(row.get("hold") or False),
        )

    def _talk_fallback(self, emotion: str) -> str:
        if emotion in ("angry", "rage"):
            return "idle_talk_heated"
        if emotion in ("fear", "worried", "tense"):
            return "idle_talk_whisper"
        return "idle_talk_calm"

    def _canonicalize(self, name: str, emotion: str, fallback: str) -> str:
        token = (name or "").strip()
        if token in self.library.by_id:
            return token
        if token:
            matches = self.library.query(intent=token, emotion=emotion or None)
            if matches and (
                matches[0].action_id == token
                or token in matches[0].intent_tags
                or matches[0].action_id.startswith(token + "_")
            ):
                return matches[0].action_id
        if fallback in self.library.by_id:
            return fallback
        return "idle_neutral"
