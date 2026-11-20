class_name Puppet2D
extends Node2D

## Skinned 2D mesh puppet built at runtime from rig.json + texture.png.
##
## The puppet's local origin is the centre of the feet. Every Bone2D rests at
## rotation 0, so a clip angle is applied as bone.rotation with no rest-angle
## conversion. Legs are re-solved each frame so locked feet never slide.
##
## State conventions (apply_state):
##   look_dir / lean  screen space, +1 = toward screen right.
##   facing           "left" / "front" / "right" (or -1 / 0 / 1); biases the
##                    turn only. The art is front-facing and is never mirrored
##                    unless state["mirror"] is true.
##   root_offset      ratio of body height, +y down (Godot 2D).

# Path preload instead of the MotionLibrary global name: global class names
# need the editor's class cache, which a fresh CI checkout does not have.
const MotionLibraryScript := preload("res://player/motion.gd")
const FACE_SCRIPT_PATH := "res://player/face.gd"
const FACE_METHODS: Array[String] = ["set_viseme", "set_emotion", "set_blink", "set_gaze"]

# Largest delta (degrees, either direction) the skin tolerates per bone.
const LIMITS_DEG := {
	"root": 10.0,
	"pelvis": 16.0,
	"torso": 16.0,
	"neck": 20.0,
	"head": 20.0,
	"upper_arm_l": 55.0,
	"upper_arm_r": 55.0,
	"lower_arm_l": 90.0,
	"lower_arm_r": 90.0,
	"hand_l": 40.0,
	"hand_r": 40.0,
	"upper_leg_l": 38.0,
	"upper_leg_r": 38.0,
	"lower_leg_l": 65.0,
	"lower_leg_r": 65.0,
	"foot_l": 30.0,
	"foot_r": 30.0,
}
const DEFAULT_LIMIT_DEG := 45.0

# Clip angles are deltas from each rig's own rest pose, and rest arms vary
# from a wide A-pose to hanging straight down. An "arms down" delta authored
# for the former drives the latter's hands across the body, so inward swing
# stops at these angles from vertical (or at the rest pose if already inside).
const ARM_MIN_ABDUCTION_DEG := 6.0
const FOREARM_MIN_ABDUCTION_DEG := 2.0

# Root travel allowed while feet are locked (ratio of body height).
const LOCK_SHIFT_X := 0.08
const LOCK_SHIFT_UP := 0.08
const LOCK_SHIFT_DOWN := 0.18
# Knee spread per unit of leg compression. A front-view knee bends toward the
# camera, so most of a crouch becomes foreshortening and only this share
# becomes sideways bow; a pure planar solve would fling near-straight knees out.
const KNEE_SPREAD := 0.6
const MIN_LEG_SQUASH := 0.55

const XFADE_SEC := 0.25
const CLIP_JUMP_SEC := 0.12
const MAX_STEP_SEC := 0.35
const LOOK_SMOOTH_SEC := 0.2
const LEAN_SMOOTH_SEC := 0.3
const TALK_RAMP_SEC := 0.16
const FACING_LOOK_BIAS := 0.35

const VISEME_ORDER := "XABCDEFGH"
const VISEME_OPENNESS := {
	"X": 0.0, "A": 0.05, "B": 0.35, "C": 0.6, "D": 1.0,
	"E": 0.55, "F": 0.3, "G": 0.2, "H": 0.45,
}


class LegChain:
	extends RefCounted

	var upper: Bone2D
	var lower: Bone2D
	var foot: Bone2D
	var foot_name := ""
	var parent_index := -1
	var hip_local := Vector2.ZERO
	var upper_vec := Vector2.DOWN
	var lower_vec := Vector2.DOWN
	var upper_len := 1.0
	var lower_len := 1.0
	var ankle_rest := Vector2.ZERO
	var rest_dist := 1.0
	var reach := 1.0
	# Signed rest knee offset from the hip-ankle line; positive bows outward.
	var bend_rest := 0.0
	var side := 1.0


class ArmGuard:
	extends RefCounted

	var upper_index := -1
	var lower_index := -1
	var side := 1.0
	# Rest angle away from straight down, in degrees; positive is outward.
	var upper_abduction := 0.0
	var lower_abduction := 0.0


static var _texture_cache: Dictionary = {}

var puppet_scale := 1.0
var target_height_px := 460.0

var _motion: MotionLibraryScript
var _ready_ok := false
var _body: Node2D
var _skeleton: Skeleton2D
var _texture: ImageTexture
var _origin_px := Vector2.ZERO

var _bone_index: Dictionary = {}
var _bone_names: Array[String] = []
var _bone_nodes: Array[Bone2D] = []
var _bone_parent: Array[int] = []
var _bone_rest: Array[Vector2] = []
var _bone_head: Array[Vector2] = []
var _bone_tail: Array[Vector2] = []
var _bone_limit: Array[float] = []
var _torso_child: Array[bool] = []
var _i_root := -1
var _i_torso := -1
var _i_head := -1
var _legs: Array[LegChain] = []
var _arms: Array[ArmGuard] = []
# Per-bone clip angles for the frame being posed (scratch, one slot per bone).
var _degrees: Array[float] = []

var _face: Node
var _face_args: Dictionary = {}
var _face_focus := Vector2.ZERO
var _has_face_focus := false

var _has_last := false
var _last_time := 0.0
var _cur_clip := ""
var _cur_clip_time := 0.0
var _cur_mirrored := false
var _last_base_pose: Dictionary = {}
var _from_pose: Dictionary = {}
var _xfade_active := false
var _xfade_start := 0.0
var _caller_blend := false
var _look := 0.0
var _lean := 0.0
var _talk := 0.0


