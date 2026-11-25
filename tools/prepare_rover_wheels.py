#!/usr/bin/env python3
"""Split the AT-1 rover GLB into chassis, 6 pivoted wheel nodes, and turret.

Reads ``media/assets3d/atuo/atuo_norm.glb`` (or ``model_norm.glb`` if present /
passed via ``--input``), identifies the 6 wheel clusters and top sensor turret
by spatial bounds, pivots each wheel at its exact axle center and the turret at
its neck stem, grounds the entire assembly cleanly at ``y = 0.0``, and exports:

- ``media/assets3d/atuo/atuo_rigged.glb`` with child nodes:
  ``Chassis``, ``Wheel_FL``, ``Wheel_ML``, ``Wheel_RL``,
  ``Wheel_FR``, ``Wheel_MR``, ``Wheel_RR``, ``Turret``
- ``media/assets3d/atuo/rover_meta.json`` recording exact axle centers, wheel
  radii, turret pivot, and grounded bounds.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import struct
import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
import trimesh

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_GLB = ROOT / "media" / "assets3d" / "atuo" / "atuo_rigged.glb"
DEFAULT_META_JSON = ROOT / "media" / "assets3d" / "atuo" / "rover_meta.json"

_GLB_MAGIC = b"glTF"
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942
_DTYPES = {5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_COMPS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

WHEEL_NAMES = ["Wheel_FL", "Wheel_ML", "Wheel_RL", "Wheel_FR", "Wheel_MR", "Wheel_RR"]


def _resolve_default_input() -> Path:
    atuo_dir = ROOT / "media" / "assets3d" / "atuo"
    for name in ("model_norm.glb", "atuo_norm.glb", "atuo_full.glb"):
        cand = atuo_dir / name
        if cand.is_file():
            return cand
    return atuo_dir / "atuo_norm.glb"


def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise ValueError(f"not a GLB: {path}")
    off = 12
    doc = None
    blob = b""
    while off + 8 <= len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        payload = data[off : off + clen]
        off += clen
        if ctype == _JSON_CHUNK:
            doc = json.loads(payload.decode("utf-8"))
        elif ctype == _BIN_CHUNK:
            blob = payload
    if not isinstance(doc, dict):
        raise ValueError(f"missing JSON chunk: {path}")
    return doc, blob


def _read_acc(doc: dict, blob: bytes, idx: int) -> np.ndarray:
    acc = doc["accessors"][idx]
    bv = doc["bufferViews"][acc["bufferView"]]
    dt = np.dtype(_DTYPES[int(acc["componentType"])])
    c = _COMPS[acc["type"]]
    cnt = int(acc["count"])
    off = int(bv.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    width = c * dt.itemsize
    stride = int(bv.get("byteStride") or width)
    if stride == width:
        return np.frombuffer(blob[off : off + cnt * width], dtype=dt).reshape(cnt, c).copy()
    return np.vstack(
        [np.frombuffer(blob[off + i * stride : off + i * stride + width], dtype=dt) for i in range(cnt)]
    ).copy()


def _detect_wheel_bays(verts: np.ndarray) -> dict[str, dict[str, Any]]:
    """Detect the 6 wheel axle centers and radii from bottom ground-contact patches."""
    y_min = float(verts[:, 1].min())
    bot = verts[verts[:, 1] < y_min + 0.045]
    bays: dict[str, dict[str, Any]] = {}

    for side_tag, is_left in (("L", True), ("R", False)):
        side_mask = bot[:, 0] > 0.0 if is_left else bot[:, 0] < 0.0
        sb = bot[side_mask]
        if len(sb) < 6:
            raise ValueError(f"insufficient bottom wheel vertices on side {side_tag}")
        # Cluster along Z into 3 wheel contact patches (Rear, Mid, Front)
        order = np.argsort(sb[:, 2])
        zs = sb[order, 2]
        gaps = np.where(np.diff(zs) > 0.12)[0]
        starts = [0] + [int(g + 1) for g in gaps]
        ends = [int(g + 1) for g in gaps] + [len(zs)]
        patches = []
        for s, e in zip(starts, ends):
            pts = sb[order[s:e]]
            if len(pts) >= 3:
                patches.append(pts)
        # Keep the 3 largest/primary patches sorted rear -> mid -> front
        patches.sort(key=lambda p: len(p), reverse=True)
        patches = sorted(patches[:3], key=lambda p: float(p[:, 2].mean()))
        if len(patches) != 3:
            raise ValueError(f"expected 3 wheel patches on side {side_tag}, found {len(patches)}")

        for pos_tag, pts in zip(("R", "M", "F"), patches):
            name = f"Wheel_{pos_tag}{side_tag}"
            x_lo = float(pts[:, 0].min()) - 0.010
            x_hi = float(pts[:, 0].max()) + 0.010
            # Keep wheel selection strictly on the outer tire (exclude chassis struts)
            if is_left:
                x_lo = max(x_lo, 0.215)
            else:
                x_hi = min(x_hi, -0.215)
            z_center = float(pts[:, 2].mean())
            # Measure actual wheel height/radius from cylindrical region around patch
            cyl = verts[
                (verts[:, 0] >= x_lo)
                & (verts[:, 0] <= x_hi)
                & (np.abs(verts[:, 2] - z_center) <= 0.175)
                & (verts[:, 1] <= y_min + 0.32)
            ]
            # Wheel outer radius is half the Z-extent at mid-wheel height (y ~ 0.14)
            mid_band = cyl[(cyl[:, 1] >= y_min + 0.08) & (cyl[:, 1] <= y_min + 0.22)]
            if len(mid_band) >= 10:
                z_lo_m = float(np.percentile(mid_band[:, 2], 3))
                z_hi_m = float(np.percentile(mid_band[:, 2], 97))
                radius = float(np.clip(0.5 * (z_hi_m - z_lo_m), 0.12, 0.165))
                z_center = 0.5 * (z_lo_m + z_hi_m)
                x_center = float(0.5 * (np.percentile(mid_band[:, 0], 5) + np.percentile(mid_band[:, 0], 95)))
                x_half = float(0.5 * (np.percentile(mid_band[:, 0], 98) - np.percentile(mid_band[:, 0], 2)))
            else:
                radius = 0.148
                x_center = float(pts[:, 0].mean())
                x_half = 0.080
            axle_y = y_min + radius
            bays[name] = {
                "center": [round(x_center, 5), round(axle_y, 5), round(z_center, 5)],
                "radius": round(radius, 5),
                "width": round(max(2.0 * x_half, 0.12), 5),
                "x_min": x_lo,
                "x_max": x_hi,
            }
    return bays


def _detect_turret(verts: np.ndarray) -> dict[str, Any]:
    """Detect the top sensor turret head & neck stem pivot."""
    y_max = float(verts[:, 1].max())
    # Look for narrow neck stem around y in [0.76, 0.86]
    neck_band = verts[(verts[:, 1] >= y_max - 0.18) & (verts[:, 1] <= y_max - 0.10)]
    if len(neck_band) >= 10:
        cx = float(np.median(neck_band[:, 0]))
        cz = float(np.median(neck_band[:, 2]))
        y_cut = float(y_max - 0.15)
    else:
        top = verts[verts[:, 1] >= y_max - 0.15]
        cx = float(top[:, 0].mean()) if len(top) else 0.0
        cz = float(top[:, 2].mean()) if len(top) else -0.07
        y_cut = float(y_max - 0.15)
    return {
        "pivot": [round(cx, 5), round(y_cut, 5), round(cz, 5)],
        "y_cut": y_cut,
    }


def _extract_submesh(
    verts: np.ndarray,
    nrms: np.ndarray,
    uvs: np.ndarray | None,
    faces: np.ndarray,
    face_mask: np.ndarray,
    pivot: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray]:
    sub_faces = faces[face_mask]
    if len(sub_faces) == 0:
        return (
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 3), dtype=np.float32),
            np.zeros((0, 2), dtype=np.float32) if uvs is not None else None,
            np.zeros((0, 3), dtype=np.uint32),
        )
    used = np.unique(sub_faces)
    remap = np.full(len(verts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    sv = (verts[used] - pivot[None, :]).astype(np.float32)
    sn = nrms[used].astype(np.float32)
    suv = uvs[used].astype(np.float32) if uvs is not None else None
    sf = remap[sub_faces].astype(np.uint32)
    return sv, sn, suv, sf


def prepare_rover_wheels(
    input_path: Path,
    output_glb: Path,
    meta_json: Path,
) -> dict[str, Any]:
    doc, blob = _read_glb(input_path)
    prim = doc["meshes"][0]["primitives"][0]
    attrs = prim["attributes"]
    verts = _read_acc(doc, blob, attrs["POSITION"]).astype(np.float64)
    if "NORMAL" in attrs:
        nrms = _read_acc(doc, blob, attrs["NORMAL"]).astype(np.float64)
    else:
        tm = trimesh.Trimesh(
            vertices=verts,
            faces=_read_acc(doc, blob, prim["indices"]).reshape(-1, 3),
            process=False,
        )
        nrms = np.asarray(tm.vertex_normals, dtype=np.float64)
    uvs = (
        _read_acc(doc, blob, attrs["TEXCOORD_0"]).astype(np.float64)
        if "TEXCOORD_0" in attrs
        else None
    )
    faces = _read_acc(doc, blob, prim["indices"]).reshape(-1, 3).astype(np.int64)

    # Ensure grounded cleanly at y = 0.0 and XZ centered
    vmin = verts.min(axis=0)
    vmax = verts.max(axis=0)
    shift = np.array(
        [-0.5 * (vmin[0] + vmax[0]), -vmin[1], -0.5 * (vmin[2] + vmax[2])],
        dtype=np.float64,
    )
    verts = verts + shift[None, :]

    # Detect 6 wheel bays and top turret
    bays = _detect_wheel_bays(verts)
    turret = _detect_turret(verts)

    # Assign each triangle by its centroid to Wheel_*, Turret, or Chassis
    tri_c = verts[faces].mean(axis=1)
    tri_label = np.full(len(faces), -1, dtype=np.int32)  # -1 = Chassis

    # 0..5 = WHEEL_NAMES, 6 = Turret
    for wi, wname in enumerate(WHEEL_NAMES):
        bay = bays[wname]
        cx, cy, cz = bay["center"]
        r = bay["radius"]
        in_x = (tri_c[:, 0] >= bay["x_min"]) & (tri_c[:, 0] <= bay["x_max"])
        dist_yz = np.hypot(tri_c[:, 1] - cy, tri_c[:, 2] - cz)
        in_cyl = dist_yz <= (r * 1.12)
        below_fender = tri_c[:, 1] <= (cy + r * 1.06)
        wmask = in_x & in_cyl & below_fender
        tri_label[wmask] = wi

    # Turret above neck cut, centered near turret pivot
    tx, ty, tz = turret["pivot"]
    tmask = (
        (tri_c[:, 1] >= turret["y_cut"])
        & (np.hypot(tri_c[:, 0] - tx, tri_c[:, 2] - tz) < 0.25)
    )
    tri_label[tmask] = 6

    # Extract embedded textures/materials from source GLB cleanly
    raw_images: list[tuple[bytes, str]] = []
    for im_desc in doc.get("images") or []:
        bv = doc["bufferViews"][im_desc["bufferView"]]
        off = int(bv.get("byteOffset", 0))
        ln = int(bv["byteLength"])
        raw_images.append((blob[off : off + ln], str(im_desc.get("mimeType", "image/png"))))

    # Build clean multi-node GLB
    out_blob = bytearray()
    out_bvs: list[dict] = []
    out_accs: list[dict] = []
    out_meshes: list[dict] = []
    out_nodes: list[dict] = []

    def pad4() -> None:
        rem = len(out_blob) % 4
        if rem:
            out_blob.extend(b"\x00" * (4 - rem))

    def push_bytes(data: bytes, target: int | None = None) -> int:
        pad4()
        off = len(out_blob)
        out_blob.extend(data)
        bv: dict[str, Any] = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target is not None:
            bv["target"] = target
        out_bvs.append(bv)
        return len(out_bvs) - 1

    def push_acc(
        arr: np.ndarray, ct: int, atype: str, target: int, minmax: bool = False
    ) -> int:
        dt = np.dtype(_DTYPES[ct])
        a = np.ascontiguousarray(arr, dtype=dt)
        bv_i = push_bytes(a.tobytes(), target=target)
        acc: dict[str, Any] = {
            "bufferView": bv_i,
            "byteOffset": 0,
            "componentType": ct,
            "count": int(a.shape[0]),
            "type": atype,
        }
        if minmax and len(a) > 0:
            acc["min"] = [float(v) for v in a.min(axis=0)]
            acc["max"] = [float(v) for v in a.max(axis=0)]
        out_accs.append(acc)
        return len(out_accs) - 1

    out_images = []
    for im_bytes, mime in raw_images:
        bv_i = push_bytes(im_bytes)
        out_images.append({"bufferView": bv_i, "mimeType": mime})

    out_textures = copy.deepcopy(doc.get("textures") or [])
    out_samplers = copy.deepcopy(doc.get("samplers") or [])
    out_materials = copy.deepcopy(doc.get("materials") or [])
    # Ensure dielectric (non-chrome) PBR defaults if metallicFactor omitted
    for mat in out_materials:
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        if "metallicFactor" not in pbr:
            pbr["metallicFactor"] = 0.0
        if "roughnessFactor" not in pbr:
            pbr["roughnessFactor"] = 0.75

    mat_idx = int(prim.get("material", 0)) if out_materials else None

    def add_part(name: str, mask: np.ndarray, pivot: list[float]) -> tuple[int, int]:
        piv = np.asarray(pivot, dtype=np.float64)
        sv, sn, suv, sf = _extract_submesh(verts, nrms, uvs, faces, mask, piv)
        if len(sf) == 0:
            raise ValueError(f"part {name} has 0 triangles")
        p_attrs: dict[str, int] = {
            "POSITION": push_acc(sv, 5126, "VEC3", 34962, minmax=True),
            "NORMAL": push_acc(sn, 5126, "VEC3", 34962),
        }
        if suv is not None:
            p_attrs["TEXCOORD_0"] = push_acc(suv, 5126, "VEC2", 34962)
        flat = sf.reshape(-1)
        ict = 5123 if int(flat.max()) < 65535 else 5125
        idx_i = push_acc(flat.astype(np.uint16 if ict == 5123 else np.uint32), ict, "SCALAR", 34963)
        prim_d: dict[str, Any] = {"attributes": p_attrs, "indices": idx_i, "mode": 4}
        if mat_idx is not None:
            prim_d["material"] = mat_idx
        mesh_i = len(out_meshes)
        out_meshes.append({"name": name, "primitives": [prim_d]})
        node_i = len(out_nodes)
        node_d: dict[str, Any] = {"name": name, "mesh": mesh_i}
        if any(abs(v) > 1e-7 for v in pivot):
            node_d["translation"] = [float(v) for v in pivot]
        out_nodes.append(node_d)
        return node_i, len(sf)

    # Root node "AT1_Rover" at index 0
    out_nodes.append({"name": "AT1_Rover", "children": []})
    children: list[int] = []

    chassis_node, chassis_tris = add_part("Chassis", tri_label == -1, [0.0, 0.0, 0.0])
    children.append(chassis_node)

    wheel_meta: dict[str, Any] = {}
    for wi, wname in enumerate(WHEEL_NAMES):
        bay = bays[wname]
        wnode, wtris = add_part(wname, tri_label == wi, bay["center"])
        children.append(wnode)
        wheel_meta[wname] = {
            "axle_center": bay["center"],
            "radius_m": bay["radius"],
            "width_m": bay["width"],
            "spin_axis": [1.0, 0.0, 0.0],
            "triangles": int(wtris),
        }

    turret_tris = 0
    if int(np.sum(tri_label == 6)) >= 12:
        tnode, turret_tris = add_part("Turret", tri_label == 6, turret["pivot"])
        children.append(tnode)

    out_nodes[0]["children"] = children

    out_doc: dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "SoundBookWorker/prepare_rover_wheels.py"},
        "scene": 0,
        "scenes": [{"name": "Scene", "nodes": [0]}],
        "nodes": out_nodes,
        "meshes": out_meshes,
        "accessors": out_accs,
        "bufferViews": out_bvs,
        "buffers": [{"byteLength": len(out_blob)}],
    }
    if out_materials:
        out_doc["materials"] = out_materials
    if out_textures:
        out_doc["textures"] = out_textures
    if out_images:
        out_doc["images"] = out_images
    if out_samplers:
        out_doc["samplers"] = out_samplers

    # Write binary GLB
    json_bytes = json.dumps(out_doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    json_bytes += b" " * ((4 - (len(json_bytes) % 4)) % 4)
    bin_bytes = bytes(out_blob)
    bin_bytes += b"\x00" * ((4 - (len(bin_bytes) % 4)) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    header = struct.pack("<4sII", _GLB_MAGIC, 2, total)
    j_hdr = struct.pack("<II", len(json_bytes), _JSON_CHUNK)
    b_hdr = struct.pack("<II", len(bin_bytes), _BIN_CHUNK)
    output_glb.parent.mkdir(parents=True, exist_ok=True)
    output_glb.write_bytes(header + j_hdr + json_bytes + b_hdr + bin_bytes)

    # Verify grounded world y_min across all wheels
    wheel_y_mins = [
        round(float(verts[faces[tri_label == wi], 1].min()), 5)
        for wi in range(len(WHEEL_NAMES))
    ]
    meta = {
        "source_glb": str(input_path.resolve()),
        "rigged_glb": str(output_glb.resolve()),
        "bounds_min": [round(float(v), 5) for v in verts.min(axis=0)],
        "bounds_max": [round(float(v), 5) for v in verts.max(axis=0)],
        "wheel_y_min": round(min(wheel_y_mins), 5),
        "chassis_triangles": int(chassis_tris),
        "wheels": wheel_meta,
        "turret": {
            "node": "Turret" if turret_tris > 0 else None,
            "pivot": turret["pivot"],
            "yaw_axis": [0.0, 1.0, 0.0],
            "triangles": int(turret_tris),
        },
        "total_triangles": int(len(faces)),
    }
    meta_json.parent.mkdir(parents=True, exist_ok=True)
    meta_json.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Prepare AT-1 rover rigged GLB with 6 pivoted wheels + turret.")
    ap.add_argument("--input", type=Path, default=_resolve_default_input())
    ap.add_argument("--output", type=Path, default=DEFAULT_OUT_GLB)
    ap.add_argument("--meta", type=Path, default=DEFAULT_META_JSON)
    args = ap.parse_args(argv)
    meta = prepare_rover_wheels(args.input, args.output, args.meta)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
