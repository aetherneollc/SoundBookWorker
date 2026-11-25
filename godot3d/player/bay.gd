extends Node3D

## Multi-template modular 3D scene builder for stage3d.json.
##
## Supported `set.scene_type` templates:
##   - "observation_bay" (default; full backward compatibility with lunar observation bay,
##                        Kolors floor/wall/console textures, and curved lunar cyclorama)
##   - "corridor"        (ribbed sci-fi transit corridor with overhead pool fixtures & end doors)
##   - "med_lab"         (clinical medical/research laboratory with teal accents & exam equipment)
##   - "command_deck"    (tactical command bridge with central holo-table, consoles & viewport)
##
## Units are metres, +Y up, front window wall faces +Z at z = room_min.z.

const LightingProfileScript := preload("res://player/lighting_profiles.gd")

const WALL := 0.2
const DEFAULT_SCENE_TYPE := "observation_bay"
const SCENE_DEFAULT_LIGHTING := {
	"observation_bay": "day_bay",
	"corridor": "dim_corridor",
	"med_lab": "med_lab",
	"command_deck": "day_bay",
}

## x, z, radius, depth of the bowls pressed into the ground outside.
const CRATERS: Array[Vector4] = [
	Vector4(9.0, -24.0, 6.5, 1.4),
	Vector4(-14.0, -38.0, 9.0, 1.8),
	Vector4(3.0, -60.0, 12.0, 2.2),
	Vector4(-30.0, -18.0, 5.0, 1.0),
	Vector4(26.0, -46.0, 10.0, 2.0),
]

const PLANET_SHADER := """
shader_type spatial;
render_mode unshaded, cull_back, fog_disabled;

uniform vec3 sun_dir = vec3(0.55, 0.3, 0.78);
uniform vec4 light_color : source_color = vec4(0.93, 0.96, 1.0, 1.0);
uniform vec4 sea_color : source_color = vec4(0.5, 0.69, 0.85, 1.0);
uniform vec4 shadow_color : source_color = vec4(0.31, 0.5, 0.71, 1.0);
varying vec3 obj_n;

float hash(vec3 p) {
	p = fract(p * 0.3183099 + 0.1);
	p *= 17.0;
	return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}

float vnoise(vec3 x) {
	vec3 i = floor(x);
	vec3 f = fract(x);
	f = f * f * (3.0 - 2.0 * f);
	return mix(
		mix(mix(hash(i), hash(i + vec3(1, 0, 0)), f.x), mix(hash(i + vec3(0, 1, 0)), hash(i + vec3(1, 1, 0)), f.x), f.y),
		mix(mix(hash(i + vec3(0, 0, 1)), hash(i + vec3(1, 0, 1)), f.x), mix(hash(i + vec3(0, 1, 1)), hash(i + vec3(1, 1, 1)), f.x), f.y),
		f.z);
}

void vertex() {
	obj_n = normalize(VERTEX);
}

void fragment() {
	vec3 wn = normalize((INV_VIEW_MATRIX * vec4(NORMAL, 0.0)).xyz);
	float n = vnoise(obj_n * 3.5) * 0.55 + vnoise(obj_n * 9.0) * 0.3 + vnoise(obj_n * 24.0) * 0.15;
	float craters = smoothstep(0.62, 0.7, vnoise(obj_n * 14.0 + 3.1));
	vec3 surface = mix(sea_color.rgb, light_color.rgb, smoothstep(0.38, 0.72, n));
	surface = mix(surface, sea_color.rgb * 0.92, craters * 0.5);
	float lit = smoothstep(-0.2, 0.35, dot(wn, normalize(sun_dir)));
	ALBEDO = mix(shadow_color.rgb * mix(0.85, 1.0, n), surface, lit);
}
"""

var scene_type := DEFAULT_SCENE_TYPE
var lighting_profile_name := "day_bay"
var room_min := Vector3(-7.0, 0.0, -5.0)
var room_max := Vector3(7.0, 3.4, 5.0)
var door_x := 0.0
var door_width := 1.6
var door_height := 2.4
var corridor_depth := 3.0
var window_x := Vector2(-5.5, 5.5)
var window_y := Vector2(0.95, 3.0)
var camera_min := Vector3(-6.7, 0.25, -4.7)
var camera_max := Vector3(6.7, 3.15, 4.75)
var corridor_camera_max_z := 7.6
var props_loaded := 0
var props_failed := 0

var _doors: Array[Dictionary] = []
var _windows: Array[Dictionary] = []
var _has_front_window := true
var _materials: Dictionary = {}
var _character_script: Script
var _ground_y := -2.4
var _palette: Dictionary = {}
var _ridge_noise: FastNoiseLite
var _hill_noise: FastNoiseLite
var _grain_noise: FastNoiseLite
var _surfaces: Dictionary = {}
var _cyclorama := false
var _sky_color := Color("#1d3f86")
var _lighting_rig: Node3D
var _obstacle_aabbs: Array[AABB] = []
var _doorframe_aabbs: Array[AABB] = []
var _interactive_devices: Array[Dictionary] = []  # BUG-010 anchors
var _dynamic_screens: Array[Dictionary] = []      # BUG-016 live screen materials
var _screen_textures: Dictionary = {}             # "kind:mode" -> ImageTexture
var _alert_strips: Array[StandardMaterial3D] = [] # BUG-012 alert beacon strips
var _storm_band: MeshInstance3D = null            # BUG-012 storm front mesh
var _last_event_screen_mode := ""


func build(set_cfg: Dictionary, character_script: Script) -> void:
	_character_script = character_script
	_obstacle_aabbs.clear()
	_doorframe_aabbs.clear()
	_interactive_devices.clear()
	_dynamic_screens.clear()
	_screen_textures.clear()
	_alert_strips.clear()
	_storm_band = null
	_last_event_screen_mode = ""
	props_loaded = 0
	props_failed = 0

	scene_type = _resolve_scene_type(set_cfg)

	var room: Dictionary = set_cfg.get("room", {}) as Dictionary if set_cfg.get("room", {}) is Dictionary else {}
	room_min = _vec3(room.get("min", null), _default_room_min(scene_type))
	room_max = _vec3(room.get("max", null), _default_room_max(scene_type))

	_parse_doors(set_cfg)
	_parse_windows(set_cfg)

	var bounds: Dictionary = set_cfg.get("camera_bounds", {}) as Dictionary if set_cfg.get("camera_bounds", {}) is Dictionary else {}
	camera_min = _vec3(bounds.get("min", null), Vector3(room_min.x + 0.35, 0.25, room_min.z + 0.35))
	camera_max = _vec3(bounds.get("max", null), Vector3(room_max.x - 0.35, room_max.y - 0.25, room_max.z - 0.35))
	corridor_camera_max_z = float(bounds.get("corridor_max_z", room_max.z + corridor_depth - 0.4))

	_surfaces = set_cfg.get("surfaces", {}) as Dictionary if set_cfg.get("surfaces", {}) is Dictionary else {}
	_build_materials()

	match scene_type:
		"corridor":
			_build_corridor_template()
		"med_lab":
			_build_med_lab_template()
		"command_deck":
			_build_command_deck_template()
		_:
			_build_observation_bay_template()

	var backdrop: Dictionary = set_cfg.get("backdrop", {}) as Dictionary if set_cfg.get("backdrop", {}) is Dictionary else {}
	if _has_front_window or not backdrop.is_empty():
		_build_outside(backdrop, set_cfg.get("outside_props", []))

	_build_props(set_cfg.get("props", []))
	_ingest_explicit_obstacles(set_cfg.get("obstacles", []))

	if _surfaces.get("console_screen", null) is Dictionary:
		_build_console_screen(_surfaces["console_screen"] as Dictionary)

	var profile_raw: String = str(set_cfg.get("lighting_profile", "")).strip_edges()
	lighting_profile_name = profile_raw if profile_raw != "" else str(SCENE_DEFAULT_LIGHTING.get(scene_type, "day_bay"))
	var lighting_cfg: Dictionary = set_cfg.get("lighting", {}) as Dictionary if set_cfg.get("lighting", {}) is Dictionary else {}
	_build_lighting(lighting_profile_name, lighting_cfg)


func get_lighting_rig() -> Node3D:
	return _lighting_rig


func get_obstacle_aabbs() -> Array[AABB]:
	var out: Array[AABB] = []
	out.append_array(_obstacle_aabbs)
	return out


func update_lighting(time_sec: float) -> void:
	LightingProfileScript.update_time(_lighting_rig, time_sec)


func set_camera_dof(focus_distance: float, enabled: bool, blur_amount: float = 0.08) -> void:
	LightingProfileScript.set_dof_focus(_lighting_rig, focus_distance, enabled, blur_amount)


## Keeps a camera inside the room, or inside the doorway corridor.
func clamp_camera(position: Vector3) -> Vector3:
	var p := position
	var half: float = door_width * 0.5 - 0.22
	if corridor_depth > 0.1 and p.z > camera_max.z and absf(p.x - door_x) <= half + 0.4:
		p.x = clampf(p.x, door_x - half, door_x + half)
		p.y = clampf(p.y, camera_min.y, door_height - 0.2)
		p.z = minf(p.z, corridor_camera_max_z)
		return p
	p.x = clampf(p.x, camera_min.x, camera_max.x)
	p.y = clampf(p.y, camera_min.y, camera_max.y)
	p.z = clampf(p.z, camera_min.z, camera_max.z)
	return p


## Returns true when `cam_pos` has safe wall/doorframe/obstacle clearance and
## an unobstructed line of sight to `look_target`.
func is_camera_clear(cam_pos: Vector3, look_target: Vector3, wall_margin: float = 0.35) -> bool:
	var in_room: bool = (
		cam_pos.x >= room_min.x + wall_margin
		and cam_pos.x <= room_max.x - wall_margin
		and cam_pos.y >= room_min.y + 0.2
		and cam_pos.y <= room_max.y - 0.2
		and cam_pos.z >= room_min.z + wall_margin
		and cam_pos.z <= room_max.z - wall_margin
	)
	var in_corridor := false
	if not in_room and corridor_depth > 0.2:
		var half: float = maxf(door_width * 0.5 - wall_margin, 0.1)
		in_corridor = (
			absf(cam_pos.x - door_x) <= half
			and cam_pos.y >= room_min.y + 0.2
			and cam_pos.y <= door_height - 0.2
			and cam_pos.z >= room_max.z + wall_margin * 0.5
			and cam_pos.z <= room_max.z + WALL + corridor_depth - wall_margin
		)
	if not in_room and not in_corridor:
		return false

	for jamb: AABB in _doorframe_aabbs:
		if jamb.grow(wall_margin).has_point(cam_pos):
			return false
		if _segment_intersects_aabb(cam_pos, look_target, jamb.grow(0.05), 0.15):
			return false

	for obs: AABB in _obstacle_aabbs:
		if obs.grow(0.18).has_point(cam_pos):
			return false
		# Trim the last 0.35m near look_target so aiming at a prop on a desk isn't false-occluded.
		if _segment_intersects_aabb(cam_pos, look_target, obs, 0.35):
			return false

	return true


func _segment_intersects_aabb(a: Vector3, b: Vector3, box: AABB, end_trim_m: float = 0.0) -> bool:
	var delta: Vector3 = b - a
	var seg_len: float = delta.length()
	if seg_len <= 0.001:
		return box.has_point(a)
	var t_max_limit: float = clampf((seg_len - end_trim_m) / seg_len, 0.0, 1.0)
	if t_max_limit <= 0.01:
		return false
	var t_min := 0.0
	var t_max := t_max_limit
	var lo: Vector3 = box.position
	var hi: Vector3 = box.end
	for axis in 3:
		var orig: float = a[axis]
		var dir: float = delta[axis]
		if absf(dir) < 1e-6:
			if orig < lo[axis] or orig > hi[axis]:
				return false
		else:
			var inv_d: float = 1.0 / dir
			var t1: float = (lo[axis] - orig) * inv_d
			var t2: float = (hi[axis] - orig) * inv_d
			if t1 > t2:
				var tmp: float = t1
				t1 = t2
				t2 = tmp
			t_min = maxf(t_min, t1)
			t_max = minf(t_max, t2)
			if t_min > t_max:
				return false
	return t_max >= 0.0 and t_min <= t_max_limit


