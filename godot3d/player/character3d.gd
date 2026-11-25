extends Node3D

## One humanoid actor loaded at runtime from a glTF/GLB file.
##
## Features:
## - 1:1 direct 65-bone playback when target skeleton matches the animation
##   library (e.g. Quaternius Universal Base Characters + UAL1_Standard.glb),
##   preserving all 40 finger joints with zero retargeting drift.
## - World-space rest-delta retargeting fallback for Mixamo/Meshy/Rigify rigs.
## - Multi-gait personality system (confident, casual, brisk, cautious, urgent,
##   sprint, heavy, feminine) with distinct locomotion clips, posture/sway
##   modulation, and per-clip planted-foot stride speed calibration.
## - Natural 5-finger hand posing (relaxed idle curl + tight prop fist grip)
##   on both right and left hands, plus upper-body carry layering & 2-bone IK.
## - Embedded FacialAnimationController driving real glTF BlendShapes, 8
##   emotions, TTS lip-sync, eye/head gaze, blinking, and listener reactions.
## - Exact per-frame foot grounding (sole y == 0.0) and QC telemetry snapshot.

const FacialControllerScript := preload("res://player/facial_controller.gd")

const SAMPLE_RATE := 30.0
const LAYER_VISIBLE := 1
const LAYER_SELF_HIDDEN := 2
const HIPS_SWAY_LIMIT := 0.08
const COLLAR_RADIUS := 0.088
const ALIGN_SPINE := false
const NOD_DEGREES := 8.0
## Upright bias, in degrees of pitch (negative leans back / lifts the chin),
## spread over the spine chain, then neck and head. Only used for retargeted
## non-matching rigs; ``look.posture`` overrides.
const POSTURE_DEFAULT := {"spine_deg": -4.0, "neck_deg": -3.0, "head_deg": -7.0, "arm_splay_max_deg": 9.0}
const FOOT_GROUND_LIMIT := 0.16
const LOOK_YAW_LIMIT := 28.0
const LOOK_PITCH_LIMIT := 14.0
const LOOK_NECK_SHARE := 0.35
const BLINK_PERIOD_S := 3.4
const FACE_CELLS := {
	"neutral": Vector2(0.0, 0.0),
	"blink": Vector2(0.5, 0.0),
	"talk_mid": Vector2(0.0, 0.5),
	"talk_open": Vector2(0.5, 0.5),
}
const TALK_SEQUENCE := ["talk_mid", "talk_open", "neutral", "talk_mid", "talk_open", "talk_mid", "neutral", "talk_open"]
const TALK_STEPS := [4, 5, 4, 5, 4, 4, 5, 4]
## Elbow bend direction for arm reach, in model space (model faces +Z).
const REACH_POLE_R := Vector3(-0.35, -1.0, -0.3)
const REACH_POLE_L := Vector3(0.35, -1.0, -0.3)

const CLIP_CANDIDATES := {
	"idle": ["Idle_Loop", "Idle", "idle", "Standing_Idle", "Breathing_Idle"],
	"walk": ["Walk_Loop", "Walking", "walking", "Walk", "walk"],
	"walk_formal": ["Walk_Formal_Loop", "Walk_Loop", "Walking", "Walk"],
	"crouch_walk": ["Crouch_Fwd_Loop", "Walk_Loop"],
	"crouch_idle": ["Crouch_Idle_Loop", "Idle_Loop"],
	"jog": ["Jog_Fwd_Loop", "Run_Loop", "Walk_Loop"],
	"sprint": ["Sprint_Loop", "Jog_Fwd_Loop", "Walk_Loop"],
	"talk": ["Idle_Talking_Loop", "Talking", "talk", "Talk"],
	"hold_idle": ["Idle_Torch_Loop", "Pistol_Idle_Loop", "Idle_Loop"],
	"sit_idle": ["Sitting_Idle_Loop", "Idle_Loop"],
	"sit_talk": ["Sitting_Talking_Loop", "Sitting_Idle_Loop", "Idle_Talking_Loop"],
	"kneel_work": ["Fixing_Kneeling", "Crouch_Idle_Loop", "Idle_Loop"],
	"handoff": ["Interact", "PickUp_Table", "Give", "handoff"],
	"point": ["Interact", "Point", "Pointing", "point"],
	"pickup": ["PickUp_Table", "Interact", "pickup"],
	"hit_chest": ["Hit_Chest", "Idle_Loop"],
	"push": ["Push_Loop", "Walk_Loop"],
	"nod": ["Idle_Loop", "Idle", "idle"],
	"grip": ["Sword_Idle", "Pistol_Idle_Loop", "Idle_Torch_Loop"],
}

const LOOPING_CLIPS := {
	"idle": true,
	"walk": true,
	"walk_formal": true,
	"crouch_walk": true,
	"crouch_idle": true,
	"jog": true,
	"sprint": true,
	"talk": true,
	"hold_idle": true,
	"sit_idle": true,
	"sit_talk": true,
	"kneel_work": true,
	"push": true,
	"nod": true,
	"grip": true,
}

const LOCOMOTION_CLIPS := {
	"walk": true,
	"walk_formal": true,
	"crouch_walk": true,
	"jog": true,
	"sprint": true,
	"push": true,
}

## Multi-gait personality presets so different characters & emotional states
## walk with clearly distinguishable posture, cadence, and body rhythm.
const GAIT_PROFILES := {
	"confident": {
		"clip": "walk_formal",
		"speed_mult": 1.02,
		"spine_pitch_deg": -3.5,
		"shoulder_roll_deg": 2.6,
		"stride_freq": 1.85,
		"breath_amp": 1.0,
	},
	"casual": {
		"clip": "walk",
		"speed_mult": 0.96,
		"spine_pitch_deg": 1.5,
		"shoulder_roll_deg": 2.0,
		"stride_freq": 1.75,
		"breath_amp": 1.1,
	},
	"brisk": {
		"clip": "walk_formal",
		"speed_mult": 1.18,
		"spine_pitch_deg": 2.5,
		"shoulder_roll_deg": 3.2,
		"stride_freq": 2.15,
		"breath_amp": 1.0,
	},
	"cautious": {
		"clip": "crouch_walk",
		"speed_mult": 0.88,
		"spine_pitch_deg": 6.0,
		"shoulder_roll_deg": 1.2,
		"stride_freq": 1.45,
		"breath_amp": 1.25,
	},
	"urgent": {
		"clip": "jog",
		"speed_mult": 1.04,
		"spine_pitch_deg": 5.5,
		"shoulder_roll_deg": 3.4,
		"stride_freq": 2.45,
		"breath_amp": 1.35,
	},
	"sprint": {
		"clip": "sprint",
		"speed_mult": 1.05,
		"spine_pitch_deg": 8.0,
		"shoulder_roll_deg": 4.0,
		"stride_freq": 2.85,
		"breath_amp": 1.5,
	},
	"heavy": {
		"clip": "walk",
		"speed_mult": 0.82,
		"spine_pitch_deg": 3.8,
		"shoulder_roll_deg": 4.6,
		"stride_freq": 1.4,
		"breath_amp": 1.2,
	},
	"feminine": {
		"clip": "walk_formal",
		"speed_mult": 1.00,
		"spine_pitch_deg": -2.2,
		"shoulder_roll_deg": 1.8,
		"stride_freq": 1.9,
		"breath_amp": 0.95,
	},
}

const GRIP_FINGERS := ["thumb", "index", "middle", "ring", "pinky"]

const TOON_SHADER := """
shader_type spatial;
render_mode diffuse_lambert, specular_schlick_ggx, cull_back;

uniform sampler2D albedo_tex : source_color, filter_linear_mipmap, repeat_enable;
uniform bool use_texture = false;
uniform vec4 base_color : source_color = vec4(1.0);
uniform bool use_cloth_mask = false;
uniform float rim_strength = 0.18;
uniform float emission_strength = 0.0;

void fragment() {
	vec3 base = base_color.rgb;
	if (use_texture) {
		base *= texture(albedo_tex, UV).rgb;
	}
	if (use_cloth_mask) {
		base = mix(base, COLOR.rgb, COLOR.a);
	}
	ALBEDO = base;
	ROUGHNESS = 0.82;
	SPECULAR = 0.18;
	float rim = pow(1.0 - clamp(dot(NORMAL, VIEW), 0.0, 1.0), 3.0);
	EMISSION = base * (rim * rim_strength + emission_strength);
}

void light() {
	float ndl = dot(NORMAL, LIGHT);
	float band = smoothstep(-0.06, 0.06, ndl) * 0.62 + smoothstep(0.42, 0.52, ndl) * 0.38;
	DIFFUSE_LIGHT += band * ATTENUATION * LIGHT_COLOR / PI;
}
"""

const FACE_TOON_SHADER := """
shader_type spatial;
render_mode diffuse_lambert, specular_disabled, cull_back;

uniform sampler2D albedo_tex : source_color, filter_linear_mipmap, repeat_enable;
uniform sampler2D atlas_tex : source_color, filter_linear_mipmap, repeat_disable;
uniform bool use_atlas = false;
uniform vec2 cell_offset = vec2(0.0);
uniform vec2 cell_scale = vec2(0.5);
uniform vec4 tint : source_color = vec4(1.0);
uniform float ambient_floor = 0.78;
uniform float rim_strength = 0.08;

void fragment() {
	vec3 base;
	if (use_atlas) {
		base = texture(atlas_tex, cell_offset + clamp(UV2, vec2(0.004), vec2(0.996)) * cell_scale).rgb;
	} else {
		base = texture(albedo_tex, UV).rgb;
	}
	base *= tint.rgb;
	ALBEDO = base;
	ROUGHNESS = 0.9;
	float rim = pow(1.0 - clamp(dot(NORMAL, VIEW), 0.0, 1.0), 3.0);
	EMISSION = base * rim * rim_strength;
}

void light() {
	float ndl = dot(NORMAL, LIGHT);
	float lower = smoothstep(-0.3, 0.06, ndl);
	float upper = smoothstep(0.2, 0.62, ndl);
	DIFFUSE_LIGHT += mix(ambient_floor, 1.0, upper) * lower * ATTENUATION * LIGHT_COLOR / PI;
}
"""

static var _library_cache: Dictionary = {}
static var _toon_shader: Shader
static var _face_shader: Shader
static var _re_side_suffix: RegEx
static var _re_side_prefix: RegEx
static var _re_segment: RegEx
static var _re_leading_zero: RegEx

var actor_id := ""
var height_m := 1.7
var natural_walk_speed := 1.0
var source_kind := "cc0_fallback"
var default_gait := "casual"
var current_gait := "casual"
var clip_report: Dictionary = {}
var direct_rig_match := false
var has_native_blendshapes := false
var has_fingers := false
var skinning_ok := true
var skinning_warnings: Array[String] = []

var _model_root: Node3D
var _model: Node3D
var _skeleton: Skeleton3D
var _target: Dictionary = {}
var _player: AnimationPlayer
var _clip_names: Dictionary = {}
var _clip_lengths: Dictionary = {}
var _clip_stride_speeds: Dictionary = {}
var _current_key := ""
var _current_clip := ""
var _last_clip_requested := "idle"
var _last_clip_status := "ok"
var _meshes: Array[MeshInstance3D] = []
var _hand_socket: Node3D
var _left_hand_socket: Node3D
var _chest_socket: Node3D
var _head_attach: BoneAttachment3D
var _grip_pose_r: Dictionary = {}
var _grip_pose_l: Dictionary = {}
var _finger_curl_axes: Dictionary = {}  # bone_idx -> Vector3 local curl axis
var _animated_bones: Dictionary = {}
var _head_bone := -1
var _neck_bone := -1
var _model_scale := 1.0
var _first_person_hidden := false
var _look: Dictionary = {}
var _palette: Dictionary = {}
var holding := false
var holding_left := false
var face_state := "neutral"

var _facial: Node
var _face_materials: Array[ShaderMaterial] = []
var _face_has_atlas := false
var _blink_phase := 0.0
var _posture: Dictionary = {}
var _model_base_y := 0.0
var _ground_offset := 0.0
var _foot_rest: Dictionary = {}
var _side_foot_bones: Dictionary = {"left": [], "right": []}
var _eye_rest := Vector3.INF
var _elapsed_s := 0.0
var _loco_phase := 0.0
var _is_moving := false
var _last_speed_mps := 0.0
var ground_smoothing := 0.65
var stride_report: Dictionary = {}


# ---------------------------------------------------------------- loading


static func load_gltf_scene(path: String) -> Node3D:
	if path == "" or not FileAccess.file_exists(path):
		print("Character3D: model not found: %s" % path)
		return null
	var doc := GLTFDocument.new()
	var state := GLTFState.new()
	var err: Error = doc.append_from_file(path, state)
	if err != OK:
		push_error("Character3D: GLTF load failed for %s: %s" % [path, error_string(err)])
		return null
	var scene: Node = doc.generate_scene(state)
	if scene == null:
		push_error("Character3D: GLTF scene generation failed for %s" % path)
		return null
	var images: Array[Texture2D] = state.get_images()
	for material: Material in state.get_materials():
		var extras: Variant = material.get_meta("extras") if material != null and material.has_meta("extras") else null
		if extras is Dictionary and (extras as Dictionary).has("atlas_image"):
			var index: int = int((extras as Dictionary)["atlas_image"])
			if index >= 0 and index < images.size():
				material.set_meta("face_atlas", images[index])
	if not scene is Node3D:
		var wrapper := Node3D.new()
		wrapper.add_child(scene)
		return wrapper
	return scene as Node3D


static func matte_materials(root: Node, roughness: float = 0.85) -> void:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if not node is MeshInstance3D or (node as MeshInstance3D).mesh == null:
			continue
		var mi := node as MeshInstance3D
		for s in mi.mesh.get_surface_count():
			var material: Material = mi.get_active_material(s)
			if material is BaseMaterial3D:
				var copy := (material as BaseMaterial3D).duplicate() as BaseMaterial3D
				copy.metallic = 0.0
				copy.metallic_specular = 0.3
				copy.roughness = roughness
				mi.set_surface_override_material(s, copy)


static func clear_cache() -> void:
	_library_cache.clear()


static func toon_material(color: Color, texture: Texture2D = null, cloth_mask: bool = false) -> ShaderMaterial:
	if _toon_shader == null:
		_toon_shader = Shader.new()
		_toon_shader.code = TOON_SHADER
	var material := ShaderMaterial.new()
	material.shader = _toon_shader
	material.set_shader_parameter("base_color", color)
	material.set_shader_parameter("use_texture", texture != null)
	if texture != null:
		material.set_shader_parameter("albedo_tex", texture)
	material.set_shader_parameter("use_cloth_mask", cloth_mask)
	return material


