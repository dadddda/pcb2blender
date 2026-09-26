import pytest
from bl_ext.user_default.pcb3d_importer.solder_joints import (
    SMD_JOINT_HEIGHT_MAX_MM,
    SMD_JOINT_HEIGHT_MIN_MM,
    THT_JOINT_HEIGHT_MAX_MM,
    THT_SLOT_JOINT_HEIGHT_MAX_MM,
    meniscus_layers,
    smd_joint_dimensions,
    smd_pad_edge_expansion,
    solder_joint_smd,
    solder_joint_tht,
    tht_joint_dimensions,
    tht_pad_edge_expansion,
)

import bmesh
import bpy
import numpy as np
from mathutils.bvhtree import BVHTree

PCB_THICKNESS_MM = 1.6
BOARD_SURFACE_Z_MM = PCB_THICKNESS_MM * 0.5


def assert_valid_quad_mesh(vertices: np.ndarray, faces: np.ndarray) -> None:
    assert vertices.ndim == 2 and vertices.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 4
    assert len(vertices) > 0 and len(faces) > 0
    assert np.all(np.isfinite(vertices))
    assert np.issubdtype(faces.dtype, np.integer)
    assert faces.min() >= 0
    assert faces.max() < len(vertices)
    assert all(len(set(face)) == 4 for face in faces)


def test_smd_joint_matches_pad_bounds_and_height():
    pad_size = np.array((4.0, 2.0))
    joint_height, _ = smd_joint_dimensions(pad_size, 1.0)
    vertices, faces = solder_joint_smd(pad_size, roundness=1.0, pcb_thickness=PCB_THICKNESS_MM)

    assert_valid_quad_mesh(vertices, faces)
    np.testing.assert_allclose(np.ptp(vertices[:, :2], axis=0), pad_size)
    assert vertices[:, 2].max() == pytest.approx(BOARD_SURFACE_Z_MM + joint_height)


@pytest.mark.parametrize(
    "size, roundness",
    (
        pytest.param((0.54, 0.64), 0.0, id="rectangular"),
        pytest.param((0.2, 1.2), 0.5, id="narrow-rounded"),
        pytest.param((1.0, 1.0), 1.0, id="circular"),
        pytest.param((2.0, 4.0), 1.0, id="oval"),
    ),
)
def test_smd_joint_has_a_tapered_crown(size: tuple[float, float], roundness: float):
    pad_size = np.array(size)
    height, _ = smd_joint_dimensions(pad_size, roundness)
    vertices, faces = solder_joint_smd(pad_size, roundness, PCB_THICKNESS_MM)
    crown = vertices[vertices[:, 2] >= BOARD_SURFACE_Z_MM + height * 0.9]

    assert_valid_quad_mesh(vertices, faces)
    np.testing.assert_allclose(np.ptp(vertices[:, :2], axis=0), pad_size)
    assert len(crown) > 0
    assert np.all(np.ptp(crown[:, :2], axis=0) < pad_size * 0.5)
    assert vertices[:, 2].max() == pytest.approx(BOARD_SURFACE_Z_MM + height)


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize(
    "size, roundness",
    (
        pytest.param((0.54, 0.64), 0.0, id="rectangular"),
        pytest.param((0.2, 1.2), 0.5, id="narrow-rounded"),
        pytest.param((0.8, 1.2), 1.0, id="oval"),
    ),
)
def test_smd_joint_has_a_closed_subdivided_surface(size: tuple[float, float], roundness: float):
    result = bpy.ops.pcb2blender.solder_joint_add(
        pad_type="SMD",
        pad_shape="RECTANGULAR",
        pad_size=size,
        roundness=roundness,
        pcb_thickness=PCB_THICKNESS_MM,
    )

    assert result == {"FINISHED"}
    joint = bpy.context.object
    assert joint is not None
    subdivisions = [modifier for modifier in joint.modifiers if modifier.type == "SUBSURF"]
    assert len(subdivisions) == 1
    assert subdivisions[0].subdivision_type == "CATMULL_CLARK"
    assert subdivisions[0].levels == subdivisions[0].render_levels == 2

    evaluated = joint.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    editable = bmesh.new()
    try:
        coords = np.array([vertex.co[:] for vertex in mesh.vertices]) * 1000
        pad_size = np.array(size)
        height, _ = smd_joint_dimensions(pad_size + smd_pad_edge_expansion(pad_size) * 2, roundness)
        assert np.all(np.isfinite(coords))
        assert np.all(np.ptp(coords[:, :2], axis=0) >= pad_size)
        assert height * 0.85 < coords[:, 2].max() - BOARD_SURFACE_Z_MM < height + 0.03
        assert all(polygon.area > 0 for polygon in mesh.polygons)
        editable.from_mesh(mesh)
        assert all(edge.is_manifold for edge in editable.edges)
    finally:
        editable.free()
        evaluated.to_mesh_clear()


