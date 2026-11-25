class_name LightingProfile
extends RefCounted

## Reusable lighting & stylized semi-realistic rendering profiles for Godot 4.
##
## Presets:
##   - day_bay        : cool lunar sunlight key + warm overhead strips + clean rim + ACES + subtle glow
##   - night_alert    : dim cool ambient + pulsing red/amber alerts + readable character key fill
##   - storm_exterior : high-contrast moody cyan-slate rim + stronger fog + subtle strobe/wind feel
##   - warm_interior  : cozy tungsten overhead + soft neutral bounce + gentle shadows
##   - dim_corridor   : overhead pool lights along corridor + cool shadows + crisp rim light
##   - med_lab        : clean bright clinical daylight-white + teal console accents + soft fill
##
## Safely configures WorldEnvironment, soft shadows, and optional CameraAttributesPractical
## DoF blur with explicit guards for GL Compatibility vs Forward+/Mobile.

const DEFAULT_PROFILE := "day_bay"

const PRESETS: Dictionary = {
	"day_bay": {
		"description": "Cool lunar sunlight key, warm interior overhead strips, clean rim light.",
		"ambient_color": "#a2b4cc",
		"ambient_energy": 0.58,
		"sun_color": "#d6e4ff",
		"sun_energy": 1.10,
		"sun_dir": [-0.38, -0.52, 0.76],
		"rim_color": "#c8e2ff",
		"rim_energy": 0.40,
		"rim_dir": [0.46, -0.34, -0.82],
		"lamp_color": "#ffeedb",
		"lamp_energy": 0.68,
		"lamp_range": 6.5,
		"lamp_attenuation": 1.2,
		"lamp_layout": "grid",
		"exposure": 1.04,
		"tonemap_white": 6.0,
		"shadow_blur": 1.6,
		"shadow_opacity": 0.82,
		"glow_enabled": true,
		"glow_intensity": 0.34,
		"glow_strength": 0.68,
		"glow_bloom": 0.07,
		"glow_hdr_threshold": 1.05,
		"fog_enabled": false,
		"fog_color": "#8aa4c2",
		"fog_density": 0.005,
		"pulse_mode": "none",
		"dof_enabled": false,
		"dof_focus_distance": 2.4,
		"dof_blur_amount": 0.08,
		"accent_fills": [],
	},
	"night_alert": {
		"description": "Dim cool ambient, pulsing red/amber alert accents, crisp character key fill.",
		"ambient_color": "#3a4f70",
		"ambient_energy": 0.42,
		"sun_color": "#cfe0ff",
		"sun_energy": 0.82,
		"sun_dir": [-0.32, -0.58, 0.75],
		"rim_color": "#74b0ff",
		"rim_energy": 0.48,
		"rim_dir": [0.52, -0.30, -0.80],
		"lamp_color": "#ffd6b0",
		"lamp_energy": 0.38,
		"lamp_range": 5.2,
		"lamp_attenuation": 1.45,
		"lamp_layout": "grid",
		"exposure": 1.06,
		"tonemap_white": 5.5,
		"shadow_blur": 1.8,
		"shadow_opacity": 0.86,
		"glow_enabled": true,
		"glow_intensity": 0.48,
		"glow_strength": 0.78,
		"glow_bloom": 0.12,
		"glow_hdr_threshold": 0.95,
		"fog_enabled": true,
		"fog_color": "#1a2234",
		"fog_density": 0.012,
		"pulse_mode": "alert",
		"pulse_hz": 1.15,
		"alert_primary_color": "#ff3b24",
		"alert_secondary_color": "#ff9e2c",
		"alert_energy_min": 0.25,
		"alert_energy_max": 1.25,
		"face_fill_color": "#ffe9d4",
		"face_fill_energy": 0.55,
		"dof_enabled": false,
		"dof_focus_distance": 2.0,
		"dof_blur_amount": 0.09,
		"accent_fills": [],
	},
	"storm_exterior": {
		"description": "High-contrast moody cyan-slate rim, stronger atmospheric fog, subtle wind strobe.",
		"ambient_color": "#4b647e",
		"ambient_energy": 0.44,
		"sun_color": "#9dc6e2",
		"sun_energy": 0.92,
		"sun_dir": [-0.52, -0.44, 0.73],
		"rim_color": "#6cd6f5",
		"rim_energy": 0.76,
		"rim_dir": [0.58, -0.26, -0.77],
		"lamp_color": "#b4d6ea",
		"lamp_energy": 0.45,
		"lamp_range": 5.8,
		"lamp_attenuation": 1.35,
		"lamp_layout": "grid",
		"exposure": 1.02,
		"tonemap_white": 5.5,
		"shadow_blur": 1.4,
		"shadow_opacity": 0.90,
		"glow_enabled": true,
		"glow_intensity": 0.42,
		"glow_strength": 0.74,
		"glow_bloom": 0.10,
		"glow_hdr_threshold": 1.0,
		"fog_enabled": true,
		"fog_color": "#4b6880",
		"fog_density": 0.030,
		"pulse_mode": "storm",
		"pulse_hz": 0.65,
		"strobe_color": "#c8ecff",
		"strobe_energy_min": 0.08,
		"strobe_energy_max": 0.95,
		"dof_enabled": false,
		"dof_focus_distance": 3.0,
		"dof_blur_amount": 0.08,
		"accent_fills": [],
	},
	"warm_interior": {
		"description": "Cozy tungsten overhead, soft neutral bounce, gentle shadows for offices/quarters.",
		"ambient_color": "#c4b7a6",
		"ambient_energy": 0.62,
		"sun_color": "#ffe6c2",
		"sun_energy": 0.88,
		"sun_dir": [-0.26, -0.68, 0.68],
		"rim_color": "#e6edf8",
		"rim_energy": 0.28,
		"rim_dir": [0.42, -0.40, -0.81],
		"lamp_color": "#ffd9a8",
		"lamp_energy": 0.78,
		"lamp_range": 6.5,
		"lamp_attenuation": 1.15,
		"lamp_layout": "grid",
		"exposure": 1.02,
		"tonemap_white": 6.0,
		"shadow_blur": 2.2,
		"shadow_opacity": 0.72,
		"glow_enabled": true,
		"glow_intensity": 0.28,
		"glow_strength": 0.60,
		"glow_bloom": 0.06,
		"glow_hdr_threshold": 1.1,
		"fog_enabled": false,
		"fog_color": "#b0a292",
		"fog_density": 0.004,
		"pulse_mode": "none",
		"dof_enabled": false,
		"dof_focus_distance": 2.2,
		"dof_blur_amount": 0.07,
		"accent_fills": [],
	},
	"dim_corridor": {
		"description": "Overhead pool lights along corridor, cool shadows, crisp rim light on actors.",
		"ambient_color": "#4f6684",
		"ambient_energy": 0.42,
		"sun_color": "#c2d6f0",
		"sun_energy": 0.62,
		"sun_dir": [-0.20, -0.78, 0.59],
		"rim_color": "#8ac4ff",
		"rim_energy": 0.58,
		"rim_dir": [0.18, -0.32, -0.93],
		"lamp_color": "#e2eeff",
		"lamp_energy": 0.92,
		"lamp_range": 4.4,
		"lamp_attenuation": 1.55,
		"lamp_layout": "corridor_pools",
		"exposure": 1.05,
		"tonemap_white": 5.5,
		"shadow_blur": 1.7,
		"shadow_opacity": 0.84,
		"glow_enabled": true,
		"glow_intensity": 0.38,
		"glow_strength": 0.70,
		"glow_bloom": 0.09,
		"glow_hdr_threshold": 1.0,
		"fog_enabled": true,
		"fog_color": "#24354a",
		"fog_density": 0.015,
		"pulse_mode": "none",
		"dof_enabled": false,
		"dof_focus_distance": 2.4,
		"dof_blur_amount": 0.08,
		"accent_fills": [],
	},
	"med_lab": {
		"description": "Clean bright clinical daylight-white, teal console glow, soft ambient fill.",
		"ambient_color": "#cadbe8",
		"ambient_energy": 0.68,
		"sun_color": "#f4faff",
		"sun_energy": 1.05,
		"sun_dir": [-0.24, -0.72, 0.65],
		"rim_color": "#c8f2ef",
		"rim_energy": 0.34,
		"rim_dir": [0.40, -0.36, -0.84],
		"lamp_color": "#f0f8ff",
		"lamp_energy": 0.84,
		"lamp_range": 6.8,
		"lamp_attenuation": 1.1,
		"lamp_layout": "grid",
		"exposure": 1.04,
		"tonemap_white": 6.5,
		"shadow_blur": 2.0,
		"shadow_opacity": 0.68,
		"glow_enabled": true,
		"glow_intensity": 0.30,
		"glow_strength": 0.62,
		"glow_bloom": 0.06,
		"glow_hdr_threshold": 1.08,
		"fog_enabled": false,
		"fog_color": "#c4dce4",
		"fog_density": 0.004,
		"pulse_mode": "none",
		"teal_accent_color": "#4ae6d8",
		"teal_accent_energy": 0.46,
		"dof_enabled": false,
		"dof_focus_distance": 2.0,
		"dof_blur_amount": 0.07,
		"accent_fills": [],
	},
}


