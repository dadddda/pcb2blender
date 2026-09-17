from collections.abc import Callable
from dataclasses import replace
from itertools import chain, product
from pathlib import Path
from typing import Any

import bl_ext.user_default.pcb3d_importer.importer as importer_module
import pytest
from bl_ext.user_default.pcb3d_importer.importer import (
    resolve_hole_shape,
    resolve_material_map_path,
)
from bl_ext.user_default.pcb3d_importer.pcb3d import PCB3D, DrillShape, PadShape, PadType

import bpy
import numpy as np

PCB_FILEPATHS = sorted((Path(__file__).parent / "test_pcbs").resolve().glob("**/*.pcb3d"))

IMPORT_BOOLEAN_OPTIONS = {
    "import_components": (True, False),
    "center_boards": (True, False),
    "cut_boards": (True, False),
    "stack_boards": (True, False),
}

IMPORT_OPTION_VARIANTS = {
    "add_solder_joints": ("NONE", "SMART", "ALL"),
    "merge_materials": (True, False),
    "enhance_materials": (False, True),
    "texture_dpi": (508, 1016),
}

IMPORT_OPTION_CASES = list(
    chain(
        (
            {key: value for key, value in zip(IMPORT_BOOLEAN_OPTIONS, permutation)}
            | {key: values[0] for key, values in IMPORT_OPTION_VARIANTS.items()}
            for permutation in product(*IMPORT_BOOLEAN_OPTIONS.values())
        ),
        (
            {
                key: values[0]
                for key, values in (IMPORT_BOOLEAN_OPTIONS | IMPORT_OPTION_VARIANTS).items()
            }
            | {key: value}
            for key, values in IMPORT_OPTION_VARIANTS.items()
            for value in values[1:]
        ),
    )
)


def pcb2blender_import_pcb3d(**kwargs: Any) -> set[str]:
    return bpy.ops.pcb2blender.import_pcb3d(**kwargs)  # pyright: ignore[reportAttributeAccessIssue]


