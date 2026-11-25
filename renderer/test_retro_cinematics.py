"""Unit tests for the PS2 cinematic Python layer.

Covers the real UAL clip index, semantic actions, performance rules,
dramatic camera grammar, art profiles, procedural foley, and the generic
stage compiler. Existing stage tests stay on the sample-story path.
"""
from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from renderer.dramatic_director import SHOT_INTENTS, DramaticDirector
from renderer.performance_planner import REQUIRED_BEAT_FIELDS, PerformancePlanner, is_static_talk
from renderer.prepare_stage3d import prepare_generic
from renderer.retro_art_profile import PROFILE_IDS, RetroArtProfile
from renderer.semantic_animation_library import PHASE_NAMES, REQUIRED_ACTION_IDS, SemanticAnimationLibrary
from renderer.sound_events import FOLEY_KINDS, MUSIC_DUCK_VOLUME, MUSIC_STATES, SoundEvent

ROOT_MOTION_CLIPS = {
    "Crouch_Fwd_Loop",
    "Death01",
    "Jog_Fwd_Loop",
    "Push_Loop",
    "Roll",
    "Sprint_Loop",
    "Swim_Fwd_Loop",
    "Sword_Attack",
    "Walk_Formal_Loop",
    "Walk_Loop",
}


def _room() -> dict:
    return {"min": [-6.0, 0.0, -4.5], "max": [6.0, 3.4, 4.5]}


def _actors() -> dict:
    return {
        "actor_positions": {"alice": [-1.2, 0.0, -0.5], "bob": [1.0, 0.0, -0.5]},
        "actor_facings": {"alice": [1.0, 0.0], "bob": [-1.0, 0.0]},
        "actor_tiers": {"alice": "TIER_A", "bob": "TIER_A"},
        "actor_heights": {"alice": 1.68, "bob": 1.78},
        "room_bounds": _room(),
        "doors": [{"id": "main_door", "wall": "back", "pos": 0.0, "width": 1.6, "height": 2.4}],
        "obstacles": [],
    }


