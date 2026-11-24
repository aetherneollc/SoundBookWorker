extends Node3D

## The lunar observation bay: a procedural shell (floor, walls, doorway,
## ceiling light bars, front window), Kenney Space Kit props from
## ``stage3d.json`` ``set.props``, a moonscape outside the window, and the
## lighting rig. Units are metres, +Y up, the window wall faces +Z at z = room min.

const WALL := 0.2
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

var _materials: Dictionary = {}
var _character_script: Script
var _ground_y := -2.4
var _palette: Dictionary = {}
var _ridge_noise: FastNoiseLite
var _hill_noise: FastNoiseLite
var _grain_noise: FastNoiseLite


func build(set_cfg: Dictionary, character_script: Script) -> void:
	_character_script = character_script
	var room: Dictionary = set_cfg.get("room", {}) as Dictionary if set_cfg.get("room", {}) is Dictionary else {}
	room_min = _vec3(room.get("min", null), room_min)
	room_max = _vec3(room.get("max", null), room_max)
	var door: Dictionary = set_cfg.get("door", {}) as Dictionary if set_cfg.get("door", {}) is Dictionary else {}
	door_x = float(door.get("x", door_x))
	door_width = float(door.get("width", door_width))
	door_height = float(door.get("height", door_height))
	corridor_depth = float(door.get("corridor_depth", corridor_depth))
	var window: Dictionary = set_cfg.get("window", {}) as Dictionary if set_cfg.get("window", {}) is Dictionary else {}
	window_x = Vector2(float(window.get("x_min", window_x.x)), float(window.get("x_max", window_x.y)))
	window_y = Vector2(float(window.get("y_min", window_y.x)), float(window.get("y_max", window_y.y)))
	var bounds: Dictionary = set_cfg.get("camera_bounds", {}) as Dictionary if set_cfg.get("camera_bounds", {}) is Dictionary else {}
	camera_min = _vec3(bounds.get("min", null), camera_min)
	camera_max = _vec3(bounds.get("max", null), camera_max)
	corridor_camera_max_z = float(bounds.get("corridor_max_z", room_max.z + corridor_depth - 0.4))

	_build_materials()
	_build_shell()
	_build_corridor()
	_build_window_wall()
	_build_ceiling()
	var backdrop: Dictionary = set_cfg.get("backdrop", {}) as Dictionary if set_cfg.get("backdrop", {}) is Dictionary else {}
	_build_outside(backdrop, set_cfg.get("outside_props", []))
	_build_props(set_cfg.get("props", []))
	_build_lighting(set_cfg.get("lighting", {}) as Dictionary if set_cfg.get("lighting", {}) is Dictionary else {})


## Keeps a camera inside the room, or inside the doorway corridor.
func clamp_camera(position: Vector3) -> Vector3:
	var p := position
	var half: float = door_width * 0.5 - 0.22
	if p.z > camera_max.z and absf(p.x - door_x) <= half + 0.4:
		p.x = clampf(p.x, door_x - half, door_x + half)
		p.y = clampf(p.y, camera_min.y, door_height - 0.2)
		p.z = minf(p.z, corridor_camera_max_z)
		return p
	p.x = clampf(p.x, camera_min.x, camera_max.x)
	p.y = clampf(p.y, camera_min.y, camera_max.y)
	p.z = clampf(p.z, camera_min.z, camera_max.z)
	return p


func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback


# ------------------------------------------------------------ materials


func _mat(color: Color, roughness: float = 0.9, emission: float = 0.0) -> StandardMaterial3D:
	var material := StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = roughness
	material.metallic_specular = 0.25
	if emission > 0.0:
		material.emission_enabled = true
		material.emission = color
		material.emission_energy_multiplier = emission
	return material


func _build_materials() -> void:
	_materials["floor"] = _tile_material(Color("#aab5bf"), Color("#87939f"), 1.0)
	_materials["wall"] = _mat(Color("#2a4a6b"))
	_materials["wall_low"] = _mat(Color("#21395a"))
	_materials["panel_line"] = _mat(Color("#1a2c44"))
	_materials["ledge"] = _mat(Color("#c98a45"), 0.7)
	_materials["frame"] = _mat(Color("#1f3550"), 0.6)
	_materials["ceiling"] = _mat(Color("#1b222d"))
	_materials["beam"] = _mat(Color("#28323f"))
	_materials["light_bar"] = _mat(Color("#f2f6ff"), 0.4, 2.4)
	_materials["trim"] = _mat(Color("#e8892e"), 0.6, 0.15)
	_materials["corridor"] = _mat(Color("#33506e"))
	_materials["ground"] = _tile_material(Color("#c9cfd8"), Color("#b6bec9"), 0.0)
	_materials["crate"] = _mat(Color("#d27a2c"), 0.75)
	_materials["crate_dark"] = _mat(Color("#3d4757"), 0.8)


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