def assert_imported_scene(result: set[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert result == {"FINISHED"}
    errors = capsys.readouterr().err
    assert not errors, errors
    assert bpy.context.view_layer is not None
    assert len(bpy.context.view_layer.objects) > 0
    assert bpy.context.object is not None


def assert_shader_nodes_defined() -> None:
    undefined_nodes = [
        (group.name, node.name)
        for group in bpy.data.node_groups
        if group.type == "SHADER"
        for node in group.nodes
        if node.bl_idname == "NodeUndefined"
    ]
    assert not undefined_nodes, undefined_nodes


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize("path", PCB_FILEPATHS, ids=lambda path: path.stem)
@pytest.mark.filterwarnings("ignore:.*U.*mode is deprecated:DeprecationWarning")
def test_importer_creates_scene_objects(capsys: pytest.CaptureFixture[str], path: Path):
    result = pcb2blender_import_pcb3d(filepath=str(path))

    assert_imported_scene(result, capsys)


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize("enhance_materials", (False, True), ids=("original-materials", "enhanced-materials"))
def test_importer_applies_selective_solder_profiles(
    write_material_map, monkeypatch, capsys, enhance_materials
):
    from_file = PCB3D.from_file
    captured = {}

    def with_connector_pads(*args, **kwargs):
        pcb = from_file(*args, **kwargs)
        source = next(iter(pcb.pads.values()))
        pad = replace(
            source, pad_type=PadType.SMD, shape=PadShape.RECT, size=(0.74, 2.79),
            is_flipped=False, rotation=0.0, has_model=True, has_paste=True, is_tht_or_smd=True,
        )
        pcb.pads = {
            "Connector_J3_0_0": pad,
            "Connector_J3_0_1": replace(pad, rotation=1.2, is_flipped=True),
            "Other_J4_1_0": pad,
            "Mount_J3_0_2": replace(pad, pad_type=PadType.THT, drill_size=(0.4, 0.8)),
        }
        captured["thickness"] = pcb.stackup.thickness_mm
        return pcb

    monkeypatch.setattr(PCB3D, "from_file", with_connector_pads)
    path = write_material_map(
        """
[solder.J3]
height = 0.55
terminal_size = [0.406, 1.8]
terminal_offset = [0.0, -0.1]
"""
    )
    result = pcb2blender_import_pcb3d(
        filepath=str(PCB_FILEPATHS[0]), material_map_selection="MANUAL",
        material_map_path=str(path), enhance_materials=enhance_materials,
        center_boards=False, cut_boards=False, stack_boards=False,
    )
    assert_imported_scene(result, capsys)
    configured = bpy.data.objects["SOLDER_Connector_J3_0_0"]
    flipped = bpy.data.objects["SOLDER_Connector_J3_0_1"]
    default = bpy.data.objects["SOLDER_Other_J4_1_0"]
    mounting = bpy.data.objects["SOLDER_Mount_J3_0_2"]
    assert configured.data == flipped.data
    assert configured.data != default.data
    assert configured.data != mounting.data
    assert configured.scale.z == 1
    assert flipped.scale.z == -1
    assert flipped.rotation_euler.z == pytest.approx(1.2)
    half_thickness = captured["thickness"] * 0.5
    assert max(vertex.co.z for vertex in configured.data.vertices) * 1000 == pytest.approx(
        half_thickness + 0.55
    )
    assert max(vertex.co.z for vertex in default.data.vertices) * 1000 <= half_thickness + 0.321
    assert min(vertex.co.z for vertex in mounting.data.vertices) * 1000 < -half_thickness


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize("cut_boards", (False, True), ids=("whole-board", "cut-boards"))
def test_importer_refines_drilled_holes(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, cut_boards: bool
):
    calls = []
    refine = importer_module.refine_board_holes

    def check_refinement(mesh, pads, offset):
        original_vertices = np.array([vertex.co[:] for vertex in mesh.vertices])
        topology = len(mesh.vertices) - len(mesh.edges) + len(mesh.polygons)
        face_count = len(mesh.polygons)
        count = refine(mesh, pads, offset)
        calls.append(count)
        if count:
            assert len(mesh.polygons) > face_count
            assert len(mesh.vertices) > len(original_vertices)
            assert len(mesh.vertices) - len(mesh.edges) + len(mesh.polygons) == topology
            vertices = np.array([vertex.co[:] for vertex in mesh.vertices])
            np.testing.assert_allclose(
                vertices.min(axis=0), original_vertices.min(axis=0), atol=1e-8
            )
            np.testing.assert_allclose(
                vertices.max(axis=0), original_vertices.max(axis=0), atol=1e-8
            )
        return count

    monkeypatch.setattr(importer_module, "refine_board_holes", check_refinement)
    path = next(path for path in PCB_FILEPATHS if path.stem == "extender_custom_colors")
    result = pcb2blender_import_pcb3d(
        filepath=str(path),
        cut_boards=cut_boards,
        center_boards=False,
        stack_boards=False,
        add_solder_joints="NONE",
    )

    assert_imported_scene(result, capsys)
    assert sum(calls) > 0
    for obj in bpy.context.scene.objects:
        assert obj.modifiers.get("PCB Subdivision") is None
        if obj.type != "MESH" or "pcb_board_edge" not in obj.data.attributes:
            continue
        assert len(obj.data.uv_layers) > 0
        uv = np.array([item.uv[:] for item in obj.data.uv_layers[0].data])
        coords = np.array(
            [
                (obj.matrix_world @ obj.data.vertices[loop.vertex_index].co).xy[:]
                for loop in obj.data.loops
            ]
        )
        assert np.all(np.isfinite(uv))
        for axis in (0, 1):
            coefficients = np.linalg.lstsq(
                np.column_stack((coords[:, axis], np.ones(len(coords)))), uv[:, axis], rcond=None
            )[0]
            np.testing.assert_allclose(
                coords[:, axis] * coefficients[0] + coefficients[1], uv[:, axis], atol=2e-5
            )
        assert "pcb_through_holes" in obj.data.attributes


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "options",
    IMPORT_OPTION_CASES,
    ids=lambda options: ",".join(f"{name}={value}" for name, value in options.items()),
)
@pytest.mark.filterwarnings("ignore:.*U.*mode is deprecated:DeprecationWarning")
def test_importer_supports_import_options(
    capsys: pytest.CaptureFixture[str], options: dict[str, Any]
):
    result = pcb2blender_import_pcb3d(filepath=str(PCB_FILEPATHS[0]), **options)

    assert_imported_scene(result, capsys)


@pytest.mark.usefixtures("empty_scene")
def test_importer_preserves_shader_nodes_on_reload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    scene_path = tmp_path / "imported_pcb.blend"
    result = pcb2blender_import_pcb3d(filepath=str(PCB_FILEPATHS[0]))
    assert_imported_scene(result, capsys)
    assert bpy.ops.wm.save_mainfile(filepath=str(scene_path)) == {"FINISHED"}
    assert_shader_nodes_defined()
    errors = capsys.readouterr().err
    assert not errors, errors

    bpy.ops.wm.read_homefile(use_empty=True)
    assert bpy.ops.wm.open_mainfile(filepath=str(scene_path)) == {"FINISHED"}

    assert_shader_nodes_defined()
    errors = capsys.readouterr().err
    assert not errors, errors


@pytest.mark.parametrize(
    "size, expected_shape",
    (
        pytest.param((0.6, 0.6), "CIRCULAR", id="circular"),
        pytest.param((0.6, 1.2), "OVAL", id="oval"),
    ),
)
def test_hole_shape_is_inferred_from_dimensions(size: tuple[float, float], expected_shape: str):
    assert resolve_hole_shape(DrillShape.UNKNOWN, size) == expected_shape


@pytest.mark.parametrize(
    "selection, selected_file, manual_path, expected_filename",
    (
        pytest.param("AUTO", "", "", "board.materials.toml", id="automatic"),
        pytest.param("FILE", "theme.toml", "", "theme.toml", id="adjacent"),
        pytest.param("MANUAL", "", "custom.toml", "custom.toml", id="manual"),
        pytest.param("NONE", "", "custom.toml", None, id="disabled"),
    ),
)
def test_material_map_selection_resolves_path(
    tmp_path: Path,
    selection: str,
    selected_file: str,
    manual_path: str,
    expected_filename: str | None,
):
    pcb_path = tmp_path / "board.pcb3d"
    automatic_path = tmp_path / "board.materials.toml"
    automatic_path.touch()
    expected_path = tmp_path / expected_filename if expected_filename else None

    path = resolve_material_map_path(pcb_path, selection, selected_file, manual_path)

    assert path == expected_path


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "selection",
    (
        pytest.param("MANUAL", id="manual"),
        pytest.param("AUTO", id="automatic-path-fallback"),
    ),
)
def test_importer_applies_pcb_theme(
    write_material_map: Callable[[str], Path], capsys: pytest.CaptureFixture[str], selection: str
):
    material_map_path = write_material_map(
        """
[pcb]
silkscreen_quality = 0.9

[pcb.surface_finish]
preset = "CUSTOM"
color = "#e7cd8c"
roughness = 0.1
texture_strength = 0.3

[pcb.solder_mask]
preset = "CUSTOM"
light_color = "#123456"
dark_color = "#091a2b"
roughness = 0.6
texture_strength = 0.4

[pcb.silkscreen]
preset = "BLACK"
texture_strength = 0.3
""",
    )

    result = pcb2blender_import_pcb3d(
        filepath=str(PCB_FILEPATHS[0]),
        material_map_selection=selection,
        material_map_path=str(material_map_path),
    )

    assert_imported_scene(result, capsys)
    material = next(
        material
        for material in bpy.data.materials
        if material.node_tree and "solder_mask" in material.node_tree.nodes
    )
    assert material.node_tree
    surface_finish = material.node_tree.nodes["exposed_copper"]
    mask = material.node_tree.nodes["solder_mask"]
    silkscreen = material.node_tree.nodes["silkscreen"]
    shader = material.node_tree.nodes["shader"]
    assert surface_finish.surface_finish == "CUSTOM"
    assert surface_finish.inputs["Color"].default_value[:3] == pytest.approx(
        (0.799103, 0.610496, 0.262251), abs=1e-5
    )
    assert surface_finish.inputs["Roughness"].default_value == pytest.approx(0.1)
    assert surface_finish.inputs["Texture Strength"].default_value == pytest.approx(0.3)
    assert mask.soldermask == "CUSTOM"
    assert mask.inputs["Roughness"].default_value == pytest.approx(0.6)
    assert mask.inputs["Texture Strength"].default_value == pytest.approx(0.4)
    assert silkscreen.silkscreen == "BLACK"
    assert silkscreen.inputs["Texture Strength"].default_value == pytest.approx(0.3)
    assert shader.inputs["Silkscreen Quality"].default_value == pytest.approx(0.9)


