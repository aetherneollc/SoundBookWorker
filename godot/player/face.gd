class_name PuppetFace
extends Node2D

## 挂在 head Bone2D 下的程序化面部。
## 纹理像素 p 映射到本节点局部坐标：(p - head_origin_px) * puppet_scale。
## head 的 rest.rotation 为 0，因此这里不再叠加旋转。

const CAVITY := Color(0.58, 0.21, 0.24, 1.0)
const THROAT := Color(0.45, 0.14, 0.17, 1.0)
const TONGUE := Color(0.86, 0.48, 0.50, 1.0)
const TEETH := Color(0.97, 0.96, 0.94, 1.0)
const GAZE_PX := 1.8
const PATCH_SEGS := 40

var _ready_face := false
var _head_origin := Vector2.ZERO
var _puppet_scale := 1.0
var _skin := Color(0.910, 0.745, 0.627, 1.0)
var _ink := Color(0.110, 0.110, 0.125, 1.0)
var _mouth := Rect2()
var _eye_left := Rect2()
var _eye_right := Rect2()
var _brow_left := Rect2()
var _brow_right := Rect2()

var _viseme := "X"
var _emotion := "neutral"
var _blink := 0.0
var _gaze := Vector2.ZERO


func _init() -> void:
	z_index = 10
	z_as_relative = true


func setup(anchors: Dictionary, head_origin_px: Vector2, puppet_scale: float) -> void:
	_head_origin = head_origin_px
	_puppet_scale = absf(puppet_scale)
	if _puppet_scale < 0.00001:
		_puppet_scale = 0.00001
	_skin = _rgb(anchors.get("skin_rgb", null), Color(0.910, 0.745, 0.627, 1.0))
	_ink = _rgb(anchors.get("ink_rgb", null), Color(0.110, 0.110, 0.125, 1.0))
	_mouth = _box(anchors.get("mouth", null))
	_eye_left = _box(anchors.get("eye_left", null))
	_eye_right = _box(anchors.get("eye_right", null))
	_brow_left = _box(anchors.get("brow_left", null))
	_brow_right = _box(anchors.get("brow_right", null))
	z_index = 10
	z_as_relative = true
	_ready_face = true
	queue_redraw()


func set_viseme(code: String) -> void:
	var next := _normalize_viseme(code)
	if next == _viseme:
		return
	_viseme = next
	queue_redraw()


func set_emotion(emotion: String) -> void:
	var next := _normalize_emotion(emotion)
	if next == _emotion:
		return
	_emotion = next
	queue_redraw()


func set_blink(amount: float) -> void:
	var next := clampf(amount, 0.0, 1.0)
	if is_equal_approx(next, _blink):
		return
	var was_drawn := _blink > 0.02
	_blink = next
	if was_drawn or next > 0.02:
		queue_redraw()


func set_gaze(offset: Vector2) -> void:
	var next := Vector2(clampf(offset.x, -1.0, 1.0), clampf(offset.y, -1.0, 1.0))
	if next.is_equal_approx(_gaze):
		return
	_gaze = next
	queue_redraw()


func _draw() -> void:
	if not _ready_face:
		return
	_draw_mouth()
	_draw_eye(_eye_left)
	_draw_eye(_eye_right)
	if _emotion != "neutral":
		_draw_brow(_brow_left, true)
		_draw_brow(_brow_right, false)


func _draw_mouth() -> void:
	if _mouth.size.x < 0.6 or _mouth.size.y < 0.6:
		return
	var center := _mouth.get_center()
	_draw_patch(center)
	var at := center + _shift()
	var mw := _mouth.size.x
	match _viseme:
		"A":
			_draw_closed(at, mw, 0.34, 0.0, 1.24, 0.84)
		"B":
			_draw_b(at, mw)
		"C":
			_draw_c(at, mw)
		"D":
			_draw_d(at, mw)
		"E":
			_draw_e(at, mw)
		"F":
			_draw_f(at, mw)
		_:
			_draw_rest(at, mw)


func _draw_patch(center: Vector2) -> void:
	# 内层椭圆必须包住整个嘴矩形（含四角），多边形还要补上弦高误差。
	var hw := _mouth.size.x * 0.5
	var hh := _mouth.size.y * 0.5
	var radial := 1.0 / cos(PI / float(PATCH_SEGS))
	var contain := sqrt(2.0) * radial * 1.015
	var irx := hw * contain
	var iry := hh * contain
	_paint_ellipse(center, irx * 1.15, iry * 1.15, _skin_a(0.55), PATCH_SEGS)
	_paint_ellipse(center, irx, iry, _skin_a(1.0), PATCH_SEGS)


