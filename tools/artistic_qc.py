#!/usr/bin/env python3
"""Artistic QC for a rendered episode. Separate from Engineering QC.

Engineering PASS does not imply Artistic PASS. This tool reads the stage,
optional Godot telemetry, optional review stills, and optional video duration.
It does not decide whether the render pipeline itself succeeded.

    python tools/artistic_qc.py --stage stage3d.json --telemetry qc_telemetry.json \\
        --video episode.mp4 --stills review_dir --out artistic_qc_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NOTE = "Artistic QC assists human visual review; Engineering PASS does not imply Artistic PASS."
MAJOR_EVENTS = ("array_restore", "module_install", "table_impact", "door_open", "storm_approaching")
GENERIC_CLIPS = {"Idle_Talking_Loop", "Interact"}
LOCOMOTION_TOKENS = ("walk", "jog", "sprint", "crouch_fwd", "roll", "pace", "halt", "turn_walk")
HANDOFF_GAP_M = 0.14
CONTACT_GAP_M = 0.18
SFX_SYNC_S = 0.120
STATIC_LIMIT = 0.45
BACK_HEAD_FRACTION = 0.45
SHADOW_LUMA = 12
HIGHLIGHT_LUMA = 245
STILL_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
CRISIS_EVENTS = {"power_flicker", "red_alert", "signal_lost", "sensor_error", "storm_approaching", "outage"}
RECOVERY_EVENTS = {"array_restore", "device_operating", "power_restore"}


def run_artistic_qc(stage: dict, telemetry=None, video_path: str | Path | None = None,
                    stills: str | Path | None = None) -> dict:
    rows = _frame_rows(telemetry)
    checks = [
        _event_visibility(stage, rows),
        _action_semantic_match(stage),
        _pose_repetition(stage),
        _static_standing_ratio(stage),
        _physical_contact(stage, rows),
        _subject_dominance(stage, rows),
        _visual_exposure_balance(stills),
        _climax_visual_delta(stage, stills),
    ]
    status = "PASS"
    for check in checks:
        if check["status"] == "FAIL":
            status = "FAIL"
            break
        if check["status"] == "WARN" and status == "PASS":
            status = "WARN"
    video = _video_note(video_path, stage)
    return {
        "note": NOTE,
        "kind": "artistic_qc",
        "status": status,
        "video": video,
        "checks": checks,
    }


def _event_visibility(stage: dict, rows: list[dict]) -> dict:
    issues = []
    events = [event for event in _events(stage) if event["type"] in MAJOR_EVENTS]
    if not events:
        return _result("event_visibility", "Major event visibility", [], {
            "events_checked": 0, "missing_types": list(MAJOR_EVENTS),
        })
    width = float(stage.get("width") or 1280)
    height = float(stage.get("height") or 720)
    aspect = width / max(1.0, height)
    for event in events:
        point = _event_point(stage, event)
        if point is None:
            issues.append(_issue("WARN", event["from"], event["type"], "target position is unknown"))
            continue
        samples = _frames_for(stage, event["from"], event["to"])
        if not samples:
            issues.append(_issue("WARN", event["from"], event["type"], "no camera covers the event"))
            continue
        inside = 0
        back_only = 0
        for frame, camera in samples:
            ndc = _project(camera, point, aspect)
            telemetry_row = _row_at(rows, frame)
            runtime_hit = _runtime_event_in_frame(telemetry_row, event) if telemetry_row is not None else False
            if telemetry_row is not None:
                ndc = _telemetry_ndc(telemetry_row, event) or ndc
            if runtime_hit:
                inside += 1
                continue
            if ndc is None or abs(ndc[0]) > 1.05 or abs(ndc[1]) > 1.05 or ndc[2] <= 0.05:
                continue
            inside += 1
            if _back_view_only(stage, camera, point, ndc):
                back_only += 1
        if inside == 0:
            issues.append(_issue("FAIL", event["from"], event["type"], "target never entered the camera frustum"))
        elif back_only == inside:
            issues.append(_issue(
                "FAIL", event["from"], event["type"],
                "target was inside the frustum only as back_view_only",
            ))
    return _result("event_visibility", "Major event visibility", issues, {"events_checked": len(events)})


def _action_semantic_match(stage: dict) -> dict:
    actions = _dramatic_entries(stage)
    if not actions:
        return _result("action_semantic_match", "Dedicated clip versus generic fallback", [
            _issue("WARN", None, "stage", "no dramatic actions to score"),
        ], {"beats": 0})
    generic = 0
    dedicated = 0
    for entry in actions:
        clip = str(entry.get("base_clip") or "")
        if clip in GENERIC_CLIPS or not clip:
            generic += 1
        else:
            dedicated += 1
    fraction = generic / max(1, len(actions))
    issues = []
    if fraction > 0.85:
        issues.append(_issue("FAIL", None, "actions", f"{generic}/{len(actions)} beats use Idle_Talking_Loop or Interact"))
    elif fraction > 0.50:
        issues.append(_issue("WARN", None, "actions", f"{generic}/{len(actions)} beats use a generic fallback clip"))
    return _result("action_semantic_match", "Dedicated clip versus generic fallback", issues, {
        "beats": len(actions), "dedicated": dedicated, "generic_fallback": generic,
        "generic_fraction": round(fraction, 3),
    })


def _pose_repetition(stage: dict) -> dict:
    shots = [shot for shot in (stage.get("shots") or []) if isinstance(shot, dict)]
    issues = []
    repeats = 0
    for earlier, later in zip(shots, shots[1:]):
        actor = str(later.get("primary_subject") or "")
        if not actor or actor != str(earlier.get("primary_subject") or ""):
            continue
        left = _shot_signature(stage, earlier, actor)
        right = _shot_signature(stage, later, actor)
        if left and left == right:
            repeats += 1
            issues.append(_issue(
                "WARN", int(later.get("from") or 0), actor,
                f"repeats {left} from the previous shot",
            ))
    return _result("pose_repetition", "Repeated gesture across consecutive shots", issues, {"repeats": repeats})


def _static_standing_ratio(stage: dict) -> dict:
    fps = max(1.0, float(stage.get("fps") or 24))
    total = float(stage.get("total_frames") or 0) / fps
    if total <= 0:
        shots = stage.get("shots") or []
        total = sum(float(shot.get("frames") or 0) for shot in shots if isinstance(shot, dict)) / fps
    if total <= 0:
        return _result("static_standing_ratio", "Static face-to-face standing ratio", [
            _issue("WARN", None, "stage", "duration is unknown"),
        ], {})
    static = 0.0
    for entry in _dramatic_entries(stage):
        if _is_static_entry(entry, stage):
            static += max(0.0, float(entry.get("to", 0)) - float(entry.get("from", 0))) / fps
    if not _dramatic_entries(stage):
        # No performance track: standing dialogue shots with no moves and no events.
        if not _any_motion(stage) and not _events(stage):
            static = total
    ratio = min(1.0, static / total)
    issues = []
    if ratio > STATIC_LIMIT:
        issues.append(_issue(
            "FAIL", None, "stage",
            f"static face-to-face standing is {ratio:.0%} of the timeline",
        ))
    return _result("static_standing_ratio", "Static face-to-face standing ratio", issues, {
        "ratio": round(ratio, 3), "limit": STATIC_LIMIT, "static_s": round(static, 3), "total_s": round(total, 3),
    })


def _physical_contact(stage: dict, rows: list[dict]) -> dict:
    issues = []
    metrics = {"handoff_samples": 0, "contact_samples": 0, "sfx_pairs": 0}
    if not rows:
        issues.append(_issue("WARN", None, "telemetry", "no telemetry for hand and prop gaps"))
    else:
        for frame, gap, subject in _handoff_gaps(rows):
            metrics["handoff_samples"] += 1
            if gap > HANDOFF_GAP_M:
                issues.append(_issue("FAIL", frame, subject, f"handoff hand-to-prop gap {gap:.3f}m"))
        for frame, gap, subject in _contact_gaps(stage, rows):
            metrics["contact_samples"] += 1
            if gap > CONTACT_GAP_M:
                issues.append(_issue("FAIL", frame, subject, f"contact gap {gap:.3f}m during {subject}"))
    for delta, subject, frame in _sfx_deltas(stage):
        metrics["sfx_pairs"] += 1
        if abs(delta) > SFX_SYNC_S:
            issues.append(_issue("FAIL", frame, subject, f"SFX sync delta {delta * 1000:.0f}ms"))
    if metrics["sfx_pairs"] == 0:
        issues.append(_issue("WARN", None, "audio", "no event/SFX pairs to compare"))
    return _result("physical_contact", "Contact distance and SFX sync", issues, metrics)


def _subject_dominance(stage: dict, rows: list[dict]) -> dict:
    issues = []
    blocked = 0
    sidelined = 0
    shots = [shot for shot in (stage.get("shots") or []) if isinstance(shot, dict)]
    for shot in shots:
        speaker = str(shot.get("primary_subject") or "")
        other = str(shot.get("secondary_subject") or "")
        frames = _frames_for(stage, int(shot.get("from") or 0), int(shot.get("from") or 0) + int(shot.get("frames") or 1))
        for frame, camera in frames[:3]:
            if other:
                fraction = _head_screen_fraction(stage, camera, other)
                facing_away = _actor_faces_away(stage, camera, other)
                if facing_away and fraction > BACK_HEAD_FRACTION:
                    blocked += 1
                    issues.append(_issue(
                        "FAIL", frame, other,
                        f"foreground back-of-head covers {fraction:.0%} of the frame",
                    ))
                    break
            row = _row_at(rows, frame)
            if row and speaker and other:
                speaker_ndc = _actor_ndc(row, speaker)
                other_ndc = _actor_ndc(row, other)
                if speaker_ndc and other_ndc and abs(speaker_ndc[0]) > 0.55 and abs(other_ndc[0]) < 0.2:
                    sidelined += 1
                    issues.append(_issue(
                        "WARN", frame, speaker,
                        "speaker is sidelined while the silent actor dominates center frame",
                    ))
                    break
    return _result("subject_dominance", "Framing balance", issues, {
        "back_head_blocks": blocked, "sidelined_speakers": sidelined,
    })


def _visual_exposure_balance(stills) -> dict:
    paths = _still_paths(stills)
    if not paths:
        return _result("visual_exposure_balance", "Shadow, highlight, and tile repetition", [
            _issue("WARN", None, "stills", "no review stills"),
        ], {"stills": 0})
    issues = []
    crushed = []
    blown = []
    tiles = []
    for index, path in enumerate(paths):
        try:
            image = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            issues.append(_issue("FAIL", index, path.name, f"unreadable still: {exc}"))
            continue
        actor = _actor_region(image)
        luma = 0.2126 * actor[:, :, 0] + 0.7152 * actor[:, :, 1] + 0.0722 * actor[:, :, 2]
        crush = float(np.mean(luma < SHADOW_LUMA))
        blow = float(np.mean(luma > HIGHLIGHT_LUMA))
        face = luma[: max(1, luma.shape[0] // 2), :]
        face_blow = float(np.mean(face > HIGHLIGHT_LUMA))
        crushed.append(crush)
        blown.append(max(blow, face_blow))
        if crush > 0.55:
            issues.append(_issue("FAIL", index, path.name, f"crushed actor shadows {crush:.0%}"))
        elif crush > 0.35:
            issues.append(_issue("WARN", index, path.name, f"heavy actor shadows {crush:.0%}"))
        if face_blow > 0.20:
            issues.append(_issue("FAIL", index, path.name, f"blown facial highlights {face_blow:.0%}"))
        elif face_blow > 0.08:
            issues.append(_issue("WARN", index, path.name, f"hot facial highlights {face_blow:.0%}"))
        tile = _tile_repetition(image)
        tiles.append(tile)
        if tile > 0.72:
            issues.append(_issue("WARN", index, path.name, f"wall texture repeats (autocorr {tile:.2f})"))
    return _result("visual_exposure_balance", "Shadow, highlight, and tile repetition", issues, {
        "stills": len(paths),
        "mean_crushed_fraction": round(float(np.mean(crushed)) if crushed else 0.0, 3),
        "mean_highlight_fraction": round(float(np.mean(blown)) if blown else 0.0, 3),
        "max_tile_repetition": round(max(tiles) if tiles else 0.0, 3),
    })


def _climax_visual_delta(stage: dict, stills) -> dict:
    paths = _still_paths(stills)
    events = _events(stage)
    crisis = [event for event in events if event["type"] in CRISIS_EVENTS]
    recovery = [event for event in events if event["type"] in RECOVERY_EVENTS]
    if not crisis or not recovery:
        return _result("climax_visual_delta", "Crisis to recovery visual shift", [], {
            "crisis_events": len(crisis), "recovery_events": len(recovery), "compared": False,
        })
    if not paths:
        return _result("climax_visual_delta", "Crisis to recovery visual shift", [
            _issue("WARN", None, "stills", "crisis and recovery events exist but no stills were compared"),
        ], {"compared": False})
    crisis_frames = {frame for event in crisis for frame in range(event["from"], event["to"])}
    recovery_frames = {frame for event in recovery for frame in range(event["from"], event["to"])}
    crisis_images = []
    recovery_images = []
    for index, path in enumerate(paths):
        frame = _frame_from_name(path.name)
        if frame is None:
            frame = index
        try:
            image = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
        except (OSError, UnidentifiedImageError, ValueError):
            continue
        if frame in crisis_frames:
            crisis_images.append(image)
        elif frame in recovery_frames:
            recovery_images.append(image)
    if not crisis_images or not recovery_images:
        # Fall back to the first and last third of the stills when names are not frame-numbered.
        split = max(1, len(paths) // 3)
        crisis_images = [_load_rgb(path) for path in paths[:split]]
        recovery_images = [_load_rgb(path) for path in paths[-split:]]
        crisis_images = [image for image in crisis_images if image is not None]
        recovery_images = [image for image in recovery_images if image is not None]
    if not crisis_images or not recovery_images:
        return _result("climax_visual_delta", "Crisis to recovery visual shift", [
            _issue("WARN", None, "stills", "could not pair crisis and recovery stills"),
        ], {"compared": False})
    before = _mean_color(crisis_images)
    after = _mean_color(recovery_images)
    luma_delta = after["luma"] - before["luma"]
    green_delta = (after["g"] - after["r"]) - (before["g"] - before["r"])
    issues = []
    if abs(luma_delta) < 8.0 and abs(green_delta) < 6.0:
        issues.append(_issue(
            "FAIL", None, "stills",
            f"crisis and recovery look alike (luma delta {luma_delta:.1f}, green-red delta {green_delta:.1f})",
        ))
    return _result("climax_visual_delta", "Crisis to recovery visual shift", issues, {
        "compared": True,
        "crisis_luma": round(before["luma"], 2),
        "recovery_luma": round(after["luma"], 2),
        "luma_delta": round(luma_delta, 2),
        "green_red_delta": round(green_delta, 2),
    })


def _result(check_id: str, name: str, issues: list[dict], metrics: dict) -> dict:
    if any(item["severity"] == "FAIL" for item in issues):
        status = "FAIL"
    elif any(item["severity"] == "WARN" for item in issues):
        status = "WARN"
    else:
        status = "PASS"
    return {"id": check_id, "name": name, "status": status, "issues": issues, "metrics": metrics}


def _issue(severity: str, frame, subject: str, message: str) -> dict:
    return {"severity": severity, "frame": frame, "subject": subject, "message": message}


def _events(stage: dict) -> list[dict]:
    found = []
    for item in stage.get("events") or []:
        if isinstance(item, str):
            item = {"type": item}
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("event") or "").strip()
        if not kind or kind == "none":
            continue
        start = int(item.get("from") or 0)
        end = int(item.get("to") or start + 1)
        if end <= start:
            end = start + 1
        found.append({"type": kind, "from": start, "to": end, "target": str(item.get("target") or "")})
    return found


def _dramatic_entries(stage: dict) -> list[dict]:
    entries = []
    actors = stage.get("actors") or {}
    if isinstance(actors, dict):
        for actor_id, actor in actors.items():
            if not isinstance(actor, dict):
                continue
            for entry in actor.get("dramatic_actions") or []:
                if isinstance(entry, dict):
                    copied = dict(entry)
                    copied.setdefault("actor", actor_id)
                    entries.append(copied)
    entries.sort(key=lambda item: (int(item.get("from") or 0), str(item.get("actor") or "")))
    return entries


def _event_point(stage: dict, event: dict):
    target = event.get("target") or ""
    props = stage.get("props") or {}
    if isinstance(props, dict) and target in props and isinstance(props[target], dict):
        return _prop_point(props[target])
    set_props = ((stage.get("set") or {}).get("props") or []) if isinstance(stage.get("set"), dict) else []
    for prop in set_props:
        if isinstance(prop, dict) and str(prop.get("id") or "") == target:
            return _prop_point(prop)
    kind = event["type"]
    hints = {
        "array_restore": ("array", "dish", "antenna", "window"),
        "module_install": ("module", "console", "rack"),
        "table_impact": ("table", "desk"),
        "door_open": ("door",),
        "storm_approaching": ("window", "storm", "glass"),
    }[kind]
    for prop in set_props:
        if not isinstance(prop, dict):
            continue
        label = (str(prop.get("id") or "") + " " + str(prop.get("kind") or "")).lower()
        if any(hint in label for hint in hints):
            return _prop_point(prop)
    if kind == "door_open":
        door = (stage.get("set") or {}).get("door") if isinstance(stage.get("set"), dict) else None
        if isinstance(door, dict):
            return (float(door.get("x") or 0.0), 1.2, _wall_z(stage, str(door.get("wall") or "back")))
    if kind in {"storm_approaching", "array_restore"}:
        window = (stage.get("set") or {}).get("window") if isinstance(stage.get("set"), dict) else None
        if isinstance(window, dict) and window.get("enabled", True):
            return (float(window.get("x") or 0.0), 1.6, _wall_z(stage, str(window.get("wall") or "front")))
    return None


def _prop_point(prop: dict):
    pos = prop.get("pos") or prop.get("position")
    if not isinstance(pos, (list, tuple)):
        return None
    if len(pos) >= 3:
        return (float(pos[0]), float(pos[1]), float(pos[2]))
    if len(pos) == 2:
        return (float(pos[0]), 0.9, float(pos[1]))
    return None


def _wall_z(stage: dict, wall: str) -> float:
    room = ((stage.get("set") or {}).get("room") or {}) if isinstance(stage.get("set"), dict) else {}
    zmin = float((room.get("min") or [0, 0, -4])[2])
    zmax = float((room.get("max") or [0, 0, 4])[2])
    if wall in {"back", "south"}:
        return zmax - 0.2
    return zmin + 0.2


def _frames_for(stage: dict, start: int, end: int) -> list[tuple[int, dict]]:
    found = []
    for shot in stage.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        shot_start = int(shot.get("from") or 0)
        shot_end = shot_start + int(shot.get("frames") or 0)
        overlap_start = max(start, shot_start)
        overlap_end = min(end, shot_end)
        if overlap_end <= overlap_start:
            continue
        for frame in (overlap_start, (overlap_start + overlap_end) // 2, max(overlap_start, overlap_end - 1)):
            camera = _camera_at(shot, frame)
            if camera is not None:
                found.append((frame, camera))
    return found


def _camera_at(shot: dict, frame: int):
    cameras = shot.get("cameras") or []
    for camera in cameras:
        if not isinstance(camera, dict):
            continue
        start = int(camera.get("from") or shot.get("from") or 0)
        end = int(camera.get("to") or start + int(shot.get("frames") or 1))
        if start <= frame <= end and "pos" in camera and "look_at" in camera:
            span = max(1, end - start)
            u = (frame - start) / span
            pos = _lerp3(camera.get("pos"), camera.get("pos_to") or camera.get("pos"), u)
            look = _lerp3(camera.get("look_at"), camera.get("look_to") or camera.get("look_at"), u)
            fov = float(camera.get("fov") or shot.get("fov") or 45.0)
            return {"pos": pos, "look_at": look, "fov": fov}
    keyframes = ((shot.get("camera") or {}).get("keyframes") or []) if isinstance(shot.get("camera"), dict) else []
    if len(keyframes) >= 2:
        a, b = keyframes[0], keyframes[-1]
        span = max(1, int(b.get("frame") or 1) - int(a.get("frame") or 0))
        u = min(1.0, max(0.0, (frame - int(a.get("frame") or 0)) / span))
        return {
            "pos": _lerp3(a.get("pos"), b.get("pos"), u),
            "look_at": _lerp3(a.get("look_at"), b.get("look_at"), u),
            "fov": float(a.get("fov") or shot.get("fov") or 45.0),
        }
    return None


def _project(camera: dict, point, aspect: float):
    pos = camera["pos"]
    look = camera["look_at"]
    forward = _norm(_sub(look, pos))
    if _len(forward) < 1e-6:
        return None
    world_up = (0.0, 1.0, 0.0)
    right = _norm(_cross(forward, world_up))
    if _len(right) < 1e-6:
        right = (1.0, 0.0, 0.0)
    up = _cross(right, forward)
    rel = _sub(point, pos)
    depth = _dot(rel, forward)
    if depth <= 0.05:
        return (0.0, 0.0, depth)
    tan = math.tan(math.radians(float(camera.get("fov") or 45.0)) * 0.5)
    ndc_x = _dot(rel, right) / (depth * tan * aspect)
    ndc_y = _dot(rel, up) / (depth * tan)
    return (ndc_x, ndc_y, depth)


def _back_view_only(stage: dict, camera: dict, point, ndc) -> bool:
    nearest = None
    nearest_d = 1e9
    actors = stage.get("actors") or {}
    if not isinstance(actors, dict):
        return False
    for actor_id, actor in actors.items():
        if not isinstance(actor, dict):
            continue
        origin = _actor_point(actor, 1.4)
        if origin is None:
            continue
        dist = _len(_sub(origin, camera["pos"]))
        if dist < nearest_d:
            nearest_d = dist
            nearest = (actor_id, actor, origin)
    if nearest is None:
        return False
    _actor_id, actor, origin = nearest
    if not _actor_faces_away(stage, camera, _actor_id):
        return False
    actor_ndc = _project(camera, origin, 16.0 / 9.0)
    if actor_ndc is None:
        return False
    return abs(actor_ndc[0] - ndc[0]) < 0.28 and abs(actor_ndc[1] - ndc[1]) < 0.35


def _shot_signature(stage: dict, shot: dict, actor: str) -> str:
    start = int(shot.get("from") or 0)
    end = start + int(shot.get("frames") or 0)
    for entry in _dramatic_entries(stage):
        if str(entry.get("actor") or "") != actor:
            continue
        if int(entry.get("to") or 0) <= start or int(entry.get("from") or 0) >= end:
            continue
        return str(entry.get("action_id") or entry.get("base_clip") or "")
    return str(shot.get("action") or shot.get("gesture") or "")


def _is_static_entry(entry: dict, stage: dict) -> bool:
    clip = str(entry.get("base_clip") or "").lower()
    action = str(entry.get("action_id") or entry.get("action") or "").lower()
    target = str(entry.get("target") or "").lower()
    if any(token in clip or token in action for token in LOCOMOTION_TOKENS):
        return False
    if target and target not in {"", "none", "person", entry.get("actor")}:
        return False
    start = int(entry.get("from") or 0)
    end = int(entry.get("to") or start)
    for event in _events(stage):
        if event["to"] <= start or event["from"] >= end:
            continue
        if event["type"] not in {"none"}:
            return False
    talk = action.startswith("idle") or "talk" in action or action in {"", "talk", "listen"}
    return talk or clip.startswith("idle")


def _any_motion(stage: dict) -> bool:
    actors = stage.get("actors") or {}
    if not isinstance(actors, dict):
        return False
    for actor in actors.values():
        if isinstance(actor, dict) and actor.get("moves"):
            return True
    return False


def _handoff_gaps(rows: list[dict]):
    previous = {}
    for row in rows:
        props = row.get("props") or {}
        if not isinstance(props, dict):
            continue
        for prop_id, prop in props.items():
            if not isinstance(prop, dict):
                continue
            owner = str(prop.get("owner") or "")
            was = previous.get(prop_id)
            gap = prop.get("hand_gap_m")
            if was and owner and was != owner:
                if gap is None:
                    gap = _measured_gap(row, owner, prop.get("position"))
                if gap is not None:
                    yield int(row.get("frame") or 0), float(gap), prop_id
            if owner:
                previous[prop_id] = owner


def _contact_gaps(stage: dict, rows: list[dict]):
    wanted = {"table_impact", "slam_table", "module_install"}
    windows = [event for event in _events(stage) if event["type"] in wanted]
    action_windows = []
    for entry in _dramatic_entries(stage):
        action = str(entry.get("action_id") or "")
        if action in {"slam_table", "install_module", "mount_sensor"}:
            action_windows.append((int(entry["from"]), int(entry["to"]), action, str(entry.get("actor") or "")))
    for row in rows:
        frame = int(row.get("frame") or 0)
        active = [event["type"] for event in windows if event["from"] <= frame < event["to"]]
        active.extend(action for start, end, action, _actor in action_windows if start <= frame < end)
        if not active:
            continue
        actors = row.get("actors") or {}
        if not isinstance(actors, dict):
            continue
        for actor_id, actor in actors.items():
            if not isinstance(actor, dict):
                continue
            if "hand_contact_dist_m" in actor:
                raw_dist = actor.get("hand_contact_dist_m")
                if raw_dist is not None and float(raw_dist) >= 0.0:
                    yield frame, float(raw_dist), active[0] + ":" + actor_id + ":contact"
                continue
            hands = actor.get("hands") or {}
            if not isinstance(hands, dict):
                continue
            for side, point in hands.items():
                gap = _nearest_prop_gap(row, point)
                if gap is None:
                    continue
                yield frame, gap, active[0] + ":" + actor_id + ":" + str(side)


def _sfx_deltas(stage: dict):
    fps = max(1.0, float(stage.get("fps") or 24))
    sounds = stage.get("sound_events") or stage.get("audio_events") or []
    if isinstance(sounds, dict):
        sounds = sounds.get("events") or []
    pairs = []
    for sound in sounds:
        if not isinstance(sound, dict):
            continue
        kind = str(sound.get("type") or sound.get("event") or sound.get("id") or "")
        when = sound.get("time_s", sound.get("time"))
        if when is None and sound.get("frame") is not None:
            when = float(sound.get("frame")) / fps
        if when is None:
            continue
        for event in _events(stage):
            if kind and event["type"] not in kind and kind not in event["type"]:
                continue
            event_time = event["from"] / fps
            pairs.append((float(when) - event_time, event["type"], event["from"]))
            break
    return pairs


def _head_screen_fraction(stage: dict, camera: dict, actor_id: str) -> float:
    actors = stage.get("actors") or {}
    actor = actors.get(actor_id) if isinstance(actors, dict) else None
    if not isinstance(actor, dict):
        return 0.0
    head = _actor_point(actor, 1.55)
    if head is None:
        return 0.0
    dist = max(0.2, _len(_sub(head, camera["pos"])))
    fov = math.radians(float(camera.get("fov") or 45.0))
    radius = 0.12
    angular = 2.0 * math.atan(radius / dist)
    fraction = (angular / max(0.2, fov)) ** 2
    return max(0.0, min(1.0, fraction))


def _actor_faces_away(stage: dict, camera: dict, actor_id: str) -> bool:
    actors = stage.get("actors") or {}
    actor = actors.get(actor_id) if isinstance(actors, dict) else None
    if not isinstance(actor, dict):
        return False
    origin = _actor_point(actor, 1.4)
    if origin is None:
        return False
    yaw = _actor_yaw(actor)
    forward = (math.sin(yaw), 0.0, math.cos(yaw))
    to_camera = _norm(_sub(camera["pos"], origin))
    return _dot(forward, to_camera) < -0.15


def _actor_point(actor: dict, height: float):
    start = actor.get("start") or {}
    pos = start.get("pos") if isinstance(start, dict) else actor.get("pos")
    if not isinstance(pos, (list, tuple)) or len(pos) < 2:
        return None
    if len(pos) >= 3:
        return (float(pos[0]), float(pos[1]) + height * 0.0 + height, float(pos[2]))
    return (float(pos[0]), height, float(pos[1]))


def _actor_yaw(actor: dict) -> float:
    start = actor.get("start") or {}
    if isinstance(start, dict) and start.get("yaw_deg") is not None:
        return math.radians(float(start["yaw_deg"]))
    facing = actor.get("facing")
    if isinstance(facing, (list, tuple)) and len(facing) >= 2 and not isinstance(facing[0], dict):
        return math.atan2(float(facing[0]), float(facing[1]))
    return 0.0


def _actor_ndc(row: dict, actor_id: str):
    camera = row.get("camera") or {}
    subject = camera.get("subject") if isinstance(camera, dict) else None
    if isinstance(subject, dict) and str(subject.get("id") or "") == actor_id:
        if subject.get("ndc_x") is None:
            return None
        return (float(subject.get("ndc_x") or 0.0), float(subject.get("ndc_y") or 0.0))
    actors = row.get("actors") or {}
    actor = actors.get(actor_id) if isinstance(actors, dict) else None
    if isinstance(actor, dict) and actor.get("ndc_x") is not None:
        return (float(actor.get("ndc_x") or 0.0), float(actor.get("ndc_y") or 0.0))
    return None


def _runtime_event_in_frame(row: dict, event: dict) -> bool:
    entities = row.get("event_visible_entities") or []
    if not isinstance(entities, list):
        return False
    kind = str(event.get("type") or "")
    for item in entities:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or item.get("id") or "") != kind:
            continue
        if item.get("in_frame"):
            return True
    return False


def _telemetry_ndc(row: dict, event: dict):
    camera = row.get("camera") or {}
    subject = camera.get("subject") if isinstance(camera, dict) else None
    target = event.get("target") or ""
    if isinstance(subject, dict) and target and str(subject.get("id") or "") == target:
        if subject.get("ndc_x") is None:
            return None
        return (float(subject["ndc_x"]), float(subject.get("ndc_y") or 0.0), 1.0)
    return None


def _measured_gap(row: dict, owner: str, position):
    actors = row.get("actors") or {}
    actor = actors.get(owner) if isinstance(actors, dict) else None
    if not isinstance(actor, dict) or not isinstance(position, (list, tuple)):
        return None
    hands = actor.get("hands") or {}
    if not isinstance(hands, dict):
        return None
    gaps = []
    for point in hands.values():
        if isinstance(point, (list, tuple)) and len(point) >= 3:
            gaps.append(_len(_sub(tuple(point[:3]), tuple(position[:3]))))
    return min(gaps) if gaps else None


def _nearest_prop_gap(row: dict, point):
    if not isinstance(point, (list, tuple)) or len(point) < 3:
        return None
    props = row.get("props") or {}
    if not isinstance(props, dict):
        return None
    gaps = []
    for prop in props.values():
        if not isinstance(prop, dict):
            continue
        if prop.get("hand_gap_m") is not None and prop.get("position") is None:
            gaps.append(float(prop["hand_gap_m"]))
            continue
        pos = prop.get("position")
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            gaps.append(_len(_sub(tuple(point[:3]), tuple(pos[:3]))))
    return min(gaps) if gaps else None


def _actor_region(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    x0, x1 = int(width * 0.28), int(width * 0.72)
    y0, y1 = int(height * 0.18), int(height * 0.88)
    return image[y0:y1, x0:x1]


def _tile_repetition(image: np.ndarray) -> float:
    gray = 0.2126 * image[:, :, 0] + 0.7152 * image[:, :, 1] + 0.0722 * image[:, :, 2]
    height, width = gray.shape
    band = gray[: max(8, height // 5), :]
    if band.shape[1] < 16:
        return 0.0
    row = band.mean(axis=0)
    row = row - row.mean()
    spectrum = np.fft.rfft(row)
    power = np.abs(spectrum) ** 2
    if power.size < 4:
        return 0.0
    power[0] = 0.0
    peak = float(power.max())
    total = float(power.sum()) or 1.0
    return peak / total


def _mean_color(images: list[np.ndarray]) -> dict:
    stack = np.stack([image.reshape(-1, 3).mean(axis=0) for image in images], axis=0)
    mean = stack.mean(axis=0)
    luma = 0.2126 * mean[0] + 0.7152 * mean[1] + 0.0722 * mean[2]
    return {"r": float(mean[0]), "g": float(mean[1]), "b": float(mean[2]), "luma": float(luma)}


def _load_rgb(path: Path):
    try:
        return np.asarray(Image.open(path).convert("RGB"), dtype=np.float32)
    except (OSError, UnidentifiedImageError, ValueError):
        return None


def _still_paths(stills) -> list[Path]:
    if stills is None:
        return []
    directory = Path(stills)
    if directory.is_file() and directory.suffix.lower() in STILL_SUFFIXES:
        return [directory]
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in STILL_SUFFIXES)


def _frame_from_name(name: str):
    digits = "".join(ch if ch.isdigit() else " " for ch in name).split()
    if not digits:
        return None
    return int(digits[-1])


def _frame_rows(telemetry) -> list[dict]:
    if telemetry is None:
        return []
    if isinstance(telemetry, list):
        raw = telemetry
    elif isinstance(telemetry, dict):
        raw = telemetry.get("frames", [])
    else:
        raw = []
    if isinstance(raw, dict):
        rows = []
        for key, value in raw.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("frame", int(key))
                rows.append(row)
        raw = rows
    indexed = {}
    for row in raw:
        if isinstance(row, dict):
            indexed[int(row.get("frame", 0))] = row
    return [indexed[key] for key in sorted(indexed)]


def _row_at(rows: list[dict], frame: int):
    for row in rows:
        if int(row.get("frame") or 0) == frame:
            return row
    return None


def _video_note(video_path, stage: dict) -> dict:
    if not video_path:
        return {"present": False}
    path = Path(video_path)
    return {"present": path.is_file(), "path": str(path), "stage_frames": stage.get("total_frames")}


def _lerp3(a, b, u: float):
    if not isinstance(a, (list, tuple)) or len(a) < 3:
        return (0.0, 1.5, 0.0)
    if not isinstance(b, (list, tuple)) or len(b) < 3:
        b = a
    return tuple(float(a[i]) + (float(b[i]) - float(a[i])) * u for i in range(3))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _len(a) -> float:
    return math.sqrt(_dot(a, a))


def _norm(a):
    n = _len(a) or 1.0
    return (a[0] / n, a[1] / n, a[2] / n)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Artistic QC, separate from Engineering QC.")
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--telemetry", type=Path, default=None)
    parser.add_argument("--video", type=Path, default=None)
    parser.add_argument("--stills", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=Path("artistic_qc_report.json"))
    args = parser.parse_args(argv)
    stage = json.loads(args.stage.read_text(encoding="utf-8"))
    telemetry = None
    if args.telemetry is not None and args.telemetry.is_file():
        telemetry = json.loads(args.telemetry.read_text(encoding="utf-8"))
    report = run_artistic_qc(stage, telemetry, args.video, args.stills)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}  {args.out}")
    for check in report["checks"]:
        print(f"  {check['status']:4}  {check['id']}")
    print(report["note"])
    return {"PASS": 0, "WARN": 2, "FAIL": 1}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
