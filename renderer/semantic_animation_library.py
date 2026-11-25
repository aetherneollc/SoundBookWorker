#!/usr/bin/env python3
"""Semantic dramatic actions grounded in the real Quaternius UAL clips.

``UAL1_Standard.glb`` and ``UAL1_Standard_RM.glb`` each contain the same 43
animation clips. The standard library is in-place (root translation span is
zero). The root-motion library only travels on locomotion and a few attack
clips; every other clip stays planted. This module never invents clip names.
Each ``ActionEntry`` points at one of those verified names and adds a
four-phase dramatic envelope (prepare, execute, follow-through, recover) so a
downstream player can perform anticipation and impact instead of a looping
idle sway.

The envelopes are pose deltas on top of the base clip: a short opposite
wind-up, a crisp execute, a same-direction follow-through, then a small
residue. Joint limits travel with every phase so the pose cannot flop past
the beat.
"""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GLB_MAGIC = 0x46546C67
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
ROOT_MOTION_MIN_M = 0.12
PHASE_NAMES: tuple[str, ...] = ("prepare", "execute", "follow_through", "recover")

# Timing is normalized 0..1. Impact beats are short; sustained poses hold
# through follow-through; locomotion commits weight instead of posing once.
_TIMING: dict[str, tuple[tuple[float, float], ...]] = {
    "impact": ((0.00, 0.16), (0.16, 0.34), (0.34, 0.62), (0.62, 1.00)),
    "gesture": ((0.00, 0.18), (0.18, 0.44), (0.44, 0.70), (0.70, 1.00)),
    "sustain": ((0.00, 0.14), (0.14, 0.36), (0.36, 0.78), (0.78, 1.00)),
    "loco": ((0.00, 0.12), (0.12, 0.55), (0.55, 0.82), (0.82, 1.00)),
}
_WEIGHTS: dict[str, dict[str, float]] = {
    "impact": {"prepare": 0.46, "execute": 1.0, "follow_through": 0.74, "recover": 0.24},
    "gesture": {"prepare": 0.42, "execute": 1.0, "follow_through": 0.70, "recover": 0.22},
    "sustain": {"prepare": 0.40, "execute": 1.0, "follow_through": 0.90, "recover": 0.30},
    "loco": {"prepare": 0.36, "execute": 1.0, "follow_through": 0.64, "recover": 0.26},
}
_SCALES: dict[str, dict[str, float]] = {
    "impact": {"prepare": -0.58, "execute": 1.0, "follow_through": 0.66, "recover": 0.18},
    "gesture": {"prepare": -0.52, "execute": 1.0, "follow_through": 0.62, "recover": 0.16},
    "sustain": {"prepare": -0.40, "execute": 1.0, "follow_through": 0.92, "recover": 0.34},
    "loco": {"prepare": -0.35, "execute": 0.90, "follow_through": 0.55, "recover": 0.20},
}
_LIMITS: dict[str, dict[str, list[float]]] = {
    "impact": {
        "spine_pitch_deg": [-22.0, 30.0],
        "spine_yaw_deg": [-26.0, 26.0],
        "spine_roll_deg": [-12.0, 12.0],
        "head_pitch_deg": [-22.0, 20.0],
        "head_yaw_deg": [-55.0, 55.0],
        "arm_raise": [-0.60, 1.20],
        "arm_reach": [-0.45, 1.15],
        "weight_shift": [-0.70, 0.70],
    },
    "gesture": {
        "spine_pitch_deg": [-18.0, 22.0],
        "spine_yaw_deg": [-30.0, 30.0],
        "spine_roll_deg": [-10.0, 10.0],
        "head_pitch_deg": [-20.0, 18.0],
        "head_yaw_deg": [-60.0, 60.0],
        "arm_raise": [-0.50, 1.15],
        "arm_reach": [-0.40, 1.10],
        "weight_shift": [-0.55, 0.55],
    },
    "sustain": {
        "spine_pitch_deg": [-16.0, 20.0],
        "spine_yaw_deg": [-22.0, 22.0],
        "spine_roll_deg": [-8.0, 8.0],
        "head_pitch_deg": [-18.0, 16.0],
        "head_yaw_deg": [-45.0, 45.0],
        "arm_raise": [-0.40, 1.05],
        "arm_reach": [-0.30, 0.95],
        "weight_shift": [-0.45, 0.45],
    },
    "loco": {
        "spine_pitch_deg": [-14.0, 16.0],
        "spine_yaw_deg": [-20.0, 20.0],
        "spine_roll_deg": [-8.0, 8.0],
        "head_pitch_deg": [-14.0, 12.0],
        "head_yaw_deg": [-40.0, 40.0],
        "arm_raise": [-0.35, 0.85],
        "arm_reach": [-0.25, 0.70],
        "weight_shift": [-0.60, 0.60],
    },
}

REQUIRED_ACTION_IDS: tuple[str, ...] = (
    "confront_step", "accuse_point", "refuse_wave", "argue_push", "interrupt_raise", "warn_finger",
    "slam_table", "shrug_open", "arms_crossed", "hands_on_hips", "pace_tense", "lean_wall",
    "lean_table", "sit_down", "stand_up",
    "abrupt_halt", "snap_look_back", "flinch_dodge", "step_back_shock", "turn_walk_away",
    "rush_enter", "catch_breath",
    "operate_console", "detach_module", "install_module", "repair_kneel", "repair_stand",
    "inspect_gauge", "trigger_door", "pass_prop", "receive_prop",
    "startle_recoil", "fear_brace", "exhale_relax", "rage_clench", "impatient_shift",
    "chin_think", "firm_nod", "head_shake_no",
)

