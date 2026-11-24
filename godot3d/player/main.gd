extends Node3D

## stage3d.json player. Same environment contract as godot/player/main.gd:
## STAGE_JSON, STAGE_RESULT_JSON, STAGE_CJK_FONT, STAGE_SAMPLE_FRAMES,
## STAGE_SAMPLE_DIR. One logical frame is evaluated per process tick so
## --fixed-fps and --write-movie stay frame-exact.

const VIEW_SIZE := Vector2(1280.0, 720.0)
const TURN_RATE_DEG := 260.0
const CULL_ALL := 0xFFFFF
const CULL_FIRST_PERSON := 0xFFFFD
const SENSOR_SCALE := 1.3
const VIEWMODEL_SENSOR_SCALE := 0.62

var _stage_data: Dictionary = {}
var _shots: Array[Dictionary] = []
var _captions: Array[Dictionary] = []
var _speech: Array[Dictionary] = []
var _stage_json_path := ""
var _base_dir := ""
var _fps := 24
var _total_frames := 0

var _character_script: Script
var _bay: Node3D
var _camera: Camera3D
var _actors: Dictionary = {}
var _actor_specs: Dictionary = {}
var _actor_state: Dictionary = {}
var _rover: Node3D
var _rover_spec: Dictionary = {}
var _rover_state: Dictionary = {}
var _rover_eye: MeshInstance3D
var _rover_eye_material: StandardMaterial3D
var _sensor: Node3D
var _sensor_spec: Dictionary = {}
var _sensor_owner := ""
var _handoff_log: Array[Dictionary] = []
var _handoff_prev_distance := INF
var _viewmodel: Node3D
var _viewmodel_sensor: Node3D
var _viewmodel_actor := ""
var _camera_state: Dictionary = {}
var _active_cut_key := ""

var _caption_layer: CanvasLayer
var _caption_root: Control
var _caption_label: Label
var _cjk_font_path := ""
var _font: FontFile

var _sample_frames: Array[int] = []
var _sample_frame_set: Dictionary = {}
var _sample_dir := ""
var _sample_files: Array[String] = []
var _result_json_path := ""

var _frame := 0
var _presented_frame := -1
var _start_usec := 0
var _finishing := false


func _ready() -> void:
	set_process(false)
	Engine.max_fps = 0
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	if not _read_configuration():
		_abort_startup("Could not load STAGE_JSON", 2)
		return
	if not _build_runtime():
		_abort_startup("Could not initialize the 3D stage player", 3)
		return
	var post_draw := Callable(self, "_on_frame_post_draw")
	if not RenderingServer.frame_post_draw.is_connected(post_draw):
		RenderingServer.frame_post_draw.connect(post_draw)
	_start_usec = Time.get_ticks_usec()
	set_process(true)
	print("STAGE3D_PLAYER ready frames=%d fps=%d actors=%d" % [_total_frames, _fps, _actors.size()])


func _process(_delta: float) -> void:
	if _finishing:
		return
	if _frame >= _total_frames and _presented_frame < 0:
		_finishing = true
		_finish()
		return
	if _presented_frame >= 0:
		return
	_apply_frame(_frame)
	_presented_frame = _frame
	_frame += 1


func _on_frame_post_draw() -> void:
	if _presented_frame < 0 or _finishing:
		return
	var completed_frame: int = _presented_frame
	_presented_frame = -1
	if _sample_frame_set.has(completed_frame):
		_capture_sample(completed_frame)


# -------------------------------------------------------- configuration


func _read_configuration() -> bool:
	_result_json_path = _resolve_output_path(OS.get_environment("STAGE_RESULT_JSON"))
	_cjk_font_path = _resolve_input_path(OS.get_environment("STAGE_CJK_FONT"))
	_parse_sample_frames(OS.get_environment("STAGE_SAMPLE_FRAMES"))
	if not _sample_frames.is_empty():
		var requested: String = OS.get_environment("STAGE_SAMPLE_DIR")
		_sample_dir = ProjectSettings.globalize_path("user://stage_samples") if requested == "" else _resolve_output_path(requested)
		if not _ensure_directory(_sample_dir):
			push_error("Stage3D: sample directory could not be created: %s" % _sample_dir)
			return false
	var stage_env: String = OS.get_environment("STAGE_JSON").strip_edges()
	if stage_env == "":
		push_error("Stage3D: STAGE_JSON is empty")
		return false
	_stage_json_path = _resolve_input_path(stage_env)
	if _stage_json_path == "" or not FileAccess.file_exists(_stage_json_path):
		push_error("Stage3D: stage3d.json not found: %s" % stage_env)
		return false
	var text: String = FileAccess.get_file_as_string(_stage_json_path)
	var parser := JSON.new()
	if parser.parse(text) != OK:
		push_error("Stage3D: invalid stage3d.json line %d: %s" % [parser.get_error_line(), parser.get_error_message()])
		return false
	if not parser.data is Dictionary:
		push_error("Stage3D: stage3d.json root must be an object")
		return false
	_stage_data = parser.data as Dictionary
	if int(_stage_data.get("version", 0)) != 1 or str(_stage_data.get("renderer", "")) != "godot3d":
		push_error("Stage3D: expected a version 1 godot3d stage")
		return false
	if int(_stage_data.get("width", 0)) != 1280 or int(_stage_data.get("height", 0)) != 720:
		push_error("Stage3D: stage size must be 1280x720")
		return false
	_fps = clampi(int(_stage_data.get("fps", 24)), 1, 240)
	_total_frames = int(_stage_data.get("total_frames", 0))
	if _total_frames <= 0:
		push_error("Stage3D: total_frames must be positive")
		return false
	_shots = _dict_array(_stage_data.get("shots", []))
	_captions = _dict_array(_stage_data.get("captions", []))
	_speech = _dict_array(_stage_data.get("speech", []))
	_base_dir = _stage_json_path.get_base_dir()
	return true


