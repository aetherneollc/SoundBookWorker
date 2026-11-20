class_name MotionLibrary
extends RefCounted

## Keyframed motion clips plus the procedural "alive" overlay used by Puppet2D.
##
## Clip JSON (res://motions/<name>.json):
##   bones        angle deltas in degrees, clockwise positive (Godot 2D).
##   root_offset  [dx, dy] as a ratio of body height; +x right, +y down.
##   ease         optional: "smooth" (default), "cosine" or "linear".
##   arm_guard    optional, default true; false lets a clip swing the arms
##                inward across the body (see Puppet2D).
## Keys are sparse per bone: a bone missing from one keyframe is interpolated
## between its own neighbouring keys instead of snapping to zero.

const DEFAULT_DIR := "res://motions"
const FALLBACK_CLIP := "idle"

enum Ease { SMOOTH, COSINE, LINEAR }

const BREATH_PERIOD_SEC := 3.6
const BREATH_CHEST_SCALE := Vector2(0.005, 0.011)
const BREATH_SHOULDER_DEG := 0.5
const BREATH_FOREARM_DEG := 0.3

# One blink per slot, jittered inside it: gaps are 3.8 +/- 1.0 s = 2.8..4.8 s.
const BLINK_SLOT_SEC := 3.8
const BLINK_JITTER_SEC := 0.5
const BLINK_DURATION_SEC := 0.14

# Neck + head share the look tilt, well inside the 12 degree budget: on a
# front-facing rig a rotation reads as a head cock, so the turn itself is
# carried by HEAD_TURN_SHIFT and the face gaze instead.
const LOOK_NECK_DEG := 2.5
const LOOK_HEAD_DEG := 4.5
const LOOK_TORSO_DEG := 1.0
const LEAN_TORSO_DEG := 5.0
const TORSO_TILT_MAX_DEG := 6.0
const LEAN_PELVIS_DEG := 1.2
const LEAN_NECK_COUNTER_DEG := 0.8
const LEAN_ROOT_SHIFT := 0.004
# Head slide (ratio of body height) that fakes a slight 2.5D turn.
const HEAD_TURN_SHIFT := 0.0075

const TALK_HEAD_DEG := 1.4
const TALK_NECK_DEG := 0.5
const TALK_TORSO_DEG := 0.5
const TALK_SHOULDER_DEG := 0.8
const TALK_HEAD_DIP := 0.0016
const TALK_ROOT_DIP := 0.001

var _clips: Dictionary = {}
var _dir_loaded := false
var _warned: Dictionary = {}
# Scratch output of _locate(); avoids allocating per sampled track.
var _seg_a := 0
var _seg_b := 0
var _seg_u := 0.0


func _init() -> void:
	_register_builtin_idle()


## Loads every *.json clip in the directory. Clips from disk replace the
## built-in idle, which stays available when the directory is missing or empty.
func load_dir(dir_path: String = DEFAULT_DIR) -> void:
	_dir_loaded = true
	if not _clips.has(FALLBACK_CLIP):
		_register_builtin_idle()
	var dir := DirAccess.open(dir_path)
	if dir == null:
		push_warning(
			"MotionLibrary: cannot open %s (%s); only the built-in idle is available"
			% [dir_path, error_string(DirAccess.get_open_error())]
		)
		return
	var files: PackedStringArray = dir.get_files()
	files.sort()
	var loaded := 0
	for file_name: String in files:
		if file_name.get_extension().to_lower() != "json":
			continue
		if _load_file(dir_path.path_join(file_name)):
			loaded += 1
	if loaded == 0:
		push_warning("MotionLibrary: no clips in %s; only the built-in idle is available" % dir_path)


