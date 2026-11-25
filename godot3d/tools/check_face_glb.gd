extends SceneTree

## Loads GLBs written by tools/fix_character_face.py the way the stage does
## (runtime GLTFDocument) and checks the rig survived:
##
##   godot --headless --path godot3d --script res://tools/check_face_glb.gd -- a.glb [b.glb ...]
##
## Each file needs a Skeleton3D, a skinned MeshInstance3D whose Skin binds to
## that skeleton, bone/weight arrays on every surface, and a FaceMat surface
## carrying UV2. Prints FACE_GLB OK or FACE_GLB FAIL per file.


func _initialize() -> void:
	var character: Script = load("res://player/character3d.gd")
	var failures := 0
	for path: String in OS.get_cmdline_user_args():
		var problems := PackedStringArray()
		var scene: Node3D = character.call("load_gltf_scene", path)
		if scene == null:
			print("FACE_GLB FAIL %s: does not load" % path)
			failures += 1
			continue
		var skeleton: Skeleton3D = null
		var meshes: Array[MeshInstance3D] = []
		var stack: Array[Node] = [scene]
		while not stack.is_empty():
			var node: Node = stack.pop_back()
			if node is Skeleton3D and skeleton == null:
				skeleton = node as Skeleton3D
			if node is MeshInstance3D:
				meshes.append(node as MeshInstance3D)
			for child: Node in node.get_children():
				stack.append(child)
		if skeleton == null:
			problems.append("no Skeleton3D")
		var face_surfaces := 0
		var surfaces := 0
		var summary := PackedStringArray()
		for mi: MeshInstance3D in meshes:
			if mi.skin == null:
				problems.append("%s has no Skin" % mi.name)
				continue
			if skeleton != null:
				for i in mi.skin.get_bind_count():
					var bone_name: String = str(mi.skin.get_bind_name(i))
					var bone: int = mi.skin.get_bind_bone(i)
					if bone < 0 and skeleton.find_bone(bone_name) < 0:
						problems.append("bind %d (%s) has no bone" % [i, bone_name])
						break
				if mi.get_node_or_null(mi.skeleton) != skeleton:
					problems.append("%s skeleton path does not resolve" % mi.name)
			for s in mi.mesh.get_surface_count():
				surfaces += 1
				var arrays: Array = mi.mesh.surface_get_arrays(s)
				var material: Material = mi.mesh.surface_get_material(s)
				var mat_name: String = material.resource_name if material != null else ""
				var verts: int = (arrays[Mesh.ARRAY_VERTEX] as PackedVector3Array).size()
				var has_uv2: bool = arrays[Mesh.ARRAY_TEX_UV2] != null and (arrays[Mesh.ARRAY_TEX_UV2] as PackedVector2Array).size() == verts
				var has_bones: bool = arrays[Mesh.ARRAY_BONES] != null and (arrays[Mesh.ARRAY_BONES] as PackedInt32Array).size() >= verts * 4
				var has_normals: bool = arrays[Mesh.ARRAY_NORMAL] != null and (arrays[Mesh.ARRAY_NORMAL] as PackedVector3Array).size() == verts
				if not has_bones:
					problems.append("surface %d lost bones/weights" % s)
				if not has_normals:
					problems.append("surface %d has no normals" % s)
				var extras: Variant = material.get_meta("extras") if material != null and material.has_meta("extras") else null
				summary.append("%s(verts=%d uv2=%s extras=%s)" % [mat_name, verts, has_uv2, extras != null])
				if mat_name == "FaceMat":
					face_surfaces += 1
					if not has_uv2:
						problems.append("FaceMat surface has no UV2")
					else:
						var lo := Vector2(INF, INF)
						var hi := Vector2(-INF, -INF)
						for uv: Vector2 in arrays[Mesh.ARRAY_TEX_UV2] as PackedVector2Array:
							lo = lo.min(uv)
							hi = hi.max(uv)
						var tex: Texture2D = (material as BaseMaterial3D).albedo_texture if material is BaseMaterial3D else null
						summary.append("FaceMat uv2=[%s..%s] albedo=%s" % [lo, hi, tex.get_size() if tex != null else "none"])
						if hi.x - lo.x < 0.5 or hi.y - lo.y < 0.5:
							problems.append("FaceMat UV2 spans less than half the cell (UV channels swapped?)")
						if extras is Dictionary and bool((extras as Dictionary).get("atlas", false)) and not (material.has_meta("face_atlas") and material.get_meta("face_atlas") is Texture2D):
							problems.append("FaceMat declares an atlas but none was attached")
		if face_surfaces != 1:
			problems.append("expected one FaceMat surface, found %d" % face_surfaces)
		var bones: int = skeleton.get_bone_count() if skeleton != null else 0
		if problems.is_empty():
			print("FACE_GLB OK %s bones=%d surfaces=%d %s" % [path, bones, surfaces, ", ".join(summary)])
		else:
			failures += 1
			print("FACE_GLB FAIL %s: %s | %s" % [path, "; ".join(problems), ", ".join(summary)])
		scene.free()
	quit(0 if failures == 0 else 1)