# ---------------------------------------------------------------- shell


func _build_shell() -> void:
	var h: float = room_max.y
	_box_between("Floor", Vector3(room_min.x - WALL, -0.2, room_min.z - WALL), Vector3(room_max.x + WALL, 0.0, room_max.z + WALL), "floor")
	for side in [-1.0, 1.0]:
		var x: float = room_min.x if side < 0.0 else room_max.x
		var outer: float = x + side * WALL
		_box_between("SideWall%d" % int(side), Vector3(x, 0.0, room_min.z), Vector3(outer, h, room_max.z), "wall")
		var inner: float = x - side * 0.04
		_box_between("SideLow%d" % int(side), Vector3(x, 0.0, room_min.z + 0.3), Vector3(inner, window_y.x, room_max.z - 0.3), "wall_low")
		var ledge_inner: float = x - side * 0.34
		_box_between("SideLedge%d" % int(side), Vector3(x, window_y.x - 0.03, room_min.z + 0.3), Vector3(ledge_inner, window_y.x + 0.03, room_max.z - 0.3), "ledge")
		var z := room_min.z + 1.2
		while z < room_max.z - 0.6:
			_box_between("SidePanel%d_%d" % [int(side), int(z * 10.0)], Vector3(x - side * 0.02, window_y.x + 0.15, z), Vector3(x - side * 0.06, h - 0.35, z + 0.05), "panel_line", false)
			z += 1.6
		_box_between("SideStrip%d" % int(side), Vector3(x - side * 0.02, h - 0.3, room_min.z + 0.4), Vector3(x - side * 0.07, h - 0.24, room_max.z - 0.4), "light_bar", false)
	var back: float = room_max.z
	var half: float = door_width * 0.5
	_box_between("BackWallL", Vector3(room_min.x - WALL, 0.0, back), Vector3(door_x - half, h, back + WALL), "wall")
	_box_between("BackWallR", Vector3(door_x + half, 0.0, back), Vector3(room_max.x + WALL, h, back + WALL), "wall")
	_box_between("BackWallTop", Vector3(door_x - half, door_height, back), Vector3(door_x + half, h, back + WALL), "wall")
	_box_between("BackLowL", Vector3(room_min.x + 0.3, 0.0, back - 0.04), Vector3(door_x - half - 0.4, window_y.x, back), "wall_low")
	_box_between("BackLowR", Vector3(door_x + half + 0.4, 0.0, back - 0.04), Vector3(room_max.x - 0.3, window_y.x, back), "wall_low")
	# Trims stand 3 cm proud of every wall face they touch, so no face is coplanar.
	var inset := 0.03
	_box_between("DoorTrimL", Vector3(door_x - half - 0.12, 0.0, back - 0.06), Vector3(door_x - half + inset, door_height + 0.12, back + WALL + inset), "trim")
	_box_between("DoorTrimR", Vector3(door_x + half - inset, 0.0, back - 0.06), Vector3(door_x + half + 0.12, door_height + 0.12, back + WALL + inset), "trim")
	_box_between("DoorTrimTop", Vector3(door_x - half - 0.12, door_height - inset, back - 0.06), Vector3(door_x + half + 0.12, door_height + 0.12, back + WALL + inset), "trim")
	_box_between("DoorSign", Vector3(door_x - 0.35, door_height + 0.25, back - 0.05), Vector3(door_x + 0.35, door_height + 0.45, back - 0.01), "light_bar", false)


func _build_corridor() -> void:
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
	while sill_panel_x < window_x.y - 0.3:
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
			_box_between("LightBar_%d_%d" % [int(x), int(zc * 10.0)], Vector3(x - 1.1, h - 0.27, zc - 0.17), Vector3(x + 1.1, h - 0.23, zc + 0.17), "light_bar", false)


# -------------------------------------------------------------- outside


func _build_outside(backdrop: Dictionary, outside_props: Variant) -> void:
	_ground_y = float(backdrop.get("ground_y", -2.4))
	var palette_value: Variant = backdrop.get("palette", {})
	_palette = palette_value as Dictionary if palette_value is Dictionary else {}
	_setup_noise(int(backdrop.get("seed", 7)))
	_build_terrain()
	_build_planet(backdrop.get("planet", {}) as Dictionary if backdrop.get("planet", {}) is Dictionary else {})
	if outside_props is Array:
		for item: Variant in outside_props as Array:
			if item is Dictionary:
				var cfg: Dictionary = (item as Dictionary).duplicate()
				var p: Vector2 = Vector2(float((cfg.get("pos", [0, 0]) as Array)[0]), float((cfg.get("pos", [0, 0]) as Array)[1]))
				cfg["pos"] = [p.x, 0.0, p.y]
				_place_prop(cfg, terrain_height(p.x, p.y) - 0.15)
	if str(backdrop.get("mode", "procedural")) == "image":
		_build_image_backdrop(backdrop)


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


