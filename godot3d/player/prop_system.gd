class_name PropInteractionSystem
extends Node3D

## Data-driven interactive prop manager for stage3d.json.
##
## Supports standard local sockets per prop:
##   - grip:           palm/finger wrap transform
##   - receive:        receiver hand contact point during handoff
##   - operate:        tool/screen tip for inspect/use aiming
##   - surface_rest_y: exact bottom offset so resting on a surface never floats/sinks
##
## Supports timeline-driven states & actions:
##   surface | held | handoff | pickup | inspect | use | put_down | hidden
## as well as legacy stage3d.json `owners` + `handoff_window` specs.
##
## Causal prop chain (declarative `handoff` / `install_into_socket` timelines):
##   held_by:<giver> -> transfer_contact -> held_by:<receiver>
##   -> align_socket -> insert_socket -> locked -> device_online
## The module has one owner per frame. `device_online` cannot precede `locked`.
##
## Emits per-frame deterministic telemetry for the QC pipeline.

const DEFAULT_HANDOFF_HEIGHT := 1.16
const TORSO_CLEARANCE_RADIUS := 0.34
const DEFAULT_MAX_HANDOFF_GAP := 0.28
const HAND_MIN_SEPARATION_M := 0.085
const RECEIVER_HOLD_OVERLAP_FRAMES := 5
const CONTACT_TARGET_M := 0.025
const LOCK_ALIGN_TARGET_M := 0.015
const POST_LOCK_DRIFT_M := 0.005
const PRE_INSERT_OFFSET_M := 0.16

var _character_script: Script
var _props: Dictionary = {}        # prop_id -> Dictionary runtime state
var _handoff_log: Array[Dictionary] = []
var _reach_requests: Dictionary = {}  # actor_id -> {target: Vector3, weight: float, side: String, prop_id: String}
var _gaze_requests: Dictionary = {}   # actor_id -> {point: Vector3, weight: float, prop_id: String}
var _bay: Node = null
var _camera_pos := Vector3(0.0, 1.6, 3.2)
var _ownership_log: Array[Dictionary] = []
var _device_timeline: Array[Dictionary] = []
var _invariant_violations: Array[Dictionary] = []
var _frame_metrics: Array[Dictionary] = []
var _contact_samples: Array[float] = []
var _contact_frame_errors: Array[float] = []
var _lock_frame_errors: Array[float] = []
var _sound_cues: Array[Dictionary] = []
var _has_install := false
var _device_online := false
var _last_frame := 0


func setup(props_cfg: Dictionary, character_script: Script = null) -> void:
	_character_script = character_script
	clear_props()
	for prop_key: Variant in props_cfg.keys():
		var prop_id: String = str(prop_key)
		var spec_v: Variant = props_cfg[prop_key]
		if spec_v is Dictionary:
			register_prop(prop_id, spec_v as Dictionary)


func clear_props() -> void:
	for prop_id: Variant in _props.keys():
		var entry: Dictionary = _props[prop_id]
		var root_node: Node3D = entry.get("root") as Node3D
		if root_node != null and is_instance_valid(root_node):
			if root_node.get_parent() != null:
				root_node.get_parent().remove_child(root_node)
			root_node.queue_free()
	_props.clear()
	_handoff_log.clear()
	_reach_requests.clear()
	_gaze_requests.clear()
	_ownership_log.clear()
	_device_timeline.clear()
	_invariant_violations.clear()
	_frame_metrics.clear()
	_contact_samples.clear()
	_contact_frame_errors.clear()
	_lock_frame_errors.clear()
	_sound_cues.clear()
	_has_install = false
	_device_online = false


func register_prop(prop_id: String, spec: Dictionary) -> Node3D:
	var root := Node3D.new()
	root.name = "Prop_%s" % prop_id
	add_child(root)

	var grip_frame := Node3D.new()
	grip_frame.name = "GripFrame"
	root.add_child(grip_frame)

	var model: Node3D = _load_or_build_model(prop_id, spec)
	model.name = "Model"
	grip_frame.add_child(model)

	var scale_val: float = float(spec.get("scale", 1.0))
	if scale_val > 0.001 and absf(scale_val - 1.0) > 0.0001:
		model.scale = Vector3.ONE * scale_val

	var sockets_cfg: Dictionary = spec.get("sockets", {}) as Dictionary if spec.get("sockets", {}) is Dictionary else {}
	var grip_point: Vector3 = _vec3(sockets_cfg.get("grip", spec.get("grip_point", null)), Vector3.ZERO)
	var grip_rot_deg: Vector3 = _vec3(sockets_cfg.get("grip_rotation_deg", spec.get("grip_rotation_deg", null)), Vector3.ZERO)
	grip_frame.rotation_degrees = grip_rot_deg
	model.position = -grip_point

	var box: AABB = _local_bounds(grip_frame, root)
	var auto_rest_y: float = -box.position.y if box.size.length_squared() > 1e-8 else 0.0
	var surface_rest_y: float = float(spec.get("surface_rest_y", auto_rest_y))

	# Standard local anchor markers inside `root` (GripAnchor, ReceiveAnchor, InspectAnchor).
	var grip_socket := Node3D.new()
	grip_socket.name = "GripAnchor"
	root.add_child(grip_socket)

	var receive_default: Vector3 = Vector3(
		0.0,
		clampf(box.size.y * 0.45, 0.08, 0.14),
		clampf(box.size.z * 0.45, 0.055, 0.10),
	)
	var receive_pos: Vector3 = _vec3(
		sockets_cfg.get("receive_anchor", sockets_cfg.get("receive", spec.get("receive_point", null))),
		receive_default,
	)
	var receive_socket := Node3D.new()
	receive_socket.name = "ReceiveAnchor"
	receive_socket.position = receive_pos
	root.add_child(receive_socket)

	var operate_default: Vector3 = Vector3(0.0, maxf(box.end.y, 0.10), maxf(box.end.z * 0.5, 0.04))
	var operate_pos: Vector3 = _vec3(
		sockets_cfg.get("inspect_anchor", sockets_cfg.get("operate", spec.get("operate_point", null))),
		operate_default,
	)
	var operate_socket := Node3D.new()
	operate_socket.name = "InspectAnchor"
	operate_socket.position = operate_pos
	root.add_child(operate_socket)

	var initial_visible: bool = bool(spec.get("visible", true))
	root.visible = initial_visible

	var state := {
		"id": prop_id,
		"entity_role": str(spec.get("entity_role", spec.get("kind", prop_id))),
		"spec": spec.duplicate(true),
		"root": root,
		"grip_frame": grip_frame,
		"model": model,
		"grip_socket": grip_socket,
		"receive_socket": receive_socket,
		"operate_socket": operate_socket,
		"inspect_socket": operate_socket,
		"surface_rest_y": surface_rest_y,
		"local_aabb": box,
		"state": "hidden" if not initial_visible else "surface",
		"owner": "",
		"owner_side": "right",
		"hand_gap_m": 0.0,
		"handoff_prev_gap": INF,
		"handoff_done_keys": {},
		"handoff_transfer_frames": {},
		"transfer_count": 0,
		"blend_offset": Transform3D.IDENTITY,
		"blend_alpha": 0.0,
		"causal_state": "",
		"causal_owner_logged": "",
		"phase_this_frame": "",
		"device_state": "offline",
		"device_online": false,
		"lock_done": false,
		"online_done": false,
		"contact_done": false,
		"contact_pending": false,
		"lock_pending": false,
		"install_tracked": false,
		"socket_err_m": -1.0,
		"giver_hand_to_prop_m": -1.0,
		"receiver_hand_to_prop_m": -1.0,
		"module_to_socket_m": -1.0,
		"desired_basis": Basis.IDENTITY,
		"has_desired_basis": false,
		"locked_transform": Transform3D.IDENTITY,
		"drift_flagged": false,
		"online_schedule_flagged": false,
		"causal_giver": "",
		"causal_receiver": "",
		"giver_side": "right",
		"receiver_side": "right",
	}
	_props[prop_id] = state
	_prepare_causal_spec(state)

	# Place at initial surface position if declared in spec.
	if spec.has("surface") and spec["surface"] is Dictionary:
		_place_on_surface(state, spec["surface"] as Dictionary, {})
	elif spec.has("start_pos"):
		_place_on_surface(state, {"pos": spec["start_pos"], "surface_y": float(spec.get("surface_y", 0.0)), "yaw_deg": float(spec.get("yaw_deg", 0.0))}, {})

	return root


func get_prop_node(prop_id: String) -> Node3D:
	if not _props.has(prop_id):
		return null
	return (_props[prop_id] as Dictionary).get("root") as Node3D


func get_prop_owner(prop_id: String) -> String:
	if not _props.has(prop_id):
		return ""
	return str((_props[prop_id] as Dictionary).get("owner", ""))


func get_prop_state(prop_id: String) -> String:
	if not _props.has(prop_id):
		return ""
	return str((_props[prop_id] as Dictionary).get("state", ""))


func get_prop_ids() -> Array[String]:
	var ids: Array[String] = []
	for key: Variant in _props.keys():
		ids.append(str(key))
	return ids


func get_socket_world_position(prop_id: String, socket_name: String = "grip") -> Vector3:
	if not _props.has(prop_id):
		return Vector3.ZERO
	var entry: Dictionary = _props[prop_id]
	var key := "%s_socket" % socket_name
	var marker: Node3D = entry.get(key, entry.get("grip_socket")) as Node3D
	if marker == null:
		return Vector3.ZERO
	return marker.global_position


func get_handoff_log() -> Array[Dictionary]:
	return _handoff_log.duplicate(true)


## Returns actor_id -> {target: Vector3, weight: float, side: String, prop_id: String}
## produced during the latest update_frame() call.
func get_actor_reach_requests() -> Dictionary:
	return _reach_requests.duplicate(true)


## Returns actor_id -> {point: Vector3, weight: float, prop_id: String}
## produced during the latest update_frame() call (e.g. inspect/use/handoff).
func get_actor_gaze_requests() -> Dictionary:
	return _gaze_requests.duplicate(true)


func set_scene_context(bay: Node) -> void:
	_bay = bay


func set_view_context(camera_pos: Vector3) -> void:
	_camera_pos = camera_pos


func has_install_interaction() -> bool:
	return _has_install


func is_device_online() -> bool:
	return _device_online


# --------------------------------------------------------- manual actions


