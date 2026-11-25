"""Skeleton-preserving face pass for already-rigged Mixamo/Meshy GLBs.

TRELLIS meshes are unwelded and flat-shaded, and the single-image texture
spends a few dozen pixels on the face. This rewrites only mesh geometry:

* every skinned triangle primitive gets smooth normals, welded by position
  (``--weld`` metres) and area weighted, with a crease angle so thin
  back-to-back sheets keep their own side;
* front-facing face skin on the head bone is split into its own primitive
  with material ``FaceMat``. Its NORMALs are bent toward an ellipsoid fitted
  to the face (spherical normal transfer), so a soft toon ramp reads as one
  round face instead of folded paper;
* face vertices carry ``TEXCOORD_1``: a front projection of the face oval
  into one cell of a 2x2 expression atlas (cell space [0,1]^2; neutral,
  blink, talk_mid, talk_open at offsets (0,0) (.5,0) (0,.5) (.5,.5)).
  ``TEXCOORD_0`` keeps the original UVs, so FaceMat still shows the
  original texture until an atlas is plugged in.

Skins, JOINTS_0/WEIGHTS_0, inverse bind matrices, nodes, animations and the
hierarchy are copied unchanged. Writes ``<out>.face.json`` (oval, eye
landmarks, cell layout) next to the output for the atlas painter.

    python tools/fix_character_face.py media/assets3d/linlan_rig/rigged.glb \
        media/assets3d/linlan_rig/rigged_face.glb [--face-atlas atlas.png]
        [--face-meta meta.json] [--bake-neutral cell.png] [--self-test]
"""
from __future__ import annotations

import argparse
import io
import json
import math
import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GODOT_DEFAULT = Path(r'C:\Users\Hero1\AppData\Local\Microsoft\WinGet\Packages'
                     r'\GodotEngine.GodotEngine_Microsoft.Winget.Source_8wekyb3d8bbwe\godot_console.exe')
FACE_MATERIAL = 'FaceMat'
CELLS = {'neutral': [0.0, 0.0], 'blink': [0.5, 0.0], 'talk_mid': [0.0, 0.5], 'talk_open': [0.5, 0.5]}
# Where the face oval's bounding box lands inside one cell, unless --face-meta says otherwise.
DEFAULT_FACE_BOX = [0.06, 0.05, 0.94, 0.97]

COMPONENTS = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}
GL_ARRAY_BUFFER, GL_ELEMENT_ARRAY_BUFFER = 34962, 34963


# ------------------------------------------------------------------ GLB io


def read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    magic, version, _length = struct.unpack_from('<4sII', data, 0)
    if magic != b'glTF' or version != 2:
        raise ValueError(f'{path} is not a glTF 2.0 binary')
    offset, doc, binary = 12, None, b''
    while offset < len(data):
        size, kind = struct.unpack_from('<II', data, offset)
        chunk = data[offset + 8: offset + 8 + size]
        if kind == 0x4E4F534A:
            doc = json.loads(chunk.decode('utf-8'))
        elif kind == 0x004E4942:
            binary = bytes(chunk)
        offset += 8 + size
    if doc is None:
        raise ValueError(f'{path} has no JSON chunk')
    return doc, binary


