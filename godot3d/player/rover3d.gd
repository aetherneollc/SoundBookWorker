class_name Rover3D
extends Node3D

## Physical rover controller with exact floor grounding, physically accurate
## wheel rotation, suspension pitch/bob, rotating sensor turret, and a
## flush-mounted multi-state status indicator LED.
##
## Data-driven from stage3d.json `rover` config + optional `rover_meta.json`.
## Works with multi-node rigged rover GLBs (Wheel_* + Turret) as well as
## single-mesh GLBs or procedural fallbacks.

const DEFAULT_WHEEL_RADIUS := 0.16
const MAX_SUSPENSION_PITCH_RAD := 0.055
const TURRET_DAMP_RATE := 7.5
const STATUS_COLORS := {
	"idle": Color("#38d9f5"),
	"moving": Color("#ffb347"),
	"scanning": Color("#00f0ff"),
	"alert": Color("#ff3b30"),
}

var rover_id := "rover"
var height_m := 0.95
var status := "idle"
var speed_mps := 0.0
var wheel_spin_rad := 0.0
var wheel_radius_m := DEFAULT_WHEEL_RADIUS
var turret_yaw_deg := 0.0

var _spec: Dictionary = {}
var _meta: Dictionary = {}
var _wheel_layer: Node3D
var _suspension: Node3D
var _body_root: Node3D
var _model: Node3D

## Array of {node: Node3D, rest_basis: Basis, spin_axis: Vector3, radius: float, spin_sign: float}
var _wheels: Array[Dictionary] = []
var _turret: Node3D
var _turret_rest_basis := Basis.IDENTITY
var _turret_yaw_rad := 0.0
var _turret_yaw_limit_rad := PI
var _turret_target := Vector3.INF
var _turret_target_weight := 0.0

## Flush-mounted status LED components
var _led_mount: Node3D
var _led_lens_mat: StandardMaterial3D
var _led_light: OmniLight3D
var _eye_anchor: Node3D

var _has_prev_pos := false
var _prev_pos := Vector3.ZERO
var _prev_speed := 0.0
var _suspension_pitch := 0.0
var _suspension_roll := 0.0
var _elapsed_s := 0.0
var _is_speaking := false
var _speech_energy := 0.0
var _status_override := ""
var _forward_axis := Vector3.BACK  # +Z forward in Godot character/stage convention


func setup(config: Dictionary, character_script: Script = null) -> bool:
	_spec = config.duplicate(true)
	rover_id = str(config.get("id", name if name != "" else "rover"))
	height_m = maxf(float(config.get("height_m", 0.95)), 0.15)
	_meta = _load_meta(config)

	_clear_children()

	_wheel_layer = Node3D.new()
	_wheel_layer.name = "WheelLayer"
	add_child(_wheel_layer)

	_suspension = Node3D.new()
	_suspension.name = "Suspension"
	add_child(_suspension)

	_body_root = Node3D.new()
	_body_root.name = "Body"
	_suspension.add_child(_body_root)

	var model_path: String = str(config.get("model", ""))
	if model_path != "" and not bool(config.get("procedural", false)):
		_model = _load_glb(model_path, character_script)
		if _model != null:
			if bool(config.get("matte", true)) and character_script != null:
				character_script.call("matte_materials", _model, float(config.get("roughness", 0.72)))
			if bool(config.get("toon", false)) and character_script != null:
				character_script.call("toon_materials", _model, Color(1, 1, 1), 0.1)

	if _model == null:
		_model = _build_procedural_rover()

	_model.name = "RoverModel"
	_body_root.add_child(_model)

	# Scale, orient, and enforce exact floor contact (min_y == 0.0).
	var raw_box: AABB = _bounds_relative(_model, _body_root)
	var scale_val: float = float(config.get("scale", 0.0))
	if scale_val <= 0.0001:
		scale_val = height_m / maxf(raw_box.size.y, 0.01)

	_model.position = Vector3(
		-(raw_box.position.x + raw_box.size.x * 0.5),
		-raw_box.position.y,
		-(raw_box.position.z + raw_box.size.z * 0.5)
	)
	_body_root.scale = Vector3.ONE * scale_val
	var yaw_offset_deg: float = float(config.get("yaw_offset_deg", _meta.get("yaw_offset_deg", 0.0)))
	_body_root.rotation.y = deg_to_rad(yaw_offset_deg)

	_discover_wheels_and_turret()
	_enforce_exact_floor_contact()
	_build_flush_status_led()
	_build_eye_anchor()

	_has_prev_pos = false
	_prev_pos = global_position
	_prev_speed = 0.0
	speed_mps = 0.0
	wheel_spin_rad = 0.0
	_elapsed_s = 0.0
	status = "idle"
	return true


