class_name PuppetFace
extends Node2D

## 挂在 head Bone2D 下的程序化面部。
## 纹理像素 p 映射到本节点局部坐标：(p - head_origin_px) * puppet_scale。
## head 的 rest.rotation 为 0，因此这里不再叠加旋转。
##
## 嘴：所有口型都是同一族“上唇线 + 下唇线”透镜的成员，X/A 是开口为 0 的特例，
## 所以口型之间可以按参数补间。set_time() 提供时钟时才补间，否则立即到位。
##
## 原画的嘴线和眉毛用从贴图里抠出来的“擦除片”盖掉：擦除片的颜色就是原图，
## 只有线稿像素被换成肤色，因此不会把肤色糊到刘海、睫毛或下颌线上，缩小后
## 也不留残影。拿不到贴图时退回羽化肤色片。

# 日系口腔色：暖暗玫瑰红、深处、下舌暖粉；齿光是低对比暖米白，不是纯白。
const CAVITY := Color(0.490, 0.220, 0.243, 1.0)
const CAVITY_DEEP := Color(0.361, 0.149, 0.169, 1.0)
const TONGUE := Color(0.722, 0.392, 0.416, 1.0)
const TOOTH_GLINT := Color(0.918, 0.867, 0.800, 1.0)
const LIP_TINT := Color(0.353, 0.184, 0.173, 1.0)

const GAZE_PX := 1.8
const PATCH_SEGS := 40
const MOUTH_SEGS := 20
const INSET_SEGS := 13
const BROW_SEGS := 18
# 口型补间时间常数：24fps 下换口型的第 1/2/3 帧分别走完约 60%/84%/94%。
const MOUTH_TAU := 0.045
const MAX_TWEEN_STEP := 0.25
# yaw = ±1 时五官绕脸部圆柱转过的角度。
const YAW_MAX_RAD := 0.14

# 口型参数下标。长度类参数以嘴的参考宽度为单位。
const P_HALF := 0   # 半宽
const P_UP := 1     # 上唇线抬起
const P_DN := 2     # 下唇线下落
const P_BEND := 3   # 嘴角连线的弯度，正值嘴角上扬
const P_ROUND := 4  # 0 = 两端收尖的透镜，1 = 圆唇
const P_TONGUE := 5
const P_GLINT := 6
const P_LINE := 7   # 上唇线线宽倍率
const P_COUNT := 8

const SHAPES := {
	"X": [0.300, 0.000, 0.000, 0.045, 0.00, 0.00, 0.0, 1.00],
	"A": [0.170, 0.000, 0.000, 0.000, 0.00, 0.00, 0.0, 1.12],
	"B": [0.285, 0.020, 0.090, 0.020, 0.10, 0.00, 0.0, 1.00],
	"C": [0.255, 0.034, 0.156, 0.016, 0.22, 0.45, 0.0, 1.00],
	"D": [0.320, 0.045, 0.245, 0.012, 0.30, 0.80, 1.0, 1.00],
	"E": [0.120, 0.050, 0.170, 0.000, 0.95, 0.35, 0.0, 1.00],
	"F": [0.105, 0.034, 0.066, 0.000, 1.00, 0.00, 0.0, 1.00],
}
# 闭嘴时各情绪的 [半宽, 弯度, 线宽倍率]。
const REST_SHAPES := {
	"neutral": [0.300, 0.045, 1.00],
	"happy": [0.335, 0.085, 1.00],
	"sad": [0.270, -0.050, 0.96],
	"angry": [0.225, -0.022, 1.14],
	"serious": [0.310, 0.000, 0.98],
	"surprised": [0.205, 0.010, 1.00],
}
# 张嘴时各情绪的 [宽度倍率, 开口倍率, 弯度增量, 圆度增量]。
const OPEN_MOODS := {
	"happy": [1.06, 1.00, 0.030, 0.00],
	"sad": [0.93, 0.90, -0.026, 0.00],
	"angry": [1.04, 0.96, -0.030, -0.05],
	"serious": [0.97, 0.92, -0.008, 0.00],
	"surprised": [0.93, 1.08, 0.000, 0.18],
}
# 情绪眉，以眉长为单位：[内端下移, 外端下移, 拱高, 向眉心平移, 整体下移, 拱顶位置(0 外 1 内)]。
const BROW_POSES := {
	"angry": [0.115, -0.060, 0.030, 0.030, 0.035, 0.50],
	"sad": [-0.105, 0.050, 0.045, 0.020, -0.020, 0.66],
	"surprised": [-0.010, 0.020, 0.105, 0.000, -0.125, 0.50],
	"happy": [-0.010, -0.030, 0.085, 0.000, -0.035, 0.50],
	"serious": [0.045, -0.010, 0.025, 0.000, 0.035, 0.50],
}

# 擦除片：线稿外扩 FILL 个贴图像素以内的晕边（锐化留下的亮圈、暗圈）换成肤色，
# SOLID 以内保持不透明，再向外分层羽化。
const ERASE_FILL_PX := 7
const ERASE_SOLID_PX := 9
const ERASE_RING_PX := 2
const MOUTH_RINGS: Array[float] = [0.72, 0.42, 0.18]
const BROW_RINGS: Array[float] = [0.65, 0.28]
const FAR := 1 << 20


## 贴图上一块矩形区域的逐像素分类。
class Scan:
	extends RefCounted

	var origin := Vector2i.ZERO
	var w := 0
	var h := 0
	var rgba := PackedByteArray()
	var opaque := PackedByteArray()
	var dark := PackedByteArray()  # 墨线或头发
	var off := PackedByteArray()   # 不是肤色：线、唇色、腮红
	var skin := Color.WHITE        # 这块区域里的肤色


var _ready_face := false
var _head_origin := Vector2.ZERO
var _puppet_scale := 1.0
var _skin := Color(0.910, 0.745, 0.627, 1.0)
var _ink := Color(0.110, 0.110, 0.125, 1.0)
var _lip_ink := Color(0.110, 0.110, 0.125, 1.0)
var _lip_soft := Color(0.110, 0.110, 0.125, 1.0)
var _mouth := Rect2()
var _eye_left := Rect2()
var _eye_right := Rect2()
var _brow_left := Rect2()
var _brow_right := Rect2()
var _brow_thick := 0.0
var _mouth_unit := 1.0
var _face_cx := 0.0
var _face_r := 1.0

var _has_image := false
var _mouth_cover := {}
var _brow_covers: Array[Dictionary] = []
var _brow_fronts: Array[Dictionary] = []

var _viseme := "X"
var _emotion := "neutral"
var _blink := 0.0
var _gaze := Vector2.ZERO
var _yaw := 0.0

var _goal := PackedFloat32Array()
var _now := PackedFloat32Array()
var _has_clock := false
var _clock := 0.0

var _feather := 1.0
var _drawn_scale := 1.0
var _warp_on := false
# 当前这一帧嘴的几何，供 _lip_top/_lip_low 取点。
var _m_center := Vector2.ZERO
var _m_half := 1.0
var _m_up := 0.0
var _m_dn := 0.0
var _m_bend := 0.0
var _m_round := 0.0


func _init() -> void:
	z_index = 10
	z_as_relative = true
	texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS


