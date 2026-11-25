class_name FacialAnimationController
extends Node

## Reusable, data-driven facial animation, lip-sync, blinking, gaze, and
## listener micro-reaction controller.
##
## Inspects a character's MeshInstance3D nodes on attach and grades capability:
##   - "blendshape":     model exposes one or more target BlendShapes
##   - "atlas_fallback": model uses a 2x2 FaceMat expression atlas
##   - "head_only":      head/neck gaze, listening nods & tilts only
##
## Never assumes every character model has BlendShapes.

const CANONICAL_SHAPES: Array[String] = [
	"jawOpen",
	"mouthFunnel",
	"mouthSmile",
	"mouthFrown",
	"eyeBlink",
	"browDown",
	"browUp",
]

## Lowercased alias tokens matched against Mesh blend shape names.
const SHAPE_ALIASES := {
	"jawOpen": [
		"jawopen", "jaw_open", "mouthopen", "mouth_open",
		"viseme_aa", "vrc.v_aa", "aa", "ah", "mouth_a",
	],
	"mouthFunnel": [
		"mouthfunnel", "mouth_funnel", "mouthpucker", "mouth_pucker",
		"viseme_ou", "viseme_oh", "vrc.v_ou", "vrc.v_oh", "ou", "oh", "oo",
	],
	"mouthSmile": [
		"mouthsmile", "mouth_smile", "mouthsmileleft", "mouthsmileright",
		"mouth_smile_l", "mouth_smile_r", "smile", "happy", "joy",
	],
	"mouthFrown": [
		"mouthfrown", "mouth_frown", "mouthfrownleft", "mouthfrownright",
		"mouth_frown_l", "mouth_frown_r", "frown", "sad",
	],
	"eyeBlink": [
		"eyeblink", "eye_blink", "eyeblinkleft", "eyeblinkright",
		"eye_blink_l", "eye_blink_r", "blink", "eyesclosed", "eye_closed",
	],
	"browDown": [
		"browdown", "brow_down", "browdownleft", "browdownright",
		"brow_down_l", "brow_down_r", "browfurrow", "brow_furrow", "angry",
	],
	"browUp": [
		"browup", "brow_up", "browinnerup", "brow_inner_up",
		"browouterupleft", "browouterupright", "brow_outer_up_l", "brow_outer_up_r",
		"surprise", "browsup",
	],
}

const EMOTIONS: Array[String] = [
	"neutral",
	"happy",
	"sad",
	"angry",
	"fear",
	"surprised",
	"worried",
	"confident",
]

const MAX_SPEECH_JAW_OPEN := 0.46
const MAX_SHOUT_JAW_OPEN := 0.56
const MAX_MOUTH_FUNNEL := 0.42
const MAX_BROW_WEIGHT := 0.76
const MAX_LIP_EMOTION_WEIGHT := 0.65
const MAX_IDLE_EMOTION_JAW := 0.16

## Target BlendShape weights + subtle head/blink modifiers per emotion.
const EMOTION_PROFILES := {
	"neutral": {
		"shapes": {},
		"head_pitch_deg": 0.0,
		"head_tilt_deg": 0.0,
		"blink_rate_mult": 1.0,
		"listener_nod_mult": 1.0,
		"listener_shapes": {"browUp": 0.08},
	},
	"happy": {
		"shapes": {"mouthSmile": 0.62, "browUp": 0.16, "eyeBlink": 0.06},
		"head_pitch_deg": -1.5,
		"head_tilt_deg": 2.0,
		"blink_rate_mult": 1.05,
		"listener_nod_mult": 1.25,
		"listener_shapes": {"mouthSmile": 0.22, "browUp": 0.10},
	},
	"sad": {
		"shapes": {"mouthFrown": 0.56, "browUp": 0.34, "browDown": 0.14},
		"head_pitch_deg": 5.5,
		"head_tilt_deg": -2.5,
		"blink_rate_mult": 0.85,
		"listener_nod_mult": 0.75,
		"listener_shapes": {"mouthFrown": 0.18, "browUp": 0.20},
	},
	"angry": {
		"shapes": {"browDown": 0.74, "mouthFrown": 0.46, "jawOpen": 0.02},
		"head_pitch_deg": 3.0,
		"head_tilt_deg": 0.0,
		"blink_rate_mult": 0.78,
		"listener_nod_mult": 0.55,
		"listener_shapes": {"browDown": 0.28, "mouthFrown": 0.14},
	},
	"fear": {
		"shapes": {"browUp": 0.68, "jawOpen": 0.10, "mouthFrown": 0.32, "mouthFunnel": 0.12},
		"head_pitch_deg": -1.0,
		"head_tilt_deg": -3.0,
		"blink_rate_mult": 1.35,
		"listener_nod_mult": 0.5,
		"listener_shapes": {"browUp": 0.24, "mouthFrown": 0.12},
	},
	"surprised": {
		"shapes": {"browUp": 0.74, "jawOpen": 0.16, "mouthFunnel": 0.18},
		"head_pitch_deg": -3.0,
		"head_tilt_deg": 1.5,
		"blink_rate_mult": 0.75,
		"listener_nod_mult": 0.7,
		"listener_shapes": {"browUp": 0.28, "jawOpen": 0.06},
	},
	"worried": {
		"shapes": {"browUp": 0.44, "browDown": 0.38, "mouthFrown": 0.44},
		"head_pitch_deg": 2.5,
		"head_tilt_deg": 3.5,
		"blink_rate_mult": 1.2,
		"listener_nod_mult": 0.9,
		"listener_shapes": {"browUp": 0.20, "browDown": 0.14, "mouthFrown": 0.14},
	},
	"confident": {
		"shapes": {"mouthSmile": 0.36, "browDown": 0.12},
		"head_pitch_deg": -3.0,
		"head_tilt_deg": -1.5,
		"blink_rate_mult": 0.9,
		"listener_nod_mult": 1.15,
		"listener_shapes": {"mouthSmile": 0.18},
	},
}