## Registers one clip from already-parsed JSON data.
func add_clip(data: Dictionary, fallback_name: String = "") -> bool:
	var clip_name: String = str(data.get("name", fallback_name)).strip_edges()
	if clip_name == "":
		clip_name = fallback_name.strip_edges()
	if clip_name == "":
		return false
	var frames: Array[Dictionary] = []
	for item: Variant in _array(data.get("keyframes", null)):
		if item is Dictionary:
			frames.append(_dictionary(item))
	if frames.is_empty():
		push_warning("MotionLibrary: clip %s has no keyframes" % clip_name)
		return false
	frames.sort_custom(_frame_before)

	var tracks: Dictionary = {}
	var root_times: Array = []
	var root_values: Array = []
	var last_time := 0.0
	for frame: Dictionary in frames:
		var key_time: float = maxf(_number(frame.get("t", 0.0), 0.0), 0.0)
		last_time = maxf(last_time, key_time)
		var pair: Array = _array(frame.get("root_offset", null))
		if pair.size() >= 2:
			root_times.append(key_time)
			root_values.append(Vector2(_number(pair[0], 0.0), _number(pair[1], 0.0)))
		var frame_bones: Dictionary = _dictionary(frame.get("bones", null))
		for bone_key: Variant in frame_bones.keys():
			var bone_name: String = str(bone_key)
			if not tracks.has(bone_name):
				tracks[bone_name] = {"t": [], "v": []}
			var track: Dictionary = tracks[bone_name]
			var track_times: Array = track["t"]
			var track_values: Array = track["v"]
			track_times.append(key_time)
			track_values.append(_number(frame_bones[bone_key], 0.0))

	var fps: int = maxi(int(_number(data.get("fps", 24), 24.0)), 1)
	var duration: float = _number(data.get("duration", last_time), last_time)
	if duration <= 0.0:
		duration = maxf(last_time, 1.0 / float(fps))
	_clips[clip_name] = {
		"name": clip_name,
		"loop": _flag(data.get("loop", true), true),
		"duration": duration,
		"fps": fps,
		"foot_lock": _flag(data.get("foot_lock", true), true),
		"arm_guard": _flag(data.get("arm_guard", true), true),
		"ease": _ease_mode(str(data.get("ease", "smooth"))),
		"tracks": tracks,
		"root_t": root_times,
		"root_v": root_values,
	}
	return true


func has_clip(clip_name: String) -> bool:
	_ensure_loaded()
	return _clips.has(clip_name)


func get_clip_names() -> PackedStringArray:
	_ensure_loaded()
	var names: Array[String] = []
	for key: Variant in _clips.keys():
		names.append(str(key))
	names.sort()
	return PackedStringArray(names)


## Clip metadata: {"loop": bool, "duration": float, "fps": int, "foot_lock": bool}.
func get_clip_info(clip_name: String) -> Dictionary:
	_ensure_loaded()
	if not _clips.has(clip_name):
		return {}
	var clip: Dictionary = _clips[clip_name]
	return {
		"loop": clip["loop"],
		"duration": clip["duration"],
		"fps": clip["fps"],
		"foot_lock": clip["foot_lock"],
	}


