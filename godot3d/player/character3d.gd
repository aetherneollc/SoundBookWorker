extends Node3D

## One humanoid actor loaded at runtime from a glTF/GLB file.
##
## Clips come from the model's own animations and/or an external humanoid
## library (Quaternius UAL). Each clip is baked once onto this skeleton with a
## world-space rest-delta retarget, so a Mixamo, Meshy, Rigify or UE mannequin
## skeleton can play clips authored on another rig. Walk clips are baked in
## place: the hips keep their bob and sway but lose any travel, and the stage
## moves this node along the path instead.

const SAMPLE_RATE := 30.0
const LAYER_VISIBLE := 1
const LAYER_SELF_HIDDEN := 2
const HIPS_SWAY_LIMIT := 0.08
const COLLAR_RADIUS := 0.088
const ALIGN_SPINE := false
const NOD_DEGREES := 8.0
## Elbow bend direction for the right-arm reach, in model space (model faces +Z).
const REACH_POLE := Vector3(-0.35, -1.0, -0.3)
const CLIP_CANDIDATES := {
	"idle": ["Idle_Loop", "Idle", "idle", "Standing_Idle", "Breathing_Idle"],
	"walk": ["Walk_Loop", "Walking", "walking", "Walk", "walk"],
	"talk": ["Idle_Talking_Loop", "Talking", "talk", "Talk"],
	"handoff": ["Interact", "PickUp_Table", "Give", "handoff"],
	"point": ["Interact", "Point", "Pointing", "point"],
	"pickup": ["PickUp_Table", "Interact", "pickup"],
	"nod": ["Idle_Loop", "Idle", "idle"],
	"grip": ["Sword_Idle", "Pistol_Idle_Loop", "Idle_Torch_Loop"],
}
const LOOPING_CLIPS := {"idle": true, "walk": true, "talk": true, "nod": true, "grip": true}
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

static var _library_cache: Dictionary = {}
static var _toon_shader: Shader
static var _re_side_suffix: RegEx
static var _re_side_prefix: RegEx
static var _re_segment: RegEx
static var _re_leading_zero: RegEx

var actor_id := ""
var height_m := 1.7
var natural_walk_speed := 1.0
var source_kind := "cc0_fallback"
var clip_report: Dictionary = {}

var _model_root: Node3D
var _model: Node3D
var _skeleton: Skeleton3D
var _target: Dictionary = {}
var _player: AnimationPlayer
var _clip_names: Dictionary = {}
var _clip_lengths: Dictionary = {}
var _current_key := ""
var _current_clip := ""
var _meshes: Array[MeshInstance3D] = []
var _hand_socket: Node3D
var _chest_socket: Node3D
var _head_attach: BoneAttachment3D
var _grip_pose: Dictionary = {}
var _animated_bones: Dictionary = {}
var _head_bone := -1
var _neck_bone := -1
var _model_scale := 1.0
var _first_person_hidden := false
var _look: Dictionary = {}
var _palette: Dictionary = {}
var holding := false


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
	if not scene is Node3D:
		var wrapper := Node3D.new()
		wrapper.add_child(scene)
		return wrapper
	return scene as Node3D


## glTF defaults metallicFactor to 1, and TRELLIS/Meshy files omit it, so
## their surfaces render as chrome. Painted props and people are dielectric.
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


## Loads the model and bakes its clips. ``config`` is one entry of
## ``stage3d.json`` ``actors``.
func setup(config: Dictionary) -> bool:
	actor_id = str(config.get("id", name))
	height_m = maxf(float(config.get("height_m", 1.7)), 0.5)
	source_kind = str(config.get("source", "cc0_fallback"))
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
	_head_bone = int(_target.human.get("head", -1))
	_neck_bone = int(_target.human.get("neck", -1))
	_apply_look()
	_build_sockets()
	_build_animation_player(config, own_rig)
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
		var pairs: Dictionary = _bone_pairs(chosen_rig.human, _target.human)
		if pairs.is_empty():
			continue
		if clip == "grip":
			_grip_pose = _bake_grip(chosen_rig, anim, pairs)
			clip_report[clip] = "%s:%s" % [chosen_source, chosen_name]
			continue
		var baked: Animation = _bake(chosen_rig, anim, pairs, LOOPING_CLIPS.has(clip), skeleton_path)
		anim_lib.add_animation(StringName(clip), baked)
		_clip_names[clip] = clip
		_clip_lengths[clip] = baked.length
		clip_report[clip] = "%s:%s" % [chosen_source, chosen_name]
		if clip == "walk":
			natural_walk_speed = _walk_speed(chosen_rig, chosen_name, rm_library)
	_player.add_animation_library("", anim_lib)


