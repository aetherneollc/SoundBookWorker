extends Node2D

## stage.json player entry point. One logical animation frame is evaluated per
## process tick so --fixed-fps and --write-movie remain frame-exact.

const VIEW_SIZE := Vector2(1280.0, 720.0)
const BASE_ACTOR_HEIGHT := 500.0
# AT-1 stands a little over half as tall as a person (about 0.95 m), wheels to sensor head.
const BASE_ROVER_HEIGHT := BASE_ACTOR_HEIGHT * 0.53
# Clips drawn aiming at one side of the screen (-1 = left). They are posed
# mirrored when the actor addresses the other side.
const AIMED_CLIPS := {"point": -1.0, "talk_explain": -1.0}
const DEFAULT_VISEME := "X"
const VALID_VISEMES := ["X", "A", "B", "C", "D", "E", "F"]

var _stage_data: Dictionary = {}
var _shots: Array[Dictionary] = []
var _speech: Array[Dictionary] = []
var _captions: Array[Dictionary] = []
var _intro: Dictionary = {}
var _cta: Dictionary = {}
var _stage_json_path := ""
var _base_dir := ""
var _fps := 24
var _total_frames := 0

var _stage: Node2D
var _camera_director: Node
var _camera: Camera2D
var _puppet_script: Script
var _motion_script: Script
var _face_script: Script
var _motion_library: Variant
var _puppets: Dictionary = {}
var _loaded_actor_count := 0
var _robot_rig: Dictionary = {}
var _rover_script: Script
var _rover: Node2D

var _captions_node: Node
var _custom_captions_active := false
var _caption_fallback_layer: CanvasLayer
var _caption_fallback_root: Control
var _caption_fallback_label: Label

var _title_card_node: Node
var _custom_title_active := false
var _title_fallback_layer: CanvasLayer
var _title_fallback_root: Control
var _title_episode_label: Label
var _title_label: Label
var _title_subtitle_label: Label
var _title_key := ""

var _cjk_font_path := ""
var _fallback_font: FontFile
var _sample_frames: Array[int] = []
var _sample_frame_set: Dictionary = {}
var _sample_dir := ""
var _sample_files: Array[String] = []
var _result_json_path := ""

var _frame := 0
var _presented_frame := -1
var _active_shot_key := ""
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
		_abort_startup("Could not initialize the stage player", 3)
		return
	var post_draw := Callable(self, "_on_frame_post_draw")
	if not RenderingServer.frame_post_draw.is_connected(post_draw):
		RenderingServer.frame_post_draw.connect(post_draw)
	_start_usec = Time.get_ticks_usec()
	set_process(true)
	print(
		"STAGE_PLAYER ready frames=%d fps=%d actors=%d"
		% [_total_frames, _fps, _loaded_actor_count]
	)


func _process(_delta: float) -> void:
	if _finishing:
		return
	if _frame >= _total_frames and _presented_frame < 0:
		_finishing = true
		_finish()
		return
	# The post-draw callback clears this before the next process tick.
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


func _read_configuration() -> bool:
	_result_json_path = _resolve_output_path(OS.get_environment("STAGE_RESULT_JSON"))
	_cjk_font_path = _resolve_optional_input_path(OS.get_environment("STAGE_CJK_FONT"))
	_parse_sample_frames(OS.get_environment("STAGE_SAMPLE_FRAMES"))
	if not _sample_frames.is_empty():
		var requested_sample_dir: String = OS.get_environment("STAGE_SAMPLE_DIR")
		if requested_sample_dir == "":
			_sample_dir = ProjectSettings.globalize_path("user://stage_samples")
		else:
			_sample_dir = _resolve_output_path(requested_sample_dir)
		if not _ensure_directory(_sample_dir):
			push_error("StagePlayer: sample directory could not be created: %s" % _sample_dir)
			return false

	var stage_env: String = OS.get_environment("STAGE_JSON").strip_edges()
	if stage_env == "":
		push_error("StagePlayer: STAGE_JSON is empty")
		return false
	_stage_json_path = _resolve_stage_json_path(stage_env)
	if _stage_json_path == "" or not FileAccess.file_exists(_stage_json_path):
		push_error("StagePlayer: stage.json not found: %s" % stage_env)
		return false

	var handle := FileAccess.open(_stage_json_path, FileAccess.READ)
	if handle == null:
		push_error(
			"StagePlayer: stage.json open failed: %s"
			% error_string(FileAccess.get_open_error())
		)
		return false
	var text: String = handle.get_as_text()
	handle.close()
	var parser := JSON.new()
	var parse_error: Error = parser.parse(text)
	if parse_error != OK:
		push_error(
			"StagePlayer: invalid stage.json line %d: %s"
			% [parser.get_error_line(), parser.get_error_message()]
		)
		return false
	var parsed: Variant = parser.data
	if not parsed is Dictionary:
		push_error("StagePlayer: stage.json root must be an object")
		return false
	_stage_data = parsed as Dictionary
	if int(_stage_data.get("version", 0)) != 1:
		push_error("StagePlayer: unsupported stage.json version")
		return false
	if int(_stage_data.get("width", 0)) != 1280 or int(_stage_data.get("height", 0)) != 720:
		push_error("StagePlayer: stage size must be 1280x720")
		return false
	_fps = clampi(int(_stage_data.get("fps", 24)), 1, 240)
	_total_frames = int(_stage_data.get("total_frames", 0))
	if _total_frames <= 0:
		push_error("StagePlayer: total_frames must be positive")
		return false
	_shots = _as_dictionary_array(_stage_data.get("shots", []))
	_speech = _as_dictionary_array(_stage_data.get("speech", []))
	_captions = _as_dictionary_array(_stage_data.get("captions", []))
	var intro_value: Variant = _stage_data.get("intro", {})
	if intro_value is Dictionary:
		_intro = intro_value as Dictionary
	var cta_value: Variant = _stage_data.get("cta", {})
	if cta_value is Dictionary:
		_cta = cta_value as Dictionary
	var robot_rig_value: Variant = _stage_data.get("robot_rig", {})
	if robot_rig_value is Dictionary:
		_robot_rig = robot_rig_value as Dictionary
	_base_dir = _stage_json_path.get_base_dir()
	return true