func _draw_rest(center: Vector2, mw: float) -> void:
	match _emotion:
		"happy":
			_draw_closed(center, mw, 0.72, 0.100, 1.06, 0.30)
		"sad":
			_draw_closed(center, mw, 0.56, -0.064, 0.96, 0.38)
		"angry":
			_draw_closed(center, mw, 0.40, -0.016, 1.34, 0.80)
		"serious":
			_draw_closed(center, mw, 0.64, 0.0, 0.90, 0.64)
		"surprised":
			_draw_closed(center, mw, 0.42, 0.010, 1.0, 0.52)
		_:
			_draw_closed(center, mw, 0.60, 0.050, 1.0, 0.34)


func _draw_closed(
	center: Vector2,
	mw: float,
	width_ratio: float,
	drop_ratio: float,
	width_mul: float,
	end_ratio: float,
) -> void:
	var half_w := maxf(mw * width_ratio * 0.5, 0.8)
	var drop := mw * drop_ratio
	var pts := _parabola(center, half_w, drop, 18)
	var width := maxf(1.05, _ink_width() * width_mul)
	_stroke_variable(pts, _ink, _widths_bell(pts.size(), width, end_ratio))


func _draw_b(center: Vector2, mw: float) -> void:
	var lw := _ink_width()
	var rx := _span(mw, 0.35, lw, 2.4)
	var ry := _span(mw, 0.090, lw, 1.05)
	var pts := _ellipse_pts(center, rx, ry, 36)
	_fill(pts, CAVITY)
	var ix := _keep(rx, lw)
	var iy := _keep(ry, lw)
	_fill(_upper_cap(center, ix, iy, 0.40, 14), TEETH)
	_fill(_lower_cap(center + Vector2(0.0, iy * 0.06), ix * 0.78, iy * 0.92, 0.30, 12), TONGUE)
	_stroke_loop(pts, _ink, lw)


func _draw_c(center: Vector2, mw: float) -> void:
	var lw := _ink_width()
	var rx := _span(mw, 0.31, lw, 2.1)
	# 纵向比初版收 12%，避免中开口把人中到下巴撑满。
	var ry := _span(mw, 0.154, lw, 1.28)
	var pts := _ellipse_pts(center, rx, ry, 40)
	_fill(pts, CAVITY)
	var ix := _keep(rx, lw)
	var iy := _keep(ry, lw)
	_paint_ellipse(center + Vector2(0.0, iy * 0.08), ix * 0.48, iy * 0.42, THROAT, 24)
	_fill(_upper_cap(center, ix, iy, 0.32, 14), TEETH)
	_tooth_seams(center.x - ix * 0.34, center.x + ix * 0.34, center.y - iy * 0.78, center.y - iy * 0.28, 1)
	_fill(_lower_cap(center, ix * 0.84, iy * 0.90, 0.32, 14), TONGUE)
	_stroke_loop(pts, _ink, lw)


func _draw_d(center: Vector2, mw: float) -> void:
	var lw := _ink_width()
	var rx := _span(mw, 0.40, lw, 2.5)
	# 纵向开口约为初版的 72%（收 28%），保持宽扁 D 形而不是方口。
	var ry := _span(mw, 0.245, lw, 1.55)
	var pts := _d_points(center, rx, ry)
	_fill(pts, CAVITY)
	_paint_ellipse(center + Vector2(0.0, ry * 0.02), rx * 0.42, ry * 0.36, THROAT, 24)
	var top_y := center.y - ry
	var tooth_h := clampf(ry * 0.36, lw * 0.95, ry * 0.48)
	var y0 := top_y + lw * 0.78
	var y1 := y0 + tooth_h
	var inset := lw * 0.9 + rx * 0.07
	_fill(_tooth_band(center.x, rx - inset, y0, y1), TEETH)
	_tooth_seams(center.x - rx * 0.46, center.x + rx * 0.46, y0 + tooth_h * 0.12, y1 - tooth_h * 0.08, 2)
	var tongue_lift := ry * 0.46
	var tongue_at := Vector2(center.x, center.y + ry * 0.58)
	_fill(_mound(tongue_at, rx * 0.56, tongue_lift, 16), TONGUE)
	_stroke_loop(pts, _ink, lw)


