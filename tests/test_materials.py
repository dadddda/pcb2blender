from collections.abc import Callable
from pathlib import Path

import pytest
from bl_ext.user_default.pcb3d_importer.materials import (
    MaterialGrain,
    MaterialMap,
    MaterialProfile,
    enhance_component_materials,
    load_material_map,
)

import bpy


def test_material_map_loads_global_assignments(write_material_map: Callable[[str], Path]):
    path = write_material_map(
        '[materials]\n"IC-BODY-EPOXY-04" = "plastic-traffic_black-matte"\n',
    )

    material_map = load_material_map(path)

    assert material_map.materials == {
        "IC-BODY-EPOXY-04": MaterialProfile("plastic-traffic_black-matte")
    }


def test_material_map_loads_component_profiles(write_material_map: Callable[[str], Path]):
    path = write_material_map(
        """
[profiles.ic_body]
material = "plastic"
color = "custom"
custom_color = "#0e0e10"
finish = "matte"
bevel = false
texture_strength = 0.2
scratches = 0.1

[profiles.ic_body.grain]
scale = 220.0
detail = 3.0
roughness = 0.7
distortion = 0.15
strength = 0.18
distance = 0.025

[components."ExampleModel".materials]
SHAPE_1 = "ic_body"
""",
    )

    material_map = load_material_map(path)

    assert material_map.components["ExampleModel"]["SHAPE_1"] == MaterialProfile(
        "plastic-custom_0e0e10-matte",
        False,
        0.2,
        0.1,
        MaterialGrain(220.0, 3.0, 0.7, 0.15, 0.18, 0.025),
    )


def test_material_map_loads_pcb_theme(write_material_map: Callable[[str], Path]):
    path = write_material_map(
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
    assert theme.surface_finish is not None
    assert theme.surface_finish.preset == "ENIG"
    assert theme.solder_mask is not None
    assert theme.solder_mask.light_color == pytest.approx((0x12 / 255, 0x34 / 255, 0x56 / 255))
    assert theme.board_edge is not None
    assert theme.board_edge.mix == pytest.approx(0.8)
    assert theme.silkscreen_quality == pytest.approx(0.9)


@pytest.mark.usefixtures("empty_scene")
def test_component_profiles_apply_per_model_with_normalized_names():
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
    nodes = []
    for mesh in meshes:
        material = mesh.materials[0]
        assert material is not None and material.node_tree is not None
        nodes.append(
            next(
                node
                for node in material.node_tree.nodes
                if node.bl_idname == "ShaderNodeBsdfMat4cad"
            )
        )
        assert "Grain Noise" not in material.node_tree.nodes
        assert "Grain Bump" not in material.node_tree.nodes
    assert (nodes[0].mat_color, nodes[0].mat_variant) == ("JET_BLACK", "MATTE")
    assert (nodes[1].mat_color, nodes[1].mat_variant) == ("PURE_WHITE", "SEMI_MATTE")
    assert nodes[1].inputs["Texture Strength"].default_value == pytest.approx(0.2)
    assert nodes[1].inputs["Scratches"].default_value == pytest.approx(0.1)


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "grain_options, expected",
    (
        pytest.param(
            """noise_dimensions = "3D"
noise_type = "fBM"
normalize = true
scale = 200.0
detail = 3.0
roughness = 0.7
lacunarity = 1.0
distortion = 0.0
invert = false
strength = 1.0
distance = 1.0
filter_width = 0.1
""",
            MaterialGrain(
                scale=200.0,
                detail=3.0,
                roughness=0.7,
                lacunarity=1.0,
                distortion=0.0,
                strength=1.0,
                distance=1.0,
            ),
            id="fbm-grain",
        ),
        pytest.param(
            """noise_dimensions = "2D"
noise_type = "MULTIFRACTAL"
normalize = false
invert = true
lacunarity = 1.5
filter_width = 0.25
distance = 0.025
""",
            MaterialGrain(
                noise_dimensions="2D",
                noise_type="MULTIFRACTAL",
                normalize=False,
                invert=True,
                lacunarity=1.5,
                filter_width=0.25,
                distance=0.025,
            ),
            id="multifractal-grain",
        ),
        pytest.param(
            "distance = 0.0\nstrength = 0.0\n",
            MaterialGrain(distance=0.0, strength=0.0),
            id="zero-bump",
        ),
        pytest.param("", MaterialGrain(distance=0.001), id="default-controls"),
    ),
)
def test_grain_controls_configure_shader_nodes(
    write_material_map: Callable[[str], Path], grain_options: str, expected: MaterialGrain
):
    path = write_material_map(
        '[profiles.ic_body]\nmaterial = "plastic"\ncolor = "jet_black"\nfinish = "matte"\n'
        "[profiles.ic_body.grain]\n"
        + grain_options
        + '\n[components.ICModel.materials]\nIC-BODY = "ic_body"\n'
    )
    material_map = load_material_map(path)
    assert material_map.components["ICModel"]["IC-BODY"].grain == expected
    mesh = bpy.data.meshes.new("ICModel")
    mesh.materials.append(bpy.data.materials.new("IC-BODY"))

    enhance_component_materials((mesh,), material_map)

    tree = mesh.materials[0].node_tree
    noise = tree.nodes["Grain Noise"]
    bump = tree.nodes["Grain Bump"]
    assert noise.noise_dimensions == expected.noise_dimensions
    assert noise.noise_type == expected.noise_type
    assert noise.normalize == expected.normalize
    for socket, value in (
        ("Scale", expected.scale),
        ("Detail", expected.detail),
        ("Roughness", expected.roughness),
        ("Lacunarity", expected.lacunarity),
        ("Distortion", expected.distortion),
    ):
        assert noise.inputs[socket].default_value == pytest.approx(value)
    assert bump.invert == expected.invert
    assert bump.inputs["Strength"].default_value == pytest.approx(expected.strength)
    assert bump.inputs["Distance"].default_value == pytest.approx(expected.distance)
    assert bump.inputs["Filter Width"].default_value == pytest.approx(expected.filter_width)
    assert bump.inputs["Height"].links[0].from_socket == noise.outputs["Fac"]
    mat4cad = next(node for node in tree.nodes if node.bl_idname == "ShaderNodeBsdfMat4cad")
    assert mat4cad.inputs["Normal"].links[0].from_socket == bump.outputs["Normal"]