# --------------------------------------------------------------- build


func _build_runtime() -> bool:
	_character_script = load("res://player/character3d.gd") as Script
	var bay_script := load("res://player/bay.gd") as Script
	if _character_script == null or bay_script == null:
		push_error("Stage3D: player scripts could not be loaded")
		return false
	_bay = bay_script.new() as Node3D
	_bay.name = "ObservationBay"
	add_child(_bay)
	var set_cfg: Dictionary = _stage_data.get("set", {}) as Dictionary if _stage_data.get("set", {}) is Dictionary else {}
	_bay.call("build", _resolve_paths(set_cfg), _character_script)

	_camera = Camera3D.new()
	_camera.name = "StageCamera"
	_camera.near = 0.04
	_camera.far = 800.0
	_camera.fov = 50.0
	add_child(_camera)
	_camera.make_current()

	var actors_value: Variant = _stage_data.get("actors", {})
	if actors_value is Dictionary:
		for key: Variant in (actors_value as Dictionary).keys():
			var actor_id: String = str(key)
			var spec: Dictionary = _resolve_paths((actors_value as Dictionary)[key] as Dictionary)
			spec["id"] = actor_id
			var actor := _character_script.new() as Node3D
			actor.name = "Actor_%s" % actor_id
			add_child(actor)
			if not bool(actor.call("setup", spec)):
				push_error("Stage3D: actor %s could not be loaded" % actor_id)
				return false
			_actors[actor_id] = actor
			_actor_specs[actor_id] = spec
			_actor_state[actor_id] = {"yaw": NAN}
	_build_sensor()
	_build_rover()
	_load_font()
	_build_captions()
	return true


func _resolve_paths(value: Variant) -> Variant:
	if value is Dictionary:
		var out := {}
		for key: Variant in (value as Dictionary).keys():
			var k: String = str(key)
			var item: Variant = (value as Dictionary)[key]
			if item is String and (k == "model" or k == "image" or k.ends_with("library") or k.ends_with("_texture") or k.ends_with("_model")):
				out[k] = _resolve_asset_path(item as String) if (item as String) != "" else ""
			else:
				out[k] = _resolve_paths(item)
		return out
	if value is Array:
		var arr: Array = []
		for item: Variant in value as Array:
			arr.append(_resolve_paths(item))
		return arr
	return value


func _build_sensor() -> void:
	var props_value: Variant = _stage_data.get("props", {})
	if not props_value is Dictionary or not (props_value as Dictionary).has("sensor"):
		return
	_sensor_spec = _resolve_paths((props_value as Dictionary)["sensor"]) as Dictionary
	_sensor = _make_sensor(_vec3(_sensor_spec.get("grip_rotation_deg", null), Vector3.ZERO))
	_sensor.name = "Sensor"
	add_child(_sensor)
	_sensor.visible = false


## The returned root is the grip frame: its origin is the middle of the
## handle, so parenting it to a hand socket puts the handle in the fist.
func _make_sensor(rotation_deg: Vector3) -> Node3D:
	var path: String = str(_sensor_spec.get("model", ""))
	if path != "":
		var model: Node3D = _character_script.call("load_gltf_scene", path) as Node3D
		if model != null:
			_character_script.call("matte_materials", model, 0.6)
			var root_glb := Node3D.new()
			var holder := Node3D.new()
			holder.name = "GripFrame"
			root_glb.add_child(holder)
			holder.rotation_degrees = rotation_deg
			holder.add_child(model)
			model.position = -_vec3(_sensor_spec.get("grip_point", null), Vector3.ZERO)
			return root_glb
	var root := Node3D.new()
	var body := Node3D.new()
	body.scale = Vector3.ONE * SENSOR_SCALE
	root.add_child(body)
	var grip := MeshInstance3D.new()
	var grip_mesh := CylinderMesh.new()
	grip_mesh.top_radius = 0.016
	grip_mesh.bottom_radius = 0.018
	grip_mesh.height = 0.11
	grip.mesh = grip_mesh
	grip.material_override = _flat(Color("#2b3038"))
	body.add_child(grip)
	var head := MeshInstance3D.new()
	var head_mesh := BoxMesh.new()
	head_mesh.size = Vector3(0.075, 0.05, 0.036)
	head.mesh = head_mesh
	head.position = Vector3(0.0, 0.078, 0.0)
	head.material_override = _flat(Color("#e9edf2"))
	body.add_child(head)
	var stripe := MeshInstance3D.new()
	var stripe_mesh := BoxMesh.new()
	stripe_mesh.size = Vector3(0.077, 0.01, 0.038)
	stripe.mesh = stripe_mesh
	stripe.position = Vector3(0.0, 0.056, 0.0)
	stripe.material_override = _flat(Color("#f08a24"), 0.3)
	body.add_child(stripe)
	var screen := MeshInstance3D.new()
	var screen_mesh := BoxMesh.new()
	screen_mesh.size = Vector3(0.054, 0.03, 0.004)
	screen.mesh = screen_mesh
	screen.position = Vector3(0.0, 0.082, 0.019)
	screen.material_override = _flat(Color("#5fefff"), 2.2)
	body.add_child(screen)
	var antenna := MeshInstance3D.new()
	var antenna_mesh := CylinderMesh.new()
	antenna_mesh.top_radius = 0.003
	antenna_mesh.bottom_radius = 0.003
	antenna_mesh.height = 0.06
	antenna.mesh = antenna_mesh
	antenna.position = Vector3(0.026, 0.13, 0.0)
	antenna.material_override = _flat(Color("#2b3038"))
	body.add_child(antenna)
	var tip := MeshInstance3D.new()
	var tip_mesh := SphereMesh.new()
	tip_mesh.radius = 0.008
	tip_mesh.height = 0.016
	tip.mesh = tip_mesh
	tip.position = Vector3(0.026, 0.162, 0.0)
	tip.material_override = _flat(Color("#ff9a3c"), 2.0)
	body.add_child(tip)
	return root


