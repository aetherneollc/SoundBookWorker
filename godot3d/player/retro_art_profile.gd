class_name RetroArtProfile
extends RefCounted

## Reusable PS2 / late-90s cinematic art profiles.
##
## An empty or omitted `art_profile` resolves to a neutral profile: existing
## materials, lighting, and framing stay untouched so Version A (profile off)
## and Version B (profile on) can be compared by toggling one dictionary.
## Pixelation, CRT scanlines, and texture jitter are strictly opt-in.

const PRESET_IDS: Array[String] = [
	"ps2_urban",
	"retro_industrial_scifi",
	"retro_horror",
	"retro_military",
]

const POST_SHADER := """
shader_type canvas_item;

uniform sampler2D screen_texture : hint_screen_texture, filter_linear, repeat_disable;
uniform float contrast = 1.08;
uniform float vignette = 0.30;
uniform float warmth = 0.06;
uniform bool pixelation = false;
uniform bool crt_scanlines = false;
uniform bool texture_jitter = false;
uniform float pixel_size = 3.0;

void fragment() {
	vec2 uv = SCREEN_UV;
	if (pixelation) {
		vec2 step_uv = SCREEN_PIXEL_SIZE * max(pixel_size, 1.0);
		uv = (floor(uv / step_uv) + 0.5) * step_uv;
	}
	if (texture_jitter) {
		float j = sin(TIME * 19.0 + SCREEN_UV.y * 40.0);
		uv += vec2(j, 0.0) * SCREEN_PIXEL_SIZE * 0.65;
	}
	vec3 col = texture(screen_texture, uv).rgb;
	col = (col - vec3(0.5)) * contrast + vec3(0.5);
	col += vec3(warmth * 0.18, warmth * 0.05, -warmth * 0.12);
	vec2 centered = SCREEN_UV - vec2(0.5);
	float vig = smoothstep(0.35, 0.78, length(centered));
	col *= 1.0 - vig * clamp(vignette, 0.0, 1.0);
	if (crt_scanlines) {
		float scan = sin(SCREEN_UV.y / max(SCREEN_PIXEL_SIZE.y, 0.0001));
		col *= 0.9 + 0.1 * scan;
	}
	COLOR = vec4(clamp(col, vec3(0.0), vec3(1.0)), 1.0);
}
"""