## Viseme hints -> [jawOpen multiplier, mouthFunnel target]
const VISEME_HINTS := {
	"sil": Vector2(0.0, 0.0),
	"mbp": Vector2(0.06, 0.0),
	"fv": Vector2(0.16, 0.08),
	"th": Vector2(0.24, 0.05),
	"dd": Vector2(0.32, 0.06),
	"kk": Vector2(0.38, 0.12),
	"ee": Vector2(0.42, 0.05),
	"ih": Vector2(0.48, 0.10),
	"aa": Vector2(0.92, 0.10),
	"oh": Vector2(0.68, 0.62),
	"ou": Vector2(0.45, 0.85),
	"oo": Vector2(0.38, 0.90),
}

const FACE_CELLS := {
	"neutral": Vector2(0.0, 0.0),
	"blink": Vector2(0.5, 0.0),
	"talk_mid": Vector2(0.0, 0.5),
	"talk_open": Vector2(0.5, 0.5),
}

const TALK_SEQUENCE: Array[String] = [
	"talk_mid", "talk_open", "neutral", "talk_mid",
	"talk_open", "talk_mid", "neutral", "talk_open",
]
const TALK_STEPS: Array[int] = [4, 5, 4, 5, 4, 4, 5, 4]

const GAZE_YAW_LIMIT_DEG := 28.0
const GAZE_PITCH_LIMIT_DEG := 14.0
const GAZE_NECK_SHARE := 0.35
const BLINK_MIN_PERIOD_S := 3.0
const BLINK_MAX_PERIOD_S := 4.2
const BLINK_CLOSE_S := 0.083  # ~2 frames at 24fps
const BLINK_OPEN_S := 0.125   # ~3 frames at 24fps
const GAZE_SHIFT_BLINK_DEG := 18.0
const GAZE_SHIFT_BLINK_COOLDOWN_S := 0.85

var actor_id := ""
var capability_grade := "head_only"
var face_state := "neutral"
var active_emotion := "neutral"
var emotion_intensity := 1.0

var _character: Node3D
var _skeleton: Skeleton3D
var _meshes: Array[MeshInstance3D] = []
var _face_materials: Array[ShaderMaterial] = []
var _face_has_atlas := false
var _head_bone := -1
var _neck_bone := -1
var _rig_info: Dictionary = {}

## canonical_name -> Array of {mesh: MeshInstance3D, index: int, raw_name: String}
var _shape_bindings: Dictionary = {}
var _discovered_shapes: Dictionary = {}
var _current_weights: Dictionary = {}

## Smoothly blended emotion weight map across all 8 emotions.
var _emotion_weights: Dictionary = {}
var _target_emotion := "neutral"
var _target_intensity := 1.0
var _emotion_transition_s := 0.35

## Lip-sync smoothed channels (attack/release)
var _jaw_smoothed := 0.0
var _funnel_smoothed := 0.0
var _is_speaking := false
var _last_speech_energy := 0.0

## Blink state
var _blink_seed := 0
var _blink_timer := 0.0
var _next_blink_interval := 3.4
var _blink_cycle := 0
var _blink_progress := -1.0  # < 0 means eyes open
var _blink_weight := 0.0
var _gaze_blink_cooldown := 0.0

