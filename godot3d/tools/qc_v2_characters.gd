extends SceneTree

## Renders two QC sheets for the v2 Quaternius+BlendShape characters:
##   1) v2_heads_blendshapes.png: 2 rows (linlan, chen) x 5 expressions
##      (neutral, jawOpen, eyeBlink, mouthSmile+browUp, mouthFrown+browDown)
##   2) v2_body_hands.png: 2 rows (linlan, chen) x 4 panels
##      (front, 3/4 side, back, 5-finger left hand close-up)
##
## Usage:
##   godot_console.exe --path godot3d --script res://tools/qc_v2_characters.gd -- \
##     <linlan.glb> <chen.glb> <heads_out.png> <body_hands_out.png>

const HEAD_PANEL := Vector2i(384, 420)
const BODY_PANEL := Vector2i(420, 560)

var _paths: Array[String] = []
var _heads_out := ""
var _body_out := ""

var _vp: SubViewport
var _root: Node3D
var _cam: Camera3D
var _models: Array[Node3D] = []
var _model_heights: Array[float] = []
var _tasks: Array[Dictionary] = []
var _idx := 0
var _wait := 0
var _heads_sheet: Image
var _body_sheet: Image


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 4:
		push_error("usage: -- <linlan.glb> <chen.glb> <heads_out.png> <body_hands_out.png>")
		quit(2)
		return
	_paths = [args[0], args[1]]
	_heads_out = args[2]
	_body_out = args[3]

	_vp = SubViewport.new()
	_vp.size = HEAD_PANEL
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
	_cam.far = 50.0
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
		var h := _measure_height(scene)
		_model_heights.append(h)
		print("LOADED %s height=%.4f blend_shapes=%s" % [_paths[i], h, _list_blend_shapes(scene)])

	_heads_sheet = Image.create(HEAD_PANEL.x * 5, HEAD_PANEL.y * 2, false, Image.FORMAT_RGBA8)
	_body_sheet = Image.create(BODY_PANEL.x * 4, BODY_PANEL.y * 2, false, Image.FORMAT_RGBA8)

	var exprs: Array[Dictionary] = [
		{},
		{"jawOpen": 1.0},
		{"eyeBlink": 1.0},
		{"mouthSmile": 1.0, "browUp": 0.85},
		{"mouthFrown": 1.0, "browDown": 1.0},
	]
	for row in 2:
		for col in exprs.size():
			_tasks.append({
				"sheet": "heads",
				"row": row,
				"col": col,
				"model": row,
				"pose": "a_pose",
				"expr": exprs[col],
				"cam": "head",
				"angle": 12.0 if col == 0 else (18.0 if col == 1 else 8.0),
			})

	var body_angles := [0.0, 62.0, 180.0, -1.0]
	for row in 2:
		for col in 4:
			_tasks.append({
				"sheet": "body",
				"row": row,
				"col": col,
				"model": row,
				"pose": "t_pose" if col == 3 else "a_pose",
				"expr": {},
				"cam": "hand" if col == 3 else "body",
				"angle": body_angles[col],
			})
	_wait = 4


func _process(_delta: float) -> bool:
	if _idx >= _tasks.size():
		return false
	if _wait == 4:
		_apply_task(_tasks[_idx])
	_wait -= 1
	if _wait > 0:
		return false
	var img := _vp.get_texture().get_image()
	img.convert(Image.FORMAT_RGBA8)
	var task := _tasks[_idx]
	if task.sheet == "heads":
		_heads_sheet.blit_rect(img, Rect2i(Vector2i.ZERO, HEAD_PANEL), Vector2i(int(task.col) * HEAD_PANEL.x, int(task.row) * HEAD_PANEL.y))
	else:
		_body_sheet.blit_rect(img, Rect2i(Vector2i.ZERO, BODY_PANEL), Vector2i(int(task.col) * BODY_PANEL.x, int(task.row) * BODY_PANEL.y))
	_idx += 1
	_wait = 4
	if _idx >= _tasks.size():
		_heads_sheet.save_png(_heads_out)
		_body_sheet.save_png(_body_out)
		print("SAVED %s" % _heads_out)
		print("SAVED %s" % _body_out)
		quit(0)
	return false


func _apply_task(task: Dictionary) -> void:
	var target_size := HEAD_PANEL if task.sheet == "heads" else BODY_PANEL
	if _vp.size != target_size:
		_vp.size = target_size
	var m_idx := int(task.model)
	for i in _models.size():
		_models[i].visible = (i == m_idx)
	var model := _models[m_idx]
	_pose_skeleton(model, str(task.pose))
	_set_blend_shapes(model, task.expr as Dictionary)

	var h := _model_heights[m_idx]
	var cam_mode := str(task.cam)
	if cam_mode == "head":
		var head_pos := _bone_world_pos(model, "Head")
		var center := Vector3(head_pos.x, head_pos.y + h * 0.052, head_pos.z + 0.025)
		var ang := deg_to_rad(float(task.angle))
		var dist := 0.56
		var offset := Vector3(sin(ang), 0.04, cos(ang)).normalized() * dist
		_cam.look_at_from_position(center + offset, center, Vector3.UP)
	elif cam_mode == "body":
		var center := Vector3(0.0, h * 0.51, 0.0)
		var ang := deg_to_rad(float(task.angle))
		var dist := h * 2.35
		var offset := Vector3(sin(ang), 0.07, cos(ang)).normalized() * dist
		_cam.look_at_from_position(center + offset, center, Vector3.UP)
	else:
		# Close-up of the 5-finger left hand in T-pose (full wrist + all 5 fingers)
		var hand_p := _bone_world_pos(model, "hand_l")
		var mid_p := _bone_world_pos(model, "middle_02_l")
		var center := hand_p.lerp(mid_p, 0.55)
		var offset := Vector3(0.03, 0.28, 0.34).normalized() * 0.44
		_cam.look_at_from_position(center + offset, center, Vector3.UP)


