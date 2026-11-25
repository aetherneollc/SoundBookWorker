extends Node3D

## Deterministic TimelineExecutor for stage3d.json.
##
## Environment contract:
##   STAGE_JSON           path to stage3d.json
##   STAGE_RESULT_JSON    path to render summary JSON
##   STAGE_TELEMETRY_JSON optional path to per-frame qc_telemetry.json
##   STAGE_CJK_FONT       optional TTF/TTC font for CJK subtitles
##   STAGE_SAMPLE_FRAMES  optional comma-separated frame indices for still PNGs
##   STAGE_SAMPLE_DIR     optional output directory for sample stills
##
## Integrates:
##   - Character3D (65-bone direct UAL playback, multi-gait, 5-finger posing,
##     Foot grounding, FacialAnimationController BlendShapes/emotions/lip-sync)
##   - PropInteractionSystem (multi-prop sockets, smooth handoff, surface rest)
##   - Rover3D (grounded wheel spin = dist/radius, turret tracking, flush LED)
##   - Bay / Scene templates + LightingProfile (time-driven updates & DoF)

const PropSystemScript := preload("res://player/prop_system.gd")
const RoverScript := preload("res://player/rover3d.gd")

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
var _prop_system: Node3D
var _rover: Node3D
var _rover_spec: Dictionary = {}
var _rover_state: Dictionary = {}
var _sensor_spec: Dictionary = {}

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
var _telemetry_json_path := ""
var _telemetry_frames: Array[Dictionary] = []

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
	print("STAGE3D_PLAYER ready frames=%d fps=%d actors=%d scene=%s lighting=%s" % [
		_total_frames, _fps, _actors.size(),
		str(_bay.get("scene_type")), str(_bay.get("lighting_profile_name"))
	])


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
	_telemetry_json_path = _resolve_output_path(OS.get_environment("STAGE_TELEMETRY_JSON"))
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
	_bay.name = "SceneSet"
	add_child(_bay)
	var set_cfg: Dictionary = _stage_data.get("set", {}) as Dictionary if _stage_data.get("set", {}) is Dictionary else {}
	var resolved_set: Dictionary = _resolve_paths(set_cfg) as Dictionary
	if _stage_data.has("lighting_profile") and not resolved_set.has("lighting_profile"):
		resolved_set["lighting_profile"] = _stage_data["lighting_profile"]
	if _stage_data.has("scene_type") and not resolved_set.has("scene_type"):
		resolved_set["scene_type"] = _stage_data["scene_type"]
	_bay.call("build", resolved_set, _character_script)

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

	_build_props()
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
			if item is String and (k == "model" or k == "image" or k == "meta" or k.ends_with("library") or k.ends_with("_texture") or k.ends_with("_model")):
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


func _build_props() -> void:
	_prop_system = PropSystemScript.new() as Node3D
	_prop_system.name = "PropInteractionSystem"
	add_child(_prop_system)
	var props_value: Variant = _stage_data.get("props", {})
	if props_value is Dictionary and not (props_value as Dictionary).is_empty():
		var resolved: Dictionary = _resolve_paths(props_value) as Dictionary
		if resolved.has("sensor") and resolved["sensor"] is Dictionary:
			_sensor_spec = resolved["sensor"] as Dictionary
		_prop_system.call("setup", resolved, _character_script)


func _build_rover() -> void:
	var rover_value: Variant = _stage_data.get("rover", {})
	if not rover_value is Dictionary or (rover_value as Dictionary).is_empty():
		return
	_rover_spec = _resolve_paths(rover_value) as Dictionary
	_rover = RoverScript.new() as Node3D
	_rover.name = "Rover_%s" % str(_rover_spec.get("id", "AT1"))
	add_child(_rover)
	_rover.call("setup", _rover_spec, _character_script)
	_rover_state = {"yaw": NAN}
	_rover.visible = false


## Builds a viewmodel sensor copy for optional first-person POV shots.
func _make_viewmodel_sensor(rotation_deg: Vector3) -> Node3D:
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


# ---------------------------------------------------------- per frame