@pytest.mark.usefixtures("empty_scene")
def test_component_profile_adds_configured_grain_nodes():
    material = bpy.data.materials.new("IC-BODY")
    mesh = bpy.data.meshes.new("ICModel")
    mesh.materials.append(material)
    grain = MaterialGrain(220.0, 3.0, 0.7, 0.15, 0.18, 0.025)
    material_map = MaterialMap(
        components={
            "ICModel": {
                "IC-BODY": MaterialProfile("plastic-jet_black-matte", True, 0.15, 0.05, grain)
            }
        }
    )

    configured = enhance_component_materials((mesh,), material_map)

    assert len(configured) == 1
    configured_material = mesh.materials[0]
    assert configured_material is not None and configured_material.node_tree is not None
    node_tree = configured_material.node_tree
    mat4cad = next(node for node in node_tree.nodes if node.bl_idname == "ShaderNodeBsdfMat4cad")
    noise = node_tree.nodes["Grain Noise"]
    bump = node_tree.nodes["Grain Bump"]
    assert noise.bl_idname == "ShaderNodeTexNoise"
    assert noise.noise_dimensions == "3D"
    assert noise.noise_type == "FBM"
    assert noise.normalize is True
    assert noise.inputs["Lacunarity"].default_value == pytest.approx(2.0)
    assert noise.inputs["Scale"].default_value == pytest.approx(220.0)
    assert noise.inputs["Detail"].default_value == pytest.approx(3.0)
    assert noise.inputs["Roughness"].default_value == pytest.approx(0.7)
    assert noise.inputs["Distortion"].default_value == pytest.approx(0.15)
    assert bump.inputs["Strength"].default_value == pytest.approx(0.18)
    assert bump.inputs["Distance"].default_value == pytest.approx(0.025)
    assert bump.invert is False
    assert bump.inputs["Filter Width"].default_value == pytest.approx(0.1)
    assert noise.outputs["Fac"].is_linked
    assert bump.inputs["Height"].links[0].from_node == noise
    assert mat4cad.inputs["Normal"].links[0].from_node == bump


@pytest.mark.parametrize(
    "options, message",
    (
        pytest.param('noise_dimensions = "5D"', "noise_dimensions must be one of", id="dimensions"),
        pytest.param('noise_type = "TURBULENCE"', "noise_type must be one of", id="noise-type"),
        pytest.param("normalize = 1", "normalize must be true or false", id="normalize"),
        pytest.param('invert = "false"', "invert must be true or false", id="invert"),
        pytest.param("lacunarity = -1.0", "lacunarity cannot be negative", id="lacunarity"),
        pytest.param("filter_width = -0.1", "filter_width cannot be negative", id="filter-width"),
        pytest.param("distance = -1.0", "distance cannot be negative", id="distance"),
        pytest.param(
            "distance_mm = 1.0",
            "unknown grain options",
            id="unsupported-option",
        ),
        pytest.param("scale = nan", "scale must be a finite number", id="nan"),
        pytest.param("distance = inf", "distance must be a finite number", id="infinity"),
        pytest.param("filter_size = 0.1", "unknown grain options", id="unknown-option"),
    ),
)
def test_material_map_rejects_invalid_grain_controls(
    write_material_map: Callable[[str], Path], options: str, message: str
):
    path = write_material_map(
        '[profiles.ic_body]\nmaterial = "plastic"\ncolor = "jet_black"\nfinish = "matte"\n'
        "[profiles.ic_body.grain]\n" + options
    )

    with pytest.raises(ValueError, match=message):
        load_material_map(path)


@pytest.mark.parametrize(
    "contents, message",
    (
        pytest.param("not valid toml", "could not load material map", id="invalid-toml"),
        pytest.param(
            'name = "missing table"\n',
            "must contain materials, profiles, components, or pcb",
            id="missing-configuration",
        ),
        pytest.param(
            '[materials]\n"IC-BODY-EPOXY-04" = "not-a-material"\n',
            "unknown Mat4CAD material",
            id="unknown-material",
        ),
        pytest.param(
            """
[profiles.ic_body]
material = "plastic"
color = "jet_black"
finish = "matte"

[profiles.ic_body.grain]
strength = 1.5
""",
            "grain strength must be between 0 and 1",
            id="invalid-grain-strength",
        ),
    ),
)
def test_material_map_rejects_invalid_configuration(
    write_material_map: Callable[[str], Path], contents: str, message: str
):
    path = write_material_map(contents)

    with pytest.raises(ValueError, match=message):
        load_material_map(path)
