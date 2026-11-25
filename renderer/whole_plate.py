"""Accept one whole Kolors figure and bend it as a single mesh.

Limb sprites cut out of a finished painting open gaps when they rotate. This
module never splits the body. A plate is kept only when the keyed figure is one
piece, has no enclosed holes, and stands with its feet near the bottom of the
frame. A walk is a small bend of that same mesh. If the bend opens a hole, the
angle is halved. If the smallest bend still opens a hole, the figure stands.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from scipy.spatial import Delaunay, QhullError

from renderer.rig_mesh import _keep_main_island


OPAQUE = 32
# Rasterizing shared triangles leaves a few uncovered pixels. A cut that
# actually opens the knee is hundreds of pixels, well above this.
HOLE_SLACK_PX = 48
HOLE_SLACK_RATIO = 0.004
DISCONNECTED_RATIO = 0.08
CYCLE_FACTORS = (0.0, 0.35, 0.65, 1.0, 0.65, 0.35)
WALK_ANGLES = (12.0, 6.0, 3.0)
WALK_SHIFTS = (18.0, 0.0)


def ensure_rgba(image: np.ndarray) -> np.ndarray:
    """Return a contiguous RGBA array in RGB channel order."""
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] not in (3, 4):
        raise ValueError("expected an RGB or RGBA image")
    if array.shape[2] == 3:
        alpha = np.full(array.shape[:2], 255, dtype=np.uint8)
        array = np.dstack((array, alpha))
    return np.ascontiguousarray(array)


def interior_hole_count(alpha: np.ndarray, threshold: int = OPAQUE) -> int:
    """Count transparent pixels sealed inside the silhouette.

    Pixels that can reach the border through other transparent pixels are the
    outside, including the gap between two legs. Only enclosed pockets count.
    """
    return int(_hole_mask(alpha, threshold).sum())


def _hole_mask(alpha: np.ndarray, threshold: int = OPAQUE) -> np.ndarray:
    mask = (np.asarray(alpha) >= threshold).astype(np.uint8)
    if int(mask.sum()) < 10:
        return np.zeros(mask.shape, dtype=bool)
    padded = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=np.uint8)
    padded[1:-1, 1:-1] = mask
    exterior = np.where(padded == 0, np.uint8(1), np.uint8(0))
    flood = exterior.copy()
    cv2.floodFill(flood, None, (0, 0), 2)
    return flood[1:-1, 1:-1] == 1


def body_hole_count(alpha: np.ndarray, silhouette: np.ndarray) -> int:
    """Interior holes, ignoring the open space between two legs.

    A contact shadow under the shoes seals that gap so a flood from the border
    cannot reach it. It is not a hole in the torso.
    """
    solid = (np.asarray(alpha) >= OPAQUE).astype(np.uint8)
    # A few pixels of screen caught in an armpit is not a missing limb.
    closed = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    holes = _hole_mask(np.where(closed > 0, 255, 0).astype(np.uint8))
    if not np.any(holes):
        return 0
    box = _body_box(silhouette)
    if box is None:
        return int(holes.sum())
    _left, top, _width, height = box
    # Below the hips, a sealed gap between the legs is outside the body.
    holes = holes.copy()
    holes[top + int(height * 0.58) :] = False
    return int(holes.sum())


def hole_budget(alpha: np.ndarray) -> int:
    """Allowed new interior holes, including a few raster cracks."""
    opaque = int(np.count_nonzero(np.asarray(alpha) >= OPAQUE))
    return HOLE_SLACK_PX + int(opaque * HOLE_SLACK_RATIO)


def _border_mask(candidate: np.ndarray) -> np.ndarray:
    """True where a candidate pixel can reach the image border."""
    count, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=4)
    if count <= 1:
        return np.zeros(candidate.shape, dtype=bool)
    edges = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    edges = edges[edges > 0]
    if len(edges) == 0:
        return np.zeros(candidate.shape, dtype=bool)
    return np.isin(labels, edges)


def key_screen(image: np.ndarray) -> np.ndarray:
    """Remove a flat screen colour and a border-connected white floor.

    The screen match is a tight RGB distance from the corner colour. Dark hair
    and brown or black boots stay, because they are far from a pale green corner.
    A broad green-hue key deletes those same pixels.
    """
    rgba = ensure_rgba(image)
    rgb = rgba[:, :, :3].astype(np.float32)
    height, width = rgb.shape[:2]
    corner = max(8, min(16, height // 40, width // 40))
    samples = np.concatenate(
        (
            rgb[:corner, :corner].reshape(-1, 3),
            rgb[:corner, -corner:].reshape(-1, 3),
            rgb[-corner:, :corner].reshape(-1, 3),
            rgb[-corner:, -corner:].reshape(-1, 3),
        ),
        axis=0,
    )
    key = np.median(samples, axis=0)
    distance = np.linalg.norm(rgb - key.reshape(1, 1, 3), axis=2)
    screen = _border_mask(distance <= 42.0)
    low = np.min(rgb, axis=2)
    high = np.max(rgb, axis=2)
    paper = _border_mask((low >= 214.0) & ((high - low) <= 28.0))
    # Shadows and the gap between the legs stay green. These plates have no green clothes.
    greenish = (rgb[:, :, 1] > rgb[:, :, 0] + 12.0) & (rgb[:, :, 1] > rgb[:, :, 2] + 8.0) & (rgb[:, :, 1] > 70.0)
    remove = screen | paper | greenish
    out = rgba.copy()
    out[:, :, 3] = np.where(remove, 0, rgba[:, :, 3]).astype(np.uint8)
    fringe = (cv2.dilate(remove.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1) > 0) & ~remove
    corrected = out[:, :, :3].astype(np.float32)
    limit = np.maximum(corrected[:, :, 0], corrected[:, :, 2]) + 4.0
    corrected[:, :, 1] = np.where(fringe, np.minimum(corrected[:, :, 1], limit), corrected[:, :, 1])
    out[:, :, :3] = np.clip(corrected, 0, 255).astype(np.uint8)
    return _trim_sole_chips(out)


def _trim_sole_chips(image: np.ndarray) -> np.ndarray:
    """Drop a white floor chip or gray contact stain along the sole."""
    out = image.copy()
    rgb = out[:, :, :3].astype(np.int16)
    alpha = out[:, :, 3] >= OPAQUE
    low = np.min(rgb, axis=2)
    high = np.max(rgb, axis=2)
    spread = high - low
    pale = alpha & (low >= 200) & (spread <= 40)
    gray = alpha & (low >= 145) & (low <= 215) & (spread <= 30)
    height, width = alpha.shape
    rows = np.arange(height)
    for column in range(width):
        body = np.where(alpha[:, column] & ~pale[:, column] & ~gray[:, column])[0]
        if len(body) == 0:
            continue
        hanging = (pale[:, column] | gray[:, column]) & (rows > int(body.max()))
        out[hanging, column, 3] = 0
    box = _body_box(out[:, :, 3] >= OPAQUE)
    if box is not None:
        _left, top, _width, body_h = box
        band = rows >= top + int(body_h * 0.92)
        chip = band[:, None] & pale
        out[chip, 3] = 0
    return out


def clean_plate(image: np.ndarray) -> np.ndarray:
    """Key a flat illustration background and keep the main figure."""
    return _keep_main_island(key_screen(image))


def _main_component(alpha: np.ndarray) -> tuple[np.ndarray, int, list[int]]:
    binary = (alpha >= OPAQUE).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if count <= 1:
        return np.zeros(alpha.shape, dtype=bool), 0, []
    areas = stats[1:, cv2.CC_STAT_AREA]
    main_index = 1 + int(np.argmax(areas))
    main_area = int(stats[main_index, cv2.CC_STAT_AREA])
    others = [
        int(stats[index, cv2.CC_STAT_AREA])
        for index in range(1, count)
        if index != main_index and int(stats[index, cv2.CC_STAT_AREA]) > main_area * DISCONNECTED_RATIO
    ]
    return labels == main_index, main_area, others


def _body_box(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    top, bottom = int(ys.min()), int(ys.max())
    left, right = int(xs.min()), int(xs.max())
    return left, top, right - left + 1, bottom - top + 1


def _lower_feet(mask: np.ndarray, box: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """Bottom points of the large blobs in the lower body."""
    left, top, width, height = box
    band_top = top + int(height * 0.62)
    band = np.zeros(mask.shape, dtype=np.uint8)
    band[band_top:] = mask[band_top:]
    count, labels, stats, _ = cv2.connectedComponentsWithStats(band, 8)
    feet: list[tuple[int, int, int]] = []
    minimum = max(40, int(width * height * 0.01))
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area < minimum:
            continue
        ys, xs = np.where(labels == index)
        bottom = int(ys.max())
        sole = xs[ys >= bottom - 4]
        feet.append((int(np.median(sole)), bottom, area))
    feet.sort(key=lambda item: item[2], reverse=True)
    return [(x, y) for x, y, _area in feet[:3]]


def _skin_in_head(image: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """True when the upper body contains a compact skin-coloured patch."""
    left, top, width, height = box
    head_bottom = min(image.shape[0], top + max(8, int(height * 0.32)))
    crop = image[top:head_bottom, left : left + width]
    if crop.size == 0:
        return False
    red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
    alpha = crop[:, :, 3]
    skin = (alpha >= OPAQUE) & (red > 150) & (green > 100) & (blue > 80) & (red > green) & (red > blue - 10)
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(skin.astype(np.uint8), 8)
    if count <= 1:
        return False
    return int(stats[1:, cv2.CC_STAT_AREA].max()) >= 40


def _face_anchors(image: np.ndarray) -> dict | None:
    try:
        from renderer.face_anchors import detect_face_anchors
    except Exception:
        return None
    try:
        anchors = detect_face_anchors(image)
    except Exception:
        return None
    if float(anchors.get("confidence") or 0.0) < 0.35:
        return None
    return anchors


def accept_plate(image: np.ndarray, *, require_face: bool = True) -> dict:
    """Key one generated figure and report whether it can be used whole."""
    rgba = ensure_rgba(image)
    height, width = rgba.shape[:2]
    keyed = key_screen(rgba)
    mask, main_area, others = _main_component(keyed[:, :, 3])
    reasons: list[str] = []
    if main_area < int(width * height * 0.08):
        reasons.append("too_small")
    if others:
        reasons.append("disconnected")
    kept = keyed.copy()
    kept[:, :, 3] = np.where(mask, kept[:, :, 3], 0).astype(np.uint8)
    holes = body_hole_count(kept[:, :, 3], mask)
    if holes > hole_budget(kept[:, :, 3]):
        reasons.append("interior_hole")
    box = _body_box(mask)
    feet: list[tuple[int, int]] = []
    if box is None:
        reasons.append("empty")
    else:
        left, top, body_w, body_h = box
        bottom = top + body_h - 1
        right = left + body_w - 1
        edge_run = 24
        touches = (
            (top <= 1 and int(mask[0].sum()) > edge_run)
            or (left <= 1 and int(mask[:, 0].sum()) > edge_run)
            or (right >= width - 2 and int(mask[:, -1].sum()) > edge_run)
            or (bottom >= height - 2 and int(mask[-1].sum()) > edge_run)
        )
        if touches:
            reasons.append("cropped")
        if top > height * 0.34:
            reasons.append("head_low")
        if bottom < height * 0.78:
            reasons.append("feet_high")
        if body_h < body_w * 1.15:
            reasons.append("not_standing")
        feet = _lower_feet(mask, box)
        if len(feet) >= 3:
            reasons.append("extra_legs")
    anchors = None
    if require_face and box is not None and not reasons:
        anchors = _face_anchors(kept)
        if anchors is None and not _skin_in_head(kept, box):
            reasons.append("no_face")
    stride = False
    pair: dict[str, tuple[int, int]] | None = None
    if box is not None and len(feet) >= 2 and "extra_legs" not in reasons:
        ordered = sorted(feet[:2], key=lambda point: point[0])
        gap = ordered[1][0] - ordered[0][0]
        if gap > box[2] * 0.16:
            stride = True
            pair = {"trail": ordered[0], "plant": ordered[1]}
    plate = _keep_main_island(kept) if not reasons else kept
    return {
        "ok": not reasons,
        "reasons": reasons,
        "image": plate,
        "stride": bool(stride and not reasons),
        "feet": pair,
        "anchors": anchors,
        "body_box": box,
        "holes": holes,
    }


def _unique_points(points: np.ndarray) -> np.ndarray:
    rounded = np.round(points).astype(np.int32)
    _values, index = np.unique(rounded, axis=0, return_index=True)
    return points[np.sort(index)].astype(np.float64)


def _sample_points(mask: np.ndarray, stride: int) -> np.ndarray:
    contour_points: list[np.ndarray] = []
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    for contour in contours:
        if len(contour) < 8:
            continue
        contour_points.append(contour[:: max(1, stride // 2), 0, :].astype(np.float64))
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return np.zeros((0, 2), dtype=np.float64)
    grid = (xs % stride == 0) & (ys % stride == 0)
    interior = np.stack((xs[grid], ys[grid]), axis=1).astype(np.float64)
    parts = [interior]
    if contour_points:
        parts.append(np.vstack(contour_points))
    return _unique_points(np.vstack(parts))


def _segment_distance(points: np.ndarray, start: np.ndarray, end: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    delta = end - start
    length_sq = float(np.dot(delta, delta)) + 1e-6
    t = np.clip(((points - start) @ delta) / length_sq, 0.0, 1.0)
    closest = start + t[:, None] * delta
    return np.linalg.norm(points - closest, axis=1), t


def _axis_x(mask: np.ndarray, y: int, hint: int, window: int) -> int:
    height, width = mask.shape
    y = int(np.clip(y, 0, height - 1))
    left = max(0, hint - window)
    right = min(width, hint + window + 1)
    columns = np.where(mask[y, left:right])[0]
    if len(columns) == 0:
        return int(hint)
    return int(left + np.median(columns))


@dataclass
class PlateRig:
    points: np.ndarray
    triangles: np.ndarray
    weights: np.ndarray
    hip: np.ndarray
    knee: np.ndarray
    trail_foot: np.ndarray
    plant_hip: np.ndarray
    plant_foot: np.ndarray
    trail_radius: float
    plant_radius: float
    trail_index: int
    plant_xy: tuple[float, float]
    pad: int


def build_rig(image: np.ndarray, feet: dict[str, tuple[int, int]], *, stride: int = 14, pad: int = 28) -> PlateRig | None:
    """One triangle mesh over the whole figure, with two leg corridors."""
    alpha = ensure_rgba(image)[:, :, 3]
    mask = alpha >= OPAQUE
    points = _sample_points(mask, stride)
    if len(points) < 12:
        return None
    try:
        triangulation = Delaunay(points)
    except QhullError:
        return None
    simplices = triangulation.simplices
    kept: list[np.ndarray] = []
    for triangle in simplices:
        verts = points[triangle]
        edges = np.linalg.norm(verts - np.roll(verts, 1, axis=0), axis=1)
        if float(edges.max()) > stride * 3.6:
            continue
        centroid = verts.mean(axis=0)
        cx, cy = int(round(float(centroid[0]))), int(round(float(centroid[1])))
        if cy < 0 or cx < 0 or cy >= mask.shape[0] or cx >= mask.shape[1] or not mask[cy, cx]:
            continue
        kept.append(triangle)
    if len(kept) < 8:
        return None
    box = _body_box(mask)
    if box is None:
        return None
    _left, top, _width, body_h = box
    trail = np.asarray(feet["trail"], dtype=np.float64)
    plant = np.asarray(feet["plant"], dtype=np.float64)
    hip_y = top + body_h * 0.50
    knee_y = top + body_h * 0.74
    window = max(12, box[2] // 5)
    hip = np.array([_axis_x(mask, int(hip_y), int(trail[0]), window), hip_y], dtype=np.float64)
    knee = np.array([_axis_x(mask, int(knee_y), int(trail[0]), window), knee_y], dtype=np.float64)
    plant_hip = np.array([_axis_x(mask, int(hip_y), int(plant[0]), window), hip_y], dtype=np.float64)
    trail_radius = float(max(10.0, min(box[2] * 0.18, abs(float(plant[0] - trail[0])) * 0.42)))
    plant_radius = trail_radius
    trail_d, trail_t = _segment_distance(points, hip, trail)
    plant_d, plant_t = _segment_distance(points, plant_hip, plant)
    weights = np.zeros((len(points), 4), dtype=np.float64)
    for index in range(len(points)):
        on_trail = trail_d[index] <= trail_radius and trail_d[index] <= plant_d[index]
        on_plant = plant_d[index] <= plant_radius and not on_trail
        if on_trail:
            t = float(trail_t[index])
            if t < 0.40:
                weights[index, 1] = 1.0
            elif t > 0.62:
                weights[index, 2] = 1.0
            else:
                blend = (t - 0.40) / 0.22
                weights[index, 1] = 1.0 - blend
                weights[index, 2] = blend
        elif on_plant:
            t = float(plant_t[index])
            if t < 0.28:
                weights[index, 0] = 1.0
            elif t > 0.58:
                weights[index, 3] = 1.0
            else:
                blend = (t - 0.28) / 0.30
                weights[index, 0] = 1.0 - blend
                weights[index, 3] = blend
        else:
            weights[index, 0] = 1.0
    trail_index = int(np.argmin(np.linalg.norm(points - trail, axis=1)))
    return PlateRig(
        points=points,
        triangles=np.asarray(kept, dtype=np.int32),
        weights=weights,
        hip=hip,
        knee=knee,
        trail_foot=trail,
        plant_hip=plant_hip,
        plant_foot=plant,
        trail_radius=trail_radius,
        plant_radius=plant_radius,
        trail_index=trail_index,
        plant_xy=(float(plant[0] + pad), float(plant[1] + pad)),
        pad=pad,
    )


def _rotate(point: np.ndarray, origin: np.ndarray, degrees: float) -> np.ndarray:
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    delta = point - origin
    return origin + np.array([cosine * delta[0] - sine * delta[1], sine * delta[0] + cosine * delta[1]])


def pose_points(rig: PlateRig, knee_deg: float, shift: float) -> np.ndarray:
    """Bend the trailing knee and shift the body off the planted foot."""
    offset = np.array([shift, 0.0], dtype=np.float64)
    thigh_deg = knee_deg * 0.25
    hip = rig.hip + offset
    knee = _rotate(rig.knee + offset, hip, thigh_deg)
    points = rig.points
    body = points + offset
    thigh = np.vstack([_rotate(point + offset, hip, thigh_deg) for point in points])
    shin = np.vstack([_rotate(point, knee, knee_deg) for point in thigh])
    posed = (
        rig.weights[:, 0:1] * body
        + rig.weights[:, 1:2] * thigh
        + rig.weights[:, 2:3] * shin
        + rig.weights[:, 3:4] * points
    )
    return posed


def _composite(destination: np.ndarray, source: np.ndarray, mask: np.ndarray, x: int, y: int) -> None:
    height, width = source.shape[:2]
    target = destination[y : y + height, x : x + width]
    alpha = (source[:, :, 3].astype(np.float32) / 255.0) * (mask.astype(np.float32) / 255.0)
    take = alpha > (target[:, :, 3].astype(np.float32) / 255.0)
    if not np.any(take):
        return
    written = target.copy()
    written[take, :3] = source[take, :3]
    written[take, 3] = np.clip(alpha[take] * 255.0, 0, 255).astype(np.uint8)
    target[:] = written


def _expanded(points: np.ndarray, amount: float = 0.45) -> np.ndarray:
    center = points.mean(axis=0)
    vectors = points - center
    lengths = np.linalg.norm(vectors, axis=1)
    scales = np.where(lengths > 1e-4, (lengths + amount) / lengths, 1.0)
    return center + vectors * scales[:, None]


def _paint_triangle(
    canvas: np.ndarray,
    texture: np.ndarray,
    source_points: np.ndarray,
    destination_points: np.ndarray,
) -> None:
    area = (source_points[1, 0] - source_points[0, 0]) * (source_points[2, 1] - source_points[0, 1])
    area -= (source_points[1, 1] - source_points[0, 1]) * (source_points[2, 0] - source_points[0, 0])
    if abs(float(area)) < 0.08:
        return
    canvas_h, canvas_w = canvas.shape[:2]
    texture_h, texture_w = texture.shape[:2]
    sx0 = max(0, int(math.floor(float(source_points[:, 0].min()))) - 2)
    sy0 = max(0, int(math.floor(float(source_points[:, 1].min()))) - 2)
    sx1 = min(texture_w, int(math.ceil(float(source_points[:, 0].max()))) + 3)
    sy1 = min(texture_h, int(math.ceil(float(source_points[:, 1].max()))) + 3)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    expanded = _expanded(destination_points)
    dx0 = max(0, int(math.floor(float(expanded[:, 0].min()))) - 2)
    dy0 = max(0, int(math.floor(float(expanded[:, 1].min()))) - 2)
    dx1 = min(canvas_w, int(math.ceil(float(expanded[:, 0].max()))) + 3)
    dy1 = min(canvas_h, int(math.ceil(float(expanded[:, 1].max()))) + 3)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    source_local = source_points - np.array([sx0, sy0], dtype=np.float32)
    destination_local = destination_points - np.array([dx0, dy0], dtype=np.float32)
    affine = cv2.getAffineTransform(source_local.astype(np.float32), destination_local.astype(np.float32))
    crop = np.ascontiguousarray(texture[sy0:sy1, sx0:sx1])
    warped = cv2.warpAffine(
        crop,
        affine,
        (dx1 - dx0, dy1 - dy0),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    mask = np.zeros((dy1 - dy0, dx1 - dx0), dtype=np.uint8)
    local = expanded - np.array([dx0, dy0], dtype=np.float32)
    cv2.fillConvexPoly(mask, np.round(local * 16.0).astype(np.int32), 255, lineType=cv2.LINE_AA, shift=4)
    _composite(canvas, warped, mask, dx0, dy0)


def render_pose(image: np.ndarray, rig: PlateRig, knee_deg: float, shift: float) -> np.ndarray:
    """Draw the posed mesh. The planted foot stays on its rest pixel."""
    texture = ensure_rgba(image)
    pad = rig.pad
    height, width = texture.shape[:2]
    canvas = np.zeros((height + pad * 2, width + pad * 2, 4), dtype=np.uint8)
    posed = pose_points(rig, knee_deg, shift)
    source = rig.points + np.array([pad, pad], dtype=np.float64)
    destination = posed + np.array([pad, pad], dtype=np.float64)
    for triangle in rig.triangles:
        _paint_triangle(canvas, texture, source[triangle].astype(np.float32), destination[triangle].astype(np.float32))
    return canvas


def _peak_is_closed(image: np.ndarray, rig: PlateRig, angle: float, shift: float) -> bool:
    rest_holes = interior_hole_count(ensure_rgba(image)[:, :, 3])
    posed = render_pose(image, rig, angle, shift)
    opened = interior_hole_count(posed[:, :, 3]) - rest_holes
    return opened <= hole_budget(image[:, :, 3] if image.shape[2] == 4 else render_pose(image, rig, 0, 0)[:, :, 3])


def choose_motion(image: np.ndarray, plate: dict) -> dict:
    """Use the largest bend that does not open a hole, or stand."""
    rgba = plate["image"]
    still = {
        "mode": "stand",
        "angle": 0.0,
        "shift": 0.0,
        "frames": [{"image": rgba, "anchor": _sole_anchor(rgba)}],
    }
    if not plate.get("stride") or not plate.get("feet"):
        return still
    rig = build_rig(rgba, plate["feet"])
    if rig is None:
        return still
    for shift in WALK_SHIFTS:
        for angle in WALK_ANGLES:
            if not _peak_is_closed(rgba, rig, angle, shift):
                continue
            unique = {}
            frames = []
            for factor in CYCLE_FACTORS:
                key = round(factor, 3)
                if key not in unique:
                    unique[key] = render_pose(rgba, rig, angle * factor, shift * factor)
                frames.append({"image": unique[key], "anchor": rig.plant_xy})
            return {"mode": "walk", "angle": angle, "shift": shift, "frames": frames, "rig": rig}
    return still


def _sole_anchor(image: np.ndarray) -> tuple[float, float]:
    alpha = ensure_rgba(image)[:, :, 3]
    ys, xs = np.where(alpha >= OPAQUE)
    if len(xs) == 0:
        return (0.0, 0.0)
    bottom = int(ys.max())
    sole = xs[ys >= bottom - 3]
    return float(np.median(sole)), float(bottom)


def open_mouth(image: np.ndarray, anchors: dict | None, amount: int = 4) -> np.ndarray:
    """Separate lip pixels inside the detected mouth. Never draw past that box."""
    if not anchors or "mouth" not in anchors:
        return ensure_rgba(image)
    mouth = anchors["mouth"]
    x, y, w, h = int(mouth["x"]), int(mouth["y"]), int(mouth["w"]), int(mouth["h"])
    rgba = ensure_rgba(image)
    height, width = rgba.shape[:2]
    if w < 6 or h < 4 or w > width * 0.22 or h > height * 0.08:
        return rgba
    x = max(0, min(width - 1, x))
    y = max(0, min(height - 1, y))
    w = min(w, width - x)
    h = min(h, height - y)
    crop = rgba[y : y + h, x : x + w]
    opaque = crop[:, :, 3] >= OPAQUE
    if int(opaque.sum()) < 12:
        return rgba
    luma = crop[:, :, 0].astype(np.int16) + crop[:, :, 1].astype(np.int16) + crop[:, :, 2].astype(np.int16)
    dark = opaque & (luma <= np.percentile(luma[opaque], 35))
    if int(dark.sum()) < 8:
        return rgba
    rows = np.where(dark.any(axis=1))[0]
    cols = np.where(dark.any(axis=0))[0]
    if len(rows) < 2 or len(cols) < 2:
        return rgba
    band = crop[int(rows.min()) : int(rows.max()) + 1, int(cols.min()) : int(cols.max()) + 1]
    band_h = band.shape[0]
    gap = int(max(1, min(amount, band_h // 3)))
    if gap < 1 or band_h < gap + 2:
        return rgba
    top_h = band_h // 2
    top = cv2.resize(band[:top_h], (band.shape[1], max(1, top_h - gap // 2)), interpolation=cv2.INTER_LINEAR)
    bottom_src = band[top_h:]
    bottom = cv2.resize(bottom_src, (band.shape[1], max(1, band_h - top.shape[0] - gap)), interpolation=cv2.INTER_LINEAR)
    opened = np.zeros_like(band)
    opened[: top.shape[0]] = top
    opened[top.shape[0] + gap :] = bottom[: opened.shape[0] - (top.shape[0] + gap)]
    lip_pixels = band[dark[int(rows.min()) : int(rows.max()) + 1, int(cols.min()) : int(cols.max()) + 1]]
    if len(lip_pixels) == 0:
        return rgba
    lip = np.median(lip_pixels[:, :3], axis=0)
    seam = opened[:, :, 3] < OPAQUE
    center = np.zeros(opened.shape[:2], dtype=bool)
    center[:, int(opened.shape[1] * 0.22) : int(opened.shape[1] * 0.78)] = True
    lip_columns = band[band_h // 2, :, 3] >= OPAQUE
    fill = seam & center & lip_columns[None, :]
    opened[fill, :3] = lip
    opened[fill, 3] = 255
    result = rgba.copy()
    y0 = y + int(rows.min())
    x0 = x + int(cols.min())
    result[y0 : y0 + opened.shape[0], x0 : x0 + opened.shape[1]] = opened
    return result
