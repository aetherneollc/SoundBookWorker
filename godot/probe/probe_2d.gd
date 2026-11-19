extends Node2D

## 10-second 2D probe: runtime PNG, parallax, two skinned puppets, CJK subtitles.
## Pixel UVs are required: Polygon2D divides uv by the texture size.

const TEX_W := 512.0
const TEX_H := 1024.0
const PUPPET_SCALE := 0.40
const GRID_COLS := 33
const GRID_ROWS := 65
const SUBTITLE := "二维网格蒙皮探针：林岚正在检查月面数据。"
const REQUIRED_CHARS := "二维网格蒙皮探针林岚正在检查月面数据"
const SAMPLE_FRAMES: Array[int] = [12, 120, 220]

const JOINTS := {
	"hip": Vector2(256, 640),
	"torso": Vector2(256, 500),
	"head": Vector2(256, 300),
	"head_top": Vector2(256, 140),
	"upper_arm_l": Vector2(176, 368),
	"lower_arm_l": Vector2(116, 528),
	"hand_l": Vector2(98, 698),
	"upper_arm_r": Vector2(336, 368),
	"lower_arm_r": Vector2(396, 528),
	"hand_r": Vector2(414, 698),
	"upper_leg_l": Vector2(214, 652),
	"lower_leg_l": Vector2(200, 828),
	"foot_l": Vector2(188, 988),
	"upper_leg_r": Vector2(296, 652),
	"lower_leg_r": Vector2(310, 828),
	"foot_r": Vector2(324, 988),
}

var frames := 240
var fps := 24
var _frame := 0
var _done := false
var _t0 := 0
var _capture_usec := 0
var _png_loaded := false
var _cjk_loaded := false
var _cjk_path := ""
var _samples: Array = []

var _camera: Camera2D
var _bg_far: Sprite2D
var _bg_mid: Sprite2D
var _bg_near: Sprite2D
var _puppets: Array[Dictionary] = []


func _ready() -> void:
	_read_env()
	Engine.max_fps = 0
	DisplayServer.window_set_vsync_mode(DisplayServer.VSYNC_DISABLED)
	_build_scene()


func _read_env() -> void:
	var env_frames := OS.get_environment("PROBE_FRAMES")
	if env_frames.is_valid_int():
		frames = clampi(env_frames.to_int(), 1, 20000)
	var env_fps := OS.get_environment("PROBE_FPS")
	if env_fps.is_valid_int():
		fps = clampi(env_fps.to_int(), 1, 120)


func _build_scene() -> void:
	var assets := OS.get_environment("PROBE_ASSETS_DIR")
	var far_tex := _load_tex(assets, "bg_far.png")
	var mid_tex := _load_tex(assets, "bg_mid.png")
	var near_tex := _load_tex(assets, "bg_near.png")
	var puppet_tex := _load_tex(assets, "puppet_tex.png")
	_png_loaded = far_tex != null and mid_tex != null and near_tex != null and puppet_tex != null

	_camera = Camera2D.new()
	_camera.name = "Camera2D"
	_camera.anchor_mode = Camera2D.ANCHOR_MODE_DRAG_CENTER
	_camera.position_smoothing_enabled = false
	_camera.position = Vector2(640, 360)
	_camera.zoom = Vector2.ONE
	_camera.enabled = true
	add_child(_camera)
	_camera.make_current()

	_bg_far = _make_bg(far_tex, 0)
	_bg_mid = _make_bg(mid_tex, 1)
	_bg_near = _make_bg(near_tex, 2)

	if puppet_tex != null:
		_puppets.append(_build_puppet("PuppetA", Vector2(480, 348), Color(1, 1, 1), puppet_tex))
		_puppets.append(_build_puppet("PuppetB", Vector2(820, 362), Color(0.86, 0.92, 1), puppet_tex))
	else:
		push_error("puppet texture missing; skinned meshes were not built")

	_build_subtitle()
	_animate(0)
	print("PROBE ready png=%s cjk=%s puppets=%d" % [_png_loaded, _cjk_loaded, _puppets.size()])