## Builds the puppet from rig.json and the texture beside it. Returns false and
## leaves the puppet empty (apply_state becomes a no-op) when anything is missing.
func setup(
	rig_path: String,
	motion_lib: MotionLibraryScript,
	target_height: float = 460.0
) -> bool:
	_teardown()
	_motion = motion_lib if motion_lib != null else MotionLibraryScript.new()

	var rig: Dictionary = _read_rig(rig_path)
	if rig.is_empty():
		return false
	var texture_name: String = str(rig.get("texture", "texture.png"))
	if texture_name.strip_edges() == "":
		texture_name = "texture.png"
	_texture = _load_texture(rig_path.get_base_dir().path_join(texture_name))
	if _texture == null:
		return false

	var body_height: float = maxf(_num(rig.get("body_height", 0.0), 0.0), 1.0)
	target_height_px = maxf(target_height, 1.0)
	puppet_scale = target_height_px / body_height
	_origin_px = Vector2(_num(rig.get("center_x", 0.0), 0.0), _num(rig.get("feet_y", 0.0), 0.0))

	_body = Node2D.new()
	_body.name = "Body"
	add_child(_body)
	_skeleton = Skeleton2D.new()
	_skeleton.name = "Skeleton2D"
	_body.add_child(_skeleton)

	if not _build_bones(rig):
		push_error("Puppet2D: rig has no usable bones: %s" % rig_path)
		_teardown()
		return false
	if _build_meshes(rig) == 0:
		push_error("Puppet2D: rig has no drawable groups: %s" % rig_path)
		_teardown()
		return false
	# The skeleton goes last so a face under the head bone, sharing the head
	# mesh's z_index, draws over that mesh yet still under the arm meshes.
	_body.move_child(_skeleton, -1)
	_build_legs()
	_build_arms()
	_build_face(rig)
	_ready_ok = true
	return true


func is_ready() -> bool:
	return _ready_ok


func get_bone(bone_name: String) -> Bone2D:
	if not _bone_index.has(bone_name):
		return null
	var index: int = _bone_index[bone_name]
	return _bone_nodes[index]


func get_face() -> Node:
	return _face


func get_texture() -> ImageTexture:
	return _texture


## Poses the puppet for one frame. Recognised keys: clip, prev_clip, blend,
## clip_time, prev_clip_time, speaking, viseme, emotion, look_dir, lean,
## facing, mirror, mirror_pose, scale_mult, actor_seed. mirror_pose reflects
## the clip's pose (left and right limbs trade roles) without flipping the art.
func apply_state(state: Dictionary, time_sec: float) -> void:
	if not _ready_ok:
		return
	var step: float = time_sec - _last_time
	var continuous: bool = _has_last and step >= 0.0 and step <= MAX_STEP_SEC
	if not continuous:
		step = 0.0
	_last_time = time_sec
	_has_last = true

	var clip_name: String = str(state.get("clip", MotionLibraryScript.FALLBACK_CLIP))
	if clip_name == "":
		clip_name = MotionLibraryScript.FALLBACK_CLIP
	var clip_time: float = _num(state.get("clip_time", time_sec), time_sec)
	var speaking: bool = _num(state.get("speaking", false), 0.0) > 0.5
	var mirror: bool = _num(state.get("mirror", false), 0.0) > 0.5
	var size_mult: float = maxf(_num(state.get("scale_mult", 1.0), 1.0), 0.01)
	var actor_seed: float = _num(state.get("actor_seed", float(get_instance_id() % 8191)), 0.0)
	var look_target: float = clampf(
		_num(state.get("look_dir", 0.0), 0.0)
		+ FACING_LOOK_BIAS * _facing_value(state.get("facing", 0.0)),
		-1.0,
		1.0
	)
	var lean_target: float = clampf(_num(state.get("lean", 0.0), 0.0), -1.0, 1.0)

	# Directions arrive as hard switches; ease them so heads turn, not snap.
	if continuous:
		_look = lerpf(_look, look_target, 1.0 - exp(-step / LOOK_SMOOTH_SEC))
		_lean = lerpf(_lean, lean_target, 1.0 - exp(-step / LEAN_SMOOTH_SEC))
		_talk = move_toward(_talk, 1.0 if speaking else 0.0, step / TALK_RAMP_SEC)
	else:
		_look = look_target
		_lean = lean_target
		_talk = 1.0 if speaking else 0.0

	var base_pose: Dictionary = _resolve_base_pose(state, clip_name, clip_time, time_sec, step, continuous)
	# Mirroring flips the body node, so directions flip with it.
	var local_sign: float = -1.0 if mirror else 1.0
	var overlay: Dictionary = _motion.build_overlay(
		time_sec, actor_seed, speaking, _look * local_sign, _lean * local_sign, _talk
	)
	_body.scale = Vector2(-size_mult if mirror else size_mult, size_mult)
	_apply_pose(base_pose, overlay)
	_drive_face(state, overlay)


## Global position of "head", "chest" or "feet" (also "face", "hips", "top",
## "center"). By default the point comes from the rest pose, so a camera
## framed on it does not inherit every nod and breath; pass animated = true
## for the live bone position.
func get_focus_point(part: String = "head", animated: bool = false) -> Vector2:
	if not _ready_ok or _body == null:
		return global_position if is_inside_tree() else position
	var body_xf: Transform2D = get_transform() * _body.get_transform()
	if _body.is_inside_tree():
		body_xf = _body.get_global_transform()
	return body_xf * _focus_local(part.to_lower(), animated)