## Lunar ground outside the window: flat regolith near the glass, rolling
## hills, then snow-bright ridges on the horizon, like the LOC2 painting.
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
			var e: float = 0.6
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


# ---------------------------------------------------------------- props


func _build_props(props: Variant) -> void:
	if not props is Array:
		return
	for item: Variant in props as Array:
		if item is Dictionary:
			_place_prop(item as Dictionary, 0.0)


func _place_prop(cfg: Dictionary, ground_y: float) -> void:
	var kind: String = str(cfg.get("kind", "model"))
	var pos_value: Variant = cfg.get("pos", [0, 0, 0])
	var pos: Vector3 = _vec3(pos_value, Vector3.ZERO)
	if pos_value is Array and (pos_value as Array).size() == 2:
		pos = Vector3(float((pos_value as Array)[0]), 0.0, float((pos_value as Array)[1]))
	pos.y += ground_y
	var yaw: float = deg_to_rad(float(cfg.get("yaw_deg", 0.0)))
	var scale_value: float = float(cfg.get("scale", 1.0))
	var label: String = str(cfg.get("id", kind))
	if kind == "crate":
		var size: Vector3 = _vec3(cfg.get("size", null), Vector3(0.9, 0.9, 0.9))
		var holder := Node3D.new()
		holder.name = label
		holder.position = pos
		holder.rotation.y = yaw
		add_child(holder)
		var body := MeshInstance3D.new()
		var mesh := BoxMesh.new()
		mesh.size = size
		body.mesh = mesh
		body.position.y = size.y * 0.5
		body.material_override = _materials["crate" if str(cfg.get("color", "orange")) == "orange" else "crate_dark"]
		holder.add_child(body)
		for band_y in [0.12, size.y - 0.12]:
			var band := MeshInstance3D.new()
			var band_mesh := BoxMesh.new()
			band_mesh.size = Vector3(size.x + 0.02, 0.06, size.z + 0.02)
			band.mesh = band_mesh
			band.position.y = band_y
			band.material_override = _materials["crate_dark" if str(cfg.get("color", "orange")) == "orange" else "trim"]
			holder.add_child(band)
		props_loaded += 1
		return
	var path: String = str(cfg.get("model", ""))
	var model: Node3D = _character_script.call("load_gltf_scene", path) as Node3D
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


func _build_lighting(cfg: Dictionary) -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_SKY
	var sky := Sky.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = _palette_color("sky_top", "#3f7fc6")
	sky_mat.sky_horizon_color = _palette_color("horizon", "#c4dcee")
	sky_mat.sky_curve = 0.12
	sky_mat.ground_bottom_color = _palette_color("rock", "#9fb0c2")
	sky_mat.ground_horizon_color = _palette_color("horizon", "#c4dcee")
	sky.sky_material = sky_mat
	env.sky = sky
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(str(cfg.get("ambient", "#a7b6cc")))
	env.ambient_light_energy = float(cfg.get("ambient_energy", 0.62))
	env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
	env.tonemap_exposure = float(cfg.get("exposure", 1.0))
	var world := WorldEnvironment.new()
	world.name = "WorldEnvironment"
	world.environment = env
	add_child(world)

	var sun := DirectionalLight3D.new()
	sun.name = "Moonlight"
	var direction := Vector3(-0.38, -0.52, 0.76).normalized()
	sun.transform.basis = Basis.looking_at(direction, Vector3.UP)
	sun.light_energy = float(cfg.get("sun_energy", 1.45))
	sun.light_color = Color("#fff3e2")
	sun.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
	sun.shadow_enabled = true
	sun.shadow_blur = 1.5
	sun.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
	sun.directional_shadow_max_distance = 30.0
	add_child(sun)

	for x in [-3.2, 3.2]:
		for zc in [-2.6, 0.4, 3.2]:
			var lamp := OmniLight3D.new()
			lamp.name = "BayLamp_%d_%d" % [int(x), int(zc * 10.0)]
			lamp.position = Vector3(x, room_max.y - 0.45, zc)
			lamp.omni_range = 6.0
			lamp.omni_attenuation = 1.2
			lamp.light_energy = float(cfg.get("lamp_energy", 0.75))
			lamp.light_color = Color("#e4ecff")
			add_child(lamp)
	var fills_value: Variant = cfg.get("fills", [])
	if fills_value is Array:
		for fill_value: Variant in fills_value as Array:
			if not fill_value is Dictionary:
				continue
			var fill: Dictionary = fill_value as Dictionary
			var light := OmniLight3D.new()
			light.name = str(fill.get("id", "Fill"))
			light.position = _vec3(fill.get("pos", null), Vector3(0.0, 1.8, 0.0))
			light.omni_range = float(fill.get("range", 4.0))
			light.light_energy = float(fill.get("energy", 0.6))
			light.light_color = Color(str(fill.get("color", "#ffffff")))
			add_child(light)
