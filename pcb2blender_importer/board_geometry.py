from collections.abc import Iterable
from math import acos, atan2, ceil, cos, pi, sin

import bmesh
import bpy
import numpy as np

from .pcb3d import Pad, PadType

HOLE_CHORD_ERROR_MM = 0.001
HOLE_FIT_TOLERANCE_MM = 0.002
HOLE_RADIUS_ALLOWANCE_MM = 0.025


def hole_rim_loops(mesh: bmesh.types.BMesh) -> list[list[bmesh.types.BMEdge]]:
    edges = {
        edge
        for edge in mesh.edges
        if len(edge.link_faces) == 2
        and abs(edge.verts[0].co.z - edge.verts[1].co.z) < 1e-8
        and any(abs(face.normal.z) > 0.999 for face in edge.link_faces)
        and any(abs(face.normal.z) < 0.001 for face in edge.link_faces)
    }
    loops = []
    while edges:
        loop = {edges.pop()}
        pending = list(loop)
        while pending:
            edge = pending.pop()
            for vertex in edge.verts:
                for neighbor in vertex.link_edges:
                    if neighbor in edges:
                        edges.remove(neighbor)
                        loop.add(neighbor)
                        pending.append(neighbor)
        vertices = {vertex for edge in loop for vertex in edge.verts}
        if len(vertices) >= 8 and all(
            sum(edge in loop for edge in vertex.link_edges) == 2 for vertex in vertices
        ):
            loops.append(list(loop))
    return loops