## Immediately attaches a prop to an actor's hand socket (smoothly blending residual offset).
func attach_to_actor(prop_id: String, actor_id: String, actors: Dictionary, side: String = "right", smooth_transfer: bool = true) -> bool:
	if not _props.has(prop_id) or not actors.has(actor_id):
		return false
	var entry: Dictionary = _props[prop_id]
	var prev_owner: String = str(entry["owner"])
	var prev_side: String = str(entry["owner_side"])
	var root: Node3D = entry["root"] as Node3D
	var prev_world: Transform3D = root.global_transform
	var was_visible: bool = root.visible

	if prev_owner != "" and prev_owner != actor_id and actors.has(prev_owner):
		_set_actor_holding(actors[prev_owner], prev_side, false)

	var socket: Node3D = _actor_hand_socket(actors[actor_id], side)
	if socket == null:
		return false

	if root.get_parent() != socket:
		if root.get_parent() != null:
			root.get_parent().remove_child(root)
		socket.add_child(root)

	if smooth_transfer and was_visible and prev_owner != "":
		var residual: Transform3D = socket.global_transform.affine_inverse() * prev_world
		if residual.origin.length() <= 0.45:
			entry["blend_offset"] = residual
			entry["blend_alpha"] = 1.0
			root.transform = residual
		else:
			entry["blend_offset"] = Transform3D.IDENTITY
			entry["blend_alpha"] = 0.0
			root.transform = Transform3D.IDENTITY
	else:
		entry["blend_offset"] = Transform3D.IDENTITY
		entry["blend_alpha"] = 0.0
		root.transform = Transform3D.IDENTITY

	if prev_owner != actor_id and prev_owner != "":
		entry["transfer_count"] = int(entry["transfer_count"]) + 1

	entry["owner"] = actor_id
	entry["owner_side"] = side
	entry["state"] = "held"
	root.visible = true
	_set_actor_holding(actors[actor_id], side, true)
	return true


## Places a prop cleanly on a surface at `world_xz` + `surface_y`, respecting `surface_rest_y`.
func place_on_surface(prop_id: String, world_pos: Vector3, surface_y: float = 0.0, yaw_deg: float = 0.0, actors: Dictionary = {}) -> bool:
	if not _props.has(prop_id):
		return false
	var entry: Dictionary = _props[prop_id]
	_place_on_surface(entry, {
		"pos": [world_pos.x, world_pos.y, world_pos.z],
		"surface_y": surface_y,
		"yaw_deg": yaw_deg,
	}, actors)
	return true


## Executes an explicit atomic handoff between giver and receiver (useful for scripts/tests).
func execute_handoff(prop_id: String, from_actor: String, to_actor: String, actors: Dictionary, frame_index: int = 0, side: String = "right") -> Dictionary:
	if not _props.has(prop_id) or not actors.has(to_actor):
		return {}
	var entry: Dictionary = _props[prop_id]
	var gap := 0.0
	if actors.has(from_actor):
		var a: Vector3 = _actor_hand_world(actors[from_actor], str(entry["owner_side"]))
		var b: Vector3 = _actor_hand_world(actors[to_actor], side)
		gap = a.distance_to(b)
	var meet: Vector3 = get_socket_world_position(prop_id, "receive")
	attach_to_actor(prop_id, to_actor, actors, side, true)
	var record := {
		"prop_id": prop_id,
		"frame": frame_index,
		"from": from_actor,
		"to": to_actor,
		"hand_gap_m": snappedf(gap, 0.001),
		"meet_pos": [snappedf(meet.x, 0.001), snappedf(meet.y, 0.001), snappedf(meet.z, 0.001)],
	}
	_handoff_log.append(record)
	return record


# --------------------------------------------------------- per-frame update


## Evaluates all registered props for `frame_index` and returns per-frame QC telemetry.
func update_frame(frame_index: int, fps: int, actors: Dictionary, dt: float = -1.0) -> Dictionary:
	var step_dt: float = dt if dt > 0.0 else (1.0 / float(maxi(fps, 1)))
	_reach_requests.clear()
	_gaze_requests.clear()
	var frame_telemetry := {}

	for prop_key: Variant in _props.keys():
		var prop_id: String = str(prop_key)
		var entry: Dictionary = _props[prop_id]
		var spec: Dictionary = entry["spec"]

		if spec.has("timeline") or spec.has("actions"):
			var list_v: Variant = spec.get("timeline", spec.get("actions", []))
			_eval_timeline(entry, _dict_array(list_v), frame_index, fps, actors)
		elif spec.has("owners"):
			_eval_legacy_owners(entry, _dict_array(spec.get("owners", [])), frame_index, actors)

		# Smoothly decay any post-transfer residual local transform to identity.
		# A locked module is parented to the console and must not be pulled by this blend.
		var owner_now := str(entry["owner"])
		var state_now := str(entry["state"])
		if float(entry["blend_alpha"]) > 0.001 and owner_now != "" and owner_now != "console_socket" and state_now != "locked" and state_now != "device_online":
			var next_alpha: float = maxf(0.0, float(entry["blend_alpha"]) - step_dt * 12.0)
			entry["blend_alpha"] = next_alpha
			var root: Node3D = entry["root"] as Node3D
			var offset: Transform3D = entry["blend_offset"]
			root.transform = Transform3D.IDENTITY.interpolate_with(offset, next_alpha)

		# Measure live hand gap for QC telemetry.
		_update_hand_gap(entry, actors)
		frame_telemetry[prop_id] = get_qc_snapshot(prop_id)

	return frame_telemetry


func _eval_timeline(entry: Dictionary, timeline: Array[Dictionary], frame_index: int, fps: int, actors: Dictionary) -> void:
	if timeline.is_empty():
		return
	var active: Dictionary = _active_timeline_entry(timeline, frame_index)
	if active.is_empty():
		return
	var action: String = str(active.get("action", active.get("state", "surface"))).to_lower()
	var prop_id: String = str(entry["id"])
	var side: String = str(active.get("side", "right"))

	match action:
		"hidden":
			_release_owner(entry, actors)
			(entry["root"] as Node3D).visible = false
			entry["state"] = "hidden"
		"surface":
			_place_on_surface(entry, active, actors)
		"held":
			var actor_id: String = str(active.get("actor", active.get("owner", "")))
			if actor_id != "" and actors.has(actor_id):
				if str(entry["owner"]) != actor_id or str(entry["owner_side"]) != side:
					attach_to_actor(prop_id, actor_id, actors, side, true)
				if _prop_uses_causal(entry):
					_set_causal(entry, frame_index, "held_by:%s" % actor_id, actor_id)
					entry["phase_this_frame"] = "held_by:%s" % actor_id
				else:
					entry["state"] = "held"
					entry["phase_this_frame"] = "held"
		"handoff":
			if _is_causal_handoff(active):
				_eval_causal_handoff(entry, active, frame_index, fps, actors)
			else:
				_eval_handoff_action(entry, active, frame_index, fps, actors)
		"install_into_socket", "install_module":
			_eval_install_action(entry, active, frame_index, fps, actors)
		"pickup":
			_eval_pickup_action(entry, active, frame_index, fps, actors)
		"put_down":
			_eval_put_down_action(entry, active, frame_index, fps, actors)
		"inspect":
			_eval_inspect_action(entry, active, frame_index, fps, actors)
		"use":
			_eval_use_action(entry, active, frame_index, fps, actors)
		_:
			_place_on_surface(entry, active, actors)


