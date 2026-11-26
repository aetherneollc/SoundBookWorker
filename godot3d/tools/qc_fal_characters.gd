extends SceneTree

## Contact sheets for the fal character GLBs.
##   godot_console.exe --path godot3d --script res://tools/qc_fal_characters.gd -- \
##     <linlan.glb> <chen.glb> <heads_out.png> <body_out.png>

const HEAD_PANEL := Vector2i(384, 420)
const BODY_PANEL := Vector2i(420, 640)

var _paths: Array[String] = []
var _heads_out := ""
var _body_out := ""
var _vp: SubViewport
var _root: Node3D
var _cam: Camera3D
var _view_light: DirectionalLight3D
var _models: Array[Node3D] = []
var _boxes: Array[AABB] = []
var _tasks: Array[Dictionary] = []
var _idx := 0
var _wait := 0
var _heads_sheet: Image
var _body_sheet: Image


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 4:
		push_error("usage: -- <linlan.glb> <chen.glb> <heads_out.png> <body_out.png>")
		quit(2)
		return
	_paths = [args[0], args[1]]
	_heads_out = args[2]
	_body_out = args[3]

	_vp = SubViewport.new()
	_vp.size = BODY_PANEL
	_vp.transparent_bg = false
	_vp.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_vp.own_world_3d = true
	_vp.msaa_3d = Viewport.MSAA_4X
	root.add_child(_vp)

	_root = Node3D.new()
	_vp.add_child(_root)
	_build_studio()

	_cam = Camera3D.new()
	_cam.fov = 28.0
	_cam.near = 0.01
	_cam.far = 200.0
	_vp.add_child(_cam)
	_cam.current = true

	for i in _paths.size():
		var scene := _load_glb(_paths[i])
		if scene == null:
			push_error("Failed to load %s" % _paths[i])
			quit(1)
			return
		scene.visible = false
		_root.add_child(scene)
		_models.append(scene)
	_heads_sheet = Image.create(HEAD_PANEL.x * 3, HEAD_PANEL.y * 2, false, Image.FORMAT_RGBA8)
	_body_sheet = Image.create(BODY_PANEL.x * 3, BODY_PANEL.y * 2, false, Image.FORMAT_RGBA8)
	var head_angles := [0.0, 38.0, 78.0]
	var body_angles := [0.0, 78.0, 180.0]
	for row in 2:
		for col in 3:
			_tasks.append({
				"sheet": "heads", "row": row, "col": col, "model": row, "angle": head_angles[col],
			})
	for row in 2:
		for col in 3:
			_tasks.append({
				"sheet": "body", "row": row, "col": col, "model": row, "angle": body_angles[col],
			})
	Engine.max_fps = 20
	_wait = 3


func _process(_delta: float) -> bool:
	if _boxes.is_empty():
		if not _models[0].is_inside_tree():
			return false
		for i in _models.size():
			_stand_up(_models[i])
			var box := _world_aabb(_models[i])
			_boxes.append(box)
			print("LOADED %s aabb_pos=%s aabb_size=%s bones=%s" % [
				_paths[i], box.position, box.size, _bone_names(_models[i]),
			])
		_apply_task(_tasks[0])
		_wait = 3
		return false
	if _wait > 0:
		_wait -= 1
		return false
	var img := _vp.get_texture().get_image()
	if img == null or img.get_width() < 8:
		_wait = 2
		return false
	img.convert(Image.FORMAT_RGBA8)
	var task: Dictionary = _tasks[_idx]
	if str(task.sheet) == "heads":
		_heads_sheet.blit_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), Vector2i(int(task.col) * HEAD_PANEL.x, int(task.row) * HEAD_PANEL.y))
	else:
		_body_sheet.blit_rect(img, Rect2i(Vector2i.ZERO, img.get_size()), Vector2i(int(task.col) * BODY_PANEL.x, int(task.row) * BODY_PANEL.y))
	_idx += 1
	if _idx >= _tasks.size():
		_heads_sheet.save_png(_heads_out)
		_body_sheet.save_png(_body_out)
		print("SAVED %s" % _heads_out)
		print("SAVED %s" % _body_out)
		quit(0)
		return true
	_apply_task(_tasks[_idx])
	_wait = 3
	return false


