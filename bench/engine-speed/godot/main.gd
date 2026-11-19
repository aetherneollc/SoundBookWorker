extends Node3D

var frames := 72
var fps := 24
var _method := "gl_compatibility"
var _mode := "readback"
var _frame := 0
var _measured := 0
var _warmup := 5
var _done := false
var _t0 := 0
var _width := 0
var _height := 0
var _pixel_mean := 0.0
var _pixel_coverage := 0.0
var _characters: Array[Node3D] = []
var _camera: Camera3D

const CLEAR := Color(0.55, 0.62, 0.70)


func _ready() -> void:
	_read_env()
	Engine.max_fps = 0
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	RenderingServer.directional_shadow_atlas_set_size(1024, true)
	_build()


func _read_env() -> void:
	var env_frames := OS.get_environment("BENCH_FRAMES")
	if env_frames.is_valid_int():
		frames = clampi(env_frames.to_int(), 1, 2000)
	var env_fps := OS.get_environment("BENCH_FPS")
	if env_fps.is_valid_int():
		fps = clampi(env_fps.to_int(), 1, 60)
	var env_method := OS.get_environment("BENCH_METHOD")
	if env_method != "":
		_method = env_method
	var env_mode := OS.get_environment("BENCH_MODE")
	if env_mode != "":
		_mode = env_mode


func _build() -> void:
	var world := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = CLEAR
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.45, 0.47, 0.50)
	env.ambient_light_energy = 0.55
	world.environment = env
	add_child(world)

	_camera = Camera3D.new()
	_camera.fov = 75.0
	_camera.position = Vector3(0, 1.5, 5.2)
	_camera.look_at(Vector3(0, 1.0, 0))
	_camera.current = true
	add_child(_camera)

	var light := DirectionalLight3D.new()
	light.rotation_degrees = Vector3(-48, 35, 0)
	light.light_energy = 1.3
	light.shadow_enabled = true
	light.directional_shadow_mode = DirectionalLight3D.SHADOW_PARALLEL_2_SPLITS
	light.directional_shadow_max_distance = 18.0
	add_child(light)

	var ground := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(24, 24)
	ground.mesh = plane
	ground.material_override = _mat(Color(0.32, 0.36, 0.30), 0.9)
	ground.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(ground)

	var box_mat := _mat(Color(0.45, 0.36, 0.28), 0.8)
	for i in 8:
		var box := MeshInstance3D.new()
		var mesh := BoxMesh.new()
		mesh.size = Vector3(0.6, 0.8 + float(i % 3) * 0.35, 0.6)
		box.mesh = mesh
		box.material_override = box_mat
		var ang := TAU * float(i) / 8.0
		box.position = Vector3(sin(ang) * 4.0, mesh.size.y * 0.5, cos(ang) * 4.0 - 1.0)
		add_child(box)

	_characters.append(_character(Vector3(-0.7, 0, 0), Color(0.86, 0.45, 0.38)))
	_characters.append(_character(Vector3(0.8, 0, 0.2), Color(0.35, 0.48, 0.78)))


func _mat(color: Color, rough: float) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.roughness = rough
	mat.metallic = 0.0
	return mat


func _character(pos: Vector3, tint: Color) -> Node3D:
	var root := Node3D.new()
	root.position = pos
	root.set_meta("z0", pos.z)
	add_child(root)
	var skin := _mat(tint, 0.72)
	var cloth := _mat(tint.darkened(0.25), 0.72)

	var body := MeshInstance3D.new()
	var body_mesh := CapsuleMesh.new()
	body_mesh.radius = 0.2
	body_mesh.height = 0.85
	body_mesh.radial_segments = 12
	body_mesh.rings = 6
	body.mesh = body_mesh
	body.position = Vector3(0, 1.05, 0)
	body.material_override = skin
	root.add_child(body)

	var head := MeshInstance3D.new()
	var head_mesh := SphereMesh.new()
	head_mesh.radius = 0.18
	head_mesh.height = 0.36
	head_mesh.radial_segments = 16
	head_mesh.rings = 8
	head.mesh = head_mesh
	head.position = Vector3(0, 1.62, 0)
	head.material_override = skin
	root.add_child(head)

	root.set_meta("arm_l", _limb(root, Vector3(-0.32, 1.35, 0), cloth))
	root.set_meta("arm_r", _limb(root, Vector3(0.32, 1.35, 0), cloth))
	root.set_meta("leg_l", _limb(root, Vector3(-0.10, 0.72, 0), cloth))
	root.set_meta("leg_r", _limb(root, Vector3(0.10, 0.72, 0), cloth))
	return root


