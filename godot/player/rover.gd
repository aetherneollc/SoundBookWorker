class_name Rover2D
extends Node2D

## Runtime AT-1 rover puppet.  The rig stays in source-image coordinates under
## _facing_root; this node's origin is always the wheel contact centre.

const VIEW_SIZE := Vector2(1280.0, 720.0)
const MIN_TARGET_HEIGHT := 48.0
const DEFAULT_SOURCE_SIZE := Vector2(1024.0, 1024.0)
# Draw order inside the rig canvas. Wheels sit in front of the hull: the body
# cut-out keeps a dark wheel-well backing that must stay hidden behind them.
const Z_SHADOW := -2
const Z_SUSPENSION := 0
const Z_CHASSIS := 4
const Z_WHEEL := 5
const ROVER_ACTIONS := [
	"idle", "drive", "patrol", "scan", "inspect", "work", "assemble",
	"pick", "carry", "wave", "alert", "tilt", "recover",
	# Legacy Remotion names remain playable.
	"sample", "reach", "signal", "stand", "fall",
]
const MOVING_ACTIONS := ["drive", "patrol", "carry"]
const IK_ACTIONS := ["scan", "inspect", "work", "assemble", "pick", "carry", "wave", "sample", "reach"]
const SIGNAL_ACTIONS := ["scan", "inspect", "work", "assemble", "alert", "signal"]


class WheelArt:
	extends Node2D

	var radius := 48.0
	var fallback := true

	func configure(value: float, use_fallback: bool) -> void:
		radius = maxf(value, 8.0)
		fallback = use_fallback
		queue_redraw()

	func _draw() -> void:
		if fallback:
			draw_circle(Vector2.ZERO, radius, Color(0.035, 0.065, 0.095, 1.0))
			draw_arc(Vector2.ZERO, radius * 0.92, 0.0, TAU, 40, Color(0.28, 0.47, 0.57), radius * 0.11, true)
			draw_circle(Vector2.ZERO, radius * 0.54, Color(0.12, 0.28, 0.38, 1.0))
			draw_arc(Vector2.ZERO, radius * 0.52, 0.0, TAU, 32, Color(0.72, 0.84, 0.86), radius * 0.08, true)
			for index: int in range(8):
				var angle: float = TAU * float(index) / 8.0
				var normal := Vector2(cos(angle), sin(angle))
				draw_line(
					normal * radius * 0.78,
					normal * radius * 0.97,
					Color(0.62, 0.76, 0.79),
					maxf(2.0, radius * 0.07),
					true
				)
		# This asymmetric mark makes exact rolling visible even on symmetric source wheels.
		draw_line(
			Vector2.ZERO,
			Vector2(radius * 0.72, 0.0),
			Color(1.0, 0.61, 0.20, 0.98),
			maxf(2.5, radius * 0.075),
			true
		)
		draw_circle(Vector2.ZERO, maxf(3.0, radius * 0.12), Color(0.96, 0.74, 0.36))


class SuspensionArt:
	extends Node2D

	var centers: Array[Vector2] = []
	var hull_y := 520.0
	var radii: Array[float] = []
	var source_width := DEFAULT_SOURCE_SIZE.x

	func configure(
		points: Array[Vector2],
		wheel_radii: Array[float],
		value_hull_y: float,
		value_source_width: float
	) -> void:
		centers = points
		radii = wheel_radii
		hull_y = value_hull_y
		source_width = value_source_width
		queue_redraw()

	func _draw() -> void:
		for index: int in range(centers.size()):
			var wheel: Vector2 = centers[index]
			var radius: float = radii[index] if index < radii.size() else 42.0
			var side: float = -1.0 if index < centers.size() / 2 else 1.0
			var hip := Vector2(
				lerpf(wheel.x, source_width * 0.5, 0.18),
				hull_y
			)
			var knee := Vector2(wheel.x + side * radius * 0.42, lerpf(hip.y, wheel.y, 0.56))
			var outline: float = maxf(8.0, radius * 0.34)
			var fill: float = maxf(4.0, radius * 0.20)
			draw_polyline(PackedVector2Array([hip, knee, wheel]), Color(0.035, 0.09, 0.13), outline, true)
			draw_polyline(PackedVector2Array([hip, knee, wheel]), Color(0.55, 0.72, 0.77), fill, true)
			draw_circle(knee, maxf(5.0, radius * 0.20), Color(0.07, 0.23, 0.34))
			draw_arc(
				knee,
				maxf(5.0, radius * 0.20),
				0.0,
				TAU,
				24,
				Color(0.96, 0.56, 0.19),
				maxf(2.0, radius * 0.055),
				true
			)