func _resolve_scene_type(set_cfg: Dictionary) -> String:
	var raw: String = str(set_cfg.get("scene_type", "")).strip_edges().to_lower()
	if raw in ["observation_bay", "corridor", "med_lab", "command_deck"]:
		return raw
	var kind: String = str(set_cfg.get("kind", "")).strip_edges().to_lower()
	if kind.find("corridor") >= 0:
		return "corridor"
	if kind.find("med") >= 0 or kind.find("lab") >= 0:
		return "med_lab"
	if kind.find("command") >= 0 or kind.find("bridge") >= 0:
		return "command_deck"
	return DEFAULT_SCENE_TYPE


func _default_room_min(kind: String) -> Vector3:
	match kind:
		"corridor":
			return Vector3(-2.4, 0.0, -7.5)
		"med_lab":
			return Vector3(-5.5, 0.0, -4.5)
		"command_deck":
			return Vector3(-7.0, 0.0, -5.5)
		_:
			return Vector3(-7.0, 0.0, -5.0)


func _default_room_max(kind: String) -> Vector3:
	match kind:
		"corridor":
			return Vector3(2.4, 3.0, 7.5)
		"med_lab":
			return Vector3(5.5, 3.2, 4.5)
		"command_deck":
			return Vector3(7.0, 3.8, 5.0)
		_:
			return Vector3(7.0, 3.4, 5.0)


func _parse_doors(set_cfg: Dictionary) -> void:
	_doors.clear()
	var doors_v: Variant = set_cfg.get("doors", null)
	if doors_v is Array and not (doors_v as Array).is_empty():
		for item: Variant in doors_v as Array:
			if item is Dictionary:
				_doors.append((item as Dictionary).duplicate())
	var door_v: Variant = set_cfg.get("door", {})
	var door_dict: Dictionary = door_v as Dictionary if door_v is Dictionary else {}
	if _doors.is_empty():
		_doors.append({
			"wall": str(door_dict.get("wall", "back")),
			"x": float(door_dict.get("x", door_x)),
			"width": float(door_dict.get("width", door_width)),
			"height": float(door_dict.get("height", door_height)),
			"corridor_depth": float(door_dict.get("corridor_depth", corridor_depth)),
		})
	var primary: Dictionary = _doors[0]
	for d: Dictionary in _doors:
		if str(d.get("wall", "back")) == "back":
			primary = d
			break
	door_x = float(primary.get("x", primary.get("pos", door_x)))
	door_width = float(primary.get("width", door_width))
	door_height = float(primary.get("height", door_height))
	corridor_depth = float(primary.get("corridor_depth", corridor_depth))


func _parse_windows(set_cfg: Dictionary) -> void:
	_windows.clear()
	var wins_v: Variant = set_cfg.get("windows", null)
	if wins_v is Array:
		for item: Variant in wins_v as Array:
			if item is Dictionary:
				_windows.append((item as Dictionary).duplicate())
	var win_v: Variant = set_cfg.get("window", null)
	if win_v is Dictionary and not (win_v as Dictionary).is_empty():
		var w: Dictionary = win_v as Dictionary
		window_x = Vector2(float(w.get("x_min", window_x.x)), float(w.get("x_max", window_x.y)))
		window_y = Vector2(float(w.get("y_min", window_y.x)), float(w.get("y_max", window_y.y)))
		_has_front_window = bool(w.get("enabled", true)) and window_x.y > window_x.x + 0.2
		if _windows.is_empty() and _has_front_window:
			_windows.append({"wall": "front", "x_min": window_x.x, "x_max": window_x.y, "y_min": window_y.x, "y_max": window_y.y})
	else:
		_has_front_window = scene_type in ["observation_bay", "command_deck"]
		if _has_front_window:
			var margin: float = 1.5
			window_x = Vector2(room_min.x + margin, room_max.x - margin)
			window_y = Vector2(0.95, minf(room_max.y - 0.4, 3.0))
			if _windows.is_empty():
				_windows.append({"wall": "front", "x_min": window_x.x, "x_max": window_x.y, "y_min": window_y.x, "y_max": window_y.y})
		elif not _windows.is_empty():
			for w: Dictionary in _windows:
				if str(w.get("wall", "")) == "front":
					_has_front_window = true
					window_x = Vector2(float(w.get("x_min", -3.0)), float(w.get("x_max", 3.0)))
					window_y = Vector2(float(w.get("y_min", 1.0)), float(w.get("y_max", 2.6)))
					break


func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback


# ------------------------------------------------------------ materials