static func list_profiles() -> Array[String]:
	var names: Array[String] = []
	for key: Variant in PRESETS.keys():
		names.append(str(key))
	names.sort()
	return names


static func has_profile(profile_name: String) -> bool:
	return PRESETS.has(profile_name)


static func get_preset(profile_name: String, overrides: Dictionary = {}) -> Dictionary:
	var key: String = profile_name if PRESETS.has(profile_name) else DEFAULT_PROFILE
	var merged: Dictionary = (PRESETS[key] as Dictionary).duplicate(true)
	merged["profile_name"] = key
	if not overrides.is_empty():
		# Support both canonical keys and legacy bay.gd lighting keys.
		var legacy_map := {
			"ambient": "ambient_color",
			"sun_color": "sun_color",
			"sun_energy": "sun_energy",
			"sun_dir": "sun_dir",
			"ambient_energy": "ambient_energy",
			"lamp_energy": "lamp_energy",
			"lamp_color": "lamp_color",
			"exposure": "exposure",
		}
		for k: Variant in overrides.keys():
			var ks: String = str(k)
			var target_k: String = str(legacy_map.get(ks, ks))
			merged[target_k] = overrides[k]
	return merged


static func get_rendering_method() -> String:
	var method: String = str(RenderingServer.get_current_rendering_method())
	if method == "":
		method = str(ProjectSettings.get_setting("rendering/renderer/rendering_method", "gl_compatibility"))
	return method.to_lower()