class BodyArt:
	extends Node2D

	var source_size := DEFAULT_SOURCE_SIZE
	var anchor_x := 0.5
	var foot_y := 0.92

	func configure(value_size: Vector2, value_anchor_x: float, value_foot_y: float) -> void:
		source_size = value_size
		anchor_x = value_anchor_x
		foot_y = value_foot_y
		queue_redraw()

	func _draw() -> void:
		var width: float = source_size.x
		var height: float = source_size.y
		var ground: float = foot_y * height
		var left: float = width * 0.13
		var right: float = width * 0.87
		var top: float = ground - height * 0.42
		var bottom: float = ground - height * 0.17
		var hull := PackedVector2Array([
			Vector2(left, top + height * 0.10),
			Vector2(left + width * 0.12, top),
			Vector2(right - width * 0.12, top),
			Vector2(right, top + height * 0.10),
			Vector2(right - width * 0.03, bottom),
			Vector2(left + width * 0.03, bottom),
		])
		draw_colored_polygon(hull, Color(0.79, 0.87, 0.88))
		draw_polyline(
			PackedVector2Array([hull[0], hull[1], hull[2], hull[3], hull[4], hull[5], hull[0]]),
			Color(0.035, 0.09, 0.14),
			maxf(5.0, width * 0.009),
			true
		)
		var bay := Rect2(
			Vector2(width * 0.29, top + height * 0.10),
			Vector2(width * 0.42, height * 0.16)
		)
		draw_rect(bay, Color(0.09, 0.24, 0.34), true)
		draw_rect(bay, Color(0.24, 0.55, 0.66), false, maxf(3.0, width * 0.005), true)
		draw_rect(
			Rect2(Vector2(width * 0.22, bottom - height * 0.035), Vector2(width * 0.56, height * 0.035)),
			Color(0.95, 0.53, 0.18),
			true
		)
		for index: int in range(4):
			draw_circle(
				Vector2(width * (0.43 + float(index) * 0.045), top + height * 0.19),
				maxf(3.0, width * 0.008),
				Color(0.25, 0.83, 0.90)
			)


class ArmLinkArt:
	extends Node2D

	var length := 120.0
	var lower_link := false

	func configure(value_length: float, value_lower: bool) -> void:
		length = maxf(value_length, 12.0)
		lower_link = value_lower
		queue_redraw()

	func _draw() -> void:
		var width: float = maxf(8.0, length * (0.13 if lower_link else 0.16))
		draw_line(Vector2.ZERO, Vector2(length, 0.0), Color(0.035, 0.09, 0.14), width + 8.0, true)
		draw_line(Vector2.ZERO, Vector2(length, 0.0), Color(0.72, 0.83, 0.84), width, true)
		draw_line(
			Vector2(length * 0.18, 0.0),
			Vector2(length * 0.80, 0.0),
			Color(0.95, 0.55, 0.19),
			maxf(2.5, width * 0.22),
			true
		)
		draw_circle(Vector2.ZERO, width * 0.72, Color(0.07, 0.22, 0.32))
		draw_arc(Vector2.ZERO, width * 0.72, 0.0, TAU, 24, Color(0.80, 0.89, 0.90), maxf(2.0, width * 0.22), true)
		if lower_link:
			var tip := Vector2(length, 0.0)
			draw_line(tip, tip + Vector2(length * 0.16, -length * 0.12), Color(0.70, 0.84, 0.84), maxf(4.0, width * 0.34), true)
			draw_line(tip, tip + Vector2(length * 0.16, length * 0.12), Color(0.70, 0.84, 0.84), maxf(4.0, width * 0.34), true)


class ShadowArt:
	extends Node2D

	var half_size := Vector2(120.0, 12.0)

	func configure(value: Vector2) -> void:
		half_size = Vector2(maxf(value.x, 8.0), maxf(value.y, 2.0))
		queue_redraw()

	func _draw() -> void:
		# Same layered flat ellipses as the actors' contact shadows in stage.gd.
		_ellipse(half_size * 1.12, Color(0.015, 0.025, 0.055, 0.08))
		_ellipse(half_size, Color(0.015, 0.025, 0.055, 0.12))
		_ellipse(half_size * 0.8, Color(0.015, 0.025, 0.055, 0.18))

	func _ellipse(radius: Vector2, color: Color) -> void:
		var points := PackedVector2Array()
		for index: int in range(40):
			var angle: float = TAU * float(index) / 40.0
			points.append(Vector2(cos(angle) * radius.x, sin(angle) * radius.y))
		draw_colored_polygon(points, color)


class SignalArt:
	extends Node2D

	var radius := 20.0
	var intensity := 0.2
	var phase := 0.0

	func configure(value_radius: float) -> void:
		radius = maxf(value_radius, 6.0)
		queue_redraw()

	func set_pulse(value: float, value_phase: float) -> void:
		intensity = clampf(value, 0.0, 1.0)
		phase = value_phase
		queue_redraw()

	func _draw() -> void:
		draw_circle(Vector2.ZERO, radius * 1.55, Color(1.0, 0.55, 0.12, 0.05 + intensity * 0.18))
		draw_circle(Vector2.ZERO, radius, Color(0.12, 0.24, 0.29, 0.98))
		draw_circle(Vector2.ZERO, radius * 0.64, Color(1.0, 0.47 + intensity * 0.34, 0.12, 0.62 + intensity * 0.38))
		draw_circle(Vector2(-radius * 0.18, -radius * 0.18), radius * 0.17, Color(1.0, 0.96, 0.72, 0.9))
		if intensity > 0.45:
			var wave: float = radius * (1.8 + fmod(phase * 2.3, 1.0) * 1.9)
			draw_arc(Vector2.ZERO, wave, -1.25, 1.25, 24, Color(1.0, 0.69, 0.24, intensity * 0.42), maxf(2.0, radius * 0.10), true)