func _focus_local(part: String, animated: bool) -> Vector2:
	var top := Vector2(0.0, -target_height_px)
	match part:
		"feet", "foot", "ground":
			return Vector2.ZERO
		"top":
			return top
		"center", "centre", "body":
			return top * 0.5
		"chest", "torso":
			return _bone_point(_i_torso, 0.68, animated, top * 0.72)
		"hips", "pelvis", "waist":
			return _bone_point(_i_root, 0.0, animated, top * 0.48)
		"face", "eyes":
			if _has_face_focus and _i_head >= 0:
				var rest_offset: Vector2 = _face_focus - _bone_head[_i_head]
				if animated:
					return _chain_xform(_i_head) * rest_offset
				return _face_focus
			return _bone_point(_i_head, 1.0, animated, top * 0.9)
		_:
			return _bone_point(_i_head, 1.0, animated, top * 0.9)


## Point at `along` (0 = joint, 1 = tip) of a bone, in body space.
func _bone_point(index: int, along: float, animated: bool, fallback: Vector2) -> Vector2:
	if index < 0:
		return fallback
	var span: Vector2 = (_bone_tail[index] - _bone_head[index]) * along
	if animated:
		return _chain_xform(index) * span
	return _bone_head[index] + span


func _resolve_base_pose(
	state: Dictionary,
	clip_name: String,
	clip_time: float,
	time_sec: float,
	step: float,
	continuous: bool
) -> Dictionary:
	var mirrored: bool = _num(state.get("mirror_pose", false), 0.0) > 0.5
	var current: Dictionary = _motion.sample(clip_name, clip_time)
	if mirrored:
		current = _motion.mirror_pose(current)
	if not continuous:
		# No pose history after a gap: the caller's prev_clip/blend is all
		# there is to blend from.
		_xfade_active = false
		_caller_blend = true
	elif not _last_base_pose.is_empty():
		# A new clip, or the same clip restarted by a new beat, would pop.
		# Cross-fade from the pose actually shown last frame, which stays
		# continuous even when a change lands in the middle of another blend.
		var restarted: bool = absf(clip_time - (_cur_clip_time + step)) > CLIP_JUMP_SEC
		if clip_name != _cur_clip or restarted or mirrored != _cur_mirrored:
			_from_pose = _last_base_pose
			_xfade_active = true
			_xfade_start = time_sec - step
			_caller_blend = false
	_cur_clip = clip_name
	_cur_clip_time = clip_time
	_cur_mirrored = mirrored

	var base_pose: Dictionary = current
	if _xfade_active:
		var progress: float = clampf((time_sec - _xfade_start) / XFADE_SEC, 0.0, 1.0)
		if progress >= 1.0:
			_xfade_active = false
		else:
			base_pose = _motion.blend_poses(_from_pose, current, _smooth(progress))
	elif _caller_blend:
		var blend: float = clampf(_num(state.get("blend", 1.0), 1.0), 0.0, 1.0)
		var prev_clip: String = str(state.get("prev_clip", ""))
		if blend >= 1.0 or prev_clip == "" or prev_clip == clip_name:
			_caller_blend = false
		else:
			var prev_time: float = _num(state.get("prev_clip_time", clip_time), clip_time)
			base_pose = _motion.blend_poses(
				_motion.sample(prev_clip, prev_time), current, _smooth(blend)
			)
	_last_base_pose = base_pose
	return base_pose


func _apply_pose(pose: Dictionary, overlay: Dictionary) -> void:
	var pose_bones: Dictionary = _dict(pose.get("bones", null))
	var over_bones: Dictionary = _dict(overlay.get("bones", null))
	var lock: float = 0.0
	if pose.has("foot_lock_weight"):
		lock = clampf(_num(pose["foot_lock_weight"], 0.0), 0.0, 1.0)
	elif _num(pose.get("foot_lock", false), 0.0) > 0.5:
		lock = 1.0

	var root_shift: Vector2 = _vec(pose.get("root_offset", null)) + _vec(overlay.get("root_offset", null))
	if lock > 0.0:
		var limited := Vector2(
			clampf(root_shift.x, -LOCK_SHIFT_X, LOCK_SHIFT_X),
			clampf(root_shift.y, -LOCK_SHIFT_UP, LOCK_SHIFT_DOWN)
		)
		root_shift = root_shift.lerp(limited, lock)
	var head_shift: Vector2 = _vec(overlay.get("head_offset", null)) * target_height_px
	var chest: Vector2 = Vector2.ONE
	var chest_value: Variant = overlay.get("torso_scale", null)
	if chest_value is Vector2:
		var chest_raw: Vector2 = chest_value
		chest = chest_raw.clamp(Vector2(0.9, 0.9), Vector2(1.1, 1.1))
	var chest_inv := Vector2(1.0 / chest.x, 1.0 / chest.y)

	var count: int = _bone_nodes.size()
	for index: int in count:
		_degrees[index] = _num(pose_bones.get(_bone_names[index], 0.0), 0.0)
	_guard_arms(clampf(_num(pose.get("arm_guard", 1.0), 1.0), 0.0, 1.0))

	for index: int in count:
		# The overlay is added after the arm guard so breathing still moves
		# an arm that is resting on its inward stop.
		var degrees: float = _degrees[index] + _num(over_bones.get(_bone_names[index], 0.0), 0.0)
		var limit: float = _bone_limit[index]
		var angle: float = deg_to_rad(clampf(degrees, -limit, limit))
		var origin: Vector2 = _bone_rest[index]
		if index == _i_root:
			origin += root_shift * target_height_px
		elif index == _i_head:
			origin += head_shift
		var bone: Bone2D = _bone_nodes[index]
		if index == _i_torso:
			# Breathing: the chest swells from the waist.
			bone.transform = Transform2D(angle, chest, 0.0, origin)
		elif _torso_child[index]:
			# Undo the chest scale so the head and arms ride it without
			# changing size themselves: local basis = inverse(scale) * rotation.
			var cos_a: float = cos(angle)
			var sin_a: float = sin(angle)
			bone.transform = Transform2D(
				Vector2(cos_a * chest_inv.x, sin_a * chest_inv.y),
				Vector2(-sin_a * chest_inv.x, cos_a * chest_inv.y),
				origin
			)
		else:
			bone.transform = Transform2D(angle, origin)

	if lock > 0.0 and not _legs.is_empty():
		_plant_feet(lock, pose_bones)


