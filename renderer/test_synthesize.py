import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from renderer.synthesize import (
    edge_audio_filter,
    edge_ffmpeg_command,
    normalize_tts_text,
    resolve_edge_voice,
    speech_cache_id,
    synthesize,
)


class SpeechCacheTests(unittest.TestCase):
    def setUp(self):
        self._tts_env = os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)

    def tearDown(self):
        if self._tts_env is None:
            os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)
        else:
            os.environ['SOUNDBOOK_TTS_ENGINE'] = self._tts_env

    def test_reuses_identical_text_voice_and_language(self):
        expected = speech_cache_id('zh', 'Weiguo', '  下一站是月球。  ')
        self.assertEqual(expected, speech_cache_id('zh', 'Weiguo', '下一站是月球。'))

    def test_changes_identity_when_text_voice_or_language_changes(self):
        base = speech_cache_id('zh', 'Weiguo', '下一站是月球。')
        self.assertNotEqual(base, speech_cache_id('zh', 'Saki', '下一站是月球。'))
        self.assertNotEqual(base, speech_cache_id('en', 'Weiguo', '下一站是月球。'))
        self.assertNotEqual(base, speech_cache_id('zh', 'Weiguo', '下一站是火星。'))

    def test_cached_rerender_links_audio_without_importing_moss_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'media' / 'moss'
            cache.mkdir(parents=True)
            intro = cache / 'intro.wav'
            intro.write_bytes(b'0' * 4096)
            track_path = cache / f"line-{speech_cache_id('zh', 'Weiguo', '准备出发。')}.wav"
            track_path.write_bytes(b'1' * 4096)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'tts_engine': 'moss', 'language': 'zh', 'intro_tts_text': '第八集。',
                'audio_tracks': [{'tts_text': '准备出发。', 'tts_voice': 'Weiguo'}],
            }), encoding='utf-8')
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch('renderer.synthesize.load_moss_runtime', side_effect=AssertionError('runtime imported')):
                    synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(Path(linked['intro_voice_file']), Path('media/moss/intro.wav'))
            self.assertEqual(Path(linked['audio_tracks'][0]['file']), track_path.relative_to(root))


class NormalizeTests(unittest.TestCase):
    def test_drops_stage_directions_and_control_characters(self):
        text = normalize_tts_text('（轻笑）你好\x00 [停顿]\n世界', 'zh')
        self.assertEqual(text, '你好 世界')
        self.assertNotIn('轻笑', text)
        self.assertNotIn('停顿', text)

    def test_rewrites_chinese_codes_percents_decimals_and_temperature(self):
        text = normalize_tts_text('AT-1 电量 15%，温差 0.5，舱温 0.5℃。', 'zh')
        self.assertEqual(text, 'A T 一号 电量 百分之十五，温差 零点五，舱温 零点五摄氏度。')

    def test_rewrites_english_codes_and_units_without_chinese_numerals(self):
        text = normalize_tts_text('AT-1 is at 15% and 0.5°C.', 'en')
        self.assertIn('A T 1', text)
        self.assertIn('15 percent', text)
        self.assertIn('0.5 degrees Celsius', text)
        self.assertNotIn('一号', text)
        self.assertNotIn('%', text)