func _load_tex(dir_path: String, file_name: String) -> ImageTexture:
	if dir_path == "":
		push_error("PROBE_ASSETS_DIR is empty")
		return null
	var path := dir_path.path_join(file_name)
	var img := Image.load_from_file(path)
	if img == null or img.is_empty():
		push_error("PNG load failed %s" % path)
		return null
	if img.get_width() <= 0 or img.get_height() <= 0:
		push_error("PNG has zero size: %s" % path)
		return null
	print("PNG %s %dx%d" % [file_name, img.get_width(), img.get_height()])
	return ImageTexture.create_from_image(img)


func _make_bg(tex: Texture2D, z: int) -> Sprite2D:
	var spr := Sprite2D.new()
	spr.centered = true
	spr.z_index = z
	spr.position = Vector2(640, 360)
	spr.texture = tex
	if tex == null:
		spr.modulate = Color(0.2, 0.25, 0.4)
	add_child(spr)
	return spr


func _tex_to_local(px: Vector2) -> Vector2:
	return (px - Vector2(TEX_W * 0.5, TEX_H * 0.5)) * PUPPET_SCALE


func _build_puppet(puppet_name: String, pos: Vector2, tint: Color, tex: Texture2D) -> Dictionary:
	var root := Node2D.new()
	root.name = puppet_name
	root.position = pos
	root.z_index = 5
	add_child(root)

	var skeleton := Skeleton2D.new()
	skeleton.name = "Skeleton2D"
	root.add_child(skeleton)

	var origin := Vector2(TEX_W * 0.5, TEX_H * 0.5)
	var hip := _add_bone(skeleton, "hip", JOINTS["hip"], origin)
	var torso := _add_bone(hip, "torso", JOINTS["torso"], JOINTS["hip"])
	var head := _add_bone(torso, "head", JOINTS["head"], JOINTS["torso"])
	var upper_arm_l := _add_bone(torso, "upper_arm_l", JOINTS["upper_arm_l"], JOINTS["torso"])
	var lower_arm_l := _add_bone(upper_arm_l, "lower_arm_l", JOINTS["lower_arm_l"], JOINTS["upper_arm_l"])
	var upper_arm_r := _add_bone(torso, "upper_arm_r", JOINTS["upper_arm_r"], JOINTS["torso"])
	var lower_arm_r := _add_bone(upper_arm_r, "lower_arm_r", JOINTS["lower_arm_r"], JOINTS["upper_arm_r"])
	var upper_leg_l := _add_bone(hip, "upper_leg_l", JOINTS["upper_leg_l"], JOINTS["hip"])
	var lower_leg_l := _add_bone(upper_leg_l, "lower_leg_l", JOINTS["lower_leg_l"], JOINTS["upper_leg_l"])
	var upper_leg_r := _add_bone(hip, "upper_leg_r", JOINTS["upper_leg_r"], JOINTS["hip"])
	var lower_leg_r := _add_bone(upper_leg_r, "lower_leg_r", JOINTS["lower_leg_r"], JOINTS["upper_leg_r"])

	var bones: Array[Bone2D] = [
		hip, torso, head,
		upper_arm_l, lower_arm_l, upper_arm_r, lower_arm_r,
		upper_leg_l, lower_leg_l, upper_leg_r, lower_leg_r,
	]
	var segments: Array[Vector2] = [
		JOINTS["hip"], JOINTS["torso"],
		JOINTS["torso"], JOINTS["head"],
		JOINTS["head"], JOINTS["head_top"],
		JOINTS["upper_arm_l"], JOINTS["lower_arm_l"],
		JOINTS["lower_arm_l"], JOINTS["hand_l"],
		JOINTS["upper_arm_r"], JOINTS["lower_arm_r"],
		JOINTS["lower_arm_r"], JOINTS["hand_r"],
		JOINTS["upper_leg_l"], JOINTS["lower_leg_l"],
		JOINTS["lower_leg_l"], JOINTS["foot_l"],
		JOINTS["upper_leg_r"], JOINTS["lower_leg_r"],
		JOINTS["lower_leg_r"], JOINTS["foot_r"],
	]
	var radii: Array[float] = [120.0, 150.0, 130.0, 78.0, 72.0, 78.0, 72.0, 84.0, 76.0, 84.0, 76.0]

	var poly := Polygon2D.new()
	poly.name = "Mesh"
	poly.color = tint
	poly.texture = tex
	poly.internal_vertex_count = 0
	root.add_child(poly)

	var points := PackedVector2Array()
	var uvs := PackedVector2Array()
	var tex_size := tex.get_size()
	for row in GRID_ROWS:
		for col in GRID_COLS:
			var u := float(col) / float(GRID_COLS - 1)
			var v := float(row) / float(GRID_ROWS - 1)
			var pixel := Vector2(u * TEX_W, v * TEX_H)
			points.append(_tex_to_local(pixel))
			uvs.append(Vector2(u * tex_size.x, v * tex_size.y))
	poly.polygon = points
	poly.uv = uvs

	var polys: Array = []
	for row in GRID_ROWS - 1:
		for col in GRID_COLS - 1:
			var i00 := row * GRID_COLS + col
			var i10 := i00 + 1
			var i01 := i00 + GRID_COLS
			var i11 := i01 + 1
			polys.append(PackedInt32Array([i00, i10, i11, i01]))
	poly.polygons = polys

	# Skeleton path must be set before add_bone. Bone paths are relative to Skeleton2D.
	poly.skeleton = poly.get_path_to(skeleton)
	poly.clear_bones()
	var vertex_count := points.size()
	for bone_i in bones.size():
		var weights := _weights_for_bone(bone_i, vertex_count, segments, radii)
		if weights.size() != vertex_count:
			push_error("weight length mismatch on %s" % bones[bone_i].name)
			continue
		poly.add_bone(skeleton.get_path_to(bones[bone_i]), weights)
	# add_bone() does not queue a redraw; the mesh is read on the next draw.
	poly.queue_redraw()

	print("%s bones=%d verts=%d" % [puppet_name, bones.size(), vertex_count])
	return {
		"root": root,
		"hip": hip,
		"torso": torso,
		"head": head,
		"upper_arm_l": upper_arm_l,
		"lower_arm_l": lower_arm_l,
		"upper_arm_r": upper_arm_r,
		"lower_arm_r": lower_arm_r,
		"upper_leg_l": upper_leg_l,
		"lower_leg_l": lower_leg_l,
		"upper_leg_r": upper_leg_r,
		"lower_leg_r": lower_leg_r,
	}