static func is_gl_compatibility() -> bool:
	return get_rendering_method() == "gl_compatibility"


static func is_dof_supported() -> bool:
	# Depth-of-field blur on CameraAttributesPractical is supported on Forward+ and Mobile.
	# On GL Compatibility we keep CameraAttributesPractical safely configured with DoF disabled
	# unless explicitly forced, avoiding driver artifacts or warnings.
	return not is_gl_compatibility()


static func create_camera_attributes(cfg: Dictionary) -> CameraAttributesPractical:
	var attrs := CameraAttributesPractical.new()
	attrs.exposure_multiplier = clampf(float(cfg.get("camera_exposure_multiplier", 1.0)), 0.25, 4.0)
	var requested_dof: bool = bool(cfg.get("dof_enabled", false))
	var force_dof: bool = bool(cfg.get("dof_force", false))
	var enable_dof: bool = requested_dof and (is_dof_supported() or force_dof)
	var focus_dist: float = maxf(float(cfg.get("dof_focus_distance", 2.2)), 0.35)
	var far_transition: float = maxf(float(cfg.get("dof_far_transition", 2.5)), 0.4)
	var near_transition: float = maxf(float(cfg.get("dof_near_transition", 0.6)), 0.2)
	var blur_amount: float = clampf(float(cfg.get("dof_blur_amount", 0.08)), 0.0, 0.35)
	attrs.dof_blur_far_enabled = enable_dof
	attrs.dof_blur_far_distance = focus_dist + maxf(float(cfg.get("dof_focus_span", 1.2)), 0.35)
	attrs.dof_blur_far_transition = far_transition
	attrs.dof_blur_near_enabled = enable_dof and bool(cfg.get("dof_near_enabled", false))
	attrs.dof_blur_near_distance = maxf(focus_dist - 0.7, 0.2)
	attrs.dof_blur_near_transition = near_transition
	attrs.dof_blur_amount = blur_amount
	return attrs