const PRESETS: Dictionary = {
	"ps2_urban": {
		"grade_contrast": 1.12,
		"grade_vignette": 0.32,
		"grade_warmth": 0.11,
		"lighting": {
			"ambient_color": "#6e5348",
			"ambient_energy": 0.40,
			"sun_color": "#ffb15a",
			"sun_energy": 1.18,
			"sun_dir": [-0.48, -0.42, 0.76],
			"rim_color": "#d7f6ff",
			"rim_energy": 0.78,
			"rim_dir": [0.55, -0.28, -0.78],
			"lamp_color": "#ffc48a",
			"lamp_energy": 0.62,
			"exposure": 1.06,
			"tonemap_white": 5.4,
			"glow_intensity": 0.42,
			"glow_strength": 0.74,
			"glow_bloom": 0.09,
			"fog_enabled": false,
			"adjustment_enabled": true,
			"adjustment_brightness": 1.02,
			"adjustment_contrast": 1.10,
			"adjustment_saturation": 1.24,
		},
		"palette": {
			"wall": Color("#8a5a3c"),
			"wall_seam": Color("#4e3124"),
			"wall_grime": Color("#241610"),
			"floor": Color("#6e5344"),
			"floor_stain": Color("#1c120e"),
			"metal": Color("#8d8378"),
			"metal_dark": Color("#3a342e"),
			"rivet": Color("#d7c4a4"),
			"hazard": Color("#e37b12"),
			"conduit": Color("#2a2622"),
			"oxidized": Color("#7a4a32"),
		},
	},
	"retro_industrial_scifi": {
		"grade_contrast": 1.08,
		"grade_vignette": 0.26,
		"grade_warmth": 0.0,
		"lighting": {
			"ambient_color": "#7d92a4",
			"ambient_energy": 0.56,
			"sun_color": "#f2e9dc",
			"sun_energy": 0.82,
			"sun_dir": [-0.36, -0.62, 0.70],
			"rim_color": "#b7c9d8",
			"rim_energy": 0.52,
			"rim_dir": [0.42, -0.22, -0.88],
			"lamp_color": "#d5e0ea",
			"lamp_energy": 0.40,
			"exposure": 0.98,
			"tonemap_white": 6.2,
			"glow_intensity": 0.16,
			"glow_strength": 0.42,
			"glow_bloom": 0.04,
			"glow_hdr_threshold": 1.35,
			"fog_enabled": false,
			"adjustment_enabled": true,
			"adjustment_brightness": 1.0,
			"adjustment_contrast": 1.12,
			"adjustment_saturation": 1.06,
		},
		"palette": {
			"wall": Color("#6e7680"),
			"wall_seam": Color("#3a424c"),
			"wall_grime": Color("#2a3036"),
			"floor": Color("#6a655e"),
			"floor_stain": Color("#2e2c28"),
			"metal": Color("#8b8f92"),
			"metal_dark": Color("#3a3e42"),
			"rivet": Color("#b7b3aa"),
			"hazard": Color("#c46a22"),
			"conduit": Color("#2c3136"),
			"oxidized": Color("#7a5340"),
		},
	},
	"retro_horror": {
		"grade_contrast": 1.22,
		"grade_vignette": 0.48,
		"grade_warmth": -0.08,
		"lighting": {
			"ambient_color": "#1a222c",
			"ambient_energy": 0.20,
			"sun_color": "#c5d4e8",
			"sun_energy": 1.28,
			"sun_dir": [-0.62, -0.38, 0.68],
			"rim_color": "#9eb4c8",
			"rim_energy": 0.55,
			"rim_dir": [0.2, -0.18, -0.96],
			"lamp_color": "#9eb0c4",
			"lamp_energy": 0.16,
			"exposure": 0.96,
			"tonemap_white": 4.6,
			"glow_intensity": 0.22,
			"glow_strength": 0.5,
			"glow_bloom": 0.04,
			"fog_enabled": true,
			"fog_color": "#0c1218",
			"fog_density": 0.048,
			"fog_energy": 0.7,
			"adjustment_enabled": true,
			"adjustment_brightness": 0.98,
			"adjustment_contrast": 1.26,
			"adjustment_saturation": 0.74,
		},
		"palette": {
			"wall": Color("#3a4048"),
			"wall_seam": Color("#1a1e24"),
			"wall_grime": Color("#101216"),
			"floor": Color("#2c3036"),
			"floor_stain": Color("#120e0c"),
			"metal": Color("#6a7078"),
			"metal_dark": Color("#1c2128"),
			"rivet": Color("#9aa3ad"),
			"hazard": Color("#8a3a22"),
			"conduit": Color("#161a1e"),
			"oxidized": Color("#4a3028"),
		},
	},
	"retro_military": {
		"grade_contrast": 1.14,
		"grade_vignette": 0.34,
		"grade_warmth": -0.02,
		"lighting": {
			"ambient_color": "#4a5248",
			"ambient_energy": 0.34,
			"sun_color": "#d9d3c0",
			"sun_energy": 0.96,
			"sun_dir": [-0.22, -0.86, 0.46],
			"rim_color": "#e8e4d8",
			"rim_energy": 0.92,
			"rim_dir": [0.06, -0.96, 0.18],
			"lamp_color": "#c8cdbe",
			"lamp_energy": 0.42,
			"exposure": 1.0,
			"tonemap_white": 5.2,
			"glow_intensity": 0.2,
			"glow_strength": 0.48,
			"glow_bloom": 0.04,
			"fog_enabled": true,
			"fog_color": "#2a3128",
			"fog_density": 0.012,
			"adjustment_enabled": true,
			"adjustment_brightness": 1.0,
			"adjustment_contrast": 1.14,
			"adjustment_saturation": 0.68,
		},
		"palette": {
			"wall": Color("#4d5648"),
			"wall_seam": Color("#2a3128"),
			"wall_grime": Color("#161a14"),
			"floor": Color("#3e453c"),
			"floor_stain": Color("#121410"),
			"metal": Color("#8b9084"),
			"metal_dark": Color("#2c332c"),
			"rivet": Color("#c2c6b8"),
			"hazard": Color("#9a6a18"),
			"conduit": Color("#222820"),
			"oxidized": Color("#5a4632"),
		},
	},
}