## Gaze state (body-local damped angles in radians)
var _gaze_target_world := Vector3.ZERO
var _gaze_target_weight := 0.0
var _gaze_weight := 0.0
var _gaze_yaw := 0.0
var _gaze_pitch := 0.0
var _prev_desired_yaw := 0.0
var _prev_desired_pitch := 0.0
var _has_prev_gaze := false

## Listener micro-reaction state
var _is_listening := false
var _listener_weight := 0.0
var _listener_speaker_pos := Vector3.ZERO
var _listener_speaker_energy := 0.0
var _listener_timer := 0.0
var _listener_nod_timer := 0.0
var _listener_nod_duration := 0.55
var _listener_nod_interval := 2.8
var _listener_nod_angle_rad := 0.0
var _listener_tilt_rad := 0.0

## Time accumulator for smooth deterministic micro-motion
var _elapsed_s := 0.0


func _init() -> void:
	for emo: String in EMOTIONS:
		_emotion_weights[emo] = 1.0 if emo == "neutral" else 0.0
	for shape_name: String in CANONICAL_SHAPES:
		_current_weights[shape_name] = 0.0


## Attach to a character and inspect its meshes for BlendShapes or FaceMat atlas.
func attach(
	character: Node3D,
	skeleton: Skeleton3D,
	meshes: Array[MeshInstance3D],
	face_materials: Array[ShaderMaterial],
	face_has_atlas: bool,
	head_bone: int,
	neck_bone: int,
	p_actor_id: String,
	rig_info: Dictionary = {}
) -> String:
	_character = character
	_skeleton = skeleton
	_meshes = meshes.duplicate()
	_face_materials = face_materials.duplicate()
	_face_has_atlas = face_has_atlas
	_head_bone = head_bone
	_neck_bone = neck_bone
	actor_id = p_actor_id
	_rig_info = rig_info

	_blink_seed = absi(hash(actor_id if actor_id != "" else str(get_instance_id())))
	_next_blink_interval = _compute_blink_interval(0)
	_blink_timer = fposmod(float(_blink_seed % 1000) / 1000.0 * _next_blink_interval, _next_blink_interval)
	_listener_nod_interval = 2.4 + float(_blink_seed % 140) / 100.0

	_inspect_blendshapes()
	if not _shape_bindings.is_empty():
		capability_grade = "blendshape"
	elif _face_has_atlas and not _face_materials.is_empty():
		capability_grade = "atlas_fallback"
	else:
		capability_grade = "head_only"
	return capability_grade


## Inspects every MeshInstance3D on the character for canonical BlendShapes.
func _inspect_blendshapes() -> void:
	_shape_bindings.clear()
	_discovered_shapes.clear()
	for mi: MeshInstance3D in _meshes:
		if mi == null or mi.mesh == null:
			continue
		var count: int = mi.mesh.get_blend_shape_count()
		if count <= 0:
			continue
		for idx in count:
			var raw_name: String = str(mi.mesh.get_blend_shape_name(idx))
			var canonical: String = _match_canonical_shape(raw_name)
			if canonical == "":
				continue
			if not _shape_bindings.has(canonical):
				_shape_bindings[canonical] = []
				_discovered_shapes[canonical] = []
			(_shape_bindings[canonical] as Array).append({
				"mesh": mi,
				"index": idx,
				"raw_name": raw_name,
			})
			(_discovered_shapes[canonical] as Array).append(raw_name)


static func _match_canonical_shape(raw_name: String) -> String:
	var norm: String = raw_name.strip_edges().to_lower().replace(" ", "").replace("-", "_")
	var compact: String = norm.replace("_", "").replace(".", "")
	for canonical: String in CANONICAL_SHAPES:
		if compact == canonical.to_lower():
			return canonical
		var aliases: Array = SHAPE_ALIASES.get(canonical, [])
		for alias_v: Variant in aliases:
			var alias: String = str(alias_v)
			if norm == alias or compact == alias.replace("_", "").replace(".", ""):
				return canonical
	return ""


# -------------------------------------------------------------- public API


func get_capability_grade() -> String:
	return capability_grade


func get_discovered_blendshapes() -> Dictionary:
	return _discovered_shapes.duplicate(true)


func get_blendshape_weights() -> Dictionary:
	return _current_weights.duplicate()


func get_emotion_state() -> Dictionary:
	return {
		"active": active_emotion,
		"target": _target_emotion,
		"intensity": snappedf(emotion_intensity, 0.001),
		"weights": _emotion_weights.duplicate(),
	}


## Smoothly transitions to one of the 8 supported emotions.
func set_emotion(emotion: String, intensity: float = 1.0, transition_s: float = 0.35) -> void:
	var key: String = emotion.strip_edges().to_lower()
	if not EMOTION_PROFILES.has(key):
		key = "neutral"
	_target_emotion = key
	_target_intensity = clampf(intensity, 0.0, 1.5)
	_emotion_transition_s = maxf(transition_s, 0.04)


