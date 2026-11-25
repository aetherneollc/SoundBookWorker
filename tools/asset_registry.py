#!/usr/bin/env python3
"""Validate glTF/GLB assets and keep a production registry.

Grades a mesh before it can enter a 3D episode:

* ``TIER_A`` — grounded, textured, humanoid, fingered, BlendShape character
* ``TIER_B`` — valid rigged character missing fingers and/or BlendShapes
* ``TIER_C`` — valid static prop, rover, or environment
* ``REJECT`` — broken topology, missing textures, NaN data, bad scale, or no required rig

``REJECT`` assets are refused by :class:`AssetRegistry` and must not be used in production.

CLI::

    python tools/asset_registry.py validate hero.glb --category character
    python tools/asset_registry.py scan media/assets3d --license CC0-1.0

The registry is JSON at ``media/assets3d/registry.json`` unless ``--registry`` is set.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / 'media' / 'assets3d' / 'registry.json'

CATEGORIES = ('character', 'prop', 'rover', 'environment')
TIERS = ('TIER_A', 'TIER_B', 'TIER_C', 'REJECT')
TIER_RANK = {'REJECT': 0, 'TIER_C': 1, 'TIER_B': 2, 'TIER_A': 3}

# Real-time episode budgets. Override per call when a shot needs a tighter cap.
TRIANGLE_BUDGETS = {
    'character': 80_000,
    'prop': 20_000,
    'rover': 60_000,
    'environment': 250_000,
}
# Inclusive sane ranges, in meters. Props are handheld. Environments cover a
# set piece through a cyclorama, and still have to sit on y = 0.
HEIGHT_LIMITS_M = {
    'character': (1.2, 2.2),
    'rover': (0.4, 2.5),
    'prop': (0.05, 0.8),
    'environment': (0.05, 250.0),
}
GROUNDED_CATEGORIES = frozenset({'character', 'rover', 'environment'})
GROUND_TOLERANCE_M = 0.05
_MEASURE_EPS = 1e-4

REQUIRED_FACIAL_MORPHS = ('jawOpen', 'eyeBlink', 'mouthSmile', 'mouthFrown', 'browDown', 'browUp')
FINGER_MIN_BONES = 4
HUMANOID_SLOTS = ('hips', 'spine', 'neck', 'head', 'left_arm', 'right_arm', 'left_leg', 'right_leg')

_GLB_MAGIC = b'glTF'
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_DTYPES = {5120: '<i1', 5121: '<u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_COMPONENTS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
_INTEGER_MAX = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535, 5125: 4294967295}


class AssetRejected(ValueError):
    """Raised when a validated asset is not allowed into the registry."""

    def __init__(self, report: dict):
        self.report = report
        reason = report.get('tier_reason') or 'asset rejected'
        super().__init__(reason)


def default_registry_path() -> Path:
    return DEFAULT_REGISTRY


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def missing_facial_morphs(names: list[str]) -> list[str]:
    """Return canonical BlendShape names that ``names`` does not cover."""
    folded = [canonical_name(name) for name in names if name]
    missing = []
    for required in REQUIRED_FACIAL_MORPHS:
        key = canonical_name(required)
        if not any(item == key or item.startswith(key) for item in folded):
            missing.append(required)
    return missing


def tier_meets(tier: str, minimum: str) -> bool:
    return TIER_RANK[tier] >= TIER_RANK[minimum]


def read_gltf(path: Path) -> tuple[dict, bytes, Path]:
    """Load a GLB or a JSON glTF. Returns the document, the GLB BIN chunk, and the base directory."""
    path = Path(path)
    data = path.read_bytes()
    if data[:4] == _GLB_MAGIC:
        document, blob = _read_glb_bytes(data, path)
        return document, blob, path.parent
    try:
        document = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('not a GLB or glTF: %s' % path) from exc
    if not isinstance(document, dict) or 'asset' not in document:
        raise ValueError('not a GLB or glTF: %s' % path)
    return document, b'', path.parent


def _read_glb_bytes(data: bytes, path: Path) -> tuple[dict, bytes]:
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise ValueError('not a GLB: %s' % path)
    version, length = struct.unpack_from('<II', data, 4)
    if version != 2:
        raise ValueError('unsupported GLB version %s: %s' % (version, path))
    if length > len(data):
        raise ValueError('GLB length exceeds the file: %s' % path)
    offset = 12
    document = None
    blob = b''
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from('<II', data, offset)
        offset += 8
        end = offset + chunk_length
        if end > len(data):
            raise ValueError('GLB chunk overruns the file: %s' % path)
        payload = data[offset:end]
        offset = end
        if chunk_type == _JSON_CHUNK and document is None:
            document = json.loads(payload.decode('utf-8'))
        elif chunk_type == _BIN_CHUNK and not blob:
            blob = payload
    if not isinstance(document, dict):
        raise ValueError('GLB is missing JSON: %s' % path)
    return document, blob


def _buffer_bytes(document: dict, blob: bytes, buffer_index: int, base_dir: Path) -> bytes:
    buffers = document.get('buffers') or []
    if buffer_index < 0 or buffer_index >= len(buffers):
        raise ValueError('buffer %s is missing' % buffer_index)
    buffer = buffers[buffer_index]
    uri = buffer.get('uri') if isinstance(buffer, dict) else None
    if not uri:
        return blob
    return _load_uri_bytes(str(uri), base_dir, 'buffer')


def _load_uri_bytes(uri: str, base_dir: Path, kind: str) -> bytes:
    if uri.startswith('data:'):
        payload = uri.split(',', 1)[1] if ',' in uri else ''
        try:
            return base64.b64decode(payload, validate=False)
        except (ValueError, TypeError) as exc:
            raise ValueError('invalid data URI %s' % kind) from exc
    file_path = Path(uri)
    if not file_path.is_absolute():
        file_path = base_dir / file_path
    if not file_path.is_file():
        raise ValueError('missing external %s %s' % (kind, uri))
    return file_path.read_bytes()


def _read_accessor(document: dict, blob: bytes, accessor_index: int, base_dir: Path) -> np.ndarray:
    accessors = document.get('accessors') or []
    if accessor_index < 0 or accessor_index >= len(accessors):
        raise ValueError('accessor %s is missing' % accessor_index)
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict):
        raise ValueError('accessor %s is not an object' % accessor_index)
    if accessor.get('bufferView') is None:
        raise ValueError('sparse or zero-fill accessor %s is not supported' % accessor_index)
    views = document.get('bufferViews') or []
    view_index = int(accessor['bufferView'])
    if view_index < 0 or view_index >= len(views) or not isinstance(views[view_index], dict):
        raise ValueError('bufferView %s is missing' % view_index)
    view = views[view_index]
    data = _buffer_bytes(document, blob, int(view.get('buffer') or 0), base_dir)
    component = int(accessor['componentType'])
    if component not in _DTYPES or accessor.get('type') not in _COMPONENTS:
        raise ValueError('unsupported accessor type')
    dtype = np.dtype(_DTYPES[component])
    components = _COMPONENTS[accessor['type']]
    count = int(accessor.get('count') or 0)
    width = components * dtype.itemsize
    stride = int(view.get('byteStride') or width)
    start = int(view.get('byteOffset') or 0) + int(accessor.get('byteOffset') or 0)
    if count < 0 or stride < width or start < 0:
        raise ValueError('accessor %s has a bad layout' % accessor_index)
    need = start + (count - 1) * stride + width if count else start
    if need > len(data):
        raise ValueError('accessor %s overruns its buffer' % accessor_index)
    if stride == width:
        raw = data[start:start + count * width]
        array = np.frombuffer(raw, dtype=dtype).reshape((count, components)) if count else np.zeros((0, components))
    else:
        rows = [np.frombuffer(data[start + index * stride:start + index * stride + width], dtype=dtype) for index in range(count)]
        array = np.vstack(rows) if rows else np.zeros((0, components))
    values = np.array(array, dtype=np.float64, copy=True)
    if accessor.get('normalized') and component in _INTEGER_MAX:
        values = values / float(_INTEGER_MAX[component])
    return values


def _local_matrix(node: dict) -> np.ndarray:
    if 'matrix' in node:
        return np.asarray(node['matrix'], dtype=np.float64).reshape((4, 4), order='F')
    translation = np.eye(4, dtype=np.float64)
    translation[:3, 3] = np.asarray(node.get('translation') or [0, 0, 0], dtype=np.float64)
    rotation = _quaternion_matrix(node.get('rotation') or [0, 0, 0, 1])
    scale = np.eye(4, dtype=np.float64)
    scales = node.get('scale') or [1, 1, 1]
    scale[0, 0], scale[1, 1], scale[2, 2] = (float(scales[0]), float(scales[1]), float(scales[2]))
    return translation @ rotation @ scale


def _quaternion_matrix(quaternion) -> np.ndarray:
    x, y, z, w = (float(value) for value in quaternion)
    matrix = np.eye(4, dtype=np.float64)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    matrix[0, 0] = 1 - 2 * (yy + zz)
    matrix[0, 1] = 2 * (xy - wz)
    matrix[0, 2] = 2 * (xz + wy)
    matrix[1, 0] = 2 * (xy + wz)
    matrix[1, 1] = 1 - 2 * (xx + zz)
    matrix[1, 2] = 2 * (yz - wx)
    matrix[2, 0] = 2 * (xz - wy)
    matrix[2, 1] = 2 * (yz + wx)
    matrix[2, 2] = 1 - 2 * (xx + yy)
    return matrix


def _world_matrices(nodes: list) -> list[np.ndarray]:
    parent = {index: None for index in range(len(nodes))}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get('children') or []:
            child_index = int(child)
            if 0 <= child_index < len(nodes):
                parent[child_index] = index
    cache: dict[int, np.ndarray] = {}

    def world(index: int, stack: tuple[int, ...]) -> np.ndarray:
        if index in cache:
            return cache[index]
        if index in stack:
            raise ValueError('node hierarchy contains a cycle')
        local = _local_matrix(nodes[index] if isinstance(nodes[index], dict) else {})
        owner = parent.get(index)
        cache[index] = local if owner is None else world(owner, stack + (index,)) @ local
        return cache[index]

    return [world(index, ()) for index in range(len(nodes))]


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    homo = np.concatenate([points, np.ones((len(points), 1), dtype=np.float64)], axis=1)
    return (np.asarray(matrix, dtype=np.float64) @ homo.T).T[:, :3]


def _triangle_count(primitive: dict, document: dict) -> int:
    accessors = document.get('accessors') or []
    mode = int(primitive.get('mode', 4))
    if 'indices' in primitive:
        count = int(accessors[int(primitive['indices'])].get('count') or 0)
    else:
        attributes = primitive.get('attributes') or {}
        if 'POSITION' not in attributes:
            return 0
        count = int(accessors[int(attributes['POSITION'])].get('count') or 0)
    if mode == 4:
        return count // 3
    if mode in (5, 6):
        return max(0, count - 2)
    return 0


def _bone_name(name: str) -> str:
    text = str(name).lower().replace('mixamorig:', '').replace('mixamorig_', '')
    text = text.replace('bip01_', '').replace('bip001_', '').replace('bip_', '')
    return re.sub(r'[^a-z0-9]', '', text)


def _limb_side(name: str) -> str | None:
    if 'left' in name:
        return 'left'
    if 'right' in name:
        return 'right'
    tokens = ('arm', 'leg', 'thigh', 'hand', 'shin', 'calf', 'upleg', 'forearm', 'shoulder', 'finger', 'thumb')
    if len(name) > 1 and name[0] in 'lr' and any(token in name[1:] for token in tokens):
        return 'left' if name[0] == 'l' else 'right'
    if len(name) > 1 and name[-1] in 'lr' and any(token in name[:-1] for token in tokens):
        return 'left' if name[-1] == 'l' else 'right'
    return None


def _is_finger(name: str) -> bool:
    if 'spring' in name:
        return False
    return any(token in name for token in ('thumb', 'index', 'middle', 'ring', 'pinky', 'finger'))


def _humanoid_slots(names: list[str]) -> dict[str, bool]:
    folded = [_bone_name(name) for name in names if name]
    present = {slot: False for slot in HUMANOID_SLOTS}
    for name in folded:
        side = _limb_side(name)
        if name in {'hips', 'pelvis', 'hip'} or name.endswith('hips') or name.endswith('pelvis'):
            present['hips'] = True
        if 'spine' in name:
            present['spine'] = True
        if 'neck' in name:
            present['neck'] = True
        if name == 'head' or (name.startswith('head') and not name.startswith(('headlight', 'headlamp', 'header'))):
            present['head'] = True
        if side == 'left' and any(token in name for token in ('arm', 'shoulder')):
            present['left_arm'] = True
        if side == 'right' and any(token in name for token in ('arm', 'shoulder')):
            present['right_arm'] = True
        if side == 'left' and any(token in name for token in ('leg', 'thigh', 'shin', 'calf', 'upleg')):
            present['left_leg'] = True
        if side == 'right' and any(token in name for token in ('leg', 'thigh', 'shin', 'calf', 'upleg')):
            present['right_leg'] = True
    return present


def _is_eyes_name(name: str) -> bool:
    compact = canonical_name(name)
    if 'eyebrow' in compact:
        return False
    if 'eyes' in compact:
        return True
    return compact in {'eye', 'lefteye', 'righteye', 'eyeleft', 'eyeright', 'eyel', 'eyer'}


def _is_eyebrow_name(name: str) -> bool:
    compact = canonical_name(name)
    return 'eyebrow' in compact or compact in {'brows', 'eyebrows'}


def _round_list(values: np.ndarray) -> list[float]:
    return [float(round(float(value), 6)) for value in values]


def _image_payload(document: dict, blob: bytes, image: dict, base_dir: Path) -> tuple[str, bytes, str | None]:
    """Return ``(kind, data, problem)``. ``kind`` is embedded, external, or missing."""
    if not isinstance(image, dict):
        return 'missing', b'', 'image is not an object'
    uri = image.get('uri')
    if uri:
        uri = str(uri)
        if uri.startswith('data:'):
            try:
                return 'embedded', _load_uri_bytes(uri, base_dir, 'image'), None
            except ValueError as exc:
                return 'missing', b'', str(exc)
        file_path = Path(uri)
        if not file_path.is_absolute():
            file_path = base_dir / file_path
        if not file_path.is_file():
            return 'missing', b'', 'missing external image %s' % uri
        data = file_path.read_bytes()
        if len(data) == 0:
            return 'external', data, 'zero-size texture %s' % uri
        return 'external', data, None
    if image.get('bufferView') is None:
        return 'missing', b'', 'image has no uri or bufferView'
    views = document.get('bufferViews') or []
    view_index = int(image['bufferView'])
    if view_index < 0 or view_index >= len(views) or not isinstance(views[view_index], dict):
        return 'missing', b'', 'image bufferView is missing'
    view = views[view_index]
    try:
        data = _buffer_bytes(document, blob, int(view.get('buffer') or 0), base_dir)
    except ValueError as exc:
        return 'missing', b'', str(exc)
    start = int(view.get('byteOffset') or 0)
    length = int(view.get('byteLength') or 0)
    if length <= 0:
        return 'embedded', b'', 'zero-size texture image[%s]' % view_index
    if start < 0 or start + length > len(data):
        return 'embedded', b'', 'texture image overruns its buffer'
    payload = data[start:start + length]
    if len(payload) == 0:
        return 'embedded', payload, 'zero-size texture image[%s]' % view_index
    return 'embedded', payload, None


def _decode_image_ok(payload: bytes, mime: str | None) -> str | None:
    if not payload:
        return 'zero-size texture'
    mime = (mime or '').lower()
    if mime and not mime.startswith('image/'):
        return None
    try:
        from PIL import Image
    except ImportError:
        return None
    import io
    try:
        with Image.open(io.BytesIO(payload)) as image:
            width, height = image.size
    except Exception:
        return 'unreadable texture'
    if width <= 0 or height <= 0:
        return 'zero-size texture'
    return None


class AssetValidator:
    """Run the seven asset checks and grade a production tier."""

    def __init__(self, triangle_budgets: dict | None = None, height_limits: dict | None = None):
        self.triangle_budgets = dict(TRIANGLE_BUDGETS)
        if triangle_budgets:
            self.triangle_budgets.update(triangle_budgets)
        self.height_limits = dict(HEIGHT_LIMITS_M)
        if height_limits:
            self.height_limits.update(height_limits)

    def validate_glb(self, path: str | Path, category: str = 'character') -> dict:
        category = str(category).lower()
        if category not in CATEGORIES:
            raise ValueError('category must be one of %s' % (CATEGORIES,))
        source = Path(path)
        try:
            document, blob, base_dir = read_gltf(source)
        except (OSError, ValueError, json.JSONDecodeError, struct.error) as exc:
            return self._rejected_load(source, category, exc)
        try:
            return self._validate_document(source, document, blob, base_dir, category)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return self._rejected_load(source, category, exc)

    def _rejected_load(self, path: Path, category: str, exc: BaseException) -> dict:
        issue = 'GLB failed to load: %s' % exc
        report = _empty_report(path, category)
        report['issues'] = [issue]
        report['tier'] = 'REJECT'
        report['tier_reason'] = 'file did not load'
        report['production_allowed'] = False
        report['checks']['topology'] = {'status': 'fail', 'detail': issue}
        report['capabilities']['tier'] = 'REJECT'
        return report

    def _validate_document(self, path: Path, document: dict, blob: bytes, base_dir: Path, category: str) -> dict:
        report = _empty_report(path, category)
        issues: list[str] = []
        notes: list[str] = []
        nodes = document.get('nodes') if isinstance(document.get('nodes'), list) else []
        meshes = document.get('meshes') if isinstance(document.get('meshes'), list) else []
        worlds = _world_matrices(nodes) if nodes else []
        mesh_nodes: dict[int, list[int]] = {}
        for index, node in enumerate(nodes):
            if isinstance(node, dict) and node.get('mesh') is not None:
                mesh_nodes.setdefault(int(node['mesh']), []).append(index)

        triangles = 0
        vertices = 0
        nan_vertices = 0
        finite_points: list[np.ndarray] = []
        topology_errors: list[str] = []
        skinning = {
            'skinned': False,
            'attributes_present': True,
            'zero_weight_vertices': 0,
            'nan_weights': 0,
            'out_of_range_joints': 0,
            'missing_attributes': [],
        }
        morph_names: list[str] = []
        mesh_names: list[str] = []

        if not meshes:
            topology_errors.append('mesh is empty')

        for mesh_index, mesh in enumerate(meshes):
            if not isinstance(mesh, dict):
                topology_errors.append('mesh %s is not an object' % mesh_index)
                continue
            mesh_names.append(str(mesh.get('name') or ''))
            primitives = mesh.get('primitives') if isinstance(mesh.get('primitives'), list) else []
            if not primitives:
                topology_errors.append('mesh %s has no primitives' % mesh_index)
            node_indexes = mesh_nodes.get(mesh_index) or []
            matrices = [worlds[index] for index in node_indexes] or [np.eye(4, dtype=np.float64)]
            joint_count = _joint_count_for_mesh(document, nodes, mesh_index)
            skinned_mesh = joint_count is not None
            if skinned_mesh:
                skinning['skinned'] = True
            for primitive in primitives:
                if not isinstance(primitive, dict):
                    topology_errors.append('mesh %s has a bad primitive' % mesh_index)
                    continue
                try:
                    triangles += _triangle_count(primitive, document)
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    topology_errors.append('mesh %s indices could not be read: %s' % (mesh_index, exc))
                attributes = primitive.get('attributes') if isinstance(primitive.get('attributes'), dict) else {}
                if 'POSITION' not in attributes:
                    topology_errors.append('mesh %s is missing POSITION' % mesh_index)
                    positions = np.zeros((0, 3), dtype=np.float64)
                else:
                    try:
                        positions = _read_accessor(document, blob, int(attributes['POSITION']), base_dir)
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        topology_errors.append('mesh %s positions could not be read: %s' % (mesh_index, exc))
                        positions = np.zeros((0, 3), dtype=np.float64)
                if positions.ndim != 2 or positions.shape[1] < 3:
                    topology_errors.append('mesh %s positions are not VEC3' % mesh_index)
                    positions = np.zeros((0, 3), dtype=np.float64)
                else:
                    positions = positions[:, :3]
                vertices += int(positions.shape[0])
                if positions.size:
                    finite = np.isfinite(positions).all(axis=1)
                    nan_vertices += int((~finite).sum())
                    if finite.any():
                        for matrix in matrices:
                            finite_points.append(_transform_points(positions[finite], matrix))
                    if (~finite).any():
                        topology_errors.append('vertex positions contain NaN or Inf')
                if skinned_mesh:
                    _inspect_skinning(document, blob, base_dir, attributes, positions.shape[0], int(joint_count or 0), skinning)
                morph_names.extend(_morph_names(mesh, primitive))

        for node in nodes:
            if isinstance(node, dict) and node.get('name'):
                mesh_names.append(str(node['name']))

        budget = int(self.triangle_budgets[category])
        if triangles <= 0 and 'mesh is empty' not in topology_errors:
            topology_errors.append('mesh is empty')
        if triangles > budget:
            topology_errors.append('triangle count %s exceeds %s budget %s' % (triangles, category, budget))
        if nan_vertices:
            message = 'vertex positions contain NaN or Inf'
            if message not in topology_errors:
                topology_errors.append(message)

        bounds_min = bounds_max = None
        height = y_min = None
        if finite_points:
            stacked = np.vstack(finite_points)
            bounds_min = stacked.min(axis=0)
            bounds_max = stacked.max(axis=0)
            height = float(bounds_max[1] - bounds_min[1])
            y_min = float(bounds_min[1])

        height_range = self.height_limits[category]
        scale_errors: list[str] = []
        if height is None or not np.isfinite(height):
            scale_errors.append('bounds could not be measured')
        else:
            lo, hi = height_range
            if height < lo - _MEASURE_EPS or height > hi + _MEASURE_EPS:
                scale_errors.append('height %.3f m outside %.2f-%.2f m' % (height, lo, hi))
        ground_required = category in GROUNDED_CATEGORIES
        grounded = y_min is not None and np.isfinite(y_min) and abs(y_min) <= GROUND_TOLERANCE_M + _MEASURE_EPS
        if ground_required and not grounded:
            shown = 'unknown' if y_min is None else '%.3f' % y_min
            scale_errors.append('not grounded: y_min=%s m' % shown)

        textures = _inspect_textures(document, blob, base_dir)
        if not textures['ok']:
            issues.extend(textures['problems'])

        joint_names = _joint_names(document, nodes)
        slots = _humanoid_slots(joint_names) if category == 'character' else {slot: False for slot in HUMANOID_SLOTS}
        missing_slots = [slot for slot, found in slots.items() if not found]
        finger_count = sum(1 for name in joint_names if _is_finger(_bone_name(name)))
        has_fingers = finger_count >= FINGER_MIN_BONES
        humanoid = category == 'character' and not missing_slots
        joint_count = len(joint_names)

        if category == 'character':
            if not joint_names:
                issues.append('missing required rig')
            elif missing_slots:
                issues.append('missing required rig: %s' % ', '.join(missing_slots))
            if not has_fingers:
                notes.append('missing finger bones')
        if skinning['skinned'] and not skinning['attributes_present']:
            issues.append('skinned mesh is missing JOINTS_0 / WEIGHTS_0')
        if skinning['nan_weights']:
            issues.append('NaN skin weights')
        if skinning['zero_weight_vertices']:
            issues.append('zero-weight vertices: %s' % skinning['zero_weight_vertices'])
        if skinning['out_of_range_joints']:
            issues.append('joint index out of range')

        found_morphs = list(dict.fromkeys(morph_names))
        missing_morphs = missing_facial_morphs(found_morphs)
        has_blendshapes = not missing_morphs and bool(found_morphs)
        has_eyes = any(_is_eyes_name(name) for name in mesh_names)
        has_brows = any(_is_eyebrow_name(name) for name in mesh_names)
        if category == 'character' and not has_blendshapes:
            notes.append('missing BlendShapes')

        topology_ok = not topology_errors and triangles > 0 and nan_vertices == 0
        textures_ok = bool(textures['ok'])
        scale_ok = not scale_errors
        weights_ok = (not skinning['skinned']) or (
            skinning['attributes_present']
            and skinning['zero_weight_vertices'] == 0
            and skinning['nan_weights'] == 0
            and skinning['out_of_range_joints'] == 0
        )
        rig_ok = humanoid and skinning['skinned'] and skinning['attributes_present']
        issues.extend(topology_errors)
        issues.extend(scale_errors)
        issues = list(dict.fromkeys(issues))
        notes = list(dict.fromkeys(notes))

        tier, reason = _grade(
            category,
            loaded=True,
            topology=topology_ok,
            textures=textures_ok,
            scale=scale_ok,
            nan_vertices=nan_vertices > 0,
            weights_ok=weights_ok,
            rig=rig_ok,
            fingers=has_fingers,
            blendshapes=has_blendshapes,
        )
        if tier == 'REJECT' and reason not in issues and not any(reason in issue for issue in issues):
            issues.insert(0, reason)

        report.update({
            'tier': tier,
            'tier_reason': reason,
            'production_allowed': tier != 'REJECT',
            'triangle_count': int(triangles),
            'triangle_budget': budget,
            'vertex_count': int(vertices),
            'nan_vertices': int(nan_vertices),
            'bounds_min': _round_list(bounds_min) if bounds_min is not None else None,
            'bounds_max': _round_list(bounds_max) if bounds_max is not None else None,
            'height_m': None if height is None else float(round(height, 6)),
            'y_min': None if y_min is None else float(round(y_min, 6)),
            'joint_count': int(joint_count),
            'has_fingers': bool(has_fingers) if category == 'character' else False,
            'blendshapes': bool(has_blendshapes) if category == 'character' else False,
            'issues': issues,
            'notes': notes,
            'checks': {
                'topology': {
                    'status': 'pass' if topology_ok else 'fail',
                    'triangle_count': int(triangles),
                    'budget': budget,
                    'nan_vertices': int(nan_vertices),
                    'vertex_count': int(vertices),
                    'problems': topology_errors,
                },
                'textures': textures,
                'scale': {
                    'status': 'pass' if scale_ok else 'fail',
                    'height_m': None if height is None else float(round(height, 6)),
                    'y_min': None if y_min is None else float(round(y_min, 6)),
                    'height_range': [float(height_range[0]), float(height_range[1])],
                    'ground_required': ground_required,
                    'grounded': bool(grounded),
                    'problems': scale_errors,
                },
                'skeleton': {
                    'status': 'skip' if category != 'character' else ('pass' if humanoid else 'fail'),
                    'joint_count': int(joint_count),
                    'slots': slots,
                    'humanoid': bool(humanoid),
                    'has_fingers': bool(has_fingers),
                    'finger_bones': int(finger_count),
                    'missing_slots': missing_slots,
                },
                'skinning': {
                    'status': 'skip' if not skinning['skinned'] else ('pass' if weights_ok else 'fail'),
                    'skinned': bool(skinning['skinned']),
                    'zero_weight_vertices': int(skinning['zero_weight_vertices']),
                    'nan_weights': int(skinning['nan_weights']),
                    'out_of_range_joints': int(skinning['out_of_range_joints']),
                    'missing_attributes': list(skinning['missing_attributes']),
                },
                'face': {
                    'status': 'skip' if category != 'character' else ('pass' if has_blendshapes else 'fail'),
                    'morph_targets': found_morphs,
                    'missing_morphs': missing_morphs,
                    'has_blendshapes': bool(has_blendshapes),
                    'has_eyes_mesh': bool(has_eyes),
                    'has_eyebrows_mesh': bool(has_brows),
                },
            },
            'capabilities': {
                'tier': tier,
                'humanoid': bool(humanoid),
                'has_fingers': bool(has_fingers) if category == 'character' else False,
                'blendshapes': bool(has_blendshapes) if category == 'character' else False,
                'morph_targets': found_morphs if category == 'character' else [],
                'missing_morphs': missing_morphs if category == 'character' else [],
                'has_eyes_mesh': bool(has_eyes),
                'has_eyebrows_mesh': bool(has_brows),
                'grounded': bool(grounded),
                'skinned': bool(skinning['skinned']),
                'height_m': None if height is None else float(round(height, 6)),
                'triangle_count': int(triangles),
            },
        })
        return report


def _joint_count_for_mesh(document: dict, nodes: list, mesh_index: int) -> int | None:
    skins = document.get('skins') if isinstance(document.get('skins'), list) else []
    for node in nodes:
        if not isinstance(node, dict) or node.get('mesh') != mesh_index or node.get('skin') is None:
            continue
        skin_index = int(node['skin'])
        if 0 <= skin_index < len(skins) and isinstance(skins[skin_index], dict):
            return len(skins[skin_index].get('joints') or [])
        return 0
    return None


def _joint_names(document: dict, nodes: list) -> list[str]:
    skins = document.get('skins') if isinstance(document.get('skins'), list) else []
    names = []
    seen = set()
    for skin in skins:
        if not isinstance(skin, dict):
            continue
        for joint in skin.get('joints') or []:
            joint_index = int(joint)
            if joint_index in seen or joint_index < 0 or joint_index >= len(nodes):
                continue
            seen.add(joint_index)
            node = nodes[joint_index]
            if isinstance(node, dict):
                names.append(str(node.get('name') or ''))
    return names


def _inspect_skinning(document, blob, base_dir, attributes, vertex_count, joint_count, skinning) -> None:
    missing = [name for name in ('JOINTS_0', 'WEIGHTS_0') if name not in attributes]
    if missing:
        skinning['attributes_present'] = False
        for name in missing:
            if name not in skinning['missing_attributes']:
                skinning['missing_attributes'].append(name)
        return
    try:
        joints = _read_accessor(document, blob, int(attributes['JOINTS_0']), base_dir)
        weights = _read_accessor(document, blob, int(attributes['WEIGHTS_0']), base_dir)
    except (KeyError, IndexError, TypeError, ValueError):
        skinning['attributes_present'] = False
        skinning['missing_attributes'].extend(['JOINTS_0', 'WEIGHTS_0'])
        return
    if joints.shape[0] != vertex_count or weights.shape[0] != vertex_count or joints.shape[1] < 4 or weights.shape[1] < 4:
        skinning['attributes_present'] = False
        skinning['missing_attributes'].append('JOINTS_0/WEIGHTS_0 count')
        return
    joints = np.rint(joints[:, :4])
    weights = weights[:, :4]
    for index in range(vertex_count):
        weight_row = weights[index]
        joint_row = joints[index]
        if not np.isfinite(weight_row).all():
            skinning['nan_weights'] += 1
        elif float(weight_row.sum()) <= 1e-6:
            skinning['zero_weight_vertices'] += 1
        if joint_count <= 0 or (joint_row < 0).any() or (joint_row >= joint_count).any():
            skinning['out_of_range_joints'] += 1


def _morph_names(mesh: dict, primitive: dict) -> list[str]:
    targets = primitive.get('targets') if isinstance(primitive.get('targets'), list) else []
    if not targets:
        return []
    names = []
    for source in (
        (mesh.get('extras') or {}).get('targetNames') if isinstance(mesh.get('extras'), dict) else None,
        (primitive.get('extras') or {}).get('targetNames') if isinstance(primitive.get('extras'), dict) else None,
    ):
        if isinstance(source, list) and source:
            names = [str(item) for item in source]
            break
    found = []
    for index, _target in enumerate(targets):
        if index < len(names) and names[index]:
            found.append(names[index])
    return found


def _inspect_textures(document: dict, blob: bytes, base_dir: Path) -> dict:
    materials = document.get('materials') if isinstance(document.get('materials'), list) else []
    textures = document.get('textures') if isinstance(document.get('textures'), list) else []
    images = document.get('images') if isinstance(document.get('images'), list) else []
    base_refs = []
    normal_refs = []
    for material in materials:
        if not isinstance(material, dict):
            continue
        pbr = material.get('pbrMetallicRoughness') if isinstance(material.get('pbrMetallicRoughness'), dict) else {}
        if isinstance(pbr.get('baseColorTexture'), dict) and pbr['baseColorTexture'].get('index') is not None:
            base_refs.append(int(pbr['baseColorTexture']['index']))
        if isinstance(material.get('normalTexture'), dict) and material['normalTexture'].get('index') is not None:
            normal_refs.append(int(material['normalTexture']['index']))
    problems = []
    base_kind = 'missing'
    normal_kind = 'absent'
    if not base_refs:
        problems.append('missing baseColor texture')
    else:
        base_kind, base_problems = _texture_health(base_refs, textures, images, document, blob, base_dir, 'baseColor')
        problems.extend(base_problems)
    if normal_refs:
        normal_kind, normal_problems = _texture_health(normal_refs, textures, images, document, blob, base_dir, 'normal')
        problems.extend(normal_problems)
    problems = list(dict.fromkeys(problems))
    return {
        'status': 'pass' if not problems else 'fail',
        'ok': not problems,
        'base_color': base_kind,
        'normal': normal_kind,
        'problems': problems,
    }


def _texture_health(indexes, textures, images, document, blob, base_dir, label: str) -> tuple[str, list[str]]:
    problems = []
    kinds = []
    for index in indexes:
        if index < 0 or index >= len(textures) or not isinstance(textures[index], dict):
            problems.append('missing %s texture' % label)
            kinds.append('missing')
            continue
        source = textures[index].get('source')
        if source is None or int(source) < 0 or int(source) >= len(images):
            problems.append('missing %s image' % label)
            kinds.append('missing')
            continue
        image = images[int(source)]
        kind, payload, problem = _image_payload(document, blob, image, base_dir)
        kinds.append(kind)
        if problem:
            problems.append(problem)
            continue
        decoded = _decode_image_ok(payload, image.get('mimeType') if isinstance(image, dict) else None)
        if decoded:
            problems.append('%s %s' % (decoded, label))
    kind = 'embedded' if 'embedded' in kinds and 'external' not in kinds else ('external' if 'external' in kinds else 'missing')
    if 'embedded' in kinds and 'external' in kinds:
        kind = 'external'
    return kind, problems


def _grade(category: str, **flags) -> tuple[str, str]:
    reasons = []
    if not flags['loaded']:
        return 'REJECT', 'file did not load'
    if not flags['topology'] or flags['nan_vertices']:
        reasons.append('broken topology')
    if not flags['textures']:
        reasons.append('missing textures')
    if not flags['weights_ok']:
        reasons.append('invalid skin weights')
    if not flags['scale']:
        reasons.append('severe scale error')
    if category == 'character' and not flags['rig']:
        reasons.append('missing required rig')
    if reasons:
        return 'REJECT', '; '.join(reasons)
    if category == 'character':
        if flags['fingers'] and flags['blendshapes']:
            return 'TIER_A', 'production-ready close-up character'
        missing = []
        if not flags['blendshapes']:
            missing.append('BlendShapes')
        if not flags['fingers']:
            missing.append('fingers')
        return 'TIER_B', 'rigged character without ' + ' or '.join(missing)
    return 'TIER_C', 'valid static %s' % category


def _empty_report(path: Path, category: str) -> dict:
    return {
        'path': path.resolve().as_posix() if path.exists() else Path(path).as_posix(),
        'file_name': Path(path).name,
        'category': category,
        'tier': 'REJECT',
        'tier_reason': '',
        'production_allowed': False,
        'triangle_count': 0,
        'triangle_budget': int(TRIANGLE_BUDGETS.get(category, 0)),
        'vertex_count': 0,
        'nan_vertices': 0,
        'bounds_min': None,
        'bounds_max': None,
        'height_m': None,
        'y_min': None,
        'joint_count': 0,
        'has_fingers': False,
        'blendshapes': False,
        'issues': [],
        'notes': [],
        'checks': {
            'topology': {'status': 'fail'},
            'textures': {'status': 'fail', 'ok': False, 'base_color': 'missing', 'normal': 'absent', 'problems': []},
            'scale': {'status': 'fail'},
            'skeleton': {'status': 'skip'},
            'skinning': {'status': 'skip'},
            'face': {'status': 'skip'},
        },
        'capabilities': {'tier': 'REJECT'},
    }


def _license_record(license_value, source: str, author: str, uri: str, notes: str) -> dict:
    if isinstance(license_value, dict):
        return {
            'spdx': str(license_value.get('spdx') or license_value.get('name') or license_value.get('id') or 'unspecified'),
            'source': str(license_value.get('source') or source or ''),
            'author': str(license_value.get('author') or author or ''),
            'uri': str(license_value.get('uri') or license_value.get('url') or uri or ''),
            'notes': str(license_value.get('notes') or notes or ''),
        }
    return {
        'spdx': str(license_value or 'unspecified'),
        'source': str(source or ''),
        'author': str(author or ''),
        'uri': str(uri or ''),
        'notes': str(notes or ''),
    }


def infer_category(path: Path) -> str | None:
    aliases = {
        'character': 'character', 'characters': 'character', 'chars': 'character',
        'prop': 'prop', 'props': 'prop',
        'rover': 'rover', 'rovers': 'rover',
        'environment': 'environment', 'environments': 'environment', 'set': 'environment', 'sets': 'environment',
    }
    for part in reversed(path.parts[:-1]):
        category = aliases.get(part.lower())
        if category:
            return category
    return None


class AssetRegistry:
    """Persist validated assets as structured JSON."""

    def __init__(self, path: str | Path | None = None, validator: AssetValidator | None = None):
        self.path = Path(path) if path is not None else default_registry_path()
        self.validator = validator or AssetValidator()
        self.version = 1
        self.assets: list[dict] = []
        if self.path.is_file():
            self.load()

    def register(
        self,
        glb_path: str | Path,
        *,
        category: str,
        license: str | dict,
        asset_id: str | None = None,
        source: str = '',
        author: str = '',
        uri: str = '',
        notes: str = '',
    ) -> dict:
        source_path = Path(glb_path)
        report = self.validator.validate_glb(source_path, category)
        if not report['production_allowed']:
            raise AssetRejected(report)
        identity = asset_id or source_path.stem
        record = {
            'asset_id': str(identity),
            'path': source_path.resolve().as_posix(),
            'file_name': source_path.name,
            'sha256': sha256_file(source_path),
            'bytes': source_path.stat().st_size,
            'category': report['category'],
            'tier': report['tier'],
            'license': _license_record(license, source, author, uri, notes),
            'capabilities': copy.deepcopy(report['capabilities']),
            'validation': copy.deepcopy(report),
            'registered_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        self.assets = [asset for asset in self.assets if asset.get('asset_id') != record['asset_id']]
        self.assets.append(record)
        self.save()
        return copy.deepcopy(record)

    def get(self, asset_id: str) -> dict | None:
        for asset in self.assets:
            if asset.get('asset_id') == asset_id:
                return copy.deepcopy(asset)
        return None

    def query(self, *, category: str | None = None, min_tier: str | None = None, include_rejected: bool = False) -> list[dict]:
        if min_tier is not None and min_tier not in TIER_RANK:
            raise ValueError('min_tier must be one of %s' % (TIERS,))
        if category is not None:
            category = category.lower()
            if category not in CATEGORIES:
                raise ValueError('category must be one of %s' % (CATEGORIES,))
        matches = []
        for asset in self.assets:
            tier = str(asset.get('tier') or 'REJECT')
            if not include_rejected and tier == 'REJECT':
                continue
            if category is not None and asset.get('category') != category:
                continue
            if min_tier is not None and not tier_meets(tier, min_tier):
                continue
            matches.append(copy.deepcopy(asset))
        return matches

    def scan(self, directory: str | Path, *, category: str | None = None, license: str | dict = 'unspecified',
             source: str = '', author: str = '', uri: str = '') -> dict:
        root = Path(directory)
        if not root.is_dir():
            raise FileNotFoundError('scan directory not found: %s' % root)
        summary = {'registered': [], 'rejected': [], 'errors': []}
        files = sorted(path for path in root.rglob('*') if path.is_file() and path.suffix.lower() in {'.glb', '.gltf'})
        for file_path in files:
            file_category = category or infer_category(file_path)
            if file_category is None:
                summary['errors'].append({'path': file_path.as_posix(), 'error': 'category could not be inferred'})
                continue
            sidecar = _sidecar_license(file_path)
            file_license = sidecar or license
            file_source = str(sidecar.get('source') or source) if isinstance(sidecar, dict) else source
            file_author = str(sidecar.get('author') or author) if isinstance(sidecar, dict) else author
            file_uri = str(sidecar.get('uri') or sidecar.get('url') or uri) if isinstance(sidecar, dict) else uri
            identity = _scan_asset_id(file_path, root)
            try:
                record = self.register(
                    file_path,
                    category=file_category,
                    license=file_license,
                    asset_id=identity,
                    source=file_source,
                    author=file_author,
                    uri=file_uri,
                )
            except AssetRejected as exc:
                summary['rejected'].append({
                    'path': file_path.resolve().as_posix(),
                    'asset_id': identity,
                    'category': file_category,
                    'tier': exc.report.get('tier'),
                    'issues': list(exc.report.get('issues') or []),
                    'sha256': sha256_file(file_path) if file_path.is_file() else '',
                })
                continue
            summary['registered'].append(record['asset_id'])
        return summary

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = {
            'version': self.version,
            'assets': sorted(self.assets, key=lambda asset: str(asset.get('asset_id'))),
        }
        self.path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def load(self) -> dict:
        document = json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(document, dict) or not isinstance(document.get('assets'), list):
            raise ValueError('registry is missing an assets array: %s' % self.path)
        self.version = int(document.get('version') or 1)
        self.assets = [asset for asset in document['assets'] if isinstance(asset, dict)]
        return document


def _sidecar_license(path: Path) -> dict | None:
    sidecar = path.with_suffix('.license.json')
    if not sidecar.is_file():
        return None
    payload = json.loads(sidecar.read_text(encoding='utf-8'))
    return payload if isinstance(payload, dict) else None


def _scan_asset_id(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).with_suffix('')
        return relative.as_posix().replace('/', '_')
    except ValueError:
        return path.stem


def validate_glb(path: str | Path, category: str = 'character') -> dict:
    return AssetValidator().validate_glb(path, category)


def _print_json(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Validate 3D assets and maintain the production registry.')
    sub = parser.add_subparsers(dest='command', required=True)

    validate = sub.add_parser('validate', help='validate one GLB/glTF and print the report')
    validate.add_argument('glb', type=Path)
    validate.add_argument('--category', required=True, choices=CATEGORIES)
    validate.add_argument('--registry', type=Path, default=None, help='also register the asset when it is not REJECT')

    scan = sub.add_parser('scan', help='validate a directory and register assets that pass')
    scan.add_argument('directory', type=Path)
    scan.add_argument('--category', choices=CATEGORIES, default=None)
    scan.add_argument('--license', default='unspecified')
    scan.add_argument('--source', default='')
    scan.add_argument('--author', default='')
    scan.add_argument('--registry', type=Path, default=None)

    args = parser.parse_args(argv)
    if args.command == 'validate':
        report = validate_glb(args.glb, args.category)
        if args.registry is not None and report['production_allowed']:
            registry = AssetRegistry(args.registry)
            registry.register(args.glb, category=args.category, license='unspecified')
        _print_json(report)
        return 0 if report['production_allowed'] else 1

    registry = AssetRegistry(args.registry)
    summary = registry.scan(
        args.directory,
        category=args.category,
        license=args.license,
        source=args.source,
        author=args.author,
    )
    summary['registry'] = registry.path.resolve().as_posix()
    _print_json(summary)
    return 0 if not summary['rejected'] and not summary['errors'] else 1


if __name__ == '__main__':
    sys.exit(main())