func _build_runtime() -> bool:
	var stage_script := load("res://player/stage.gd") as Script
	var camera_script := load("res://player/camera_director.gd") as Script
	if stage_script == null or camera_script == null:
		push_error("StagePlayer: stage or camera script could not be loaded")
		return false

	var stage_value: Variant = stage_script.new()
	if not stage_value is Node2D:
		push_error("StagePlayer: stage.gd must instantiate a Node2D")
		return false
	_stage = stage_value as Node2D
	_stage.name = "Stage2D"
	add_child(_stage)

	var director_value: Variant = camera_script.new()
	if not director_value is Node:
		push_error("StagePlayer: camera_director.gd must instantiate a Node")
		return false
	_camera_director = director_value as Node
	_camera_director.name = "CameraDirector2D"
	add_child(_camera_director)
	if not _camera_director.has_method("setup"):
		push_error("StagePlayer: CameraDirector2D.setup is missing")
		return false
	var camera_value: Variant = _camera_director.call("setup", self)
	if not camera_value is Camera2D:
		push_error("StagePlayer: CameraDirector2D did not create Camera2D")
		return false
	_camera = camera_value as Camera2D

	# Runtime string loads avoid parse-time coupling with parallel modules.
	_motion_script = load("res://player/motion.gd") as Script
	_face_script = load("res://player/face.gd") as Script
	_puppet_script = load("res://player/puppet.gd") as Script
	if _motion_script == null or _face_script == null or _puppet_script == null:
		push_error("StagePlayer: puppet/motion/face scripts are required")
		return false
	_motion_library = _motion_script.new()
	if _motion_library is Node:
		var motion_node := _motion_library as Node
		motion_node.name = "MotionLibrary"
		add_child(motion_node)

	if not _build_puppets():
		return false
	if not _build_rover():
		return false
	_load_fallback_font()
	_build_caption_layers()
	_build_title_layers()
	_hide_all_actors()
	_hide_rover()
	return true


func _build_puppets() -> bool:
	var rigs_value: Variant = _stage_data.get("rigs", {})
	if not rigs_value is Dictionary:
		push_error("StagePlayer: rigs must be an object")
		return false
	var rigs: Dictionary = rigs_value as Dictionary
	var first_configs: Dictionary = {}
	for shot: Dictionary in _shots:
		for actor_cfg: Dictionary in _as_dictionary_array(shot.get("actors", [])):
			var actor_id: String = str(actor_cfg.get("id", ""))
			if actor_id != "" and not first_configs.has(actor_id):
				first_configs[actor_id] = actor_cfg

	for actor_id_value: Variant in first_configs.keys():
		var actor_id: String = str(actor_id_value)
		var rig_value: Variant = rigs.get(actor_id, {})
		if not rig_value is Dictionary:
			push_warning("StagePlayer: rig missing for actor %s" % actor_id)
			continue
		var rig_cfg: Dictionary = rig_value as Dictionary
		var actor_cfg: Dictionary = first_configs[actor_id]
		var rig_json: String = str(rig_cfg.get("rig_json", "")).strip_edges()
		if rig_json == "":
			push_warning("StagePlayer: rig_json missing for actor %s" % actor_id)
			continue
		var depth: String = str(actor_cfg.get("depth", "mid"))
		var depth_scale: float = _stage_depth_scale(depth)
		var stature: float = maxf(float(rig_cfg.get("stature_scale", 1.0)), 0.1)
		var setup_scale: float = depth_scale * stature
		var puppet_value: Variant = _puppet_script.new()
		if not puppet_value is Node2D:
			push_error("StagePlayer: puppet.gd must instantiate a Node2D")
			return false
		var puppet := puppet_value as Node2D
		puppet.name = StringName("Puppet_%s" % actor_id.replace("/", "_"))
		puppet.visible = false
		add_child(puppet)
		if not puppet.has_method("setup") or not puppet.has_method("apply_state"):
			push_error("StagePlayer: Puppet2D contract is incomplete")
			return false
		var rig_path: String = _resolve_asset_path(rig_json)
		puppet.call("setup", rig_path, _motion_library, BASE_ACTOR_HEIGHT * setup_scale)
		_puppets[actor_id] = {
			"puppet": puppet,
			"stature": stature,
			"setup_scale": setup_scale,
			"current_clip": "",
			"previous_clip": "",
			"clip_change_frame": -1000000,
			"actor_seed": absi(actor_id.hash()),
		}
		_loaded_actor_count += 1
	return _loaded_actor_count > 0 or first_configs.is_empty()