static func face_material(texture: Texture2D, atlas: Texture2D, tint: Color) -> ShaderMaterial:
	if _face_shader == null:
		_face_shader = Shader.new()
		_face_shader.code = FACE_TOON_SHADER
	var material := ShaderMaterial.new()
	material.shader = _face_shader
	material.set_shader_parameter("tint", tint)
	if texture != null:
		material.set_shader_parameter("albedo_tex", texture)
	material.set_shader_parameter("use_atlas", atlas != null)
	if atlas != null:
		material.set_shader_parameter("atlas_tex", atlas)
	return material


static func toon_materials(root: Node, tint: Color = Color(1, 1, 1), rim: float = 0.1) -> void:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if not node is MeshInstance3D or (node as MeshInstance3D).mesh == null:
			continue
		var mi := node as MeshInstance3D
		for s in mi.mesh.get_surface_count():
			var source: Material = mi.get_active_material(s)
			var color := tint
			var tex: Texture2D = null
			if source is BaseMaterial3D:
				tex = (source as BaseMaterial3D).albedo_texture
				color = (source as BaseMaterial3D).albedo_color * tint
			var material := toon_material(color, tex)
			material.set_shader_parameter("rim_strength", rim)
			mi.set_surface_override_material(s, material)


func setup(config: Dictionary) -> bool:
	actor_id = str(config.get("id", name))
	height_m = maxf(float(config.get("height_m", 1.7)), 0.5)
	source_kind = str(config.get("source", "cc0_fallback"))
	default_gait = str(config.get("gait", config.get("gait_profile", "casual"))).strip_edges().to_lower()
	if not GAIT_PROFILES.has(default_gait):
		default_gait = "casual"
	current_gait = default_gait

	var look_value: Variant = config.get("look", {})
	_look = look_value as Dictionary if look_value is Dictionary else {}
	_model = load_gltf_scene(str(config.get("model", "")))
	if _model == null:
		return false
	_model_root = Node3D.new()
	_model_root.name = "ModelRoot"
	add_child(_model_root)
	_model.name = "Model"
	_model_root.add_child(_model)
	_skeleton = _find_first(_model, "Skeleton3D") as Skeleton3D
	if _skeleton == null:
		push_error("Character3D: %s has no Skeleton3D" % actor_id)
		return false
	var own_player := _find_first(_model, "AnimationPlayer") as AnimationPlayer
	var own_rig: Dictionary = _extract_rig(_model)
	if own_player != null:
		own_player.stop()
		own_player.get_parent().remove_child(own_player)
		own_player.free()
	_target = own_rig
	_normalize_height()
	_collect_meshes()
	_detect_capabilities()
	_verify_skinning()
	_head_bone = int(_target.human.get("head", -1))
	_neck_bone = int(_target.human.get("neck", -1))
	_blink_phase = float(posmod(absi(hash(actor_id)), 1000)) / 1000.0
	_capture_foot_rest()
	_detect_eye_rest_from_meshes()
	_apply_look()
	_build_sockets()
	_compute_finger_curl_axes()
	_build_animation_player(config, own_rig)
	_calibrate_all_strides()
	_init_facial_controller()
	_apply_rest_pose()
	return true


func _find_first(root: Node, type_name: String) -> Node:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_front()
		if node.is_class(type_name):
			return node
		for child: Node in node.get_children():
			stack.append(child)
	return null


func _normalize_height() -> void:
	var box: AABB = _skinned_bounds()
	var model_height: float = maxf(box.size.y, 0.1)
	_model_scale = height_m / model_height
	_model_root.scale = Vector3.ONE * _model_scale
	_model_root.position = Vector3(0.0, -box.position.y * _model_scale, 0.0)
	_model_base_y = _model_root.position.y


func _skinned_bounds() -> AABB:
	var result := AABB()
	var first := true
	for node: Node in _all_children(_model):
		if node is MeshInstance3D and (node as MeshInstance3D).mesh != null:
			var mi := node as MeshInstance3D
			var box: AABB = _relative_xform(mi, _model) * mi.mesh.get_aabb()
			result = box if first else result.merge(box)
			first = false
	return result


func _all_children(root: Node) -> Array[Node]:
	var out: Array[Node] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		out.append(node)
		for child: Node in node.get_children():
			stack.append(child)
	return out


func _relative_xform(node: Node, ancestor: Node) -> Transform3D:
	var xform := Transform3D.IDENTITY
	var current: Node = node
	while current != null and current != ancestor:
		if current is Node3D:
			xform = (current as Node3D).transform * xform
		current = current.get_parent()
	return xform


func _collect_meshes() -> void:
	_meshes.clear()
	for node: Node in _all_children(_model_root):
		if node is MeshInstance3D:
			_meshes.append(node as MeshInstance3D)


func _detect_capabilities() -> void:
	has_native_blendshapes = false
	for mi: MeshInstance3D in _meshes:
		if mi.mesh != null and mi.mesh.get_blend_shape_count() > 0:
			has_native_blendshapes = true
			break
	var human: Dictionary = _target.human
	var finger_hits := 0
	for side_prefix: String in ["l", "r"]:
		for f: String in GRIP_FINGERS:
			if human.has("%s_%s1" % [side_prefix, f]):
				finger_hits += 1
	has_fingers = finger_hits >= 8


func _verify_skinning() -> void:
	skinning_ok = true
	skinning_warnings.clear()
	var bone_count: int = _skeleton.get_bone_count()
	if bone_count < 15:
		skinning_ok = false
		skinning_warnings.append("skeleton has only %d bones" % bone_count)
	var skinned_meshes := 0
	for mi: MeshInstance3D in _meshes:
		if mi.skin != null and mi.mesh != null:
			skinned_meshes += 1
			if mi.skin.get_bind_count() == 0:
				skinning_ok = false
				skinning_warnings.append("%s has empty skin bind list" % mi.name)
	if skinned_meshes == 0:
		skinning_ok = false
		skinning_warnings.append("no skinned MeshInstance3D found")


func _detect_eye_rest_from_meshes() -> void:
	for mi: MeshInstance3D in _meshes:
		if mi.mesh == null:
			continue
		if str(mi.name).to_lower().contains("eye") and not str(mi.name).to_lower().contains("eyebrow"):
			var box: AABB = _relative_xform(mi, _model) * mi.mesh.get_aabb()
			if box.size.length_squared() > 1e-6:
				_eye_rest = Vector3(box.position.x + box.size.x * 0.5, box.position.y + box.size.y * 0.5, box.end.z)
				return


func _init_facial_controller() -> void:
	_facial = FacialControllerScript.new()
	_facial.name = "FacialController"
	add_child(_facial)
	_facial.call(
		"attach",
		self,
		_skeleton,
		_meshes,
		_face_materials,
		_face_has_atlas,
		_head_bone,
		_neck_bone,
		actor_id,
		_target
	)


# ------------------------------------------------------- humanoid mapping


static func _regex(pattern: String) -> RegEx:
	var re := RegEx.new()
	re.compile(pattern)
	return re


static func canonical_bone(raw: String) -> String:
	if _re_side_suffix == null:
		_re_side_suffix = _regex("[._ \\-](l|r|left|right)$")
		_re_side_prefix = _regex("^(l|r|left|right)[._ \\-]")
		_re_segment = _regex("[._](\\d{3})$")
		_re_leading_zero = _regex("0+(\\d)")
	var n: String = raw.strip_edges().to_lower()
	for prefix: String in ["mixamorig:", "mixamorig_", "mixamorig", "def-", "def_", "org-", "bip001 ", "bip001_", "bip01 ", "bip01_", "armature_", "armature|"]:
		if n.begins_with(prefix):
			n = n.substr(prefix.length())
	if _re_segment.search(n) != null:
		return ""
	for junk: String in ["leaf", "twist", "roll", "_end", ".end", "headtop", "ctrl", "mch", "_ik", "ik_", "pole", "socket", "weapon", "prop"]:
		if n.contains(junk):
			return ""
	var side := ""
	var m: RegExMatch = _re_side_suffix.search(n)
	if m != null:
		side = m.get_string(1).substr(0, 1)
		n = n.substr(0, m.get_start())
	else:
		m = _re_side_prefix.search(n)
		if m != null:
			side = m.get_string(1).substr(0, 1)
			n = n.substr(m.get_end())
		elif n.begins_with("left"):
			side = "l"
			n = n.substr(4)
		elif n.begins_with("right"):
			side = "r"
			n = n.substr(5)
	n = n.replace(".", "").replace("_", "").replace(" ", "").replace("-", "")
	n = _re_leading_zero.sub(n, "$1", true)
	if n.begins_with("hand") and n.length() > 4 and not n.substr(4, 1).is_valid_int():
		n = n.substr(4)
	if n.begins_with("finger"):
		n = n.substr(6)
	n = n.replace("little", "pinky").replace("pinkie", "pinky")
	var base := ""
	match n:
		"hips", "pelvis", "hip":
			base = "hips"
		"neck", "neck1":
			base = "neck"
		"head":
			base = "head"
		"shoulder", "clavicle", "collar":
			base = "shoulder"
		"arm", "upperarm", "uparm":
			base = "upper_arm"
		"forearm", "lowerarm", "elbow":
			base = "lower_arm"
		"hand", "wrist":
			base = "hand"
		"upleg", "thigh", "upperleg":
			base = "upper_leg"
		"leg", "calf", "shin", "lowerleg", "knee":
			base = "lower_leg"
		"foot", "ankle":
			base = "foot"
		"toebase", "toe", "toes", "ball":
			base = "toes"
		_:
			for finger: String in GRIP_FINGERS:
				if n.begins_with(finger):
					var digit: String = n.substr(finger.length())
					if digit in ["1", "2", "3"]:
						base = finger + digit
			if base == "":
				return ""
	if base in ["hips", "neck", "head"]:
		return base
	if side == "":
		return ""
	return "%s_%s" % [side, base]


static func humanoid_map(names: PackedStringArray, parents: PackedInt32Array) -> Dictionary:
	var result := {}
	for i in names.size():
		var key: String = canonical_bone(names[i])
		if key != "" and not result.has(key):
			result[key] = i
	var hips: int = int(result.get("hips", -1))
	var top: int = int(result.get("neck", result.get("head", -1)))
	if hips >= 0 and top >= 0:
		var chain: Array[int] = []
		var bone: int = parents[top]
		while bone >= 0 and bone != hips:
			chain.push_front(bone)
			bone = parents[bone]
		if bone == hips:
			for k in chain.size():
				result["spine%d" % k] = chain[k]
			result["spine_count"] = chain.size()
	return result


static func _bone_pairs(src_human: Dictionary, tgt_human: Dictionary) -> Dictionary:
	var pairs := {}
	for key: Variant in tgt_human.keys():
		var k: String = str(key)
		if k.begins_with("spine") or not src_human.has(k):
			continue
		pairs[int(tgt_human[k])] = int(src_human[k])
	var src_count: int = int(src_human.get("spine_count", 0))
	var tgt_count: int = int(tgt_human.get("spine_count", 0))
	if src_count > 0 and tgt_count > 0:
		for t in tgt_count:
			var s: int = 0 if tgt_count == 1 else int(round(float(t) * float(src_count - 1) / float(tgt_count - 1)))
			pairs[int(tgt_human["spine%d" % t])] = int(src_human["spine%d" % s])
	return pairs


static func _exact_name_pairs(src: Dictionary, tgt: Dictionary) -> Dictionary:
	var src_names: PackedStringArray = src.get("names", PackedStringArray())
	var tgt_names: PackedStringArray = tgt.get("names", PackedStringArray())
	var src_index := {}
	for i in src_names.size():
		src_index[src_names[i].to_lower()] = i
	var pairs := {}
	for t in tgt_names.size():
		var k: String = tgt_names[t].to_lower()
		if src_index.has(k):
			pairs[t] = int(src_index[k])
	return pairs


# ------------------------------------------------------------ rig data


static func _extract_rig(scene: Node) -> Dictionary:
	var skeleton: Skeleton3D = null
	var player: AnimationPlayer = null
	var stack: Array[Node] = [scene]
	while not stack.is_empty():
		var node: Node = stack.pop_front()
		if skeleton == null and node is Skeleton3D:
			skeleton = node as Skeleton3D
		if player == null and node is AnimationPlayer:
			player = node as AnimationPlayer
		for child: Node in node.get_children():
			stack.append(child)
	if skeleton == null:
		return {}
	var names := PackedStringArray()
	var parents := PackedInt32Array()
	var rests: Array[Transform3D] = []
	for i in skeleton.get_bone_count():
		names.append(skeleton.get_bone_name(i))
		parents.append(skeleton.get_bone_parent(i))
		rests.append(skeleton.get_bone_rest(i))
	var skel_xform := Transform3D.IDENTITY
	var current: Node = skeleton
	while current != null and current != scene:
		if current is Node3D:
			skel_xform = (current as Node3D).transform * skel_xform
		current = current.get_parent()
	var order: Array[int] = _bone_order(parents)
	var globals: Array[Transform3D] = []
	globals.resize(names.size())
	for b: int in order:
		var p: int = parents[b]
		globals[b] = rests[b] if p < 0 else globals[p] * rests[b]
	var anims := {}
	var tracks := {}
	if player != null:
		for anim_name: StringName in player.get_animation_list():
			var anim: Animation = player.get_animation(anim_name)
			anims[str(anim_name)] = anim
			var map := {}
			for t in anim.get_track_count():
				var bone_name: String = str(anim.track_get_path(t).get_concatenated_subnames())
				var bone: int = names.find(bone_name)
				if bone < 0:
					continue
				var entry: Dictionary = map.get(bone, {})
				match anim.track_get_type(t):
					Animation.TYPE_ROTATION_3D:
						entry["rot"] = t
					Animation.TYPE_POSITION_3D:
						entry["pos"] = t
				map[bone] = entry
			tracks[str(anim_name)] = map
	var human: Dictionary = humanoid_map(names, parents)
	var hips: int = int(human.get("hips", -1))
	var hips_height: float = 1.0
	if hips >= 0:
		hips_height = maxf((skel_xform * globals[hips]).origin.y, 0.05)
	return {
		"names": names,
		"parents": parents,
		"rests": rests,
		"globals": globals,
		"order": order,
		"skel_xform": skel_xform,
		"anims": anims,
		"tracks": tracks,
		"human": human,
		"hips_height": hips_height,
	}


static func _bone_order(parents: PackedInt32Array) -> Array[int]:
	var order: Array[int] = []
	var placed := {}
	var remaining: int = parents.size()
	while remaining > 0:
		var progressed := false
		for i in parents.size():
			if placed.has(i):
				continue
			var p: int = parents[i]
			if p < 0 or placed.has(p):
				order.append(i)
				placed[i] = true
				remaining -= 1
				progressed = true
		if not progressed:
			break
	return order


