class_name CharacterPerformanceController
extends RefCounted

## Unified non-hardcoded CharacterPerformanceController (BUG-006, BUG-007, BUG-010).
##
## Coordinates per-actor:
##   - Idle posture classification (relaxed_idle, professional_idle, alert_idle,
##     combat_idle, conversation_idle) so civilian/sci-fi crew never default to
##     boxer/combat fists.
##   - Semantic dialogue gestures (confront, report/point, worry, reassure, explain)
##     selected from line text/emotion without mechanically repeating the same gesture.
##   - Interactive device operation (tapping console/touchscreen, reading gauge).
##   - Listener micro-reactions, subtle torso lean, and gaze target prioritization.

const GESTURE_CYCLE: Array[String] = ["open_explain", "beat_right", "point_device", "open_both", "chest_hold"]

var _last_gesture_by_actor: Dictionary = {}   # actor_id -> String
var _last_line_key_by_actor: Dictionary = {}  # actor_id -> String
var _gesture_choice_by_actor: Dictionary = {} # actor_id -> String
var _active_gaze_target: Dictionary = {}      # actor_id -> String (telemetry)


func reset() -> void:
	_last_gesture_by_actor.clear()
	_last_line_key_by_actor.clear()
	_gesture_choice_by_actor.clear()
	_active_gaze_target.clear()


## Choose an appropriate non-combat idle style from context (BUG-006).
func choose_idle_style(
	actor_id: String,
	actor_cfg: Dictionary,
	scene_type: String,
	emotion: String,
	is_speaking: bool,
	is_listening: bool,
	active_events: Array[String]
) -> String:
	var explicit: String = str(actor_cfg.get("idle_style", "")).strip_edges().to_lower()
	if explicit in ["relaxed_idle", "professional_idle", "alert_idle", "combat_idle", "conversation_idle"]:
		return explicit
	var role: String = str(actor_cfg.get("role", "")).strip_edges().to_lower()
	if role == "combat" or role == "soldier":
		return "combat_idle"
	if emotion == "angry" or "red_alert" in active_events:
		return "alert_idle" if not is_listening else "conversation_idle"
	if is_speaking or is_listening:
		return "conversation_idle"
	if scene_type in ["med_lab", "command_deck", "observation_bay"]:
		return "professional_idle"
	return "relaxed_idle"


## Classify semantic gesture intent from Chinese/English line text & emotion (BUG-007).
func _classify_line_gesture(actor_id: String, line_text: String, emotion: String, has_device: bool, line_key: String) -> String:
	if _last_line_key_by_actor.get(actor_id, "") == line_key and _gesture_choice_by_actor.has(actor_id):
		return str(_gesture_choice_by_actor[actor_id])

	var t: String = line_text.strip_edges()
	var preferred := "open_explain"
	if emotion == "angry" or t.contains("为什么") or t.contains("站住") or t.contains("警报") or t.contains("不报"):
		preferred = "confront_lean"
	elif (has_device and (t.contains("信号") or t.contains("读数") or t.contains("终端") or t.contains("稳定") or t.contains("窗口") or t.contains("三号站") or t.contains("校准"))):
		preferred = "point_device"
	elif emotion in ["worried", "fear", "sad"] or t.contains("担心") or t.contains("风暴") or t.contains("断了") or t.contains("不对"):
		preferred = "worry_check"
	elif emotion in ["happy", "confident"] or t.contains("放心") or t.contains("交给我") or t.contains("拿好") or t.contains("备件"):
		preferred = "reassure_palm"

	# Prevent mechanical repetition on back-to-back lines by the same actor.
	var prev: String = str(_last_gesture_by_actor.get(actor_id, ""))
	if preferred == prev:
		var idx: int = GESTURE_CYCLE.find(preferred)
		preferred = GESTURE_CYCLE[(idx + 1) % GESTURE_CYCLE.size()]

	_last_line_key_by_actor[actor_id] = line_key
	_gesture_choice_by_actor[actor_id] = preferred
	_last_gesture_by_actor[actor_id] = preferred
	return preferred