func _apply_frame(frame_index: int) -> void:
	var shot: Dictionary = _find_shot(frame_index)
	var shot_frame: int = frame_index - int(shot.get("from", 0))
	var dt: float = 1.0 / float(_fps)
	var time_sec: float = float(frame_index) / float(_fps)

	# 0. Update time-driven lighting (subtle alert pulses, ambient fill).
	if _bay != null and _bay.has_method("update_lighting"):
		_bay.call("update_lighting", time_sec)

	# 1. Identify active speaker on this frame (for listener micro-reactions & rover gaze).
	var active_speaker: Dictionary = _active_speaker_at(frame_index)

	# 2. First pass on actors: transform, gait, clip playback, facial expressions & emotions.
	var actor_paths := {}
	for actor_id_v: Variant in _actors.keys():
		var actor_id: String = str(actor_id_v)
		actor_paths[actor_id] = _apply_actor_base(actor_id, frame_index, dt, active_speaker)

	# 3. Update rover position, exact wheel spin (dist/radius), turret target, status LED.
	_apply_rover(frame_index, dt, active_speaker)

	# 4. Evaluate PropInteractionSystem (ownership, handoffs, reach & gaze requests).
	var prop_reach_requests: Dictionary = {}
	var prop_gaze_requests: Dictionary = {}
	if _prop_system != null:
		_prop_system.call("update_frame", frame_index, _fps, _actors, dt)
		prop_reach_requests = _prop_system.call("get_actor_reach_requests")
		prop_gaze_requests = _prop_system.call("get_actor_gaze_requests")

	# 5. Second pass on actors: gaze (merging prop gaze + partner gaze) + IK/grip/grounding overlays.
	for actor_id_v: Variant in _actors.keys():
		var actor_id: String = str(actor_id_v)
		_apply_actor_overlays(
			actor_id,
			frame_index,
			dt,
			shot,
			actor_paths.get(actor_id, {}),
			prop_reach_requests.get(actor_id, {}),
			prop_gaze_requests.get(actor_id, {})
		)

	# 6. Camera & DoF & Captions.
	_apply_camera(shot, shot_frame, frame_index)
	_update_captions(frame_index)

	# 7. Deterministic QC telemetry snapshot.
	if _telemetry_json_path != "":
		_telemetry_frames.append(_capture_telemetry_frame(frame_index, shot))


func _apply_actor_base(actor_id: String, frame_index: int, dt: float, active_speaker: Dictionary) -> Dictionary:
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

	# Resolve per-beat gait profile override (falls back to actor's default_gait).
	var gait: String = str(spec.get("gait", spec.get("gait_profile", "casual")))
	var gait_entry: Dictionary = _active_entry(spec.get("gaits", []), frame_index)
	if not gait_entry.is_empty():
		gait = str(gait_entry.get("gait", gait))
	if path.has("gait") and str(path["gait"]) != "":
		gait = str(path["gait"])
	actor.call("set_gait_profile", gait)

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
		var loco_clip: String = str(actor.call("resolve_locomotion_clip", gait))
		clip = loco_clip
		key = "%s:%s" % [loco_clip, gait]
		var natural: float = maxf(float(actor.call("stride_speed_for", loco_clip)), 0.25)
		speed = clampf(float(path.speed) / natural, 0.25, 2.2)
	elif clip in ["walk", "walk_formal", "crouch_walk", "jog", "sprint"]:
		clip = "idle"
		key = "idle"

	actor.call("update_animation", clip, key, speed, dt, 0.10 if moving else 0.22)

	# Emotions & listener micro-reactions.
	var emo_entry: Dictionary = _active_entry(spec.get("emotions", []), frame_index)
	if not emo_entry.is_empty():
		actor.call(
			"set_emotion",
			str(emo_entry.get("emotion", "neutral")),
			float(emo_entry.get("intensity", 1.0)),
			float(emo_entry.get("transition_s", 0.35))
		)
	elif spec.has("emotion"):
		actor.call("set_emotion", str(spec["emotion"]), float(spec.get("emotion_intensity", 0.85)), 0.4)

	var line: Dictionary = _speech_at(actor_id, frame_index)
	var is_speaking: bool = not line.is_empty()
	var energy := -1.0
	var viseme := ""
	if is_speaking:
		if line.get("energy", null) is Array:
			var env: Array = line["energy"]
			var k: int = frame_index - int(line.get("from", 0))
			if k >= 0 and k < env.size():
				energy = float(env[k])
		if line.get("visemes", null) is Array:
			var vlist: Array = line["visemes"]
			var kv: int = frame_index - int(line.get("from", 0))
			if kv >= 0 and kv < vlist.size():
				viseme = str(vlist[kv])
		if line.has("emotion"):
			actor.call("set_emotion", str(line["emotion"]), float(line.get("emotion_intensity", 1.0)), 0.28)
		actor.call("set_listening", false)
	else:
		var spk_id: String = str(active_speaker.get("actor", ""))
		if spk_id != "" and spk_id != actor_id and actor.visible:
			var spk_pos := Vector3.ZERO
			if _actors.has(spk_id):
				spk_pos = _actors[spk_id].call("eye_world_position")
			elif _rover != null and spk_id == str(_rover_spec.get("id", "at1")):
				spk_pos = _rover.call("get_eye_world_position")
			var spk_energy: float = float(active_speaker.get("energy_now", 0.45))
			var listen_emo: String = str(active_speaker.get("listener_emotion", ""))
			actor.call("set_listening", spk_pos != Vector3.ZERO, spk_pos, listen_emo, spk_energy)
		else:
			actor.call("set_listening", false)

	actor.call("update_face", frame_index, _fps, is_speaking, energy, viseme, dt)
	return path