## Stops clip deltas in _degrees from swinging an arm inward past its stop.
func _guard_arms(weight: float) -> void:
	if weight <= 0.0:
		return
	for arm: ArmGuard in _arms:
		# Work in "outward" degrees so both sides share one rule.
		var upper_out: float = -arm.side * _degrees[arm.upper_index]
		var upper_stop: float = minf(arm.upper_abduction, ARM_MIN_ABDUCTION_DEG) - arm.upper_abduction
		upper_out = lerpf(upper_out, maxf(upper_out, upper_stop), weight)
		_degrees[arm.upper_index] = -arm.side * upper_out
		if arm.lower_index < 0:
			continue
		var lower_out: float = -arm.side * _degrees[arm.lower_index]
		var forearm: float = arm.lower_abduction + upper_out + lower_out
		var forearm_stop: float = minf(arm.lower_abduction, FOREARM_MIN_ABDUCTION_DEG)
		if forearm < forearm_stop:
			lower_out = lerpf(lower_out, lower_out + forearm_stop - forearm, weight)
			_degrees[arm.lower_index] = -arm.side * lower_out


## Two-bone analytic IK (law of cosines) that pins both ankles to their rest
## spots and keeps the soles level. `lock` blends from FK (0) to planted (1).
func _plant_feet(lock: float, pose_bones: Dictionary) -> void:
	var parent_xforms: Array[Transform2D] = []
	for leg: LegChain in _legs:
		parent_xforms.append(_chain_xform(leg.parent_index))

	# A hip farther from its ankle than a straight leg can span would lift the
	# foot. Pull the whole body toward the ground until both legs reach.
	if _i_root >= 0:
		var shift := Vector2.ZERO
		for _pass: int in 4:
			var moved := false
			for leg_i: int in _legs.size():
				var chain: LegChain = _legs[leg_i]
				var hip: Vector2 = parent_xforms[leg_i] * chain.hip_local + shift
				var to_ankle: Vector2 = chain.ankle_rest - hip
				var dist: float = to_ankle.length()
				if dist > chain.reach + 0.0001:
					shift += to_ankle / dist * (dist - chain.reach)
					moved = true
			if not moved:
				break
		shift *= lock
		if shift != Vector2.ZERO:
			var root_bone: Bone2D = _bone_nodes[_i_root]
			root_bone.position += shift
			for leg_i: int in parent_xforms.size():
				var moved_xf: Transform2D = parent_xforms[leg_i]
				moved_xf.origin += shift
				parent_xforms[leg_i] = moved_xf

	for leg_i: int in _legs.size():
		var chain: LegChain = _legs[leg_i]
		var parent_xf: Transform2D = parent_xforms[leg_i]
		var hip: Vector2 = parent_xf * chain.hip_local
		var upper_fk: Transform2D = parent_xf * chain.upper.get_transform()
		var lower_fk: Transform2D = upper_fk * chain.lower.get_transform()
		var foot_fk: Transform2D = lower_fk * chain.foot.get_transform()

		var ankle: Vector2 = foot_fk.origin.lerp(chain.ankle_rest, lock)
		var knee: Vector2 = lower_fk.origin.lerp(_solve_knee(chain, hip, ankle), lock)
		# A planted sole stays level; the clip's own foot angle still applies.
		var foot_limit: float = _num(LIMITS_DEG.get(chain.foot_name, DEFAULT_LIMIT_DEG), DEFAULT_LIMIT_DEG)
		var sole_angle: float = deg_to_rad(
			clampf(_num(pose_bones.get(chain.foot_name, 0.0), 0.0), -foot_limit, foot_limit)
		)
		var foot_angle: float = lerp_angle(foot_fk.get_rotation(), sole_angle, lock)

		var upper_xf: Transform2D = _segment_xform(hip, knee, chain.upper_vec)
		var lower_xf: Transform2D = _segment_xform(knee, ankle, chain.lower_vec)
		var foot_xf := Transform2D(foot_angle, ankle)
		chain.upper.transform = parent_xf.affine_inverse() * upper_xf
		chain.lower.transform = upper_xf.affine_inverse() * lower_xf
		chain.foot.transform = lower_xf.affine_inverse() * foot_xf


