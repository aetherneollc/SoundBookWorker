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
const RetroArtScript := preload("res://player/retro_art_profile.gd")

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
var _storm_clouds: Array[MeshInstance3D] = []
var _last_event_screen_mode := ""
var _art_profile: Dictionary = {}
var _door_leaves: Array[Dictionary] = []
var _door_leds: Array[StandardMaterial3D] = []
var _door_amount := 1.0
var _door_target := 1.0
var _mech_clock := -1.0
var _light_bar_mat: StandardMaterial3D
var _light_bar_base := 1.0
var _emergency_mats: Array[StandardMaterial3D] = []
var _module_leds: Array[StandardMaterial3D] = []
var _jolt_targets: Array[Node3D] = []
var _jolt_home: Dictionary = {}
var _array_root: Node3D
var _array_yaw_pivot: Node3D
var _array_pitch_pivot: Node3D
var _array_beacon_mat: StandardMaterial3D
var _array_beam: MeshInstance3D
var _array_beam_mat: StandardMaterial3D
var _array_sparks: Node3D
var _array_pitch_deg := -6.0
var _array_yaw_deg := 8.0
var _array_lock := 0.0
var _array_was_fault := false
var _array_held_lock := false
var _array_fault_now := false
var _lightning_light: OmniLight3D
var _lightning_flash: MeshInstance3D
var _lightning_energy := 0.0
var _blast_far: OmniLight3D
var _blast_north: OmniLight3D
var _spark_light: OmniLight3D
var _threat_light: OmniLight3D
var _blast_glow: MeshInstance3D
var _blast_glow_north: MeshInstance3D
var _blast_glow_mat: StandardMaterial3D
var _blast_glow_north_mat: StandardMaterial3D
var _dressing_count := 0
var _sky_backdrop_mats: Array[StandardMaterial3D] = []
var _planet_mat: ShaderMaterial
var _module_slot: Node3D
var _device_online := false
var _module_slot_state := "empty_offline"
var _slot_latch: MeshInstance3D
var _storm_exterior := false
var _rain_streaks: Array[MeshInstance3D] = []
var _beacon_lamps: Dictionary = {}


func build(set_cfg: Dictionary, character_script: Script) -> void:
	_character_script = character_script
	_obstacle_aabbs.clear()
	_doorframe_aabbs.clear()
	_interactive_devices.clear()
	_dynamic_screens.clear()
	_screen_textures.clear()
	_alert_strips.clear()
	_storm_band = null
	_storm_clouds.clear()
	_last_event_screen_mode = ""
	_door_leaves.clear()
	_door_leds.clear()
	_door_amount = 1.0
	_door_target = 1.0
	if str(set_cfg.get("door_start", "open")).strip_edges().to_lower() == "closed":
		_door_amount = 0.0
		_door_target = 0.0
	_mech_clock = -1.0
	_emergency_mats.clear()
	_module_leds.clear()
	_jolt_targets.clear()
	_jolt_home.clear()
	_array_root = null
	_array_yaw_pivot = null
	_array_pitch_pivot = null
	_array_beacon_mat = null
	_array_beam = null
	_array_beam_mat = null
	_array_sparks = null
	_array_pitch_deg = -6.0
	_array_yaw_deg = 8.0
	_array_lock = 0.0
	_array_was_fault = false
	_array_held_lock = false
	_array_fault_now = false
	_lightning_light = null
	_lightning_flash = null
	_lightning_energy = 0.0
	_blast_far = null
	_blast_north = null
	_spark_light = null
	_threat_light = null
	_blast_glow = null
	_blast_glow_north = null
	_blast_glow_mat = null
	_blast_glow_north_mat = null
	_dressing_count = 0
	_sky_backdrop_mats.clear()
	_planet_mat = null
	_module_slot = null
	_device_online = false
	_module_slot_state = "empty_offline"
	_slot_latch = null
	_storm_exterior = false
	_rain_streaks.clear()
	_beacon_lamps.clear()
	_light_bar_mat = null
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
	_ingest_art_profile(set_cfg)
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

	if bool(_art_profile.get("enabled", false)) and bool(_art_profile.get("environment_dressing", false)):
		_build_environment_dressing()
	if _wants_modular_scene():
		_build_modular_industrial_bay()
	if _wants_hero_industrial():
		_build_hero_industrial_set()
	_build_emergency_power_strips()
	if _has_front_window and scene_type in ["observation_bay", "command_deck"]:
		_build_exterior_array()
	_sync_door_leaves()

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
			# Stained industrial hall. Darker than the observation bay, with worn metal
			# and a dull emergency trim. The bay match below is left alone.
			if _materials["floor"] == null:
				_materials["floor"] = _tile_material(Color("#4a453c"), Color("#2c2924"), 0.96)
			if _materials["wall"] == null:
				_materials["wall"] = _panel_material(Color("#3a403f"), Color("#222624"), 0.84, 0.16)
			if _materials["wall_low"] == null:
				_materials["wall_low"] = _panel_material(Color("#2a2e2c"), Color("#171a18"), 0.88, 0.18)
			if _materials["corridor"] == null:
				_materials["corridor"] = _panel_material(Color("#343a38"), Color("#1c201e"), 0.86, 0.16)
			_materials["panel_line"] = _mat(Color("#141816"), 0.8)
			_materials["ledge"] = _mat(Color("#5c5348"), 0.7, 0.0, 0.12)
			_materials["frame"] = _mat(Color("#2a2c2a"), 0.62, 0.0, 0.22)
			_materials["ceiling"] = _mat(Color("#121416"), 0.9)
			_materials["beam"] = _mat(Color("#3e3830"), 0.72, 0.0, 0.18)
			_materials["light_bar"] = _mat(Color("#c5ced6"), 0.55, 0.28)
			_materials["trim"] = _mat(Color("#7a3018"), 0.62, 0.18)
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

	_apply_art_materials()

	# Shared semantic furniture & exterior materials (BUG-015: subtle brushed panel detail
	# so consoles/beds/cabinets match architectural walls instead of flat plastic).
	_materials["exterior"] = _mat(Color("#2b3440"), 0.7)
	_materials["exterior_lit"] = _mat(Color("#e9a24a"), 0.5, 1.6)
	_materials["ground"] = _tile_material(Color("#c9cfd8"), Color("#b6bec9"), 0.0)
	_materials["crate"] = _panel_material(Color("#cf782c"), Color("#9a5318"), 0.74, 0.08)
	_materials["crate_dark"] = _panel_material(Color("#3b4657"), Color("#262f3c"), 0.76, 0.16)
	_materials["metal_dark"] = _panel_material(Color("#2f3b4a"), Color("#1e2733"), 0.54, 0.28)
	if scene_type == "corridor":
		_materials["metal_dark"] = _panel_material(Color("#322c26"), Color("#1a1612"), 0.78, 0.2)
		_materials["crate"] = _panel_material(Color("#6a4e34"), Color("#3c2c1c"), 0.84, 0.05)
		_materials["crate_dark"] = _panel_material(Color("#3a342c"), Color("#221c16"), 0.86, 0.1)
	if scene_type == "corridor":
		_materials["metal_light"] = _panel_material(Color("#6a6258"), Color("#3e3832"), 0.7, 0.16)
	elif scene_type == "med_lab":
		_materials["metal_light"] = _panel_material(Color("#8ca0b0"), Color("#6b7f90"), 0.50, 0.24)
	else:
		_materials["metal_light"] = _panel_material(Color("#9aa9b8"), Color("#6b7f90"), 0.50, 0.24)
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
		var stub_light := get_node_or_null("CorridorLight") as OmniLight3D
		if stub_light != null:
			stub_light.light_energy = 0.12
			stub_light.light_color = Color("#6a3018")
	_build_corridor_industrial()


func _build_corridor_industrial() -> void:
	var h: float = room_max.y
	var z0: float = room_min.z + 0.45
	var z1: float = room_max.z - 0.45
	# Cable tray and stained pipes. Kenney pieces stay at the far bulkhead.
	_box_between("CableTray", Vector3(-0.58, h - 0.36, z0), Vector3(0.58, h - 0.24, z1), "beam")
	_box_between("CableTrayLipL", Vector3(-0.62, h - 0.4, z0), Vector3(-0.54, h - 0.2, z1), "metal_dark")
	_box_between("CableTrayLipR", Vector3(0.54, h - 0.4, z0), Vector3(0.62, h - 0.2, z1), "metal_dark")
	_box_between("PipeHighL", Vector3(room_min.x + 0.06, h - 0.62, z0), Vector3(room_min.x + 0.18, h - 0.48, z1), "metal_dark")
	_box_between("PipeHighR", Vector3(room_max.x - 0.18, h - 0.58, z0), Vector3(room_max.x - 0.06, h - 0.46, z1), "metal_light")
	_box_between("PipeMidL", Vector3(room_min.x + 0.04, 1.72, z0), Vector3(room_min.x + 0.1, 1.82, z1), "metal_dark")
	var drop_z: float = room_min.z + 1.8
	while drop_z < room_max.z - 1.2:
		_box_between("PipeDropL_%d" % int(drop_z * 10.0), Vector3(room_min.x + 0.05, 0.35, drop_z), Vector3(room_min.x + 0.12, h - 0.48, drop_z + 0.07), "metal_dark")
		_box_between("Hanger_%d" % int(drop_z * 10.0), Vector3(-0.08, h - 0.48, drop_z), Vector3(0.08, h - 0.24, drop_z + 0.05), "metal_dark")
		drop_z += 2.8
	# Worn floor plates. They sit a few millimetres off the deck so they do not z-fight.
	_box_between("WearPlateA", Vector3(-0.85, 0.004, 1.2), Vector3(0.35, 0.012, 3.4), "metal_dark", false)
	_box_between("WearPlateB", Vector3(-0.2, 0.004, -4.6), Vector3(1.15, 0.012, -2.4), "crate_dark", false)
	_box_between("WearPlateC", Vector3(-1.3, 0.004, -1.1), Vector3(-0.4, 0.01, 0.6), "metal_dark", false)
	if _character_script != null:
		_spawn_kenney(self, "structure_detailed.glb", Vector3(-1.72, 0.0, 6.15), 90.0, 2.15, false)
		_spawn_kenney(self, "structure.glb", Vector3(1.78, 0.0, 5.35), -80.0, 1.7, false)
		_spawn_kenney(self, "pipe_straight.glb", Vector3(-1.55, 2.35, 4.2), 0.0, 0.28, false)
		_spawn_kenney(self, "pipe_corner.glb", Vector3(1.62, 2.28, 3.4), 180.0, 0.42, false)
	_blast_glow_mat = _make_blast_material()
	_blast_glow = _make_blast_card("FarBlastGlow", Vector3(0.0, 1.55, room_max.z - 0.2), Vector3(1.05, 1.45, 0.08), _blast_glow_mat)
	_blast_glow_north_mat = _make_blast_material()
	_blast_glow_north = _make_blast_card("NorthBlastGlow", Vector3(0.0, 1.45, room_min.z - 0.95), Vector3(1.15, 1.7, 0.08), _blast_glow_north_mat)
	_blast_far = _make_action_omni("FarBlastLight", Vector3(0.0, 1.7, room_max.z - 0.55), 5.5, Color("#ff6418"))
	_blast_north = _make_action_omni("NorthBlastLight", Vector3(0.0, 1.45, room_min.z - 1.15), 4.4, Color("#ff5c1c"))
	_spark_light = _make_action_omni("MachineSpark", Vector3(0.45, 1.15, -4.2), 2.2, Color("#ffd0a0"))
	_threat_light = _make_action_omni("ThreatLamp", Vector3(0.15, 1.7, room_min.z - 1.35), 3.6, Color("#ff2418"))
	var machine_glow := _make_action_omni("MachineRed", Vector3(0.82, 1.05, -4.55), 2.6, Color("#ff2a18"))
	machine_glow.light_energy = 0.55