## Returns {"root_offset": Vector2, "bones": {bone_name: degrees},
## "foot_lock": bool} plus "foot_lock_weight" and "arm_guard" (both 0..1,
## so they can be blended) and "clip".
## Unknown clips fall back to idle so a bad name never breaks a frame.
func sample(clip_name: String, time_sec: float) -> Dictionary:
	_ensure_loaded()
	var clip: Dictionary = _resolve_clip(clip_name)
	var duration: float = clip["duration"]
	var looping: bool = clip["loop"]
	var mode: int = clip["ease"]
	var local_time := 0.0
	if not is_nan(time_sec):
		local_time = fposmod(time_sec, duration) if looping else clampf(time_sec, 0.0, duration)

	var bones: Dictionary = {}
	var tracks: Dictionary = clip["tracks"]
	for bone_key: Variant in tracks.keys():
		var track: Dictionary = tracks[bone_key]
		var times: Array = track["t"]
		var values: Array = track["v"]
		_locate(times, local_time, looping, duration)
		var from_value: float = values[_seg_a]
		var to_value: float = values[_seg_b]
		bones[bone_key] = lerpf(from_value, to_value, _eased(_seg_u, mode))

	var root_offset := Vector2.ZERO
	var root_times: Array = clip["root_t"]
	if not root_times.is_empty():
		var root_values: Array = clip["root_v"]
		_locate(root_times, local_time, looping, duration)
		var from_root: Vector2 = root_values[_seg_a]
		var to_root: Vector2 = root_values[_seg_b]
		root_offset = from_root.lerp(to_root, _eased(_seg_u, mode))

	var locked: bool = clip["foot_lock"]
	var guarded: bool = clip["arm_guard"]
	return {
		"root_offset": root_offset,
		"bones": bones,
		"foot_lock": locked,
		"foot_lock_weight": 1.0 if locked else 0.0,
		"arm_guard": 1.0 if guarded else 0.0,
		"clip": clip["name"],
	}


## Left-right reflection of a sampled pose: `_l` and `_r` bones trade places,
## every angle and the horizontal root offset change sign. The art itself is
## not flipped, so a one-sided gesture can be played toward either side.
func mirror_pose(pose: Dictionary) -> Dictionary:
	var bones: Dictionary = {}
	var source: Dictionary = _dictionary(pose.get("bones", null))
	for bone_key: Variant in source.keys():
		var bone_name: String = str(bone_key)
		if bone_name.ends_with("_l"):
			bone_name = bone_name.trim_suffix("_l") + "_r"
		elif bone_name.ends_with("_r"):
			bone_name = bone_name.trim_suffix("_r") + "_l"
		bones[bone_name] = -_number(source[bone_key], 0.0)
	var mirrored: Dictionary = pose.duplicate()
	var root_offset: Vector2 = _vector(pose.get("root_offset", null))
	mirrored["root_offset"] = Vector2(-root_offset.x, root_offset.y)
	mirrored["bones"] = bones
	return mirrored


## Linear blend of two sampled poses; weight 0 returns a, 1 returns b.
## A bone missing from one side counts as 0 degrees there.
func blend_poses(a: Dictionary, b: Dictionary, weight: float) -> Dictionary:
	var amount: float = 0.0 if is_nan(weight) else clampf(weight, 0.0, 1.0)
	var bones_a: Dictionary = _dictionary(a.get("bones", null))
	var bones_b: Dictionary = _dictionary(b.get("bones", null))
	var bones: Dictionary = {}
	for bone_key: Variant in bones_a.keys():
		bones[bone_key] = lerpf(
			_number(bones_a[bone_key], 0.0),
			_number(bones_b.get(bone_key, 0.0), 0.0),
			amount
		)
	for bone_key: Variant in bones_b.keys():
		if not bones_a.has(bone_key):
			bones[bone_key] = lerpf(0.0, _number(bones_b[bone_key], 0.0), amount)

	var lock_weight: float = lerpf(_lock_weight(a), _lock_weight(b), amount)
	return {
		"root_offset": _vector(a.get("root_offset", null)).lerp(
			_vector(b.get("root_offset", null)), amount
		),
		"bones": bones,
		"foot_lock": lock_weight >= 0.5,
		"foot_lock_weight": lock_weight,
		"arm_guard": lerpf(
			clampf(_number(a.get("arm_guard", 1.0), 1.0), 0.0, 1.0),
			clampf(_number(b.get("arm_guard", 1.0), 1.0), 0.0, 1.0),
			amount
		),
		"clip": str(b.get("clip", "")) if amount >= 0.5 else str(a.get("clip", "")),
	}