## Sets the desired world-space gaze target (partner's eyes, prop, landmark).
func set_gaze_target(world_target: Vector3, weight: float = 0.85) -> void:
	_gaze_target_world = world_target
	_gaze_target_weight = clampf(weight, 0.0, 1.0)


## Enables or disables attentive listener micro-reactions toward a speaker.
func set_listening(
	is_listening: bool,
	speaker_world_pos: Vector3 = Vector3.ZERO,
	listener_emotion: String = "",
	speaker_energy: float = 0.0
) -> void:
	_is_listening = is_listening
	_listener_speaker_pos = speaker_world_pos
	_listener_speaker_energy = clampf(speaker_energy, 0.0, 1.0)
	if is_listening and listener_emotion != "":
		set_emotion(listener_emotion, 0.75, 0.45)


## Forces a specific atlas cell (used by QC face sheets and stills).
func show_face(state: String) -> void:
	face_state = state
	var offset: Vector2 = FACE_CELLS.get(state, Vector2.ZERO)
	for material: ShaderMaterial in _face_materials:
		if material != null:
			material.set_shader_parameter("cell_offset", offset)


## Advances emotions, TTS lip-sync, natural blinking, and listener micro-reactions.
func update_expression(
	frame_index: int,
	fps: int,
	is_speaking: bool,
	speech_energy: float = -1.0,
	viseme_hint: String = "",
	dt: float = -1.0
) -> void:
	var rate: float = float(maxi(fps, 1))
	var step_dt: float = dt if dt > 0.0 else (1.0 / rate)
	_elapsed_s += step_dt
	_is_speaking = is_speaking

	_step_emotions(step_dt)
	_step_lipsync(frame_index, is_speaking, speech_energy, viseme_hint, step_dt)
	_step_listening(step_dt)
	_step_blinking(step_dt)
	_compose_and_apply_shapes(frame_index, is_speaking, speech_energy)