func _make_blast_material() -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(0.28, 0.07, 0.02)
	mat.roughness = 0.8
	mat.emission_enabled = true
	mat.emission = Color("#ff5a16")
	mat.emission_energy_multiplier = 0.0
	return mat


func _make_blast_card(label: String, pos: Vector3, size: Vector3, mat: StandardMaterial3D) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = label
	var mesh := BoxMesh.new()
	mesh.size = size
	mi.mesh = mesh
	mi.position = pos
	mi.material_override = mat
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	mi.visible = false
	add_child(mi)
	return mi


func _make_action_omni(label: String, pos: Vector3, range_m: float, color: Color) -> OmniLight3D:
	var light := OmniLight3D.new()
	light.name = label
	light.position = pos
	light.omni_range = range_m
	light.omni_attenuation = 1.85
	light.light_color = color
	light.light_energy = 0.0
	light.shadow_enabled = false
	add_child(light)
	return light


func _set_action_omni(light: OmniLight3D, energy: float) -> void:
	if light != null and is_instance_valid(light):
		light.light_energy = maxf(energy, 0.0)


func _drive_corridor_action_lights(state: Dictionary) -> void:
	if scene_type != "corridor":
		return
	var t_s: float = float(state.get("time_s", 0.0))
	var impact: float = float(state.get("table_impact", 0.0))
	var age: float = float(state.get("table_impact_age_s", 0.0))
	var pulse: float = exp(-age * 0.8) if impact > 0.02 else 0.0
	_set_action_omni(_blast_far, 0.0)
	_set_action_omni(_blast_north, 0.0)
	_set_action_omni(_spark_light, 0.0)
	if _blast_glow != null and is_instance_valid(_blast_glow):
		_blast_glow.visible = false
	if _blast_glow_north != null and is_instance_valid(_blast_glow_north):
		_blast_glow_north.visible = false
	if _blast_glow_mat != null:
		_blast_glow_mat.emission_energy_multiplier = 0.0
	if _blast_glow_north_mat != null:
		_blast_glow_north_mat.emission_energy_multiplier = 0.0
	if impact > 0.02 and t_s < 8.0:
		var energy: float = (0.55 + 1.7 * pulse) * impact
		_set_action_omni(_blast_far, energy * 2.2)
		if _blast_glow != null and is_instance_valid(_blast_glow):
			_blast_glow.visible = true
		if _blast_glow_mat != null:
			_blast_glow_mat.emission = Color("#ff6418")
			_blast_glow_mat.emission_energy_multiplier = 0.45 + 1.05 * pulse
	elif impact > 0.02 and t_s < 19.0:
		var spark: float = exp(-age * 7.0) * impact
		_set_action_omni(_spark_light, 0.8 + 4.2 * spark)
	elif impact > 0.02:
		var north_energy: float = (0.4 + 1.35 * pulse) * impact
		_set_action_omni(_blast_north, north_energy * 1.8)
		if _blast_glow_north != null and is_instance_valid(_blast_glow_north):
			_blast_glow_north.visible = true
		if _blast_glow_north_mat != null:
			_blast_glow_north_mat.emission = Color("#ff5c1c")
			_blast_glow_north_mat.emission_energy_multiplier = 0.7 + 1.5 * pulse
	var radio := false
	var active: Variant = state.get("active_events", [])
	if active is Array:
		for item: Variant in active as Array:
			if str(item) == "robot_speaking":
				radio = true
	if _threat_light != null and is_instance_valid(_threat_light):
		_threat_light.light_energy = 2.1 if radio else 0.0
		_threat_light.light_color = Color("#ff2418")


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
	_attach_sliding_door("BackDoor", Vector3(door_x, door_height * 0.5, back - 0.045), door_width, door_height, Vector3.RIGHT, Vector3(0.0, 0.0, -1.0))


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
	var face_n := Vector3(0.0, 0.0, 1.0 if z_max >= z_min else -1.0)
	var face_z: float = z_max + (0.045 if face_n.z > 0.0 else -0.045)
	_attach_sliding_door("%sDoor" % prefix, Vector3(dx, dh * 0.5, face_z), dw, dh, Vector3.RIGHT, face_n)


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
	if _wants_storm_surface(backdrop):
		_storm_exterior = true
		_build_planetary_storm()
	else:
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
	material.albedo_color = Color.WHITE
	screen.material_override = material
	_sky_backdrop_mats.append(material)
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
	_planet_mat = material
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
	material.albedo_color = Color.WHITE
	material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
	material.disable_fog = true
	plane.material_override = material
	_sky_backdrop_mats.append(material)
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
		if kind in ["console", "workstation", "command_table", "lab_bench", "workbench"]:
			_jolt_targets.append(holder)
			_jolt_home[holder.get_instance_id()] = holder.position
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
	var tint_name := str(cfg.get("tint", "")).strip_edges()
	if float(tint_value) > 0.0 or tint_name != "":
		var tint := Color.WHITE
		if tint_name != "":
			tint = Color(tint_name)
		for node: Node in _descendants(model):
			if node is MeshInstance3D:
				var mi := node as MeshInstance3D
				for s in mi.mesh.get_surface_count():
					var material: Material = mi.get_active_material(s)
					if material is StandardMaterial3D:
						var copy := (material as StandardMaterial3D).duplicate() as StandardMaterial3D
						if tint_name != "":
							copy.albedo_color = copy.albedo_color.lerp(tint, 0.78)
						if float(tint_value) > 0.0:
							copy.emission_enabled = true
							copy.emission = tint if tint_name != "" else copy.albedo_color
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
			if _wants_hero_industrial():
				_ensure_hero_materials()
				mat_main = "hero_gun"
			var mat_band: String = "crate_dark" if mat_main == "crate" else "trim"
			_child_box(holder, "Body", size, Vector3(0.0, size.y * 0.5, 0.0), mat_main)
			for band_y: float in [0.12, size.y - 0.12]:
				_child_box(holder, "Band_%d" % int(band_y * 100.0), Vector3(size.x + 0.02, 0.06, size.z + 0.02), Vector3(0.0, band_y, 0.0), mat_band)
			return size
		"console", "workstation":
			var size: Vector3 = _vec3(cfg.get("size", null), Vector3(1.6, 0.92, 0.72))
			if _wants_hero_industrial():
				_build_hero_console(holder, size)
				return Vector3(size.x, size.y + 0.62, size.z)
			if _wants_modular_scene() and _build_modular_console(holder, size):
				return Vector3(size.x, size.y + 0.62, size.z)
			var glow_key: String = "teal_glow" if scene_type == "med_lab" else "cyan_glow"
			_child_box(holder, "Base", Vector3(size.x * 0.9, size.y - 0.08, size.z * 0.8), Vector3(0.0, (size.y - 0.08) * 0.5, 0.0), "metal_dark")
			_child_box(holder, "Top", Vector3(size.x, 0.08, size.z), Vector3(0.0, size.y - 0.04, 0.0), "metal_light")
			_child_box(holder, "ScreenBezel", Vector3(size.x * 0.68, 0.52, 0.06), Vector3(0.0, size.y + 0.30, -size.z * 0.28), "frame")
			_child_dynamic_screen(holder, "ScreenPanel", Vector3(size.x * 0.62, 0.44, 0.02), Vector3(0.0, size.y + 0.30, -size.z * 0.24), "console")
			_child_box(holder, "KeyStrip", Vector3(size.x * 0.56, 0.02, 0.18), Vector3(0.0, size.y + 0.01, size.z * 0.14), glow_key, false)
			_child_status_led(holder, Vector3(size.x * 0.38, size.y + 0.03, size.z * 0.22))
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
			_child_status_led(holder, Vector3(size.x * 0.42, size.y + 0.04, size.z * 0.36))
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
			_child_status_led(holder, Vector3(size.x * 0.4, size.y + 0.03, size.z * 0.2))
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
	var merged: Dictionary = cfg.duplicate(true)
	if bool(_art_profile.get("enabled", false)):
		var over: Dictionary = _art_profile.get("lighting", {}) as Dictionary if _art_profile.get("lighting", {}) is Dictionary else {}
		for key_v: Variant in over.keys():
			var key := str(key_v)
			if key == "fills":
				if not cfg.has("fills"):
					merged["fills"] = over["fills"]
			elif key != "practicals":
				merged[key] = over[key_v]
	var sky_palette: Dictionary = _palette
	if scene_type == "corridor":
		sky_palette = {
			"sky_top": "#07080c",
			"horizon": "#12141a",
			"rock": "#0a0c10",
		}
	elif _storm_exterior:
		sky_palette = {
			"sky_top": "#1a222c",
			"horizon": "#3a4652",
			"rock": "#141a16",
		}
	var backdrop_opts := {
		"cyclorama": _cyclorama,
		"sky_color": Color("#07080c") if scene_type == "corridor" else (Color("#1a222c") if _storm_exterior else _sky_color),
		"palette": sky_palette,
	}
	_lighting_rig = LightingProfileScript.apply_profile(
		self,
		profile_name,
		room_min,
		room_max,
		merged,
		backdrop_opts
	)
	LightingProfileScript.apply_art_profile(_lighting_rig, _art_profile)
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
		"locked_carrier":
			bg = Color("#04140c")
			grid = Color("#0e3320")
			primary = Color("#3dff8a")
			accent = Color("#d8ffe8")
			alert_col = Color("#3dff8a")
		"impact_spike":
			bg = Color("#1a1208")
			grid = Color("#3a2a14")
			primary = Color("#ffcf4a")
			accent = Color("#ff5a2a")
			alert_col = Color("#ffcf4a")
		"power_flicker":
			bg = Color("#120e0c")
			grid = Color("#2a221c")
			primary = Color("#ff8844")
			accent = Color("#ff4428")
			alert_col = Color("#ff6622")
		"device_operating":
			bg = Color("#071820")
			grid = Color("#123040")
			primary = Color("#46e0ff")
			accent = Color("#9af6ff")
			alert_col = Color("#46e0ff")

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

	var force_wave := mode in ["locked_carrier", "impact_spike", "power_flicker"]
	if ui_kind == "tactical_map" and not force_wave:
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
			var draw_line := true
			if mode == "signal_lost":
				var drop: float = 0.0 if (u > 0.52 and u < 0.86) else sin(u * TAU * 4.5) * 14.0
				y_wave1 = clampi(62 + int(round(drop)), 30, 92)
				y_wave2 = clampi(114 + int(round(((x * 13) % 7 - 3) * (2.0 if u > 0.52 else 0.5))), 96, 146)
			elif mode == "sensor_error":
				var spike: float = sin(u * TAU * 6.0) * 16.0 + (18.0 if (x % 29) < 4 else 0.0)
				y_wave1 = clampi(62 - int(round(spike)), 28, 94)
				y_wave2 = clampi(114 + int(round(cos(u * TAU * 3.0) * 12.0)), 96, 146)
			elif mode == "locked_carrier":
				y_wave1 = clampi(64 - int(round(sin(u * TAU * 3.0) * 10.0)), 28, 94)
				y_wave2 = clampi(116 - int(round(sin(u * TAU * 6.0) * 6.0)), 96, 146)
			elif mode == "impact_spike":
				var spike_i := 0.0
				if u > 0.28 and u < 0.48:
					spike_i = sin((u - 0.28) / 0.2 * PI) * 34.0
				y_wave1 = clampi(66 - int(round(spike_i + sin(u * TAU * 2.0) * 4.0)), 26, 96)
				y_wave2 = clampi(118 + int(round(sin(u * TAU * 5.0) * 8.0)), 96, 146)
			elif mode == "power_flicker":
				draw_line = (x % 19) >= 5
				y_wave1 = clampi(70 + int(round(sin(u * TAU * 1.5) * 6.0)), 30, 94)
				y_wave2 = clampi(120, 96, 146)
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
			if draw_line:
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
	# Dark drifting cloud deck outside the panoramic window, plus a lightning sheet.
	_storm_band = _make_storm_band("StormFrontBand", Vector2(26.0, 5.2), Vector3(0.0, 2.4, room_min.z - 11.5), Color(0.16, 0.18, 0.22, 1.0))
	_make_storm_band("StormDeckHigh", Vector2(32.0, 3.4), Vector3(-1.5, 4.6, room_min.z - 16.0), Color(0.08, 0.09, 0.12, 1.0))
	_make_storm_band("StormDeckLow", Vector2(22.0, 2.6), Vector3(2.0, 1.15, room_min.z - 8.5), Color(0.12, 0.13, 0.16, 1.0))
	_make_storm_band("StormVeilA", Vector2(42.0, 9.0), Vector3(0.0, 3.4, room_min.z - 6.4), Color(0.05, 0.06, 0.10, 1.0))
	_make_storm_band("StormVeilB", Vector2(36.0, 7.0), Vector3(1.4, 2.1, room_min.z - 9.2), Color(0.06, 0.07, 0.11, 1.0))
	var flash := MeshInstance3D.new()
	flash.name = "StormLightningSheet"
	var flash_quad := QuadMesh.new()
	flash_quad.size = Vector2(9.0, 4.2)
	flash.mesh = flash_quad
	flash.position = Vector3(-2.4, 3.4, room_min.z - 9.5)
	flash.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var flash_mat := StandardMaterial3D.new()
	flash_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	flash_mat.albedo_color = Color(0.72, 0.8, 0.9, 1.0)
	flash_mat.emission_enabled = true
	flash_mat.emission = Color(0.72, 0.82, 0.95)
	flash_mat.emission_energy_multiplier = 1.15
	flash.material_override = flash_mat
	flash.visible = false
	add_child(flash)
	_lightning_flash = flash
	var bolt := OmniLight3D.new()
	bolt.name = "StormLightningFill"
	bolt.position = Vector3(0.0, 1.8, room_min.z + 1.15)
	bolt.omni_range = maxf(room_max.x - room_min.x, 8.0)
	bolt.omni_attenuation = 0.85
	bolt.light_color = Color("#d7ecff")
	bolt.light_energy = 0.0
	bolt.shadow_enabled = false
	add_child(bolt)
	_lightning_light = bolt


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
	if screen_mode == "locked_carrier":
		scr_energy = 1.7
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

	var stutter: float = LightingProfileScript.power_stutter_mul(t_s, float(state.get("power_flicker", 0.0)))
	var flicker: float = clampf(float(state.get("power_flicker", 0.0)), 0.0, 1.0)
	if _light_bar_mat != null:
		_light_bar_mat.emission_energy_multiplier = _light_bar_base * stutter
	for emerg: StandardMaterial3D in _emergency_mats:
		if flicker > 0.01:
			var dip := clampf(stutter, 0.15, 0.35)
			emerg.emission = Color("#8e2414").lerp(Color("#c45a18"), 0.45 if dip < 0.24 else 0.75)
			emerg.albedo_color = emerg.emission
			emerg.emission_energy_multiplier = lerpf(0.9, 1.05, 1.0 - dip) * flicker
		else:
			emerg.emission_energy_multiplier = 0.0
	for entry3: Dictionary in _dynamic_screens:
		var mat3: StandardMaterial3D = entry3["material"] as StandardMaterial3D
		mat3.emission_energy_multiplier = scr_energy * stutter

	if scene_type == "corridor" and flicker > 0.01:
		for emerg_rail: StandardMaterial3D in _emergency_mats:
			emerg_rail.emission_energy_multiplier = maxf(emerg_rail.emission_energy_multiplier, 1.35)
	_advance_doors(state, t_s)
	_advance_array(state, t_s)
	_advance_impact(state)
	_advance_storm(storm_intensity, t_s)
	_drive_corridor_action_lights(state)