# Impact vector: torso pitch, yaw, roll, weight shift, right raise, right reach,
# left raise, left reach, head pitch, head yaw. Prepare negates it.
_ROWS: tuple[tuple[Any, ...], ...] = (
    ("confront_step", "Walk_Formal_Loop", "gesture", 2, 1.00, "authoritative,formal", "confront,challenge,step", "angry,tense", 0.45, 0.90, "dialogue,confrontation,corridor,command_deck", "person", "confrontation", (8, 12, 0, 0.40, 0.15, 0.35, 0.05, 0.05, 2, 8)),
    ("accuse_point", "Punch_Jab", "impact", 2, 1.00, "aggressive,sharp", "accuse,blame,point", "angry", 0.55, 1.00, "confrontation,dialogue,command_deck", "person", "confrontation", (6, 14, 0, 0.28, 0.78, 0.98, 0.04, 0.0, 1, 16)),
    ("refuse_wave", "Spell_Simple_Exit", "gesture", 2, 1.00, "guarded,dismissive", "refuse,dismiss,wave", "angry,tense", 0.40, 0.85, "confrontation,dialogue", "person", "confrontation", (-2, -16, 0, -0.20, 0.55, 0.40, 0.10, 0.05, -2, -18)),
    ("argue_push", "Push_Loop", "impact", 2, 1.00, "aggressive", "argue,push,shove", "angry", 0.60, 1.00, "confrontation,dialogue", "person", "confrontation", (12, 6, 0, 0.45, 0.40, 0.85, 0.40, 0.80, 4, 4)),
    ("interrupt_raise", "Spell_Simple_Enter", "gesture", 2, 1.00, "assertive", "interrupt,raise,stop", "angry,urgent", 0.50, 0.95, "dialogue,confrontation", "person", "confrontation", (-4, 4, 0, 0.10, 0.95, 0.20, 0.15, 0.0, -6, 6)),
    ("warn_finger", "Pistol_Aim_Up", "impact", 2, 0.90, "authoritative", "warn,finger,threat", "tense,angry", 0.50, 0.90, "confrontation,dialogue", "person", "confrontation", (4, 8, 0, 0.15, 0.90, 0.70, 0.05, 0.0, -4, 10)),
    ("slam_table", "Punch_Cross", "impact", 2, 1.00, "aggressive", "slam,impact,accuse", "angry", 0.70, 1.00, "table,confrontation,command_deck", "table", "posture", (18, 4, 2, 0.50, 0.25, 0.90, 0.20, 0.55, 10, 2)),
    ("shrug_open", "Idle_Talking_Loop", "gesture", 2, 0.85, "dismissive,casual", "shrug,dismiss,open", "neutral,tense", 0.20, 0.60, "dialogue", "person", "posture", (-6, 0, 0, 0.0, 0.45, 0.10, 0.45, 0.10, -4, 0)),
    ("arms_crossed", "Idle_Loop", "sustain", 2, 1.00, "guarded,cold", "cross,posture,refuse", "tense,angry", 0.30, 0.80, "dialogue,confrontation", "none", "posture", (2, 0, 0, 0.05, 0.35, 0.45, 0.35, 0.45, -2, 0)),
    ("hands_on_hips", "Idle_Loop", "sustain", 2, 0.95, "assertive,confident", "hips,posture,confront", "confident,angry", 0.35, 0.80, "dialogue,confrontation", "none", "posture", (4, 0, 0, 0.10, 0.20, 0.15, 0.20, 0.15, 2, 0)),
    ("pace_tense", "Walk_Loop", "loco", 2, 1.00, "nervous,impatient", "pace,tension,walk", "tense,angry", 0.40, 0.85, "dialogue,corridor,command_deck", "floor", "posture", (6, 8, 0, 0.45, 0.20, 0.15, 0.10, 0.05, 2, 6)),
    ("lean_wall", "Idle_Loop", "sustain", 2, 0.90, "weary,guarded", "lean,wall,rest", "weary,tense", 0.20, 0.60, "corridor,dialogue", "wall", "posture", (8, 10, 4, -0.15, 0.05, 0.0, 0.05, 0.0, 4, 8)),
    ("lean_table", "Idle_Talking_Loop", "sustain", 2, 0.95, "focused,tense", "table,lean,talk", "tense,focused", 0.30, 0.75, "table,dialogue,command_deck", "table", "posture", (14, 4, 0, 0.30, 0.25, 0.40, 0.20, 0.30, 6, 2)),
    ("sit_down", "Sitting_Enter", "gesture", 2, 1.00, "calm,weary", "sit,down,chair", "weary,neutral", 0.20, 0.60, "dialogue,command_deck", "chair", "posture", (10, 0, 0, -0.10, 0.15, 0.10, 0.15, 0.10, 4, 0)),
    ("stand_up", "Sitting_Exit", "gesture", 2, 1.00, "assertive,urgent", "stand,rise,chair", "confident,urgent", 0.35, 0.80, "dialogue,command_deck", "chair", "posture", (-8, 0, 0, 0.20, 0.20, 0.15, 0.20, 0.10, -2, 0)),
    ("abrupt_halt", "Jump_Land", "impact", 2, 1.00, "alert", "halt,stop,brake", "surprise,tense", 0.55, 1.00, "corridor,action", "none", "reaction", (8, 0, 0, -0.35, 0.25, 0.10, 0.25, 0.10, 2, 0)),
    ("snap_look_back", "Hit_Head", "impact", 2, 1.00, "alert", "look_back,snap,notice", "surprise,fear", 0.60, 1.00, "corridor,storm,observation_bay", "window", "reaction", (0, 20, 0, 0.10, 0.05, 0.0, 0.05, 0.0, 0, 40)),
    ("flinch_dodge", "Roll", "impact", 2, 1.00, "nervous", "dodge,flinch,evade", "fear,surprise", 0.65, 1.00, "action,horror", "none", "reaction", (6, -18, 8, -0.40, 0.35, 0.20, 0.10, 0.05, 4, -16)),
    ("step_back_shock", "Hit_Chest", "impact", 2, 1.00, "nervous", "retreat,shock,step_back", "fear,surprise", 0.60, 1.00, "confrontation,dialogue", "person", "reaction", (-8, 0, 0, -0.50, 0.40, 0.15, 0.35, 0.10, -4, 0)),
    ("turn_walk_away", "Walk_Loop", "loco", 2, 0.95, "cold,angry", "exit,leave,turn", "angry,sad", 0.40, 0.80, "corridor,doorway", "door", "reaction", (4, 28, 0, 0.30, 0.10, 0.05, 0.10, 0.05, 2, 24)),
    ("rush_enter", "Sprint_Loop", "loco", 2, 1.05, "urgent", "enter,rush,arrive", "urgent,angry", 0.70, 1.00, "doorway,corridor", "door", "reaction", (12, 0, 0, 0.55, 0.35, 0.25, 0.30, 0.20, 2, 0)),
    ("catch_breath", "Crouch_Idle_Loop", "sustain", 2, 0.90, "weary", "breathe,recover,exhausted", "weary,fear", 0.35, 0.80, "action,corridor", "none", "reaction", (16, 0, 0, -0.10, 0.15, 0.20, 0.15, 0.20, 8, 0)),
    ("operate_console", "Interact", "gesture", 2, 1.00, "precise,technician", "operate,console,use", "focused,tense", 0.30, 0.80, "console,command_deck,observation_bay,industrial", "console", "interaction", (8, 4, 0, 0.20, 0.35, 0.55, 0.30, 0.40, 6, 2)),
    ("detach_module", "PickUp_Table", "gesture", 2, 1.00, "precise", "detach,remove,module", "focused", 0.40, 0.85, "console,industrial,med_lab", "module", "interaction", (10, 2, 0, 0.15, 0.30, 0.70, 0.25, 0.45, 8, 0)),
    ("install_module", "Interact", "impact", 2, 1.00, "precise,urgent", "install,mount,module", "focused,urgent", 0.45, 0.90, "console,industrial,observation_bay", "module", "interaction", (12, 2, 0, 0.25, 0.25, 0.80, 0.25, 0.60, 10, 0)),
    ("repair_kneel", "Fixing_Kneeling", "sustain", 2, 1.00, "precise,technician", "repair,kneel,fix", "focused", 0.40, 0.85, "repair,industrial,med_lab", "floor", "interaction", (18, 4, 0, 0.10, 0.20, 0.55, 0.20, 0.50, 12, 2)),
    ("repair_stand", "Interact", "gesture", 2, 0.95, "precise", "repair,stand,fix", "focused", 0.35, 0.80, "console,industrial,med_lab", "console", "interaction", (10, 6, 0, 0.15, 0.40, 0.60, 0.15, 0.25, 8, 4)),
    ("inspect_gauge", "Idle_Torch_Loop", "sustain", 2, 0.90, "precise,focused", "inspect,gauge,read", "focused,tense", 0.25, 0.70, "console,command_deck,industrial", "gauge", "interaction", (6, 8, 0, 0.05, 0.15, 0.25, 0.10, 0.10, 10, 6)),
    ("trigger_door", "Interact", "gesture", 2, 0.95, "precise", "door,open,trigger", "focused", 0.30, 0.75, "doorway,corridor", "door", "interaction", (4, 12, 0, 0.20, 0.55, 0.65, 0.05, 0.05, 0, 8)),
    ("pass_prop", "PickUp_Table", "gesture", 2, 0.90, "precise,calm", "pass,give,handoff", "neutral,focused", 0.30, 0.70, "dialogue", "prop", "interaction", (6, 10, 0, 0.20, 0.40, 0.75, 0.10, 0.15, 2, 8)),
    ("receive_prop", "Interact", "gesture", 2, 0.90, "precise", "receive,take,handoff", "focused", 0.30, 0.70, "dialogue", "prop", "interaction", (4, -6, 0, -0.05, 0.35, 0.60, 0.20, 0.30, 2, -4)),
    ("startle_recoil", "Hit_Chest", "impact", 2, 1.05, "nervous", "startle,recoil,shock", "surprise,fear", 0.70, 1.00, "action,dialogue,horror", "none", "emotion", (-10, 0, 2, -0.45, 0.55, 0.10, 0.50, 0.10, -6, 0)),
    ("fear_brace", "Crouch_Idle_Loop", "sustain", 2, 1.00, "nervous", "fear,brace,cower", "fear", 0.55, 1.00, "horror,storm,action", "none", "emotion", (12, 0, 0, -0.20, 0.45, 0.25, 0.45, 0.25, 6, 0)),
    ("exhale_relax", "Idle_Loop", "sustain", 2, 0.80, "calm", "relax,exhale,release", "relieved,neutral", 0.15, 0.50, "dialogue", "none", "emotion", (-6, 0, 0, 0.0, -0.10, 0.0, -0.10, 0.0, -4, 0)),
    ("rage_clench", "Punch_Jab", "sustain", 2, 1.00, "aggressive", "rage,clench,anger", "angry", 0.75, 1.00, "confrontation", "none", "emotion", (6, 0, 0, 0.15, 0.35, 0.20, 0.35, 0.20, 4, 0)),
    ("impatient_shift", "Idle_Talking_Loop", "gesture", 2, 0.85, "impatient", "impatient,shift,fidget", "tense,angry", 0.35, 0.75, "dialogue", "none", "emotion", (2, 8, 0, 0.35, 0.10, 0.05, 0.05, 0.0, 0, 6)),
    ("chin_think", "Idle_Loop", "sustain", 2, 0.90, "calm,focused", "think,consider,chin", "focused,neutral", 0.20, 0.55, "dialogue,command_deck", "none", "emotion", (4, 6, 0, 0.05, 0.55, 0.25, 0.05, 0.0, 8, 4)),
    ("firm_nod", "Idle_Talking_Loop", "gesture", 2, 0.85, "authoritative,confident", "nod,agree,confirm", "confident,neutral", 0.30, 0.70, "dialogue", "person", "emotion", (2, 0, 0, 0.05, 0.05, 0.0, 0.05, 0.0, 12, 0)),
    ("head_shake_no", "Idle_Talking_Loop", "gesture", 2, 0.90, "guarded", "deny,shake,refuse", "tense,angry", 0.35, 0.80, "dialogue,confrontation", "person", "emotion", (0, 0, 0, 0.0, 0.05, 0.0, 0.05, 0.0, 0, 22)),
    ("walk_casual", "Walk_Loop", "loco", 0, 0.70, "casual", "walk,locomotion", "neutral", 0.20, 0.50, "corridor,observation_bay", "none", "locomotion", (4, 2, 0, 0.30, 0.15, 0.05, 0.12, 0.05, 0, 2)),
    ("walk_formal", "Walk_Formal_Loop", "loco", 0, 0.80, "formal", "walk,approach", "confident", 0.30, 0.60, "dialogue,command_deck", "none", "locomotion", (2, 0, 0, 0.25, 0.08, 0.05, 0.08, 0.05, 2, 0)),
    ("walk_confident", "Walk_Formal_Loop", "loco", 1, 1.00, "confident", "walk,enter", "confident", 0.40, 0.75, "doorway,command_deck", "door", "locomotion", (6, 4, 0, 0.40, 0.18, 0.12, 0.12, 0.08, 0, 2)),
    ("walk_weary", "Walk_Loop", "loco", 0, 0.65, "weary", "walk,trudge", "weary", 0.20, 0.55, "corridor", "none", "locomotion", (10, 0, 2, 0.20, 0.05, 0.0, 0.05, 0.0, 6, 0)),
    ("walk_cautious", "Crouch_Fwd_Loop", "loco", 0, 0.85, "cautious", "walk,sneak", "fear,tense", 0.35, 0.70, "horror,corridor", "none", "locomotion", (12, 4, 0, 0.25, 0.20, 0.15, 0.15, 0.10, 4, 4)),
    ("walk_urgent", "Jog_Fwd_Loop", "loco", 1, 1.00, "urgent", "jog,hurry", "urgent", 0.60, 0.90, "corridor,doorway", "none", "locomotion", (10, 0, 0, 0.50, 0.30, 0.20, 0.25, 0.15, 2, 0)),
    ("walk_escort", "Walk_Formal_Loop", "loco", 0, 0.75, "calm,formal", "walk,escort", "neutral", 0.25, 0.55, "corridor", "person", "locomotion", (2, 6, 0, 0.22, 0.10, 0.08, 0.08, 0.05, 2, 4)),
    ("walk_patrol", "Walk_Loop", "loco", 0, 0.85, "alert", "walk,patrol", "tense", 0.30, 0.65, "military,corridor", "none", "locomotion", (4, 10, 0, 0.30, 0.12, 0.08, 0.10, 0.05, 0, 12)),
    ("jog_approach", "Jog_Fwd_Loop", "loco", 1, 0.95, "urgent", "jog,approach", "urgent,tense", 0.55, 0.85, "doorway,confrontation", "person", "locomotion", (8, 6, 0, 0.48, 0.22, 0.18, 0.18, 0.12, 2, 4)),
    ("jog_urgent", "Jog_Fwd_Loop", "loco", 0, 1.05, "urgent", "jog,urgent", "urgent", 0.70, 1.00, "corridor", "none", "locomotion", (12, 0, 0, 0.55, 0.32, 0.18, 0.28, 0.14, 2, 0)),
    ("jog_carry", "Jog_Fwd_Loop", "loco", 0, 0.80, "focused", "jog,carry", "focused", 0.45, 0.80, "corridor,industrial", "prop", "locomotion", (8, 2, 0, 0.35, 0.40, 0.35, 0.40, 0.35, 4, 0)),
    ("sprint_burst", "Sprint_Loop", "loco", 1, 1.10, "urgent", "sprint,burst", "urgent", 0.80, 1.00, "action,corridor", "none", "locomotion", (14, 0, 0, 0.60, 0.40, 0.20, 0.35, 0.18, 2, 0)),
    ("sprint_escape", "Sprint_Loop", "loco", 0, 1.05, "nervous", "sprint,escape", "fear", 0.75, 1.00, "horror,doorway", "door", "locomotion", (16, 4, 2, 0.55, 0.35, 0.15, 0.30, 0.12, 4, 6)),
    ("sprint_intercept", "Sprint_Loop", "loco", 0, 1.00, "aggressive", "sprint,intercept", "angry", 0.80, 1.00, "confrontation", "person", "locomotion", (12, 8, 0, 0.55, 0.30, 0.25, 0.20, 0.15, 2, 8)),
    ("crouch_advance", "Crouch_Fwd_Loop", "loco", 0, 0.90, "cautious", "crouch,advance", "tense,fear", 0.40, 0.80, "military,horror", "none", "locomotion", (14, 2, 0, 0.30, 0.18, 0.12, 0.15, 0.10, 6, 2)),
    ("crouch_hold", "Crouch_Idle_Loop", "sustain", 0, 0.85, "alert", "crouch,hold", "tense", 0.30, 0.70, "military,horror", "none", "locomotion", (12, 0, 0, 0.05, 0.20, 0.15, 0.20, 0.15, 4, 0)),
    ("crouch_cover", "Crouch_Idle_Loop", "sustain", 0, 1.00, "nervous", "crouch,cover", "fear", 0.50, 0.90, "horror", "none", "locomotion", (16, 6, 4, -0.10, 0.45, 0.20, 0.40, 0.18, 8, 4)),
    ("push_obstacle", "Push_Loop", "loco", 0, 0.90, "focused", "push,move", "focused", 0.40, 0.80, "industrial", "prop", "locomotion", (14, 0, 0, 0.40, 0.35, 0.70, 0.35, 0.65, 6, 0)),
    ("push_crate", "Push_Loop", "loco", 0, 0.80, "weary", "push,crate", "weary", 0.45, 0.85, "industrial", "prop", "locomotion", (16, 2, 0, 0.35, 0.30, 0.60, 0.30, 0.55, 8, 0)),
    ("roll_evade", "Roll", "impact", 1, 1.00, "alert", "roll,evade", "fear,urgent", 0.70, 1.00, "action", "none", "locomotion", (8, -14, 10, 0.20, 0.25, 0.15, 0.10, 0.05, 2, -10)),
    ("dive_cover", "Roll", "impact", 0, 1.05, "nervous", "dive,cover", "fear", 0.75, 1.00, "horror,action", "none", "locomotion", (14, -10, 8, 0.15, 0.40, 0.25, 0.20, 0.10, 6, -8)),
    ("jump_up", "Jump_Start", "impact", 0, 1.00, "urgent", "jump,up", "urgent", 0.60, 0.95, "action,industrial", "none", "locomotion", (-6, 0, 0, 0.20, 0.70, 0.15, 0.65, 0.10, -4, 0)),
    ("jump_gap", "Jump_Loop", "loco", 0, 0.90, "focused", "jump,cross", "tense", 0.50, 0.90, "industrial,action", "none", "locomotion", (4, 0, 0, 0.15, 0.40, 0.20, 0.35, 0.15, 2, 0)),
    ("land_heavy", "Jump_Land", "impact", 1, 1.05, "alert", "land,heavy", "tense", 0.60, 1.00, "action", "none", "locomotion", (14, 0, 0, -0.30, 0.20, 0.10, 0.20, 0.10, 6, 0)),
    ("land_soft", "Jump_Land", "impact", 0, 0.60, "calm", "land,soft", "neutral", 0.25, 0.55, "action", "none", "locomotion", (6, 0, 0, -0.10, 0.10, 0.05, 0.10, 0.05, 2, 0)),
    ("formal_approach", "Walk_Formal_Loop", "gesture", 1, 0.95, "formal,authoritative", "approach,confront", "confident,angry", 0.40, 0.80, "confrontation,command_deck", "person", "locomotion", (6, 8, 0, 0.35, 0.12, 0.20, 0.08, 0.05, 2, 6)),
    ("formal_withdraw", "Walk_Formal_Loop", "loco", 0, 0.80, "formal", "withdraw,leave", "tense", 0.30, 0.70, "corridor,doorway", "door", "locomotion", (2, 18, 0, 0.25, 0.08, 0.05, 0.08, 0.05, 0, 14)),
    ("stop_at_threshold", "Jump_Land", "impact", 1, 0.85, "alert", "halt,threshold,door", "tense", 0.50, 0.85, "doorway", "door", "locomotion", (6, 4, 0, -0.25, 0.15, 0.10, 0.12, 0.08, 2, 4)),
    ("corridor_sweep", "Walk_Loop", "loco", 0, 0.85, "alert", "walk,sweep", "tense", 0.35, 0.70, "corridor,military", "none", "locomotion", (4, 14, 0, 0.28, 0.10, 0.08, 0.08, 0.05, 0, 16)),
    ("idle_neutral", "Idle_Loop", "sustain", 0, 0.40, "calm", "idle,stand", "neutral", 0.10, 0.35, "dialogue", "none", "idle", (1, 0, 0, 0.04, 0.02, 0.0, 0.02, 0.0, 0, 0)),
    ("idle_listen", "Idle_Loop", "sustain", 0, 0.55, "calm", "idle,listen", "neutral,tense", 0.15, 0.45, "dialogue", "person", "idle", (2, 6, 0, 0.05, 0.04, 0.0, 0.04, 0.0, 2, 8)),
    ("idle_talk_calm", "Idle_Talking_Loop", "gesture", 1, 0.60, "calm", "talk,idle", "neutral", 0.20, 0.50, "dialogue", "person", "idle", (2, 4, 0, 0.08, 0.18, 0.12, 0.08, 0.04, 2, 4)),
    ("idle_talk_heated", "Idle_Talking_Loop", "gesture", 1, 1.00, "aggressive", "talk,argue", "angry", 0.55, 0.90, "confrontation,dialogue", "person", "idle", (6, 10, 0, 0.22, 0.35, 0.30, 0.15, 0.10, 4, 8)),
    ("idle_talk_whisper", "Idle_Talking_Loop", "gesture", 0, 0.45, "cautious", "talk,whisper", "tense,fear", 0.15, 0.40, "dialogue,horror", "person", "idle", (4, 8, 0, 0.06, 0.10, 0.08, 0.06, 0.02, 4, 10)),
    ("idle_guard", "Sword_Idle", "sustain", 0, 0.80, "alert", "idle,guard", "tense", 0.30, 0.70, "military", "none", "idle", (4, 0, 0, 0.08, 0.25, 0.15, 0.10, 0.05, 0, 4)),
    ("idle_alert", "Pistol_Idle_Loop", "sustain", 1, 0.85, "alert", "idle,alert,ready", "tense,urgent", 0.40, 0.80, "military,command_deck", "none", "idle", (2, 2, 0, 0.06, 0.40, 0.25, 0.10, 0.05, -2, 2)),
    ("idle_weary", "Idle_Loop", "sustain", 0, 0.55, "weary", "idle,weary", "weary", 0.15, 0.45, "dialogue", "none", "idle", (10, 0, 2, -0.08, 0.05, 0.0, 0.05, 0.0, 6, 0)),
    ("idle_cold", "Idle_Loop", "sustain", 0, 0.70, "cold", "idle,stare", "angry,tense", 0.35, 0.70, "confrontation", "person", "idle", (2, 0, 0, 0.04, 0.08, 0.05, 0.08, 0.05, -2, 0)),
    ("idle_sit", "Sitting_Idle_Loop", "sustain", 0, 0.50, "calm,weary", "idle,sit", "neutral,weary", 0.10, 0.40, "dialogue,command_deck", "chair", "idle", (4, 0, 0, 0.0, 0.08, 0.05, 0.08, 0.05, 2, 0)),
    ("idle_sit_listen", "Sitting_Idle_Loop", "sustain", 0, 0.55, "calm", "sit,listen", "neutral", 0.15, 0.45, "dialogue", "person", "idle", (4, 8, 0, 0.0, 0.06, 0.04, 0.06, 0.04, 2, 10)),
    ("idle_sit_explain", "Sitting_Talking_Loop", "gesture", 0, 0.70, "focused", "sit,talk", "focused", 0.25, 0.60, "dialogue,command_deck", "person", "idle", (4, 6, 0, 0.05, 0.22, 0.18, 0.10, 0.06, 2, 6)),
    ("idle_console_watch", "Idle_Torch_Loop", "sustain", 0, 0.70, "focused", "idle,watch,console", "focused", 0.20, 0.55, "console,command_deck,observation_bay", "console", "idle", (6, 4, 0, 0.05, 0.12, 0.15, 0.08, 0.08, 8, 2)),
    ("idle_window_watch", "Idle_Loop", "sustain", 0, 0.75, "tense", "idle,watch,window", "tense,fear", 0.25, 0.65, "storm,observation_bay", "window", "idle", (2, 12, 0, 0.05, 0.05, 0.0, 0.05, 0.0, 0, 14)),
    ("idle_ready", "Sword_Idle", "sustain", 0, 0.90, "alert", "idle,ready", "tense", 0.35, 0.75, "military", "none", "idle", (4, 0, 0, 0.10, 0.30, 0.18, 0.12, 0.06, 0, 0)),
    ("idle_impatient_tap", "Idle_Talking_Loop", "gesture", 0, 0.80, "impatient", "idle,impatient,tap", "tense", 0.35, 0.70, "dialogue,console", "console", "idle", (2, 4, 0, 0.18, 0.15, 0.20, 0.05, 0.05, 2, 2)),
    ("idle_breathe", "Idle_Loop", "sustain", 0, 0.50, "weary", "idle,breathe", "weary,relieved", 0.15, 0.40, "dialogue", "none", "idle", (6, 0, 0, 0.0, 0.04, 0.0, 0.04, 0.0, 2, 0)),
    ("idle_injured", "Crouch_Idle_Loop", "sustain", 0, 0.90, "weary", "idle,injured", "weary,fear", 0.40, 0.80, "action,med_lab", "none", "idle", (14, 2, 2, -0.12, 0.20, 0.15, 0.10, 0.08, 8, 0)),
    ("idle_torch", "Idle_Torch_Loop", "sustain", 0, 0.80, "alert", "idle,torch,search", "tense", 0.30, 0.70, "horror,corridor", "none", "idle", (4, 10, 0, 0.08, 0.35, 0.20, 0.08, 0.05, 2, 12)),
    ("check_storm_window", "Idle_Loop", "gesture", 2, 1.00, "alert,tense", "look,window,storm", "tense,fear", 0.40, 0.80, "storm,observation_bay,window", "window", "reaction", (2, 26, 0, 0.15, 0.08, 0.05, 0.05, 0.0, 0, 28)),
    ("glance_console", "Idle_Talking_Loop", "gesture", 0, 0.75, "focused", "glance,console", "focused,tense", 0.30, 0.65, "console,command_deck", "console", "interaction", (6, 10, 0, 0.08, 0.10, 0.12, 0.06, 0.04, 8, 8)),
    ("double_take", "Hit_Head", "impact", 1, 1.00, "alert", "double_take,notice", "surprise", 0.65, 1.00, "dialogue", "person", "reaction", (0, 24, 0, 0.08, 0.05, 0.0, 0.05, 0.0, -2, 32)),
    ("dismiss_turn", "Walk_Formal_Loop", "gesture", 0, 0.85, "cold", "dismiss,turn", "angry", 0.40, 0.75, "confrontation", "person", "confrontation", (2, 30, 0, 0.20, 0.20, 0.10, 0.05, 0.0, 0, 22)),
    ("beckon_come", "Spell_Simple_Enter", "gesture", 1, 0.90, "assertive", "beckon,come", "urgent", 0.40, 0.80, "doorway,dialogue", "person", "confrontation", (-2, 8, 0, 0.10, 0.70, 0.35, 0.10, 0.05, -2, 8)),
    ("offer_hand", "Interact", "gesture", 0, 0.80, "calm", "offer,hand", "neutral", 0.25, 0.60, "dialogue", "person", "interaction", (4, 8, 0, 0.12, 0.30, 0.55, 0.08, 0.10, 2, 6)),
    ("take_document", "PickUp_Table", "gesture", 0, 0.85, "focused", "take,document", "focused", 0.30, 0.65, "dialogue,command_deck", "prop", "interaction", (8, 4, 0, 0.10, 0.25, 0.60, 0.15, 0.30, 8, 2)),
    ("kneel_inspect", "Fixing_Kneeling", "sustain", 1, 0.90, "precise", "kneel,inspect", "focused", 0.35, 0.75, "industrial,med_lab,repair", "floor", "interaction", (16, 4, 0, 0.08, 0.18, 0.45, 0.18, 0.40, 12, 2)),
    ("rise_from_kneel", "Sitting_Exit", "gesture", 0, 0.90, "focused", "rise,kneel", "focused", 0.35, 0.70, "industrial,repair", "floor", "posture", (-10, 0, 0, 0.18, 0.15, 0.10, 0.12, 0.08, -2, 0)),
    ("sit_collapse", "Sitting_Enter", "gesture", 0, 0.80, "weary", "sit,collapse", "weary,sad", 0.30, 0.70, "dialogue,med_lab", "chair", "posture", (14, 0, 2, -0.15, 0.10, 0.05, 0.10, 0.05, 8, 0)),
    ("sit_alert", "Sitting_Talking_Loop", "sustain", 1, 0.90, "alert", "sit,alert", "tense,urgent", 0.45, 0.80, "command_deck,dialogue", "chair", "posture", (4, 4, 0, 0.08, 0.20, 0.15, 0.12, 0.08, 2, 4)),
    ("flinch_head", "Hit_Head", "impact", 0, 1.00, "nervous", "flinch,head", "fear,surprise", 0.70, 1.00, "action", "none", "reaction", (-4, 12, 4, -0.15, 0.20, 0.05, 0.15, 0.05, -8, 14)),
    ("flinch_body", "Hit_Chest", "impact", 0, 0.95, "nervous", "flinch,body", "fear", 0.65, 1.00, "action", "none", "reaction", (-8, 4, 2, -0.30, 0.35, 0.10, 0.30, 0.08, -4, 4)),
    ("stagger_back", "Hit_Chest", "impact", 0, 0.85, "weary", "stagger,back", "fear,weary", 0.60, 0.95, "action", "none", "reaction", (-12, 2, 4, -0.55, 0.25, 0.08, 0.20, 0.06, -2, 2)),
    ("brace_impact", "Crouch_Idle_Loop", "impact", 1, 1.00, "alert", "brace,impact", "tense,fear", 0.60, 1.00, "action,storm", "none", "reaction", (10, 0, 0, -0.20, 0.40, 0.20, 0.40, 0.20, 4, 0)),
    ("open_panel", "Interact", "gesture", 0, 0.85, "precise", "open,panel", "focused", 0.30, 0.70, "console,industrial", "console", "interaction", (6, 8, 0, 0.10, 0.30, 0.50, 0.20, 0.25, 4, 4)),
    ("close_panel", "Interact", "gesture", 0, 0.80, "precise", "close,panel", "focused", 0.30, 0.70, "console,industrial", "console", "interaction", (8, -4, 0, 0.12, 0.25, 0.45, 0.20, 0.30, 6, -2)),
    ("press_button", "Interact", "impact", 0, 0.75, "precise", "press,button,console", "focused", 0.25, 0.65, "console,command_deck", "console", "interaction", (6, 2, 0, 0.08, 0.20, 0.55, 0.05, 0.10, 6, 0)),
    ("flip_switch", "Interact", "gesture", 0, 0.80, "precise", "switch,console", "focused", 0.30, 0.70, "console,industrial", "console", "interaction", (4, 6, 0, 0.08, 0.35, 0.40, 0.08, 0.08, 4, 4)),
    ("pull_lever", "Interact", "impact", 1, 1.00, "focused,urgent", "lever,pull", "focused,urgent", 0.45, 0.85, "industrial,console", "console", "interaction", (8, -8, 0, -0.15, 0.45, 0.50, 0.15, 0.15, 4, -4)),
    ("mount_sensor", "Interact", "impact", 1, 1.00, "precise,urgent", "install,sensor,mount", "focused,urgent", 0.50, 0.90, "console,observation_bay,industrial", "module", "interaction", (12, 2, 0, 0.22, 0.28, 0.75, 0.22, 0.55, 10, 0)),
    ("seat_module", "Interact", "gesture", 0, 0.90, "precise", "seat,install,module", "focused", 0.40, 0.80, "console,industrial", "module", "interaction", (10, 0, 0, 0.15, 0.22, 0.65, 0.22, 0.50, 8, 0)),
    ("lock_clamp", "Punch_Jab", "impact", 0, 0.80, "precise", "lock,clamp", "focused", 0.45, 0.85, "console,industrial", "module", "interaction", (8, 2, 0, 0.18, 0.20, 0.70, 0.15, 0.30, 6, 0)),
    ("unlatch_cover", "PickUp_Table", "gesture", 0, 0.85, "precise", "unlatch,detach", "focused", 0.40, 0.80, "console,industrial", "module", "interaction", (8, 4, 0, 0.10, 0.30, 0.55, 0.20, 0.35, 6, 2)),
    ("salute_brief", "Sword_Idle", "gesture", 0, 0.70, "formal", "salute,acknowledge", "confident", 0.30, 0.60, "military", "person", "emotion", (2, 0, 0, 0.05, 0.80, 0.15, 0.05, 0.0, 0, 0)),
    ("nod_to_partner", "Idle_Talking_Loop", "gesture", 0, 0.70, "calm,confident", "nod,partner", "confident", 0.25, 0.60, "dialogue", "person", "emotion", (2, 4, 0, 0.05, 0.05, 0.0, 0.04, 0.0, 10, 4)),
    ("shake_head_hard", "Idle_Talking_Loop", "gesture", 0, 1.05, "aggressive", "deny,shake,hard", "angry", 0.60, 0.95, "confrontation", "person", "emotion", (2, 0, 0, 0.08, 0.08, 0.0, 0.06, 0.0, 2, 28)),
    ("slow_nod", "Sitting_Talking_Loop", "gesture", 0, 0.50, "weary", "nod,slow", "weary,neutral", 0.15, 0.45, "dialogue", "person", "emotion", (2, 0, 0, 0.0, 0.04, 0.0, 0.04, 0.0, 8, 0)),
    ("rage_step", "Walk_Formal_Loop", "gesture", 1, 1.05, "aggressive", "rage,step,confront", "angry", 0.75, 1.00, "confrontation", "person", "confrontation", (8, 10, 0, 0.48, 0.25, 0.30, 0.15, 0.10, 4, 8)),
    ("cold_stare", "Idle_Loop", "sustain", 0, 0.75, "cold", "stare,cold", "angry", 0.40, 0.80, "confrontation", "person", "emotion", (2, 0, 0, 0.04, 0.06, 0.02, 0.06, 0.02, -4, 0)),
    ("fearful_retreat", "Walk_Loop", "loco", 0, 0.90, "nervous", "retreat,fear", "fear", 0.55, 0.90, "horror,corridor", "door", "reaction", (-6, 16, 0, -0.35, 0.20, 0.08, 0.18, 0.06, -2, 12)),
    ("relieved_drop", "Idle_Loop", "sustain", 0, 0.70, "calm", "relax,relief", "relieved", 0.20, 0.50, "dialogue", "none", "emotion", (-8, 0, 0, -0.05, -0.12, 0.0, -0.12, 0.0, -6, 0)),
    ("door_enter", "Walk_Loop", "loco", 1, 0.90, "urgent,tense", "enter,door", "urgent,tense", 0.40, 0.80, "doorway,corridor", "door", "locomotion", (6, 0, 0, 0.35, 0.15, 0.12, 0.12, 0.08, 2, 0)),
    ("door_exit", "Walk_Formal_Loop", "loco", 0, 0.85, "calm", "exit,door", "tense", 0.35, 0.75, "doorway", "door", "locomotion", (2, 20, 0, 0.28, 0.10, 0.06, 0.08, 0.04, 0, 16)),
    ("table_lean_in", "Idle_Talking_Loop", "gesture", 1, 1.00, "aggressive", "lean,table,confront", "angry", 0.50, 0.85, "table,confrontation", "table", "posture", (16, 6, 0, 0.35, 0.20, 0.35, 0.15, 0.25, 8, 4)),
    ("table_push_off", "Idle_Loop", "gesture", 0, 0.90, "assertive", "push_off,table,stand", "angry", 0.45, 0.80, "table", "table", "posture", (-8, 0, 0, -0.20, 0.25, 0.20, 0.20, 0.15, -2, 0)),
    ("chair_sit", "Sitting_Enter", "gesture", 0, 0.85, "calm", "sit,chair", "weary", 0.20, 0.55, "dialogue", "chair", "posture", (8, 0, 0, -0.08, 0.12, 0.08, 0.12, 0.08, 4, 0)),
    ("chair_rise", "Sitting_Exit", "gesture", 1, 1.00, "urgent", "stand,chair", "urgent", 0.40, 0.80, "dialogue,command_deck", "chair", "posture", (-10, 2, 0, 0.22, 0.18, 0.12, 0.14, 0.08, -2, 2)),
    ("catch_breath_stand", "Idle_Loop", "sustain", 0, 0.75, "weary", "breathe,stand", "weary", 0.30, 0.70, "action", "none", "emotion", (8, 0, 0, 0.0, 0.08, 0.0, 0.08, 0.0, 4, 0)),
    ("pant_hands_knees", "Crouch_Idle_Loop", "sustain", 0, 1.00, "weary", "breathe,pant,kneel", "weary,fear", 0.45, 0.90, "action", "none", "emotion", (18, 0, 0, -0.08, 0.20, 0.25, 0.20, 0.25, 10, 0)),
    ("argue_lean", "Idle_Talking_Loop", "gesture", 0, 1.00, "aggressive", "argue,lean", "angry", 0.55, 0.90, "confrontation,dialogue", "person", "confrontation", (12, 10, 0, 0.30, 0.25, 0.30, 0.12, 0.10, 4, 8)),
    ("accuse_step", "Walk_Formal_Loop", "gesture", 0, 0.95, "aggressive", "accuse,step", "angry", 0.60, 0.95, "confrontation", "person", "confrontation", (6, 12, 0, 0.42, 0.20, 0.35, 0.08, 0.05, 2, 10)),
    ("refuse_step_back", "Walk_Loop", "loco", 0, 0.85, "guarded", "refuse,step_back", "angry,tense", 0.45, 0.80, "confrontation", "person", "confrontation", (-4, -8, 0, -0.35, 0.30, 0.15, 0.10, 0.05, -2, -10)),
    ("warn_step", "Walk_Formal_Loop", "gesture", 0, 0.95, "authoritative", "warn,step", "tense,angry", 0.50, 0.90, "confrontation", "person", "confrontation", (4, 8, 0, 0.32, 0.55, 0.40, 0.08, 0.04, 0, 8)),
    ("interrupt_step", "Walk_Loop", "gesture", 0, 0.95, "assertive", "interrupt,step", "urgent,angry", 0.55, 0.90, "dialogue", "person", "confrontation", (4, 6, 0, 0.38, 0.40, 0.25, 0.10, 0.05, -2, 6)),
    ("confront_halt", "Jump_Land", "impact", 0, 1.00, "aggressive", "halt,stop,confront", "angry", 0.65, 1.00, "confrontation", "person", "confrontation", (8, 6, 0, -0.20, 0.20, 0.15, 0.15, 0.10, 2, 6)),
    ("device_scan", "Idle_Torch_Loop", "sustain", 0, 0.80, "precise", "scan,device", "focused", 0.30, 0.70, "console,industrial", "console", "interaction", (6, 6, 0, 0.06, 0.18, 0.22, 0.12, 0.12, 8, 4)),
    ("device_reboot", "Interact", "gesture", 1, 0.95, "focused,urgent", "reboot,console", "urgent,focused", 0.45, 0.85, "console,command_deck", "console", "interaction", (8, 2, 0, 0.12, 0.30, 0.50, 0.20, 0.30, 6, 0)),
    ("error_recoil", "Hit_Chest", "impact", 1, 0.90, "nervous", "recoil,error,alarm", "surprise,fear", 0.60, 0.95, "console,command_deck", "console", "reaction", (-8, -4, 0, -0.30, 0.35, 0.10, 0.30, 0.08, -4, -4)),
    ("pass_tool", "PickUp_Table", "gesture", 0, 0.80, "precise", "pass,tool", "focused", 0.30, 0.65, "dialogue,industrial", "prop", "interaction", (6, 8, 0, 0.15, 0.35, 0.65, 0.12, 0.15, 2, 6)),
    ("receive_tool", "Interact", "gesture", 0, 0.80, "precise", "receive,tool", "focused", 0.30, 0.65, "dialogue,industrial", "prop", "interaction", (4, -4, 0, 0.0, 0.30, 0.55, 0.18, 0.25, 2, -2)),
    ("pass_sensor", "PickUp_Table", "gesture", 1, 0.95, "precise,urgent", "pass,sensor", "focused,urgent", 0.40, 0.80, "dialogue,observation_bay", "prop", "interaction", (6, 10, 0, 0.18, 0.38, 0.72, 0.12, 0.18, 4, 8)),
    ("receive_sensor", "Interact", "gesture", 1, 0.95, "precise,urgent", "receive,sensor", "focused,urgent", 0.40, 0.80, "dialogue,observation_bay", "prop", "interaction", (4, -6, 0, 0.05, 0.32, 0.62, 0.20, 0.28, 4, -4)),
    ("collapse_down", "Death01", "impact", 0, 1.00, "weary", "collapse,down", "weary,fear", 0.70, 1.00, "action,med_lab", "floor", "reaction", (20, 4, 6, -0.20, 0.15, 0.10, 0.10, 0.08, 12, 2)),
    ("console_error_tap", "Interact", "impact", 0, 0.90, "impatient", "console,error,tap", "tense,angry", 0.50, 0.85, "console,command_deck", "console", "interaction", (8, 2, 0, 0.12, 0.25, 0.60, 0.08, 0.10, 6, 2)),
    ("aim_low_check", "Pistol_Aim_Down", "impact", 0, 0.70, "cautious", "aim,check,low", "tense", 0.30, 0.60, "console,industrial", "gauge", "interaction", (10, 2, 0, 0.08, 0.35, 0.40, 0.08, 0.05, 12, 0)),
    ("aim_level_warn", "Pistol_Aim_Neutral", "impact", 0, 0.85, "authoritative", "aim,warn", "tense,angry", 0.45, 0.80, "confrontation,military", "person", "confrontation", (2, 6, 0, 0.10, 0.70, 0.55, 0.08, 0.05, 0, 6)),
    ("signal_pulse", "Spell_Simple_Shoot", "impact", 0, 0.90, "urgent", "signal,pulse", "urgent", 0.55, 0.90, "console,command_deck", "console", "interaction", (4, 0, 0, 0.10, 0.60, 0.45, 0.15, 0.10, 0, 0)),
    ("charge_device", "Spell_Simple_Idle_Loop", "sustain", 0, 0.80, "focused", "charge,device", "focused", 0.30, 0.70, "console,industrial", "console", "interaction", (4, 0, 0, 0.06, 0.40, 0.35, 0.35, 0.30, 2, 0)),
    ("release_charge", "Spell_Simple_Exit", "gesture", 0, 0.75, "focused", "release,device", "relieved", 0.25, 0.60, "console", "console", "interaction", (-4, 0, 0, -0.05, 0.20, 0.15, 0.15, 0.10, -2, 0)),
    ("door_close_pull", "Interact", "impact", 1, 0.95, "precise", "door,close", "focused", 0.35, 0.75, "doorway", "door", "interaction", (6, 14, 0, 0.25, 0.50, 0.55, 0.10, 0.08, 0, 8)),
    ("lean_doorframe", "Idle_Loop", "sustain", 0, 0.80, "weary,tense", "lean,door", "weary,tense", 0.20, 0.55, "doorway,corridor", "door", "posture", (8, 12, 4, -0.12, 0.08, 0.0, 0.05, 0.0, 2, 10)),
    ("victory_nod", "Idle_Talking_Loop", "gesture", 0, 0.80, "confident", "nod,victory", "confident,relieved", 0.30, 0.65, "dialogue", "person", "emotion", (2, 0, 0, 0.08, 0.08, 0.04, 0.06, 0.02, 10, 0)),
    ("lunge_strike", "Sword_Attack", "impact", 0, 1.00, "aggressive", "lunge,strike", "angry", 0.70, 1.00, "military,action", "person", "confrontation", (14, 8, 0, 0.55, 0.20, 0.85, 0.10, 0.20, 4, 6)),
    ("reload_sidearm", "Pistol_Reload", "gesture", 0, 0.85, "precise,alert", "reload,sidearm", "tense", 0.40, 0.75, "military", "none", "interaction", (4, 4, 0, 0.05, 0.35, 0.40, 0.40, 0.35, 6, 2)),
    ("sidearm_fire", "Pistol_Shoot", "impact", 0, 1.00, "aggressive,alert", "fire,sidearm", "angry,tense", 0.70, 1.00, "military,action", "person", "confrontation", (2, 2, 0, 0.12, 0.55, 0.60, 0.10, 0.08, -2, 2)),
    ("wade_forward", "Swim_Fwd_Loop", "loco", 0, 0.80, "weary", "wade,forward", "tense", 0.35, 0.70, "industrial", "none", "locomotion", (8, 0, 0, 0.30, 0.25, 0.20, 0.22, 0.18, 4, 0)),
    ("tread_water", "Swim_Idle_Loop", "sustain", 0, 0.60, "weary", "tread,idle", "fear,weary", 0.25, 0.55, "horror", "none", "idle", (4, 2, 0, 0.05, 0.15, 0.10, 0.15, 0.10, 2, 2)),
    ("drive_wait", "Driving_Loop", "sustain", 0, 0.55, "focused", "drive,wait", "tense,focused", 0.20, 0.50, "corridor,observation_bay", "console", "idle", (2, 0, 0, 0.0, 0.20, 0.25, 0.20, 0.25, 2, 0)),
)

