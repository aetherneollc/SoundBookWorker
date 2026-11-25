class_name Stage2D
extends Node2D

## Runtime-loaded 2D set with three parallax planes, grounded actor shadows,
## and lightweight cel-shaded props.

const VIEW_SIZE := Vector2(1280.0, 720.0)
const VIEW_CENTER := VIEW_SIZE * 0.5
const BACKGROUND_OVERSCAN := 1.15
const NEAR_ALPHA_THRESHOLD := 0.2
const NEAR_COVERAGE_THRESHOLD := 0.50
const NEAR_FRONT_Z := 40
const NEAR_BACK_Z := -18


class GroundShadowLayer:
	extends Node2D

	var shadows: Array[Dictionary] = []

	func set_shadows(value: Array[Dictionary]) -> void:
		shadows = value
		queue_redraw()

	func _draw() -> void:
		for shadow: Dictionary in shadows:
			var center: Vector2 = shadow.get("position", Vector2.ZERO)
			var scale_value: float = maxf(float(shadow.get("scale", 1.0)), 0.1)
			_draw_soft_ellipse(center, Vector2(78.0, 11.0) * scale_value)

	func _draw_soft_ellipse(center: Vector2, radius: Vector2) -> void:
		# Layered flat ellipses read as a soft contact shadow without a shader.
		_draw_ellipse(center + Vector2(0.0, 3.0), radius * 1.15, Color(0.02, 0.03, 0.05, 0.28))
		_draw_ellipse(center + Vector2(0.0, 2.0), radius * 0.92, Color(0.01, 0.02, 0.04, 0.48))
		_draw_ellipse(center + Vector2(0.0, 1.0), radius * 0.62, Color(0.0, 0.0, 0.0, 0.62))

	func _draw_ellipse(center: Vector2, radius: Vector2, color: Color) -> void:
		var points := PackedVector2Array()
		for point_index: int in range(32):
			var angle: float = TAU * float(point_index) / 32.0
			points.append(center + Vector2(cos(angle) * radius.x, sin(angle) * radius.y))
		draw_colored_polygon(points, color)


class PropArt:
	extends Node2D

	const OUTLINE := Color(0.055, 0.075, 0.12, 1.0)
	const BODY := Color(0.22, 0.31, 0.43, 1.0)
	const BODY_LIGHT := Color(0.36, 0.48, 0.60, 1.0)
	const ACCENT := Color(0.18, 0.82, 0.88, 1.0)

	var kind := ""

	func configure(value: String) -> void:
		kind = value
		queue_redraw()

	func _draw() -> void:
		match kind:
			"console_desk":
				_draw_console_desk()
			"telemetry_screen":
				_draw_telemetry_screen()
			"workbench":
				_draw_workbench()
			_:
				_draw_workbench()

	func _filled_rect(rect: Rect2, fill: Color, width: float = 5.0) -> void:
		draw_rect(rect, fill, true)
		draw_rect(rect, OUTLINE, false, width, true)

	func _draw_console_desk() -> void:
		_filled_rect(Rect2(-132.0, -80.0, 264.0, 34.0), BODY_LIGHT, 6.0)
		_filled_rect(Rect2(-112.0, -47.0, 224.0, 47.0), BODY, 6.0)
		_filled_rect(Rect2(-102.0, -134.0, 204.0, 56.0), BODY, 6.0)
		_filled_rect(Rect2(-86.0, -122.0, 78.0, 32.0), Color(0.06, 0.17, 0.25), 4.0)
		_filled_rect(Rect2(8.0, -122.0, 78.0, 32.0), Color(0.06, 0.17, 0.25), 4.0)
		draw_line(Vector2(-73.0, -106.0), Vector2(-30.0, -106.0), ACCENT, 3.0, true)
		draw_circle(Vector2(36.0, -106.0), 5.0, ACCENT)
		draw_circle(Vector2(56.0, -106.0), 5.0, Color(1.0, 0.64, 0.25))

	func _draw_telemetry_screen() -> void:
		_filled_rect(Rect2(-116.0, -230.0, 232.0, 156.0), BODY, 7.0)
		_filled_rect(Rect2(-99.0, -212.0, 198.0, 119.0), Color(0.035, 0.11, 0.17), 4.0)
		var trace := PackedVector2Array([
			Vector2(-86.0, -145.0),
			Vector2(-56.0, -145.0),
			Vector2(-43.0, -180.0),
			Vector2(-21.0, -117.0),
			Vector2(3.0, -158.0),
			Vector2(28.0, -136.0),
			Vector2(83.0, -136.0),
		])
		draw_polyline(trace, ACCENT, 4.0, true)
		_filled_rect(Rect2(-15.0, -74.0, 30.0, 52.0), BODY_LIGHT, 5.0)
		_filled_rect(Rect2(-72.0, -24.0, 144.0, 24.0), BODY, 5.0)

	func _draw_workbench() -> void:
		_filled_rect(Rect2(-148.0, -93.0, 296.0, 35.0), BODY_LIGHT, 6.0)
		_filled_rect(Rect2(-132.0, -59.0, 28.0, 59.0), BODY, 6.0)
		_filled_rect(Rect2(104.0, -59.0, 28.0, 59.0), BODY, 6.0)
		_filled_rect(Rect2(-76.0, -124.0, 66.0, 29.0), Color(0.73, 0.44, 0.21), 4.0)
		_filled_rect(Rect2(16.0, -137.0, 82.0, 42.0), Color(0.12, 0.42, 0.50), 4.0)
		draw_line(Vector2(28.0, -116.0), Vector2(86.0, -116.0), ACCENT, 3.0, true)


