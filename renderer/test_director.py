import json
import unittest
from pathlib import Path

try:
    from renderer import director
except ImportError:  # Direct execution from inside renderer/.
    import director  # type: ignore

FPS = 24
MIN_CUT = 28
RIGS = {
    'linlan': {'rig_json': 'media/rigs/linlan/rig.json', 'stature_scale': 0.93, 'name': '林岚'},
    'chen': {'rig_json': 'media/rigs/chen/rig.json', 'name': '陈工'},
    'zhou': {'rig_json': 'media/rigs/zhou/rig.json', 'name': '周岳'},
}
CUES = [{'start': 0.0, 'end': 0.12, 'value': 'X'}, {'start': 0.12, 'end': 0.3, 'value': 'D'},
        {'start': 0.3, 'end': 0.5, 'value': 'B'}]


def actor(name, character_id, x, action='idle', to_x=None, **extra):
    return {'name': name, 'character_id': character_id, 'action': action,
            'from_x': x, 'to_x': x if to_x is None else to_x, **extra}


def linlan(x=0.32, action='talk', **extra):
    return actor('林岚', 'linlan', x, action, facing='right', depth='mid', **extra)


def chen(x=0.68, action='listen', **extra):
    return actor('陈工', 'chen', x, action, facing='left', depth='mid', **extra)


def shot(start, frames, actors, scene=1, framing='wide', **extra):
    return {'scene_number': scene, 'from': start, 'frames': frames,
            'story_motion': {'framing': framing, 'camera': '', 'actors': actors}, **extra}


def line(speaker, start, end, text='', **extra):
    return {'speaker': speaker, 'from': start, 'to': end, 'text': text, 'cues': CUES, **extra}


def build(shots, speech=(), captions=(), audio=(), rigs=RIGS, manifest=None, intro=None, cta=None):
    return director.build_stage(manifest or {'version': 1, 'fps': FPS}, list(shots), list(speech),
                                list(captions), rigs, list(audio), intro, cta)


def beats_of(stage_shot, actor_id, clip=None):
    return [beat for beat in stage_shot['beats']
            if beat['actor'] == actor_id and (clip is None or beat['clip'] == clip)]


def cut_plan(stage_shot):
    return [(cut['framing'], cut['focus_actor']) for cut in stage_shot['cuts']]


class StageAssertions(unittest.TestCase):
    def assertPlayable(self, stage):
        self.assertEqual(director.validate_stage(stage), [])
        json.dumps(stage)

    def assertCutsTile(self, stage_shot, min_cut=MIN_CUT):
        cuts = stage_shot['cuts']
        self.assertEqual(cuts[0]['from'], 0)
        self.assertEqual(cuts[-1]['to'], stage_shot['frames'])
        for previous, current in zip(cuts, cuts[1:]):
            self.assertEqual(previous['to'], current['from'])
        if len(cuts) > 1:
            for cut in cuts:
                self.assertGreaterEqual(cut['to'] - cut['from'], min_cut, cut)


class ActionMappingTests(unittest.TestCase):
    def test_every_legacy_action_maps_to_a_motion_clip(self):
        expected = {'idle': 'idle', 'listen': 'listen', 'talk': 'talk_calm', 'work': 'talk_explain',
                    'point': 'point', 'wave': 'wave', 'react': 'react', 'console': 'point'}
        for action, clip in expected.items():
            self.assertEqual(director.map_action(action), clip, action)
        for action in ('idle', 'listen', 'walk', 'work', 'point', 'wave', 'talk', 'react', 'carry', 'console'):
            self.assertIn(action, director.LEGACY_ACTION_CLIPS)
            for displacement in (0.0, 0.08, 0.2):
                self.assertIn(director.map_action(action, displacement), director.CLIPS)

    def test_walk_and_carry_are_staged_by_displacement(self):
        for action in ('walk', 'carry'):
            self.assertEqual(director.map_action(action, 0.2), 'walk_medium')
            self.assertEqual(director.map_action(action, -0.15), 'walk_medium')
            self.assertEqual(director.map_action(action, 0.14), 'step_shift')
            self.assertEqual(director.map_action(action, -0.05), 'step_shift')
            self.assertEqual(director.map_action(action, 0.03), 'idle')
            self.assertEqual(director.map_action(action), 'idle')

    def test_clip_names_pass_through_and_unknown_actions_idle(self):
        self.assertEqual(director.map_action('talk_emphatic'), 'talk_emphatic')
        self.assertEqual(director.map_action(' NOD '), 'nod')
        self.assertEqual(director.map_action('moonwalk'), 'idle')
        self.assertEqual(director.map_action(None), 'idle')

    def test_clip_table_matches_the_godot_motion_library(self):
        motions = Path(__file__).resolve().parents[1] / 'godot' / 'motions'
        files = sorted(motions.glob('*.json'))
        if not files:
            self.skipTest('godot/motions is not present')
        durations = {path.stem: float(json.loads(path.read_text(encoding='utf-8'))['duration']) for path in files}
        self.assertEqual(durations, director.CLIP_SECONDS)