func _build_rover() -> bool:
	if _robot_rig.is_empty():
		return true
	var has_rover := false
	for shot: Dictionary in _shots:
		if shot.get("rover") is Dictionary:
			has_rover = true
			break
	if not has_rover:
		return true
	var rig_json: String = str(_robot_rig.get("rig_json", "")).strip_edges()
	if rig_json == "":
		push_error("StagePlayer: robot_rig.rig_json is required by rover shots")
		return false
	_rover_script = load("res://player/rover.gd") as Script
	if _rover_script == null:
		push_error("StagePlayer: rover.gd could not be loaded")
		return false
	var rover_value: Variant = _rover_script.new()
	if not rover_value is Node2D:
		push_error("StagePlayer: rover.gd must instantiate a Node2D")
		return false
	_rover = rover_value as Node2D
	_rover.name = "Rover_AT1"
	_rover.visible = false
	add_child(_rover)
	if not _rover.has_method("setup") or not _rover.has_method("apply_state"):
		push_error("StagePlayer: Rover2D contract is incomplete")
		return false
	var rig_loaded: bool = bool(
		_rover.call("setup", _resolve_asset_path(rig_json), BASE_ROVER_HEIGHT)
	)
	if not rig_loaded:
		push_warning("StagePlayer: Rover2D is using procedural fallback parts")
	return true


func _build_caption_layers() -> void:
	_caption_fallback_layer = CanvasLayer.new()
	_caption_fallback_layer.name = "CaptionFallback"
	_caption_fallback_layer.layer = 70
	add_child(_caption_fallback_layer)
	_caption_fallback_root = Control.new()
	_caption_fallback_root.name = "Root"
	_caption_fallback_root.position = Vector2.ZERO
	_caption_fallback_root.size = VIEW_SIZE
	_caption_fallback_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_caption_fallback_layer.add_child(_caption_fallback_root)
	var bar := ColorRect.new()
	bar.color = Color(0.015, 0.02, 0.035, 0.72)
	bar.position = Vector2(0.0, 626.0)
	bar.size = Vector2(1280.0, 94.0)
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_caption_fallback_root.add_child(bar)
	_caption_fallback_label = Label.new()
	_caption_fallback_label.position = Vector2(48.0, 630.0)
	_caption_fallback_label.size = Vector2(1184.0, 84.0)
	_caption_fallback_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_caption_fallback_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_caption_fallback_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_caption_fallback_label.add_theme_font_size_override("font_size", 36)
	_caption_fallback_label.add_theme_color_override("font_color", Color(1.0, 0.97, 0.91))
	_caption_fallback_label.add_theme_color_override("font_shadow_color", Color(0.0, 0.0, 0.0, 0.95))
	_caption_fallback_label.add_theme_constant_override("shadow_offset_x", 2)
	_caption_fallback_label.add_theme_constant_override("shadow_offset_y", 2)
	if _fallback_font != null:
		_caption_fallback_label.add_theme_font_override("font", _fallback_font)
	_caption_fallback_root.add_child(_caption_fallback_label)
	_caption_fallback_root.visible = false

	if ResourceLoader.exists("res://player/captions.gd"):
		var captions_script := load("res://player/captions.gd") as Script
		if captions_script != null:
			var captions_value: Variant = captions_script.new()
			if captions_value is Node:
				_captions_node = captions_value as Node
				_captions_node.name = "CaptionsLayer"
				add_child(_captions_node)
				_set_optional_property(_captions_node, "cjk_font_path", _cjk_font_path)
				_configure_custom_captions()


func _configure_custom_captions() -> void:
	if _captions_node == null:
		return
	if _captions_node.has_method("setup"):
		var count: int = _method_argument_count(_captions_node, "setup")
		if count == 0:
			_captions_node.call("setup")
		elif count == 1:
			_captions_node.call("setup", _captions)
		elif count == 2:
			_captions_node.call("setup", _captions, _cjk_font_path)
	_custom_captions_active = (
		_captions_node.has_method("apply_frame")
		or _captions_node.has_method("update_frame")
		or _captions_node.has_method("set_frame")
		or _captions_node.has_method("set_caption")
	)
	_set_node_visible(_captions_node, false)


func _build_title_layers() -> void:
	_title_fallback_layer = CanvasLayer.new()
	_title_fallback_layer.name = "TitleCardFallback"
	_title_fallback_layer.layer = 90
	add_child(_title_fallback_layer)
	_title_fallback_root = Control.new()
	_title_fallback_root.position = Vector2.ZERO
	_title_fallback_root.size = VIEW_SIZE
	_title_fallback_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_title_fallback_layer.add_child(_title_fallback_root)
	var background := ColorRect.new()
	background.color = Color(0.035, 0.06, 0.12, 1.0)
	background.position = Vector2.ZERO
	background.size = VIEW_SIZE
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_title_fallback_root.add_child(background)
	var accent := ColorRect.new()
	accent.color = Color(0.16, 0.78, 0.86, 1.0)
	accent.position = Vector2(248.0, 246.0)
	accent.size = Vector2(784.0, 5.0)
	accent.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_title_fallback_root.add_child(accent)

	_title_episode_label = _make_title_label(Vector2(140.0, 150.0), Vector2(1000.0, 64.0), 30)
	_title_label = _make_title_label(Vector2(90.0, 270.0), Vector2(1100.0, 125.0), 58)
	_title_subtitle_label = _make_title_label(Vector2(130.0, 422.0), Vector2(1020.0, 86.0), 30)
	_title_fallback_root.add_child(_title_episode_label)
	_title_fallback_root.add_child(_title_label)
	_title_fallback_root.add_child(_title_subtitle_label)
	_title_fallback_root.visible = false

	if ResourceLoader.exists("res://player/title_card.gd"):
		var title_script := load("res://player/title_card.gd") as Script
		if title_script != null:
			var title_value: Variant = title_script.new()
			if title_value is Node:
				_title_card_node = title_value as Node
				_title_card_node.name = "TitleCard"
				add_child(_title_card_node)
				_set_optional_property(_title_card_node, "cjk_font_path", _cjk_font_path)
				if _title_card_node.has_method("setup"):
					var count: int = _method_argument_count(_title_card_node, "setup")
					if count == 0:
						_title_card_node.call("setup")
					elif count == 1:
						_title_card_node.call("setup", _cjk_font_path)
				_custom_title_active = (
					_title_card_node.has_method("show_intro")
					or _title_card_node.has_method("show_cta")
					or _title_card_node.has_method("show_card")
				)
				_set_node_visible(_title_card_node, false)