var _texture_cache: Dictionary = {}
var _near_alpha_stats_cache: Dictionary = {}
var _far_layer: Node2D
var _mid_layer: Node2D
var _near_layer: Node2D
var _near_prop_layer: Node2D
var _shadow_layer: GroundShadowLayer
var _far_sprite: Sprite2D
var _mid_sprite: Sprite2D
var _near_sprite: Sprite2D
var _prop_node: Node2D
var _ground_y_px := VIEW_SIZE.y * 0.86
var _last_camera_zoom := 1.0
var _last_near_alpha_stats: Dictionary = {}


func _ready() -> void:
	_ensure_layers()


func apply_set(set_cfg: Dictionary, base_dir: String) -> void:
	_ensure_layers()
	_clear_prop()

	_ground_y_px = ground_y_to_pixels(float(set_cfg.get("ground_y", 0.86)))
	var plate_path: String = str(set_cfg.get("plate", "")).strip_edges()
	var far_path: String = str(set_cfg.get("far", "")).strip_edges()
	var near_path: String = str(set_cfg.get("near", "")).strip_edges()
	var resolved_plate: String = _resolve_path(plate_path, base_dir)
	var resolved_far: String = _resolve_path(far_path if far_path != "" else plate_path, base_dir)
	var resolved_near: String = _resolve_path(near_path, base_dir)

	var far_texture: Texture2D = _load_texture(resolved_far)
	var mid_texture: Texture2D = null
	if far_path != "" and plate_path != "" and resolved_plate != resolved_far:
		mid_texture = _load_texture(resolved_plate)
	var near_texture: Texture2D = _load_texture(resolved_near)
	_configure_near_depth(near_texture, resolved_near, bool(set_cfg.get("near_in_front", false)))

	_set_background(_far_sprite, far_texture)
	_set_background(_mid_sprite, mid_texture)
	_set_background(_near_sprite, near_texture)

	var prop_value: Variant = set_cfg.get("prop", {})
	if prop_value is Dictionary:
		_build_prop(prop_value as Dictionary, base_dir)
	update_parallax(VIEW_CENTER, 1.0, 0.0, 0.0)


func update_parallax(
	cam_pos: Vector2,
	cam_zoom: float,
	shot_time: float,
	walk_scroll_speed: float
) -> void:
	_ensure_layers()
	_last_camera_zoom = maxf(cam_zoom, 0.001)
	var camera_offset: Vector2 = cam_pos - VIEW_CENTER
	var walk_offset: float = shot_time * walk_scroll_speed
	_far_layer.position = camera_offset * 0.25 + Vector2(-walk_offset * 0.25, 0.0)
	_mid_layer.position = Vector2(-walk_offset * 0.65, 0.0)
	_near_layer.position = camera_offset * -0.35 + Vector2(-walk_offset * 1.15, 0.0)
	_near_prop_layer.position = _near_layer.position
	# Shadows belong to the moving mid plane but stay locked to actor feet.
	_shadow_layer.position = -_mid_layer.position


func set_actor_shadows(shadows: Array[Dictionary]) -> void:
	_ensure_layers()
	_shadow_layer.set_shadows(shadows)


func depth_to_scale(depth: String) -> float:
	match depth.to_lower():
		"far":
			return 0.86
		"near":
			return 1.12
		_:
			return 1.0