class DialogueCutTests(StageAssertions):
    def dialogue(self):
        speech = [line('林岚', 74, 146, '信号比昨天稳定。'), line('陈工', 153, 230, '延迟还是偏大。'),
                  line('林岚', 237, 300, '我再核对一遍。'), line('陈工', 307, 380, '好，我等你的结果。')]
        return build([shot(68, 400, [linlan(), chen()])], speech)

    def test_opens_wide_then_cuts_between_speakers(self):
        stage = self.dialogue()
        stage_shot = stage['shots'][0]
        self.assertPlayable(stage)
        self.assertCutsTile(stage_shot)
        opening = stage_shot['cuts'][0]
        self.assertEqual((opening['framing'], opening['focus_actor']), ('wide', ''))
        self.assertTrue(round(1.8 * FPS) <= opening['to'] <= round(2.2 * FPS), opening)
        self.assertEqual(cut_plan(stage_shot)[1:5], [('medium', 'linlan'), ('medium', 'chen'),
                                                     ('medium', 'linlan'), ('medium', 'chen')])

    def test_cut_lands_on_the_frame_the_new_speaker_starts(self):
        stage_shot = self.dialogue()['shots'][0]
        starts = [cut['from'] for cut in stage_shot['cuts'][2:5]]
        self.assertEqual(starts, [153 - 68, 237 - 68, 307 - 68])

    def test_first_line_inside_the_window_ends_the_establishing_shot(self):
        stage_shot = build([shot(0, 240, [linlan(), chen()])], [line('陈工', 50, 200, '开始吧。')])['shots'][0]
        self.assertEqual(stage_shot['cuts'][0]['to'], 50)
        self.assertEqual(cut_plan(stage_shot)[1], ('medium', 'chen'))

    def test_later_shot_of_a_scene_establishes_with_a_two_shot(self):
        stage = build([shot(0, 60, [linlan(), chen()]), shot(60, 200, [linlan(), chen()])],
                      [line('林岚', 70, 150, '继续。')])
        self.assertEqual(cut_plan(stage['shots'][0]), [('wide', '')])
        self.assertEqual(cut_plan(stage['shots'][1])[0], ('two_shot', ''))

    def test_short_shot_goes_straight_to_the_speaker(self):
        # Under 4.5 s there is no room for an establishing shot plus a cut-in.
        stage = build([shot(0, 60, [linlan(), chen()]), shot(60, 90, [linlan(), chen()])],
                      [line('陈工', 64, 140, '收到。')])
        self.assertEqual(cut_plan(stage['shots'][1]), [('medium', 'chen')])
        self.assertPlayable(stage)

    def test_rapid_exchange_never_cuts_faster_than_the_minimum(self):
        speech = []
        for index in range(10):
            start = 60 + index * 15
            speech.append(line('林岚' if index % 2 == 0 else '陈工', start, start + 12, '嗯。'))
        stage = build([shot(0, 240, [linlan(), chen()])], speech)
        stage_shot = stage['shots'][0]
        self.assertPlayable(stage)
        self.assertCutsTile(stage_shot)
        self.assertLess(len(stage_shot['cuts']), len(speech))
        self.assertTrue(all(cut['to'] - cut['from'] >= MIN_CUT for cut in stage_shot['cuts']))

    def test_minimum_cut_scales_with_frame_rate(self):
        speech = [line('林岚' if index % 2 == 0 else '陈工', 70 + index * 20, 86 + index * 20, '嗯。')
                  for index in range(8)]
        stage = build([shot(0, 300, [linlan(), chen()])], speech, manifest={'fps': 30})
        self.assertCutsTile(stage['shots'][0], min_cut=35)
        self.assertEqual(director.validate_stage(stage), [])

    def test_strong_emotion_and_short_emphatic_lines_cut_close(self):
        speech = [line('林岚', 60, 130, '你怎么能擅自改参数。', emotion='愤怒'),
                  line('陈工', 140, 170, '住手！'),
                  line('林岚', 200, 290, '那就按原计划执行。')]
        stage_shot = build([shot(0, 320, [linlan(), chen()])], speech)['shots'][0]
        plan = cut_plan(stage_shot)
        self.assertIn(('close', 'linlan'), plan)
        self.assertIn(('close', 'chen'), plan)
        self.assertCutsTile(stage_shot)

    def test_camera_moves_rotate_without_repeating(self):
        stage = self.dialogue()
        moves = [cut['camera_move'] for stage_shot in stage['shots'] for cut in stage_shot['cuts']]
        self.assertTrue(set(moves) <= set(director.CAMERA_MOVES))
        self.assertGreaterEqual(len(set(moves)), 3)
        for previous, current in zip(moves, moves[1:]):
            self.assertNotEqual(previous, current)

    def test_story_camera_text_sets_the_opening_move(self):
        timeline = shot(0, 200, [linlan(), chen()])
        timeline['story_motion']['camera'] = '缓慢向左摇镜'
        self.assertEqual(build([timeline])['shots'][0]['cuts'][0]['camera_move'], 'slow_pan_left')

    def test_stage_steps_back_when_the_talk_stops(self):
        stage_shot = build([shot(0, 300, [linlan(), chen()])], [line('林岚', 50, 120, '就这样。')])['shots'][0]
        self.assertEqual(cut_plan(stage_shot), [('wide', ''), ('medium', 'linlan'), ('wide', '')])
        self.assertEqual(stage_shot['cuts'][2]['from'], 132)