## texture 是角色贴图，可省略；给了才能精确擦掉原画的嘴线和眉毛。
func setup(
	anchors: Dictionary,
	head_origin_px: Vector2,
	puppet_scale: float,
	texture: Texture2D = null,
) -> void:
	_head_origin = head_origin_px
	_puppet_scale = absf(puppet_scale)
	if _puppet_scale < 0.00001:
		_puppet_scale = 0.00001
	_skin = _rgb(anchors.get("skin_rgb", null), Color(0.910, 0.745, 0.627, 1.0))
	_ink = _rgb(anchors.get("ink_rgb", null), Color(0.110, 0.110, 0.125, 1.0))
	var mouth_px := _box_px(anchors.get("mouth", null))
	var eye_left_px := _box_px(anchors.get("eye_left", null))
	var eye_right_px := _box_px(anchors.get("eye_right", null))
	var brow_left_px := _box_px(anchors.get("brow_left", null))
	var brow_right_px := _box_px(anchors.get("brow_right", null))
	var face_px := _box_px(anchors.get("face_box", null))
	_mouth = _to_local(mouth_px)
	_eye_left = _to_local(eye_left_px)
	_eye_right = _to_local(eye_right_px)
	_brow_left = _to_local(brow_left_px)
	_brow_right = _to_local(brow_right_px)
	_brow_thick = 0.0
	_measure_face(_to_local(face_px))
	_set_lip_ink(_ink.lerp(LIP_TINT, 0.30))

	_mouth_cover = {}
	_brow_covers.clear()
	_brow_fronts.clear()
	var image := _texture_image(texture)
	_has_image = image != null
	if image != null:
		_prepare_mouth_cover(image, mouth_px)
		_prepare_brows(image, eye_left_px, eye_right_px, brow_left_px, brow_right_px)

	_goal = _goal_for(_viseme, _emotion)
	_now = _goal.duplicate()
	_has_clock = false
	z_index = 10
	z_as_relative = true
	_ready_face = true
	queue_redraw()


func set_viseme(code: String) -> void:
	var next := _normalize_viseme(code)
	if next == _viseme:
		return
	_viseme = next
	_retarget()


func set_emotion(emotion: String) -> void:
	var next := _normalize_emotion(emotion)
	if next == _emotion:
		return
	_emotion = next
	_retarget()
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


## 2.5D 转头接口，-1（看向画面左）..+1（看向画面右）。嘴和眉朝看向的一侧微移，
## 远侧略微压窄；yaw == 0 时与正面逐像素一致。眼皮要盖住贴图上的眼睛，不随之移动。
func set_yaw(yaw: float) -> void:
	var next := clampf(yaw, -1.0, 1.0)
	if is_equal_approx(next, _yaw):
		return
	_yaw = next
	queue_redraw()


## 每帧在其它 set_* 之后调用一次（秒）。有了时钟，口型才在帧间补间。
func set_time(time_sec: float) -> void:
	var step := time_sec - _clock
	var continuous := _has_clock and step > 0.0 and step <= MAX_TWEEN_STEP
	_has_clock = true
	_clock = time_sec
	if continuous:
		_advance_mouth(step)
	else:
		snap_mouth()
	# 镜头推拉会改变一个屏幕像素对应的局部长度，抗锯齿边要跟着重算。
	if _ready_face and absf(_pixel_scale() - _drawn_scale) > _drawn_scale * 0.04:
		queue_redraw()


## 跳过补间，直接摆到当前口型。
func snap_mouth() -> void:
	if _now == _goal:
		return
	_now = _goal.duplicate()
	queue_redraw()


func _retarget() -> void:
	_goal = _goal_for(_viseme, _emotion)
	if not _has_clock:
		snap_mouth()


func _advance_mouth(step: float) -> void:
	if _now.size() != P_COUNT or _now == _goal:
		return
	var blend := 1.0 - exp(-step / MOUTH_TAU)
	var settled := true
	for i in P_COUNT:
		_now[i] = lerpf(_now[i], _goal[i], blend)
		if absf(_now[i] - _goal[i]) > 0.002:
			settled = false
	if settled:
		_now = _goal.duplicate()
	queue_redraw()


func _goal_for(viseme: String, emotion: String) -> PackedFloat32Array:
	var base: Array = SHAPES.get(viseme, SHAPES["X"])
	var shape := PackedFloat32Array(base)
	if viseme == "X":
		var rest: Array = REST_SHAPES.get(emotion, REST_SHAPES["neutral"])
		shape[P_HALF] = rest[0]
		shape[P_BEND] = rest[1]
		shape[P_LINE] = rest[2]
		return shape
	var mood: Array = OPEN_MOODS.get(emotion, [])
	if mood.is_empty():
		return shape
	if viseme == "A":
		shape[P_BEND] += float(mood[2]) * 0.5
		return shape
	# 圆唇口型靠圆度成立，情绪只轻微影响它。
	var sway := 1.0 - 0.7 * shape[P_ROUND]
	shape[P_HALF] *= lerpf(1.0, float(mood[0]), sway)
	shape[P_UP] *= float(mood[1])
	shape[P_DN] *= float(mood[1])
	shape[P_BEND] += float(mood[2]) * sway
	shape[P_ROUND] = clampf(shape[P_ROUND] + float(mood[3]), 0.0, 1.0)
	return shape


func _draw() -> void:
	if not _ready_face:
		return
	_drawn_scale = _pixel_scale()
	_feather = clampf(1.0 / _drawn_scale, 0.12, 3.0)
	_draw_mouth()
	if _emotion != "neutral":
		_draw_brow_covers()
		_warp_on = true
		_draw_brow(_brow_left, true)
		_draw_brow(_brow_right, false)
		_warp_on = false
		# 头发在眉毛前面：被刘海挡住的那一段眉不该透出来。
		for front: Dictionary in _brow_fronts:
			var front_rect: Rect2 = front["rect"]
			var front_tex: Texture2D = front["tex"]
			draw_texture_rect(front_tex, front_rect, false)
	# 眼皮最后画，闭眼时要盖住上面贴回来的睫毛。
	_draw_eye(_eye_left)
	_draw_eye(_eye_right)


# ---------------------------------------------------------------- 嘴

func _draw_mouth() -> void:
	if _mouth.size.x < 0.6 or _mouth.size.y < 0.6 or _now.size() != P_COUNT:
		return
	_draw_mouth_cover()
	var lw := _ink_width()
	_m_half = maxf(_now[P_HALF] * _mouth_unit, 0.8)
	_m_up = maxf(_now[P_UP], 0.0) * _mouth_unit
	_m_dn = maxf(_now[P_DN], 0.0) * _mouth_unit
	_m_bend = _now[P_BEND] * _mouth_unit
	_m_round = clampf(_now[P_ROUND], 0.0, 1.0)
	# 张嘴主要是下颌下落，上唇线只略微上提。
	_m_center = _mouth.get_center() + _shift() - Vector2(0.0, _m_dn * 0.18)
	var gap := _m_up + _m_dn
	var count := MOUTH_SEGS + 1
	var top := _vec(count)
	var low := _vec(count)
	for i in count:
		# 余弦分布让嘴角附近的点更密，圆唇的侧边才圆。
		var u := -cos(PI * float(i) / float(MOUTH_SEGS))
		top[i] = _lip_top(u)
		low[i] = _lip_low(u)

	_warp_on = true
	var open := smoothstep(lw * 0.30, lw * 1.10, gap)
	if open > 0.01:
		_band(top, low, _fade(CAVITY, open))
		if gap > lw * 2.0:
			var deep := _vec(count)
			for i in count:
				var u := -cos(PI * float(i) / float(MOUTH_SEGS))
				deep[i] = top[i].lerp(low[i], 0.50 * (1.0 - u * u * u * u))
			_band(top, deep, _fade(CAVITY_DEEP, open * smoothstep(lw * 2.0, lw * 3.2, gap)))
		var glint := clampf(_now[P_GLINT], 0.0, 1.0)
		if glint > 0.02 and gap > lw * 2.5:
			_draw_glint(lw, gap, glint, open)
		var tongue := clampf(_now[P_TONGUE], 0.0, 1.0)
		if tongue > 0.02 and gap > lw * 2.2:
			_draw_tongue(lw, gap, tongue, open)
		# 下唇线更轻、更细。
		var low_widths := _taper(count, lw * 0.62, lerpf(0.22, 0.80, _m_round))
		_stroke(low, low_widths, _fade(_lip_soft, open))
	var top_widths := _taper(count, lw * _now[P_LINE], lerpf(0.30, 0.85, _m_round))
	_stroke(top, top_widths, _lip_ink)
	_warp_on = false


