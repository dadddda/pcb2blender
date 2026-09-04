import numpy as np
import pytest

from bl_ext.user_default.pcb3d_importer.solder_joints import (
    SMD_JOINT_HEIGHT_MM,
    THT_COMPONENT_SIDE_HEIGHT_MM,
    THT_JOINT_HEIGHT_MM,
    THT_PAD_EDGE_EXPANSION_MM,
    THT_SLOT_JOINT_HEIGHT_MM,
    solder_joint_smd,
    solder_joint_tht,
)


def assert_valid_quad_mesh(verts: np.ndarray, faces: np.ndarray):
    assert verts.ndim == 2 and verts.shape[1] == 3
    assert faces.ndim == 2 and faces.shape[1] == 4
    assert faces.min() >= 0
    assert faces.max() < len(verts)


def test_oval_smd_solder_joint_retains_both_axes():
    verts, faces = solder_joint_smd(np.array((4.0, 2.0)), roundness=1.0)

    assert_valid_quad_mesh(verts, faces)
    assert len(verts) == 50
    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), (4.0, 2.0))
    assert verts[:, 2].max() == pytest.approx(0.8 + SMD_JOINT_HEIGHT_MM)


def test_oblong_tht_solder_joint_retains_pad_and_hole_axes():
    verts, faces = solder_joint_tht(
        np.array((5.0, 3.0)), np.array((3.0, 1.0)), roundness=1.0
    )

    assert_valid_quad_mesh(verts, faces)
    assert len(verts) == 418
    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), (5.0, 3.0))

    assert verts[:, 2].max() == pytest.approx(0.8 + THT_SLOT_JOINT_HEIGHT_MM)
    assert verts[:, 2].min() == pytest.approx(-0.8 - THT_COMPONENT_SIDE_HEIGHT_MM)


def test_circular_tht_retains_long_pin_height():
    verts, faces = solder_joint_tht(
        np.array((2.0, 2.0)), np.array((1.0, 1.0)), roundness=1.0
    )

    assert_valid_quad_mesh(verts, faces)
    assert verts[:, 2].max() == pytest.approx(0.8 + THT_JOINT_HEIGHT_MM)


def test_tht_pad_edge_expansion_covers_solderable_area():
    pad_size = np.array((1.2, 2.01)) + THT_PAD_EDGE_EXPANSION_MM * 2.0
    verts, _ = solder_joint_tht(pad_size, np.array((0.6, 1.2)), roundness=1.0)

    np.testing.assert_allclose(np.ptp(verts[:, :2], axis=0), (1.4, 2.21))