func _mat(color: Color, roughness: float = 0.9, emission: float = 0.0, metallic: float = 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = roughness
	material.metallic = metallic
	material.metallic_specular = 0.28
	if emission > 0.0:
		material.emission_enabled = true
		material.emission = color
		material.emission_energy_multiplier = emission
	return material


func _glass_mat(color: Color) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	material.roughness = 0.12
	material.metallic_specular = 0.7
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	return material


func _build_materials() -> void:
	_materials.clear()
	_materials["floor"] = _surface_material("floor", Color.WHITE)
	_materials["wall"] = _surface_material("wall", Color.WHITE)
	var wall_cfg: Dictionary = _surfaces.get("wall", {}) as Dictionary if _surfaces.get("wall", {}) is Dictionary else {}
	_materials["wall_low"] = _surface_material("wall", Color(str(wall_cfg.get("low_tint", "#c4cad6"))))
	_materials["corridor"] = _surface_material("wall", Color("#d6deea"))

	match scene_type:
		"med_lab":
			# BUG-014: Distinct cool-slate floor, darker wainscoting, brushed aluminum frames
			# and deeper teal accents give clinical white rooms clear depth & silhouette separation.
			if _materials["floor"] == null:
				_materials["floor"] = _tile_material(Color("#b6c8d6"), Color("#8ca3b5"), 0.85)
			if _materials["wall"] == null:
				_materials["wall"] = _panel_material(Color("#d5e1e8"), Color("#aebfcf"), 0.72, 0.04)
			if _materials["wall_low"] == null:
				_materials["wall_low"] = _panel_material(Color("#9bb2c2"), Color("#7892a6"), 0.76, 0.08)
			if _materials["corridor"] == null:
				_materials["corridor"] = _panel_material(Color("#b2c4d0"), Color("#8ca2b2"), 0.75, 0.06)
			_materials["panel_line"] = _mat(Color("#7690a3"), 0.7)
			_materials["ledge"] = _mat(Color("#7e98aa"), 0.52, 0.0, 0.16)
			_materials["frame"] = _mat(Color("#364d5e"), 0.52, 0.0, 0.28)
			_materials["ceiling"] = _mat(Color("#bccad4"), 0.82)
			_materials["beam"] = _mat(Color("#889fae"), 0.62, 0.0, 0.15)
			_materials["light_bar"] = _mat(Color("#eaf7ff"), 0.35, 1.55)
			_materials["trim"] = _mat(Color("#1fa89e"), 0.48, 0.28)
		"corridor":
			# BUG-013: Mid-slate ribbed bulkhead panels with brighter trim so dark uniforms
			# separate cleanly from walls without washing out moody corridor lighting.
			if _materials["floor"] == null:
				_materials["floor"] = _tile_material(Color("#627082"), Color("#434f5e"), 1.0)
			if _materials["wall"] == null:
				_materials["wall"] = _panel_material(Color("#445870"), Color("#2c3c4f"), 0.76, 0.12)
			if _materials["wall_low"] == null:
				_materials["wall_low"] = _panel_material(Color("#34465c"), Color("#223040"), 0.80, 0.14)
			if _materials["corridor"] == null:
				_materials["corridor"] = _panel_material(Color("#3c4e64"), Color("#273647"), 0.78, 0.12)
			_materials["panel_line"] = _mat(Color("#1f2c3c"), 0.72)
			_materials["ledge"] = _mat(Color("#6c829b"), 0.58, 0.0, 0.18)
			_materials["frame"] = _mat(Color("#2a3748"), 0.52, 0.0, 0.28)
			_materials["ceiling"] = _mat(Color("#1d2633"), 0.85)
			_materials["beam"] = _mat(Color("#374659"), 0.60, 0.0, 0.22)
			_materials["light_bar"] = _mat(Color("#e4f0ff"), 0.35, 2.0)
			_materials["trim"] = _mat(Color("#e6933a"), 0.52, 0.24)
		"command_deck":
			if _materials["floor"] == null:
				_materials["floor"] = _tile_material(Color("#4c5868"), Color("#303a48"), 1.0)
			if _materials["wall"] == null:
				_materials["wall"] = _panel_material(Color("#2d425b"), Color("#1c2c40"), 0.74, 0.15)
			if _materials["wall_low"] == null:
				_materials["wall_low"] = _panel_material(Color("#22344a"), Color("#162334"), 0.78, 0.16)
			if _materials["corridor"] == null:
				_materials["corridor"] = _panel_material(Color("#314660"), Color("#1f3045"), 0.76, 0.14)
			_materials["panel_line"] = _mat(Color("#162130"), 0.7)
			_materials["ledge"] = _mat(Color("#768ba3"), 0.52, 0.0, 0.22)
			_materials["frame"] = _mat(Color("#1e2d3f"), 0.52, 0.0, 0.28)
			_materials["ceiling"] = _mat(Color("#161d28"), 0.85)
			_materials["beam"] = _mat(Color("#2c394b"), 0.58, 0.0, 0.24)
			_materials["light_bar"] = _mat(Color("#dff0ff"), 0.35, 1.8)
			_materials["trim"] = _mat(Color("#e89232"), 0.52, 0.24)
		_:
			if _materials["floor"] == null:
				_materials["floor"] = _tile_material(Color("#aab5bf"), Color("#87939f"), 1.0)
			if _materials["wall"] == null:
				_materials["wall"] = _panel_material(Color("#2e5072"), Color("#1f3954"), 0.76, 0.14)
			if _materials["wall_low"] == null:
				_materials["wall_low"] = _panel_material(Color("#243f5e"), Color("#182c44"), 0.80, 0.14)
			if _materials["corridor"] == null:
				_materials["corridor"] = _panel_material(Color("#355575"), Color("#243d57"), 0.78, 0.12)
			_materials["panel_line"] = _mat(Color("#1a2c44"))
			_materials["ledge"] = _mat(Color("#c98a45"), 0.65, 0.0, 0.14)
			_materials["frame"] = _mat(Color("#1f3550"), 0.56, 0.0, 0.22)
			_materials["ceiling"] = _mat(Color("#1b222d"))
			_materials["beam"] = _mat(Color("#2c3847"), 0.62, 0.0, 0.20)
			_materials["light_bar"] = _mat(Color("#fff3e0"), 0.4, 2.2)
			_materials["trim"] = _mat(Color("#e8892e"), 0.56, 0.16)

	# Shared semantic furniture & exterior materials (BUG-015: subtle brushed panel detail
	# so consoles/beds/cabinets match architectural walls instead of flat plastic).
	_materials["exterior"] = _mat(Color("#2b3440"), 0.7)
	_materials["exterior_lit"] = _mat(Color("#e9a24a"), 0.5, 1.6)
	_materials["ground"] = _tile_material(Color("#c9cfd8"), Color("#b6bec9"), 0.0)
	_materials["crate"] = _panel_material(Color("#cf782c"), Color("#9a5318"), 0.74, 0.08)
	_materials["crate_dark"] = _panel_material(Color("#3b4657"), Color("#262f3c"), 0.76, 0.16)
	_materials["metal_dark"] = _panel_material(Color("#2f3b4a"), Color("#1e2733"), 0.54, 0.28)
	_materials["metal_light"] = _panel_material(Color("#8ca0b0") if scene_type == "med_lab" else Color("#9aa9b8"), Color("#6b7f90"), 0.50, 0.24)
	_materials["medical_white"] = _panel_material(Color("#d9e3ea"), Color("#b2c4d0"), 0.60, 0.06)
	_materials["medical_pad"] = _panel_material(Color("#4f8396"), Color("#396678"), 0.78, 0.02)
	_materials["teal_glow"] = _mat(Color("#2fd1c4"), 0.38, 1.05)
	_materials["cyan_glow"] = _mat(Color("#38b8ea"), 0.38, 1.05)
	_materials["amber_glow"] = _mat(Color("#f29d35"), 0.42, 1.15)
	_materials["holo_glass"] = _glass_mat(Color(0.24, 0.78, 0.96, 0.28))
	_materials["glass_partition"] = _glass_mat(Color(0.68, 0.86, 0.92, 0.22))


func _surface_material(key: String, tint: Color) -> StandardMaterial3D:
	var cfg: Dictionary = _surfaces.get(key, {}) as Dictionary if _surfaces.get(key, {}) is Dictionary else {}
	var texture: Texture2D = _load_texture(str(cfg.get("texture", "")))
	if texture == null:
		return null
	var material := StandardMaterial3D.new()
	material.albedo_texture = texture
	material.albedo_color = Color(str(cfg.get("tint", "#ffffff"))) * tint
	material.roughness = float(cfg.get("roughness", 0.8))
	material.metallic = float(cfg.get("metallic", 0.0))
	material.metallic_specular = float(cfg.get("specular", 0.4))
	material.uv1_triplanar = true
	material.uv1_world_triplanar = true
	material.uv1_triplanar_sharpness = 8.0
	material.uv1_scale = Vector3.ONE / maxf(float(cfg.get("tile_m", 2.5)), 0.1)
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	return material


func _load_texture(path: String) -> Texture2D:
	if path == "" or not FileAccess.file_exists(path):
		return null
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return null
	image.generate_mipmaps()
	return ImageTexture.create_from_image(image)


func _tile_material(base: Color, grout: Color, grout_weight: float) -> StandardMaterial3D:
	var size := 256
	var image := Image.create(size, size, true, Image.FORMAT_RGB8)
	for y in size:
		for x in size:
			var c: Color = base
			var edge: bool = x < 3 or y < 3
			var speck: float = float((x * 73856093) ^ (y * 19349663)) / 4294967296.0
			c = c.darkened(fposmod(speck, 1.0) * 0.035)
			if edge and grout_weight > 0.0:
				c = c.lerp(grout, grout_weight)
			image.set_pixel(x, y, c)
	image.generate_mipmaps()
	var material := StandardMaterial3D.new()
	material.albedo_texture = ImageTexture.create_from_image(image)
	material.roughness = 0.85
	material.metallic_specular = 0.3
	material.uv1_triplanar = true
	material.uv1_world_triplanar = true
	material.uv1_scale = Vector3(0.5, 0.5, 0.5)
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	return material


func _box(label: String, size: Vector3, center: Vector3, material_key: String, shadows: bool = true) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = label
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	mi.position = center
	mi.material_override = _materials[material_key]
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if shadows else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(mi)
	return mi


func _box_between(label: String, a: Vector3, b: Vector3, material_key: String, shadows: bool = true) -> MeshInstance3D:
	var lo := Vector3(minf(a.x, b.x), minf(a.y, b.y), minf(a.z, b.z))
	var hi := Vector3(maxf(a.x, b.x), maxf(a.y, b.y), maxf(a.z, b.z))
	return _box(label, hi - lo, (lo + hi) * 0.5, material_key, shadows)


func _register_doorframe(a: Vector3, b: Vector3) -> void:
	var lo := Vector3(minf(a.x, b.x), minf(a.y, b.y), minf(a.z, b.z))
	var hi := Vector3(maxf(a.x, b.x), maxf(a.y, b.y), maxf(a.z, b.z))
	_doorframe_aabbs.append(AABB(lo, hi - lo))


func _register_obstacle(lo: Vector3, hi: Vector3) -> void:
	var mn := Vector3(minf(lo.x, hi.x), minf(lo.y, hi.y), minf(lo.z, hi.z))
	var mx := Vector3(maxf(lo.x, hi.x), maxf(lo.y, hi.y), maxf(lo.z, hi.z))
	if (mx - mn).length_squared() > 1e-6:
		_obstacle_aabbs.append(AABB(mn, mx - mn))


# ----------------------------------------------------------- templates


func _build_observation_bay_template() -> void:
	_build_shell()
	_build_corridor_stub()
	_build_window_wall()
	_build_ceiling()


func _build_corridor_template() -> void:
	var h: float = room_max.y
	_box_between("Floor", Vector3(room_min.x - WALL, -0.2, room_min.z - WALL), Vector3(room_max.x + WALL, 0.0, room_max.z + WALL), "floor")
	_box_between("Ceiling", Vector3(room_min.x - WALL, h, room_min.z - WALL), Vector3(room_max.x + WALL, h + 0.2, room_max.z + WALL), "ceiling")
	# Floor safety guide strips running along corridor center lane.
	var lane_half: float = minf((room_max.x - room_min.x) * 0.26, 0.72)
	for side: float in [-1.0, 1.0]:
		var sx: float = side * lane_half
		_box_between("FloorStripe%d" % int(side), Vector3(sx - 0.03, 0.002, room_min.z + 0.5), Vector3(sx + 0.03, 0.006, room_max.z - 0.5), "trim", false)

	# Side walls with structural bulkhead ribs & recessed wall light strips.
	for side: float in [-1.0, 1.0]:
		var x: float = room_min.x if side < 0.0 else room_max.x
		var outer: float = x + side * WALL
		_box_between("SideWall%d" % int(side), Vector3(x, 0.0, room_min.z), Vector3(outer, h, room_max.z), "wall")
		_box_between("SideLow%d" % int(side), Vector3(x, 0.0, room_min.z + 0.2), Vector3(x - side * 0.05, 0.92, room_max.z - 0.2), "wall_low")
		_box_between("SideTrim%d" % int(side), Vector3(x - side * 0.02, 0.92, room_min.z + 0.2), Vector3(x - side * 0.06, 0.97, room_max.z - 0.2), "trim", false)
		var rz: float = room_min.z + 1.5
		while rz < room_max.z - 0.8:
			_box_between("Rib%d_%d" % [int(side), int(rz * 10.0)], Vector3(x - side * 0.14, 0.0, rz - 0.1), Vector3(x, h, rz + 0.1), "beam")
			_box_between("CeilArch_%d" % int(rz * 10.0), Vector3(room_min.x, h - 0.22, rz - 0.1), Vector3(room_max.x, h, rz + 0.1), "beam")
			rz += 2.6

	# Overhead recessed pool light fixtures down the center axis.
	var lz: float = room_min.z + 1.8
	while lz < room_max.z - 1.0:
		_box_between("CorridorFixture_%d" % int(lz * 10.0), Vector3(-0.45, h - 0.06, lz - 0.4), Vector3(0.45, h - 0.01, lz + 0.4), "light_bar", false)
		lz += 3.2

	# Front bulkhead wall (with optional front doorway or closed bulkhead).
	var front_door: Dictionary = _find_door("front")
	if not front_door.is_empty():
		_build_wall_with_door("Front", room_min.z - WALL, room_min.z, front_door)
	else:
		_box_between("FrontWall", Vector3(room_min.x - WALL, 0.0, room_min.z - WALL), Vector3(room_max.x + WALL, h, room_min.z), "wall")
		_box_between("FrontBulkheadFrame", Vector3(-0.95, 0.0, room_min.z), Vector3(0.95, 2.35, room_min.z + 0.06), "frame")
		_box_between("FrontStatusBar", Vector3(-0.35, 2.45, room_min.z + 0.01), Vector3(0.35, 2.55, room_min.z + 0.05), "cyan_glow", false)

	# Back wall with entry door + optional corridor stub.
	_build_back_wall_with_door()
	if corridor_depth > 0.2:
		_build_corridor_stub()


func _build_med_lab_template() -> void:
	var h: float = room_max.y
	_box_between("Floor", Vector3(room_min.x - WALL, -0.2, room_min.z - WALL), Vector3(room_max.x + WALL, 0.0, room_max.z + WALL), "floor")
	_box_between("Ceiling", Vector3(room_min.x - WALL, h, room_min.z - WALL), Vector3(room_max.x + WALL, h + 0.2, room_max.z + WALL), "ceiling")

	# Clean sanitary side walls with teal dado accent band & sterile ceiling light panels.
	for side: float in [-1.0, 1.0]:
		var x: float = room_min.x if side < 0.0 else room_max.x
		var outer: float = x + side * WALL
		_box_between("SideWall%d" % int(side), Vector3(x, 0.0, room_min.z), Vector3(outer, h, room_max.z), "wall")
		_box_between("SideLow%d" % int(side), Vector3(x, 0.0, room_min.z + 0.2), Vector3(x - side * 0.04, 1.05, room_max.z - 0.2), "wall_low")
		_box_between("SideTealBand%d" % int(side), Vector3(x - side * 0.02, 1.05, room_min.z + 0.2), Vector3(x - side * 0.05, 1.13, room_max.z - 0.2), "trim", false)

	# Front wall: either glass observation window or clinical diagnostic backdrop wall.
	if _has_front_window:
		_build_window_wall()
	else:
		_box_between("FrontWall", Vector3(room_min.x - WALL, 0.0, room_min.z - WALL), Vector3(room_max.x + WALL, h, room_min.z), "wall")
		_box_between("FrontLow", Vector3(room_min.x + 0.2, 0.0, room_min.z), Vector3(room_max.x - 0.2, 1.05, room_min.z + 0.04), "wall_low")
		_box_between("FrontTealBand", Vector3(room_min.x + 0.2, 1.05, room_min.z + 0.01), Vector3(room_max.x - 0.2, 1.13, room_min.z + 0.05), "trim", false)
		# Clean recessed diagnostic light strip along front header.
		_box_between("FrontDiagLight", Vector3(room_min.x + 1.2, h - 0.38, room_min.z + 0.02), Vector3(room_max.x - 1.2, h - 0.28, room_min.z + 0.06), "teal_glow", false)

	# Back wall with entry door + corridor stub.
	_build_back_wall_with_door()
	if corridor_depth > 0.2:
		_build_corridor_stub()

	# Clean bright ceiling light panels in a 2x2 clinical grid.
	var cx: float = (room_min.x + room_max.x) * 0.5
	var cz: float = (room_min.z + room_max.z) * 0.5
	var dx: float = (room_max.x - room_min.x) * 0.24
	var dz: float = (room_max.z - room_min.z) * 0.24
	for ix: float in [-1.0, 1.0]:
		for iz: float in [-1.0, 1.0]:
			var px: float = cx + ix * dx
			var pz: float = cz + iz * dz
			_box_between("LabPanelFrame_%d_%d" % [int(ix), int(iz)], Vector3(px - 0.95, h - 0.08, pz - 0.55), Vector3(px + 0.95, h, pz + 0.55), "beam")
			_box_between("LabPanelLight_%d_%d" % [int(ix), int(iz)], Vector3(px - 0.85, h - 0.1, pz - 0.45), Vector3(px + 0.85, h - 0.06, pz + 0.45), "light_bar", false)


func _build_command_deck_template() -> void:
	var h: float = room_max.y
	_box_between("Floor", Vector3(room_min.x - WALL, -0.2, room_min.z - WALL), Vector3(room_max.x + WALL, 0.0, room_max.z + WALL), "floor")
	_box_between("Ceiling", Vector3(room_min.x - WALL, h, room_min.z - WALL), Vector3(room_max.x + WALL, h + 0.2, room_max.z + WALL), "ceiling")

	# Side armored bulkheads with recessed tactical light bands.
	for side: float in [-1.0, 1.0]:
		var x: float = room_min.x if side < 0.0 else room_max.x
		var outer: float = x + side * WALL
		_box_between("SideWall%d" % int(side), Vector3(x, 0.0, room_min.z), Vector3(outer, h, room_max.z), "wall")
		_box_between("SideLow%d" % int(side), Vector3(x, 0.0, room_min.z + 0.3), Vector3(x - side * 0.06, 1.0, room_max.z - 0.3), "wall_low")
		_box_between("SideStrip%d" % int(side), Vector3(x - side * 0.02, h - 0.36, room_min.z + 0.5), Vector3(x - side * 0.08, h - 0.28, room_max.z - 0.5), "cyan_glow", false)

	if _has_front_window:
		_build_window_wall()
	else:
		_box_between("FrontWall", Vector3(room_min.x - WALL, 0.0, room_min.z - WALL), Vector3(room_max.x + WALL, h, room_min.z), "wall")

	_build_back_wall_with_door()
	if corridor_depth > 0.2:
		_build_corridor_stub()

	# Tactical ceiling truss & halo lighting ring over command center.
	var z: float = room_min.z + 1.2
	while z < room_max.z - 0.6:
		_box_between("DeckBeam%d" % int(z * 10.0), Vector3(room_min.x, h - 0.26, z - 0.14), Vector3(room_max.x, h, z + 0.14), "beam")
		z += 2.4
	var cx: float = (room_min.x + room_max.x) * 0.5
	var cz: float = (room_min.z + room_max.z) * 0.5
	_box_between("DeckLightN", Vector3(cx - 2.4, h - 0.3, cz - 1.8), Vector3(cx + 2.4, h - 0.24, cz - 1.62), "light_bar", false)
	_box_between("DeckLightS", Vector3(cx - 2.4, h - 0.3, cz + 1.62), Vector3(cx + 2.4, h - 0.24, cz + 1.8), "light_bar", false)
	_box_between("DeckLightW", Vector3(cx - 2.4, h - 0.3, cz - 1.62), Vector3(cx - 2.22, h - 0.24, cz + 1.62), "light_bar", false)
	_box_between("DeckLightE", Vector3(cx + 2.22, h - 0.3, cz - 1.62), Vector3(cx + 2.4, h - 0.24, cz + 1.62), "light_bar", false)


func _find_door(wall_name: String) -> Dictionary:
	for d: Dictionary in _doors:
		if str(d.get("wall", "back")) == wall_name:
			return d
	return {}


# ---------------------------------------------------------------- shell


func _build_shell() -> void:
	var h: float = room_max.y
	_box_between("Floor", Vector3(room_min.x - WALL, -0.2, room_min.z - WALL), Vector3(room_max.x + WALL, 0.0, room_max.z + WALL), "floor")
	for side: float in [-1.0, 1.0]:
		var x: float = room_min.x if side < 0.0 else room_max.x
		var outer: float = x + side * WALL
		_box_between("SideWall%d" % int(side), Vector3(x, 0.0, room_min.z), Vector3(outer, h, room_max.z), "wall")
		var inner: float = x - side * 0.04
		_box_between("SideLow%d" % int(side), Vector3(x, 0.0, room_min.z + 0.3), Vector3(inner, window_y.x, room_max.z - 0.3), "wall_low")
		var ledge_inner: float = x - side * 0.34
		_box_between("SideLedge%d" % int(side), Vector3(x, window_y.x - 0.03, room_min.z + 0.3), Vector3(ledge_inner, window_y.x + 0.03, room_max.z - 0.3), "ledge")
		var z := room_min.z + 1.2
		while z < room_max.z - 0.6 and not _surfaces.has("wall"):
			_box_between("SidePanel%d_%d" % [int(side), int(z * 10.0)], Vector3(x - side * 0.02, window_y.x + 0.15, z), Vector3(x - side * 0.06, h - 0.35, z + 0.05), "panel_line", false)
			z += 1.6
		_box_between("SideStrip%d" % int(side), Vector3(x - side * 0.02, h - 0.3, room_min.z + 0.4), Vector3(x - side * 0.07, h - 0.24, room_max.z - 0.4), "light_bar", false)
	_build_back_wall_with_door()


func _build_back_wall_with_door() -> void:
	var h: float = room_max.y
	var back: float = room_max.z
	var half: float = door_width * 0.5
	_box_between("BackWallL", Vector3(room_min.x - WALL, 0.0, back), Vector3(door_x - half, h, back + WALL), "wall")
	_box_between("BackWallR", Vector3(door_x + half, 0.0, back), Vector3(room_max.x + WALL, h, back + WALL), "wall")
	_box_between("BackWallTop", Vector3(door_x - half, door_height, back), Vector3(door_x + half, h, back + WALL), "wall")
	if room_min.x + 0.3 < door_x - half - 0.4:
		_box_between("BackLowL", Vector3(room_min.x + 0.3, 0.0, back - 0.04), Vector3(door_x - half - 0.4, window_y.x, back), "wall_low")
	if door_x + half + 0.4 < room_max.x - 0.3:
		_box_between("BackLowR", Vector3(door_x + half + 0.4, 0.0, back - 0.04), Vector3(room_max.x - 0.3, window_y.x, back), "wall_low")
	# Trims stand 3 cm proud of every wall face they touch, so no face is coplanar.
	var inset := 0.03
	var jl_a := Vector3(door_x - half - 0.12, 0.0, back - 0.06)
	var jl_b := Vector3(door_x - half + inset, door_height + 0.12, back + WALL + inset)
	var jr_a := Vector3(door_x + half - inset, 0.0, back - 0.06)
	var jr_b := Vector3(door_x + half + 0.12, door_height + 0.12, back + WALL + inset)
	var jt_a := Vector3(door_x - half - 0.12, door_height - inset, back - 0.06)
	var jt_b := Vector3(door_x + half + 0.12, door_height + 0.12, back + WALL + inset)
	_box_between("DoorTrimL", jl_a, jl_b, "trim")
	_box_between("DoorTrimR", jr_a, jr_b, "trim")
	_box_between("DoorTrimTop", jt_a, jt_b, "trim")
	_register_doorframe(jl_a, jl_b)
	_register_doorframe(jr_a, jr_b)
	_box_between("DoorSign", Vector3(door_x - 0.35, door_height + 0.25, back - 0.05), Vector3(door_x + 0.35, door_height + 0.45, back - 0.01), "light_bar", false)


func _build_wall_with_door(prefix: String, z_min: float, z_max: float, door_cfg: Dictionary) -> void:
	var h: float = room_max.y
	var dx: float = float(door_cfg.get("x", door_cfg.get("pos", 0.0)))
	var dw: float = float(door_cfg.get("width", 1.6))
	var dh: float = float(door_cfg.get("height", 2.4))
	var half: float = dw * 0.5
	_box_between("%sWallL" % prefix, Vector3(room_min.x - WALL, 0.0, z_min), Vector3(dx - half, h, z_max), "wall")
	_box_between("%sWallR" % prefix, Vector3(dx + half, 0.0, z_min), Vector3(room_max.x + WALL, h, z_max), "wall")
	_box_between("%sWallTop" % prefix, Vector3(dx - half, dh, z_min), Vector3(dx + half, h, z_max), "wall")
	var inset := 0.03
	var jl_a := Vector3(dx - half - 0.12, 0.0, z_min - inset)
	var jl_b := Vector3(dx - half + inset, dh + 0.12, z_max + 0.06)
	var jr_a := Vector3(dx + half - inset, 0.0, z_min - inset)
	var jr_b := Vector3(dx + half + 0.12, dh + 0.12, z_max + 0.06)
	_box_between("%sDoorTrimL" % prefix, jl_a, jl_b, "trim")
	_box_between("%sDoorTrimR" % prefix, jr_a, jr_b, "trim")
	_box_between("%sDoorTrimTop" % prefix, Vector3(dx - half - 0.12, dh - inset, z_min - inset), Vector3(dx + half + 0.12, dh + 0.12, z_max + 0.06), "trim")
	_register_doorframe(jl_a, jl_b)
	_register_doorframe(jr_a, jr_b)


func _build_corridor_stub() -> void:
	var half: float = door_width * 0.5 + 0.2
	var z0: float = room_max.z + WALL
	var z1: float = z0 + corridor_depth
	var ch: float = door_height + 0.25
	_box_between("CorridorFloor", Vector3(door_x - half, -0.2, z0), Vector3(door_x + half, 0.0, z1), "floor")
	_box_between("CorridorCeil", Vector3(door_x - half, ch, z0), Vector3(door_x + half, ch + 0.15, z1), "ceiling")
	_box_between("CorridorWallL", Vector3(door_x - half - WALL, 0.0, z0), Vector3(door_x - half, ch, z1), "corridor")
	_box_between("CorridorWallR", Vector3(door_x + half, 0.0, z0), Vector3(door_x + half + WALL, ch, z1), "corridor")
	_box_between("CorridorEnd", Vector3(door_x - half - WALL, 0.0, z1), Vector3(door_x + half + WALL, ch, z1 + WALL), "corridor")
	_box_between("CorridorStrip", Vector3(door_x - 0.12, ch - 0.04, z0 + 0.2), Vector3(door_x + 0.12, ch, z1 - 0.2), "light_bar", false)
	var light := OmniLight3D.new()
	light.name = "CorridorLight"
	light.position = Vector3(door_x, ch - 0.35, z0 + corridor_depth * 0.5)
	light.omni_range = 4.0
	light.light_energy = 0.9
	light.light_color = Color("#dfe8ff")
	add_child(light)


func _build_window_wall() -> void:
	var z: float = room_min.z
	var h: float = room_max.y
	_box_between("WindowSill", Vector3(room_min.x - WALL, 0.0, z - WALL), Vector3(room_max.x + WALL, window_y.x, z), "wall_low")
	_box_between("WindowLedge", Vector3(window_x.x - 0.2, window_y.x - 0.04, z - 0.05), Vector3(window_x.y + 0.2, window_y.x + 0.04, z + 0.42), "ledge")
	var sill_panel_x: float = window_x.x + 0.4
	while sill_panel_x < window_x.y - 0.3 and not _surfaces.has("wall"):
		_box_between("SillPanel%d" % int(sill_panel_x * 10.0), Vector3(sill_panel_x, 0.12, z + 0.0), Vector3(sill_panel_x + 0.04, window_y.x - 0.12, z + 0.03), "panel_line", false)
		sill_panel_x += 1.3
	_box_between("WindowPierL", Vector3(room_min.x - WALL, window_y.x, z - WALL), Vector3(window_x.x, h, z), "wall")
	_box_between("WindowPierR", Vector3(window_x.y, window_y.x, z - WALL), Vector3(room_max.x + WALL, h, z), "wall")
	_box_between("WindowHeader", Vector3(window_x.x, window_y.y, z - WALL), Vector3(window_x.y, h, z), "wall")
	var f := 0.12
	_box_between("FrameL", Vector3(window_x.x, window_y.x, z - 0.1), Vector3(window_x.x + f, window_y.y, z + 0.06), "frame")
	_box_between("FrameR", Vector3(window_x.y - f, window_y.x, z - 0.1), Vector3(window_x.y, window_y.y, z + 0.06), "frame")
	_box_between("FrameTop", Vector3(window_x.x, window_y.y - f, z - 0.1), Vector3(window_x.y, window_y.y, z + 0.06), "frame")
	_box_between("FrameBottom", Vector3(window_x.x, window_y.x, z - 0.1), Vector3(window_x.y, window_y.x + 0.05, z + 0.06), "frame")
	var span: float = window_x.y - window_x.x
	for k in [1, 2]:
		var mx: float = window_x.x + span * (0.5 + (float(k) - 1.5) * 0.62)
		_box_between("Mullion%d" % k, Vector3(mx - 0.05, window_y.x, z - 0.08), Vector3(mx + 0.05, window_y.y, z + 0.02), "frame")


func _build_ceiling() -> void:
	var h: float = room_max.y
	_box_between("Ceiling", Vector3(room_min.x - WALL, h, room_min.z - WALL), Vector3(room_max.x + WALL, h + 0.2, room_max.z + WALL), "ceiling")
	var z: float = room_min.z + 0.8
	while z < room_max.z:
		_box_between("Beam%d" % int(z * 10.0), Vector3(room_min.x, h - 0.22, z - 0.12), Vector3(room_max.x, h, z + 0.12), "beam")
		z += 2.0
	_box_between("Spine", Vector3(-0.25, h - 0.3, room_min.z), Vector3(0.25, h - 0.18, room_max.z), "beam")
	for x in [-3.2, 3.2]:
		for zc in [-2.6, 0.4, 3.2]:
			if x > room_min.x + 1.2 and x < room_max.x - 1.2 and zc > room_min.z + 0.5 and zc < room_max.z - 0.5:
				_box_between("LightBar_%d_%d" % [int(x), int(zc * 10.0)], Vector3(x - 1.1, h - 0.27, zc - 0.17), Vector3(x + 1.1, h - 0.23, zc + 0.17), "light_bar", false)


# -------------------------------------------------------------- outside


func _build_outside(backdrop: Dictionary, outside_props: Variant) -> void:
	_ground_y = float(backdrop.get("ground_y", -2.4))
	var palette_value: Variant = backdrop.get("palette", {})
	_palette = palette_value as Dictionary if palette_value is Dictionary else {}
	_setup_noise(int(backdrop.get("seed", 7)))
	_cyclorama = str(backdrop.get("mode", "procedural")) == "cyclorama" and _build_cyclorama(backdrop)
	if not _cyclorama and str(backdrop.get("mode", "procedural")) != "none":
		_build_terrain()
		_build_planet(backdrop.get("planet", {}) as Dictionary if backdrop.get("planet", {}) is Dictionary else {})
	if outside_props is Array:
		for item: Variant in outside_props as Array:
			if item is Dictionary:
				var cfg: Dictionary = (item as Dictionary).duplicate()
				var pos_arr: Array = cfg.get("pos", [0, 0]) as Array
				var p := Vector2(float(pos_arr[0]), float(pos_arr[pos_arr.size() - 1]))
				cfg["pos"] = [p.x, 0.0, p.y]
				if str(cfg.get("kind", "model")) == "catwalk":
					_build_catwalk(cfg)
					continue
				_place_prop(cfg, (_ground_y if _cyclorama else terrain_height(p.x, p.y)) - 0.15, false)
	if str(backdrop.get("mode", "procedural")) == "image":
		_build_image_backdrop(backdrop)


func _build_cyclorama(backdrop: Dictionary) -> bool:
	var path: String = str(backdrop.get("image", ""))
	if path == "" or not FileAccess.file_exists(path):
		return false
	var image := Image.load_from_file(path)
	if image == null or image.is_empty():
		return false
	image.convert(Image.FORMAT_RGB8)
	var radius: float = float(backdrop.get("radius", 100.0))
	var arc: float = deg_to_rad(float(backdrop.get("arc_deg", 140.0)))
	var yaw: float = deg_to_rad(float(backdrop.get("yaw_deg", 0.0)))
	var center := Vector2.ZERO
	if backdrop.get("center", null) is Array and (backdrop["center"] as Array).size() >= 2:
		center = Vector2(float(backdrop["center"][0]), float(backdrop["center"][1]))
	var height: float = radius * arc * float(image.get_height()) / float(maxi(image.get_width(), 1)) * float(backdrop.get("v_scale", 1.0))
	var horizon_v: float = float(backdrop.get("horizon_v", 0.85))
	var top: float = float(backdrop.get("horizon_y", 1.5)) + horizon_v * height
	var segments := 160
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in segments + 1:
		var t: float = float(i) / float(segments)
		var theta: float = yaw - arc * 0.5 + arc * t
		var x: float = center.x + radius * sin(theta)
		var z: float = center.y - radius * cos(theta)
		var inward := Vector3(-sin(theta), 0.0, cos(theta))
		st.set_normal(inward)
		st.set_uv(Vector2(t, 0.0))
		st.add_vertex(Vector3(x, top, z))
		st.set_normal(inward)
		st.set_uv(Vector2(t, 1.0))
		st.add_vertex(Vector3(x, top - height, z))
	for i in segments:
		var a: int = i * 2
		st.add_index(a)
		st.add_index(a + 2)
		st.add_index(a + 1)
		st.add_index(a + 1)
		st.add_index(a + 2)
		st.add_index(a + 3)
	var screen := MeshInstance3D.new()
	screen.name = "Cyclorama"
	screen.mesh = st.commit()
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.disable_fog = true
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	material.texture_repeat = false
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	var mipped: Image = image.duplicate() as Image
	mipped.generate_mipmaps()
	material.albedo_texture = ImageTexture.create_from_image(mipped)
	screen.material_override = material
	screen.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(screen)

	var top_sum := Color(0, 0, 0)
	for k in 32:
		top_sum += image.get_pixel(int((float(k) + 0.5) / 32.0 * float(image.get_width() - 1)), 2)
	_sky_color = top_sum / 32.0

	var ground_v: float = clampf((top - _ground_y) / height + 0.004, 0.0, 1.0)
	var near_tone: Color = Color(str(backdrop.get("near_ground", "#3a4f74")))
	var rings := 18
	var ground := SurfaceTool.new()
	ground.begin(Mesh.PRIMITIVE_TRIANGLES)
	var r0: float = maxf(absf(room_min.z) - 0.5, 1.0)
	for k in rings + 1:
		var s: float = float(k) / float(rings)
		var rr: float = lerpf(r0, radius * 0.997, pow(s, 1.6))
		for i in segments + 1:
			var t: float = float(i) / float(segments)
			var theta: float = yaw - arc * 0.5 + arc * t
			var far: Color = image.get_pixel(int(t * float(image.get_width() - 1)), int(ground_v * float(image.get_height() - 1)))
			ground.set_color(near_tone.lerp(far, smoothstep(0.0, 0.85, s)))
			ground.add_vertex(Vector3(center.x + rr * sin(theta), _ground_y, center.y - rr * cos(theta)))
	for k in rings:
		for i in segments:
			var a: int = k * (segments + 1) + i
			var c: int = a + segments + 1
			ground.add_index(a)
			ground.add_index(a + 1)
			ground.add_index(c)
			ground.add_index(a + 1)
			ground.add_index(c + 1)
			ground.add_index(c)
	var skirt := MeshInstance3D.new()
	skirt.name = "CycloramaGround"
	skirt.mesh = ground.commit()
	var ground_mat := StandardMaterial3D.new()
	ground_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ground_mat.vertex_color_use_as_albedo = true
	ground_mat.vertex_color_is_srgb = true
	ground_mat.disable_fog = true
	ground_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	skirt.material_override = ground_mat
	skirt.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(skirt)
	return true


func _build_catwalk(cfg: Dictionary) -> void:
	var p: Array = cfg.get("pos", [0, 0, -12]) as Array
	var cx: float = float(p[0])
	var cz: float = float(p[2])
	var length: float = float(cfg.get("length", 26.0))
	var depth: float = float(cfg.get("depth", 1.3))
	var deck_y: float = float(cfg.get("deck_y", -0.15))
	var x0: float = cx - length * 0.5
	var x1: float = cx + length * 0.5
	_box_between("CatwalkDeck", Vector3(x0, deck_y - 0.14, cz - depth * 0.5), Vector3(x1, deck_y, cz + depth * 0.5), "exterior")
	for side: float in [-1.0, 1.0]:
		var rz: float = cz + side * (depth * 0.5 - 0.05)
		_box_between("CatwalkRailTop%d" % int(side), Vector3(x0, deck_y + 1.02, rz - 0.03), Vector3(x1, deck_y + 1.07, rz + 0.03), "exterior")
		_box_between("CatwalkRailMid%d" % int(side), Vector3(x0, deck_y + 0.52, rz - 0.02), Vector3(x1, deck_y + 0.56, rz + 0.02), "exterior")
		var x: float = x0
		while x <= x1 + 0.01:
			_box_between("CatwalkPost%d_%d" % [int(side), int(x * 10.0)], Vector3(x - 0.035, deck_y, rz - 0.035), Vector3(x + 0.035, deck_y + 1.07, rz + 0.035), "exterior")
			x += 2.0
	var leg_x: float = x0 + 2.0
	while leg_x < x1 - 1.0:
		_box_between("CatwalkLeg%d" % int(leg_x * 10.0), Vector3(leg_x - 0.09, _ground_y, cz - 0.09), Vector3(leg_x + 0.09, deck_y - 0.14, cz + 0.09), "exterior")
		leg_x += 6.0
	var lamp_x: float = x0 + 1.0
	while lamp_x < x1:
		_box_between("CatwalkLamp%d" % int(lamp_x * 10.0), Vector3(lamp_x - 0.05, deck_y + 1.07, cz + depth * 0.5 - 0.1), Vector3(lamp_x + 0.05, deck_y + 1.13, cz + depth * 0.5), "exterior_lit", false)
		lamp_x += 4.0


func _palette_color(key: String, fallback: String) -> Color:
	return Color(str(_palette.get(key, fallback)))


func _setup_noise(seed_value: int) -> void:
	_ridge_noise = FastNoiseLite.new()
	_ridge_noise.seed = seed_value
	_ridge_noise.noise_type = FastNoiseLite.TYPE_PERLIN
	_ridge_noise.frequency = 0.011
	_ridge_noise.fractal_octaves = 4
	_hill_noise = FastNoiseLite.new()
	_hill_noise.seed = seed_value + 11
	_hill_noise.frequency = 0.03
	_hill_noise.fractal_octaves = 3
	_grain_noise = FastNoiseLite.new()
	_grain_noise.seed = seed_value + 23
	_grain_noise.frequency = 0.18


func terrain_height(x: float, z: float) -> float:
	var d: float = maxf(room_min.z - z, 0.0)
	var far_t: float = clampf((d - 22.0) / 190.0, 0.0, 1.0)
	var ridge: float = 1.0 - absf(_ridge_noise.get_noise_2d(x, z))
	ridge = ridge * ridge * ridge
	var hills: float = _hill_noise.get_noise_2d(x, z) * 0.5 + 0.5
	var h: float = _ground_y + _grain_noise.get_noise_2d(x, z) * 0.25
	h += hills * (1.2 + 9.0 * far_t)
	h += ridge * 46.0 * far_t * far_t
	for crater: Vector4 in CRATERS:
		var dist: float = Vector2(x - crater.x, z - crater.y).length() / crater.z
		if dist < 1.35:
			var bowl: float = -crater.w * (1.0 - dist * dist) if dist < 1.0 else crater.w * 0.35 * sin((dist - 1.0) / 0.35 * PI)
			h += bowl
	return h


func _build_terrain() -> void:
	var rows := 110
	var cols := 140
	var depth := 330.0
	var near_z: float = room_min.z - WALL - 0.3
	var light_dir := Vector3(-0.55, 0.62, 0.56).normalized()
	var snow: Color = _palette_color("snow", "#eef3f8")
	var rock: Color = _palette_color("rock", "#9fb0c2")
	var shade_color: Color = _palette_color("shadow", "#6f85a0")
	var haze: Color = _palette_color("horizon", "#c4dcee")
	var heights: Array[PackedFloat32Array] = []
	var xs: Array[PackedFloat32Array] = []
	var zs := PackedFloat32Array()
	for i in rows:
		var t: float = float(i) / float(rows - 1)
		var z: float = near_z - pow(t, 1.7) * depth
		zs.append(z)
		var half_width: float = 45.0 + 300.0 * t
		var row_x := PackedFloat32Array()
		var row_h := PackedFloat32Array()
		for j in cols:
			var x: float = (float(j) / float(cols - 1) * 2.0 - 1.0) * half_width
			row_x.append(x)
			row_h.append(terrain_height(x, z))
		xs.append(row_x)
		heights.append(row_h)
	var st := SurfaceTool.new()
	st.begin(Mesh.PRIMITIVE_TRIANGLES)
	for i in rows:
		for j in cols:
			var x: float = xs[i][j]
			var z: float = zs[i]
			var h: float = heights[i][j]
			var e := 0.6
			var normal := Vector3(terrain_height(x - e, z) - terrain_height(x + e, z), 2.0 * e, terrain_height(x, z + e) - terrain_height(x, z - e)).normalized()
			var rise: float = clampf((h - _ground_y) / 34.0, 0.0, 1.0)
			var base: Color = rock.lerp(snow, pow(rise, 0.55))
			var flat: float = clampf(normal.y, 0.0, 1.0)
			base = base.lerp(snow, clampf((flat - 0.8) * 2.5, 0.0, 0.5) * rise)
			var lit: float = clampf(normal.dot(light_dir), 0.0, 1.0)
			var color: Color = shade_color.lerp(base, 0.35 + 0.65 * lit)
			var distance: float = maxf(near_z - z, 0.0)
			color = color.lerp(haze, clampf((distance - 15.0) / 300.0, 0.0, 0.72))
			st.set_color(color)
			st.add_vertex(Vector3(x, h, z))
	for i in rows - 1:
		for j in cols - 1:
			var a: int = i * cols + j
			var b: int = a + 1
			var c: int = a + cols
			var d: int = c + 1
			st.add_index(a)
			st.add_index(c)
			st.add_index(b)
			st.add_index(b)
			st.add_index(c)
			st.add_index(d)
	var terrain := MeshInstance3D.new()
	terrain.name = "Moonscape"
	terrain.mesh = st.commit()
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.vertex_color_use_as_albedo = true
	material.vertex_color_is_srgb = true
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	terrain.material_override = material
	terrain.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(terrain)


func _build_planet(cfg: Dictionary) -> void:
	var shader := Shader.new()
	shader.code = PLANET_SHADER
	var material := ShaderMaterial.new()
	material.shader = shader
	material.set_shader_parameter("light_color", _palette_color("planet_light", "#eef4fb"))
	material.set_shader_parameter("sea_color", _palette_color("planet_mid", "#7fb0d8"))
	material.set_shader_parameter("shadow_color", _palette_color("planet_shadow", "#4f7fb5"))
	var radius: float = float(cfg.get("radius", 58.0))
	var sphere := SphereMesh.new()
	sphere.radius = radius
	sphere.height = radius * 2.0
	sphere.radial_segments = 64
	sphere.rings = 32
	var planet := MeshInstance3D.new()
	planet.name = "Planet"
	planet.mesh = sphere
	planet.material_override = material
	planet.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	planet.position = _vec3(cfg.get("pos", null), Vector3(-30.0, 20.0, -330.0))
	add_child(planet)


func _build_image_backdrop(backdrop: Dictionary) -> void:
	var image_path: String = str(backdrop.get("image", ""))
	if image_path == "" or not FileAccess.file_exists(image_path):
		return
	var distance: float = float(backdrop.get("distance", 70.0))
	var ground_y: float = _ground_y
	var image := Image.load_from_file(image_path)
	if image == null or image.is_empty():
		return
	image.generate_mipmaps()
	var width: float = float(backdrop.get("width", 200.0))
	var height: float = width * float(image.get_height()) / float(maxi(image.get_width(), 1))
	var quad := QuadMesh.new()
	quad.size = Vector2(width, height)
	var plane := MeshInstance3D.new()
	plane.name = "MoonscapeBackdrop"
	plane.mesh = quad
	var material := StandardMaterial3D.new()
	material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material.albedo_texture = ImageTexture.create_from_image(image)
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	material.disable_fog = true
	plane.material_override = material
	plane.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var bottom: float = float(backdrop.get("bottom_y", ground_y - 1.0))
	plane.position = Vector3(float(backdrop.get("center_x", 0.0)), bottom + height * 0.5, room_min.z - distance)
	add_child(plane)


func _build_console_screen(cfg: Dictionary) -> void:
	var texture: Texture2D = _load_texture(str(cfg.get("texture", "")))
	if texture == null:
		return
	var holder := Node3D.new()
	holder.name = "ConsoleMonitor"
	holder.position = _vec3(cfg.get("pos", null), Vector3(-3.25, 1.42, -3.95))
	holder.rotation = Vector3(deg_to_rad(-float(cfg.get("tilt_deg", 8.0))), deg_to_rad(float(cfg.get("yaw_deg", 0.0))), 0.0)
	add_child(holder)
	var size := Vector2(1.1, 0.62)
	if cfg.get("size", null) is Array and (cfg["size"] as Array).size() >= 2:
		size = Vector2(float(cfg["size"][0]), float(cfg["size"][1]))
	var bezel := MeshInstance3D.new()
	var bezel_mesh := BoxMesh.new()
	bezel_mesh.size = Vector3(size.x + 0.07, size.y + 0.07, 0.05)
	bezel.mesh = bezel_mesh
	bezel.position = Vector3(0.0, 0.0, -0.03)
	bezel.material_override = _materials["frame"]
	holder.add_child(bezel)
	var stand_bottom: float = float(cfg.get("stand_bottom_y", 0.97))
	var stand := MeshInstance3D.new()
	var stand_mesh := BoxMesh.new()
	var stand_h: float = maxf(holder.position.y - size.y * 0.5 - stand_bottom + 0.05, 0.02)
	stand_mesh.size = Vector3(0.08, stand_h, 0.06)
	stand.mesh = stand_mesh
	stand.position = Vector3(0.0, -size.y * 0.5 - stand_h * 0.5 + 0.05, -0.06)
	stand.material_override = _materials["frame"]
	holder.add_child(stand)
	var screen := MeshInstance3D.new()
	screen.name = "ConsoleScreen"
	var quad := QuadMesh.new()
	quad.size = size
	screen.mesh = quad
	var material := StandardMaterial3D.new()
	material.albedo_texture = texture
	material.albedo_color = Color(str(cfg.get("tint", "#202630")))
	material.roughness = 1.0
	material.metallic_specular = 0.0
	material.emission_enabled = true
	material.emission_texture = texture
	material.emission = Color(1, 1, 1)
	material.emission_operator = BaseMaterial3D.EMISSION_OP_MULTIPLY
	material.emission_energy_multiplier = float(cfg.get("emission", 0.65))
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	screen.material_override = material
	screen.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	screen.position = Vector3(0.0, 0.0, 0.0)
	holder.add_child(screen)


# ---------------------------------------------------------------- props


func _build_props(props: Variant) -> void:
	if not props is Array:
		return
	for item: Variant in props as Array:
		if item is Dictionary:
			_place_prop(item as Dictionary, 0.0, true)


func _ingest_explicit_obstacles(obs_value: Variant) -> void:
	if not obs_value is Array:
		return
	for item: Variant in obs_value as Array:
		if not item is Dictionary:
			continue
		var d: Dictionary = item as Dictionary
		if d.has("min") and d.has("max"):
			_register_obstacle(_vec3(d["min"], Vector3.ZERO), _vec3(d["max"], Vector3.ZERO))


func _child_box(parent: Node3D, label: String, size: Vector3, local_pos: Vector3, mat_key: String, shadows: bool = true) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = label
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	mi.position = local_pos
	mi.material_override = _materials[mat_key]
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_ON if shadows else GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	parent.add_child(mi)
	return mi


func _place_prop(cfg: Dictionary, ground_y: float, track_obstacle: bool = true) -> void:
	var kind: String = str(cfg.get("kind", "model")).to_lower()
	var pos_value: Variant = cfg.get("pos", [0, 0, 0])
	var pos: Vector3 = _vec3(pos_value, Vector3.ZERO)
	if pos_value is Array and (pos_value as Array).size() == 2:
		pos = Vector3(float((pos_value as Array)[0]), 0.0, float((pos_value as Array)[1]))
	pos.y += ground_y
	var yaw: float = deg_to_rad(float(cfg.get("yaw_deg", 0.0)))
	var scale_value: float = float(cfg.get("scale", 1.0))
	var label: String = str(cfg.get("id", kind))

	if kind in ["crate", "console", "workstation", "medical_bed", "exam_bed", "command_table", "pillar", "column", "screen", "wall_screen", "bio_monitor", "lab_bench", "workbench", "cabinet", "locker", "bench", "seat"]:
		var holder := Node3D.new()
		holder.name = label
		holder.position = pos
		holder.rotation.y = yaw
		add_child(holder)
		var size: Vector3 = _build_procedural_prop(holder, kind, cfg)
		if track_obstacle:
			_register_rotated_box_obstacle(pos, size, yaw)
		_register_interactive_device(label, kind, holder, size)
		props_loaded += 1
		return

	var path: String = str(cfg.get("model", ""))
	var model: Node3D = _character_script.call("load_gltf_scene", path) as Node3D if _character_script != null else null
	if model == null:
		props_failed += 1
		return
	var holder3 := Node3D.new()
	holder3.name = label
	add_child(holder3)
	holder3.add_child(model)
	var box: AABB = _bounds(model)
	model.position = Vector3(-(box.position.x + box.size.x * 0.5), -box.position.y, -(box.position.z + box.size.z * 0.5))
	holder3.scale = Vector3.ONE * scale_value
	holder3.rotation.y = yaw
	holder3.position = pos
	if track_obstacle:
		_register_rotated_box_obstacle(pos, box.size * scale_value, yaw)
	if kind in ["console", "workstation", "bio_monitor", "command_table", "screen"] or label.to_lower().contains("console"):
		_register_interactive_device(label, "console", holder3, box.size * scale_value)
	var tint_value: Variant = cfg.get("emission_boost", 0.0)
	if float(tint_value) > 0.0:
		for node: Node in _descendants(model):
			if node is MeshInstance3D:
				var mi := node as MeshInstance3D
				for s in mi.mesh.get_surface_count():
					var material: Material = mi.get_active_material(s)
					if material is StandardMaterial3D:
						var copy := (material as StandardMaterial3D).duplicate() as StandardMaterial3D
						copy.emission_enabled = true
						copy.emission = copy.albedo_color
						copy.emission_energy_multiplier = float(tint_value)
						mi.set_surface_override_material(s, copy)
	props_loaded += 1


func _register_rotated_box_obstacle(pos: Vector3, size: Vector3, yaw: float) -> void:
	var c: float = absf(cos(yaw))
	var s: float = absf(sin(yaw))
	var wx: float = size.x * c + size.z * s
	var wz: float = size.x * s + size.z * c
	var lo := Vector3(pos.x - wx * 0.5, pos.y, pos.z - wz * 0.5)
	var hi := Vector3(pos.x + wx * 0.5, pos.y + size.y, pos.z + wz * 0.5)
	_register_obstacle(lo, hi)


func _build_procedural_prop(holder: Node3D, kind: String, cfg: Dictionary) -> Vector3:
	match kind:
		"crate":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(0.9, 0.9, 0.9))
			var mat_main: String = "crate" if str(cfg.get("color", "orange")) == "orange" else "crate_dark"
			var mat_band: String = "crate_dark" if mat_main == "crate" else "trim"
			_child_box(holder, "Body", size, Vector3(0.0, size.y * 0.5, 0.0), mat_main)
			for band_y: float in [0.12, size.y - 0.12]:
				_child_box(holder, "Band_%d" % int(band_y * 100.0), Vector3(size.x + 0.02, 0.06, size.z + 0.02), Vector3(0.0, band_y, 0.0), mat_band)
			return size
		"console", "workstation":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(1.6, 0.92, 0.72))
			var glow_key: String = "teal_glow" if scene_type == "med_lab" else "cyan_glow"
			_child_box(holder, "Base", Vector3(size.x * 0.9, size.y - 0.08, size.z * 0.8), Vector3(0.0, (size.y - 0.08) * 0.5, 0.0), "metal_dark")
			_child_box(holder, "Top", Vector3(size.x, 0.08, size.z), Vector3(0.0, size.y - 0.04, 0.0), "metal_light")
			_child_box(holder, "ScreenBezel", Vector3(size.x * 0.68, 0.52, 0.06), Vector3(0.0, size.y + 0.30, -size.z * 0.28), "frame")
			_child_dynamic_screen(holder, "ScreenPanel", Vector3(size.x * 0.62, 0.44, 0.02), Vector3(0.0, size.y + 0.30, -size.z * 0.24), "console")
			_child_box(holder, "KeyStrip", Vector3(size.x * 0.56, 0.02, 0.18), Vector3(0.0, size.y + 0.01, size.z * 0.14), glow_key, false)
			return Vector3(size.x, size.y + 0.56, size.z)
		"medical_bed", "exam_bed":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(2.1, 0.88, 0.95))
			_child_box(holder, "Pedestal", Vector3(size.x * 0.55, size.y * 0.68, size.z * 0.58), Vector3(0.0, size.y * 0.34, 0.0), "metal_dark")
			_child_box(holder, "Deck", Vector3(size.x, 0.12, size.z), Vector3(0.0, size.y - 0.18, 0.0), "medical_white")
			_child_box(holder, "Mattress", Vector3(size.x - 0.08, 0.12, size.z - 0.08), Vector3(0.0, size.y - 0.06, 0.0), "medical_pad")
			_child_box(holder, "HeadBolster", Vector3(0.32, 0.10, size.z - 0.16), Vector3(-size.x * 0.36, size.y + 0.03, 0.0), "medical_white")
			_child_box(holder, "RailFront", Vector3(size.x * 0.65, 0.05, 0.04), Vector3(0.0, size.y + 0.06, size.z * 0.48), "metal_light")
			_child_box(holder, "RailBack", Vector3(size.x * 0.65, 0.05, 0.04), Vector3(0.0, size.y + 0.06, -size.z * 0.48), "metal_light")
			_child_box(holder, "ScannerAccent", Vector3(0.24, 0.04, size.z * 0.85), Vector3(size.x * 0.12, size.y - 0.12, 0.0), "teal_glow", false)
			return Vector3(size.x, size.y + 0.10, size.z)
		"command_table":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(2.8, 0.95, 1.8))
			_child_box(holder, "Plinth", Vector3(size.x * 0.72, size.y * 0.72, size.z * 0.68), Vector3(0.0, size.y * 0.36, 0.0), "metal_dark")
			_child_box(holder, "Rim", Vector3(size.x, 0.14, size.z), Vector3(0.0, size.y - 0.07, 0.0), "frame")
			_child_dynamic_screen(holder, "DisplayCore", Vector3(size.x * 0.78, 0.03, size.z * 0.70), Vector3(0.0, size.y + 0.015, 0.0), "tactical_map")
			_child_box(holder, "HoloPrism", Vector3(size.x * 0.44, 0.24, size.z * 0.38), Vector3(0.0, size.y + 0.22, 0.0), "holo_glass", false)
			_child_alert_strip(holder, "TrimStripe", Vector3(size.x + 0.02, 0.04, size.z + 0.02), Vector3(0.0, size.y - 0.12, 0.0))
			return Vector3(size.x, size.y + 0.35, size.z)
		"pillar", "column":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(0.48, room_max.y, 0.48))
			_child_box(holder, "BasePlinth", Vector3(size.x + 0.12, 0.22, size.z + 0.12), Vector3(0.0, 0.11, 0.0), "metal_dark")
			_child_box(holder, "Shaft", Vector3(size.x, size.y, size.z), Vector3(0.0, size.y * 0.5, 0.0), "beam")
			_child_box(holder, "Cap", Vector3(size.x + 0.12, 0.20, size.z + 0.12), Vector3(0.0, size.y - 0.10, 0.0), "metal_dark")
			_child_alert_strip(holder, "Strip", Vector3(size.x + 0.02, size.y * 0.65, 0.06), Vector3(0.0, size.y * 0.52, 0.0))
			return size
		"screen", "wall_screen", "bio_monitor":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(1.1, 1.75, 0.26))
			var ui_kind: String = "bio_vitals" if (kind == "bio_monitor" or scene_type == "med_lab") else ("corridor_status" if scene_type == "corridor" else "tactical_map")
			_child_box(holder, "StandBase", Vector3(0.46, 0.06, 0.36), Vector3(0.0, 0.03, 0.0), "metal_dark")
			_child_box(holder, "StandPost", Vector3(0.10, size.y * 0.65, 0.10), Vector3(0.0, size.y * 0.34, -0.02), "metal_light")
			_child_box(holder, "Bezel", Vector3(size.x, 0.72, 0.08), Vector3(0.0, size.y - 0.36, 0.0), "frame")
			_child_dynamic_screen(holder, "Display", Vector3(size.x - 0.10, 0.60, 0.02), Vector3(0.0, size.y - 0.36, 0.045), ui_kind)
			return size
		"lab_bench", "workbench":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(2.0, 0.90, 0.78))
			_child_box(holder, "Cabinet", Vector3(size.x - 0.08, size.y - 0.08, size.z - 0.08), Vector3(0.0, (size.y - 0.08) * 0.5, 0.0), "medical_white")
			_child_box(holder, "Worktop", Vector3(size.x, 0.08, size.z), Vector3(0.0, size.y - 0.04, 0.0), "metal_light")
			_child_box(holder, "DrawerLine", Vector3(size.x - 0.16, 0.03, 0.02), Vector3(0.0, size.y * 0.55, size.z * 0.5 - 0.03), "trim", false)
			return size
		"cabinet", "locker":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(0.95, 2.0, 0.55))
			var body_mat: String = "medical_white" if scene_type == "med_lab" else "metal_dark"
			_child_box(holder, "Body", size, Vector3(0.0, size.y * 0.5, 0.0), body_mat)
			_child_box(holder, "Seam", Vector3(0.03, size.y - 0.2, 0.02), Vector3(0.0, size.y * 0.5, size.z * 0.5 + 0.005), "frame", false)
			_child_box(holder, "StatusLED", Vector3(0.14, 0.04, 0.02), Vector3(0.0, size.y - 0.14, size.z * 0.5 + 0.01), "teal_glow" if scene_type == "med_lab" else "amber_glow", false)
			return size
		_:
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(1.5, 0.48, 0.48))
			_child_box(holder, "LegL", Vector3(0.12, size.y - 0.08, size.z * 0.85), Vector3(-size.x * 0.4, (size.y - 0.08) * 0.5, 0.0), "metal_dark")
			_child_box(holder, "LegR", Vector3(0.12, size.y - 0.08, size.z * 0.85), Vector3(size.x * 0.4, (size.y - 0.08) * 0.5, 0.0), "metal_dark")
			_child_box(holder, "Seat", Vector3(size.x, 0.08, size.z), Vector3(0.0, size.y - 0.04, 0.0), "metal_light")
			return size