func _lip_top(u: float) -> Vector2:
	var seam := _m_bend * (1.0 - u * u)
	var lift := pow(maxf(1.0 - u * u, 0.0), lerpf(1.0, 0.5, _m_round))
	return Vector2(_m_center.x + u * _m_half, _m_center.y + seam - _m_up * lift)


func _lip_low(u: float) -> Vector2:
	var seam := _m_bend * (1.0 - u * u)
	var body := maxf(1.0 - pow(absf(u), lerpf(2.3, 2.0, _m_round)), 0.0)
	var drop := pow(body, lerpf(0.85, 0.5, _m_round))
	return Vector2(_m_center.x + u * _m_half, _m_center.y + seam + _m_dn * drop)


## 只在大开口出现的上缘齿光：一条两端收细的薄带，没有牙缝。
func _draw_glint(lw: float, gap: float, amount: float, open: float) -> void:
	var thick := clampf(gap * 0.11, lw * 0.50, lw * 0.95) * amount
	var upper := _vec(INSET_SEGS)
	var lower := _vec(INSET_SEGS)
	for i in INSET_SEGS:
		var v := lerpf(-1.0, 1.0, float(i) / float(INSET_SEGS - 1))
		var edge := _lip_top(v * 0.60) + Vector2(0.0, lw * 0.42)
		upper[i] = edge
		lower[i] = edge + Vector2(0.0, thick * sqrt(maxf(1.0 - v * v, 0.0)))
	# 向口腔色靠一些，只是一点提示，不是一排白牙。
	_band(upper, lower, _fade(TOOTH_GLINT.lerp(CAVITY, 0.22), open * 0.80 * amount))


## 贴着下唇线的一小片舌面。
func _draw_tongue(lw: float, gap: float, amount: float, open: float) -> void:
	var lift := gap * 0.40 * amount
	var upper := _vec(INSET_SEGS)
	var lower := _vec(INSET_SEGS)
	for i in INSET_SEGS:
		var v := lerpf(-1.0, 1.0, float(i) / float(INSET_SEGS - 1))
		var edge := _lip_low(v * 0.70) - Vector2(0.0, lw * 0.28)
		lower[i] = edge
		upper[i] = edge - Vector2(0.0, lift * pow(maxf(1.0 - v * v, 0.0), 0.75))
	_band(upper, lower, _fade(TONGUE, open * 0.92 * clampf(amount * 1.4, 0.0, 1.0)))


func _draw_mouth_cover() -> void:
	if not _mouth_cover.is_empty():
		var cover_rect: Rect2 = _mouth_cover["rect"]
		var cover_tex: Texture2D = _mouth_cover["tex"]
		draw_texture_rect(cover_tex, cover_rect, false)
		return
	# 没有贴图：圆角矩形状的肤色片包住整个嘴矩形（含四角和多边形弦高），外加三层羽化。
	var radial := 1.0 / cos(PI / float(PATCH_SEGS))
	var contain := pow(2.0, 0.25) * radial * 1.02
	var rx := _mouth.size.x * 0.5 * contain
	var ry := _mouth.size.y * 0.5 * contain
	var stops: Array[Vector2] = [
		Vector2(1.0, 1.0), Vector2(1.07, 0.70), Vector2(1.14, 0.36), Vector2(1.22, 0.0),
	]
	_soft_blob(_mouth.get_center(), rx, ry, 4.0, stops)


# ---------------------------------------------------------------- 眼

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
	_stroke(lash, _taper(lash.size(), lash_w, 0.26), _ink)


func _lid_y(top: float, frontier: float, bow: float, t: float) -> float:
	var u := absf(t - 0.5) * 2.0
	return maxf(top + 0.35, frontier - bow * u * u)


# ---------------------------------------------------------------- 眉

func _draw_brow_covers() -> void:
	if _has_image:
		for cover: Dictionary in _brow_covers:
			var cover_rect: Rect2 = cover["rect"]
			var cover_tex: Texture2D = cover["tex"]
			draw_texture_rect(cover_tex, cover_rect, false)
		return
	# 没有贴图：高不透明肤色内层盖住原眉，外圈羽化。
	for rect: Rect2 in [_brow_left, _brow_right]:
		if rect.size.x < 1.0 or rect.size.y < 0.4:
			continue
		var rx := maxf(rect.size.x * 0.58, 1.6)
		var ry := maxf(rect.size.y * 1.30, rect.size.x * 0.09)
		var stops: Array[Vector2] = [
			Vector2(1.0, 0.96), Vector2(1.16, 0.65), Vector2(1.34, 0.28), Vector2(1.50, 0.0),
		]
		_soft_blob(rect.get_center(), rx, ry, 2.0, stops)


## 两端收尖的细眉弧线。
func _draw_brow(rect: Rect2, is_left: bool) -> void:
	if rect.size.x < 1.0 or rect.size.y < 0.4:
		return
	var pose: Array = BROW_POSES.get(_emotion, [])
	if pose.is_empty():
		return
	var bw := rect.size.x
	var inner_dy := float(pose[0]) * bw
	var outer_dy := float(pose[1]) * bw
	var arch := float(pose[2]) * bw
	var slide := Vector2(float(pose[3]) * bw, float(pose[4]) * bw)
	var peak := float(pose[5])
	if not is_left:
		slide.x = -slide.x
	var center := rect.get_center() + _shift() + slide
	var half_w := bw * 0.46
	var peak_span := maxf(peak, 1.0 - peak)
	var pts := _vec(BROW_SEGS)
	var widths := _floats(BROW_SEGS)
	var width := _brow_width(rect)
	for i in BROW_SEGS:
		var t := float(i) / float(BROW_SEGS - 1)
		# 图像左侧那条眉的内端在右。
		var inner_t := t if is_left else 1.0 - t
		var bell := 1.0 - clampf(absf(inner_t - peak) / peak_span, 0.0, 1.0)
		bell = bell * bell * (3.0 - 2.0 * bell)
		var y := lerpf(outer_dy, inner_dy, inner_t) - arch * bell
		pts[i] = center + Vector2(lerpf(-half_w, half_w, t), y)
		# 最粗处略偏眉头，两端柔和收尖。
		var body := sin(PI * pow(inner_t, 1.357))
		widths[i] = width * lerpf(0.20, 1.0, pow(maxf(body, 0.0), 0.75))
	_stroke(pts, widths, _ink)


func _brow_width(rect: Rect2) -> float:
	# 上限比“记号笔”时期的线宽细约 37%。
	var limit := maxf(1.30, minf(rect.size.y * 0.92, rect.size.x * 0.18)) * 0.63
	if _brow_thick <= 0.0:
		return limit
	# 量到了原画眉毛的粗细就跟它走，略细一点，但不超过上限。
	return clampf(_brow_thick * 0.90, 0.80, limit)