static func configure_world_environment(cfg: Dictionary, backdrop_opts: Dictionary = {}) -> WorldEnvironment:
	var env := Environment.new()
	var use_cyclorama: bool = bool(backdrop_opts.get("cyclorama", false))
	if use_cyclorama:
		env.background_mode = Environment.BG_COLOR
		var sky_c: Variant = backdrop_opts.get("sky_color", Color("#1d3f86"))
		env.background_color = sky_c as Color if sky_c is Color else Color(str(sky_c))
	else:
		env.background_mode = Environment.BG_SKY
		var palette_v: Variant = backdrop_opts.get("palette", {})
		var palette: Dictionary = palette_v as Dictionary if palette_v is Dictionary else {}
		var sky := Sky.new()
		var sky_mat := ProceduralSkyMaterial.new()
		sky_mat.sky_top_color = Color(str(palette.get("sky_top", "#3f7fc6")))
		sky_mat.sky_horizon_color = Color(str(palette.get("horizon", "#c4dcee")))
		sky_mat.sky_curve = 0.12
		sky_mat.ground_bottom_color = Color(str(palette.get("rock", "#9fb0c2")))
		sky_mat.ground_horizon_color = Color(str(palette.get("horizon", "#c4dcee")))
		sky.sky_material = sky_mat
		env.sky = sky

	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(str(cfg.get("ambient_color", "#a2b4cc")))
	env.ambient_light_energy = clampf(float(cfg.get("ambient_energy", 0.58)), 0.05, 2.5)

	# Stylized semi-realistic ACES Filmic tonemapping.
	var tonemap_name: String = str(cfg.get("tonemap_mode", "aces")).to_lower()
	match tonemap_name:
		"linear":
			env.tonemap_mode = Environment.TONE_MAPPER_LINEAR
		"reinhard":
			env.tonemap_mode = Environment.TONE_MAPPER_REINHARDT
		"filmic":
			env.tonemap_mode = Environment.TONE_MAPPER_FILMIC
		_:
			env.tonemap_mode = Environment.TONE_MAPPER_ACES
	env.tonemap_exposure = clampf(float(cfg.get("exposure", 1.04)), 0.2, 3.0)
	env.tonemap_white = clampf(float(cfg.get("tonemap_white", 6.0)), 1.0, 16.0)

	# Subtle glow (safe on both GL Compatibility and Forward+; avoid heavy SDFGI/SSAO/SSIL).
	var glow_on: bool = bool(cfg.get("glow_enabled", true))
	env.glow_enabled = glow_on
	if glow_on:
		env.glow_intensity = clampf(float(cfg.get("glow_intensity", 0.34)), 0.0, 1.5)
		env.glow_strength = clampf(float(cfg.get("glow_strength", 0.68)), 0.0, 1.5)
		env.glow_bloom = clampf(float(cfg.get("glow_bloom", 0.07)), 0.0, 0.4)
		env.glow_hdr_threshold = clampf(float(cfg.get("glow_hdr_threshold", 1.05)), 0.5, 4.0)
		env.glow_blend_mode = Environment.GLOW_BLEND_MODE_SOFTLIGHT

	# Atmospheric exponential fog (supported on GL Compatibility & Forward+).
	var fog_on: bool = bool(cfg.get("fog_enabled", false))
	env.fog_enabled = fog_on
	if fog_on:
		env.fog_mode = Environment.FOG_MODE_EXPONENTIAL
		env.fog_light_color = Color(str(cfg.get("fog_color", "#4b6880")))
		env.fog_light_energy = clampf(float(cfg.get("fog_energy", 0.85)), 0.0, 2.0)
		env.fog_density = clampf(float(cfg.get("fog_density", 0.015)), 0.0, 0.25)
		env.fog_sky_affect = clampf(float(cfg.get("fog_sky_affect", 0.35)), 0.0, 1.0)

	var world := WorldEnvironment.new()
	world.name = "WorldEnvironment"
	world.environment = env
	world.camera_attributes = create_camera_attributes(cfg)
	return world