func _add_bone(parent: Node, bone_name: String, joint: Vector2, parent_joint: Vector2) -> Bone2D:
	var bone := Bone2D.new()
	bone.name = bone_name
	bone.position = _tex_to_local(joint) - _tex_to_local(parent_joint)
	bone.rotation = 0.0
	bone.set_autocalculate_length_and_angle(false)
	parent.add_child(bone)
	# rest must match the bind-pose transform or skinning collapses the mesh.
	bone.rest = bone.transform
	return bone


func _weights_for_bone(bone_i: int, vertex_count: int, segments: Array[Vector2], radii: Array[float]) -> PackedFloat32Array:
	var weights := PackedFloat32Array()
	weights.resize(vertex_count)
	for row in GRID_ROWS:
		for col in GRID_COLS:
			var vi := row * GRID_COLS + col
			var u := float(col) / float(GRID_COLS - 1)
			var v := float(row) / float(GRID_ROWS - 1)
			var pixel := Vector2(u * TEX_W, v * TEX_H)
			var best_i: Array[int] = [-1, -1, -1, -1]
			var best_w: Array[float] = [0.0, 0.0, 0.0, 0.0]
			var nearest := 0
			var nearest_d := 1.0e20
			for bi in radii.size():
				var dist := _dist_to_segment(pixel, segments[bi * 2], segments[bi * 2 + 1])
				if dist < nearest_d:
					nearest_d = dist
					nearest = bi
				var radius: float = radii[bi]
				var influence := 0.0
				if dist < radius:
					var t := 1.0 - dist / radius
					influence = t * t
				if influence <= 0.0:
					continue
				for k in 4:
					if influence > best_w[k]:
						for shift in range(3, k, -1):
							best_w[shift] = best_w[shift - 1]
							best_i[shift] = best_i[shift - 1]
						best_w[k] = influence
						best_i[k] = bi
						break
			var total := 0.0
			var hit := false
			for k in 4:
				if best_i[k] == bone_i:
					total += best_w[k]
					hit = true
			var sum := best_w[0] + best_w[1] + best_w[2] + best_w[3]
			if sum <= 0.0:
				weights[vi] = 1.0 if bone_i == nearest else 0.0
			elif hit:
				weights[vi] = total / sum
			else:
				weights[vi] = 0.0
	return weights