func _descendants(root: Node) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		out.append(node)
		for child: Node in node.get_children():
			stack.append(child)
	return out


func _bounds(root: Node3D) -> AABB:
	var result := AABB()
	var first := true
	for node: Node in _descendants(root):
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var mi := node as MeshInstance3D
			var xform := Transform3D.IDENTITY
			var current: Node = mi
			while current != null and current != root.get_parent():
				if current is Node3D:
					xform = (current as Node3D).transform * xform
				current = current.get_parent()
			var box: AABB = xform * mi.mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result


# ------------------------------------------------------------- lighting


func _build_lighting(profile_name: String, cfg: Dictionary) -> void:
	if _lighting_rig != null and is_instance_valid(_lighting_rig):
		_lighting_rig.queue_free()
	var backdrop_opts := {
		"cyclorama": _cyclorama,
		"sky_color": _sky_color,
		"palette": _palette,
	}
	_lighting_rig = LightingProfileScript.apply_profile(
		self,
		profile_name,
		room_min,
		room_max,
		cfg,
		backdrop_opts
	)
	if _has_front_window:
		_build_storm_front_mesh()


# ------------------------------------ cohesive PBR, dynamic screens & events (BUG-010/012/014/015/016)


## Procedural brushed-panel PBR material (BUG-015): breaks up flat plastic surfaces
## on walls, consoles, med beds, and cabinets with subtle architectural seam & grain detail.
func _panel_material(base_col: Color, seam_col: Color, roughness: float = 0.72, metallic: float = 0.10) -> StandardMaterial3D:
	var img := Image.create(128, 128, false, Image.FORMAT_RGB8)
	for y in 128:
		var edge_y: bool = y <= 2 or y >= 125 or (y >= 62 and y <= 64)
		for x in 128:
			var edge_x: bool = x <= 2 or x >= 125
			if edge_x or edge_y:
				img.set_pixel(x, y, seam_col)
			else:
				var h: int = (x * 73856093 ^ y * 19349663) & 255
				var grain: float = (float(h) / 255.0 - 0.5) * 0.045
				var bevel: float = 0.03 if (x == 3 or y == 3 or y == 65) else (-0.025 if (x == 124 or y == 124 or y == 61) else 0.0)
				var c := Color(
					clampf(base_col.r + grain + bevel, 0.0, 1.0),
					clampf(base_col.g + grain + bevel, 0.0, 1.0),
					clampf(base_col.b + grain + bevel, 0.0, 1.0),
				)
				img.set_pixel(x, y, c)
	img.generate_mipmaps()
	var tex := ImageTexture.create_from_image(img)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = tex
	mat.roughness = roughness
	mat.metallic = metallic
	mat.uv1_triplanar = true
	mat.uv1_scale = Vector3(0.75, 0.75, 0.75)
	return mat