func _bake(src: Dictionary, anim: Animation, pairs: Dictionary, loop: bool, skeleton_path: String) -> Animation:
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
	for k in steps + 1:
		var time: float = minf(anim.length, float(k) / SAMPLE_RATE)
		var src_pose: Array[Transform3D] = _posed_globals(src, anim, time)
		var g_rot: Array[Quaternion] = []
		g_rot.resize(tgt_count)
		var g_full: Array[Transform3D] = []
		g_full.resize(tgt_count)
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


## World rotations of the source bones in a pose that matches the target's
## rest: each mapped limb is swung so it points where the target's does. A
## T-pose library then drives an A-pose rig without the arms overshooting.
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


## Rest offset from ``bone`` to a descendant ``child``, in ``bone``'s local space.
static func _chain_offset(rig: Dictionary, bone: int, child: int) -> Vector3:
	var parents: PackedInt32Array = rig.parents
	var rests: Array[Transform3D] = rig.rests
	var xform := Transform3D.IDENTITY
	var walk: int = child
	while walk >= 0 and walk != bone:
		xform = Transform3D(Basis(rests[walk].basis.get_rotation_quaternion()), rests[walk].origin) * xform
		walk = parents[walk]
	return xform.origin


func _bake_grip(src: Dictionary, anim: Animation, pairs: Dictionary) -> Dictionary:
	var src_pose: Array[Transform3D] = _posed_globals(src, anim, 0.0)
	var s_src: Quaternion = (src.skel_xform as Transform3D).basis.get_rotation_quaternion()
	var s_tgt: Quaternion = (_target.skel_xform as Transform3D).basis.get_rotation_quaternion()
	var result := {}
	var human: Dictionary = _target.human
	var hand: int = int(human.get("r_hand", -1))
	if hand < 0 or not pairs.has(hand):
		return result
	for finger: String in GRIP_FINGERS:
		for digit in [1, 2, 3]:
			var key := "r_%s%d" % [finger, digit]
			if not human.has(key):
				continue
			var b: int = int(human[key])
			if not pairs.has(b):
				continue
			var p: int = (_target.parents as PackedInt32Array)[b]
			var sb: int = int(pairs[b])
			var sp: int = (src.parents as PackedInt32Array)[sb]
			if sp < 0 or p < 0:
				continue
			# Finger pose relative to its parent, carried through the rest delta.
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


func _apply_rest_pose() -> void:
	if _player != null and _clip_names.has("idle"):
		_player.play("idle", 0.0)
		_player.advance(0.0)
		_current_clip = "idle"
		_current_key = "idle"


# ----------------------------------------------------------- per frame


func has_clip(clip: String) -> bool:
	return _clip_names.has(clip)


func clip_length(clip: String) -> float:
	return float(_clip_lengths.get(clip, 0.0))


## ``clip_key`` changes when a beat restarts the same clip.
func update_animation(clip: String, clip_key: String, speed: float, delta: float, blend: float = 0.25) -> void:
	if _player == null:
		return
	var resolved: String = clip if _clip_names.has(clip) else ("idle" if _clip_names.has("idle") else "")
	if resolved == "":
		return
	if clip_key != _current_key:
		var restart: bool = resolved == _current_clip and not LOOPING_CLIPS.has(resolved)
		if resolved != _current_clip or restart:
			_player.play(StringName(resolved), blend if _current_clip != "" else 0.0)
			if restart:
				_player.seek(0.0, true)
		_current_clip = resolved
		_current_key = clip_key
	_player.speed_scale = clampf(speed, 0.05, 3.0)
	_player.advance(delta)