def write_glb(path: Path, doc: dict, binary: bytes) -> None:
    text = json.dumps(doc, separators=(',', ':')).encode('utf-8')
    text += b' ' * (-len(text) % 4)
    binary += b'\x00' * (-len(binary) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    out = struct.pack('<4sII', b'glTF', 2, total)
    out += struct.pack('<II', len(text), 0x4E4F534A) + text
    out += struct.pack('<II', len(binary), 0x004E4942) + binary
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


def read_accessor(doc: dict, binary: bytes, index: int) -> np.ndarray:
    acc = doc['accessors'][index]
    if 'sparse' in acc:
        raise ValueError('sparse accessors are not supported')
    dtype = np.dtype(COMPONENTS[acc['componentType']]).newbyteorder('<')
    width = WIDTH[acc['type']]
    count = acc['count']
    view = doc['bufferViews'][acc['bufferView']]
    start = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
    stride = view.get('byteStride', 0) or dtype.itemsize * width
    if stride == dtype.itemsize * width:
        flat = np.frombuffer(binary, dtype=dtype, count=count * width, offset=start)
        return flat.reshape(count, width).copy()
    rows = np.empty((count, width), dtype=dtype)
    for i in range(count):
        rows[i] = np.frombuffer(binary, dtype=dtype, count=width, offset=start + i * stride)
    return rows


class Packer:
    """Re-packs the BIN chunk: copies the views still in use, appends new ones."""

    def __init__(self, doc: dict, binary: bytes):
        self.doc, self.binary = doc, binary
        self.blob = bytearray()
        self.views: list[dict] = []

    def add(self, data: bytes, target: int | None = None, stride: int | None = None) -> int:
        self.blob += b'\x00' * (-len(self.blob) % 4)
        view = {'buffer': 0, 'byteOffset': len(self.blob), 'byteLength': len(data)}
        if target is not None:
            view['target'] = target
        if stride:
            view['byteStride'] = stride
        self.blob += data
        self.views.append(view)
        return len(self.views) - 1

    def copy_view(self, index: int) -> int:
        old = self.doc['bufferViews'][index]
        start = old.get('byteOffset', 0)
        return self.add(self.binary[start:start + old['byteLength']], old.get('target'), old.get('byteStride'))


def accessor_for(packer: Packer, array: np.ndarray, gltf_type: str, target: int | None,
                 normalized: bool = False, bounds: bool = False) -> dict:
    kinds = {np.dtype(v): k for k, v in COMPONENTS.items()}
    array = np.ascontiguousarray(array)
    acc = {
        'bufferView': packer.add(array.astype(array.dtype.newbyteorder('<')).tobytes(), target),
        'componentType': kinds[array.dtype],
        'count': int(array.shape[0]),
        'type': gltf_type,
    }
    if normalized:
        acc['normalized'] = True
    if bounds:
        flat = array.reshape(array.shape[0], -1)
        acc['min'] = [float(v) for v in flat.min(axis=0)]
        acc['max'] = [float(v) for v in flat.max(axis=0)]
    return acc


# -------------------------------------------------------------- geometry


def face_normals(corners: np.ndarray) -> np.ndarray:
    """Unnormalised (2 x area) normal per triangle, from (T, 3, 3) corner positions."""
    return np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])


