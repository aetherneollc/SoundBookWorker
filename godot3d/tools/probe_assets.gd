extends SceneTree

## Loads every CC0 model with GLTFDocument the same way the player does and
## prints one line per file. Any engine error during a load shows up on stderr.
##   godot --headless --path godot3d --script res://tools/probe_assets.gd -- <cc0 dir>


func _init() -> void:
	var args: PackedStringArray = OS.get_cmdline_user_args()
	var root_dir: String = args[0] if args.size() > 0 else ProjectSettings.globalize_path("res://").get_base_dir().path_join("media/assets3d/cc0")
	var files: Array[String] = []
	_collect(root_dir, files)
	files.sort()
	var ok := 0
	var failed := 0
	for path: String in files:
		var line: String = _probe(path)
		if line.begins_with("FAIL"):
			failed += 1
		else:
			ok += 1
		print(line)
	print("PROBE_SUMMARY ok=%d failed=%d" % [ok, failed])
	quit(0 if failed == 0 else 1)


func _collect(dir_path: String, out: Array[String]) -> void:
	var dir := DirAccess.open(dir_path)
	if dir == null:
		return
	for name: String in dir.get_directories():
		if name.begins_with("_"):
			continue
		_collect(dir_path.path_join(name), out)
	for name: String in dir.get_files():
		var ext: String = name.get_extension().to_lower()
		if ext == "glb" or ext == "gltf":
			out.append(dir_path.path_join(name))


func _probe(path: String) -> String:
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err: Error = doc.append_from_file(path, state)
	if err != OK:
		return "FAIL %s append_from_file %s" % [path.get_file(), error_string(err)]
	var scene: Node = doc.generate_scene(state)
	if scene == null:
		return "FAIL %s generate_scene returned null" % path.get_file()
	var meshes := 0
	var skeleton_bones := 0
	var anims := 0
	var aabb := AABB()
	var first := true
	var stack: Array[Node] = [scene]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if node is MeshInstance3D:
			meshes += 1
			var mi := node as MeshInstance3D
			if mi.mesh != null:
				var box: AABB = _global_xform(mi) * mi.mesh.get_aabb()
				aabb = box if first else aabb.merge(box)
				first = false
		elif node is Skeleton3D:
			skeleton_bones = maxi(skeleton_bones, (node as Skeleton3D).get_bone_count())
		elif node is AnimationPlayer:
			anims += (node as AnimationPlayer).get_animation_list().size()
	var info := "OK %s meshes=%d bones=%d anims=%d size=(%.2f, %.2f, %.2f) min_y=%.3f" % [
		path.get_file(), meshes, skeleton_bones, anims,
		aabb.size.x, aabb.size.y, aabb.size.z, aabb.position.y,
	]
	if path.get_file().begins_with("UAL1_Standard_RM"):
		info += _root_motion_report(scene)
	scene.free()
	return info


func _global_xform(node: Node3D) -> Transform3D:
	var xform := node.transform
	var parent := node.get_parent()
	while parent is Node3D:
		xform = (parent as Node3D).transform * xform
		parent = parent.get_parent()
	return xform


func _root_motion_report(scene: Node) -> String:
	var player: AnimationPlayer = scene.find_child("AnimationPlayer", true, false) as AnimationPlayer
	if player == null:
		return ""
	var parts: Array[String] = []
	for clip: String in ["Walk_Loop", "Walk_Formal_Loop", "Jog_Fwd_Loop"]:
		if not player.has_animation(clip):
			continue
		var anim: Animation = player.get_animation(clip)
		for t in anim.get_track_count():
			var path_text: String = str(anim.track_get_path(t))
			if anim.track_get_type(t) != Animation.TYPE_POSITION_3D:
				continue
			if not (path_text.ends_with(":root") or path_text.ends_with(":pelvis")):
				continue
			var a: Vector3 = anim.position_track_interpolate(t, 0.0)
			var b: Vector3 = anim.position_track_interpolate(t, anim.length)
			parts.append("%s[%s] len=%.3f d=(%.3f, %.3f, %.3f)" % [clip, path_text.get_slice(":", 1), anim.length, b.x - a.x, b.y - a.y, b.z - a.z])
	return " RM: " + "; ".join(parts)