func _apply_actor_overlays(
	actor_id: String,
	frame_index: int,
	dt: float,
	shot: Dictionary,
	path: Dictionary,
	prop_reach: Dictionary,
	prop_gaze: Dictionary
) -> void:
	var actor: Node3D = _actors[actor_id]
	var spec: Dictionary = _actor_specs[actor_id]

	# Gaze: prop interaction gaze takes priority when active, else partner/path gaze.
	var gaze: Dictionary = _gaze_target(actor_id, spec, path, frame_index, shot)
	if not prop_gaze.is_empty() and float(prop_gaze.get("weight", 0.0)) > 0.05:
		gaze = {"point": prop_gaze["point"], "weight": float(prop_gaze["weight"])}
	actor.call("apply_look_at", gaze.get("point", actor.position), float(gaze.get("weight", 0.0)), dt)

	# Explicit nods.
	var nod_weight := 0.0
	var nod_phase := 0.0
	var entry: Dictionary = _active_entry(spec.get("clips", []), frame_index)
	var nods: Array[Dictionary] = _dict_array(spec.get("nods", []))
	if str(entry.get("clip", "")) == "nod" and not entry.is_empty():
		nods.append(entry)
	for nod: Dictionary in nods:
		var a: int = int(nod.get("from", 0))
		var b: int = int(nod.get("to", a))
		if frame_index >= a and frame_index < b:
			var u: float = float(frame_index - a) / float(maxi(b - a, 1))
			nod_weight = maxf(nod_weight, sin(u * PI))
			nod_phase = float(frame_index - a) / float(_fps) * float(nod.get("rate", 1.7))

	# Reach IK: merge PropInteractionSystem requests with legacy actor reaches[].
	var reach_target := Vector3.ZERO
	var reach_weight := 0.0
	var reach_side := "right"
	if not prop_reach.is_empty() and float(prop_reach.get("weight", 0.0)) > 0.001:
		reach_target = prop_reach["target"] as Vector3
		reach_weight = float(prop_reach["weight"])
		reach_side = str(prop_reach.get("side", "right"))
	else:
		var reach: Dictionary = _active_entry(spec.get("reaches", []), frame_index)
		if not reach.is_empty():
			var partner: String = str(reach.get("target", ""))
			var a_f: int = int(reach.get("from", 0))
			var b_f: int = int(reach.get("to", a_f + 1))
			var ramp: float = maxf(float(reach.get("ramp_s", 0.4)) * float(_fps), 1.0)
			var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
			reach_weight = w * w * (3.0 - 2.0 * w)
			reach_side = str(reach.get("side", "right"))
			var meet: Vector3 = actor.position
			if _actors.has(partner):
				meet = (actor.position + (_actors[partner] as Node3D).position) * 0.5
			reach_target = Vector3(meet.x, float(reach.get("height", 1.05)), meet.z)

	actor.call("apply_overlays", nod_phase, nod_weight, reach_target, reach_weight, reach_side)