func _limb(parent: Node3D, origin: Vector3, mat: Material) -> Node3D:
	var pivot := Node3D.new()
	pivot.position = origin
	parent.add_child(pivot)
	var mesh_node := MeshInstance3D.new()
	var mesh := CapsuleMesh.new()
	mesh.radius = 0.07
	mesh.height = 0.55
	mesh.radial_segments = 8
	mesh.rings = 4
	mesh_node.mesh = mesh
	mesh_node.position = Vector3(0, -0.28, 0)
	mesh_node.material_override = mat
	pivot.add_child(mesh_node)
	return pivot


func _process(_dt: float) -> void:
	if _done:
		return
	_frame += 1
	_animate()
	if _frame <= _warmup:
		return
	_measured += 1
	if _measured == 1:
		_t0 = Time.get_ticks_usec()
	var mid := maxi(int(frames / 2.0), 1)
	if _mode == "readback" or _measured == mid:
		var tex := get_viewport().get_texture()
		var img: Image = tex.get_image() if tex != null else null
		if img != null and _measured == mid:
			_width = img.get_width()
			_height = img.get_height()
			_pixel_mean = _mean(img)
			_pixel_coverage = _coverage(img)
	if _measured >= frames:
		_finish()


func _animate() -> void:
	var t := float(_frame) / float(fps)
	var swing := sin(t * TAU * 1.6)
	_camera.position.x = sin(t * 0.4) * 0.3
	_camera.look_at(Vector3(0, 1.0, 0))
	for ch in _characters:
		(ch.get_meta("arm_l") as Node3D).rotation.x = swing * 0.8
		(ch.get_meta("arm_r") as Node3D).rotation.x = -swing * 0.8
		(ch.get_meta("leg_l") as Node3D).rotation.x = -swing * 0.6
		(ch.get_meta("leg_r") as Node3D).rotation.x = swing * 0.6
		ch.position.z = float(ch.get_meta("z0")) + sin(t * 0.7) * 0.15


func _mean(img: Image) -> float:
	var acc := 0.0
	var n := 0
	var x0 := int(img.get_width() * 0.25)
	var x1 := int(img.get_width() * 0.75)
	var y0 := int(img.get_height() * 0.35)
	var y1 := int(img.get_height() * 0.95)
	for y in range(y0, y1, 8):
		for x in range(x0, x1, 8):
			var c := img.get_pixel(x, y)
			acc += c.r + c.g + c.b
			n += 3
	if n == 0:
		return 0.0
	return acc / float(n)


func _coverage(img: Image) -> float:
	var hit := 0
	var n := 0
	var x0 := int(img.get_width() * 0.25)
	var x1 := int(img.get_width() * 0.75)
	var y0 := int(img.get_height() * 0.35)
	var y1 := int(img.get_height() * 0.95)
	for y in range(y0, y1, 8):
		for x in range(x0, x1, 8):
			var c := img.get_pixel(x, y)
			var d := absf(c.r - CLEAR.r) + absf(c.g - CLEAR.g) + absf(c.b - CLEAR.b)
			if d > 0.18:
				hit += 1
			n += 1
	if n == 0:
		return 0.0
	return float(hit) / float(n)


func _finish() -> void:
	if _done:
		return
	_done = true
	var loop_s := (Time.get_ticks_usec() - _t0) / 1000000.0
	if loop_s <= 0.0:
		loop_s = 0.000001
	var done := maxi(_measured, 1)
	var payload := {
		"engine": "godot",
		"method": _method,
		"mode": _mode,
		"frames": done,
		"fps_setting": fps,
		"width": _width,
		"height": _height,
		"loop_s": loop_s,
		"ms_per_frame": loop_s * 1000.0 / float(done),
		"loop_fps": float(done) / loop_s,
		"adapter": RenderingServer.get_video_adapter_name(),
		"pixel_mean": _pixel_mean,
		"pixel_coverage": _pixel_coverage,
		"pixel_ok": _pixel_coverage >= 0.08 and _width >= 1200 and _height >= 680,
		"scene": "1280x720, 2 capsule characters, 8 boxes, one 1024 directional shadow",
	}
	var text := JSON.stringify(payload)
	print("BENCH_JSON " + text)
	var result_path := OS.get_environment("BENCH_RESULT")
	if result_path != "":
		var handle := FileAccess.open(result_path, FileAccess.WRITE)
		if handle != null:
			handle.store_string(text)
			handle.close()
	get_tree().quit(0)