## Overlays that run after the clip pose: right-arm reach, hand grip, nod.
## ``reach_target`` is in world space; ``reach_weight`` 0 leaves the clip alone.
func apply_overlays(nod_phase: float, nod_weight: float, reach_target: Vector3 = Vector3.ZERO, reach_weight: float = 0.0) -> void:
	if _skeleton == null:
		return
	if reach_weight > 0.001:
		_reach_right_arm(_skeleton.global_transform.affine_inverse() * reach_target, clampf(reach_weight, 0.0, 1.0))
	if holding:
		for bone: Variant in _grip_pose.keys():
			var b: int = int(bone)
			if _animated_bones.has(b):
				var current: Quaternion = _skeleton.get_bone_pose_rotation(b)
				_skeleton.set_bone_pose_rotation(b, current.slerp(_grip_pose[b], 0.85))
	if nod_weight > 0.001 and _head_bone >= 0 and _animated_bones.has(_head_bone):
		var angle: float = deg_to_rad(NOD_DEGREES) * nod_weight * maxf(0.0, sin(nod_phase * TAU))
		_pitch_bone(_head_bone, angle)
		if _neck_bone >= 0 and _animated_bones.has(_neck_bone):
			_pitch_bone(_neck_bone, angle * 0.45)


## Analytic two-bone IK: the upper arm swings the elbow into the plane of the
## pole, then the forearm aims the wrist at ``target`` (skeleton space).
func _reach_right_arm(target: Vector3, weight: float) -> void:
	var human: Dictionary = _target.human
	var upper: int = int(human.get("r_upper_arm", -1))
	var lower: int = int(human.get("r_lower_arm", -1))
	var hand: int = int(human.get("r_hand", -1))
	if upper < 0 or lower < 0 or hand < 0:
		return
	if not _animated_bones.has(upper) or not _animated_bones.has(lower):
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
	var pole: Vector3 = ((_target.skel_xform as Transform3D).basis.inverse() * REACH_POLE).normalized()
	var v: Vector3 = (pole - u * pole.dot(u)).normalized()
	var cos_a: float = clampf((a * a + d * d - b * b) / (2.0 * a * d), -1.0, 1.0)
	var elbow: Vector3 = s + u * a * cos_a + v * a * sqrt(1.0 - cos_a * cos_a)
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


func _pitch_bone(bone: int, angle: float) -> void:
	var global_pose: Transform3D = _skeleton.get_bone_global_pose(bone)
	var lateral_skel: Vector3 = (_target.skel_xform as Transform3D).basis.inverse() * Vector3.RIGHT
	var axis_local: Vector3 = (global_pose.basis.inverse() * lateral_skel).normalized()
	if axis_local.length_squared() < 0.5:
		return
	var current: Quaternion = _skeleton.get_bone_pose_rotation(bone)
	_skeleton.set_bone_pose_rotation(bone, current * Quaternion(axis_local, angle))


func head_world_position() -> Vector3:
	if _skeleton == null or _head_bone < 0:
		return global_position + Vector3.UP * height_m * 0.93
	return _skeleton.global_transform * _skeleton.get_bone_global_pose(_head_bone).origin


func hand_world_position() -> Vector3:
	if _hand_socket == null:
		return global_position
	return _hand_socket.global_position


# ------------------------------------------------------------ sockets


func get_hand_socket() -> Node3D:
	return _hand_socket


func _make_attachment(bone: int, label: String) -> BoneAttachment3D:
	var attach := BoneAttachment3D.new()
	attach.name = label
	_skeleton.add_child(attach)
	attach.bone_name = _skeleton.get_bone_name(bone)
	attach.bone_idx = bone
	return attach


func _build_sockets() -> void:
	var human: Dictionary = _target.human
	var hand: int = int(human.get("r_hand", -1))
	if hand >= 0:
		var attach := _make_attachment(hand, "RightHandSocket")
		_hand_socket = Node3D.new()
		_hand_socket.name = "Grip"
		attach.add_child(_hand_socket)
		_hand_socket.transform = _grip_transform(hand)
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


## Transform that maps model-scene space into a bone's local space at rest.
func _scene_to_bone(bone: int) -> Transform3D:
	var rest_scene: Transform3D = (_target.skel_xform as Transform3D) * (_target.globals[bone] as Transform3D)
	return rest_scene.affine_inverse()


