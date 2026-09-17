import pytest
from bl_ext.user_default.pcb3d_importer.solder_joints import solder_joint_smd
from bl_ext.user_default.pcb3d_importer.solder_profiles import (
    SmdSolderProfile,
    match_solder_profile,
    parse_solder_profiles,
)

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree


def test_solder_rules_match_reference_and_pad_size():
    profiles = parse_solder_profiles(
        {
            "J2": [
                {"height": 0.24, "terminal_size": [0.2, 0.4], "pad_size": [0.35, 0.7]},
                {"height": 0.25, "terminal_size": [0.42, 0.4]},
            ],
        }
    )

    assert match_solder_profile(profiles, "Example_Connector_J2_1_2", (0.35000001, 0.7)) == (
        SmdSolderProfile(0.24, (0.2, 0.4), pad_size=(0.35, 0.7))
    )
    assert match_solder_profile(profiles, "Example_Connector_J2_1_0", (0.65, 0.7)).height == 0.25
    assert match_solder_profile(profiles, "10k_R2_2_0", (0.35, 0.7)) is None
    assert match_solder_profile(profiles, "unidentified", (0.35, 0.7)) is None


def test_solder_rules_reject_ambiguous_matches():
    profile = SmdSolderProfile(0.25, (0.42, 0.4))
    with pytest.raises(ValueError, match="ambiguous solder rules"):
        match_solder_profile({"J2": (profile, profile)}, "Example_Connector_J2_1_0", (0.65, 0.7))


@pytest.mark.parametrize(
    "rule",
    (
        pytest.param({"height": -1, "terminal_size": [0.2, 0.4]}, id="negative-height"),
        pytest.param({"height": float("nan"), "terminal_size": [0.2, 0.4]}, id="nonfinite-height"),
        pytest.param({"height": True, "terminal_size": [0.2, 0.4]}, id="boolean-height"),
        pytest.param({"height": 0.2}, id="missing-terminal"),
        pytest.param({"height": 0.2, "terminal_size": [0, 0.4]}, id="zero-width"),
        pytest.param(
            {"height": 0.2, "terminal_size": [0.2, 0.4], "mode": "guess"}, id="unknown-field"
        ),
    ),
)
def test_solder_rules_reject_invalid_dimensions(rule):
    with pytest.raises(ValueError):
        parse_solder_profiles({"J2": rule})


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize("placed", (False, True), ids=("local", "rotated-bottom"))
@pytest.mark.parametrize(
    "size, terminal, offset, height, terminal_height",
    (
        pytest.param((0.35, 0.7), (0.2, 0.36), (0.0, -0.14), 0.24, 0.17, id="narrow-terminal"),
        pytest.param((0.65, 0.7), (0.42, 0.36), (0.0, -0.14), 0.24, 0.17, id="wide-terminal"),
        pytest.param((0.74, 2.79), (0.406, 1.8), (0.0, 0.0), 0.55, 0.41, id="header"),
    ),
)
def test_smd_profile_covers_terminal_surface(
    size, terminal, offset, height, terminal_height, placed
):
    result = bpy.ops.pcb2blender.solder_joint_add(
        pad_type="SMD",
        pad_shape="RECTANGULAR",
        pad_size=size,
        smd_height=height,
        terminal_size=terminal,
        terminal_offset=offset,
    )
    assert result == {"FINISHED"}
    obj = bpy.context.object
    if placed:
        obj.location = (0.09, -0.08, 0.0)
        obj.rotation_euler.z = 0.71
        obj.scale.z = -1
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        assert all(edge.is_manifold for edge in editable.edges)
        bvh = BVHTree.FromPolygons(
            [vertex.co[:] for vertex in mesh.vertices],
            [face.vertices[:] for face in mesh.polygons],
        )
        for fraction in np.linspace(-0.5, 0.5, 7):
            for x_coord, y_coord in (
                (fraction * terminal[0], -terminal[1] * 0.5),
                (fraction * terminal[0], terminal[1] * 0.5),
                (-terminal[0] * 0.5, fraction * terminal[1]),
                (terminal[0] * 0.5, fraction * terminal[1]),
            ):
                hit, _, _, _ = bvh.ray_cast(
                    ((x_coord + offset[0]) * 0.001, (y_coord + offset[1]) * 0.001, 0.01),
                    (0, 0, -1),
                )
                assert hit is not None
                assert hit.z * 1000 - 0.8 > terminal_height
    finally:
        editable.free()
        evaluated.to_mesh_clear()


@pytest.mark.parametrize(
    "size, roundness, terminal",
    (
        pytest.param((0.35, 0.7), 0.0, (0.6, 0.36), id="wide-terminal"),
        pytest.param((1.0, 1.0), 1.0, (0.8, 0.8), id="outside-circular-contour"),
    ),
)
def test_smd_profile_requires_a_fitting_terminal(size, roundness, terminal):
    with pytest.raises(ValueError, match="must fit inside"):
        solder_joint_smd(np.array(size), roundness, profile=SmdSolderProfile(0.24, terminal))


@pytest.mark.parametrize(
    "data",
    (
        pytest.param([], id="non-table"),
        pytest.param({"J2": []}, id="empty-rules"),
        pytest.param({"J2": "invalid"}, id="non-table-rule"),
        pytest.param(
            {
                "J2": {
                    "height": 0.2,
                    "terminal_size": [0.2, 0.4],
                    "terminal_offset": [float("inf"), 0],
                }
            },
            id="nonfinite-offset",
        ),
    ),
)
def test_solder_profiles_reject_invalid_tables(data):
    with pytest.raises(ValueError):
        parse_solder_profiles(data)