static func _load_library(path: String) -> Dictionary:
	if path == "":
		return {}
	if _library_cache.has(path):
		return _library_cache[path]
	var scene: Node3D = load_gltf_scene(path)
	var info := {}
	if scene != null:
		info = _extract_rig(scene)
		scene.free()
	_library_cache[path] = info
	return info


static func _posed_globals(rig: Dictionary, anim: Animation, time: float) -> Array[Transform3D]:
	var rests: Array[Transform3D] = rig.rests
	var parents: PackedInt32Array = rig.parents
	var map: Dictionary = rig.tracks.get(_anim_key(rig, anim), {})
	var out: Array[Transform3D] = []
	out.resize(rests.size())
	for b: int in rig.order:
		var local: Transform3D = rests[b]
		var entry: Dictionary = map.get(b, {})
		if entry.has("rot"):
			local.basis = Basis(anim.rotation_track_interpolate(int(entry["rot"]), time))
		else:
			local.basis = Basis(local.basis.get_rotation_quaternion())
		if entry.has("pos"):
			local.origin = anim.position_track_interpolate(int(entry["pos"]), time)
		var p: int = parents[b]
		out[b] = local if p < 0 else out[p] * local
	return out


static func _anim_key(rig: Dictionary, anim: Animation) -> String:
	for key: Variant in rig.anims.keys():
		if rig.anims[key] == anim:
			return str(key)
	return ""


static func _find_clip(rig: Dictionary, candidates: Array) -> String:
	if rig.is_empty():
		return ""
	var anims: Dictionary = rig.anims
	for candidate: Variant in candidates:
		if anims.has(str(candidate)):
			return str(candidate)
	for candidate: Variant in candidates:
		var wanted: String = str(candidate).to_lower()
		for key: Variant in anims.keys():
			var have: String = str(key).to_lower()
			if have == wanted or have.ends_with("|" + wanted) or have.begins_with(wanted + "_"):
				return str(key)
	return ""


# ------------------------------------------------------------- baking


func _skeleton_path() -> String:
	var parts := PackedStringArray()
	var current: Node = _skeleton
	while current != null and current != self:
		parts.insert(0, str(current.name))
		current = current.get_parent()
	return "/".join(parts)


func _build_animation_player(config: Dictionary, own_rig: Dictionary) -> void:
	var anim_value: Variant = config.get("animation", {})
	var anim_cfg: Dictionary = anim_value as Dictionary if anim_value is Dictionary else {}
	var library: Dictionary = _load_library(str(anim_cfg.get("library", "")))
	var rm_library: Dictionary = _load_library(str(anim_cfg.get("root_motion_library", "")))
	var order_value: Variant = anim_cfg.get("sources", ["library", "own"])
	var source_order: Array = order_value as Array if order_value is Array else ["library", "own"]
	var overrides_value: Variant = anim_cfg.get("clips", {})
	var overrides: Dictionary = overrides_value as Dictionary if overrides_value is Dictionary else {}
	var pinned_value: Variant = anim_cfg.get("clip_sources", {})
	var clip_sources: Dictionary = pinned_value as Dictionary if pinned_value is Dictionary else {}

	_player = AnimationPlayer.new()
	_player.name = "RetargetPlayer"
	add_child(_player)
	_player.root_node = NodePath("..")
	_player.callback_mode_process = AnimationMixer.ANIMATION_CALLBACK_MODE_PROCESS_MANUAL
	var anim_lib := AnimationLibrary.new()
	var skeleton_path: String = _skeleton_path()

	for clip_key: Variant in CLIP_CANDIDATES.keys():
		var clip: String = str(clip_key)
		var candidates: Array = []
		if overrides.has(clip):
			candidates.append(str(overrides[clip]))
		candidates.append_array(CLIP_CANDIDATES[clip])
		var chosen_rig: Dictionary = {}
		var chosen_name := ""
		var chosen_source := ""
		var pinned: Dictionary = clip_sources.get(clip, {}) as Dictionary if clip_sources.get(clip, {}) is Dictionary else {}
		if not pinned.is_empty():
			var pinned_rig: Dictionary = _load_library(str(pinned.get("library", "")))
			var pinned_name: String = _find_clip(pinned_rig, [str(pinned.get("clip", ""))])
			if pinned_name != "":
				chosen_rig = pinned_rig
				chosen_name = pinned_name
				chosen_source = "pinned"
		for source: Variant in source_order:
			if chosen_name != "":
				break
			var rig: Dictionary = library if str(source) == "library" else own_rig
			var found: String = _find_clip(rig, candidates)
			if found != "":
				chosen_rig = rig
				chosen_name = found
				chosen_source = str(source)
				break
		if chosen_name == "":
			continue
		var anim: Animation = chosen_rig.anims[chosen_name]
		var exact_pairs: Dictionary = _exact_name_pairs(chosen_rig, _target)
		var use_direct: bool = exact_pairs.size() >= 45 and exact_pairs.size() >= int(float((_target.names as PackedStringArray).size()) * 0.75)
		var pairs: Dictionary = exact_pairs if use_direct else _bone_pairs(chosen_rig.human, _target.human)
		if pairs.is_empty():
			continue
		if use_direct:
			direct_rig_match = true
		if clip == "grip":
			_grip_pose_r = _bake_grip(chosen_rig, anim, pairs, "r", use_direct)
			_grip_pose_l = _bake_grip(chosen_rig, anim, pairs, "l", use_direct)
			clip_report[clip] = "%s:%s" % [chosen_source, chosen_name]
			continue
		var baked: Animation
		if use_direct:
			baked = _bake_direct(chosen_rig, anim, exact_pairs, LOOPING_CLIPS.has(clip), skeleton_path)
		else:
			baked = _bake(chosen_rig, anim, pairs, LOOPING_CLIPS.has(clip), skeleton_path,
				clip in ["idle", "walk", "walk_formal", "talk", "nod"], chosen_source == "library")
		anim_lib.add_animation(StringName(clip), baked)
		_clip_names[clip] = clip
		_clip_lengths[clip] = baked.length
		clip_report[clip] = "%s%s:%s" % ["direct:" if use_direct else "", chosen_source, chosen_name]
		if clip == "walk":
			natural_walk_speed = _walk_speed(chosen_rig, chosen_name, rm_library)
	_player.add_animation_library("", anim_lib)


## Direct 1:1 skeleton bake for matching rigs (Quaternius 65-bone + UAL1_Standard):
## preserves all 65 bones including all 40 finger joints with zero retarget drift,
## while stripping horizontal root travel so path locomotion drives XZ cleanly.
func _bake_direct(src: Dictionary, anim: Animation, exact_pairs: Dictionary, loop: bool, skeleton_path: String) -> Animation:
	var out := Animation.new()
	out.length = maxf(anim.length, 1.0 / SAMPLE_RATE)
	out.loop_mode = Animation.LOOP_LINEAR if loop else Animation.LOOP_NONE
	var src_map: Dictionary = src.tracks.get(_anim_key(src, anim), {})
	var tgt_names: PackedStringArray = _target.names
	var tgt_rests: Array[Transform3D] = _target.rests
	var src_rests: Array[Transform3D] = src.rests
	var tgt_hips: int = int(_target.human.get("hips", -1))
	var src_hips: int = int(src.human.get("hips", -1))

	var rot_tracks := {}
	for t_idx_v: Variant in exact_pairs.keys():
		var t_idx: int = int(t_idx_v)
		var s_idx: int = int(exact_pairs[t_idx])
		if not src_map.has(s_idx):
			continue
		var entry: Dictionary = src_map[s_idx]
		if entry.has("rot"):
			var tr: int = out.add_track(Animation.TYPE_ROTATION_3D)
			out.track_set_path(tr, NodePath("%s:%s" % [skeleton_path, tgt_names[t_idx]]))
			rot_tracks[t_idx] = int(entry["rot"])
			_animated_bones[t_idx] = true

	var pos_track := -1
	var src_hips_pos_track := -1
	if tgt_hips >= 0 and src_hips >= 0 and src_map.has(src_hips):
		var h_entry: Dictionary = src_map[src_hips]
		if h_entry.has("pos"):
			pos_track = out.add_track(Animation.TYPE_POSITION_3D)
			out.track_set_path(pos_track, NodePath("%s:%s" % [skeleton_path, tgt_names[tgt_hips]]))
			src_hips_pos_track = int(h_entry["pos"])

	var src_basis: Basis = (src.skel_xform as Transform3D).basis
	var tgt_basis_inv: Basis = (_target.skel_xform as Transform3D).basis.inverse()
	var ratio: float = float(_target.hips_height) / maxf(float(src.hips_height), 0.01)
	var delta_start := Vector3.ZERO
	var delta_end := Vector3.ZERO
	if src_hips_pos_track >= 0:
		var p0: Vector3 = anim.position_track_interpolate(src_hips_pos_track, 0.0)
		var p1: Vector3 = anim.position_track_interpolate(src_hips_pos_track, anim.length)
		delta_start = src_basis * (p0 - src_rests[src_hips].origin)
		delta_end = src_basis * (p1 - src_rests[src_hips].origin)

	var steps: int = maxi(1, ceili(anim.length * SAMPLE_RATE))
	var track_idx_by_bone := {}
	for tr_i in out.get_track_count():
		if out.track_get_type(tr_i) == Animation.TYPE_ROTATION_3D:
			var bname: String = str(out.track_get_path(tr_i).get_concatenated_subnames())
			track_idx_by_bone[tgt_names.find(bname)] = tr_i

	for k in steps + 1:
		var time: float = minf(anim.length, float(k) / SAMPLE_RATE)
		for t_idx_v: Variant in rot_tracks.keys():
			var t_idx: int = int(t_idx_v)
			var s_tr: int = int(rot_tracks[t_idx])
			var q: Quaternion = anim.rotation_track_interpolate(s_tr, time).normalized()
			out.rotation_track_insert_key(int(track_idx_by_bone[t_idx]), time, q)
		if pos_track >= 0 and src_hips_pos_track >= 0:
			var sp: Vector3 = anim.position_track_interpolate(src_hips_pos_track, time)
			var delta: Vector3 = src_basis * (sp - src_rests[src_hips].origin)
			var trend: Vector3 = delta_start
			if loop and anim.length > 0.0:
				trend = delta_start.lerp(delta_end, time / anim.length)
			var sway := Vector2(delta.x - trend.x, delta.z - trend.z).limit_length(HIPS_SWAY_LIMIT)
			var locked := Vector3(sway.x, delta.y, sway.y) * ratio
			var local_pos: Vector3 = tgt_rests[tgt_hips].origin + tgt_basis_inv * locked
			out.position_track_insert_key(pos_track, time, local_pos)
	return out


func _bake(src: Dictionary, anim: Animation, pairs: Dictionary, loop: bool, skeleton_path: String, fix_arms: bool = true, fix_posture: bool = true) -> Animation:
	var out := Animation.new()
	out.length = maxf(anim.length, 1.0 / SAMPLE_RATE)
	out.loop_mode = Animation.LOOP_LINEAR if loop else Animation.LOOP_NONE
	var tgt_names: PackedStringArray = _target.names
	var tgt_parents: PackedInt32Array = _target.parents
	var tgt_rests: Array[Transform3D] = _target.rests
	var tgt_globals: Array[Transform3D] = _target.globals
	var src_globals_rest: Array[Transform3D] = src.globals
	var s_src: Quaternion = (src.skel_xform as Transform3D).basis.get_rotation_quaternion()
	var s_tgt: Quaternion = (_target.skel_xform as Transform3D).basis.get_rotation_quaternion()
	var reference: Array[Quaternion] = _reference_rotations(src, pairs)
	var rot_tracks := {}
	for t_idx: Variant in pairs.keys():
		var track: int = out.add_track(Animation.TYPE_ROTATION_3D)
		out.track_set_path(track, NodePath("%s:%s" % [skeleton_path, tgt_names[int(t_idx)]]))
		rot_tracks[int(t_idx)] = track
		_animated_bones[int(t_idx)] = true
	var tgt_hips: int = int(_target.human.get("hips", -1))
	var src_hips: int = int(src.human.get("hips", -1))
	var pos_track := -1
	if tgt_hips >= 0 and src_hips >= 0:
		pos_track = out.add_track(Animation.TYPE_POSITION_3D)
		out.track_set_path(pos_track, NodePath("%s:%s" % [skeleton_path, tgt_names[tgt_hips]]))
	var ratio: float = float(_target.hips_height) / maxf(float(src.hips_height), 0.01)
	var src_basis: Basis = (src.skel_xform as Transform3D).basis
	var tgt_basis_inv: Basis = (_target.skel_xform as Transform3D).basis.inverse()
	var delta_start := Vector3.ZERO
	var delta_end := Vector3.ZERO
	if src_hips >= 0:
		var g0: Array[Transform3D] = _posed_globals(src, anim, 0.0)
		var g1: Array[Transform3D] = _posed_globals(src, anim, anim.length)
		delta_start = src_basis * (g0[src_hips].origin - src_globals_rest[src_hips].origin)
		delta_end = src_basis * (g1[src_hips].origin - src_globals_rest[src_hips].origin)

	var steps: int = maxi(1, ceili(anim.length * SAMPLE_RATE))
	var tgt_count: int = tgt_names.size()
	var fixes: Dictionary = _posture_fixes()
	var bias: Dictionary = fixes.bias if fix_posture else {}
	var arm_root: Dictionary = fixes.arm_root
	for k in steps + 1:
		var time: float = minf(anim.length, float(k) / SAMPLE_RATE)
		var src_pose: Array[Transform3D] = _posed_globals(src, anim, time)
		var g_rot: Array[Quaternion] = []
		g_rot.resize(tgt_count)
		var g_full: Array[Transform3D] = []
		g_full.resize(tgt_count)
		var arm_fix := {}
		for b: int in _target.order:
			var p: int = tgt_parents[b]
			var parent_rot: Quaternion = Quaternion.IDENTITY if p < 0 else g_rot[p]
			var local_rot: Quaternion
			if pairs.has(b):
				var s_b: int = int(pairs[b])
				var ws: Quaternion = s_src * src_pose[s_b].basis.get_rotation_quaternion()
				var ws0: Quaternion = reference[s_b]
				var wt0: Quaternion = s_tgt * tgt_globals[b].basis.get_rotation_quaternion()
				var global_rot: Quaternion = s_tgt.inverse() * ((ws * ws0.inverse()) * wt0)
				if bias.has(b):
					global_rot = (bias[b] as Quaternion) * global_rot
				if fix_arms and arm_root.has(b):
					var upper: int = int(arm_root[b])
					if upper == b:
						arm_fix[b] = _arm_splay_fix(b, global_rot, fixes)
					global_rot = (arm_fix.get(upper, Quaternion.IDENTITY) as Quaternion) * global_rot
					if (fixes.hand_axis as Dictionary).has(b):
						global_rot = _wrist_fix(b, global_rot, parent_rot, fixes)
				local_rot = (parent_rot.inverse() * global_rot).normalized()
				out.rotation_track_insert_key(int(rot_tracks[b]), time, local_rot)
			else:
				local_rot = tgt_rests[b].basis.get_rotation_quaternion()
			g_rot[b] = (parent_rot * local_rot).normalized()
			var local_full := Transform3D(Basis(local_rot), tgt_rests[b].origin)
			g_full[b] = local_full if p < 0 else g_full[p] * local_full
		if pos_track >= 0:
			var delta: Vector3 = src_basis * (src_pose[src_hips].origin - src_globals_rest[src_hips].origin)
			var trend: Vector3 = delta_start
			if loop and anim.length > 0.0:
				trend = delta_start.lerp(delta_end, time / anim.length)
			var sway := Vector2(delta.x - trend.x, delta.z - trend.z).limit_length(HIPS_SWAY_LIMIT)
			var locked := Vector3(sway.x, delta.y, sway.y) * ratio
			var target_pos: Vector3 = tgt_globals[tgt_hips].origin + tgt_basis_inv * locked
			var hp: int = tgt_parents[tgt_hips]
			var local_pos: Vector3 = target_pos if hp < 0 else g_full[hp].affine_inverse() * target_pos
			out.position_track_insert_key(pos_track, time, local_pos)
	return out