func _draw_e(center: Vector2, mw: float) -> void:
	var lw := _ink_width()
	# 圆唇略收：宽约 92%，高约 84%，仍是竖向小中口。
	var rx := _span(mw, 0.143, lw, 1.06)
	var ry := _span(mw, 0.181, lw, 1.30)
	var pts := _ellipse_pts(center, rx, ry, 40)
	_fill(pts, CAVITY)
	var ix := _keep(rx, lw)
	var iy := _keep(ry, lw)
	_paint_ellipse(center + Vector2(0.0, iy * 0.06), ix * 0.52, iy * 0.46, THROAT, 22)
	_fill(_upper_cap(center, ix * 0.92, iy * 0.94, 0.20, 12), TEETH)
	_fill(_lower_cap(center, ix * 0.70, iy * 0.88, 0.20, 12), TONGUE)
	_stroke_loop(pts, _ink, lw)


func _draw_f(center: Vector2, mw: float) -> void:
	var lw := _ink_width()
	var rx := _span(mw, 0.28, lw, 2.1)
	var ry := _span(mw, 0.078, lw, 1.2)
	var pts := _ellipse_pts(center, rx, ry, 32)
	_fill(pts, TEETH)
	var ix := _keep(rx, lw)
	var iy := _keep(ry, lw)
	_fill(_lower_cap(center, ix * 0.92, iy * 0.94, 0.38, 12), CAVITY)
	_stroke_loop(pts, _ink, lw)
	var press := _parabola(Vector2(center.x, center.y + ry + lw * 0.05), rx * 0.90, maxf(ry * 0.42, 0.7), 14)
	_stroke_variable(press, _ink, _widths_bell(press.size(), maxf(1.15, lw * 0.92), 0.32))


func _draw_eye(rect: Rect2) -> void:
	if _blink <= 0.02 or rect.size.x < 0.8 or rect.size.y < 0.8:
		return
	var amount := clampf(_blink, 0.0, 1.0)
	var origin := rect.position + _shift()
	var wh := rect.size
	var pad_x := maxf(2.8, wh.x * 0.06)
	var left := origin.x - pad_x
	var right := origin.x + wh.x + pad_x
	var top := origin.y - wh.y * 0.04
	var bottom := origin.y + wh.y * 1.02
	var frontier := lerpf(top, bottom, amount)
	var bow := wh.y * lerpf(0.045, 0.24, amount)
	if amount >= 0.96:
		# 全闭时眼皮必须盖住整个眼矩形，弯月线两端抬起后下角也不能露出原眼。
		_fill_rect(Rect2(Vector2(left, top), Vector2(right - left, bottom - top + 2.0)), _skin)
	var segs := 14
	var lid := _vec(segs + 3)
	lid[0] = Vector2(left, top)
	lid[1] = Vector2(right, top)
	for i in segs + 1:
		var t := float(segs - i) / float(segs)
		lid[i + 2] = Vector2(lerpf(left, right, t), _lid_y(top, frontier, bow, t))
	_fill(lid, _skin)
	var lash := _vec(segs + 1)
	for i in segs + 1:
		var t := float(i) / float(segs)
		lash[i] = Vector2(lerpf(left, right, t), _lid_y(top, frontier, bow, t))
	var lash_w := maxf(1.15, wh.y * 0.090)
	_stroke_variable(lash, _ink, _widths_bell(lash.size(), lash_w, 0.26))