func _active_speaker_at(frame_index: int) -> Dictionary:
	for entry: Dictionary in _speech:
		var a: int = int(entry.get("from", 0))
		var b: int = int(entry.get("to", 0))
		if frame_index >= a and frame_index < b:
			var out: Dictionary = entry.duplicate()
			var e := 0.5
			if entry.get("energy", null) is Array:
				var env: Array = entry["energy"]
				var k: int = frame_index - a
				if k >= 0 and k < env.size():
					e = float(env[k])
			out["energy_now"] = e
			return out
	return {}


func _speech_at(actor_id: String, frame_index: int) -> Dictionary:
	for entry: Dictionary in _speech:
		if str(entry.get("actor", "")) == actor_id and frame_index >= int(entry.get("from", 0)) and frame_index < int(entry.get("to", 0)):
			return entry
	return {}


func _gaze_target(actor_id: String, spec: Dictionary, path: Dictionary, frame_index: int, shot: Dictionary) -> Dictionary:
	var actor: Node3D = _actors[actor_id]
	if bool(actor.call("is_first_person_hidden")) or not actor.visible:
		return {"weight": 0.0}
	var eye_height: float = float(actor.get("height_m")) * 0.93
	var heading: Vector2 = path.get("heading", Vector2.ZERO)
	if float(path.get("speed", 0.0)) > 0.04 and heading.length_squared() > 0.5:
		var ahead: Vector3 = actor.position + Vector3(heading.x, 0.0, heading.y) * 3.0
		return {"point": Vector3(ahead.x, eye_height - 0.16, ahead.z), "weight": 0.55}
	var point := Vector3.INF
	var entry: Dictionary = _active_entry(spec.get("facing", []), frame_index)
	if entry.has("target"):
		var target: String = str(entry["target"])
		if _actors.has(target) and target != actor_id:
			point = _actors[target].call("eye_world_position")
		elif _rover != null and target == str(_rover_spec.get("id", "at1")) and _rover.visible:
			point = _rover.call("get_eye_world_position")
	elif entry.has("point"):
		var p: Vector2 = _vec2(entry["point"])
		point = Vector3(p.x, eye_height, p.y)
	if point == Vector3.INF:
		return {"weight": 0.0}
	var weight := 0.8
	var shot_type: String = str(shot.get("type", shot.get("shot_type", ""))).to_lower()
	if (shot_type == "closeup" or shot_type == "close_up" or shot_type == "extreme_close_up") and _camera_state.has("pos"):
		point = point.lerp(_camera_state["pos"] as Vector3, 0.25)
		weight = 0.85
	return {"point": point, "weight": weight}


func _visible_at(spec: Dictionary, frame_index: int) -> bool:
	var entry: Dictionary = _active_entry(spec.get("visibility", []), frame_index)
	if entry.is_empty():
		return true
	return bool(entry.get("visible", true))


func _apply_rover(frame_index: int, dt: float, active_speaker: Dictionary) -> void:
	if _rover == null:
		return
	var path: Dictionary = _path_state(_rover_spec, frame_index)
	var pos2: Vector2 = path.pos
	var world_pos := Vector3(pos2.x, 0.0, pos2.y)
	var desired: float = _desired_yaw(_rover_spec, path, frame_index, world_pos)
	_rover_state["yaw"] = _turn_toward(float(_rover_state.get("yaw", NAN)), desired, dt)
	_rover.visible = _visible_at(_rover_spec, frame_index)

	var rover_id: String = str(_rover_spec.get("id", "at1"))
	var line: Dictionary = _speech_at(rover_id, frame_index)
	var speaking: bool = not line.is_empty()
	var energy: float = float(active_speaker.get("energy_now", 0.5)) if speaking else 0.0

	var turret_target := Vector3.INF
	var face_entry: Dictionary = _active_entry(_rover_spec.get("facing", []), frame_index)
	if face_entry.has("target"):
		var t_id: String = str(face_entry["target"])
		if _actors.has(t_id):
			turret_target = _actors[t_id].call("eye_world_position")
	elif not active_speaker.is_empty():
		var spk: String = str(active_speaker.get("actor", ""))
		if _actors.has(spk):
			turret_target = _actors[spk].call("eye_world_position")

	var status_override := ""
	var st_entry: Dictionary = _active_entry(_rover_spec.get("statuses", []), frame_index)
	if not st_entry.is_empty():
		status_override = str(st_entry.get("status", ""))

	_rover.call("update_frame", world_pos, float(_rover_state["yaw"]), dt, turret_target, status_override, speaking, energy)