func get_event_visual_state() -> Dictionary:
	var beacon := Color("#000000")
	if _array_beacon_mat != null:
		beacon = _array_beacon_mat.albedo_color
	var led := Color("#000000")
	if not _module_leds.is_empty():
		led = _module_leds[0].albedo_color
	var emerg := 0.0
	if not _emergency_mats.is_empty():
		emerg = _emergency_mats[0].emission_energy_multiplier
	var bar_energy := 0.0
	if _light_bar_mat != null:
		bar_energy = _light_bar_mat.emission_energy_multiplier
	var jolt := 0.0
	if not _jolt_targets.is_empty() and is_instance_valid(_jolt_targets[0]):
		var home: Vector3 = _jolt_home.get(_jolt_targets[0].get_instance_id(), _jolt_targets[0].position) as Vector3
		jolt = _jolt_targets[0].position.distance_to(home)
	return {
		"door_amount": snappedf(_door_amount, 0.001),
		"array_pitch_deg": snappedf(_array_pitch_deg, 0.1),
		"array_yaw_deg": snappedf(_array_yaw_deg, 0.1),
		"array_lock": snappedf(_array_lock, 0.001),
		"beacon_r": snappedf(beacon.r, 0.001),
		"beacon_g": snappedf(beacon.g, 0.001),
		"beam_visible": _array_beam != null and is_instance_valid(_array_beam) and _array_beam.visible,
		"emergency_energy": snappedf(emerg, 0.001),
		"light_bar_energy": snappedf(bar_energy, 0.001),
		"light_bar_base": snappedf(_light_bar_base, 0.001),
		"lightning": snappedf(_lightning_energy, 0.001),
		"led_r": snappedf(led.r, 0.001),
		"led_g": snappedf(led.g, 0.001),
		"jolt_m": snappedf(jolt, 0.0001),
		"screen_mode": _last_event_screen_mode,
		"dressing_nodes": _dressing_count,
		"art_enabled": bool(_art_profile.get("enabled", false)),
		"art_preset": str(_art_profile.get("preset", "")),
	}


func _ingest_art_profile(set_cfg: Dictionary) -> void:
	var raw: Variant = set_cfg.get("art_profile", {})
	_art_profile = RetroArtScript.resolve(raw)


func _apply_art_materials() -> void:
	if bool(_art_profile.get("enabled", false)):
		_materials["floor"] = RetroArtScript.make_material("floor_plate", _art_profile, 0.9, 0.06)
		_materials["wall"] = RetroArtScript.make_material("wall_plate", _art_profile, 0.82, 0.12)
		_materials["wall_low"] = RetroArtScript.make_material("wall_plate", _art_profile, 0.86, 0.1)
		(_materials["wall_low"] as StandardMaterial3D).albedo_color = Color(0.78, 0.8, 0.76)
		_materials["corridor"] = RetroArtScript.make_material("wall_plate", _art_profile, 0.84, 0.1)
		_materials["ceiling"] = RetroArtScript.make_material("metal", _art_profile, 0.78, 0.18)
		(_materials["ceiling"] as StandardMaterial3D).albedo_color = Color(0.62, 0.63, 0.6)
		_materials["beam"] = RetroArtScript.make_material("metal", _art_profile, 0.62, 0.35)
		_materials["frame"] = RetroArtScript.make_material("metal", _art_profile, 0.55, 0.42)
		_materials["metal_dark"] = RetroArtScript.make_material("metal", _art_profile, 0.58, 0.38)
		_materials["metal_light"] = RetroArtScript.make_material("metal", _art_profile, 0.5, 0.28)
		(_materials["metal_light"] as StandardMaterial3D).albedo_color = Color(0.86, 0.87, 0.82)
		_materials["hazard"] = RetroArtScript.make_material("hazard", _art_profile, 0.72, 0.04)
		_materials["conduit"] = RetroArtScript.make_material("conduit", _art_profile, 0.7, 0.16)
		_materials["sign"] = RetroArtScript.make_material("sign", _art_profile, 0.68, 0.02)
		_materials["trim"] = RetroArtScript.make_material("hazard", _art_profile, 0.6, 0.08)
	_cache_light_bar()


func _cache_light_bar() -> void:
	var mat_v: Variant = _materials.get("light_bar", null)
	if mat_v is StandardMaterial3D:
		_light_bar_mat = mat_v as StandardMaterial3D
		_light_bar_base = _light_bar_mat.emission_energy_multiplier


func _dress_wants(key: String) -> bool:
	var spec: Variant = _art_profile.get("environment_dressing_spec", {})
	if not spec is Dictionary or (spec as Dictionary).is_empty():
		return true
	return bool((spec as Dictionary).get(key, false))


func _build_environment_dressing() -> void:
	if _wants_modular_scene():
		return
	var z0: float = room_min.z + 0.55
	var z1: float = room_max.z - 0.55
	if z1 - z0 < 1.0:
		return
	var pipes := _dress_wants("exposed_pipes") or _dress_wants("heavy_cables")
	var hazards := _dress_wants("hazard_stripes")
	var worn := _dress_wants("worn_panels") or _dress_wants("worn_machinery")
	var pipe_y: float = minf(room_max.y - 0.08, maxf(camera_max.y + 0.05, room_max.y - 0.14))
	var x_l: float = room_min.x + 0.18
	var x_r: float = room_max.x - 0.18
	if pipes:
		_dress_box("PipeL", Vector3(x_l - 0.045, pipe_y - 0.045, z0), Vector3(x_l + 0.045, pipe_y + 0.045, z1), "conduit")
		_dress_box("PipeR", Vector3(x_r - 0.045, pipe_y - 0.045, z0), Vector3(x_r + 0.045, pipe_y + 0.045, z1), "conduit")
		var z: float = z0 + 0.45
		while z < z1:
			_dress_box("HangerL", Vector3(x_l - 0.025, pipe_y - 0.32, z), Vector3(x_l + 0.025, pipe_y, z + 0.05), "metal_dark")
			_dress_box("HangerR", Vector3(x_r - 0.025, pipe_y - 0.32, z), Vector3(x_r + 0.025, pipe_y, z + 0.05), "metal_dark")
			z += 2.4
		_dress_box("PipeCross", Vector3(x_l, pipe_y - 0.16, room_max.z - 0.72), Vector3(x_r, pipe_y - 0.08, room_max.z - 0.6), "conduit")
		var cy: float = clampf(room_max.y * 0.62, 1.55, room_max.y - 0.55)
		_dress_box("ConduitL", Vector3(room_min.x + 0.012, cy - 0.035, z0), Vector3(room_min.x + 0.07, cy + 0.035, z1), "conduit")
		_dress_box("ConduitR", Vector3(room_max.x - 0.07, cy - 0.035, z0), Vector3(room_max.x - 0.012, cy + 0.035, z1), "conduit")
		_dress_box("DropFL", Vector3(room_min.x + 0.012, 0.4, room_min.z + 0.28), Vector3(room_min.x + 0.055, cy, room_min.z + 0.35), "conduit")
		_dress_box("DropBR", Vector3(room_max.x - 0.055, 0.4, room_max.z - 0.62), Vector3(room_max.x - 0.012, cy, room_max.z - 0.54), "conduit")
	var cy_sign: float = clampf(room_max.y * 0.62, 1.55, room_max.y - 0.55)
	if hazards:
		_dress_box("HazardL", Vector3(room_min.x + 0.1, 0.004, z0), Vector3(room_min.x + 0.28, 0.012, z1), "hazard")
		_dress_box("HazardR", Vector3(room_max.x - 0.28, 0.004, z0), Vector3(room_max.x - 0.1, 0.012, z1), "hazard")
		_dress_box("SignL", Vector3(room_min.x + 0.02, 1.55, (z0 + z1) * 0.5 - 0.18), Vector3(room_min.x + 0.045, 1.86, (z0 + z1) * 0.5 + 0.18), "sign")
		_dress_box("SignR", Vector3(room_max.x - 0.045, 1.48, (z0 + z1) * 0.42 - 0.16), Vector3(room_max.x - 0.02, 1.78, (z0 + z1) * 0.42 + 0.16), "sign")
	if worn:
		var vy: float = minf(room_max.y - 0.38, maxf(cy_sign + 0.28, 2.05))
		_dress_box("VentL", Vector3(room_min.x + 0.008, vy, room_min.z + 1.05), Vector3(room_min.x + 0.04, vy + 0.26, room_min.z + 1.7), "metal_dark")
		_dress_box("VentR", Vector3(room_max.x - 0.04, vy, room_max.z - 1.9), Vector3(room_max.x - 0.008, vy + 0.26, room_max.z - 1.25), "metal_dark")
		_dress_clutter(Vector3(room_min.x + 0.05, 0.0, room_max.z - 0.05), "BackLeft")
		_dress_clutter(Vector3(room_max.x - 0.05, 0.0, room_min.z + 0.2), "FrontRight")
		if room_max.x - room_min.x > 3.4:
			var mx: float = room_max.x - 0.02
			var mz: float = (room_min.z + room_max.z) * 0.15
			_dress_box("MachineBody", Vector3(mx - 0.2, 0.0, mz - 0.22), Vector3(mx - 0.04, 0.7, mz + 0.22), "metal_dark")
			_dress_box("MachineHead", Vector3(mx - 0.22, 0.7, mz - 0.12), Vector3(mx - 0.04, 0.86, mz + 0.12), "frame")


