extends SceneTree

## Renders QC sheets for one model, four panels side by side.
##
##   godot --path godot3d --script res://tools/preview_model.gd -- turnaround model.glb out.png
##       Cameras on +Z, +X, -Z, -X looking at the model. A model whose front
##       faces +Z shows its face in the first panel.
##   godot --path godot3d --script res://tools/preview_model.gd -- clips config.json out.png
##       Loads an actor through player/character3d.gd (same retarget path as
##       the stage) and poses it: idle, walk at 25% and 75%, reach. Front and
##       side rows.
##
## Prints the model's size, triangle count, bones and animations.

const PANEL := Vector2i(480, 640)

var _mode := ""
var _source := ""
var _out := ""
var _viewport: SubViewport
var _camera: Camera3D
var _root: Node3D
var _actor: Node3D
var _views: Array[Dictionary] = []
var _index := 0
var _waiting := 0
var _sheet: Image
var _center := Vector3.ZERO
var _radius := 1.0


func _initialize() -> void:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	if args.size() < 3:
		push_error("usage: -- turnaround|clips <model.glb|config.json> <out.png>")
		quit(2)
		return
	_mode = args[0]
	_source = args[1]
	_out = args[2]
	_viewport = SubViewport.new()
	_viewport.size = PANEL
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_viewport.own_world_3d = true
	root.add_child(_viewport)
	_root = Node3D.new()
	_viewport.add_child(_root)
	_build_lights()
	_camera = Camera3D.new()
	_camera.fov = 30.0
	_camera.near = 0.01
	_camera.far = 100.0
	_viewport.add_child(_camera)
	_camera.current = true
	if _mode == "clips":
		if not _setup_actor():
			quit(1)
			return
	elif not _setup_model():
		quit(1)
		return
	var rows: int = 2 if _mode == "clips" else 1
	var cols: int = 4
	_sheet = Image.create(PANEL.x * cols, PANEL.y * rows, false, Image.FORMAT_RGBA8)
	_waiting = 3


func _process(_delta: float) -> bool:
	if _index >= _views.size():
		return false
	if _waiting == 3:
		_apply_view(_views[_index])
	_waiting -= 1
	if _waiting > 0:
		return false
	var image: Image = _viewport.get_texture().get_image()
	image.convert(Image.FORMAT_RGBA8)
	var view: Dictionary = _views[_index]
	_sheet.blit_rect(image, Rect2i(Vector2i.ZERO, PANEL), Vector2i(int(view.col) * PANEL.x, int(view.row) * PANEL.y))
	_index += 1
	_waiting = 3
	if _index >= _views.size():
		_sheet.save_png(_out)
		print("PREVIEW %s" % _out)
		if _actor != null:
			_actor.queue_free()
		quit(0)
	return false


func _build_lights() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color(0.82, 0.85, 0.9)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(1, 1, 1)
	env.ambient_light_energy = 0.55
	var world := WorldEnvironment.new()
	world.environment = env
	_root.add_child(world)
	var key := DirectionalLight3D.new()
	key.transform.basis = Basis.looking_at(Vector3(-0.4, -0.6, -0.7).normalized(), Vector3.UP)
	key.light_energy = 1.1
	_root.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.transform.basis = Basis.looking_at(Vector3(0.6, -0.3, 0.7).normalized(), Vector3.UP)
	fill.light_energy = 0.5
	_root.add_child(fill)
	var floor_mesh := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(6, 6)
	floor_mesh.mesh = plane
	var floor_mat := StandardMaterial3D.new()
	floor_mat.albedo_color = Color(0.6, 0.63, 0.68)
	floor_mesh.material_override = floor_mat
	_root.add_child(floor_mesh)
	var axis := MeshInstance3D.new()
	var axis_mesh := BoxMesh.new()
	axis_mesh.size = Vector3(0.02, 0.005, 0.5)
	axis.mesh = axis_mesh
	axis.position = Vector3(0, 0.003, 0.25)
	var axis_mat := StandardMaterial3D.new()
	axis_mat.albedo_color = Color(0.1, 0.4, 1.0)
	axis.material_override = axis_mat
	_root.add_child(axis)