## Applies damped neck+head gaze toward target (or speaker when listening),
## plus emotion head pitch/tilt and listener micro-nod.
## Call after skeleton animation evaluation and before arm IK overlays.
func apply_head_gaze(
	eye_world_pos: Vector3,
	animated_bones: Dictionary,
	dt: float = 1.0 / 24.0
) -> void:
	if _skeleton == null or _character == null or _head_bone < 0:
		return
	if not animated_bones.is_empty() and not animated_bones.has(_head_bone):
		return

	var target_world: Vector3 = _gaze_target_world
	var desired_weight: float = _gaze_target_weight
	if desired_weight < 0.05 and _is_listening and _listener_speaker_pos != Vector3.ZERO:
		target_world = _listener_speaker_pos
		desired_weight = 0.78 * _listener_weight

	var blend_alpha: float = 1.0 - exp(-dt * 8.5)
	_gaze_weight = lerpf(_gaze_weight, clampf(desired_weight, 0.0, 1.0), blend_alpha)

	var body: Basis = _character.global_transform.basis.orthonormalized()
	var sg: Basis = _skeleton.global_transform.basis.orthonormalized()
	var skel_xform: Transform3D = _rig_info.get("skel_xform", Transform3D.IDENTITY)
	var globals: Array = _rig_info.get("globals", [])

	var yaw_lim: float = deg_to_rad(GAZE_YAW_LIMIT_DEG)
	var pitch_lim: float = deg_to_rad(GAZE_PITCH_LIMIT_DEG)

	var emo_pitch: float = 0.0
	var emo_tilt: float = 0.0
	for emo: String in EMOTIONS:
		var w: float = float(_emotion_weights.get(emo, 0.0))
		if w > 0.001:
			var prof: Dictionary = EMOTION_PROFILES[emo]
			emo_pitch += deg_to_rad(float(prof.get("head_pitch_deg", 0.0))) * w
			emo_tilt += deg_to_rad(float(prof.get("head_tilt_deg", 0.0))) * w

	var extra_pitch: float = emo_pitch * emotion_intensity + _listener_nod_angle_rad
	var extra_tilt: float = emo_tilt * emotion_intensity + _listener_tilt_rad

	if _gaze_weight >= 0.002 and globals.size() > _head_bone:
		var head_pose: Transform3D = _skeleton.get_bone_global_pose(_head_bone)
		var forward_skel: Vector3 = skel_xform.basis.inverse() * Vector3.BACK
		var rest_head: Basis = (globals[_head_bone] as Transform3D).basis.orthonormalized()
		var facing_skel: Vector3 = head_pose.basis.orthonormalized() * (rest_head.inverse() * forward_skel)
		var current: Vector3 = body.inverse() * (sg * facing_skel).normalized()
		var to_target: Vector3 = body.inverse() * (target_world - eye_world_pos)
		if to_target.length_squared() >= 0.0004:
			var desired_yaw: float = clampf(atan2(to_target.x, to_target.z), -yaw_lim, yaw_lim)
			var horiz: float = maxf(Vector2(to_target.x, to_target.z).length(), 0.0001)
			var desired_pitch: float = clampf(atan2(to_target.y, horiz), -pitch_lim, pitch_lim)

			# Trigger a subtle saccade micro-blink on large gaze shifts.
			if _has_prev_gaze and _gaze_blink_cooldown <= 0.0:
				var shift_deg: float = rad_to_deg(
					Vector2(desired_yaw - _prev_desired_yaw, desired_pitch - _prev_desired_pitch).length()
				)
				if shift_deg >= GAZE_SHIFT_BLINK_DEG and _blink_progress < 0.0:
					_trigger_blink()
					_gaze_blink_cooldown = GAZE_SHIFT_BLINK_COOLDOWN_S
			_prev_desired_yaw = desired_yaw
			_prev_desired_pitch = desired_pitch
			_has_prev_gaze = true

			var cur_yaw: float = atan2(current.x, current.z)
			var cur_pitch: float = atan2(current.y, maxf(Vector2(current.x, current.z).length(), 0.0001))
			var gaze_damp: float = 1.0 - exp(-dt * 9.0)
			_gaze_yaw = lerpf(_gaze_yaw, wrapf(desired_yaw - cur_yaw, -PI, PI), gaze_damp)
			_gaze_pitch = lerpf(_gaze_pitch, desired_pitch - cur_pitch, gaze_damp)

			var wanted_yaw: float = clampf(cur_yaw + _gaze_yaw, -yaw_lim, yaw_lim)
			var wanted_pitch: float = clampf(cur_pitch + _gaze_pitch - extra_pitch, -pitch_lim, pitch_lim)
			var wanted := Vector3(
				sin(wanted_yaw) * cos(wanted_pitch),
				sin(wanted_pitch),
				cos(wanted_yaw) * cos(wanted_pitch)
			)
			var turn_body := Quaternion(current.normalized(), wanted.normalized())
			var tilt_quat := Quaternion(Vector3.BACK, clampf(extra_tilt, -0.18, 0.18))
			var combined_body: Quaternion = (turn_body * tilt_quat).normalized()
			var turn_skel := Quaternion((sg.inverse() * body) * Basis(combined_body) * (body.inverse() * sg)).normalized()
			var total: Quaternion = Quaternion.IDENTITY.slerp(turn_skel, _gaze_weight)
			_apply_neck_head_turn(total, head_pose, animated_bones)
			return

	# Even with gaze weight 0, apply subtle emotion pitch/tilt & listener nod.
	if absf(extra_pitch) > 0.001 or absf(extra_tilt) > 0.001:
		var head_pose2: Transform3D = _skeleton.get_bone_global_pose(_head_bone)
		var local_body := Quaternion(Vector3.RIGHT, clampf(extra_pitch, -pitch_lim, pitch_lim)) * Quaternion(Vector3.BACK, clampf(extra_tilt, -0.18, 0.18))
		var turn_skel2 := Quaternion((sg.inverse() * body) * Basis(local_body) * (body.inverse() * sg)).normalized()
		_apply_neck_head_turn(turn_skel2, head_pose2, animated_bones)


func _apply_neck_head_turn(total: Quaternion, head_pose: Transform3D, animated_bones: Dictionary) -> void:
	var head_parent: int = _skeleton.get_bone_parent(_head_bone)
	var parent_rot: Quaternion = Quaternion.IDENTITY
	if head_parent >= 0:
		parent_rot = _skeleton.get_bone_global_pose(head_parent).basis.get_rotation_quaternion()
	var neck_ok: bool = head_parent == _neck_bone and _neck_bone >= 0 and (animated_bones.is_empty() or animated_bones.has(_neck_bone))
	if neck_ok:
		var neck_turn: Quaternion = Quaternion.IDENTITY.slerp(total, GAZE_NECK_SHARE)
		var neck_parent: int = _skeleton.get_bone_parent(_neck_bone)
		var neck_parent_rot: Quaternion = Quaternion.IDENTITY if neck_parent < 0 else _skeleton.get_bone_global_pose(neck_parent).basis.get_rotation_quaternion()
		var new_neck: Quaternion = (neck_turn * parent_rot).normalized()
		_skeleton.set_bone_pose_rotation(_neck_bone, (neck_parent_rot.inverse() * new_neck).normalized())
		parent_rot = new_neck
	var new_head: Quaternion = (total * head_pose.basis.get_rotation_quaternion()).normalized()
	_skeleton.set_bone_pose_rotation(_head_bone, (parent_rot.inverse() * new_head).normalized())


