from pathlib import Path

import pytest

import bpy

from bl_ext.user_default.pcb3d_importer.materials import (
    MaterialMap,
    MaterialProfile,
    enhance_component_materials,
    load_material_map,
)


def write_material_map(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


def test_load_material_map(tmp_path: Path):
    path = write_material_map(
        tmp_path / "materials.toml",
        '[materials]\n"IC-BODY-EPOXY-04" = "plastic-traffic_black-matte"\n',
    )

    assert load_material_map(path).materials == {
        "IC-BODY-EPOXY-04": MaterialProfile("plastic-traffic_black-matte")
    }


def test_load_structured_component_profiles(tmp_path: Path):
    path = write_material_map(
        tmp_path / "materials.toml",
        """
[profiles.ic_body]
material = "plastic"
color = "custom"
custom_color = "#0e0e10"
finish = "matte"
bevel = false
texture_strength = 0.2
scratches = 0.1

[components."ExampleModel".materials]
SHAPE_1 = "ic_body"
""",
    )

    material_map = load_material_map(path)
    assert material_map.components["ExampleModel"]["SHAPE_1"] == MaterialProfile(
        "plastic-custom_0e0e10-matte", False, 0.2, 0.1
    )


def test_load_pcb_theme(tmp_path: Path):
    path = write_material_map(
        tmp_path / "materials.toml",
        """
[pcb]
silkscreen_quality = 0.9

[pcb.base]
material = "pcb"
color = "pcb_brown"
finish = "default"
bevel = false
texture_strength = 0.2
scratches = 0.1

[pcb.surface_finish]
preset = "ENIG"
roughness = 0.15
texture_strength = 0.3

[pcb.solder_mask]
preset = "CUSTOM"
light_color = "#123456"
dark_color = "#091a2b"
roughness = 0.6
texture_strength = 0.4

[pcb.silkscreen]
preset = "WHITE"
roughness = 0.2
texture_strength = 0.5

[pcb.board_edge]
base_color = "#a4a858"
mix = 0.8
roughness = 0.7
texture_strength = 0.3

[pcb.solder]
color = "#aaaaa6"
roughness = 0.25
texture_strength = 0.6
""",
    )

    theme = load_material_map(path).pcb
    assert theme is not None
    assert theme.base == MaterialProfile("pcb-pcb_brown-default", False, 0.2, 0.1)
    assert theme.surface_finish and theme.surface_finish.preset == "ENIG"
    assert theme.solder_mask and theme.solder_mask.light_color == pytest.approx(
        (0x12 / 255, 0x34 / 255, 0x56 / 255)
    )
    assert theme.board_edge and theme.board_edge.mix == pytest.approx(0.8)
    assert theme.silkscreen_quality == pytest.approx(0.9)


def test_component_profiles_disambiguate_shared_slot_names():
    shared_material = bpy.data.materials.new("SHAPE_1")
    meshes = [bpy.data.meshes.new(name) for name in ("ICModel.001", "ResistorModel")]
    for mesh in meshes:
        mesh.materials.append(shared_material)

    material_map = MaterialMap(
        components={
            "ICModel": {"SHAPE_1": MaterialProfile("plastic-jet_black-matte")},
            "ResistorModel": {
                "SHAPE_1": MaterialProfile("plastic-pure_white-semi_matte", True, 0.2, 0.1)
            },
        }
    )

    configured = enhance_component_materials(meshes, material_map)

    assert len(configured) == 2
    assert meshes[0].materials[0] != meshes[1].materials[0]
    nodes = [
        next(node for node in mesh.materials[0].node_tree.nodes if node.bl_idname == "ShaderNodeBsdfMat4cad")
        for mesh in meshes
    ]
    assert (nodes[0].mat_color, nodes[0].mat_variant) == ("JET_BLACK", "MATTE")
    assert (nodes[1].mat_color, nodes[1].mat_variant) == ("PURE_WHITE", "SEMI_MATTE")
    assert nodes[1].inputs["Texture Strength"].default_value == pytest.approx(0.2)
    assert nodes[1].inputs["Scratches"].default_value == pytest.approx(0.1)


@pytest.mark.parametrize(
    "contents, message",
    (
        ("not valid toml", "could not load material map"),
        ('name = "missing table"\n', "must contain materials, profiles, components, or pcb"),
        (
            '[materials]\n"IC-BODY-EPOXY-04" = "not-a-material"\n',
            "unknown Mat4CAD material",
        ),
    ),
)
def test_load_material_map_rejects_invalid_presets(
    tmp_path: Path, contents: str, message: str
):
    path = write_material_map(tmp_path / "materials.toml", contents)

    with pytest.raises(ValueError, match=message):
        load_material_map(path)