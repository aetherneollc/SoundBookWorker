import json
import os
import tempfile
import unittest
from pathlib import Path

from renderer.synthesize import speech_cache_id, synthesize


class SpeechCacheTests(unittest.TestCase):
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
                synthesize(manifest)
            finally:
                os.chdir(original_cwd)
            linked = json.loads(manifest.read_text(encoding='utf-8'))
            self.assertEqual(linked['intro_voice_file'], 'media/moss/intro.wav')
            self.assertEqual(linked['audio_tracks'][0]['file'], str(track_path.relative_to(root)))


if __name__ == '__main__':
    unittest.main()