# --------------------------------------------------------- internal steps


func _step_emotions(dt: float) -> void:
	var alpha: float = 1.0 - exp(-dt / maxf(_emotion_transition_s * 0.35, 0.02))
	var best_emo := "neutral"
	var best_w := -1.0
	for emo: String in EMOTIONS:
		var goal: float = 1.0 if emo == _target_emotion else 0.0
		var next_w: float = lerpf(float(_emotion_weights.get(emo, 0.0)), goal, alpha)
		_emotion_weights[emo] = next_w
		if next_w > best_w:
			best_w = next_w
			best_emo = emo
	active_emotion = best_emo
	emotion_intensity = lerpf(emotion_intensity, _target_intensity, alpha)


func _step_lipsync(
	frame_index: int,
	is_speaking: bool,
	speech_energy: float,
	viseme_hint: String,
	dt: float
) -> void:
	var jaw_target := 0.0
	var funnel_target := 0.0
	if is_speaking:
		var energy: float = speech_energy
		if energy < 0.0:
			# Fallback procedural syllable envelope when TTS energy array is absent.
			var phase: float = float(frame_index) * 0.52 + float(_blink_seed % 37)
			energy = clampf(0.36 + 0.44 * sin(phase) + 0.20 * sin(phase * 2.13), 0.0, 1.0)
		_last_speech_energy = energy
		if energy > 0.08:
			var shaped: float = pow(clampf((energy - 0.08) / 0.92, 0.0, 1.0), 0.85)
			# Syllable cadence prevents static held-open mouth when TTS energy stays high.
			var syl_phase: float = _elapsed_s * 16.5 + float(_blink_seed % 29)
			var cadence: float = 0.42 + 0.58 * clampf(absf(sin(syl_phase)), 0.0, 1.0)
			var max_jaw: float = MAX_SHOUT_JAW_OPEN if (active_emotion in ["angry", "surprised"] and emotion_intensity >= 0.75) else MAX_SPEECH_JAW_OPEN
			jaw_target = clampf(shaped * max_jaw * cadence, 0.0, max_jaw)
			var vkey: String = viseme_hint.strip_edges().to_lower()
			if vkey != "" and VISEME_HINTS.has(vkey):
				var vpair: Vector2 = VISEME_HINTS[vkey]
				jaw_target = clampf(shaped * vpair.x * (max_jaw / 0.85) * cadence, 0.0, max_jaw)
				funnel_target = clampf(shaped * vpair.y * 0.52, 0.0, MAX_MOUTH_FUNNEL)
			else:
				var vowel_wave: float = 0.5 + 0.5 * sin(_elapsed_s * 13.5 + float(_blink_seed % 19))
				var round_factor: float = clampf(vowel_wave * 0.46 - 0.14, 0.0, 0.34)
				funnel_target = clampf(shaped * round_factor * cadence, 0.0, MAX_MOUTH_FUNNEL)
	else:
		_last_speech_energy = 0.0

	# Crisp asymmetric attack/release smoothing for clear Mandarin syllables without chatter.
	var jaw_rate: float = 24.0 if jaw_target > _jaw_smoothed else 15.0
	var funnel_rate: float = 18.0 if funnel_target > _funnel_smoothed else 12.0
	_jaw_smoothed = lerpf(_jaw_smoothed, jaw_target, 1.0 - exp(-dt * jaw_rate))
	_funnel_smoothed = lerpf(_funnel_smoothed, funnel_target, 1.0 - exp(-dt * funnel_rate))
	if _jaw_smoothed < 0.004:
		_jaw_smoothed = 0.0
	if _funnel_smoothed < 0.004:
		_funnel_smoothed = 0.0