def _wav_peak(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        raw = handle.readframes(handle.getnframes())
        channels = handle.getnchannels()
        rate = handle.getframerate()
        frames = handle.getnframes()
    if frames <= 0 or rate != 24000 or channels != 1:
        return 0
    peak = 0
    for index in range(0, len(raw), 2):
        value = int.from_bytes(raw[index:index + 2], "little", signed=True)
        peak = max(peak, abs(value))
    return peak


class SemanticAnimationLibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lib = SemanticAnimationLibrary()

    def test_loads_real_clips_and_at_least_100_actions(self) -> None:
        summary = self.lib.catalog_summary()
        self.assertGreaterEqual(summary["clip_count"], 43)
        self.assertEqual(summary["clip_count"], 43)
        self.assertGreaterEqual(summary["action_count"], 100)
        self.assertTrue(summary["required_actions_present"])
        self.assertEqual(set(summary["root_motion_clips"]), ROOT_MOTION_CLIPS)
        self.assertAlmostEqual(self.lib.clips["Walk_Loop"].duration_s, 1.3333, places=3)
        self.assertAlmostEqual(self.lib.clips["Fixing_Kneeling"].duration_s, 5.2, places=2)
        self.assertFalse(self.lib.clips["Idle_Loop"].root_motion)
        self.assertTrue(self.lib.clips["Sprint_Loop"].root_motion)

    def test_every_action_uses_a_real_clip_and_four_phases(self) -> None:
        for action_id in REQUIRED_ACTION_IDS:
            self.assertIn(action_id, self.lib.by_id)
        for entry in self.lib.entries:
            self.assertIn(entry.base_clip, self.lib.clips)
            self.assertEqual(set(entry.phases), set(PHASE_NAMES))
            self.assertGreater(entry.duration_s, 0.0)
            self.assertEqual(entry.root_motion, self.lib.clips[entry.base_clip].root_motion)
            cursor = 0.0
            for name in PHASE_NAMES:
                phase = entry.phases[name]
                self.assertGreaterEqual(phase["t0"], cursor - 1e-6)
                self.assertGreater(phase["t1"], phase["t0"])
                self.assertIn("weight", phase)
                self.assertIn("torso", phase)
                self.assertIn("arm", phase)
                self.assertIn("head", phase)
                self.assertIn("joint_limits", phase)
                self.assertIn("pitch", phase["torso"])
                self.assertIn("raise", phase["arm"])
                self.assertIn("yaw", phase["head"])
                cursor = phase["t1"]
            self.assertAlmostEqual(entry.phases["recover"]["t1"], 1.0)
            self.assertGreaterEqual(entry.phases["execute"]["weight"], entry.phases["prepare"]["weight"])
            self.assertGreaterEqual(entry.phases["execute"]["weight"], entry.phases["recover"]["weight"])

    def test_slam_envelope_is_anticipation_then_impact_not_sway(self) -> None:
        spec = self.lib.export_godot_spec("slam_table", intensity=0.9, target="table")
        prepare = spec["phases"]["prepare"]["torso"]["pitch"]
        execute = spec["phases"]["execute"]["torso"]["pitch"]
        follow = spec["phases"]["follow_through"]["torso"]["pitch"]
        self.assertLess(prepare * execute, 0.0)
        self.assertGreater(abs(execute), abs(prepare))
        self.assertGreater(follow * execute, 0.0)
        self.assertLess(abs(follow), abs(execute))
        self.assertEqual(spec["base_clip"], "Punch_Cross")
        self.assertEqual(spec["target"], "table")

    def test_queries_are_deterministic_and_specific(self) -> None:
        confront = self.lib.query(intent="confront", emotion="angry", intensity=0.7)
        again = self.lib.query(intent="confront", emotion="angry", intensity=0.7)
        self.assertEqual([item.action_id for item in confront], [item.action_id for item in again])
        self.assertEqual(confront[0].action_id, "confront_step")
        self.assertEqual(self.lib.query(intent="install", target="module")[0].action_id, "install_module")
        self.assertEqual(self.lib.query(intent="refuse", emotion="angry")[0].action_id, "refuse_wave")
        self.assertEqual(self.lib.query(intent="fear_brace")[0].action_id, "fear_brace")
        console = self.lib.query(intent="operate", personality="precise", scene_type="command_deck", target="console")
        self.assertEqual(console[0].action_id, "operate_console")


class PerformancePlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lib = SemanticAnimationLibrary()
        cls.planner = PerformancePlanner(cls.lib)

    def _talking_heads(self) -> list[dict]:
        return [
            {
                "actor": "linlan", "partner": "chen", "duration": 3.0,
                "action": "talk", "blocking": "face_to_face", "emotion": "neutral", "intensity": 0.4,
                "objective": "Report the lost signal", "conflict": "Chen wants her to wait",
            },
            {
                "actor": "chen", "partner": "linlan", "duration": 3.0,
                "action": "talk", "blocking": "standing", "emotion": "tense", "intensity": 0.4,
            },
            {
                "actor": "linlan", "partner": "chen", "duration": 3.0,
                "action": "talk", "blocking": "face_to_face", "emotion": "angry", "intensity": 0.9,
            },
            {
                "actor": "chen", "partner": "linlan", "duration": 2.5,
                "action": "slam_table", "blocking": "at the table", "emotion": "angry", "intensity": 0.2,
                "target": "table",
            },
        ]

    def test_eleven_fields_tension_curve_and_static_remediation(self) -> None:
        raw = self._talking_heads()
        report = self.planner.validate_scene_beats(raw, scene_type="command_deck", fps=24)
        self.assertFalse(report["ok"])
        self.assertTrue(any("static_talking_heads" in issue for issue in report["issues"]))
        self.assertTrue(any("tension_curve" in issue for issue in report["issues"]))
        beats = report["beats"]
        self.assertEqual(len(beats), 4)
        self.assertEqual([beat.tension_phase for beat in beats], ["setup", "escalate", "climax", "release"])
        for earlier, later in zip(beats, beats[1:]):
            self.assertFalse(is_static_talk(earlier) and is_static_talk(later))
        for beat in beats:
            payload = beat.to_dict()
            for field in REQUIRED_BEAT_FIELDS:
                self.assertIn(field, payload)
            self.assertIsInstance(payload["intensity"], float)
            self.assertIsInstance(payload["environment_event"], dict)
            self.assertIn(payload["environment_event"]["type"], {
                "signal_lost", "sensor_error", "red_alert", "storm_approaching", "device_operating",
                "robot_speaking", "door_open", "door_close", "power_flicker", "array_restore",
                "table_impact", "module_install", "none",
            })
            for field in REQUIRED_BEAT_FIELDS:
                if field in ("intensity", "environment_event"):
                    continue
                self.assertTrue(str(payload[field]).strip(), field)
            self.assertIn(beat.action, self.lib.by_id)
            self.assertIn(beat.reaction, self.lib.by_id)
            self.assertIn(beat.camera_intent, SHOT_INTENTS)
        climax = max(beat.intensity for beat in beats if beat.tension_phase == "climax")
        setup = max(beat.intensity for beat in beats if beat.tension_phase == "setup")
        release = max(beat.intensity for beat in beats if beat.tension_phase == "release")
        self.assertGreater(climax, setup)
        self.assertLess(release, climax)
        self.assertEqual(beats[-1].action, "slam_table")
        again = self.planner.validate_scene_beats(beats, scene_type="command_deck", fps=24)
        self.assertTrue(again["ok"], again["issues"])

    def test_dramatic_actions_use_verified_phases(self) -> None:
        beats = self.planner.plan({"scene_type": "observation_bay", "fps": 24, "beats": self._talking_heads()})
        timeline = self.planner.compile_dramatic_actions(beats, 24)
        self.assertIn("linlan", timeline)
        self.assertIn("chen", timeline)
        for entries in timeline.values():
            self.assertTrue(entries)
            for entry in entries:
                self.assertIn(entry["base_clip"], self.lib.clips)
                self.assertEqual(set(entry["phases"]), set(PHASE_NAMES))
                self.assertGreater(entry["to"], entry["from"])
                self.assertIsInstance(entry["intensity"], float)
                self.assertTrue(entry["action_id"])
                self.assertIn("target", entry)
        events = self.planner.environment_events(beats)
        self.assertTrue(any(event["type"] == "table_impact" for event in events))
        self.assertTrue(any(event["type"] in ("device_operating", "module_install", "storm_approaching") for event in events))


class DramaticDirectorTests(unittest.TestCase):
    def test_all_shot_intents_clear_walls_and_actors(self) -> None:
        director = DramaticDirector(fps=24)
        for intent in SHOT_INTENTS:
            with self.subTest(intent=intent):
                director.reset_state()
                shot = director.plan_beat({
                    **_actors(),
                    "beat_id": f"intent_{intent}",
                    "duration": 2.5,
                    "frames": 60,
                    "speaker": "alice",
                    "listener": "bob",
                    "emotion": "tense",
                    "action": "talk",
                    "camera_intent": intent,
                })
                self.assertEqual(shot["shot_intent"], intent)
                self.assertIn(shot["cut_reason"], {
                    "scene_open", "impact", "action", "reaction", "environment_event", "continuity", "pacing",
                })
                self.assertNotEqual(shot["cut_reason"], "speaker_change")
                self.assertIn("push_curve", shot)
                self.assertGreaterEqual(shot["director_meta"]["wall_clearance_m"], 0.35)
                self.assertFalse(shot["director_meta"]["dynamic_occluded"])
                self.assertFalse(shot["director_meta"]["occluded"])
                self.assertFalse(shot["director_meta"]["crosses_180_axis"])
                self.assertGreaterEqual(shot["director_meta"]["doorframe_clearance_m"], 0.35)

    def test_low_and_high_angles_read_on_purpose(self) -> None:
        director = DramaticDirector(fps=24)
        low = director.plan_beat({
            **_actors(), "beat_id": "low", "duration": 2.5, "frames": 60,
            "speaker": "alice", "listener": "bob", "action": "confront_step", "camera_intent": "low_angle",
        })
        high = director.plan_beat({
            **_actors(), "beat_id": "high", "duration": 2.5, "frames": 60,
            "speaker": "alice", "listener": "bob", "action": "fear_brace", "camera_intent": "high_angle",
        })
        low_cam = low["cameras"][0]
        high_cam = high["cameras"][0]
        self.assertLess(low_cam["pos"][1], low_cam["look_at"][1] - 0.35)
        self.assertGreater(high_cam["pos"][1], high_cam["look_at"][1] + 0.35)
        for shot in (low, high):
            self.assertGreaterEqual(shot["director_meta"]["wall_clearance_m"], 0.35)
            self.assertFalse(shot["director_meta"]["dynamic_occluded"])
            self.assertFalse(shot["director_meta"]["crosses_180_axis"])

    def test_speaker_change_cuts_on_reaction(self) -> None:
        director = DramaticDirector(fps=24)
        base = _actors()
        shots = director.plan_sequence([
            {**base, "beat_id": "line_a", "duration": 3.0, "frames": 72, "speaker": "alice", "listener": "bob", "action": "talk", "emotion": "angry"},
            {**base, "beat_id": "line_b", "duration": 3.0, "frames": 72, "speaker": "bob", "listener": "alice", "action": "talk", "emotion": "tense"},
        ])
        self.assertGreaterEqual(len(shots), 2)
        self.assertEqual(sum(shot["frames"] for shot in shots), 144)
        self.assertTrue(all(shot["cut_reason"] != "speaker_change" for shot in shots))
        reaction = next(shot for shot in shots[1:] if shot["cut_reason"] == "reaction")
        self.assertEqual(reaction["shot_intent"], "reaction")
        self.assertEqual(reaction["primary_subject"], "alice")
        self.assertFalse(any(shot["director_meta"]["crosses_180_axis"] for shot in shots))

    def test_impact_environment_and_pacing(self) -> None:
        director = DramaticDirector(fps=24)
        base = _actors()
        slam = director.plan_beat({
            **base, "beat_id": "slam", "duration": 2.0, "frames": 48,
            "speaker": "alice", "listener": "bob", "action": "slam_table", "emotion": "angry",
            "camera_intent": "climax_push", "intensity": 0.9,
            "environment_event": {"type": "table_impact", "intensity": 0.9, "target": "table"},
        })
        self.assertEqual(slam["camera_punch"]["kind"], "impact")
        self.assertGreater(slam["camera_punch"]["strength"], 0.0)
        self.assertGreater(slam["camera_punch"]["duration_frames"], 0)
        self.assertEqual(slam["cut_reason"], "impact")
        self.assertEqual(slam["push_curve"]["kind"], "ease_in_impact")

        storm = director.plan_sequence([
            {**base, "beat_id": "setup", "duration": 3.0, "frames": 72, "speaker": "alice", "listener": "bob", "action": "talk"},
            {
                **base, "beat_id": "storm", "duration": 2.5, "frames": 60,
                "speaker": "alice", "listener": "bob", "action": "check_storm_window",
                "environment_event": {"type": "storm_approaching", "target": "window", "intensity": 0.8},
            },
        ])
        self.assertEqual(storm[-1]["shot_intent"], "environment_insert")
        self.assertEqual(storm[-1]["cut_reason"], "environment_event")

        door = director.plan_beat({
            **base, "beat_id": "door", "duration": 2.0, "frames": 48,
            "speaker": "alice", "listener": "bob", "action": "trigger_door",
            "environment_event": {"type": "door_close", "target": "door", "intensity": 0.7},
        })
        self.assertEqual(door["camera_punch"]["kind"], "door")

        long_talk = director.plan_sequence([{
            **base, "beat_id": "long", "duration": 9.0, "frames": 216,
            "speaker": "alice", "listener": "bob", "action": "talk", "emotion": "neutral",
        }])
        self.assertGreater(len(long_talk), 1)
        self.assertEqual(sum(shot["frames"] for shot in long_talk), 216)
        for shot in long_talk:
            self.assertGreaterEqual(shot["frames"] / 24.0, 1.99)
            self.assertLessEqual(shot["frames"] / 24.0, 5.01)

        long_action = director.plan_sequence([{
            **base, "beat_id": "rush", "duration": 6.0, "frames": 144,
            "speaker": "alice", "listener": "bob", "action": "rush_enter", "camera_intent": "action_track",
        }])
        self.assertGreater(len(long_action), 1)
        self.assertEqual(sum(shot["frames"] for shot in long_action), 144)
        for shot in long_action:
            self.assertGreaterEqual(shot["frames"] / 24.0, 0.99)
            self.assertLessEqual(shot["frames"] / 24.0, 3.01)

        held = director.plan_sequence([{
            **base, "beat_id": "hold", "duration": 6.0, "frames": 144,
            "speaker": "alice", "listener": "bob", "action": "talk",
            "camera_intent": "climax_push", "hold": True, "narrative_importance": 0.95,
        }])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0]["frames"], 144)


