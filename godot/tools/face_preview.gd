extends SceneTree

## Renders face close-up contact sheets for one or more rigs.
##
##   godot_console --path godot --rendering-method gl_compatibility --rendering-driver opengl3 \
##     --audio-driver Dummy -s res://tools/face_preview.gd
##
## Environment:
##   FACE_PREVIEW_RIGS  "name=path/to/rig.json;name2=..."  (required)
##   FACE_PREVIEW_OUT   output directory                   (required)
##   FACE_PREVIEW_CELL  cell size in pixels, default 264
##   FACE_PREVIEW_FACE  another face script to draw with, for before/after sheets
##
## Per rig it writes <name>_grid.png (emotions down, visemes across) and
## <name>_extras.png (yaw sweep, the frames of an X -> D mouth tween, a blink).

const PuppetScript := preload("res://player/puppet.gd")
const MotionScript := preload("res://player/motion.gd")

const VISEMES: Array[String] = ["X", "A", "B", "C", "D", "E", "F"]
const EMOTIONS: Array[String] = ["neutral", "happy", "angry", "sad", "surprised"]
const ACTOR_HEIGHT := 500.0
const BACKDROP := Color(0.74, 0.80, 0.86, 1.0)

var _cell := 264
var _motion: MotionScript
var _face_override: GDScript


func _initialize() -> void:
	_run.call_deferred()


func _run() -> void:
	var out_dir := OS.get_environment("FACE_PREVIEW_OUT").strip_edges().replace("\\", "/")
	var rigs := _parse_rigs(OS.get_environment("FACE_PREVIEW_RIGS"))
	if out_dir == "" or rigs.is_empty():
		push_error("face_preview: FACE_PREVIEW_RIGS and FACE_PREVIEW_OUT are required")
		quit(2)
		return
	if not DirAccess.dir_exists_absolute(out_dir) and DirAccess.make_dir_recursive_absolute(out_dir) != OK:
		push_error("face_preview: cannot create %s" % out_dir)
		quit(2)
		return
	var cell_env := OS.get_environment("FACE_PREVIEW_CELL").strip_edges()
	if cell_env.is_valid_int():
		_cell = clampi(cell_env.to_int(), 96, 640)
	var override_path := OS.get_environment("FACE_PREVIEW_FACE").strip_edges().replace("\\", "/")
	if override_path != "":
		_face_override = load(override_path) as GDScript
		if _face_override == null:
			push_error("face_preview: cannot load %s" % override_path)
			quit(2)
			return
	_motion = MotionScript.new()

	var failed := false
	for entry: Array in rigs:
		var actor: String = entry[0]
		var rig_path: String = entry[1]
		var rig := _read_json(rig_path)
		if rig.is_empty():
			push_error("face_preview: cannot read %s" % rig_path)
			failed = true
			continue
		var grid: Array[Dictionary] = []
		for emotion in EMOTIONS:
			for viseme in VISEMES:
				grid.append({"viseme": viseme, "emotion": emotion, "label": "%s  %s" % [viseme, emotion]})
		if not await _render_sheet(rig, rig_path, grid, VISEMES.size(), out_dir.path_join(actor + "_grid.png")):
			failed = true
		if _face_override == null:
			var extras: Array[Dictionary] = []
			for yaw: float in [-1.0, -0.5, 0.5, 1.0]:
				extras.append({
					"viseme": "C", "emotion": "happy", "yaw": yaw, "label": "C happy  yaw %+.1f" % yaw,
				})
			for step in [1, 2, 3]:
				extras.append({
					"viseme": "D", "emotion": "neutral", "tween": step, "label": "X>D  frame %d" % step,
				})
			for blink: float in [0.5, 1.0]:
				extras.append({
					"viseme": "B", "emotion": "angry", "blink": blink, "label": "B angry  blink %.1f" % blink,
				})
			if not await _render_sheet(rig, rig_path, extras, extras.size(), out_dir.path_join(actor + "_extras.png")):
				failed = true
	quit(1 if failed else 0)


func _render_sheet(
	rig: Dictionary,
	rig_path: String,
	cells: Array[Dictionary],
	columns: int,
	out_path: String,
) -> bool:
	var views: Array[SubViewport] = []
	for cell: Dictionary in cells:
		var view := _build_cell(rig, rig_path, cell)
		if view == null:
			for made in views:
				made.queue_free()
			return false
		views.append(view)
	# A few frames so every viewport has drawn its final state.
	for _i in 4:
		await process_frame
	@warning_ignore("integer_division")
	var rows := (cells.size() + columns - 1) / columns
	var sheet := Image.create(columns * _cell, rows * _cell, false, Image.FORMAT_RGBA8)
	sheet.fill(BACKDROP)
	for index in views.size():
		var shot := views[index].get_texture().get_image()
		if shot == null or shot.is_empty():
			push_error("face_preview: viewport capture failed")
			return false
		shot.convert(Image.FORMAT_RGBA8)
		@warning_ignore("integer_division")
		var at := Vector2i((index % columns) * _cell, (index / columns) * _cell)
		sheet.blit_rect(shot, Rect2i(0, 0, _cell, _cell), at)
	for view in views:
		view.queue_free()
	await process_frame
	var error := sheet.save_png(out_path)
	if error != OK:
		push_error("face_preview: save failed for %s: %s" % [out_path, error_string(error)])
		return false
	print("FACE_PREVIEW wrote %s" % out_path)
	return true