def capsule_projection(
    points: np.ndarray, center: np.ndarray, rotation: np.ndarray, half_line: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    local = (points - center) @ rotation
    nearest = np.clip(local, -half_line, half_line)
    radial = local - nearest
    distances = np.linalg.norm(radial, axis=1)
    return nearest, radial, distances


def capsule_parameter(point: np.ndarray, half_length: float, radius: float) -> float:
    horizontal, vertical = point
    if half_length == 0:
        return atan2(vertical, horizontal) % (2 * pi) * radius
    if horizontal <= -half_length:
        angle = (atan2(vertical, horizontal + half_length) - pi / 2) % (2 * pi)
        return 2 * half_length + radius * angle
    if horizontal >= half_length:
        angle = (atan2(vertical, horizontal - half_length) + pi / 2) % (2 * pi)
        return 4 * half_length + pi * radius + radius * angle
    if vertical >= 0:
        return half_length - horizontal
    return 3 * half_length + pi * radius + horizontal


def capsule_point(parameter: float, half_length: float, radius: float) -> np.ndarray:
    parameter %= 4 * half_length + 2 * pi * radius
    if half_length == 0:
        angle = parameter / radius
        return np.array((radius * cos(angle), radius * sin(angle)))
    if parameter < 2 * half_length:
        return np.array((half_length - parameter, radius))
    parameter -= 2 * half_length
    if parameter < pi * radius:
        angle = pi / 2 + parameter / radius
        return np.array((-half_length + radius * cos(angle), radius * sin(angle)))
    parameter -= pi * radius
    if parameter < 2 * half_length:
        return np.array((-half_length + parameter, -radius))
    angle = -pi / 2 + (parameter - 2 * half_length) / radius
    return np.array((half_length + radius * cos(angle), radius * sin(angle)))


def refine_board_holes(
    mesh: bpy.types.Mesh, pads: Iterable[Pad], offset_mm: tuple[float, float] = (0.0, 0.0)
) -> int:
    drills = []
    for pad in pads:
        size = np.array(pad.drill_size, dtype=float)
        if pad.pad_type not in {PadType.THT, PadType.NPTH} or not np.all(np.isfinite(size)):
            continue
        if size.min() <= 0 or not np.isfinite(pad.rotation):
            continue
        center = np.array((pad.position[0], -pad.position[1])) - offset_mm
        if not np.all(np.isfinite(center)):
            continue
        rotation = np.array(
            ((cos(pad.rotation), -sin(pad.rotation)), (sin(pad.rotation), cos(pad.rotation)))
        )
        radius = size.min() * 0.5
        half_line = (size - size.min()) * 0.5
        drills.append((center, rotation, half_line, radius))

    if not drills:
        return 0

    editable = bmesh.new()
    try:
        editable.from_mesh(mesh)
        editable.normal_update()
        matches: dict[int, list[tuple[list[bmesh.types.BMEdge], float]]] = {}
        for loop in hole_rim_loops(editable):
            vertices = {vertex for edge in loop for vertex in edge.verts}
            points = np.array([vertex.co.xy[:] for vertex in vertices]) * 1000
            bounds_center = (points.min(axis=0) + points.max(axis=0)) * 0.5
            for index, (center, rotation, half_line, expected_radius) in enumerate(drills):
                if np.linalg.norm(bounds_center - center) > HOLE_RADIUS_ALLOWANCE_MM:
                    continue
                _, _, distances = capsule_projection(points, center, rotation, half_line)
                radius = float(np.median(distances))
                if abs(radius - expected_radius) > HOLE_RADIUS_ALLOWANCE_MM:
                    continue
                if np.max(np.abs(distances - radius)) > HOLE_FIT_TOLERANCE_MM:
                    continue
                if not all(
                    np.dot(
                        np.array(face.normal.xy),
                        center - np.array(face.calc_center_median().xy) * 1000,
                    )
                    > 0
                    for edge in loop
                    for face in edge.link_faces
                    if abs(face.normal.z) < 0.001
                ):
                    continue
                matches.setdefault(index, []).append((loop, radius))
                break

        refined = 0
        changed_faces: set[bmesh.types.BMFace] = set()
        for index, matched_loops in matches.items():
            if len(matched_loops) != 2:
                continue
            loops = [loop for loop, _ in matched_loops]
            if not all(edge.is_valid for loop in loops for edge in loop):
                continue
            vertices_by_side = [
                {vertex for edge in loop for vertex in edge.verts} for loop in loops
            ]
            if len(vertices_by_side[0]) != len(vertices_by_side[1]):
                continue
            xy_sets = [
                {(round(vertex.co.x, 7), round(vertex.co.y, 7)) for vertex in vertices}
                for vertices in vertices_by_side
            ]
            if xy_sets[0] != xy_sets[1]:
                continue
            if (
                abs(next(iter(vertices_by_side[0])).co.z - next(iter(vertices_by_side[1])).co.z)
                < 1e-8
            ):
                continue
            if abs(matched_loops[0][1] - matched_loops[1][1]) > HOLE_FIT_TOLERANCE_MM:
                continue
            center, rotation, half_line, _ = drills[index]
            radius = sum(radius for _, radius in matched_loops) * 0.5
            max_angle = min(pi / 24, 2 * acos(max(-1.0, 1 - HOLE_CHORD_ERROR_MM / radius)))
            major_axis = int(np.argmax(half_line))
            axes = [major_axis, 1 - major_axis]
            half_length = float(half_line[major_axis])
            perimeter = 4 * half_length + 2 * pi * radius
            split_edges = []
            for edge in loops[0] + loops[1]:
                points = np.array([vertex.co.xy[:] for vertex in edge.verts]) * 1000
                local = ((points - center) @ rotation)[:, axes]
                start, end = (capsule_parameter(point, half_length, radius) for point in local)
                span = (end - start + perimeter / 2) % perimeter - perimeter / 2
                _, radial, distances = capsule_projection(points, center, rotation, half_line)
                straight = np.dot(radial[0], radial[1]) / np.prod(distances) > 1 - 1e-8
                segments = 1 if straight else ceil(abs(span) / (radius * max_angle))
                if segments > 1:
                    split_edges.append((edge, start, span, segments))
            if not split_edges or any(segments > 128 for _, _, _, segments in split_edges):
                continue
            for edge, start, span, segments in split_edges:
                changed_faces.update(edge.link_faces)
                vertex = edge.verts[0]
                end_vertex = edge.verts[1]
                for step in range(1, segments):
                    _, vertex = bmesh.utils.edge_split(edge, vertex, 1 / (segments - step + 1))
                    point = capsule_point(start + span * step / segments, half_length, radius)
                    local_point = np.empty(2)
                    local_point[axes] = point
                    vertex.co.xy = (local_point @ rotation.T + center) * 0.001
                    edge = next(
                        candidate
                        for candidate in vertex.link_edges
                        if end_vertex in candidate.verts
                    )
            refined += 1

        if refined:
            bmesh.ops.triangulate(editable, faces=[face for face in changed_faces if face.is_valid])
            editable.normal_update()
            editable.to_mesh(mesh)
            mesh.update()
        return refined
    finally:
        editable.free()