func ground_y_to_pixels(value: float) -> float:
	if value >= 0.0 and value <= 2.0:
		return clampf(value * VIEW_SIZE.y, 0.0, VIEW_SIZE.y)
	return clampf(value, 0.0, VIEW_SIZE.y)


func get_ground_y() -> float:
	return _ground_y_px


func get_cached_texture_count() -> int:
	return _texture_cache.size()


func get_near_alpha_stats() -> Dictionary:
	return _last_near_alpha_stats.duplicate()


func _ensure_layers() -> void:
	if _far_layer != null:
		return
	_far_layer = Node2D.new()
	_far_layer.name = "FarLayer"
	_far_layer.z_index = -30
	add_child(_far_layer)

	_mid_layer = Node2D.new()
	_mid_layer.name = "MidLayer"
	_mid_layer.z_index = -10
	add_child(_mid_layer)

	_near_layer = Node2D.new()
	_near_layer.name = "NearLayer"
	_near_layer.z_index = NEAR_FRONT_Z
	add_child(_near_layer)

	# Keep explicit near-depth props in front even when a full-width legacy
	# near texture is reclassified as part of the room behind the actors.
	_near_prop_layer = Node2D.new()
	_near_prop_layer.name = "NearPropLayer"
	_near_prop_layer.z_index = NEAR_FRONT_Z
	add_child(_near_prop_layer)

	_far_sprite = _make_background_sprite("Far")
	_far_layer.add_child(_far_sprite)
	_mid_sprite = _make_background_sprite("Mid")
	_mid_layer.add_child(_mid_sprite)
	_near_sprite = _make_background_sprite("Near")
	_near_layer.add_child(_near_sprite)

	_shadow_layer = GroundShadowLayer.new()
	_shadow_layer.name = "GroundShadows"
	_mid_layer.add_child(_shadow_layer)


func _make_background_sprite(sprite_name: String) -> Sprite2D:
	var sprite := Sprite2D.new()
	sprite.name = sprite_name
	sprite.centered = true
	sprite.position = VIEW_CENTER
	sprite.visible = false
	return sprite


func _set_background(sprite: Sprite2D, texture: Texture2D) -> void:
	sprite.texture = texture
	sprite.visible = texture != null
	if texture == null:
		sprite.scale = Vector2.ONE
		return
	var texture_size: Vector2 = texture.get_size()
	if texture_size.x <= 0.0 or texture_size.y <= 0.0:
		sprite.visible = false
		return
	var target_size: Vector2 = VIEW_SIZE * BACKGROUND_OVERSCAN
	var fit_scale: float = maxf(target_size.x / texture_size.x, target_size.y / texture_size.y)
	sprite.scale = Vector2.ONE * fit_scale


func _load_texture(path: String) -> Texture2D:
	if path == "":
		return null
	var cache_key: String = path.replace("\\", "/").simplify_path()
	if _texture_cache.has(cache_key):
		return _texture_cache[cache_key] as Texture2D
	var image: Image = Image.load_from_file(path)
	if image == null or image.is_empty():
		push_error("Stage2D: PNG load failed: %s" % path)
		_texture_cache[cache_key] = null
		return null
	if image.get_width() <= 0 or image.get_height() <= 0:
		push_error("Stage2D: PNG has zero size: %s" % path)
		_texture_cache[cache_key] = null
		return null
	var texture := ImageTexture.create_from_image(image)
	_texture_cache[cache_key] = texture
	return texture


func _configure_near_depth(
	texture: Texture2D,
	path: String,
	force_in_front: bool
) -> void:
	_last_near_alpha_stats = _measure_near_alpha(texture, path)
	var horizontal_coverage: float = float(
		_last_near_alpha_stats.get("horizontal_coverage", 0.0)
	)
	var opaque_area_ratio: float = float(
		_last_near_alpha_stats.get("opaque_area_ratio", 0.0)
	)
	var full_width_layer: bool = texture != null and horizontal_coverage > NEAR_COVERAGE_THRESHOLD
	var in_front: bool = force_in_front or not full_width_layer
	_near_layer.z_index = NEAR_FRONT_Z if in_front else NEAR_BACK_Z
	if texture != null:
		print(
			"Stage2D near alpha horizontal_coverage=%.3f opaque_area_ratio=%.3f z=%d forced=%s"
			% [
				horizontal_coverage,
				opaque_area_ratio,
				_near_layer.z_index,
				str(force_in_front),
			]
		)