static func apply_profile(
	parent: Node3D,
	profile_name: String,
	room_min: Vector3,
	room_max: Vector3,
	overrides: Dictionary = {},
	backdrop_opts: Dictionary = {}
) -> Node3D:
	var cfg: Dictionary = get_preset(profile_name, overrides)
	var rig := Node3D.new()
	rig.name = "LightingRig"
	parent.add_child(rig)

	var world: WorldEnvironment = configure_world_environment(cfg, backdrop_opts)
	rig.add_child(world)

	var room_center: Vector3 = (room_min + room_max) * 0.5
	var room_size: Vector3 = room_max - room_min

	# Primary key directional light with soft shadows (SHADOW_ORTHOGONAL is clean & cheap).
	var key := DirectionalLight3D.new()
	key.name = "KeyLight"
	var key_dir: Vector3 = _vec3(cfg.get("sun_dir", null), Vector3(-0.38, -0.52, 0.76)).normalized()
	key.transform.basis = Basis.looking_at(key_dir, Vector3.UP)
	key.light_energy = float(cfg.get("sun_energy", 1.10))
	key.light_color = Color(str(cfg.get("sun_color", "#d6e4ff")))
	key.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
	key.shadow_enabled = bool(cfg.get("shadows_enabled", true))
	key.shadow_blur = clampf(float(cfg.get("shadow_blur", 1.6)), 0.2, 4.0)
	key.shadow_bias = 0.04
	key.shadow_normal_bias = 1.2
	key.shadow_opacity = clampf(float(cfg.get("shadow_opacity", 0.82)), 0.2, 1.0)
	key.directional_shadow_mode = DirectionalLight3D.SHADOW_ORTHOGONAL
	key.directional_shadow_max_distance = maxf(room_size.length() * 2.0, 26.0)
	rig.add_child(key)

	# Clean backlight / rim directional light (shadowless for crisp actor silhouettes).
	var rim_energy: float = float(cfg.get("rim_energy", 0.35))
	if rim_energy > 0.01:
		var rim := DirectionalLight3D.new()
		rim.name = "RimLight"
		var rim_dir: Vector3 = _vec3(cfg.get("rim_dir", null), Vector3(0.46, -0.34, -0.82)).normalized()
		rim.transform.basis = Basis.looking_at(rim_dir, Vector3.UP)
		rim.light_energy = rim_energy
		rim.light_color = Color(str(cfg.get("rim_color", "#c8e2ff")))
		rim.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
		rim.shadow_enabled = false
		rig.add_child(rim)

	# Overhead interior lights adapted to room dimensions.
	var lamp_layout: String = str(cfg.get("lamp_layout", "grid"))
	var lamp_color := Color(str(cfg.get("lamp_color", "#ffeedb")))
	var lamp_energy: float = float(cfg.get("lamp_energy", 0.68))
	var lamp_range: float = float(cfg.get("lamp_range", 6.5))
	var lamp_atten: float = float(cfg.get("lamp_attenuation", 1.2))
	var lamp_y: float = room_max.y - 0.42

	if lamp_layout == "corridor_pools":
		var along_z: bool = room_size.z >= room_size.x
		var span: float = room_size.z if along_z else room_size.x
		var count: int = clampi(int(round(span / 3.4)), 2, 7)
		for i in count:
			var t: float = (float(i) + 0.5) / float(count)
			var pos := Vector3(
				room_center.x if along_z else lerpf(room_min.x + 0.9, room_max.x - 0.9, t),
				lamp_y,
				lerpf(room_min.z + 0.9, room_max.z - 0.9, t) if along_z else room_center.z
			)
			var pool := SpotLight3D.new()
			pool.name = "CorridorPool_%d" % i
			pool.position = pos
			pool.rotation_degrees = Vector3(-90.0, 0.0, 0.0)
			pool.spot_range = maxf(lamp_range, room_size.y + 1.4)
			pool.spot_angle = 54.0
			pool.spot_angle_attenuation = 0.65
			pool.spot_attenuation = lamp_atten
			pool.light_energy = lamp_energy * 1.45
			pool.light_color = lamp_color
			pool.shadow_enabled = false
			rig.add_child(pool)
	else:
		var cols: int = 1 if room_size.x < 5.5 else 2
		var rows: int = clampi(int(round(room_size.z / 3.4)), 2, 4)
		for ix in cols:
			var lx: float = room_center.x if cols == 1 else lerpf(room_min.x + room_size.x * 0.26, room_max.x - room_size.x * 0.26, float(ix))
			for iz in rows:
				var tz: float = (float(iz) + 0.5) / float(rows)
				var lz: float = lerpf(room_min.z + 0.9, room_max.z - 0.9, tz)
				var lamp := OmniLight3D.new()
				lamp.name = "OverheadLamp_%d_%d" % [ix, iz]
				lamp.position = Vector3(lx, lamp_y, lz)
				lamp.omni_range = lamp_range
				lamp.omni_attenuation = lamp_atten
				lamp.light_energy = lamp_energy
				lamp.light_color = lamp_color
				lamp.shadow_enabled = false
				rig.add_child(lamp)

	# Profile-specific accent & dynamic lights.
	var dynamic_lights: Array[Dictionary] = []
	var pulse_mode: String = str(cfg.get("pulse_mode", "none"))
	if pulse_mode == "alert":
		var alert_a := OmniLight3D.new()
		alert_a.name = "AlertAccent_L"
		alert_a.position = Vector3(room_min.x + 0.7, room_max.y - 0.55, room_center.z)
		alert_a.omni_range = maxf(room_size.x * 0.75, 5.5)
		alert_a.omni_attenuation = 1.3
		alert_a.light_color = Color(str(cfg.get("alert_primary_color", "#ff3b24")))
		alert_a.light_energy = float(cfg.get("alert_energy_max", 1.25)) * 0.65
		alert_a.shadow_enabled = false
		rig.add_child(alert_a)
		dynamic_lights.append({
			"light": alert_a,
			"mode": "alert",
			"phase": 0.0,
			"hz": float(cfg.get("pulse_hz", 1.15)),
			"min": float(cfg.get("alert_energy_min", 0.25)),
			"max": float(cfg.get("alert_energy_max", 1.25)),
		})

		var alert_b := OmniLight3D.new()
		alert_b.name = "AlertAccent_R"
		alert_b.position = Vector3(room_max.x - 0.7, room_max.y - 0.55, room_center.z)
		alert_b.omni_range = maxf(room_size.x * 0.75, 5.5)
		alert_b.omni_attenuation = 1.3
		alert_b.light_color = Color(str(cfg.get("alert_secondary_color", "#ff9e2c")))
		alert_b.light_energy = float(cfg.get("alert_energy_max", 1.25)) * 0.55
		alert_b.shadow_enabled = false
		rig.add_child(alert_b)
		dynamic_lights.append({
			"light": alert_b,
			"mode": "alert",
			"phase": PI,
			"hz": float(cfg.get("pulse_hz", 1.15)),
			"min": float(cfg.get("alert_energy_min", 0.25)),
			"max": float(cfg.get("alert_energy_max", 1.15)),
		})

		# Readable warm-neutral center character fill so faces stay clear under alert tint.
		var face_fill := OmniLight3D.new()
		face_fill.name = "AlertFaceKeyFill"
		face_fill.position = Vector3(room_center.x, minf(room_max.y - 0.9, 2.1), room_center.z)
		face_fill.omni_range = maxf(room_size.length() * 0.65, 6.0)
		face_fill.omni_attenuation = 1.0
		face_fill.light_color = Color(str(cfg.get("face_fill_color", "#ffe9d4")))
		face_fill.light_energy = float(cfg.get("face_fill_energy", 0.55))
		face_fill.shadow_enabled = false
		rig.add_child(face_fill)
	elif pulse_mode == "storm":
		var strobe := DirectionalLight3D.new()
		strobe.name = "StormStrobe"
		strobe.transform.basis = Basis.looking_at(Vector3(0.35, -0.55, 0.75).normalized(), Vector3.UP)
		strobe.light_color = Color(str(cfg.get("strobe_color", "#c8ecff")))
		strobe.light_energy = float(cfg.get("strobe_energy_min", 0.08))
		strobe.sky_mode = DirectionalLight3D.SKY_MODE_LIGHT_ONLY
		strobe.shadow_enabled = false
		rig.add_child(strobe)
		dynamic_lights.append({
			"light": strobe,
			"mode": "storm",
			"phase": 0.0,
			"hz": float(cfg.get("pulse_hz", 0.65)),
			"min": float(cfg.get("strobe_energy_min", 0.08)),
			"max": float(cfg.get("strobe_energy_max", 0.95)),
		})

	if cfg.has("teal_accent_color") and float(cfg.get("teal_accent_energy", 0.0)) > 0.0:
		var teal := OmniLight3D.new()
		teal.name = "MedLabTealAccent"
		teal.position = Vector3(room_min.x + 1.2, 1.55, room_min.z + 1.4)
		teal.omni_range = 4.8
		teal.omni_attenuation = 1.2
		teal.light_color = Color(str(cfg["teal_accent_color"]))
		teal.light_energy = float(cfg["teal_accent_energy"])
		teal.shadow_enabled = false
		rig.add_child(teal)

	# Custom fill lights from overrides (e.g. ConsoleGlow, FaceFill, WindowSpill).
	var fills_value: Variant = cfg.get("fills", [])
	if fills_value is Array:
		for fill_value: Variant in fills_value as Array:
			if not fill_value is Dictionary:
				continue
			var fill: Dictionary = fill_value as Dictionary
			var light := OmniLight3D.new()
			light.name = str(fill.get("id", "Fill"))
			light.position = _vec3(fill.get("pos", null), Vector3(room_center.x, 1.8, room_center.z))
			light.omni_range = float(fill.get("range", 4.0))
			light.omni_attenuation = float(fill.get("attenuation", 1.2))
			light.light_energy = float(fill.get("energy", 0.6))
			light.light_color = Color(str(fill.get("color", "#ffffff")))
			light.shadow_enabled = false
			rig.add_child(light)

	rig.set_meta("profile_name", str(cfg.get("profile_name", DEFAULT_PROFILE)))
	rig.set_meta("world_env", world)
	rig.set_meta("dynamic_lights", dynamic_lights)
	return rig