func _flat(color: Color, emission: float = 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = 0.6
	if emission > 0.0:
		material.emission_enabled = true
		material.emission = color
		material.emission_energy_multiplier = emission
	return material


func _build_rover() -> void:
	var rover_value: Variant = _stage_data.get("rover", {})
	if not rover_value is Dictionary or (rover_value as Dictionary).is_empty():
		return
	_rover_spec = _resolve_paths(rover_value) as Dictionary
	var model: Node3D = _character_script.call("load_gltf_scene", str(_rover_spec.get("model", ""))) as Node3D
	if model != null and bool(_rover_spec.get("matte", false)):
		_character_script.call("matte_materials", model, 0.7)
	_rover = Node3D.new()
	_rover.name = "Rover_AT1"
	add_child(_rover)
	var body := Node3D.new()
	body.name = "Body"
	_rover.add_child(body)
	if model != null:
		body.add_child(model)
		var box: AABB = _bounds(model, body)
		var scale_value: float = float(_rover_spec.get("height_m", 1.0)) / maxf(box.size.y, 0.01)
		model.position = Vector3(-(box.position.x + box.size.x * 0.5), -box.position.y, -(box.position.z + box.size.z * 0.5))
		body.scale = Vector3.ONE * scale_value
		body.rotation.y = deg_to_rad(float(_rover_spec.get("yaw_offset_deg", 0.0)))
	else:
		var hull := MeshInstance3D.new()
		var hull_mesh := BoxMesh.new()
		hull_mesh.size = Vector3(0.7, 0.5, 0.9)
		hull.mesh = hull_mesh
		hull.position.y = 0.45
		hull.material_override = _flat(Color("#d9dde3"))
		body.add_child(hull)
	_rover_eye = MeshInstance3D.new()
	var eye_mesh := SphereMesh.new()
	eye_mesh.radius = 0.045
	eye_mesh.height = 0.09
	_rover_eye.mesh = eye_mesh
	_rover_eye_material = _flat(Color("#ffb347"), 1.0)
	_rover_eye.material_override = _rover_eye_material
	var eye_pos: Variant = _rover_spec.get("eye_offset", [0.0, 1.02, 0.12])
	_rover_eye.position = _vec3(eye_pos, Vector3(0.0, 1.02, 0.12))
	_rover.add_child(_rover_eye)
	_rover_state = {"yaw": NAN}
	_rover.visible = false


func _bounds(root: Node3D, relative_to: Node) -> AABB:
	var result := AABB()
	var first := true
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var xform := Transform3D.IDENTITY
			var current: Node = node
			while current != null and current != relative_to:
				if current is Node3D:
					xform = (current as Node3D).transform * xform
				current = current.get_parent()
			var box: AABB = xform * (node as MeshInstance3D).mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result


# ---------------------------------------------------------- per frame


func _apply_frame(frame_index: int) -> void:
	var shot: Dictionary = _find_shot(frame_index)
	var shot_frame: int = frame_index - int(shot.get("from", 0))
	var dt: float = 1.0 / float(_fps)
	for actor_id: Variant in _actors.keys():
		_apply_actor(str(actor_id), frame_index, dt)
	_apply_sensor(frame_index)
	_apply_rover(frame_index)
	_apply_camera(shot, shot_frame, frame_index)
	_update_captions(frame_index)


func _apply_actor(actor_id: String, frame_index: int, dt: float) -> void:
	var actor: Node3D = _actors[actor_id]
	var spec: Dictionary = _actor_specs[actor_id]
	var state: Dictionary = _actor_state[actor_id]
	var path: Dictionary = _path_state(spec, frame_index)
	var pos2: Vector2 = path.pos
	actor.position = Vector3(pos2.x, 0.0, pos2.y)
	actor.visible = _visible_at(spec, frame_index)
	var desired: float = _desired_yaw(spec, path, frame_index, actor.position)
	state["yaw"] = _turn_toward(float(state.get("yaw", NAN)), desired, dt)
	actor.rotation = Vector3(0.0, float(state["yaw"]), 0.0)

	var clip := "idle"
	var key := "idle"
	var speed := 1.0
	var entry: Dictionary = _active_entry(spec.get("clips", []), frame_index)
	if not entry.is_empty():
		clip = str(entry.get("clip", "idle"))
		key = "%s@%d" % [clip, int(entry.get("from", 0))]
		speed = float(entry.get("speed", 1.0))
	var moving: bool = float(path.speed) > 0.04
	if moving:
		clip = "walk"
		key = "walk"
		var natural: float = maxf(float(actor.get("natural_walk_speed")), 0.1)
		speed = clampf(float(path.speed) / natural, 0.15, 2.2)
	elif clip == "walk":
		clip = "idle"
		key = "idle"
	actor.call("update_animation", clip, key, speed, dt, 0.3 if clip == "walk" or key == "idle" else 0.25)

	var nod_weight := 0.0
	var nod_phase := 0.0
	var nods: Array[Dictionary] = _dict_array(spec.get("nods", []))
	if clip == "nod" and not entry.is_empty():
		nods.append(entry)
	for nod: Dictionary in nods:
		var a: int = int(nod.get("from", 0))
		var b: int = int(nod.get("to", a))
		if frame_index >= a and frame_index < b:
			var u: float = float(frame_index - a) / float(maxi(b - a, 1))
			nod_weight = maxf(nod_weight, sin(u * PI))
			nod_phase = float(frame_index - a) / float(_fps) * float(nod.get("rate", 1.7))
	var reach_target := Vector3.ZERO
	var reach_weight := 0.0
	var reach: Dictionary = _active_entry(spec.get("reaches", []), frame_index)
	if not reach.is_empty():
		var partner: String = str(reach.get("target", ""))
		var a_f: int = int(reach.get("from", 0))
		var b_f: int = int(reach.get("to", a_f + 1))
		var ramp: float = maxf(float(reach.get("ramp_s", 0.4)) * float(_fps), 1.0)
		var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
		reach_weight = w * w * (3.0 - 2.0 * w)
		var meet: Vector3 = actor.position
		if _actors.has(partner):
			meet = (actor.position + (_actors[partner] as Node3D).position) * 0.5
		reach_target = Vector3(meet.x, float(reach.get("height", 1.05)), meet.z)
	actor.call("apply_overlays", nod_phase, nod_weight, reach_target, reach_weight)


func _visible_at(spec: Dictionary, frame_index: int) -> bool:
	var entry: Dictionary = _active_entry(spec.get("visibility", []), frame_index)
	if entry.is_empty():
		return true
	return bool(entry.get("visible", true))


func _apply_sensor(frame_index: int) -> void:
	if _sensor == null:
		return
	# A handoff window swaps hands on the frame the two hands stop closing in,
	# or at the window's last frame if they never come within max_gap.
	var owner: String = _sensor_owner
	for entry: Dictionary in _dict_array(_sensor_spec.get("owners", [])):
		var actor_id: String = str(entry.get("actor", ""))
		var window_value: Variant = entry.get("handoff_window", [])
		var window: Array = window_value as Array if window_value is Array else []
		if window.size() != 2:
			if frame_index < int(entry.get("from", 0)):
				break
			owner = actor_id
			continue
		if frame_index < int(window[0]):
			break
		if frame_index > int(window[1]) or _sensor_owner == actor_id or _sensor_owner == "":
			owner = actor_id
			continue
		if not _actors.has(_sensor_owner) or not _actors.has(actor_id):
			owner = actor_id
			continue
		var a: Vector3 = _actors[_sensor_owner].call("hand_world_position")
		var b: Vector3 = _actors[actor_id].call("hand_world_position")
		var distance: float = a.distance_to(b)
		var close_enough: bool = minf(distance, _handoff_prev_distance) < float(entry.get("max_gap", 0.3))
		var turning_back: bool = distance > _handoff_prev_distance + 0.001
		if (close_enough and turning_back) or frame_index >= int(window[1]):
			_handoff_log.append({
				"frame": frame_index,
				"from": _sensor_owner,
				"to": actor_id,
				"hand_gap_m": snappedf(minf(distance, _handoff_prev_distance), 0.001),
			})
			_handoff_prev_distance = INF
			owner = actor_id
		else:
			_handoff_prev_distance = distance
		break
	if owner == "" or not _actors.has(owner):
		_sensor.visible = false
		return
	if owner != _sensor_owner:
		if _sensor_owner != "" and _actors.has(_sensor_owner):
			_actors[_sensor_owner].set("holding", false)
		var socket: Node3D = _actors[owner].call("get_hand_socket")
		if socket != null:
			if _sensor.get_parent() != null:
				_sensor.get_parent().remove_child(_sensor)
			socket.add_child(_sensor)
			_sensor.transform = Transform3D.IDENTITY
		_actors[owner].set("holding", true)
		_sensor_owner = owner
	_sensor.visible = true


func _apply_rover(frame_index: int) -> void:
	if _rover == null:
		return
	var path: Dictionary = _path_state(_rover_spec, frame_index)
	var pos2: Vector2 = path.pos
	var moving: bool = float(path.speed) > 0.02
	var t: float = float(frame_index) / float(_fps)
	_rover.position = Vector3(pos2.x, 0.012 * sin(t * 23.0) if moving else 0.0, pos2.y)
	var desired: float = _desired_yaw(_rover_spec, path, frame_index, _rover.position)
	_rover_state["yaw"] = _turn_toward(float(_rover_state.get("yaw", NAN)), desired, 1.0 / float(_fps))
	_rover.rotation = Vector3(0.0, float(_rover_state["yaw"]), 0.0)
	_rover.visible = _visible_at(_rover_spec, frame_index)
	var speaking := false
	for entry: Dictionary in _speech:
		if str(entry.get("actor", "")) == str(_rover_spec.get("id", "at1")) and frame_index >= int(entry.get("from", 0)) and frame_index < int(entry.get("to", 0)):
			speaking = true
	_rover_eye_material.emission_energy_multiplier = (1.6 + 1.4 * absf(sin(t * 11.0))) if speaking else 0.9


# --------------------------------------------------------------- paths


## Position and speed (m/s) on a ``start`` + ``moves`` path. Each move runs
## a polyline with a trapezoid speed profile so feet accelerate and settle.
func _path_state(spec: Dictionary, frame_index: int) -> Dictionary:
	var start_value: Variant = spec.get("start", {})
	var start: Dictionary = start_value as Dictionary if start_value is Dictionary else {}
	var pos: Vector2 = _vec2(start.get("pos", [0, 0]))
	var heading := Vector2.ZERO
	for move: Dictionary in _dict_array(spec.get("moves", [])):
		var f0: int = int(move.get("from", 0))
		var f1: int = maxi(int(move.get("to", f0 + 1)), f0 + 1)
		if frame_index < f0:
			break
		var points: Array[Vector2] = [pos]
		for p: Variant in move.get("points", []):
			points.append(_vec2(p))
		var lengths: Array[float] = []
		var total := 0.0
		for i in range(1, points.size()):
			var seg: float = points[i - 1].distance_to(points[i])
			lengths.append(seg)
			total += seg
		if total < 0.0001:
			continue
		if frame_index >= f1:
			pos = points[points.size() - 1]
			heading = (points[points.size() - 1] - points[points.size() - 2]).normalized()
			continue
		var duration: float = float(f1 - f0) / float(_fps)
		var u: float = float(frame_index - f0) / float(f1 - f0)
		var ramp: float = clampf(float(move.get("ramp_s", 0.45)) / duration, 0.02, 0.45)
		var vmax: float = 1.0 / (1.0 - ramp)
		var s := 0.0
		var ds := 0.0
		if u < ramp:
			s = 0.5 * vmax * u * u / ramp
			ds = vmax * u / ramp
		elif u < 1.0 - ramp:
			s = 0.5 * vmax * ramp + vmax * (u - ramp)
			ds = vmax
		else:
			s = 1.0 - 0.5 * vmax * (1.0 - u) * (1.0 - u) / ramp
			ds = vmax * (1.0 - u) / ramp
		var along: float = clampf(s, 0.0, 1.0) * total
		for i in lengths.size():
			if along <= lengths[i] or i == lengths.size() - 1:
				var k: float = clampf(along / maxf(lengths[i], 0.0001), 0.0, 1.0)
				heading = (points[i + 1] - points[i]).normalized()
				return {"pos": points[i].lerp(points[i + 1], k), "speed": total * ds / duration, "heading": heading}
			along -= lengths[i]
	return {"pos": pos, "speed": 0.0, "heading": heading}


func _desired_yaw(spec: Dictionary, path: Dictionary, frame_index: int, position: Vector3) -> float:
	var heading: Vector2 = path.get("heading", Vector2.ZERO)
	if float(path.speed) > 0.04 and heading.length_squared() > 0.5:
		return atan2(heading.x, heading.y)
	var entry: Dictionary = _active_entry(spec.get("facing", []), frame_index)
	if entry.has("target"):
		var target: String = str(entry["target"])
		var other: Vector3 = Vector3.INF
		if _actors.has(target):
			other = (_actors[target] as Node3D).position
		elif _rover != null and target == str(_rover_spec.get("id", "at1")):
			other = _rover.position
		if other != Vector3.INF:
			var d := Vector2(other.x - position.x, other.z - position.z)
			if d.length_squared() > 0.0004:
				return atan2(d.x, d.y)
	if entry.has("point"):
		var p: Vector2 = _vec2(entry["point"])
		var d2 := Vector2(p.x - position.x, p.y - position.z)
		if d2.length_squared() > 0.0004:
			return atan2(d2.x, d2.y)
	if entry.has("yaw_deg"):
		return deg_to_rad(float(entry["yaw_deg"]))
	var start: Dictionary = spec.get("start", {}) as Dictionary if spec.get("start", {}) is Dictionary else {}
	return deg_to_rad(float(start.get("yaw_deg", 0.0)))


func _turn_toward(current: float, desired: float, dt: float) -> float:
	if is_nan(current):
		return desired
	var diff: float = wrapf(desired - current, -PI, PI)
	var max_step: float = deg_to_rad(TURN_RATE_DEG) * dt
	var step: float = clampf(diff * 0.35, -max_step, max_step)
	if absf(diff) < 0.002:
		return desired
	return current + step


func _active_entry(list_value: Variant, frame_index: int) -> Dictionary:
	var best: Dictionary = {}
	var best_from := -1
	for entry: Dictionary in _dict_array(list_value):
		var a: int = int(entry.get("from", 0))
		var has_to: bool = entry.has("to")
		var b: int = int(entry.get("to", 0))
		if frame_index < a or (has_to and frame_index >= b):
			continue
		if a >= best_from:
			best = entry
			best_from = a
	return best


# -------------------------------------------------------------- camera


func _apply_camera(shot: Dictionary, shot_frame: int, frame_index: int) -> void:
	var cut: Dictionary = _find_cut(shot, shot_frame)
	var cut_key: String = "%s@%d" % [str(shot.get("id", "")), int(cut.get("from", 0))]
	var snap: bool = cut_key != _active_cut_key
	_active_cut_key = cut_key
	var mode: String = str(cut.get("mode", "static"))
	var a: int = int(cut.get("from", 0))
	var b: int = maxi(int(cut.get("to", a + 1)), a + 1)
	var u: float = clampf(float(shot_frame - a) / float(maxi(b - a - 1, 1)), 0.0, 1.0)
	var eased: float = u * u * (3.0 - 2.0 * u)
	var pos: Vector3 = _vec3(cut.get("pos", null), Vector3(0.0, 1.6, 4.0))
	var look: Vector3 = _vec3(cut.get("look_at", null), Vector3(0.0, 1.2, 0.0))
	var fov: float = float(cut.get("fov", 50.0))
	var first_person := ""
	match mode:
		"dolly":
			pos = pos.lerp(_vec3(cut.get("pos_to", null), pos), eased)
			look = look.lerp(_vec3(cut.get("look_to", null), look), eased)
			fov = lerpf(fov, float(cut.get("fov_to", fov)), eased)
		"follow":
			var actor_id: String = str(cut.get("actor", ""))
			if _actors.has(actor_id):
				var anchor: Vector3 = (_actors[actor_id] as Node3D).position
				var offset: Vector3 = _vec3(cut.get("offset", null), Vector3(0.6, 1.9, 3.0))
				offset = offset.lerp(_vec3(cut.get("offset_to", null), offset), eased)
				var look_offset: Vector3 = _vec3(cut.get("look_offset", null), Vector3(0.0, 1.2, -1.5))
				look_offset = look_offset.lerp(_vec3(cut.get("look_offset_to", null), look_offset), eased)
				var target_pos: Vector3 = _bay.call("clamp_camera", anchor + offset)
				var target_look: Vector3 = anchor + look_offset
				var smooth: float = clampf(float(cut.get("smooth", 0.18)), 0.01, 1.0)
				if snap or not _camera_state.has("pos"):
					pos = target_pos
					look = target_look
				else:
					pos = (_camera_state["pos"] as Vector3).lerp(target_pos, smooth)
					look = (_camera_state["look"] as Vector3).lerp(target_look, smooth)
		"pov":
			first_person = str(cut.get("actor", ""))
			if _actors.has(first_person):
				var actor: Node3D = _actors[first_person]
				var eye: float = float(cut.get("eye_height", float(actor.get("height_m")) * 0.94))
				var forward := Vector3(sin(actor.rotation.y), 0.0, cos(actor.rotation.y))
				pos = actor.position + Vector3(0.0, eye, 0.0) + forward * 0.1
				look = look.lerp(_vec3(cut.get("look_to", null), look), eased)
				var t: float = float(frame_index) / float(_fps)
				var sway: float = float(cut.get("sway", 0.006))
				pos += Vector3(sin(t * 1.3) * sway, sin(t * 2.1) * sway * 0.6, 0.0)
	pos = _bay.call("clamp_camera", pos)
	_camera_state["pos"] = pos
	_camera_state["look"] = look
	_camera.fov = fov
	if pos.distance_to(look) > 0.01:
		_camera.look_at_from_position(pos, look, Vector3.UP)
	else:
		_camera.position = pos
	_camera.rotate_object_local(Vector3.BACK, deg_to_rad(float(cut.get("roll_deg", 0.0))))
	_set_first_person(first_person)


func _set_first_person(actor_id: String) -> void:
	for key: Variant in _actors.keys():
		var hidden: bool = str(key) == actor_id
		if bool(_actors[key].call("is_first_person_hidden")) != hidden:
			_actors[key].call("set_first_person_hidden", hidden)
	_camera.cull_mask = CULL_FIRST_PERSON if actor_id != "" else CULL_ALL
	if _sensor != null:
		var hide_real: bool = actor_id != "" and actor_id == _sensor_owner
		_set_layers(_sensor, 2 if hide_real else 1)
	if actor_id == "":
		if _viewmodel != null:
			_viewmodel.visible = false
		if _viewmodel_sensor != null:
			_viewmodel_sensor.visible = false
		return
	if _viewmodel == null or _viewmodel_actor != actor_id:
		if _viewmodel != null:
			_viewmodel.queue_free()
		if _viewmodel_sensor != null:
			_viewmodel_sensor.queue_free()
		_viewmodel = _actors[actor_id].call("build_viewmodel")
		_camera.add_child(_viewmodel)
		_viewmodel_actor = actor_id
		var vm_cfg: Dictionary = _stage_data.get("viewmodel", {}) as Dictionary if _stage_data.get("viewmodel", {}) is Dictionary else {}
		_viewmodel_sensor = _make_sensor(_vec3(vm_cfg.get("sensor_rotation_deg", null), Vector3.ZERO))
		_viewmodel_sensor.name = "ViewmodelSensor"
		_camera.add_child(_viewmodel_sensor)
		_place_viewmodel()
	_viewmodel.visible = true
	_viewmodel_sensor.visible = _sensor != null and _sensor_owner == actor_id


func _place_viewmodel() -> void:
	var vm: Dictionary = _stage_data.get("viewmodel", {}) as Dictionary if _stage_data.get("viewmodel", {}) is Dictionary else {}
	var hand: Vector3 = _vec3(vm.get("hand", null), Vector3(0.15, -0.17, -0.38))
	var arm_dir: Vector3 = _vec3(vm.get("arm_dir", null), Vector3(-0.32, 0.42, -0.85)).normalized()
	var palm_hint: Vector3 = _vec3(vm.get("palm_hint", null), Vector3(-0.55, 0.25, 0.8))
	var x_axis: Vector3 = arm_dir.cross(palm_hint).normalized()
	var z_axis: Vector3 = x_axis.cross(arm_dir).normalized()
	var forearm: Node3D = _viewmodel.get_node("Forearm") as Node3D
	forearm.transform = Transform3D(Basis(x_axis, arm_dir, z_axis), hand - arm_dir * 0.05)
	var up: Vector3 = _vec3(vm.get("sensor_up", null), Vector3(0.12, 1.0, 0.42)).normalized()
	var toward: Vector3 = Vector3(0.0, 0.0, 1.0)
	var sx: Vector3 = up.cross(toward).normalized()
	var sz: Vector3 = sx.cross(up).normalized()
	var sensor_basis := Basis(sx, up, sz) * Basis.from_scale(Vector3.ONE * float(vm.get("sensor_scale", VIEWMODEL_SENSOR_SCALE)))
	_viewmodel_sensor.transform = Transform3D(sensor_basis, hand + _vec3(vm.get("sensor_offset", null), Vector3(-0.01, 0.0, 0.01)))
	_set_layers(_viewmodel, 1)
	_set_layers(_viewmodel_sensor, 1)


func _set_layers(root: Node, layer: int) -> void:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is VisualInstance3D:
			(node as VisualInstance3D).layers = layer
		for child: Node in node.get_children():
			stack.append(child)


func _find_shot(frame_index: int) -> Dictionary:
	for shot: Dictionary in _shots:
		var a: int = int(shot.get("from", 0))
		if frame_index >= a and frame_index < a + int(shot.get("frames", 0)):
			return shot
	return _shots[_shots.size() - 1] if not _shots.is_empty() else {}


func _find_cut(shot: Dictionary, shot_frame: int) -> Dictionary:
	var latest: Dictionary = {}
	for cut: Dictionary in _dict_array(shot.get("cameras", [])):
		var a: int = int(cut.get("from", 0))
		if shot_frame >= a and shot_frame < int(cut.get("to", a + 1)):
			return cut
		if a <= shot_frame:
			latest = cut
	return latest


# ------------------------------------------------------------ captions


func _load_font() -> void:
	if _cjk_font_path == "" or not FileAccess.file_exists(_cjk_font_path):
		return
	var font := FontFile.new()
	if font.load_dynamic_font(_cjk_font_path) == OK:
		_font = font


func _build_captions() -> void:
	_caption_layer = CanvasLayer.new()
	_caption_layer.name = "Captions"
	_caption_layer.layer = 70
	add_child(_caption_layer)
	_caption_root = Control.new()
	_caption_root.size = VIEW_SIZE
	_caption_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_caption_layer.add_child(_caption_root)
	var bar := ColorRect.new()
	bar.color = Color(0.015, 0.02, 0.035, 0.62)
	bar.position = Vector2(0.0, 634.0)
	bar.size = Vector2(1280.0, 86.0)
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_caption_root.add_child(bar)
	_caption_label = Label.new()
	_caption_label.position = Vector2(48.0, 638.0)
	_caption_label.size = Vector2(1184.0, 78.0)
	_caption_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_caption_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_caption_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_caption_label.add_theme_font_size_override("font_size", 36)
	_caption_label.add_theme_color_override("font_color", Color(1.0, 0.97, 0.91))
	_caption_label.add_theme_color_override("font_shadow_color", Color(0.0, 0.0, 0.0, 0.95))
	_caption_label.add_theme_constant_override("shadow_offset_x", 2)
	_caption_label.add_theme_constant_override("shadow_offset_y", 2)
	if _font != null:
		_caption_label.add_theme_font_override("font", _font)
	_caption_root.add_child(_caption_label)
	_caption_root.visible = false


func _update_captions(frame_index: int) -> void:
	var text := ""
	for caption: Dictionary in _captions:
		var a: int = int(caption.get("from", 0))
		var b: int = maxi(int(caption.get("to", a)), a + 1)
		if frame_index >= a and frame_index < b:
			text = str(caption.get("text", ""))
	_caption_label.text = text
	_caption_root.visible = text != ""


# --------------------------------------------------------------- output


func _capture_sample(frame_index: int) -> void:
	var texture: ViewportTexture = get_viewport().get_texture()
	var image: Image = texture.get_image() if texture != null else null
	if image == null or image.is_empty():
		push_error("Stage3D: viewport capture failed at frame %d" % frame_index)
		return
	var file_path: String = _sample_dir.path_join("frame_%06d.png" % frame_index)
	var save_error: Error = image.save_png(file_path)
	if save_error != OK:
		push_error("Stage3D: save_png failed for %s: %s" % [file_path, error_string(save_error)])
		return
	_sample_files.append(file_path)
	print("STAGE_SAMPLE frame=%d file=%s" % [frame_index, file_path])


func _finish() -> void:
	set_process(false)
	var post_draw := Callable(self, "_on_frame_post_draw")
	if RenderingServer.frame_post_draw.is_connected(post_draw):
		RenderingServer.frame_post_draw.disconnect(post_draw)
	var elapsed_sec: float = maxf(float(Time.get_ticks_usec() - _start_usec) / 1000000.0, 0.000001)
	var actors_report := {}
	for key: Variant in _actors.keys():
		var actor: Node3D = _actors[key]
		actors_report[str(key)] = {
			"source": str(actor.get("source_kind")),
			"clips": actor.get("clip_report"),
			"natural_walk_speed": snappedf(float(actor.get("natural_walk_speed")), 0.001),
		}
	var result := {
		"total_frames": _total_frames,
		"elapsed_sec": elapsed_sec,
		"fps": _fps,
		"render_fps": float(_total_frames) / elapsed_sec,
		"loaded_actors": _actors.size(),
		"actors": actors_report,
		"props_loaded": int(_bay.get("props_loaded")),
		"props_failed": int(_bay.get("props_failed")),
		"rover_loaded": _rover != null,
		"handoffs": _handoff_log,
		"sample_files": _sample_files,
	}
	_write_result(result)
	print("STAGE_RESULT %s" % JSON.stringify(result))
	_character_script.call("clear_cache")
	get_tree().quit(0)


func _abort_startup(message: String, exit_code: int) -> void:
	_finishing = true
	push_error("Stage3D: %s" % message)
	_write_result({
		"total_frames": _total_frames,
		"elapsed_sec": 0.0,
		"fps": _fps,
		"render_fps": 0.0,
		"loaded_actors": _actors.size(),
		"sample_files": _sample_files,
		"error": message,
	})
	get_tree().quit(exit_code)


func _write_result(payload: Dictionary) -> void:
	if _result_json_path == "":
		return
	var parent_dir: String = _result_json_path.get_base_dir()
	if parent_dir != "" and not _ensure_directory(parent_dir):
		push_error("Stage3D: result directory could not be created: %s" % parent_dir)
		return
	var handle := FileAccess.open(_result_json_path, FileAccess.WRITE)
	if handle == null:
		push_error("Stage3D: result JSON open failed: %s" % error_string(FileAccess.get_open_error()))
		return
	handle.store_string(JSON.stringify(payload, "\t") + "\n")
	handle.close()


# -------------------------------------------------------------- helpers


func _parse_sample_frames(value: String) -> void:
	_sample_frames.clear()
	_sample_frame_set.clear()
	for token_value: String in value.split(",", false):
		var token: String = token_value.strip_edges()
		if not token.is_valid_int():
			continue
		var sample_frame: int = token.to_int()
		if sample_frame < 0 or _sample_frame_set.has(sample_frame):
			continue
		_sample_frames.append(sample_frame)
		_sample_frame_set[sample_frame] = true
	_sample_frames.sort()


func _dict_array(value: Variant) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	if value is Array:
		for item: Variant in value as Array:
			if item is Dictionary:
				result.append(item as Dictionary)
	return result


func _vec2(value: Variant) -> Vector2:
	if value is Array and (value as Array).size() >= 2:
		return Vector2(float((value as Array)[0]), float((value as Array)[1]))
	return Vector2.ZERO


func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback


func _resolve_input_path(raw_path: String) -> String:
	var normalized: String = raw_path.strip_edges().replace("\\", "/")
	if normalized == "":
		return ""
	if normalized.begins_with("res://") or normalized.begins_with("user://") or normalized.is_absolute_path():
		return normalized.simplify_path()
	var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
	var project_candidate: String = project_dir.path_join(normalized).simplify_path()
	if FileAccess.file_exists(project_candidate):
		return project_candidate
	return project_dir.get_base_dir().path_join(normalized).simplify_path()


func _resolve_asset_path(raw_path: String) -> String:
	var normalized: String = raw_path.replace("\\", "/")
	if normalized.begins_with("res://") or normalized.begins_with("user://") or normalized.is_absolute_path():
		return normalized.simplify_path()
	return _base_dir.path_join(normalized).simplify_path()


func _resolve_output_path(raw_path: String) -> String:
	var normalized: String = raw_path.strip_edges().replace("\\", "/")
	if normalized == "":
		return ""
	if normalized.begins_with("res://") or normalized.begins_with("user://"):
		return ProjectSettings.globalize_path(normalized).simplify_path()
	if normalized.is_absolute_path():
		return normalized.simplify_path()
	var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
	return project_dir.get_base_dir().path_join(normalized).simplify_path()


func _ensure_directory(path: String) -> bool:
	if path == "":
		return false
	if DirAccess.dir_exists_absolute(path):
		return true
	return DirAccess.make_dir_recursive_absolute(path) == OK
