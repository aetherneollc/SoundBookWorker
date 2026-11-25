#!/usr/bin/env python3
"""Normalize a GLB for Godot: meters, Y-up, feet on y=0, XZ centered, optional height.

Skinned glTF files (a non-empty ``skins`` array) are not decimated. Static meshes
over ``--max-tris`` are decimated with Blender when ``blender`` is on PATH, and
otherwise with trimesh + fast_simplification while keeping materials and UVs.

    python tools/normalize_glb.py input.glb output.glb --target-height 1.7 --rotate-y-deg -90 --max-tris 10000

Positive ``--rotate-y-deg`` is a right-handed rotation about +Y. A mesh whose
front points along +X needs ``--rotate-y-deg -90`` to face +Z.

``--smooth-normals`` welds area-weighted vertex normals across coincident
positions, including UV seams, without changing vertex count, UVs, or skin
weights. Pass it when a mesh should shade smooth instead of faceted.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import trimesh

BLENDER_DECIMATE_SCRIPT = r'''
import sys
import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    src, dst, max_tris_text = argv
    max_tris = int(max_tris_text)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=src)

    def tri_count(obj):
        mesh = obj.data
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)

    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    if not meshes:
        raise SystemExit("no meshes")
    total = sum(tri_count(obj) for obj in meshes)
    if total > max_tris and total > 0:
        ratio = max(float(max_tris) / float(total), 1.0 / float(total))
        ratio = min(1.0, ratio)
        for obj in meshes:
            if tri_count(obj) <= 4:
                continue
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            mod = obj.modifiers.new(name="Decimate", type="DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            try:
                with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                    bpy.ops.object.modifier_apply(modifier=mod.name)
            except Exception:
                bpy.ops.object.modifier_apply(modifier=mod.name)
    try:
        bpy.ops.export_scene.gltf(
            filepath=dst,
            export_format="GLB",
            export_yup=True,
            export_texcoords=True,
            export_materials="EXPORT",
        )
    except TypeError:
        bpy.ops.export_scene.gltf(filepath=dst)


if __name__ == "__main__":
    main()
'''

_GLB_MAGIC = b'glTF'
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_DTYPES = {5120: '<i1', 5121: '<u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_COMPONENTS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}


def normalize_glb(
    input_path: Path,
    output_path: Path,
    *,
    target_height: float | None = None,
    rotate_y_deg: float = 0.0,
    max_tris: int = 10000,
    smooth_normals: bool = False,
) -> dict:
    """Write a normalized GLB and return the JSON report fields."""
    source = Path(input_path)
    destination = Path(output_path)
    if not source.is_file():
        raise FileNotFoundError('GLB not found: %s' % source)
    if max_tris < 4:
        raise ValueError('max_tris must be at least 4')
    if target_height is not None and target_height <= 0:
        raise ValueError('target height must be positive')

    document, blob = read_glb(source)
    input_tris = count_tris(document)
    skinned = glb_is_skinned(document)
    had_textures = glb_has_textures(document)
    backend = None
    skipped = None

    if skinned:
        if smooth_normals:
            blob = _smooth_glb_normals(document, blob)
        new_doc, new_blob, bounds_min, bounds_max, height = _normalize_skinned(
            document, blob, target_height, rotate_y_deg,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_glb(destination, new_doc, new_blob)
        skipped = 'skinned'
        output_doc, _output_blob = read_glb(destination)
        output_tris = count_tris(output_doc)
        has_textures = glb_has_textures(output_doc)
    else:
        load_path = source
        blender_file = None
        if input_tris > max_tris:
            blender_file = _try_blender_decimate(source, max_tris, had_textures)
            if blender_file is not None:
                load_path = blender_file
                backend = 'blender'
        try:
            meshes = _load_meshes(load_path)
            _apply_matrix(meshes, rotation_y(rotate_y_deg))
            face_count = sum(len(mesh.faces) for mesh in meshes)
            if face_count > max_tris:
                meshes = _decimate_meshes(meshes, max_tris)
                backend = 'blender+trimesh' if backend == 'blender' else 'trimesh'
            bounds_min, bounds_max = _bounds_of(meshes)
            height = float(bounds_max[1] - bounds_min[1])
            _apply_matrix(meshes, _scale_translate_matrix(bounds_min, bounds_max, target_height))
            bounds_min, bounds_max = _bounds_of(meshes)
            height = float(bounds_max[1] - bounds_min[1])
            if smooth_normals:
                for mesh in meshes:
                    compute_smooth_normals_by_position(mesh)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _export_meshes(meshes, destination, include_normals=True if smooth_normals else None)
        finally:
            if blender_file is not None:
                blender_file.unlink(missing_ok=True)
        output_doc, _output_blob = read_glb(destination)
        output_tris = count_tris(output_doc)
        has_textures = glb_has_textures(output_doc)

    return {
        'input_path': str(source.resolve()),
        'output_path': str(destination.resolve()),
        'input_tris': int(input_tris),
        'output_tris': int(output_tris),
        'bounds_min': _clean_vec(bounds_min),
        'bounds_max': _clean_vec(bounds_max),
        'height_m': _clean_float(height),
        'skinned': bool(skinned),
        'has_textures': bool(has_textures),
        'decimation_skipped': skipped,
        'decimation_backend': backend,
        'smooth_normals': bool(smooth_normals),
    }


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = Path(path).read_bytes()
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise ValueError('not a GLB: %s' % path)
    _version, _length = struct.unpack_from('<II', data, 4)
    offset = 12
    document = None
    blob = b''
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from('<II', data, offset)
        offset += 8
        payload = data[offset:offset + chunk_length]
        offset += chunk_length
        if chunk_type == _JSON_CHUNK:
            document = json.loads(payload.decode('utf-8'))
        elif chunk_type == _BIN_CHUNK:
            blob = payload
    if not isinstance(document, dict):
        raise ValueError('GLB is missing JSON: %s' % path)
    return document, blob


def write_glb(path: Path, document: dict, blob: bytes) -> None:
    json_bytes = json.dumps(document, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    json_bytes += b' ' * ((4 - (len(json_bytes) % 4)) % 4)
    blob = bytes(blob)
    blob += b'\x00' * ((4 - (len(blob) % 4)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    header = struct.pack('<4sII', _GLB_MAGIC, 2, total)
    json_header = struct.pack('<II', len(json_bytes), _JSON_CHUNK)
    bin_header = struct.pack('<II', len(blob), _BIN_CHUNK)
    Path(path).write_bytes(header + json_header + json_bytes + bin_header + blob)


def glb_is_skinned(document: dict) -> bool:
    skins = document.get('skins')
    return isinstance(skins, list) and len(skins) > 0


def glb_has_textures(document: dict) -> bool:
    if document.get('textures') or document.get('images'):
        return True
    for material in document.get('materials') or []:
        if not isinstance(material, dict):
            continue
        pbr = material.get('pbrMetallicRoughness') or {}
        if isinstance(pbr, dict) and pbr.get('baseColorTexture'):
            return True
        if material.get('emissiveTexture') or material.get('normalTexture'):
            return True
    return False


def count_tris(document: dict) -> int:
    total = 0
    accessors = document.get('accessors') or []
    for mesh in document.get('meshes') or []:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get('primitives') or []:
            if not isinstance(primitive, dict):
                continue
            mode = int(primitive.get('mode', 4))
            if 'indices' in primitive:
                count = int(accessors[primitive['indices']].get('count') or 0)
            else:
                attributes = primitive.get('attributes') or {}
                if 'POSITION' not in attributes:
                    continue
                count = int(accessors[attributes['POSITION']].get('count') or 0)
            if mode == 4:
                total += count // 3
            elif mode in (5, 6):
                total += max(0, count - 2)
    return total


def rotation_y(degrees: float) -> np.ndarray:
    """Right-handed rotation about +Y, column vectors."""
    radians = math.radians(float(degrees))
    cosine = math.cos(radians)
    sine = math.sin(radians)
    matrix = np.eye(4, dtype=np.float64)
    matrix[0, 0] = cosine
    matrix[0, 2] = sine
    matrix[2, 0] = -sine
    matrix[2, 2] = cosine
    return matrix


def _normalize_skinned(document, blob, target_height, rotate_y_deg):
    points = _world_positions(document, blob)
    if len(points) == 0:
        raise ValueError('skinned GLB has no vertex positions')
    matrix = _fit_matrix(points, target_height, rotate_y_deg)
    final = _transform_points(points, matrix)
    document = json.loads(json.dumps(document))
    _add_root(document, matrix)
    bounds_min = final.min(axis=0)
    bounds_max = final.max(axis=0)
    height = float(bounds_max[1] - bounds_min[1])
    return document, blob, bounds_min, bounds_max, height


def _fit_matrix(points: np.ndarray, target_height: float | None, rotate_y_deg: float) -> np.ndarray:
    rotation = rotation_y(rotate_y_deg)
    rotated = _transform_points(points, rotation)
    mins = rotated.min(axis=0)
    maxs = rotated.max(axis=0)
    return _scale_translate_matrix(mins, maxs, target_height) @ rotation


def _scale_translate_matrix(mins, maxs, target_height: float | None) -> np.ndarray:
    height = float(maxs[1] - mins[1])
    if height <= 1e-8:
        raise ValueError('mesh height is zero')
    scale = 1.0 if target_height is None else float(target_height) / height
    if scale <= 0:
        raise ValueError('target height must be positive')
    scaled_mins = np.asarray(mins, dtype=np.float64) * scale
    scaled_maxs = np.asarray(maxs, dtype=np.float64) * scale
    translation = np.array([
        -0.5 * (scaled_mins[0] + scaled_maxs[0]),
        -scaled_mins[1],
        -0.5 * (scaled_mins[2] + scaled_maxs[2]),
    ], dtype=np.float64)
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] *= scale
    matrix[:3, 3] = translation
    return matrix


def _add_root(document: dict, matrix: np.ndarray) -> None:
    nodes = document.setdefault('nodes', [])
    if not isinstance(nodes, list):
        raise ValueError('glTF nodes are not an array')
    if not nodes and document.get('meshes'):
        for index in range(len(document['meshes'])):
            nodes.append({'mesh': index})
    scenes = document.get('scenes')
    if not isinstance(scenes, list) or not scenes:
        scenes = [{'nodes': list(range(len(nodes)))}]
        document['scenes'] = scenes
    scene_index = int(document.get('scene') or 0)
    scene = scenes[scene_index]
    old_roots = list(scene.get('nodes') or [])
    nodes.append({
        'name': 'NormalizeRoot',
        'matrix': _matrix_to_gltf(matrix),
        'children': old_roots,
    })
    scene['nodes'] = [len(nodes) - 1]
    document['scene'] = scene_index


def _world_positions(document: dict, blob: bytes) -> np.ndarray:
    nodes = document.get('nodes') or []
    worlds = _world_matrices(nodes)
    chunks = []
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or node.get('mesh') is None:
            continue
        mesh = document['meshes'][node['mesh']]
        for primitive in mesh.get('primitives') or []:
            attributes = primitive.get('attributes') or {}
            if 'POSITION' not in attributes:
                continue
            positions = _read_accessor(document, blob, attributes['POSITION'])
            chunks.append(_transform_points(positions, worlds[index]))
    if not chunks:
        return np.zeros((0, 3), dtype=np.float64)
    return np.vstack(chunks)


def _world_matrices(nodes: list) -> list[np.ndarray]:
    parent = {index: None for index in range(len(nodes))}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        for child in node.get('children') or []:
            parent[int(child)] = index
    cache: dict[int, np.ndarray] = {}

    def world(index: int) -> np.ndarray:
        if index in cache:
            return cache[index]
        local = _local_matrix(nodes[index] if isinstance(nodes[index], dict) else {})
        owner = parent.get(index)
        cache[index] = local if owner is None else world(owner) @ local
        return cache[index]

    return [world(index) for index in range(len(nodes))]


def _local_matrix(node: dict) -> np.ndarray:
    if 'matrix' in node:
        return _matrix_from_gltf(node['matrix'])
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


def _matrix_from_gltf(values) -> np.ndarray:
    return np.asarray(values, dtype=np.float64).reshape((4, 4), order='F')


def _matrix_to_gltf(matrix: np.ndarray) -> list[float]:
    flat = np.asarray(matrix, dtype=np.float64).reshape((4, 4)).flatten(order='F')
    return [float(value) for value in flat]


def _read_accessor(document: dict, blob: bytes, accessor_index: int) -> np.ndarray:
    accessor = document['accessors'][accessor_index]
    if accessor.get('bufferView') is None:
        raise ValueError('sparse glTF accessors are not supported')
    view = document['bufferViews'][accessor['bufferView']]
    data = _buffer_bytes(document, blob, int(view.get('buffer') or 0))
    dtype = np.dtype(_DTYPES[int(accessor['componentType'])])
    components = _COMPONENTS[accessor['type']]
    count = int(accessor['count'])
    width = components * dtype.itemsize
    stride = int(view.get('byteStride') or width)
    start = int(view.get('byteOffset') or 0) + int(accessor.get('byteOffset') or 0)
    if stride == width:
        raw = data[start:start + count * width]
        array = np.frombuffer(raw, dtype=dtype).reshape((count, components))
    else:
        rows = []
        for index in range(count):
            offset = start + index * stride
            rows.append(np.frombuffer(data[offset:offset + width], dtype=dtype))
        array = np.vstack(rows)
    return np.array(array, dtype=np.float64, copy=True)


def _buffer_bytes(document: dict, blob: bytes, buffer_index: int) -> bytes:
    buffer = document['buffers'][buffer_index]
    uri = buffer.get('uri')
    if not uri:
        return blob
    if isinstance(uri, str) and uri.startswith('data:'):
        import base64
        payload = uri.split(',', 1)[1]
        return base64.b64decode(payload)
    raise ValueError('external glTF buffers are not supported')


def _transform_points(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if len(points) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    homo = np.concatenate([points, np.ones((len(points), 1), dtype=np.float64)], axis=1)
    return (np.asarray(matrix, dtype=np.float64) @ homo.T).T[:, :3]


def _load_meshes(path: Path) -> list[trimesh.Trimesh]:
    loaded = trimesh.load_scene(path, process=False)
    meshes = []
    for geom in loaded.dump():
        if isinstance(geom, trimesh.Trimesh) and len(geom.faces):
            meshes.append(geom)
    if not meshes:
        raise ValueError('GLB does not contain a triangle mesh')
    return meshes


def _apply_matrix(meshes: list[trimesh.Trimesh], matrix: np.ndarray) -> None:
    if np.allclose(matrix, np.eye(4)):
        return
    for mesh in meshes:
        mesh.apply_transform(matrix)


def _bounds_of(meshes: list[trimesh.Trimesh]) -> tuple[np.ndarray, np.ndarray]:
    points = np.vstack([mesh.vertices for mesh in meshes])
    return points.min(axis=0), points.max(axis=0)


def _decimate_meshes(meshes: list[trimesh.Trimesh], max_tris: int) -> list[trimesh.Trimesh]:
    total = sum(len(mesh.faces) for mesh in meshes)
    if total <= max_tris:
        return meshes
    targets = []
    for mesh in meshes:
        if len(mesh.faces) == 0:
            targets.append(0)
        else:
            targets.append(max(4, int(len(mesh.faces) * max_tris / total)))
    while sum(targets) > max_tris and any(target > 4 for target in targets):
        index = max(range(len(targets)), key=lambda item: targets[item])
        if targets[index] <= 4:
            break
        targets[index] -= 1
    simplified = []
    for mesh, target in zip(meshes, targets):
        simplified.append(_decimate_mesh(mesh, target) if len(mesh.faces) > target else mesh)
    return simplified


def _decimate_mesh(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    import fast_simplification

    target_faces = max(4, int(target_faces))
    current = mesh
    for aggression in (7.0, 9.0, 10.0):
        if len(current.faces) <= target_faces:
            break
        try:
            simplified = _simplify_once(current, target_faces, aggression, fast_simplification)
        except Exception:
            continue
        if len(simplified.faces) < len(current.faces):
            current = simplified
    return current


def _simplify_once(mesh: trimesh.Trimesh, target_faces: int, aggression: float, fast_simplification) -> trimesh.Trimesh:
    points = np.ascontiguousarray(mesh.vertices, dtype=np.float64)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.int32)
    kind, values, material = _vertex_payload(mesh)
    _unused_points, _unused_faces, collapses = fast_simplification.simplify(
        points,
        faces,
        target_count=int(target_faces),
        agg=float(aggression),
        return_collapses=True,
    )
    decimated_points, decimated_faces, mapping = fast_simplification.replay_simplification(points, faces, collapses)
    simplified = trimesh.Trimesh(
        vertices=np.array(decimated_points, dtype=np.float64, copy=True),
        faces=np.array(decimated_faces, dtype=np.int64, copy=True),
        process=False,
    )
    if kind == 'texture' and values is not None and material is not None:
        mapped = _average_attribute(values, mapping, len(simplified.vertices))
        if mapped is None:
            mapped = _planar_uv(simplified.vertices)
        simplified.visual = trimesh.visual.texture.TextureVisuals(uv=mapped, material=material)
    elif kind == 'vertex' and values is not None:
        mapped = _average_attribute(values.astype(np.float64), mapping, len(simplified.vertices))
        if mapped is not None:
            colors = np.clip(np.rint(mapped), 0, 255).astype(np.uint8)
            simplified.visual = trimesh.visual.color.ColorVisuals(vertex_colors=colors)
    return simplified


def _vertex_payload(mesh: trimesh.Trimesh):
    visual = mesh.visual
    kind = getattr(visual, 'kind', None)
    if kind == 'texture' and getattr(visual, 'uv', None) is not None and len(visual.uv) == len(mesh.vertices):
        return 'texture', np.array(visual.uv, dtype=np.float64), visual.material
    if kind == 'vertex' and getattr(visual, 'vertex_colors', None) is not None and len(visual.vertex_colors) == len(mesh.vertices):
        return 'vertex', np.array(visual.vertex_colors), None
    material = getattr(visual, 'material', None)
    image = getattr(material, 'baseColorTexture', None) or getattr(material, 'image', None)
    if image is not None:
        return 'texture', None, material
    return None, None, None


def _average_attribute(values: np.ndarray, mapping: np.ndarray, count: int):
    if len(values) != len(mapping):
        return None
    width = values.shape[1] if values.ndim == 2 else 1
    flat = values.reshape((len(values), width))
    accumulated = np.zeros((count, width), dtype=np.float64)
    hits = np.zeros(count, dtype=np.int32)
    for old_index, new_index in enumerate(mapping):
        index = int(new_index)
        if index < 0 or index >= count:
            continue
        accumulated[index] += flat[old_index]
        hits[index] += 1
    if not np.any(hits):
        return None
    return accumulated / np.maximum(hits, 1)[:, None]


def _planar_uv(vertices: np.ndarray) -> np.ndarray:
    uv = np.zeros((len(vertices), 2), dtype=np.float64)
    span_x = float(np.ptp(vertices[:, 0])) or 1.0
    span_y = float(np.ptp(vertices[:, 1])) or 1.0
    uv[:, 0] = (vertices[:, 0] - float(vertices[:, 0].min())) / span_x
    uv[:, 1] = (vertices[:, 1] - float(vertices[:, 1].min())) / span_y
    return uv


def compute_smooth_normals_by_position(mesh, decimals: int = 5) -> np.ndarray:
    """Area-weighted smooth normals, welded by rounded position.

    Vertices that share a position (UV seams included) receive the same unit
    normal: the normalized sum of incident face cross-products. Vertex count,
    UVs, and skin weights are left unchanged. The result is stored on
    ``mesh.vertex_normals`` so glTF export keeps it instead of recomputing
    split normals.
    """
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    normals = np.zeros((len(vertices), 3), dtype=np.float64)
    if len(vertices) == 0 or len(faces) == 0:
        if len(vertices):
            normals[:, 1] = 1.0
        _assign_vertex_normals(mesh, normals)
        return normals.copy()
    if faces.ndim != 2 or faces.shape[1] != 3:
        raise ValueError('smooth normals expect triangle faces')

    triangles = vertices[faces]
    crossed = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    scale = 10.0 ** int(decimals)
    keys = np.rint(vertices * scale).astype(np.int64)
    keys = np.ascontiguousarray(keys)
    packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
    _unique, inverse = np.unique(packed, return_inverse=True)
    accum = np.zeros((int(inverse.max()) + 1, 3), dtype=np.float64)
    groups = inverse[faces]
    g0, g1, g2 = groups[:, 0], groups[:, 1], groups[:, 2]
    np.add.at(accum, g0, crossed)
    differ_1 = g1 != g0
    np.add.at(accum, g1[differ_1], crossed[differ_1])
    differ_2 = (g2 != g0) & (g2 != g1)
    np.add.at(accum, g2[differ_2], crossed[differ_2])

    lengths = np.linalg.norm(accum, axis=1)
    valid = lengths > 1e-12
    accum[valid] /= lengths[valid, None]
    accum[~valid] = (0.0, 1.0, 0.0)
    normals = np.ascontiguousarray(accum[inverse], dtype=np.float64)
    _assign_vertex_normals(mesh, normals)
    return normals.copy()


def _assign_vertex_normals(mesh, normals: np.ndarray) -> None:
    if normals.shape != np.asarray(mesh.vertices).shape:
        raise ValueError('smooth normals do not match the vertex count')
    mesh.vertex_normals = normals
    cached = getattr(mesh, '_cache', None)
    store = getattr(cached, 'cache', None)
    if isinstance(store, dict) and 'vertex_normals' not in store:
        store['vertex_normals'] = np.ascontiguousarray(normals, dtype=np.float64)


def _smooth_glb_normals(document: dict, blob: bytes) -> bytes:
    """Rewrite NORMAL accessors in a skinned GLB. Other attributes stay put."""
    buffers = document.get('buffers') or []
    if not buffers:
        return bytes(blob)
    declared = int(buffers[0].get('byteLength') or len(blob))
    raw = bytearray(blob[:declared])
    for mesh in document.get('meshes') or []:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get('primitives') or []:
            if not isinstance(primitive, dict) or int(primitive.get('mode', 4)) != 4:
                continue
            attributes = primitive.get('attributes') or {}
            if 'POSITION' not in attributes:
                continue
            positions = _read_accessor(document, bytes(raw), attributes['POSITION'])
            if 'indices' in primitive:
                indices = np.asarray(_read_accessor(document, bytes(raw), primitive['indices'])).reshape(-1)
            else:
                indices = np.arange(len(positions), dtype=np.int64)
            if len(indices) < 3 or len(positions) == 0:
                continue
            usable = len(indices) - (len(indices) % 3)
            faces = indices[:usable].astype(np.int64).reshape((-1, 3))
            tri = trimesh.Trimesh(vertices=positions, faces=faces, process=False)
            normals = compute_smooth_normals_by_position(tri)
            if 'NORMAL' in attributes:
                _overwrite_vec3(document, raw, int(attributes['NORMAL']), normals)
            else:
                _append_vec3_normal(document, raw, primitive, normals)
    buffers[0]['byteLength'] = len(raw)
    return bytes(raw)


def _overwrite_vec3(document: dict, raw: bytearray, accessor_index: int, values: np.ndarray) -> None:
    accessor = document['accessors'][accessor_index]
    if int(accessor.get('componentType') or 0) != 5126 or accessor.get('type') != 'VEC3':
        raise ValueError('NORMAL accessor is not float32 VEC3')
    view = document['bufferViews'][accessor['bufferView']]
    if int(view.get('buffer') or 0) != 0:
        raise ValueError('external NORMAL buffers are not supported')
    count = int(accessor['count'])
    if count != len(values):
        raise ValueError('NORMAL count does not match the vertex count')
    stride = int(view.get('byteStride') or 12)
    start = int(view.get('byteOffset') or 0) + int(accessor.get('byteOffset') or 0)
    packed = np.ascontiguousarray(values, dtype=np.float32)
    if stride == 12 and start % 4 == 0:
        target = np.ndarray((count, 3), dtype='<f4', buffer=raw, offset=start)
        target[:] = packed
    else:
        for index in range(count):
            offset = start + index * stride
            raw[offset:offset + 12] = packed[index].tobytes()
    if 'min' in accessor or 'max' in accessor:
        accessor['min'] = [float(channel) for channel in packed.min(axis=0)]
        accessor['max'] = [float(channel) for channel in packed.max(axis=0)]


def _append_vec3_normal(document: dict, raw: bytearray, primitive: dict, values: np.ndarray) -> None:
    packed = np.ascontiguousarray(values, dtype=np.float32)
    while len(raw) % 4:
        raw.append(0)
    offset = len(raw)
    raw.extend(packed.tobytes())
    views = document.setdefault('bufferViews', [])
    accessors = document.setdefault('accessors', [])
    views.append({
        'buffer': 0,
        'byteOffset': offset,
        'byteLength': int(packed.nbytes),
        'target': 34962,
    })
    accessors.append({
        'bufferView': len(views) - 1,
        'byteOffset': 0,
        'componentType': 5126,
        'count': int(len(packed)),
        'type': 'VEC3',
    })
    primitive.setdefault('attributes', {})['NORMAL'] = len(accessors) - 1


def _export_meshes(meshes: list[trimesh.Trimesh], path: Path, include_normals: bool | None = None) -> None:
    kwargs = {}
    if include_normals is not None:
        kwargs['include_normals'] = include_normals
    if len(meshes) == 1:
        meshes[0].export(path, **kwargs)
        return
    scene = trimesh.Scene()
    for index, mesh in enumerate(meshes):
        scene.add_geometry(mesh, geom_name='geom_%d' % index, node_name='node_%d' % index)
    scene.export(path, **kwargs)


def _try_blender_decimate(source: Path, max_tris: int, had_textures: bool) -> Path | None:
    blender = shutil.which('blender')
    if not blender:
        return None
    script_path = None
    destination = None
    try:
        with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False, encoding='utf-8') as handle:
            handle.write(BLENDER_DECIMATE_SCRIPT)
            script_path = Path(handle.name)
        with tempfile.NamedTemporaryFile(suffix='.glb', delete=False) as handle:
            destination = Path(handle.name)
        completed = subprocess.run(
            [blender, '--background', '--python', str(script_path), '--', str(source), str(destination), str(max_tris)],
            check=False,
            capture_output=True,
            timeout=600,
        )
        if completed.returncode != 0 or not destination.is_file() or destination.stat().st_size < 20:
            destination.unlink(missing_ok=True)
            return None
        document, _blob = read_glb(destination)
        if count_tris(document) <= 0:
            destination.unlink(missing_ok=True)
            return None
        if had_textures and not glb_has_textures(document):
            destination.unlink(missing_ok=True)
            return None
        if count_tris(document) > max_tris:
            # Keep it only when it actually reduced something; trimesh finishes the rest.
            original_doc, _original_blob = read_glb(source)
            if count_tris(document) >= count_tris(original_doc):
                destination.unlink(missing_ok=True)
                return None
        return destination
    except Exception:
        if destination is not None:
            destination.unlink(missing_ok=True)
        return None
    finally:
        if script_path is not None:
            script_path.unlink(missing_ok=True)


def _clean_float(value: float) -> float:
    number = float(value)
    if abs(number) < 1e-9:
        return 0.0
    return number


def _clean_vec(values) -> list[float]:
    return [_clean_float(value) for value in values]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Normalize a GLB for Godot 3D: meters, Y-up, grounded, XZ centered.',
    )
    parser.add_argument('input_path')
    parser.add_argument('output_path')
    parser.add_argument('--target-height', type=float, default=None, help='uniform scale to this height in meters')
    parser.add_argument('--rotate-y-deg', type=float, default=0.0, help='right-handed degrees about +Y; -90 turns +X front to +Z')
    parser.add_argument('--max-tris', type=int, default=10000, help='decimate static meshes above this triangle count')
    parser.add_argument(
        '--smooth-normals',
        action='store_true',
        help='weld area-weighted vertex normals across coincident positions and UV seams',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = normalize_glb(
            Path(args.input_path),
            Path(args.output_path),
            target_height=args.target_height,
            rotate_y_deg=args.rotate_y_deg,
            max_tris=args.max_tris,
            smooth_normals=args.smooth_normals,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if report.get('decimation_skipped'):
        print('decimation_skipped="%s"' % report['decimation_skipped'], file=sys.stderr)
    json.dump(report, sys.stdout, indent=2, ensure_ascii=True)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
