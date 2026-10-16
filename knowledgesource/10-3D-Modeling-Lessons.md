# 10. 3D Modeling Lessons

> This chapter distills cross-session experience from 3D modeling tasks: handling HS2 (Honey Select 2) character assets exported to Blender — material rebuilding, color fixes, VRM export, etc.
> **Read before taking on a modeling task**: every item below was actually verified; they are direct answers to "how to do it" and "what not to do".

## 1. Structure & Inherent Pitfalls of HS2 Exported Assets

**Source**: a character exported from the game by Grey.MeshExporter (example: `<HS2-install>\Export\20261003155559_五十岚_美和\`, containing a 5.2MB FBX + Textures/ directory).

**Object structure (537)**: 513 EMPTY + 22 MESH + 1 ARMATURE (+1 light added later in the scene).

**❗ Six major pitfalls (the root of the "endless loop")**:

1. **24 objects carry duplicate-name suffixes** — `o_body_cf` is both an EMPTY and a MESH (the mesh was renamed .001); `n_o_root` has 8 copies, `n_clothes_cf_00` 6, `GameObject` 7, `n_bot_a` 3, etc. **Never fetch objects by name**; always filter by type (e.g. `[o for o in bpy.data.objects if o.type == 'MESH']`) or a unique trait.
2. **513 EMPTY noise (96%)** — HS2 generates a full node tree per clothing slot (8 slots: tops/bottoms/inner top/bottom/panties/shoes/body/silhouette), plus 178 FK helpers + 61 physics nodes + 7 colliders. Traversal, selection and batch operations are all disturbed.
3. **The FBX contains no materials** (0 materials, no texture references) — materials must be rebuilt by hand from Textures/; texture naming = game material name_shader variable (e.g. `cf_m_skin_body_00_MainTex.png`; `*_BumpMap_converted.png` is a converted normal map — use Non-Color).
4. **Non-standard coordinate system** — Armature scale=0.1 + rotation X+90° (original import 0.01, deliberately ×10-corrected to normal human size). To change size, edit only the Armature object scale, never mesh vertices.
5. **All 331 bones use_deform** (cf_J_* female body 305 + hair/aux 26), and 32 carry non-identity pose data (imported with the file; do not clear).
6. **Shape keys span multiple meshes** — o_head 59 keys (expression e/g/k series; k18_a~k22_o are mouth shapes), o_namida 59 keys (same names as o_head, linked), o_eyelashes 17, o_tang 12, o_tooth 8; body & clothing 0 keys (body deformation is via bone scaling).

**Other facts**: face points toward -Y; character ~1.6m tall; no separate eyeball mesh (o_eyebase is the eyeball layer, skinned to cf_J_eye_rs_L/R).

## 2. Case: Face Turned Grey (solved 2026-10-03)

**Symptom**: the face color looked grey, inconsistent with the body skin tone.

**Root causes (two overlapping)**:

1. **Desaturating color chain**: the face material M_o_head used a `GAMMA(0.35)` node in its color chain. Experiments confirmed the node's math = `input^0.35` (brightening), but **it boosts dark channels far more than bright ones** (R×4.5, G×7.1, B×8.3) — the three-channel spread was compressed and the skin was "washed" into low-saturation grey. The body used `VECT_MATH MULTIPLY ×5.8` (proportional brightening, saturation preserved).
2. **Wrong image in the normal chain**: the face NORMAL_MAP input was mistakenly connected to the color texture (face_rebuilt) instead of the normal map.

**Fix steps (code verified in practice)**:

```python
import bpy
mat = bpy.data.materials['M_o_head']
nt = mat.node_tree

# 1. Color chain: remove GAMMA, switch to VECT_MATH MULTIPLY ×5.8 (same as body)
gam = next(n for n in nt.nodes if n.type == 'GAMMA')
src_node = gam.inputs['Color'].links[0].from_node
src_sock = gam.inputs['Color'].links[0].from_socket.name
dst_node = gam.outputs['Color'].links[0].to_node
dst_sock = gam.outputs['Color'].links[0].to_socket.name
nt.nodes.remove(gam)

vm = nt.nodes.new('ShaderNodeVectorMath')
vm.name = 'BOOST_MUL'
vm.operation = 'MULTIPLY'
vm.inputs[1].default_value = (5.8, 5.8, 5.8)
nt.links.new(src_node.outputs[src_sock], vm.inputs[0])
nt.links.new(vm.outputs[0], dst_node.inputs[dst_sock])

# 2. Normal chain: use the correct normal map
nm = next(n for n in nt.nodes if n.type == 'NORMAL_MAP')
tex = nm.inputs['Color'].links[0].from_node
tex.image = bpy.data.images.get('cf_m_skin_head_01_BumpMap_converted.png')
```

**Verification data (isolated-copy render, 16×16 grid region averages)**:

| Region | Before | After | Torso reference |
|------|--------|--------|---------|
| Face | (0.58, 0.51, 0.49) grey, low saturation | (0.80, 0.68, 0.65) warm skin | (0.75, 0.61, 0.55) |

After the fix the face material is **fully isomorphic** with the body material (node structure / coefficients / color space all match).

**Note**: the user evaluates results in **Material Preview mode** (viewport shading.type = MATERIAL + studio light), not Solid mode — the viewport refreshes live after edits; evaluate in that mode.

## 3. Key Blender Experiment Conclusions (reusable)

| Conclusion | Experimental evidence |
|------|---------|
| Gamma node math = `input^gamma` (gamma=0.35 brightens); **NOT** 1/gamma | input 0.5 → rendered read-back 0.7847 (=0.5^0.35); input 0.2 → 0.5693 |
| `img.pixels` read-back = sRGB-encoded display values (8-bit byte space) | linear 0.5 → read-back 0.7353 (= sRGB-encode(0.5)) |
| Vector Math MULTIPLY on colors = proportional linear-space scaling | 0.089 × 5.8 ≈ 0.52 linear → ≈ 0.74 displayed (normal skin brightness) |
| **⚠️ Render-engine difference (major)**: the same material chain (Vector Math on colors) behaves differently in Cycles vs EEVEE — Cycles renders the face dark grey (0.2 neutral), EEVEE normal (0.77 warm white = the user's viewport) | Same scene, same material, rendered in both engines for comparison |
| **Verification renders for the user must use EEVEE** (user viewport = Material Preview = EEVEE) | EEVEE headless render successfully reproduced the user's perception |
| Aid: world environment light 1.0 white approximates bright even lighting (reliable in EEVEE only) | — |

## 4. "Getting Worse / Endless Loop" Root Cause & Correct Posture

**Symptom**: trial-and-error edits on a complex scene → hit a pitfall (wrong object / new problem introduced) → fixing breaks something else → every loop leaves junk (debug images, orphan data, unsaved state).

**Correct posture (five rules, verified effective)**:

1. **Isolated verification**: validate complex operations on a copy / in a separate headless process first, never trial-and-edit the main scene.
2. **Scripted + atomic**: one step, one verification; stop on failure; no consecutive patching.
3. **Never fetch objects by name**: 24 duplicate-named objects are pitfall #1; always use type filtering + unique-trait location.
4. **Back up before changes**: `bpy.ops.wm.save_as_mainfile(filepath=..., copy=True)` saves an in-memory copy (does not switch the current file).
5. **Set acceptance criteria**: for color edits use numeric comparison (rendered read-back RGB), not "looks about right".

**Isolated-verification technique notes**:

- Separate process: `subprocess.Popen([blender_exe, "--background", "--factory-startup", "--python", script])`; inside the script `bpy.ops.wm.read_factory_settings(use_empty=True)` + `import_scene.fbx` for a clean import
- Render verification: **EEVEE** (matches the user's viewport; Cycles behaves differently on non-standard material chains — data reference only); 512×512 small renders + brightened world
- Numeric comparison: after read-back, compute 16×16 grid region averages (face vs torso) to judge hue consistency
- Pixel read-back: `img.pixels.foreach_get(np_array)` (numpy-accelerated)

## 5. Material Rebuild Spec (body chain as the standard)

**Standard structure (color + normal chains)**:

- Color chain: `TEX_IMAGE(sRGB) → VECT_MATH(MULTIPLY, ×5.8) → Principled BSDF.Base Color`
- Normal chain: `TEX_IMAGE(Non-Color) → NORMAL_MAP → Principled BSDF.Normal`
- Blend mode: blend_method = HASHED (face/body); M_o_namida uses BLEND

**Provenance of special textures**:

- `face_rebuilt` (2048², packed) — alpha-repaired face texture (original alpha ≈ 0; rebuilt alpha = 1, identical RGB)
- `eye_rebuilt.png` (512², packed) — crudely rebuilt eye texture (pale base + solid purple disc); **already replaced with the game's original `c_m_eye_01_Composite.png`** (grey-pink sclera + gradient purple iris, correct look). Replacement note: the material has several TEX_IMAGE nodes; match precisely by "name contains eye_rebuilt" and do not swap the normal-map node by mistake

**Open observation item**: M_o_eyebase_L/R textures now use Composite (direct, no brightening). Eye-side profile checks look normal: the eyeball sits recessed behind the eyelid (eyelid protrudes 1.4mm), eye slit ~9mm in profile — the "big eyes" look mainly came from the old texture; if it still reads too large, evaluate a geometry approach.

## 6. HS2 → VRM Export (2026-10-03 result, key points)

**Artifact**: `<workspace>\五十岚_美和.vrm` (56MB, VRM 1.0, 54 bones, 18 expression presets).

**Key points**:

- Bone mapping: HS2 naming is special and auto-mapping fails; manual per-bone mapping required (54 bones; e.g. left_eye → cf_J_eye_rs_L)
- Expression mapping: bind.index = shape-key name string (aa→k18_a, blink→e01_close, happy→k01_warai1+e04_warai, etc.); o_namida links with o_head
- Export operator: `bpy.ops.export_scene.vrm(filepath=..., armature_object_name="Armature", ignore_warning=True)`
- Not done: T-pose not corrected, SpringBone not configured, MToon not converted

## 7. Eyebrow Problem & Rebuild (2026-10-03, in progress)

**Background**: the Blender scene had "no eyebrows" (no eyebrow trace in face renders); investigation identified the mechanism and the rebuild began.

**Eyebrow mechanism (identified)**:

1. **Eyebrows are raised geometry on the o_head mesh** — a strip at the brow ridge protruding ~6.6mm beyond the skin (normals facing forward); vertices are lightly controlled by eyebrow bones (cf_J_MayuMid_s_L/R, cf_J_MayuTip_s_L/R; weights ≤0.19, mostly following the head).
2. **No eyebrows at the texture level** — MainTex / face_rebuilt are uniform brown (0.37, 0.26, 0.23) in the "eyebrow UV region"; o_head has 3 UV layers (uv1 main + uv2/uv3 dedicated layers).
3. **The game engine renders eyebrows in a separate render layer** (like the eyelash mechanism); that layer is lost in Blender export/import → the eyebrow region renders as skin color.

**⚠️ Failed lesson: the face-patch coloring approach is unworkable (rolled back)**:

- 157 faces were selected by "brow-ridge region + protrusion (y < -0.0584)" and assigned a dark-brown material
- **Result**: the user's viewport showed a "big black patch on the forehead" — the selection mixed in non-eyebrow faces (forehead/brow transition area); dark brown turns black in Material Preview
- **Lesson: never auto-select eyebrow faces by "3D position + protrusion"** — the protruding brow-ridge surface includes both eyebrow and forehead areas; they cannot be reliably separated this way

**Correct approach (user-specified): build eyebrows as independent geometry** — create two thin strips conforming to the brow-ridge surface (ray-projected onto the surface + offset ~1mm along the normal), as a separate object with a dark material, parented to the Armature and following the head bone.

## 8. Evidence File Locations

| Content | Location |
|------|------|
| Assessment report (user-copied archive) | `<project>\warning\新建 文本文档.txt` |
| Probe/verification data & scripts | `<project>\.pebble\_tmp_fbx_probe\` (scene_state*.json, fbx_import_state*.json, verify2/3_*.png, probe_gamma.py, verify_face_fix*.py, eevee_result.json, final_fix.png, brow_depth_report.txt, eye_side_result.json) |
| Scene file | `<workspace>\未命名.blend` (face fix applied) |
| VRM artifact | `<workspace>\五十岚_美和.vrm` |
| Blender | `<blender-install>\blender.exe` (5.2.2 LTS, MCP addon installed) |

> ⚠️ `_tmp_fbx_probe` is a temporary directory; migrate evidence to a stable location if it must be kept long-term.