# -------------------------------------------------------------- public API


func set_status(new_status: String) -> void:
	var key: String = new_status.strip_edges().to_lower()
	if STATUS_COLORS.has(key):
		_status_override = key
		status = key
	elif key == "" or key == "auto":
		_status_override = ""


func set_speaking(is_speaking: bool, speech_energy: float = 0.5) -> void:
	_is_speaking = is_speaking
	_speech_energy = clampf(speech_energy, 0.0, 1.0)


func set_turret_target(world_target: Vector3, weight: float = 1.0) -> void:
	_turret_target = world_target
	_turret_target_weight = clampf(weight, 0.0, 1.0)


func clear_turret_target() -> void:
	_turret_target = Vector3.INF
	_turret_target_weight = 0.0


func get_eye_world_position() -> Vector3:
	if _eye_anchor != null:
		return _eye_anchor.global_position
	return global_position + Vector3(0.0, height_m * 0.85, 0.0)


func get_wheel_count() -> int:
	return _wheels.size()


func has_turret() -> bool:
	return _turret != null


## Advances rover pose, wheel rotation, suspension pitch/bob, turret tracking,
## and flush status indicator LED for one frame.
func update_frame(
	world_pos: Vector3,
	yaw_rad: float,
	dt: float,
	turret_target: Vector3 = Vector3.INF,
	status_override: String = "",
	is_speaking: bool = false,
	speech_energy: float = 0.0
) -> void:
	var step_dt: float = maxf(dt, 1.0 / 240.0)
	_elapsed_s += step_dt

	# Lock rover root Y cleanly to floor plane (y = 0.0) unless explicit elevation provided.
	var grounded_pos := Vector3(world_pos.x, world_pos.y, world_pos.z)
	global_position = grounded_pos
	rotation = Vector3(0.0, yaw_rad, 0.0)

	if turret_target != Vector3.INF:
		set_turret_target(turret_target, 1.0)
	if status_override != "":
		set_status(status_override)
	_is_speaking = is_speaking
	_speech_energy = clampf(speech_energy, 0.0, 1.0)

	# 1. Signed forward displacement & exact physical wheel spin.
	var signed_dist := 0.0
	if _has_prev_pos:
		var delta_pos: Vector3 = grounded_pos - _prev_pos
		var forward_world: Vector3 = (global_transform.basis * _forward_axis).normalized()
		signed_dist = delta_pos.dot(forward_world)
		speed_mps = signed_dist / step_dt
	else:
		speed_mps = 0.0
		_has_prev_pos = true
	_prev_pos = grounded_pos

	var accel: float = (speed_mps - _prev_speed) / step_dt
	_prev_speed = speed_mps

	if absf(signed_dist) > 1e-6:
		var mean_r: float = maxf(wheel_radius_m, 0.02)
		wheel_spin_rad += signed_dist / mean_r
		for w_entry: Dictionary in _wheels:
			var w_node: Node3D = w_entry["node"] as Node3D
			var r: float = maxf(float(w_entry["radius"]), 0.02)
			var angle: float = float(w_entry["angle"]) + (signed_dist / r) * float(w_entry["spin_sign"])
			w_entry["angle"] = angle
			var axis: Vector3 = w_entry["spin_axis"] as Vector3
			var rest_basis: Basis = w_entry["rest_basis"] as Basis
			if w_node != null and is_instance_valid(w_node):
				w_node.transform.basis = rest_basis * Basis(axis, angle)

	# 2. Smooth acceleration/deceleration suspension pitch + subtle rolling micro-bob.
	var abs_speed: float = absf(speed_mps)
	var moving: bool = abs_speed > 0.025
	var drive: float = clampf(abs_speed / 0.45, 0.0, 1.0) if moving else 0.0
	var bob_amp: float = 0.0055 if not _wheels.is_empty() else 0.0025
	var raw_bob: float = drive * (bob_amp * sin(_elapsed_s * 18.0) + bob_amp * 0.4 * sin(_elapsed_s * 29.0 + 1.1))
	# When wheels are fused into the body, keep bob non-negative so mesh never penetrates floor.
	var bob: float = raw_bob if not _wheels.is_empty() else maxf(raw_bob, 0.0)
	var target_pitch: float = drive * deg_to_rad(0.65) * sin(_elapsed_s * 7.2) - clampf(accel * 0.032, -MAX_SUSPENSION_PITCH_RAD, MAX_SUSPENSION_PITCH_RAD)
	var target_roll: float = drive * deg_to_rad(0.45) * sin(_elapsed_s * 5.3)
	var damp: float = 1.0 - exp(-step_dt * 10.0)
	_suspension_pitch = lerpf(_suspension_pitch, target_pitch, damp)
	_suspension_roll = lerpf(_suspension_roll, target_roll, damp)
	_suspension.position = Vector3(0.0, bob, 0.0)
	_suspension.rotation = Vector3(_suspension_pitch, 0.0, _suspension_roll)

	# 3. Auto status selection if not overridden.
	if _status_override != "":
		status = _status_override
	elif moving:
		status = "moving"
	else:
		status = "idle"

	# 4. Turret smooth tracking / scanning sweep.
	_update_turret(step_dt)

	# 5. Flush status LED glow & pulse.
	_update_status_led()