func _reference_rotations(src: Dictionary, pairs: Dictionary) -> Array[Quaternion]:
	var src_parents: PackedInt32Array = src.parents
	var src_rests: Array[Transform3D] = src.rests
	var s_basis := (src.skel_xform as Transform3D).basis.orthonormalized()
	var s_rot: Quaternion = s_basis.get_rotation_quaternion()
	var t_xform: Transform3D = _target.skel_xform
	var src_to_tgt := {}
	for t_idx: Variant in pairs.keys():
		src_to_tgt[int(pairs[t_idx])] = int(t_idx)
	var src_key := {}
	for key: Variant in (src.human as Dictionary).keys():
		if str(key) != "spine_count":
			src_key[int(src.human[key])] = str(key)
	var next_key := {
		"upper_arm": "lower_arm", "lower_arm": "hand", "shoulder": "upper_arm",
		"upper_leg": "lower_leg", "lower_leg": "foot", "foot": "toes",
	}
	var spine_count: int = int((src.human as Dictionary).get("spine_count", 0))
	var count: int = src_parents.size()
	var world_rot: Array[Quaternion] = []
	world_rot.resize(count)
	var world_pos: Array[Vector3] = []
	world_pos.resize(count)
	for b: int in src.order:
		var p: int = src_parents[b]
		var local_rot: Quaternion = src_rests[b].basis.get_rotation_quaternion()
		var parent_rot: Quaternion = s_rot if p < 0 else world_rot[p]
		var parent_pos: Vector3 = Vector3.ZERO if p < 0 else world_pos[p]
		world_pos[b] = parent_pos + (Basis(parent_rot) * src_rests[b].origin if p >= 0 else s_basis * src_rests[b].origin)
		world_rot[b] = (parent_rot * local_rot).normalized()
		if not src_to_tgt.has(b) or not src_key.has(b):
			continue
		var key: String = src_key[b]
		var child_key := ""
		if key.begins_with("spine") or key == "neck":
			if not ALIGN_SPINE:
				continue
			var k: int = key.substr(5).to_int() if key != "neck" else spine_count
			child_key = ("spine%d" % (k + 1) if k + 1 < spine_count else "neck") if key != "neck" else "head"
		else:
			var parts: PackedStringArray = key.split("_", true, 1)
			if parts.size() == 2 and next_key.has(parts[1]):
				child_key = "%s_%s" % [parts[0], next_key[parts[1]]]
		if child_key == "" or not (src.human as Dictionary).has(child_key) or not (_target.human as Dictionary).has(child_key):
			continue
		var src_child: int = int(src.human[child_key])
		if src_parents[src_child] != b and not src_key.has(src_child):
			continue
		var child_pos: Vector3 = world_pos[b] + Basis(world_rot[b]) * _chain_offset(src, b, src_child)
		var t_b: int = int(src_to_tgt[b])
		var t_c: int = int(_target.human[child_key])
		var t_dir: Vector3 = (t_xform * (_target.globals[t_c] as Transform3D).origin) - (t_xform * (_target.globals[t_b] as Transform3D).origin)
		var s_dir: Vector3 = child_pos - world_pos[b]
		if t_dir.length_squared() < 1e-8 or s_dir.length_squared() < 1e-8:
			continue
		var swing := Quaternion(s_dir.normalized(), t_dir.normalized())
		world_rot[b] = (swing * world_rot[b]).normalized()
	return world_rot


func _posture_fixes() -> Dictionary:
	if _posture.has("bias"):
		return _posture
	var cfg: Dictionary = POSTURE_DEFAULT.duplicate()
	var override_value: Variant = _look.get("posture", {})
	if override_value is Dictionary:
		for key: Variant in (override_value as Dictionary).keys():
			cfg[str(key)] = float((override_value as Dictionary)[key])
	var human: Dictionary = _target.human
	var parents: PackedInt32Array = _target.parents
	var skel_basis: Basis = (_target.skel_xform as Transform3D).basis.orthonormalized()
	var lateral: Vector3 = (skel_basis.inverse() * Vector3.RIGHT).normalized()
	var bias := {}
	var count: int = int(human.get("spine_count", 0))
	var total := 0.0
	for i in count:
		total = float(cfg.spine_deg) * float(i + 1) / float(count)
		bias[int(human["spine%d" % i])] = Quaternion(lateral, deg_to_rad(total))
	if count == 0:
		total = float(cfg.spine_deg)
	for key: String in ["neck", "head"]:
		total += float(cfg["%s_deg" % key])
		if human.has(key):
			bias[int(human[key])] = Quaternion(lateral, deg_to_rad(total))
	var arm_root := {}
	var side := {}
	var child_offset := {}
	for prefix: String in ["l", "r"]:
		var upper: int = int(human.get("%s_upper_arm" % prefix, -1))
		var lower: int = int(human.get("%s_lower_arm" % prefix, -1))
		if upper < 0 or lower < 0:
			continue
		side[upper] = 1.0 if prefix == "l" else -1.0
		child_offset[upper] = _chain_offset(_target, upper, lower)
		for b in parents.size():
			var walk: int = b
			while walk >= 0 and walk != upper:
				walk = parents[walk]
			if walk == upper:
				arm_root[b] = upper
	var hand_axis: Dictionary = _measure_hand_axes()
	var hand_offset := {}
	for hand: Variant in hand_axis.keys():
		var lower_arm: int = parents[int(hand)]
		hand_offset[hand] = _chain_offset(_target, lower_arm, int(hand))
	_posture = {
		"bias": bias,
		"arm_root": arm_root,
		"side": side,
		"child_offset": child_offset,
		"skel_basis": skel_basis,
		"splay_max": deg_to_rad(float(cfg.arm_splay_max_deg)),
		"hand_axis": hand_axis,
		"hand_offset": hand_offset,
		"config": cfg,
	}
	return _posture


func _measure_hand_axes() -> Dictionary:
	var result := {}
	var hands := {}
	for prefix: String in ["l", "r"]:
		var hand: int = int(_target.human.get("%s_hand" % prefix, -1))
		if hand >= 0:
			hands[hand] = []
	if hands.is_empty():
		return result
	var parents: PackedInt32Array = _target.parents
	for mi: MeshInstance3D in _meshes:
		if mi.skin == null or mi.mesh == null:
			continue
		var bind_bone := PackedInt32Array()
		for i in mi.skin.get_bind_count():
			var bone: int = mi.skin.get_bind_bone(i)
			if bone < 0:
				bone = _skeleton.find_bone(str(mi.skin.get_bind_name(i)))
			while bone >= 0 and not hands.has(bone):
				bone = parents[bone]
			bind_bone.append(bone)
		for s in mi.mesh.get_surface_count():
			var arrays: Array = mi.mesh.surface_get_arrays(s)
			var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
			if arrays[Mesh.ARRAY_BONES] == null or verts.is_empty():
				continue
			var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
			var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
			var per: int = floori(float(bones.size()) / float(verts.size()))
			for v in verts.size():
				var w := 0.0
				var owner := -1
				for j in per:
					var hb: int = bind_bone[bones[v * per + j]] if bones[v * per + j] < bind_bone.size() else -1
					if hb >= 0:
						owner = hb
						w += weights[v * per + j]
				if owner >= 0 and w > 0.6:
					(hands[owner] as Array).append(verts[v])
	var skel_inv: Transform3D = (_target.skel_xform as Transform3D).affine_inverse()
	for hand: Variant in hands.keys():
		var points: Array = hands[hand]
		if points.size() < 30:
			continue
		var to_local: Transform3D = (_target.globals[int(hand)] as Transform3D).affine_inverse() * skel_inv
		var centroid := Vector3.ZERO
		var local: Array[Vector3] = []
		for p: Vector3 in points:
			var q: Vector3 = to_local * p
			local.append(q)
			centroid += q
		centroid /= float(local.size())
		var cov := Basis(Vector3.ZERO, Vector3.ZERO, Vector3.ZERO)
		for q: Vector3 in local:
			var d: Vector3 = q - centroid
			cov.x += d * d.x
			cov.y += d * d.y
			cov.z += d * d.z
		var axis: Vector3 = centroid.normalized() if centroid.length_squared() > 1e-10 else Vector3.UP
		for _i in 40:
			axis = (cov * axis).normalized()
		var along := 0.0
		var across := 0.0
		for q: Vector3 in local:
			var d2: Vector3 = q - centroid
			var a: float = d2.dot(axis)
			along += a * a
			across += (d2 - axis * a).length_squared() * 0.5
		if along < across * 1.7:
			continue
		if axis.dot(centroid) < 0.0:
			axis = -axis
		result[int(hand)] = axis
	return result


func _wrist_fix(hand: int, global_rot: Quaternion, parent_rot: Quaternion, fixes: Dictionary) -> Quaternion:
	var axis: Vector3 = Basis(global_rot) * (fixes.hand_axis[hand] as Vector3)
	var fore: Vector3 = Basis(parent_rot) * (fixes.hand_offset[hand] as Vector3)
	if fore.length_squared() < 1e-10:
		return global_rot
	var angle: float = axis.angle_to(fore)
	var keep: float = deg_to_rad(12.0)
	if angle <= keep:
		return global_rot
	var turn_axis: Vector3 = axis.cross(fore)
	if turn_axis.length_squared() < 1e-10:
		return global_rot
	return (Quaternion(turn_axis.normalized(), (angle - keep) * 0.85) * global_rot).normalized()


func _arm_splay_fix(upper: int, global_rot: Quaternion, fixes: Dictionary) -> Quaternion:
	var skel_basis: Basis = fixes.skel_basis
	var offset: Vector3 = fixes.child_offset[upper]
	if offset.length_squared() < 1e-10:
		return Quaternion.IDENTITY
	var d: Vector3 = (skel_basis * (Basis(global_rot) * offset)).normalized()
	var side: float = float(fixes.side[upper])
	var limit: float = float(fixes.splay_max)
	if asin(clampf(side * d.x, -1.0, 1.0)) <= limit:
		return Quaternion.IDENTITY
	var yz := Vector2(d.y, d.z)
	yz = yz.normalized() if yz.length_squared() > 1e-8 else Vector2(-1.0, 0.0)
	var wanted := Vector3(side * sin(limit), yz.x * cos(limit), yz.y * cos(limit))
	var swing := Basis(Quaternion(d, wanted.normalized()))
	return Quaternion(skel_basis.inverse() * swing * skel_basis).normalized()


static func _chain_offset(rig: Dictionary, bone: int, child: int) -> Vector3:
	var parents: PackedInt32Array = rig.parents
	var rests: Array[Transform3D] = rig.rests
	var xform := Transform3D.IDENTITY
	var walk: int = child
	while walk >= 0 and walk != bone:
		xform = Transform3D(Basis(rests[walk].basis.get_rotation_quaternion()), rests[walk].origin) * xform
		walk = parents[walk]
	return xform.origin


func _bake_grip(src: Dictionary, anim: Animation, pairs: Dictionary, side: String, use_direct: bool) -> Dictionary:
	var result := {}
	var human: Dictionary = _target.human
	var hand: int = int(human.get("%s_hand" % side, -1))
	if hand < 0 or not pairs.has(hand):
		return result
	var src_map: Dictionary = src.tracks.get(_anim_key(src, anim), {})
	var src_pose: Array[Transform3D] = _posed_globals(src, anim, 0.0)
	var s_src: Quaternion = (src.skel_xform as Transform3D).basis.get_rotation_quaternion()
	var s_tgt: Quaternion = (_target.skel_xform as Transform3D).basis.get_rotation_quaternion()
	for finger: String in GRIP_FINGERS:
		for digit in [1, 2, 3]:
			var key := "%s_%s%d" % [side, finger, digit]
			if not human.has(key):
				continue
			var b: int = int(human[key])
			if not pairs.has(b):
				continue
			var sb: int = int(pairs[b])
			if use_direct and src_map.has(sb) and (src_map[sb] as Dictionary).has("rot"):
				result[b] = anim.rotation_track_interpolate(int((src_map[sb] as Dictionary)["rot"]), 0.0).normalized()
				continue
			var p: int = (_target.parents as PackedInt32Array)[b]
			var sp: int = (src.parents as PackedInt32Array)[sb]
			if sp < 0 or p < 0:
				continue
			var ws: Quaternion = s_src * src_pose[sb].basis.get_rotation_quaternion()
			var wsp: Quaternion = s_src * src_pose[sp].basis.get_rotation_quaternion()
			var ws0: Quaternion = s_src * (src.globals[sb] as Transform3D).basis.get_rotation_quaternion()
			var wsp0: Quaternion = s_src * (src.globals[sp] as Transform3D).basis.get_rotation_quaternion()
			var wt0: Quaternion = s_tgt * (_target.globals[b] as Transform3D).basis.get_rotation_quaternion()
			var wtp0: Quaternion = s_tgt * (_target.globals[p] as Transform3D).basis.get_rotation_quaternion()
			var parent_delta: Quaternion = wsp * wsp0.inverse()
			var child_delta: Quaternion = ws * ws0.inverse()
			var wt: Quaternion = child_delta * wt0
			var wtp: Quaternion = parent_delta * wtp0
			result[b] = (wtp.inverse() * wt).normalized()
	return result


