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

import numpy as np


def assert_valid_quad_mesh(verts: np.ndarray, faces: np.ndarray):
    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 4
    assert faces.min() >= 0
    assert faces.max() < len(verts)


def test_oval_smd_solder_joint_retains_both_axes():
    pad_size = np.array((4.0, 2.0))
    joint_height, _ = smd_joint_dimensions(pad_size, 1.0)
    verts, faces = solder_joint_smd(pad_size, roundness=1.0)

    assert_valid_quad_mesh(verts, faces)
    assert len(verts) == 98
    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), (4.0, 2.0))
    assert verts[:, 2].max() == pytest.approx(0.8 + joint_height)


def test_oblong_tht_solder_joint_retains_pad_and_hole_axes():
    pad_size = np.array((5.0, 3.0))
    hole_size = np.array((3.0, 1.0))
    joint_height, component_height, _ = tht_joint_dimensions(pad_size, hole_size, 1.0)
    verts, faces = solder_joint_tht(pad_size, hole_size, roundness=1.0)

    assert_valid_quad_mesh(verts, faces)
    assert len(verts) == 450
    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), (5.0, 3.0))

    assert verts[:, 2].max() == pytest.approx(0.8 + joint_height)
    assert verts[:, 2].min() == pytest.approx(-0.8 - component_height)
    assert joint_height <= THT_SLOT_JOINT_HEIGHT_MAX_MM


def test_tht_solder_volume_scales_with_annular_area():
    hole_size = np.array((1.0, 1.0))
    thin_height, _, _ = tht_joint_dimensions(np.array((1.4, 1.4)), hole_size, 1.0)
    thick_height, _, _ = tht_joint_dimensions(np.array((3.0, 3.0)), hole_size, 1.0)
    very_thick_height, _, _ = tht_joint_dimensions(np.array((8.0, 8.0)), hole_size, 1.0)

    assert thin_height < thick_height < very_thick_height
    assert very_thick_height == THT_JOINT_HEIGHT_MAX_MM


def test_tht_pad_edge_expansion_covers_solderable_area():
    original_pad_size = np.array((1.2, 2.01))
    hole_size = np.array((0.6, 1.2))
    expansion = tht_pad_edge_expansion(original_pad_size, hole_size)
    pad_size = original_pad_size + expansion * 2.0
    verts, _ = solder_joint_tht(pad_size, hole_size, roundness=1.0)

    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), pad_size)


def test_smd_solder_height_and_spread_scale_with_pad_area():
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