class WalkTests(StageAssertions):
    def test_long_walk_becomes_a_waist_up_medium_with_scrolling_set(self):
        walker = actor('林岚', 'linlan', 0.2, 'walk', to_x=0.4, facing='right')
        stage = build([shot(0, 240, [walker, chen()])], [line('林岚', 80, 160, '我来了。')])
        self.assertPlayable(stage)
        walk, rest = stage['shots']
        self.assertEqual((walk['id'], walk['from'], rest['id'], rest['from']), ('s1_sh1', 0, 's1_sh1b', walk['frames']))
        self.assertEqual(walk['frames'] + rest['frames'], 240)
        self.assertEqual(cut_plan(walk), [('medium', 'linlan')])
        self.assertEqual(walk['set']['walk_scroll_speed'], 38.0)
        steps = beats_of(walk, 'linlan')
        self.assertEqual(len(steps), 1)
        self.assertEqual((steps[0]['clip'], steps[0]['from'], steps[0]['to']), ('walk_medium', 0, walk['frames']))
        self.assertEqual(steps[0]['move_x'], [0.2, 0.4])
        # A whole number of walk cycles, so the stride does not break at the cut.
        self.assertEqual(walk['frames'] % round(director.CLIP_SECONDS['walk_medium'] * FPS), 0)

    def test_after_the_walk_the_actor_stands_at_the_destination(self):
        walker = actor('林岚', 'linlan', 0.2, 'walk', to_x=0.4, facing='right')
        rest = build([shot(0, 240, [walker, chen()])], [line('林岚', 80, 160, '我来了。')])['shots'][1]
        self.assertEqual(rest['set']['walk_scroll_speed'], 0.0)
        self.assertEqual(rest['actors'][0]['x'], 0.4)
        self.assertFalse(beats_of(rest, 'linlan', 'walk_medium'))
        self.assertTrue(all(beat['move_x'] == [0.4, 0.4] for beat in beats_of(rest, 'linlan')))
        self.assertTrue(beats_of(rest, 'linlan', 'talk_calm') or beats_of(rest, 'linlan', 'talk_explain'))

    def test_line_spoken_across_the_walk_cut_keeps_one_gesture(self):
        walker = actor('林岚', 'linlan', 0.2, 'walk', to_x=0.4, facing='right')
        stage = build([shot(0, 240, [walker, chen()])], [line('林岚', 30, 160, '数据在哪里？')])
        self.assertPlayable(stage)
        walk, rest = stage['shots']
        self.assertEqual([beat['clip'] for beat in beats_of(walk, 'linlan')], ['walk_medium'])
        spoken = [beat for beat in beats_of(rest, 'linlan') if beat['speaking']]
        self.assertEqual([(beat['clip'], beat['from'], beat['to']) for beat in spoken],
                         [('talk_explain', 0, 160 - walk['frames'])])
        self.assertEqual(stage['speech'][0]['from'], 30)

    def test_walking_left_scrolls_the_other_way(self):
        walker = actor('陈工', 'chen', 0.7, 'walk', to_x=0.5)
        walk = build([shot(0, 200, [walker])])['shots'][0]
        self.assertEqual(walk['set']['walk_scroll_speed'], -38.0)
        self.assertEqual(walk['actors'][0]['facing'], 'left')

    def test_short_shot_is_spent_entirely_on_the_walk(self):
        walker = actor('林岚', 'linlan', 0.2, 'walk', to_x=0.4)
        stage = build([shot(0, 70, [walker])])
        self.assertEqual(len(stage['shots']), 1)
        self.assertEqual(beats_of(stage['shots'][0], 'linlan')[0]['to'], 70)
        self.assertPlayable(stage)

    def test_small_reposition_is_a_step_shift_in_the_wide(self):
        mover = actor('林岚', 'linlan', 0.30, 'walk', to_x=0.38)
        stage = build([shot(0, 200, [mover, chen()])])
        self.assertPlayable(stage)
        self.assertEqual(len(stage['shots']), 1)
        stage_shot = stage['shots'][0]
        self.assertEqual(stage_shot['set']['walk_scroll_speed'], 0.0)
        steps = beats_of(stage_shot, 'linlan', 'step_shift')
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]['move_x'], [0.30, 0.38])
        self.assertFalse(beats_of(stage_shot, 'linlan', 'walk_medium'))
        self.assertEqual(stage_shot['cuts'][0]['framing'], 'wide')
        self.assertLessEqual(stage_shot['cuts'][0]['from'], steps[0]['from'])

    def test_actor_holds_the_new_position_after_a_step_shift(self):
        # Without a beat the player would snap the actor back to actors[].x.
        mover = actor('林岚', 'linlan', 0.30, 'walk', to_x=0.38)
        stage_shot = build([shot(0, 200, [mover, chen()])])['shots'][0]
        beats = beats_of(stage_shot, 'linlan')
        step = beats_of(stage_shot, 'linlan', 'step_shift')[0]
        covered = sorted((beat['from'], beat['to']) for beat in beats if beat['from'] >= step['from'])
        self.assertEqual(covered[-1][1], 200)
        for (_, end), (start, _) in zip(covered, covered[1:]):
            self.assertEqual(end, start)
        self.assertTrue(all(beat['move_x'] == [0.38, 0.38] for beat in beats if beat['from'] >= step['to']))

    def test_boundary_displacements(self):
        at_limit = build([shot(0, 200, [actor('林岚', 'linlan', 0.20, 'walk', to_x=0.34)])])
        self.assertEqual(len(at_limit['shots']), 1)
        self.assertTrue(beats_of(at_limit['shots'][0], 'linlan', 'step_shift'))
        tiny = build([shot(0, 200, [actor('林岚', 'linlan', 0.30, 'walk', to_x=0.32)])])['shots'][0]
        self.assertEqual(tiny['beats'], [])
        self.assertEqual(tiny['actors'][0]['x'], 0.30)


