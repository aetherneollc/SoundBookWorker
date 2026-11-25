class_name SceneEventController
extends RefCounted

## Data-driven SceneEventController (BUG-012).
##
## Drives environmental feedback across lighting, dynamic screens, doors,
## storm atmosphere, and robot state from timeline events or narrative cues:
##   - signal_lost
##   - sensor_error
##   - red_alert
##   - storm_approaching
##   - device_operating
##   - robot_speaking
##   - door_open
##   - door_close

const SUPPORTED_EVENTS: Array[String] = [
	"signal_lost",
	"sensor_error",
	"red_alert",
	"storm_approaching",
	"device_operating",
	"robot_speaking",
	"door_open",
	"door_close",
]

var _events: Array[Dictionary] = []
var _active_now: Array[String] = []
var _last_screen_mode := ""
var _alert_pulse := 0.0
var _storm_intensity := 0.0


func setup(events_list: Array, scene_type: String, lighting_profile: String, captions: Array) -> void:
	_events.clear()
	_active_now.clear()
	_last_screen_mode = ""
	_alert_pulse = 0.0
	_storm_intensity = 0.0

	for ev_v: Variant in events_list:
		if ev_v is Dictionary:
			var ev: Dictionary = ev_v as Dictionary
			var kind: String = str(ev.get("type", ev.get("event", ""))).strip_edges().to_lower()
			if kind in SUPPORTED_EVENTS:
				_events.append({
					"type": kind,
					"from": int(ev.get("from", 0)),
					"to": int(ev.get("to", 999999)),
					"intensity": float(ev.get("intensity", 1.0)),
					"target": str(ev.get("target", "")),
				})

	# Auto-derive narrative scene events from lighting preset & dialogue cues when
	# not explicitly authored so every story automatically gets environmental feedback!
	if lighting_profile == "night_alert" and not _has_event_type("red_alert"):
		_events.append({"type": "red_alert", "from": 0, "to": 999999, "intensity": 1.0, "target": ""})

	for cap_v: Variant in captions:
		if not (cap_v is Dictionary):
			continue
		var cap: Dictionary = cap_v as Dictionary
		var txt: String = str(cap.get("text", ""))
		var f0: int = int(cap.get("from", 0))
		var f1: int = int(cap.get("to", f0 + 72))
		if (txt.contains("信号") and txt.contains("断")) and not _has_event_type("signal_lost"):
			_events.append({"type": "signal_lost", "from": maxf(0, f0 - 24), "to": 999999, "intensity": 0.9, "target": ""})
		if (txt.contains("读数不对") or txt.contains("校准") or txt.contains("传感器")) and not _has_event_type("sensor_error"):
			_events.append({"type": "sensor_error", "from": 0, "to": 999999, "intensity": 0.85, "target": ""})
		if (txt.contains("红色警报") or txt.contains("警报")) and not _has_event_type("red_alert"):
			_events.append({"type": "red_alert", "from": 0, "to": 999999, "intensity": 1.0, "target": ""})
		if txt.contains("风暴") and not _has_event_type("storm_approaching"):
			_events.append({"type": "storm_approaching", "from": maxi(0, f0 - 36), "to": 999999, "intensity": 0.9, "target": ""})
		if txt.contains("阿拓") or str(cap.get("speaker", "")).to_lower() in ["rover", "atuo", "robot"]:
			_events.append({"type": "robot_speaking", "from": f0, "to": f1, "intensity": 1.0, "target": "rover"})


func _has_event_type(kind: String) -> bool:
	for ev: Dictionary in _events:
		if str(ev["type"]) == kind:
			return true
	return false


func update(frame_index: int, fps: int, bay: Node, lighting_rig: Node, rover: Node) -> Dictionary:
	var t_s: float = float(frame_index) / float(maxi(fps, 1))
	_active_now.clear()
	var intensities: Dictionary = {}

	for ev: Dictionary in _events:
		if frame_index >= int(ev["from"]) and frame_index <= int(ev["to"]):
			var kind: String = str(ev["type"])
			if not (kind in _active_now):
				_active_now.append(kind)
			intensities[kind] = maxf(float(intensities.get(kind, 0.0)), float(ev.get("intensity", 1.0)))

	# Determine priority screen telemetry mode.
	var screen_mode := "nominal"
	if "red_alert" in _active_now:
		screen_mode = "red_alert"
	elif "storm_approaching" in _active_now:
		screen_mode = "storm_approaching"
	elif "sensor_error" in _active_now:
		screen_mode = "sensor_error"
	elif "signal_lost" in _active_now:
		screen_mode = "signal_lost"
	elif "device_operating" in _active_now:
		screen_mode = "device_operating"

	# Red alert pulsing beacon signal (~1.25 Hz).
	_alert_pulse = (0.5 + 0.5 * sin(t_s * TAU * 1.25)) * float(intensities.get("red_alert", 0.0))
	_storm_intensity = float(intensities.get("storm_approaching", 0.0))

	if bay != null and bay.has_method("apply_scene_events"):
		bay.call("apply_scene_events", {
			"active_events": _active_now.duplicate(),
			"screen_mode": screen_mode,
			"alert_pulse": _alert_pulse,
			"storm_intensity": _storm_intensity,
			"door_open": "door_open" in _active_now and not ("door_close" in _active_now),
			"time_s": t_s,
		})
	_last_screen_mode = screen_mode

	if lighting_rig != null and lighting_rig.has_method("apply_event_feedback"):
		lighting_rig.call("apply_event_feedback", _alert_pulse, _storm_intensity, "signal_lost" in _active_now, t_s)

	if rover != null and rover.has_method("set_speaking_feedback"):
		rover.call("set_speaking_feedback", "robot_speaking" in _active_now, t_s)

	return {
		"active_events": _active_now.duplicate(),
		"screen_mode": screen_mode,
		"alert_pulse": snappedf(_alert_pulse, 0.001),
		"storm_intensity": snappedf(_storm_intensity, 0.001),
	}


func get_active_events() -> Array[String]:
	return _active_now.duplicate()