## Live procedural sci-fi screen surface (BUG-016: NEVER a flat white glowing rectangle).
func _child_dynamic_screen(parent: Node3D, node_name: String, size: Vector3, pos: Vector3, ui_kind: String) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = node_name
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var mat := StandardMaterial3D.new()
	var tex: ImageTexture = _get_screen_texture(ui_kind, "nominal")
	mat.albedo_texture = tex
	mat.albedo_color = Color(0.95, 0.98, 1.0)
	mat.roughness = 0.28
	mat.metallic = 0.05
	mat.emission_enabled = true
	mat.emission_texture = tex
	mat.emission = Color(1.0, 1.0, 1.0)
	mat.emission_operator = BaseMaterial3D.EMISSION_OP_MULTIPLY
	mat.emission_energy_multiplier = 0.92
	mi.material_override = mat
	parent.add_child(mi)
	_dynamic_screens.append({"material": mat, "kind": ui_kind, "mesh": mi})
	return mi


func _child_alert_strip(parent: Node3D, node_name: String, size: Vector3, pos: Vector3) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = node_name
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var base_col: Color = Color("#2fd1c4") if scene_type == "med_lab" else Color("#38b8ea")
	var mat := _mat(base_col, 0.38, 0.85)
	mat.set_meta("base_color", base_col)
	mi.material_override = mat
	parent.add_child(mi)
	_alert_strips.append(mat)
	return mi