func _setup_model() -> bool:
	var character: Script = load("res://player/character3d.gd")
	var model: Node3D = character.call("load_gltf_scene", _source)
	if model == null:
		return false
	character.call("matte_materials", model)
	_root.add_child(model)
	var box := _bounds(model)
	var tris := 0
	var bones := PackedStringArray()
	var anims := PackedStringArray()
	for node: Node in _descendants(model):
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var mesh: Mesh = (node as MeshInstance3D).mesh
			for s in mesh.get_surface_count():
				var arrays: Array = mesh.surface_get_arrays(s)
				var idx: Variant = arrays[Mesh.ARRAY_INDEX]
				tris += (idx as PackedInt32Array).size() / 3 if idx != null else (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size() / 3
		elif node is Skeleton3D:
			var skel := node as Skeleton3D
			for i in skel.get_bone_count():
				bones.append(skel.get_bone_name(i))
		elif node is AnimationPlayer:
			for anim_name: StringName in (node as AnimationPlayer).get_animation_list():
				anims.append(str(anim_name))
	print("MODEL size=(%.3f, %.3f, %.3f) min=(%.3f, %.3f, %.3f) tris=%d bones=%d anims=%s" % [
		box.size.x, box.size.y, box.size.z, box.position.x, box.position.y, box.position.z, tris, bones.size(), anims])
	if not bones.is_empty():
		print("BONES %s" % ", ".join(bones))
	_center = box.get_center()
	_radius = maxf(box.size.length() * 0.5, 0.05)
	if OS.get_cmdline_user_args().size() > 3 and OS.get_cmdline_user_args()[3] == "head":
		_center = Vector3(box.get_center().x, box.end.y - box.size.y * 0.09, box.get_center().z)
		_radius = box.size.y * 0.11
	for i in 4:
		_views.append({"row": 0, "col": i, "angle": float(i) * 90.0})
	return true


func _setup_actor() -> bool:
	var config: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(_source))
	var character: Script = load("res://player/character3d.gd")
	_actor = character.new() as Node3D
	_root.add_child(_actor)
	if not bool(_actor.call("setup", config)):
		return false
	print("CLIPS %s walk_speed=%.3f source=%s" % [_actor.get("clip_report"), float(_actor.get("natural_walk_speed")), _actor.get("source_kind")])
	var height: float = float(_actor.get("height_m"))
	_center = Vector3(0, height * 0.5, 0)
	_radius = height * 0.62
	var poses := [["idle", 0.6, 0.0], ["walk", 0.33, 0.0], ["walk", 1.0, 0.0], ["idle", 1.0, 1.0]]
	for row in 2:
		for col in 4:
			var pose: Array = poses[col]
			_views.append({"row": row, "col": col, "angle": 0.0 if row == 0 else 90.0, "clip": pose[0], "time": pose[1], "reach": pose[2]})
	return true


func _apply_view(view: Dictionary) -> void:
	if _actor != null and view.has("clip"):
		_actor.call("update_animation", str(view.clip), "qc_%d" % _index, 1.0, 0.0, 0.0)
		_actor.call("update_animation", str(view.clip), "qc_%d" % _index, 1.0, float(view.time), 0.0)
		var target := Vector3(0.0, 1.05, 0.55)
		_actor.set("holding", float(view.reach) > 0.5)
		_actor.call("apply_overlays", 0.0, 0.0, target, float(view.reach))
	var angle: float = deg_to_rad(float(view.angle))
	var distance: float = _radius / tan(deg_to_rad(_camera.fov * 0.5)) * 1.1
	var offset := Vector3(sin(angle), 0.15, cos(angle)).normalized() * distance
	_camera.look_at_from_position(_center + offset, _center, Vector3.UP)


func _descendants(node: Node) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [node]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		out.append(n)
		for c: Node in n.get_children():
			stack.append(c)
	return out


func _bounds(model: Node3D) -> AABB:
	var result := AABB()
	var first := true
	for node: Node in _descendants(model):
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var mi := node as MeshInstance3D
			var xform := Transform3D.IDENTITY
			var current: Node = mi
			while current != null and current != _root:
				if current is Node3D:
					xform = (current as Node3D).transform * xform
				current = current.get_parent()
			var box: AABB = xform * mi.mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result