func _dist_to_segment(point: Vector2, a: Vector2, b: Vector2) -> float:
	var ab := b - a
	var len2 := ab.length_squared()
	if len2 < 0.001:
		return point.distance_to(a)
	var t := clampf((point - a).dot(ab) / len2, 0.0, 1.0)
	return point.distance_to(a + ab * t)


func _build_subtitle() -> void:
	var font := _load_cjk_font()
	var layer := CanvasLayer.new()
	layer.name = "SubtitleLayer"
	layer.layer = 30
	add_child(layer)

	var bar := ColorRect.new()
	bar.name = "SubtitleBar"
	bar.color = Color(0, 0, 0, 0.58)
	bar.position = Vector2(0, 636)
	bar.size = Vector2(1280, 84)
	bar.mouse_filter = Control.MOUSE_FILTER_IGNORE
	layer.add_child(bar)
	bar.position = Vector2(0, 636)
	bar.size = Vector2(1280, 84)

	var label := Label.new()
	label.name = "Subtitle"
	label.text = SUBTITLE
	label.position = Vector2(40, 640)
	label.size = Vector2(1200, 76)
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	if font != null:
		label.add_theme_font_override("font", font)
		label.add_theme_font_size_override("font_size", 36)
	label.add_theme_color_override("font_color", Color(1, 0.97, 0.92))
	label.add_theme_color_override("font_shadow_color", Color(0, 0, 0, 0.9))
	label.add_theme_constant_override("shadow_offset_x", 2)
	label.add_theme_constant_override("shadow_offset_y", 2)
	layer.add_child(label)
	label.position = Vector2(40, 640)
	label.size = Vector2(1200, 76)


func _load_cjk_font() -> FontFile:
	var from_env := OS.get_environment("PROBE_CJK_FONT")
	if from_env != "":
		var env_font := _try_font(from_env)
		if env_font != null:
			_accept_font(from_env, env_font)
			return env_font
	var paths: Array[String] = []
	_collect_fonts("/usr/share/fonts", paths)
	for path in paths:
		var font := _try_font(path)
		if font != null:
			_accept_font(path, font)
			return font
	push_error("no Noto CJK font covered the probe subtitle")
	_cjk_loaded = false
	_cjk_path = from_env
	return null


func _accept_font(path: String, font: FontFile) -> void:
	_cjk_loaded = true
	_cjk_path = path
	print("CJK font %s face=%d name=%s" % [path, font.get_face_index(0), font.get_font_name()])


func _collect_fonts(root: String, into: Array[String]) -> void:
	var dir := DirAccess.open(root)
	if dir == null:
		return
	for entry in dir.get_files():
		var lower := String(entry).to_lower()
		var is_font := lower.ends_with(".ttc") or lower.ends_with(".otf") or lower.ends_with(".ttf")
		if is_font and "noto" in lower and "cjk" in lower:
			var full := root.path_join(String(entry))
			if full not in into:
				into.append(full)
	for entry in dir.get_directories():
		_collect_fonts(root.path_join(String(entry)), into)


func _try_font(path: String) -> FontFile:
	if not FileAccess.file_exists(path):
		return null
	var font := FontFile.new()
	var err := font.load_dynamic_font(path)
	if err != OK:
		push_error("load_dynamic_font failed %s (%s)" % [path, error_string(err)])
		return null
	var face_count := int(font.get_face_count())
	if face_count <= 0:
		push_error("font has no faces: %s" % path)
		return null
	var best := -1
	var best_score := -1
	for face_i in face_count:
		font.set_face_index(0, face_i)
		font.allow_system_fallback = false
		if not _font_covers(font, REQUIRED_CHARS):
			continue
		var name := font.get_font_name().to_lower()
		var score := 100
		if "sans" in name:
			score += 20
		if name.ends_with(" sc") or name.ends_with("sc") or "simplified" in name:
			score += 40
		if "mono" in name:
			score -= 5
		if "regular" in name:
			score += 8
		if score > best_score:
			best_score = score
			best = face_i
	if best < 0:
		return null
	font.set_face_index(0, best)
	font.allow_system_fallback = false
	return font