func _make_title_label(position: Vector2, size: Vector2, font_size: int) -> Label:
	var label := Label.new()
	label.position = position
	label.size = size
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", Color(0.96, 0.98, 1.0))
	label.add_theme_color_override("font_shadow_color", Color(0.0, 0.0, 0.0, 0.9))
	label.add_theme_constant_override("shadow_offset_x", 2)
	label.add_theme_constant_override("shadow_offset_y", 3)
	if _fallback_font != null:
		label.add_theme_font_override("font", _fallback_font)
	return label


func _apply_frame(frame_index: int) -> void:
	var intro_frames: int = int(_intro.get("frames", 0))
	if intro_frames > 0 and frame_index < intro_frames:
		_apply_title_frame("intro", _intro, frame_index)
		return
	var cta_from: int = int(_cta.get("from", -1))
	var cta_frames: int = int(_cta.get("frames", 0))
	var in_cta: bool = (
		cta_from >= 0
		and cta_frames > 0
		and frame_index >= cta_from
		and frame_index < cta_from + cta_frames
	)
	if in_cta:
		_apply_title_frame("cta", _cta, frame_index - cta_from)
		return
	_apply_shot_frame(frame_index)


func _apply_title_frame(mode: String, config: Dictionary, local_frame: int) -> void:
	_active_shot_key = ""
	_stage.visible = false
	_hide_all_actors()
	_hide_rover()
	_clear_actor_shadows()
	if _camera_director.has_method("reset_wide"):
		_camera_director.call("reset_wide")
	_hide_captions()
	_show_title(mode, config, local_frame)


func _apply_shot_frame(frame_index: int) -> void:
	var shot: Dictionary = _find_shot(frame_index)
	if shot.is_empty():
		_apply_gap_frame()
		return
	_hide_title()
	_stage.visible = true
	var shot_from: int = int(shot.get("from", 0))
	var shot_frame: int = frame_index - shot_from
	var shot_key: String = "%s@%d" % [str(shot.get("id", "")), shot_from]
	if shot_key != _active_shot_key:
		var set_value: Variant = shot.get("set", {})
		var set_cfg: Dictionary = set_value as Dictionary if set_value is Dictionary else {}
		_stage.call("apply_set", set_cfg, _base_dir)
		_active_shot_key = shot_key

	var actor_records: Array[Dictionary] = _position_active_actors(shot, shot_frame)
	_apply_actor_states(actor_records, frame_index, shot_frame, shot)
	_apply_rover_state(shot, shot_frame, frame_index)
	var cut: Dictionary = _find_cut(shot, shot_frame)
	_camera_director.call("update_camera", cut, actor_records, shot_frame)
	var set_value: Variant = shot.get("set", {})
	var set_cfg: Dictionary = set_value as Dictionary if set_value is Dictionary else {}
	var walk_speed: float = float(set_cfg.get("walk_scroll_speed", 0.0))
	var shot_time: float = float(shot_frame) / float(_fps)
	_stage.call("update_parallax", _camera.position, _camera.zoom.x, shot_time, walk_speed)
	_update_captions(frame_index)


func _apply_gap_frame() -> void:
	_active_shot_key = ""
	_stage.visible = false
	_hide_all_actors()
	_hide_rover()
	_clear_actor_shadows()
	_hide_title()
	_hide_captions()
	if _camera_director.has_method("reset_wide"):
		_camera_director.call("reset_wide")