static func update_time(rig: Node3D, time_sec: float) -> void:
	if rig == null or not rig.has_meta("dynamic_lights"):
		return
	var entries: Variant = rig.get_meta("dynamic_lights")
	if not entries is Array:
		return
	for item: Variant in entries as Array:
		if not item is Dictionary:
			continue
		var entry: Dictionary = item as Dictionary
		var light: Light3D = entry.get("light", null) as Light3D
		if light == null or not is_instance_valid(light):
			continue
		var mode: String = str(entry.get("mode", ""))
		var hz: float = float(entry.get("hz", 1.0))
		var phase: float = float(entry.get("phase", 0.0))
		var lo: float = float(entry.get("min", 0.1))
		var hi: float = float(entry.get("max", 1.0))
		if mode == "alert":
			var wave: float = 0.5 + 0.5 * sin(time_sec * TAU * hz + phase)
			var shaped: float = wave * wave
			light.light_energy = lerpf(lo, hi, shaped)
		elif mode == "storm":
			var wind_wave: float = 0.5 + 0.5 * sin(time_sec * TAU * hz + phase)
			var flash: float = pow(maxf(0.0, sin(time_sec * TAU * (hz * 0.41) + 0.7)), 14.0)
			light.light_energy = lerpf(lo, hi, clampf(wind_wave * 0.25 + flash * 0.85, 0.0, 1.0))


static func set_dof_focus(rig: Node3D, focus_distance: float, enabled: bool, blur_amount: float = 0.08) -> void:
	if rig == null or not rig.has_meta("world_env"):
		return
	var world: WorldEnvironment = rig.get_meta("world_env") as WorldEnvironment
	if world == null or not is_instance_valid(world):
		return
	var attrs: CameraAttributesPractical = world.camera_attributes as CameraAttributesPractical
	if attrs == null:
		attrs = CameraAttributesPractical.new()
		world.camera_attributes = attrs
	var allow: bool = enabled and is_dof_supported()
	attrs.dof_blur_far_enabled = allow
	attrs.dof_blur_far_distance = maxf(focus_distance, 0.4) + 1.1
	attrs.dof_blur_far_transition = 2.2
	attrs.dof_blur_amount = clampf(blur_amount, 0.0, 0.35)


static func _vec3(value: Variant, fallback: Vector3) -> Vector3:
	if value is Array and (value as Array).size() >= 3:
		var a: Array = value as Array
		return Vector3(float(a[0]), float(a[1]), float(a[2]))
	return fallback