func _step_listening(dt: float) -> void:
	var goal: float = 1.0 if (_is_listening and not _is_speaking) else 0.0
	_listener_weight = lerpf(_listener_weight, goal, 1.0 - exp(-dt * 5.0))
	if _listener_weight < 0.01:
		_listener_nod_angle_rad = lerpf(_listener_nod_angle_rad, 0.0, 1.0 - exp(-dt * 8.0))
		_listener_tilt_rad = lerpf(_listener_tilt_rad, 0.0, 1.0 - exp(-dt * 6.0))
		return

	_listener_timer += dt
	var nod_mult: float = float((EMOTION_PROFILES.get(active_emotion, EMOTION_PROFILES["neutral"]) as Dictionary).get("listener_nod_mult", 1.0))

	if _listener_nod_timer > 0.0:
		_listener_nod_timer = maxf(_listener_nod_timer - dt, 0.0)
		var u: float = 1.0 - (_listener_nod_timer / maxf(_listener_nod_duration, 0.01))
		_listener_nod_angle_rad = deg_to_rad(4.5 * nod_mult) * sin(u * PI) * _listener_weight
	else:
		_listener_nod_angle_rad = lerpf(_listener_nod_angle_rad, 0.0, 1.0 - exp(-dt * 10.0))
		var interval: float = _listener_nod_interval / maxf(0.65 + _listener_speaker_energy * 0.5, 0.5)
		if _listener_timer >= interval:
			_listener_timer = 0.0
			_listener_nod_timer = _listener_nod_duration
			var cycle_seed: int = absi(int(_elapsed_s * 10.0) * 31 + _blink_seed)
			_listener_nod_interval = 2.4 + float(cycle_seed % 140) / 100.0

	var tilt_target: float = deg_to_rad(2.6) * sin(_elapsed_s * 0.75 + float(_blink_seed % 11)) * _listener_weight
	_listener_tilt_rad = lerpf(_listener_tilt_rad, tilt_target, 1.0 - exp(-dt * 3.0))


func _step_blinking(dt: float) -> void:
	if _gaze_blink_cooldown > 0.0:
		_gaze_blink_cooldown = maxf(_gaze_blink_cooldown - dt, 0.0)

	var rate_mult := 1.0
	var prof: Dictionary = EMOTION_PROFILES.get(active_emotion, EMOTION_PROFILES["neutral"])
	rate_mult = float(prof.get("blink_rate_mult", 1.0))

	if _blink_progress >= 0.0:
		_blink_progress += dt
		var total_dur: float = BLINK_CLOSE_S + BLINK_OPEN_S
		if _blink_progress >= total_dur:
			_blink_progress = -1.0
			_blink_weight = 0.0
		elif _blink_progress <= BLINK_CLOSE_S:
			var u: float = _blink_progress / BLINK_CLOSE_S
			_blink_weight = u * u * (3.0 - 2.0 * u)
		else:
			var v: float = (_blink_progress - BLINK_CLOSE_S) / BLINK_OPEN_S
			_blink_weight = 1.0 - (v * v * (3.0 - 2.0 * v))
		return

	_blink_timer += dt * rate_mult
	if _blink_timer >= _next_blink_interval:
		_blink_timer = 0.0
		_blink_cycle += 1
		_next_blink_interval = _compute_blink_interval(_blink_cycle)
		_trigger_blink()


func _trigger_blink() -> void:
	_blink_progress = 0.0
	_blink_weight = 0.0


func _compute_blink_interval(cycle: int) -> float:
	var h: int = posmod(cycle * 7919 + _blink_seed * 131 + 17, 1000)
	var frac: float = float(h) / 999.0
	return lerpf(BLINK_MIN_PERIOD_S, BLINK_MAX_PERIOD_S, frac)