# --------------------------------------------------------------- paths


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
				return {
					"pos": points[i].lerp(points[i + 1], k),
					"speed": total * ds / duration,
					"heading": heading,
					"gait": str(move.get("gait", "")),
				}
			along -= lengths[i]
	return {"pos": pos, "speed": 0.0, "heading": heading, "gait": ""}


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
	var fov: float = float(cut.get("fov", shot.get("fov", 50.0)))
	var dof_enabled: bool = bool(cut.get("dof_enabled", shot.get("dof_enabled", false)))
	var dof_dist: float = float(cut.get("dof_focus_distance", shot.get("dof_focus_distance", 0.0)))
	var first_person := ""

	# Support CinematicDirector keyframes format if `cameras[]` was omitted.
	if cut.is_empty() and shot.get("camera", null) is Dictionary:
		var cam_obj: Dictionary = shot["camera"] as Dictionary
		var kfs: Array[Dictionary] = _dict_array(cam_obj.get("keyframes", []))
		if kfs.size() >= 2:
			var k0: Dictionary = kfs[0]
			var k1: Dictionary = kfs[kfs.size() - 1]
			var f0: int = int(k0.get("frame", 0))
			var f1: int = maxi(int(k1.get("frame", int(shot.get("frames", 24)))), f0 + 1)
			var ku: float = clampf(float(shot_frame - f0) / float(f1 - f0), 0.0, 1.0)
			var ke: float = ku * ku * (3.0 - 2.0 * ku)
			pos = _vec3(k0.get("pos", null), pos).lerp(_vec3(k1.get("pos", null), pos), ke)
			look = _vec3(k0.get("look_at", null), look).lerp(_vec3(k1.get("look_at", null), look), ke)
			fov = lerpf(float(k0.get("fov", fov)), float(k1.get("fov", fov)), ke)
			dof_dist = lerpf(float(k0.get("dof_focus_distance", dof_dist)), float(k1.get("dof_focus_distance", dof_dist)), ke)
			mode = "static"
		elif kfs.size() == 1:
			pos = _vec3(kfs[0].get("pos", null), pos)
			look = _vec3(kfs[0].get("look_at", null), look)
			fov = float(kfs[0].get("fov", fov))
			mode = "static"

	match mode:
		"dolly", "tracking":
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
	_camera_state["fov"] = fov
	_camera.fov = fov
	if pos.distance_to(look) > 0.01:
		_camera.look_at_from_position(pos, look, Vector3.UP)
	else:
		_camera.position = pos
	_camera.rotate_object_local(Vector3.BACK, deg_to_rad(float(cut.get("roll_deg", 0.0))))

	# Optional cinematic depth-of-field focus update.
	if _bay != null and _bay.has_method("set_camera_dof"):
		var focus: float = dof_dist if dof_dist > 0.1 else pos.distance_to(look)
		_bay.call("set_camera_dof", focus, dof_enabled, float(cut.get("dof_blur", 0.07)))

	_set_first_person(first_person)