@pytest.mark.usefixtures("empty_scene")
def test_importer_applies_component_grain(
    write_material_map: Callable[[str], Path], capsys: pytest.CaptureFixture[str]
):
    material_map_path = write_material_map(
        """
[profiles.ic_body]
material = "plastic"
color = "jet_black"
finish = "matte"

[profiles.ic_body.grain]
noise_dimensions = "3D"
noise_type = "FBM"
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

[components."MSOP-16_3x4mm_P0.5mm".materials]
IC-BODY-EPOXY-04 = "ic_body"
""",
    )

    result = pcb2blender_import_pcb3d(
        filepath=str(PCB_FILEPATHS[0]),
        material_map_selection="MANUAL",
        material_map_path=str(material_map_path),
    )

    assert_imported_scene(result, capsys)
    mesh = bpy.data.meshes["MSOP-16_3x4mm_P0.5mm"]
    material = next(material for material in mesh.materials if material.name.startswith("IC-BODY"))
    assert material is not None and material.node_tree is not None
    node_tree = material.node_tree
    noise = node_tree.nodes["Grain Noise"]
    bump = node_tree.nodes["Grain Bump"]
    mat4cad = next(node for node in node_tree.nodes if node.bl_idname == "ShaderNodeBsdfMat4cad")
    assert noise.noise_dimensions == "3D"
    assert noise.noise_type == "FBM"
    assert noise.normalize is True
    assert noise.inputs["Scale"].default_value == pytest.approx(200.0)
    assert noise.inputs["Detail"].default_value == pytest.approx(3.0)
    assert noise.inputs["Roughness"].default_value == pytest.approx(0.7)
    assert noise.inputs["Lacunarity"].default_value == pytest.approx(1.0)
    assert noise.inputs["Distortion"].default_value == pytest.approx(0.0)
    assert bump.invert is False
    assert bump.inputs["Strength"].default_value == pytest.approx(1.0)
    assert bump.inputs["Distance"].default_value == pytest.approx(1.0)
    assert bump.inputs["Filter Width"].default_value == pytest.approx(0.1)
    assert bump.inputs["Height"].links[0].from_node == noise
    assert mat4cad.inputs["Normal"].links[0].from_node == bump