def _unit(v: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(length, 1e-12)


def weld_groups(points: np.ndarray, weld: float) -> np.ndarray:
    keys = np.round(points / weld).astype(np.int64)
    _, group = np.unique(keys, axis=0, return_inverse=True)
    return group.reshape(-1)


def smooth_normals(corners: np.ndarray, weld: float, crease_deg: float) -> np.ndarray:
    """Per-corner normals (T*3, 3): area-weighted average of the face normals that
    share the corner's welded position and bend less than ``crease_deg`` from it."""
    tri_count = corners.shape[0]
    fn = face_normals(corners)
    fn_unit = _unit(fn)
    group = weld_groups(corners.reshape(-1, 3), weld)
    tri_of = np.repeat(np.arange(tri_count), 3)
    order = np.argsort(group, kind='stable')
    sorted_group = group[order]
    starts = np.searchsorted(sorted_group, sorted_group, side='left')
    ends = np.searchsorted(sorted_group, sorted_group, side='right')
    sizes = ends - starts
    # Every (corner, member of its weld group) pair, built without a Python loop.
    owner = np.repeat(np.arange(order.size), sizes)
    offsets = np.arange(owner.size) - np.repeat(np.cumsum(sizes) - sizes, sizes)
    member = order[starts[owner] + offsets]
    corner = order[owner]
    t_corner, t_member = tri_of[corner], tri_of[member]
    keep = np.einsum('ij,ij->i', fn_unit[t_corner], fn_unit[t_member]) >= math.cos(math.radians(crease_deg))
    accum = np.zeros((tri_count * 3, 3))
    np.add.at(accum, corner[keep], fn[t_member[keep]])
    out = _unit(accum)
    bad = np.linalg.norm(accum, axis=1) < 1e-12
    out[bad] = fn_unit[tri_of[bad]]
    return out.astype(np.float32)


def triangle_adjacency(corners: np.ndarray, weld: float) -> list[list[int]]:
    """Edge neighbours per triangle, on welded positions."""
    group = weld_groups(corners.reshape(-1, 3), weld).reshape(-1, 3)
    edges: dict[tuple[int, int], list[int]] = {}
    for t, (a, b, c) in enumerate(group):
        for u, v in ((a, b), (b, c), (c, a)):
            if u != v:
                edges.setdefault((min(u, v), max(u, v)), []).append(t)
    neighbours: list[list[int]] = [[] for _ in range(len(group))]
    for tris in edges.values():
        for t in tris:
            neighbours[t].extend(o for o in tris if o != t)
    return neighbours


def components(mask: np.ndarray, neighbours: list[list[int]]) -> list[np.ndarray]:
    seen = np.zeros(mask.size, bool)
    out = []
    for seed in np.flatnonzero(mask):
        if seen[seed]:
            continue
        stack, members = [seed], []
        seen[seed] = True
        while stack:
            t = stack.pop()
            members.append(t)
            for o in neighbours[t]:
                if mask[o] and not seen[o]:
                    seen[o] = True
                    stack.append(o)
        out.append(np.array(members))
    out.sort(key=len, reverse=True)
    return out


def sample_albedo(texture: np.ndarray, uv: np.ndarray) -> np.ndarray:
    h, w = texture.shape[:2]
    u = np.mod(uv[..., 0], 1.0)
    v = np.mod(uv[..., 1], 1.0)
    px = np.clip((u * (w - 1)).round().astype(int), 0, w - 1)
    py = np.clip((v * (h - 1)).round().astype(int), 0, h - 1)
    return texture[py, px].astype(np.float32)


# ------------------------------------------------------------ face finding


def head_joint_set(doc: dict, skin: dict) -> tuple[set[int], int]:
    """Skin joint indices of the head bone and everything below it, and the head's own index."""
    nodes = doc['nodes']
    head_node = None
    for node_index in skin['joints']:
        name = str(nodes[node_index].get('name', '')).lower().replace('mixamorig:', '').replace('mixamorig_', '')
        if name == 'head':
            head_node = node_index
            break
    if head_node is None:
        raise ValueError('no head joint in skin')
    below, stack = set(), [head_node]
    while stack:
        n = stack.pop()
        below.add(n)
        stack.extend(nodes[n].get('children', []))
    joints = skin['joints']
    return {i for i, node_index in enumerate(joints) if node_index in below}, joints.index(head_node)


def joint_position(doc: dict, binary: bytes, skin: dict, joint: int) -> np.ndarray:
    ibm = read_accessor(doc, binary, skin['inverseBindMatrices']).reshape(-1, 4, 4)
    return np.linalg.inv(ibm[joint].T.astype(np.float64))[:3, 3]


def find_face(corners, normals_flat, uv0, joints, weights, texture, head_joints, weld, head_pos: np.ndarray,
              side_lum_drop: float = 30.0) -> dict:
    head_x, head_y = float(head_pos[0]), float(head_pos[1])
    tri_count = corners.shape[0]
    head_w = np.zeros(joints.shape[0])
    for k in range(joints.shape[1]):
        head_w += np.where(np.isin(joints[:, k], list(head_joints)), weights[:, k], 0.0)
    tri_head = head_w.reshape(tri_count, 3).mean(axis=1)
    fn = _unit(face_normals(corners))
    centroid = corners.mean(axis=1)
    samples = np.concatenate([uv0, uv0.mean(axis=1, keepdims=True)], axis=1)
    lum = sample_albedo(texture, samples).mean(axis=(1, 2))

    head = tri_head > 0.5
    # Below the head joint the front of the neck is skin too, but it sits well
    # behind the chin: drop it so the oval ends at the jaw.
    front_z = float(np.percentile(centroid[head & (fn[:, 2] > 0.35), 2], 90))
    allowed = 0.05 + 0.6 * np.abs(centroid[:, 0] - head_x)
    head = head & ~((centroid[:, 1] < head_y + 0.01) & (front_z - centroid[:, 2] > allowed))
    hair_lum = float(np.median(lum[head & (fn[:, 2] < -0.3)])) if np.any(head & (fn[:, 2] < -0.3)) else float(np.percentile(lum[head], 20))
    skin_lum = float(np.percentile(lum[head & (fn[:, 2] > 0.35)], 92))
    dark = lum < hair_lum + 0.38 * (skin_lum - hair_lum)
    neighbours = triangle_adjacency(corners, weld)

    seed = head & (fn[:, 2] > 0.35) & ~dark
    parts = components(seed, neighbours)
    if not parts:
        raise ValueError('no front-facing skin on the head')
    core = parts[0]
    pts = corners[core].reshape(-1, 3)
    x0, x1 = np.percentile(pts[:, 0], [1, 99])
    y0, y1 = pts[:, 1].min(), np.percentile(pts[:, 1], 99.5)
    oval = {'cx': float((x0 + x1) / 2), 'cy': float((y0 + y1) / 2),
            'rx': float((x1 - x0) / 2 * 1.04), 'ry': float((y1 - y0) / 2 * 1.03)}

    r = np.hypot((centroid[:, 0] - oval['cx']) / oval['rx'], (centroid[:, 1] - oval['cy']) / oval['ry'])
    region = head & (fn[:, 2] > 0.25) & (r < 1.0)
    face = region & ~dark
    face_parts = components(face, neighbours)
    face = np.zeros(tri_count, bool)
    face[face_parts[0]] = True
    # Painted eyes and brows are dark too: take back any dark island the skin
    # encloses. Bangs reach the oval edge and stay with the hair.
    for part in components(region & ~face, neighbours):
        touches_edge = any(not region[o] for t in part for o in neighbours[t]) or np.any(r[part] > 0.9)
        if not touches_edge:
            face[part] = True
    # The cheeks and jaw past the oval carry the same pale face paint; left on
    # the body they show as a seam against the atlas skin. Grow over them (not
    # the darker ears, hair or neck): they land on the cell's plain-skin border.
    core_face = face.copy()
    paint_lum = float(np.median(lum[core_face]))
    side = head & ~dark & (fn[:, 2] > -0.35) & (r < 1.5) & (lum > paint_lum - side_lum_drop)
    frontier = list(np.flatnonzero(face))
    while frontier:
        nxt = []
        for t in frontier:
            for o in neighbours[t]:
                if side[o] and not face[o]:
                    face[o] = True
                    nxt.append(o)
        frontier = nxt
    front = corners[core_face].reshape(-1, 3)
    z_front = float(np.percentile(front[:, 2], 95))
    radii = np.array([oval['rx'] * 1.18, oval['ry'] * 1.08, oval['rx'] * 1.12])
    center = np.array([oval['cx'], oval['cy'] - oval['ry'] * 0.08, z_front - radii[2]])
    return {'mask': face, 'oval': oval, 'center': center, 'radii': radii,
            'hair_lum': hair_lum, 'skin_lum': skin_lum, 'head_tris': int(head.sum())}


def face_uv(points: np.ndarray, oval: dict, box: list[float]) -> np.ndarray:
    """Front (+Z) projection of the face oval's bounding box onto ``box`` in cell space."""
    u = (points[:, 0] - (oval['cx'] - oval['rx'])) / (2 * oval['rx'])
    v = ((oval['cy'] + oval['ry']) - points[:, 1]) / (2 * oval['ry'])
    uv = np.stack([box[0] + u * (box[2] - box[0]), box[1] + v * (box[3] - box[1])], axis=1)
    return np.clip(uv, 0.005, 0.995).astype(np.float32)


def proxy_normals(points: np.ndarray, smooth: np.ndarray, info: dict, strength: float) -> np.ndarray:
    radii = info['radii']
    grad = _unit((points - info['center']) / (radii ** 2))
    oval = info['oval']
    r = np.hypot((points[:, 0] - oval['cx']) / oval['rx'], (points[:, 1] - oval['cy']) / oval['ry'])
    # Full transfer in the middle of the face, easing back to the mesh normal
    # at the rim and gone on the side cheeks past it.
    w = strength * (1.0 - np.clip((r - 0.7) / 0.35, 0.0, 1.0) * 0.45) * (1.0 - np.clip((r - 1.05) / 0.35, 0.0, 1.0))
    return _unit(smooth * (1.0 - w[:, None]) + grad * w[:, None]).astype(np.float32)


def eye_landmarks(cell: np.ndarray, box: list[float]) -> list[list[float]]:
    """Centre of the darkest paint in each upper quarter of the face box, in cell UV."""
    size = cell.shape[0]
    lum = cell.astype(np.float32).mean(axis=2)
    vs, us = np.mgrid[0:size, 0:size] / (size - 1)
    fu = (us - box[0]) / (box[2] - box[0])
    fv = (vs - box[1]) / (box[3] - box[1])
    eyes = []
    for side in (fu < 0.5, fu >= 0.5):
        region = side & (fu > 0.08) & (fu < 0.92) & (fv > 0.15) & (fv < 0.62)
        if not region.any():
            continue
        dark = region & (lum <= np.percentile(lum[region], 6))
        weight = (lum.max() - lum[dark]) + 1e-3
        eyes.append([round(float(np.average(us[dark], weights=weight)), 4), round(float(np.average(vs[dark], weights=weight)), 4)])
    return eyes


def bake_neutral(path: Path | None, corners: np.ndarray, uv0: np.ndarray, uv1: np.ndarray, texture: np.ndarray, size: int = 512) -> np.ndarray:
    """Resamples the original face texture into cell space, for the atlas painter."""
    img = np.zeros((size, size, 3), np.float32)
    filled = np.zeros((size, size), bool)
    for tri in range(corners.shape[0]):
        p = uv1[tri] * (size - 1)
        lo = np.floor(p.min(axis=0)).astype(int)
        hi = np.ceil(p.max(axis=0)).astype(int)
        xs, ys = np.meshgrid(np.arange(lo[0], hi[0] + 1), np.arange(lo[1], hi[1] + 1))
        q = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float64)
        m = np.array([[p[0, 0] - p[2, 0], p[1, 0] - p[2, 0]], [p[0, 1] - p[2, 1], p[1, 1] - p[2, 1]]])
        if abs(np.linalg.det(m)) < 1e-9:
            continue
        bary = np.linalg.solve(m, (q - p[2]).T).T
        b = np.concatenate([bary, 1.0 - bary.sum(axis=1, keepdims=True)], axis=1)
        inside = np.all(b >= -0.02, axis=1)
        q, b = q[inside].astype(int), b[inside]
        ok = (q[:, 0] >= 0) & (q[:, 0] < size) & (q[:, 1] >= 0) & (q[:, 1] < size)
        q, b = q[ok], b[ok]
        src = b @ uv0[tri]
        img[q[:, 1], q[:, 0]] = sample_albedo(texture, src)
        filled[q[:, 1], q[:, 0]] = True
    if filled.any():
        skin = np.median(img[filled], axis=0)
        img[~filled] = skin
    out = np.clip(img, 0, 255).astype(np.uint8)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(out).save(path)
    return out


