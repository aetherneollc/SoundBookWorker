extends CanvasLayer

## Episode opener and end card. main.gd calls setup(font), show_intro(config),
## show_cta(config), apply_frame(local_frame), and hide_card().

const REQUIRED_CHARS := "二维网格蒙皮探针林岚正在检查月面数据"
const VIEW := Vector2(1280.0, 720.0)
const INTRO_ACCENT := Color(0.49, 0.89, 0.92, 1.0)
const CTA_ACCENT := Color(0.96, 0.78, 0.42, 1.0)

var cjk_font_path: String = ""

var _font: FontFile
var _root: Control
var _panel: Panel
var _panel_style: StyleBoxFlat
var _episode: Label
var _title: Label
var _subtitle: Label
var _accent: ColorRect
var _kicker: Label
var _panel_home := Vector2(150.0, 132.0)
var _frames := 0
var _mode := ""


func _ready() -> void:
	layer = 100
	visible = false
	_build_ui()


func setup(font_path: String) -> void:
	if _root == null:
		_build_ui()
	if font_path != "":
		cjk_font_path = font_path
	_font = _load_cjk_font(cjk_font_path)
	_apply_font()
	_root.visible = false


func show_intro(config: Dictionary) -> void:
	_present("intro", config)


func show_cta(config: Dictionary) -> void:
	_present("cta", config)


func apply_frame(local_frame: int) -> void:
	if _root == null or _panel == null or _accent == null:
		return
	var fade := 8
	var alpha := 1.0
	if local_frame < fade:
		alpha = float(local_frame + 1) / float(fade + 1)
	elif _frames > fade and local_frame > _frames - fade:
		alpha = clampf(float(_frames - local_frame) / float(fade), 0.0, 1.0)
	_root.modulate = Color(1.0, 1.0, 1.0, clampf(alpha, 0.0, 1.0))
	var settle := clampf(float(local_frame) / float(fade), 0.0, 1.0)
	_panel.position = _panel_home + Vector2(0.0, (1.0 - settle) * 18.0)
	var grow := clampf(0.18 + float(local_frame) / 16.0, 0.18, 1.0)
	_accent.scale = Vector2(grow, 1.0)


func hide_card() -> void:
	_mode = ""
	if _root != null:
		_root.visible = false


func _present(mode: String, config: Dictionary) -> void:
	if _root == null:
		_build_ui()
	_mode = mode
	_frames = maxi(int(config.get("frames", 0)), 1)
	var episode := int(config.get("episode_number", 0))
	var title := str(config.get("title", ""))
	var subtitle := str(config.get("subtitle", ""))
	var accent := INTRO_ACCENT if mode == "intro" else CTA_ACCENT
	_kicker.text = ""
	_kicker.visible = false
	if mode == "intro" and episode > 0:
		_episode.text = "第 %d 集" % episode
	else:
		_episode.text = ""
	_episode.visible = _episode.text != ""
	_title.position = Vector2(48.0, 176.0) if mode == "intro" else Vector2(48.0, 132.0)
	_title.text = title
	_subtitle.text = subtitle
	_subtitle.visible = subtitle != ""
	_accent.color = accent
	_episode.add_theme_color_override("font_color", accent)
	_kicker.add_theme_color_override("font_color", accent)
	if _panel_style != null:
		_panel_style.border_color = Color(accent.r, accent.g, accent.b, 0.55)
	_root.visible = true
	_root.modulate = Color(1.0, 1.0, 1.0, 1.0)
	apply_frame(0)


func _build_ui() -> void:
	_root = Control.new()
	_root.name = "TitleRoot"
	_root.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_root.position = Vector2.ZERO
	_root.size = VIEW
	_root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(_root)

	var backdrop := TextureRect.new()
	backdrop.mouse_filter = Control.MOUSE_FILTER_IGNORE
	backdrop.texture = _vertical_gradient(
		Color(0.03, 0.06, 0.1, 1.0),
		Color(0.07, 0.14, 0.2, 1.0)
	)
	backdrop.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	backdrop.stretch_mode = TextureRect.STRETCH_SCALE
	backdrop.position = Vector2.ZERO
	backdrop.size = VIEW
	_root.add_child(backdrop)

	_panel = Panel.new()
	_panel.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_panel.position = _panel_home
	_panel.size = Vector2(980.0, 456.0)
	_panel_style = StyleBoxFlat.new()
	_panel_style.bg_color = Color(0.045, 0.08, 0.12, 0.94)
	_panel_style.set_corner_radius_all(28)
	_panel_style.set_border_width_all(1)
	_panel_style.border_color = Color(INTRO_ACCENT.r, INTRO_ACCENT.g, INTRO_ACCENT.b, 0.45)
	_panel_style.shadow_color = Color(0.0, 0.0, 0.0, 0.38)
	_panel_style.shadow_size = 28
	_panel_style.shadow_offset = Vector2(0.0, 12.0)
	_panel_style.content_margin_left = 36.0
	_panel_style.content_margin_right = 36.0
	_panel.add_theme_stylebox_override("panel", _panel_style)
	_root.add_child(_panel)

	_kicker = _make_label(Vector2(70.0, 36.0), Vector2(840.0, 36.0), 22, INTRO_ACCENT)
	_episode = _make_label(Vector2(70.0, 72.0), Vector2(840.0, 48.0), 28, INTRO_ACCENT)
	_accent = ColorRect.new()
	_accent.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_accent.color = INTRO_ACCENT
	_accent.position = Vector2(350.0, 148.0)
	_accent.size = Vector2(280.0, 3.0)
	_accent.pivot_offset = Vector2(140.0, 1.5)
	_title = _make_label(Vector2(48.0, 176.0), Vector2(884.0, 150.0), 60, Color(0.97, 0.98, 1.0))
	_subtitle = _make_label(Vector2(80.0, 340.0), Vector2(820.0, 72.0), 28, Color(0.78, 0.86, 0.9))
	_panel.add_child(_kicker)
	_panel.add_child(_episode)
	_panel.add_child(_accent)
	_panel.add_child(_title)
	_panel.add_child(_subtitle)
	_root.visible = false


func _make_label(pos: Vector2, label_size: Vector2, font_size: int, color: Color) -> Label:
	var label := Label.new()
	label.position = pos
	label.size = label_size
	label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	label.add_theme_font_size_override("font_size", font_size)
	label.add_theme_color_override("font_color", color)
	label.add_theme_color_override("font_shadow_color", Color(0.0, 0.0, 0.0, 0.55))
	label.add_theme_constant_override("shadow_offset_x", 0)
	label.add_theme_constant_override("shadow_offset_y", 2)
	return label


func _apply_font() -> void:
	if _font == null:
		return
	_assign_font(_kicker)
	_assign_font(_episode)
	_assign_font(_title)
	_assign_font(_subtitle)


func _assign_font(label: Label) -> void:
	if label != null:
		label.add_theme_font_override("font", _font)


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
	push_warning("TitleCard: no Noto CJK font covered the title")
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
		push_warning("TitleCard: load_dynamic_font failed %s (%s)" % [path, error_string(err)])
		return null
	var face_count := int(font.get_face_count())
	if face_count <= 0:
		push_warning("TitleCard: font has no faces: %s" % path)
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