# ---------------------------------------------------------------- 贴图分析

func _texture_image(texture: Texture2D) -> Image:
	if texture == null:
		return null
	var image := texture.get_image()
	if image == null or image.is_empty():
		return null
	if image.is_compressed() and image.decompress() != OK:
		return null
	# 这是一份私有拷贝：只留第 0 层，擦掉的线稿会直接写回它。
	if image.has_mipmaps():
		image.clear_mipmaps()
	if image.get_format() != Image.FORMAT_RGBA8:
		image.convert(Image.FORMAT_RGBA8)
	return image


func _scan(image: Image, wanted: Rect2i) -> Scan:
	var region := wanted.intersection(Rect2i(Vector2i.ZERO, image.get_size()))
	if region.size.x < 8 or region.size.y < 6:
		return null
	var sub := image.get_region(region)
	if sub.has_mipmaps():
		sub.clear_mipmaps()
	if sub.get_format() != Image.FORMAT_RGBA8:
		sub.convert(Image.FORMAT_RGBA8)
	var scan := Scan.new()
	scan.origin = region.position
	scan.w = region.size.x
	scan.h = region.size.y
	scan.rgba = sub.get_data()
	var count := scan.w * scan.h
	if scan.rgba.size() != count * 4:
		return null
	scan.opaque = _bytes(count)
	scan.dark = _bytes(count)
	scan.off = _bytes(count)
	var skin := Vector3(_skin.r, _skin.g, _skin.b) * 255.0
	var skin_lum := 0.299 * skin.x + 0.587 * skin.y + 0.114 * skin.z
	# 赛璐珞上色的皮肤是一整块平色：区域内肤色取“接近锚点肤色的像素”里出现最多的那一档，
	# 线稿的晕边和抗锯齿过渡色都落不进这一档。
	var bins := {}
	var top_key := -1
	var top_count := 0
	for i in count:
		var at := i * 4
		if scan.rgba[at + 3] < 24:
			continue
		scan.opaque[i] = 1
		var rgb := Vector3(scan.rgba[at], scan.rgba[at + 1], scan.rgba[at + 2])
		var lum := 0.299 * rgb.x + 0.587 * rgb.y + 0.114 * rgb.z
		var apart := rgb.distance_squared_to(skin)
		if lum < skin_lum * 0.50 or (apart > 8100.0 and lum < skin_lum * 0.66):
			scan.dark[i] = 1
		if apart > 900.0:
			scan.off[i] = 1
		if apart < 3600.0:
			var key := (
				((scan.rgba[at] >> 2) << 12) | ((scan.rgba[at + 1] >> 2) << 6) | (scan.rgba[at + 2] >> 2)
			)
			var seen: int = bins.get(key, 0) + 1
			bins[key] = seen
			if seen > top_count:
				top_count = seen
				top_key = key
	var tone := skin
	if top_count >= 12:
		var mode := Vector3i((top_key >> 12) & 63, (top_key >> 6) & 63, top_key & 63)
		var tone_sum := Vector3.ZERO
		var tone_count := 0
		for i in count:
			if scan.opaque[i] == 0:
				continue
			var at := i * 4
			if (
				absi((scan.rgba[at] >> 2) - mode.x) <= 1
				and absi((scan.rgba[at + 1] >> 2) - mode.y) <= 1
				and absi((scan.rgba[at + 2] >> 2) - mode.z) <= 1
			):
				tone_sum += Vector3(scan.rgba[at], scan.rgba[at + 1], scan.rgba[at + 2])
				tone_count += 1
		tone = tone_sum / float(tone_count)
	scan.skin = Color(tone.x / 255.0, tone.y / 255.0, tone.z / 255.0, 1.0)
	return scan


## 每个像素到最近的 mask == target 像素的棋盘距离；区域外不算。
func _cheb_to(mask: PackedByteArray, w: int, h: int, target: int) -> PackedInt32Array:
	var dist := _ints(w * h)
	for y in h:
		var row := y * w
		for x in w:
			var i := row + x
			if mask[i] == target:
				dist[i] = 0
				continue
			var best := FAR
			if x > 0:
				best = mini(best, dist[i - 1])
			if y > 0:
				best = mini(best, dist[i - w])
				if x > 0:
					best = mini(best, dist[i - w - 1])
				if x < w - 1:
					best = mini(best, dist[i - w + 1])
			dist[i] = mini(best + 1, FAR)
	for y in range(h - 1, -1, -1):
		var row := y * w
		for x in range(w - 1, -1, -1):
			var i := row + x
			var best := dist[i]
			if best == 0:
				continue
			if x < w - 1:
				best = mini(best, dist[i + 1] + 1)
			if y < h - 1:
				best = mini(best, dist[i + w] + 1)
				if x < w - 1:
					best = mini(best, dist[i + w + 1] + 1)
				if x > 0:
					best = mini(best, dist[i + w - 1] + 1)
			dist[i] = best
	return dist


## 8 邻接连通域，每个连通域是一串像素下标。
func _components(mask: PackedByteArray, w: int, h: int) -> Array[PackedInt32Array]:
	var seen := _bytes(w * h)
	var found: Array[PackedInt32Array] = []
	var stack := PackedInt32Array()
	for start in w * h:
		if mask[start] == 0 or seen[start] == 1:
			continue
		var pixels := PackedInt32Array()
		seen[start] = 1
		stack.push_back(start)
		while not stack.is_empty():
			var last := stack.size() - 1
			var at := stack[last]
			stack.remove_at(last)
			pixels.push_back(at)
			var x := at % w
			@warning_ignore("integer_division")
			var y := at / w
			for ny in range(maxi(y - 1, 0), mini(y + 2, h)):
				for nx in range(maxi(x - 1, 0), mini(x + 2, w)):
					var next := ny * w + nx
					if mask[next] == 1 and seen[next] == 0:
						seen[next] = 1
						stack.push_back(next)
		found.append(pixels)
	return found