## Computes the exact bone-local curl axis (curving toward palm) for every
## finger joint on both hands, used for relaxed idle curl & tight prop grip.
func _compute_finger_curl_axes() -> void:
	_finger_curl_axes.clear()
	var human: Dictionary = _target.human
	for side: String in ["l", "r"]:
		var hand: int = int(human.get("%s_hand" % side, -1))
		if hand < 0 or not human.has("%s_middle1" % side):
			continue
		var hand_global: Transform3D = _target.globals[hand]
		var inv_hand: Transform3D = hand_global.affine_inverse()
		var idx_p: Vector3 = inv_hand * (_target.globals[int(human.get("%s_index1" % side, hand))] as Transform3D).origin
		var pnk_p: Vector3 = inv_hand * (_target.globals[int(human.get("%s_pinky1" % side, hand))] as Transform3D).origin
		var mid_p: Vector3 = inv_hand * (_target.globals[int(human["%s_middle1" % side])] as Transform3D).origin
		var thm_p: Vector3 = inv_hand * (_target.globals[int(human.get("%s_thumb1" % side, hand))] as Transform3D).origin
		var finger_dir: Vector3 = mid_p.normalized()
		var across: Vector3 = (idx_p - pnk_p)
		across = (across - finger_dir * across.dot(finger_dir)).normalized()
		if across.length_squared() < 0.25:
			continue
		var palm: Vector3 = finger_dir.cross(across).normalized()
		if palm.dot(thm_p) < 0.0:
			palm = -palm
		# Curl axis in hand space: rotating +angle swings finger_dir toward palm.
		var curl_hand: Vector3 = finger_dir.cross(palm).normalized()
		var curl_skel: Vector3 = (hand_global.basis.orthonormalized() * curl_hand).normalized()
		var thumb_curl_skel: Vector3 = (hand_global.basis.orthonormalized() * (palm.cross(across).normalized())).normalized()
		for finger: String in GRIP_FINGERS:
			for digit in [1, 2, 3]:
				var key := "%s_%s%d" % [side, finger, digit]
				if not human.has(key):
					continue
				var b: int = int(human[key])
				var b_basis: Basis = (_target.globals[b] as Transform3D).basis.orthonormalized()
				var world_axis: Vector3 = thumb_curl_skel if finger == "thumb" else curl_skel
				var local_axis: Vector3 = (b_basis.inverse() * world_axis).normalized()
				if local_axis.length_squared() > 0.25:
					_finger_curl_axes[b] = local_axis


func _walk_speed(rig: Dictionary, clip_name: String, rm_library: Dictionary) -> float:
	var src_speed := 0.0
	var reference: Dictionary = rig
	var source_clip: String = clip_name
	if not rm_library.is_empty() and rm_library.anims.has(clip_name):
		reference = rm_library
	var anim: Animation = reference.anims.get(source_clip)
	var hips: int = int(reference.human.get("hips", -1))
	if anim != null and hips >= 0 and anim.length > 0.0:
		var a: Array[Transform3D] = _posed_globals(reference, anim, 0.0)
		var b: Array[Transform3D] = _posed_globals(reference, anim, anim.length)
		var travel: Vector3 = (reference.skel_xform as Transform3D).basis * (b[hips].origin - a[hips].origin)
		var horizontal: float = Vector2(travel.x, travel.z).length()
		if horizontal > 0.2 * anim.length:
			src_speed = horizontal / anim.length
	if src_speed <= 0.0:
		src_speed = float(reference.hips_height) * 1.0
	var ratio: float = float(_target.hips_height) / maxf(float(reference.hips_height), 0.01)
	return src_speed * ratio * _model_scale


func _calibrate_all_strides() -> void:
	_clip_stride_speeds.clear()
	for clip_name: String in ["walk", "walk_formal", "crouch_walk", "jog", "sprint", "push"]:
		if not _clip_names.has(clip_name):
			continue
		var s: float = _measure_stride_speed(clip_name)
		if s > 0.2:
			_clip_stride_speeds[clip_name] = s
			if clip_name == "walk":
				stride_report["fallback_speed"] = snappedf(natural_walk_speed, 0.001)
				natural_walk_speed = s
	if not _clip_stride_speeds.has("walk"):
		_clip_stride_speeds["walk"] = maxf(natural_walk_speed, 0.85)


func _apply_rest_pose() -> void:
	if _player != null and _clip_names.has("idle"):
		_player.play("idle", 0.0)
		_player.advance(0.0)
		_current_clip = "idle"
		_current_key = "idle"


# ----------------------------------------------------------- gait & facial API


func set_gait_profile(gait: String) -> void:
	var key: String = gait.strip_edges().to_lower()
	if GAIT_PROFILES.has(key):
		current_gait = key
	elif key == "":
		current_gait = default_gait


func get_gait_profile() -> String:
	return current_gait


func resolve_locomotion_clip(gait: String = "") -> String:
	var g: String = gait.strip_edges().to_lower() if gait != "" else current_gait
	var prof: Dictionary = GAIT_PROFILES.get(g, GAIT_PROFILES["casual"])
	var wanted: String = str(prof.get("clip", "walk"))
	if _clip_names.has(wanted):
		return wanted
	if _clip_names.has("walk"):
		return "walk"
	return "idle"


func stride_speed_for(clip: String) -> float:
	if _clip_stride_speeds.has(clip):
		return maxf(float(_clip_stride_speeds[clip]), 0.25)
	return maxf(natural_walk_speed, 0.85)


func set_hand_holding(side: String, is_holding: bool) -> void:
	if side == "left":
		holding_left = is_holding
	else:
		holding = is_holding


func get_hand_socket() -> Node3D:
	return _hand_socket


func get_left_hand_socket() -> Node3D:
	return _left_hand_socket


func get_facial_controller() -> Node:
	return _facial


func set_emotion(emotion: String, intensity: float = 1.0, transition_s: float = 0.35) -> void:
	if _facial != null:
		_facial.call("set_emotion", emotion, intensity, transition_s)


func set_listening(is_listening: bool, speaker_world_pos: Vector3 = Vector3.ZERO, listener_emotion: String = "", speaker_energy: float = 0.0) -> void:
	if _facial != null:
		_facial.call("set_listening", is_listening, speaker_world_pos, listener_emotion, speaker_energy)


# ----------------------------------------------------------- per frame


func has_clip(clip: String) -> bool:
	return _clip_names.has(clip)


func clip_length(clip: String) -> float:
	return float(_clip_lengths.get(clip, 0.0))


func pose_clip(clip: String, time: float) -> void:
	if _player == null or not _clip_names.has(clip):
		return
	_player.play(StringName(clip), 0.0)
	_player.seek(time, true)
	_current_clip = clip
	_current_key = "pose:%s" % clip


func update_animation(clip: String, clip_key: String, speed: float, delta: float, blend: float = 0.25) -> void:
	if _player == null:
		return
	_elapsed_s += maxf(delta, 0.0)
	_last_clip_requested = clip
	var resolved := ""
	if _clip_names.has(clip):
		resolved = clip
		_last_clip_status = "ok"
	elif _clip_names.has("idle"):
		resolved = "idle"
		_last_clip_status = "fallback"
	else:
		_last_clip_status = "missing"
		return
	if clip_key != _current_key:
		var entering_loco: bool = LOCOMOTION_CLIPS.has(resolved) and not LOCOMOTION_CLIPS.has(_current_clip)
		var restart: bool = resolved == _current_clip and not LOOPING_CLIPS.has(resolved)
		if resolved != _current_clip or restart:
			var eff_blend: float = 0.04 if entering_loco else (blend if _current_clip != "" else 0.0)
			_player.play(StringName(resolved), eff_blend)
			if restart:
				_player.seek(0.0, true)
			elif entering_loco:
				_player.seek(0.16, true)
		_current_clip = resolved
		_current_key = clip_key
	var clamped_speed: float = clampf(speed, 0.05, 3.0)
	_player.speed_scale = clamped_speed
	_player.advance(delta)
	_is_moving = LOCOMOTION_CLIPS.has(resolved)
	if _is_moving:
		var prof: Dictionary = GAIT_PROFILES.get(current_gait, GAIT_PROFILES["casual"])
		_loco_phase += delta * float(prof.get("stride_freq", 1.8)) * clamped_speed * TAU


## Updates facial BlendShapes, emotions, TTS lip-sync, and blinking.
func update_face(frame_index: int, fps: int, is_speaking: bool, speech_energy: float = -1.0, viseme_hint: String = "", dt: float = -1.0) -> void:
	if _facial != null:
		_facial.call("update_expression", frame_index, fps, is_speaking, speech_energy, viseme_hint, dt)
		face_state = str(_facial.get("face_state"))
		return
	# Fallback if facial controller is somehow absent.
	show_face("talk_mid" if is_speaking else "neutral")


func show_face(state: String) -> void:
	face_state = state
	if _facial != null:
		_facial.call("show_face", state)
		return
	var offset: Vector2 = FACE_CELLS.get(state, Vector2.ZERO)
	for material: ShaderMaterial in _face_materials:
		material.set_shader_parameter("cell_offset", offset)


func has_face_atlas() -> bool:
	return _face_has_atlas


## Applies smooth neck+head gaze via FacialAnimationController (with emotion
## head pitch/tilt & listener micro-nods).
func apply_look_at(world_target: Vector3, weight: float, dt: float = 1.0 / 24.0) -> void:
	if _skeleton == null or _head_bone < 0:
		return
	if _facial != null:
		_facial.call("set_gaze_target", world_target, weight)
		_facial.call("apply_head_gaze", eye_world_position(), _animated_bones, dt)


## Procedural overlays after clip evaluation and gaze:
## breathing, gait posture/sway, upper-body carry layering, arm IK (right/left),
## natural 5-finger curl/grip, explicit nod, and exact floor grounding.
func apply_overlays(
	nod_phase: float,
	nod_weight: float,
	reach_target: Vector3 = Vector3.ZERO,
	reach_weight: float = 0.0,
	reach_side: String = "right"
) -> void:
	if _skeleton == null:
		return

	# 1. Subtle breathing micro-motion + gait posture modulation.
	_apply_breathing_and_gait()

	# 2. Upper-body carry layering: when walking while holding a prop, subtly
	# steady the carrying forearm so the prop stays naturally in front of hip.
	if _is_moving and reach_weight <= 0.05:
		if holding:
			_apply_carry_arm_bias("r")
		if holding_left:
			_apply_carry_arm_bias("l")

	# 3. Analytic two-bone arm IK for handoff / pickup / inspect / use.
	if reach_weight > 0.001:
		var side_prefix: String = "l" if reach_side == "left" else "r"
		_reach_arm(side_prefix, _skeleton.global_transform.affine_inverse() * reach_target, clampf(reach_weight, 0.0, 1.0))

	# 4. Natural 5-finger posing: tight grip when holding, gentle relaxed curl otherwise.
	_apply_hand_fingers("r", holding)
	_apply_hand_fingers("l", holding_left)

	# 5. Explicit script-triggered nod on top of gaze.
	if nod_weight > 0.001 and _head_bone >= 0 and _animated_bones.has(_head_bone):
		var angle: float = deg_to_rad(NOD_DEGREES) * nod_weight * maxf(0.0, sin(nod_phase * TAU))
		_pitch_bone(_head_bone, angle)
		if _neck_bone >= 0 and _animated_bones.has(_neck_bone):
			_pitch_bone(_neck_bone, angle * 0.45)

	# 6. Exact per-frame floor grounding so stance sole stays at y == 0.0.
	_ground_feet()


func _apply_breathing_and_gait() -> void:
	var human: Dictionary = _target.human
	var spine_count: int = int(human.get("spine_count", 0))
	if spine_count <= 0:
		return
	var prof: Dictionary = GAIT_PROFILES.get(current_gait, GAIT_PROFILES["casual"])
	var chest_bone: int = int(human.get("spine%d" % (spine_count - 1), -1))
	var mid_spine: int = int(human.get("spine%d" % maxi(spine_count - 2, 0), -1))

	# Natural breathing (~0.27 Hz) even during idle/talking so characters stay alive.
	var breath_amp: float = float(prof.get("breath_amp", 1.0))
	var breath_rad: float = deg_to_rad(1.1 * breath_amp) * sin(_elapsed_s * 1.7 + _blink_phase * TAU)
	if chest_bone >= 0:
		_pitch_bone(chest_bone, breath_rad)

	# Gait-specific torso posture & rhythmic shoulder counter-roll during locomotion.
	if _is_moving:
		var lean_deg: float = float(prof.get("spine_pitch_deg", 0.0))
		var roll_deg: float = float(prof.get("shoulder_roll_deg", 2.0))
		if absf(lean_deg) > 0.05 and mid_spine >= 0:
			_pitch_bone(mid_spine, deg_to_rad(lean_deg * 0.6))
		if absf(lean_deg) > 0.05 and chest_bone >= 0:
			_pitch_bone(chest_bone, deg_to_rad(lean_deg * 0.4))
		if absf(roll_deg) > 0.05 and chest_bone >= 0:
			_roll_bone(chest_bone, deg_to_rad(roll_deg) * sin(_loco_phase))


func _apply_carry_arm_bias(side: String) -> void:
	var human: Dictionary = _target.human
	var upper: int = int(human.get("%s_upper_arm" % side, -1))
	var lower: int = int(human.get("%s_lower_arm" % side, -1))
	if upper < 0 or lower < 0:
		return
	# Gently steady forearm slightly forward so held prop doesn't swing backward through hip.
	var carry_local := Vector3(0.20 if side == "r" else -0.20, height_m * 0.62, 0.28)
	var carry_world: Vector3 = global_transform * carry_local
	_reach_arm(side, _skeleton.global_transform.affine_inverse() * carry_world, 0.38)


func _apply_hand_fingers(side: String, is_holding: bool) -> void:
	var human: Dictionary = _target.human
	var grip_map: Dictionary = _grip_pose_r if side == "r" else _grip_pose_l
	if is_holding:
		for finger: String in GRIP_FINGERS:
			for digit in [1, 2, 3]:
				var key := "%s_%s%d" % [side, finger, digit]
				if not human.has(key):
					continue
				var b: int = int(human[key])
				if grip_map.has(b):
					var cur: Quaternion = _skeleton.get_bone_pose_rotation(b)
					_skeleton.set_bone_pose_rotation(b, cur.slerp(grip_map[b], 0.85))
				if _finger_curl_axes.has(b):
					var axis: Vector3 = _finger_curl_axes[b] as Vector3
					var extra_deg: float = 28.0 if finger == "thumb" else (32.0 if digit == 1 else (42.0 if digit == 2 else 34.0))
					var q_now: Quaternion = _skeleton.get_bone_pose_rotation(b)
					var blend_amt: float = 0.35 if grip_map.has(b) else 0.92
					_skeleton.set_bone_pose_rotation(b, (q_now * Quaternion(axis, deg_to_rad(extra_deg * blend_amt))).normalized())
	else:
		# Subtle relaxed natural finger curvature (never stiff splayed paddles).
		for finger: String in GRIP_FINGERS:
			for digit in [1, 2, 3]:
				var key := "%s_%s%d" % [side, finger, digit]
				if not human.has(key):
					continue
				var b: int = int(human[key])
				if not _finger_curl_axes.has(b):
					continue
				var axis: Vector3 = _finger_curl_axes[b] as Vector3
				var relax_deg: float = 8.0 if finger == "thumb" else (12.0 if digit == 1 else (16.0 if digit == 2 else 12.0))
				var q_now: Quaternion = _skeleton.get_bone_pose_rotation(b)
				_skeleton.set_bone_pose_rotation(b, (q_now * Quaternion(axis, deg_to_rad(relax_deg))).normalized())