func _position_active_actors(shot: Dictionary, shot_frame: int) -> Array[Dictionary]:
	_hide_all_actors()
	var records: Array[Dictionary] = []
	var shadows: Array[Dictionary] = []
	var seen: Dictionary = {}
	var ground_y: float = float(_stage.call("get_ground_y"))
	for actor_cfg: Dictionary in _as_dictionary_array(shot.get("actors", [])):
		var actor_id: String = str(actor_cfg.get("id", ""))
		if actor_id == "" or seen.has(actor_id) or not _puppets.has(actor_id):
			continue
		seen[actor_id] = true
		var runtime: Dictionary = _puppets[actor_id]
		var puppet := runtime.get("puppet") as Node2D
		if puppet == null:
			continue
		var beat: Dictionary = _find_beat(shot, actor_id, shot_frame)
		var x_value: float = float(actor_cfg.get("x", 0.5))
		var move_value: Variant = beat.get("move_x", [])
		if move_value is Array and (move_value as Array).size() >= 2:
			var move: Array = move_value as Array
			var beat_from: int = int(beat.get("from", 0))
			var beat_to: int = int(beat.get("to", beat_from + 1))
			var move_progress: float = clampf(
				float(shot_frame - beat_from) / float(maxi(beat_to - beat_from, 1)),
				0.0,
				1.0
			)
			x_value = lerpf(float(move[0]), float(move[1]), move_progress)
		var depth: String = str(actor_cfg.get("depth", "mid")).to_lower()
		var depth_scale: float = _stage_depth_scale(depth)
		puppet.position = Vector2(_horizontal_to_pixels(x_value), ground_y)
		puppet.z_index = _depth_z_index(depth)
		puppet.visible = true
		var facing: String = str(actor_cfg.get("facing", "front")).to_lower()
		records.append({
			"id": actor_id,
			"puppet": puppet,
			"cfg": actor_cfg,
			"beat": beat,
			"facing": facing,
			"depth_scale": depth_scale,
		})
		shadows.append({
			"position": puppet.position,
			"scale": depth_scale,
		})
	_stage.call("set_actor_shadows", shadows)
	return records


func _apply_rover_state(shot: Dictionary, shot_frame: int, frame_index: int) -> void:
	if _rover == null:
		return
	var rover_value: Variant = shot.get("rover")
	if not rover_value is Dictionary:
		_hide_rover()
		return
	var rover_cfg: Dictionary = rover_value as Dictionary
	var active: bool = bool(rover_cfg.get("active", true))
	var shot_frames: int = maxi(int(shot.get("frames", 1)), 1)
	var progress: float = clampf(
		float(shot_frame) / float(maxi(shot_frames - 1, 1)),
		0.0,
		1.0
	)
	var eased: float = progress * progress * (3.0 - 2.0 * progress)
	var from_x: float = float(rover_cfg.get("from_x", 0.65))
	var to_x: float = float(rover_cfg.get("to_x", from_x))
	var depth: String = str(rover_cfg.get("depth", "mid")).to_lower()
	var depth_scale: float = _stage_depth_scale(depth)
	_rover.position.y = float(_stage.call("get_ground_y"))
	# One step behind an actor at the same depth: a hand on the hull stays visible.
	_rover.z_index = _depth_z_index(depth) - 1
	_rover.call("apply_state", {
		"active": active,
		"action": str(rover_cfg.get("action", "idle")),
		"x": lerpf(from_x, to_x, eased),
		"facing": str(rover_cfg.get("facing", "right")),
		"target_pos": Vector2(
			float(rover_cfg.get("target_x", to_x)),
			float(rover_cfg.get("target_y", 0.55))
		),
		"scale_mult": depth_scale,
	}, float(frame_index) / float(_fps))


func _apply_actor_states(
	actor_records: Array[Dictionary],
	frame_index: int,
	shot_frame: int,
	shot: Dictionary
) -> void:
	for record: Dictionary in actor_records:
		var actor_id: String = str(record.get("id", ""))
		var runtime: Dictionary = _puppets[actor_id]
		var puppet := runtime.get("puppet") as Node2D
		var actor_cfg: Dictionary = record.get("cfg", {})
		var beat: Dictionary = record.get("beat", {})
		var desired_clip: String = str(beat.get("clip", actor_cfg.get("base_clip", "idle")))
		if desired_clip == "":
			desired_clip = "idle"
		var transition: Dictionary = _clip_transition(runtime, desired_clip, frame_index)
		var emotion: String = str(beat.get("emotion", actor_cfg.get("emotion", "neutral")))
		var speech_state: Dictionary = _speech_state(actor_id, frame_index)
		var speaking: bool = (
			bool(beat.get("speaking", false))
			or bool(speech_state.get("speaking", false))
		)
		var viseme: String = str(speech_state.get("viseme", DEFAULT_VISEME))
		var facing: String = str(record.get("facing", "front"))
		var look_dir: float = _look_direction(record, actor_records)
		var lean: float = clampf(float(actor_cfg.get("lean", 0.0)), -1.0, 1.0)
		var stature: float = float(runtime.get("stature", 1.0))
		var setup_scale: float = maxf(float(runtime.get("setup_scale", 1.0)), 0.001)
		var current_scale: float = float(record.get("depth_scale", 1.0)) * stature
		var clip_origin: int = int(beat.get("from", 0)) if not beat.is_empty() else 0
		var aim: float = float(AIMED_CLIPS.get(desired_clip, 0.0))
		var aim_dir: float = _aim_direction(puppet, look_dir, shot) if aim != 0.0 else 0.0
		var state := {
			"clip": desired_clip,
			"mirror_pose": aim_dir != 0.0 and signf(aim_dir) != signf(aim),
			"prev_clip": str(transition.get("prev_clip", desired_clip)),
			"blend": float(transition.get("blend", 1.0)),
			"clip_time": float(maxi(shot_frame - clip_origin, 0)) / float(_fps),
			"speaking": speaking,
			"viseme": viseme,
			"emotion": emotion,
			"look_dir": look_dir,
			"lean": lean,
			"facing": facing,
			"scale_mult": current_scale / setup_scale,
			"actor_seed": int(runtime.get("actor_seed", 0)),
		}
		puppet.call("apply_state", state, float(frame_index) / float(_fps))


