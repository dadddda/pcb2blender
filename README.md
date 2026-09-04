# pcb2blender

[![version](https://img.shields.io/badge/version-2.18.0-blue)](https://github.com/dadddda/pcb2blender)
[![blender](https://img.shields.io/badge/Blender-5.1-orange)](https://www.blender.org/)
[![kicad](https://img.shields.io/badge/KiCad-9.0-blue)](https://www.kicad.org/)
[![license](https://img.shields.io/badge/License-GPLv3-lightgrey)](LICENSE)

<img src="images/header.jpg" alt="Rendered PCB"/>

Export KiCad boards as `.pcb3d` files and import them into Blender for product rendering.

This fork builds on [30350n/pcb2blender](https://github.com/30350n/pcb2blender) with:

- Reusable TOML themes for component materials and PCB shaders.
- Model-scoped material rules for repeated components.
- Improved circular, oval, and slotted THT solder joints.
- Adaptive THT profiles and raised SMD solder beads.
- Compatibility with current KiCad drill-shape metadata.

<img src="images/e201_soldered.jpg" alt="Rendered solder joints"/>

## Quick start

1. In KiCad PCB Editor, select **Export to Blender (.pcb3d)**.
2. In Blender, select **File > Import > PCB (.pcb3d)**.
3. Configure solder-joint and material options, then import.

## Installation

The importer requires Blender 5.1 and the exporter targets KiCad 9.0.

### Blender importer

Build the extension from the repository root:

```powershell
$Blender = "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
uv sync
uv run python download_dependencies.py
New-Item -ItemType Directory -Force .\dist | Out-Null
& $Blender --command extension build `
    --source-dir .\pcb2blender_importer `
    --output-filepath .\dist\pcb2blender_importer.zip
```

Install `dist/pcb2blender_importer.zip` through
**Edit > Preferences > Add-ons > Install from Disk**.

### KiCad exporter

Build the KiCad PCM package from the repository root:

```powershell
uv run python .github/workflows/build_kicad_addon.py `
    --package-only `
    --source pcb2blender_exporter `
    --out dist `
    --icon images/icon.png `
    --extra-files images/blender_icon_32x32.png
```

Install `dist/pcb2blender_exporter_v2-18-0_k9-0.zip` through
**Plugin and Content Manager > Install from File**. For a manual installation, place the contents
of `pcb2blender_exporter` in the
[KiCad plugin directory](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/).

## Material themes

Select a TOML preset using **Material Map** in the Blender import panel:

- **Auto** loads `<board>.materials.toml` beside `<board>.pcb3d`.
- **Choose Adjacent File** lists `.toml` files beside the selected PCB.
- **Manual Path** accepts an absolute or PCB-relative path.
- **None** disables the material map.

Profiles define complete Mat4CAD settings and can be reused by any component model:

```toml
[profiles.ic_body]
material = "plastic"
color = "jet_black"
finish = "matte"
bevel = true
texture_strength = 0.15
scratches = 0.05

[profiles.custom_body]
material = "plastic"
color = "custom"
custom_color = "#2f6f9f"
finish = "semi_matte"
bevel = true
texture_strength = 0.2
scratches = 0.0
```

Assign profiles to a model's normalized material slots. All instances of the model share the rule;
Blender suffixes are ignored, so `SHAPE_1.013` matches `SHAPE_1`:

```toml
[components."LQFP-64_10x10mm_P0.5mm".materials]
SHAPE_1 = "ic_body"
SHAPE_5 = "tin_connection"
```

Legacy global mappings remain supported:

```toml
[materials]
"IC-BODY-EPOXY-04" = "plastic-traffic_black-matte"
"PIN-01" = "special-pins_silver-default"
```

See [material_map.example.toml](material_map.example.toml) for a starter preset.

## PCB themes

The same TOML file can configure the generated PCB shader. Every field is optional; omitted values
come from the KiCad stackup or add-on defaults. Colors are six-digit sRGB hex values and numeric
controls range from `0.0` to `1.0`.

```toml
[pcb]
silkscreen_quality = 0.8

[pcb.base]
material = "pcb"
color = "pcb_yellow"
finish = "default"
bevel = true
texture_strength = 0.5
scratches = 0.5

[pcb.surface_finish]
preset = "ENIG" # HASL, ENIG, NONE, or CUSTOM

[pcb.solder_mask]
preset = "BLACK"
roughness = 0.45
texture_strength = 1.0

[pcb.silkscreen]
preset = "WHITE"
roughness = 0.1
texture_strength = 1.0

[pcb.board_edge]
base_color = "#a4a858"
mix = 0.95
roughness = 0.6
texture_strength = 0.5

[pcb.solder]
color = "#aaaaa6"
roughness = 0.25
texture_strength = 1.0
```

Custom solder masks support `light_color` and `dark_color`; custom silkscreen and surface finishes
support `color`. Explicit color and scalar values override their selected preset.

## Solder-joint profiles

Generated solder joints use a bounded geometry heuristic rather than one fixed profile:

- THT fillet volume is estimated from pad area minus hole area.
- Wider annular rings produce broader and taller fillets.
- Slotted mounting tabs stay shallow, while circular through-hole pins can form taller fillets.
- SMD bead height and edge spread scale with pad area and minimum pad width.
- Smoothstep-interpolated layers approximate a surface-tension meniscus without a costly physics solve.

The `.pcb3d` format does not include exact terminal dimensions or paste volume, so this remains a
visual approximation based on the available pad, hole, shape, and PCB-thickness metadata.

## Development

Initialize the repository and run the Blender test suite:

```powershell
git submodule update --init --recursive
uv sync
uv run pytest `
    --blender-executable "C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" `
    -v tests/
```

The original repository is retained as the `upstream` Git remote. To incorporate upstream changes:

```powershell
git fetch upstream
git merge upstream/master
```

## Upstream and license

This is a fork of [30350n/pcb2blender](https://github.com/30350n/pcb2blender), created by Max
Schlecht. The PCB shader is inspired by
[PCB-Arts/stylized-blender-setup](https://github.com/PCB-Arts/stylized-blender-setup), and the
project name is inspired by [svg2shenzhen](https://github.com/badgeek/svg2shenzhen).

The project is licensed under [GPLv3](LICENSE).