func _dress_box(label: String, a: Vector3, b: Vector3, mat_key: String) -> void:
	if not _materials.has(mat_key):
		return
	_box_between(label, a, b, mat_key, false)
	_dressing_count += 1


func _dress_clutter(origin: Vector3, tag: String) -> void:
	var center := (room_min + room_max) * 0.5
	var ix: float = 1.0 if origin.x < center.x else -1.0
	var iz: float = 1.0 if origin.z < center.z else -1.0
	_dress_box("%sCrate" % tag, origin, origin + Vector3(ix * 0.2, 0.26, iz * 0.16), "metal_dark")
	_dress_box("%sCan" % tag, origin + Vector3(ix * 0.05, 0.26, iz * 0.04), origin + Vector3(ix * 0.12, 0.38, iz * 0.1), "crate_dark")


func _attach_sliding_door(id: String, center: Vector3, width: float, height: float, slide_axis: Vector3, face_normal: Vector3) -> void:
	if width < 0.4 or height < 0.8:
		return
	var axis: Vector3 = slide_axis.normalized()
	var normal: Vector3 = face_normal.normalized()
	var leaf_w: float = maxf(width * 0.46, 0.18)
	var leaf_h: float = maxf(height - 0.08, 0.4)
	var size: Vector3 = axis.abs() * leaf_w + Vector3.UP * leaf_h + normal.abs() * 0.05
	var half: float = width * 0.25
	var travel: float = width * 0.46
	var closed_l: Vector3 = center - axis * half + normal * 0.01
	var open_l: Vector3 = closed_l - axis * travel
	var closed_r: Vector3 = center + axis * half + normal * 0.01
	var open_r: Vector3 = closed_r + axis * travel
	var mat_key := "metal_dark" if _materials.has("metal_dark") else "frame"
	_add_door_leaf("%sLeafL" % id, size, closed_l, open_l, mat_key)
	_add_door_leaf("%sLeafR" % id, size, closed_r, open_r, mat_key)
	var led := MeshInstance3D.new()
	led.name = "%sLED" % id
	var led_mesh := BoxMesh.new()
	led_mesh.size = Vector3(0.16, 0.05, 0.03)
	if absf(axis.z) > 0.5:
		led_mesh.size = Vector3(0.03, 0.05, 0.16)
	led.mesh = led_mesh
	led.position = center + Vector3.UP * (height * 0.5 + 0.1) + normal * 0.03
	led.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var led_mat := _mat(Color("#19d86a"), 0.4, 1.2)
	led.material_override = led_mat
	add_child(led)
	_door_leds.append(led_mat)


func _add_door_leaf(label: String, size: Vector3, closed_pos: Vector3, open_pos: Vector3, mat_key: String) -> void:
	var leaf := MeshInstance3D.new()
	leaf.name = label
	var mesh := BoxMesh.new()
	mesh.size = size
	leaf.mesh = mesh
	leaf.material_override = _materials[mat_key]
	leaf.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	leaf.position = closed_pos.lerp(open_pos, _door_amount)
	add_child(leaf)
	_door_leaves.append({"node": leaf, "closed": closed_pos, "open": open_pos})


func _sync_door_leaves() -> void:
	for entry: Dictionary in _door_leaves:
		var leaf: MeshInstance3D = entry.get("node", null) as MeshInstance3D
		if leaf == null or not is_instance_valid(leaf):
			continue
		var closed_pos: Vector3 = entry["closed"] as Vector3
		var open_pos: Vector3 = entry["open"] as Vector3
		leaf.position = closed_pos.lerp(open_pos, _door_amount)
	var led_col := Color("#19d86a") if _door_amount > 0.82 else (Color("#ff3a2a") if _door_amount < 0.18 else Color("#ff9a1f"))
	for led_mat: StandardMaterial3D in _door_leds:
		led_mat.albedo_color = led_col
		led_mat.emission = led_col
		led_mat.emission_energy_multiplier = 1.35


func _advance_doors(state: Dictionary, t_s: float) -> void:
	var dt: float = t_s - _mech_clock
	if _mech_clock < 0.0:
		dt = 1.0 / 24.0
	elif dt < 0.0:
		dt = 0.0
	elif dt > 0.5:
		dt = 0.5
	_mech_clock = t_s
	if state.has("door_command"):
		var cmd := str(state.get("door_command", "hold"))
		if cmd == "open":
			_door_target = 1.0
		elif cmd == "close":
			_door_target = 0.0
	_door_amount = move_toward(_door_amount, _door_target, dt * 1.7)
	_sync_door_leaves()


func _build_emergency_power_strips() -> void:
	var z0: float = room_min.z + 0.35
	var z1: float = room_max.z - 0.35
	if z1 <= z0 + 0.4:
		return
	var mat := _mat(Color("#8e2414"), 0.48, 0.0)
	mat.emission_enabled = true
	mat.emission = Color("#8e2414")
	mat.emission_energy_multiplier = 0.0
	var y: float = room_max.y - 0.05
	for side: float in [-1.0, 1.0]:
		var x: float = room_min.x + 0.06 if side < 0.0 else room_max.x - 0.06
		var mi := MeshInstance3D.new()
		mi.name = "EmergencyStrip%d" % int(side)
		var mesh := BoxMesh.new()
		mesh.size = Vector3(0.035, 0.025, z1 - z0)
		mi.mesh = mesh
		mi.position = Vector3(x, y, (z0 + z1) * 0.5)
		mi.material_override = mat
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(mi)
	_emergency_mats.append(mat)
	if scene_type != "corridor":
		return
	var rail := _mat(Color("#a32014"), 0.55, 0.0)
	rail.emission_enabled = true
	rail.emission = Color("#c42816")
	rail.emission_energy_multiplier = 0.0
	for side: float in [-1.0, 1.0]:
		var rail_x: float = room_min.x + 0.05 if side < 0.0 else room_max.x - 0.05
		var rail_mi := MeshInstance3D.new()
		rail_mi.name = "EmergencyRail%d" % int(side)
		var rail_mesh := BoxMesh.new()
		rail_mesh.size = Vector3(0.045, 0.055, z1 - z0)
		rail_mi.mesh = rail_mesh
		rail_mi.position = Vector3(rail_x, 1.38, (z0 + z1) * 0.5)
		rail_mi.material_override = rail
		rail_mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(rail_mi)
	_emergency_mats.append(rail)


func _build_exterior_array() -> void:
	var root := Node3D.new()
	root.name = "ExteriorArrayRoot"
	root.position = Vector3((room_min.x + room_max.x) * 0.5, 0.0, room_min.z - 3.35)
	add_child(root)
	_array_root = root
	var modular_mast := false
	if _wants_modular_scene() and not _wants_hero_industrial():
		modular_mast = _spawn_kenney(root, "supports_high.glb", Vector3.ZERO, 0.0, 2.2, false) != null
		_spawn_kenney(root, "satelliteDish_detailed.glb", Vector3(1.25, 0.0, 0.35), 25.0, 1.15, false)
	if not modular_mast:
		_child_box(root, "Plinth", Vector3(0.72, 0.16, 0.72), Vector3(0.0, 0.08, 0.0), "frame")
		_child_box(root, "Mast", Vector3(0.12, 2.35, 0.12), Vector3(0.0, 1.28, 0.0), "metal_dark")
	var yaw := Node3D.new()
	yaw.name = "YawPivot"
	yaw.position = Vector3(0.0, 2.42, 0.0)
	root.add_child(yaw)
	_array_yaw_pivot = yaw
	var pitch := Node3D.new()
	pitch.name = "PitchPivot"
	yaw.add_child(pitch)
	_array_pitch_pivot = pitch
	var dish_glb: Node3D = null
	if _wants_modular_scene() and not _wants_hero_industrial():
		dish_glb = _spawn_kenney(pitch, "satelliteDish_large.glb", Vector3.ZERO, 0.0, 1.85, false, false)
		if dish_glb != null:
			# Bowl faces the bay so the window camera sees the reflector, not the housing.
			dish_glb.rotation_degrees = Vector3(-72.0, 180.0, 0.0)
			dish_glb.name = "Dish"
	if dish_glb == null:
		var dish := MeshInstance3D.new()
		dish.name = "Dish"
		_ensure_hero_materials()
		var cyl := CylinderMesh.new()
		cyl.top_radius = 0.92
		cyl.bottom_radius = 0.16
		cyl.height = 0.22
		cyl.radial_segments = 24
		dish.mesh = cyl
		dish.rotation_degrees = Vector3(-76.0, 180.0, 0.0)
		dish.material_override = _materials["hero_steel"] if _materials.has("hero_steel") else _materials["frame"]
		pitch.add_child(dish)
		for rib in 6:
			var rib_mi := MeshInstance3D.new()
			rib_mi.name = "DishRib%d" % rib
			var rib_mesh := BoxMesh.new()
			rib_mesh.size = Vector3(0.025, 0.86, 0.02)
			rib_mi.mesh = rib_mesh
			rib_mi.position = Vector3(0.0, 0.02, 0.04)
			rib_mi.rotation_degrees = Vector3(0.0, 0.0, rib * 30.0)
			rib_mi.material_override = _materials["hero_gun"] if _materials.has("hero_gun") else _materials["metal_dark"]
			dish.add_child(rib_mi)
		_child_box(pitch, "FeedBoom", Vector3(0.04, 0.04, 0.34), Vector3(0.0, 0.0, 0.2), "hero_gun" if _materials.has("hero_gun") else "frame", false)
		_child_box(pitch, "FeedHorn", Vector3(0.1, 0.1, 0.08), Vector3(0.0, 0.0, 0.36), "hero_olive" if _materials.has("hero_olive") else "frame", false)
		_child_box(root, "TrussA", Vector3(0.06, 2.2, 0.06), Vector3(-0.28, 1.15, -0.18), "hero_steel" if _materials.has("hero_steel") else "metal_dark")
		_child_box(root, "TrussB", Vector3(0.06, 2.2, 0.06), Vector3(0.28, 1.15, -0.18), "hero_steel" if _materials.has("hero_steel") else "metal_dark")
		_child_box(root, "TrussC", Vector3(0.06, 2.2, 0.06), Vector3(0.0, 1.15, 0.26), "hero_steel" if _materials.has("hero_steel") else "metal_dark")
		_child_box(root, "TrussBrace", Vector3(0.62, 0.04, 0.04), Vector3(0.0, 1.35, 0.0), "hero_gun" if _materials.has("hero_gun") else "frame")
		_child_box(root, "ElevationPiston", Vector3(0.07, 0.9, 0.07), Vector3(0.16, 1.55, 0.08), "hero_olive" if _materials.has("hero_olive") else "metal_dark")
	var feed := MeshInstance3D.new()
	feed.name = "Feed"
	var feed_mesh := BoxMesh.new()
	feed_mesh.size = Vector3(0.06, 0.06, 0.28)
	feed.mesh = feed_mesh
	feed.position = Vector3(0.0, 0.0, 0.28)
	feed.material_override = _materials["frame"]
	pitch.add_child(feed)
	var beam := MeshInstance3D.new()
	beam.name = "UplinkBeam"
	var beam_mesh := TorusMesh.new()
	beam_mesh.inner_radius = 0.08
	beam_mesh.outer_radius = 0.13
	beam_mesh.rings = 8
	beam_mesh.ring_segments = 16
	beam.mesh = beam_mesh
	beam.position = Vector3(0.0, 0.0, 0.36)
	beam.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var beam_mat := _mat(Color("#8fd8c4"), 0.4, 0.12)
	beam_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	beam_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	beam_mat.albedo_color = Color(0.55, 0.82, 0.72, 0.06)
	beam_mat.emission_energy_multiplier = 0.0
	beam.material_override = beam_mat
	beam.visible = false
	pitch.add_child(beam)
	_array_beam = beam
	_array_beam_mat = beam_mat
	_beacon_lamps = _add_array_beacons(root)
	var sparks := Node3D.new()
	sparks.name = "ArraySparks"
	sparks.position = Vector3(0.2, 2.2, 0.1)
	root.add_child(sparks)
	var spark_mat := _mat(Color("#ffd2a1"), 0.3, 2.2)
	for i in 3:
		var spark := MeshInstance3D.new()
		spark.name = "Spark%d" % i
		var spark_mesh := BoxMesh.new()
		spark_mesh.size = Vector3(0.035, 0.035, 0.035)
		spark.mesh = spark_mesh
		spark.position = Vector3(0.05 * float(i - 1), 0.04 * float(i), 0.03 * float(i))
		spark.material_override = spark_mat
		spark.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		sparks.add_child(spark)
	sparks.visible = false
	_array_sparks = sparks
	_apply_array_pose()