# --------------------------------------------------------- internal setup


func _clear_children() -> void:
	for child: Node in get_children():
		remove_child(child)
		child.queue_free()
	_wheels.clear()
	_turret = null
	_led_mount = null
	_eye_anchor = null


func _load_meta(config: Dictionary) -> Dictionary:
	var candidates: Array[String] = []
	var explicit_meta: String = str(config.get("meta", ""))
	if explicit_meta != "":
		candidates.append(explicit_meta)
	var model_path: String = str(config.get("model", ""))
	if model_path != "":
		var base_dir: String = model_path.get_base_dir()
		var stem: String = model_path.get_file().get_basename()
		candidates.append(base_dir.path_join("%s_meta.json" % stem))
		candidates.append(base_dir.path_join("rover_meta.json"))
	for path: String in candidates:
		if path != "" and FileAccess.file_exists(path):
			var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
			if parsed is Dictionary:
				return parsed as Dictionary
	return {}


func _load_glb(path: String, character_script: Script) -> Node3D:
	if path == "" or not FileAccess.file_exists(path):
		return null
	if character_script != null:
		return character_script.call("load_gltf_scene", path) as Node3D
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		return null
	var scene: Node = doc.generate_scene(state)
	if scene == null:
		return null
	if scene is Node3D:
		return scene as Node3D
	var wrapper := Node3D.new()
	wrapper.add_child(scene)
	return wrapper


