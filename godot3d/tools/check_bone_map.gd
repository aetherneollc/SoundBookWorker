extends SceneTree

## Checks Character3D.canonical_bone against the rig naming schemes we expect
## from Meshy (Mixamo template), Mixamo FBX->glTF, Rigify and the UE mannequin.
##   godot --headless --path godot3d --script res://tools/check_bone_map.gd

const CASES := {
	"mixamorig_Hips": "hips",
	"mixamorig_Neck": "neck",
	"mixamorig_Head": "head",
	"mixamorig_HeadTop_End": "",
	"mixamorig_RightHand": "r_hand",
	"mixamorig_LeftForeArm": "l_lower_arm",
	"mixamorig_RightArm": "r_upper_arm",
	"mixamorig_LeftShoulder": "l_shoulder",
	"mixamorig_RightUpLeg": "r_upper_leg",
	"mixamorig_LeftLeg": "l_lower_leg",
	"mixamorig_LeftToeBase": "l_toes",
	"mixamorig_RightHandIndex1": "r_index1",
	"mixamorig_RightHandThumb3": "r_thumb3",
	"mixamorig_RightHandPinky4": "",
	"mixamorig_Spine2": "",
	"Hips": "hips",
	"RightHand": "r_hand",
	"LeftHandMiddle2": "l_middle2",
	"DEF-hips": "hips",
	"DEF-upper_arm.L": "l_upper_arm",
	"DEF-upper_arm.L.001": "",
	"DEF-forearm.R": "r_lower_arm",
	"DEF-hand.R": "r_hand",
	"DEF-thigh.L": "l_upper_leg",
	"DEF-shin.R": "r_lower_leg",
	"Hand.R": "r_hand",
	"pelvis": "hips",
	"spine_03": "",
	"neck_01": "neck",
	"clavicle_l": "l_shoulder",
	"upperarm_r": "r_upper_arm",
	"lowerarm_l": "l_lower_arm",
	"hand_r": "r_hand",
	"thigh_l": "l_upper_leg",
	"calf_r": "r_lower_leg",
	"foot_l": "l_foot",
	"ball_r": "r_toes",
	"index_01_r": "r_index1",
	"thumb_03_l": "l_thumb3",
	"index_04_leaf_r": "",
	"ik_foot_l": "",
	"root": "",
}


func _init() -> void:
	var character: Script = load("res://player/character3d.gd")
	var failures := 0
	for raw: Variant in CASES.keys():
		var got: String = character.call("canonical_bone", str(raw))
		if got != str(CASES[raw]):
			failures += 1
			print("FAIL %s -> %s (want %s)" % [raw, got, CASES[raw]])
	print("BONE_MAP cases=%d failures=%d" % [CASES.size(), failures])
	quit(0 if failures == 0 else 1)