func _build_cell(rig: Dictionary, rig_path: String, cell: Dictionary) -> SubViewport:
	var view := SubViewport.new()
	view.size = Vector2i(_cell, _cell)
	view.transparent_bg = false
	view.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(view)

	var backdrop_layer := CanvasLayer.new()
	backdrop_layer.layer = -10
	view.add_child(backdrop_layer)
	var backdrop := ColorRect.new()
	backdrop.color = BACKDROP
	backdrop.size = Vector2(_cell, _cell)
	backdrop_layer.add_child(backdrop)

	var puppet := PuppetScript.new()
	view.add_child(puppet)
	if not puppet.setup(rig_path, _motion, ACTOR_HEIGHT):
		push_error("face_preview: puppet setup failed for %s" % rig_path)
		view.queue_free()
		return null
	var anchors: Dictionary = rig.get("face_anchors", {})
	var face_box: Array = anchors.get("face_box", [0.0, 0.0, 1.0, 1.0])
	var rig_origin := Vector2(float(rig.get("center_x", 0.0)), float(rig.get("feet_y", 0.0)))
	var scale: float = puppet.puppet_scale
	# Brows to chin, a little wider than the face itself.
	var face_center := Vector2(
		float(face_box[0]) + float(face_box[2]) * 0.5,
		float(face_box[1]) + float(face_box[3]) * 0.46
	)
	var camera := Camera2D.new()
	camera.position = (face_center - rig_origin) * scale
	var zoom := float(_cell) / maxf(float(face_box[2]) * scale * 1.04, 1.0)
	camera.zoom = Vector2(zoom, zoom)
	view.add_child(camera)
	camera.make_current()

	var face: Node = puppet.get_face()
	if _face_override != null:
		face = _swap_face(rig, face, scale)
	if face == null:
		push_error("face_preview: rig has no face anchors: %s" % rig_path)
		view.queue_free()
		return null
	face.call("set_emotion", str(cell.get("emotion", "neutral")))
	if cell.has("tween"):
		# The same calls the puppet makes: one set_time per frame at 24 fps.
		face.call("set_viseme", "X")
		face.call("set_time", 0.0)
		face.call("set_viseme", str(cell["viseme"]))
		for frame in int(cell["tween"]):
			face.call("set_time", float(frame + 1) / 24.0)
	else:
		face.call("set_viseme", str(cell.get("viseme", "X")))
	if cell.has("yaw") and face.has_method("set_yaw"):
		face.call("set_yaw", float(cell["yaw"]))
	if cell.has("blink"):
		face.call("set_blink", float(cell["blink"]))

	var label_layer := CanvasLayer.new()
	label_layer.layer = 10
	view.add_child(label_layer)
	var plate := ColorRect.new()
	plate.color = Color(0.06, 0.08, 0.12, 0.78)
	plate.size = Vector2(_cell, 22.0)
	label_layer.add_child(plate)
	var label := Label.new()
	label.text = str(cell.get("label", ""))
	label.position = Vector2(6.0, 0.0)
	label.add_theme_font_size_override("font_size", 15)
	label.add_theme_color_override("font_color", Color(0.96, 0.97, 1.0))
	label_layer.add_child(label)
	return view


## Replaces the puppet's face with one made from FACE_PREVIEW_FACE.
func _swap_face(rig: Dictionary, current: Node, scale: float) -> Node:
	if current == null:
		return null
	var parent := current.get_parent()
	var layer := 2
	if current is CanvasItem:
		layer = (current as CanvasItem).z_index
	parent.remove_child(current)
	current.free()
	var head_px := Vector2.ZERO
	for bone: Dictionary in rig.get("bones", []):
		if str(bone.get("name", "")) == "head":
			var head: Array = bone.get("head", [0.0, 0.0])
			head_px = Vector2(float(head[0]), float(head[1]))
	var replacement: Node = _face_override.new()
	parent.add_child(replacement)
	replacement.call("setup", rig.get("face_anchors", {}), head_px, scale)
	if replacement is CanvasItem:
		(replacement as CanvasItem).z_as_relative = true
		(replacement as CanvasItem).z_index = layer
	return replacement


func _parse_rigs(value: String) -> Array[Array]:
	var rigs: Array[Array] = []
	for item in value.split(";", false):
		var parts := item.split("=", false, 1)
		if parts.size() != 2:
			continue
		var actor := parts[0].strip_edges()
		var path := parts[1].strip_edges().replace("\\", "/")
		if actor != "" and path != "":
			rigs.append([actor, path])
	return rigs


func _read_json(path: String) -> Dictionary:
	if not FileAccess.file_exists(path):
		return {}
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if parsed is Dictionary:
		return parsed as Dictionary
	return {}