## Knee position for a hip and ankle target, in skeleton space.
func _solve_knee(chain: LegChain, hip: Vector2, ankle: Vector2) -> Vector2:
	var to_ankle: Vector2 = ankle - hip
	var dist: float = to_ankle.length()
	if dist < 0.0001:
		return hip + chain.upper_vec * MIN_LEG_SQUASH
	var along: Vector2 = to_ankle / dist
	var total: float = chain.upper_len + chain.lower_len
	var bend: float = chain.bend_rest
	var squash: float = 1.0
	var compression: float = chain.rest_dist - dist
	if compression > 0.0:
		# Sideways bow grows linearly with compression; the rest of the
		# shortening is foreshortening of both segments.
		bend = chain.bend_rest + KNEE_SPREAD * compression
		squash = clampf((dist + 2.0 * bend * bend / total) / total, MIN_LEG_SQUASH, 1.0)
	var upper_len: float = chain.upper_len * squash
	var lower_len: float = chain.lower_len * squash
	# Law of cosines: angle at the hip between the ankle line and the thigh.
	var cos_hip: float = clampf(
		(upper_len * upper_len + dist * dist - lower_len * lower_len) / (2.0 * upper_len * dist),
		-1.0,
		1.0
	)
	var sin_hip: float = sqrt(maxf(1.0 - cos_hip * cos_hip, 0.0))
	var outward: Vector2 = Vector2(along.y, -along.x) * chain.side
	if bend < 0.0:
		outward = -outward
	return hip + (along * cos_hip + outward * sin_hip) * upper_len


## Transform at `origin` that carries the bone's rest vector onto
## `tip - origin`: a rotation plus a stretch along the bone's own axis only.
static func _segment_xform(origin: Vector2, tip: Vector2, rest_vec: Vector2) -> Transform2D:
	var rest_len: float = maxf(rest_vec.length(), 0.0001)
	var axis: Vector2 = rest_vec / rest_len
	var reach_vec: Vector2 = tip - origin
	if reach_vec.length() < rest_len * 0.02:
		reach_vec = axis * rest_len * 0.02
	var stretch: float = reach_vec.length() / rest_len - 1.0
	var turn: float = axis.angle_to(reach_vec)
	var basis_x := Vector2(1.0 + stretch * axis.x * axis.x, stretch * axis.x * axis.y)
	var basis_y := Vector2(stretch * axis.x * axis.y, 1.0 + stretch * axis.y * axis.y)
	return Transform2D(basis_x.rotated(turn), basis_y.rotated(turn), origin)


## Bone transform in skeleton space, composed from the local transforms.
func _chain_xform(index: int) -> Transform2D:
	var result := Transform2D.IDENTITY
	var cursor: int = index
	var guard := 0
	while cursor >= 0 and guard < 64:
		result = _bone_nodes[cursor].get_transform() * result
		cursor = _bone_parent[cursor]
		guard += 1
	return result


func _drive_face(state: Dictionary, overlay: Dictionary) -> void:
	if _face == null or not is_instance_valid(_face):
		return
	var viseme: Variant = state.get("viseme", "X")
	if viseme == null:
		viseme = "X"
	_face_send("set_viseme", viseme)
	_face_send("set_emotion", str(state.get("emotion", "neutral")))
	_face_send("set_blink", clampf(_num(overlay.get("blink", 0.0), 0.0), 0.0, 1.0))
	if not _face_args.has("set_gaze"):
		return
	var gaze: Vector2 = _vec(overlay.get("gaze", null))
	var types: Array[int] = _face_args["set_gaze"]
	if types.size() >= 2:
		_face.call("set_gaze", _coerce(gaze.x, types[0]), _coerce(gaze.y, types[1]))
	elif types.size() == 1 and types[0] == TYPE_VECTOR2:
		_face.call("set_gaze", gaze)
	elif types.size() == 1:
		_face.call("set_gaze", _coerce(gaze.x, types[0]))
	else:
		_face.call("set_gaze")


func _face_send(method_name: String, value: Variant) -> void:
	if not _face_args.has(method_name):
		return
	var types: Array[int] = _face_args[method_name]
	if types.is_empty():
		_face.call(method_name)
	else:
		_face.call(method_name, _coerce(value, types[0]))


## Converts a value to the type face.gd declared for that argument, so the
## puppet works whether the face takes a viseme letter or a mouth openness.
static func _coerce(value: Variant, type_id: int) -> Variant:
	match type_id:
		TYPE_FLOAT:
			return _amount(value)
		TYPE_INT:
			if value is String or value is StringName:
				var slot: int = VISEME_ORDER.find(str(value).strip_edges().to_upper())
				if slot >= 0 and str(value).strip_edges().length() == 1:
					return slot
			return roundi(_amount(value))
		TYPE_BOOL:
			return _amount(value) > 0.5
		TYPE_STRING:
			return str(value)
		TYPE_STRING_NAME:
			return StringName(str(value))
		TYPE_VECTOR2:
			if value is Vector2:
				return value
			return Vector2(_amount(value), 0.0)
		_:
			return value


static func _amount(value: Variant) -> float:
	if value is Vector2:
		var vector: Vector2 = value
		return vector.x
	if value is String or value is StringName:
		var text: String = str(value).strip_edges()
		if text.is_valid_float():
			return text.to_float()
		return _num(VISEME_OPENNESS.get(text.to_upper(), 0.0), 0.0)
	return _num(value, 0.0)


func _read_rig(rig_path: String) -> Dictionary:
	if rig_path.strip_edges() == "" or not FileAccess.file_exists(rig_path):
		push_error("Puppet2D: rig.json not found: %s" % rig_path)
		return {}
	var text: String = FileAccess.get_file_as_string(rig_path)
	var parser := JSON.new()
	var parse_error: Error = parser.parse(text)
	if parse_error != OK:
		push_error(
			"Puppet2D: %s line %d: %s"
			% [rig_path, parser.get_error_line(), parser.get_error_message()]
		)
		return {}
	var data: Variant = parser.data
	if not data is Dictionary:
		push_error("Puppet2D: rig.json root must be an object: %s" % rig_path)
		return {}
	return _dict(data)