class RetroArtProfileTests(unittest.TestCase):
    def test_four_profiles_keep_pixel_filters_off(self) -> None:
        profile = RetroArtProfile()
        self.assertEqual(profile.ids(), list(PROFILE_IDS))
        for name in PROFILE_IDS:
            spec = profile.export(name)
            self.assertEqual(spec["id"], name)
            for section in ("palette", "lighting", "character_material", "environment_dressing", "post_fx"):
                self.assertIn(section, spec)
            fx = spec["post_fx"]
            self.assertFalse(fx["pixelation"])
            self.assertFalse(fx["crt_scanlines"])
            self.assertFalse(fx["texture_jitter"])
            self.assertIn("vignette", fx)
            self.assertIn("contrast", fx)
            self.assertIn("saturation", fx)
            self.assertTrue(str(fx["warm_tint"]).startswith("#"))
        industrial = profile.export("retro_industrial_scifi")
        self.assertEqual(industrial["post_fx"]["vignette"], 0.25)
        self.assertEqual(industrial["post_fx"]["contrast"], 1.14)
        self.assertEqual(industrial["post_fx"]["saturation"], 0.92)
        self.assertEqual(industrial["post_fx"]["warm_tint"], "#ffd8a8")
        self.assertIn("hazard_stripes", industrial["environment_dressing"])
        opted = profile.resolve({"id": "ps2_urban", "post_fx": {"pixelation": True}})
        self.assertTrue(opted["post_fx"]["pixelation"])
        self.assertFalse(opted["post_fx"]["crt_scanlines"])
        self.assertFalse(opted["post_fx"]["texture_jitter"])
        named = profile.resolve("retro_horror")
        self.assertEqual(named["id"], "retro_horror")
        self.assertFalse(named["post_fx"]["pixelation"])