var wheel_angle := 0.0

var _rig_base_dir := ""
var _source_size := DEFAULT_SOURCE_SIZE
# Painted extent of the rover inside the source canvas. Masters keep a wide
# transparent margin, so size and arm placement follow this, not the canvas.
var _content_rect := Rect2(Vector2.ZERO, DEFAULT_SOURCE_SIZE)
var _body_image: Image
var _render_scale := 1.0
var _anchor_x := 0.5
var _foot_y := 0.92
var _native_facing := "right"
var _facing := "right"
var _mirror_sign := 1.0
var _body_pivot := Vector2(512.0, 700.0)
var _signal_point := Vector2(512.0, 180.0)
var _arm_mount := Vector2(820.0, 500.0)
var _arm_upper_length := 150.0
var _arm_lower_length := 130.0
var _arm_shoulder_angle := 0.0
var _arm_elbow_angle := 0.0
var _current_action := "idle"
var _action_started_at := 0.0
var _has_last_world_x := false
var _last_world_x := 0.0
var _wheel_radius_world := 1.0

var _facing_root: Node2D
var _visual_root: Node2D
var _suspension: SuspensionArt
var _chassis: Node2D
var _mast_pivot: Node2D
var _arm_upper: Node2D
var _arm_elbow: Node2D
var _signal: SignalArt
var _wheel_runtimes: Array[Dictionary] = []


## Builds the rover from robot_rig.json. target_height is the on-screen height
## of the painted rover (wheels to sensor head) at scale 1, in pixels.
func setup(robot_rig_path: String, target_height: float = 265.0) -> bool:
	_clear_parts()
	_rig_base_dir = ""
	_body_image = null
	wheel_angle = 0.0
	_has_last_world_x = false
	_current_action = "idle"
	_action_started_at = 0.0

	var rig := _read_rig(robot_rig_path)
	var rig_loaded: bool = not rig.is_empty()
	_source_size = Vector2(
		maxf(_number(rig.get("width"), DEFAULT_SOURCE_SIZE.x), 16.0),
		maxf(_number(rig.get("height"), DEFAULT_SOURCE_SIZE.y), 16.0)
	)
	var texture_size_value: Variant = rig.get("texture_size", [])
	if texture_size_value is Array and (texture_size_value as Array).size() >= 2:
		var texture_size: Array = texture_size_value as Array
		_source_size = Vector2(
			maxf(_number(texture_size[0], _source_size.x), 16.0),
			maxf(_number(texture_size[1], _source_size.y), 16.0)
		)
	_anchor_x = clampf(_number(rig.get("anchor_x"), 0.5), 0.0, 1.0)
	_foot_y = clampf(_number(rig.get("foot_y"), 0.92), 0.2, 1.0)
	_native_facing = str(rig.get("native_facing", "right")).to_lower()
	if _native_facing not in ["left", "right"]:
		_native_facing = "right"
	_facing = _native_facing
	_body_image = _load_image(_part_path(_body_value(rig)))
	_content_rect = _measure_content(rig)
	_render_scale = maxf(target_height, MIN_TARGET_HEIGHT) / _content_rect.size.y

	_facing_root = Node2D.new()
	_facing_root.name = "Facing"
	add_child(_facing_root)
	_visual_root = Node2D.new()
	_visual_root.name = "RigCanvas"
	_visual_root.position = Vector2(-_anchor_x * _source_size.x, -_foot_y * _source_size.y) * _render_scale
	_visual_root.scale = Vector2.ONE * _render_scale
	_facing_root.add_child(_visual_root)

	_body_pivot = _rig_point(rig.get("body_pivot"), Vector2(_anchor_x, _foot_y - 0.22))
	_signal_point = _rig_point(rig.get("signal_point"), Vector2(_anchor_x, maxf(0.08, _foot_y - 0.70)))

	var wheels: Array[Dictionary] = _normalise_wheels(rig)
	# A painted rover keeps the wheels it was drawn with (AT-1 shows three in
	# side view). Only a rig without a usable wheel pair gets the procedural set.
	if wheels.size() < 2:
		_ensure_four_wheels(wheels)
	_build_shadow()
	if _body_image == null:
		# The painted hull already carries its own undercarriage.
		_build_suspension(wheels)
	_build_wheels(wheels)
	_build_chassis()
	_build_mast(rig)
	_build_arm(rig)
	_build_signal()
	_apply_facing(_native_facing)
	visible = false
	return rig_loaded


