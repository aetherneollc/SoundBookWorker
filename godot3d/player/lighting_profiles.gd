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
		"alert_primary_color": "#ff2e1c",
		"alert_secondary_color": "#ff7a20",
		"alert_energy_min": 0.55,
		"alert_energy_max": 2.35,
		"face_fill_color": "#ffe9d4",
		"face_fill_energy": 0.62,
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
		"description": "Overhead pool lights along corridor, cool shadows, crisp rim & face separation on actors.",
		"ambient_color": "#587292",
		"ambient_energy": 0.48,
		"sun_color": "#cfe0f5",
		"sun_energy": 0.72,
		"sun_dir": [-0.24, -0.74, 0.62],
		"rim_color": "#9ad0ff",
		"rim_energy": 0.88,
		"rim_dir": [0.22, -0.28, -0.93],
		"lamp_color": "#e6f0ff",
		"lamp_energy": 1.02,
		"lamp_range": 4.8,
		"lamp_attenuation": 1.38,
		"lamp_layout": "corridor_pools",
		"exposure": 1.06,
		"tonemap_white": 5.5,
		"shadow_blur": 1.6,
		"shadow_opacity": 0.80,
		"glow_enabled": true,
		"glow_intensity": 0.34,
		"glow_strength": 0.65,
		"glow_bloom": 0.08,
		"glow_hdr_threshold": 1.05,
		"fog_enabled": true,
		"fog_color": "#263a52",
		"fog_density": 0.012,
		"pulse_mode": "none",
		"dof_enabled": false,
		"dof_focus_distance": 2.4,
		"dof_blur_amount": 0.08,
		"accent_fills": [],
	},
	"med_lab": {
		"description": "Clinical daylight-white with directional contrast, contact shadow depth, and teal task glow.",
		"ambient_color": "#8eaec4",
		"ambient_energy": 0.44,
		"sun_color": "#f6fbff",
		"sun_energy": 0.94,
		"sun_dir": [-0.42, -0.62, 0.66],
		"rim_color": "#8cebe4",
		"rim_energy": 0.46,
		"rim_dir": [0.44, -0.32, -0.84],
		"lamp_color": "#edf6ff",
		"lamp_energy": 0.56,
		"lamp_range": 6.4,
		"lamp_attenuation": 1.25,
		"lamp_layout": "grid",
		"exposure": 0.98,
		"tonemap_white": 6.0,
		"shadow_blur": 1.5,
		"shadow_opacity": 0.84,
		"glow_enabled": true,
		"glow_intensity": 0.24,
		"glow_strength": 0.54,
		"glow_bloom": 0.05,
		"glow_hdr_threshold": 1.12,
		"fog_enabled": false,
		"fog_color": "#b4d0dc",
		"fog_density": 0.004,
		"pulse_mode": "none",
		"teal_accent_color": "#38dcd0",
		"teal_accent_energy": 0.62,
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

	if bool(cfg.get("adjustment_enabled", false)):
		env.adjustment_enabled = true
		env.adjustment_brightness = clampf(float(cfg.get("adjustment_brightness", 1.0)), 0.4, 2.0)
		env.adjustment_contrast = clampf(float(cfg.get("adjustment_contrast", 1.0)), 0.5, 2.0)
		env.adjustment_saturation = clampf(float(cfg.get("adjustment_saturation", 1.0)), 0.0, 2.0)

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
	var tracked: Array = []

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
	tracked.append({"light": key, "base": key.light_energy, "group": "key"})

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
		tracked.append({"light": rim, "base": rim.light_energy, "group": "rim"})

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
			tracked.append({"light": pool, "base": pool.light_energy, "group": "overhead"})
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
				tracked.append({"light": lamp, "base": lamp.light_energy, "group": "overhead"})

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

	# Dedicated close-up & dark-scene character face exposure light (BUG-005 / BUG-013).
	var char_face_fill := OmniLight3D.new()
	char_face_fill.name = "CharacterFaceFill"
	char_face_fill.position = Vector3(room_center.x, 1.75, room_center.z)
	char_face_fill.omni_range = 2.8
	char_face_fill.omni_attenuation = 1.6
	char_face_fill.light_color = Color("#fff2e2")
	char_face_fill.light_energy = 0.0
	char_face_fill.shadow_enabled = false
	rig.add_child(char_face_fill)

	# Dedicated event-driven tactical alert wash (BUG-012).
	var event_alert := OmniLight3D.new()
	event_alert.name = "EventAlertWash"
	event_alert.position = Vector3(room_center.x, maxf(room_max.y - 0.32, 2.2), room_center.z)
	event_alert.omni_range = maxf(room_max.x - room_min.x, room_max.z - room_min.z) * 1.15
	event_alert.omni_attenuation = 1.1
	event_alert.light_color = Color("#ff2a1a")
	event_alert.light_energy = 0.0
	event_alert.shadow_enabled = false
	rig.add_child(event_alert)

	var emergency := OmniLight3D.new()
	emergency.name = "EventEmergencyWash"
	emergency.position = Vector3(room_center.x, maxf(room_max.y - 0.28, 2.15), room_center.z)
	emergency.omni_range = maxf(room_size.x, room_size.z) * 1.25
	emergency.omni_attenuation = 1.05
	emergency.light_color = Color("#ff5a18")
	emergency.light_energy = 0.0
	emergency.shadow_enabled = false
	rig.add_child(emergency)

	var lightning := OmniLight3D.new()
	lightning.name = "EventLightningKick"
	lightning.position = Vector3(room_center.x, 1.85, room_min.z + minf(1.4, room_size.z * 0.25))
	lightning.omni_range = maxf(room_size.length(), 8.0)
	lightning.omni_attenuation = 0.9
	lightning.light_color = Color("#d7ecff")
	lightning.light_energy = 0.0
	lightning.shadow_enabled = false
	rig.add_child(lightning)

	var array_lock := OmniLight3D.new()
	array_lock.name = "EventArrayLockSpill"
	array_lock.position = Vector3(room_center.x, 1.65, room_min.z + minf(1.1, room_size.z * 0.2))
	array_lock.omni_range = maxf(room_size.z * 0.85, 5.5)
	array_lock.omni_attenuation = 1.15
	array_lock.light_color = Color("#39ffc0")
	array_lock.light_energy = 0.0
	array_lock.shadow_enabled = false
	rig.add_child(array_lock)

	rig.set_meta("profile_name", str(cfg.get("profile_name", DEFAULT_PROFILE)))
	rig.set_meta("world_env", world)
	rig.set_meta("dynamic_lights", dynamic_lights)
	rig.set_meta("tracked_lights", tracked)
	rig.set_meta("char_face_fill", char_face_fill)
	rig.set_meta("event_alert", event_alert)
	rig.set_meta("event_emergency", emergency)
	rig.set_meta("event_lightning", lightning)
	rig.set_meta("event_array_lock", array_lock)
	rig.set_meta("room_min", room_min)
	rig.set_meta("room_max", room_max)
	rig.set_meta("base_ambient_energy", float(cfg.get("ambient_energy", 0.55)))
	rig.set_meta("base_exposure", float(cfg.get("exposure", 1.0)))
	rig.set_meta("array_lock_held", false)
	return rig


## Extra practicals for a resolved RetroArtProfile. Neutral profiles no-op.
static func apply_art_profile(rig: Node3D, profile: Dictionary) -> void:
	if rig == null or not bool(profile.get("enabled", false)):
		return
	if bool(rig.get_meta("art_decorated", false)):
		return
	var preset := str(profile.get("preset", ""))
	var room_min: Vector3 = rig.get_meta("room_min", Vector3(-7.0, 0.0, -5.0)) as Vector3
	var room_max: Vector3 = rig.get_meta("room_max", Vector3(7.0, 3.4, 5.0)) as Vector3
	var center: Vector3 = (room_min + room_max) * 0.5
	match preset:
		"ps2_urban":
			_add_fill(rig, "RetroFillMagenta", Vector3(room_min.x + 1.1, 1.7, center.z), Color("#c45cff"), 0.42, 7.5)
			_add_fill(rig, "RetroFillTeal", Vector3(room_max.x - 1.1, 1.35, center.z + 0.4), Color("#14b8b0"), 0.48, 7.2)
		"retro_industrial_scifi":
			_add_fill(rig, "RetroSodiumFill", Vector3(center.x, 2.05, room_max.z - 1.2), Color("#ffb060"), 0.36, 6.5)
			_add_fill(rig, "RetroBeacon", Vector3(room_max.x - 0.8, room_max.y - 0.55, room_min.z + 1.3), Color("#ff5a1f"), 0.55, 5.5)
			_add_fill(rig, "RetroFluorescent", Vector3(room_min.x + 1.4, 1.6, center.z), Color("#c5e8ff"), 0.34, 6.0)
		"retro_horror":
			var spot := SpotLight3D.new()
			spot.name = "RetroHorrorCone"
			spot.position = Vector3(center.x, room_max.y - 0.18, center.z)
			spot.rotation_degrees = Vector3(-78.0, 18.0, 0.0)
			spot.spot_range = maxf(room_max.y + 1.5, 4.5)
			spot.spot_angle = 24.0
			spot.spot_angle_attenuation = 0.45
			spot.light_color = Color("#d5e6f5")
			spot.light_energy = 2.6
			spot.shadow_enabled = false
			rig.add_child(spot)
		"retro_military":
			var top := SpotLight3D.new()
			top.name = "RetroBunkerTop"
			top.position = Vector3(center.x, room_max.y - 0.16, center.z)
			top.rotation_degrees = Vector3(-90.0, 0.0, 0.0)
			top.spot_range = maxf(room_max.y + 0.8, 4.0)
			top.spot_angle = 42.0
			top.light_color = Color("#e4e0d2")
			top.light_energy = 1.35
			top.shadow_enabled = false
			rig.add_child(top)
			_add_fill(rig, "RetroBunkerFill", Vector3(center.x, 1.15, room_max.z - 1.0), Color("#6d7464"), 0.28, 6.0)
		_:
			pass
	rig.set_meta("art_decorated", true)
	rig.set_meta("art_preset", preset)


static func _add_fill(rig: Node3D, node_name: String, pos: Vector3, color: Color, energy: float, range_m: float) -> void:
	var light := OmniLight3D.new()
	light.name = node_name
	light.position = pos
	light.omni_range = range_m
	light.omni_attenuation = 1.25
	light.light_color = color
	light.light_energy = energy
	light.shadow_enabled = false
	rig.add_child(light)


## Deterministic storm bolt: one primary flash and a short secondary flicker per 2.4s.
static func storm_flash_amount(time_sec: float, storm_intensity: float) -> float:
	if storm_intensity < 0.02:
		return 0.0
	var cycle: float = fmod(maxf(time_sec, 0.0), 2.4)
	var bolt := 0.0
	if cycle < 0.055:
		bolt = 1.0 - cycle / 0.055
	elif cycle > 1.05 and cycle < 1.12:
		bolt = 0.55 * (1.0 - (cycle - 1.05) / 0.07)
	return bolt * clampf(storm_intensity, 0.0, 1.0)


## Overhead blackout stutter. Returns 1.0 when flicker is off.
static func power_stutter_mul(time_sec: float, flicker: float) -> float:
	var amount: float = clampf(flicker, 0.0, 1.0)
	if amount < 0.01:
		return 1.0
	var pattern: Array[float] = [0.06, 0.7, 0.0, 0.42, 0.88, 0.15, 1.0]
	var idx: int = int(floor(maxf(time_sec, 0.0) * 14.0)) % pattern.size()
	return lerpf(1.0, pattern[idx], amount)


## Automatically illuminates primary subject's face during close-ups & dark scenes
## without washing out surrounding walls (BUG-005 / BUG-013).
static func update_subject_fill(rig: Node3D, cam_pos: Vector3, subject_head: Vector3, shot_type: String) -> void:
	if rig == null or not rig.has_meta("char_face_fill"):
		return
	var fill: OmniLight3D = rig.get_meta("char_face_fill") as OmniLight3D
	if fill == null or not is_instance_valid(fill):
		return
	if subject_head == Vector3.ZERO:
		fill.light_energy = 0.0
		return
	var prof: String = str(rig.get_meta("profile_name", "day_bay"))
	var to_cam: Vector3 = (cam_pos - subject_head)
	var dist: float = to_cam.length()
	if dist < 0.05:
		fill.light_energy = 0.0
		return
	var dir_c: Vector3 = to_cam / dist
	# Place soft key fill 0.85m in front & slightly above eye line toward camera.
	fill.position = subject_head + dir_c * minf(0.85, dist * 0.55) + Vector3(0.0, 0.22, 0.0)
	var is_closeup: bool = shot_type in ["close_up", "closeup", "extreme_close_up", "reaction", "over_the_shoulder", "over_shoulder"]
	var is_dark: bool = prof in ["dim_corridor", "night_alert", "storm_exterior"]
	if is_dark and is_closeup:
		fill.light_energy = 0.58
		fill.omni_range = 2.6
	elif is_dark:
		fill.light_energy = 0.42
		fill.omni_range = 3.2
	elif is_closeup and prof != "med_lab":
		fill.light_energy = 0.26
		fill.omni_range = 2.4
	else:
		fill.light_energy = 0.0


## Modulates scene lighting in response to SceneEventController events.
## `event_state` carries power_flicker, array_restore, and storm timing.
## Calling it with an empty state preserves the original alert/storm exposure path.
static func apply_event_feedback(rig: Node3D, alert_pulse: float, storm_intensity: float, signal_lost: bool, time_sec: float, event_state: Dictionary = {}) -> void:
	if rig == null:
		return
	if rig.has_meta("event_alert"):
		var alert_l: OmniLight3D = rig.get_meta("event_alert") as OmniLight3D
		if alert_l != null and is_instance_valid(alert_l):
			alert_l.light_energy = 1.65 * clampf(alert_pulse, 0.0, 1.0)

	var flicker: float = clampf(float(event_state.get("power_flicker", 0.0)), 0.0, 1.0)
	var stutter: float = power_stutter_mul(time_sec, flicker)
	var flash: float = storm_flash_amount(time_sec, storm_intensity)
	var storm_c: float = clampf(storm_intensity, 0.0, 1.0)
	if rig.has_meta("tracked_lights"):
		var tracked: Variant = rig.get_meta("tracked_lights")
		if tracked is Array:
			for item: Variant in tracked as Array:
				if not item is Dictionary:
					continue
				var entry: Dictionary = item as Dictionary
				var light: Light3D = entry.get("light", null) as Light3D
				if light == null or not is_instance_valid(light):
					continue
				var base_e: float = float(entry.get("base", light.light_energy))
				var group: String = str(entry.get("group", ""))
				if group == "overhead":
					light.light_energy = base_e * stutter
				elif group == "key":
					var key_mul: float = lerpf(1.0, 0.58 + 0.2 * clampf(stutter, 0.0, 1.0), flicker)
					light.light_energy = base_e * key_mul + flash * 3.2
				elif group == "rim":
					light.light_energy = base_e * lerpf(1.0, 0.75, flicker) + flash * 0.8

	if rig.has_meta("event_emergency"):
		var emerg: OmniLight3D = rig.get_meta("event_emergency") as OmniLight3D
		if emerg != null and is_instance_valid(emerg):
			if flicker > 0.01:
				emerg.light_energy = lerpf(0.2, 1.85, 1.0 - clampf(stutter, 0.0, 1.0)) * flicker
				emerg.light_color = Color("#ff5a18") if stutter < 0.35 else Color("#ff9a32")
			else:
				emerg.light_energy = 0.0

	if rig.has_meta("event_lightning"):
		var bolt: OmniLight3D = rig.get_meta("event_lightning") as OmniLight3D
		if bolt != null and is_instance_valid(bolt):
			bolt.light_energy = flash * 8.0

	var restore_amt: float = clampf(float(event_state.get("array_restore", 0.0)), 0.0, 1.5)
	var restore_u: float = clampf(float(event_state.get("array_restore_u", 0.0)), 0.0, 1.0)
	var fault: bool = bool(event_state.get("array_fault", signal_lost))
	if restore_amt > 0.1 and restore_u > 0.75:
		rig.set_meta("array_lock_held", true)
	if fault and restore_amt < 0.05:
		rig.set_meta("array_lock_held", false)
	var held_lock: bool = bool(rig.get_meta("array_lock_held", false))
	if rig.has_meta("event_array_lock"):
		var lock_l: OmniLight3D = rig.get_meta("event_array_lock") as OmniLight3D
		if lock_l != null and is_instance_valid(lock_l):
			if restore_amt > 0.05:
				lock_l.light_color = Color("#ff2a18").lerp(Color("#39ffc0"), restore_u)
				lock_l.light_energy = 1.35 * restore_u * minf(restore_amt, 1.0)
			elif held_lock and not fault:
				lock_l.light_color = Color("#39ffc0")
				lock_l.light_energy = 0.55
			elif fault or signal_lost:
				lock_l.light_color = Color("#ff3a22")
				lock_l.light_energy = 0.34
			else:
				lock_l.light_energy = 0.0

	if rig.has_meta("world_env"):
		var world: WorldEnvironment = rig.get_meta("world_env") as WorldEnvironment
		if world != null and world.environment != null:
			var base_exp: float = float(rig.get_meta("base_exposure", 1.0))
			var base_amb: float = float(rig.get_meta("base_ambient_energy", 0.55))
			var amb_mul := 1.0
			var exp_mul := 1.0
			if flicker > 0.01:
				amb_mul = lerpf(1.0, 0.28 + 0.45 * clampf(stutter, 0.0, 1.0), flicker)
				exp_mul = lerpf(1.0, 0.72, flicker * (1.0 - clampf(stutter, 0.0, 1.0)))
			world.environment.tonemap_exposure = base_exp * (1.0 - 0.10 * storm_c) * (1.0 + 0.38 * flash) * exp_mul
			world.environment.ambient_light_energy = base_amb * (1.0 - 0.16 * storm_c) * amb_mul


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