class ListenerTests(StageAssertions):
    def setUp(self):
        speech = [line('林岚', 60, 150, '这组数据说明温度在下降。'), line('陈工', 157, 200, '明白。')]
        self.stage = build([shot(0, 300, [linlan(), chen()])], speech)
        self.shot = self.stage['shots'][0]

    def test_listener_listens_and_looks_at_the_speaker(self):
        self.assertPlayable(self.stage)
        for frame in range(60, 150):
            beat = next(beat for beat in beats_of(self.shot, 'chen') if beat['from'] <= frame < beat['to'])
            self.assertIn(beat['clip'], ('listen', 'nod'))
            self.assertEqual(beat['look_at'], 'linlan')
            self.assertFalse(beat['speaking'])
        speaking = next(beat for beat in beats_of(self.shot, 'linlan') if beat['speaking'])
        self.assertEqual((speaking['from'], speaking['look_at']), (60, 'chen'))
        replies = [beat for beat in beats_of(self.shot, 'linlan') if beat['from'] >= 157 and beat['to'] <= 200]
        self.assertTrue(replies and all(beat['look_at'] == 'chen' and not beat['speaking'] for beat in replies))

    def test_listener_nods_once_near_the_end_of_a_long_line(self):
        nods = [beat for beat in beats_of(self.shot, 'chen', 'nod') if beat['to'] <= 150]
        self.assertEqual(len(nods), 1)
        self.assertEqual(nods[0]['to'] - nods[0]['from'], round(director.CLIP_SECONDS['nod'] * FPS))
        self.assertGreaterEqual(nods[0]['from'], 150 - 36)
        self.assertEqual(nods[0]['look_at'], 'linlan')

    def test_listener_nods_in_the_pause_after_a_short_line(self):
        nods = beats_of(self.shot, 'linlan', 'nod')
        self.assertEqual([nod['from'] for nod in nods], [200])

    def test_no_nod_answers_a_question(self):
        stage_shot = build([shot(0, 300, [linlan(), chen()])], [line('林岚', 60, 150, '你确定读数没有问题吗？')])['shots'][0]
        self.assertFalse(beats_of(stage_shot, 'chen', 'nod'))

    def test_lean_and_facing_point_at_the_partner(self):
        left, right = self.shot['actors']
        self.assertEqual((left['id'], left['lean'], left['facing']), ('linlan', 0.25, 'right'))
        self.assertEqual((right['id'], right['lean'], right['facing']), ('chen', -0.25, 'left'))
        self.assertEqual((left['base_clip'], right['base_clip']), ('idle', 'listen'))

    def test_single_actor_does_not_lean(self):
        stage_shot = build([shot(0, 100, [linlan()], framing='medium')])['shots'][0]
        self.assertEqual(stage_shot['actors'][0]['lean'], 0.0)

    def test_everyone_else_looks_at_the_speaker_in_a_group(self):
        cast = [linlan(0.2), chen(0.5), actor('周岳', 'zhou', 0.8, 'idle')]
        stage = build([shot(0, 200, cast)], [line('周岳', 60, 150, '我补充一点。')])
        self.assertPlayable(stage)
        stage_shot = stage['shots'][0]
        self.assertEqual([item['lean'] for item in stage_shot['actors']], [0.25, 0.0, -0.25])
        for listener in ('linlan', 'chen'):
            self.assertTrue(all(beat['look_at'] == 'zhou' for beat in beats_of(stage_shot, listener)))
        self.assertEqual(stage_shot['cuts'][0]['framing'], 'wide')


