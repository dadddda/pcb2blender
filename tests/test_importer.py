from collections.abc import Callable
from itertools import chain, product
from pathlib import Path
from typing import Any

import pytest
from bl_ext.user_default.pcb3d_importer.importer import (
    resolve_hole_shape,
    resolve_material_map_path,
)
from bl_ext.user_default.pcb3d_importer.pcb3d import DrillShape

import bpy

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