func apply_state(state: Dictionary, time_sec: float) -> void:
	var active: bool = bool(state.get("active", true))
	visible = active
	if not active:
		_has_last_world_x = false
		return

	var scale_mult: float = maxf(_number(state.get("scale_mult"), 1.0), 0.1)
	scale = Vector2.ONE * scale_mult
	if state.has("x"):
		var x_value: float = _number(state.get("x"), position.x)
		position.x = x_value * VIEW_SIZE.x if x_value >= -0.5 and x_value <= 1.5 else x_value
	_apply_facing(str(state.get("facing", _facing)).to_lower())

	var action: String = str(state.get("action", "idle")).strip_edges().to_lower()
	if action not in ROVER_ACTIONS:
		action = "idle"
	if action != _current_action:
		_current_action = action
		_action_started_at = time_sec
	var action_time: float = maxf(0.0, time_sec - _action_started_at)

	var world_x: float = global_position.x
	if _has_last_world_x:
		var dx: float = world_x - _last_world_x
		wheel_angle += dx / maxf(_wheel_radius_world * scale_mult, 1.0)
		for runtime: Dictionary in _wheel_runtimes:
			var radius_world: float = maxf(
				float(runtime.get("radius_world", _wheel_radius_world)) * scale_mult,
				1.0
			)
			var angle: float = float(runtime.get("angle", 0.0)) + dx / radius_world
			runtime["angle"] = angle
			var wheel_node := runtime.get("node") as Node2D
			if wheel_node != null:
				# Reflection reverses visual rotation, so compensate to preserve screen-space rolling.
				wheel_node.rotation = angle * _mirror_sign
	_last_world_x = world_x
	_has_last_world_x = true

	_apply_chassis_motion(action, action_time)
	_apply_mast_and_signal(action, time_sec)
	_apply_arm(action, state.get("target_pos"), time_sec)


func get_focus_point() -> Vector2:
	if _signal != null and is_instance_valid(_signal):
		return _signal.to_global(Vector2.ZERO)
	return global_position + Vector2(0.0, -maxf(80.0, _source_size.y * _render_scale * 0.45))


func _clear_parts() -> void:
	for child: Node in get_children():
		remove_child(child)
		child.queue_free()
	_facing_root = null
	_visual_root = null
	_suspension = null
	_chassis = null
	_mast_pivot = null
	_arm_upper = null
	_arm_elbow = null
	_signal = null
	_wheel_runtimes.clear()


func _read_rig(path_value: String) -> Dictionary:
	var path: String = path_value.strip_edges().replace("\\", "/")
	if path == "":
		return {}
	if not path.begins_with("res://") and not path.begins_with("user://") and not path.is_absolute_path():
		var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
		var candidates := [
			path,
			project_dir.path_join(path).simplify_path(),
			project_dir.get_base_dir().path_join(path).simplify_path(),
		]
		for candidate: String in candidates:
			if FileAccess.file_exists(candidate):
				path = candidate
				break
	if not FileAccess.file_exists(path):
		return {}
	var handle := FileAccess.open(path, FileAccess.READ)
	if handle == null:
		return {}
	var text: String = handle.get_as_text()
	handle.close()
	var parser := JSON.new()
	if parser.parse(text) != OK or not parser.data is Dictionary:
		return {}
	_rig_base_dir = path.get_base_dir()
	return parser.data as Dictionary


func _normalise_wheels(rig: Dictionary) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	var raw_value: Variant = rig.get("wheels", [])
	var raw_items: Array = []
	if raw_value is Array:
		raw_items = raw_value as Array
	elif raw_value is Dictionary:
		var wheel_map: Dictionary = raw_value as Dictionary
		var keys: Array = wheel_map.keys()
		keys.sort()
		for key: Variant in keys:
			raw_items.append(wheel_map[key])

	var centers_value: Variant = rig.get("wheel_centers", [])
	var centers: Array = centers_value as Array if centers_value is Array else []
	var radius_value: Variant = rig.get("wheel_radius", 0.085)
	for index: int in range(maxi(raw_items.size(), centers.size())):
		var raw: Variant = raw_items[index] if index < raw_items.size() else {}
		var entry: Dictionary = raw.duplicate() if raw is Dictionary else {"file": str(raw)}
		var center := Vector2(
			_number(entry.get("cx"), NAN),
			_number(entry.get("cy"), NAN)
		)
		if (is_nan(center.x) or is_nan(center.y)) and index < centers.size():
			center = _point_value(centers[index], Vector2(0.5, _foot_y - 0.09))
		var radius: float = _number(entry.get("r"), NAN)
		if is_nan(radius):
			if radius_value is Array and index < (radius_value as Array).size():
				radius = _number((radius_value as Array)[index], 0.085)
			else:
				radius = _number(radius_value, 0.085)
		if absf(center.x) <= 1.5:
			center.x *= _source_size.x
		if absf(center.y) <= 1.5:
			center.y *= _source_size.y
		if absf(radius) <= 1.0:
			radius *= _source_size.y
		entry["center"] = center
		entry["radius"] = maxf(radius, 8.0)
		result.append(entry)
	result.sort_custom(func(left: Dictionary, right: Dictionary) -> bool:
		return (left.get("center") as Vector2).x < (right.get("center") as Vector2).x
	)
	return result