class NarrationTests(StageAssertions):
    def test_narration_keeps_captions_and_audio_without_driving_anyone(self):
        captions = [{'from': 20, 'to': 120, 'text': '旁白：控制室里只剩下仪器的声音。'}]
        audio = [{'file': 'media/voice_1.wav', 'start_ms': 833, 'volume': 1.0, 'loop': False}]
        stage = build([shot(0, 240, [linlan(), chen()])], [line('旁白', 20, 120, '控制室里只剩下仪器的声音。')],
                      captions, audio)
        self.assertPlayable(stage)
        stage_shot = stage['shots'][0]
        self.assertEqual(stage['speech'], [])
        self.assertEqual(stage['captions'], captions)
        self.assertEqual(stage['audio'], audio)
        self.assertFalse(any(beat['speaking'] for beat in stage_shot['beats']))
        self.assertTrue(all(beat['clip'] in ('idle', 'listen') for beat in stage_shot['beats']))
        self.assertEqual([item['base_clip'] for item in stage_shot['actors']], ['idle', 'listen'])
        self.assertCutsTile(stage_shot)

    def test_speaker_who_is_not_in_the_shot_is_ignored_on_stage(self):
        speech = [line('阿拓 AT-1', 10, 80, '自检完成。'), line('周岳', 90, 150, '收到。'),
                  line('林岚', 160, 230, '开始记录。')]
        stage = build([shot(0, 240, [linlan(), chen()])], speech)
        self.assertPlayable(stage)
        self.assertEqual([entry['actor'] for entry in stage['speech']], ['linlan'])
        speakers = {beat['actor'] for beat in stage['shots'][0]['beats'] if beat['speaking']}
        self.assertEqual(speakers, {'linlan'})

    def test_shot_without_cast_or_story_motion_still_plays(self):
        empty = {'scene_number': 2, 'from': 0, 'frames': 120, 'src': 'media/shot.png'}
        stage = build([empty], [line('旁白', 5, 100, '月面一片寂静。')])
        self.assertPlayable(stage)
        stage_shot = stage['shots'][0]
        self.assertEqual((stage_shot['actors'], stage_shot['beats']), ([], []))
        self.assertEqual(cut_plan(stage_shot), [('wide', '')])
        self.assertEqual(stage_shot['set']['plate'], 'media/shot.png')

    def test_character_without_a_rig_stays_off_stage(self):
        rigs = {key: value for key, value in RIGS.items() if key != 'zhou'}
        cast = [linlan(), actor('周岳', 'zhou', 0.7, 'talk')]
        with self.assertLogs('soundbook.director', level='WARNING'):
            stage = build([shot(0, 200, cast)], [line('周岳', 20, 90, '我在。')], rigs=rigs)
        self.assertPlayable(stage)
        self.assertEqual([item['id'] for item in stage['shots'][0]['actors']], ['linlan'])
        self.assertEqual(stage['speech'], [])
        self.assertEqual(list(stage['rigs']), ['linlan'])


class MonologueTests(StageAssertions):
    def test_solo_monologue_changes_shot_size(self):
        stage = build([shot(0, 240, [linlan(0.46)], framing='medium')],
                      [line('林岚', 6, 230, '今天的任务分三步，首先校准传感器，然后复核轨道，最后提交报告。')])
        self.assertPlayable(stage)
        stage_shot = stage['shots'][0]
        self.assertCutsTile(stage_shot)
        self.assertEqual(cut_plan(stage_shot), [('medium', 'linlan'), ('close', 'linlan'), ('medium', 'linlan')])
        self.assertTrue(all(cut['to'] - cut['from'] <= round(4.5 * FPS) for cut in stage_shot['cuts']))

    def test_monologue_with_a_listener_cuts_away_to_the_reaction(self):
        stage = build([shot(0, 300, [linlan(), chen()])],
                      [line('林岚', 50, 290, '先别急着下结论，这条曲线在第三分钟出现了拐点，我们得把原始记录调出来。')])
        self.assertPlayable(stage)
        stage_shot = stage['shots'][0]
        self.assertCutsTile(stage_shot)
        plan = cut_plan(stage_shot)
        index = plan.index(('medium', 'chen'))
        cutaway = stage_shot['cuts'][index]
        self.assertEqual(cutaway['to'] - cutaway['from'], round(1.5 * FPS))
        self.assertEqual((plan[index - 1][1], plan[index + 1][1]), ('linlan', 'linlan'))
        nods = [beat for beat in beats_of(stage_shot, 'chen', 'nod')
                if cutaway['from'] <= beat['from'] and beat['to'] <= cutaway['to']]
        self.assertEqual(len(nods), 1)

    def test_short_line_is_one_take(self):
        stage_shot = build([shot(0, 100, [linlan(0.46)], framing='medium')], [line('林岚', 6, 90, '收到。')])['shots'][0]
        self.assertEqual(cut_plan(stage_shot), [('medium', 'linlan')])


