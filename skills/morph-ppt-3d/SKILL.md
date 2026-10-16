---
name: morph-ppt-3d
description: "Extend morph-ppt with 3D GLB models — cinematographic camera moves, model-content layout patterns, 3D Morph transitions."
---

# OfficeCLI Morph PPT 3D Skill

**This skill extends `morph-ppt`.** Load `morph-ppt` and `officecli-pptx` first for base rules.

## Setup

Follow install instructions from `officecli` skill. The `.glb` file must be in the same directory as the `.pptx`.

## Key Rules

- Only `.glb` format supported. Each slide must `add --type 3dmodel` fresh (no slide cloning).
- All models share the same `name` prop for morph pairing.
- Model `width × height` area ratio between adjacent slides ≥ 1.5x or ≤ 0.67x.
- Adjacent `roty` delta: 30°-90°. Total arc across deck: 180°-360°.
- Rotx range: -25 to +40. Adjacent rotx delta ≤ 20.

## Layout Patterns

| Pattern | Model position | Content |
|---------|---------------|---------|
| A | Right 15-20cm, 14-18cm wide | Content left |
| B | Left 0-2cm, 14-18cm wide | Content right |
| C | Centered 18-24cm | Text overlay top/bottom |
| D | Small corner 5-10cm | Content dominant |
| E | XL backdrop 28-36cm | Text overlaid on model |
| F | Bleed off edge | Partial model visible |

## Key Operations

```bash
# Add 3D model
officecli add "$FILE" '/slide[1]' --type 3dmodel \
  --prop path=model.glb --prop 'name=!!model-hero' \
  --prop x=16cm --prop y=1cm --prop width=16cm --prop height=16cm \
  --prop roty=30 --prop rotx=8

# Controls: x, y, width, height, name, roty, rotx, rotz
# Do NOT set: meterPerModelUnit, preTrans, camera depth
```