## Procedural life layer, additive on top of a sampled pose. look_dir and lean
## are in the puppet's own space: +1 turns/leans toward its +x side.
## Returns {"bones": {bone_name: degrees}, "root_offset": Vector2,
## "head_offset": Vector2, "torso_scale": Vector2, "blink": 0..1,
## "breath": -1..1, "gaze": Vector2}. Offsets are ratios of body height.
## speak_level (0..1) lets the caller ease speech in and out; when negative
## the speaking flag is used as a hard switch.
func build_overlay(
	time_sec: float,
	actor_seed: float,
	speaking: bool,
	look_dir: float,
	lean: float,
	speak_level: float = -1.0
) -> Dictionary:
	var now: float = 0.0 if is_nan(time_sec) else time_sec
	var key: float = 0.0 if is_nan(actor_seed) else fposmod(actor_seed, 8191.0)
	var talk: float = clampf(speak_level, 0.0, 1.0) if speak_level >= 0.0 else (1.0 if speaking else 0.0)
	var look: float = 0.0 if is_nan(look_dir) else clampf(look_dir, -1.0, 1.0)
	var bend: float = 0.0 if is_nan(lean) else clampf(lean, -1.0, 1.0)

	# Breathing: the chest swells from the waist, shoulders rise and settle.
	var breath_period: float = BREATH_PERIOD_SEC * (0.95 + 0.10 * _hash01(key, 1.0))
	var breath: float = sin(TAU * now / breath_period + TAU * _hash01(key, 2.0))
	# Slow weight drift keeps a held pose from reading as a still image.
	var drift: float = (
		0.62 * sin(TAU * now / 7.3 + TAU * _hash01(key, 3.0))
		+ 0.38 * sin(TAU * now / 4.1 + TAU * _hash01(key, 4.0))
	)
	var head_drift: float = sin(TAU * now / 5.7 + TAU * _hash01(key, 5.0))

	var pelvis_deg: float = LEAN_PELVIS_DEG * bend
	var torso_deg: float = 0.30 * drift + clampf(
		LEAN_TORSO_DEG * bend + LOOK_TORSO_DEG * look,
		-TORSO_TILT_MAX_DEG,
		TORSO_TILT_MAX_DEG
	)
	var neck_deg: float = LOOK_NECK_DEG * look - LEAN_NECK_COUNTER_DEG * bend
	var head_deg: float = LOOK_HEAD_DEG * look - 0.35 * drift + 0.25 * head_drift
	var upper_arm_l_deg: float = BREATH_SHOULDER_DEG * breath
	var upper_arm_r_deg: float = -BREATH_SHOULDER_DEG * breath
	var lower_arm_l_deg: float = -BREATH_FOREARM_DEG * breath
	var lower_arm_r_deg: float = BREATH_FOREARM_DEG * breath
	var root_offset := Vector2(0.001 * drift + LEAN_ROOT_SHIFT * bend, 0.0)
	var head_offset := Vector2(HEAD_TURN_SHIFT * look, 0.0)

	if talk > 0.0:
		# A syllable-rate beat over a slower phrase swell; the small head dip
		# stands in for a pitch nod, which a flat rig cannot rotate into.
		var beat_hz: float = 2.2 + 0.5 * _hash01(key, 6.0)
		var beat: float = sin(TAU * beat_hz * now + TAU * _hash01(key, 7.0))
		var phrase: float = sin(TAU * 0.63 * now + TAU * _hash01(key, 8.0))
		var shoulder: float = sin(TAU * 0.9 * now + TAU * _hash01(key, 9.0))
		head_deg += talk * TALK_HEAD_DEG * (0.55 * beat + 0.45 * phrase)
		neck_deg += talk * TALK_NECK_DEG * phrase
		torso_deg += talk * TALK_TORSO_DEG * shoulder
		upper_arm_l_deg += talk * TALK_SHOULDER_DEG * shoulder
		upper_arm_r_deg += talk * TALK_SHOULDER_DEG * shoulder
		head_offset.y += talk * TALK_HEAD_DIP * (0.5 + 0.5 * beat)
		root_offset.y += talk * TALK_ROOT_DIP * (0.5 + 0.5 * phrase)

	return {
		"bones": {
			"pelvis": pelvis_deg,
			"torso": torso_deg,
			"neck": neck_deg,
			"head": head_deg,
			"upper_arm_l": upper_arm_l_deg,
			"upper_arm_r": upper_arm_r_deg,
			"lower_arm_l": lower_arm_l_deg,
			"lower_arm_r": lower_arm_r_deg,
		},
		"root_offset": root_offset,
		"head_offset": head_offset,
		"torso_scale": Vector2.ONE + BREATH_CHEST_SCALE * breath,
		"blink": _blink_amount(now, key),
		"breath": breath,
		"gaze": Vector2(look, 0.0),
	}