class GestureTests(StageAssertions):
    def spoken_clips(self, texts, action='talk', **extra):
        speech = [line('林岚', 10 + index * 60, 60 + index * 60, text, **extra) for index, text in enumerate(texts)]
        stage = build([shot(0, 20 + len(texts) * 60, [linlan(action=action), chen()])], speech)
        self.assertPlayable(stage)
        return [beat['clip'] for beat in stage['shots'][0]['beats'] if beat['speaking']]

    def test_neutral_lines_do_not_repeat_one_talk_clip(self):
        clips = self.spoken_clips(['嗯，可以。', '我同意。', '就这么办。', '走吧。'])
        self.assertEqual(clips, ['talk_calm', 'talk_explain', 'talk_calm', 'talk_explain'])

    def test_line_content_picks_the_gesture(self):
        self.assertEqual(self.spoken_clips(['大家好。'])[0], 'wave')
        self.assertEqual(self.spoken_clips(['你看这里的读数。'])[0], 'point')
        self.assertEqual(self.spoken_clips(['快停下！'])[0], 'talk_emphatic')
        self.assertEqual(self.spoken_clips(['因为电池温度太低。'])[0], 'talk_explain')

    def test_short_startled_line_reacts_and_a_long_one_stays_emphatic(self):
        def clip_for(end):
            stage_shot = build([shot(0, 200, [linlan(), chen()])], [line('林岚', 10, end, '什么？', emotion='surprised')])['shots'][0]
            return next(beat['clip'] for beat in stage_shot['beats'] if beat['speaking'])
        self.assertEqual(clip_for(40), 'react')
        self.assertEqual(clip_for(100), 'talk_emphatic')

    def test_shot_action_shapes_the_first_line_only(self):
        self.assertEqual(self.spoken_clips(['好的。', '没问题。'], action='point'), ['point', 'talk_calm'])
        self.assertEqual(self.spoken_clips(['好的。'], action='work'), ['talk_explain'])

    def test_long_line_opens_with_the_gesture_then_settles(self):
        stage_shot = build([shot(0, 200, [linlan(), chen()])], [line('林岚', 10, 180, '你看这里，曲线已经回到正常范围了。')])['shots'][0]
        spoken = [beat for beat in stage_shot['beats'] if beat['speaking']]
        self.assertEqual([beat['clip'] for beat in spoken], ['point', 'talk_explain'])
        self.assertEqual(spoken[0]['to'] - spoken[0]['from'], round(director.CLIP_SECONDS['point'] * FPS))
        self.assertEqual((spoken[0]['to'], spoken[1]['to']), (spoken[1]['from'], 180))

    def test_emotion_reaches_the_speaking_beat(self):
        stage_shot = build([shot(0, 200, [linlan(), chen()])], [line('林岚', 10, 90, '这不对。', emotion='质疑')])['shots'][0]
        spoken = next(beat for beat in stage_shot['beats'] if beat['speaking'])
        self.assertEqual(spoken['emotion'], 'serious')
        self.assertEqual(director.normalize_emotion('沉稳清楚'), 'neutral')
        self.assertEqual(director.normalize_emotion('', '太好了，成功了！'), 'happy')

    def test_silent_actor_plays_the_shot_action(self):
        stage_shot = build([shot(0, 200, [linlan(action='wave'), chen(action='work')])])['shots'][0]
        self.assertEqual([beat['clip'] for beat in beats_of(stage_shot, 'linlan')], ['wave'])
        self.assertEqual([(beat['clip'], beat['from'], beat['to']) for beat in beats_of(stage_shot, 'chen')],
                         [('talk_explain', 0, 200)])