func _set_first_person(actor_id: String) -> void:
	for key: Variant in _actors.keys():
		var hidden: bool = str(key) == actor_id
		if bool(_actors[key].call("is_first_person_hidden")) != hidden:
			_actors[key].call("set_first_person_hidden", hidden)
	_camera.cull_mask = CULL_FIRST_PERSON if actor_id != "" else CULL_ALL
	var sensor_node: Node3D = _prop_system.call("get_prop_node", "sensor") if _prop_system != null else null
	var sensor_owner: String = str(_prop_system.call("get_prop_owner", "sensor")) if _prop_system != null else ""
	if sensor_node != null:
		var hide_real: bool = actor_id != "" and actor_id == sensor_owner
		_set_layers(sensor_node, 2 if hide_real else 1)
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
		var vm_cfg: Dictionary = _stage_data.get("viewmodel", {}) as Dictionary if _stage_data.get("viewmodel", {}) is Dictionary else {}
		_viewmodel = _actors[actor_id].call("build_viewmodel", float(vm_cfg.get("grip_radius", 0.022)))
		_camera.add_child(_viewmodel)
		_viewmodel_actor = actor_id
		_viewmodel_sensor = _make_viewmodel_sensor(_vec3(vm_cfg.get("sensor_rotation_deg", null), Vector3.ZERO))
		_viewmodel_sensor.name = "ViewmodelSensor"
		_camera.add_child(_viewmodel_sensor)
		_place_viewmodel()
	_viewmodel.visible = true
	_viewmodel_sensor.visible = sensor_node != null and sensor_owner == actor_id


func _place_viewmodel() -> void:
	var vm: Dictionary = _stage_data.get("viewmodel", {}) as Dictionary if _stage_data.get("viewmodel", {}) is Dictionary else {}
	var hand: Vector3 = _vec3(vm.get("hand", null), Vector3(0.15, -0.17, -0.38))
	var arm_dir: Vector3 = _vec3(vm.get("arm_dir", null), Vector3(-0.32, 0.42, -0.85)).normalized()
	var palm_hint: Vector3 = _vec3(vm.get("palm_hint", null), Vector3(-0.55, 0.25, 0.8))
	var x_axis: Vector3 = arm_dir.cross(palm_hint).normalized()
	var z_axis: Vector3 = x_axis.cross(arm_dir).normalized()
	var up: Vector3 = _vec3(vm.get("sensor_up", null), Vector3(0.12, 1.0, 0.42)).normalized()
	var toward: Vector3 = Vector3(0.0, 0.0, 1.0)
	var sx: Vector3 = up.cross(toward).normalized()
	var sz: Vector3 = sx.cross(up).normalized()
	var sensor_basis := Basis(sx, up, sz) * Basis.from_scale(Vector3.ONE * float(vm.get("sensor_scale", VIEWMODEL_SENSOR_SCALE)))
	var grip_center: Vector3 = hand + _vec3(vm.get("sensor_offset", null), Vector3(-0.01, 0.0, 0.01))
	_viewmodel_sensor.transform = Transform3D(sensor_basis, grip_center)
	var back: Vector3 = -arm_dir + up * arm_dir.dot(up)
	back = back.normalized() if back.length_squared() > 1e-6 else Vector3.BACK
	var palm_side: Vector3 = up.cross(back).normalized()
	var fist: Node3D = _viewmodel.get_node_or_null("Fist") as Node3D
	if fist != null:
		fist.transform = Transform3D(Basis(palm_side, up, palm_side.cross(up).normalized()), grip_center)
	var forearm: Node3D = _viewmodel.get_node("Forearm") as Node3D
	var wrist: Vector3 = grip_center + palm_side * float(vm.get("wrist_side", 0.036)) - arm_dir * float(vm.get("wrist_back", 0.075))
	forearm.transform = Transform3D(Basis(x_axis, arm_dir, z_axis), wrist)
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


# ----------------------------------------------------------- telemetry


