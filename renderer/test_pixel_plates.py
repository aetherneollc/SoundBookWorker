import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from renderer.prepare_remotion import pixel_story_source


class PixelPlateTests(unittest.TestCase):
    def test_background_is_cropped_and_reduced_to_the_game_canvas(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.png'
            image = Image.new('RGB', (640, 360))
            for x in range(640):
                for y in range(360):
                    image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
            image.save(source)
            old = Path.cwd()
            try:
                os.chdir(root)
                (root / 'public').mkdir()
                url = pixel_story_source({'file': str(source)}, root / 'public')
            finally:
                os.chdir(old)
            generated = root / 'public' / Path(url).name
            with Image.open(generated) as plate:
                self.assertEqual(plate.size, (320, 180))
                self.assertLessEqual(len(plate.getcolors(320 * 180) or []), 24)

    def test_old_placeholder_keeps_the_procedural_backdrop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'placeholder.png'
            Image.new('RGB', (1, 1), 'black').save(source)
            self.assertEqual(pixel_story_source({'file': str(source)}, root / 'public'), '')


if __name__ == '__main__':
    unittest.main()