## 连通域的 [min_x, min_y, max_x, max_y]。
func _bounds(pixels: PackedInt32Array, w: int) -> Rect2i:
	var x0 := FAR
	var y0 := FAR
	var x1 := -1
	var y1 := -1
	for at in pixels:
		var x := at % w
		@warning_ignore("integer_division")
		var y := at / w
		x0 = mini(x0, x)
		x1 = maxi(x1, x)
		y0 = mini(y0, y)
		y1 = maxi(y1, y)
	return Rect2i(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


## 墨色像素里去掉成片的头发，只留细笔画。
func _thin_strokes(scan: Scan, radius: int) -> PackedByteArray:
	var count := scan.w * scan.h
	var depth := _cheb_to(scan.dark, scan.w, scan.h, 0)
	var core := _bytes(count)
	var any_core := false
	for i in count:
		if scan.dark[i] == 1 and depth[i] > radius:
			core[i] = 1
			any_core = true
	var thin := scan.dark.duplicate()
	if not any_core:
		return thin
	var reach := _cheb_to(core, scan.w, scan.h, 1)
	for i in count:
		if reach[i] <= radius + 1:
			thin[i] = 0
	return thin


## 在眼睛上方找原画的眉毛笔画。找不到返回空数组。
func _pick_brow(scan: Scan, thin: PackedByteArray, eye: Rect2) -> PackedByteArray:
	var w := scan.w
	var h := scan.h
	var ew := eye.size.x
	# 眉毛近乎水平：只留横向连续段够长、且横向比纵向长的像素，
	# 斜着垂下来的发丝和竖着的发绺就被滤掉了。
	var across := _run_lengths(thin, w, h, true)
	var down := _run_lengths(thin, w, h, false)
	var run_min := maxi(6, roundi(0.22 * ew))
	var flat := _bytes(w * h)
	for i in w * h:
		if thin[i] == 1 and across[i] >= run_min and across[i] >= down[i]:
			flat[i] = 1
	var comps := _components(flat, w, h)
	var boxes: Array[Rect2i] = []
	var eye_top := eye.position.y + 0.10 * eye.size.y - float(scan.origin.y)
	var best := -1
	var best_area := 0
	for index in comps.size():
		var box := _bounds(comps[index], w)
		boxes.append(box)
		var area := comps[index].size()
		var thickness := float(area) / float(box.size.x)
		var touches_eye := box.end.y >= h or float(box.end.y - 1) > eye_top
		if (
			not touches_eye
			and float(box.size.x) >= 0.40 * ew
			and float(box.size.x) >= 2.2 * float(box.size.y)
			and thickness >= maxf(3.0, 0.06 * ew)
			and thickness <= 0.20 * ew
			and area > best_area
		):
			best = index
			best_area = area
	if best < 0:
		return PackedByteArray()

	# 眉毛和一绺头发交叠的地方纵向更长，那里留给头发，不擦。
	# 眉身卡得紧，向外生长时放宽到眉毛自身粗细的两倍多（眉梢会变陡）。
	var spans: Array[int] = []
	for at in comps[best]:
		spans.append(down[at])
	spans.sort()
	@warning_ignore("integer_division")
	var typical := float(spans[spans.size() / 2])
	var body_tall := roundi(typical * 1.6 + 1.0)
	var tall := roundi(maxf(typical * 2.2, typical + 4.0))

	var picked := _bytes(w * h)
	var frontier := PackedInt32Array()
	var best_box := boxes[best]
	var best_mid := float(best_box.position.y) + float(best_box.size.y) * 0.5
	var best_thick := float(best_area) / float(best_box.size.x)
	for index in comps.size():
		var box := boxes[index]
		var area := comps[index].size()
		var keep := index == best
		if not keep and area >= 25 and box.end.y < h:
			# 被头发隔断的同一条眉：高度对齐、粗细相近。
			var mid := float(box.position.y) + float(box.size.y) * 0.5
			var thickness := float(area) / float(box.size.x)
			keep = (
				absf(mid - best_mid) <= 0.6 * maxf(float(best_box.size.y), 8.0)
				and thickness >= 0.6 * best_thick
			)
		if keep:
			for at in comps[index]:
				if down[at] <= body_tall:
					picked[at] = 1
					frontier.push_back(at)
	# 沿着细笔画往外长，把眉头眉尾上翘、下弯的那一小段也收进来。
	# 长到头就自然停下的是眉梢；长到上限还没停的是连着的发丝，整段退回。
	var reach := maxi(3, roundi(0.20 * ew))
	var grown := _bytes(w * h)
	var deepest := _ints(w * h)
	for step in reach:
		var wave := PackedInt32Array()
		for at in frontier:
			var x := at % w
			@warning_ignore("integer_division")
			var y := at / w
			for ny in range(maxi(y - 1, 0), mini(y + 2, h)):
				for nx in range(maxi(x - 1, 0), mini(x + 2, w)):
					var next := ny * w + nx
					if (
						thin[next] == 1 and picked[next] == 0
						and grown[next] == 0 and down[next] <= tall
					):
						grown[next] = 1
						deepest[next] = step + 1
						wave.push_back(next)
		if wave.is_empty():
			break
		frontier = wave
	for branch in _components(grown, w, h):
		var ended := true
		for at in branch:
			if deepest[at] >= reach:
				ended = false
				break
		if ended:
			for at in branch:
				picked[at] = 1
	return picked


## 每个 mask 像素所在的横向（或纵向）连续段的长度。
func _run_lengths(mask: PackedByteArray, w: int, h: int, horizontal: bool) -> PackedInt32Array:
	var lengths := _ints(w * h)
	var lines := h if horizontal else w
	var span := w if horizontal else h
	var stride := 1 if horizontal else w
	for line in lines:
		var first := line * w if horizontal else line
		var at := 0
		while at < span:
			if mask[first + at * stride] == 0:
				at += 1
				continue
			var from := at
			while at < span and mask[first + at * stride] == 1:
				at += 1
			for filled in range(from, at):
				lengths[first + filled * stride] = at - from
	return lengths


## 一条眉的检测结果：{rect, thick, tex, tex_rect, area}，尺寸都是贴图像素。
func _detect_brow(image: Image, eye: Rect2, eye_h: float) -> Dictionary:
	if eye.size.x < 6.0 or eye.size.y < 3.0:
		return {}
	var ew := eye.size.x
	var x0 := floori(eye.position.x - 0.35 * ew)
	var x1 := ceili(eye.end.x + 0.35 * ew)
	var y0 := floori(eye.position.y - 1.30 * eye_h)
	var y1 := floori(eye.position.y + 0.12 * eye.size.y)
	var scan := _scan(image, Rect2i(x0, y0, x1 - x0, y1 - y0))
	if scan == null:
		return {}
	var thin := _thin_strokes(scan, maxi(4, roundi(0.115 * ew)))
	var picked := _pick_brow(scan, thin, eye)
	if picked.is_empty():
		return {}
	return _brow_result(image, scan, picked)


## 没找到眉毛笔画时退回锚点框：擦掉框里的细笔画。
func _anchor_brow(image: Image, anchor: Rect2, eye_w: float) -> Dictionary:
	if anchor.size.x < 2.0 or anchor.size.y < 1.0:
		return {}
	var pad := Vector2(0.08 * anchor.size.x, maxf(2.0, 0.6 * anchor.size.y))
	var inner := anchor.grow_individual(pad.x, pad.y, pad.x, pad.y)
	var margin := float(ERASE_SOLID_PX + ERASE_RING_PX * BROW_RINGS.size() + 2)
	var outer := inner.grow(margin)
	var zone := Rect2i(
		floori(outer.position.x), floori(outer.position.y),
		ceili(outer.size.x), ceili(outer.size.y)
	)
	var scan := _scan(image, zone)
	if scan == null:
		return {}
	var thin := _thin_strokes(scan, maxi(4, roundi(0.115 * maxf(eye_w, anchor.size.x * 0.8))))
	var picked := _bytes(scan.w * scan.h)
	var hits := 0
	for y in scan.h:
		var py := float(scan.origin.y + y) + 0.5
		if py < inner.position.y or py > inner.end.y:
			continue
		for x in scan.w:
			var px := float(scan.origin.x + x) + 0.5
			if px < inner.position.x or px > inner.end.x:
				continue
			if thin[y * scan.w + x] == 1:
				picked[y * scan.w + x] = 1
				hits += 1
	if hits < 6:
		return {}
	var result := _brow_result(image, scan, picked)
	result["rect"] = anchor
	result["thick"] = 0.0
	return result


func _brow_result(image: Image, scan: Scan, picked: PackedByteArray) -> Dictionary:
	var w := scan.w
	var h := scan.h
	var columns := _ints(w)
	var x0 := w
	var x1 := -1
	var y0 := h
	var y1 := -1
	var area := 0
	for y in h:
		for x in w:
			if picked[y * w + x] == 0:
				continue
			columns[x] += 1
			area += 1
			x0 = mini(x0, x)
			x1 = maxi(x1, x)
			y0 = mini(y0, y)
			y1 = maxi(y1, y)
	if area == 0:
		return {}
	var heights: Array[int] = []
	for x in w:
		if columns[x] > 0:
			heights.append(columns[x])
	heights.sort()
	@warning_ignore("integer_division")
	var thick := float(heights[heights.size() / 2])
	var protect := _bytes(w * h)
	for i in w * h:
		if scan.dark[i] == 1 and picked[i] == 0:
			protect[i] = 1
	return {
		"rect": Rect2(
			Vector2(scan.origin.x + x0, scan.origin.y + y0),
			Vector2(x1 - x0 + 1, y1 - y0 + 1)
		),
		"thick": thick,
		"area": area,
		"tex": _erase_texture(image, scan, picked, protect, BROW_RINGS),
		"tex_rect": Rect2(Vector2(scan.origin), Vector2(w, h)),
	}


## 擦除片：颜色是原图，picked 及其晕边换成肤色；透明度从线稿向外分层衰减。
## protect 是要留下的其它线稿（头发、睫毛、下颌线），贴着它们的像素不改色。
## 擦掉的像素同时写回 image，之后再扫描这块区域就看不到这条线了。
func _erase_texture(
	image: Image,
	scan: Scan,
	picked: PackedByteArray,
	protect: PackedByteArray,
	rings: Array[float],
) -> ImageTexture:
	var w := scan.w
	var h := scan.h
	var near := _cheb_to(picked, w, h, 1)
	var guard := _cheb_to(protect, w, h, 1)
	var data := scan.rgba.duplicate()
	var skin_r := roundi(scan.skin.r * 255.0)
	var skin_g := roundi(scan.skin.g * 255.0)
	var skin_b := roundi(scan.skin.b * 255.0)
	var skin_fill := Color8(skin_r, skin_g, skin_b, 255)
	var skin_rgb := Vector3(skin_r, skin_g, skin_b)
	var skin_lum := 0.299 * skin_rgb.x + 0.587 * skin_rgb.y + 0.114 * skin_rgb.z
	for i in w * h:
		var at := i * 4
		if scan.opaque[i] == 0:
			data[at + 3] = 0
			continue
		var gap := near[i]
		var fill := gap <= 1
		if not fill and gap <= ERASE_FILL_PX:
			# 再往外只改线稿的晕边（接近肤色或比肤色亮），不碰旁边的高光、阴影色块。
			var rgb := Vector3(data[at], data[at + 1], data[at + 2])
			fill = (
				rgb.distance_squared_to(skin_rgb) < 3600.0
				or 0.299 * rgb.x + 0.587 * rgb.y + 0.114 * rgb.z > skin_lum
			)
		if fill and guard[i] > 1:
			data[at] = skin_r
			data[at + 1] = skin_g
			data[at + 2] = skin_b
			@warning_ignore("integer_division")
			image.set_pixel(scan.origin.x + i % w, scan.origin.y + i / w, skin_fill)
		var alpha := 0.0
		if gap <= ERASE_SOLID_PX:
			alpha = 1.0
		else:
			@warning_ignore("integer_division")
			var ring := (gap - ERASE_SOLID_PX - 1) / ERASE_RING_PX
			if ring < rings.size():
				alpha = rings[ring]
		data[at + 3] = roundi(alpha * 255.0)
	var cover := Image.create_from_data(w, h, false, Image.FORMAT_RGBA8, data)
	# 和角色贴图一样带 mipmap，缩小时两者的取样才一致。
	if cover.generate_mipmaps() != OK:
		push_warning("PuppetFace: mipmaps unavailable for a face cover")
	return ImageTexture.create_from_image(cover)


func _prepare_mouth_cover(image: Image, mouth: Rect2) -> void:
	if mouth.size.x < 2.0 or mouth.size.y < 2.0:
		return
	var reach := float(ERASE_SOLID_PX + ERASE_RING_PX * MOUTH_RINGS.size() + 2)
	var pad := Vector2(0.18 * mouth.size.x, 0.45 * mouth.size.y)
	var outer := mouth.grow_individual(pad.x + reach, pad.y + reach, pad.x + reach, pad.y + reach)
	var zone := Rect2i(
		floori(outer.position.x), floori(outer.position.y),
		ceili(outer.size.x), ceili(outer.size.y)
	)
	var scan := _scan(image, zone)
	if scan == null:
		return
	var w := scan.w
	var h := scan.h
	var off_count := 0
	for i in w * h:
		off_count += scan.off[i]
	# 大半个区域都不像肤色，说明肤色锚点不可信，交给肤色片兜底。
	if off_count * 10 > w * h * 6:
		return
	var picked := _bytes(w * h)
	var hits := 0
	var ink_sum := Vector3.ZERO
	var ink_count := 0
	var skin_lum := (0.299 * scan.skin.r + 0.587 * scan.skin.g + 0.114 * scan.skin.b) * 255.0
	for pixels in _components(scan.off, w, h):
		var inside := 0
		var on_border := false
		for at in pixels:
			var x := at % w
			@warning_ignore("integer_division")
			var y := at / w
			if x == 0 or y == 0 or x == w - 1 or y == h - 1:
				on_border = true
			if mouth.has_point(Vector2(scan.origin.x + x, scan.origin.y + y) + Vector2(0.5, 0.5)):
				inside += 1
		# 穿出区域的线（下颌、鼻影）不是嘴，除非它主体就在嘴矩形里。
		if inside == 0 or (on_border and inside * 10 < pixels.size() * 6):
			continue
		for at in pixels:
			picked[at] = 1
			hits += 1
			var rgb := Vector3(scan.rgba[at * 4], scan.rgba[at * 4 + 1], scan.rgba[at * 4 + 2])
			if 0.299 * rgb.x + 0.587 * rgb.y + 0.114 * rgb.z < skin_lum * 0.78:
				ink_sum += rgb
				ink_count += 1
	if hits == 0:
		return
	var protect := _bytes(w * h)
	for i in w * h:
		if scan.off[i] == 1 and picked[i] == 0:
			protect[i] = 1
	_mouth_cover = {
		"tex": _erase_texture(image, scan, picked, protect, MOUTH_RINGS),
		"rect": _to_local(Rect2(Vector2(scan.origin), Vector2(w, h))),
	}
	if ink_count >= 8:
		# 唇线沿用原画嘴线的色相，只压深一些。
		var art := ink_sum / float(ink_count) / 255.0
		_set_lip_ink(Color(art.x, art.y, art.z, 1.0).lerp(_ink, 0.50))


func _prepare_brows(
	image: Image,
	eye_left: Rect2,
	eye_right: Rect2,
	anchor_left: Rect2,
	anchor_right: Rect2,
) -> void:
	var eye_h := maxf(eye_left.size.y, eye_right.size.y)
	var left := _detect_brow(image, eye_left, eye_h)
	var right := _detect_brow(image, eye_right, eye_h)
	if left.is_empty() and right.is_empty():
		left = _anchor_brow(image, anchor_left, eye_left.size.x)
		right = _anchor_brow(image, anchor_right, eye_right.size.x)
	else:
		var left_rect: Rect2 = left.get("rect", Rect2())
		var right_rect: Rect2 = right.get("rect", Rect2())
		# 线宽跟露出得更完整的那条眉走。
		var fuller := left if left_rect.size.x > right_rect.size.x else right
		_brow_thick = float(fuller.get("thick", 0.0)) * _puppet_scale
		# 一侧只从刘海下露出一截时，按另一侧相对眼睛的位置镜像过来，由头发去挡。
		if not left.is_empty() and left_rect.size.x < 0.75 * right_rect.size.x:
			left_rect = _mirror_brow(right_rect, eye_right, eye_left)
		elif not right.is_empty() and right_rect.size.x < 0.75 * left_rect.size.x:
			right_rect = _mirror_brow(left_rect, eye_left, eye_right)
		# 原画里完全看不到眉毛的那一侧（整条在刘海后面）不画新眉。
		_brow_left = _to_local(left_rect)
		_brow_right = _to_local(right_rect)
	for found: Dictionary in [left, right]:
		if found.is_empty():
			continue
		var tex_rect: Rect2 = found["tex_rect"]
		_brow_covers.append({"tex": found["tex"], "rect": _to_local(tex_rect)})
	# 原眉已经从 image 里擦掉了，剩下的深色就是该挡在新眉前面的头发和睫毛。
	for brow: Rect2 in [_brow_left, _brow_right]:
		var front := _front_cover(image, brow)
		if not front.is_empty():
			_brow_fronts.append(front)


## 新眉活动范围内的头发：颜色和透明度都取自原图，画在新眉之上。
## brow 是局部坐标里的眉矩形。
func _front_cover(image: Image, brow: Rect2) -> Dictionary:
	if brow.size.x < 1.0 or brow.size.y < 0.4:
		return {}
	var origin := brow.position / _puppet_scale + _head_origin
	var wh := brow.size / _puppet_scale
	var pad := Vector2(0.12 * wh.x + 2.0, 0.30 * wh.x + 2.0)
	var zone := Rect2i(
		floori(origin.x - pad.x), floori(origin.y - pad.y),
		ceili(wh.x + pad.x * 2.0), ceili(wh.y + pad.y * 2.0)
	)
	var scan := _scan(image, zone)
	if scan == null:
		return {}
	var w := scan.w
	var h := scan.h
	# 高光是偏冷的灰白，皮肤和它的阴影是暖色；肤色本身就偏冷时只认深色。
	var warmth := (scan.skin.r - scan.skin.b) * 255.0
	var cool_limit := warmth * 0.45 if warmth >= 24.0 else -1000.0
	var front := _bytes(w * h)
	var hits := 0
	for i in w * h:
		if scan.opaque[i] == 0:
			continue
		var at := i * 4
		if scan.dark[i] == 1 or float(scan.rgba[at] - scan.rgba[at + 2]) < cool_limit:
			front[i] = 1
			hits += 1
	if hits == 0:
		return {}
	var near := _cheb_to(front, w, h, 1)
	var data := scan.rgba.duplicate()
	for i in w * h:
		var alpha := 0
		if scan.opaque[i] == 1:
			if near[i] == 0:
				alpha = 255
			elif near[i] == 1:
				alpha = 128
		data[i * 4 + 3] = alpha
	var cover := Image.create_from_data(w, h, false, Image.FORMAT_RGBA8, data)
	if cover.generate_mipmaps() != OK:
		push_warning("PuppetFace: mipmaps unavailable for a face cover")
	return {
		"tex": ImageTexture.create_from_image(cover),
		"rect": _to_local(Rect2(Vector2(scan.origin), Vector2(w, h))),
	}


func _mirror_brow(brow: Rect2, from_eye: Rect2, to_eye: Rect2) -> Rect2:
	var offset := brow.get_center() - from_eye.get_center()
	var center := to_eye.get_center() + Vector2(-offset.x, offset.y)
	return Rect2(center - brow.size * 0.5, brow.size)


# ---------------------------------------------------------------- 状态与度量

func _normalize_viseme(code: String) -> String:
	var key := code.strip_edges().to_upper()
	match key:
		"A", "B", "C", "D", "E", "F":
			return key
		"G":  # Rhubarb 的扩展口型，按它自己的回退规则落到基本口型。
			return "B"
		"H":
			return "C"
		_:
			return "X"


func _normalize_emotion(emotion: String) -> String:
	var key := emotion.strip_edges().to_lower()
	match key:
		"neutral", "happy", "angry", "sad", "surprised", "serious":
			return key
		_:
			return "neutral"


func _measure_face(face: Rect2) -> void:
	# 嘴的参考宽度：检测框偏宽或偏窄时按脸宽收拢，不同角色的口型比例才一致。
	_mouth_unit = maxf(_mouth.size.x, 1.0)
	if face.size.x > 1.0:
		_mouth_unit = clampf(_mouth_unit, face.size.x * 0.27, face.size.x * 0.34)
		_face_cx = face.get_center().x
		_face_r = face.size.x * 0.54
	elif _eye_left.size.x > 0.0 and _eye_right.size.x > 0.0:
		var left := _eye_left.get_center()
		var right := _eye_right.get_center()
		_face_cx = (left.x + right.x) * 0.5
		_face_r = absf(right.x - left.x) * 1.25
	else:
		_face_cx = _mouth.get_center().x
		_face_r = _mouth.size.x * 1.6
	_face_r = maxf(_face_r, 1.0)


func _set_lip_ink(color: Color) -> void:
	_lip_ink = Color(color.r, color.g, color.b, 1.0)
	# 太浅的原画嘴线压不住口腔色，再向墨色靠一些。
	if _lip_ink.get_luminance() > 0.34:
		_lip_ink = _lip_ink.lerp(_ink, 0.35)
	_lip_soft = _lip_ink.lerp(CAVITY, 0.45)
	_lip_soft.a = 0.90


func _shift() -> Vector2:
	return _gaze * GAZE_PX


## 线宽随嘴宽缩放，落在原画线稿的粗细范围内。
func _ink_width() -> float:
	return clampf(_mouth_unit * 0.062, 0.85, 1.35)


## 一个屏幕像素对应多少倍的局部长度（含镜头缩放）。
func _pixel_scale() -> float:
	if not is_inside_tree():
		return 1.0
	var xf := get_global_transform_with_canvas()
	return maxf((xf.x.length() + xf.y.length()) * 0.5, 0.05)


## 把点绕脸部圆柱转过 yaw 角：中间的点移动最多，看向的那一侧被压窄。
func _warp(point: Vector2) -> Vector2:
	if _yaw == 0.0:
		return point
	var side := clampf((point.x - _face_cx) / _face_r, -1.0, 1.0)
	var turned := clampf(asin(side) + _yaw * YAW_MAX_RAD, -PI * 0.5, PI * 0.5)
	return Vector2(point.x + (sin(turned) - side) * _face_r, point.y)


func _fade(color: Color, amount: float) -> Color:
	return Color(color.r, color.g, color.b, color.a * clampf(amount, 0.0, 1.0))


func _taper(count: int, width: float, end_ratio: float) -> PackedFloat32Array:
	var safe := maxi(count, 0)
	var widths := _floats(safe)
	var denom := float(maxi(safe - 1, 1))
	for i in safe:
		var bell := pow(sin((float(i) / denom) * PI), 0.6)
		widths[i] = width * lerpf(end_ratio, 1.0, bell)
	return widths


# ---------------------------------------------------------------- 绘制基元

## 带羽化边的三角形。所有自绘的嘴和眉都走这里，yaw 也在这里生效。
func _tris(points: PackedVector2Array, colors: PackedColorArray, indices: PackedInt32Array) -> void:
	if indices.size() < 3 or points.size() != colors.size():
		return
	if _warp_on and _yaw != 0.0:
		for i in points.size():
			points[i] = _warp(points[i])
	RenderingServer.canvas_item_add_triangle_array(get_canvas_item(), indices, points, colors)


## 变宽折线，两侧各有一条羽化边，两端收成软尖。
func _stroke(pts: PackedVector2Array, widths: PackedFloat32Array, color: Color) -> void:
	var count := pts.size()
	if count < 2 or widths.size() != count or color.a <= 0.003:
		return
	var clear := Color(color.r, color.g, color.b, 0.0)
	var points := PackedVector2Array()
	var colors := PackedColorArray()
	var indices := PackedInt32Array()
	var first_dir := Vector2.RIGHT
	var last_dir := Vector2.RIGHT
	var first_rim := 0.0
	var last_rim := 0.0
	for i in count:
		var dir := pts[mini(i + 1, count - 1)] - pts[maxi(i - 1, 0)]
		dir = dir.normalized() if dir.length_squared() > 0.0000001 else Vector2.RIGHT
		var nrm := Vector2(-dir.y, dir.x)
		var half := maxf(widths[i], 0.0) * 0.5
		var core := maxf(half - _feather * 0.5, 0.0)
		var rim := half + _feather * 0.5
		# 比一个像素还细的线靠降低不透明度表现。
		var solid := Color(color.r, color.g, color.b, color.a * clampf(half * 2.0 / _feather, 0.0, 1.0))
		points.append(pts[i] + nrm * rim)
		points.append(pts[i] + nrm * core)
		points.append(pts[i] - nrm * core)
		points.append(pts[i] - nrm * rim)
		colors.append(clear)
		colors.append(solid)
		colors.append(solid)
		colors.append(clear)
		if i == 0:
			first_dir = dir
			first_rim = rim
		if i == count - 1:
			last_dir = dir
			last_rim = rim
	for i in count - 1:
		var base := i * 4
		for lane in 3:
			var a := base + lane
			indices.append_array(PackedInt32Array([a, a + 1, a + 5, a, a + 5, a + 4]))
	var head_tip := points.size()
	points.append(pts[0] - first_dir * first_rim)
	colors.append(clear)
	var tail_tip := points.size()
	points.append(pts[count - 1] + last_dir * last_rim)
	colors.append(clear)
	var tail := (count - 1) * 4
	for lane in 3:
		indices.append_array(PackedInt32Array([lane, lane + 1, head_tip]))
		indices.append_array(PackedInt32Array([tail + lane, tail + lane + 1, tail_tip]))
	_tris(points, colors, indices)


## 两条等长折线之间的填充带，上下外缘羽化。任何弯度都不需要多边形三角剖分。
func _band(upper: PackedVector2Array, lower: PackedVector2Array, color: Color) -> void:
	var count := upper.size()
	if count < 2 or lower.size() != count or color.a <= 0.003:
		return
	var clear := Color(color.r, color.g, color.b, 0.0)
	var rim := _feather * 0.75
	# 外法线的朝向按中点定一次：upper 的外侧背离 lower。
	@warning_ignore("integer_division")
	var mid := count / 2
	var mid_dir := upper[mini(mid + 1, count - 1)] - upper[maxi(mid - 1, 0)]
	var side := 1.0
	if Vector2(-mid_dir.y, mid_dir.x).dot(upper[mid] - lower[mid]) < 0.0:
		side = -1.0
	var points := PackedVector2Array()
	var colors := PackedColorArray()
	var indices := PackedInt32Array()
	for i in count:
		var up_dir := upper[mini(i + 1, count - 1)] - upper[maxi(i - 1, 0)]
		var low_dir := lower[mini(i + 1, count - 1)] - lower[maxi(i - 1, 0)]
		up_dir = up_dir.normalized() if up_dir.length_squared() > 0.0000001 else Vector2.RIGHT
		low_dir = low_dir.normalized() if low_dir.length_squared() > 0.0000001 else Vector2.RIGHT
		points.append(upper[i] + Vector2(-up_dir.y, up_dir.x) * side * rim)
		points.append(upper[i])
		points.append(lower[i])
		points.append(lower[i] - Vector2(-low_dir.y, low_dir.x) * side * rim)
		colors.append(clear)
		colors.append(color)
		colors.append(color)
		colors.append(clear)
	for i in count - 1:
		var base := i * 4
		for lane in 3:
			var a := base + lane
			indices.append_array(PackedInt32Array([a, a + 1, a + 5, a, a + 5, a + 4]))
	_tris(points, colors, indices)


## 径向渐变的肤色片。power = 2 是椭圆，越大越接近圆角矩形；
## stops 是由内到外的 (半径倍率, 不透明度)，第一档以内不透明度不变。
func _soft_blob(center: Vector2, rx: float, ry: float, power: float, stops: Array[Vector2]) -> void:
	if rx < 0.2 or ry < 0.2 or stops.is_empty():
		return
	var points := PackedVector2Array()
	var colors := PackedColorArray()
	var indices := PackedInt32Array()
	points.append(center)
	colors.append(_fade(_skin, stops[0].y))
	var bend := 2.0 / maxf(power, 1.0)
	for ring in stops.size():
		var tone := _fade(_skin, stops[ring].y)
		for i in PATCH_SEGS:
			var angle := TAU * float(i) / float(PATCH_SEGS)
			var c := cos(angle)
			var s := sin(angle)
			var unit := Vector2(signf(c) * pow(absf(c), bend), signf(s) * pow(absf(s), bend))
			points.append(center + Vector2(unit.x * rx, unit.y * ry) * stops[ring].x)
			colors.append(tone)
	for i in PATCH_SEGS:
		var next := (i + 1) % PATCH_SEGS
		indices.append_array(PackedInt32Array([0, 1 + i, 1 + next]))
		for ring in stops.size() - 1:
			var inner := 1 + ring * PATCH_SEGS
			var outer := inner + PATCH_SEGS
			indices.append_array(PackedInt32Array([
				inner + i, outer + i, outer + next,
				inner + i, outer + next, inner + next,
			]))
	_tris(points, colors, indices)


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


# ---------------------------------------------------------------- 小工具

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


func _bytes(count: int) -> PackedByteArray:
	var values := PackedByteArray()
	@warning_ignore("return_value_discarded")
	values.resize(maxi(count, 0))
	return values


func _ints(count: int) -> PackedInt32Array:
	var values := PackedInt32Array()
	@warning_ignore("return_value_discarded")
	values.resize(maxi(count, 0))
	return values


func _as_float(value: Variant) -> float:
	var kind := typeof(value)
	if kind == TYPE_INT or kind == TYPE_FLOAT:
		return float(str(value))
	return 0.0


## 锚点框（{x, y, w, h} 或 [x, y, w, h]），贴图像素。
func _box_px(value: Variant) -> Rect2:
	var origin := Vector2.ZERO
	var wh := Vector2.ZERO
	if value is Dictionary:
		var box: Dictionary = value
		origin = Vector2(_num(box, "x"), _num(box, "y"))
		wh = Vector2(_num(box, "w"), _num(box, "h"))
	elif value is Array:
		var items: Array = value
		if items.size() >= 4:
			origin = Vector2(_as_float(items[0]), _as_float(items[1]))
			wh = Vector2(_as_float(items[2]), _as_float(items[3]))
	if wh.x <= 0.0 or wh.y <= 0.0:
		return Rect2()
	return Rect2(origin, wh)


func _to_local(box: Rect2) -> Rect2:
	if box.size.x <= 0.0 or box.size.y <= 0.0:
		return Rect2()
	return Rect2((box.position - _head_origin) * _puppet_scale, box.size * _puppet_scale)


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
