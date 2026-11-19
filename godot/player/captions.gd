extends CanvasLayer

## Screen-space captions for stage.json. main.gd calls setup(captions, font),
## apply_frame(frame), and clear_caption().

const REQUIRED_CHARS := "二维网格蒙皮探针林岚正在检查月面数据"
const VIEW := Vector2(1280.0, 720.0)

var cjk_font_path: String = ""

var _captions: Array[Dictionary] = []
var _font: FontFile
var _root: Control
var _label: Label


func _ready() -> void:
	layer = 80
	visible = false
	_build_ui()


func setup(captions: Array[Dictionary], font_path: String) -> void:
	if _root == null:
		_build_ui()
	_captions = captions
	if font_path != "":
		cjk_font_path = font_path
	_font = _load_cjk_font(cjk_font_path)
	_apply_font()
	_label.text = ""
	_root.visible = false


func apply_frame(frame_index: int) -> void:
	if _label == null:
		return
	var text := _text_at(frame_index)
	_label.text = text
	_root.visible = text != ""


func clear_caption() -> void:
	if _label != null:
		_label.text = ""
	if _root != null:
		_root.visible = false


func _build_ui() -> void:
	_root = Control.new()
	_root.name = "CaptionBar"
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_root.position = Vector2.ZERO
	_root.size = VIEW
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(_root)

	var fade := TextureRect.new()
	fade.mouse_filter = Control.MOUSE_FILTER_IGNORE
	fade.texture = _vertical_gradient(
		Color(0.01, 0.015, 0.03, 0.0),
		Color(0.012, 0.018, 0.034, 0.9)
	)
	fade.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	fade.stretch_mode = TextureRect.STRETCH_SCALE
	fade.position = Vector2(0.0, 588.0)
	fade.size = Vector2(1280.0, 132.0)
	_root.add_child(fade)

	_label = Label.new()
	_label.position = Vector2(64.0, 616.0)
	_label.size = Vector2(1152.0, 88.0)
	_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_label.add_theme_font_size_override("font_size", 36)
	_label.add_theme_color_override("font_color", Color(1.0, 0.97, 0.93))
	_label.add_theme_color_override("font_outline_color", Color(0.02, 0.03, 0.06, 0.92))
	_label.add_theme_color_override("font_shadow_color", Color(0.0, 0.0, 0.0, 0.78))
	_label.add_theme_constant_override("outline_size", 5)
	_label.add_theme_constant_override("shadow_offset_x", 0)
	_label.add_theme_constant_override("shadow_offset_y", 2)
	_label.add_theme_constant_override("line_spacing", 4)
	_root.add_child(_label)
	_root.visible = false


func _text_at(frame_index: int) -> String:
	for caption in _captions:
		var from_frame: int = int(caption.get("from", 0))
		var to_frame: int = int(caption.get("to", from_frame))
		if frame_index >= from_frame and frame_index < maxi(to_frame, from_frame + 1):
			return str(caption.get("text", ""))
	return ""


func _apply_font() -> void:
	if _font == null or _label == null:
		return
	_label.add_theme_font_override("font", _font)


func _vertical_gradient(top: Color, bottom: Color) -> GradientTexture2D:
	var gradient := Gradient.new()
	gradient.set_color(0, top)
	gradient.set_offset(0, 0.0)
	gradient.set_color(1, bottom)
	gradient.set_offset(1, 1.0)
	var texture := GradientTexture2D.new()
	texture.gradient = gradient
	texture.width = 8
	texture.height = 128
	texture.fill_from = Vector2(0.5, 0.0)
	texture.fill_to = Vector2(0.5, 1.0)
	return texture


func _load_cjk_font(requested: String) -> FontFile:
	if requested != "":
		var chosen := _try_font(requested)
		if chosen != null:
			_accept_font(requested, chosen)
			return chosen
	var paths: Array[String] = []
	for root in _font_roots():
		_collect_fonts(root, paths)
	for path in paths:
		var font := _try_font(path)
		if font != null:
			_accept_font(path, font)
			return font
	push_warning("Captions: no Noto CJK font covered the subtitle")
	return null


func _accept_font(path: String, font: FontFile) -> void:
	cjk_font_path = path
	print("CJK font %s face=%d name=%s" % [path, int(font.get_face_index(0)), font.get_font_name()])


func _font_roots() -> Array[String]:
	var roots: Array[String] = ["/usr/share/fonts"]
	var home := OS.get_environment("HOME")
	if home == "":
		home = OS.get_environment("USERPROFILE")
	if home != "":
		roots.append(home.path_join(".local/share/fonts"))
	return roots


func _collect_fonts(root: String, into: Array[String]) -> void:
	var dir := DirAccess.open(root)
	if dir == null:
		return
	for entry in dir.get_files():
		var name := String(entry)
		var lower := name.to_lower()
		var is_font := lower.ends_with(".ttc") or lower.ends_with(".otf") or lower.ends_with(".ttf")
		if is_font and lower.contains("noto") and lower.contains("cjk"):
			var full := root.path_join(name)
			if full not in into:
				into.append(full)
	for folder in dir.get_directories():
		_collect_fonts(root.path_join(String(folder)), into)


func _try_font(path: String) -> FontFile:
	if not FileAccess.file_exists(path):
		return null
	var font := FontFile.new()
	var err := font.load_dynamic_font(path)
	if err != OK:
		push_warning("Captions: load_dynamic_font failed %s (%s)" % [path, error_string(err)])
		return null
	var face_count := int(font.get_face_count())
	if face_count <= 0:
		push_warning("Captions: font has no faces: %s" % path)
		return null
	var best := -1
	var best_score := -1
	for face_i in face_count:
		font.set_face_index(0, face_i)
		font.allow_system_fallback = false
		if not _font_covers(font, REQUIRED_CHARS):
			continue
		var face_name := font.get_font_name().to_lower()
		var score := 100
		if face_name.contains("sans"):
			score += 20
		if face_name.ends_with(" sc") or face_name.ends_with("sc") or face_name.contains("simplified"):
			score += 40
		if face_name.contains("mono"):
			score -= 5
		if face_name.contains("regular"):
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
	var index := 0
	while index < text.length():
		if not font.has_char(text.unicode_at(index)):
			return false
		index += 1
	return true