func _eval_handoff_action(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var giver: String = str(active.get("from_actor", active.get("giver", entry["owner"])))
	var receiver: String = str(active.get("to_actor", active.get("receiver", "")))
	if giver == "" or receiver == "" or not actors.has(giver) or not actors.has(receiver):
		return

	var a_f: int = int(active.get("from", 0))
	var b_f: int = maxi(int(active.get("to", a_f + 24)), a_f + 1)
	var default_transfer: int = a_f + int(round(float(b_f - a_f) * 0.5))
	var transfer_frame: int = int(active.get("transfer_frame", default_transfer))
	var handoff_key := "%d:%s->%s" % [a_f, giver, receiver]
	var already_done: bool = (entry["handoff_done_keys"] as Dictionary).has(handoff_key)

	var giver_side: String = str(active.get("giver_side", "right"))
	var recv_side: String = str(active.get("receiver_side", "right"))
	var giver_node: Node3D = actors[giver] as Node3D
	var recv_node: Node3D = actors[receiver] as Node3D

	# Ensure giver holds the prop before transfer.
	if not already_done and str(entry["owner"]) != giver:
		attach_to_actor(prop_id, giver, actors, giver_side, false)

	# Compute torso-safe meeting point between giver and receiver (chest height, above subtitles).
	var meet: Vector3 = _compute_safe_handoff_point(giver_node, recv_node, float(active.get("height", DEFAULT_HANDOFF_HEIGHT)))

	# Validate actor spacing and mutual facing prior to handoff.
	var delta_xz := Vector2(recv_node.global_position.x - giver_node.global_position.x, recv_node.global_position.z - giver_node.global_position.z)
	var actor_dist_m: float = delta_xz.length()
	var dir_to_recv: Vector3 = Vector3(delta_xz.x, 0.0, delta_xz.y).normalized() if actor_dist_m > 0.001 else Vector3.FORWARD
	var giver_fwd: Vector3 = (giver_node.global_transform.basis * Vector3(0.0, 0.0, 1.0))
	giver_fwd.y = 0.0
	var recv_fwd: Vector3 = (recv_node.global_transform.basis * Vector3(0.0, 0.0, 1.0))
	recv_fwd.y = 0.0
	var facing_ok: bool = (
		giver_fwd.normalized().dot(dir_to_recv) > 0.25
		and recv_fwd.normalized().dot(-dir_to_recv) > 0.25
	)
	var distance_ok: bool = actor_dist_m >= 0.45 and actor_dist_m <= 1.55

	# Smooth ramp-in / ramp-out reach weights.
	var ramp: float = maxf(float(active.get("ramp_s", 0.35)) * float(maxi(fps, 1)), 1.0)
	var w_in: float = clampf(float(frame_index - a_f) / ramp, 0.0, 1.0)
	var w_out: float = clampf(float(b_f - frame_index) / ramp, 0.0, 1.0)
	var reach_w: float = minf(w_in, w_out)
	reach_w = reach_w * reach_w * (3.0 - 2.0 * reach_w)

	# Keep distinct GripAnchor (giver side) and ReceiveAnchor (receiver side) separated
	# by at least HAND_MIN_SEPARATION_M so palms/fingers never intersect.
	var receive_world: Vector3 = (entry["receive_socket"] as Node3D).global_position
	var grip_world: Vector3 = (entry["grip_socket"] as Node3D).global_position
	var grip_to_receive: Vector3 = receive_world - grip_world
	var sep_vec: Vector3 = dir_to_recv * (HAND_MIN_SEPARATION_M * 0.55)
	var giver_target: Vector3 = meet - grip_to_receive * 0.5 - sep_vec
	var receiver_target: Vector3 = (receive_world + sep_vec * 0.35) if not already_done else (meet + sep_vec * 0.5)

	# Post-transfer overlap: giver releases smoothly ONLY AFTER receiver has gripped for
	# RECEIVER_HOLD_OVERLAP_FRAMES frames.
	var transfer_map: Dictionary = entry["handoff_transfer_frames"] as Dictionary
	var giver_reach_w: float = reach_w
	var recv_reach_w: float = reach_w
	if already_done:
		var done_frame: int = int(transfer_map.get(handoff_key, transfer_frame))
		var post_f: int = frame_index - done_frame
		if post_f <= RECEIVER_HOLD_OVERLAP_FRAMES:
			var hold_u: float = 1.0 - float(post_f) / float(maxi(RECEIVER_HOLD_OVERLAP_FRAMES, 1))
			giver_reach_w = maxf(reach_w * 0.35, reach_w * hold_u)
		else:
			giver_reach_w = reach_w * clampf(1.0 - float(post_f - RECEIVER_HOLD_OVERLAP_FRAMES) / maxf(ramp * 0.6, 4.0), 0.0, 1.0)
		recv_reach_w = maxf(reach_w, 0.85 * w_out)

	if giver_reach_w > 0.001:
		_reach_requests[giver] = {"target": giver_target, "weight": giver_reach_w, "side": giver_side, "prop_id": prop_id}
		_gaze_requests[giver] = {"point": meet, "weight": 0.65 * giver_reach_w, "prop_id": prop_id}
	if recv_reach_w > 0.001:
		_reach_requests[receiver] = {"target": receiver_target, "weight": recv_reach_w, "side": recv_side, "prop_id": prop_id}
		_gaze_requests[receiver] = {"point": meet, "weight": 0.80 * recv_reach_w, "prop_id": prop_id}

	entry["state"] = "handoff"

	if not already_done:
		var recv_hand: Vector3 = _actor_hand_world(recv_node, recv_side)
		var giver_hand: Vector3 = _actor_hand_world(giver_node, giver_side)
		var gap: float = receive_world.distance_to(recv_hand)
		var hand_sep: float = giver_hand.distance_to(recv_hand)
		var max_gap: float = float(active.get("max_gap", DEFAULT_MAX_HANDOFF_GAP))
		var contact_ok: bool = minf(gap, float(entry["handoff_prev_gap"])) <= max_gap
		var late_deadline: int = maxi(transfer_frame + 6, b_f - RECEIVER_HOLD_OVERLAP_FRAMES - 2)
		var contact_trigger: bool = contact_ok and frame_index >= (transfer_frame - 4)
		var fallback_trigger: bool = frame_index >= late_deadline
		if contact_trigger or fallback_trigger:
			var final_gap: float = minf(gap, float(entry["handoff_prev_gap"]))
			# Preserve exact world-space position/orientation across ownership switch.
			attach_to_actor(prop_id, receiver, actors, recv_side, true)
			(entry["handoff_done_keys"] as Dictionary)[handoff_key] = true
			transfer_map[handoff_key] = frame_index
			entry["handoff_prev_gap"] = INF
			entry["state"] = "handoff"
			_handoff_log.append({
				"prop_id": prop_id,
				"entity_role": str(entry.get("entity_role", prop_id)),
				"frame": frame_index,
				"from": giver,
				"to": receiver,
				"hand_gap_m": snappedf(final_gap, 0.001),
				"hand_separation_m": snappedf(hand_sep, 0.001),
				"contact_verified": contact_ok,
				"stance_ok": distance_ok and facing_ok,
				"actor_dist_m": snappedf(actor_dist_m, 0.001),
				"meet_pos": [snappedf(meet.x, 0.001), snappedf(meet.y, 0.001), snappedf(meet.z, 0.001)],
			})
		else:
			entry["handoff_prev_gap"] = gap


func _eval_pickup_action(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var actor_id: String = str(active.get("actor", ""))
	if actor_id == "" or not actors.has(actor_id):
		return
	var side: String = str(active.get("side", "right"))
	var a_f: int = int(active.get("from", 0))
	var b_f: int = maxi(int(active.get("to", a_f + 20)), a_f + 1)
	var grab_frame: int = int(active.get("transfer_frame", a_f + int(round(float(b_f - a_f) * 0.5))))
	var ramp: float = maxf(float(active.get("ramp_s", 0.3)) * float(maxi(fps, 1)), 1.0)
	var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
	var reach_w: float = w * w * (3.0 - 2.0 * w)

	if frame_index < grab_frame:
		if str(entry["owner"]) != "":
			_place_on_surface(entry, active, actors)
		var grip_pos: Vector3 = (entry["grip_socket"] as Node3D).global_position
		_reach_requests[actor_id] = {"target": grip_pos, "weight": reach_w, "side": side, "prop_id": prop_id}
		_gaze_requests[actor_id] = {"point": grip_pos, "weight": 0.75 * reach_w, "prop_id": prop_id}
	else:
		if str(entry["owner"]) != actor_id:
			attach_to_actor(prop_id, actor_id, actors, side, true)
		if reach_w > 0.01:
			var actor_node: Node3D = actors[actor_id] as Node3D
			var carry_pt: Vector3 = actor_node.global_position + actor_node.global_transform.basis * Vector3(0.22, 1.02, 0.34)
			_reach_requests[actor_id] = {"target": carry_pt, "weight": reach_w * 0.5, "side": side, "prop_id": prop_id}
	entry["state"] = "pickup"


func _eval_put_down_action(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var actor_id: String = str(active.get("actor", entry["owner"]))
	var side: String = str(active.get("side", "right"))
	var a_f: int = int(active.get("from", 0))
	var b_f: int = maxi(int(active.get("to", a_f + 20)), a_f + 1)
	var release_frame: int = int(active.get("transfer_frame", a_f + int(round(float(b_f - a_f) * 0.55))))
	var ramp: float = maxf(float(active.get("ramp_s", 0.3)) * float(maxi(fps, 1)), 1.0)
	var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
	var reach_w: float = w * w * (3.0 - 2.0 * w)

	var dest_pos: Vector3 = _surface_target_world(entry, active)
	if frame_index < release_frame:
		if actor_id != "" and actors.has(actor_id):
			if str(entry["owner"]) != actor_id:
				attach_to_actor(prop_id, actor_id, actors, side, false)
			_reach_requests[actor_id] = {"target": dest_pos, "weight": reach_w, "side": side, "prop_id": prop_id}
			_gaze_requests[actor_id] = {"point": dest_pos, "weight": 0.75 * reach_w, "prop_id": prop_id}
	else:
		_place_on_surface(entry, active, actors)
		if actor_id != "" and actors.has(actor_id) and reach_w > 0.01:
			_reach_requests[actor_id] = {"target": dest_pos + Vector3(0.0, 0.08, 0.0), "weight": reach_w * 0.4, "side": side, "prop_id": prop_id}
	entry["state"] = "put_down"


func _eval_inspect_action(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var actor_id: String = str(active.get("actor", entry["owner"]))
	if actor_id == "" or not actors.has(actor_id):
		return
	var side: String = str(active.get("side", "right"))
	if str(entry["owner"]) != actor_id:
		attach_to_actor(prop_id, actor_id, actors, side, true)
	var a_f: int = int(active.get("from", 0))
	var b_f: int = maxi(int(active.get("to", a_f + 30)), a_f + 1)
	var ramp: float = maxf(float(active.get("ramp_s", 0.35)) * float(maxi(fps, 1)), 1.0)
	var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
	var reach_w: float = w * w * (3.0 - 2.0 * w)

	var actor_node: Node3D = actors[actor_id] as Node3D
	var h: float = float(actor_node.get("height_m")) if actor_node.get("height_m") != null else 1.7
	var local_inspect := Vector3(0.14 if side == "right" else -0.14, h * 0.74, 0.40)
	var inspect_world: Vector3 = actor_node.global_transform * local_inspect
	_reach_requests[actor_id] = {"target": inspect_world, "weight": reach_w * 0.85, "side": side, "prop_id": prop_id}
	var op_world: Vector3 = (entry["operate_socket"] as Node3D).global_position
	_gaze_requests[actor_id] = {"point": op_world, "weight": 0.9 * reach_w, "prop_id": prop_id}
	entry["state"] = "inspect"


func _eval_use_action(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var actor_id: String = str(active.get("actor", entry["owner"]))
	if actor_id == "" or not actors.has(actor_id):
		return
	var side: String = str(active.get("side", "right"))
	if str(entry["owner"]) != actor_id:
		attach_to_actor(prop_id, actor_id, actors, side, true)
	var a_f: int = int(active.get("from", 0))
	var b_f: int = maxi(int(active.get("to", a_f + 30)), a_f + 1)
	var ramp: float = maxf(float(active.get("ramp_s", 0.3)) * float(maxi(fps, 1)), 1.0)
	var w: float = minf(clampf(float(frame_index - a_f) / ramp, 0.0, 1.0), clampf(float(b_f - frame_index) / ramp, 0.0, 1.0))
	var reach_w: float = w * w * (3.0 - 2.0 * w)

	var actor_node: Node3D = actors[actor_id] as Node3D
	var h: float = float(actor_node.get("height_m")) if actor_node.get("height_m") != null else 1.7
	var target_world: Vector3 = actor_node.global_transform * Vector3(0.18 if side == "right" else -0.18, h * 0.68, 0.55)
	if active.has("target_point"):
		target_world = _vec3(active["target_point"], target_world)
	elif active.has("target") and actors.has(str(active["target"])):
		var other: Node3D = actors[str(active["target"])] as Node3D
		target_world = other.global_position + Vector3(0.0, 1.05, 0.0)

	# Keep reach within arm reach distance while aiming toward target.
	var shoulder: Vector3 = actor_node.global_position + Vector3(0.0, h * 0.80, 0.0)
	var to_t: Vector3 = target_world - shoulder
	var reach_pt: Vector3 = shoulder + to_t.normalized() * clampf(to_t.length(), 0.28, 0.56) if to_t.length() > 0.01 else target_world
	_reach_requests[actor_id] = {"target": reach_pt, "weight": reach_w * 0.9, "side": side, "prop_id": prop_id}
	_gaze_requests[actor_id] = {"point": target_world, "weight": 0.85 * reach_w, "prop_id": prop_id}
	entry["state"] = "use"


## Backward-compatible evaluator for existing stage3d.json `owners` array format.
func _eval_legacy_owners(entry: Dictionary, owners: Array[Dictionary], frame_index: int, actors: Dictionary) -> void:
	var prop_id: String = str(entry["id"])
	var current_owner: String = str(entry["owner"])
	var desired_owner: String = current_owner

	for owner_cfg: Dictionary in owners:
		var actor_id: String = str(owner_cfg.get("actor", ""))
		var window_v: Variant = owner_cfg.get("handoff_window", [])
		var window: Array = window_v as Array if window_v is Array else []
		if window.size() != 2:
			if frame_index < int(owner_cfg.get("from", 0)):
				break
			desired_owner = actor_id
			continue
		if frame_index < int(window[0]):
			break
		if frame_index > int(window[1]) or current_owner == actor_id or current_owner == "":
			desired_owner = actor_id
			continue
		if not actors.has(current_owner) or not actors.has(actor_id):
			desired_owner = actor_id
			continue
		var a: Vector3 = _actor_hand_world(actors[current_owner], "right")
		var b: Vector3 = _actor_hand_world(actors[actor_id], "right")
		var dist: float = a.distance_to(b)
		var prev_gap: float = float(entry["handoff_prev_gap"])
		var max_gap: float = float(owner_cfg.get("max_gap", DEFAULT_MAX_HANDOFF_GAP))
		var close_enough: bool = minf(dist, prev_gap) < max_gap
		var turning_back: bool = dist > prev_gap + 0.001
		if (close_enough and turning_back) or frame_index >= int(window[1]):
			var meet: Vector3 = (entry["receive_socket"] as Node3D).global_position
			_handoff_log.append({
				"prop_id": prop_id,
				"frame": frame_index,
				"from": current_owner,
				"to": actor_id,
				"hand_gap_m": snappedf(minf(dist, prev_gap), 0.001),
				"meet_pos": [snappedf(meet.x, 0.001), snappedf(meet.y, 0.001), snappedf(meet.z, 0.001)],
			})
			entry["handoff_prev_gap"] = INF
			desired_owner = actor_id
		else:
			entry["handoff_prev_gap"] = dist
		break

	if desired_owner == "" or not actors.has(desired_owner):
		(entry["root"] as Node3D).visible = false
		return
	if desired_owner != current_owner:
		attach_to_actor(prop_id, desired_owner, actors, "right", true)
	(entry["root"] as Node3D).visible = true
	entry["state"] = "held"


# ------------------------------------------------------- causal chain


func finalize_frame(frame_index: int, actors: Dictionary) -> void:
	_last_frame = frame_index
	for prop_key: Variant in _props.keys():
		var entry: Dictionary = _props[str(prop_key)]
		var phase := str(entry.get("phase_this_frame", ""))
		if bool(entry.get("has_desired_basis", false)) and phase in ["transfer_contact", "align_socket", "insert_socket"]:
			_set_world_basis_keep_origin(entry["root"] as Node3D, entry["desired_basis"] as Basis)
		if bool(entry.get("contact_pending", false)) and not bool(entry.get("contact_done", false)):
			_commit_contact(entry, frame_index, actors)
		if bool(entry.get("lock_pending", false)) and not bool(entry.get("lock_done", false)):
			_commit_lock(entry, frame_index, actors)
		if str(entry.get("owner", "")) == "console_socket":
			_reseat_locked(entry, frame_index)
		_record_frame_metric(entry, frame_index, actors)
	_refresh_device_online()


func get_interaction_audit() -> Dictionary:
	var contact_stats := _sample_stats(_contact_samples)
	var contact_at := _sample_stats(_contact_frame_errors)
	var lock_stats := _sample_stats(_lock_frame_errors)
	var chain_errors := _chain_errors()
	var contact_error := float(contact_at["max"]) if int(contact_at["count"]) > 0 else 0.0
	var socket_error := float(lock_stats["max"]) if int(lock_stats["count"]) > 0 else 0.0
	var valid := _invariant_violations.is_empty() and chain_errors.is_empty()
	return {
		"version": 1,
		"causal_chain_valid": valid,
		"handoff_contact_error_m": snappedf(contact_error, 0.0001),
		"handoff_contact_min_m": float(contact_at["min"]) if int(contact_at["count"]) > 0 else float(contact_stats["min"]),
		"handoff_contact_mean_m": float(contact_at["mean"]) if int(contact_at["count"]) > 0 else float(contact_stats["mean"]),
		"socket_insert_error_m": snappedf(socket_error, 0.0001),
		"socket_insert_min_m": float(lock_stats["min"]),
		"socket_insert_mean_m": float(lock_stats["mean"]),
		"ownership_log": _ownership_log.duplicate(true),
		"device_timeline": _device_timeline.duplicate(true),
		"invariant_violations": _invariant_violations.duplicate(true),
		"chain_errors": chain_errors,
		"sound_cues": _sound_cues.duplicate(true),
		"frames": _frame_metrics.duplicate(true),
		"has_install_interaction": _has_install,
		"device_online": _device_online,
	}


func _prepare_causal_spec(entry: Dictionary) -> void:
	var spec: Dictionary = entry["spec"]
	var timeline: Array[Dictionary] = []
	var src: Variant = spec.get("timeline", spec.get("actions", []))
	if src is Array:
		for item: Variant in src as Array:
			if item is Dictionary:
				timeline.append((item as Dictionary).duplicate(true))
	var added := false
	if spec.get("handoff") is Dictionary:
		var handoff: Dictionary = (spec["handoff"] as Dictionary).duplicate(true)
		if not _timeline_has(timeline, "handoff"):
			handoff["action"] = "handoff"
			_normalize_handoff_window(handoff)
			timeline.append(handoff)
			added = true
	if spec.get("install_into_socket") is Dictionary:
		var install: Dictionary = (spec["install_into_socket"] as Dictionary).duplicate(true)
		if not _timeline_has(timeline, "install_into_socket"):
			install["action"] = "install_into_socket"
			_normalize_install_window(install)
			timeline.append(install)
			added = true
	for item: Dictionary in timeline:
		var action := str(item.get("action", item.get("state", ""))).to_lower()
		if action == "handoff" and _is_causal_handoff(item):
			_normalize_handoff_window(item)
		elif action == "install_into_socket" or action == "install_module":
			_normalize_install_window(item)
			_has_install = true
			entry["install_tracked"] = true
	if spec.has("timeline") or spec.has("actions") or added:
		if not timeline.is_empty():
			spec["timeline"] = timeline
	entry["spec"] = spec


func _timeline_has(timeline: Array[Dictionary], action_name: String) -> bool:
	for item: Dictionary in timeline:
		if str(item.get("action", item.get("state", ""))).to_lower() == action_name:
			return true
	return false


func _is_causal_handoff(active: Dictionary) -> bool:
	return active.has("start_frame") or active.has("contact_frame") or active.has("release_frame") or active.has("end_frame")


func _prop_uses_causal(entry: Dictionary) -> bool:
	var spec: Dictionary = entry["spec"]
	if spec.get("handoff") is Dictionary or spec.get("install_into_socket") is Dictionary:
		return true
	for item: Dictionary in _dict_array(spec.get("timeline", [])):
		var action := str(item.get("action", item.get("state", ""))).to_lower()
		if action == "install_into_socket" or action == "install_module":
			return true
		if action == "handoff" and _is_causal_handoff(item):
			return true
	return false


func _normalize_handoff_window(item: Dictionary) -> void:
	if not item.has("from"):
		item["from"] = int(item.get("start_frame", 0))
	if not item.has("to"):
		var end_inclusive := int(item.get("end_frame", int(item.get("release_frame", int(item["from"]) + 16))))
		item["to"] = end_inclusive + 1


func _normalize_install_window(item: Dictionary) -> void:
	if not item.has("from"):
		item["from"] = int(item.get("start_frame", 0))
	if not item.has("to"):
		var end_inclusive := int(item.get("withdraw_end_frame", int(item.get("online_frame", int(item.get("lock_frame", int(item["from"]) + 24))))))
		item["to"] = end_inclusive + 1


func _eval_causal_handoff(entry: Dictionary, active: Dictionary, frame_index: int, fps: int, actors: Dictionary) -> void:
	var prop_id := str(entry["id"])
	var giver := str(active.get("from_actor", active.get("giver", "")))
	var receiver := str(active.get("to_actor", active.get("receiver", "")))
	if giver == "" or receiver == "" or not actors.has(giver) or not actors.has(receiver):
		return
	var start_f := int(active.get("start_frame", active.get("from", frame_index)))
	var contact_f := int(active.get("contact_frame", active.get("transfer_frame", start_f + maxi(int((int(active.get("to", start_f + 16)) - start_f) / 2), 4))))
	var release_f := int(active.get("release_frame", contact_f + 6))
	var end_f := int(active.get("end_frame", int(active.get("to", release_f + 1)) - 1))
	if contact_f < start_f:
		contact_f = start_f
	if release_f < contact_f:
		release_f = contact_f
	if end_f < release_f:
		end_f = release_f
	var giver_side := str(active.get("giver_side", active.get("side", "right")))
	var recv_side := str(active.get("receiver_side", active.get("side", "right")))
	entry["causal_giver"] = giver
	entry["causal_receiver"] = receiver
	entry["giver_side"] = giver_side
	entry["receiver_side"] = recv_side
	entry["resolved_contact_frame"] = contact_f

	var giver_node := actors[giver] as Node3D
	var recv_node := actors[receiver] as Node3D
	var chest_h := (_actor_chest_y(giver_node) + _actor_chest_y(recv_node)) * 0.5
	var transfer: Vector3 = _compute_transfer_point(giver_node, recv_node, chest_h)
	var face := recv_node.global_position - giver_node.global_position
	face.y = 0.0
	var basis := Basis.IDENTITY
	if face.length() > 0.02:
		basis = Basis.looking_at(face.normalized(), Vector3.UP)
	var receive_local: Vector3 = (entry["receive_socket"] as Node3D).position
	var giver_target := transfer
	var receiver_target := transfer + basis * receive_local
	entry["desired_basis"] = basis
	entry["has_desired_basis"] = true

	var contact_done := bool(entry.get("contact_done", false))
	if not contact_done:
		if str(entry["owner"]) != giver:
			attach_to_actor(prop_id, giver, actors, giver_side, false)
		if str(entry.get("causal_state", "")) == "":
			_set_causal(entry, frame_index, "held_by:%s" % giver, giver)
		_set_causal(entry, frame_index, "transfer_contact", giver)
		entry["phase_this_frame"] = "transfer_contact"
		entry["contact_pending"] = frame_index >= contact_f
	else:
		if str(entry["owner"]) != receiver:
			_transfer_owner_preserve(entry, receiver, actors, recv_side)
		_set_causal(entry, frame_index, "held_by:%s" % receiver, receiver)
		entry["phase_this_frame"] = "held_by:%s" % receiver
		entry["has_desired_basis"] = false

	var giver_w := 1.0
	var recv_w := 1.0
	var giver_fingers := "grip"
	var recv_fingers := "grip"
	if frame_index < contact_f:
		var u := _frame_u(frame_index, start_f, contact_f)
		giver_w = lerpf(0.45, 1.0, u)
		recv_w = lerpf(0.45, 1.0, u)
	elif frame_index < release_f:
		var u_out := _frame_u(frame_index, contact_f, release_f)
		giver_w = lerpf(1.0, 0.0, u_out)
		recv_w = 1.0
		giver_fingers = "release"
	else:
		var u_end := _frame_u(frame_index, release_f, maxi(end_f, release_f + 1))
		giver_w = 0.0
		recv_w = lerpf(0.85, 0.0, u_end)
		giver_fingers = "open"
	if giver_w > 0.02:
		_queue_reach(giver, giver_target, giver_w, giver_side, prop_id, giver_fingers)
		_gaze_requests[giver] = {"point": transfer, "weight": 0.7 * giver_w, "prop_id": prop_id}
	if recv_w > 0.02 and not contact_done:
		_queue_reach(receiver, receiver_target, recv_w, recv_side, prop_id, recv_fingers)
		_gaze_requests[receiver] = {"point": transfer, "weight": 0.85 * recv_w, "prop_id": prop_id}
	elif contact_done and recv_w > 0.02:
		var held_grip: Vector3 = (entry["grip_socket"] as Node3D).global_position
		_queue_reach(receiver, held_grip, recv_w, recv_side, prop_id, "grip")
		_gaze_requests[receiver] = {"point": held_grip, "weight": 0.55, "prop_id": prop_id}
	fps = maxi(fps, 1)


func _eval_install_action(entry: Dictionary, active: Dictionary, frame_index: int, _fps: int, actors: Dictionary) -> void:
	var prop_id := str(entry["id"])
	var actor_id := str(active.get("actor", active.get("owner", entry.get("owner", ""))))
	if actor_id == "" or not actors.has(actor_id):
		return
	_has_install = true
	entry["install_tracked"] = true
	var side := str(active.get("side", "right"))
	var start_f := int(active.get("start_frame", active.get("from", frame_index)))
	var align_f := int(active.get("align_frame", start_f + 8))
	var insert_f := int(active.get("insert_frame", align_f))
	var lock_f := int(active.get("lock_frame", insert_f + 8))
	if align_f < start_f:
		align_f = start_f
	if insert_f < align_f:
		insert_f = align_f
	if lock_f <= insert_f:
		lock_f = insert_f + 1
	var authored_online := int(active.get("online_frame", lock_f + 4))
	if active.has("online_frame") and authored_online < lock_f + 4 and not bool(entry.get("online_schedule_flagged", false)):
		entry["online_schedule_flagged"] = true
		_add_violation(entry, frame_index, "premature_online_schedule", "online_frame %d is earlier than lock_frame+4 (%d)" % [authored_online, lock_f + 4])
	var online_f := maxi(authored_online, lock_f + 4)
	var withdraw_f := int(active.get("withdraw_end_frame", online_f))
	if withdraw_f < lock_f:
		withdraw_f = lock_f
	entry["resolved_lock_frame"] = lock_f
	entry["resolved_online_frame"] = online_f
	entry["install_side"] = side
	entry["causal_receiver"] = actor_id

	var socket := _query_socket(active)
	var socket_pos: Vector3 = socket["pos"]
	var socket_normal: Vector3 = socket["normal"]
	var socket_basis: Basis = socket["basis"]
	entry["socket_pos"] = socket_pos
	entry["socket_normal"] = socket_normal
	entry["socket_basis"] = socket_basis
	var pre_insert: Vector3 = socket_pos + socket_normal * PRE_INSERT_OFFSET_M

	if not bool(entry.get("lock_done", false)):
		if str(entry["owner"]) != actor_id:
			attach_to_actor(prop_id, actor_id, actors, side, true)
		var hand_target := pre_insert
		if frame_index < align_f:
			var prior := str(entry.get("causal_state", ""))
			if prior == "" or prior.begins_with("held_by:"):
				_set_causal(entry, frame_index, "held_by:%s" % actor_id, actor_id)
			_set_causal(entry, frame_index, "align_socket", actor_id)
			entry["phase_this_frame"] = "align_socket"
			if not entry.has("align_from"):
				entry["align_from"] = (entry["root"] as Node3D).global_position
			hand_target = (entry["align_from"] as Vector3).lerp(pre_insert, _frame_u(frame_index, start_f, align_f))
		elif frame_index < lock_f:
			_set_causal(entry, frame_index, "insert_socket", actor_id)
			entry["phase_this_frame"] = "insert_socket"
			var slide := 0.0 if frame_index < insert_f else _frame_u(frame_index, insert_f, lock_f)
			hand_target = pre_insert.lerp(socket_pos, slide)
		else:
			_set_causal(entry, frame_index, "insert_socket", actor_id)
			entry["phase_this_frame"] = "insert_socket"
			entry["lock_pending"] = true
			hand_target = socket_pos
		entry["desired_basis"] = socket_basis
		entry["has_desired_basis"] = true
		var palm_target := _palm_target_for_module(entry, actors[actor_id], side, hand_target)
		_queue_reach(actor_id, palm_target, 1.0, side, prop_id, "grip")
		_gaze_requests[actor_id] = {"point": hand_target, "weight": 0.8, "prop_id": prop_id}
		return

	if frame_index >= online_f and not bool(entry.get("online_done", false)):
		_go_online(entry, frame_index)
	elif not bool(entry.get("online_done", false)):
		_set_causal(entry, frame_index, "locked", "console_socket")
	entry["phase_this_frame"] = "device_online" if bool(entry.get("online_done", false)) else "locked"
	entry["has_desired_basis"] = false
	if frame_index <= withdraw_f:
		var u_back := _frame_u(frame_index, lock_f, maxi(withdraw_f, lock_f + 1))
		var back: Vector3 = socket_pos + socket_normal * 0.34
		var fingers := "release" if u_back < 0.35 else "open"
		_queue_reach(actor_id, socket_pos.lerp(back, u_back), lerpf(1.0, 0.0, u_back), side, prop_id, fingers)
		_gaze_requests[actor_id] = {"point": socket_pos, "weight": lerpf(0.7, 0.25, u_back), "prop_id": prop_id}


func _commit_contact(entry: Dictionary, frame_index: int, actors: Dictionary) -> void:
	var giver := str(entry.get("causal_giver", ""))
	var receiver := str(entry.get("causal_receiver", ""))
	var recv_side := str(entry.get("receiver_side", "right"))
	var receive_pos: Vector3 = (entry["receive_socket"] as Node3D).global_position
	if receiver != "" and actors.has(receiver) and (actors[receiver] as Node).has_method("reach_hand_world"):
		var recv_node := actors[receiver] as Node
		if bool(entry.get("has_desired_basis", false)):
			_set_world_basis_keep_origin(entry["root"] as Node3D, entry["desired_basis"] as Basis)
		for _pass in 3:
			receive_pos = (entry["receive_socket"] as Node3D).global_position
			recv_node.call("reach_hand_world", recv_side, receive_pos, 1.0)
	var gaps := _measure_hand_gaps(entry, actors)
	var giver_gap: float = float(gaps["giver"])
	var recv_gap: float = float(gaps["receiver"])
	entry["giver_hand_to_prop_m"] = giver_gap
	entry["receiver_hand_to_prop_m"] = recv_gap
	var worst := _worst_gap(giver_gap, recv_gap)
	if worst >= 0.0:
		_contact_samples.append(worst)
		_contact_frame_errors.append(worst)
	entry["contact_done"] = true
	entry["contact_pending"] = false
	if receiver != "" and actors.has(receiver):
		_transfer_owner_preserve(entry, receiver, actors, recv_side)
	entry["hand_gap_m"] = worst if worst >= 0.0 else 0.0
	_set_causal(entry, frame_index, "held_by:%s" % receiver, receiver)
	var meet: Vector3 = (entry["receive_socket"] as Node3D).global_position
	_handoff_log.append({
		"prop_id": str(entry["id"]),
		"entity_role": str(entry.get("entity_role", "")),
		"frame": frame_index,
		"from": giver,
		"to": receiver,
		"hand_gap_m": snappedf(maxf(worst, 0.0), 0.001),
		"contact_verified": worst >= 0.0 and worst <= CONTACT_TARGET_M,
		"meet_pos": [snappedf(meet.x, 0.001), snappedf(meet.y, 0.001), snappedf(meet.z, 0.001)],
	})


func _commit_lock(entry: Dictionary, frame_index: int, actors: Dictionary) -> void:
	var actor_id := str(entry.get("causal_receiver", entry.get("owner", "")))
	var side := str(entry.get("install_side", "right"))
	var socket_pos: Vector3 = entry.get("socket_pos", Vector3.ZERO)
	var socket_basis: Basis = entry.get("socket_basis", Basis.IDENTITY)
	if actor_id != "" and actors.has(actor_id) and str(entry.get("owner", "")) == actor_id:
		var actor := actors[actor_id] as Node
		if actor.has_method("reach_hand_world"):
			for _seat in 3:
				if bool(entry.get("has_desired_basis", false)):
					_set_world_basis_keep_origin(entry["root"] as Node3D, socket_basis)
				var palm: Vector3 = _palm_target_for_module(entry, actor, side, socket_pos)
				actor.call("reach_hand_world", side, palm, 1.0)
	var root := entry["root"] as Node3D
	var before: float = root.global_position.distance_to(socket_pos)
	entry["socket_err_m"] = before
	entry["module_to_socket_m"] = before
	_lock_frame_errors.append(before)
	_lock_module(entry, socket_pos, socket_basis, actors)
	entry["lock_pending"] = false
	entry["device_state"] = "locked"
	_set_causal(entry, frame_index, "locked", "console_socket")
	if _bay != null and _bay.has_method("set_module_slot_state"):
		_bay.call("set_module_slot_state", "locked")
	_emit_lock_cue(socket_pos, frame_index)
	_device_timeline.append({
		"prop_id": str(entry["id"]),
		"frame": frame_index,
		"state": "locked",
		"device_state": "locked",
	})


func _lock_module(entry: Dictionary, socket_pos: Vector3, socket_basis: Basis, actors: Dictionary) -> void:
	var prev_owner := str(entry.get("owner", ""))
	var prev_side := str(entry.get("owner_side", "right"))
	if prev_owner != "" and prev_owner != "console_socket" and actors.has(prev_owner):
		_set_actor_holding(actors[prev_owner], prev_side, false)
	var root := entry["root"] as Node3D
	if root.get_parent() != self:
		if root.get_parent() != null:
			root.get_parent().remove_child(root)
		add_child(root)
	var seated := Transform3D(socket_basis.orthonormalized(), socket_pos)
	root.global_transform = seated
	entry["owner"] = "console_socket"
	entry["owner_side"] = ""
	entry["locked_transform"] = seated
	entry["blend_alpha"] = 0.0
	entry["blend_offset"] = Transform3D.IDENTITY
	entry["lock_done"] = true
	entry["state"] = "locked"
	entry["module_to_socket_m"] = 0.0


func _go_online(entry: Dictionary, frame_index: int) -> void:
	if bool(entry.get("online_done", false)):
		return
	if not bool(entry.get("lock_done", false)):
		_add_violation(entry, frame_index, "premature_online", "device_online requested before locked")
		return
	var lock_f := int(entry.get("resolved_lock_frame", frame_index))
	if frame_index < lock_f + 4:
		_add_violation(entry, frame_index, "premature_online", "device_online at frame %d is before lock+4 (%d)" % [frame_index, lock_f + 4])
		return
	entry["online_done"] = true
	entry["device_online"] = true
	entry["device_state"] = "online"
	_set_causal(entry, frame_index, "device_online", "console_socket")
	_device_timeline.append({
		"prop_id": str(entry["id"]),
		"frame": frame_index,
		"state": "device_online",
		"device_state": "online",
	})
	if _bay != null and _bay.has_method("set_module_slot_state"):
		_bay.call("set_module_slot_state", "online")
	if _bay != null and _bay.has_method("set_device_online"):
		_bay.call("set_device_online", true)
	_refresh_device_online()


func _reseat_locked(entry: Dictionary, frame_index: int) -> void:
	var root := entry["root"] as Node3D
	var seated: Transform3D = entry.get("locked_transform", root.global_transform)
	var drift: float = root.global_position.distance_to(seated.origin)
	if drift > POST_LOCK_DRIFT_M and not bool(entry.get("drift_flagged", false)):
		entry["drift_flagged"] = true
		_add_violation(entry, frame_index, "post_lock_drift", "module drifted %.4f m after lock" % drift)
	root.global_transform = seated
	entry["module_to_socket_m"] = 0.0
	entry["socket_err_m"] = 0.0
	if str(entry.get("owner", "")) != "console_socket":
		_add_violation(entry, frame_index, "dual_ownership", "locked module owner is %s" % str(entry.get("owner", "")))
		entry["owner"] = "console_socket"


func _record_frame_metric(entry: Dictionary, frame_index: int, actors: Dictionary) -> void:
	var gaps := _measure_hand_gaps(entry, actors)
	var giver_gap: float = float(gaps["giver"])
	var recv_gap: float = float(gaps["receiver"])
	entry["giver_hand_to_prop_m"] = giver_gap
	entry["receiver_hand_to_prop_m"] = recv_gap
	var phase := str(entry.get("phase_this_frame", ""))
	if phase == "transfer_contact":
		var worst := _worst_gap(giver_gap, recv_gap)
		if worst >= 0.0:
			_contact_samples.append(worst)
	var socket_pos: Vector3 = entry.get("socket_pos", Vector3.INF)
	var module_err := -1.0
	if socket_pos != Vector3.INF and (bool(entry.get("install_tracked", false)) or str(entry.get("owner", "")) == "console_socket"):
		module_err = (entry["root"] as Node3D).global_position.distance_to(socket_pos)
		entry["module_to_socket_m"] = module_err
	var owner_gap := float(entry.get("hand_gap_m", 0.0))
	if str(entry.get("owner", "")) != "" and str(entry.get("owner", "")) != "console_socket":
		owner_gap = giver_gap if str(entry.get("owner", "")) == str(entry.get("causal_giver", "")) and giver_gap >= 0.0 else recv_gap
		if owner_gap < 0.0:
			owner_gap = float(entry.get("hand_gap_m", 0.0))
		entry["hand_gap_m"] = owner_gap
	_frame_metrics.append({
		"frame": frame_index,
		"prop_id": str(entry["id"]),
		"state": str(entry.get("causal_state", entry.get("state", ""))),
		"phase": phase,
		"owner": str(entry.get("owner", "")),
		"giver_hand_to_prop_m": snappedf(giver_gap, 0.001) if giver_gap >= 0.0 else -1.0,
		"receiver_hand_to_prop_m": snappedf(recv_gap, 0.001) if recv_gap >= 0.0 else -1.0,
		"module_to_socket_m": snappedf(module_err, 0.001) if module_err >= 0.0 else -1.0,
		"device_state": str(entry.get("device_state", "offline")),
		"hand_gap_m": snappedf(float(entry.get("hand_gap_m", 0.0)), 0.001),
		"socket_err_m": snappedf(float(entry.get("socket_err_m", -1.0)), 0.001),
	})


func _measure_hand_gaps(entry: Dictionary, actors: Dictionary) -> Dictionary:
	var giver := str(entry.get("causal_giver", ""))
	var receiver := str(entry.get("causal_receiver", ""))
	var grip: Vector3 = (entry["grip_socket"] as Node3D).global_position
	var receive: Vector3 = (entry["receive_socket"] as Node3D).global_position
	var giver_gap := -1.0
	var recv_gap := -1.0
	if giver != "" and actors.has(giver):
		giver_gap = _actor_hand_world(actors[giver], str(entry.get("giver_side", "right"))).distance_to(grip)
	if receiver != "" and actors.has(receiver):
		recv_gap = _actor_hand_world(actors[receiver], str(entry.get("receiver_side", "right"))).distance_to(receive)
	return {"giver": giver_gap, "receiver": recv_gap}


func _worst_gap(a: float, b: float) -> float:
	if a < 0.0 and b < 0.0:
		return -1.0
	if a < 0.0:
		return b
	if b < 0.0:
		return a
	return maxf(a, b)


func _palm_target_for_module(entry: Dictionary, actor: Variant, side: String, module_pos: Vector3) -> Vector3:
	var hand: Vector3 = _actor_hand_world(actor, side)
	var origin: Vector3 = (entry["root"] as Node3D).global_position
	return hand + (module_pos - origin)


func _queue_reach(actor_id: String, target: Vector3, weight: float, side: String, prop_id: String, fingers: String) -> void:
	_reach_requests[actor_id] = {
		"target": target,
		"weight": clampf(weight, 0.0, 1.0),
		"side": side,
		"prop_id": prop_id,
		"finger_pose": fingers,
	}


func _transfer_owner_preserve(entry: Dictionary, actor_id: String, actors: Dictionary, side: String) -> void:
	if not actors.has(actor_id):
		return
	var root := entry["root"] as Node3D
	var prev_world := root.global_transform
	var prev_owner := str(entry.get("owner", ""))
	var prev_side := str(entry.get("owner_side", "right"))
	if prev_owner != "" and prev_owner != actor_id and prev_owner != "console_socket" and actors.has(prev_owner):
		_set_actor_holding(actors[prev_owner], prev_side, false)
	var socket := _actor_hand_socket(actors[actor_id], side)
	if socket == null:
		return
	if root.get_parent() != socket:
		if root.get_parent() != null:
			root.get_parent().remove_child(root)
		socket.add_child(root)
	root.global_transform = prev_world
	# Keep the captured local offset. Blending it back to identity would slide the
	# module into the palm after the hands have already met.
	entry["blend_offset"] = Transform3D.IDENTITY
	entry["blend_alpha"] = 0.0
	if prev_owner != actor_id and prev_owner != "":
		entry["transfer_count"] = int(entry["transfer_count"]) + 1
	entry["owner"] = actor_id
	entry["owner_side"] = side
	root.visible = true
	_set_actor_holding(actors[actor_id], side, true)


func _set_world_basis_keep_origin(root: Node3D, basis: Basis) -> void:
	if root == null:
		return
	var origin := root.global_position
	root.global_transform = Transform3D(basis.orthonormalized(), origin)


func _set_causal(entry: Dictionary, frame_index: int, state: String, owner: String) -> void:
	var prev_state := str(entry.get("causal_state", ""))
	var prev_owner := str(entry.get("causal_owner_logged", ""))
	entry["state"] = state
	entry["causal_state"] = state
	if prev_state == state and prev_owner == owner:
		return
	if prev_owner != "" and owner != "" and prev_owner != owner and prev_state == state:
		_add_violation(entry, frame_index, "dual_ownership", "owner changed %s -> %s without a state change" % [prev_owner, owner])
	entry["causal_owner_logged"] = owner
	var sock := float(entry.get("socket_err_m", -1.0))
	_ownership_log.append({
		"prop_id": str(entry.get("id", "")),
		"frame": frame_index,
		"state": state,
		"owner": owner,
		"hand_gap_m": snappedf(float(entry.get("hand_gap_m", 0.0)), 0.001),
		"socket_err_m": snappedf(sock, 0.001) if sock >= 0.0 else -1.0,
	})


func _add_violation(entry: Dictionary, frame_index: int, code: String, detail: String) -> void:
	_invariant_violations.append({
		"prop_id": str(entry.get("id", "")),
		"frame": frame_index,
		"code": code,
		"detail": detail,
	})


func _refresh_device_online() -> void:
	var any_install := false
	var all_online := true
	for prop_key: Variant in _props.keys():
		var entry: Dictionary = _props[str(prop_key)]
		if not bool(entry.get("install_tracked", false)):
			continue
		any_install = true
		if not bool(entry.get("online_done", false)):
			all_online = false
	_device_online = any_install and all_online


func _query_socket(active: Dictionary) -> Dictionary:
	var configured_normal := _vec3(active.get("socket_normal", null), Vector3.ZERO)
	var configured_pos := _vec3(active.get("socket_pos", null), Vector3.INF)
	if _bay != null and _bay.has_method("get_module_socket_transform"):
		var result: Variant = _bay.call("get_module_socket_transform")
		var parsed := _socket_from_variant(result)
		if not parsed.is_empty():
			return parsed
	if _bay != null:
		var target_name := str(active.get("socket_target", "")).strip_edges()
		var named: Node = _bay.find_child(target_name, true, false) if target_name != "" else null
		var slot: Node = named if named is Node3D else _bay.find_child("ModuleSlot", true, false)
		if slot is Node3D:
			var node := slot as Node3D
			var facing := (-node.global_transform.basis.z).normalized()
			if facing.length_squared() < 0.5:
				facing = Vector3.FORWARD
			return {"pos": node.global_position, "normal": facing, "basis": node.global_transform.basis}
	if configured_pos != Vector3.INF:
		var normal := configured_normal.normalized() if configured_normal.length() > 0.01 else Vector3(0.0, 0.0, 1.0)
		return {"pos": configured_pos, "normal": normal, "basis": _basis_from_normal(normal)}
	if _bay != null and _bay.has_method("get_event_focus_point"):
		var focus: Dictionary = _bay.call("get_event_focus_point", "module_install")
		var pos: Vector3 = focus.get("position", Vector3(0.0, 1.0, 0.0))
		var view: Vector3 = focus.get("view_direction", Vector3(0.0, 0.0, 1.0))
		var flat := Vector3(view.x, 0.0, view.z)
		var normal := (-flat.normalized()) if flat.length() > 0.01 else Vector3(0.0, 0.0, 1.0)
		return {"pos": pos, "normal": normal, "basis": _basis_from_normal(normal)}
	var fallback_normal := configured_normal.normalized() if configured_normal.length() > 0.01 else Vector3(0.0, 0.0, 1.0)
	return {"pos": Vector3(0.0, 1.0, 0.0), "normal": fallback_normal, "basis": _basis_from_normal(fallback_normal)}


func _socket_from_variant(result: Variant) -> Dictionary:
	if result is Transform3D:
		var xf := result as Transform3D
		var facing := (-xf.basis.z).normalized()
		if facing.length_squared() < 0.5:
			facing = Vector3.FORWARD
		return {"pos": xf.origin, "normal": facing, "basis": xf.basis}
	if result is Dictionary:
		var data := result as Dictionary
		var pos := _vec3(data.get("origin", data.get("position", data.get("pos", null))), Vector3.INF)
		if pos == Vector3.INF:
			return {}
		var normal := _vec3(data.get("normal", null), Vector3.ZERO)
		var basis := Basis.IDENTITY
		if data.get("basis") is Basis:
			basis = data["basis"] as Basis
			if normal.length() < 0.01:
				normal = (-basis.z).normalized()
		if normal.length() < 0.01:
			normal = Vector3(0.0, 0.0, 1.0)
		else:
			normal = normal.normalized()
		if not (data.get("basis") is Basis):
			basis = _basis_from_normal(normal)
		return {"pos": pos, "normal": normal, "basis": basis}
	return {}


func _basis_from_normal(normal: Vector3) -> Basis:
	var n := normal.normalized()
	if n.length_squared() < 0.5:
		n = Vector3(0.0, 0.0, 1.0)
	var up := Vector3.UP
	if absf(n.dot(up)) > 0.92:
		up = Vector3.FORWARD
	var x := up.cross(n).normalized()
	var y := n.cross(x).normalized()
	return Basis(x, y, -n).orthonormalized()


func _compute_transfer_point(giver: Node3D, receiver: Node3D, height: float) -> Vector3:
	var mid := (giver.global_position + receiver.global_position) * 0.5
	mid.y = height
	var across := receiver.global_position - giver.global_position
	across.y = 0.0
	if across.length() < 0.001:
		across = Vector3(0.0, 0.0, 1.0)
	across = across.normalized()
	var side := Vector3(-across.z, 0.0, across.x)
	var to_cam := _camera_pos - mid
	to_cam.y = 0.0
	if to_cam.length() > 0.05 and side.dot(to_cam) < 0.0:
		side = -side
	mid += side * 0.12
	for actor_node: Node3D in [giver, receiver]:
		var delta := Vector2(mid.x - actor_node.global_position.x, mid.z - actor_node.global_position.z)
		if delta.length() < TORSO_CLEARANCE_RADIUS:
			mid += side * (TORSO_CLEARANCE_RADIUS - delta.length() + 0.04)
	return mid


func _actor_chest_y(actor: Node3D) -> float:
	var h := 1.7
	if actor.get("height_m") != null:
		h = float(actor.get("height_m"))
	return actor.global_position.y + h * 0.66


func _frame_u(frame_index: int, start_frame: int, end_frame: int) -> float:
	if end_frame <= start_frame:
		return 1.0 if frame_index >= end_frame else 0.0
	var u := clampf(float(frame_index - start_frame) / float(end_frame - start_frame), 0.0, 1.0)
	return u * u * (3.0 - 2.0 * u)


func _emit_lock_cue(at: Vector3, frame_index: int) -> void:
	_sound_cues.append({
		"frame": frame_index,
		"id": "module_lock",
		"pos": [snappedf(at.x, 0.001), snappedf(at.y, 0.001), snappedf(at.z, 0.001)],
	})
	var rate := 22050
	var count := int(float(rate) * 0.045)
	var samples := PackedByteArray()
	samples.resize(count * 2)
	for i in count:
		var t := float(i) / float(rate)
		var env := exp(-t * 90.0)
		var sample := sin(t * TAU * 1400.0) * env * 0.85
		var value := int(clampf(sample, -1.0, 1.0) * 32767.0)
		samples[i * 2] = value & 255
		samples[i * 2 + 1] = (value >> 8) & 255
	var wav := AudioStreamWAV.new()
	wav.format = AudioStreamWAV.FORMAT_16_BITS
	wav.mix_rate = rate
	wav.stereo = false
	wav.data = samples
	var player := AudioStreamPlayer3D.new()
	player.stream = wav
	player.volume_db = -6.0
	add_child(player)
	player.global_position = at
	player.play()


func _sample_stats(samples: Array[float]) -> Dictionary:
	if samples.is_empty():
		return {"min": 0.0, "mean": 0.0, "max": 0.0, "count": 0}
	var low := samples[0]
	var high := samples[0]
	var sum := 0.0
	for value: float in samples:
		low = minf(low, value)
		high = maxf(high, value)
		sum += value
	return {
		"min": snappedf(low, 0.0001),
		"mean": snappedf(sum / float(samples.size()), 0.0001),
		"max": snappedf(high, 0.0001),
		"count": samples.size(),
	}


func _causal_rank(state: String, seen_transfer: bool) -> int:
	if state == "transfer_contact":
		return 2
	if state.begins_with("held_by:"):
		return 3 if seen_transfer else 1
	match state:
		"align_socket":
			return 4
		"insert_socket":
			return 5
		"locked":
			return 6
		"device_online":
			return 7
		_:
			return 0


func _chain_errors() -> Array[String]:
	var errors: Array[String] = []
	var grouped := {}
	for rec: Dictionary in _ownership_log:
		var prop_id := str(rec.get("prop_id", ""))
		if not grouped.has(prop_id):
			grouped[prop_id] = []
		(grouped[prop_id] as Array).append(rec)
	for prop_key: Variant in grouped.keys():
		var prop_id := str(prop_key)
		var rows: Array = grouped[prop_id]
		var seen_transfer := false
		var seen_lock := false
		var lock_frame := -1
		var prev_rank := 0
		for rec_v: Variant in rows:
			var rec: Dictionary = rec_v
			var state := str(rec.get("state", ""))
			var owner := str(rec.get("owner", ""))
			var rank := _causal_rank(state, seen_transfer)
			if rank > 0 and prev_rank > 0 and rank < prev_rank:
				errors.append("%s regressed to %s" % [prop_id, state])
			if state == "transfer_contact":
				if seen_transfer:
					errors.append("%s repeated transfer_contact" % prop_id)
				seen_transfer = true
			if state.begins_with("held_by:") and owner == "":
				errors.append("%s held_by has an empty owner" % prop_id)
			if state == "locked":
				seen_lock = true
				lock_frame = int(rec.get("frame", -1))
				if owner != "console_socket":
					errors.append("%s locked with owner %s" % [prop_id, owner])
			if state == "device_online":
				if not seen_lock:
					errors.append("%s device_online before locked" % prop_id)
				elif int(rec.get("frame", 0)) < lock_frame + 4:
					errors.append("%s device_online is not at least 4 frames after lock" % prop_id)
			if seen_lock and state != "locked" and state != "device_online":
				errors.append("%s left the socket via %s" % [prop_id, state])
			if seen_lock and owner != "console_socket":
				errors.append("%s owner %s after lock" % [prop_id, owner])
			if rank > 0:
				prev_rank = rank
	for prop_key2: Variant in _props.keys():
		var entry: Dictionary = _props[str(prop_key2)]
		var contact_f := int(entry.get("resolved_contact_frame", 1 << 30))
		if str(entry.get("causal_giver", "")) != "" and contact_f <= _last_frame and not bool(entry.get("contact_done", false)):
			errors.append("%s never completed transfer_contact" % str(prop_key2))
		var lock_f := int(entry.get("resolved_lock_frame", 1 << 30))
		if bool(entry.get("install_tracked", false)) and lock_f <= _last_frame and not bool(entry.get("lock_done", false)):
			errors.append("%s never locked into the socket" % str(prop_key2))
		var online_f := int(entry.get("resolved_online_frame", 1 << 30))
		if bool(entry.get("install_tracked", false)) and bool(entry.get("lock_done", false)) and online_f <= _last_frame and not bool(entry.get("online_done", false)):
			errors.append("%s stayed offline after the online frame" % str(prop_key2))
	return errors


# --------------------------------------------------------- helpers & QC


func _compute_safe_handoff_point(giver: Node3D, receiver: Node3D, height: float) -> Vector3:
	var a: Vector3 = giver.global_position
	var b: Vector3 = receiver.global_position
	var mid: Vector3 = (a + b) * 0.5
	mid.y = height
	# Push meeting point outside each actor's torso clearance cylinder if too close.
	for actor_node: Node3D in [giver, receiver]:
		var delta_xz := Vector2(mid.x - actor_node.global_position.x, mid.z - actor_node.global_position.z)
		if delta_xz.length() < TORSO_CLEARANCE_RADIUS:
			var fwd: Vector3 = actor_node.global_transform.basis * Vector3.BACK
			var dir2 := Vector2(fwd.x, fwd.z)
			if delta_xz.length_squared() > 1e-4:
				dir2 = delta_xz.normalized()
			elif dir2.length_squared() > 1e-4:
				dir2 = dir2.normalized()
			else:
				dir2 = Vector2(0.0, 1.0)
			mid.x = actor_node.global_position.x + dir2.x * TORSO_CLEARANCE_RADIUS
			mid.z = actor_node.global_position.z + dir2.y * TORSO_CLEARANCE_RADIUS
	return mid


func _surface_target_world(entry: Dictionary, cfg: Dictionary) -> Vector3:
	var pos_v: Variant = cfg.get("pos", [0.0, 0.0, 0.0])
	var world_pos := Vector3.ZERO
	if pos_v is Array and (pos_v as Array).size() == 2:
		world_pos = Vector3(float((pos_v as Array)[0]), float(cfg.get("surface_y", 0.0)), float((pos_v as Array)[1]))
	else:
		world_pos = _vec3(pos_v, Vector3.ZERO)
		if cfg.has("surface_y"):
			world_pos.y = float(cfg["surface_y"])
	world_pos.y += float(entry["surface_rest_y"])
	return world_pos


func _place_on_surface(entry: Dictionary, cfg: Dictionary, actors: Dictionary) -> void:
	_release_owner(entry, actors)
	var root: Node3D = entry["root"] as Node3D
	if root.get_parent() != self:
		if root.get_parent() != null:
			root.get_parent().remove_child(root)
		add_child(root)
	var target: Vector3 = _surface_target_world(entry, cfg)
	root.global_position = target
	if cfg.has("rotation_deg"):
		root.rotation_degrees = _vec3(cfg["rotation_deg"], Vector3.ZERO)
	else:
		root.rotation = Vector3(0.0, deg_to_rad(float(cfg.get("yaw_deg", 0.0))), 0.0)
	root.visible = true
	entry["state"] = "surface"
	entry["blend_alpha"] = 0.0


func _release_owner(entry: Dictionary, actors: Dictionary) -> void:
	var prev_owner: String = str(entry["owner"])
	var prev_side: String = str(entry["owner_side"])
	if prev_owner != "" and actors.has(prev_owner):
		_set_actor_holding(actors[prev_owner], prev_side, false)
	entry["owner"] = ""


func _update_hand_gap(entry: Dictionary, actors: Dictionary) -> void:
	var owner: String = str(entry["owner"])
	if owner == "" or not actors.has(owner):
		entry["hand_gap_m"] = 0.0
		return
	var hand_pos: Vector3 = _actor_hand_world(actors[owner], str(entry["owner_side"]))
	var grip_pos: Vector3 = (entry["grip_socket"] as Node3D).global_position
	entry["hand_gap_m"] = hand_pos.distance_to(grip_pos)


func _actor_hand_socket(actor: Variant, side: String) -> Node3D:
	if actor == null or not actor is Node:
		return null
	var node := actor as Node
	if side == "left" and node.has_method("get_left_hand_socket"):
		var ls: Variant = node.call("get_left_hand_socket")
		if ls is Node3D:
			return ls as Node3D
	if node.has_method("get_hand_socket"):
		var rs: Variant = node.call("get_hand_socket")
		if rs is Node3D:
			return rs as Node3D
	return null


func _actor_hand_world(actor: Variant, side: String) -> Vector3:
	var socket: Node3D = _actor_hand_socket(actor, side)
	if socket != null:
		return socket.global_position
	if actor is Node and (actor as Node).has_method("hand_world_position"):
		return (actor as Node).call("hand_world_position")
	if actor is Node3D:
		return (actor as Node3D).global_position
	return Vector3.ZERO


func _set_actor_holding(actor: Variant, side: String, is_holding: bool) -> void:
	if actor == null or not actor is Node:
		return
	var node := actor as Node
	if node.has_method("set_hand_holding"):
		node.call("set_hand_holding", side, is_holding)
	elif side == "right":
		node.set("holding", is_holding)


func _load_or_build_model(prop_id: String, spec: Dictionary) -> Node3D:
	var path: String = str(spec.get("model", ""))
	if path != "" and _character_script != null:
		var loaded: Node3D = _character_script.call("load_gltf_scene", path) as Node3D
		if loaded != null:
			if bool(spec.get("matte", true)):
				_character_script.call("matte_materials", loaded, float(spec.get("roughness", 0.65)))
			if bool(spec.get("toon", false)):
				_character_script.call("toon_materials", loaded, Color(1, 1, 1), 0.1)
			return loaded
	return _build_fallback_prop(prop_id, spec)


func _build_fallback_prop(prop_id: String, spec: Dictionary) -> Node3D:
	var root := Node3D.new()
	root.name = "Fallback_%s" % prop_id
	var kind: String = str(spec.get("kind", "generic")).to_lower()
	var size: Vector3 = _vec3(spec.get("size", null), Vector3(0.08, 0.18, 0.05))
	var color := Color(str(spec.get("color", "#4a90d9")))
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = 0.6

	if kind == "sensor" or kind == "tool":
		var grip := MeshInstance3D.new()
		var grip_mesh := CylinderMesh.new()
		grip_mesh.top_radius = 0.018
		grip_mesh.bottom_radius = 0.020
		grip_mesh.height = 0.12
		grip.mesh = grip_mesh
		grip.material_override = mat
		root.add_child(grip)

		var head := MeshInstance3D.new()
		var head_mesh := BoxMesh.new()
		head_mesh.size = Vector3(0.08, 0.055, 0.04)
		head.mesh = head_mesh
		head.position = Vector3(0.0, 0.085, 0.0)
		var head_mat := StandardMaterial3D.new()
		head_mat.albedo_color = Color("#e6ebf0")
		head_mat.roughness = 0.55
		head.material_override = head_mat
		root.add_child(head)
	else:
		var box_mi := MeshInstance3D.new()
		var box_mesh := BoxMesh.new()
		box_mesh.size = size
		box_mi.mesh = box_mesh
		box_mi.material_override = mat
		root.add_child(box_mi)
	return root


func _local_bounds(node: Node3D, relative_to: Node) -> AABB:
	var result := AABB()
	var first := true
	var stack: Array[Node] = [node]
	while not stack.is_empty():
		var cur: Node = stack.pop_back()
		for child: Node in cur.get_children():
			stack.append(child)
		if cur is MeshInstance3D and (cur as MeshInstance3D).mesh != null:
			var xform := Transform3D.IDENTITY
			var walk: Node = cur
			while walk != null and walk != relative_to:
				if walk is Node3D:
					xform = (walk as Node3D).transform * xform
				walk = walk.get_parent()
			var box: AABB = xform * (cur as MeshInstance3D).mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result


func _world_min_y(root: Node3D) -> float:
	var lowest := INF
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var cur: Node = stack.pop_back()
		for child: Node in cur.get_children():
			stack.append(child)
		if cur is MeshInstance3D and (cur as MeshInstance3D).mesh != null:
			var mi := cur as MeshInstance3D
			var aabb: AABB = mi.mesh.get_aabb()
			for i in 8:
				var corner: Vector3 = mi.global_transform * aabb.get_endpoint(i)
				lowest = minf(lowest, corner.y)
	return 0.0 if lowest == INF else lowest


func _active_timeline_entry(list: Array[Dictionary], frame_index: int) -> Dictionary:
	var best: Dictionary = {}
	var best_from := -1
	for item: Dictionary in list:
		var window := _entry_window(item)
		if frame_index < window.x or frame_index >= window.y:
			continue
		if window.x >= best_from:
			best = item
			best_from = window.x
	return best


func _entry_window(item: Dictionary) -> Vector2i:
	var start := int(item.get("from", item.get("start_frame", 0)))
	if item.has("to"):
		return Vector2i(start, int(item["to"]))
	if item.has("end_frame") or item.has("withdraw_end_frame") or item.has("release_frame") or item.has("lock_frame") or item.has("online_frame"):
		var action := str(item.get("action", item.get("state", ""))).to_lower()
		var end_inclusive := start
		if action == "install_into_socket" or action == "install_module":
			end_inclusive = int(item.get("withdraw_end_frame", item.get("online_frame", item.get("lock_frame", start))))
		else:
			end_inclusive = int(item.get("end_frame", item.get("release_frame", start)))
		return Vector2i(start, end_inclusive + 1)
	# Legacy open-ended timeline rows stay active until a later row takes over.
	return Vector2i(start, 1 << 30)


func _dict_array(value: Variant) -> Array[Dictionary]:
	var out: Array[Dictionary] = []
	if value is Array:
		for item: Variant in value as Array:
			if item is Dictionary:
				out.append(item as Dictionary)
	return out


func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback


func get_qc_snapshot(prop_id: String) -> Dictionary:
	if not _props.has(prop_id):
		return {}
	var entry: Dictionary = _props[prop_id]
	var root: Node3D = entry["root"] as Node3D
	var wp: Vector3 = root.global_position
	var gp: Vector3 = (entry["grip_socket"] as Node3D).global_position
	var rp: Vector3 = (entry["receive_socket"] as Node3D).global_position
	var op: Vector3 = (entry["operate_socket"] as Node3D).global_position
	return {
		"prop_id": prop_id,
		"state": str(entry["state"]),
		"owner": str(entry["owner"]),
		"owner_side": str(entry["owner_side"]),
		"visible": root.visible,
		"world_pos": [snappedf(wp.x, 0.001), snappedf(wp.y, 0.001), snappedf(wp.z, 0.001)],
		"min_y": snappedf(_world_min_y(root), 0.001),
		"surface_rest_y": snappedf(float(entry["surface_rest_y"]), 0.001),
		"hand_gap_m": snappedf(float(entry["hand_gap_m"]), 0.001),
		"grip_world_pos": [snappedf(gp.x, 0.001), snappedf(gp.y, 0.001), snappedf(gp.z, 0.001)],
		"receive_world_pos": [snappedf(rp.x, 0.001), snappedf(rp.y, 0.001), snappedf(rp.z, 0.001)],
		"operate_world_pos": [snappedf(op.x, 0.001), snappedf(op.y, 0.001), snappedf(op.z, 0.001)],
		"transfer_count": int(entry["transfer_count"]),
		"causal_state": str(entry.get("causal_state", "")),
		"interaction_phase": str(entry.get("phase_this_frame", "")),
		"device_state": str(entry.get("device_state", "offline")),
		"socket_err_m": snappedf(float(entry.get("socket_err_m", -1.0)), 0.001),
		"giver_hand_to_prop_m": snappedf(float(entry.get("giver_hand_to_prop_m", -1.0)), 0.001),
		"receiver_hand_to_prop_m": snappedf(float(entry.get("receiver_hand_to_prop_m", -1.0)), 0.001),
		"module_to_socket_m": snappedf(float(entry.get("module_to_socket_m", -1.0)), 0.001),
	}


func get_all_qc_snapshots() -> Dictionary:
	var out := {}
	for prop_key: Variant in _props.keys():
		var prop_id: String = str(prop_key)
		out[prop_id] = get_qc_snapshot(prop_id)
	return out