func _load_texture(path: String) -> ImageTexture:
	var cache_key: String = path.replace("\\", "/").simplify_path()
	var cached: Variant = _texture_cache.get(cache_key, null)
	if cached is ImageTexture:
		var cached_texture: ImageTexture = cached
		return cached_texture
	var image: Image = Image.load_from_file(path)
	if image == null or image.is_empty():
		push_error("Puppet2D: texture load failed: %s" % path)
		return null
	# The source is several times larger than its on-screen size; without
	# mipmaps the ink lines shimmer as the puppet moves.
	if not image.has_mipmaps():
		var mip_error: Error = image.generate_mipmaps()
		if mip_error != OK:
			push_warning("Puppet2D: mipmaps unavailable for %s (%s)" % [path, error_string(mip_error)])
	var texture: ImageTexture = ImageTexture.create_from_image(image)
	if texture == null:
		push_error("Puppet2D: texture creation failed: %s" % path)
		return null
	_texture_cache[cache_key] = texture
	return texture


func _to_body(pixel: Vector2) -> Vector2:
	return (pixel - _origin_px) * puppet_scale


func _build_bones(rig: Dictionary) -> bool:
	var records: Dictionary = {}
	var pending: Array[String] = []
	for item: Variant in _array(rig.get("bones", null)):
		if not item is Dictionary:
			continue
		var record: Dictionary = _dict(item)
		var bone_name: String = str(record.get("name", "")).strip_edges()
		if bone_name == "" or records.has(bone_name):
			continue
		records[bone_name] = record
		pending.append(bone_name)
	if pending.is_empty():
		return false

	# Parents first, whatever order the file lists them in.
	var guard := 0
	while not pending.is_empty() and guard < 64:
		guard += 1
		var waiting: Array[String] = []
		for bone_name: String in pending:
			var record: Dictionary = records[bone_name]
			var parent_name: String = _parent_name(record)
			if parent_name != "" and records.has(parent_name) and parent_name != bone_name:
				if _bone_index.has(parent_name):
					var parent_index: int = _bone_index[parent_name]
					_add_bone(bone_name, record, parent_index)
				else:
					waiting.append(bone_name)
			else:
				_add_bone(bone_name, record, -1)
		if waiting.size() == pending.size():
			# A parent cycle: attach the rest to the skeleton rather than hang.
			for bone_name: String in waiting:
				var orphan: Dictionary = records[bone_name]
				_add_bone(bone_name, orphan, -1)
			waiting.clear()
		pending = waiting

	_i_root = _index_of("root")
	_i_torso = _index_of("torso")
	_i_head = _index_of("head")
	for index: int in _bone_nodes.size():
		_torso_child.append(_i_torso >= 0 and _bone_parent[index] == _i_torso)
		_degrees.append(0.0)
	return true


func _index_of(bone_name: String) -> int:
	if not _bone_index.has(bone_name):
		return -1
	var index: int = _bone_index[bone_name]
	return index


func _add_bone(bone_name: String, record: Dictionary, parent_index: int) -> void:
	var head: Vector2 = _to_body(_vec(record.get("head", null)))
	var tail: Vector2 = _to_body(_vec(record.get("tail", record.get("head", null))))
	var parent_node: Node = _skeleton
	var parent_head := Vector2.ZERO
	if parent_index >= 0:
		parent_node = _bone_nodes[parent_index]
		parent_head = _bone_head[parent_index]

	var bone := Bone2D.new()
	bone.name = bone_name
	bone.position = head - parent_head
	bone.rotation = 0.0
	bone.set_autocalculate_length_and_angle(false)
	parent_node.add_child(bone)
	# rest must equal the bind-pose transform or skinning collapses the mesh.
	bone.rest = bone.transform
	var span: Vector2 = tail - head
	if span.length() > 0.01:
		bone.set_length(span.length())
		bone.set_bone_angle(span.angle())

	_bone_index[bone_name] = _bone_nodes.size()
	_bone_names.append(bone_name)
	_bone_nodes.append(bone)
	_bone_parent.append(parent_index)
	_bone_rest.append(bone.position)
	_bone_head.append(head)
	_bone_tail.append(tail)
	_bone_limit.append(_num(LIMITS_DEG.get(bone_name, DEFAULT_LIMIT_DEG), DEFAULT_LIMIT_DEG))