func _font_covers(font: Font, text: String) -> bool:
	for i in text.length():
		if not font.has_char(text.unicode_at(i)):
			return false
	return true


func _process(_dt: float) -> void:
	if _done:
		return
	if _frame == 0:
		_t0 = Time.get_ticks_usec()
	_frame += 1
	_animate(_frame)
	if _frame % 48 == 0:
		print("PROBE frame %d/%d" % [_frame, frames])
	# get_image() reads the viewport already drawn, same pattern as the speed bench.
	if SAMPLE_FRAMES.has(_frame):
		_capture(_frame)
	if _frame >= frames:
		_finish()


func _animate(frame_i: int) -> void:
	var t := float(frame_i) / float(fps)
	var u := clampf(float(frame_i) / float(maxi(frames, 1)), 0.0, 1.0)
	var pan := Vector2(140.0 * u, -28.0 * u)
	if _camera != null:
		_camera.position = Vector2(640, 360) + pan
		_camera.zoom = Vector2.ONE * (1.0 + 0.16 * u)
	if _bg_far != null:
		_bg_far.position = Vector2(640, 360) + pan * 0.82
	if _bg_mid != null:
		_bg_mid.position = Vector2(640, 360) + pan * 0.42
	if _bg_near != null:
		_bg_near.position = Vector2(640, 360) + pan * 0.08
	for i in _puppets.size():
		var puppet: Dictionary = _puppets[i]
		var phase := float(i) * 0.85
		var wave := sin(t * TAU * 1.35 + phase)
		(puppet["upper_arm_r"] as Bone2D).rotation = wave * 0.95
		(puppet["lower_arm_r"] as Bone2D).rotation = 0.25 + wave * 0.65
		(puppet["upper_arm_l"] as Bone2D).rotation = sin(t * TAU * 0.7 + phase) * 0.28
		(puppet["lower_arm_l"] as Bone2D).rotation = 0.18 + sin(t * TAU * 0.7 + phase) * 0.2
		(puppet["torso"] as Bone2D).rotation = sin(t * TAU * 0.35 + phase) * 0.045
		(puppet["head"] as Bone2D).rotation = sin(t * TAU * 0.5 + phase) * 0.14
		(puppet["hip"] as Bone2D).rotation = sin(t * TAU * 0.35 + phase) * 0.03
		(puppet["upper_leg_l"] as Bone2D).rotation = sin(t * TAU * 0.7 + phase) * 0.05
		(puppet["upper_leg_r"] as Bone2D).rotation = sin(t * TAU * 0.7 + phase + PI) * 0.05
		(puppet["lower_leg_l"] as Bone2D).rotation = 0.1 + sin(t * TAU * 0.7 + phase) * 0.07
		(puppet["lower_leg_r"] as Bone2D).rotation = -0.08 + sin(t * TAU * 0.7 + phase + PI) * 0.07


func _capture(frame_id: int) -> void:
	var started := Time.get_ticks_usec()
	var tex := get_viewport().get_texture()
	var img: Image = tex.get_image() if tex != null else null
	var file_name := "frame_%03d.png" % frame_id
	var sample := {
		"frame": frame_id,
		"file": file_name,
		"width": 0,
		"height": 0,
		"mean_luma": 0.0,
		"nonblack_ratio": 0.0,
		"puppet_visible": false,
		"saved": false,
	}
	if img != null and img.get_width() > 0 and img.get_height() > 0:
		var stats := _measure(img)
		sample["width"] = img.get_width()
		sample["height"] = img.get_height()
		sample["mean_luma"] = stats["mean_luma"]
		sample["nonblack_ratio"] = stats["nonblack_ratio"]
		sample["puppet_visible"] = _puppets_visible(img)
		var out_dir := OS.get_environment("PROBE_OUT_DIR")
		if out_dir != "":
			var path := out_dir.path_join(file_name)
			var err := img.save_png(path)
			sample["saved"] = err == OK
			if err != OK:
				push_error("save_png failed %s (%s)" % [path, error_string(err)])
	_samples.append(sample)
	_capture_usec += Time.get_ticks_usec() - started
	print("PROBE sample %s" % JSON.stringify(sample))