class SoundEventTests(unittest.TestCase):
    def test_foley_varies_and_music_ducks_on_the_frame_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            synth = SoundEvent(Path(tmp))
            first = synth.synthesize("footstep_heavy", seed=1, frame=10)
            second = synth.synthesize("footstep_heavy", seed=2, frame=22)
            repeat = synth.synthesize("footstep_heavy", seed=1, frame=10)
            self.assertGreater(_wav_peak(first), 100)
            self.assertNotEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), repeat.read_bytes())
            for kind in FOLEY_KINDS:
                path = synth.synthesize(kind, seed=7, frame=3)
                self.assertGreater(path.stat().st_size, 44)
                self.assertGreater(_wav_peak(path), 0)
            for state in MUSIC_STATES:
                bed = synth.music_bed(state, 0.45, seed=4, frame=0)
                self.assertGreater(_wav_peak(bed), 0)
            tension = synth.music_bed("tension", 0.4, seed=4, frame=1)
            action = synth.music_bed("action", 0.4, seed=4, frame=1)
            self.assertNotEqual(tension.read_bytes(), action.read_bytes())
            step_a = synth.timeline_entry("footstep_heavy", frame=10, fps=24, volume=0.7, seed=5)
            step_b = synth.timeline_entry("footstep_heavy", frame=22, fps=24, volume=0.7, seed=5)
            beep = synth.timeline_entry("console_beep", frame=48, fps=24, volume=0.6, seed=9)
            self.assertNotEqual(Path(step_a["file"]).read_bytes(), Path(step_b["file"]).read_bytes())
            self.assertEqual(beep["start_ms"], 2000)
            self.assertEqual(step_a["start_ms"], int(round(10 * 1000 / 24)))
            self.assertFalse(beep["loop"])
            self.assertIn("file", beep)
            bed_entry = synth.timeline_entry("tension", frame=0, fps=24, loop=True, seed=3, duration_s=0.5)
            self.assertTrue(bed_entry["loop"])
            self.assertLessEqual(bed_entry["volume"], MUSIC_DUCK_VOLUME + 1e-6)
            self.assertEqual(bed_entry["start_ms"], 0)


