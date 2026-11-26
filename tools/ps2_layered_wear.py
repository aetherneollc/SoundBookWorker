"""Separate-mesh workwear for ``--ps2-layered`` characters.

Garments are lofted from relaxed cross-sections (not a muscle offset) and
skinned by smooth nearest-vertex weight transfer on the same 65-bone rig.
The default builder never imports this module.
"""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree


def _hex(value: str) -> np.ndarray:
    s = str(value).strip().lstrip("#")
    return np.array(
        [int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)], dtype=np.float64
    ) / 255.0


def _rotate_points(pts: np.ndarray, origin: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    a = axis / max(float(np.linalg.norm(axis)), 1e-8)
    c = math.cos(angle)
    s = math.sin(angle)
    k = np.array(
        [[0.0, -a[2], a[1]], [a[2], 0.0, -a[0]], [-a[1], a[0], 0.0]],
        dtype=np.float64,
    )
    rot = np.eye(3) * c + s * k + (1.0 - c) * np.outer(a, a)
    return (pts - origin) @ rot.T + origin


def _weight_sum(joints: np.ndarray, weights: np.ndarray, bone_ids: set[int]) -> np.ndarray:
    if not bone_ids:
        return np.zeros(len(joints), dtype=np.float64)
    mask = np.isin(joints, list(bone_ids))
    return np.sum(weights * mask, axis=1)


def _distal_finger(name: str, finger: str, side: str, level: int) -> bool:
    prefix = f"{finger}_0"
    if not (name.startswith(prefix) and name.endswith(f"_{side}")):
        return False
    digit = name[len(prefix) : len(prefix) + 1]
    if digit not in "1234":
        return False
    return int(digit) > (level + 1)


def soften_finger_rest(
    pos: np.ndarray,
    joints_attr: np.ndarray,
    weights_attr: np.ndarray,
    joint_names: list[str],
    joint_world: dict[str, np.ndarray],
) -> tuple[np.ndarray, float]:
    """Gentle palm-ward curl so a straight T-pose hand is not a rigid plank.

    Bone rest translations are not modified. Only finger-weighted vertices move.
    """
    pos = np.array(pos, dtype=np.float64, copy=True)
    name_to_i = {name: i for i, name in enumerate(joint_names)}
    moved = {key: np.array(val, dtype=np.float64, copy=True) for key, val in joint_world.items()}
    angles = {
        "thumb": (0.08, 0.12, 0.08),
        "index": (0.10, 0.16, 0.14),
        "middle": (0.11, 0.18, 0.15),
        "ring": (0.10, 0.16, 0.14),
        "pinky": (0.12, 0.18, 0.14),
    }
    before = None
    after = None
    for side in ("l", "r"):
        try:
            hand = moved[f"hand_{side}"]
            index = moved[f"index_01_{side}"]
            pinky = moved[f"pinky_01_{side}"]
            middle = moved[f"middle_01_{side}"]
        except KeyError:
            continue
        palm = np.cross(middle - hand, pinky - index)
        plen = float(np.linalg.norm(palm))
        if plen < 1e-8:
            continue
        palm = palm / plen
        for finger, angs in angles.items():
            chain = [f"{finger}_0{i}_{side}" for i in (1, 2, 3)]
            leaf = f"{finger}_04_leaf_{side}"
            if any(name not in moved or name not in name_to_i for name in chain):
                continue
            if finger == "middle" and side == "l":
                before = moved[chain[2]].copy()
            for level, bone in enumerate(chain):
                origin = moved[bone].copy()
                child = chain[level + 1] if level < 2 else leaf
                if child not in moved:
                    child = chain[min(level + 1, 2)]
                direction = moved[child] - origin
                dlen = float(np.linalg.norm(direction))
                if dlen < 1e-6:
                    continue
                direction = direction / dlen
                axis = np.cross(direction, palm)
                if float(np.linalg.norm(axis)) < 1e-6:
                    continue
                angle = float(angs[level])
                test = origin + direction * 0.02
                spun = _rotate_points(test[None, :], origin, axis, angle)[0]
                if float(np.dot(spun - test, palm)) < 0.0:
                    angle = -angle
                ids = {name_to_i[name] for name in chain[level:]}
                if leaf in name_to_i:
                    ids.add(name_to_i[leaf])
                infl = _weight_sum(joints_attr, weights_attr, ids)
                sel = infl > 0.15
                if not np.any(sel):
                    continue
                spun_v = _rotate_points(pos[sel], origin, axis, angle)
                blend = np.clip(infl[sel], 0.0, 1.0)[:, None]
                pos[sel] = pos[sel] * (1.0 - blend) + spun_v * blend
                for nm in list(moved):
                    if _distal_finger(nm, finger, side, level):
                        moved[nm] = _rotate_points(moved[nm][None, :], origin, axis, angle)[0]
            if finger == "middle" and side == "l":
                after = moved[chain[2]].copy()
    delta = 0.0 if before is None or after is None else float(np.linalg.norm(after - before))
    return pos, delta


def deflate_covered_body(
    pos: np.ndarray,
    normals: np.ndarray,
    joints_attr: np.ndarray,
    weights_attr: np.ndarray,
    groups: dict[str, set[int]],
    landmarks: dict[str, float],
) -> np.ndarray:
    """Pull hidden torso and limb verts inward so muscles sit inside the shells."""
    pos = np.array(pos, dtype=np.float64, copy=True)
    covered = np.clip(
        _weight_sum(joints_attr, weights_attr, groups["torso"] | groups["pelvis"])
        + _weight_sum(joints_attr, weights_attr, groups["upper_arm"] | groups["lower_arm"])
        + _weight_sum(joints_attr, weights_attr, groups["thigh"] | groups["calf"]),
        0.0,
        1.0,
    )
    protect = np.clip(
        _weight_sum(joints_attr, weights_attr, groups["head"]) * 1.4
        + _weight_sum(joints_attr, weights_attr, groups["neck"]) * 0.9
        + _weight_sum(joints_attr, weights_attr, groups["hand"])
        + _weight_sum(joints_attr, weights_attr, groups["foot"]),
        0.0,
        1.0,
    )
    covered *= 1.0 - protect
    neck_col = (pos[:, 1] > landmarks["collar_bot_y"]) & (np.abs(pos[:, 0]) < 0.09)
    covered = np.where(neck_col, covered * 0.15, covered)
    pos -= normals * (0.008 * covered)[:, None]
    return pos


def _orient_outward(pos: np.ndarray, faces: np.ndarray, ref: np.ndarray) -> np.ndarray:
    faces = np.array(faces, dtype=np.int64, copy=True)
    for i, face in enumerate(faces):
        edge = np.cross(pos[face[1]] - pos[face[0]], pos[face[2]] - pos[face[0]])
        if float(np.dot(edge, pos[face].mean(axis=0) - ref)) < 0.0:
            faces[i] = face[::-1]
    return faces


_NORMALS = None


def set_normal_fn(fn) -> None:
    global _NORMALS
    _NORMALS = fn


def _mesh_normals(pos: np.ndarray, faces: np.ndarray) -> np.ndarray:
    return _NORMALS(pos, faces)


def _ring_xz(y: float, cx: float, cz: float, rx: float, zf: float, zb: float, n: int = 16, power: float = 2.4) -> np.ndarray:
    pts = np.zeros((n, 3), dtype=np.float64)
    exp = 2.0 / power
    for i in range(n):
        ang = 2.0 * math.pi * i / n
        c = math.cos(ang)
        s = math.sin(ang)
        x = math.copysign(abs(c) ** exp, c) * rx
        zr = zf if s >= 0.0 else zb
        z = math.copysign(abs(s) ** exp, s) * zr
        pts[i] = (cx + x, y, cz + z)
    return pts


def _loft(rings: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    count = len(rings[0])
    pos = np.vstack(rings)
    uv = np.zeros((len(rings) * count, 2), dtype=np.float64)
    faces: list[list[int]] = []
    for i in range(len(rings)):
        v = i / max(len(rings) - 1, 1)
        for s in range(count):
            uv[i * count + s, 0] = s / count
            uv[i * count + s, 1] = v
    for i in range(len(rings) - 1):
        for s in range(count):
            a = i * count + s
            b = i * count + (s + 1) % count
            c = (i + 1) * count + s
            d = (i + 1) * count + (s + 1) % count
            faces.append([a, c, b])
            faces.append([b, c, d])
    face_arr = np.asarray(faces, dtype=np.int64)
    face_arr = _orient_outward(pos, face_arr, pos.mean(axis=0))
    return pos, _mesh_normals(pos, face_arr), uv, face_arr


def _parallel_frames(points: np.ndarray) -> list[tuple[np.ndarray, np.ndarray]]:
    tang = np.zeros_like(points)
    tang[0] = points[1] - points[0]
    tang[-1] = points[-1] - points[-2]
    if len(points) > 2:
        tang[1:-1] = points[2:] - points[:-2]
    tang /= np.maximum(np.linalg.norm(tang, axis=1, keepdims=True), 1e-8)
    frames = []
    prev = None
    up = np.array([0.0, 1.0, 0.0])
    alt = np.array([0.0, 0.0, 1.0])
    for tangent in tang:
        ref = alt if abs(float(np.dot(tangent, up))) > 0.88 else up
        if prev is None:
            normal = np.cross(tangent, ref)
        else:
            normal = prev - tangent * float(np.dot(prev, tangent))
            if float(np.linalg.norm(normal)) < 1e-6:
                normal = np.cross(tangent, ref)
        normal = normal / max(float(np.linalg.norm(normal)), 1e-8)
        bitan = np.cross(tangent, normal)
        bitan = bitan / max(float(np.linalg.norm(bitan)), 1e-8)
        frames.append((normal, bitan))
        prev = normal
    return frames


def _tube(points: list[np.ndarray], radii: list[float], sides: int = 12) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    pts = np.vstack(points)
    frames = _parallel_frames(pts)
    rings = []
    for point, radius, (normal, bitan) in zip(pts, radii, frames):
        ring = np.zeros((sides, 3), dtype=np.float64)
        for i in range(sides):
            ang = 2.0 * math.pi * i / sides
            ring[i] = point + normal * (radius * math.cos(ang)) + bitan * (radius * math.sin(ang))
        rings.append(ring)
    return _loft(rings)


def _box(center: np.ndarray, size: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    c = np.asarray(center, dtype=np.float64)
    h = np.asarray(size, dtype=np.float64) * 0.5
    signs = np.array(
        [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]],
        dtype=np.float64,
    )
    corners = c + signs * h
    quads = (
        (0, 1, 2, 3),
        (4, 7, 6, 5),
        (0, 4, 5, 1),
        (1, 5, 6, 2),
        (2, 6, 7, 3),
        (3, 7, 4, 0),
    )
    verts: list[np.ndarray] = []
    uvs: list[list[float]] = []
    faces: list[list[int]] = []
    for quad in quads:
        base = len(verts)
        for k, idx in enumerate(quad):
            verts.append(corners[idx])
            uvs.append([float(k in (1, 2)), float(k >= 2)])
        faces += [[base, base + 1, base + 2], [base, base + 2, base + 3]]
    pos = np.asarray(verts, dtype=np.float64)
    face_arr = _orient_outward(pos, np.asarray(faces, dtype=np.int64), c)
    return pos, _mesh_normals(pos, face_arr), np.asarray(uvs, dtype=np.float64), face_arr


def _merge(parts: list[dict]) -> dict:
    pos, nrm, uv, faces, joints, weights = [], [], [], [], [], []
    offset = 0
    for part in parts:
        pos.append(part["pos"])
        nrm.append(part["nrm"])
        uv.append(part["uv"])
        faces.append(part["faces"] + offset)
        joints.append(part["j"])
        weights.append(part["w"])
        offset += len(part["pos"])
    return {
        "pos": np.vstack(pos),
        "nrm": np.vstack(nrm),
        "uv": np.vstack(uv),
        "faces": np.vstack(faces),
        "joints": np.vstack(joints),
        "weights": np.vstack(weights),
    }


def transfer_weights(
    garment: np.ndarray,
    body: np.ndarray,
    body_j: np.ndarray,
    body_w: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    sel = np.flatnonzero(mask)
    if sel.size < 8:
        sel = np.arange(len(body))
    pts = body[sel]
    k = 3 if len(pts) >= 3 else 1
    dist, ix = cKDTree(pts).query(garment, k=k)
    if k == 1:
        dist = dist[:, None]
        ix = ix[:, None]
    src_j = body_j[sel][ix]
    src_w = body_w[sel][ix]
    inv = 1.0 / np.maximum(dist, 1e-5)
    inv /= inv.sum(axis=1, keepdims=True)
    n = len(garment)
    out_j = np.zeros((n, 4), dtype=np.uint8)
    out_w = np.zeros((n, 4), dtype=np.float64)
    flat_j = src_j.reshape(n, k * 4)
    flat_w = (src_w * inv[:, :, None]).reshape(n, k * 4)
    for i in range(n):
        bucket: dict[int, float] = {}
        for bone, weight in zip(flat_j[i], flat_w[i]):
            if weight <= 1e-6:
                continue
            bucket[int(bone)] = bucket.get(int(bone), 0.0) + float(weight)
        if not bucket:
            bucket[0] = 1.0
        top = sorted(bucket.items(), key=lambda item: -item[1])[:4]
        total = sum(item[1] for item in top) or 1.0
        for slot, (bone, weight) in enumerate(top):
            out_j[i, slot] = bone
            out_w[i, slot] = weight / total
    return out_j, out_w


def _bind(pos, nrm, uv, faces, mask, body, body_j, body_w) -> dict:
    joints, weights = transfer_weights(pos, body, body_j, body_w, mask)
    return {"pos": pos, "nrm": nrm, "uv": uv, "faces": faces, "j": joints, "w": weights}


def _slice(pos: np.ndarray, y: float, band: float, x_limit: float):
    sel = (np.abs(pos[:, 1] - y) < band) & (np.abs(pos[:, 0]) < x_limit)
    sl = pos[sel]
    if len(sl) < 16:
        return None
    cx = float(np.median(sl[:, 0]))
    cz = float(np.median(sl[:, 2]))
    rx = float(np.percentile(np.abs(sl[:, 0] - cx), 96))
    z_rel = sl[:, 2] - cz
    zf = float(max(np.percentile(z_rel, 96), 0.04))
    zb = float(max(-np.percentile(z_rel, 4), 0.04))
    return cx, cz, rx, zf, zb


def _segment_radius(pos: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    ab = b - a
    length2 = float(np.dot(ab, ab))
    if length2 < 1e-8:
        return 0.055
    t = np.clip(((pos - a) @ ab) / length2, 0.0, 1.0)
    dist = np.linalg.norm(pos - (a + t[:, None] * ab), axis=1)
    near = (t > 0.12) & (t < 0.88) & (dist < 0.12)
    if int(near.sum()) < 12:
        return 0.055
    return float(np.percentile(dist[near], 68))


def _rescue(pos, faces, body, body_n, mask, margin=0.006):
    sel = np.flatnonzero(mask)
    if sel.size < 8:
        return pos, 99.0
    pts = body[sel]
    nrm = body_n[sel]
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
    _, ix = cKDTree(pts).query(pos, k=1)
    delta = pos - pts[ix]
    dist = np.linalg.norm(delta, axis=1)
    signed = np.sum(delta * nrm[ix], axis=1)
    # Far nearest-vertex hits with an opposing normal are not real intersections.
    pen = (signed < margin) & (dist < 0.028)
    out = pos.copy()
    if np.any(pen):
        out[pen] += nrm[ix][pen] * (margin - signed[pen])[:, None]
    close = signed[dist < 0.04]
    report = float(np.min(close)) if close.size else 0.02
    return out, report


def _side_mask(pos: np.ndarray, weights: np.ndarray, sign: float) -> np.ndarray:
    return (weights > 0.22) & (pos[:, 0] * sign > 0.0)


def _smooth_shell(pos: np.ndarray, faces: np.ndarray, iters: int, lam: float) -> np.ndarray:
    out = pos.copy()
    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]], faces[:, [1, 0]], faces[:, [2, 1]], faces[:, [0, 2]]])
    edges = edges[edges[:, 0] != edges[:, 1]]
    deg = np.bincount(edges[:, 0], minlength=len(pos)).astype(np.float64)
    valid = deg > 0
    src, dst = edges[:, 0], edges[:, 1]
    for _ in range(iters):
        acc = np.zeros_like(out)
        np.add.at(acc, src, out[dst])
        avg = out.copy()
        avg[valid] = acc[valid] / deg[valid, None]
        out = out + lam * (avg - out)
    return out