@pytest.mark.usefixtures("empty_scene")
@pytest.mark.parametrize("placed", (False, True), ids=("local", "rotated-flipped"))
@pytest.mark.parametrize(
    "pad_type, size, drill",
    (
        pytest.param("THT", (1.2, 2.01), (0.6, 1.2), id="vertical-slot"),
        pytest.param("THT", (2.01, 1.2), (1.2, 0.6), id="horizontal-slot"),
        pytest.param("THT", (1.2, 2.01), (0.6, 0.6), id="oval-round-drill"),
        pytest.param("SMD", (0.8, 1.2), (0.0, 0.0), id="oval-smd"),
        pytest.param("SMD", (0.3, 1.5), (0.0, 0.0), id="narrow-smd"),
    ),
)
def test_oval_joint_covers_pad_perimeter(pad_type: str, size, drill, placed: bool):
    result = bpy.ops.pcb2blender.solder_joint_add(
        pad_type=pad_type,
        pad_shape="OVAL",
        pad_size=size,
        hole_shape="OVAL",
        hole_size=drill,
        pcb_thickness=PCB_THICKNESS_MM,
    )
    assert result == {"FINISHED"}
    joint = bpy.context.object
    assert joint is not None
    if placed:
        joint.location = (0.012, -0.008, 0)
        joint.rotation_euler.z = 0.65
        joint.scale.z = -1.0
    bpy.context.view_layer.update()
    evaluated = joint.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        surface = BVHTree.FromPolygons(
            [vertex.co[:] for vertex in mesh.vertices],
            [polygon.vertices[:] for polygon in mesh.polygons],
        )
        radius = min(size) * 0.5
        half_line = (np.array(size) - min(size)) * 0.5
        perimeter = []
        for angle in np.linspace(0, 2 * np.pi, 72, endpoint=False):
            direction = np.array((np.cos(angle), np.sin(angle)))
            perimeter.append(direction * radius + np.sign(direction) * half_line)
        major_axis = int(np.argmax(size))
        for amount in np.linspace(-1.0, 1.0, 17):
            for side in (-1, 1):
                point = np.zeros(2)
                point[major_axis] = amount * half_line[major_axis]
                point[1 - major_axis] = side * radius
                perimeter.append(point)
        for point in perimeter:
            for side in (-1, 1) if pad_type == "THT" else (1,):
                hit, _, _, _ = surface.ray_cast((*tuple(point * 0.001), side * 0.01), (0, 0, -side))
                assert hit is not None, (pad_type, size, point, side)
                assert hit.z * side * 1000 >= BOARD_SURFACE_Z_MM * 1.015, (
                    pad_type,
                    size,
                    point,
                    side,
                    hit.z * side * 1000,
                )
    finally:
        evaluated.to_mesh_clear()


def test_tht_joint_matches_pad_bounds_and_fillet_heights():
    pad_size = np.array((5.0, 3.0))
    hole_size = np.array((3.0, 1.0))
    joint_height, component_height, _ = tht_joint_dimensions(pad_size, hole_size, 1.0)
    vertices, faces = solder_joint_tht(
        pad_size, hole_size, roundness=1.0, pcb_thickness=PCB_THICKNESS_MM
    )

    assert_valid_quad_mesh(vertices, faces)
    np.testing.assert_allclose(np.ptp(vertices[:, :2], axis=0), pad_size)

    assert vertices[:, 2].max() == pytest.approx(BOARD_SURFACE_Z_MM + joint_height)
    assert vertices[:, 2].min() == pytest.approx(-BOARD_SURFACE_Z_MM - component_height)
    assert joint_height <= THT_SLOT_JOINT_HEIGHT_MAX_MM