func _apply_array_pose() -> void:
	if _array_yaw_pivot != null and is_instance_valid(_array_yaw_pivot):
		_array_yaw_pivot.rotation.y = deg_to_rad(_array_yaw_deg)
	if _array_pitch_pivot != null and is_instance_valid(_array_pitch_pivot):
		_array_pitch_pivot.rotation.x = deg_to_rad(_array_pitch_deg)


func _advance_array(state: Dictionary, t_s: float) -> void:
	if _array_root == null:
		return
	var restore: float = float(state.get("array_restore", 0.0))
	var restore_u: float = clampf(float(state.get("array_restore_u", 0.0)), 0.0, 1.0)
	var fault: bool = bool(state.get("array_fault", false))
	var fault_u: float = clampf(float(state.get("array_fault_u", 1.0 if fault else 0.0)), 0.0, 1.0)
	_array_fault_now = fault and restore < 0.05
	if restore > 0.05:
		var eased: float = restore_u * restore_u * (3.0 - 2.0 * restore_u)
		var from_pitch: float = 46.0 if _array_was_fault else -6.0
		_array_pitch_deg = lerpf(from_pitch, -22.0, eased)
		_array_yaw_deg = lerpf(18.0, 0.0, eased)
		_array_lock = eased
		if eased > 0.85:
			_array_held_lock = true
			_array_was_fault = false
	elif fault:
		_array_was_fault = true
		_array_held_lock = false
		_array_lock = 0.0
		var eased_fault: float = fault_u * fault_u * (3.0 - 2.0 * fault_u)
		_array_pitch_deg = lerpf(-6.0, 46.0, eased_fault)
		_array_yaw_deg = lerpf(8.0, 24.0, eased_fault)
	elif _array_was_fault and not _array_held_lock:
		pass
	elif not _array_held_lock:
		_array_pitch_deg = -6.0
		_array_yaw_deg = 8.0
		_array_lock = 0.0
	_apply_array_pose()
	_sync_array_beacons(t_s)
	if _array_beam != null:
		var show_beam: bool = _array_lock > 0.72 or (_array_held_lock and not _array_fault_now)
		_array_beam.visible = show_beam
		if show_beam and _array_beam_mat != null:
			var alpha_u: float = _array_lock if _array_lock > 0.05 else 0.9
			_array_beam_mat.albedo_color = Color(0.62, 0.86, 0.74, 0.05 + 0.07 * alpha_u)
			_array_beam_mat.emission = Color("#8fd8c4")
			_array_beam_mat.emission_energy_multiplier = 0.08 + 0.16 * alpha_u
	if _array_sparks != null:
		_array_sparks.visible = _array_fault_now and sin(t_s * 36.0) > 0.0


func _advance_impact(state: Dictionary) -> void:
	var table_i: float = float(state.get("table_impact", 0.0))
	var mod_i: float = float(state.get("module_install", 0.0))
	var impact_i: float = maxf(table_i, mod_i * 0.8)
	var age: float = float(state.get("table_impact_age_s", 0.0)) if table_i >= mod_i else float(state.get("module_install_age_s", 0.0))
	var env: float = exp(-age * 8.0) if impact_i > 0.02 else 0.0
	for node: Node3D in _jolt_targets:
		if node == null or not is_instance_valid(node):
			continue
		var home: Vector3 = _jolt_home.get(node.get_instance_id(), node.position) as Vector3
		if impact_i > 0.02:
			node.position = home + Vector3(0.0, -0.012 * env * impact_i, 0.028 * env * impact_i)
		else:
			node.position = home
	var install_u: float = float(state.get("module_install_u", 0.0))
	if mod_i > 0.05:
		if install_u < 0.35:
			set_module_slot_state("aligning")
		elif install_u < 0.62:
			set_module_slot_state("locked")
		else:
			set_device_online(true)
	elif table_i > 0.05:
		for led: StandardMaterial3D in _module_leds:
			led.albedo_color = Color("#ffb03a")
			led.emission = Color("#ffb03a")
			led.emission_energy_multiplier = 1.15
	else:
		_apply_module_leds()


func _advance_storm(storm_intensity: float, t_s: float) -> void:
	var flash: float = LightingProfileScript.storm_flash_amount(t_s, storm_intensity)
	_lightning_energy = flash
	if _lightning_light != null and is_instance_valid(_lightning_light):
		# Interior spill stays off. The rig's window directional owns the bolt.
		_lightning_light.light_energy = 0.0
		_lightning_light.position = Vector3((window_x.x + window_x.y) * 0.5, 2.1, room_min.z - 1.4)
		_lightning_light.omni_range = 4.5
	for streak: MeshInstance3D in _rain_streaks:
		if streak != null and is_instance_valid(streak):
			var mat: StandardMaterial3D = streak.material_override as StandardMaterial3D
			if mat != null:
				mat.uv1_offset.y = -fmod(t_s * 1.35 + float(streak.get_meta("rain_phase", 0.0)), 1.0)
	if _lightning_flash != null and is_instance_valid(_lightning_flash):
		_lightning_flash.visible = flash > 0.08
	var index := 0
	for cloud: MeshInstance3D in _storm_clouds:
		if cloud == null or not is_instance_valid(cloud):
			index += 1
			continue
		cloud.visible = storm_intensity > 0.02
		if cloud.visible:
			cloud.position.x = sin(t_s * 0.22 + float(index)) * (1.5 + float(index) * 0.45)
			var sm: StandardMaterial3D = cloud.material_override as StandardMaterial3D
			if sm != null:
				var storm_u: float = clampf(storm_intensity, 0.0, 1.0)
				var darkness: float = lerpf(0.14, 0.045, storm_u) + 0.012 * float(index)
				var alpha: float = clampf(0.35 + storm_u * 0.58, 0.0, 0.96)
				if storm_intensity > 0.1:
					alpha = clampf(0.62 + storm_u * 0.32, 0.0, 0.97)
				sm.albedo_color = Color(darkness * 0.75, darkness * 0.82, darkness + 0.06, alpha)
		index += 1
	if _storm_band != null and is_instance_valid(_storm_band):
		_storm_band.visible = storm_intensity > 0.02
	_apply_storm_sky(storm_intensity, flash)


func _make_storm_band(node_name: String, size: Vector2, pos: Vector3, tint: Color) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.name = node_name
	var quad := QuadMesh.new()
	quad.size = size
	mi.mesh = quad
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var image := Image.create(128, 64, false, Image.FORMAT_RGBA8)
	for y in 64:
		var vy: float = float(y) / 63.0
		var v_fade: float = sin(vy * PI)
		for x in 128:
			var vx: float = float(x) / 127.0
			var cloud: float = 0.55 + 0.28 * sin(vx * TAU * 2.2 + vy * 4.0) + 0.17 * cos(vx * TAU * 5.0 - vy * 2.0)
			var alpha: float = clampf(v_fade * cloud * 0.7, 0.0, 0.78)
			image.set_pixel(x, y, Color(tint.r, tint.g, tint.b, alpha))
	var texture := ImageTexture.create_from_image(image)
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = texture
	mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.albedo_color = Color(tint.r, tint.g, tint.b, 0.0)
	mi.material_override = mat
	mi.visible = false
	add_child(mi)
	_storm_clouds.append(mi)
	return mi


func _child_status_led(parent: Node3D, pos: Vector3) -> void:
	var mi := MeshInstance3D.new()
	mi.name = "StatusLED"
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.06, 0.035, 0.02)
	mi.mesh = mesh
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var mat := _mat(Color("#1c6e78"), 0.42, 0.45)
	mat.set_meta("rest_color", Color("#1c6e78"))
	mat.set_meta("rest_energy", 0.45)
	mi.material_override = mat
	parent.add_child(mi)
	_module_leds.append(mat)


## World position of the subject plus an interior camera origin and look direction.
## Keys: event_type, position, view_origin, view_direction.
## array_restore -> exterior dish, module_install/table_impact -> ModuleSlot,
## door_open -> bulkhead, storm_approaching -> window vista.
func get_event_focus_point(event_type: String) -> Dictionary:
	var key := event_type.strip_edges().to_lower()
	var position := Vector3.ZERO
	var origin := Vector3.ZERO
	match key:
		"array_restore":
			position = _array_focus_position()
			origin = Vector3(position.x, 1.62, room_min.z + 2.35)
		"module_install", "table_impact":
			position = _console_focus_position()
			origin = position + Vector3(0.42, 0.58, 1.45)
		"door_open", "door_close":
			position = Vector3(door_x, door_height * 0.5, room_max.z - 0.04)
			origin = Vector3(door_x, 1.55, room_max.z - 2.45)
		"storm_approaching":
			position = Vector3((window_x.x + window_x.y) * 0.5, (window_y.x + window_y.y) * 0.5, room_min.z - 1.6)
			origin = Vector3(position.x, 1.6, room_min.z + 2.55)
		_:
			position = (room_min + room_max) * 0.5
			position.y = 1.4
			origin = position + Vector3(0.0, 0.2, 2.2)
	origin.x = clampf(origin.x, room_min.x + 0.7, room_max.x - 0.7)
	origin.y = clampf(origin.y, 1.35, minf(room_max.y - 0.4, 1.9))
	origin.z = clampf(origin.z, room_min.z + 0.75, room_max.z - 0.75)
	var direction := position - origin
	if direction.length_squared() < 1e-6:
		direction = Vector3(0.0, 0.0, -1.0)
	else:
		direction = direction.normalized()
	return {
		"event_type": key,
		"position": position,
		"view_origin": origin,
		"view_direction": direction,
	}


func _wants_modular_scene() -> bool:
	# Explicit false keeps the Version-B room. Explicit true builds the Kenney bay.
	# When the key is absent, worn panels may still opt into the modular set.
	if _art_profile.has("modular_scene_assets"):
		return bool(_art_profile["modular_scene_assets"])
	var spec: Variant = _art_profile.get("environment_dressing_spec", {})
	if spec is Dictionary and bool((spec as Dictionary).get("worn_panels", false)):
		return true
	return false