def _cylindrical_uv(pos: np.ndarray) -> np.ndarray:
    ang = np.arctan2(pos[:, 0], pos[:, 2])
    u = ang / (2.0 * math.pi) + 0.5
    span = max(float(pos[:, 1].max() - pos[:, 1].min()), 1e-4)
    v = (pos[:, 1] - float(pos[:, 1].min())) / span
    return np.stack([u, v], axis=1).astype(np.float64)


def _orient_normals(pos: np.ndarray, faces: np.ndarray, normals: np.ndarray, anchor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    outward = np.sum(normals * (pos - anchor), axis=1)
    if float(np.median(outward)) < 0.0:
        return faces[:, ::-1].copy(), -normals
    return faces, normals


def _smooth_masked(pos: np.ndarray, faces: np.ndarray, mask: np.ndarray, iters: int, lam: float) -> np.ndarray:
    out = pos.copy()
    edges = np.vstack(
        [faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]], faces[:, [1, 0]], faces[:, [2, 1]], faces[:, [0, 2]]]
    )
    edges = edges[edges[:, 0] != edges[:, 1]]
    deg = np.bincount(edges[:, 0], minlength=len(pos)).astype(np.float64)
    valid = deg > 0
    src, dst = edges[:, 0], edges[:, 1]
    move = mask.astype(np.float64)
    for _ in range(iters):
        acc = np.zeros_like(out)
        np.add.at(acc, src, out[dst])
        avg = out.copy()
        avg[valid] = acc[valid] / deg[valid, None]
        out = out + (lam * move)[:, None] * (avg - out)
    return out