class PrepareGenericRetroTests(unittest.TestCase):
    def test_generic_manifest_emits_the_shared_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            output = root / "stage3d.json"
            manifest.write_text("{}", encoding="utf-8")
            doc = {
                "version": 1,
                "mode": "generic",
                "renderer": "godot3d",
                "width": 1280,
                "height": 720,
                "fps": 24,
                "scene_type": "observation_bay",
                "lighting_profile": "day_bay",
                "title": "retro wiring",
                "art_profile": "retro_industrial_scifi",
                "dramatic_director": True,
                "events": [{"type": "storm_approaching", "from": 0, "to": 48, "intensity": 0.55, "target": "window"}],
                "sound_events": [{"kind": "wind_gust", "frame": 12, "volume": 0.4}],
                "cast": {
                    "linlan": {"name": "林岚", "height_m": 1.65, "start_anchor": "center_a", "yaw_deg": 90},
                    "chen": {"name": "陈工", "height_m": 1.76, "start_anchor": "center_b", "yaw_deg": -90},
                },
                "beats": [
                    {
                        "id": "b1", "duration": 3.0, "speaker": "linlan", "listener": "chen",
                        "emotion": "angry", "action": "talk", "text": "信号断了。", "blocking": "face_to_face",
                    },
                    {
                        "id": "b2", "duration": 3.0, "speaker": "chen", "listener": "linlan",
                        "emotion": "tense", "action": "talk", "text": "我在查。", "blocking": "face_to_face",
                    },
                    {
                        "id": "b3", "duration": 2.5, "speaker": "linlan", "listener": "chen",
                        "emotion": "angry", "action": "slam_table", "text": "听好。",
                        "target": "table", "blocking": "at the table",
                    },
                ],
            }
            stage = prepare_generic(doc, manifest, output, check_files=False)
            art = stage["art_profile"]
            self.assertEqual(art["id"], "retro_industrial_scifi")
            self.assertFalse(art["post_fx"]["pixelation"])
            self.assertFalse(art["post_fx"]["crt_scanlines"])
            self.assertFalse(art["post_fx"]["texture_jitter"])
            self.assertEqual(art["post_fx"]["warm_tint"], "#ffd8a8")
            event_types = {event["type"] for event in stage["events"]}
            self.assertIn("storm_approaching", event_types)
            self.assertTrue(event_types & {"table_impact", "device_operating", "module_install"})
            for event in stage["events"]:
                for key in ("type", "from", "to", "intensity", "target"):
                    self.assertIn(key, event)
            found_action = False
            for actor in stage["actors"].values():
                for entry in actor.get("dramatic_actions") or []:
                    found_action = True
                    self.assertIn(entry["base_clip"], SemanticAnimationLibrary().clips)
                    self.assertEqual(set(entry["phases"]), set(PHASE_NAMES))
                    self.assertGreater(entry["to"], entry["from"])
            self.assertTrue(found_action)
            self.assertTrue(stage["shots"])
            self.assertTrue(any(shot.get("shot_intent") for shot in stage["shots"]))
            self.assertTrue(any(
                (shot.get("camera_punch") or {}).get("kind") == "impact"
                for shot in stage["shots"]
            ))
            cursor = 0
            for shot in stage["shots"]:
                self.assertEqual(shot["from"], cursor)
                cursor += shot["frames"]
                self.assertNotEqual(shot.get("cut_reason"), "speaker_change")
            self.assertEqual(cursor, stage["total_frames"])
            kinds = [item.get("kind") for item in stage["audio"] if item.get("kind")]
            self.assertIn("wind_gust", kinds)
            self.assertTrue(any(str(item.get("kind", "")).startswith("music_") or item.get("kind") in MUSIC_STATES for item in stage["audio"]))
            for item in stage["audio"]:
                self.assertTrue(Path(item["file"]).is_file())
                self.assertIn("start_ms", item)
                self.assertIn("volume", item)


if __name__ == "__main__":
    unittest.main()
