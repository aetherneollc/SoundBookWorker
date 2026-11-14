import unittest

from renderer.prepare_remotion import ensure_stage_contract


class PrepareRemotionTests(unittest.TestCase):
    def test_repairs_old_generic_layout_from_control_room_location(self):
        shot = {
            'scene_title': '确认姿态读数',
            'location': '地球停泊轨道遥测阶段',
            'description': '周岳在控制台前操作平板，重新排列时间戳。',
            'pixel_story': True,
            'cartoon': {
                'world': {'room': 'exterior', 'light': 'day'},
                'actors': [{'name': '林岚', 'action': 'point'}, {'name': '周岳', 'action': 'work'}],
                'action_event': {'kind': 'reach', 'subject': 'character', 'target': 'panel',
                                 'start': .08, 'contact': .52, 'end': .92,
                                 'from_state': 'ready', 'to_state': 'changed'},
                'stage_layout': {
                    'version': 1, 'coordinate_space': {'width': 320, 'height': 180},
                    'dimensions': {'actor_height': 62, 'rover_width': 56, 'rover_height': 38},
                    'floor': {'far_y': 138, 'near_y': 162, 'vanishing_x': .5},
                    'actors': {'林岚': {'x': .2, 'depth': .5, 'surface': 'floor'}},
                    'rover': {'x': .6, 'depth': .5, 'surface': 'floor'},
                    'setpiece': {'id': 'generic', 'depth': .55},
                },
            },
        }

        ensure_stage_contract(shot)

        self.assertEqual(shot['cartoon']['world']['room'], 'interior')
        self.assertEqual(shot['cartoon']['world']['light'], 'lamp')
        self.assertEqual(shot['cartoon']['action_event']['target'], 'tablet')
        self.assertEqual(shot['cartoon']['action_event']['actor_name'], '周岳')

    def test_orbital_cabin_generic_layout_becomes_spacecraft_stage(self):
        shot = {
            'scene_title': '在轨检查', 'location': '月球轨道',
            'description': '阿拓 AT-1 在航天器内部检查星敏感器。', 'pixel_story': True,
            'cartoon': {
                'world': {'room': 'exterior', 'light': 'day'}, 'actors': [],
                'stage_layout': {'setpiece': {'id': 'generic'}},
            },
        }

        ensure_stage_contract(shot)

        self.assertEqual(shot['cartoon']['world']['room'], 'interior')
        self.assertEqual(shot['cartoon']['stage_layout']['setpiece']['id'], 'orbit')

    def test_ground_control_orbit_telemetry_is_not_mistaken_for_spacecraft_view(self):
        shot = {
            'scene_title': '姿态读数', 'location': '地球停泊轨道遥测阶段',
            'description': '林岚在地面控制室主屏幕前按下 HOLD。', 'pixel_story': True,
            'cartoon': {
                'world': {'room': 'exterior', 'light': 'day'}, 'actors': [{'name': '林岚'}],
                'stage_layout': {'setpiece': {'id': 'generic'}},
            },
        }

        ensure_stage_contract(shot)

        self.assertEqual(shot['cartoon']['stage_layout']['setpiece']['id'], 'generic')
        self.assertEqual(shot['cartoon']['world']['room'], 'interior')


if __name__ == '__main__':
    unittest.main()