def _group_ids(pos: np.ndarray, decimals: int = 4) -> np.ndarray:
    """Quantize positions so UV-seam duplicates share one id."""
    scale = float(10**decimals)
    keys = np.ascontiguousarray(np.rint(np.asarray(pos, dtype=np.float64) * scale).astype(np.int64))
    packed = keys.view(np.dtype((np.void, keys.dtype.itemsize * 3))).ravel()
    _, inv = np.unique(packed, return_inverse=True)
    return inv


def _weld_normals(pos: np.ndarray, normals: np.ndarray) -> np.ndarray:
    """Average normals of vertices that occupy the same 3D point."""
    inv = _group_ids(pos, 4)
    acc = np.zeros((int(inv.max()) + 1, 3), dtype=np.float64)
    np.add.at(acc, inv, normals)
    counts = np.bincount(inv).astype(np.float64)
    acc /= np.maximum(counts[:, None], 1.0)
    length = np.linalg.norm(acc, axis=1, keepdims=True)
    acc /= np.maximum(length, 1e-8)
    return acc[inv]


def _weld_positions(keys_pos: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Pull UV-seam copies onto one extruded position so the shell cannot split."""
    inv = _group_ids(keys_pos, 4)
    acc = np.zeros((int(inv.max()) + 1, 3), dtype=np.float64)
    np.add.at(acc, inv, values)
    counts = np.bincount(inv).astype(np.float64)
    acc /= np.maximum(counts[:, None], 1.0)
    return acc[inv]


def _extract_shell(
    pos: np.ndarray,
    normals: np.ndarray,
    faces: np.ndarray,
    mask: np.ndarray,
    joints: np.ndarray,
    weights: np.ndarray,
    offset: float,
    anchor: np.ndarray,
    smooth: int = 2,
    hard_exclude: np.ndarray | None = None,
) -> dict | None:
    """Copy the masked body surface and push it out along its normals."""
    score = mask[faces].mean(axis=1)
    keep = score >= 0.22
    if hard_exclude is not None:
        keep &= ~hard_exclude[faces].any(axis=1)
    # Pull in the shoulder and crotch faces that sit on the seam.
    vert_hits = np.zeros(len(mask), dtype=np.int32)
    np.add.at(vert_hits, faces[keep].reshape(-1), 1)
    neighbor = vert_hits[faces].sum(axis=1) >= 2
    keep |= neighbor & (score >= 0.08)
    if hard_exclude is not None:
        keep &= ~hard_exclude[faces].any(axis=1)
    if int(keep.sum()) < 12:
        return None
    sub = faces[keep]
    used = np.unique(sub)
    remap = np.full(len(pos), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    local_f = remap[sub]
    local_p = pos[used].copy()
    local_n = normals[used].copy()
    length = np.linalg.norm(local_n, axis=1, keepdims=True)
    local_n = local_n / np.maximum(length, 1e-8)
    # UV seams duplicate vertices. Average those normals before the push, or the shell splits open.
    local_n = _weld_normals(local_p, local_n)
    local_f, local_n = _orient_normals(local_p, local_f, local_n, anchor)
    # Fill waist and knee hollows a little so the cloth drapes instead of printing muscles.
    neighbor = _smooth_shell(local_p, local_f, 1, 1.0)
    hollow = np.clip(np.sum((neighbor - local_p) * local_n, axis=1), 0.0, 0.012)
    dist = offset + hollow * 0.65
    inflated = local_p + local_n * dist[:, None]
    inflated = _weld_positions(local_p, inflated)
    if smooth:
        inflated = _smooth_shell(inflated, local_f, smooth, 0.28)
    signed = np.sum((inflated - local_p) * local_n, axis=1)
    short = signed < offset * 0.72
    inflated[short] = local_p[short] + local_n[short] * (offset * 0.85)
    # Smoothing pulls UV-seam copies apart again. Snap them back onto one position.
    inflated = _weld_positions(local_p, inflated)
    inflated = _pinch_cracks(inflated, local_f, 0.0035)
    nrm = _mesh_normals(inflated, local_f)
    local_f, nrm = _orient_normals(inflated, local_f, nrm, anchor)
    return {
        "pos": inflated,
        "nrm": nrm,
        "uv": _cylindrical_uv(inflated),
        "faces": local_f,
        "j": joints[used].astype(np.uint8),
        "w": weights[used].astype(np.float64),
        "src": local_p,
        "src_n": local_n,
    }


def _blister(shell: dict, vert_mask: np.ndarray, height: float) -> dict | None:
    """Shallow patch sitting on the shell, protruding `height` metres."""
    faces = shell["faces"]
    if vert_mask.shape[0] != len(shell["pos"]) or int(vert_mask.sum()) < 6:
        return None
    keep = vert_mask[faces].all(axis=1)
    if int(keep.sum()) < 4:
        return None
    sub = faces[keep]
    used = np.unique(sub)
    remap = np.full(len(shell["pos"]), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    local = remap[sub]
    nrm = shell["nrm"][used]
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
    base = shell["pos"][used] + nrm * 0.0015
    top = shell["pos"][used] + nrm * height
    n = len(used)
    cap = local + n
    edge_count: dict[tuple[int, int], int] = {}
    directed: list[tuple[int, int]] = []
    for tri in local:
        for a, b in ((int(tri[0]), int(tri[1])), (int(tri[1]), int(tri[2])), (int(tri[2]), int(tri[0]))):
            key = (a, b) if a < b else (b, a)
            edge_count[key] = edge_count.get(key, 0) + 1
            directed.append((a, b))
    walls: list[list[int]] = []
    for a, b in directed:
        key = (a, b) if a < b else (b, a)
        if edge_count[key] != 1:
            continue
        walls.append([a, b, b + n])
        walls.append([a, b + n, a + n])
    face_list = [cap]
    if walls:
        face_list.append(np.asarray(walls, dtype=np.int64))
    out_f = np.vstack(face_list)
    out_p = np.vstack([base, top])
    return {
        "pos": out_p,
        "nrm": _mesh_normals(out_p, out_f),
        "uv": _cylindrical_uv(out_p),
        "faces": out_f,
        "j": np.vstack([shell["j"][used], shell["j"][used]]),
        "w": np.vstack([shell["w"][used], shell["w"][used]]),
    }


def _push_masked(shell: dict, mask: np.ndarray, extra: float) -> None:
    if not np.any(mask):
        return
    nrm = shell["nrm"]
    nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
    shell["pos"][mask] += nrm[mask] * extra


def _pinch_cracks(pos: np.ndarray, faces: np.ndarray, tol: float = 0.0035) -> np.ndarray:
    """Average unconnected vertices that still sit a few millimetres apart."""
    edges = np.vstack([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    a = np.minimum(edges[:, 0], edges[:, 1])
    b = np.maximum(edges[:, 0], edges[:, 1])
    edge_set = set(zip(a.tolist(), b.tolist()))
    parent = np.arange(len(pos), dtype=np.int64)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = int(parent[i])
        return i

    for i, j in cKDTree(pos).query_pairs(tol):
        if (int(i), int(j)) in edge_set or (int(j), int(i)) in edge_set:
            continue
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[rj] = ri
    root = np.array([find(i) for i in range(len(pos))], dtype=np.int64)
    _, inv = np.unique(root, return_inverse=True)
    acc = np.zeros((int(inv.max()) + 1, 3), dtype=np.float64)
    np.add.at(acc, inv, pos)
    counts = np.bincount(inv).astype(np.float64)
    means = acc / np.maximum(counts[:, None], 1.0)
    return means[inv]


def _seal_shell(shell: dict) -> None:
    """Close UV-seam splits after later edits have pulled the copies apart."""
    if "src" not in shell:
        return
    shell["pos"] = _weld_positions(shell["src"], shell["pos"])
    shell["pos"] = _pinch_cracks(shell["pos"], shell["faces"], 0.0055)
    shell["nrm"] = _mesh_normals(shell["pos"], shell["faces"])


def _relax_jacket_waist(shell: dict, waist_y: float, chest_y: float) -> None:
    """Loft the pinched waist and ribcage out toward the hip/chest line."""
    p = shell["pos"]
    src = shell["src"]
    rad = np.hypot(p[:, 0], p[:, 2])
    hip_m = (np.abs(p[:, 0]) < 0.24) & (p[:, 1] < waist_y - 0.015) & (p[:, 1] > waist_y - 0.10)
    chest_m = (np.abs(p[:, 0]) < 0.18) & (np.abs(p[:, 1] - chest_y) < 0.04)
    hip_r = float(np.median(rad[hip_m])) if int(hip_m.sum()) > 8 else 0.18
    chest_r = float(np.median(rad[chest_m])) if int(chest_m.sum()) > 8 else hip_r * 0.92
    span = max(chest_y - waist_y, 0.16)
    t = np.clip((p[:, 1] - waist_y) / span, 0.0, 1.0)
    mid = (np.abs(src[:, 0]) < 0.20) & (p[:, 1] > waist_y - 0.02) & (p[:, 1] < chest_y - 0.02)
    fade = np.clip((chest_y - 0.035 - p[:, 1]) / 0.08, 0.0, 1.0)
    envelope = (1.0 - t) * hip_r + t * max(chest_r, hip_r * 0.92)
    push = np.clip(envelope - rad, 0.0, 0.035) * fade
    push = np.where(mid, push, 0.0)
    direction = rad.copy()
    direction[direction < 1e-6] = 1.0
    p[:, 0] += p[:, 0] / direction * push
    p[:, 2] += p[:, 2] / direction * push


def _relax_trouser_legs(shell: dict, knee_y: float) -> None:
    """Open knee and calf out to a straight trouser, about 2.2 cm."""
    p = shell["pos"]
    for sign in (1.0, -1.0):
        side = p[:, 0] * sign > 0.05
        thigh = side & (p[:, 1] > knee_y + 0.05) & (p[:, 1] < knee_y + 0.18)
        if int(thigh.sum()) < 8:
            continue
        axis_x = float(np.median(p[thigh, 0]))
        axis_z = float(np.median(p[thigh, 2]))
        dx = p[:, 0] - axis_x
        dz = p[:, 2] - axis_z
        rad = np.hypot(dx, dz)
        thigh_r = float(np.median(rad[thigh]))
        zone = side & (p[:, 1] < knee_y + 0.05) & (p[:, 1] > knee_y - 0.30)
        knee_bell = np.exp(-0.5 * ((p[:, 1] - knee_y) / 0.10) ** 2)
        calf_bell = np.exp(-0.5 * ((p[:, 1] - (knee_y - 0.14)) / 0.10) ** 2)
        bell = np.maximum(knee_bell, 0.85 * calf_bell)
        target = thigh_r + 0.022 * bell
        push = np.clip(target - rad, 0.0, 0.026)
        push = np.where(zone, push, 0.0)
        p[:, 0] += (dx / np.maximum(rad, 1e-6)) * push
        p[:, 2] += (dz / np.maximum(rad, 1e-6)) * push


def _separate_overlap(jacket: dict, pants: dict) -> None:
    """Keep the jacket 1 cm outside the trousers wherever the two shells overlap."""
    dist, ix = cKDTree(pants["pos"]).query(jacket["pos"], k=1)
    jpos = jacket["pos"]
    ppos = pants["pos"][ix]
    jrad = np.hypot(jpos[:, 0], jpos[:, 2])
    prad = np.hypot(ppos[:, 0], ppos[:, 2])
    overlap = (np.abs(jpos[:, 0]) < 0.30) & (dist < 0.07) & (np.abs(jpos[:, 1] - ppos[:, 1]) < 0.045)
    need = overlap & (jrad > 0.05) & ((jrad - prad) < 0.010)
    if not np.any(need):
        return
    scale = np.ones(len(jpos), dtype=np.float64)
    scale[need] = (prad[need] + 0.011) / np.maximum(jrad[need], 1e-6)
    jpos[:, 0] *= scale
    jpos[:, 2] *= scale


def _tube_band(shell: dict, origin: np.ndarray, axis: np.ndarray, radius: float, length: float, n: int = 16) -> dict:
    """Short solid ring. `axis` is the bone direction through the cuff."""
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / max(float(np.linalg.norm(axis)), 1e-8)
    tmp = np.array([0.0, 1.0, 0.0]) if abs(float(axis[1])) < 0.85 else np.array([1.0, 0.0, 0.0])
    side = np.cross(axis, tmp)
    side = side / max(float(np.linalg.norm(side)), 1e-8)
    up = np.cross(side, axis)
    rings = []
    center0 = np.asarray(origin, dtype=np.float64)
    for end in (-0.5, 0.5):
        center = center0 + axis * (length * end)
        ang = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
        ring = center + np.outer(np.cos(ang), side * radius) + np.outer(np.sin(ang), up * radius)
        rings.append(ring)
    return _pack_procedural(*_loft(rings), shell)


def _skin_from(shell: dict, pos: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    _, ix = cKDTree(shell["pos"]).query(pos, k=1)
    return shell["j"][ix].astype(np.uint8), shell["w"][ix].astype(np.float64)


def _pack_procedural(pos: np.ndarray, nrm: np.ndarray, uv: np.ndarray, faces: np.ndarray, shell: dict) -> dict:
    joints, weights = _skin_from(shell, pos)
    return {"pos": pos, "nrm": nrm, "uv": uv, "faces": faces, "j": joints, "w": weights}


def build_garments(
    pos: np.ndarray,
    normals: np.ndarray,
    faces: np.ndarray,
    joints_attr: np.ndarray,
    weights_attr: np.ndarray,
    joint_names: list[str],
    joint_world: dict[str, np.ndarray],
    groups: dict[str, set[int]],
    landmarks: dict[str, float],
    gender: str = "female",
) -> tuple[list[dict], float]:
    """Jackets, trousers and boots extruded from the body surface."""
    del joint_names
    torso_w = _weight_sum(joints_attr, weights_attr, groups["torso"] | groups["pelvis"])
    arm_w = _weight_sum(joints_attr, weights_attr, groups["upper_arm"] | groups["lower_arm"])
    lower_w = _weight_sum(joints_attr, weights_attr, groups["lower_arm"])
    pelvis_w = _weight_sum(joints_attr, weights_attr, groups["pelvis"])
    thigh_w = _weight_sum(joints_attr, weights_attr, groups["thigh"])
    calf_w = _weight_sum(joints_attr, weights_attr, groups["calf"])
    foot_w = _weight_sum(joints_attr, weights_attr, groups["foot"])
    hand_w = _weight_sum(joints_attr, weights_attr, groups["hand"])
    head_w = _weight_sum(joints_attr, weights_attr, groups["head"])
    neck_w = _weight_sum(joints_attr, weights_attr, groups["neck"])
    waist_y = float(landmarks["waist_y"])
    chest_y = float(landmarks["chest_y"])
    shoulder_y = float(landmarks["shoulder_y"])
    boot_top = float(landmarks["boot_top_y"])
    y = pos[:, 1]
    anchor = pos.mean(axis=0)

    jacket_m = np.clip(torso_w * 1.2 + arm_w + pelvis_w, 0.0, 1.0)
    jacket_m *= 1.0 - np.clip(hand_w * 1.6 + head_w * 1.6, 0.0, 1.0)
    # Keep the shoulder caps. Only the throat column stays open for the neck.
    neck_joint = joint_world["neck_01"]
    throat = np.hypot(pos[:, 0] - neck_joint[0], pos[:, 2] - neck_joint[2]) < 0.052
    jacket_m = np.where(throat & (neck_w > 0.35) & (y > shoulder_y - 0.01), 0.0, jacket_m)
    # Hem overlaps the trousers across the hips. Thighs stay out of the jacket.
    # Hem hangs about 4 cm below the trouser waistband.
    jacket_m = np.where((thigh_w > 0.6) & (y < waist_y - 0.045) & (pelvis_w < 0.15), 0.0, jacket_m)

    pants_m = np.clip(pelvis_w + thigh_w + calf_w, 0.0, 1.0)
    pants_m *= 1.0 - np.clip(foot_w * 1.3, 0.0, 1.0)
    pants_m = np.where(y > waist_y + 0.03, pants_m * ((pelvis_w > 0.12) | (thigh_w > 0.25)), pants_m)
    pants_m = np.where(y < boot_top - 0.012, pants_m * (1.0 - np.clip(foot_w, 0.0, 1.0)), pants_m)
    pants_m = np.where(y < boot_top - 0.055, 0.0, pants_m)

    boot_m = np.clip(foot_w + calf_w * (y < boot_top + 0.01), 0.0, 1.0)
    boot_m = np.where(y > boot_top + 0.005, 0.0, boot_m)

    collar_m = np.clip(neck_w * 1.2 + torso_w * 0.15, 0.0, 1.0)
    collar_m *= 1.0 - np.clip(head_w * 1.4, 0.0, 1.0)
    collar_m = np.where((y < shoulder_y - 0.02) | (y > float(joint_world["Head"][1]) - 0.02), 0.0, collar_m)

    hand_block = hand_w > 0.45
    jacket = _extract_shell(
        pos, normals, faces, jacket_m, joints_attr, weights_attr, 0.020, anchor, smooth=3, hard_exclude=hand_block
    )
    pants = _extract_shell(pos, normals, faces, pants_m, joints_attr, weights_attr, 0.020, anchor, smooth=3)
    boots = _extract_shell(pos, normals, faces, boot_m, joints_attr, weights_attr, 0.013, anchor, smooth=2)
    collar = _extract_shell(pos, normals, faces, collar_m, joints_attr, weights_attr, 0.020, anchor, smooth=3)

    parts_jacket: list[dict] = []
    parts_trim: list[dict] = []
    if jacket is None:
        clearance = 0.0
    else:
        # Solid wrist cuffs, clear of the fingers.
        cuff = np.zeros(len(jacket["pos"]), dtype=bool)
        for side, sign in (("l", 1.0), ("r", -1.0)):
            hand = joint_world[f"hand_{side}"]
            dist = np.linalg.norm(jacket["pos"] - hand, axis=1)
            cuff |= (jacket["pos"][:, 0] * sign > 0.05) & (dist > 0.04) & (dist < 0.085)
        _push_masked(jacket, cuff, 0.008)
        short = np.sum((jacket["pos"] - jacket["src"]) * jacket["src_n"], axis=1) < 0.012
        _push_masked(jacket, short, 0.006)
        jacket["nrm"] = _mesh_normals(jacket["pos"], jacket["faces"])
        parts_jacket.append(jacket)

        front = jacket["pos"][:, 2] > float(np.median(jacket["src"][:, 2]))
        for sign in (-1.0, 1.0):
            chest = (
                front
                & (np.abs(jacket["pos"][:, 0] - sign * 0.075) < 0.042)
                & (np.abs(jacket["pos"][:, 1] - (chest_y - 0.015)) < 0.042)
            )
            blister = _blister(jacket, chest, 0.008)
            if blister is not None:
                parts_jacket.append(blister)
        seam = (jacket["pos"][:, 1] > shoulder_y - 0.03) & (jacket["pos"][:, 1] < shoulder_y + 0.04)
        seam &= np.abs(jacket["pos"][:, 0]) > 0.08
        seam &= np.abs(jacket["pos"][:, 0]) < 0.22
        if gender == "female":
            band = seam & (jacket["pos"][:, 1] > shoulder_y - 0.01)
            trim = _blister(jacket, band, 0.003)
            if trim is not None:
                parts_trim.append(trim)
        else:
            for sign in (-1.0, 1.0):
                shoulder = joint_world["upperarm_l" if sign > 0.0 else "upperarm_r"]
                hip_pt = np.array([sign * 0.05, waist_y, float(np.percentile(jacket["pos"][:, 2], 70))])
                seg = hip_pt - shoulder
                length2 = float(np.dot(seg, seg)) + 1e-8
                rel = jacket["pos"] - shoulder
                t = np.clip((rel @ seg) / length2, 0.0, 1.0)
                dist = np.linalg.norm(rel - t[:, None] * seg, axis=1)
                strap = (dist < 0.014) & (t > 0.08) & (t < 0.92) & front
                blister = _blister(jacket, strap, 0.004)
                if blister is not None:
                    parts_trim.append(blister)

        delta = jacket["pos"] - jacket["src"]
        clearance = float(np.median(np.sum(delta * jacket["src_n"], axis=1)))

    if collar is not None:
        neck = joint_world["neck_01"]
        radial = collar["pos"][:, [0, 2]] - neck[[0, 2]]
        radial_n = np.linalg.norm(radial, axis=1, keepdims=True)
        radial = radial / np.maximum(radial_n, 1e-6)
        top = collar["pos"][:, 1] > float(np.percentile(collar["pos"][:, 1], 55))
        collar["pos"][top, 0] += radial[top, 0] * 0.012
        collar["pos"][top, 2] += radial[top, 1] * 0.012
        collar["nrm"] = _mesh_normals(collar["pos"], collar["faces"])
        collar_mat = "trim" if gender == "male" else "jacket"
    else:
        collar_mat = "jacket"

    if boots is not None:
        for sign in (1.0, -1.0):
            side = boots["pos"][:, 0] * sign > 0.02
            if int(side.sum()) < 8:
                continue
            side_y = boots["pos"][side, 1]
            sole_cut = float(np.percentile(side_y, 18))
            sole = side & (boots["pos"][:, 1] <= sole_cut)
            center = boots["pos"][side].mean(axis=0)
            boots["pos"][sole, 1] -= 0.02
            boots["pos"][sole, 0] = center[0] + (boots["pos"][sole, 0] - center[0]) * 1.08
            boots["pos"][sole, 2] = center[2] + (boots["pos"][sole, 2] - center[2]) * 1.06
            collar_band = side & (boots["pos"][:, 1] >= float(np.percentile(side_y, 78)))
            nrm = boots["nrm"]
            nrm = nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-8)
            boots["pos"][collar_band] += nrm[collar_band] * 0.008
        boots["nrm"] = _mesh_normals(boots["pos"], boots["faces"])
        boots["uv"] = _cylindrical_uv(boots["pos"])

    if jacket is not None:
        _seal_shell(jacket)
        _relax_jacket_waist(jacket, waist_y, chest_y)
    if pants is not None:
        _seal_shell(pants)
        _relax_trouser_legs(pants, float(landmarks["knee_y"]))
    if boots is not None:
        _seal_shell(boots)
    if jacket is not None and pants is not None:
        _separate_overlap(jacket, pants)
        jacket["nrm"] = _mesh_normals(jacket["pos"], jacket["faces"])

    pant_parts: list[dict] = []
    boot_parts: list[dict] = []
    if pants is not None:
        pant_parts.append(pants)
    if boots is not None:
        boot_parts.append(boots)
    if boots is not None and pants is not None:
        for sign in (1.0, -1.0):
            bside = boots["pos"][:, 0] * sign > 0.03
            pside = pants["pos"][:, 0] * sign > 0.05
            if int(bside.sum()) < 8 or int(pside.sum()) < 8:
                continue
            top_y = float(np.percentile(boots["pos"][bside, 1], 86))
            hem = float(np.percentile(pants["pos"][pside, 1], 12))
            crown = bside & (np.abs(boots["pos"][:, 1] - top_y) < 0.04)
            if int(crown.sum()) < 4:
                crown = bside
            cx = float(np.median(boots["pos"][crown, 0]))
            cz = float(np.median(boots["pos"][crown, 2]))
            brad = float(np.median(np.hypot(boots["pos"][crown, 0] - cx, boots["pos"][crown, 2] - cz)))
            y_lo = min(top_y, hem) - 0.004
            y_hi = max(top_y, hem) + 0.012
            if y_hi - y_lo > 0.065:
                y_hi = y_lo + 0.065
            boot_parts.append(
                _tube_band(boots, np.array([cx, y_lo + 0.016, cz]), np.array([0.0, 1.0, 0.0]), brad + 0.009, 0.034)
            )
            pant_parts.append(
                _tube_band(pants, np.array([cx, y_lo + 0.034, cz]), np.array([0.0, 1.0, 0.0]), brad + 0.016, 0.046)
            )
    if jacket is not None:
        for side, sign in (("l", 1.0), ("r", -1.0)):
            hand = joint_world[f"hand_{side}"]
            dist = np.linalg.norm(jacket["pos"] - hand, axis=1)
            sleeve = (jacket["pos"][:, 0] * sign > 0.08) & (dist > 0.05) & (dist < 0.10)
            if int(sleeve.sum()) < 6:
                continue
            origin = np.median(jacket["pos"][sleeve], axis=0)
            axis = hand - origin
            radial = jacket["pos"][sleeve] - origin
            along = np.dot(radial, axis) / max(float(np.dot(axis, axis)), 1e-8)
            off = radial - along[:, None] * axis
            radius = float(np.median(np.linalg.norm(off, axis=1))) + 0.012
            parts_jacket.append(_tube_band(jacket, origin, axis, radius, 0.030))

    hem_ready = False
    if jacket is not None and pants is not None:
        hips = np.abs(pants["pos"][:, 0]) < 0.20
        waistband = float(np.percentile(pants["pos"][hips, 1], 90))
        high = hips & (pants["pos"][:, 1] > waistband)
        pants["pos"][high, 1] = waistband
        torso = (np.abs(jacket["pos"][:, 0]) < 0.26) & (jacket["pos"][:, 1] < waistband + 0.02)
        y_bot = waistband - 0.045
        low = torso & (jacket["pos"][:, 1] < y_bot)
        jacket["pos"][low, 1] = y_bot
        cloud = np.vstack(
            [
                jacket["pos"][torso & (np.abs(jacket["pos"][:, 1] - y_bot) < 0.03)],
                pants["pos"][hips & (pants["pos"][:, 1] > waistband - 0.04)],
            ]
        )
        cx = float(np.mean(cloud[:, 0]))
        cz = float(np.median(cloud[:, 2]))
        rx = float(np.percentile(np.abs(cloud[:, 0] - cx), 98)) + 0.012
        z_rel = cloud[:, 2] - cz
        zf = float(max(np.percentile(z_rel, 98), 0.08)) + 0.012
        zb = float(max(-np.percentile(z_rel, 2), 0.08)) + 0.012
        parts_jacket.append(
            _pack_procedural(
                *_loft(
                    [
                        _ring_xz(y_bot, cx, cz, rx, zf, zb, n=24, power=2.2),
                        _ring_xz(waistband + 0.012, cx, cz, rx, zf, zb, n=24, power=2.2),
                    ]
                ),
                jacket,
            )
        )
        rx, zf, zb = rx + 0.012, zf + 0.012, zb + 0.012
        y0 = waistband - 0.050
        y1 = waistband - 0.008
        hem_ready = True

    garments: list[dict] = []
    if parts_jacket:
        garments.append({"name": "WorkJacket", "material": "jacket", **_merge(parts_jacket)})
    if collar is not None:
        garments.append({"name": "WorkCollar", "material": collar_mat, **_merge([collar])})
    if parts_trim:
        garments.append({"name": "WorkTrim", "material": "trim", **_merge(parts_trim)})
    if pant_parts:
        garments.append({"name": "WorkTrousers", "material": "pants", **_merge(pant_parts)})
    if hem_ready:
            belt_pos, belt_n, belt_uv, belt_f = _loft(
                [
                    _ring_xz(y0, cx, cz, rx, zf, zb, n=22, power=2.15),
                    _ring_xz(y1, cx, cz, rx, zf, zb, n=22, power=2.15),
                ]
            )
            belt_parts = [_pack_procedural(belt_pos, belt_n, belt_uv, belt_f, jacket)]
            buckle = _box(np.array([cx, (y0 + y1) * 0.5, cz + zf + 0.006]), np.array([0.052, 0.036, 0.016]))
            belt_parts.append(_pack_procedural(*buckle, jacket))
            for sign in (-1.0, 1.0):
                pouch = _box(
                    np.array([cx + sign * rx * 0.62, y0 + 0.004, cz + zf * 0.62]),
                    np.array([0.058, 0.052, 0.026]),
                )
                belt_parts.append(_pack_procedural(*pouch, jacket))
            garments.append({"name": "WorkBelt", "material": "belt", **_merge(belt_parts)})
    if boot_parts:
        garments.append({"name": "WorkBoots", "material": "boots", **_merge(boot_parts)})
    return garments, clearance if jacket is not None else 0.0

def _ribbon(origin: np.ndarray, down: np.ndarray, side: np.ndarray, length: float, width: float, segments: int = 5) -> tuple:
    down_n = down / max(float(np.linalg.norm(down)), 1e-8)
    side_n = side / max(float(np.linalg.norm(side)), 1e-8)
    verts = []
    uvs = []
    for i in range(segments + 1):
        t = i / segments
        center = origin + down_n * (length * t) + np.array([0.0, 0.0, 0.012 * math.sin(t * math.pi)])
        half = width * (1.0 - 0.28 * t)
        verts.append(center - side_n * half)
        verts.append(center + side_n * half)
        uvs += [[0.0, t], [1.0, t]]
    faces = []
    for i in range(segments):
        a = i * 2
        faces += [[a, a + 2, a + 1], [a + 1, a + 2, a + 3]]
    pos = np.asarray(verts, dtype=np.float64)
    face_arr = np.asarray(faces, dtype=np.int64)
    return pos, _mesh_normals(pos, face_arr), np.asarray(uvs, dtype=np.float64), face_arr


def layer_hair(pos, normals, uv, faces, head_pos: np.ndarray) -> list[dict]:
    """A continuous outer shell plus short bangs. Cards stay on the hairline."""
    shells = []
    jitter = 0.002 * np.sin(pos[:, 0] * 48.0 + pos[:, 1] * 22.0)
    outer = pos + normals * (0.007 + jitter)[:, None]
    shells.append(
        {
            "name": "HairOuter",
            "pos": outer,
            "nrm": normals,
            "uv": uv,
            "faces": faces,
            "roughness": 0.62,
        }
    )
    cards = []
    head = np.asarray(head_pos, dtype=np.float64)
    for x in np.linspace(-0.035, 0.035, 4):
        origin = head + np.array([float(x), 0.105, 0.015])
        cards.append(_ribbon(origin, np.array([0.0, -1.0, 0.08]), np.array([1.0, 0.0, 0.0]), 0.04, 0.012, 3))
    if cards:
        shells.append({"name": "HairCards", "roughness": 0.7, **_merge_raw(cards)})
    return shells


def _merge_raw(chunks: list[tuple]) -> dict:
    pos, nrm, uv, faces = [], [], [], []
    offset = 0
    for p, n, u, f in chunks:
        pos.append(p)
        nrm.append(n)
        uv.append(u)
        faces.append(f + offset)
        offset += len(p)
    return {
        "pos": np.vstack(pos),
        "nrm": np.vstack(nrm),
        "uv": np.vstack(uv),
        "faces": np.vstack(faces),
    }


def make_colored_cloth(hex_color: str, size: int = 512) -> Image.Image:
    """Nearly flat garment color. Fine weave only, so bad UVs cannot turn into static."""
    color = _hex(hex_color).astype(np.float32)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    weave = 0.965 + 0.035 * np.sin((xx + yy) * 0.22) * np.sin((xx * 0.17 - yy * 0.13))
    albedo = np.clip(color[None, None, :] * weave[:, :, None], 0.0, 1.0)
    hem = np.clip((yy / size - 0.88) / 0.12, 0.0, 1.0) * 0.06
    albedo *= (1.0 - hem)[:, :, None]
    return Image.fromarray((albedo * 255.0).astype(np.uint8), "RGB")


def rebalance_skin(image: Image.Image, target_hex: str) -> Image.Image:
    arr = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
    red, green, blue = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    skin = (red > 0.30) & (red > green + 0.01) & (green > blue * 0.80) & ((red - blue) < 0.45) & (blue > 0.10)
    target = _hex(target_hex)
    lum = 0.30 * red + 0.57 * green + 0.13 * blue
    tgt_l = float(0.30 * target[0] + 0.57 * target[1] + 0.13 * target[2])
    tinted = np.clip(target[None, None, :] * (lum / max(tgt_l, 1e-3))[:, :, None], 0.0, 1.0)
    mixed = np.clip(tinted * 0.78 + arr * 0.22, 0.0, 1.0)
    out = np.where(skin[:, :, None], mixed, arr)
    return Image.fromarray((np.clip(out, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB")


def soften_face_normals(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGB")).astype(np.float32)
    cut = max(int(arr.shape[0] * 0.38), 1)
    band = arr[:cut]
    blur = cv2.GaussianBlur(band, (0, 0), 2.6)
    arr[:cut] = band * 0.55 + blur * 0.45
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def enhance_eyes(image: Image.Image) -> Image.Image:
    arr = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
    lum = arr.mean(axis=2)
    sclera = np.clip((lum - 0.42) / 0.34, 0.0, 1.0)
    pupil = np.clip((0.14 - lum) / 0.14, 0.0, 1.0)
    iris = np.clip(1.0 - np.maximum(sclera, pupil), 0.0, 1.0)
    arr[:, :, 0] = np.clip(arr[:, :, 0] - 0.045 * sclera, 0.0, 1.0)
    arr[:, :, 1] = np.clip(arr[:, :, 1] + 0.02 * sclera, 0.0, 1.0)
    arr[:, :, 2] = np.clip(arr[:, :, 2] + 0.075 * sclera, 0.0, 1.0)
    arr *= (1.0 - 0.62 * pupil)[:, :, None]
    arr = np.clip(arr * (1.0 + 0.22 * iris[:, :, None]) - 0.03 * iris[:, :, None], 0.0, 1.0)
    ring = np.clip(1.0 - np.abs(lum - 0.30) / 0.07, 0.0, 1.0) * iris
    arr *= (1.0 - 0.40 * ring)[:, :, None]
    return Image.fromarray((np.clip(arr, 0.0, 1.0) * 255.0).astype(np.uint8), "RGB")
