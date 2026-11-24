"""Synthetic GLB checks for grounding, centering, height, decimation, and textures."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.normalize_glb import BLENDER_DECIMATE_SCRIPT, read_glb, write_glb

SCRIPT = ROOT / 'tools' / 'normalize_glb.py'


def _textured_sphere(path: Path) -> trimesh.Trimesh:
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
    mesh.apply_scale([2.0, 1.0, 0.5])
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    span_x = float(np.ptp(vertices[:, 0])) or 1.0
    span_y = float(np.ptp(vertices[:, 1])) or 1.0
    uv = np.zeros((len(vertices), 2), dtype=np.float64)
    uv[:, 0] = (vertices[:, 0] - float(vertices[:, 0].min())) / span_x
    uv[:, 1] = (vertices[:, 1] - float(vertices[:, 1].min())) / span_y
    image = Image.new('RGBA', (4, 4), (12, 34, 56, 255))
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=image,
        metallicFactor=0.0,
        roughnessFactor=1.0,
    )
    mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=material)
    mesh.apply_translation([3.0, 5.0, -2.0])
    mesh.export(path)
    return mesh


def _run_normalize(source: Path, destination: Path, *args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), str(source), str(destination), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding='utf-8',
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout), completed.stderr


class NormalizeGlbTests(unittest.TestCase):
    def test_height_grounding_centering_decimation_and_texture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'input.glb'
            destination = root / 'output.glb'
            original = _textured_sphere(source)
            report, stderr = _run_normalize(
                source,
                destination,
                '--target-height', '1.6',
                '--rotate-y-deg', '90',
                '--max-tris', '300',
            )
            self.assertEqual(stderr.strip(), '')
            self.assertGreater(report['input_tris'], 300)
            self.assertEqual(report['input_tris'], len(original.faces))
            self.assertLessEqual(report['output_tris'], 300)
            self.assertLess(report['output_tris'], report['input_tris'])
            self.assertFalse(report['skinned'])
            self.assertTrue(report['has_textures'])
            self.assertIsNone(report['decimation_skipped'])
            self.assertAlmostEqual(report['bounds_min'][1], 0.0, delta=1e-4)
            self.assertAlmostEqual(report['height_m'], 1.6, delta=1e-4)
            center_x = (report['bounds_min'][0] + report['bounds_max'][0]) / 2
            center_z = (report['bounds_min'][2] + report['bounds_max'][2]) / 2
            self.assertAlmostEqual(center_x, 0.0, delta=1e-4)
            self.assertAlmostEqual(center_z, 0.0, delta=1e-4)

            scene = trimesh.load_scene(destination, process=False)
            bounds = np.asarray(scene.bounds, dtype=np.float64)
            self.assertAlmostEqual(float(bounds[0, 1]), 0.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[1, 1] - bounds[0, 1]), 1.6, delta=1e-4)
            self.assertAlmostEqual(float(bounds[0, 0] + bounds[1, 0]) / 2, 0.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[0, 2] + bounds[1, 2]) / 2, 0.0, delta=1e-4)
            loaded = next(iter(scene.geometry.values()))
            self.assertLessEqual(len(loaded.faces), 300)
            self.assertEqual(loaded.visual.kind, 'texture')
            image = loaded.visual.material.baseColorTexture
            self.assertIsNotNone(image)
            self.assertEqual(tuple(image.getpixel((0, 0)))[:3], (12, 34, 56))
            document, _blob = read_glb(destination)
            self.assertTrue(document.get('images') or document.get('textures'))
            materials = document.get('materials') or []
            self.assertTrue(any(
                isinstance(material.get('pbrMetallicRoughness'), dict)
                and 'baseColorTexture' in material['pbrMetallicRoughness']
                for material in materials
                if isinstance(material, dict)
            ))

    def test_rotate_y_swaps_extents_and_grounds_without_decimation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'box.glb'
            destination = root / 'box-out.glb'
            box = trimesh.creation.box(extents=(4.0, 2.0, 1.0))
            box.apply_translation([10.0, 5.0, -3.0])
            box.export(source)
            report, _stderr = _run_normalize(source, destination, '--rotate-y-deg', '90')
            self.assertEqual(report['input_tris'], 12)
            self.assertEqual(report['output_tris'], 12)
            self.assertFalse(report['has_textures'])
            scene = trimesh.load_scene(destination, process=False)
            bounds = np.asarray(scene.bounds, dtype=np.float64)
            self.assertAlmostEqual(float(bounds[0, 1]), 0.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[1, 1] - bounds[0, 1]), 2.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[1, 0] - bounds[0, 0]), 1.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[1, 2] - bounds[0, 2]), 4.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[0, 0] + bounds[1, 0]) / 2, 0.0, delta=1e-4)
            self.assertAlmostEqual(float(bounds[0, 2] + bounds[1, 2]) / 2, 0.0, delta=1e-4)

    def test_skinned_mesh_skips_decimation_and_keeps_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'skinned.glb'
            destination = root / 'skinned-out.glb'
            _textured_sphere(source)
            document, blob = read_glb(source)
            document['skins'] = [{'name': 'Armature', 'joints': [0], 'skeleton': 0}]
            write_glb(source, document, blob)
            _before_doc, before_blob = read_glb(source)
            report, stderr = _run_normalize(
                source,
                destination,
                '--target-height', '1.25',
                '--max-tris', '30',
            )
            self.assertIn('decimation_skipped="skinned"', stderr)
            self.assertEqual(report['decimation_skipped'], 'skinned')
            self.assertTrue(report['skinned'])
            self.assertTrue(report['has_textures'])
            self.assertGreater(report['input_tris'], 30)
            self.assertEqual(report['output_tris'], report['input_tris'])
            self.assertAlmostEqual(report['height_m'], 1.25, delta=1e-4)
            self.assertAlmostEqual(report['bounds_min'][1], 0.0, delta=1e-4)
            _after_doc, after_blob = read_glb(destination)
            self.assertEqual(after_blob, before_blob)
            scene = trimesh.load_scene(destination, process=False)
            bounds = np.asarray(scene.bounds, dtype=np.float64)
            self.assertAlmostEqual(float(bounds[0, 1]), 0.0, delta=1e-3)
            self.assertAlmostEqual(float(bounds[1, 1] - bounds[0, 1]), 1.25, delta=1e-3)
            self.assertAlmostEqual(float(bounds[0, 0] + bounds[1, 0]) / 2, 0.0, delta=1e-3)
            self.assertAlmostEqual(float(bounds[0, 2] + bounds[1, 2]) / 2, 0.0, delta=1e-3)

    def test_blender_decimate_script_is_valid_python(self):
        compile(BLENDER_DECIMATE_SCRIPT, 'blender_decimate.py', 'exec')


if __name__ == '__main__':
    unittest.main()
