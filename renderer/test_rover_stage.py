"""End-to-end AT-1 rig, director, and stage preparation coverage."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from renderer.director import validate_stage
from renderer.prepare_stage import prepare


def _make_rover(path: Path) -> list[list[float]]:
    width, height = 800, 500
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    wheel_specs = [(145, 405, 55), (315, 405, 55), (500, 405, 55), (670, 405, 55)]
    for cx, cy, radius in wheel_specs:
        draw.line((cx, 292, cx + (-18 if cx < width / 2 else 18), 350, cx, cy),
                  fill=(157, 190, 199, 255), width=22, joint='curve')
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                     fill=(22, 32, 43, 255), outline=(124, 164, 179, 255), width=8)
        draw.ellipse((cx - 29, cy - 29, cx + 29, cy + 29),
                     fill=(32, 82, 112, 255), outline=(226, 235, 231, 255), width=6)
        draw.line((cx, cy, cx + 36, cy), fill=(247, 157, 55, 255), width=7)
    draw.polygon(
        [(92, 220), (190, 167), (605, 167), (708, 220), (680, 325), (120, 325)],
        fill=(211, 226, 226, 255),
        outline=(22, 52, 70, 255),
    )
    draw.rectangle((230, 200, 565, 293), fill=(37, 83, 105, 255),
                   outline=(91, 180, 204, 255), width=6)
    draw.rectangle((318, 146, 502, 183), fill=(18, 54, 78, 255),
                   outline=(179, 211, 215, 255), width=5)
    draw.line((400, 167, 400, 89), fill=(213, 230, 229, 255), width=12)
    draw.rounded_rectangle((351, 53, 449, 111), radius=16, fill=(221, 231, 229, 255),
                           outline=(23, 57, 77, 255), width=6)
    draw.ellipse((380, 67, 420, 107), fill=(244, 151, 43, 255),
                 outline=(26, 65, 87, 255), width=5)
    image.save(path)
    return [[cx / width, cy / height, radius / height] for cx, cy, radius in wheel_specs]


def _make_arm(path: Path) -> None:
    image = Image.new('RGBA', (640, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((48, 118, 104, 174), fill=(35, 75, 99, 255),
                 outline=(212, 229, 228, 255), width=6)
    draw.polygon([(82, 128), (290, 67), (310, 104), (101, 164)],
                 fill=(205, 220, 220, 255), outline=(28, 62, 80, 255))
    draw.ellipse((273, 61, 327, 115), fill=(30, 76, 105, 255),
                 outline=(229, 157, 58, 255), width=6)
    draw.polygon([(306, 79), (511, 149), (493, 188), (291, 112)],
                 fill=(196, 216, 219, 255), outline=(26, 60, 79, 255))
    draw.line((497, 169, 565, 139), fill=(190, 219, 220, 255), width=12)
    draw.line((497, 169, 563, 204), fill=(190, 219, 220, 255), width=12)
    image.save(path)


class RoverStageTests(unittest.TestCase):
    def test_prepare_builds_and_caches_rover_rig_and_directs_actions(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            rover_master = work / 'at1.png'
            arm_master = work / 'at1-arm.png'
            hints = _make_rover(rover_master)
            _make_arm(arm_master)
            manifest = {
                'version': 1,
                'renderer': 'godot2d',
                'width': 1280,
                'height': 720,
                'fps': 24,
                'timeline_mode': 'authored',
                'robot_rig': {
                    'master_file': str(rover_master),
                    'arm_file': str(arm_master),
                    'wheel_hints': hints,
                    'native_facing': 'right',
                },
                'shots': [
                    {
                        'scene_number': 1,
                        'shot_number': 1,
                        'duration': 3,
                        'rover_action': 'drive',
                        'rover': {
                            'from_x': 0.68,
                            'to_x': 0.42,
                            'facing': 'left',
                            'depth': 'mid',
                            'target_x': 0.30,
                            'target_y': 0.58,
                        },
                    },
                    {
                        'scene_number': 1,
                        'shot_number': 2,
                        'duration': 2,
                        'story_motion': {
                            'framing': 'wide',
                            'rover_action': 'sample',
                            'rover': {'x': 0.42, 'facing': 'right', 'depth': 'near'},
                            'actors': [],
                        },
                    },
                ],
            }
            manifest_path = work / 'manifest.json'
            stage_path = work / 'stage.json'
            cache = work / 'rig-cache'
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

            stage = prepare(manifest_path, stage_path, cache)
            self.assertEqual(validate_stage(stage), [])
            self.assertEqual(stage['rigs'], {})
            self.assertEqual(json.loads(stage_path.read_text(encoding='utf-8')), stage)

            robot_rig_path = Path(stage['robot_rig']['rig_json'])
            self.assertTrue(robot_rig_path.is_file())
            robot_rig = json.loads(robot_rig_path.read_text(encoding='utf-8'))
            self.assertEqual(robot_rig['level'], 'wheeled')
            self.assertEqual(len(robot_rig['wheels']), 4)
            self.assertIn('arm', robot_rig)
            for part in [robot_rig['body_file'], robot_rig['arm']['file'],
                         *(wheel['file'] for wheel in robot_rig['wheels'])]:
                self.assertTrue(Path(part).is_file(), part)

            drive, inspect = stage['shots']
            self.assertEqual(drive['rover'], {
                'action': 'drive',
                'from_x': 0.68,
                'to_x': 0.42,
                'facing': 'left',
                'depth': 'mid',
                'target_x': 0.3,
                'target_y': 0.58,
                'active': True,
            })
            self.assertEqual(inspect['rover']['action'], 'inspect')
            self.assertEqual((inspect['rover']['from_x'], inspect['rover']['to_x']), (0.42, 0.42))
            self.assertEqual(inspect['rover']['depth'], 'near')

            stamped = robot_rig_path.stat().st_mtime_ns
            again = prepare(manifest_path, stage_path, cache)
            self.assertEqual(validate_stage(again), [])
            self.assertEqual(Path(again['robot_rig']['rig_json']), robot_rig_path)
            self.assertEqual(robot_rig_path.stat().st_mtime_ns, stamped)

    def test_validator_rejects_bad_rover_contract(self):
        stage = {
            'version': 1,
            'width': 1280,
            'height': 720,
            'fps': 24,
            'total_frames': 24,
            'rigs': {},
            'shots': [{
                'id': 's1_sh1',
                'from': 0,
                'frames': 24,
                'actors': [],
                'beats': [],
                'cuts': [{'from': 0, 'to': 24, 'framing': 'wide',
                          'focus_actor': '', 'camera_move': 'static'}],
                'rover': {
                    'action': 'teleport',
                    'from_x': -1,
                    'to_x': 2,
                    'facing': 'front',
                    'depth': 'huge',
                    'target_x': 4,
                    'target_y': -2,
                    'active': 'yes',
                },
            }],
            'speech': [],
            'captions': [],
        }
        problems = validate_stage(stage)
        self.assertTrue(any('top-level robot_rig' in problem for problem in problems))
        self.assertTrue(any('unknown rover action' in problem for problem in problems))
        self.assertTrue(any('rover active must be boolean' in problem for problem in problems))


if __name__ == '__main__':
    unittest.main()