static var _tex_cache: Dictionary = {}


static func preset_ids() -> Array[String]:
	var ids: Array[String] = []
	ids.append_array(PRESET_IDS)
	return ids


static func resolve(raw: Variant) -> Dictionary:
	var neutral := _neutral()
	if not raw is Dictionary:
		return neutral
	var src: Dictionary = raw as Dictionary
	if src.is_empty():
		return neutral
	var preset_id := str(src.get("preset", src.get("profile", src.get("id", "")))).strip_edges().to_lower()
	if preset_id == "" or preset_id == "none" or preset_id == "neutral" or not PRESETS.has(preset_id):
		return neutral
	var resolved: Dictionary = (PRESETS[preset_id] as Dictionary).duplicate(true)
	for key: Variant in _finish_defaults().keys():
		resolved[key] = _finish_defaults()[key]
	resolved["enabled"] = true
	resolved["preset"] = preset_id
	resolved["id"] = preset_id
	resolved["seed"] = int(src.get("seed", 11))
	_consume_authoring(resolved, src)
	var fx: Dictionary = src.get("post_fx", {}) as Dictionary if src.get("post_fx", {}) is Dictionary else {}
	resolved["pixelation"] = _flag_from(src, fx, "pixelation", false)
	resolved["crt_scanlines"] = _flag_from(src, fx, "crt_scanlines", false)
	resolved["texture_jitter"] = _flag_from(src, fx, "texture_jitter", false)
	resolved["environment_dressing"] = _dressing_enabled(src)
	var dressing_raw: Variant = src.get("environment_dressing", {})
	resolved["environment_dressing_spec"] = (dressing_raw as Dictionary).duplicate(true) if dressing_raw is Dictionary else {}
	# Only an explicit flag wins. Omitting it leaves the worn-panel fallback intact.
	if src.has("modular_scene_assets"):
		resolved["modular_scene_assets"] = bool(src["modular_scene_assets"])
	resolved["post_fx"] = _post_fx_enabled(src)
	return resolved


static func make_texture(kind: String, profile: Dictionary) -> ImageTexture:
	var preset := str(profile.get("preset", "neutral"))
	var seed := int(profile.get("seed", 11))
	var cache_key := "%s|%s|%d" % [preset, kind, seed]
	if _tex_cache.has(cache_key):
		return _tex_cache[cache_key] as ImageTexture
	var palette: Dictionary = profile.get("palette", {}) as Dictionary if profile.get("palette", {}) is Dictionary else {}
	var image := Image.create(256, 256, false, Image.FORMAT_RGB8)
	for y in 256:
		for x in 256:
			image.set_pixel(x, y, _texel(kind, x, y, seed, palette))
	image.generate_mipmaps()
	var texture := ImageTexture.create_from_image(image)
	_tex_cache[cache_key] = texture
	return texture


static func make_material(kind: String, profile: Dictionary, roughness: float = 0.82, metallic: float = 0.12) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_texture = make_texture(kind, profile)
	mat.albedo_color = Color.WHITE
	mat.roughness = roughness
	mat.metallic = metallic
	mat.metallic_specular = 0.16 if metallic > 0.25 else 0.08
	mat.uv1_triplanar = true
	mat.uv1_world_triplanar = true
	if kind == "wall_plate":
		# One tall tile covers most of a bay wall so plates do not checkerboard every 0.4 m.
		mat.uv1_scale = Vector3(0.20, 0.28, 0.20)
	elif kind == "floor_plate":
		mat.uv1_scale = Vector3(0.15, 0.15, 0.15)
	else:
		mat.uv1_scale = Vector3(0.72, 0.72, 0.72)
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
	return mat