func _ensure_loaded() -> void:
	if not _dir_loaded:
		load_dir(DEFAULT_DIR)


func _resolve_clip(clip_name: String) -> Dictionary:
	if _clips.has(clip_name):
		return _clips[clip_name]
	if not _warned.has(clip_name):
		_warned[clip_name] = true
		push_warning("MotionLibrary: unknown clip '%s'; using %s" % [clip_name, FALLBACK_CLIP])
	if not _clips.has(FALLBACK_CLIP):
		_register_builtin_idle()
	return _clips[FALLBACK_CLIP]


func _register_builtin_idle() -> void:
	var registered: bool = add_clip({
		"name": FALLBACK_CLIP,
		"loop": true,
		"duration": 4.0,
		"fps": 24,
		"foot_lock": true,
		"keyframes": [
			{"t": 0.0, "root_offset": [0.0, 0.0], "bones": {
				"pelvis": 0.0, "torso": 0.0, "neck": 0.0, "head": 0.0,
				"upper_arm_l": 0.0, "upper_arm_r": 0.0,
			}},
			{"t": 2.0, "root_offset": [0.001, 0.002], "bones": {
				"pelvis": -0.4, "torso": 0.7, "neck": -0.2, "head": -0.5,
				"upper_arm_l": 1.0, "upper_arm_r": -1.0,
			}},
			{"t": 4.0, "root_offset": [0.0, 0.0], "bones": {
				"pelvis": 0.0, "torso": 0.0, "neck": 0.0, "head": 0.0,
				"upper_arm_l": 0.0, "upper_arm_r": 0.0,
			}},
		],
	}, FALLBACK_CLIP)
	if not registered:
		push_error("MotionLibrary: built-in idle clip failed to register")


func _load_file(path: String) -> bool:
	var text: String = FileAccess.get_file_as_string(path)
	if text.strip_edges() == "":
		push_warning("MotionLibrary: empty or unreadable clip %s" % path)
		return false
	var parser := JSON.new()
	var parse_error: Error = parser.parse(text)
	if parse_error != OK:
		push_warning(
			"MotionLibrary: %s line %d: %s"
			% [path, parser.get_error_line(), parser.get_error_message()]
		)
		return false
	var data: Variant = parser.data
	if not data is Dictionary:
		push_warning("MotionLibrary: %s must contain a JSON object" % path)
		return false
	return add_clip(_dictionary(data), path.get_file().get_basename())


## Finds the keyframe pair around time_sec and stores it in _seg_a/_seg_b/_seg_u.
func _locate(times: Array, time_sec: float, looping: bool, duration: float) -> void:
	var count: int = times.size()
	_seg_a = 0
	_seg_b = 0
	_seg_u = 0.0
	if count <= 1:
		return
	var first: float = times[0]
	var last: float = times[count - 1]
	if time_sec <= first or time_sec >= last:
		var edge: int = 0 if time_sec <= first else count - 1
		_seg_a = edge
		_seg_b = edge
		if not looping:
			return
		# Looping clips interpolate across the seam: last key -> first key.
		var span: float = first + duration - last
		if span <= 0.000001:
			return
		var elapsed: float = time_sec - last if time_sec >= last else time_sec + duration - last
		_seg_a = count - 1
		_seg_b = 0
		_seg_u = clampf(elapsed / span, 0.0, 1.0)
		return
	var upper: int = clampi(times.bsearch(time_sec, false), 1, count - 1)
	_seg_a = upper - 1
	_seg_b = upper
	var from_time: float = times[_seg_a]
	var to_time: float = times[_seg_b]
	if to_time - from_time > 0.000001:
		_seg_u = clampf((time_sec - from_time) / (to_time - from_time), 0.0, 1.0)


