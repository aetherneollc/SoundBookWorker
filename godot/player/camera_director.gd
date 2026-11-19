class_name CameraDirector2D
extends Node

## Deterministic anime-style framing. Cuts are immediate; movement inside a
## cut is evaluated directly from its frame range and therefore never drifts.

const VIEW_SIZE := Vector2(1280.0, 720.0)
const VIEW_CENTER := VIEW_SIZE * 0.5
const BACKGROUND_OVERSCAN := 1.15
const MEDIUM_LOOK_ROOM := 65.0
const PAN_DISTANCE := 35.0

var _camera: Camera2D
var _last_framing := "wide"


func setup(host: Node2D) -> Camera2D:
	if _camera != null and is_instance_valid(_camera):
		return _camera
	_camera = Camera2D.new()
	_camera.name = "Camera2D"
	_camera.anchor_mode = Camera2D.ANCHOR_MODE_DRAG_CENTER
	_camera.position = VIEW_CENTER
	_camera.zoom = Vector2.ONE
	_camera.enabled = true
	_camera.position_smoothing_enabled = false
	_camera.rotation_smoothing_enabled = false
	_camera.ignore_rotation = true
	host.add_child(_camera)
	_camera.make_current()
	return _camera


func update_camera(
	cut_cfg: Dictionary,
	actor_records: Array[Dictionary],
	shot_frame: int
) -> void:
	if _camera == null:
		return
	var framing: String = str(cut_cfg.get("framing", "wide")).to_lower()
	var focus_actor: String = str(cut_cfg.get("focus_actor", ""))
	var zoom_value: float = _framing_zoom(framing)
	var target: Vector2 = _framing_target(framing, focus_actor, actor_records, zoom_value)

	var from_frame: int = int(cut_cfg.get("from", 0))
	var to_frame: int = int(cut_cfg.get("to", from_frame + 1))
	var duration: int = maxi(to_frame - from_frame, 1)
	var progress: float = clampf(float(shot_frame - from_frame) / float(duration), 0.0, 1.0)
	var eased: float = _smoothstep(progress)
	var camera_move: String = str(cut_cfg.get("camera_move", "static")).to_lower()
	match camera_move:
		"push_in":
			zoom_value *= lerpf(1.0, 1.07, eased)
		"pull_out":
			zoom_value *= lerpf(1.07, 1.0, eased)
		"slow_pan_left":
			target.x += lerpf(PAN_DISTANCE, -PAN_DISTANCE, eased)
		"slow_pan_right":
			target.x += lerpf(-PAN_DISTANCE, PAN_DISTANCE, eased)
		_:
			pass

	target = _clamp_position(target, zoom_value)
	# No smoothing here: a new cut jumps immediately to its prescribed frame.
	_camera.position = target
	_camera.zoom = Vector2.ONE * zoom_value
	_last_framing = framing


func reset_wide() -> void:
	if _camera == null:
		return
	_camera.position = VIEW_CENTER
	_camera.zoom = Vector2.ONE
	_last_framing = "wide"


func get_camera() -> Camera2D:
	return _camera


func get_last_framing() -> String:
	return _last_framing


func _framing_zoom(framing: String) -> float:
	match framing:
		"two_shot":
			return 1.18
		"medium":
			return 1.42
		"close":
			return 1.85
		_:
			return 1.0


func _framing_target(
	framing: String,
	focus_actor: String,
	actor_records: Array[Dictionary],
	zoom_value: float
) -> Vector2:
	if framing == "wide" or actor_records.is_empty():
		return VIEW_CENTER
	if framing == "two_shot":
		return _two_shot_target(actor_records)

	var record: Dictionary = _find_actor(focus_actor, actor_records)
	if record.is_empty():
		record = actor_records[0]
	if framing == "close":
		var head: Vector2 = _focus_point(record, "head")
		# Put the head near the upper third while retaining safe headroom.
		return Vector2(head.x, head.y + 55.0 / maxf(zoom_value, 0.001))

	var chest: Vector2 = _focus_point(record, "chest")
	var facing: String = str(record.get("facing", "front")).to_lower()
	# Moving the camera opposite the desired screen placement puts a
	# right-looking actor left of center, and vice versa.
	if facing == "right":
		chest.x += MEDIUM_LOOK_ROOM / maxf(zoom_value, 0.001)
	elif facing == "left":
		chest.x -= MEDIUM_LOOK_ROOM / maxf(zoom_value, 0.001)
	chest.y -= 20.0
	return chest


func _two_shot_target(actor_records: Array[Dictionary]) -> Vector2:
	var first: Vector2 = _focus_point(actor_records[0], "chest")
	if actor_records.size() == 1:
		return first
	var second: Vector2 = _focus_point(actor_records[1], "chest")
	return (first + second) * 0.5


func _find_actor(actor_id: String, actor_records: Array[Dictionary]) -> Dictionary:
	if actor_id != "":
		for record: Dictionary in actor_records:
			if str(record.get("id", "")) == actor_id:
				return record
	return {}


func _focus_point(record: Dictionary, part: String) -> Vector2:
	var puppet_value: Variant = record.get("puppet")
	if puppet_value is Object:
		var puppet := puppet_value as Object
		if is_instance_valid(puppet) and puppet.has_method("get_focus_point"):
			var point_value: Variant = puppet.call("get_focus_point", part)
			if point_value is Vector2:
				return point_value as Vector2
	if puppet_value is Node2D:
		return (puppet_value as Node2D).global_position
	return VIEW_CENTER


func _clamp_position(value: Vector2, zoom_value: float) -> Vector2:
	var safe_zoom: float = maxf(zoom_value, 0.001)
	var visible_half: Vector2 = VIEW_SIZE * 0.5 / safe_zoom
	var background_half: Vector2 = VIEW_SIZE * BACKGROUND_OVERSCAN * 0.5
	var minimum: Vector2 = VIEW_CENTER - background_half + visible_half
	var maximum: Vector2 = VIEW_CENTER + background_half - visible_half
	var result := value
	if minimum.x > maximum.x:
		result.x = VIEW_CENTER.x
	else:
		result.x = clampf(result.x, minimum.x, maximum.x)
	if minimum.y > maximum.y:
		result.y = VIEW_CENTER.y
	else:
		result.y = clampf(result.y, minimum.y, maximum.y)
	return result


func _smoothstep(value: float) -> float:
	var clamped: float = clampf(value, 0.0, 1.0)
	return clamped * clamped * (3.0 - 2.0 * clamped)