func _world_of(node: Node3D) -> Vector3:
	if node == null:
		return Vector3.ZERO
	if is_inside_tree() and node.is_inside_tree():
		return node.global_position
	var pos := node.position
	var current: Node = node.get_parent()
	while current is Node3D:
		pos = (current as Node3D).transform * pos
		if current == self:
			break
		current = current.get_parent()
	return pos


func _array_focus_position() -> Vector3:
	if _array_pitch_pivot != null and is_instance_valid(_array_pitch_pivot):
		return _world_of(_array_pitch_pivot)
	return Vector3((room_min.x + room_max.x) * 0.5, 2.42, room_min.z - 3.35)


func _console_focus_position() -> Vector3:
	if _module_slot != null and is_instance_valid(_module_slot):
		return _world_of(_module_slot)
	var found := find_child("ModuleSlot", true, false)
	if found is Node3D and is_instance_valid(found):
		_module_slot = found as Node3D
		return _world_of(_module_slot)
	var width := room_max.x - room_min.x
	return Vector3(room_min.x + width * 0.26 + 0.18, 1.02, room_min.z + 0.9)


func _canonical_console_pos() -> Vector3:
	var width := room_max.x - room_min.x
	return Vector3(room_min.x + width * 0.26, 0.0, room_min.z + 0.72)


func _kenney_path(file_name: String) -> String:
	var repo := ProjectSettings.globalize_path("res://").simplify_path().get_base_dir()
	return repo.path_join("media/assets3d/cc0/kenney_space_kit").path_join(file_name)


func _spawn_kenney(
	parent: Node3D,
	file_name: String,
	local_pos: Vector3,
	yaw_deg: float,
	target_height: float,
	obstacle: bool,
	anchor_bottom: bool = true
) -> Node3D:
	if parent == null or _character_script == null:
		return null
	var path := _kenney_path(file_name)
	var model: Node3D = _character_script.call("load_gltf_scene", path) as Node3D
	if model == null:
		props_failed += 1
		return null
	var holder := Node3D.new()
	holder.name = file_name.get_basename()
	parent.add_child(holder)
	holder.add_child(model)
	var box := _bounds(model)
	var height := maxf(box.size.y, 0.001)
	var fit := 1.0 if target_height <= 0.0 else target_height / height
	var y_off := box.position.y + (0.0 if anchor_bottom else box.size.y * 0.5)
	model.position = Vector3(-(box.position.x + box.size.x * 0.5), -y_off, -(box.position.z + box.size.z * 0.5))
	holder.scale = Vector3.ONE * fit
	holder.position = local_pos
	holder.rotation.y = deg_to_rad(yaw_deg)
	if obstacle and parent == self:
		var half := box.size * fit * 0.5
		var center := local_pos + Vector3(0.0, half.y if anchor_bottom else 0.0, 0.0)
		var reach := maxf(half.x, half.z)
		_register_obstacle(center - Vector3(reach, 0.0, reach), center + Vector3(reach, half.y * 2.0, reach))
	_dressing_count += 1
	return holder


func _clear_of_door(pos: Vector3, pad: float) -> bool:
	if pos.z < room_max.z - 1.5:
		return true
	return absf(pos.x - door_x) > door_width * 0.5 + pad


func _build_modular_industrial_bay() -> void:
	_place_modular_walls()
	_place_modular_pipes()
	_place_modular_machinery()
	_ensure_modular_console()


func _place_modular_walls() -> void:
	var z := room_min.z + 1.7
	var step := 3.15
	var index := 0
	while z < room_max.z - 1.15:
		var file := "supports_high.glb"
		var height := 2.15
		if index % 3 == 1:
			file = "structure_detailed.glb"
			height = 1.45
		elif index % 3 == 2:
			file = "chimney_detailed.glb"
			height = 2.45
		var left := Vector3(room_min.x + 0.5, 0.0, z)
		var right := Vector3(room_max.x - 0.5, 0.0, z + 0.35)
		if _clear_of_door(left, 0.7):
			_spawn_kenney(self, file, left, 90.0, height, true)
		if _clear_of_door(right, 0.7):
			_spawn_kenney(self, file, right, -90.0, height, true)
		z += step
		index += 1
	for side: float in [-1.0, 1.0]:
		var px := room_min.x + 0.42 if side < 0.0 else room_max.x - 0.42
		var corner := Vector3(px, 0.0, room_min.z + 0.48)
		if absf(corner.x) > absf(window_x.x) - 0.2 or absf(corner.x) > absf(window_x.y) - 0.2:
			_spawn_kenney(self, "chimney_detailed.glb", corner, 0.0, 2.3, true)
		var gate_x := door_x + side * (door_width * 0.5 + 0.95)
		var gate_pos := Vector3(gate_x, 0.0, room_max.z - 0.42)
		if gate_pos.x > room_min.x + 0.6 and gate_pos.x < room_max.x - 0.6 and _clear_of_door(gate_pos, 0.35):
			_spawn_kenney(self, "gate_complex.glb", gate_pos, 180.0, 1.7, true)
	var bulkhead := Vector3(room_max.x - 0.85, 0.0, (room_min.z + room_max.z) * 0.5)
	if _clear_of_door(bulkhead, 0.8):
		_spawn_kenney(self, "corridor_detailed.glb", bulkhead, -90.0, 1.55, true)


func _place_modular_pipes() -> void:
	if _wants_hero_industrial():
		return
	var y := room_max.y - 0.46
	var z := room_min.z + 0.7
	var use_ring := false
	while z < room_max.z - 0.7:
		var file := "pipe_ring.glb" if use_ring else "pipe_straight.glb"
		_spawn_kenney(self, file, Vector3(room_min.x + 0.22, y, z), 0.0, 0.4, false)
		_spawn_kenney(self, file, Vector3(room_max.x - 0.22, y, z), 0.0, 0.4, false)
		z += 0.78
		use_ring = not use_ring
	_spawn_kenney(self, "pipe_cornerRound.glb", Vector3(room_min.x + 0.22, y, room_min.z + 0.42), 90.0, 0.48, false)
	_spawn_kenney(self, "pipe_cornerRound.glb", Vector3(room_max.x - 0.22, y, room_max.z - 0.42), -90.0, 0.48, false)


func _place_modular_machinery() -> void:
	if _wants_hero_industrial():
		return
	var spots: Array[Dictionary] = [
		{"file": "machine_generatorLarge.glb", "pos": Vector3(room_max.x - 0.9, 0.0, room_max.z - 1.05), "yaw": -90.0, "h": 1.3},
		{"file": "machine_wirelessCable.glb", "pos": Vector3(room_max.x - 0.75, 0.0, room_max.z - 2.35), "yaw": -80.0, "h": 1.1},
		{"file": "machine_barrelLarge.glb", "pos": Vector3(room_min.x + 0.72, 0.0, room_max.z - 1.2), "yaw": 15.0, "h": 0.95},
		{"file": "barrels_rail.glb", "pos": Vector3(room_min.x + 0.62, 0.0, room_min.z + 1.45), "yaw": 90.0, "h": 0.55},
	]
	for spot: Dictionary in spots:
		var pos: Vector3 = spot["pos"]
		if not _clear_of_door(pos, 0.85):
			continue
		if absf(pos.x) < 1.15 and pos.z > room_min.z + 1.8 and pos.z < room_max.z - 1.7:
			continue
		_spawn_kenney(self, str(spot["file"]), pos, float(spot["yaw"]), float(spot["h"]), true)
	var has_chair := false
	for child: Node in get_children():
		if str(child.name).to_lower().contains("chair"):
			has_chair = true
			break
	if not has_chair:
		var seat := _canonical_console_pos() + Vector3(0.05, 0.0, 1.05)
		_spawn_kenney(self, "desk_chairArms.glb", seat, 180.0, 0.92, true)


func _find_main_console() -> Node3D:
	var best: Node3D = null
	var best_d := 2.4
	var want := _canonical_console_pos()
	for child: Node in get_children():
		if not child is Node3D:
			continue
		var node := child as Node3D
		var node_name := str(node.name).to_lower()
		if not (node_name.contains("console") or node_name.contains("workstation")):
			continue
		var dist := Vector2(node.position.x - want.x, node.position.z - want.z).length()
		if dist < best_d:
			best = node
			best_d = dist
	return best


func _ensure_modular_console() -> void:
	if find_child("ModuleSlot", true, false) != null:
		_point_console_anchor_at_slot()
		return
	var existing := _find_main_console()
	var origin := existing.position if existing != null else _canonical_console_pos()
	var host := Node3D.new()
	host.name = "MainConsoleStation"
	host.position = origin
	add_child(host)
	if _wants_hero_industrial():
		_build_hero_console(host, Vector3(1.72, 0.96, 0.78))
		_register_obstacle(origin + Vector3(-0.95, 0.0, -0.5), origin + Vector3(1.15, 1.5, 0.55))
		_jolt_targets.append(host)
		_jolt_home[host.get_instance_id()] = host.position
		if existing == null:
			_register_interactive_device("main_console", "console", host, Vector3(1.8, 1.45, 0.9))
		_point_console_anchor_at_slot()
		return
	if existing == null:
		if _spawn_kenney(host, "desk_computer.glb", Vector3.ZERO, 0.0, 1.05, false) == null:
			_build_modular_console(host, Vector3(1.7, 0.92, 0.78))
			_register_obstacle(origin + Vector3(-0.9, 0.0, -0.45), origin + Vector3(1.15, 1.45, 0.5))
			_jolt_targets.append(host)
			_jolt_home[host.get_instance_id()] = host.position
			_register_interactive_device("main_console", "console", host, Vector3(1.8, 1.35, 0.9))
			_point_console_anchor_at_slot()
			return
	_spawn_kenney(host, "desk_computerCorner.glb", Vector3(-0.98, 0.0, 0.02), 0.0, 0.9, false)
	_spawn_kenney(host, "desk_computerScreen.glb", Vector3(0.02, 0.74, -0.1), 0.0, 0.6, false)
	_spawn_kenney(host, "machine_wireless.glb", Vector3(0.92, 0.0, 0.06), 18.0, 0.7, false)
	_add_module_slot(host, Vector3(0.2, 1.0, 0.16))
	_add_switch_bank(host, Vector3(-0.05, 1.01, 0.28))
	_child_dynamic_screen(host, "ScreenPanel", Vector3(0.62, 0.34, 0.02), Vector3(0.02, 1.18, -0.16), "console")
	_register_obstacle(origin + Vector3(-1.2, 0.0, -0.5), origin + Vector3(1.25, 1.5, 0.55))
	_jolt_targets.append(host)
	_jolt_home[host.get_instance_id()] = host.position
	if existing == null:
		_register_interactive_device("main_console", "console", host, Vector3(1.9, 1.4, 0.95))
	_point_console_anchor_at_slot()


func _build_modular_console(holder: Node3D, size: Vector3) -> bool:
	if _spawn_kenney(holder, "desk_computer.glb", Vector3.ZERO, 0.0, size.y, false) == null:
		return false
	_spawn_kenney(holder, "desk_computerCorner.glb", Vector3(-size.x * 0.46, 0.0, 0.02), 0.0, size.y * 0.94, false)
	_spawn_kenney(holder, "desk_computerScreen.glb", Vector3(0.0, size.y * 0.62, -size.z * 0.18), 0.0, 0.58, false)
	_spawn_kenney(holder, "machine_wireless.glb", Vector3(size.x * 0.46, 0.0, 0.04), 12.0, 0.68, false)
	_add_module_slot(holder, Vector3(size.x * 0.12, size.y + 0.02, size.z * 0.16))
	_add_switch_bank(holder, Vector3(0.0, size.y + 0.035, size.z * 0.28))
	_child_dynamic_screen(holder, "ScreenPanel", Vector3(size.x * 0.46, 0.32, 0.02), Vector3(0.0, size.y + 0.48, -size.z * 0.24), "console")
	return true