func _compose_and_apply_shapes(frame_index: int, is_speaking: bool, speech_energy: float) -> void:
	var composed := {}
	for shape_name: String in CANONICAL_SHAPES:
		composed[shape_name] = 0.0

	# 1. Smoothly blended emotion baseline.
	for emo: String in EMOTIONS:
		var ew: float = float(_emotion_weights.get(emo, 0.0)) * emotion_intensity
		if ew <= 0.001:
			continue
		var shapes: Dictionary = (EMOTION_PROFILES[emo] as Dictionary).get("shapes", {})
		for s_key: Variant in shapes.keys():
			var sn: String = str(s_key)
			composed[sn] = float(composed.get(sn, 0.0)) + float(shapes[s_key]) * ew

	# 2. Responsive listener micro-expressions.
	if _listener_weight > 0.01:
		var prof: Dictionary = EMOTION_PROFILES.get(active_emotion, EMOTION_PROFILES["neutral"])
		var l_shapes: Dictionary = prof.get("listener_shapes", {})
		var pulse: float = 0.65 + 0.35 * sin(_elapsed_s * 1.4 + float(_blink_seed % 7))
		for s_key: Variant in l_shapes.keys():
			var sn: String = str(s_key)
			composed[sn] = float(composed.get(sn, 0.0)) + float(l_shapes[s_key]) * _listener_weight * pulse

	# 3. Decoupled TTS lip-sync vs emotional mouth baseline.
	if is_speaking or _jaw_smoothed > 0.004 or _funnel_smoothed > 0.004:
		var max_jaw: float = MAX_SHOUT_JAW_OPEN if (active_emotion in ["angry", "surprised"] and emotion_intensity >= 0.75) else MAX_SPEECH_JAW_OPEN
		composed["jawOpen"] = clampf(maxf(float(composed["jawOpen"]) * 0.20, _jaw_smoothed), 0.0, max_jaw)
		composed["mouthFunnel"] = clampf(maxf(float(composed["mouthFunnel"]) * 0.30, _funnel_smoothed), 0.0, MAX_MOUTH_FUNNEL)
		# Dampen smile/frown gently during open vowels so lips deform naturally without pinching.
		var speech_damp: float = 1.0 - clampf(_jaw_smoothed * 0.55, 0.0, 0.45)
		composed["mouthSmile"] = float(composed["mouthSmile"]) * speech_damp
		composed["mouthFrown"] = float(composed["mouthFrown"]) * speech_damp
	else:
		# Non-speaking mouth stays naturally closed (never hanging open).
		composed["jawOpen"] = clampf(float(composed["jawOpen"]), 0.0, MAX_IDLE_EMOTION_JAW)
		composed["mouthFunnel"] = clampf(float(composed["mouthFunnel"]), 0.0, 0.18)

	# Enforce natural anatomical limits per channel.
	composed["browDown"] = clampf(float(composed["browDown"]), 0.0, MAX_BROW_WEIGHT)
	composed["browUp"] = clampf(float(composed["browUp"]), 0.0, MAX_BROW_WEIGHT)
	composed["mouthSmile"] = clampf(float(composed["mouthSmile"]), 0.0, MAX_LIP_EMOTION_WEIGHT)
	composed["mouthFrown"] = clampf(float(composed["mouthFrown"]), 0.0, MAX_LIP_EMOTION_WEIGHT)

	# 4. Natural blink channel.
	composed["eyeBlink"] = maxf(float(composed["eyeBlink"]), _blink_weight)

	# Clamp and write to meshes if BlendShapes are available.
	for shape_name: String in CANONICAL_SHAPES:
		var val: float = clampf(float(composed[shape_name]), 0.0, 1.0)
		_current_weights[shape_name] = val
		if _shape_bindings.has(shape_name):
			for binding_v: Variant in _shape_bindings[shape_name] as Array:
				var binding: Dictionary = binding_v as Dictionary
				var mi: MeshInstance3D = binding["mesh"] as MeshInstance3D
				if mi != null and is_instance_valid(mi):
					mi.set_blend_shape_value(int(binding["index"]), val)

	# 5. Atlas fallback / logical state tracking.
	var state := "neutral"
	if is_speaking:
		if speech_energy >= 0.0:
			if _jaw_smoothed >= 0.48:
				state = "talk_open"
			elif _jaw_smoothed >= 0.14:
				state = "talk_mid"
			else:
				state = "neutral"
		else:
			state = _fallback_talk_shape(frame_index)
	if _blink_weight >= 0.5 and state != "talk_open":
		state = "blink"
	show_face(state)


func _fallback_talk_shape(frame_index: int) -> String:
	var cycle_len := 0
	for step: int in TALK_STEPS:
		cycle_len += step
	var pos: int = posmod(frame_index, maxi(cycle_len, 1))
	for i in TALK_STEPS.size():
		if pos < TALK_STEPS[i]:
			return TALK_SEQUENCE[i]
		pos -= TALK_STEPS[i]
	return "talk_mid"


func get_qc_snapshot() -> Dictionary:
	return {
		"actor_id": actor_id,
		"capability": capability_grade,
		"emotion": active_emotion,
		"target_emotion": _target_emotion,
		"emotion_intensity": snappedf(emotion_intensity, 0.001),
		"jaw_open": snappedf(float(_current_weights.get("jawOpen", 0.0)), 0.001),
		"mouth_funnel": snappedf(float(_current_weights.get("mouthFunnel", 0.0)), 0.001),
		"mouth_smile": snappedf(float(_current_weights.get("mouthSmile", 0.0)), 0.001),
		"mouth_frown": snappedf(float(_current_weights.get("mouthFrown", 0.0)), 0.001),
		"eye_blink": snappedf(float(_current_weights.get("eyeBlink", 0.0)), 0.001),
		"brow_down": snappedf(float(_current_weights.get("browDown", 0.0)), 0.001),
		"brow_up": snappedf(float(_current_weights.get("browUp", 0.0)), 0.001),
		"gaze_yaw_deg": snappedf(rad_to_deg(_gaze_yaw), 0.1),
		"gaze_pitch_deg": snappedf(rad_to_deg(_gaze_pitch), 0.1),
		"is_speaking": _is_speaking,
		"is_listening": _is_listening,
		"face_state": face_state,
		"discovered_blendshapes": _discovered_shapes.keys(),
	}
