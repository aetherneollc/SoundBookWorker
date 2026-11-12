import unittest

from renderer.synthesize import speech_cache_id


class SpeechCacheTests(unittest.TestCase):
    def test_reuses_identical_text_voice_and_language(self):
        expected = speech_cache_id('zh', 'Weiguo', '  下一站是月球。  ')
        self.assertEqual(expected, speech_cache_id('zh', 'Weiguo', '下一站是月球。'))

    def test_changes_identity_when_text_voice_or_language_changes(self):
        base = speech_cache_id('zh', 'Weiguo', '下一站是月球。')
        self.assertNotEqual(base, speech_cache_id('zh', 'Saki', '下一站是月球。'))
        self.assertNotEqual(base, speech_cache_id('en', 'Weiguo', '下一站是月球。'))
        self.assertNotEqual(base, speech_cache_id('zh', 'Weiguo', '下一站是火星。'))


if __name__ == '__main__':
    unittest.main()