@pytest.mark.usefixtures("empty_scene")
def test_importer_applies_adjacent_material_map(
    tmp_path: Path,
    write_material_map: Callable[[str], Path],
    capsys: pytest.CaptureFixture[str],
):
    pcb_path = tmp_path / "board.pcb3d"
    pcb_path.write_bytes(PCB_FILEPATHS[0].read_bytes())
    material_map_path = write_material_map(
        """
[pcb.surface_finish]
preset = "CUSTOM"
color = "#e7cd8c"
roughness = 0.15
texture_strength = 1.0
""",
    )

    result = pcb2blender_import_pcb3d(
        filepath=str(pcb_path),
        material_map_selection="FILE",
        material_map_file=material_map_path.name,
    )

    assert_imported_scene(result, capsys)
    material = next(
        material
        for material in bpy.data.materials
        if material.node_tree and "exposed_copper" in material.node_tree.nodes
    )
    assert material.node_tree
    surface_finish = material.node_tree.nodes["exposed_copper"]
    assert surface_finish.surface_finish == "CUSTOM"
    assert surface_finish.inputs["Color"].default_value[:3] == pytest.approx(
        (0.799103, 0.610496, 0.262251), abs=1e-5
    )
    assert surface_finish.inputs["Roughness"].default_value == pytest.approx(0.15)
    assert surface_finish.inputs["Texture Strength"].default_value == pytest.approx(1.0)