class EdgeVoiceTests(unittest.TestCase):
    def test_maps_moss_aliyun_character_and_direct_names(self):
        cases = {
            ('Xiaoyu', 'zh', '林岚'): 'zh-CN-XiaoxiaoNeural',
            ('longxiaochun', 'zh', ''): 'zh-CN-XiaoxiaoNeural',
            ('女性主声线', 'zh', ''): 'zh-CN-XiaoxiaoNeural',
            ('Junhao', 'zh', '陈工'): 'zh-CN-YunxiNeural',
            ('longcheng', 'zh', ''): 'zh-CN-YunxiNeural',
            ('Weiguo', 'zh', '旁白'): 'zh-CN-YunyangNeural',
            ('longhua', 'zh', ''): 'zh-CN-YunyangNeural',
            ('Zhiming', 'zh', '阿拓'): 'zh-CN-YunjianNeural',
            ('longshu', 'zh', ''): 'zh-CN-YunjianNeural',
            ('Yuewen', 'zh', ''): 'zh-CN-XiaoyiNeural',
            ('longanyang', 'zh', ''): 'zh-CN-XiaoyiNeural',
            ('Lingyu', 'zh', ''): 'zh-CN-XiaochenNeural',
            ('Nathan', 'en', ''): 'en-US-AndrewNeural',
            ('Adam', 'en', ''): 'en-US-BrianNeural',
            ('Ava', 'en', ''): 'en-US-AriaNeural',
            ('Bella', 'en', ''): 'en-US-JennyNeural',
            ('Saki', 'ja', ''): 'ja-JP-NanamiNeural',
            ('Soyo', 'ja', ''): 'ja-JP-NanamiNeural',
            ('Mortis', 'ja', ''): 'ja-JP-KeitaNeural',
            ('Xiaoyu', 'ko', ''): 'ko-KR-SunHiNeural',
            ('Junhao', 'ko', ''): 'ko-KR-InJoonNeural',
            ('zh-CN-YunxiNeural', 'zh', '阿拓'): 'zh-CN-YunxiNeural',
        }
        for (voice, language, speaker), expected in cases.items():
            self.assertEqual(resolve_edge_voice(voice, language, speaker), expected, voice)

    def test_robot_speaker_uses_a_clear_voice_and_a_gentle_eq(self):
        self.assertEqual(resolve_edge_voice('Weiguo', 'zh', '阿拓 AT-1'), 'zh-CN-YunjianNeural')
        robot = edge_audio_filter('阿拓')
        human = edge_audio_filter('林岚')
        self.assertIn('highpass=f=100', robot)
        self.assertIn('equalizer=f=3200', robot)
        self.assertNotIn('bandpass=', robot)
        self.assertNotIn('highpass=', human)
        for filt in (robot, human):
            self.assertEqual(filt.count('start_silence=0.04'), 2)
            self.assertIn('areverse', filt)
            self.assertNotIn('stop_periods', filt)
            self.assertIn('loudnorm=I=-18:TP=-2:LRA=8', filt)

    def test_ffmpeg_command_targets_24k_mono_pcm(self):
        command = edge_ffmpeg_command('ffmpeg-bin', Path('in.mp3'), Path('out.wav'), '林岚')
        self.assertEqual(command[0], 'ffmpeg-bin')
        self.assertIn('24000', command)
        self.assertIn('pcm_s16le', command)
        self.assertIn('1', command)


class EdgeSynthesizeTests(unittest.TestCase):
    def setUp(self):
        self._tts_env = os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)

    def tearDown(self):
        if self._tts_env is None:
            os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)
        else:
            os.environ['SOUNDBOOK_TTS_ENGINE'] = self._tts_env

    def test_cache_hit_skips_edge_and_ffmpeg(self):
        text = normalize_tts_text('准备出发。', 'zh')
        voice = resolve_edge_voice('Weiguo', 'zh', '旁白')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'media' / 'tts'
            cache.mkdir(parents=True)
            track_path = cache / f"line-{speech_cache_id('edge:zh', voice, text)}.wav"
            track_path.write_bytes(b'1' * 4096)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'tts_engine': 'edge', 'language': 'zh',
                'audio_tracks': [{'tts_text': '准备出发。', 'tts_voice': 'Weiguo', 'speaker': '旁白'}],
            }), encoding='utf-8')
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch('renderer.synthesize.write_edge_mp3', side_effect=AssertionError('network')), \
                     mock.patch('renderer.synthesize.convert_speech_wav', side_effect=AssertionError('ffmpeg')):
                    synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(Path(linked['audio_tracks'][0]['file']), track_path.relative_to(root))

    def test_env_override_uses_edge_cache_for_a_moss_manifest(self):
        text = normalize_tts_text('准备出发。', 'zh')
        voice = resolve_edge_voice('Weiguo', 'zh', '')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / 'media' / 'tts'
            cache.mkdir(parents=True)
            track_path = cache / f"line-{speech_cache_id('edge:zh', voice, text)}.wav"
            track_path.write_bytes(b'1' * 4096)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'tts_engine': 'moss', 'language': 'zh',
                'audio_tracks': [{'tts_text': '准备出发。', 'tts_voice': 'Weiguo'}],
            }), encoding='utf-8')
            original_cwd = Path.cwd()
            os.environ['SOUNDBOOK_TTS_ENGINE'] = 'edge'
            try:
                os.chdir(root)
                with mock.patch('renderer.synthesize.load_moss_runtime', side_effect=AssertionError('moss')), \
                     mock.patch('renderer.synthesize.write_edge_mp3', side_effect=AssertionError('network')):
                    synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(Path(linked['audio_tracks'][0]['file']), track_path.relative_to(root))

    def test_edge_mock_normalizes_text_maps_voice_and_retries(self):
        calls = []

        def flaky_save(text, voice, rate, destination):
            calls.append((text, voice, rate))
            if len(calls) < 3:
                raise TimeoutError('network')
            destination.write_bytes(b'ID3' + b'\x00' * 200)

        def fake_convert(source, destination, speaker=''):
            self.assertTrue(source.exists())
            destination.write_bytes(b'R' * 4096)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'tts_engine': 'edge', 'language': 'zh',
                'audio_tracks': [{
                    'tts_text': '（点头）AT-1 还有 15%。',
                    'tts_voice': 'Xiaoyu',
                    'speaker': '林岚',
                }],
            }), encoding='utf-8')
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch('renderer.synthesize.write_edge_mp3', side_effect=flaky_save), \
                     mock.patch('renderer.synthesize.convert_speech_wav', side_effect=fake_convert), \
                     mock.patch('renderer.synthesize.time.sleep'):
                    synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[-1][0], 'A T 一号 还有 百分之十五。')
        self.assertEqual(calls[-1][1], 'zh-CN-XiaoxiaoNeural')
        self.assertEqual(calls[-1][2], '+12%')
        self.assertTrue(str(linked['audio_tracks'][0]['file']).startswith('media/tts/line-'))


class MossCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self._tts_env = os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)

    def tearDown(self):
        if self._tts_env is None:
            os.environ.pop('SOUNDBOOK_TTS_ENGINE', None)
        else:
            os.environ['SOUNDBOOK_TTS_ENGINE'] = self._tts_env

    def test_moss_normalizes_text_and_falls_back_when_wetext_errors(self):
        calls = []

        class Runtime:
            def list_builtin_voices(self):
                return [{'voice': 'Weiguo'}, {'voice': 'Xiaoyu'}, {'voice': 'Junhao'}]

            def synthesize(self, **kwargs):
                calls.append(kwargs)
                if kwargs.get('enable_wetext') is True:
                    raise RuntimeError('wetext is not installed')
                Path(kwargs['output_audio_path']).write_bytes(b'z' * 5000)
                return {'text_chunks': ['ok']}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'tts_engine': 'moss', 'language': 'zh',
                'intro_tts_text': '（开场）第八集。',
                'intro_tts_voice': 'Junhao',
                'audio_tracks': [{'tts_text': 'AT-1 电量 15%。', 'tts_voice': 'Xiaoyu', 'speaker': '林岚'}],
            }), encoding='utf-8')
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                with mock.patch('renderer.synthesize.load_moss_runtime', return_value=Runtime()), \
                     mock.patch('renderer.synthesize.tighten_pacing') as pacing:
                    synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
        self.assertTrue(any(call.get('enable_wetext') is True for call in calls))
        spoken = [call['text'] for call in calls if call.get('enable_wetext') is False]
        self.assertIn('A T 一号 电量 百分之十五。', spoken)
        self.assertTrue(all('（' not in text and '%' not in text for text in spoken))
        self.assertGreaterEqual(pacing.call_count, 1)
        self.assertEqual(Path(linked['intro_voice_file']), Path('media/moss/intro.wav'))
        self.assertTrue(str(linked['audio_tracks'][0]['file']).startswith('media/moss/line-'))

    def test_moss_omits_wetext_when_the_runtime_rejects_the_argument(self):
        calls = []

        class Runtime:
            def synthesize(self, **kwargs):
                calls.append(kwargs)
                if 'enable_wetext' in kwargs:
                    raise TypeError("unexpected keyword argument 'enable_wetext'")
                return {'text_chunks': ['ok']}

        from renderer.synthesize import moss_synthesize

        moss_synthesize(Runtime(), text='准备出发。', voice='Weiguo', output_audio_path=Path('out.wav'))
        self.assertEqual(calls[-1], {'text': '准备出发。', 'voice': 'Weiguo', 'output_audio_path': Path('out.wav')})
        self.assertTrue(any(call.get('enable_wetext') is True for call in calls))
        self.assertTrue(any(call.get('enable_wetext') is False for call in calls))


if __name__ == '__main__':
    unittest.main()