func _get_screen_texture(ui_kind: String, mode: String) -> ImageTexture:
	var key := "%s:%s" % [ui_kind, mode]
	if _screen_textures.has(key):
		return _screen_textures[key] as ImageTexture

	var w := 256
	var h := 160
	var img := Image.create(w, h, false, Image.FORMAT_RGB8)

	# Palette by event mode so red_alert / signal_lost / sensor_error / storm_approaching are unmistakable.
	var bg := Color("#07131e")
	var grid := Color("#102c40")
	var primary := Color("#2ad8cc") if (ui_kind == "bio_vitals" or scene_type == "med_lab") else Color("#34b8f0")
	var accent := Color("#78f2e8") if ui_kind == "bio_vitals" else Color("#ffae38")
	var alert_col := primary
	match mode:
		"red_alert":
			bg = Color("#1d070a")
			grid = Color("#421218")
			primary = Color("#ff3b28")
			accent = Color("#ff9c38")
			alert_col = Color("#ff2a1a")
		"sensor_error":
			bg = Color("#161108")
			grid = Color("#342812")
			primary = Color("#ff9c28")
			accent = Color("#ff4634")
			alert_col = Color("#ff8822")
		"signal_lost":
			bg = Color("#111018")
			grid = Color("#27243a")
			primary = Color("#f08c2e")
			accent = Color("#ff3c38")
			alert_col = Color("#f08c2e")
		"storm_approaching":
			bg = Color("#081622")
			grid = Color("#17344a")
			primary = Color("#4cc4f4")
			accent = Color("#ffaa33")
			alert_col = Color("#ffaa33")

	img.fill(bg)

	# Subtle coordinate grid.
	for gx in range(0, w, 16):
		for gy in h:
			img.set_pixel(gx, gy, grid)
	for gy in range(0, h, 16):
		for gx in w:
			img.set_pixel(gx, gy, grid)

	# Outer bezel border & top status header bar.
	for x in range(4, w - 4):
		img.set_pixel(x, 4, primary)
		img.set_pixel(x, h - 5, primary)
		for hy in range(8, 22):
			if x < w - 58:
				img.set_pixel(x, hy, alert_col * 0.45)
			elif x > w - 48 and x < w - 10:
				img.set_pixel(x, hy, alert_col)
	for y in range(4, h - 4):
		img.set_pixel(4, y, primary)
		img.set_pixel(w - 5, y, primary)

	# Header faux-telemetry glyph blocks.
	for seg in 6:
		var sx: int = 12 + seg * 26
		for dx in range(0, 18):
			for dy in range(12, 18):
				img.set_pixel(sx + dx, dy, primary * 1.1)

	if ui_kind == "tactical_map":
		# Concentric lunar sector radar + target blips + right sidebar bars.
		var cx := 102
		var cy := 90
		for r in [18, 34, 50]:
			for deg in range(0, 360, 2):
				var rad: float = deg_to_rad(float(deg))
				var px: int = clampi(cx + int(round(cos(rad) * r)), 6, w - 7)
				var py: int = clampi(cy + int(round(sin(rad) * r)), 26, h - 7)
				img.set_pixel(px, py, primary)
		# Crosshair & sweep line.
		for d in range(-50, 51):
			img.set_pixel(cx + d, cy, grid * 1.6)
			img.set_pixel(cx, cy + d, grid * 1.6)
		for r2 in range(0, 50):
			var px2: int = clampi(cx + int(round(r2 * 0.72)), 6, w - 7)
			var py2: int = clampi(cy - int(round(r2 * 0.68)), 26, h - 7)
			img.set_pixel(px2, py2, accent)
		# Sector target markers.
		for blip: Vector2i in [Vector2i(cx + 22, cy - 16), Vector2i(cx - 28, cy + 12), Vector2i(cx + 12, cy + 28)]:
			for bx in range(-3, 4):
				for by in range(-3, 4):
					if bx * bx + by * by <= 9:
						img.set_pixel(blip.x + bx, blip.y + by, accent)
		# Right telemetry progress bars.
		for b_idx in 5:
			var by0: int = 38 + b_idx * 22
			var fill_w: int = 24 + ((b_idx * 19 + (42 if mode == "red_alert" else 17)) % 56)
			for bx2 in range(172, 242):
				for by2 in range(by0, by0 + 10):
					var col_b: Color = (accent if b_idx == 0 else primary) if (bx2 - 172) < fill_w else (grid * 0.9)
					img.set_pixel(bx2, by2, col_b)
	else:
		# Dual live waveform traces (ECG / RF signal / sensor calibration) + diagnostic sidebar.
		for x in range(12, 180):
			var u: float = float(x - 12) / 168.0
			var y_wave1: int = 62
			var y_wave2: int = 114
			if mode == "signal_lost":
				var drop: float = 0.0 if (u > 0.52 and u < 0.86) else sin(u * TAU * 4.5) * 14.0
				y_wave1 = clampi(62 + int(round(drop)), 30, 92)
				y_wave2 = clampi(114 + int(round(((x * 13) % 7 - 3) * (2.0 if u > 0.52 else 0.5))), 96, 146)
			elif mode == "sensor_error":
				var spike: float = sin(u * TAU * 6.0) * 16.0 + (18.0 if (x % 29) < 4 else 0.0)
				y_wave1 = clampi(62 - int(round(spike)), 28, 94)
				y_wave2 = clampi(114 + int(round(cos(u * TAU * 3.0) * 12.0)), 96, 146)
			else:
				# Crisp ECG / telemetry waveform.
				var cyc: float = fmod(u * 3.0, 1.0)
				var ecg := 0.0
				if cyc > 0.42 and cyc < 0.48:
					ecg = 22.0 * sin(((cyc - 0.42) / 0.06) * PI)
				elif cyc >= 0.48 and cyc < 0.53:
					ecg = -10.0 * sin(((cyc - 0.48) / 0.05) * PI)
				elif cyc > 0.65 and cyc < 0.78:
					ecg = 7.0 * sin(((cyc - 0.65) / 0.13) * PI)
				y_wave1 = clampi(66 - int(round(ecg)), 28, 94)
				y_wave2 = clampi(118 - int(round(sin(u * TAU * 4.0) * 12.0 + cos(u * TAU * 9.0) * 4.0)), 96, 146)
			for dy in range(-1, 2):
				img.set_pixel(x, clampi(y_wave1 + dy, 26, h - 7), primary)
				img.set_pixel(x, clampi(y_wave2 + dy, 26, h - 7), accent)
		# Right status readout bars.
		for b_idx in 4:
			var by0: int = 34 + b_idx * 28
			var fill_w: int = 20 + ((b_idx * 17 + 23) % 36)
			for bx2 in range(192, 246):
				for by2 in range(by0, by0 + 12):
					var col_b: Color = (accent if (b_idx == 0 and mode != "nominal") else primary) if (bx2 - 192) < fill_w else grid
					img.set_pixel(bx2, by2, col_b)

	img.generate_mipmaps()
	var tex := ImageTexture.create_from_image(img)
	_screen_textures[key] = tex
	return tex