func _build_procedural_rover() -> Node3D:
	var root := Node3D.new()
	root.name = "ProceduralRover"

	var hull_mat := StandardMaterial3D.new()
	hull_mat.albedo_color = Color("#d8dde4")
	hull_mat.roughness = 0.65

	var trim_mat := StandardMaterial3D.new()
	trim_mat.albedo_color = Color("#e88a2e")
	trim_mat.roughness = 0.55

	var wheel_mat := StandardMaterial3D.new()
	wheel_mat.albedo_color = Color("#242933")
	wheel_mat.roughness = 0.88

	var wheel_r := 0.16
	var wheel_w := 0.11

	var chassis := MeshInstance3D.new()
	chassis.name = "Chassis"
	var chassis_mesh := BoxMesh.new()
	chassis_mesh.size = Vector3(0.62, 0.34, 0.86)
	chassis.mesh = chassis_mesh
	chassis.position = Vector3(0.0, wheel_r + 0.24, 0.0)
	chassis.material_override = hull_mat
	root.add_child(chassis)

	var bumper := MeshInstance3D.new()
	bumper.name = "TrimBand"
	var bumper_mesh := BoxMesh.new()
	bumper_mesh.size = Vector3(0.64, 0.07, 0.88)
	bumper.mesh = bumper_mesh
	bumper.position = Vector3(0.0, wheel_r + 0.14, 0.0)
	bumper.material_override = trim_mat
	root.add_child(bumper)

	# 4 separate wheels positioned so their bottom rests cleanly at y = 0.0.
	var wheel_specs := [
		["Wheel_FL", Vector3(-0.34, wheel_r, 0.28)],
		["Wheel_FR", Vector3(0.34, wheel_r, 0.28)],
		["Wheel_RL", Vector3(-0.34, wheel_r, -0.28)],
		["Wheel_RR", Vector3(0.34, wheel_r, -0.28)],
	]
	for ws: Array in wheel_specs:
		var wheel_node := Node3D.new()
		wheel_node.name = str(ws[0])
		wheel_node.position = ws[1] as Vector3
		var cyl_mi := MeshInstance3D.new()
		var cyl := CylinderMesh.new()
		cyl.top_radius = wheel_r
		cyl.bottom_radius = wheel_r
		cyl.height = wheel_w
		cyl.radial_segments = 20
		cyl_mi.mesh = cyl
		cyl_mi.rotation = Vector3(0.0, 0.0, PI * 0.5)
		cyl_mi.material_override = wheel_mat
		wheel_node.add_child(cyl_mi)
		root.add_child(wheel_node)

	# Rotating camera/sensor turret on top of chassis.
	var turret := Node3D.new()
	turret.name = "Turret"
	turret.position = Vector3(0.0, wheel_r + 0.44, 0.10)
	var neck := MeshInstance3D.new()
	var neck_mesh := CylinderMesh.new()
	neck_mesh.top_radius = 0.06
	neck_mesh.bottom_radius = 0.075
	neck_mesh.height = 0.14
	neck.mesh = neck_mesh
	neck.position = Vector3(0.0, 0.07, 0.0)
	neck.material_override = wheel_mat
	turret.add_child(neck)

	var head := MeshInstance3D.new()
	var head_mesh := BoxMesh.new()
	head_mesh.size = Vector3(0.24, 0.14, 0.20)
	head.mesh = head_mesh
	head.position = Vector3(0.0, 0.19, 0.02)
	head.material_override = hull_mat
	turret.add_child(head)

	var visor := MeshInstance3D.new()
	var visor_mesh := BoxMesh.new()
	visor_mesh.size = Vector3(0.18, 0.07, 0.02)
	visor.mesh = visor_mesh
	visor.position = Vector3(0.0, 0.19, 0.12)
	var visor_mat := StandardMaterial3D.new()
	visor_mat.albedo_color = Color("#121a24")
	visor_mat.emission_enabled = true
	visor_mat.emission = Color("#38d9f5")
	visor_mat.emission_energy_multiplier = 0.9
	visor.material_override = visor_mat
	turret.add_child(visor)

	root.add_child(turret)
	return root