func _reach_arm(side: String, target: Vector3, weight: float) -> void:
	var human: Dictionary = _target.human
	var upper: int = int(human.get("%s_upper_arm" % side, -1))
	var lower: int = int(human.get("%s_lower_arm" % side, -1))
	var hand: int = int(human.get("%s_hand" % side, -1))
	if upper < 0 or lower < 0 or hand < 0:
		return
	var gu: Transform3D = _skeleton.get_bone_global_pose(upper)
	var gl: Transform3D = _skeleton.get_bone_global_pose(lower)
	var gh: Transform3D = _skeleton.get_bone_global_pose(hand)
	var s: Vector3 = gu.origin
	var e: Vector3 = gl.origin
	var w: Vector3 = gh.origin
	var a: float = s.distance_to(e)
	var b: float = e.distance_to(w)
	if a < 0.001 or b < 0.001:
		return
	var to_target: Vector3 = target - s
	var d: float = clampf(to_target.length(), absf(a - b) + 0.01, (a + b) * 0.995)
	var u: Vector3 = to_target.normalized()
	var pole_model: Vector3 = REACH_POLE_L if side == "l" else REACH_POLE_R
	var pole: Vector3 = ((_target.skel_xform as Transform3D).basis.inverse() * pole_model).normalized()
	var v: Vector3 = (pole - u * pole.dot(u)).normalized()
	if v.length_squared() < 0.01:
		v = Vector3.DOWN
	var cos_a: float = clampf((a * a + d * d - b * b) / (2.0 * a * d), -1.0, 1.0)
	var elbow: Vector3 = s + u * a * cos_a + v * a * sqrt(maxf(1.0 - cos_a * cos_a, 0.0))
	var upper_rot: Quaternion = gu.basis.get_rotation_quaternion()
	var new_upper: Quaternion = (Quaternion((e - s).normalized(), (elbow - s).normalized()) * upper_rot).normalized()
	var parent: int = _skeleton.get_bone_parent(upper)
	var parent_rot: Quaternion = Quaternion.IDENTITY if parent < 0 else _skeleton.get_bone_global_pose(parent).basis.get_rotation_quaternion()
	var lower_local: Quaternion = _skeleton.get_bone_pose_rotation(lower)
	var lower_offset: Vector3 = _skeleton.get_bone_pose_position(lower)
	var hand_offset: Vector3 = _skeleton.get_bone_pose_position(hand)
	var lower_rot: Quaternion = new_upper * lower_local
	var elbow_now: Vector3 = s + Basis(new_upper) * lower_offset
	var wrist_now: Vector3 = elbow_now + Basis(lower_rot) * hand_offset
	var new_lower: Quaternion = (Quaternion((wrist_now - elbow_now).normalized(), (target - elbow_now).normalized()) * lower_rot).normalized()
	var upper_local_ik: Quaternion = (parent_rot.inverse() * new_upper).normalized()
	var lower_local_ik: Quaternion = (new_upper.inverse() * new_lower).normalized()
	_skeleton.set_bone_pose_rotation(upper, _skeleton.get_bone_pose_rotation(upper).slerp(upper_local_ik, weight))
	_skeleton.set_bone_pose_rotation(lower, lower_local.slerp(lower_local_ik, weight))


func bone_world_position(key: String) -> Vector3:
	var bone: int = int(_target.human.get(key, -1)) if not _target.is_empty() else -1
	if bone < 0 or _skeleton == null:
		return global_position
	return _skeleton.global_transform * _skeleton.get_bone_global_pose(bone).origin


func eye_world_position() -> Vector3:
	if _skeleton == null or _head_bone < 0:
		return global_position + Vector3.UP * height_m * 0.93
	var rest: Transform3D = _target.globals[_head_bone]
	var pose: Transform3D = _skeleton.get_bone_global_pose(_head_bone)
	var eye_skel: Vector3 = (_target.skel_xform as Transform3D).affine_inverse() * _eye_rest_model()
	return _skeleton.global_transform * (pose * (rest.affine_inverse() * eye_skel))


func _eye_rest_model() -> Vector3:
	if _eye_rest != Vector3.INF:
		return _eye_rest
	var head_rest: Vector3 = ((_target.skel_xform as Transform3D) * (_target.globals[_head_bone] as Transform3D)).origin
	return head_rest + Vector3(0.0, 0.075, 0.08) * (height_m / 1.7) / maxf(_model_scale, 0.001)


func _ground_feet() -> void:
	if _foot_rest.is_empty() or _model_root == null:
		return
	var skel: Transform3D = _target.skel_xform
	var lowest := INF
	for bone: Variant in _foot_rest.keys():
		var y: float = (skel * _skeleton.get_bone_global_pose(int(bone)).origin).y
		lowest = minf(lowest, y - float(_foot_rest[bone]))
	if lowest == INF:
		return
	var wanted: float = clampf(-lowest * _model_scale, -FOOT_GROUND_LIMIT, FOOT_GROUND_LIMIT)
	_ground_offset = lerpf(_ground_offset, wanted, ground_smoothing)
	_model_root.position.y = _model_base_y + _ground_offset


func _capture_foot_rest() -> void:
	_foot_rest.clear()
	_side_foot_bones = {"left": [], "right": []}
	var skel: Transform3D = _target.skel_xform
	for side_key: String in ["left", "right"]:
		var prefix: String = "l" if side_key == "left" else "r"
		for part: String in ["foot", "toes"]:
			var bone: int = int(_target.human.get("%s_%s" % [prefix, part], -1))
			if bone >= 0:
				_foot_rest[bone] = (skel * (_target.globals[bone] as Transform3D)).origin.y
				(_side_foot_bones[side_key] as Array).append(bone)


## World sole position [x, sole_y, z] for left/right foot (sole_y == 0.0 when planted).
## XZ always tracks a consistent foot reference point (midpoint of ankle & ball)
## so heel-strike / toe-off never teleports XZ between bones.
func foot_sole_world_position(side: String) -> Vector3:
	var bones: Array = _side_foot_bones.get(side, [])
	if bones.is_empty() or _skeleton == null:
		return global_position
	var skel: Transform3D = _target.skel_xform
	var min_sole_y := INF
	var sum_xz := Vector2.ZERO
	var count := 0
	for b_v: Variant in bones:
		var b: int = int(b_v)
		var pose_origin: Vector3 = _skeleton.get_bone_global_pose(b).origin
		var model_y: float = (skel * pose_origin).y
		var sole_y: float = global_position.y + (model_y - float(_foot_rest.get(b, model_y))) * _model_scale + _ground_offset
		var wpos: Vector3 = _skeleton.global_transform * pose_origin
		min_sole_y = minf(min_sole_y, sole_y)
		sum_xz += Vector2(wpos.x, wpos.z)
		count += 1
	var avg_xz: Vector2 = sum_xz / float(maxi(count, 1))
	return Vector3(avg_xz.x, 0.0 if min_sole_y == INF else min_sole_y, avg_xz.y)


func _measure_stride_speed(clip_name: String = "walk") -> float:
	if not _clip_names.has(clip_name) or _foot_rest.is_empty():
		return 0.0
	var length: float = float(_clip_lengths.get(clip_name, 0.0))
	if length <= 0.05:
		return 0.0
	var samples := 60
	var skel: Transform3D = _target.skel_xform
	var feet: Array[int] = []
	for key: String in ["l_foot", "r_foot"]:
		if _target.human.has(key):
			feet.append(int(_target.human[key]))
	var track := {}
	for foot: int in feet:
		track[foot] = []
	var anim: Animation = _player.get_animation(StringName(clip_name))
	var names: PackedStringArray = _target.names
	var bone_tracks := {}
	for t in anim.get_track_count():
		var bone: int = names.find(str(anim.track_get_path(t).get_concatenated_subnames()))
		if bone < 0:
			continue
		var entry: Dictionary = bone_tracks.get(bone, {})
		entry["rot" if anim.track_get_type(t) == Animation.TYPE_ROTATION_3D else "pos"] = t
		bone_tracks[bone] = entry
	var rests: Array[Transform3D] = _target.rests
	var parents: PackedInt32Array = _target.parents
	for i in samples + 1:
		var time: float = length * float(i) / float(samples)
		var globals: Array[Transform3D] = []
		globals.resize(rests.size())
		for b: int in _target.order:
			var local: Transform3D = rests[b]
			var entry: Dictionary = bone_tracks.get(b, {})
			if entry.has("rot"):
				local.basis = Basis(anim.rotation_track_interpolate(int(entry["rot"]), time))
			if entry.has("pos"):
				local.origin = anim.position_track_interpolate(int(entry["pos"]), time)
			globals[b] = local if parents[b] < 0 else globals[parents[b]] * local
		for foot: int in feet:
			(track[foot] as Array).append(skel * globals[foot].origin)
	var speeds: Array[float] = []
	var dt: float = length / float(samples)
	for foot: int in feet:
		var points: Array = track[foot]
		var low := INF
		var high := -INF
		for p: Vector3 in points:
			low = minf(low, p.y)
			high = maxf(high, p.y)
		var planted: float = low + (high - low) * 0.2
		for i in range(1, points.size()):
			var a: Vector3 = points[i - 1]
			var b: Vector3 = points[i]
			if a.y <= planted and b.y <= planted:
				var back: float = -(b.z - a.z) / dt
				if back > 0.0:
					speeds.append(back)
	if speeds.size() < 4:
		if clip_name == "walk":
			stride_report = {"failed": "planted samples=%d" % speeds.size(), "length": length}
		return 0.0
	speeds.sort()
	if clip_name == "walk":
		stride_report = {"samples": speeds.size(), "median_model_units": snappedf(speeds[speeds.size() / 2], 0.001)}
	return speeds[speeds.size() / 2] * _model_scale


func _first_child(bone: int) -> int:
	if bone < 0:
		return -1
	var parents: PackedInt32Array = _target.parents
	for i in parents.size():
		if parents[i] == bone:
			return i
	return -1


func posture_report() -> Dictionary:
	if _skeleton == null:
		return {}
	var human: Dictionary = _target.human
	var skel_basis: Basis = (_target.skel_xform as Transform3D).basis
	var out := {}
	var hips: int = int(human.get("hips", -1))
	if hips >= 0 and _neck_bone >= 0:
		var lean: Vector3 = skel_basis * (_skeleton.get_bone_global_pose(_neck_bone).origin - _skeleton.get_bone_global_pose(hips).origin)
		out["torso_lean_deg"] = snappedf(rad_to_deg(atan2(lean.z, lean.y)), 0.1)
	if _head_bone >= 0:
		var rest_head: Basis = (_target.globals[_head_bone] as Transform3D).basis.orthonormalized()
		var forward_skel: Vector3 = skel_basis.inverse() * Vector3.BACK
		var facing: Vector3 = skel_basis * (_skeleton.get_bone_global_pose(_head_bone).basis.orthonormalized() * (rest_head.inverse() * forward_skel))
		out["face_pitch_deg"] = snappedf(rad_to_deg(asin(clampf(facing.normalized().y, -1.0, 1.0))), 0.1)
	for prefix: String in ["l", "r"]:
		var upper: int = int(human.get("%s_upper_arm" % prefix, -1))
		var lower: int = int(human.get("%s_lower_arm" % prefix, -1))
		if upper >= 0 and lower >= 0:
			var d: Vector3 = (skel_basis * (_skeleton.get_bone_global_pose(lower).origin - _skeleton.get_bone_global_pose(upper).origin)).normalized()
			var side: float = 1.0 if prefix == "l" else -1.0
			out["%s_arm_splay_deg" % prefix] = snappedf(rad_to_deg(asin(clampf(side * d.x, -1.0, 1.0))), 0.1)
		var hand: int = int(human.get("%s_hand" % prefix, -1))
		var tip: int = _first_child(hand)
		if hand >= 0 and lower >= 0 and tip >= 0:
			var fore: Vector3 = _skeleton.get_bone_global_pose(hand).origin - _skeleton.get_bone_global_pose(lower).origin
			var palm: Vector3 = _skeleton.get_bone_global_pose(tip).origin - _skeleton.get_bone_global_pose(hand).origin
			var fore0: Vector3 = (_target.globals[hand] as Transform3D).origin - (_target.globals[lower] as Transform3D).origin
			var palm0: Vector3 = (_target.globals[tip] as Transform3D).origin - (_target.globals[hand] as Transform3D).origin
			out["%s_wrist_bend_deg" % prefix] = [snappedf(rad_to_deg(fore0.angle_to(palm0)), 0.1), snappedf(rad_to_deg(fore.angle_to(palm)), 0.1)]
	out["ground_offset_m"] = snappedf(_ground_offset, 0.001)
	out["gait"] = current_gait
	out["direct_rig_match"] = direct_rig_match
	return out


## Per-frame deterministic telemetry matching tools/qc_pipeline.py schema.
func get_qc_snapshot() -> Dictionary:
	var lf: Vector3 = foot_sole_world_position("left")
	var rf: Vector3 = foot_sole_world_position("right")
	var pelvis: Vector3 = bone_world_position("hips")
	var lh: Vector3 = _left_hand_socket.global_position if _left_hand_socket != null else bone_world_position("l_hand")
	var rh: Vector3 = _hand_socket.global_position if _hand_socket != null else bone_world_position("r_hand")
	var clip_status := {}
	clip_status[_last_clip_requested] = _last_clip_status
	var face_snap: Dictionary = _facial.call("get_qc_snapshot") if _facial != null else {}
	return {
		"actor_id": actor_id,
		"left_foot": [snappedf(lf.x, 0.001), snappedf(lf.y, 0.001), snappedf(lf.z, 0.001)],
		"right_foot": [snappedf(rf.x, 0.001), snappedf(rf.y, 0.001), snappedf(rf.z, 0.001)],
		"pelvis": [snappedf(pelvis.x, 0.001), snappedf(pelvis.y, 0.001), snappedf(pelvis.z, 0.001)],
		"hands": {
			"left": [snappedf(lh.x, 0.001), snappedf(lh.y, 0.001), snappedf(lh.z, 0.001)],
			"right": [snappedf(rh.x, 0.001), snappedf(rh.y, 0.001), snappedf(rh.z, 0.001)],
		},
		"clips": [_current_clip],
		"clip_status": clip_status,
		"gait": current_gait,
		"holding_right": holding,
		"holding_left": holding_left,
		"facial": face_snap,
	}


