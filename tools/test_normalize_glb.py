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

from tools.normalize_glb import BLENDER_DECIMATE_SCRIPT, _read_accessor, read_glb, write_glb

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

    def test_smooth_normals_weld_seams_and_keep_texture(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'seam.glb'
            smoothed = root / 'seam-smooth.glb'
            faceted = root / 'seam-flat.glb'
            original_uv = _faceted_seam(source)

            report, stderr = _run_normalize(source, smoothed, '--smooth-normals')
            self.assertEqual(stderr.strip(), '')
            self.assertTrue(report['smooth_normals'])
            self.assertTrue(report['has_textures'])
            self.assertEqual(report['output_tris'], 4)
            self.assertEqual(report['input_tris'], 4)

            positions, normals = _read_pos_nrm(smoothed)
            self.assertEqual(len(positions), 8)
            self.assertEqual(len(normals), 8)
            groups = _position_groups(positions)
            seams = [indexes for indexes in groups.values() if len(indexes) >= 2]
            singles = [indexes[0] for indexes in groups.values() if len(indexes) == 1]
            self.assertGreaterEqual(len(seams), 2)
            self.assertGreaterEqual(len(singles), 2)
            for indexes in seams:
                block = normals[indexes]
                self.assertTrue(np.allclose(block, block[0], atol=1e-4))
                self.assertGreater(abs(float(block[0, 1])), 0.95)
                self.assertLess(abs(float(block[0, 2])), 0.05)
            self.assertTrue(any(abs(float(normals[index, 2])) > 0.4 for index in singles))

            flat_report, _flat_stderr = _run_normalize(source, faceted)
            self.assertFalse(flat_report['smooth_normals'])
            flat_scene = trimesh.load_scene(faceted, process=False)
            flat_mesh = next(iter(flat_scene.geometry.values()))
            flat_groups = _position_groups(np.asarray(flat_mesh.vertices))
            flat_normals = np.asarray(flat_mesh.vertex_normals, dtype=np.float64)
            flat_seams = [indexes for indexes in flat_groups.values() if len(indexes) >= 2]
            self.assertTrue(any(
                abs(float(np.dot(flat_normals[indexes[0]], flat_normals[indexes[1]]))) < 0.2
                for indexes in flat_seams
            ))

            scene = trimesh.load_scene(smoothed, process=False)
            loaded = next(iter(scene.geometry.values()))
            self.assertEqual(loaded.visual.kind, 'texture')
            self.assertEqual(len(loaded.vertices), 8)
            self.assertEqual(len(loaded.visual.uv), 8)
            np.testing.assert_allclose(_sorted_rows(loaded.visual.uv), _sorted_rows(original_uv), atol=1e-5)
            image = loaded.visual.material.baseColorTexture
            self.assertEqual(tuple(image.getpixel((0, 0)))[:3], (12, 34, 56))
            self.assertEqual(tuple(image.getpixel((3, 2)))[:3], (200, 10, 20))


def _faceted_seam(path: Path) -> np.ndarray:
    """Two panels sharing a ridge, with the ridge split so a UV seam stays faceted."""
    vertices = np.array([
        [0.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 1.0, -1.0],
        [0.0, 1.0, -1.0],
        [0.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
        [2.0, 1.0, 1.0],
        [0.0, 1.0, 1.0],
    ], dtype=np.float64)
    faces = np.array([
        [0, 1, 2],
        [0, 2, 3],
        [4, 6, 5],
        [4, 7, 6],
    ], dtype=np.int64)
    uv = np.array([
        [0.00, 0.00],
        [0.49, 0.00],
        [0.49, 1.00],
        [0.00, 1.00],
        [0.51, 0.00],
        [1.00, 0.00],
        [1.00, 1.00],
        [0.51, 1.00],
    ], dtype=np.float64)
    image = Image.new('RGBA', (8, 8), (12, 34, 56, 255))
    image.putpixel((3, 2), (200, 10, 20, 255))
    material = trimesh.visual.material.PBRMaterial(
        baseColorTexture=image,
        metallicFactor=0.0,
        roughnessFactor=1.0,
    )
    mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
    mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv, material=material)
    mesh.export(path)
    return uv


def _read_pos_nrm(path: Path):
    document, blob = read_glb(path)
    primitive = document['meshes'][0]['primitives'][0]
    attributes = primitive['attributes']
    if 'NORMAL' not in attributes:
        raise AssertionError('exported GLB is missing NORMAL')
    positions = _read_accessor(document, blob, attributes['POSITION'])
    normals = _read_accessor(document, blob, attributes['NORMAL'])
    return positions, normals


def _sorted_rows(values: np.ndarray) -> np.ndarray:
    rows = np.asarray(values, dtype=np.float64)
    order = np.lexsort((np.round(rows[:, 1], 5), np.round(rows[:, 0], 5)))
    return rows[order]


def _position_groups(positions: np.ndarray) -> dict:
    keys = np.rint(np.asarray(positions, dtype=np.float64) * 1e4).astype(np.int64)
    groups: dict = {}
    for index, key in enumerate(map(tuple, keys)):
        groups.setdefault(key, []).append(index)
    return groups


if __name__ == '__main__':
    unittest.main()