func _ensure_four_wheels(wheels: Array[Dictionary]) -> void:
	if wheels.size() >= 4:
		return
	var radius: float = (
		float(wheels[0].get("radius", _source_size.y * 0.085))
		if not wheels.is_empty()
		else _source_size.y * 0.085
	)
	var y: float = _foot_y * _source_size.y - radius
	var slots := [0.20, 0.40, 0.62, 0.82]
	while wheels.size() < 4:
		var best_x: float = float(slots[0]) * _source_size.x
		var best_gap := -1.0
		for slot: float in slots:
			var candidate: float = slot * _source_size.x
			var gap: float = _source_size.x
			for wheel: Dictionary in wheels:
				var center: Vector2 = wheel.get("center", Vector2.ZERO)
				gap = minf(gap, absf(center.x - candidate))
			if gap > best_gap:
				best_gap = gap
				best_x = candidate
		wheels.append({"center": Vector2(best_x, y), "radius": radius, "fallback": true})
	wheels.sort_custom(func(left: Dictionary, right: Dictionary) -> bool:
		return (left.get("center") as Vector2).x < (right.get("center") as Vector2).x
	)


func _build_suspension(wheels: Array[Dictionary]) -> void:
	var centers: Array[Vector2] = []
	var radii: Array[float] = []
	for wheel: Dictionary in wheels:
		centers.append(wheel.get("center", Vector2.ZERO))
		radii.append(float(wheel.get("radius", 40.0)))
	_suspension = SuspensionArt.new()
	_suspension.name = "WheelLegs"
	_suspension.z_index = Z_SUSPENSION
	_suspension.configure(
		centers,
		radii,
		_body_pivot.y + _source_size.y * 0.05,
		_source_size.x
	)
	_visual_root.add_child(_suspension)


func _build_wheels(wheels: Array[Dictionary]) -> void:
	var total_radius_world := 0.0
	for index: int in range(wheels.size()):
		var entry: Dictionary = wheels[index]
		var center: Vector2 = entry.get("center", Vector2.ZERO)
		var radius: float = maxf(float(entry.get("radius", 40.0)), 8.0)
		var tire_path: String = _part_path(entry.get("tire_file", entry.get("tire_src", "")))
		var tire_texture: Texture2D = _load_png(tire_path)
		if tire_texture != null:
			var tire_sprite := Sprite2D.new()
			tire_sprite.name = "Tire_%d" % index
			tire_sprite.texture = tire_texture
			tire_sprite.centered = false
			tire_sprite.z_index = Z_WHEEL
			_visual_root.add_child(tire_sprite)

		var wheel := WheelArt.new()
		wheel.name = "Wheel_%d" % index
		wheel.position = center
		wheel.z_index = Z_WHEEL
		var wheel_path: String = _part_path(entry.get("file", entry.get("src", "")))
		var wheel_texture: Texture2D = _load_png(wheel_path)
		if wheel_texture != null:
			var sprite := Sprite2D.new()
			sprite.name = "Texture"
			sprite.texture = wheel_texture
			sprite.centered = false
			sprite.position = -center
			wheel.add_child(sprite)
			wheel.configure(radius, false)
		else:
			wheel.configure(radius, true)
		_visual_root.add_child(wheel)
		var radius_world: float = radius * _render_scale
		total_radius_world += radius_world
		_wheel_runtimes.append({
			"node": wheel,
			"radius_world": radius_world,
			"angle": 0.0,
		})
	_wheel_radius_world = total_radius_world / float(maxi(_wheel_runtimes.size(), 1))


func _body_value(rig: Dictionary) -> Variant:
	var body_value: Variant = rig.get("body_file", "")
	if str(body_value).strip_edges() == "":
		body_value = rig.get("body", rig.get("body_src", ""))
	return body_value