func _apply_task(task: Dictionary) -> void:
	var heads := str(task.sheet) == "heads"
	var target_size := HEAD_PANEL if heads else BODY_PANEL
	if _vp.size != target_size:
		_vp.size = target_size
	var m_idx := int(task.model)
	for i in _models.size():
		_models[i].visible = (i == m_idx)
	var box: AABB = _boxes[m_idx]
	var focus := box.get_center()
	var dist := maxf(box.size.length(), 0.2)
	if heads:
		focus = Vector3(box.get_center().x, box.position.y + box.size.y * 0.86, box.get_center().z)
		dist = maxf(box.size.y * 0.55, 0.15)
	var ang := deg_to_rad(float(task.angle))
	var offset := Vector3(sin(ang), 0.04 if heads else 0.02, cos(ang)).normalized() * (dist * (1.15 if heads else 2.25))
	_cam.look_at_from_position(focus + offset, focus, Vector3.UP)
	_view_light.look_at_from_position(focus + offset, focus, Vector3.UP)


func _stand_up(model: Node3D) -> void:
	var box := _world_aabb(model)
	if box.size.y + 0.001 >= box.size.z and box.size.y + 0.001 >= box.size.x:
		return
	if box.size.z > box.size.y and box.size.z >= box.size.x:
		model.rotate_x(deg_to_rad(-90.0))
		print("ROTATED_X %s" % model.name)


func _world_aabb(model: Node3D) -> AABB:
	var box := AABB()
	var first := true
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is MeshInstance3D and (n as MeshInstance3D).mesh != null:
			var mi := n as MeshInstance3D
			var local: AABB = mi.mesh.get_aabb()
			var xf := mi.global_transform
			for c in 8:
				var corner := Vector3(
					local.position.x if (c & 1) == 0 else local.end.x,
					local.position.y if (c & 2) == 0 else local.end.y,
					local.position.z if (c & 4) == 0 else local.end.z
				)
				var point: Vector3 = xf * corner
				if first:
					box = AABB(point, Vector3.ZERO)
					first = false
				else:
					box = box.expand(point)
		for child in n.get_children():
			stack.append(child)
	if first:
		return AABB(Vector3.ZERO, Vector3(0.4, 1.7, 0.4))
	return box


func _bone_names(model: Node) -> PackedStringArray:
	var names := PackedStringArray()
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is Skeleton3D:
			var skel := n as Skeleton3D
			for i in skel.get_bone_count():
				names.append(skel.get_bone_name(i))
		for child in n.get_children():
			stack.append(child)
	return names


func _load_glb(path: String) -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err := doc.append_from_file(path, state)
	if err != OK:
		push_error("gltf %s err %s" % [path, err])
		return null
	var scene := doc.generate_scene(state)
	if scene is Node3D:
		return scene as Node3D
	var wrap := Node3D.new()
	wrap.add_child(scene)
	return wrap


func _build_studio() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.74, 0.78, 0.84)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.95, 0.96, 1.0)
	env.ambient_light_energy = 0.55
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var we := WorldEnvironment.new()
	we.environment = env
	_root.add_child(we)

	var key := DirectionalLight3D.new()
	key.rotation = Vector3(deg_to_rad(-48.0), deg_to_rad(28.0), 0.0)
	key.light_energy = 1.3
	_root.add_child(key)

	var fill := DirectionalLight3D.new()
	fill.rotation = Vector3(deg_to_rad(-20.0), deg_to_rad(-50.0), 0.0)
	fill.light_color = Color(0.86, 0.92, 1.0)
	fill.light_energy = 0.55
	_root.add_child(fill)

	_view_light = DirectionalLight3D.new()
	_view_light.light_energy = 1.15
	_view_light.shadow_enabled = false
	_root.add_child(_view_light)
