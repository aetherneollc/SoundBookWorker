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
const RetroArtScript := preload("res://player/retro_art_profile.gd")
const SceneEventScript := preload("res://player/scene_event_controller.gd")
const BayScript := preload("res://player/bay.gd")
const LightingScript := preload("res://player/lighting_profiles.gd")

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
	var clip_sprint: String = str(linlan.call("resolve_locomotion_clip", "sprint"))
	_assert(clip_conf == "walk_formal", "confident gait selects walk_formal (got %s)" % clip_conf)
	_assert(clip_cas == "walk", "casual gait selects walk (got %s)" % clip_cas)
	_assert(clip_caut == "crouch_walk", "cautious gait selects crouch_walk (got %s)" % clip_caut)
	if bool(linlan.call("has_clip", "Sprint_Urgent_Upright")):
		_assert(clip_urg == "Sprint_Urgent_Upright", "urgent gait selects Sprint_Urgent_Upright (got %s)" % clip_urg)
		_assert(clip_sprint == "Sprint_Urgent_Upright", "sprint gait selects Sprint_Urgent_Upright (got %s)" % clip_sprint)
	else:
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
	_assert(float(fsnap.get("jaw_open", 0.0)) > 0.20 and float(fsnap.get("jaw_open", 0.0)) <= 0.56, "linlan jawOpen naturally bounded during speech (got %s)" % str(fsnap.get("jaw_open")))
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

	# 6. Retro art profile, mechanical scene events, and 4-phase dramatic actions.
	var neutral_art: Dictionary = RetroArtScript.resolve({})
	_assert(not bool(neutral_art.get("enabled", true)), "omitted art_profile stays neutral")
	_assert(not bool(neutral_art.get("pixelation", true)), "pixelation defaults off")
	_assert(not bool(neutral_art.get("crt_scanlines", true)), "CRT scanlines default off")
	_assert(not bool(neutral_art.get("texture_jitter", true)), "texture jitter defaults off")
	_assert(not bool(neutral_art.get("environment_dressing", true)), "environment dressing stays off without a profile")
	var opted: Dictionary = RetroArtScript.resolve({
		"preset": "ps2_urban",
		"pixelation": true,
		"crt_scanlines": true,
		"texture_jitter": true,
	})
	_assert(bool(opted.get("pixelation", false)) and bool(opted.get("crt_scanlines", false)) and bool(opted.get("texture_jitter", false)), "pixelation/CRT/jitter are strictly opt-in")
	for preset_name: String in RetroArtScript.preset_ids():
		var preset: Dictionary = RetroArtScript.resolve({"preset": preset_name})
		_assert(bool(preset.get("enabled", false)), "%s profile enables" % preset_name)
		_assert(not bool(preset.get("pixelation", true)) and not bool(preset.get("crt_scanlines", true)) and not bool(preset.get("texture_jitter", true)), "%s keeps gimmick flags off" % preset_name)
		var wall_tex: Texture2D = RetroArtScript.make_texture("wall_plate", preset)
		_assert(wall_tex != null and wall_tex.get_width() == 256 and wall_tex.get_height() == 256, "%s wall texture is 256x256" % preset_name)
	var floor_tex: Texture2D = RetroArtScript.make_texture("floor_plate", RetroArtScript.resolve({"preset": "retro_industrial_scifi"}))
	_assert(floor_tex != null and floor_tex.get_width() == 256, "industrial floor texture is 256x256")
	var neutral_fx: Node = RetroArtScript.attach_post_fx(root, neutral_art)
	_assert(neutral_fx == null, "neutral profile does not add a post-FX layer")
	var urban: Dictionary = RetroArtScript.resolve({"preset": "ps2_urban"})
	var fx: Node = RetroArtScript.attach_post_fx(root, urban)
	_assert(fx != null, "active retro profile adds a post-FX overlay")
	var grade: ColorRect = fx.get_node("Grade") as ColorRect
	var grade_mat: ShaderMaterial = grade.material as ShaderMaterial
	_assert(grade_mat != null and not bool(grade_mat.get_shader_parameter("pixelation")) and not bool(grade_mat.get_shader_parameter("crt_scanlines")) and not bool(grade_mat.get_shader_parameter("texture_jitter")), "post-FX uniforms default pixelation/CRT/jitter off")
	fx.queue_free()
	_assert(LightingScript.storm_flash_amount(0.02, 1.0) > 0.5, "storm lightning flash is periodic, not continuous")
	_assert(LightingScript.storm_flash_amount(0.6, 1.0) < 0.05, "storm lightning is dark between bolts")
	_assert(LightingScript.power_stutter_mul(0.0, 1.0) < 0.2, "power flicker dips overhead lights")
	_assert(absf(LightingScript.power_stutter_mul(0.0, 0.0) - 1.0) < 0.001, "power flicker is a no-op when inactive")

	linlan.call("apply_retro_material_finish", neutral_art)
	var finish_off: Dictionary = linlan.call("get_retro_finish_report")
	_assert(not bool(finish_off.get("applied", true)), "neutral art profile does not retune character materials")
	linlan.call("apply_retro_material_finish", RetroArtScript.resolve({"preset": "retro_industrial_scifi"}))
	var finish_on: Dictionary = linlan.call("get_retro_finish_report")
	_assert(bool(finish_on.get("applied", false)), "retro profile applies character material finish")
	_assert(float(finish_on.get("roughness_min", 0.0)) >= 0.78, "clothing/skin roughness is matte (got %s)" % str(finish_on.get("roughness_min")))
	_assert(float(finish_on.get("specular_max", 1.0)) <= 0.08, "skin/cloth specular hotspots are suppressed (got %s)" % str(finish_on.get("specular_max")))

	var ev_only: RefCounted = SceneEventScript.new()
	ev_only.call("setup", [
		{"type": "power_flicker", "from": 0, "to": 12},
		{"type": "array_restore", "from": 0, "to": 12},
		{"type": "door_open", "from": 0, "to": 12},
		{"type": "door_close", "from": 0, "to": 12},
		{"type": "module_install", "from": 0, "to": 12},
		{"type": "table_impact", "from": 0, "to": 12},
	], "command_deck", "day_bay", [])
	var ev_state: Dictionary = ev_only.call("update", 5, 24, null, null, null)
	var ev_active: Array = ev_state.get("active_events", [])
	for ev_name: String in ["power_flicker", "array_restore", "door_open", "door_close", "module_install", "table_impact"]:
		_assert(ev_name in ev_active, "SceneEventController handles %s" % ev_name)

	var bay_a: Node3D = BayScript.new()
	bay_a.name = "BayNeutral"
	root.add_child(bay_a)
	bay_a.call("build", {"scene_type": "corridor"}, CharacterScript)
	var vis_a: Dictionary = bay_a.call("get_event_visual_state")
	_assert(not bool(vis_a.get("art_enabled", true)), "Version A bay has no art profile")
	_assert(int(vis_a.get("dressing_nodes", 1)) == 0, "Version A has no industrial dressing")
	_assert(float(vis_a.get("door_amount", 0.0)) > 0.95, "doors rest open when no door event is playing")

	var bay_b: Node3D = BayScript.new()
	bay_b.name = "BayRetro"
	root.add_child(bay_b)
	bay_b.call("build", {
		"scene_type": "command_deck",
		"art_profile": {"preset": "retro_industrial_scifi"},
		"backdrop": {"mode": "none"},
		"props": [{"id": "console_a", "kind": "console", "pos": [-1.6, 0.0, 0.4]}],
	}, CharacterScript)
	var vis_boot: Dictionary = bay_b.call("get_event_visual_state")
	_assert(bool(vis_boot.get("art_enabled", false)), "Version B art profile is active")
	_assert(int(vis_boot.get("dressing_nodes", 0)) > 8, "Version B adds environmental dressing (got %s)" % str(vis_boot.get("dressing_nodes")))
	var rig_b: Node = bay_b.call("get_lighting_rig")
	_assert(rig_b != null and str(rig_b.get_meta("art_preset", "")) == "retro_industrial_scifi", "lighting rig received the industrial retro profile")

	var ev_bay: RefCounted = SceneEventScript.new()
	ev_bay.call("setup", [
		{"type": "power_flicker", "from": 0, "to": 8},
		{"type": "door_close", "from": 0, "to": 20},
		{"type": "signal_lost", "from": 10, "to": 30},
		{"type": "door_open", "from": 30, "to": 50},
		{"type": "array_restore", "from": 40, "to": 70},
		{"type": "module_install", "from": 72, "to": 96},
		{"type": "table_impact", "from": 100, "to": 112},
		{"type": "storm_approaching", "from": 110, "to": 130},
	], "command_deck", "day_bay", [])
	ev_bay.call("update", 0, 24, bay_b, null, null)
	var vis_flick: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_flick.get("emergency_energy", 0.0)) > 0.8, "power flicker turns emergency strips on (got %s)" % str(vis_flick.get("emergency_energy")))
	_assert(float(vis_flick.get("light_bar_energy", 9.0)) < float(vis_flick.get("light_bar_base", 0.0)) * 0.35, "power flicker dips overhead bars")
	_assert(str(vis_flick.get("screen_mode", "")) == "power_flicker", "screens show the power flicker mode")
	for frame_i in range(1, 30):
		ev_bay.call("update", frame_i, 24, bay_b, null, null)
	var vis_fault: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_fault.get("door_amount", 1.0)) < 0.3, "door_close slides the bulkhead shut (got %s)" % str(vis_fault.get("door_amount")))
	_assert(float(vis_fault.get("array_pitch_deg", 0.0)) > 20.0, "signal_lost slumps the exterior array (got %s)" % str(vis_fault.get("array_pitch_deg")))
	_assert(float(vis_fault.get("beacon_r", 0.0)) > float(vis_fault.get("beacon_g", 1.0)), "fault beacon is red")
	for frame_i2 in range(30, 71):
		ev_bay.call("update", frame_i2, 24, bay_b, null, null)
	var vis_lock: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_lock.get("door_amount", 0.0)) > 0.65, "door_open slides the bulkhead open (got %s)" % str(vis_lock.get("door_amount")))
	_assert(float(vis_lock.get("array_pitch_deg", 40.0)) < float(vis_fault.get("array_pitch_deg", 0.0)) - 20.0, "array_restore elevates the dish (got %s)" % str(vis_lock.get("array_pitch_deg")))
	_assert(float(vis_lock.get("beacon_g", 0.0)) > float(vis_lock.get("beacon_r", 1.0)), "array lock beacon turns cyan-green")
	_assert(bool(vis_lock.get("beam_visible", false)), "array lock shows the uplink beam")
	_assert(str(vis_lock.get("screen_mode", "")) == "locked_carrier", "array restore shows a locked carrier screen")
	for frame_i3 in range(72, 96):
		ev_bay.call("update", frame_i3, 24, bay_b, null, null)
	var vis_mod: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_mod.get("led_g", 0.0)) > float(vis_mod.get("led_r", 1.0)), "module_install locks the console LED green")
	ev_bay.call("update", 100, 24, bay_b, null, null)
	var vis_hit: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_hit.get("jolt_m", 0.0)) > 0.008, "table_impact recoils the console (got %s)" % str(vis_hit.get("jolt_m")))
	_assert(str(vis_hit.get("screen_mode", "")) == "impact_spike", "table impact jumps the console waveform")
	ev_bay.call("update", 116, 24, bay_b, null, null)
	var vis_storm: Dictionary = bay_b.call("get_event_visual_state")
	_assert(float(vis_storm.get("lightning", 0.0)) > 0.25, "storm_approaching emits a lightning flash (got %s)" % str(vis_storm.get("lightning")))

	linlan.call("set_dramatic_actions", [{
		"from": 0,
		"to": 48,
		"action_id": "slam_table",
		"base_clip": "hit_chest",
		"intensity": 1.0,
		"target": "console_a",
		"phases": {
			"prepare": {"portion": 0.22},
			"execute": {"portion": 0.30},
			"follow_through": {"portion": 0.24},
			"recover": {"portion": 0.24},
		},
	}])
	var seen_phases := {}
	var prep_hand_y := -999.0
	var exec_hand_y := 999.0
	for dram_f in 49:
		linlan.call("update_dramatic_action", dram_f, 24)
		var dram_clip: String = str(linlan.call("dramatic_playback_clip"))
		var play_clip := dram_clip if dram_clip != "" else "idle"
		linlan.call("update_animation", play_clip, "dramatic-test", 1.0, 1.0 / 24.0, 0.08)
		linlan.call("apply_overlays", 0.0, 0.0, Vector3.ZERO, 0.0, "right")
		var dram_state: Dictionary = linlan.call("get_dramatic_state")
		var phase_name := str(dram_state.get("phase", ""))
		seen_phases[phase_name] = true
		var hand_pos: Vector3 = linlan.call("bone_world_position", "r_hand")
		var hand_y: float = hand_pos.y
		if phase_name == "prepare":
			prep_hand_y = maxf(prep_hand_y, hand_y)
		elif phase_name == "execute":
			exec_hand_y = minf(exec_hand_y, hand_y)
	_assert(bool(seen_phases.get("prepare", false)) and bool(seen_phases.get("execute", false)) and bool(seen_phases.get("follow_through", false)) and bool(seen_phases.get("recover", false)), "dramatic action visits all 4 phases")
	_assert(exec_hand_y < prep_hand_y - 0.08, "slam execute drops the hand below the wind-up (exec %.3f prep %.3f)" % [exec_hand_y, prep_hand_y])
	var snap_end: Dictionary = linlan.call("get_qc_snapshot")
	var end_l: Array = snap_end.get("left_foot", [0, 9, 0])
	var end_r: Array = snap_end.get("right_foot", [0, 9, 0])
	var end_stance: float = minf(float(end_l[1]), float(end_r[1]))
	_assert(absf(end_stance) < 0.035, "dramatic action keeps the stance foot grounded (got %.4f m)" % end_stance)

	CharacterScript.clear_cache()
	if _failures.is_empty():
		print("ALL P0/P1 RUNTIME CHECKS PASSED!")
		quit(0)
	else:
		push_error("P0/P1 RUNTIME CHECKS FAILED (%d)" % _failures.size())
		quit(1)