func _discover_wheels_and_turret() -> void:
	_wheels.clear()
	_turret = null

	var meta_wheels: Dictionary = {}
	if _meta.get("wheels", null) is Array:
		for item: Variant in _meta["wheels"] as Array:
			if item is String:
				meta_wheels[str(item).to_lower()] = {}
			elif item is Dictionary:
				meta_wheels[str((item as Dictionary).get("name", "")).to_lower()] = item as Dictionary

	var turret_hint: String = str(_meta.get("turret_node", _spec.get("turret_node", ""))).to_lower()
	var found_wheel_nodes: Array[Node3D] = []

	var stack: Array[Node] = [_model]
	while not stack.is_empty():
		var cur: Node = stack.pop_front()
		if cur is Node3D and cur != _model:
			var lname: String = str(cur.name).to_lower()
			var is_wheel: bool = lname.begins_with("wheel_") or lname.begins_with("wheel") or meta_wheels.has(lname)
			if is_wheel and not _has_wheel_ancestor(cur, found_wheel_nodes):
				found_wheel_nodes.append(cur as Node3D)
				continue
			if _turret == null:
				var is_turret: bool = (turret_hint != "" and lname == turret_hint) or lname == "turret" or lname.begins_with("turret_") or lname == "camera_turret" or lname == "sensor_turret"
				if is_turret:
					_turret = cur as Node3D
		for child: Node in cur.get_children():
			stack.append(child)

	# Reparent discovered wheels into the grounded _wheel_layer so suspension
	# bob/pitch moves the chassis without ever lifting wheels off the floor!
	var radii_sum := 0.0
	var default_spin_axis: Vector3 = _vec3(_meta.get("wheel_spin_axis", _spec.get("wheel_spin_axis", null)), Vector3.RIGHT).normalized()
	var default_spin_sign: float = float(_meta.get("wheel_spin_sign", _spec.get("wheel_spin_sign", 1.0)))

	for w_node: Node3D in found_wheel_nodes:
		var rel_xform: Transform3D = _relative_xform(w_node, self)
		if w_node.get_parent() != null:
			w_node.get_parent().remove_child(w_node)
		_wheel_layer.add_child(w_node)
		w_node.transform = rel_xform

		var lname: String = str(w_node.name).to_lower()
		var w_meta: Dictionary = meta_wheels.get(lname, {}) as Dictionary if meta_wheels.get(lname, {}) is Dictionary else {}
		var w_box: AABB = _bounds_relative(w_node, _wheel_layer)
		var measured_r: float = maxf(w_box.size.y * 0.5, 0.03) if w_box.size.y > 0.01 else DEFAULT_WHEEL_RADIUS
		var r: float = float(w_meta.get("radius", _meta.get("wheel_radius", _spec.get("wheel_radius", measured_r))))
		var rest_bottom_offset: float = w_node.position.y - w_box.position.y if w_box.size.length_squared() > 1e-8 else r
		var local_axis: Vector3 = _vec3(w_meta.get("spin_axis", null), default_spin_axis).normalized()
		# Map rover-local spin axis (+X) into the wheel node's rest local frame.
		var rest_basis: Basis = w_node.transform.basis
		var axis_in_wheel: Vector3 = (rest_basis.inverse() * local_axis).normalized()
		if axis_in_wheel.length_squared() < 0.25:
			axis_in_wheel = Vector3.RIGHT
		var spin_sign: float = float(w_meta.get("spin_sign", default_spin_sign))
		_wheels.append({
			"node": w_node,
			"rest_basis": rest_basis,
			"spin_axis": axis_in_wheel,
			"radius": r,
			"rest_bottom_offset": rest_bottom_offset,
			"spin_sign": spin_sign,
			"angle": 0.0,
		})
		radii_sum += r

	if not _wheels.is_empty():
		wheel_radius_m = radii_sum / float(_wheels.size())
	else:
		wheel_radius_m = float(_meta.get("wheel_radius", _spec.get("wheel_radius", DEFAULT_WHEEL_RADIUS)))

	if _turret != null:
		_turret_rest_basis = _turret.transform.basis
		_turret_yaw_limit_rad = deg_to_rad(float(_meta.get("turret_yaw_limit_deg", _spec.get("turret_yaw_limit_deg", 180.0))))


func _has_wheel_ancestor(node: Node, wheels: Array[Node3D]) -> bool:
	var walk: Node = node.get_parent()
	while walk != null and walk != _model:
		if walk is Node3D and (walk as Node3D) in wheels:
			return true
		walk = walk.get_parent()
	return false


## Guarantees exact floor contact (min_y == 0.0) for wheels and/or full rover body.
func _enforce_exact_floor_contact() -> void:
	if not _wheels.is_empty():
		var min_wy := INF
		for w_entry: Dictionary in _wheels:
			var w_node: Node3D = w_entry["node"] as Node3D
			var wb: AABB = _bounds_relative(w_node, self)
			if wb.size.length_squared() > 1e-8:
				min_wy = minf(min_wy, wb.position.y)
		if min_wy != INF and absf(min_wy) > 1e-5:
			_wheel_layer.position.y -= min_wy
			_body_root.position.y -= min_wy
	else:
		var hull_box: AABB = _bounds_relative(_body_root, self)
		if hull_box.size.length_squared() > 1e-8 and absf(hull_box.position.y) > 1e-5:
			_body_root.position.y -= hull_box.position.y