func _measure_near_alpha(texture: Texture2D, path: String) -> Dictionary:
	if texture == null or path == "":
		return {}
	var cache_key: String = path.replace("\\", "/").simplify_path()
	if _near_alpha_stats_cache.has(cache_key):
		return (_near_alpha_stats_cache[cache_key] as Dictionary).duplicate()
	var image: Image = texture.get_image()
	if image == null or image.is_empty():
		return {}
	var width: int = image.get_width()
	var height: int = image.get_height()
	if width <= 0 or height <= 0:
		return {}
	var from_y: int = clampi(int(floor(float(height) * 0.40)), 0, height - 1)
	var to_y: int = clampi(int(ceil(float(height) * 0.85)), from_y + 1, height)
	var covered_columns := 0
	var opaque_pixels := 0
	for x: int in range(width):
		var column_covered := false
		for y: int in range(from_y, to_y):
			if image.get_pixel(x, y).a <= NEAR_ALPHA_THRESHOLD:
				continue
			opaque_pixels += 1
			column_covered = true
		if column_covered:
			covered_columns += 1
	var sample_pixels: int = width * (to_y - from_y)
	var stats := {
		"horizontal_coverage": float(covered_columns) / float(width),
		"opaque_area_ratio": float(opaque_pixels) / float(maxi(sample_pixels, 1)),
		"sample_y_from": from_y,
		"sample_y_to": to_y,
		"alpha_threshold": NEAR_ALPHA_THRESHOLD,
	}
	_near_alpha_stats_cache[cache_key] = stats
	return stats.duplicate()


func _resolve_path(raw_path: String, base_dir: String) -> String:
	if raw_path == "":
		return ""
	var normalized: String = raw_path.replace("\\", "/")
	if normalized.begins_with("res://") or normalized.begins_with("user://"):
		return normalized.simplify_path()
	if normalized.is_absolute_path():
		return normalized.simplify_path()
	if base_dir == "":
		return normalized.simplify_path()
	return base_dir.path_join(normalized).simplify_path()


func _build_prop(prop_cfg: Dictionary, base_dir: String) -> void:
	var kind: String = str(prop_cfg.get("kind", "")).strip_edges()
	var image_path: String = str(prop_cfg.get("image", "")).strip_edges()
	if kind == "" and image_path == "":
		return
	var depth: String = str(prop_cfg.get("depth", "mid")).to_lower()
	var x_px: float = _horizontal_to_pixels(float(prop_cfg.get("x", 0.5)))
	var prop_scale: float = depth_to_scale(depth)
	var layer: Node2D = _layer_for_depth(depth)

	if image_path != "":
		var texture: Texture2D = _load_texture(_resolve_path(image_path, base_dir))
		if texture == null:
			return
		var sprite := Sprite2D.new()
		sprite.name = "SetProp"
		sprite.texture = texture
		sprite.centered = true
		var texture_size: Vector2 = texture.get_size()
		var size_guard: float = 1.0
		if texture_size.x > 0.0 and texture_size.y > 0.0:
			size_guard = minf(1.0, minf(460.0 / texture_size.x, 340.0 / texture_size.y))
		var final_scale: float = prop_scale * size_guard
		sprite.scale = Vector2.ONE * final_scale
		sprite.position = Vector2(x_px, _ground_y_px - texture_size.y * final_scale * 0.5)
		layer.add_child(sprite)
		_prop_node = sprite
		return

	if kind not in ["console_desk", "telemetry_screen", "workbench"]:
		return
	var art := PropArt.new()
	art.name = "SetProp"
	art.configure(kind)
	art.position = Vector2(x_px, _ground_y_px)
	art.scale = Vector2.ONE * prop_scale
	layer.add_child(art)
	_prop_node = art


func _clear_prop() -> void:
	if _prop_node == null or not is_instance_valid(_prop_node):
		_prop_node = null
		return
	var parent: Node = _prop_node.get_parent()
	if parent != null:
		parent.remove_child(_prop_node)
	_prop_node.queue_free()
	_prop_node = null


func _layer_for_depth(depth: String) -> Node2D:
	match depth:
		"far":
			return _far_layer
		"near":
			return _near_prop_layer
		_:
			return _mid_layer


func _horizontal_to_pixels(value: float) -> float:
	if value >= -0.5 and value <= 1.5:
		return value * VIEW_SIZE.x
	return value