static func attach_post_fx(parent: Node, profile: Dictionary) -> CanvasLayer:
	if parent == null or not bool(profile.get("enabled", false)) or not bool(profile.get("post_fx", false)):
		return null
	var layer := CanvasLayer.new()
	layer.name = "RetroPostFX"
	layer.layer = 20
	var rect := ColorRect.new()
	rect.name = "Grade"
	rect.mouse_filter = Control.MOUSE_FILTER_IGNORE
	rect.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	rect.color = Color(1, 1, 1, 1)
	var shader := Shader.new()
	shader.code = POST_SHADER
	var mat := ShaderMaterial.new()
	mat.shader = shader
	mat.set_shader_parameter("contrast", float(profile.get("grade_contrast", 1.08)))
	mat.set_shader_parameter("vignette", float(profile.get("grade_vignette", 0.30)))
	mat.set_shader_parameter("warmth", float(profile.get("grade_warmth", 0.05)))
	mat.set_shader_parameter("pixelation", bool(profile.get("pixelation", false)))
	mat.set_shader_parameter("crt_scanlines", bool(profile.get("crt_scanlines", false)))
	mat.set_shader_parameter("texture_jitter", bool(profile.get("texture_jitter", false)))
	mat.set_shader_parameter("pixel_size", 3.0)
	rect.material = mat
	layer.add_child(rect)
	parent.add_child(layer)
	return layer


static func _neutral() -> Dictionary:
	var out := {
		"enabled": false,
		"preset": "",
		"seed": 11,
		"pixelation": false,
		"crt_scanlines": false,
		"texture_jitter": false,
		"environment_dressing": false,
		"modular_scene_assets": false,
		"post_fx": false,
		"grade_contrast": 1.0,
		"grade_vignette": 0.0,
		"grade_warmth": 0.0,
		"lighting": {},
		"palette": {},
	}
	for key: Variant in _finish_defaults().keys():
		out[key] = _finish_defaults()[key]
	return out


static func _finish_defaults() -> Dictionary:
	return {
		"cloth_roughness_min": 0.78,
		"cloth_roughness_max": 0.92,
		"skin_roughness": 0.91,
		"skin_specular": 0.0,
		"cloth_specular": 0.05,
		"rim_cloth": 0.26,
		"rim_skin": 0.08,
	}


static func _flag(raw: Dictionary, key: String, default_value: bool) -> bool:
	if not raw.has(key):
		return default_value
	return bool(raw[key])


static func _flag_from(src: Dictionary, nested: Dictionary, key: String, default_value: bool) -> bool:
	if nested.has(key):
		return bool(nested[key])
	return _flag(src, key, default_value)


static func _post_fx_enabled(src: Dictionary) -> bool:
	if not src.has("post_fx"):
		return true
	var raw: Variant = src["post_fx"]
	if raw is Dictionary:
		return true
	return bool(raw)


static func _dressing_enabled(src: Dictionary) -> bool:
	if not src.has("environment_dressing"):
		return true
	var raw: Variant = src["environment_dressing"]
	if raw is bool:
		return raw
	if not raw is Dictionary:
		return bool(raw)
	var spec: Dictionary = raw as Dictionary
	if spec.is_empty():
		return false
	for key_v: Variant in spec.keys():
		var value: Variant = spec[key_v]
		if value is float or value is int:
			if float(value) > 0.0:
				return true
		elif bool(value):
			return true
	return false


static func _as_color(value: Variant, fallback: Color) -> Color:
	if value is Color:
		return value as Color
	var text := str(value).strip_edges()
	if text == "":
		return fallback
	return Color(text)


static func _consume_authoring(resolved: Dictionary, src: Dictionary) -> void:
	_consume_palette(resolved, src)
	_consume_lighting(resolved, src)
	_consume_character_material(resolved, src)
	_consume_post_fx(resolved, src)