func _build_meshes(rig: Dictionary) -> int:
	# UVs are texture pixels. Rescale them if the file on disk is not the size
	# the rig was authored against.
	var actual_size: Vector2 = _texture.get_size()
	var uv_scale := Vector2.ONE
	var declared: Vector2 = _vec(rig.get("texture_size", null))
	if declared.x > 0.0 and declared.y > 0.0:
		uv_scale = actual_size / declared

	var built := 0
	for group_value: Variant in _array(rig.get("groups", null)):
		if not group_value is Dictionary:
			continue
		var group: Dictionary = _dict(group_value)
		var vertices: Array = _array(group.get("vertices", null))
		var uv_list: Array = _array(group.get("uvs", null))
		var count: int = vertices.size()
		if count < 3:
			continue
		var points: Array[Vector2] = []
		var uvs: Array[Vector2] = []
		var has_uvs: bool = uv_list.size() == count
		for vertex_i: int in count:
			var pixel: Vector2 = _vec(vertices[vertex_i])
			points.append(_to_body(pixel))
			uvs.append((_vec(uv_list[vertex_i]) if has_uvs else pixel) * uv_scale)

		var triangles: Array = []
		for triangle_value: Variant in _array(group.get("triangles", null)):
			var corners: Array = _array(triangle_value)
			if corners.size() < 3:
				continue
			var a: int = int(_num(corners[0], -1.0))
			var b: int = int(_num(corners[1], -1.0))
			var c: int = int(_num(corners[2], -1.0))
			if a < 0 or b < 0 or c < 0 or a >= count or b >= count or c >= count:
				continue
			triangles.append(PackedInt32Array([a, b, c]))
		if triangles.is_empty():
			continue

		var poly := Polygon2D.new()
		poly.name = "Mesh_%s" % str(group.get("name", built))
		poly.z_index = int(_num(group.get("z_index", built), float(built)))
		poly.texture = _texture
		poly.texture_filter = CanvasItem.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS
		poly.polygon = PackedVector2Array(points)
		poly.uv = PackedVector2Array(uvs)
		poly.polygons = triangles
		poly.internal_vertex_count = 0
		_body.add_child(poly)
		# The skeleton path must be set before add_bone; bone paths are
		# relative to the Skeleton2D.
		poly.skeleton = poly.get_path_to(_skeleton)
		poly.clear_bones()
		var weights: Dictionary = _dict(group.get("weights", null))
		for bone_key: Variant in weights.keys():
			var bone_name: String = str(bone_key)
			var values: Array = _array(weights[bone_key])
			if not _bone_index.has(bone_name) or values.size() != count:
				push_warning("Puppet2D: skipped weights for %s in %s" % [bone_name, poly.name])
				continue
			var bone_weights: Array[float] = []
			for vertex_i: int in count:
				bone_weights.append(_num(values[vertex_i], 0.0))
			var bone_i: int = _bone_index[bone_name]
			poly.add_bone(
				_skeleton.get_path_to(_bone_nodes[bone_i]), PackedFloat32Array(bone_weights)
			)
		# add_bone() does not queue a redraw on its own.
		poly.queue_redraw()
		built += 1
	return built


func _build_legs() -> void:
	_legs.clear()
	for suffix: String in ["l", "r"]:
		var foot_name: String = "foot_%s" % suffix
		var upper_i: int = _index_of("upper_leg_%s" % suffix)
		var lower_i: int = _index_of("lower_leg_%s" % suffix)
		var foot_i: int = _index_of(foot_name)
		if upper_i < 0 or lower_i < 0 or foot_i < 0:
			continue
		if _bone_parent[lower_i] != upper_i or _bone_parent[foot_i] != lower_i:
			continue
		var hip: Vector2 = _bone_head[upper_i]
		var knee: Vector2 = _bone_head[lower_i]
		var ankle: Vector2 = _bone_head[foot_i]
		var chain := LegChain.new()
		chain.upper = _bone_nodes[upper_i]
		chain.lower = _bone_nodes[lower_i]
		chain.foot = _bone_nodes[foot_i]
		chain.foot_name = foot_name
		chain.parent_index = _bone_parent[upper_i]
		chain.hip_local = _bone_rest[upper_i]
		chain.upper_vec = knee - hip
		chain.lower_vec = ankle - knee
		chain.upper_len = chain.upper_vec.length()
		chain.lower_len = chain.lower_vec.length()
		if chain.upper_len < 0.01 or chain.lower_len < 0.01:
			continue
		chain.ankle_rest = ankle
		chain.rest_dist = hip.distance_to(ankle)
		var total: float = chain.upper_len + chain.lower_len
		chain.reach = maxf(total, chain.rest_dist)
		# Body space is centred between the hips, so x tells the sides apart.
		chain.side = -1.0 if suffix == "l" else 1.0
		if absf(hip.x) > 0.01:
			chain.side = signf(hip.x)
		var slack: float = maxf(total - chain.rest_dist, 0.0)
		var line: Vector2 = ankle - hip
		var lateral := 0.0
		if line.length() > 0.0001:
			lateral = (knee - hip).dot(Vector2(line.y, -line.x) / line.length()) * chain.side
		var bend_sign: float = -1.0 if lateral < -0.0001 else 1.0
		chain.bend_rest = bend_sign * sqrt(slack * total * 0.5)
		_legs.append(chain)


func _build_arms() -> void:
	_arms.clear()
	for suffix: String in ["l", "r"]:
		var upper_i: int = _index_of("upper_arm_%s" % suffix)
		if upper_i < 0:
			continue
		var upper_span: Vector2 = _bone_tail[upper_i] - _bone_head[upper_i]
		if upper_span.length() < 0.01:
			continue
		var arm := ArmGuard.new()
		arm.upper_index = upper_i
		arm.side = -1.0 if suffix == "l" else 1.0
		if absf(_bone_head[upper_i].x) > 0.01:
			arm.side = signf(_bone_head[upper_i].x)
		arm.upper_abduction = _abduction_deg(upper_span, arm.side)
		var lower_i: int = _index_of("lower_arm_%s" % suffix)
		if lower_i >= 0 and _bone_parent[lower_i] == upper_i:
			var lower_span: Vector2 = _bone_tail[lower_i] - _bone_head[lower_i]
			if lower_span.length() >= 0.01:
				arm.lower_index = lower_i
				arm.lower_abduction = _abduction_deg(lower_span, arm.side)
		_arms.append(arm)


## Angle of a limb away from straight down, positive toward its own side.
static func _abduction_deg(span: Vector2, side: float) -> float:
	return rad_to_deg(angle_difference(PI * 0.5, span.angle())) * -side