class SpeechTests(StageAssertions):
    def test_rhubarb_cues_become_relative_frames(self):
        cues = [{'start': 0.0, 'end': 0.125, 'value': 'X'}, {'start': 0.125, 'end': 0.29, 'value': 'D'},
                {'start': 0.29, 'end': 0.5, 'value': 'G'}, {'start': 0.5, 'end': 0.7, 'value': 'B'},
                {'start': 0.7, 'end': 0.9, 'value': 'H'}]
        self.assertEqual(director.normalize_cues(cues, FPS), [[0, 'X'], [3, 'D'], [7, 'B'], [17, 'C']])

    def test_every_rhubarb_shape_maps_to_a_face_viseme(self):
        mapped = {shape: director.map_viseme(shape) for shape in 'ABCDEFGHX'}
        self.assertEqual(mapped, {'A': 'A', 'B': 'B', 'C': 'C', 'D': 'D', 'E': 'E', 'F': 'F',
                                  'G': 'B', 'H': 'C', 'X': 'X'})
        self.assertEqual(director.map_viseme('?'), 'X')
        self.assertEqual([director.map_viseme(level) for level in (0.0, 0.2, 0.5, 0.9)], ['X', 'B', 'C', 'D'])

    def test_cues_start_at_zero_and_stay_inside_the_line(self):
        cues = [[4, 'D'], [2, 'b'], [2, 'C'], [40, 'A']]
        self.assertEqual(director.normalize_cues(cues, FPS, line_frames=30), [[0, 'X'], [2, 'C'], [4, 'D']])
        self.assertEqual(director.normalize_cues(None, FPS), [[0, 'X']])

    def test_speech_entries_follow_the_stage_contract(self):
        stage = build([shot(68, 200, [linlan(), chen()])], [line('林岚', 74, 146, '先看月面传回的数据。')])
        self.assertEqual(stage['speech'], [{'actor': 'linlan', 'from': 74, 'to': 146,
                                            'cues': [[0, 'X'], [3, 'D'], [7, 'B']]}])

    def test_flat_frame_cues_are_grouped_into_lines(self):
        def cue(speaker, start, end, value):
            return {'speaker': speaker, 'from': start, 'to': end, 'value': value, 'scene_number': 1}
        flat = [cue('林岚', 100, 103, 'X'), cue('林岚', 103, 110, 'D'), cue('林岚', 110, 150, 'B'),
                cue('林岚', 157, 160, 'X'), cue('林岚', 160, 200, 'C'),
                cue('陈工', 207, 210, 'X'), cue('陈工', 210, 260, 'E')]
        stage = build([shot(0, 300, [linlan(), chen()])], flat)
        self.assertPlayable(stage)
        self.assertEqual(stage['speech'], [
            {'actor': 'linlan', 'from': 100, 'to': 150, 'cues': [[0, 'X'], [3, 'D'], [10, 'B']]},
            {'actor': 'linlan', 'from': 157, 'to': 200, 'cues': [[0, 'X'], [3, 'C']]},
            {'actor': 'chen', 'from': 207, 'to': 260, 'cues': [[0, 'X'], [3, 'E']]},
        ])
        self.assertTrue(any(beat['speaking'] and beat['actor'] == 'chen' for beat in stage['shots'][0]['beats']))

    def test_line_text_falls_back_to_the_captions(self):
        track = {'speaker': '林岚', 'from': 10, 'to': 90, 'cues': CUES}
        captions = [{'from': 10, 'to': 90, 'text': '林岚：大家好。'}]
        stage_shot = build([shot(0, 200, [linlan(), chen()])], [track], captions)['shots'][0]
        self.assertEqual(next(beat['clip'] for beat in stage_shot['beats'] if beat['speaking']), 'wave')