func _capture_telemetry_frame(frame_index: int, shot: Dictionary) -> Dictionary:
	var actors_row := {}
	for key: Variant in _actors.keys():
		var aid: String = str(key)
		actors_row[aid] = _actors[aid].call("get_qc_snapshot")

	var props_row := {}
	if _prop_system != null:
		var snaps: Dictionary = _prop_system.call("get_all_qc_snapshots")
		for pk: Variant in snaps.keys():
			var pid: String = str(pk)
			var s: Dictionary = snaps[pid] as Dictionary
			props_row[pid] = {
				"owner": str(s.get("owner", "")),
				"position": s.get("world_pos", [0.0, 0.0, 0.0]),
				"hand_gap_m": float(s.get("hand_gap_m", 0.0)),
				"state": str(s.get("state", "")),
				"min_y": float(s.get("min_y", 0.0)),
			}

	var rover_row: Variant = null
	if _rover != null and _rover.visible:
		var rsnap: Dictionary = _rover.call("get_qc_snapshot")
		rover_row = {
			"wheel_min_y": float(rsnap.get("wheel_min_y", 0.0)),
			"hull_min_y": float(rsnap.get("hull_min_y", 0.0)),
			"position": rsnap.get("world_pos", [0.0, 0.0, 0.0]),
			"speed_mps": float(rsnap.get("speed_mps", 0.0)),
			"wheel_spin_rad": float(rsnap.get("wheel_spin_rad", 0.0)),
			"turret_yaw_deg": float(rsnap.get("turret_yaw_deg", 0.0)),
			"status": str(rsnap.get("status", "idle")),
		}

	var cam_pos: Vector3 = _camera_state.get("pos", _camera.global_position) as Vector3
	var cam_look: Vector3 = _camera_state.get("look", Vector3.ZERO) as Vector3
	var cam_fov: float = float(_camera_state.get("fov", _camera.fov))
	var wall_dist: float = _camera_wall_clearance(cam_pos)
	var subject_info: Dictionary = _subject_projection(shot, cam_pos)

	var row := {
		"frame": frame_index,
		"shot_id": str(shot.get("id", "")),
		"actors": actors_row,
		"props": props_row,
		"camera": {
			"position": [snappedf(cam_pos.x, 0.001), snappedf(cam_pos.y, 0.001), snappedf(cam_pos.z, 0.001)],
			"look_at": [snappedf(cam_look.x, 0.001), snappedf(cam_look.y, 0.001), snappedf(cam_look.z, 0.001)],
			"fov": snappedf(cam_fov, 0.1),
			"wall_distance_m": snappedf(wall_dist, 0.001),
			"subject": subject_info,
		},
	}
	if rover_row != null:
		row["rover"] = rover_row
	return row


func _camera_wall_clearance(cam_pos: Vector3) -> float:
	if _bay == null:
		return 1.0
	var rmin: Vector3 = _bay.get("room_min") as Vector3
	var rmax: Vector3 = _bay.get("room_max") as Vector3
	var in_room: bool = (
		cam_pos.x >= rmin.x and cam_pos.x <= rmax.x
		and cam_pos.y >= rmin.y and cam_pos.y <= rmax.y
		and cam_pos.z >= rmin.z and cam_pos.z <= rmax.z
	)
	if in_room:
		return minf(
			minf(cam_pos.x - rmin.x, rmax.x - cam_pos.x),
			minf(minf(cam_pos.y - rmin.y, rmax.y - cam_pos.y), minf(cam_pos.z - rmin.z, rmax.z - cam_pos.z))
		)
	var cdepth: float = float(_bay.get("corridor_depth"))
	if cdepth > 0.1:
		var dx: float = float(_bay.get("door_x"))
		var half: float = float(_bay.get("door_width")) * 0.5
		var dh: float = float(_bay.get("door_height"))
		var z_far: float = rmax.z + cdepth
		return minf(
			minf(cam_pos.x - (dx - half), (dx + half) - cam_pos.x),
			minf(minf(cam_pos.y - rmin.y, dh - cam_pos.y), minf(cam_pos.z - rmax.z, z_far - cam_pos.z))
		)
	return 0.0