func _clip_transition(runtime: Dictionary, desired_clip: String, frame_index: int) -> Dictionary:
	var current_clip: String = str(runtime.get("current_clip", ""))
	if current_clip == "":
		runtime["current_clip"] = desired_clip
		runtime["previous_clip"] = desired_clip
		runtime["clip_change_frame"] = frame_index - maxi(int(round(float(_fps) * 0.25)), 1)
	elif current_clip != desired_clip:
		runtime["previous_clip"] = current_clip
		runtime["current_clip"] = desired_clip
		runtime["clip_change_frame"] = frame_index
	var blend_frames: int = maxi(int(round(float(_fps) * 0.25)), 1)
	var changed_at: int = int(runtime.get("clip_change_frame", frame_index))
	return {
		"prev_clip": str(runtime.get("previous_clip", desired_clip)),
		"blend": clampf(float(frame_index - changed_at) / float(blend_frames), 0.0, 1.0),
	}


## Screen side an aimed gesture should address: the rover when it is on stage,
## otherwise whoever the actor is looking at.
func _aim_direction(puppet: Node2D, look_dir: float, shot: Dictionary) -> float:
	var rover_value: Variant = shot.get("rover")
	if _rover != null and puppet != null and rover_value is Dictionary:
		var rover_cfg: Dictionary = rover_value as Dictionary
		if bool(rover_cfg.get("active", true)):
			var mark: float = float(rover_cfg.get("to_x", rover_cfg.get("from_x", 0.65)))
			var offset: float = _horizontal_to_pixels(mark) - puppet.position.x
			if absf(offset) > 1.0:
				return signf(offset)
	return look_dir


func _look_direction(record: Dictionary, actor_records: Array[Dictionary]) -> float:
	var beat: Dictionary = record.get("beat", {})
	var look_at: String = str(beat.get("look_at", ""))
	var puppet := record.get("puppet") as Node2D
	if look_at != "" and puppet != null:
		for target: Dictionary in actor_records:
			if str(target.get("id", "")) != look_at:
				continue
			var target_puppet := target.get("puppet") as Node2D
			if target_puppet != null:
				return signf(target_puppet.position.x - puppet.position.x)
	var facing: String = str(record.get("facing", "front"))
	if facing == "right":
		return 1.0
	if facing == "left":
		return -1.0
	return 0.0


func _speech_state(actor_id: String, frame_index: int) -> Dictionary:
	for entry: Dictionary in _speech:
		if str(entry.get("actor", "")) != actor_id:
			continue
		var from_frame: int = int(entry.get("from", 0))
		var to_frame: int = int(entry.get("to", from_frame))
		if not _frame_in_range(frame_index, from_frame, to_frame):
			continue
		var relative_frame: int = frame_index - from_frame
		var viseme := DEFAULT_VISEME
		var cues_value: Variant = entry.get("cues", [])
		if cues_value is Array:
			for cue_value: Variant in cues_value as Array:
				if not cue_value is Array:
					continue
				var cue: Array = cue_value as Array
				if cue.size() < 2 or int(cue[0]) > relative_frame:
					continue
				var candidate: String = str(cue[1]).to_upper()
				viseme = candidate if candidate in VALID_VISEMES else DEFAULT_VISEME
		return {"speaking": true, "viseme": viseme}
	return {"speaking": false, "viseme": DEFAULT_VISEME}


func _find_shot(frame_index: int) -> Dictionary:
	for shot: Dictionary in _shots:
		var from_frame: int = int(shot.get("from", 0))
		var frames: int = int(shot.get("frames", 0))
		if frames > 0 and frame_index >= from_frame and frame_index < from_frame + frames:
			return shot
	return {}


func _find_beat(shot: Dictionary, actor_id: String, shot_frame: int) -> Dictionary:
	var match_value: Dictionary = {}
	for beat: Dictionary in _as_dictionary_array(shot.get("beats", [])):
		if str(beat.get("actor", "")) != actor_id:
			continue
		var from_frame: int = int(beat.get("from", 0))
		var to_frame: int = int(beat.get("to", from_frame))
		if _frame_in_range(shot_frame, from_frame, to_frame):
			match_value = beat
	return match_value


func _find_cut(shot: Dictionary, shot_frame: int) -> Dictionary:
	var latest: Dictionary = {}
	var latest_from := -1
	for cut: Dictionary in _as_dictionary_array(shot.get("cuts", [])):
		var from_frame: int = int(cut.get("from", 0))
		var to_frame: int = int(cut.get("to", from_frame))
		if _frame_in_range(shot_frame, from_frame, to_frame):
			return cut
		if from_frame <= shot_frame and from_frame > latest_from:
			latest = cut
			latest_from = from_frame
	if not latest.is_empty():
		return latest
	return {
		"from": 0,
		"to": maxi(int(shot.get("frames", 1)), 1),
		"framing": "wide",
		"focus_actor": "",
		"camera_move": "static",
	}


func _update_captions(frame_index: int) -> void:
	var text: String = _caption_text(frame_index)
	if _custom_captions_active and _captions_node != null:
		_caption_fallback_root.visible = false
		_set_node_visible(_captions_node, true)
		if _invoke_caption_frame_method("apply_frame", frame_index):
			return
		if _invoke_caption_frame_method("update_frame", frame_index):
			return
		if _invoke_caption_frame_method("set_frame", frame_index):
			return
		if _captions_node.has_method("set_caption"):
			var count: int = _method_argument_count(_captions_node, "set_caption")
			if count == 1:
				_captions_node.call("set_caption", text)
				return
			if count == 2:
				_captions_node.call("set_caption", text, text != "")
				return
	_caption_fallback_label.text = text
	_caption_fallback_root.visible = text != ""