func _draw_brow(rect: Rect2, is_left: bool) -> void:
	if rect.size.x < 1.0 or rect.size.y < 0.4:
		return
	var bw := rect.size.x
	var bh := rect.size.y
	var inner_dy := 0.0
	var outer_dy := 0.0
	var arch := 0.0
	var slide := Vector2.ZERO
	match _emotion:
		"angry":
			inner_dy = bh * 1.35
			outer_dy = -bh * 0.95
			arch = bh * 0.12
			slide = Vector2(bw * 0.07, bh * 0.42)
		"sad":
			inner_dy = -maxf(bh * 1.65, bw * 0.10)
			outer_dy = bh * 0.48
			arch = maxf(bh * 0.70, bw * 0.05)
			slide = Vector2(bw * 0.04, -bh * 0.20)
		"surprised":
			inner_dy = -bh * 0.10
			outer_dy = bh * 0.18
			arch = maxf(bh * 1.25, bw * 0.11)
			slide = Vector2(0.0, -maxf(bh * 1.65, bw * 0.15))
		"happy":
			inner_dy = -bh * 0.06
			outer_dy = -bh * 0.42
			arch = maxf(bh * 0.82, bw * 0.045)
			slide = Vector2(0.0, -bh * 0.28)
		"serious":
			inner_dy = bh * 0.28
			outer_dy = bh * 0.06
			arch = bh * 0.06
			slide = Vector2(0.0, bh * 0.48)
		_:
			return
	if not is_left:
		slide.x = -slide.x
	var anchor := rect.get_center() + _shift()
	var rx := maxf(bw * 0.58, 1.6)
	var ry := maxf(bh * 1.30, bw * 0.09)
	_paint_ellipse(anchor, rx * 1.22, ry * 1.30, _skin_a(0.30), 24)
	_paint_ellipse(anchor, rx, ry, _skin_a(0.58), 24)
	var center := anchor + slide
	var half_w := bw * 0.48
	var pts := _brow_pts(center, half_w, is_left, inner_dy, outer_dy, arch)
	var width := maxf(1.30, minf(bh * 0.92, bw * 0.18))
	_stroke_variable(pts, _ink, _widths_brow(pts.size(), width, is_left))


func _brow_pts(
	center: Vector2,
	half_w: float,
	is_left: bool,
	inner_dy: float,
	outer_dy: float,
	arch: float,
) -> PackedVector2Array:
	var segs := 18
	var pts := _vec(segs)
	var peak := 0.5
	if _emotion == "sad":
		peak = 0.70 if is_left else 0.30
	var peak_span := maxf(peak, 1.0 - peak)
	for i in segs:
		var t := float(i) / float(segs - 1)
		var inner_t := t if is_left else 1.0 - t
		var y := lerpf(outer_dy, inner_dy, inner_t)
		var dist := clampf(absf(t - peak) / peak_span, 0.0, 1.0)
		var bell := 1.0 - dist
		bell = bell * bell * (3.0 - 2.0 * bell)
		y -= arch * bell
		pts[i] = center + Vector2(lerpf(-half_w, half_w, t), y)
	return pts


func _lid_y(top: float, frontier: float, bow: float, t: float) -> float:
	var u := absf(t - 0.5) * 2.0
	return maxf(top + 0.35, frontier - bow * u * u)


func _normalize_viseme(code: String) -> String:
	match code.strip_edges().to_upper():
		"A", "B", "C", "D", "E", "F":
			return code.strip_edges().to_upper()
		_:
			return "X"


func _normalize_emotion(emotion: String) -> String:
	match emotion.strip_edges().to_lower():
		"neutral", "happy", "angry", "sad", "surprised", "serious":
			return emotion.strip_edges().to_lower()
		_:
			return "neutral"


func _shift() -> Vector2:
	return _gaze * GAZE_PX


func _ink_width() -> float:
	return maxf(1.2, _mouth.size.y * 0.14)


func _span(mw: float, fraction: float, lw: float, lw_mul: float) -> float:
	return maxf(mw * fraction, lw * lw_mul)


func _keep(radius: float, lw: float) -> float:
	return maxf(radius - lw * 0.62, radius * 0.42)


func _skin_a(alpha: float) -> Color:
	return Color(_skin.r, _skin.g, _skin.b, alpha)


func _vec(count: int) -> PackedVector2Array:
	var pts := PackedVector2Array()
	@warning_ignore("return_value_discarded")
	pts.resize(maxi(count, 0))
	return pts


func _floats(count: int) -> PackedFloat32Array:
	var values := PackedFloat32Array()
	@warning_ignore("return_value_discarded")
	values.resize(maxi(count, 0))
	return values


func _as_float(value: Variant) -> float:
	var kind := typeof(value)
	if kind == TYPE_INT or kind == TYPE_FLOAT:
		return float(str(value))
	return 0.0


func _paint_ellipse(center: Vector2, rx: float, ry: float, color: Color, segs: int) -> void:
	_fill(_ellipse_pts(center, rx, ry, segs), color)


func _fill(pts: PackedVector2Array, color: Color) -> void:
	if pts.size() >= 3 and color.a > 0.001:
		draw_colored_polygon(pts, color)


func _fill_rect(rect: Rect2, color: Color) -> void:
	var pts := _vec(4)
	var origin := rect.position
	var wh := rect.size
	pts[0] = origin
	pts[1] = origin + Vector2(wh.x, 0.0)
	pts[2] = origin + wh
	pts[3] = origin + Vector2(0.0, wh.y)
	_fill(pts, color)