func _pitch_bone(bone: int, angle: float) -> void:
	var global_pose: Transform3D = _skeleton.get_bone_global_pose(bone)
	var lateral_skel: Vector3 = (_target.skel_xform as Transform3D).basis.inverse() * Vector3.RIGHT
	var axis_local: Vector3 = (global_pose.basis.inverse() * lateral_skel).normalized()
	if axis_local.length_squared() < 0.5:
		return
	var current: Quaternion = _skeleton.get_bone_pose_rotation(bone)
	_skeleton.set_bone_pose_rotation(bone, (current * Quaternion(axis_local, angle)).normalized())


func _roll_bone(bone: int, angle: float) -> void:
	var global_pose: Transform3D = _skeleton.get_bone_global_pose(bone)
	var fwd_skel: Vector3 = (_target.skel_xform as Transform3D).basis.inverse() * Vector3.BACK
	var axis_local: Vector3 = (global_pose.basis.inverse() * fwd_skel).normalized()
	if axis_local.length_squared() < 0.5:
		return
	var current: Quaternion = _skeleton.get_bone_pose_rotation(bone)
	_skeleton.set_bone_pose_rotation(bone, (current * Quaternion(axis_local, angle)).normalized())


func head_world_position() -> Vector3:
	if _skeleton == null or _head_bone < 0:
		return global_position + Vector3.UP * height_m * 0.93
	return _skeleton.global_transform * _skeleton.get_bone_global_pose(_head_bone).origin


func hand_world_position() -> Vector3:
	if _hand_socket == null:
		return global_position
	return _hand_socket.global_position


# ------------------------------------------------------------ sockets


func _make_attachment(bone: int, label: String) -> BoneAttachment3D:
	var attach := BoneAttachment3D.new()
	attach.name = label
	_skeleton.add_child(attach)
	attach.bone_name = _skeleton.get_bone_name(bone)
	attach.bone_idx = bone
	return attach


func _build_sockets() -> void:
	var human: Dictionary = _target.human
	var r_hand: int = int(human.get("r_hand", -1))
	if r_hand >= 0:
		var attach := _make_attachment(r_hand, "RightHandSocket")
		_hand_socket = Node3D.new()
		_hand_socket.name = "Grip"
		attach.add_child(_hand_socket)
		_hand_socket.transform = _grip_transform(r_hand, "r")
	var l_hand: int = int(human.get("l_hand", -1))
	if l_hand >= 0:
		var attach_l := _make_attachment(l_hand, "LeftHandSocket")
		_left_hand_socket = Node3D.new()
		_left_hand_socket.name = "GripLeft"
		attach_l.add_child(_left_hand_socket)
		_left_hand_socket.transform = _grip_transform(l_hand, "l")
	var chest: int = int(human.get("spine%d" % maxi(int(human.get("spine_count", 1)) - 1, 0), -1))
	if chest >= 0:
		var attach_chest := _make_attachment(chest, "ChestSocket")
		_chest_socket = Node3D.new()
		_chest_socket.name = "Chest"
		attach_chest.add_child(_chest_socket)
		_chest_socket.transform = _scene_to_bone(chest)
	if _head_bone >= 0:
		_head_attach = _make_attachment(_head_bone, "HeadSocket")
	_build_hair()
	_build_badge()


func _scene_to_bone(bone: int) -> Transform3D:
	var rest_scene: Transform3D = (_target.skel_xform as Transform3D) * (_target.globals[bone] as Transform3D)
	return rest_scene.affine_inverse()


func _grip_transform(hand: int, side: String = "r") -> Transform3D:
	var human: Dictionary = _target.human
	var hand_global: Transform3D = _target.globals[hand]
	var inv: Transform3D = hand_global.affine_inverse()
	var index_local := Vector3(0.0, 0.08, 0.0)
	var pinky_local := Vector3(0.0, 0.07, 0.03)
	var middle_local := Vector3(0.0, 0.09, 0.0)
	var thumb_local := Vector3(0.03, 0.03, 0.0)
	if human.has("%s_index1" % side):
		index_local = inv * (_target.globals[int(human["%s_index1" % side])] as Transform3D).origin
	if human.has("%s_pinky1" % side):
		pinky_local = inv * (_target.globals[int(human["%s_pinky1" % side])] as Transform3D).origin
	if human.has("%s_middle1" % side):
		middle_local = inv * (_target.globals[int(human["%s_middle1" % side])] as Transform3D).origin
	if human.has("%s_thumb1" % side):
		thumb_local = inv * (_target.globals[int(human["%s_thumb1" % side])] as Transform3D).origin
	if not human.has("%s_middle1" % side):
		return _grip_transform_without_fingers(hand)
	var finger_dir: Vector3 = middle_local.normalized()
	var across: Vector3 = (index_local - pinky_local)
	across = (across - finger_dir * across.dot(finger_dir)).normalized()
	if across.length_squared() < 0.5:
		across = finger_dir.cross(Vector3.FORWARD).normalized()
	var palm: Vector3 = finger_dir.cross(across).normalized()
	if palm.dot(thumb_local) < 0.0:
		palm = -palm
	var length: float = middle_local.length()
	var origin: Vector3 = finger_dir * length * 1.15 + palm * length * 0.35
	var basis := Basis(across.cross(palm).normalized(), across, palm)
	var unscale: float = 1.0 / maxf(_model_scale, 0.001)
	return Transform3D(basis.orthonormalized().scaled(Vector3.ONE * unscale), origin)


func _grip_transform_without_fingers(hand: int) -> Transform3D:
	var skel: Transform3D = _target.skel_xform
	var hand_global: Transform3D = skel * (_target.globals[hand] as Transform3D)
	var tip_scene: Vector3 = hand_global * Vector3(0.0, 0.09 / maxf(_model_scale, 0.001), 0.0)
	var parents: PackedInt32Array = _target.parents
	for i in parents.size():
		if parents[i] == hand:
			tip_scene = skel * (_target.globals[i] as Transform3D).origin
			break
	var to_local: Basis = hand_global.basis.orthonormalized().inverse()
	var finger_scene: Vector3 = tip_scene - hand_global.origin
	var length: float = finger_scene.length()
	var finger: Vector3 = (to_local * finger_scene).normalized()
	var palm: Vector3 = to_local * Vector3.DOWN
	palm = (palm - finger * palm.dot(finger)).normalized()
	var across: Vector3 = palm.cross(finger).normalized()
	if across.dot(to_local * Vector3.BACK) < 0.0:
		across = -across
	var offset_value: Variant = _look.get("grip_offset", [0.45, 0.35, 0.0])
	var offset: Array = offset_value as Array if offset_value is Array else [0.45, 0.35, 0.0]
	var origin: Vector3 = finger * length * float(offset[0]) + palm * length * float(offset[1]) + across * length * float(offset[2])
	var basis := Basis(across.cross(palm).normalized(), across, palm)
	var unscale: float = 1.0 / maxf(_model_scale, 0.001)
	return Transform3D(basis.orthonormalized().scaled(Vector3.ONE * unscale), origin)


func _has_native_hair_mesh() -> bool:
	for mi: MeshInstance3D in _meshes:
		if str(mi.name).to_lower().contains("hair"):
			return true
	return false


func _build_hair() -> void:
	if _head_attach == null or _has_native_hair_mesh():
		return
	var hair_path: String = str(_look.get("hair_model", ""))
	var hair_color: Color = Color(str(_look.get("hair_color", "#101014")))
	var to_head: Transform3D = _scene_to_bone(_head_bone)
	if hair_path != "":
		var hair: Node3D = load_gltf_scene(hair_path)
		if hair != null:
			hair.name = "Hair"
			_head_attach.add_child(hair)
			hair.transform = to_head
			for node: Node in _all_children(hair):
				if node is MeshInstance3D:
					var mi := node as MeshInstance3D
					var tex: Texture2D = _first_texture(mi)
					for s in mi.mesh.get_surface_count():
						mi.set_surface_override_material(s, toon_material(hair_color, tex))
	if bool(_look.get("ponytail", false)):
		var box: AABB = _head_bounds_scene()
		var tie := Vector3(0.0, box.position.y + box.size.y * 0.72, box.position.z - 0.01)
		var tail := Node3D.new()
		tail.name = "Ponytail"
		_head_attach.add_child(tail)
		tail.transform = to_head * Transform3D(Basis(Vector3.RIGHT, deg_to_rad(-28.0)), tie)
		var knot := MeshInstance3D.new()
		var knot_mesh := SphereMesh.new()
		knot_mesh.radius = 0.034
		knot_mesh.height = 0.06
		knot.mesh = knot_mesh
		knot.material_override = toon_material(hair_color)
		tail.add_child(knot)
		var band := MeshInstance3D.new()
		var band_mesh := CylinderMesh.new()
		band_mesh.top_radius = 0.022
		band_mesh.bottom_radius = 0.022
		band_mesh.height = 0.02
		band.mesh = band_mesh
		band.position = Vector3(0.0, -0.03, -0.012)
		band.material_override = toon_material(Color(str(_look.get("collar", "#f08a24"))))
		tail.add_child(band)
		var strand := MeshInstance3D.new()
		var strand_mesh := CylinderMesh.new()
		strand_mesh.top_radius = 0.03
		strand_mesh.bottom_radius = 0.008
		strand_mesh.height = 0.24
		strand.mesh = strand_mesh
		strand.position = Vector3(0.0, -0.15, -0.03)
		strand.rotation = Vector3(deg_to_rad(12.0), 0.0, 0.0)
		strand.material_override = toon_material(hair_color)
		tail.add_child(strand)


func _head_bounds_scene() -> AABB:
	var head_global: Transform3D = (_target.skel_xform as Transform3D) * (_target.globals[_head_bone] as Transform3D)
	var top: float = height_m / _model_scale
	return AABB(Vector3(-0.1, head_global.origin.y - 0.02, head_global.origin.z - 0.11), Vector3(0.2, maxf(top - head_global.origin.y + 0.02, 0.15), 0.22))


func _build_badge() -> void:
	if _chest_socket == null or not bool(_look.get("badge", false)) or has_native_blendshapes:
		return
	var spine_top: int = int(_target.human.get("spine%d" % maxi(int(_target.human.get("spine_count", 1)) - 1, 0), -1))
	var chest_scene: Vector3 = ((_target.skel_xform as Transform3D) * (_target.globals[spine_top] as Transform3D)).origin
	var badge := MeshInstance3D.new()
	badge.name = "ChestBadge"
	var mesh := BoxMesh.new()
	mesh.size = Vector3(0.055, 0.035, 0.012)
	badge.mesh = mesh
	var material := toon_material(Color(str(_look.get("collar", "#f08a24"))))
	material.set_shader_parameter("emission_strength", 0.25)
	badge.material_override = material
	_chest_socket.add_child(badge)
	var front: float = float(_look.get("badge_front_z", 0.14))
	badge.position = Vector3(0.075, chest_scene.y + float(_look.get("badge_drop", 0.02)), chest_scene.z + front)


func _first_texture(mi: MeshInstance3D) -> Texture2D:
	if mi.mesh == null:
		return null
	for s in mi.mesh.get_surface_count():
		var material: Material = mi.get_active_material(s)
		if material is BaseMaterial3D:
			var tex: Texture2D = (material as BaseMaterial3D).albedo_texture
			if tex != null:
				return tex
	return null


# --------------------------------------------------------------- look


func _apply_look() -> void:
	_palette = {
		"jacket": Color(str(_look.get("jacket", "#2c5aa0"))),
		"collar": Color(str(_look.get("collar", "#f08a24"))),
		"pants": Color(str(_look.get("pants", "#1c2638"))),
		"boots": Color(str(_look.get("boots", "#15171c"))),
	}

	# TIER_A sculpted BlendShape models carry baked 2048x2048 UV outfits, skin,
	# normal maps, roughness maps, and real morph targets. NEVER overwrite them
	# with _dress_mesh() (which strips BlendShapes) or flat toon shaders.
	var preserve_pbr: bool = has_native_blendshapes or bool(_look.get("preserve_materials", false))
	if preserve_pbr:
		for mi: MeshInstance3D in _meshes:
			if mi.mesh == null:
				continue
			var lower_m: String = str(mi.name).to_lower()
			for s in mi.mesh.get_surface_count():
				var src_mat: Material = mi.get_active_material(s)
				if src_mat is StandardMaterial3D:
					var mat := (src_mat as StandardMaterial3D).duplicate() as StandardMaterial3D
					mat.metallic = 0.0
					if lower_m.contains("eye") and not lower_m.contains("eyebrow"):
						mat.roughness = minf(mat.roughness, 0.22)
						mat.metallic_specular = 0.55
					else:
						mat.roughness = clampf(mat.roughness, 0.56, 0.92)
						mat.metallic_specular = 0.24
						mat.rim_enabled = true
						mat.rim = 0.14
						mat.rim_tint = 0.32
					mi.set_surface_override_material(s, mat)
		return

	var skin_tex: Texture2D = null
	var skin_path: String = str(_look.get("skin_texture", ""))
	if skin_path != "" and FileAccess.file_exists(skin_path):
		var image := Image.load_from_file(skin_path)
		if image != null and not image.is_empty():
			image.generate_mipmaps()
			skin_tex = ImageTexture.create_from_image(image)
	var hair_color := Color(str(_look.get("hair_color", "#101014")))
	var dress: bool = bool(_look.get("uniform", true))
	for mi: MeshInstance3D in _meshes:
		if mi.mesh == null:
			continue
		var lower: String = str(mi.name).to_lower()
		if lower.contains("eyebrow"):
			for s in mi.mesh.get_surface_count():
				mi.set_surface_override_material(s, toon_material(hair_color, _first_texture(mi)))
			continue
		if lower.contains("eye"):
			continue
		if not dress:
			var own_tex: Texture2D = _first_texture(mi)
			for s in mi.mesh.get_surface_count():
				var source_material: Material = mi.mesh.surface_get_material(s)
				if source_material != null and source_material.resource_name == "FaceMat":
					mi.set_surface_override_material(s, _make_face_material(source_material, own_tex))
					continue
				var painted := toon_material(Color(str(_look.get("tint", "#ffffff"))), own_tex)
				painted.set_shader_parameter("rim_strength", float(_look.get("rim_strength", 0.12)))
				mi.set_surface_override_material(s, painted)
			continue
		if mi.skin == null:
			continue
		var tex: Texture2D = skin_tex if skin_tex != null else _first_texture(mi)
		var dressed: ArrayMesh = _dress_mesh(mi)
		if dressed == null:
			continue
		mi.mesh = dressed
		for s in dressed.get_surface_count():
			mi.set_surface_override_material(s, toon_material(Color(1, 1, 1), tex, true))


