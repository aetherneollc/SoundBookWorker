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
## Emits per-frame deterministic telemetry for the QC pipeline.

const DEFAULT_HANDOFF_HEIGHT := 1.16
const TORSO_CLEARANCE_RADIUS := 0.34
const DEFAULT_MAX_HANDOFF_GAP := 0.28
const HAND_MIN_SEPARATION_M := 0.085
const RECEIVER_HOLD_OVERLAP_FRAMES := 5

var _character_script: Script
var _props: Dictionary = {}        # prop_id -> Dictionary runtime state
var _handoff_log: Array[Dictionary] = []
var _reach_requests: Dictionary = {}  # actor_id -> {target: Vector3, weight: float, side: String, prop_id: String}
var _gaze_requests: Dictionary = {}   # actor_id -> {point: Vector3, weight: float, prop_id: String}


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
	}
	_props[prop_id] = state

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
		if float(entry["blend_alpha"]) > 0.001 and str(entry["owner"]) != "":
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
				entry["state"] = "held"
		"handoff":
			_eval_handoff_action(entry, active, frame_index, fps, actors)
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
		var a: int = int(item.get("from", 0))
		var has_to: bool = item.has("to")
		var b: int = int(item.get("to", 0))
		if frame_index < a or (has_to and frame_index >= b):
			continue
		if a >= best_from:
			best = item
			best_from = a
	return best


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
	}


func get_all_qc_snapshots() -> Dictionary:
	var out := {}
	for prop_key: Variant in _props.keys():
		var prop_id: String = str(prop_key)
		out[prop_id] = get_qc_snapshot(prop_id)
	return out