## Painted extent in source pixels: the hull's opaque bounds joined with the
## wheels, or the rig's content_top / foot_y band when there is no hull image.
func _measure_content(rig: Dictionary) -> Rect2:
	var foot: float = _foot_y * _source_size.y
	var top_ratio: float = _number(rig.get("content_top"), NAN)
	if is_nan(top_ratio) or top_ratio < 0.0 or top_ratio > _foot_y - 0.08:
		top_ratio = maxf(0.0, _foot_y - 0.58)
	var top: float = top_ratio * _source_size.y
	var left: float = _source_size.x * 0.13
	var right: float = _source_size.x * 0.87
	if _body_image != null:
		var used: Rect2i = _body_image.get_used_rect()
		if used.size.x > 0 and used.size.y > 0:
			# Scale the hull image's own pixels into the declared source canvas.
			var ratio := Vector2(
				_source_size.x / float(_body_image.get_width()),
				_source_size.y / float(_body_image.get_height())
			)
			left = float(used.position.x) * ratio.x
			right = float(used.end.x) * ratio.x
			top = minf(top, float(used.position.y) * ratio.y)
	var wheels_value: Variant = rig.get("wheels", [])
	if wheels_value is Array:
		for wheel_value: Variant in wheels_value as Array:
			if not wheel_value is Dictionary:
				continue
			var wheel: Dictionary = wheel_value as Dictionary
			var cx: float = _number(wheel.get("cx"), NAN)
			var radius: float = _number(wheel.get("r"), NAN)
			if is_nan(cx) or is_nan(radius):
				continue
			if absf(cx) <= 1.5:
				cx *= _source_size.x
			if absf(radius) <= 1.0:
				radius *= _source_size.y
			left = minf(left, cx - radius)
			right = maxf(right, cx + radius)
	return Rect2(
		Vector2(left, top),
		Vector2(maxf(right - left, 16.0), maxf(foot - top, 16.0))
	)


func _build_shadow() -> void:
	var shadow := ShadowArt.new()
	shadow.name = "ContactShadow"
	shadow.z_index = Z_SHADOW
	shadow.position = Vector2(_content_rect.get_center().x, _foot_y * _source_size.y)
	shadow.configure(Vector2(_content_rect.size.x * 0.52, _content_rect.size.y * 0.055))
	_visual_root.add_child(shadow)


func _build_chassis() -> void:
	_chassis = Node2D.new()
	_chassis.name = "Chassis"
	_chassis.position = _body_pivot
	_chassis.z_index = Z_CHASSIS
	_visual_root.add_child(_chassis)
	if _body_image != null:
		var sprite := Sprite2D.new()
		sprite.name = "Body"
		sprite.texture = ImageTexture.create_from_image(_body_image)
		sprite.centered = false
		sprite.position = -_body_pivot
		_chassis.add_child(sprite)
	else:
		var body := BodyArt.new()
		body.name = "BodyFallback"
		body.position = -_body_pivot
		body.configure(_source_size, _anchor_x, _foot_y)
		_chassis.add_child(body)


func _build_mast(rig: Dictionary) -> void:
	var mast_value: Variant = rig.get("mast", {})
	var mast: Dictionary = mast_value as Dictionary if mast_value is Dictionary else {}
	var mast_pivot_source: Vector2 = _rig_point(
		mast.get("pivot"),
		Vector2(_anchor_x, maxf(0.16, _foot_y - 0.48))
	)
	_mast_pivot = Node2D.new()
	_mast_pivot.name = "SensorMast"
	_mast_pivot.position = mast_pivot_source - _body_pivot
	_mast_pivot.z_index = 2
	_chassis.add_child(_mast_pivot)
	var texture: Texture2D = _load_png(_part_path(mast))
	if texture != null:
		var sprite := Sprite2D.new()
		sprite.texture = texture
		sprite.centered = false
		sprite.position = -mast_pivot_source
		_mast_pivot.add_child(sprite)
	elif _body_image == null:
		# A painted hull keeps its own mast; only the procedural hull needs a stem.
		var stem := Line2D.new()
		stem.width = maxf(8.0, _source_size.x * 0.012)
		stem.default_color = Color(0.72, 0.84, 0.85)
		stem.points = PackedVector2Array([Vector2.ZERO, Vector2(0.0, -_source_size.y * 0.16)])
		_mast_pivot.add_child(stem)


func _build_arm(rig: Dictionary) -> void:
	var arm_value: Variant = rig.get("arm", {})
	var arm: Dictionary = arm_value as Dictionary if arm_value is Dictionary else {}
	var mount_value: Variant = arm.get("pivot", rig.get("arm_mount", null))
	# Without a rigged pivot the shoulder sits on the hull's front deck, found
	# on the painted body itself so it never floats beside a small rover.
	var forward: float = 1.0 if _native_facing == "right" else -1.0
	var mount_x: float = _content_rect.get_center().x + forward * _content_rect.size.x * 0.27
	var mount_px := Vector2(mount_x, _hull_top_at(mount_x) + _content_rect.size.y * 0.05)
	_arm_mount = _rig_point(mount_value, mount_px / _source_size)
	_arm_upper_length = maxf(_content_rect.size.x * 0.30, 28.0)
	_arm_lower_length = maxf(_content_rect.size.x * 0.25, 24.0)

	_arm_upper = Node2D.new()
	_arm_upper.name = "ArmUpper"
	_arm_upper.position = _arm_mount - _body_pivot
	_arm_upper.z_index = 4
	_chassis.add_child(_arm_upper)
	var upper_art := ArmLinkArt.new()
	upper_art.name = "UpperLink"
	upper_art.configure(_arm_upper_length, false)
	_arm_upper.add_child(upper_art)

	var arm_texture: Texture2D = _load_png(_part_path(arm))
	if arm_texture != null:
		var sprite := Sprite2D.new()
		sprite.name = "ArmTexture"
		sprite.texture = arm_texture
		sprite.centered = false
		sprite.position = -_arm_mount
		sprite.modulate = Color(1.0, 1.0, 1.0, 0.92)
		_arm_upper.add_child(sprite)

	_arm_elbow = Node2D.new()
	_arm_elbow.name = "ArmLower"
	_arm_elbow.position = Vector2(_arm_upper_length, 0.0)
	_arm_upper.add_child(_arm_elbow)
	var lower_art := ArmLinkArt.new()
	lower_art.name = "LowerLink"
	lower_art.configure(_arm_lower_length, true)
	_arm_elbow.add_child(lower_art)