@pytest.mark.parametrize("component_roundness", (0.0, 0.5, 1.0))
def test_tht_joint_has_independent_component_side_geometry(component_roundness):
    solder_size = np.array((3.0, 4.0))
    component_size = np.array((2.0, 2.5))
    hole_size = np.array((0.6, 1.0))
    vertices, faces = solder_joint_tht(
        solder_size, hole_size, 1.0, PCB_THICKNESS_MM,
        component_size, component_roundness,
    )
    assert_valid_quad_mesh(vertices, faces)
    solder = vertices[vertices[:, 2] > BOARD_SURFACE_Z_MM]
    component = vertices[vertices[:, 2] < -BOARD_SURFACE_Z_MM]
    np.testing.assert_allclose(np.ptp(solder[:, :2], axis=0), solder_size)
    np.testing.assert_allclose(np.ptp(component[:, :2], axis=0), component_size)
    height, _, _ = tht_joint_dimensions(solder_size, hole_size, 1.0)
    _, component_height, _ = tht_joint_dimensions(component_size, hole_size, component_roundness)
    assert vertices[:, 2].max() == pytest.approx(BOARD_SURFACE_Z_MM + height)
    assert vertices[:, 2].min() == pytest.approx(-BOARD_SURFACE_Z_MM - component_height)


def test_tht_joint_height_scales_with_annular_area():
    hole_size = np.array((1.0, 1.0))
    thin_height, _, _ = tht_joint_dimensions(np.array((1.4, 1.4)), hole_size, 1.0)
    thick_height, _, _ = tht_joint_dimensions(np.array((3.0, 3.0)), hole_size, 1.0)
    very_thick_height, _, _ = tht_joint_dimensions(np.array((8.0, 8.0)), hole_size, 1.0)

    assert thin_height < thick_height < very_thick_height
    assert very_thick_height == THT_JOINT_HEIGHT_MAX_MM


def test_tht_joint_covers_expanded_pad():
    original_pad_size = np.array((1.2, 2.01))
    hole_size = np.array((0.6, 1.2))
    expansion = tht_pad_edge_expansion(original_pad_size, hole_size)
    pad_size = original_pad_size + expansion * 2.0
    vertices, faces = solder_joint_tht(
        pad_size, hole_size, roundness=1.0, pcb_thickness=PCB_THICKNESS_MM
    )

    assert_valid_quad_mesh(vertices, faces)
    np.testing.assert_allclose(np.ptp(vertices[:, :2], axis=0), pad_size)


def test_smd_joint_height_and_spread_scale_with_pad_size():
    thin_pad = np.array((0.2, 0.6))
    thick_pad = np.array((2.0, 3.0))
    thin_height, _ = smd_joint_dimensions(thin_pad, 0.2)
    thick_height, _ = smd_joint_dimensions(thick_pad, 0.2)

    assert SMD_JOINT_HEIGHT_MIN_MM <= thin_height < thick_height
    assert thick_height <= SMD_JOINT_HEIGHT_MAX_MM
    assert smd_pad_edge_expansion(thin_pad) < smd_pad_edge_expansion(thick_pad)


def test_meniscus_profile_tapers_smoothly():
    start_size = np.array((3.0, 2.0))
    end_size = np.array((1.0, 0.5))
    layers = meniscus_layers(start_size, end_size, 0.0, 1.0, 7)
    sizes = np.array([size for size, _ in layers])

    np.testing.assert_allclose(sizes[0], start_size)
    np.testing.assert_allclose(sizes[-1], end_size)
    assert np.all(np.diff(sizes, axis=0) < 0)
    assert np.linalg.norm(sizes[0] - sizes[1]) < np.linalg.norm(sizes[2] - sizes[3])
    assert np.linalg.norm(sizes[-2] - sizes[-1]) < np.linalg.norm(sizes[3] - sizes[4])