def render_mask_qc(path: Path, corners: np.ndarray, uv0: np.ndarray, face: np.ndarray, texture: np.ndarray,
                   size: int = 900, span: float = 0.4) -> None:
    """Front view of the head: original texture, FaceMat triangles tinted green."""
    from PIL import ImageDraw
    top = float(corners[:, :, 1].max())
    centroid = corners.mean(axis=1)
    keep = centroid[:, 1] > top - span
    order = [t for t in np.argsort(centroid[:, 2]) if keep[t]]
    colors = sample_albedo(texture, uv0.mean(axis=1))
    im = Image.new('RGB', (size, size), (255, 0, 255))
    draw = ImageDraw.Draw(im)
    scale = size / span
    for t in order:
        col = colors[t]
        if face[t]:
            col = col * 0.5 + np.array([0.0, 255.0, 0.0]) * 0.5
        pts = [((v[0] + span / 2) * scale, (top + 0.01 - v[1]) * scale) for v in corners[t]]
        draw.polygon(pts, fill=tuple(int(c) for c in col), outline=(0, 0, 0) if face[t] else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)


# ------------------------------------------------------------------ main


def process(src: Path, dst: Path, *, weld: float = 1e-4, crease_deg: float = 62.0, proxy: float = 0.8,
            face_atlas: Path | None = None, face_meta: Path | None = None, bake: Path | None = None,
            qc: Path | None = None) -> dict:
    doc, binary = read_glb(src)
    if not doc.get('skins'):
        raise ValueError(f'{src} has no skin; run this on the rigged GLB')
    box = list(DEFAULT_FACE_BOX)
    meta_in = {}
    if face_meta is not None:
        meta_in = json.loads(face_meta.read_text(encoding='utf-8'))
        box = [float(v) for v in meta_in.get('face_box', box)]

    skinned_meshes = {node['mesh']: node['skin'] for node in doc['nodes'] if 'mesh' in node and 'skin' in node}
    packer = Packer(doc, binary)
    new_accessors: list[dict] = []
    report: dict = {'source': src.as_posix(), 'output': dst.as_posix(), 'primitives': []}
    materials = doc.setdefault('materials', [])
    face_material_index = None
    face_info = None

    textures_np: dict[int, np.ndarray] = {}

    def texture_for(material_index: int | None) -> np.ndarray | None:
        if material_index is None:
            return None
        tex_ref = (materials[material_index].get('pbrMetallicRoughness') or {}).get('baseColorTexture')
        if tex_ref is None:
            return None
        image_index = doc['textures'][tex_ref['index']]['source']
        if image_index not in textures_np:
            image = doc['images'][image_index]
            view = doc['bufferViews'][image['bufferView']]
            raw = binary[view.get('byteOffset', 0): view.get('byteOffset', 0) + view['byteLength']]
            textures_np[image_index] = np.asarray(Image.open(io.BytesIO(raw)).convert('RGB'))
        return textures_np[image_index]

    # Accessors that keep their bytes: everything not owned by a rewritten primitive.
    rewritten_prims = []
    for mesh_index, mesh in enumerate(doc['meshes']):
        if mesh_index not in skinned_meshes:
            continue
        for prim in mesh['primitives']:
            if prim.get('mode', 4) == 4 and 'POSITION' in prim['attributes'] and 'targets' not in prim:
                rewritten_prims.append((mesh_index, prim))

    for mesh_index, prim in rewritten_prims:
        skin = doc['skins'][skinned_meshes[mesh_index]]
        attrs = {name: read_accessor(doc, binary, idx) for name, idx in prim['attributes'].items()}
        normalized = {name: bool(doc['accessors'][idx].get('normalized')) for name, idx in prim['attributes'].items()}
        count = attrs['POSITION'].shape[0]
        indices = read_accessor(doc, binary, prim['indices']).reshape(-1) if 'indices' in prim else np.arange(count)
        indices = indices[: indices.size - indices.size % 3].astype(np.int64)
        corner_attrs = {name: values[indices] for name, values in attrs.items()}
        corners = corner_attrs['POSITION'].astype(np.float64).reshape(-1, 3, 3)
        tri_count = corners.shape[0]
        normals = smooth_normals(corners, weld, crease_deg)
        corner_attrs['NORMAL'] = normals
        normalized['NORMAL'] = False
        corner_attrs.pop('TANGENT', None)

        texture = texture_for(prim.get('material'))
        face_mask = np.zeros(tri_count, bool)
        if texture is not None and 'TEXCOORD_0' in corner_attrs and 'JOINTS_0' in corner_attrs and face_info is None:
            try:
                head, head_joint = head_joint_set(doc, skin)
                head_pos = joint_position(doc, binary, skin, head_joint)
                face_info = find_face(corners, normals, corner_attrs['TEXCOORD_0'].reshape(-1, 3, 2),
                                      corner_attrs['JOINTS_0'], corner_attrs['WEIGHTS_0'], texture, head, weld, head_pos)
                face_mask = face_info['mask']
                if qc is not None:
                    render_mask_qc(qc, corners, corner_attrs['TEXCOORD_0'].reshape(-1, 3, 2), face_mask, texture)
            except ValueError as exc:
                print(f'face split skipped: {exc}', flush=True)

        corner_mask = np.repeat(face_mask, 3)
        groups = [('body', ~corner_mask, prim.get('material'))]
        if face_mask.any():
            points = corner_attrs['POSITION'].astype(np.float64)
            corner_attrs['NORMAL'] = np.where(corner_mask[:, None], proxy_normals(points, normals, face_info, proxy), normals)
            uv1 = np.zeros((points.shape[0], 2), np.float32)
            uv1[corner_mask] = face_uv(points[corner_mask], face_info['oval'], box)
            corner_attrs['TEXCOORD_1'] = uv1
            normalized['TEXCOORD_1'] = False
            if face_material_index is None:
                face_material_index = len(materials)
                body_tex = (materials[prim['material']].get('pbrMetallicRoughness') or {}).get('baseColorTexture')
                materials.append({
                    'name': FACE_MATERIAL,
                    'pbrMetallicRoughness': {'baseColorTexture': dict(body_tex), 'metallicFactor': 0.0, 'roughnessFactor': 0.85},
                    'extras': {'face_center': [float(v) for v in face_info['center']],
                               'face_radii': [float(v) for v in face_info['radii']], 'atlas': False},
                })
            groups.append(('face', corner_mask, face_material_index))
            cell = bake_neutral(bake, points[corner_mask].reshape(-1, 3, 3), corner_attrs['TEXCOORD_0'][corner_mask].reshape(-1, 3, 2),
                                uv1[corner_mask].reshape(-1, 3, 2), texture)
            face_info['eyes_uv'] = eye_landmarks(cell, box)

        new_prims = []
        for label, mask, material in groups:
            n = int(mask.sum())
            if n == 0:
                continue
            out_attrs = {}
            for name, values in corner_attrs.items():
                if name == 'TEXCOORD_1' and label != 'face':
                    continue
                part = values[mask]
                width = part.shape[1] if part.ndim > 1 else 1
                gltf_type = {1: 'SCALAR', 2: 'VEC2', 3: 'VEC3', 4: 'VEC4'}[width]
                acc = accessor_for(packer, part, gltf_type, GL_ARRAY_BUFFER, normalized.get(name, False), bounds=name == 'POSITION')
                new_accessors.append(acc)
                out_attrs[name] = -len(new_accessors)
            idx_dtype = np.uint16 if n < 65536 else np.uint32
            new_accessors.append(accessor_for(packer, np.arange(n, dtype=idx_dtype), 'SCALAR', GL_ELEMENT_ARRAY_BUFFER))
            out = {'attributes': out_attrs, 'indices': -len(new_accessors), 'mode': 4}
            if material is not None:
                out['material'] = material
            new_prims.append(out)
            report['primitives'].append({'mesh': mesh_index, 'part': label, 'triangles': n // 3})
        prim['_replace'] = new_prims

    # Rebuild meshes, then re-pack every untouched accessor (IBMs, animation data, images).
    for mesh in doc['meshes']:
        rebuilt = []
        for prim in mesh['primitives']:
            rebuilt.extend(prim.pop('_replace', [prim]))
        mesh['primitives'] = rebuilt

    old_accessors = doc.get('accessors', [])
    used_old: dict[int, int] = {}
    final_accessors: list[dict] = []

    def keep_old(index: int) -> int:
        if index not in used_old:
            acc = dict(old_accessors[index])
            acc['bufferView'] = packer.copy_view(acc['bufferView'])
            final_accessors.append(acc)
            used_old[index] = len(final_accessors) - 1
        return used_old[index]

    new_index: dict[int, int] = {}

    def remap(index: int) -> int:
        if index < 0:
            k = -index - 1
            if k not in new_index:
                final_accessors.append(new_accessors[k])
                new_index[k] = len(final_accessors) - 1
            return new_index[k]
        return keep_old(index)

    for mesh in doc['meshes']:
        for prim in mesh['primitives']:
            prim['attributes'] = {k: remap(v) for k, v in prim['attributes'].items()}
            if 'indices' in prim:
                prim['indices'] = remap(prim['indices'])
            for target in prim.get('targets', []):
                for k in list(target):
                    target[k] = remap(target[k])
    for skin in doc.get('skins', []):
        if 'inverseBindMatrices' in skin:
            skin['inverseBindMatrices'] = remap(skin['inverseBindMatrices'])
    for anim in doc.get('animations', []):
        for sampler in anim['samplers']:
            sampler['input'] = remap(sampler['input'])
            sampler['output'] = remap(sampler['output'])
    for image in doc.get('images', []):
        if 'bufferView' in image:
            image['bufferView'] = packer.copy_view(image['bufferView'])

    if face_atlas is not None and face_material_index is not None:
        atlas_bytes = face_atlas.read_bytes()
        Image.open(io.BytesIO(atlas_bytes)).verify()
        # Base colour stays on the original paint (texCoord 0): Godot's importer
        # swaps UV and UV2 for a material whose base colour reads texCoord 1,
        # which would put the atlas projection under the body texture. The
        # atlas rides along as an image the player picks up from the extras.
        doc.setdefault('images', []).append({'bufferView': packer.add(atlas_bytes), 'mimeType': 'image/png', 'name': 'FaceAtlas'})
        face_mat = materials[face_material_index]
        face_mat['extras']['atlas'] = True
        face_mat['extras']['atlas_image'] = len(doc['images']) - 1
        report['face_atlas'] = face_atlas.as_posix()

    doc['accessors'] = final_accessors
    doc['bufferViews'] = packer.views
    doc['buffers'] = [{'byteLength': len(packer.blob)}]
    write_glb(dst, doc, bytes(packer.blob))

    if face_info is not None:
        oval = face_info['oval']
        eyes_uv = face_info.get('eyes_uv', [])
        meta = {
            'glb': dst.name,
            'material': FACE_MATERIAL,
            'uv_set': 'TEXCOORD_1',
            'cells': CELLS,
            'cell_scale': [0.5, 0.5],
            'face_box': box,
            'oval_m': {k: round(v, 5) for k, v in oval.items()},
            'head_center_m': [round(float(v), 5) for v in face_info['center']],
            'head_radii_m': [round(float(v), 5) for v in face_info['radii']],
            'eyes_cell_uv': eyes_uv,
            'face_triangles': int(face_info['mask'].sum()),
            'note': 'cell (0,0) top-left is the oval box top-left; v grows downward (image rows)',
            **({'input_meta': meta_in} if meta_in else {}),
        }
        dst.with_suffix('.face.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
        report['face'] = {'triangles': meta['face_triangles'], 'oval_m': meta['oval_m'], 'eyes_cell_uv': eyes_uv,
                          'hair_lum': round(face_info['hair_lum'], 1), 'skin_lum': round(face_info['skin_lum'], 1)}
    return report


def verify_structure(src: Path, dst: Path) -> list[str]:
    """Byte-level check that the rig survived: nodes, skins, IBMs, animations."""
    a, abin = read_glb(src)
    b, bbin = read_glb(dst)
    problems = []
    if a['nodes'] != b['nodes']:
        problems.append('nodes changed')
    if len(a.get('animations', [])) != len(b.get('animations', [])):
        problems.append('animation count changed')
    for sa, sb in zip(a['skins'], b['skins']):
        if sa['joints'] != sb['joints']:
            problems.append('skin joints changed')
        if not np.array_equal(read_accessor(a, abin, sa['inverseBindMatrices']), read_accessor(b, bbin, sb['inverseBindMatrices'])):
            problems.append('inverse bind matrices changed')
    for anim_a, anim_b in zip(a.get('animations', []), b.get('animations', [])):
        for sa, sb in zip(anim_a['samplers'], anim_b['samplers']):
            if not np.array_equal(read_accessor(a, abin, sa['output']), read_accessor(b, bbin, sb['output'])):
                problems.append(f"animation {anim_a.get('name')} data changed")
                break
    # Same skinned triangles, same joints/weights per corner (order may differ).
    def corners(doc, binary):
        rows = []
        for mesh in doc['meshes']:
            for prim in mesh['primitives']:
                pos = read_accessor(doc, binary, prim['attributes']['POSITION'])
                idx = read_accessor(doc, binary, prim['indices']).reshape(-1) if 'indices' in prim else np.arange(len(pos))
                j = read_accessor(doc, binary, prim['attributes']['JOINTS_0']).astype(np.float64)
                w = read_accessor(doc, binary, prim['attributes']['WEIGHTS_0']).astype(np.float64)
                rows.append(np.concatenate([pos[idx], j[idx], np.round(w[idx], 5)], axis=1))
        data = np.concatenate(rows)
        return data[np.lexsort(data.T[::-1])]
    ca, cb = corners(a, abin), corners(b, bbin)
    if ca.shape != cb.shape or not np.allclose(ca, cb, atol=1e-6):
        problems.append('skinned corners (position/joints/weights) changed')
    return problems


def godot_self_test(paths: list[Path], godot: Path) -> bool:
    script = 'res://tools/check_face_glb.gd'
    cmd = [str(godot), '--headless', '--path', str(ROOT / 'godot3d'), '--script', script, '--'] + [str(p.resolve()) for p in paths]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if 'FACE_GLB' in ln or 'ERROR' in ln]
    print('\n'.join(lines), flush=True)
    return proc.returncode == 0 and any('FACE_GLB OK' in ln for ln in lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('input', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--face-atlas', type=Path, default=None, help='2x2 expression atlas PNG to embed in FaceMat')
    parser.add_argument('--face-meta', type=Path, default=None, help='JSON with face_box [u0,v0,u1,v1] in cell space')
    parser.add_argument('--bake-neutral', type=Path, default=None, help='write the original face resampled into cell space')
    parser.add_argument('--qc', type=Path, default=None, help='write a front-view PNG with the FaceMat triangles tinted')
    parser.add_argument('--weld', type=float, default=1e-4)
    parser.add_argument('--crease', type=float, default=62.0, help='crease angle in degrees for smooth normals')
    parser.add_argument('--proxy', type=float, default=0.8, help='0..1 spherical normal transfer on the face')
    parser.add_argument('--self-test', action='store_true', help='verify the rig bytes and load the result in Godot')
    parser.add_argument('--godot', type=Path, default=GODOT_DEFAULT)
    args = parser.parse_args(argv)
    report = process(args.input, args.output, weld=args.weld, crease_deg=args.crease, proxy=args.proxy,
                     face_atlas=args.face_atlas, face_meta=args.face_meta, bake=args.bake_neutral, qc=args.qc)
    print(json.dumps(report, indent=2), flush=True)
    if not args.self_test:
        return 0
    problems = verify_structure(args.input, args.output)
    print('structure: ' + ('OK' if not problems else '; '.join(problems)), flush=True)
    ok = not problems and godot_self_test([args.output], args.godot)
    print('self-test: ' + ('PASS' if ok else 'FAIL'), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