static func _consume_palette(resolved: Dictionary, src: Dictionary) -> void:
	var incoming: Variant = src.get("palette", {})
	if not incoming is Dictionary or (incoming as Dictionary).is_empty():
		return
	var pal: Dictionary = resolved.get("palette", {}) as Dictionary if resolved.get("palette", {}) is Dictionary else {}
	var spec: Dictionary = incoming as Dictionary
	if spec.has("olive"):
		pal["wall"] = _as_color(spec["olive"], Color("#5c6454"))
	if spec.has("slate"):
		var slate := _as_color(spec["slate"], Color("#3e4650"))
		pal["floor"] = slate
		pal["wall_seam"] = slate.darkened(0.28)
	if spec.has("shadow"):
		var shadow := _as_color(spec["shadow"], Color("#1a1c16"))
		pal["wall_grime"] = shadow
		pal["floor_stain"] = shadow
		pal["metal_dark"] = shadow.lightened(0.12)
	if spec.has("rust"):
		pal["oxidized"] = _as_color(spec["rust"], Color("#6e4a32"))
	if spec.has("sodium"):
		pal["hazard"] = _as_color(spec["sodium"], Color("#d86a12"))
	if spec.has("fluorescent"):
		var cool := _as_color(spec["fluorescent"], Color("#d7e7ea"))
		pal["metal"] = cool.darkened(0.42)
		pal["rivet"] = cool
	if spec.has("conduit"):
		pal["conduit"] = _as_color(spec["conduit"], Color("#232824"))
	for slot: String in ["wall", "wall_seam", "wall_grime", "floor", "floor_stain", "metal", "metal_dark", "rivet", "hazard", "conduit", "oxidized"]:
		if spec.has(slot):
			pal[slot] = _as_color(spec[slot], _pal(pal, slot, Color("#5c6454")))
	resolved["palette"] = pal


static func _consume_lighting(resolved: Dictionary, src: Dictionary) -> void:
	var incoming: Variant = src.get("lighting", {})
	if not incoming is Dictionary or (incoming as Dictionary).is_empty():
		return
	var light: Dictionary = resolved.get("lighting", {}) as Dictionary if resolved.get("lighting", {}) is Dictionary else {}
	var spec: Dictionary = incoming as Dictionary
	var alias := {
		"ambient": "ambient_color",
		"key_color": "sun_color",
		"key_energy": "sun_energy",
		"key_dir": "sun_dir",
		"fill_color": "lamp_color",
		"fill_energy": "lamp_energy",
	}
	for key_v: Variant in spec.keys():
		var key := str(key_v)
		if key == "practicals":
			continue
		var dest := str(alias.get(key, key))
		light[dest] = spec[key_v]
	if spec.has("fog_density") and float(spec["fog_density"]) >= 0.03:
		light["fog_enabled"] = true
	if str(resolved.get("preset", "")) == "retro_industrial_scifi":
		# Cool slate fill, a soft neutral key, and exposure that cannot blow skin out.
		light["ambient_color"] = "#7d92a4"
		light["ambient_energy"] = clampf(maxf(float(light.get("ambient_energy", 0.56)), 0.50), 0.50, 0.68)
		light["sun_color"] = "#f2e9dc"
		light["sun_energy"] = minf(float(light.get("sun_energy", 0.82)), 0.85)
		light["lamp_color"] = "#d5e0ea"
		light["rim_color"] = "#b7c9d8"
		light["rim_energy"] = clampf(float(light.get("rim_energy", 0.52)), 0.35, 0.70)
		light["exposure"] = minf(float(light.get("exposure", 0.98)), 1.0)
		resolved["grade_warmth"] = minf(float(resolved.get("grade_warmth", 0.0)), 0.0)
	resolved["lighting"] = light


static func _consume_character_material(resolved: Dictionary, src: Dictionary) -> void:
	var incoming: Variant = src.get("character_material", {})
	if not incoming is Dictionary or (incoming as Dictionary).is_empty():
		return
	var spec: Dictionary = (incoming as Dictionary).duplicate(true)
	resolved["character_material"] = spec
	var rough := float(spec.get("roughness", 0.78))
	var wear := clampf(float(spec.get("wear", 0.0)), 0.0, 1.0)
	var cloth := clampf(maxf(rough, 0.78) + wear * 0.1, 0.78, 0.94)
	resolved["cloth_roughness_min"] = cloth
	resolved["cloth_roughness_max"] = clampf(cloth + 0.08, cloth, 0.96)
	resolved["skin_roughness"] = clampf(0.86 + wear * 0.06, 0.84, 0.95)
	resolved["skin_specular"] = 0.0
	resolved["cloth_specular"] = clampf(float(spec.get("metallic", 0.04)) * 0.35, 0.0, 0.06)
	resolved["rim_cloth"] = clampf(float(spec.get("rim_strength", 0.26)), 0.05, 0.42)
	resolved["rim_skin"] = clampf(float(spec.get("rim_strength", 0.12)) * 0.28, 0.0, 0.12)