func _add_module_slot(parent: Node3D, pos: Vector3) -> void:
	var slot := Node3D.new()
	slot.name = "ModuleSlot"
	slot.position = pos
	parent.add_child(slot)
	_module_slot = slot
	_child_box(slot, "BayCavity", Vector3(0.28, 0.045, 0.16), Vector3.ZERO, "metal_dark", false)
	_child_status_led(slot, Vector3(-0.08, 0.045, 0.07))
	_child_status_led(slot, Vector3(0.0, 0.045, 0.07))
	_child_status_led(slot, Vector3(0.08, 0.045, 0.07))


func _add_switch_bank(parent: Node3D, pos: Vector3) -> void:
	var bank := Node3D.new()
	bank.name = "SwitchBank"
	bank.position = pos
	parent.add_child(bank)
	_child_box(bank, "KeyRail", Vector3(0.5, 0.018, 0.1), Vector3(0.0, -0.01, 0.0), "frame", false)
	for i in 8:
		_child_box(bank, "Key%d" % i, Vector3(0.042, 0.02, 0.045), Vector3(-0.2 + float(i) * 0.055, 0.012, 0.0), "metal_dark", false)


func _point_console_anchor_at_slot() -> void:
	if _module_slot == null or not is_instance_valid(_module_slot):
		var found := find_child("ModuleSlot", true, false)
		if found is Node3D:
			_module_slot = found as Node3D
	if _module_slot == null:
		return
	var world_slot := _world_of(_module_slot)
	var patched := false
	for i in _interactive_devices.size():
		var dev: Dictionary = _interactive_devices[i]
		var id := str(dev.get("id", "")).to_lower()
		if id.contains("console") or str(dev.get("kind", "")) in ["console", "workstation"]:
			dev["interaction_anchor"] = world_slot
			dev["look_target"] = world_slot + Vector3(0.0, 0.14, 0.0)
			_interactive_devices[i] = dev
			patched = true
			break
	if not patched and _module_slot.get_parent() is Node3D:
		_register_interactive_device("main_console", "console", _module_slot.get_parent() as Node3D, Vector3(1.8, 1.35, 0.9))


func _apply_storm_sky(storm_intensity: float, flash: float) -> void:
	var u := 0.0
	if storm_intensity > 0.1:
		u = clampf((storm_intensity - 0.1) / 0.9, 0.0, 1.0)
	var tint := Color(1.0, 1.0, 1.0).lerp(Color(0.16, 0.18, 0.30), u)
	if flash > 0.04:
		tint = tint.lerp(Color(0.74, 0.82, 0.96), clampf(flash, 0.0, 1.0) * 0.7)
	for mat: StandardMaterial3D in _sky_backdrop_mats:
		if mat != null:
			mat.albedo_color = Color(tint.r, tint.g, tint.b, 1.0)
	if _planet_mat != null:
		_planet_mat.set_shader_parameter("light_color", Color("#eef4fb").lerp(Color("#6d7688"), u))
		_planet_mat.set_shader_parameter("sea_color", Color("#7fb0d8").lerp(Color("#243044"), u))
		_planet_mat.set_shader_parameter("shadow_color", Color("#4f7fb5").lerp(Color("#141a28"), u))


func _wants_hero_industrial() -> bool:
	return str(_art_profile.get("preset", "")) == "retro_industrial_scifi" or _wants_modular_scene()


func _wants_storm_surface(backdrop: Dictionary) -> bool:
	var mode := str(backdrop.get("mode", "")).strip_edges().to_lower()
	if mode == "storm_surface" or bool(backdrop.get("storm_surface", false)):
		return true
	return str(_art_profile.get("preset", "")) == "retro_industrial_scifi"


func _hash2(x: int, y: int) -> float:
	var n := (x * 374761393) ^ (y * 668265263)
	n = (n ^ (n >> 13)) * 1274126177
	return float(absi(n) % 1000) / 999.0


func _weathered_metal(base: Color, seam: Color, rough: float, metal: float) -> StandardMaterial3D:
	var img := Image.create(256, 256, false, Image.FORMAT_RGB8)
	for y in 256:
		for x in 256:
			var h := _hash2(x, y)
			var c := base.lightened((h - 0.5) * 0.08)
			if x % 64 <= 1 or y % 72 <= 1:
				c = seam
			var rx := x % 52
			var ry := y % 52
			if rx > 2 and rx < 7 and ry > 2 and ry < 7:
				c = c.lightened(0.16)
			if h > 0.86:
				c = c.lerp(Color("#3a342c"), 0.4)
			if y > 210 and h > 0.55:
				c = c.lerp(Color("#2a2620"), 0.35)
			img.set_pixel(x, y, c)
	img.generate_mipmaps()
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = ImageTexture.create_from_image(img)
	mat.roughness = rough
	mat.metallic = metal
	mat.metallic_specular = 0.18
	mat.uv1_triplanar = true
	mat.uv1_scale = Vector3(0.85, 0.85, 0.85)
	return mat


func _ensure_hero_materials() -> void:
	if _materials.has("hero_steel"):
		return
	_materials["hero_steel"] = _weathered_metal(Color("#5a6268"), Color("#343a40"), 0.62, 0.42)
	_materials["hero_gun"] = _weathered_metal(Color("#3b434b"), Color("#262c32"), 0.74, 0.30)
	_materials["hero_olive"] = _weathered_metal(Color("#4b514a"), Color("#2e332c"), 0.78, 0.2)
	_materials["hero_cable"] = _weathered_metal(Color("#1c2126"), Color("#101316"), 0.94, 0.05)
	_materials["hero_socket"] = _mat(Color("#12161a"), 0.9, 0.0, 0.06)


func _build_hero_industrial_set() -> void:
	_ensure_hero_materials()
	var names: Array[String] = ["BAY-04", "COMM-A", "PWR-2", "AUX-1"]
	var z := room_min.z + 1.15
	var index := 0
	while z < room_max.z - 1.35 and index < names.size():
		var span := 1.2 if index % 2 == 0 else 0.78
		for side in [-1.0, 1.0]:
			var x0 := room_min.x if side < 0.0 else room_max.x - 0.18
			var x1 := room_min.x + 0.18 if side < 0.0 else room_max.x
			var probe := Vector3((x0 + x1) * 0.5, 1.2, z)
			if not _clear_of_door(probe, 0.55):
				continue
			var mat_key := "hero_gun" if index % 2 == 0 else "hero_olive"
			_box_between(
				"WallBay_%d_%d" % [index, int(side)],
				Vector3(x0, 0.12, z - span * 0.5),
				Vector3(x1, room_max.y - 0.42, z + span * 0.5),
				mat_key,
				false
			)
			for slat in 4:
				var vy := 1.55 + float(slat) * 0.07
				_box_between(
					"Vent_%d_%d_%d" % [index, int(side), slat],
					Vector3(x0 - side * 0.012, vy, z - 0.2),
					Vector3(x1 - side * 0.012, vy + 0.016, z + 0.2),
					"hero_socket",
					false
				)
			_add_bay_stencil(names[index], Vector3(x1 if side < 0.0 else x0, 2.05, z), side)
		z += span + 0.62
		index += 1
	if _clear_of_door(Vector3(room_min.x + 0.2, 1.2, room_max.z - 1.3), 0.4):
		_box_between("BreakerL", Vector3(room_min.x + 0.02, 0.95, room_max.z - 1.7), Vector3(room_min.x + 0.22, 1.85, room_max.z - 0.95), "hero_steel", false)
	if _clear_of_door(Vector3(room_max.x - 0.2, 1.2, room_min.z + 1.6), 0.4):
		_box_between("BreakerR", Vector3(room_max.x - 0.22, 0.85, room_min.z + 1.25), Vector3(room_max.x - 0.02, 1.75, room_min.z + 1.95), "hero_olive", false)
	var tray_y := room_max.y - 0.22
	_box_between("CableTray", Vector3(room_min.x + 0.45, tray_y, room_min.z + 0.35), Vector3(room_max.x - 0.45, tray_y + 0.05, room_max.z - 0.35), "hero_gun", false)
	_box_between("CableRun", Vector3(-0.06, tray_y - 0.06, room_min.z + 0.4), Vector3(0.06, tray_y, room_max.z - 0.4), "hero_cable", false)
	var drop_z := room_min.z + 1.4
	while drop_z < room_max.z - 1.0:
		_box_between("CableDrop_%d" % int(drop_z * 10.0), Vector3(room_max.x - 0.55, 1.3, drop_z), Vector3(room_max.x - 0.48, tray_y, drop_z + 0.04), "hero_cable", false)
		drop_z += 2.2
	var seam_z := room_min.z + 0.55
	while seam_z < room_max.z - 0.3:
		_box_between("FloorSeam_%d" % int(seam_z * 10.0), Vector3(room_min.x + 0.35, 0.003, seam_z), Vector3(room_max.x - 0.35, 0.01, seam_z + 0.02), "hero_socket", false)
		seam_z += 1.65
	_dressing_count += 6


func _add_bay_stencil(text: String, pos: Vector3, side: float) -> void:
	var label := Label3D.new()
	label.name = "Stencil_%s" % text
	label.text = text
	label.font_size = 48
	label.pixel_size = 0.002
	label.modulate = Color("#d7dbd4")
	label.outline_size = 8
	label.outline_modulate = Color(0.05, 0.06, 0.05, 0.85)
	label.position = pos
	label.rotation.y = -PI * 0.5 if side < 0.0 else PI * 0.5
	add_child(label)
	_dressing_count += 1


func _build_hero_console(holder: Node3D, size: Vector3) -> void:
	_ensure_hero_materials()
	_child_box(holder, "FrameL", Vector3(0.08, size.y, size.z * 0.9), Vector3(-size.x * 0.46, size.y * 0.5, 0.0), "hero_steel")
	_child_box(holder, "FrameR", Vector3(0.08, size.y, size.z * 0.9), Vector3(size.x * 0.46, size.y * 0.5, 0.0), "hero_steel")
	_child_box(holder, "FootL", Vector3(0.18, 0.05, size.z), Vector3(-size.x * 0.4, 0.025, 0.0), "hero_gun")
	_child_box(holder, "FootR", Vector3(0.18, 0.05, size.z), Vector3(size.x * 0.4, 0.025, 0.0), "hero_gun")
	_child_box(holder, "Plinth", Vector3(size.x * 0.82, size.y * 0.7, size.z * 0.72), Vector3(0.0, size.y * 0.36, 0.0), "hero_gun")
	_child_box(holder, "Desktop", Vector3(size.x, 0.06, size.z), Vector3(0.0, size.y - 0.01, 0.02), "hero_steel")
	_child_box(holder, "RearLip", Vector3(size.x * 0.96, 0.1, 0.07), Vector3(0.0, size.y + 0.05, -size.z * 0.4), "hero_olive")
	var hood := _child_box(holder, "MonitorHood", Vector3(size.x * 0.58, 0.36, 0.14), Vector3(0.0, size.y + 0.26, -size.z * 0.2), "hero_steel")
	hood.rotation.x = deg_to_rad(-16.0)
	_child_dynamic_screen(holder, "ScreenPanel", Vector3(size.x * 0.46, 0.26, 0.018), Vector3(0.0, size.y + 0.22, -size.z * 0.12), "console")
	for row in 2:
		for i in 8:
			var key_mat := "hero_olive" if (i + row) % 5 == 0 else "hero_gun"
			_child_box(
				holder,
				"Key_%d_%d" % [row, i],
				Vector3(0.042, 0.018, 0.038),
				Vector3(-size.x * 0.22 + float(i) * 0.052, size.y + 0.045, size.z * 0.22 - float(row) * 0.055),
				key_mat,
				false
			)
	var slot := Node3D.new()
	slot.name = "ModuleSlot"
	slot.position = Vector3(size.x * 0.18, size.y + 0.015, 0.02)
	holder.add_child(slot)
	_module_slot = slot
	_child_box(slot, "SocketWell", Vector3(0.36, 0.075, 0.22), Vector3(0.0, -0.02, 0.0), "hero_socket", false)
	_child_box(slot, "GuideL", Vector3(0.018, 0.028, 0.16), Vector3(-0.15, 0.025, -0.01), "hero_steel", false)
	_child_box(slot, "GuideR", Vector3(0.018, 0.028, 0.16), Vector3(0.15, 0.025, -0.01), "hero_steel", false)
	_slot_latch = _child_box(slot, "ClampLatch", Vector3(0.3, 0.018, 0.05), Vector3(0.0, 0.05, 0.1), "hero_olive", false)
	_slot_latch.rotation.x = deg_to_rad(-68.0)
	for i in 3:
		_child_module_led(slot, Vector3(-0.08 + float(i) * 0.08, 0.055, 0.12))
	_module_slot_state = "empty_offline"
	_device_online = false