## Builds a flush-mounted bezel + recessed status LED seated directly onto the
## rover hull/turret surface (no floating disconnected sphere).
func _build_flush_status_led() -> void:
	var body_box: AABB = _bounds_relative(_body_root, _suspension)
	var default_pos: Vector3
	if body_box.size.length_squared() > 1e-6:
		# Seated flush into the upper deck surface of the rover body.
		default_pos = Vector3(
			body_box.position.x + body_box.size.x * 0.5,
			body_box.end.y - 0.004,
			body_box.position.z + body_box.size.z * 0.65
		)
	else:
		default_pos = Vector3(0.0, height_m * 0.82, 0.14)

	var led_pos: Vector3 = _vec3(_spec.get("led_offset", _meta.get("led_offset", null)), default_pos)

	_led_mount = Node3D.new()
	_led_mount.name = "FlushStatusLED"
	_led_mount.position = led_pos
	_suspension.add_child(_led_mount)

	# Low-profile recessed metallic bezel embedded into the hull surface.
	var bezel := MeshInstance3D.new()
	bezel.name = "Bezel"
	var bezel_mesh := CylinderMesh.new()
	bezel_mesh.top_radius = 0.030
	bezel_mesh.bottom_radius = 0.034
	bezel_mesh.height = 0.014
	bezel_mesh.radial_segments = 20
	bezel.mesh = bezel_mesh
	bezel.position = Vector3(0.0, -0.002, 0.0)
	var bezel_mat := StandardMaterial3D.new()
	bezel_mat.albedo_color = Color("#1e242c")
	bezel_mat.roughness = 0.55
	bezel_mat.metallic = 0.35
	bezel.material_override = bezel_mat
	_led_mount.add_child(bezel)

	# Flush optical lens seated inside the bezel rim (never floating).
	var lens := MeshInstance3D.new()
	lens.name = "Lens"
	var lens_mesh := CylinderMesh.new()
	lens_mesh.top_radius = 0.022
	lens_mesh.bottom_radius = 0.024
	lens_mesh.height = 0.006
	lens_mesh.radial_segments = 20
	lens.mesh = lens_mesh
	lens.position = Vector3(0.0, 0.004, 0.0)
	_led_lens_mat = StandardMaterial3D.new()
	_led_lens_mat.albedo_color = STATUS_COLORS["idle"]
	_led_lens_mat.roughness = 0.25
	_led_lens_mat.emission_enabled = true
	_led_lens_mat.emission = STATUS_COLORS["idle"]
	_led_lens_mat.emission_energy_multiplier = 1.2
	lens.material_override = _led_lens_mat
	_led_mount.add_child(lens)

	_led_light = OmniLight3D.new()
	_led_light.name = "StatusGlow"
	_led_light.light_color = STATUS_COLORS["idle"]
	_led_light.omni_range = 0.85
	_led_light.light_energy = 0.15
	_led_light.shadow_enabled = false
	_led_light.position = Vector3(0.0, 0.025, 0.0)
	_led_mount.add_child(_led_light)


func _build_eye_anchor() -> void:
	_eye_anchor = Node3D.new()
	_eye_anchor.name = "EyeAnchor"
	if _turret != null:
		_turret.add_child(_eye_anchor)
		var tbox: AABB = _bounds_relative(_turret, _turret)
		_eye_anchor.position = tbox.get_center() if tbox.size.length_squared() > 1e-6 else Vector3(0.0, 0.15, 0.08)
	else:
		_suspension.add_child(_eye_anchor)
		var fallback_eye := Vector3(0.0, height_m * 0.86, 0.12)
		_eye_anchor.position = _vec3(_spec.get("eye_offset", _meta.get("eye_offset", null)), fallback_eye)


# --------------------------------------------------------- turret & LED


func _update_turret(dt: float) -> void:
	if _turret == null:
		turret_yaw_deg = 0.0
		return
	var desired_yaw := 0.0
	if _turret_target != Vector3.INF and _turret_target_weight > 0.01:
		var local_t: Vector3 = global_transform.affine_inverse() * _turret_target
		var to_t := Vector2(local_t.x, local_t.z)
		if to_t.length_squared() > 0.0004:
			desired_yaw = clampf(atan2(to_t.x, to_t.y), -_turret_yaw_limit_rad, _turret_yaw_limit_rad) * _turret_target_weight
	elif status == "scanning":
		desired_yaw = deg_to_rad(42.0) * sin(_elapsed_s * 1.35)

	var alpha: float = 1.0 - exp(-dt * TURRET_DAMP_RATE)
	var diff: float = wrapf(desired_yaw - _turret_yaw_rad, -PI, PI)
	_turret_yaw_rad = clampf(_turret_yaw_rad + diff * alpha, -_turret_yaw_limit_rad, _turret_yaw_limit_rad)
	turret_yaw_deg = rad_to_deg(_turret_yaw_rad)
	_turret.transform.basis = Basis(Vector3.UP, _turret_yaw_rad) * _turret_rest_basis