static func _consume_post_fx(resolved: Dictionary, src: Dictionary) -> void:
	var incoming: Variant = src.get("post_fx", {})
	if not incoming is Dictionary or (incoming as Dictionary).is_empty():
		return
	var fx: Dictionary = incoming as Dictionary
	if fx.has("contrast"):
		resolved["grade_contrast"] = clampf(minf(float(fx["contrast"]), 1.06), 0.8, 1.06)
	if fx.has("vignette"):
		resolved["grade_vignette"] = clampf(minf(float(fx["vignette"]), 0.22), 0.0, 0.22)
	if fx.has("warm_tint"):
		var tint := _as_color(fx["warm_tint"], Color("#ffd8a8"))
		resolved["grade_warmth"] = clampf((tint.r - tint.b) * 0.45, -0.2, 0.22)
	if fx.has("saturation"):
		var light: Dictionary = resolved.get("lighting", {}) as Dictionary if resolved.get("lighting", {}) is Dictionary else {}
		light["adjustment_enabled"] = true
		light["adjustment_saturation"] = clampf(float(fx["saturation"]), 0.0, 1.6)
		resolved["lighting"] = light


static func _pal(palette: Dictionary, key: String, fallback: Color) -> Color:
	var value: Variant = palette.get(key, fallback)
	if value is Color:
		return value as Color
	return Color(str(value))


static func _n2(x: int, y: int, seed: int) -> float:
	var n: int = (x * 374761393) ^ (y * 668265263) ^ (seed * 1442695041)
	n = (n ^ (n >> 13)) * 1274126177
	n = n ^ (n >> 16)
	return float(absi(n) % 10000) / 9999.0