func _invoke_caption_frame_method(method_name: String, frame_index: int) -> bool:
	if _captions_node == null or not _captions_node.has_method(method_name):
		return false
	var count: int = _method_argument_count(_captions_node, method_name)
	if count == 1:
		_captions_node.call(method_name, frame_index)
		return true
	if count == 2:
		_captions_node.call(method_name, frame_index, _fps)
		return true
	return false


func _caption_text(frame_index: int) -> String:
	for caption: Dictionary in _captions:
		var from_frame: int = int(caption.get("from", 0))
		var to_frame: int = int(caption.get("to", from_frame))
		if _frame_in_range(frame_index, from_frame, to_frame):
			return str(caption.get("text", ""))
	return ""


func _hide_captions() -> void:
	if _caption_fallback_root != null:
		_caption_fallback_root.visible = false
	if _captions_node != null:
		var can_clear: bool = (
			_captions_node.has_method("clear_caption")
			and _method_argument_count(_captions_node, "clear_caption") == 0
		)
		if can_clear:
			_captions_node.call("clear_caption")
		_set_node_visible(_captions_node, false)


func _show_title(mode: String, config: Dictionary, local_frame: int) -> void:
	var next_key: String = "%s:%s" % [mode, JSON.stringify(config)]
	var custom_invoked := false
	if _custom_title_active and _title_card_node != null:
		_set_node_visible(_title_card_node, true)
		if next_key != _title_key:
			custom_invoked = _invoke_title_card(mode, config)
		else:
			custom_invoked = true
		if _title_card_node.has_method("apply_frame"):
			var count: int = _method_argument_count(_title_card_node, "apply_frame")
			if count == 1:
				_title_card_node.call("apply_frame", local_frame)
			elif count == 2:
				_title_card_node.call("apply_frame", local_frame, _fps)
	if not custom_invoked:
		_set_node_visible(_title_card_node, false)
		_title_fallback_root.visible = true
		if mode == "intro":
			var episode: int = int(config.get("episode_number", 0))
			_title_episode_label.text = "第 %d 集" % episode if episode > 0 else ""
		else:
			_title_episode_label.text = ""
		_title_label.text = str(config.get("title", ""))
		_title_subtitle_label.text = str(config.get("subtitle", ""))
	else:
		_title_fallback_root.visible = false
	_title_key = next_key


func _invoke_title_card(mode: String, config: Dictionary) -> bool:
	if _title_card_node == null:
		return false
	var method_name: String = "show_intro" if mode == "intro" else "show_cta"
	if _title_card_node.has_method(method_name):
		var count: int = _method_argument_count(_title_card_node, method_name)
		if count == 1:
			_title_card_node.call(method_name, config)
			return true
		if mode == "intro" and count == 3:
			_title_card_node.call(
				method_name,
				int(config.get("episode_number", 0)),
				str(config.get("title", "")),
				str(config.get("subtitle", ""))
			)
			return true
		if mode == "cta" and count == 2:
			_title_card_node.call(
				method_name,
				str(config.get("title", "")),
				str(config.get("subtitle", ""))
			)
			return true
	if _title_card_node.has_method("show_card"):
		var count: int = _method_argument_count(_title_card_node, "show_card")
		if count == 1:
			_title_card_node.call("show_card", config)
			return true
		if count == 2:
			_title_card_node.call("show_card", mode, config)
			return true
	return false


func _hide_title() -> void:
	if _title_key == "":
		return
	_title_key = ""
	if _title_fallback_root != null:
		_title_fallback_root.visible = false
	if _title_card_node != null:
		var can_hide: bool = (
			_title_card_node.has_method("hide_card")
			and _method_argument_count(_title_card_node, "hide_card") == 0
		)
		if can_hide:
			_title_card_node.call("hide_card")
		_set_node_visible(_title_card_node, false)


func _hide_all_actors() -> void:
	for actor_id: Variant in _puppets.keys():
		var runtime: Dictionary = _puppets[actor_id]
		var puppet := runtime.get("puppet") as Node2D
		if puppet != null:
			puppet.visible = false


func _hide_rover() -> void:
	if _rover == null:
		return
	_rover.call("apply_state", {"active": false}, float(_frame) / float(maxi(_fps, 1)))


func _clear_actor_shadows() -> void:
	var empty_shadows: Array[Dictionary] = []
	_stage.call("set_actor_shadows", empty_shadows)


func _capture_sample(frame_index: int) -> void:
	var texture: ViewportTexture = get_viewport().get_texture()
	var image: Image = texture.get_image() if texture != null else null
	if image == null or image.is_empty():
		push_error("StagePlayer: viewport capture failed at frame %d" % frame_index)
		return
	var file_name: String = "frame_%06d.png" % frame_index
	var file_path: String = _sample_dir.path_join(file_name)
	var save_error: Error = image.save_png(file_path)
	if save_error != OK:
		push_error(
			"StagePlayer: save_png failed for %s: %s"
			% [file_path, error_string(save_error)]
		)
		return
	_sample_files.append(file_path)
	print("STAGE_SAMPLE frame=%d file=%s" % [frame_index, file_path])