func _pose_skeleton(model: Node3D, mode: String) -> void:
	var skel := _find_skeleton(model)
	if skel == null:
		return
	skel.reset_bone_poses()
	if mode == "a_pose":
		# Relax T-pose arms ~62 degrees downward so outfits read naturally
		var ul := skel.find_bone("upperarm_l")
		var ur := skel.find_bone("upperarm_r")
		if ul >= 0:
			_rotate_bone_world(skel, ul, Vector3(0, 0, -1), deg_to_rad(62.0))
		if ur >= 0:
			_rotate_bone_world(skel, ur, Vector3(0, 0, 1), deg_to_rad(62.0))
		var ll := skel.find_bone("lowerarm_l")
		var lr := skel.find_bone("lowerarm_r")
		if ll >= 0:
			_rotate_bone_world(skel, ll, Vector3(1, 0, 0), deg_to_rad(-10.0))
		if lr >= 0:
			_rotate_bone_world(skel, lr, Vector3(1, 0, 0), deg_to_rad(-10.0))


func _rotate_bone_world(skel: Skeleton3D, bone: int, world_axis: Vector3, angle: float) -> void:
	var parent := skel.get_bone_parent(bone)
	var parent_basis := Basis.IDENTITY if parent < 0 else skel.get_bone_global_pose(parent).basis.orthonormalized()
	var rest_rot := skel.get_bone_rest(bone).basis.get_rotation_quaternion()
	var world_rot := parent_basis.get_rotation_quaternion() * rest_rot
	var new_world := Quaternion(world_axis.normalized(), angle) * world_rot
	var new_local := (parent_basis.get_rotation_quaternion().inverse() * new_world).normalized()
	skel.set_bone_pose_rotation(bone, new_local)


func _set_blend_shapes(model: Node3D, values: Dictionary) -> void:
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is MeshInstance3D:
			var mi := n as MeshInstance3D
			var cnt := mi.get_blend_shape_count()
			for i in cnt:
				var bname := str(mi.mesh.get_blend_shape_name(i))
				mi.set_blend_shape_value(i, float(values.get(bname, 0.0)))
		for c: Node in n.get_children():
			stack.append(c)


func _list_blend_shapes(model: Node3D) -> PackedStringArray:
	var out := PackedStringArray()
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is MeshInstance3D:
			var mi := n as MeshInstance3D
			if mi.get_blend_shape_count() > 0:
				var names := PackedStringArray()
				for i in mi.get_blend_shape_count():
					names.append(str(mi.mesh.get_blend_shape_name(i)))
				out.append("%s:[%s]" % [mi.name, ",".join(names)])
		for c: Node in n.get_children():
			stack.append(c)
	return out


func _find_skeleton(model: Node) -> Skeleton3D:
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is Skeleton3D:
			return n as Skeleton3D
		for c: Node in n.get_children():
			stack.append(c)
	return null


func _bone_world_pos(model: Node3D, bone_name: String) -> Vector3:
	var skel := _find_skeleton(model)
	if skel == null:
		return Vector3(0, 1.5, 0)
	var b := skel.find_bone(bone_name)
	if b < 0:
		return Vector3(0, 1.5, 0)
	return skel.global_transform * skel.get_bone_global_pose(b).origin


func _measure_height(model: Node3D) -> float:
	var lo := INF
	var hi := -INF
	var stack: Array[Node] = [model]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is MeshInstance3D and (n as MeshInstance3D).mesh != null:
			var box := (n as MeshInstance3D).mesh.get_aabb()
			lo = minf(lo, box.position.y)
			hi = maxf(hi, box.end.y)
		for c: Node in n.get_children():
			stack.append(c)
	return maxf(hi - lo, 0.5)


func _load_glb(path: String) -> Node3D:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	if doc.append_from_file(path, state) != OK:
		return null
	var scene := doc.generate_scene(state)
	if scene is Node3D:
		return scene as Node3D
	var w := Node3D.new()
	w.add_child(scene)
	return w


func _build_studio() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.74, 0.78, 0.84)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.95, 0.96, 1.0)
	env.ambient_light_energy = 0.52
	env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	var we := WorldEnvironment.new()
	we.environment = env
	_root.add_child(we)

	var key := DirectionalLight3D.new()
	key.transform.basis = Basis.looking_at(Vector3(-0.35, -0.45, -0.82).normalized(), Vector3.UP)
	key.light_color = Color(1.0, 0.97, 0.93)
	key.light_energy = 1.25
	_root.add_child(key)

	var fill := DirectionalLight3D.new()
	fill.transform.basis = Basis.looking_at(Vector3(0.65, -0.20, -0.72).normalized(), Vector3.UP)
	fill.light_color = Color(0.86, 0.92, 1.0)
	fill.light_energy = 0.55
	_root.add_child(fill)

	var rim := DirectionalLight3D.new()
	rim.transform.basis = Basis.looking_at(Vector3(0.15, -0.35, 0.92).normalized(), Vector3.UP)
	rim.light_color = Color(0.92, 0.95, 1.0)
	rim.light_energy = 0.45
	_root.add_child(rim)

	var floor_mi := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(6.0, 6.0)
	floor_mi.mesh = plane
	var fmat := StandardMaterial3D.new()
	fmat.albedo_color = Color(0.55, 0.59, 0.65)
	fmat.roughness = 0.9
	floor_mi.material_override = fmat
	_root.add_child(floor_mi)
