class_name SceneEventController
extends RefCounted

## Data-driven SceneEventController.
##
## Drives environmental feedback across lighting, dynamic screens, doors,
## storm atmosphere, power, exterior arrays, and physical console hits:
##   - signal_lost
##   - sensor_error
##   - red_alert
##   - storm_approaching
##   - device_operating
##   - robot_speaking
##   - door_open
##   - door_close
##   - power_flicker
##   - array_restore
##   - table_impact
##   - module_install

const SUPPORTED_EVENTS: Array[String] = [
	"signal_lost",
	"sensor_error",
	"red_alert",
	"storm_approaching",
	"device_operating",
	"robot_speaking",
	"door_open",
	"door_close",
	"power_flicker",
	"array_restore",
	"table_impact",
	"module_install",
]

var _events: Array[Dictionary] = []
var _active_now: Array[String] = []
var _last_screen_mode := ""
var _alert_pulse := 0.0
var _storm_intensity := 0.0


func setup(events_list: Array, _scene_type: String, lighting_profile: String, captions: Array) -> void:
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
	# not explicitly authored so every story automatically gets environmental feedback.
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
			_events.append({"type": "signal_lost", "from": maxi(0, f0 - 24), "to": 999999, "intensity": 0.9, "target": ""})
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


func _event_sample(kind: String, frame_index: int, fps: int) -> Dictionary:
	var found := false
	var best_i := 0.0
	var best_u := 0.0
	var best_age := 0.0
	var best_from := -1
	for ev: Dictionary in _events:
		if str(ev["type"]) != kind:
			continue
		var f0: int = int(ev["from"])
		var f1: int = int(ev["to"])
		if frame_index < f0 or frame_index > f1:
			continue
		var span: int = maxi(f1 - f0, 1)
		var u: float = clampf(float(frame_index - f0) / float(span), 0.0, 1.0)
		var age: float = float(frame_index - f0) / float(maxi(fps, 1))
		var inten: float = float(ev.get("intensity", 1.0))
		if not found or inten >= best_i:
			found = true
			best_i = inten
			best_u = u
			best_age = age
			best_from = f0
	return {
		"active": found,
		"intensity": best_i if found else 0.0,
		"u": best_u,
		"age_s": best_age,
		"from": best_from,
	}


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

	var flick := _event_sample("power_flicker", frame_index, fps)
	var restore := _event_sample("array_restore", frame_index, fps)
	var impact := _event_sample("table_impact", frame_index, fps)
	var install := _event_sample("module_install", frame_index, fps)
	var fault_sig := _event_sample("signal_lost", frame_index, fps)
	var fault_sen := _event_sample("sensor_error", frame_index, fps)
	var door_o := _event_sample("door_open", frame_index, fps)
	var door_c := _event_sample("door_close", frame_index, fps)

	var door_command := "hold"
	if bool(door_o["active"]) and bool(door_c["active"]):
		door_command = "close" if int(door_c["from"]) >= int(door_o["from"]) else "open"
	elif bool(door_c["active"]):
		door_command = "close"
	elif bool(door_o["active"]):
		door_command = "open"

	var screen_mode := "nominal"
	if "red_alert" in _active_now:
		screen_mode = "red_alert"
	elif bool(flick["active"]):
		screen_mode = "power_flicker"
	elif bool(install["active"]):
		screen_mode = "locked_carrier"
	elif bool(restore["active"]):
		screen_mode = "locked_carrier"
	elif bool(impact["active"]):
		screen_mode = "impact_spike"
	elif "storm_approaching" in _active_now:
		screen_mode = "storm_approaching"
	elif "sensor_error" in _active_now:
		screen_mode = "sensor_error"
	elif "signal_lost" in _active_now:
		screen_mode = "signal_lost"
	elif "device_operating" in _active_now:
		screen_mode = "device_operating"

	_alert_pulse = (0.5 + 0.5 * sin(t_s * TAU * 1.25)) * float(intensities.get("red_alert", 0.0))
	_storm_intensity = float(intensities.get("storm_approaching", 0.0))

	var payload := {
		"active_events": _active_now.duplicate(),
		"screen_mode": screen_mode,
		"alert_pulse": _alert_pulse,
		"storm_intensity": _storm_intensity,
		"door_open": door_command == "open",
		"door_command": door_command,
		"power_flicker": float(flick["intensity"]) if bool(flick["active"]) else 0.0,
		"array_restore": float(restore["intensity"]) if bool(restore["active"]) else 0.0,
		"array_restore_u": float(restore["u"]) if bool(restore["active"]) else 0.0,
		"array_fault": bool(fault_sig["active"]) or bool(fault_sen["active"]),
		"array_fault_u": maxf(float(fault_sig["u"]) if bool(fault_sig["active"]) else 0.0, float(fault_sen["u"]) if bool(fault_sen["active"]) else 0.0),
		"table_impact": float(impact["intensity"]) if bool(impact["active"]) else 0.0,
		"table_impact_u": float(impact["u"]) if bool(impact["active"]) else 0.0,
		"table_impact_age_s": float(impact["age_s"]) if bool(impact["active"]) else 0.0,
		"module_install": float(install["intensity"]) if bool(install["active"]) else 0.0,
		"module_install_u": float(install["u"]) if bool(install["active"]) else 0.0,
		"module_install_age_s": float(install["age_s"]) if bool(install["active"]) else 0.0,
		"signal_lost": bool(fault_sig["active"]),
		"time_s": t_s,
	}

	if bay != null and bay.has_method("apply_scene_events"):
		bay.call("apply_scene_events", payload)
	_last_screen_mode = screen_mode

	if lighting_rig != null and lighting_rig.has_method("apply_event_feedback"):
		lighting_rig.call("apply_event_feedback", _alert_pulse, _storm_intensity, bool(fault_sig["active"]), t_s)

	if rover != null and rover.has_method("set_speaking_feedback"):
		rover.call("set_speaking_feedback", "robot_speaking" in _active_now, t_s)

	payload["alert_pulse"] = snappedf(_alert_pulse, 0.001)
	payload["storm_intensity"] = snappedf(_storm_intensity, 0.001)
	return payload


func get_active_events() -> Array[String]:
	return _active_now.duplicate()