func _stroke_loop(pts: PackedVector2Array, color: Color, width: float) -> void:
	if pts.size() < 3 or width <= 0.05 or color.a <= 0.001:
		return
	var count := pts.size()
	var loop := _vec(count + 2)
	for i in count:
		loop[i] = pts[i]
	loop[count] = pts[0]
	loop[count + 1] = pts[1]
	draw_polyline(loop, color, width, true)


func _stroke_variable(pts: PackedVector2Array, color: Color, widths: PackedFloat32Array) -> void:
	var count := pts.size()
	if count < 2 or widths.size() != count or color.a <= 0.001:
		return
	for i in count - 1:
		var p0 := pts[i]
		var p1 := pts[i + 1]
		var dir := p1 - p0
		var length := dir.length()
		if length < 0.05:
			continue
		dir /= length
		var nrm := Vector2(-dir.y, dir.x)
		var w0 := maxf(widths[i], 0.0)
		var w1 := maxf(widths[i + 1], 0.0)
		var quad := _vec(4)
		quad[0] = p0 + nrm * w0 * 0.5
		quad[1] = p1 + nrm * w1 * 0.5
		quad[2] = p1 - nrm * w1 * 0.5
		quad[3] = p0 - nrm * w0 * 0.5
		draw_colored_polygon(quad, color)
		draw_line(p0, p1, color, maxf((w0 + w1) * 0.5, 0.4), true)
	_cap(pts[0], widths[0], color)
	_cap(pts[count - 1], widths[count - 1], color)


func _cap(at: Vector2, width: float, color: Color) -> void:
	var radius := maxf(width, 0.0) * 0.5
	if radius > 0.15:
		draw_circle(at, radius, color, true, -1.0, true)


func _widths_bell(count: int, width: float, end_ratio: float) -> PackedFloat32Array:
	var safe := maxi(count, 0)
	var widths := _floats(safe)
	var denom := float(maxi(safe - 1, 1))
	for i in safe:
		var bell := sin((float(i) / denom) * PI)
		widths[i] = width * lerpf(end_ratio, 1.0, bell)
	return widths


func _widths_brow(count: int, width: float, is_left: bool) -> PackedFloat32Array:
	var safe := maxi(count, 0)
	var widths := _floats(safe)
	var denom := float(maxi(safe - 1, 1))
	for i in safe:
		var t := float(i) / denom
		var inner_t := t if is_left else 1.0 - t
		var shaped := pow(clampf(inner_t, 0.0, 1.0), 0.55)
		widths[i] = lerpf(width * 0.14, width, shaped)
	return widths


func _ellipse_pts(center: Vector2, rx: float, ry: float, segs: int) -> PackedVector2Array:
	var pts := PackedVector2Array()
	if rx < 0.2 or ry < 0.2:
		return pts
	var count := maxi(segs, 8)
	pts = _vec(count)
	for i in count:
		var a := TAU * float(i) / float(count)
		pts[i] = center + Vector2(cos(a) * rx, sin(a) * ry)
	return pts


func _arc_pts(
	center: Vector2,
	rx: float,
	ry: float,
	a0: float,
	a1: float,
	segs: int,
) -> PackedVector2Array:
	var pts := PackedVector2Array()
	if rx < 0.2 or ry < 0.2:
		return pts
	var count := maxi(segs, 2)
	pts = _vec(count)
	var denom := float(count - 1)
	for i in count:
		var a := lerpf(a0, a1, float(i) / denom)
		pts[i] = center + Vector2(cos(a) * rx, sin(a) * ry)
	return pts


func _upper_cap(center: Vector2, rx: float, ry: float, fraction: float, segs: int) -> PackedVector2Array:
	var rel := clampf(-1.0 + 2.0 * fraction, -0.92, 0.92)
	var a_cut := asin(rel)
	return _arc_pts(center, rx, ry, PI - a_cut, TAU + a_cut, segs)


func _lower_cap(center: Vector2, rx: float, ry: float, fraction: float, segs: int) -> PackedVector2Array:
	var rel := clampf(1.0 - 2.0 * fraction, -0.92, 0.92)
	var a_cut := asin(rel)
	return _arc_pts(center, rx, ry, a_cut, PI - a_cut, segs)