func _grip_transform(hand: int) -> Transform3D:
	var human: Dictionary = _target.human
	var hand_global: Transform3D = _target.globals[hand]
	var inv: Transform3D = hand_global.affine_inverse()
	var index_local := Vector3(0.0, 0.08, 0.0)
	var pinky_local := Vector3(0.0, 0.07, 0.03)
	var middle_local := Vector3(0.0, 0.09, 0.0)
	var thumb_local := Vector3(0.03, 0.03, 0.0)
	if human.has("r_index1"):
		index_local = inv * (_target.globals[int(human["r_index1"])] as Transform3D).origin
	if human.has("r_pinky1"):
		pinky_local = inv * (_target.globals[int(human["r_pinky1"])] as Transform3D).origin
	if human.has("r_middle1"):
		middle_local = inv * (_target.globals[int(human["r_middle1"])] as Transform3D).origin
	if human.has("r_thumb1"):
		thumb_local = inv * (_target.globals[int(human["r_thumb1"])] as Transform3D).origin
	if not human.has("r_middle1"):
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
	var origin: Vector3 = finger_dir * length * 1.25 + palm * length * 0.35
	# The grip's +Y runs through the fist toward the thumb, +Z out of the palm.
	var basis := Basis(across.cross(palm).normalized(), across, palm)
	var unscale: float = 1.0 / maxf(_model_scale, 0.001)
	return Transform3D(basis.orthonormalized().scaled(Vector3.ONE * unscale), origin)


## Meshy's Mixamo template stops at the hand (one fingertip leaf). The palm is
## taken as facing down in the rest A-pose with the thumb forward (+Z), and the
## grip sits about halfway down the fingers. ``look.grip_offset`` nudges it.
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


func _build_hair() -> void:
	if _head_attach == null:
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
	if _chest_socket == null or not bool(_look.get("badge", false)):
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


## Paints the uniform into vertex colours, by the bone each vertex follows
## most. Alpha 1 is cloth, alpha 0 keeps the skin texture.
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


## Unwelds the surface so every triangle carries one uniform colour: hard
## cel-style edges instead of colours smeared across big triangles.
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


## Moves every body mesh onto render layer 2 so a camera without layer 2
## never sees this actor's own head. Props held by the actor stay on layer 1.
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


## A forearm, cuff and gripping hand in this actor's colours, for the
## first-person camera. Returns the root; its "Grip" child holds the prop.
func build_viewmodel() -> Node3D:
	var root := Node3D.new()
	root.name = "Viewmodel_%s" % actor_id
	var skin_color := Color(str(_look.get("viewmodel_skin", "#e3b996")))
	var jacket: Color = _palette.get("jacket", Color("#2c5aa0"))
	var collar: Color = _palette.get("collar", Color("#f08a24"))
	var arm := Node3D.new()
	arm.name = "Forearm"
	root.add_child(arm)
	var sleeve := MeshInstance3D.new()
	var sleeve_mesh := CylinderMesh.new()
	sleeve_mesh.top_radius = 0.043
	sleeve_mesh.bottom_radius = 0.052
	sleeve_mesh.height = 0.36
	sleeve.mesh = sleeve_mesh
	sleeve.material_override = toon_material(jacket)
	sleeve.position = Vector3(0.0, -0.2, 0.0)
	arm.add_child(sleeve)
	var cuff := MeshInstance3D.new()
	var cuff_mesh := CylinderMesh.new()
	cuff_mesh.top_radius = 0.046
	cuff_mesh.bottom_radius = 0.046
	cuff_mesh.height = 0.035
	cuff.mesh = cuff_mesh
	cuff.material_override = toon_material(collar)
	cuff.position = Vector3(0.0, -0.035, 0.0)
	arm.add_child(cuff)
	var hand := MeshInstance3D.new()
	var hand_mesh := BoxMesh.new()
	hand_mesh.size = Vector3(0.085, 0.09, 0.05)
	hand.mesh = hand_mesh
	hand.material_override = toon_material(skin_color)
	hand.position = Vector3(0.0, 0.035, 0.0)
	arm.add_child(hand)
	var fingers := MeshInstance3D.new()
	var finger_mesh := CapsuleMesh.new()
	finger_mesh.radius = 0.024
	finger_mesh.height = 0.095
	fingers.mesh = finger_mesh
	fingers.material_override = toon_material(skin_color)
	fingers.position = Vector3(0.0, 0.07, 0.035)
	fingers.rotation = Vector3(0.0, 0.0, PI * 0.5)
	arm.add_child(fingers)
	var thumb := MeshInstance3D.new()
	var thumb_mesh := CapsuleMesh.new()
	thumb_mesh.radius = 0.016
	thumb_mesh.height = 0.06
	thumb.mesh = thumb_mesh
	thumb.material_override = toon_material(skin_color)
	thumb.position = Vector3(-0.045, 0.06, 0.012)
	thumb.rotation = Vector3(0.0, 0.0, deg_to_rad(-30.0))
	arm.add_child(thumb)
	var grip := Node3D.new()
	grip.name = "Grip"
	grip.position = Vector3(0.0, 0.075, 0.02)
	arm.add_child(grip)
	return root