## Registers an interactive scene device anchor (BUG-010: InteractionAnchor, StandPoint, LookTarget).
func _register_interactive_device(dev_id: String, kind: String, holder: Node3D, size: Vector3) -> void:
	if not (kind in ["console", "workstation", "command_table", "bio_monitor", "medical_bed", "exam_bed", "screen", "wall_screen", "lab_bench"]):
		return
	var xf: Transform3D = holder.global_transform
	var anchor_local := Vector3(0.0, clampf(size.y * 0.72, 0.86, 1.15), size.z * 0.36)
	var look_local := Vector3(0.0, clampf(size.y * 0.92, 1.05, 1.48), 0.0)
	var stand_local := Vector3(0.0, 0.0, size.z * 0.5 + 0.58)
	if kind == "command_table":
		anchor_local = Vector3(0.0, size.y * 0.96, size.z * 0.42)
		look_local = Vector3(0.0, size.y + 0.12, 0.0)
	_interactive_devices.append({
		"id": dev_id,
		"kind": kind,
		"interaction_anchor": xf * anchor_local,
		"look_target": xf * look_local,
		"stand_point": xf * stand_local,
	})


func find_nearest_device(world_pos: Vector3) -> Dictionary:
	var best: Dictionary = {}
	var best_d := 999.0
	for dev: Dictionary in _interactive_devices:
		var anc: Vector3 = dev["interaction_anchor"] as Vector3
		var d: float = Vector2(world_pos.x - anc.x, world_pos.z - anc.z).length()
		if d < best_d:
			best_d = d
			best = dev.duplicate()
			best["distance_m"] = d
	return best