func _build_face(rig: Dictionary) -> void:
	var anchors: Dictionary = _dict(rig.get("face_anchors", null))
	if anchors.is_empty() or _i_head < 0:
		return
	var eye_l: Dictionary = _dict(anchors.get("eye_left", null))
	var eye_r: Dictionary = _dict(anchors.get("eye_right", null))
	if not eye_l.is_empty() and not eye_r.is_empty():
		_face_focus = _to_body((_box_center(eye_l) + _box_center(eye_r)) * 0.5)
		_has_face_focus = true

	# Loaded by path at runtime: face.gd is written in parallel and must not
	# become a parse-time dependency of the puppet.
	if not ResourceLoader.exists(FACE_SCRIPT_PATH):
		return
	var face_script: GDScript = load(FACE_SCRIPT_PATH) as GDScript
	if face_script == null or not face_script.can_instantiate():
		push_warning("Puppet2D: %s could not be loaded; face disabled" % FACE_SCRIPT_PATH)
		return
	var instance: Variant = face_script.new()
	if not instance is Node:
		push_warning("Puppet2D: %s must extend a Node; face disabled" % FACE_SCRIPT_PATH)
		if instance is Object and not instance is RefCounted:
			var stray: Object = instance
			stray.free()
		return
	var face: Node = instance
	face.name = "Face"
	_bone_nodes[_i_head].add_child(face)
	var head_px: Vector2 = _bone_head[_i_head] / puppet_scale + _origin_px
	if face.has_method("setup"):
		if Callable(face, "setup").get_argument_count() >= 4:
			face.call("setup", anchors, head_px, puppet_scale, _texture)
		else:
			face.call("setup", anchors, head_px, puppet_scale)
	# Layering belongs to the puppet: the face sits on the head mesh's layer
	# (drawn after it by tree order), so a raised hand still covers the mouth.
	if face is CanvasItem:
		var face_item: CanvasItem = face as CanvasItem
		face_item.z_as_relative = true
		face_item.z_index = _head_mesh_z(rig)
	_face = face
	_face_args.clear()
	for entry: Dictionary in face.get_method_list():
		var method_name: String = str(entry.get("name", ""))
		if not FACE_METHODS.has(method_name) or _face_args.has(method_name):
			continue
		var types: Array[int] = []
		for arg: Variant in _array(entry.get("args", null)):
			types.append(int(_num(_dict(arg).get("type", TYPE_NIL), float(TYPE_NIL))))
		_face_args[method_name] = types


func _head_mesh_z(rig: Dictionary) -> int:
	for group_value: Variant in _array(rig.get("groups", null)):
		var group: Dictionary = _dict(group_value)
		if str(group.get("name", "")) == "head":
			return int(_num(group.get("z_index", 2), 2.0))
	return 2


func _teardown() -> void:
	_ready_ok = false
	if _body != null and is_instance_valid(_body):
		if _body.get_parent() != null:
			_body.get_parent().remove_child(_body)
		_body.free()
	_body = null
	_skeleton = null
	_texture = null
	_face = null
	_face_args.clear()
	_has_face_focus = false
	_bone_index.clear()
	_bone_names.clear()
	_bone_nodes.clear()
	_bone_parent.clear()
	_bone_rest.clear()
	_bone_head.clear()
	_bone_tail.clear()
	_bone_limit.clear()
	_torso_child.clear()
	_degrees.clear()
	_legs.clear()
	_arms.clear()
	_i_root = -1
	_i_torso = -1
	_i_head = -1
	_has_last = false
	_cur_clip = ""
	_last_base_pose = {}
	_from_pose = {}
	_xfade_active = false
	_caller_blend = false
	_look = 0.0
	_lean = 0.0
	_talk = 0.0


static func _parent_name(record: Dictionary) -> String:
	var value: Variant = record.get("parent", null)
	if value == null:
		return ""
	return str(value).strip_edges()


static func _facing_value(value: Variant) -> float:
	if value is String or value is StringName:
		match str(value).strip_edges().to_lower():
			"left", "l":
				return -1.0
			"right", "r":
				return 1.0
			_:
				return 0.0
	return clampf(_num(value, 0.0), -1.0, 1.0)


static func _box_center(box: Dictionary) -> Vector2:
	return Vector2(
		_num(box.get("x", 0.0), 0.0) + _num(box.get("w", 0.0), 0.0) * 0.5,
		_num(box.get("y", 0.0), 0.0) + _num(box.get("h", 0.0), 0.0) * 0.5
	)


static func _smooth(amount: float) -> float:
	var clamped: float = clampf(amount, 0.0, 1.0)
	return clamped * clamped * (3.0 - 2.0 * clamped)


static func _num(value: Variant, fallback: float) -> float:
	match typeof(value):
		TYPE_FLOAT, TYPE_INT:
			var number: float = value
			return fallback if is_nan(number) or is_inf(number) else number
		TYPE_BOOL:
			var enabled: bool = value
			return 1.0 if enabled else 0.0
		TYPE_STRING, TYPE_STRING_NAME:
			var text: String = str(value).strip_edges()
			return text.to_float() if text.is_valid_float() else fallback
		_:
			return fallback


static func _vec(value: Variant) -> Vector2:
	if value is Vector2:
		var vector: Vector2 = value
		return vector
	var pair: Array = _array(value)
	if pair.size() >= 2:
		return Vector2(_num(pair[0], 0.0), _num(pair[1], 0.0))
	return Vector2.ZERO


static func _dict(value: Variant) -> Dictionary:
	if value is Dictionary:
		var entries: Dictionary = value
		return entries
	return {}


static func _array(value: Variant) -> Array:
	if value is Array:
		var items: Array = value
		return items
	return []