static func _texel(kind: String, x: int, y: int, seed: int, palette: Dictionary) -> Color:
	var wall := _pal(palette, "wall", Color("#5c6454"))
	var seam := _pal(palette, "wall_seam", Color("#2c332c"))
	var grime := _pal(palette, "wall_grime", Color("#1a1c16"))
	var floor_c := _pal(palette, "floor", Color("#4a4e48"))
	var stain := _pal(palette, "floor_stain", Color("#14120e"))
	var metal := _pal(palette, "metal", Color("#8a9188"))
	var metal_dark := _pal(palette, "metal_dark", Color("#2e3530"))
	var rivet := _pal(palette, "rivet", Color("#c5c8be"))
	var hazard := _pal(palette, "hazard", Color("#d86a12"))
	var conduit := _pal(palette, "conduit", Color("#232824"))
	var oxidized := _pal(palette, "oxidized", Color("#6e4a32"))
	var grain := _n2(x, y, seed + 3) - 0.5
	match kind:
		"floor_plate":
			var base := floor_c.lerp(metal, 0.22)
			base = base.lightened(grain * 0.035)
			if _plate_gutter(x, seed, 0) or _plate_gutter(y, seed + 5, 1):
				base = base.lerp(seam, 0.88)
			# Worn walkway track: a broad scuffed lane, not a repeating square grid.
			var lane := smoothstep(78.0, 96.0, float(x)) * (1.0 - smoothstep(158.0, 176.0, float(x)))
			if lane > 0.2:
				base = base.lerp(metal_dark, 0.18 * lane + 0.08 * _n2(int(x / 6), int(y / 14), seed + 17))
			var oil := _n2(int(x / 11), int(y / 9), seed + 19)
			if oil > 0.84:
				base = base.lerp(stain, clampf((oil - 0.84) * 3.0, 0.0, 0.7))
			if _n2(int(x / 7), int(y / 3), seed + 4) > 0.93:
				base = base.lightened(0.07)
			var wear := _n2(int(x / 28), int(y / 22), seed + 7)
			if wear > 0.74:
				base = base.lerp(metal, (wear - 0.74) * 0.55)
			return base
		"hazard":
			var stripe := int((x + y * 2) / 18) % 2
			var chev := hazard if stripe == 0 else Color(0.05, 0.045, 0.04)
			if _n2(x, y, seed) > 0.9:
				chev = chev.lerp(stain, 0.45)
			return chev
		"metal":
			var brushed := metal.lerp(metal_dark, 0.5 + 0.5 * sin(float(y) * 0.85))
			brushed = brushed.lightened(grain * 0.05)
			if y % 47 == 0 or x % 83 == 11:
				brushed = brushed.lightened(0.12)
			if _rivet(x, y, 64):
				brushed = rivet
			if _n2(int(x / 6), int(y / 2), seed + 4) > 0.92:
				brushed = brushed.lerp(oxidized, 0.55)
			return brushed
		"conduit":
			var cable := conduit.lerp(metal_dark, 0.35)
			var band := int(y / 18) % 5
			if band == 0 or band == 3:
				cable = metal.darkened(0.25)
			elif band == 2:
				cable = oxidized.darkened(0.35)
			if x % 40 < 2:
				cable = cable.darkened(0.2)
			cable = cable.lightened(grain * 0.03)
			return cable
		"sign":
			var plate := metal_dark
			if x < 10 or y < 10 or x > 245 or y > 245:
				plate = hazard
			elif y > 28 and y < 52 and x > 24 and x < 232:
				plate = hazard.darkened(0.15)
			elif absf(float((x + y) % 28) - 8.0) < 5.0 and y > 70 and y < 200 and x > 40 and x < 216:
				plate = Color(0.95, 0.82, 0.2)
			elif y > 210 and y < 232 and x > 36 and x < 220 and (x / 14) % 2 == 0:
				plate = Color(0.9, 0.9, 0.82)
			return plate
		_:
			var panel := wall.lightened(grain * 0.03)
			if _plate_gutter(x, seed, 0) or _plate_gutter(y, seed + 4, 1):
				panel = seam.lerp(panel, 0.12)
			elif _rivet(x, y, 118) and _n2(int(x / 20), int(y / 20), seed) > 0.4:
				panel = rivet.lerp(seam, 0.35)
			var streak := _n2(int(x / 3), 11, seed + 6)
			if streak > 0.76:
				panel = panel.lerp(grime, clampf((streak - 0.76) * 2.8 * float(y) / 255.0, 0.0, 0.58))
			# Baseboard rust sits in the low-V band. Wall UV scale maps that band to the floor line.
			if y < 30:
				var rust_u := (1.0 - float(y) / 30.0) * (0.28 + 0.72 * _n2(x, 2, seed + 8))
				panel = panel.lerp(oxidized, clampf(rust_u, 0.0, 0.62))
			if _n2(int(x / 17), int(y / 23), seed + 11) > 0.91:
				panel = panel.lerp(oxidized, 0.28)
			return panel


static func _plate_gutter(coord: int, seed: int, axis: int) -> bool:
	var cursor := 0
	var guard := 0
	while cursor < 256 and guard < 16:
		var width := 78 + int(_n2(cursor + axis * 19, axis + 3, seed) * 92.0)
		var end := mini(cursor + width, 256)
		if coord >= cursor and coord < end:
			var on_edge := coord - cursor <= 2 or end - 1 - coord <= 1
			if on_edge and _n2(cursor, axis + 9, seed + 2) > 0.18:
				return true
			return false
		cursor = end
		guard += 1
	return false


static func _rivet(x: int, y: int, spacing: int) -> bool:
	var lx := x % spacing
	var ly := y % spacing
	var near_x := lx <= 6 or lx >= spacing - 7
	var near_y := ly <= 6 or ly >= spacing - 7
	if not (near_x and near_y):
		return false
	var cx := 4 if lx <= 6 else spacing - 5
	var cy := 4 if ly <= 6 else spacing - 5
	var dx := lx - cx
	var dy := ly - cy
	return dx * dx + dy * dy <= 6
