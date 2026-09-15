from math import cos, pi, sin

import pytest
from bl_ext.user_default.pcb3d_importer.board_geometry import (
    HOLE_CHORD_ERROR_MM,
    hole_rim_loops,
    refine_board_holes,
)
from bl_ext.user_default.pcb3d_importer.pcb3d import DrillShape, Pad, PadShape, PadType

import bmesh
import bpy
import numpy as np


def make_drilled_board(size, angle, offset, pad_type=PadType.THT):
    radius = min(size) / 2
    half_line = (np.array(size) - min(size)) / 2
    center = np.array((10.0, -20.0)) - offset
    rotation = np.array(((cos(angle), -sin(angle)), (sin(angle), cos(angle))))
    directions = np.array([(cos(step * pi / 8), sin(step * pi / 8)) for step in range(16)])
    local = directions * (radius + 0.0127) + np.sign(directions) * half_line
    inner = local @ rotation.T + center
    outer = directions * 4 + center
    vertices = [
        (*point, height) for height in (-0.8, 0.8) for ring in (outer, inner) for point in ring
    ]
    faces = []
    for index in range(16):
        following = (index + 1) % 16
        faces.extend(
            (
                (index, following, following + 16, index + 16),
                (index + 32, index + 48, following + 48, following + 32),
                (index, index + 32, following + 32, following),
                (index + 16, following + 16, following + 48, index + 48),
            )
        )
    mesh = bpy.data.meshes.new("DrilledBoard")
    mesh.from_pydata((np.array(vertices) * 0.001).tolist(), [], faces)
    editable = bmesh.new()
    editable.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(editable, faces=editable.faces)
    edge_tag = editable.faces.layers.int.new("pcb_board_edge")
    hole_tag = editable.faces.layers.int.new("pcb_through_holes")
    for face in editable.faces:
        face[edge_tag] = int(abs(face.normal.z) < 0.01)
        face[hole_tag] = int(face.index % 4 == 3)
        face.material_index = face.index % 4
    editable.to_mesh(mesh)
    editable.free()
    pad = Pad(
        position=(10.0, 20.0),
        is_flipped=False,
        has_model=True,
        is_tht_or_smd=True,
        has_paste=False,
        pad_type=pad_type,
        shape=PadShape.OVAL,
        size=(size[0] + 0.6, size[1] + 0.6),
        rotation=angle,
        roundness=1.0,
        drill_shape=DrillShape.CIRCULAR if size[0] == size[1] else DrillShape.OVAL,
        drill_size=size,
    )
    return mesh, pad, center, rotation, half_line


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "size, angle, offset, pad_type",
    (
        pytest.param((1.0, 1.0), 0.0, (0.0, 0.0), PadType.THT, id="circle"),
        pytest.param((3.2, 3.2), 0.0, (1.0, -2.0), PadType.NPTH, id="mounting-hole"),
        pytest.param((0.6, 1.2), 0.0, (0.0, 0.0), PadType.THT, id="vertical-slot"),
        pytest.param((0.6, 1.2), pi / 2, (5.0, -8.0), PadType.THT, id="rotated-slot"),
        pytest.param((2.0, 0.8), 0.65, (0.0, 0.0), PadType.THT, id="angled-slot"),
    ),
)
def test_drilled_hole_rims_follow_curves(size, angle, offset, pad_type):
    mesh, pad, center, rotation, half_line = make_drilled_board(size, angle, offset, pad_type)
    original_vertices = [vertex.co.copy() for vertex in mesh.vertices]
    original_topology = len(mesh.vertices) - len(mesh.edges) + len(mesh.polygons)

    count = refine_board_holes(mesh, (pad,), offset)

    assert count == 1
    assert len(mesh.vertices) > len(original_vertices)
    assert len(mesh.vertices) - len(mesh.edges) + len(mesh.polygons) == original_topology
    for original in original_vertices:
        assert min((vertex.co - original).length for vertex in mesh.vertices) < 1e-8
    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        assert all(edge.is_manifold for edge in editable.edges)
        assert all(face.calc_area() > 1e-16 for face in editable.faces)
        hole_tag = editable.faces.layers.int["pcb_through_holes"]
        for face in editable.faces:
            if face[hole_tag]:
                assert face.material_index == 3
        loops = hole_rim_loops(editable)
        inner_loops = []
        for loop in loops:
            points = np.array([vertex.co.xy[:] for edge in loop for vertex in edge.verts]) * 1000
            if np.linalg.norm(points[0] - center) < 3.0:
                inner_loops.append(loop)
        assert len(inner_loops) == 2
        for loop in inner_loops:
            for edge in loop:
                points = np.array([vertex.co.xy[:] for vertex in edge.verts]) * 1000
                local = (points - center) @ rotation
                distances = np.linalg.norm(local - np.clip(local, -half_line, half_line), axis=1)
                np.testing.assert_allclose(distances, min(size) / 2 + 0.0127, atol=2e-5)
                midpoint = local.mean(axis=0)
                midpoint_radius = np.linalg.norm(
                    midpoint - np.clip(midpoint, -half_line, half_line)
                )
                assert min(size) / 2 + 0.0127 - midpoint_radius <= HOLE_CHORD_ERROR_MM + 2e-5
    finally:
        editable.free()


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "position, size",
    (
        pytest.param((10.0, 20.0), (0.3, 0.3), id="mismatched-size"),
        pytest.param((10.2, 20.0), (1.0, 1.0), id="offset-drill"),
        pytest.param((10.0, 20.0), (0.0, 0.0), id="zero-drill"),
        pytest.param((10.0, 20.0), (float("nan"), 1.0), id="nonfinite-drill"),
    ),
)
def test_unmatched_hole_geometry_is_unchanged(position, size):
    mesh, pad, _, _, _ = make_drilled_board((1.0, 1.0), 0.0, (0.0, 0.0))
    original = [vertex.co[:] for vertex in mesh.vertices]
    pad.position = position
    pad.drill_size = size

    assert refine_board_holes(mesh, (pad,)) == 0
    assert [vertex.co[:] for vertex in mesh.vertices] == original


@pytest.mark.usefixtures("empty_scene")
def test_open_hole_rim_is_unchanged():
    mesh, pad, _, _, _ = make_drilled_board((1.0, 1.0), 0.0, (0.0, 0.0))
    editable = bmesh.new()
    editable.from_mesh(mesh)
    editable.faces.ensure_lookup_table()
    bmesh.ops.delete(editable, geom=[editable.faces[3]], context="FACES_ONLY")
    editable.to_mesh(mesh)
    editable.free()
    original = [vertex.co[:] for vertex in mesh.vertices]

    assert refine_board_holes(mesh, (pad,)) == 0
    assert [vertex.co[:] for vertex in mesh.vertices] == original