func _build_signal() -> void:
	_signal = SignalArt.new()
	_signal.name = "Signal"
	_signal.position = _signal_point - _body_pivot
	_signal.z_index = 6
	_signal.configure(maxf(10.0, _source_size.x * 0.018))
	_chassis.add_child(_signal)


func _apply_facing(value: String) -> void:
	if value in ["left", "right"]:
		_facing = value
	_mirror_sign = -1.0 if _facing != _native_facing else 1.0
	if _facing_root != null:
		_facing_root.scale = Vector2(_mirror_sign, 1.0)


func _apply_chassis_motion(action: String, action_time: float) -> void:
	if _chassis == null:
		return
	var bob_world := 0.55 * sin(action_time * 2.0)
	if action in MOVING_ACTIONS:
		bob_world = 1.5 * sin(action_time * 11.0) + 0.35 * sin(action_time * 23.0)
	elif action in IK_ACTIONS:
		bob_world = 0.75 * sin(action_time * 3.2)
	var tilt_degrees := 0.45 * sin(action_time * 2.1)
	match action:
		"alert":
			tilt_degrees = 3.5 * sin(action_time * 8.5)
			bob_world += 1.0 * sin(action_time * 12.0)
		"tilt", "fall":
			tilt_degrees = 14.0 + 1.5 * sin(action_time * 3.0)
			bob_world += 4.0
		"recover":
			var spring: float = exp(-action_time * 1.35)
			tilt_degrees = 15.0 * spring * cos(action_time * 5.3)
			bob_world += 4.0 * spring
		"stand":
			tilt_degrees = 1.2 * sin(action_time * 2.0)
	_chassis.position = _body_pivot + Vector2(0.0, bob_world / maxf(_render_scale, 0.001))
	_chassis.rotation = deg_to_rad(tilt_degrees) * _mirror_sign


func _apply_mast_and_signal(action: String, time_sec: float) -> void:
	if _mast_pivot != null:
		var amplitude: float = 7.0 if action in SIGNAL_ACTIONS else 2.4
		var speed: float = 2.8 if action in SIGNAL_ACTIONS else 1.25
		_mast_pivot.rotation = deg_to_rad(sin(time_sec * speed) * amplitude) * _mirror_sign
	if _signal != null:
		var active: bool = action in SIGNAL_ACTIONS
		var pulse: float = 0.22
		if active:
			pulse = 0.55 + 0.45 * maxf(0.0, sin(time_sec * 10.0))
		_signal.set_pulse(pulse, time_sec)


func _apply_arm(action: String, target_value: Variant, time_sec: float) -> void:
	if _arm_upper == null or _arm_elbow == null or _chassis == null:
		return
	var target_rel: Vector2
	var reach: float = _arm_upper_length + _arm_lower_length
	var working: bool = action in IK_ACTIONS
	# Only a working action reaches for the stage target; driving and idling
	# keep the arm in its carry pose instead of trailing a fixed world point.
	var explicit_world: Variant = _target_world(target_value) if working else null
	if explicit_world is Vector2:
		var target_chassis: Vector2 = _chassis.to_local(explicit_world as Vector2)
		target_rel = target_chassis - _arm_upper.position
		# A small tool-tip orbit keeps the arm visibly busy on a fixed target.
		var orbit: float = reach * (0.07 if action in ["scan", "wave"] else 0.035)
		target_rel += Vector2(sin(time_sec * 2.3), cos(time_sec * 1.7)) * orbit
	else:
		var direction: float = 1.0 if _native_facing == "right" else -1.0
		if working:
			var sweep: float = sin(time_sec * (1.25 if action in ["scan", "inspect"] else 1.8))
			var lift: float = -0.30 - 0.16 * sin(time_sec * 1.35)
			if action == "wave":
				lift = -0.55 + 0.12 * sin(time_sec * 5.0)
			elif action in ["pick", "sample"]:
				lift = 0.22 + 0.08 * sin(time_sec * 1.6)
			target_rel = Vector2(
				direction * reach * (0.72 + sweep * 0.08),
				reach * lift
			)
		else:
			target_rel = Vector2(direction * reach * 0.58, reach * 0.18)
	var solution: Vector2 = _solve_two_bone(
		target_rel,
		_arm_upper_length,
		_arm_lower_length,
		1.0 if _native_facing == "right" else -1.0
	)
	var blend: float = 0.22 if working else 0.12
	_arm_shoulder_angle = lerp_angle(_arm_shoulder_angle, solution.x, blend)
	_arm_elbow_angle = lerp_angle(_arm_elbow_angle, solution.y, blend)
	_arm_upper.rotation = _arm_shoulder_angle
	_arm_elbow.rotation = _arm_elbow_angle