func _measure(img: Image) -> Dictionary:
	var width := img.get_width()
	var height := img.get_height()
	var acc := 0.0
	var count := 0
	var nonblack := 0
	var step := 4
	for y in range(0, height, step):
		for x in range(0, width, step):
			var color := img.get_pixel(x, y)
			acc += 0.2126 * color.r + 0.7152 * color.g + 0.0722 * color.b
			count += 1
			if color.r > 0.02 or color.g > 0.02 or color.b > 0.02:
				nonblack += 1
	return {
		"mean_luma": acc / float(maxi(count, 1)),
		"nonblack_ratio": float(nonblack) / float(maxi(count, 1)),
	}


func _puppets_visible(img: Image) -> bool:
	if _puppets.is_empty():
		return false
	var vp := Vector2(img.get_width(), img.get_height())
	var visible := 0
	for puppet in _puppets:
		var world: Vector2 = (puppet["root"] as Node2D).position
		var screen := (world - _camera.position) * _camera.zoom + vp * 0.5
		if _region_is_jacket(img, screen):
			visible += 1
	return visible == _puppets.size()


func _region_is_jacket(img: Image, center: Vector2) -> bool:
	var hits := 0
	var count := 0
	for y in range(-12, 13, 2):
		for x in range(-12, 13, 2):
			var px := int(center.x) + x
			var py := int(center.y) + y
			if px < 0 or py < 0 or px >= img.get_width() or py >= img.get_height():
				continue
			count += 1
			var color := img.get_pixel(px, py)
			if color.r > 0.45 and color.r > color.g + 0.12 and color.r > color.b + 0.08:
				hits += 1
	return count > 0 and float(hits) / float(count) >= 0.35


func _pixel_ok() -> bool:
	if _samples.size() < SAMPLE_FRAMES.size():
		return false
	for sample in _samples:
		var width := int(sample["width"])
		var height := int(sample["height"])
		var luma := float(sample["mean_luma"])
		var coverage := float(sample["nonblack_ratio"])
		if not bool(sample["saved"]):
			return false
		if width < 1200 or height < 680:
			return false
		if luma < 0.08 or coverage < 0.45:
			return false
		if not bool(sample["puppet_visible"]):
			return false
	return true


func _finish() -> void:
	if _done:
		return
	_done = true
	var elapsed := (Time.get_ticks_usec() - _t0 - _capture_usec) / 1000000.0
	if elapsed <= 0.0:
		elapsed = 0.000001
	var render_fps := float(frames) / elapsed
	var payload := {
		"frames": frames,
		"fps_target": fps,
		"elapsed_sec": elapsed,
		"render_fps": render_fps,
		"realtime_factor": render_fps / float(maxi(fps, 1)),
		"png_loaded": _png_loaded,
		"cjk_font_loaded": _cjk_loaded,
		"cjk_font_path": _cjk_path,
		"pixel_ok": _pixel_ok(),
		"sample_frames": _samples,
		"puppets": _puppets.size(),
		"bones_per_puppet": 11,
		"adapter": RenderingServer.get_video_adapter_name(),
	}
	var text := JSON.stringify(payload, "\t")
	print("PROBE_JSON " + text)
	var out_dir := OS.get_environment("PROBE_OUT_DIR")
	if out_dir != "":
		var handle := FileAccess.open(out_dir.path_join("result.json"), FileAccess.WRITE)
		if handle == null:
			push_error("result.json open failed: %s" % error_string(FileAccess.get_open_error()))
		else:
			handle.store_string(text + "\n")
			handle.close()
	get_tree().quit(0)