## Evaluates one actor's performance overlay for the current frame.
## Returns a Dictionary consumed by Character3D.set_performance_overlay().
func evaluate_actor(
	actor_id: String,
	actor_node: Node3D,
	actor_cfg: Dictionary,
	frame_index: int,
	fps: int,
	scene_type: String,
	emotion: String,
	is_speaking: bool,
	is_listening: bool,
	line_text: String,
	line_start_frame: int,
	line_end_frame: int,
	is_moving: bool,
	holding_right: bool,
	holding_left: bool,
	prop_reach_active: bool,
	nearest_device: Dictionary,
	active_events: Array[String]
) -> Dictionary:
	var idle_style: String = choose_idle_style(
		actor_id, actor_cfg, scene_type, emotion, is_speaking, is_listening, active_events
	)
	var out: Dictionary = {
		"idle_style": idle_style,
		"gesture": "none",
		"torso_lean_deg": 0.0,
		"torso_roll_deg": 0.0,
		"right_reach_weight": 0.0,
		"left_reach_weight": 0.0,
		"open_hand_weight": 0.0,
		"device_operating": false,
		"device_id": "",
	}
	if is_moving or prop_reach_active:
		return out

	var h: float = float(actor_node.get("height_m")) if actor_node.get("height_m") != null else 1.7
	var t_s: float = float(frame_index) / float(maxi(fps, 1))
	var seed_off: float = float(absi(actor_id.hash()) % 17) * 0.41

	# Interactive device operation when standing right in front of a console/terminal (BUG-010).
	var has_device: bool = not nearest_device.is_empty()
	var dev_dist: float = float(nearest_device.get("distance_m", 99.0))
	var dev_anchor: Vector3 = nearest_device.get("interaction_anchor", Vector3.ZERO) as Vector3
	var dev_look: Vector3 = nearest_device.get("look_target", Vector3.ZERO) as Vector3
	var dev_id: String = str(nearest_device.get("id", ""))
	var should_operate: bool = has_device and dev_dist <= 1.25 and not holding_right and (
		(not is_speaking and not is_listening)
		or ("device_operating" in active_events)
	)
	if should_operate and dev_anchor != Vector3.ZERO:
		# Verify actor is roughly facing the console before reaching to tap controls.
		var to_dev: Vector3 = dev_anchor - actor_node.global_position
		to_dev.y = 0.0
		var fwd: Vector3 = actor_node.global_transform.basis * Vector3(0.0, 0.0, 1.0)
		fwd.y = 0.0
		if to_dev.length() > 0.05 and fwd.normalized().dot(to_dev.normalized()) > 0.35:
			var tap_wave: float = 0.5 + 0.5 * sin(t_s * 3.4 + seed_off)
			var tap_pos: Vector3 = dev_anchor + Vector3(0.06 * sin(t_s * 1.9 + seed_off), 0.03 * tap_wave, 0.0)
			out["gesture"] = "operate_console"
			out["torso_lean_deg"] = 2.8
			out["right_reach_weight"] = 0.62
			out["right_reach_world"] = tap_pos
			out["open_hand_weight"] = 0.75
			out["device_operating"] = true
			out["device_id"] = dev_id
			out["device_look_target"] = dev_look
			return out

	# Dialogue gestures for active speaker (BUG-007).
	if is_speaking:
		var line_key := "%d:%d:%s" % [line_start_frame, line_end_frame, line_text.left(12)]
		var gesture: String = _classify_line_gesture(actor_id, line_text, emotion, has_device, line_key)
		var span: float = maxf(float(line_end_frame - line_start_frame), 12.0)
		var u: float = clampf(float(frame_index - line_start_frame) / span, 0.0, 1.0)
		# Smooth bell-shaped envelope so gesture enters & exits naturally during the line.
		var env: float = sin(clampf(u, 0.0, 1.0) * PI)
		env = clampf(env * 1.35, 0.0, 1.0)
		var beat_osc: float = sin(t_s * 5.2 + seed_off)
		out["gesture"] = gesture

		match gesture:
			"confront_lean":
				out["torso_lean_deg"] = 5.0 * env
				if not holding_right:
					var r_pt: Vector3 = actor_node.global_transform * Vector3(-0.24, h * 0.66 + 0.05 * beat_osc, 0.42)
					out["right_reach_weight"] = 0.62 * env
					out["right_reach_world"] = r_pt
					out["open_hand_weight"] = 0.85 * env
			"point_device":
				out["torso_lean_deg"] = 2.2 * env
				var target_pt: Vector3 = dev_look if (has_device and dev_look != Vector3.ZERO) else (actor_node.global_transform * Vector3(-0.32, h * 0.70, 0.48))
				# Aim hand halfway toward the device/screen without overextending.
				var shoulder_w: Vector3 = actor_node.global_transform * Vector3(-0.18, h * 0.78, 0.05)
				var dir_d: Vector3 = (target_pt - shoulder_w).normalized()
				var hand_pt: Vector3 = shoulder_w + dir_d * 0.42 + Vector3(0.0, 0.02 * beat_osc, 0.0)
				if not holding_right:
					out["right_reach_weight"] = 0.65 * env
					out["right_reach_world"] = hand_pt
					out["open_hand_weight"] = 0.80 * env
				elif not holding_left:
					out["left_reach_weight"] = 0.58 * env
					out["left_reach_world"] = hand_pt
					out["open_hand_weight"] = 0.80 * env
			"worry_check":
				out["torso_lean_deg"] = 1.8 * env
				out["torso_roll_deg"] = 2.4 * sin(t_s * 1.6 + seed_off) * env
				if not holding_left:
					var l_pt: Vector3 = actor_node.global_transform * Vector3(0.14, h * 0.64 + 0.02 * beat_osc, 0.30)
					out["left_reach_weight"] = 0.50 * env
					out["left_reach_world"] = l_pt
					out["open_hand_weight"] = 0.85 * env
			"reassure_palm":
				out["torso_lean_deg"] = 2.5 * env
				if not holding_right:
					var r_pt2: Vector3 = actor_node.global_transform * Vector3(-0.22, h * 0.63 + 0.03 * beat_osc, 0.38)
					out["right_reach_weight"] = 0.56 * env
					out["right_reach_world"] = r_pt2
					out["open_hand_weight"] = 0.95 * env
				elif not holding_left:
					var l_pt2: Vector3 = actor_node.global_transform * Vector3(0.20, h * 0.63 + 0.03 * beat_osc, 0.36)
					out["left_reach_weight"] = 0.54 * env
					out["left_reach_world"] = l_pt2
					out["open_hand_weight"] = 0.95 * env
			"open_both":
				out["torso_lean_deg"] = 2.0 * env
				if not holding_right:
					out["right_reach_weight"] = 0.48 * env
					out["right_reach_world"] = actor_node.global_transform * Vector3(-0.26, h * 0.62 + 0.03 * beat_osc, 0.34)
				if not holding_left:
					out["left_reach_weight"] = 0.44 * env
					out["left_reach_world"] = actor_node.global_transform * Vector3(0.25, h * 0.61 - 0.02 * beat_osc, 0.32)
				out["open_hand_weight"] = 0.90 * env
			_:
				# open_explain / beat_right / chest_hold
				out["torso_lean_deg"] = 2.2 * env
				if not holding_right:
					var r_pt3: Vector3 = actor_node.global_transform * Vector3(-0.23, h * 0.62 + 0.04 * beat_osc, 0.36)
					out["right_reach_weight"] = 0.52 * env
					out["right_reach_world"] = r_pt3
					out["open_hand_weight"] = 0.90 * env
				elif not holding_left:
					var l_pt3: Vector3 = actor_node.global_transform * Vector3(0.22, h * 0.62 + 0.04 * beat_osc, 0.35)
					out["left_reach_weight"] = 0.50 * env
					out["left_reach_world"] = l_pt3
					out["open_hand_weight"] = 0.90 * env
	elif is_listening:
		# Subtle attentive listening posture (never frozen stiff).
		out["gesture"] = "listen_attentive"
		out["torso_lean_deg"] = 1.2 if emotion in ["worried", "angry"] else 0.6
		out["torso_roll_deg"] = 1.4 * sin(t_s * 0.8 + seed_off)

	return out