func _child_module_led(parent: Node3D, pos: Vector3) -> void:
	var mi := MeshInstance3D.new()
	mi.name = "ModuleLED"
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.045, 0.028, 0.02)
	mi.mesh = mesh
	mi.position = pos
	mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	var mat := _mat(Color("#6a1814"), 0.42, 0.28)
	mat.set_meta("rest_color", Color("#6a1814"))
	mat.set_meta("rest_energy", 0.28)
	mat.set_meta("led_role", "module")
	mi.material_override = mat
	parent.add_child(mi)
	_module_leds.append(mat)


func get_module_socket_transform() -> Transform3D:
	var slot := _module_slot
	if slot == null or not is_instance_valid(slot):
		var found := find_child("ModuleSlot", true, false)
		if found is Node3D:
			slot = found as Node3D
			_module_slot = slot
	if slot == null:
		return Transform3D(Basis.IDENTITY, _console_focus_position())
	if is_inside_tree() and slot.is_inside_tree():
		return slot.global_transform
	var xf := slot.transform
	var current: Node = slot.get_parent()
	while current is Node3D:
		xf = (current as Node3D).transform * xf
		if current == self:
			break
		current = current.get_parent()
	return xf


func set_device_online(online: bool) -> void:
	if online:
		set_module_slot_state("online")
	else:
		_device_online = false
		if _module_slot_state == "online":
			set_module_slot_state("empty_offline")
		else:
			_apply_module_leds()


func set_module_slot_state(state: String) -> void:
	if state not in ["empty_offline", "aligning", "locked", "online"]:
		state = "empty_offline"
	_module_slot_state = state
	_device_online = state == "online"
	_apply_slot_latch()
	_apply_module_leds()


func _apply_slot_latch() -> void:
	if _slot_latch == null or not is_instance_valid(_slot_latch):
		return
	match _module_slot_state:
		"empty_offline":
			_slot_latch.rotation.x = deg_to_rad(-68.0)
		"aligning":
			_slot_latch.rotation.x = deg_to_rad(-30.0)
		_:
			_slot_latch.rotation.x = 0.0


func _apply_module_leds() -> void:
	var tagged := false
	for led: StandardMaterial3D in _module_leds:
		if str(led.get_meta("led_role", "")) == "module":
			tagged = true
			break
	var col := Color("#6a1814")
	var energy := 0.28
	if _device_online or _module_slot_state == "online":
		col = Color("#1f8f45")
		energy = 1.15
	elif _module_slot_state == "locked":
		col = Color("#c47a18")
		energy = 0.72
	elif _module_slot_state == "aligning":
		col = Color("#a84818")
		energy = 0.55
	for led: StandardMaterial3D in _module_leds:
		var role := str(led.get_meta("led_role", ""))
		if tagged and role != "module":
			var rest: Color = led.get_meta("rest_color", Color("#1c6e78")) as Color
			led.albedo_color = rest
			led.emission = rest
			led.emission_energy_multiplier = float(led.get_meta("rest_energy", 0.45))
			continue
		led.albedo_color = col
		led.emission = col
		led.emission_enabled = true
		led.emission_energy_multiplier = energy
		if _device_online:
			led.set_meta("rest_color", col)
			led.set_meta("rest_energy", energy)


func _add_array_beacons(root: Node3D) -> Dictionary:
	var lamps := {}
	var specs: Array[Dictionary] = [
		{"id": "red", "pos": Vector3(-0.24, 1.92, 0.16), "rest": Color("#4a1210")},
		{"id": "amber", "pos": Vector3(0.0, 2.08, 0.18), "rest": Color("#4a3410")},
		{"id": "green", "pos": Vector3(0.24, 1.92, 0.16), "rest": Color("#12301c")},
	]
	for spec: Dictionary in specs:
		var mi := MeshInstance3D.new()
		mi.name = "Beacon%s" % str(spec["id"]).capitalize()
		var mesh := SphereMesh.new()
		mesh.radius = 0.045
		mesh.height = 0.09
		mesh.radial_segments = 10
		mesh.rings = 6
		mi.mesh = mesh
		mi.position = spec["pos"] as Vector3
		mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		var mat := _mat(spec["rest"] as Color, 0.38, 0.12)
		mi.material_override = mat
		root.add_child(mi)
		lamps[str(spec["id"])] = mat
	_array_beacon_mat = lamps["red"] as StandardMaterial3D
	return lamps


func _sync_array_beacons(t_s: float) -> void:
	if _beacon_lamps.is_empty() or _array_beacon_mat == null:
		return
	var active := "red"
	var energy := 0.4
	if _array_fault_now:
		active = "red"
		energy = 0.4 + 1.05 * (0.5 + 0.5 * sin(t_s * 16.0))
	elif _array_lock > 0.72 or (_array_held_lock and not _array_fault_now):
		active = "green"
		var lock_u: float = _array_lock if _array_lock > 0.05 else 1.0
		energy = 0.65 + 0.7 * lock_u
	elif _array_lock > 0.18:
		active = "amber"
		energy = 0.5 + 0.55 * _array_lock
	var cols := {
		"red": Color("#ff2a18"),
		"amber": Color("#ff9a1f"),
		"green": Color("#3dff6a"),
	}
	for key: String in ["red", "amber", "green"]:
		var mat: StandardMaterial3D = _beacon_lamps.get(key, null) as StandardMaterial3D
		if mat == null:
			continue
		var on := key == active
		var col: Color = cols[key] if on else Color("#16120e")
		mat.albedo_color = col
		mat.emission = col
		mat.emission_enabled = true
		mat.emission_energy_multiplier = energy if on else 0.02
	_array_beacon_mat = _beacon_lamps[active] as StandardMaterial3D


func _build_planetary_storm() -> void:
	_ensure_hero_materials()
	var ground := _mat(Color("#1c2420"), 0.94, 0.0, 0.02)
	var deck := MeshInstance3D.new()
	deck.name = "StormTerrain"
	var deck_mesh := BoxMesh.new()
	deck_mesh.size = Vector3(90.0, 0.6, 64.0)
	deck.mesh = deck_mesh
	deck.position = Vector3(0.0, _ground_y - 0.3, room_min.z - 34.0)
	deck.material_override = ground
	deck.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(deck)
	var ridge := SurfaceTool.new()
	ridge.begin(Mesh.PRIMITIVE_TRIANGLES)
	var heights: Array[float] = [0.6, 1.8, 0.9, 2.6, 1.2, 2.1, 0.7, 1.5, 0.8, 1.9, 0.5]
	var x_cursor := -42.0
	for i in heights.size() - 1:
		var x0 := x_cursor
		var x1 := x_cursor + 8.4
		var y0 := _ground_y + heights[i]
		var y1 := _ground_y + heights[i + 1]
		var z := room_min.z - 46.0
		ridge.set_color(Color("#12181c"))
		ridge.add_vertex(Vector3(x0, _ground_y, z))
		ridge.add_vertex(Vector3(x1, _ground_y, z))
		ridge.add_vertex(Vector3(x0, y0, z - 1.5))
		ridge.add_vertex(Vector3(x1, _ground_y, z))
		ridge.add_vertex(Vector3(x1, y1, z - 1.5))
		ridge.add_vertex(Vector3(x0, y0, z - 1.5))
		x_cursor = x1
	var ridge_mi := MeshInstance3D.new()
	ridge_mi.name = "StormRidge"
	ridge_mi.mesh = ridge.commit()
	var ridge_mat := StandardMaterial3D.new()
	ridge_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ridge_mat.vertex_color_use_as_albedo = true
	ridge_mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	ridge_mi.material_override = ridge_mat
	ridge_mi.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	add_child(ridge_mi)
	for gantry in 2:
		var gx := -16.0 if gantry == 0 else 18.0
		var gz := room_min.z - 22.0 - float(gantry) * 6.0
		_box_between("GantryMast_%d" % gantry, Vector3(gx - 0.18, _ground_y, gz - 0.18), Vector3(gx + 0.18, _ground_y + 7.5, gz + 0.18), "hero_gun", false)
		_box_between("GantryArm_%d" % gantry, Vector3(gx - 2.4, _ground_y + 6.6, gz - 0.08), Vector3(gx + 2.4, _ground_y + 6.85, gz + 0.08), "hero_steel", false)
		_box_between("GantryDish_%d" % gantry, Vector3(gx + 1.6, _ground_y + 6.2, gz - 0.7), Vector3(gx + 2.5, _ground_y + 7.1, gz - 0.15), "hero_olive", false)
	var cloud_specs: Array[Dictionary] = [
		{"pos": Vector3(0.0, 6.5, room_min.z - 18.0), "size": Vector2(46.0, 8.0), "col": Color(0.10, 0.12, 0.15, 0.92)},
		{"pos": Vector3(-6.0, 9.2, room_min.z - 28.0), "size": Vector2(62.0, 7.0), "col": Color(0.07, 0.08, 0.11, 0.88)},
		{"pos": Vector3(4.0, 4.2, room_min.z - 12.0), "size": Vector2(36.0, 4.5), "col": Color(0.16, 0.18, 0.2, 0.8)},
		{"pos": Vector3(0.0, 12.0, room_min.z - 40.0), "size": Vector2(80.0, 10.0), "col": Color(0.05, 0.06, 0.08, 0.95)},
	]
	for spec: Dictionary in cloud_specs:
		var cloud := MeshInstance3D.new()
		cloud.name = "StormBank"
		var quad := QuadMesh.new()
		quad.size = spec["size"] as Vector2
		cloud.mesh = quad
		cloud.position = spec["pos"] as Vector3
		cloud.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		var mat := StandardMaterial3D.new()
		mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		mat.albedo_color = spec["col"] as Color
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		mat.cull_mode = BaseMaterial3D.CULL_DISABLED
		cloud.material_override = mat
		add_child(cloud)
	for i in 5:
		var streak := MeshInstance3D.new()
		streak.name = "RainStreak%d" % i
		var quad := QuadMesh.new()
		quad.size = Vector2(1.6, 2.8)
		streak.mesh = quad
		streak.position = Vector3(-3.2 + float(i) * 1.6, 1.7, room_min.z - 0.55)
		streak.set_meta("rain_phase", float(i) * 0.17)
		var img := Image.create(16, 96, false, Image.FORMAT_RGBA8)
		img.fill(Color(0, 0, 0, 0))
		for sx in [3, 8, 13]:
			for sy in range(0, 96, 14):
				for k in 7:
					img.set_pixel(sx, mini(sy + k, 95), Color(0.72, 0.78, 0.82, 0.38))
		var smat := StandardMaterial3D.new()
		smat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		smat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		smat.albedo_texture = ImageTexture.create_from_image(img)
		smat.cull_mode = BaseMaterial3D.CULL_DISABLED
		smat.uv1_scale = Vector3(1.0, 1.0, 1.0)
		streak.material_override = smat
		streak.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
		add_child(streak)
		_rain_streaks.append(streak)