## Returns (shoulder, elbow) rotations. bend is +1 for a rig drawn facing right
## and -1 for one drawn facing left, so the elbow rises above the hull either way.
func _solve_two_bone(target: Vector2, upper: float, lower: float, bend: float = 1.0) -> Vector2:
	var distance: float = clampf(target.length(), absf(upper - lower) + 0.001, upper + lower - 0.001)
	var cosine_elbow: float = clampf(
		(distance * distance - upper * upper - lower * lower) / (2.0 * upper * lower),
		-1.0,
		1.0
	)
	var elbow: float = acos(cosine_elbow) * (1.0 if bend >= 0.0 else -1.0)
	var shoulder: float = atan2(target.y, target.x) - atan2(
		lower * sin(elbow),
		upper + lower * cos(elbow)
	)
	return Vector2(shoulder, elbow)


func _target_world(value: Variant) -> Variant:
	var point: Vector2
	if value is Vector2:
		point = value as Vector2
	elif value is Array and (value as Array).size() >= 2:
		point = Vector2(_number((value as Array)[0]), _number((value as Array)[1]))
	elif value is Dictionary:
		var data: Dictionary = value as Dictionary
		if not data.has("x") or not data.has("y"):
			return null
		point = Vector2(_number(data.get("x")), _number(data.get("y")))
	else:
		return null
	if point.x >= -0.5 and point.x <= 1.5 and point.y >= -0.5 and point.y <= 1.5:
		point *= VIEW_SIZE
	return point


func _rig_point(value: Variant, fallback_normalized: Vector2) -> Vector2:
	var point: Vector2 = _point_value(value, fallback_normalized)
	if absf(point.x) <= 1.5:
		point.x *= _source_size.x
	if absf(point.y) <= 1.5:
		point.y *= _source_size.y
	return point


func _point_value(value: Variant, fallback: Vector2) -> Vector2:
	if value is Vector2:
		return value as Vector2
	if value is Array and (value as Array).size() >= 2:
		return Vector2(_number((value as Array)[0], fallback.x), _number((value as Array)[1], fallback.y))
	if value is Dictionary:
		var data: Dictionary = value as Dictionary
		return Vector2(_number(data.get("x"), fallback.x), _number(data.get("y"), fallback.y))
	return fallback


func _part_path(value: Variant) -> String:
	var raw := ""
	if value is String:
		raw = str(value)
	elif value is Dictionary:
		var data: Dictionary = value as Dictionary
		raw = str(data.get("file", data.get("src", "")))
	raw = raw.strip_edges().replace("\\", "/")
	if raw == "":
		return ""
	if raw.begins_with("res://") or raw.begins_with("user://") or raw.is_absolute_path():
		return raw.simplify_path()
	var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
	var candidates := [
		_rig_base_dir.path_join(raw).simplify_path(),
		project_dir.get_base_dir().path_join(raw).simplify_path(),
		project_dir.path_join(raw).simplify_path(),
		raw.simplify_path(),
	]
	for candidate: String in candidates:
		if FileAccess.file_exists(candidate):
			return candidate
	return candidates[0]


## First opaque row of the hull in the source column x, or a deck estimate when
## there is no hull image to read.
func _hull_top_at(x_source: float) -> float:
	var estimate: float = _content_rect.position.y + _content_rect.size.y * 0.30
	if _body_image == null:
		return estimate
	var width: int = _body_image.get_width()
	var height: int = _body_image.get_height()
	var column: int = clampi(int(round(x_source * float(width) / _source_size.x)), 0, width - 1)
	for row: int in range(height):
		if _body_image.get_pixel(column, row).a > 0.5:
			return float(row) * _source_size.y / float(height)
	return estimate


func _load_image(path: String) -> Image:
	if path == "" or not FileAccess.file_exists(path):
		return null
	var image: Image = Image.load_from_file(path)
	if image == null or image.is_empty() or image.get_width() <= 0 or image.get_height() <= 0:
		return null
	return image


func _load_png(path: String) -> Texture2D:
	var image: Image = _load_image(path)
	if image == null:
		return null
	return ImageTexture.create_from_image(image)


func _number(value: Variant, fallback: float = 0.0) -> float:
	if value is bool or value == null:
		return fallback
	if value is int or value is float:
		var number := float(value)
		return number if is_finite(number) else fallback
	var text := str(value)
	if not text.is_valid_float():
		return fallback
	var parsed := text.to_float()
	return parsed if is_finite(parsed) else fallback