func _subject_projection(shot: Dictionary, cam_pos: Vector3) -> Dictionary:
	var subj_id: String = str(shot.get("primary_subject", shot.get("subject", shot.get("actor", ""))))
	var world_pt := Vector3.INF
	if subj_id != "" and _actors.has(subj_id):
		var act: Node3D = _actors[subj_id]
		if bool(act.call("is_first_person_hidden")):
			# In POV, the focal subject is the look-at target (usually the partner or rover).
			subj_id = str(shot.get("secondary_subject", "pov_target"))
			world_pt = _camera_state.get("look", cam_pos + Vector3.FORWARD) as Vector3
		else:
			world_pt = act.call("head_world_position").lerp(act.call("bone_world_position", "hips"), 0.35)
	elif subj_id != "" and _rover != null and subj_id == str(_rover_spec.get("id", "at1")) and _rover.visible:
		world_pt = _rover.call("get_eye_world_position")
	elif subj_id != "" and _prop_system != null and _prop_system.call("get_prop_node", subj_id) != null:
		world_pt = _prop_system.call("get_socket_world_position", subj_id, "grip")
	else:
		# Pick the visible actor closest to the camera's look_at point.
		var look_pt: Vector3 = _camera_state.get("look", Vector3.ZERO) as Vector3
		var best_d := INF
		for aid_v: Variant in _actors.keys():
			var aid: String = str(aid_v)
			var act2: Node3D = _actors[aid]
			if not act2.visible or bool(act2.call("is_first_person_hidden")):
				continue
			var p: Vector3 = act2.call("head_world_position").lerp(act2.call("bone_world_position", "hips"), 0.35)
			var d: float = p.distance_to(look_pt)
			if d < best_d:
				best_d = d
				subj_id = aid
				world_pt = p
	if world_pt == Vector3.INF:
		world_pt = _camera_state.get("look", cam_pos + Vector3.FORWARD) as Vector3
		if subj_id == "":
			subj_id = "scene"

	var local_pt: Vector3 = _camera.global_transform.affine_inverse() * world_pt
	var ndc_x := 2.0
	var ndc_y := 2.0
	if local_pt.z < -0.05:
		var half_h: float = tan(deg_to_rad(_camera.fov * 0.5)) * (-local_pt.z)
		var half_w: float = half_h * (VIEW_SIZE.x / VIEW_SIZE.y)
		ndc_x = local_pt.x / maxf(half_w, 0.0001)
		ndc_y = local_pt.y / maxf(half_h, 0.0001)

	var occluded := false
	if _bay != null and _bay.has_method("is_camera_clear"):
		occluded = not bool(_bay.call("is_camera_clear", cam_pos, world_pt, 0.14))

	return {
		"id": subj_id,
		"ndc_x": snappedf(ndc_x, 0.001),
		"ndc_y": snappedf(ndc_y, 0.001),
		"occluded": occluded,
	}


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
		var facial: Node = actor.call("get_facial_controller")
		actors_report[str(key)] = {
			"source": str(actor.get("source_kind")),
			"direct_rig_match": bool(actor.get("direct_rig_match")),
			"has_blendshapes": bool(actor.get("has_native_blendshapes")),
			"has_fingers": bool(actor.get("has_fingers")),
			"facial_capability": str(facial.call("get_capability_grade")) if facial != null else "head_only",
			"default_gait": str(actor.get("default_gait")),
			"clips": actor.get("clip_report"),
			"natural_walk_speed": snappedf(float(actor.get("natural_walk_speed")), 0.001),
		}
	var handoffs: Array[Dictionary] = _prop_system.call("get_handoff_log") if _prop_system != null else []
	var result := {
		"total_frames": _total_frames,
		"elapsed_sec": elapsed_sec,
		"fps": _fps,
		"render_fps": float(_total_frames) / elapsed_sec,
		"scene_type": str(_bay.get("scene_type")),
		"lighting_profile": str(_bay.get("lighting_profile_name")),
		"loaded_actors": _actors.size(),
		"actors": actors_report,
		"props_loaded": int(_bay.get("props_loaded")),
		"props_failed": int(_bay.get("props_failed")),
		"rover_loaded": _rover != null,
		"rover_wheels": int(_rover.call("get_wheel_count")) if _rover != null else 0,
		"handoffs": handoffs,
		"sample_files": _sample_files,
		"telemetry_file": _telemetry_json_path,
	}
	if _telemetry_json_path != "":
		_write_json(_telemetry_json_path, {
			"version": 1,
			"fps": _fps,
			"total_frames": _total_frames,
			"scene_type": str(_bay.get("scene_type")),
			"lighting_profile": str(_bay.get("lighting_profile_name")),
			"frames": _telemetry_frames,
		})
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
	_write_json(_result_json_path, payload)


func _write_json(path: String, payload: Dictionary) -> void:
	var parent_dir: String = path.get_base_dir()
	if parent_dir != "" and not _ensure_directory(parent_dir):
		push_error("Stage3D: output directory could not be created: %s" % parent_dir)
		return
	var handle := FileAccess.open(path, FileAccess.WRITE)
	if handle == null:
		push_error("Stage3D: JSON open failed (%s): %s" % [path, error_string(FileAccess.get_open_error())])
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