func _finish() -> void:
	set_process(false)
	var post_draw := Callable(self, "_on_frame_post_draw")
	if RenderingServer.frame_post_draw.is_connected(post_draw):
		RenderingServer.frame_post_draw.disconnect(post_draw)
	var elapsed_sec: float = maxf(
		float(Time.get_ticks_usec() - _start_usec) / 1000000.0,
		0.000001
	)
	var result := {
		"total_frames": _total_frames,
		"elapsed_sec": elapsed_sec,
		"fps": _fps,
		"render_fps": float(_total_frames) / elapsed_sec,
		"loaded_actors": _loaded_actor_count,
		"sample_files": _sample_files,
	}
	_write_result(result)
	print("STAGE_RESULT %s" % JSON.stringify(result))
	get_tree().quit(0)


func _abort_startup(message: String, exit_code: int) -> void:
	_finishing = true
	push_error("StagePlayer: %s" % message)
	_write_result({
		"total_frames": _total_frames,
		"elapsed_sec": 0.0,
		"fps": _fps,
		"render_fps": 0.0,
		"loaded_actors": _loaded_actor_count,
		"sample_files": _sample_files,
		"error": message,
	})
	get_tree().quit(exit_code)


func _write_result(payload: Dictionary) -> void:
	if _result_json_path == "":
		return
	var parent_dir: String = _result_json_path.get_base_dir()
	if parent_dir != "" and not _ensure_directory(parent_dir):
		push_error("StagePlayer: result directory could not be created: %s" % parent_dir)
		return
	var handle := FileAccess.open(_result_json_path, FileAccess.WRITE)
	if handle == null:
		push_error(
			"StagePlayer: result JSON open failed: %s"
			% error_string(FileAccess.get_open_error())
		)
		return
	handle.store_string(JSON.stringify(payload, "\t") + "\n")
	handle.close()


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


func _load_fallback_font() -> void:
	if _cjk_font_path == "" or not FileAccess.file_exists(_cjk_font_path):
		return
	var font := FontFile.new()
	var load_error: Error = font.load_dynamic_font(_cjk_font_path)
	if load_error != OK:
		push_warning(
			"StagePlayer: CJK font load failed: %s"
			% error_string(load_error)
		)
		return
	_fallback_font = font


func _set_optional_property(target: Object, property_name: String, value: Variant) -> void:
	for property_value: Variant in target.get_property_list():
		var property: Dictionary = property_value as Dictionary
		if str(property.get("name", "")) == property_name:
			target.set(property_name, value)
			return


func _set_node_visible(target: Node, value: bool) -> void:
	if target == null:
		return
	if target is CanvasItem:
		(target as CanvasItem).visible = value
		return
	_set_optional_property(target, "visible", value)


func _method_argument_count(target: Object, method_name: String) -> int:
	var method_callable := Callable(target, method_name)
	return method_callable.get_argument_count()


func _as_dictionary_array(value: Variant) -> Array[Dictionary]:
	var result: Array[Dictionary] = []
	if value is Array:
		for item: Variant in value as Array:
			if item is Dictionary:
				result.append(item as Dictionary)
	return result


func _frame_in_range(frame_index: int, from_frame: int, to_frame: int) -> bool:
	var exclusive_to: int = maxi(to_frame, from_frame + 1)
	return frame_index >= from_frame and frame_index < exclusive_to


func _stage_depth_scale(depth: String) -> float:
	return float(_stage.call("depth_to_scale", depth))


func _depth_z_index(depth: String) -> int:
	match depth:
		"far":
			return -2
		"near":
			return 24
		_:
			return 10


func _horizontal_to_pixels(value: float) -> float:
	if value >= -0.5 and value <= 1.5:
		return value * VIEW_SIZE.x
	return value


func _resolve_stage_json_path(raw_path: String) -> String:
	var normalized: String = raw_path.replace("\\", "/")
	if normalized.begins_with("res://") or normalized.begins_with("user://"):
		return normalized.simplify_path()
	if normalized.is_absolute_path():
		return normalized.simplify_path()
	if FileAccess.file_exists(normalized):
		return normalized.simplify_path()
	var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
	var project_candidate: String = project_dir.path_join(normalized).simplify_path()
	if FileAccess.file_exists(project_candidate):
		return project_candidate
	var repository_candidate: String = project_dir.get_base_dir().path_join(normalized).simplify_path()
	if FileAccess.file_exists(repository_candidate):
		return repository_candidate
	return repository_candidate


func _resolve_optional_input_path(raw_path: String) -> String:
	var normalized: String = raw_path.strip_edges().replace("\\", "/")
	if normalized == "":
		return ""
	if normalized.begins_with("res://") or normalized.begins_with("user://"):
		return normalized.simplify_path()
	if normalized.is_absolute_path():
		return normalized.simplify_path()
	var project_dir: String = ProjectSettings.globalize_path("res://").simplify_path()
	var project_candidate: String = project_dir.path_join(normalized).simplify_path()
	if FileAccess.file_exists(project_candidate):
		return project_candidate
	return project_dir.get_base_dir().path_join(normalized).simplify_path()


func _resolve_asset_path(raw_path: String) -> String:
	var normalized: String = raw_path.replace("\\", "/")
	if normalized.begins_with("res://") or normalized.begins_with("user://"):
		return normalized.simplify_path()
	if normalized.is_absolute_path():
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
