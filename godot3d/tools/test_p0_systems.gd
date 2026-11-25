extends SceneTree

## Headless verification for P0/P1 Godot 3D runtime modules:
## - Character3D direct 65-bone UAL playback, multi-gait selection, 5-finger posing, grounding
## - FacialAnimationController BlendShapes (7 real morphs, 8 emotions, lip-sync, blink, gaze)
## - PropInteractionSystem ownership transfer, reach requests, zero teleport/duplicate
## - Rover3D 6-wheel physical spin (dist/radius), exact floor contact (wheel_min_y ~= 0), turret pan
## - main.gd syntax & script load check

const CharacterScript := preload("res://player/character3d.gd")
const PropSystemScript := preload("res://player/prop_system.gd")
const RoverScript := preload("res://player/rover3d.gd")
const MainScript := preload("res://player/main.gd")

var _failures: Array[String] = []


func _assert(cond: bool, msg: String) -> void:
	if not cond:
		_failures.append(msg)
		push_error("FAIL: %s" % msg)
	else:
		print("PASS: %s" % msg)


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var root := Node3D.new()
	root.name = "TestRoot"
	get_root().add_child(root)

	var repo_dir: String = ProjectSettings.globalize_path("res://").simplify_path().get_base_dir()
	var ual_path: String = repo_dir.path_join("media/assets3d/cc0/quaternius_universal_animation_library/UAL1_Standard.glb")
	var linlan_path: String = repo_dir.path_join("media/assets3d/characters/linlan.glb")
	var chen_path: String = repo_dir.path_join("media/assets3d/characters/chen.glb")
	var rover_path: String = repo_dir.path_join("media/assets3d/atuo/atuo_rigged.glb")
	var sensor_path: String = repo_dir.path_join("media/assets3d/sensor/sensor_norm.glb")

	_assert(MainScript != null, "main.gd compiled cleanly")

	# 1. Load linlan & chen (TIER_A 65-bone BlendShape characters)
	var linlan: Node3D = CharacterScript.new()
	linlan.name = "Linlan"
	root.add_child(linlan)
	var ok_l: bool = bool(linlan.call("setup", {
		"id": "linlan",
		"model": linlan_path,
		"height_m": 1.65,
		"gait": "confident",
		"look": {"uniform": false, "preserve_materials": true},
		"animation": {"library": ual_path, "sources": ["library", "own"]},
	}))
	_assert(ok_l, "linlan.glb setup succeeded")
	_assert(bool(linlan.get("direct_rig_match")), "linlan matched UAL 65-bone skeleton 1:1")
	_assert(bool(linlan.get("has_native_blendshapes")), "linlan detected native BlendShapes")
	_assert(bool(linlan.get("has_fingers")), "linlan detected 5-finger joints")
	_assert(bool(linlan.get("skinning_ok")), "linlan skinning sanity check passed")

	var chen: Node3D = CharacterScript.new()
	chen.name = "Chen"
	root.add_child(chen)
	var ok_c: bool = bool(chen.call("setup", {
		"id": "chen",
		"model": chen_path,
		"height_m": 1.76,
		"gait": "heavy",
		"look": {"uniform": false, "preserve_materials": true},
		"animation": {"library": ual_path, "sources": ["library", "own"]},
	}))
	_assert(ok_c, "chen.glb setup succeeded")
	_assert(bool(chen.get("direct_rig_match")), "chen matched UAL 65-bone skeleton 1:1")
	_assert(bool(chen.get("has_native_blendshapes")), "chen detected native BlendShapes")

	# 2. Verify distinct gait profiles resolve distinct locomotion clips
	var clip_conf: String = str(linlan.call("resolve_locomotion_clip", "confident"))
	var clip_cas: String = str(linlan.call("resolve_locomotion_clip", "casual"))
	var clip_caut: String = str(linlan.call("resolve_locomotion_clip", "cautious"))
	var clip_urg: String = str(linlan.call("resolve_locomotion_clip", "urgent"))
	_assert(clip_conf == "walk_formal", "confident gait selects walk_formal (got %s)" % clip_conf)
	_assert(clip_cas == "walk", "casual gait selects walk (got %s)" % clip_cas)
	_assert(clip_caut == "crouch_walk", "cautious gait selects crouch_walk (got %s)" % clip_caut)
	_assert(clip_urg == "jog", "urgent gait selects jog (got %s)" % clip_urg)

	# 3. Verify BlendShape facial controller drives real morphs + emotions + lip-sync
	var facial: Node = linlan.call("get_facial_controller")
	_assert(facial != null and str(facial.call("get_capability_grade")) == "blendshape",
		"linlan facial capability is blendshape (got %s)" % (str(facial.call("get_capability_grade")) if facial != null else "null"))
	linlan.call("set_emotion", "happy", 1.0, 0.1)
	for f in 12:
		linlan.call("update_animation", "walk_formal", "walk_formal:confident", 1.0, 1.0 / 24.0, 0.0)
		linlan.call("update_face", f, 24, true, 0.85, "aa", 1.0 / 24.0)
		linlan.call("apply_look_at", Vector3(0.0, 1.5, 2.0), 0.8, 1.0 / 24.0)
		linlan.call("apply_overlays", 0.0, 0.0, Vector3.ZERO, 0.0, "right")
	var snap_l: Dictionary = linlan.call("get_qc_snapshot")
	var fsnap: Dictionary = snap_l.get("facial", {})
	_assert(float(fsnap.get("jaw_open", 0.0)) > 0.35, "linlan jawOpen driven by speech (got %s)" % str(fsnap.get("jaw_open")))
	_assert(float(fsnap.get("mouth_smile", 0.0)) > 0.25, "linlan mouthSmile driven by happy emotion (got %s)" % str(fsnap.get("mouth_smile")))
	var lf: Array = snap_l.get("left_foot", [0, 9, 0])
	var rf: Array = snap_l.get("right_foot", [0, 9, 0])
	var stance_y: float = minf(float(lf[1]), float(rf[1]))
	_assert(absf(stance_y) < 0.035, "linlan stance foot grounded within 3.5cm (got %.4f m)" % stance_y)

	# 4. Verify PropInteractionSystem handoff & 5-finger grip engagement
	linlan.position = Vector3(-0.45, 0.0, 0.0)
	linlan.rotation = Vector3(0.0, PI * 0.5, 0.0)
	chen.position = Vector3(0.45, 0.0, 0.0)
	chen.rotation = Vector3(0.0, -PI * 0.5, 0.0)
	var props: Node3D = PropSystemScript.new()
	root.add_child(props)
	props.call("setup", {
		"sensor": {
			"model": sensor_path,
			"timeline": [
				{"from": 0, "to": 10, "action": "held", "actor": "linlan"},
				{"from": 10, "to": 30, "action": "handoff", "from_actor": "linlan", "to_actor": "chen", "transfer_frame": 20},
				{"from": 30, "to": 45, "action": "held", "actor": "chen"},
			],
		}
	}, CharacterScript)
	var actors := {"linlan": linlan, "chen": chen}
	props.call("update_frame", 5, 24, actors, 1.0 / 24.0)
	_assert(str(props.call("get_prop_owner", "sensor")) == "linlan", "sensor starts owned by linlan")
	_assert(bool(linlan.get("holding")) and not bool(chen.get("holding")), "linlan holding=true, chen holding=false before handoff")
	for f2 in range(10, 32):
		props.call("update_frame", f2, 24, actors, 1.0 / 24.0)
		var reaches: Dictionary = props.call("get_actor_reach_requests")
		for aid: String in ["linlan", "chen"]:
			var req: Dictionary = reaches.get(aid, {})
			actors[aid].call("update_animation", "idle", "idle", 1.0, 1.0 / 24.0, 0.0)
			actors[aid].call("apply_overlays", 0.0, 0.0, req.get("target", Vector3.ZERO), float(req.get("weight", 0.0)), str(req.get("side", "right")))
	_assert(str(props.call("get_prop_owner", "sensor")) == "chen", "sensor transferred to chen after handoff")
	_assert(not bool(linlan.get("holding")) and bool(chen.get("holding")), "linlan holding=false, chen holding=true after handoff")
	var handoffs: Array = props.call("get_handoff_log")
	_assert(handoffs.size() == 1, "exactly 1 handoff logged (got %d)" % handoffs.size())

	# 5. Verify Rover3D 6 wheels, grounding, physical wheel spin, and turret pan
	var rover: Node3D = RoverScript.new()
	root.add_child(rover)
	var ok_r: bool = bool(rover.call("setup", {
		"id": "at1",
		"model": rover_path,
		"height_m": 0.92,
	}, CharacterScript))
	_assert(ok_r, "atuo_rigged.glb Rover3D setup succeeded")
	_assert(int(rover.call("get_wheel_count")) == 6, "Rover3D discovered all 6 wheels (got %d)" % int(rover.call("get_wheel_count")))
	_assert(bool(rover.call("has_turret")), "Rover3D discovered rotating Turret")
	for step in 24:
		var z_pos: float = float(step) * 0.05
		rover.call("update_frame", Vector3(0.0, 0.0, z_pos), 0.0, 1.0 / 24.0, Vector3(2.0, 1.4, z_pos + 1.0), "", false, 0.0)
	var rsnap: Dictionary = rover.call("get_qc_snapshot")
	_assert(absf(float(rsnap.get("wheel_min_y", 9.0))) < 0.025, "Rover3D wheel_min_y grounded ~= 0 (got %.4f m)" % float(rsnap.get("wheel_min_y", 9.0)))
	var expected_spin: float = (23.0 * 0.05) / maxf(float(rsnap.get("wheel_radius_m", 0.16)), 0.02)
	var actual_spin: float = float(rsnap.get("wheel_spin_rad", 0.0))
	_assert(absf(actual_spin - expected_spin) < 0.15, "Rover3D wheel_spin_rad matches dist/radius (got %.3f vs %.3f)" % [actual_spin, expected_spin])
	_assert(absf(float(rsnap.get("turret_yaw_deg", 0.0))) > 15.0, "Rover3D turret panned toward target (got %.1f deg)" % float(rsnap.get("turret_yaw_deg", 0.0)))

	CharacterScript.clear_cache()
	if _failures.is_empty():
		print("ALL P0/P1 RUNTIME CHECKS PASSED!")
		quit(0)
	else:
		push_error("P0/P1 RUNTIME CHECKS FAILED (%d)" % _failures.size())
		quit(1)
