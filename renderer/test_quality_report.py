import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from renderer.quality_report import extract_action_evidence, inspect_layout, run, validate_props


def shot(scene: int, focus: str, event: bool = True) -> dict:
    return {
        'pixel_story': True,
        'scene_number': scene,
        'cartoon': {
            'focus': focus,
            'action_event': {'start': .1, 'contact': .5, 'end': .9} if event else None,
            'stage_layout': {
                'version': 1,
                'coordinate_space': {'width': 320, 'height': 180},
                'dimensions': {'actor_height': 62, 'rover_width': 56, 'rover_height': 38},
                'floor': {'far_y': 138, 'near_y': 162, 'vanishing_x': .5},
                'actors': {'L': {'x': .48, 'depth': .58, 'surface': 'floor'}},
                'rover': {'x': .66, 'depth': .32, 'surface': 'test_platform'},
            },
        },
    }


class QualityReportTests(unittest.TestCase):
    def test_accepts_valid_layout_and_action_timing(self):
        self.assertEqual(inspect_layout(shot(1, 'medium')), [])

    def test_rejects_out_of_stage_anchors_and_broken_timing(self):
        invalid = shot(1, 'medium')
        invalid['cartoon']['stage_layout']['rover']['x'] = 1.3
        invalid['cartoon']['action_event'] = {'start': .8, 'contact': .3, 'end': .2}
        issues = inspect_layout(invalid)
        self.assertIn('anchor is outside stage', issues)
        self.assertIn('invalid action_event timing', issues)

    def test_requires_multiple_compositions_and_story_actions(self):
        props = {'shots': [shot(1, 'wide', False), shot(1, 'wide', False), shot(1, 'wide', False)]}
        issues, _ = validate_props(props)
        self.assertIn('scene 1: fewer than two camera compositions', issues)
        self.assertIn('scene 1: fewer than two visible action events', issues)

    def test_voice_captions_must_cover_audio_without_overflow(self):
        props = {'durationInFrames': 240, 'audio': [
            {'from': 24, 'duration': 48, 'scene_number': 1, 'dialogue_number': 1},
        ], 'captions': [
            {'from': 24, 'to': 45, 'scene_number': 1, 'dialogue_number': 1, 'text': '第一句'},
            {'from': 45, 'to': 72, 'scene_number': 1, 'dialogue_number': 1, 'text': '第二句'},
        ], 'speech': [{'from': 28, 'to': 66, 'scene_number': 1}]}
        issues, summary = validate_props(props)
        self.assertEqual(issues, [])
        self.assertEqual(summary['voice_track_count'], 1)
        self.assertEqual(summary['caption_count'], 2)
        props['captions'][1]['to'] = 60
        issues, _ = validate_props(props)
        self.assertTrue(any('captions do not cover the full speech audio' in item for item in issues))

    def test_rejects_caption_and_speech_frames_past_episode_end(self):
        props = {'durationInFrames': 100,
                 'captions': [{'from': 90, 'to': 101, 'text': '字幕'}],
                 'speech': [{'from': 98, 'to': 102, 'scene_number': 1}]}
        issues, _ = validate_props(props)
        self.assertIn('caption 1: extends beyond episode duration', issues)
        self.assertIn('speech cue 1: extends beyond episode duration', issues)

    def test_pixel_story_voice_requires_mouth_cues_inside_dialogue_audio(self):
        props = {'durationInFrames': 120, 'shots': [{**shot(1, 'wide'), 'from': 0, 'duration': 120}],
                 'audio': [{'from': 12, 'duration': 48, 'scene_number': 1, 'dialogue_number': 2}],
                 'captions': [{'from': 12, 'to': 60, 'scene_number': 1, 'dialogue_number': 2, 'text': '对白'}],
                 'speech': [{'from': 18, 'to': 54, 'scene_number': 1, 'dialogue_number': 2}]}
        issues, _ = validate_props(props)
        self.assertEqual(issues, [])
        props['speech'][0]['to'] = 62
        issues, _ = validate_props(props)
        self.assertIn('voice track 1 (1:2): mouth cues exceed the dialogue audio', issues)
        props['speech'] = []
        issues, _ = validate_props(props)
        self.assertIn('voice track 1 (1:2): no timed mouth cues', issues)

    def test_action_evidence_saves_three_frames_and_motion_measure(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / 'sample.mp4'
            subprocess.run([
                'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                '-i', 'testsrc2=size=320x180:rate=12:duration=3', '-pix_fmt', 'yuv420p', str(video),
            ], check=True, capture_output=True)
            props = {'fps': 12, 'shots': [{
                'pixel_story': True, 'scene_number': 1, 'from': 0, 'duration': 36,
                'cartoon': {'action_event': {'kind': 'reach', 'start': .1, 'contact': .5, 'end': .9}},
            }]}
            evidence, contact = extract_action_evidence(video, props, root / 'qa')
            self.assertEqual(len(evidence), 1)
            self.assertEqual(len(evidence[0]['frames']), 3)
            self.assertGreater(evidence[0]['mean_frame_difference'], 0)
            self.assertGreaterEqual(evidence[0]['mean_frame_difference'], evidence[0]['before_contact_difference'])
            self.assertGreaterEqual(evidence[0]['mean_frame_difference'], evidence[0]['contact_after_difference'])
            self.assertEqual(evidence[0]['motion_signal'], 'visible_change')
            self.assertEqual(contact, 'action-contact.jpg')
            self.assertTrue((root / 'qa' / contact).is_file())

    def test_missing_ffprobe_produces_a_report_instead_of_a_traceback(self):
        props = {'width': 1280, 'height': 720, 'fps': 24, 'durationInFrames': 24, 'shots': [shot(1, 'wide')]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, props_path, video = root / 'manifest.json', root / 'props.json', root / 'output.mp4'
            manifest.write_text('{}', encoding='utf-8')
            props_path.write_text(json.dumps(props), encoding='utf-8')
            video.write_bytes(b'x' * 100_001)
            with patch('renderer.quality_report.command', side_effect=FileNotFoundError('ffprobe')):
                report = run(manifest, props_path, video, root / 'qa')
            self.assertEqual(report['result'], 'failed')
            self.assertIn('ffprobe is not installed; technical media checks could not run', report['issues'])
            self.assertTrue((root / 'qa' / 'report.json').is_file())


if __name__ == '__main__':
    unittest.main()
