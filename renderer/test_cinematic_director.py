"""Unit tests for renderer/cinematic_director.py (CinematicDirector)."""
from __future__ import annotations

import unittest

from renderer.cinematic_director import (
    DOORFRAME_CLEARANCE_MIN_M,
    SHOT_TYPES,
    WALL_CLEARANCE_MIN_M,
    AABB3,
    BeatSpec,
    CinematicDirector,
    segment_intersects_aabb,
)
from renderer.scene_layout import SUPPORTED_SCENE_TYPES, SceneLayoutSystem


def _standard_room() -> dict:
    return {"min": [-6.0, 0.0, -4.5], "max": [6.0, 3.4, 4.5]}


def _standard_actors() -> dict:
    return {
        "actor_positions": {
            "alice": [-1.2, 0.0, -0.5],
            "bob": [1.0, 0.0, -0.5],
        },
        "actor_facings": {
            "alice": [1.0, 0.0],
            "bob": [-1.0, 0.0],
        },
        "actor_tiers": {
            "alice": "TIER_A",
            "bob": "TIER_A",
        },
        "room_bounds": _standard_room(),
        "doors": [{"id": "main_door", "wall": "back", "pos": 0.0, "width": 1.6, "height": 2.4}],
        "obstacles": [],
    }


class CinematicDirectorTests(unittest.TestCase):
    def test_all_ten_shot_types_supported_and_emit_stage3d_schema(self) -> None:
        self.assertEqual(len(SHOT_TYPES), 10)
        director = CinematicDirector(fps=24)
        base = _standard_actors()

        for shot_type in SHOT_TYPES:
            with self.subTest(shot_type=shot_type):
                director.reset_state()
                beat = {
                    **base,
                    "beat_id": f"b_{shot_type}",
                    "duration": 3.0,
                    "speaker": "alice",
                    "listener": "bob",
                    "action": "walk" if shot_type == "tracking" else "talk",
                    "actor_end_positions": {"alice": [-0.2, 0.0, 1.4]} if shot_type == "tracking" else {},
                    "prop_event": {"prop_id": "sensor", "event": "inspect", "position": [-0.1, 1.05, -0.5]} if shot_type == "insert" else None,
                    "preferred_shot": shot_type,
                }
                shot = director.plan_beat(beat, start_frame=0)
                self.assertEqual(shot["shot_type"], shot_type)
                self.assertEqual(shot["type"], shot_type)
                self.assertIn("fov", shot)
                self.assertIn("dof_focus_distance", shot)
                self.assertIn("primary_subject", shot)
                self.assertIn("cameras", shot)
                self.assertIn("camera", shot)
                self.assertEqual(len(shot["cameras"]), 1)
                self.assertEqual(len(shot["camera"]["keyframes"]), 2)
                self.assertGreater(shot["fov"], 20.0)
                self.assertGreater(shot["dof_focus_distance"], 0.3)
                self.assertGreaterEqual(shot["director_meta"]["wall_clearance_m"], WALL_CLEARANCE_MIN_M)
                self.assertFalse(shot["director_meta"]["occluded"])

                if shot_type == "reaction":
                    self.assertEqual(shot["primary_subject"], "bob")
                elif shot_type == "insert":
                    self.assertEqual(shot["primary_subject"], "sensor")

    def test_180_degree_line_of_action_enforced_across_dialogue(self) -> None:
        director = CinematicDirector(fps=24)
        base = _standard_actors()
        dialogue_beats = [
            {**base, "beat_id": "d1", "speaker": "alice", "listener": "bob", "preferred_shot": "two_shot"},
            {**base, "beat_id": "d2", "speaker": "alice", "listener": "bob", "preferred_shot": "over_the_shoulder"},
            {**base, "beat_id": "d3", "speaker": "bob", "listener": "alice", "preferred_shot": "over_the_shoulder"},
            {**base, "beat_id": "d4", "speaker": "bob", "listener": "alice", "preferred_shot": "close_up"},
            {**base, "beat_id": "d5", "speaker": "bob", "listener": "alice", "preferred_shot": "reaction"},
            {**base, "beat_id": "d6", "speaker": "alice", "listener": "bob", "preferred_shot": "medium"},
        ]
        shots = director.plan_sequence(dialogue_beats, enable_cutaways=False)
        sides = [s["axis_side"] for s in shots]
        self.assertIn(sides[0], (-1, 1))
        for shot in shots:
            self.assertEqual(shot["axis_side"], sides[0], f"Shot {shot['id']} crossed 180-degree line!")
            self.assertFalse(shot["director_meta"]["crosses_180_axis"])

    def test_wall_and_doorframe_clearance_rejection(self) -> None:
        director = CinematicDirector(fps=24)
        # Put speaker right next to the back wall & doorframe jamb at z = +4.05, x = +0.75.
        beat = {
            "beat_id": "near_door",
            "duration": 3.0,
            "speaker": "alice",
            "listener": "bob",
            "preferred_shot": "close_up",
            "actor_positions": {
                "alice": [0.75, 0.0, 3.85],
                "bob": [-0.70, 0.0, 3.10],
            },
            "actor_facings": {
                "alice": [0.0, 1.0],  # facing straight toward the back wall/doorframe!
                "bob": [0.7, 0.7],
            },
            "room_bounds": {"min": [-5.0, 0.0, -4.0], "max": [5.0, 3.2, 4.2]},
            "doors": [{"id": "door_back", "wall": "back", "pos": 0.0, "width": 1.6, "height": 2.4}],
            "obstacles": [],
        }
        shot = director.plan_beat(beat)
        meta = shot["director_meta"]
        self.assertGreaterEqual(meta["wall_clearance_m"], WALL_CLEARANCE_MIN_M)
        self.assertGreaterEqual(meta["doorframe_clearance_m"], DOORFRAME_CLEARANCE_MIN_M)
        self.assertFalse(meta["frustum_edge_blocked"])
        cam = shot["cameras"][0]
        for key in ("pos", "pos_to"):
            x, y, z = cam[key]
            self.assertTrue(-5.0 + WALL_CLEARANCE_MIN_M <= x <= 5.0 - WALL_CLEARANCE_MIN_M)
            self.assertTrue(-4.0 + WALL_CLEARANCE_MIN_M <= z <= 4.2 - WALL_CLEARANCE_MIN_M)

    def test_obstacle_occlusion_rejected_via_ray_aabb(self) -> None:
        director = CinematicDirector(fps=24)
        # Place a wide opaque blocker directly in front of Alice (she faces +Z at z=0).
        blocker = {"id": "front_wall_pillar", "min": [-1.2, 0.0, 0.55], "max": [1.2, 2.8, 0.95], "kind": "pillar"}
        beat = {
            "beat_id": "occluded_front",
            "duration": 3.0,
            "speaker": "alice",
            "preferred_shot": "medium",
            "actor_positions": {"alice": [0.0, 0.0, 0.0]},
            "actor_facings": {"alice": [0.0, 1.0]},
            "room_bounds": _standard_room(),
            "obstacles": [blocker],
        }
        shot = director.plan_beat(beat)
        self.assertFalse(shot["director_meta"]["occluded"])
        cam = shot["cameras"][0]
        box = AABB3("front_wall_pillar", (-1.2, 0.0, 0.55), (1.2, 2.8, 0.95))
        self.assertFalse(
            segment_intersects_aabb(tuple(cam["pos"]), tuple(cam["look_at"]), box, end_trim_m=0.35),
            "Chosen camera line-of-sight still intersects blocker AABB!",
        )

    def test_tier_b_actor_refuses_extreme_close_up(self) -> None:
        director = CinematicDirector(fps=24)
        base = _standard_actors()
        beat_tier_b = {
            **base,
            "beat_id": "tier_b_ecu",
            "speaker": "alice",
            "listener": "bob",
            "emotion": "fear",
            "narrative_importance": 0.98,
            "preferred_shot": "extreme_close_up",
            "actor_tiers": {"alice": "TIER_B", "bob": "TIER_A"},
        }
        shot_b = director.plan_beat(beat_tier_b)
        self.assertNotEqual(shot_b["shot_type"], "extreme_close_up")
        self.assertEqual(shot_b["director_meta"]["downgrade_reason"], "tier_b_no_blendshapes")

        # Also check dict-form capability specification with blendshapes=False.
        beat_no_bs = {
            **base,
            "beat_id": "no_bs_ecu",
            "speaker": "alice",
            "preferred_shot": "extreme_close_up",
            "actor_tiers": {"alice": {"tier": "TIER_A", "blendshapes": False}},
        }
        shot_no_bs = director.plan_beat(beat_no_bs)
        self.assertNotEqual(shot_no_bs["shot_type"], "extreme_close_up")
        self.assertEqual(shot_no_bs["director_meta"]["downgrade_reason"], "tier_b_no_blendshapes")

        # TIER_A actor with BlendShapes IS allowed extreme_close_up.
        beat_tier_a = {
            **base,
            "beat_id": "tier_a_ecu",
            "speaker": "alice",
            "preferred_shot": "extreme_close_up",
            "actor_tiers": {"alice": "TIER_A"},
        }
        shot_a = director.plan_beat(beat_tier_a)
        self.assertEqual(shot_a["shot_type"], "extreme_close_up")
        self.assertEqual(shot_a["director_meta"]["downgrade_reason"], "")

    def test_automatic_reaction_and_insert_shot_planning(self) -> None:
        director = CinematicDirector(fps=24)
        base = _standard_actors()
        beats = [
            {**base, "beat_id": "b1", "duration": 3.0, "speaker": "alice", "listener": "bob", "action": "talk", "narrative_importance": 0.4},
            {**base, "beat_id": "b2", "duration": 4.2, "speaker": "alice", "listener": "bob", "action": "talk", "emotion": "angry", "narrative_importance": 0.85},
            {**base, "beat_id": "b3", "duration": 4.0, "speaker": "bob", "listener": "alice", "action": "handoff",
             "prop_event": {"prop_id": "sensor", "event": "handoff", "position": [-0.1, 1.06, -0.5]}, "narrative_importance": 0.8},
            {**base, "beat_id": "b4", "duration": 2.8, "speaker": "", "listener": "alice", "action": "inspect",
             "prop_event": {"prop_id": "sensor", "event": "inspect", "position": [-0.6, 1.10, -0.5]}, "narrative_importance": 0.75},
        ]
        shots = director.plan_sequence(beats, enable_cutaways=True)
        shot_types = [s["shot_type"] for s in shots]
        self.assertEqual(shot_types[0], "establishing")
        self.assertIn("reaction", shot_types, f"Expected reaction cutaway in {shot_types}")
        self.assertIn("insert", shot_types, f"Expected prop insert shot in {shot_types}")

        # Verify contiguous frame timeline.
        cursor = 0
        for s in shots:
            self.assertEqual(s["from"], cursor)
            cursor += s["frames"]

    def test_avoids_monotonous_tracking_and_gratuitous_pov(self) -> None:
        director = CinematicDirector(fps=24)
        base = _standard_actors()
        # Four consecutive walking beats should NOT all become tracking!
        walk_beats = [
            {**base, "beat_id": f"w{i}", "duration": 2.5, "speaker": "alice", "action": "walk",
             "actor_positions": {"alice": [-3.0 + i * 1.2, 0.0, 1.0]},
             "actor_end_positions": {"alice": [-1.8 + i * 1.2, 0.0, 1.0]}}
            for i in range(4)
        ]
        shots = director.plan_sequence(walk_beats, enable_cutaways=False)
        types = [s["shot_type"] for s in shots]
        for a, b in zip(types, types[1:]):
            self.assertFalse(a == "tracking" and b == "tracking", f"Monotonous consecutive tracking in {types}")

        # Gratuitous POV without pov_required=True must be rejected.
        gratuitous_pov = {**base, "beat_id": "no_pov", "speaker": "alice", "listener": "bob", "preferred_shot": "pov", "pov_required": False}
        shot_no_pov = director.plan_beat(gratuitous_pov)
        self.assertNotEqual(shot_no_pov["shot_type"], "pov")
        self.assertEqual(shot_no_pov["first_person"], "")
        self.assertEqual(shot_no_pov["director_meta"]["downgrade_reason"], "pov_not_narratively_required")

        # Explicit narrative POV with pov_required=True IS respected.
        required_pov = {**base, "beat_id": "yes_pov", "speaker": "alice", "listener": "bob", "preferred_shot": "pov", "pov_required": True}
        shot_pov = director.plan_beat(required_pov)
        self.assertEqual(shot_pov["shot_type"], "pov")
        self.assertEqual(shot_pov["first_person"], "alice")

    def test_deterministic_scoring_and_layout_integration_across_all_scene_types(self) -> None:
        layout_sys = SceneLayoutSystem()
        for scene_type in SUPPORTED_SCENE_TYPES:
            with self.subTest(scene_type=scene_type):
                layout = layout_sys.solve(scene_type)
                anchors = list(layout.standing_anchors.values())
                a1 = anchors[1]
                a2 = anchors[2] if len(anchors) > 2 else anchors[0]
                surf = next(iter(layout.surface_anchors.values()))

                beats = [
                    {
                        "beat_id": "open",
                        "duration": 3.0,
                        "speaker": "hero",
                        "listener": "partner",
                        "actor_positions": {"hero": list(a1.position), "partner": list(a2.position)},
                        "actor_facings": {"hero": a1.facing_yaw_deg, "partner": a2.facing_yaw_deg},
                    },
                    {
                        "beat_id": "talk_1",
                        "duration": 3.2,
                        "speaker": "hero",
                        "listener": "partner",
                        "emotion": "confident",
                        "actor_positions": {"hero": list(a1.position), "partner": list(a2.position)},
                        "actor_facings": {"hero": a1.facing_yaw_deg, "partner": a2.facing_yaw_deg},
                    },
                    {
                        "beat_id": "prop_check",
                        "duration": 2.6,
                        "speaker": "partner",
                        "listener": "hero",
                        "action": "inspect",
                        "prop_event": {"prop_id": "device", "event": "inspect", "position": list(surf.position)},
                        "actor_positions": {"hero": list(a1.position), "partner": list(a2.position)},
                        "actor_facings": {"hero": a1.facing_yaw_deg, "partner": a2.facing_yaw_deg},
                    },
                ]
                d1 = CinematicDirector(fps=24)
                d2 = CinematicDirector(fps=24)
                run1 = d1.plan_sequence(beats, layout=layout)
                run2 = d2.plan_sequence(beats, layout=layout)
                self.assertEqual(run1, run2, "Director output must be 100% deterministic")
                for shot in run1:
                    self.assertGreaterEqual(shot["director_meta"]["wall_clearance_m"], WALL_CLEARANCE_MIN_M)
                    self.assertFalse(shot["director_meta"]["occluded"], f"{scene_type} shot {shot['id']} was occluded")

    def test_dynamic_actor_occlusion_rejected_and_narrative_meta_emitted(self) -> None:
        """BUG-001 & BUG-008: Walking passerby crossing camera->speaker must not block speaker, and narrative fields exist."""
        director = CinematicDirector(fps=24)
        beat = BeatSpec(
            beat_id="medlab_walk_cross",
            duration=3.0,
            speaker="linlan",
            listener="chen",
            emotion="worried",
            action="talk",
            preferred_shot="medium",
            actor_positions={"linlan": (0.0, 0.0, -1.0), "chen": (-2.4, 0.0, 1.0)},
            actor_end_positions={"linlan": (0.0, 0.0, -1.0), "chen": (1.6, 0.0, 0.6)},
            actor_trajectories={
                "linlan": [(0.0, (0.0, 0.0, -1.0)), (1.0, (0.0, 0.0, -1.0))],
                "chen": [
                    (0.0, (-2.4, 0.0, 1.0)),
                    (0.4, (-0.4, 0.0, 0.85)),
                    (0.6, (0.1, 0.0, 0.80)),
                    (1.0, (1.6, 0.0, 0.6)),
                ],
            },
            actor_facings={"linlan": (0.0, 1.0), "chen": (0.0, -1.0)},
            actor_tiers={"linlan": "TIER_A", "chen": "TIER_A"},
            room_bounds={"min": [-5.0, 0.0, -4.5], "max": [5.0, 3.6, 4.5]},
        )
        shot = director.plan_beat(beat)
        meta = shot["director_meta"]
        self.assertFalse(meta["dynamic_occluded"], "Director must avoid cameras where chen walks directly in front of linlan")
        self.assertFalse(meta["camera_inside_actor"])
        for req_key in (
            "speaker", "listener", "focus_target", "action_target", "shot_type",
            "emotional_intensity", "visibility_requirements", "subtitle_safe_area", "camera_movement",
        ):
            self.assertIn(req_key, shot, f"BUG-008 shot metadata missing {req_key}")


if __name__ == "__main__":
    unittest.main()