func _build_storm_front_mesh() -> void:
	# Translucent lunar dust/storm cloud band visible outside the panoramic window
	# when `storm_approaching` fires (BUG-012).
	var mi := MeshInstance3D.new()
	mi.name = "StormFrontBand"
	var quad := QuadMesh.new()
	quad.size = Vector2(26.0, 5.2)
	mi.mesh = quad
	mi.position = Vector3(0.0, 2.2, room_min.z - 12.5)
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var img := Image.create(128, 64, false, Image.FORMAT_RGBA8)
	for y in 64:
		var vy: float = float(y) / 63.0
		var v_fade: float = sin(vy * PI)
		for x in 128:
			var vx: float = float(x) / 127.0
			var cloud: float = 0.5 + 0.3 * sin(vx * TAU * 3.0 + vy * 5.0) + 0.2 * cos(vx * TAU * 7.0 - vy * 3.0)
			var alpha: float = clampf(v_fade * cloud * 0.62, 0.0, 0.72)
			img.set_pixel(x, y, Color(0.28, 0.38, 0.52, alpha))
	var tex := ImageTexture.create_from_image(img)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = tex
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(0.65, 0.78, 0.92, 0.0)
	mi.material_override = mat
	mi.visible = false
	add_child(mi)
	_storm_band = mi


## SceneEventController hook updating dynamic screens, alert strips, and storm front (BUG-012/016).
func apply_scene_events(state: Dictionary) -> void:
	var screen_mode: String = str(state.get("screen_mode", "nominal"))
	var alert_pulse: float = clampf(float(state.get("alert_pulse", 0.0)), 0.0, 1.0)
	var storm_intensity: float = clampf(float(state.get("storm_intensity", 0.0)), 0.0, 1.0)
	var t_s: float = float(state.get("time_s", 0.0))

	if screen_mode != _last_event_screen_mode:
		_last_event_screen_mode = screen_mode
		for entry: Dictionary in _dynamic_screens:
			var mat: StandardMaterial3D = entry["material"] as StandardMaterial3D
			var kind: String = str(entry["kind"])
			var tex: ImageTexture = _get_screen_texture(kind, screen_mode)
			mat.albedo_texture = tex
			mat.emission_texture = tex

	# Subtle live screen pulse on active alert/error modes.
	var scr_energy: float = 0.92 + (0.28 * alert_pulse if screen_mode == "red_alert" else 0.0)
	for entry2: Dictionary in _dynamic_screens:
		var mat2: StandardMaterial3D = entry2["material"] as StandardMaterial3D
		mat2.emission_energy_multiplier = scr_energy

	# Alert strips pulse unmistakable crimson during red_alert.
	for smat: StandardMaterial3D in _alert_strips:
		var base_c: Color = smat.get_meta("base_color", Color("#38b8ea")) as Color
		if alert_pulse > 0.01:
			var c: Color = base_c.lerp(Color("#ff2418"), clampf(0.55 + 0.45 * alert_pulse, 0.0, 1.0))
			smat.albedo_color = c
			smat.emission = c
			smat.emission_energy_multiplier = 0.85 + 1.85 * alert_pulse
		else:
			smat.albedo_color = base_c
			smat.emission = base_c
			smat.emission_energy_multiplier = 0.75

	# Storm front drifts outside panoramic windows when storm_approaching is active.
	if _storm_band != null and is_instance_valid(_storm_band):
		_storm_band.visible = storm_intensity > 0.02
		if _storm_band.visible:
			_storm_band.position.x = sin(t_s * 0.25) * 2.2
			var sm: StandardMaterial3D = _storm_band.material_override as StandardMaterial3D
			if sm != null:
				sm.albedo_color = Color(0.58, 0.70, 0.84, clampf(storm_intensity * 0.75, 0.0, 0.78))