class AssemblyTests(StageAssertions):
    def test_top_level_document(self):
        manifest = {'version': 1, 'width': 1280, 'height': 720, 'fps': 24, 'episode_number': 3, 'episode_title': '月面来信'}
        stage = build([shot(68, 592, [linlan(), chen()])], [line('林岚', 74, 146, '开始。')], manifest=manifest,
                      intro={'frames': 68, 'subtitle': '第一章'}, cta={'frames': 60, 'title': '关注我们', 'subtitle': '下集见'})
        self.assertPlayable(stage)
        self.assertEqual(list(stage), ['version', 'width', 'height', 'fps', 'total_frames', 'intro', 'cta',
                                       'rigs', 'shots', 'speech', 'captions', 'audio'])
        self.assertEqual((stage['version'], stage['width'], stage['height'], stage['fps']), (1, 1280, 720, 24))
        self.assertEqual(stage['intro'], {'frames': 68, 'episode_number': 3, 'title': '月面来信', 'subtitle': '第一章'})
        self.assertEqual(stage['cta'], {'from': 660, 'frames': 60, 'title': '关注我们', 'subtitle': '下集见'})
        self.assertEqual(stage['total_frames'], 720)
        self.assertEqual(stage['rigs'], {
            'linlan': {'rig_json': 'media/rigs/linlan/rig.json', 'stature_scale': 0.93},
            'chen': {'rig_json': 'media/rigs/chen/rig.json', 'stature_scale': 1.0},
        })

    def test_intro_and_cta_default_to_empty_cards(self):
        stage = build([shot(0, 100, [linlan()])])
        self.assertEqual(stage['intro'], {'frames': 0, 'episode_number': 1, 'title': '', 'subtitle': ''})
        self.assertEqual(stage['cta'], {'from': 100, 'frames': 0, 'title': '', 'subtitle': ''})
        self.assertEqual(stage['total_frames'], 100)

    def test_shot_fields_and_set(self):
        timeline = shot(68, 180, [linlan(action='console')], framing='medium',
                        plate={'src': 'media/loc1-plate.png', 'far': 'media/loc1-far.png', 'near': 'media/loc1-near.png'},
                        plate_metrics={'mid_y': 0.88})
        timeline['story_motion']['set_prop'] = {'kind': 'console_desk', 'x': 0.5, 'depth': 'mid',
                                                'occlude_waist': True, 'prop_src': 'media/desk.png'}
        stage_shot = build([timeline])['shots'][0]
        self.assertEqual(list(stage_shot), ['id', 'scene_number', 'from', 'frames', 'set', 'actors', 'beats', 'cuts'])
        self.assertEqual((stage_shot['id'], stage_shot['scene_number'], stage_shot['from'], stage_shot['frames']),
                         ('s1_sh1', 1, 68, 180))
        self.assertEqual(stage_shot['set'], {
            'plate': 'media/loc1-plate.png', 'far': 'media/loc1-far.png', 'near': 'media/loc1-near.png',
            'ground_y': 0.88, 'walk_scroll_speed': 0.0,
            'prop': {'kind': 'console_desk', 'x': 0.5, 'depth': 'mid', 'image': 'media/desk.png'},
        })
        self.assertEqual(stage_shot['actors'], [{'id': 'linlan', 'name': '林岚', 'x': 0.32, 'depth': 'mid',
                                                 'facing': 'right', 'lean': 0.0, 'base_clip': 'idle',
                                                 'emotion': 'neutral'}])
        for beat in stage_shot['beats']:
            self.assertEqual(list(beat), ['from', 'to', 'actor', 'clip', 'emotion', 'speaking', 'look_at', 'move_x'])
        for cut in stage_shot['cuts']:
            self.assertEqual(list(cut), ['from', 'to', 'framing', 'focus_actor', 'camera_move'])

    def test_raw_manifest_plate_and_missing_prop(self):
        timeline = shot(0, 100, [linlan()])
        timeline['story_motion']['plate'] = {'plate_file': 'media\\plate.png', 'far_file': 'media/far.png'}
        scene_set = build([timeline])['shots'][0]['set']
        self.assertEqual((scene_set['plate'], scene_set['far'], scene_set['near'], scene_set['ground_y']),
                         ('media/plate.png', 'media/far.png', '', 0.86))
        self.assertEqual(scene_set['prop'], {'kind': '', 'x': 0.5, 'depth': 'mid', 'image': ''})

    def test_shots_without_from_follow_the_intro_in_order(self):
        timeline = [{'scene_number': 1, 'duration': 90, 'story_motion': {'actors': [linlan()]}},
                    {'scene_number': 1, 'duration': 60, 'story_motion': {'actors': [linlan()]}},
                    {'scene_number': 2, 'duration': 50, 'story_motion': {'actors': [chen()]}}]
        stage = build(timeline, intro={'frames': 68})
        self.assertEqual([(item['id'], item['from'], item['frames']) for item in stage['shots']],
                         [('s1_sh1', 68, 90), ('s1_sh2', 158, 60), ('s2_sh1', 218, 50)])
        self.assertEqual(stage['total_frames'], 268)
        self.assertPlayable(stage)

    def test_audio_tracks_are_normalised(self):
        audio = [{'file': 'media/voice_1.wav', 'start_ms': 3083, 'volume': 1, 'loop': False},
                 {'src': 'media/ambience.mp3', 'from': 68, 'volume': 9, 'loop': True},
                 {'volume': 1}]
        stage = build([shot(0, 100, [linlan()])], audio=audio)
        self.assertEqual(stage['audio'], [
            {'file': 'media/voice_1.wav', 'start_ms': 3083, 'volume': 1.0, 'loop': False},
            {'file': 'media/ambience.mp3', 'start_ms': 2833, 'volume': 4.0, 'loop': True},
        ])

    def test_actor_id_is_stable_when_rigs_are_keyed_by_name(self):
        rigs = {'林岚': {'rig_json': 'media/rigs/a/rig.json'}, '陈工': 'media/rigs/b/rig.json'}
        cast = [actor('林岚', 'char_01', 0.3, 'talk', stature_scale=0.93), actor('陈工', '', 0.7)]
        stage = build([shot(0, 100, cast), shot(100, 100, [actor('林岚博士', '', 0.3)])],
                      [line('林岚', 10, 80, '你好。')], rigs=rigs)
        self.assertPlayable(stage)
        self.assertEqual(stage['rigs'], {'char_01': {'rig_json': 'media/rigs/a/rig.json', 'stature_scale': 0.93},
                                         'actor_2': {'rig_json': 'media/rigs/b/rig.json', 'stature_scale': 1.0}})
        self.assertEqual(stage['shots'][1]['actors'][0]['id'], 'char_01')
        self.assertEqual(stage['speech'][0]['actor'], 'char_01')

    def test_validator_reports_contract_breaks(self):
        stage = build([shot(0, 300, [linlan(), chen()])], [line('林岚', 60, 150, '开始。'), line('陈工', 160, 250, '好。')])
        self.assertEqual(director.validate_stage(stage), [])
        stage['shots'][0]['cuts'][1]['from'] += 30
        stage['shots'][0]['beats'][0]['clip'] = 'dance'
        stage['width'] = 1920
        problems = director.validate_stage(stage)
        self.assertTrue(any('tile' in problem for problem in problems))
        self.assertTrue(any('unknown clip' in problem for problem in problems))
        self.assertTrue(any('1280x720' in problem for problem in problems))


if __name__ == '__main__':
    unittest.main()