func _parabola(center: Vector2, half_w: float, drop: float, segs: int) -> PackedVector2Array:
	var count := maxi(segs, 2)
	var pts := _vec(count)
	var denom := float(count - 1)
	for i in count:
		var u := lerpf(-1.0, 1.0, float(i) / denom)
		pts[i] = center + Vector2(u * half_w, drop * (1.0 - u * u))
	return pts


func _d_points(center: Vector2, rx: float, ry: float) -> PackedVector2Array:
	var top_y := center.y - ry
	var depth := ry * 2.0
	var corner := clampf(depth * 0.075, 0.4, rx * 0.18)
	var steps := 16
	var pts := _vec(4 + steps - 1)
	pts[0] = Vector2(center.x - rx, top_y + corner)
	pts[1] = Vector2(center.x - rx + corner, top_y)
	pts[2] = Vector2(center.x + rx - corner, top_y)
	pts[3] = Vector2(center.x + rx, top_y + corner)
	for i in range(1, steps):
		var ang := lerpf(0.0, PI, float(i) / float(steps))
		var x := center.x + cos(ang) * rx
		var y := top_y + corner + sin(ang) * (depth - corner)
		pts[3 + i] = Vector2(x, y)
	return pts


func _tooth_band(mid_x: float, half_w: float, y0: float, y1: float) -> PackedVector2Array:
	if half_w < 0.4 or y1 <= y0:
		return PackedVector2Array()
	var side := maxf((y1 - y0) * 0.22, 0.4)
	var segs := 8
	var pts := _vec(4 + segs + 1)
	pts[0] = Vector2(mid_x - half_w, y0 + side)
	pts[1] = Vector2(mid_x - half_w + side, y0)
	pts[2] = Vector2(mid_x + half_w - side, y0)
	pts[3] = Vector2(mid_x + half_w, y0 + side)
	for i in range(segs, -1, -1):
		var u := lerpf(-1.0, 1.0, float(i) / float(segs))
		var y := y1 + (1.0 - u * u) * (y1 - y0) * 0.10
		pts[4 + segs - i] = Vector2(mid_x + u * half_w, y)
	return pts


func _mound(center: Vector2, rx: float, lift: float, segs: int) -> PackedVector2Array:
	var count := maxi(segs, 3)
	var pts := _vec(count)
	var denom := float(count - 1)
	for i in count:
		var u := lerpf(-1.0, 1.0, float(i) / denom)
		pts[i] = Vector2(center.x + u * rx, center.y - lift * (1.0 - u * u))
	return pts


func _tooth_seams(x0: float, x1: float, y0: float, y1: float, count: int) -> void:
	var span := x1 - x0
	if count <= 0 or span < 15.0 or y1 - y0 < 2.0:
		return
	var seam := Color(0.76, 0.70, 0.66, 0.72)
	var width := maxf(0.65, _ink_width() * 0.15)
	for i in count:
		var x := lerpf(x0, x1, float(i + 1) / float(count + 1))
		draw_line(Vector2(x, y0), Vector2(x, y1), seam, width, true)


func _box(value: Variant) -> Rect2:
	if value is not Dictionary:
		return Rect2()
	var box: Dictionary = value
	var origin := Vector2(_num(box, "x"), _num(box, "y"))
	var wh := Vector2(_num(box, "w"), _num(box, "h"))
	if wh.x <= 0.0 or wh.y <= 0.0:
		return Rect2()
	return Rect2((origin - _head_origin) * _puppet_scale, wh * _puppet_scale)


func _num(box: Dictionary, key: String) -> float:
	return _as_float(box.get(key, 0.0))


func _rgb(value: Variant, fallback: Color) -> Color:
	return Color(
		_channel(value, 0, fallback.r),
		_channel(value, 1, fallback.g),
		_channel(value, 2, fallback.b),
		1.0,
	)


func _channel(value: Variant, index: int, fallback: float) -> float:
	if value is PackedFloat32Array:
		var packed: PackedFloat32Array = value
		if index < packed.size():
			return clampf(packed[index] / 255.0, 0.0, 1.0)
	elif value is PackedInt32Array:
		var packed_i: PackedInt32Array = value
		if index < packed_i.size():
			return clampf(float(packed_i[index]) / 255.0, 0.0, 1.0)
	elif value is PackedByteArray:
		var packed_b: PackedByteArray = value
		if index < packed_b.size():
			return clampf(float(packed_b[index]) / 255.0, 0.0, 1.0)
	elif value is Array:
		var items: Array = value
		if index < items.size():
			return clampf(_as_float(items[index]) / 255.0, 0.0, 1.0)
	return fallback