func _make_face_material(source: Material, body_texture: Texture2D) -> ShaderMaterial:
	var extras: Dictionary = source.get_meta("extras", {}) as Dictionary if source.get_meta("extras", {}) is Dictionary else {}
	var own: Texture2D = (source as BaseMaterial3D).albedo_texture if source is BaseMaterial3D else null
	var atlas: Texture2D = null
	var atlas_path: String = str(_look.get("face_atlas_texture", ""))
	if atlas_path != "" and FileAccess.file_exists(atlas_path):
		var image := Image.load_from_file(atlas_path)
		if image != null and not image.is_empty():
			image.generate_mipmaps()
			atlas = ImageTexture.create_from_image(image)
	elif source.has_meta("face_atlas") and source.get_meta("face_atlas") is Texture2D:
		var embedded: Image = (source.get_meta("face_atlas") as Texture2D).get_image()
		if embedded != null and not embedded.is_empty():
			embedded.decompress()
			embedded.generate_mipmaps()
			atlas = ImageTexture.create_from_image(embedded)
	var plain: Texture2D = own if own != null else body_texture
	var material := face_material(plain, atlas, Color(str(_look.get("face_tint", _look.get("tint", "#ffffff")))))
	material.set_shader_parameter("ambient_floor", float(_look.get("face_ambient_floor", 0.78)))
	_face_materials.append(material)
	_face_has_atlas = atlas != null
	var center_value: Variant = extras.get("face_center", null)
	var radii_value: Variant = extras.get("face_radii", null)
	if center_value is Array and radii_value is Array and (center_value as Array).size() == 3 and (radii_value as Array).size() == 3:
		var c: Array = center_value
		_eye_rest = Vector3(float(c[0]), float(c[1]) + float((radii_value as Array)[1]) * 0.12, float(c[2]) + float((radii_value as Array)[2]))
	return material


func _dress_mesh(mi: MeshInstance3D) -> ArrayMesh:
	var source: Mesh = mi.mesh
	var skin: Skin = mi.skin
	var bind_region: Array[String] = []
	var bind_pose_inv: Array[Transform3D] = []
	var human: Dictionary = _target.human
	var region_by_bone := {}
	for key: Variant in human.keys():
		var k: String = str(key)
		if k == "spine_count":
			continue
		var b: int = int(human[k])
		var region := ""
		if k == "head":
			region = "head"
		elif k == "neck":
			region = "neck"
		elif k == "hips":
			region = "hips"
		elif k.begins_with("spine") or k.ends_with("shoulder") or k.ends_with("upper_arm") or k.ends_with("lower_arm"):
			region = "jacket"
		elif k.ends_with("_hand") or k.contains("thumb") or k.contains("index") or k.contains("middle") or k.contains("ring") or k.contains("pinky"):
			region = "skin"
		elif k.ends_with("upper_leg") or k.ends_with("lower_leg"):
			region = "pants"
		elif k.ends_with("foot") or k.ends_with("toes"):
			region = "boots"
		region_by_bone[b] = region
	for i in skin.get_bind_count():
		var bone: int = skin.get_bind_bone(i)
		if bone < 0:
			bone = _skeleton.find_bone(str(skin.get_bind_name(i)))
		var region: String = str(region_by_bone.get(bone, ""))
		if region == "" and bone >= 0:
			var walk: int = bone
			while walk >= 0 and region == "":
				walk = _skeleton.get_bone_parent(walk)
				region = str(region_by_bone.get(walk, ""))
			if region in ["head", "hips"]:
				region = "skin" if region == "head" else "pants"
		bind_region.append(region)
		bind_pose_inv.append(skin.get_bind_pose(i).affine_inverse())
	var neck_y := 0.0
	var neck_xz := Vector2.ZERO
	var head_y := 0.0
	var hips_y := 0.0
	for i in skin.get_bind_count():
		var bone_i: int = skin.get_bind_bone(i)
		if bone_i < 0:
			bone_i = _skeleton.find_bone(str(skin.get_bind_name(i)))
		if bone_i == _neck_bone:
			neck_y = bind_pose_inv[i].origin.y
			neck_xz = Vector2(bind_pose_inv[i].origin.x, bind_pose_inv[i].origin.z)
		elif bone_i == _head_bone:
			head_y = bind_pose_inv[i].origin.y
		elif bone_i == int(human.get("hips", -2)):
			hips_y = bind_pose_inv[i].origin.y
	var collar_top: float = neck_y + (head_y - neck_y) * 0.42
	var collar_bottom: float = neck_y - (head_y - neck_y) * 0.25
	var jacket_hem: float = hips_y - (neck_y - hips_y) * 0.12

	var out := ArrayMesh.new()
	for s in source.get_surface_count():
		var arrays: Array = source.surface_get_arrays(s)
		var verts: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES] if arrays[Mesh.ARRAY_BONES] != null else PackedInt32Array()
		var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS] if arrays[Mesh.ARRAY_WEIGHTS] != null else PackedFloat32Array()
		if verts.is_empty() or bones.is_empty():
			return null
		var per: int = floori(float(bones.size()) / float(verts.size()))
		var regions := PackedStringArray()
		regions.resize(verts.size())
		for v in verts.size():
			var best_w := -1.0
			var best_bind := 0
			for j in per:
				var w: float = weights[v * per + j]
				if w > best_w:
					best_w = w
					best_bind = bones[v * per + j]
			var region: String = bind_region[best_bind] if best_bind < bind_region.size() else ""
			var y: float = verts[v].y
			var near_neck: bool = Vector2(verts[v].x, verts[v].z).distance_to(neck_xz) < COLLAR_RADIUS
			if (region == "neck" or region == "jacket") and y > collar_bottom and near_neck:
				region = "collar" if y < collar_top else "skin"
			elif region == "neck":
				region = "jacket" if y < collar_top else "skin"
			elif region == "hips":
				region = "jacket" if y > jacket_hem else "pants"
			elif region == "head":
				region = "skin"
			regions[v] = region
		out.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, _flat_faces(arrays, regions, per), [], {}, Mesh.ARRAY_FLAG_USE_8_BONE_WEIGHTS if per == 8 else 0)
	return out


func _flat_faces(arrays: Array, regions: PackedStringArray, per: int) -> Array:
	var indices: PackedInt32Array = arrays[Mesh.ARRAY_INDEX] if arrays[Mesh.ARRAY_INDEX] != null else PackedInt32Array()
	var count: int = indices.size() if not indices.is_empty() else (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
	var src_v: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
	var src_n: PackedVector3Array = arrays[Mesh.ARRAY_NORMAL] if arrays[Mesh.ARRAY_NORMAL] != null else PackedVector3Array()
	var src_t: PackedFloat32Array = arrays[Mesh.ARRAY_TANGENT] if arrays[Mesh.ARRAY_TANGENT] != null else PackedFloat32Array()
	var src_uv: PackedVector2Array = arrays[Mesh.ARRAY_TEX_UV] if arrays[Mesh.ARRAY_TEX_UV] != null else PackedVector2Array()
	var src_b: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
	var src_w: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
	var out_v := PackedVector3Array()
	var out_n := PackedVector3Array()
	var out_t := PackedFloat32Array()
	var out_uv := PackedVector2Array()
	var out_c := PackedColorArray()
	var out_b := PackedInt32Array()
	var out_w := PackedFloat32Array()
	out_v.resize(count)
	out_c.resize(count)
	out_b.resize(count * per)
	out_w.resize(count * per)
	if not src_n.is_empty():
		out_n.resize(count)
	if not src_t.is_empty():
		out_t.resize(count * 4)
	if not src_uv.is_empty():
		out_uv.resize(count)
	for tri in range(0, count - 2, 3):
		var ids: Array[int] = []
		for k in 3:
			ids.append(indices[tri + k] if not indices.is_empty() else tri + k)
		var region: String = regions[ids[0]]
		if regions[ids[1]] == regions[ids[2]]:
			region = regions[ids[1]]
		var color := Color(0, 0, 0, 0)
		if _palette.has(region):
			color = (_palette[region] as Color).srgb_to_linear()
			color.a = 1.0
		for k in 3:
			var dst: int = tri + k
			var src: int = ids[k]
			out_v[dst] = src_v[src]
			out_c[dst] = color
			if not src_n.is_empty():
				out_n[dst] = src_n[src]
			if not src_t.is_empty():
				for c in 4:
					out_t[dst * 4 + c] = src_t[src * 4 + c]
			if not src_uv.is_empty():
				out_uv[dst] = src_uv[src]
			for j in per:
				out_b[dst * per + j] = src_b[src * per + j]
				out_w[dst * per + j] = src_w[src * per + j]
	var result: Array = []
	result.resize(Mesh.ARRAY_MAX)
	result[Mesh.ARRAY_VERTEX] = out_v
	if not out_n.is_empty():
		result[Mesh.ARRAY_NORMAL] = out_n
	if not out_t.is_empty():
		result[Mesh.ARRAY_TANGENT] = out_t
	if not out_uv.is_empty():
		result[Mesh.ARRAY_TEX_UV] = out_uv
	result[Mesh.ARRAY_COLOR] = out_c
	result[Mesh.ARRAY_BONES] = out_b
	result[Mesh.ARRAY_WEIGHTS] = out_w
	return result


# -------------------------------------------------------- first person


func set_first_person_hidden(hidden: bool) -> void:
	_first_person_hidden = hidden
	for mi: MeshInstance3D in _meshes:
		mi.layers = LAYER_SELF_HIDDEN if hidden else LAYER_VISIBLE
	if _head_attach != null:
		for node: Node in _all_children(_head_attach):
			if node is MeshInstance3D:
				(node as MeshInstance3D).layers = LAYER_SELF_HIDDEN if hidden else LAYER_VISIBLE
	if _chest_socket != null:
		for node: Node in _all_children(_chest_socket):
			if node is MeshInstance3D:
				(node as MeshInstance3D).layers = LAYER_SELF_HIDDEN if hidden else LAYER_VISIBLE


func is_first_person_hidden() -> bool:
	return _first_person_hidden


func build_viewmodel(grip_radius: float = 0.022) -> Node3D:
	var root := Node3D.new()
	root.name = "Viewmodel_%s" % actor_id
	var jacket: Color = _palette.get("jacket", Color("#2c5aa0"))
	var collar: Color = _palette.get("collar", Color("#f08a24"))
	var glove := toon_material(Color(str(_look.get("glove", "#23272e"))))
	glove.set_shader_parameter("rim_strength", 0.22)
	var glove_trim := toon_material(Color(str(_look.get("glove_trim", "#3b414a"))))
	var arm := Node3D.new()
	arm.name = "Forearm"
	root.add_child(arm)
	arm.add_child(_vm_cylinder(0.047, 0.056, 0.4, -0.25, toon_material(jacket)))
	arm.add_child(_vm_cylinder(0.05, 0.05, 0.026, -0.043, toon_material(collar)))
	arm.add_child(_vm_cylinder(0.039, 0.043, 0.04, -0.012, glove_trim))

	var fist := Node3D.new()
	fist.name = "Fist"
	root.add_child(fist)
	var r: float = grip_radius
	var palm := MeshInstance3D.new()
	var palm_mesh := SphereMesh.new()
	palm_mesh.radius = 0.5
	palm_mesh.height = 1.0
	palm.mesh = palm_mesh
	palm.material_override = glove
	palm.scale = Vector3(0.034, 0.094, 0.088)
	palm.position = Vector3(r + 0.015, 0.0, 0.022)
	fist.add_child(palm)
	var heel := MeshInstance3D.new()
	heel.mesh = palm_mesh
	heel.material_override = glove
	heel.scale = Vector3(0.05, 0.07, 0.06)
	heel.position = Vector3(r + 0.02, -0.004, 0.058)
	fist.add_child(heel)
	var heights := [0.03, 0.01, -0.01, -0.029]
	var radii := [0.0105, 0.011, 0.0104, 0.0092]
	var reach := [215.0, 220.0, 215.0, 200.0]
	for i in 4:
		var fr: float = radii[i]
		var ring: float = r + fr * 0.9
		var y: float = heights[i]
		var knuckle := Vector3(r + fr * 1.8, y, -0.006)
		var angles := [55.0, 120.0, 175.0, float(reach[i])]
		var points: Array[Vector3] = [knuckle]
		for a: float in angles:
			var rad: float = deg_to_rad(a)
			points.append(Vector3(cos(rad) * ring, y - 0.002 * (a / 90.0), -sin(rad) * ring))
		for k in range(points.size() - 1):
			fist.add_child(_vm_capsule(points[k], points[k + 1], fr * (1.0 - 0.06 * k), glove))
	var thumb_points: Array[Vector3] = [Vector3(r + 0.022, 0.03, 0.04)]
	for a: float in [-30.0, -80.0, -128.0]:
		var rad: float = deg_to_rad(a)
		thumb_points.append(Vector3(cos(rad) * (r + 0.011), 0.043 + 0.004 * (a / 90.0), -sin(rad) * (r + 0.011)))
	for k in range(thumb_points.size() - 1):
		fist.add_child(_vm_capsule(thumb_points[k], thumb_points[k + 1], 0.0122 - 0.0008 * k, glove))
	var knuckles := MeshInstance3D.new()
	var knuckle_mesh := CapsuleMesh.new()
	knuckle_mesh.radius = 0.011
	knuckle_mesh.height = 0.082
	knuckles.mesh = knuckle_mesh
	knuckles.material_override = glove_trim
	knuckles.position = Vector3(r + 0.03, 0.0, -0.01)
	fist.add_child(knuckles)
	var grip := Node3D.new()
	grip.name = "Grip"
	fist.add_child(grip)
	return root


func _vm_cylinder(top: float, bottom: float, length: float, y: float, material: Material) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := CylinderMesh.new()
	mesh.top_radius = top
	mesh.bottom_radius = bottom
	mesh.height = length
	mesh.radial_segments = 20
	mi.mesh = mesh
	mi.material_override = material
	mi.position = Vector3(0.0, y, 0.0)
	return mi


func _vm_capsule(a: Vector3, b: Vector3, radius: float, material: Material) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var mesh := CapsuleMesh.new()
	var length: float = a.distance_to(b)
	mesh.radius = radius
	mesh.height = length + radius * 2.0
	mesh.radial_segments = 12
	mesh.rings = 4
	mi.mesh = mesh
	mi.material_override = material
	var y_axis: Vector3 = (b - a).normalized() if length > 1e-5 else Vector3.UP
	var x_axis: Vector3 = y_axis.cross(Vector3.UP if absf(y_axis.y) < 0.9 else Vector3.RIGHT).normalized()
	var z_axis: Vector3 = x_axis.cross(y_axis).normalized()
	mi.transform = Transform3D(Basis(x_axis, y_axis, z_axis), (a + b) * 0.5)
	return mi