func _update_status_led() -> void:
	if _led_lens_mat == null:
		return
	var col: Color = STATUS_COLORS.get(status, STATUS_COLORS["idle"])
	var energy := 1.0
	match status:
		"idle":
			energy = 0.75 + 0.30 * (0.5 + 0.5 * sin(_elapsed_s * 2.2))
		"moving":
			energy = 1.15 + 0.65 * pow(maxf(0.0, sin(_elapsed_s * TAU * 0.9)), 4.0)
		"scanning":
			energy = 1.35 + 1.45 * (0.5 + 0.5 * sin(_elapsed_s * 7.5))
		"alert":
			var phase: float = fposmod(_elapsed_s * 2.4, 1.0)
			var flash: float = 1.0 if (phase < 0.18 or (phase > 0.28 and phase < 0.46)) else 0.15
			energy = 0.4 + 2.8 * flash
	if _is_speaking:
		energy += 0.9 + 1.2 * maxf(_speech_energy, absf(sin(_elapsed_s * 11.0)) * 0.5)

	_led_lens_mat.albedo_color = col
	_led_lens_mat.emission = col
	_led_lens_mat.emission_energy_multiplier = energy
	if _led_light != null:
		_led_light.light_color = col
		_led_light.light_energy = clampf(energy * 0.18, 0.05, 0.65)


# --------------------------------------------------------- geometry & QC


func _relative_xform(node: Node3D, ancestor: Node) -> Transform3D:
	var xform := Transform3D.IDENTITY
	var walk: Node = node
	while walk != null and walk != ancestor:
		if walk is Node3D:
			xform = (walk as Node3D).transform * xform
		walk = walk.get_parent()
	return xform


func _bounds_relative(root: Node3D, relative_to: Node) -> AABB:
	var result := AABB()
	var first := true
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var cur: Node = stack.pop_back()
		for child: Node in cur.get_children():
			stack.append(child)
		if cur is MeshInstance3D and (cur as MeshInstance3D).mesh != null:
			var xform: Transform3D = _relative_xform(cur as Node3D, relative_to)
			var box: AABB = xform * (cur as MeshInstance3D).mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result


func _world_min_y_of(nodes: Array[Node3D]) -> float:
	var lowest := INF
	for root: Node3D in nodes:
		if root == null or not is_instance_valid(root):
			continue
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


func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback


func get_qc_snapshot() -> Dictionary:
	var hull_min_y: float = _world_min_y_of([_body_root])
	var wheel_min_y := INF
	for w_entry: Dictionary in _wheels:
		var wn: Node3D = w_entry["node"] as Node3D
		if wn != null:
			# Physical tire contact bottom = axle world Y - wheel rest bottom radius
			# (avoids rotated-AABB cube corner overshoot when a cylinder spins 45 deg).
			var tire_y: float = wn.global_position.y - float(w_entry.get("rest_bottom_offset", w_entry.get("radius", wheel_radius_m)))
			wheel_min_y = minf(wheel_min_y, tire_y)
	if wheel_min_y == INF:
		wheel_min_y = hull_min_y
	var wp: Vector3 = global_position
	return {
		"rover_id": rover_id,
		"wheel_min_y": snappedf(wheel_min_y, 0.001),
		"hull_min_y": snappedf(hull_min_y, 0.001),
		"speed_mps": snappedf(speed_mps, 0.001),
		"wheel_spin_rad": snappedf(wheel_spin_rad, 0.001),
		"wheel_count": _wheels.size(),
		"wheel_radius_m": snappedf(wheel_radius_m, 0.001),
		"turret_yaw_deg": snappedf(turret_yaw_deg, 0.1),
		"has_turret": _turret != null,
		"status": status,
		"world_pos": [snappedf(wp.x, 0.001), snappedf(wp.y, 0.001), snappedf(wp.z, 0.001)],
	}