_CLIP_CACHE: tuple[Any, dict[str, "ClipRecord"]] | None = None


def default_library_paths() -> tuple[Path, Path]:
    """Return the on-disk standard and root-motion UAL glb paths."""
    root = Path(__file__).resolve().parents[1]
    base = root / "media" / "assets3d" / "cc0" / "quaternius_universal_animation_library"
    return base / "UAL1_Standard.glb", base / "UAL1_Standard_RM.glb"


@dataclass(frozen=True)
class ClipRecord:
    """One animation clip measured from the glTF JSON and the root bone track."""

    name: str
    duration_s: float
    root_motion: bool
    root_travel_m: float
    in_standard: bool
    in_root_motion_library: bool


@dataclass(frozen=True)
class ActionEntry:
    """One semantic dramatic action bound to a real UAL clip."""

    action_id: str
    base_clip: str
    personality_tags: tuple[str, ...]
    intent_tags: tuple[str, ...]
    emotions: tuple[str, ...]
    intensity_range: tuple[float, float]
    scene_tags: tuple[str, ...]
    interaction_targets: tuple[str, ...]
    duration_s: float
    phases: dict[str, dict[str, Any]]
    category: str = ""
    priority: int = 0
    root_motion: bool = False
    family: str = "gesture"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready description, including the four-phase envelope."""
        return {
            "action_id": self.action_id,
            "base_clip": self.base_clip,
            "personality_tags": list(self.personality_tags),
            "intent_tags": list(self.intent_tags),
            "emotions": list(self.emotions),
            "intensity_range": [self.intensity_range[0], self.intensity_range[1]],
            "scene_tags": list(self.scene_tags),
            "interaction_targets": list(self.interaction_targets),
            "duration_s": self.duration_s,
            "root_motion": self.root_motion,
            "category": self.category,
            "phases": self.phases,
        }


def _load_glb(path: Path) -> tuple[dict[str, Any], bytes]:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError(f"{path} is too small to be a glb")
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"{path} is not a binary glTF")
    offset = 12
    document: dict[str, Any] | None = None
    blob = b""
    while offset + 8 <= len(data):
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        start = offset + 8
        chunk = data[start:start + chunk_len]
        offset = start + chunk_len
        if chunk_type == JSON_CHUNK:
            document = json.loads(chunk.decode("utf-8").rstrip("\x00").strip())
        elif chunk_type == BIN_CHUNK:
            blob = chunk
    if document is None:
        raise ValueError(f"{path} has no JSON chunk")
    return document, blob


def _read_floats(document: dict[str, Any], blob: bytes, accessor_index: int, components: int) -> list[float]:
    accessor = document["accessors"][accessor_index]
    if int(accessor.get("componentType", 5126)) != 5126:
        raise ValueError(f"accessor {accessor_index} is not float32")
    view = document["bufferViews"][accessor["bufferView"]]
    start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
    stride = int(view.get("byteStride", components * 4))
    count = int(accessor["count"])
    values: list[float] = []
    for index in range(count):
        base = start + index * stride
        values.extend(struct.unpack_from("<" + "f" * components, blob, base))
    return values


def _accessor_max_scalar(document: dict[str, Any], blob: bytes, accessor_index: int) -> float:
    accessor = document["accessors"][accessor_index]
    if accessor.get("max") is not None:
        raw = accessor["max"]
        return float(raw[0] if isinstance(raw, (list, tuple)) else raw)
    values = _read_floats(document, blob, accessor_index, 1)
    return max(values) if values else 0.0


def _vec3_span_xz(document: dict[str, Any], blob: bytes, accessor_index: int) -> float:
    values = _read_floats(document, blob, accessor_index, 3)
    if not values:
        return 0.0
    xs = values[0::3]
    zs = values[2::3]
    return math.hypot(max(xs) - min(xs), max(zs) - min(zs))


def _measure_animations(path: Path) -> dict[str, tuple[float, float]]:
    """Map clip name to (duration seconds, root XZ travel meters)."""
    document, blob = _load_glb(path)
    nodes = document.get("nodes") or []
    measured: dict[str, tuple[float, float]] = {}
    for anim in document.get("animations") or []:
        name = str(anim.get("name") or "")
        if not name:
            continue
        duration = 0.0
        for sampler in anim.get("samplers") or []:
            duration = max(duration, _accessor_max_scalar(document, blob, sampler["input"]))
        travel = 0.0
        for channel in anim.get("channels") or []:
            target = channel.get("target") or {}
            if target.get("path") != "translation":
                continue
            node_index = target.get("node")
            if not isinstance(node_index, int) or node_index >= len(nodes):
                continue
            if nodes[node_index].get("name") != "root":
                continue
            sampler = anim["samplers"][channel["sampler"]]
            travel = max(travel, _vec3_span_xz(document, blob, sampler["output"]))
        measured[name] = (round(duration, 4), round(travel, 4))
    return measured


def load_clip_index(standard: Path | None = None, root_motion: Path | None = None) -> dict[str, ClipRecord]:
    """Parse both UAL libraries and cache the clip index for the process."""
    global _CLIP_CACHE
    std_path, rm_path = default_library_paths()
    standard = Path(standard) if standard is not None else std_path
    root_motion = Path(root_motion) if root_motion is not None else rm_path
    if not standard.is_file() or not root_motion.is_file():
        raise FileNotFoundError(f"UAL libraries missing: {standard} / {root_motion}")
    key = (str(standard.resolve()), str(root_motion.resolve()), standard.stat().st_mtime_ns, root_motion.stat().st_mtime_ns)
    if _CLIP_CACHE is not None and _CLIP_CACHE[0] == key:
        return _CLIP_CACHE[1]
    standard_map = _measure_animations(standard)
    motion_map = _measure_animations(root_motion)
    records: dict[str, ClipRecord] = {}
    for name in sorted(set(standard_map) | set(motion_map)):
        std = standard_map.get(name)
        mot = motion_map.get(name)
        duration = (std or mot)[0]
        travel = mot[1] if mot is not None else 0.0
        records[name] = ClipRecord(
            name=name,
            duration_s=duration,
            root_motion=travel > ROOT_MOTION_MIN_M,
            root_travel_m=travel,
            in_standard=std is not None,
            in_root_motion_library=mot is not None,
        )
    _CLIP_CACHE = (key, records)
    return records


def _split_tags(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(raw).split(",") if part.strip())


def _clamp(value: float, bounds: list[float]) -> float:
    return max(bounds[0], min(bounds[1], value))


def build_phases(
    impact: tuple[float, ...],
    family: str,
    gain: float,
    intensity: float,
    intensity_range: tuple[float, float],
) -> dict[str, dict[str, Any]]:
    """Build the four-phase envelope for one impact pose.

    Prepare is the opposite wind-up. Execute is the impact. Follow-through
    keeps the same direction at a lower amplitude. Recover leaves a small
    residue instead of swinging back through center.
    """
    family_key = family if family in _TIMING else "gesture"
    lo, hi = intensity_range
    if hi <= lo:
        span = 1.0
    else:
        span = (float(intensity) - lo) / (hi - lo)
    span = max(0.0, min(1.0, span))
    amount = float(gain) * (0.72 + 0.38 * span)
    limits = {key: list(values) for key, values in _LIMITS[family_key].items()}
    timing = _TIMING[family_key]
    weights = _WEIGHTS[family_key]
    scales = _SCALES[family_key]
    labels = (
        ("torso_pitch", 0, "spine_pitch_deg"),
        ("torso_yaw", 1, "spine_yaw_deg"),
        ("torso_roll", 2, "spine_roll_deg"),
        ("weight_shift", 3, "weight_shift"),
        ("right_raise", 4, "arm_raise"),
        ("right_reach", 5, "arm_reach"),
        ("left_raise", 6, "arm_raise"),
        ("left_reach", 7, "arm_reach"),
        ("head_pitch", 8, "head_pitch_deg"),
        ("head_yaw", 9, "head_yaw_deg"),
    )
    phases: dict[str, dict[str, Any]] = {}
    for name, (t0, t1) in zip(PHASE_NAMES, timing):
        scale = scales[name] * amount
        posed: dict[str, float] = {}
        for label, index, limit_key in labels:
            raw = float(impact[index]) * scale if index < len(impact) else 0.0
            posed[label] = round(_clamp(raw, limits[limit_key]), 3)
        right_raise = posed["right_raise"]
        left_raise = posed["left_raise"]
        right_reach = posed["right_reach"]
        left_reach = posed["left_reach"]
        phases[name] = {
            "t0": t0,
            "t1": t1,
            "weight": weights[name],
            "torso": {
                "pitch": posed["torso_pitch"],
                "yaw": posed["torso_yaw"],
                "roll": posed["torso_roll"],
                "weight_shift": posed["weight_shift"],
            },
            "arm": {
                "raise": round(max(right_raise, left_raise), 3),
                "reach": round(max(right_reach, left_reach), 3),
                "right": {"raise": right_raise, "reach": right_reach},
                "left": {"raise": left_raise, "reach": left_reach},
            },
            "head": {"pitch": posed["head_pitch"], "yaw": posed["head_yaw"]},
            "joint_limits": {key: list(values) for key, values in limits.items()},
        }
    return phases


def _row_to_entry(row: tuple[Any, ...], clips: dict[str, ClipRecord]) -> ActionEntry:
    action_id, clip_name, family, priority, gain, personality, intents, emotions, lo, hi, scenes, targets, category, impact = row
    if clip_name not in clips:
        raise KeyError(f"action {action_id} references unknown clip {clip_name}")
    clip = clips[clip_name]
    lo_f, hi_f = float(lo), float(hi)
    midpoint = (lo_f + hi_f) * 0.5
    phases = build_phases(tuple(impact), str(family), float(gain), midpoint, (lo_f, hi_f))
    return ActionEntry(
        action_id=str(action_id),
        base_clip=str(clip_name),
        personality_tags=_split_tags(personality),
        intent_tags=_split_tags(intents),
        emotions=_split_tags(emotions),
        intensity_range=(lo_f, hi_f),
        scene_tags=_split_tags(scenes),
        interaction_targets=_split_tags(targets),
        duration_s=clip.duration_s,
        phases=phases,
        category=str(category),
        priority=int(priority),
        root_motion=clip.root_motion,
        family=str(family),
    )


class SemanticAnimationLibrary:
    """Index of real UAL clips plus the semantic dramatic action catalog.

    ``query`` is deterministic: the same arguments always return the same
    ranking. Scores prefer an exact action id, then a leading intent tag,
    then emotion, personality, scene, target, and intensity fit.
    """

    def __init__(
        self,
        standard_path: Path | None = None,
        root_motion_path: Path | None = None,
        clips: dict[str, ClipRecord] | None = None,
    ) -> None:
        self.standard_path, self.root_motion_path = default_library_paths()
        if standard_path is not None:
            self.standard_path = Path(standard_path)
        if root_motion_path is not None:
            self.root_motion_path = Path(root_motion_path)
        self.clips: dict[str, ClipRecord] = clips if clips is not None else load_clip_index(
            self.standard_path, self.root_motion_path
        )
        self.entries: list[ActionEntry] = [_row_to_entry(row, self.clips) for row in _ROWS]
        self.by_id: dict[str, ActionEntry] = {}
        for entry in self.entries:
            if entry.action_id in self.by_id:
                raise ValueError(f"duplicate action_id {entry.action_id}")
            self.by_id[entry.action_id] = entry
        missing = [action_id for action_id in REQUIRED_ACTION_IDS if action_id not in self.by_id]
        if missing:
            raise RuntimeError(f"catalog is missing required actions: {missing}")
        if len(self.entries) < 100:
            raise RuntimeError(f"catalog has {len(self.entries)} actions; need at least 100")
        self._impacts: dict[str, tuple[Any, ...]] = {}
        for row in _ROWS:
            self._impacts[str(row[0])] = row

    def root_motion_clip_names(self) -> list[str]:
        """Clip names whose root-motion library track actually travels."""
        return sorted(name for name, clip in self.clips.items() if clip.root_motion)

    def catalog_summary(self) -> dict[str, Any]:
        """Counts, clip durations, and which clips carry root motion."""
        categories: dict[str, int] = {}
        for entry in self.entries:
            categories[entry.category] = categories.get(entry.category, 0) + 1
        return {
            "libraries": {
                "standard": self.standard_path.as_posix(),
                "root_motion": self.root_motion_path.as_posix(),
            },
            "clip_count": len(self.clips),
            "action_count": len(self.entries),
            "root_motion_clips": self.root_motion_clip_names(),
            "inplace_clips": sorted(name for name, clip in self.clips.items() if not clip.root_motion),
            "categories": categories,
            "required_actions_present": all(action_id in self.by_id for action_id in REQUIRED_ACTION_IDS),
            "clips": {
                name: {
                    "duration_s": clip.duration_s,
                    "root_motion": clip.root_motion,
                    "root_travel_m": clip.root_travel_m,
                    "in_standard": clip.in_standard,
                    "in_root_motion_library": clip.in_root_motion_library,
                }
                for name, clip in self.clips.items()
            },
        }

    def query(
        self,
        intent: str | None = None,
        emotion: str | None = None,
        personality: str | None = None,
        intensity: float | None = None,
        scene_type: str | None = None,
        target: str | None = None,
    ) -> list[ActionEntry]:
        """Return every action, best match first. Ranking is stable."""
        intent_l = (intent or "").strip().lower()
        emotion_l = (emotion or "").strip().lower()
        personality_l = (personality or "").strip().lower()
        scene_l = (scene_type or "").strip().lower()
        target_l = (target or "").strip().lower()
        ranked: list[tuple[float, int, str, ActionEntry]] = []
        for entry in self.entries:
            score = 0.0
            tags = entry.intent_tags
            if intent_l:
                if intent_l == entry.action_id:
                    score += 12.0
                elif entry.action_id.startswith(intent_l + "_"):
                    score += 6.0
                if intent_l in tags:
                    score += 4.0
                    if tags and tags[0] == intent_l:
                        score += 3.0
                elif any(intent_l in tag or tag in intent_l for tag in tags):
                    score += 1.5
            if emotion_l:
                if emotion_l in entry.emotions:
                    score += 2.2
                elif any(emotion_l in item or item in emotion_l for item in entry.emotions):
                    score += 0.8
            if personality_l and personality_l in entry.personality_tags:
                score += 1.6
            if scene_l and scene_l in entry.scene_tags:
                score += 1.3
            if target_l:
                if target_l in entry.interaction_targets:
                    score += 1.8
                elif any(target_l in item or item in target_l for item in entry.interaction_targets):
                    score += 0.7
            if intensity is not None:
                lo, hi = entry.intensity_range
                value = float(intensity)
                if lo <= value <= hi:
                    score += 1.0
                else:
                    dist = (lo - value) if value < lo else (value - hi)
                    score -= min(2.0, dist * 2.0)
            score += entry.priority * 0.35
            ranked.append((score, entry.priority, entry.action_id, entry))
        ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        return [item[3] for item in ranked]

    def export_godot_spec(
        self,
        action_id: str,
        intensity: float | None = None,
        target: str = "",
    ) -> dict[str, Any]:
        """Phase spec consumed by ``stage3d.json`` dramatic_actions.

        ``from`` / ``to`` are filled by the performance planner; this payload
        is the action identity, the verified clip, and the four phases.
        """
        entry = self.by_id[action_id]
        row = self._impacts[action_id]
        gain = float(row[4])
        impact = tuple(row[13])
        if intensity is None:
            intensity = (entry.intensity_range[0] + entry.intensity_range[1]) * 0.5
        phases = build_phases(impact, entry.family, gain, float(intensity), entry.intensity_range)
        return {
            "action_id": entry.action_id,
            "base_clip": entry.base_clip,
            "intensity": round(float(intensity), 3),
            "target": str(target or ""),
            "root_motion": entry.root_motion,
            "duration_s": entry.duration_s,
            "phases": phases,
        }
