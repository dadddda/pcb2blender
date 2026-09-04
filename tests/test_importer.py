from itertools import chain, product
from pathlib import Path
from tempfile import gettempdir
from typing import Any

import pytest
from bl_ext.user_default.pcb3d_importer.importer import (
    resolve_hole_shape,
    resolve_material_map_path,
)
from bl_ext.user_default.pcb3d_importer.pcb3d import DrillShape

import bpy

TEST_FILEPATHS = list((Path(__file__).parent / "test_pcbs").resolve().glob("**/*.pcb3d"))

KWARGS_TEST_PERMUTATIONS = {
    "import_components": (True, False),
    "center_boards": (True, False),
    "cut_boards": (True, False),
    "stack_boards": (True, False),
}

KWARGS_TEST_ONCE = {
    "add_solder_joints": ("NONE", "SMART", "ALL"),
    "merge_materials": (True, False),
    "enhance_materials": (False, True),
    "texture_dpi": (508, 1016),
}

TEST_PARAMETERS = chain(
    (
        {key: value for key, value in zip(KWARGS_TEST_PERMUTATIONS.keys(), permutation)}
        | {key: values[0] for key, values in KWARGS_TEST_ONCE.items()}
        for permutation in product(*KWARGS_TEST_PERMUTATIONS.values())
    ),
    (
        {key: values[0] for key, values in (KWARGS_TEST_PERMUTATIONS | KWARGS_TEST_ONCE).items()}
        | {key: value}
        for key, values in KWARGS_TEST_ONCE.items()
        for value in values[1:]
    ),
)


def pcb2blender_import_pcb3d(**kwargs: Any) -> set[str]:
    return bpy.ops.pcb2blender.import_pcb3d(**kwargs)  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.parametrize("path", TEST_FILEPATHS)
@pytest.mark.filterwarnings("ignore:.*U.*mode is deprecated:DeprecationWarning")
def test_importer(capsys: pytest.CaptureFixture[str], path: Path):
    bpy.ops.wm.read_homefile(use_empty=True)

    result = pcb2blender_import_pcb3d(filepath=str(path))

    if error := capsys.readouterr().err:
        raise Exception(error)
    assert result == {"FINISHED"}
    assert bpy.context.view_layer
    assert len(bpy.context.view_layer.objects) > 0
    assert bpy.context.object is not None


@pytest.mark.parametrize("kwargs", TEST_PARAMETERS)
@pytest.mark.filterwarnings("ignore:.*U.*mode is deprecated:DeprecationWarning")
def test_importer_parameters(capsys: pytest.CaptureFixture[str], kwargs: Any):
    bpy.ops.wm.read_homefile(use_empty=True)

    result = pcb2blender_import_pcb3d(filepath=str(TEST_FILEPATHS[0]), **kwargs)

    if error := capsys.readouterr().err:
        raise Exception(error)
    assert result == {"FINISHED"}
    assert bpy.context.view_layer
    assert len(bpy.context.view_layer.objects) > 0
    assert bpy.context.object is not None


def test_load_file(capsys: pytest.CaptureFixture[str]):
    test_path = str(Path(gettempdir()) / "pcb2blender_test.blend")

    bpy.ops.wm.read_homefile(use_empty=True)
    pcb2blender_import_pcb3d(filepath=str(TEST_FILEPATHS[0]))
    bpy.ops.wm.save_mainfile(filepath=test_path)

    assert not has_undefined_nodes()
    if error := capsys.readouterr().err:
        raise Exception(error)

    bpy.ops.wm.read_homefile(use_empty=True)
    bpy.ops.wm.open_mainfile(filepath=test_path)

    assert not has_undefined_nodes()
    if error := capsys.readouterr().err:
        raise Exception(error)


def has_undefined_nodes():
    node_groups = (group for group in bpy.data.node_groups if group.type == "SHADER")
    return "NodeUndefined" in (node.bl_idname for group in node_groups for node in group.nodes)


def test_resolve_unknown_hole_shape_from_dimensions():
    assert resolve_hole_shape(DrillShape.UNKNOWN, (0.6, 0.6)) == "CIRCULAR"
    assert resolve_hole_shape(DrillShape.UNKNOWN, (0.6, 1.2)) == "OVAL"


def test_resolve_material_map_selection(tmp_path: Path):
    pcb_path = tmp_path / "board.pcb3d"
    automatic_path = tmp_path / "board.materials.toml"
    automatic_path.touch()

    assert resolve_material_map_path(pcb_path, "AUTO", "", "") == automatic_path
    assert resolve_material_map_path(pcb_path, "FILE", "theme.toml", "") == tmp_path / "theme.toml"
    assert resolve_material_map_path(pcb_path, "MANUAL", "", "custom.toml") == tmp_path / "custom.toml"
    assert resolve_material_map_path(pcb_path, "NONE", "", "custom.toml") is None


def test_importer_applies_pcb_theme(tmp_path: Path):
    material_map_path = tmp_path / "materials.toml"
    material_map_path.write_text(
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
        encoding="utf-8",
    )
    bpy.ops.wm.read_homefile(use_empty=True)

    result = pcb2blender_import_pcb3d(
        filepath=str(TEST_FILEPATHS[0]), material_map_path=str(material_map_path)
    )

    assert result == {"FINISHED"}
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


def test_importer_loads_adjacent_material_map(tmp_path: Path):
    pcb_path = tmp_path / "board.pcb3d"
    pcb_path.write_bytes(TEST_FILEPATHS[0].read_bytes())
    material_map_path = tmp_path / "board-theme.toml"
    material_map_path.write_text(
        """
[pcb.surface_finish]
preset = "CUSTOM"
color = "#e7cd8c"
roughness = 0.15
texture_strength = 1.0
""",
        encoding="utf-8",
    )
    bpy.ops.wm.read_homefile(use_empty=True)

    result = pcb2blender_import_pcb3d(
        filepath=str(pcb_path),
        material_map_selection="FILE",
        material_map_file=material_map_path.name,
    )

    assert result == {"FINISHED"}
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