func _blink_amount(time_sec: float, key: float) -> float:
	var shifted: float = time_sec + _hash01(key, 11.0) * BLINK_SLOT_SEC
	var slot: float = floorf(shifted / BLINK_SLOT_SEC)
	var jitter: float = (_hash01(key, 31.0 + slot) - 0.5) * 2.0 * BLINK_JITTER_SEC
	var start: float = (slot + 0.5) * BLINK_SLOT_SEC + jitter
	var phase: float = (shifted - start) / BLINK_DURATION_SEC
	if phase <= 0.0 or phase >= 1.0:
		return 0.0
	# Lids close fast, hold for a moment, then open more slowly.
	if phase < 0.36:
		return _smooth(phase / 0.36)
	if phase < 0.5:
		return 1.0
	return 1.0 - _smooth((phase - 0.5) / 0.5)


static func _frame_before(a: Dictionary, b: Dictionary) -> bool:
	return _number(a.get("t", 0.0), 0.0) < _number(b.get("t", 0.0), 0.0)


static func _ease_mode(label: String) -> int:
	match label.strip_edges().to_lower():
		"linear":
			return Ease.LINEAR
		"cosine", "cos":
			return Ease.COSINE
		_:
			return Ease.SMOOTH


static func _eased(amount: float, mode: int) -> float:
	match mode:
		Ease.LINEAR:
			return amount
		Ease.COSINE:
			return 0.5 - 0.5 * cos(PI * amount)
		_:
			return _smooth(amount)


static func _smooth(amount: float) -> float:
	var clamped: float = clampf(amount, 0.0, 1.0)
	return clamped * clamped * (3.0 - 2.0 * clamped)


## Deterministic 0..1 hash so every actor keeps its own stable phases.
static func _hash01(key: float, salt: float) -> float:
	var raw: float = sin(key * 12.9898 + salt * 78.233) * 43758.5453
	return raw - floorf(raw)


static func _number(value: Variant, fallback: float) -> float:
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


static func _flag(value: Variant, fallback: bool) -> bool:
	match typeof(value):
		TYPE_BOOL:
			var enabled: bool = value
			return enabled
		TYPE_FLOAT, TYPE_INT:
			var number: float = value
			return number != 0.0
		TYPE_STRING, TYPE_STRING_NAME:
			var text: String = str(value).strip_edges().to_lower()
			if text in ["true", "yes", "1", "on"]:
				return true
			if text in ["false", "no", "0", "off"]:
				return false
			return fallback
		_:
			return fallback


static func _vector(value: Variant) -> Vector2:
	if value is Vector2:
		var vector: Vector2 = value
		return vector
	var pair: Array = _array(value)
	if pair.size() >= 2:
		return Vector2(_number(pair[0], 0.0), _number(pair[1], 0.0))
	return Vector2.ZERO


static func _dictionary(value: Variant) -> Dictionary:
	if value is Dictionary:
		var entries: Dictionary = value
		return entries
	return {}


static func _array(value: Variant) -> Array:
	if value is Array:
		var items: Array = value
		return items
	return []


static func _lock_weight(pose: Dictionary) -> float:
	if pose.has("foot_lock_weight"):
		return clampf(_number(pose["foot_lock_weight"], 0.0), 0.0, 1.0)
	return 1.0 if _flag(pose.get("foot_lock", false), false) else 0.0
