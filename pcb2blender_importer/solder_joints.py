from typing import TYPE_CHECKING, assert_never

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty
from numpy.typing import NDArray

from .custom_node_utils import NodesDef, setup_node_tree
from .importer import MM_TO_M

if TYPE_CHECKING:
    from bpy.stub_internal.rna_enums import OperatorReturnItems
else:
    OperatorReturnItems = str


THT_PAD_EDGE_EXPANSION_MIN_MM = 0.05
THT_PAD_EDGE_EXPANSION_MAX_MM = 0.15
THT_JOINT_HEIGHT_MIN_MM = 0.35
THT_JOINT_HEIGHT_MAX_MM = 1.8
THT_SLOT_JOINT_HEIGHT_MIN_MM = 0.16
THT_SLOT_JOINT_HEIGHT_MAX_MM = 0.3
THT_COMPONENT_SIDE_HEIGHT_MIN_MM = 0.1
THT_COMPONENT_SIDE_HEIGHT_MAX_MM = 0.25
SMD_PAD_EDGE_EXPANSION_MIN_MM = 0.02
SMD_PAD_EDGE_EXPANSION_MAX_MM = 0.08
SMD_JOINT_HEIGHT_MIN_MM = 0.12
SMD_JOINT_HEIGHT_MAX_MM = 0.32


class PCB2BLENDER_OT_solder_joint_add(bpy.types.Operator):
    """Add a solder joint"""

    bl_idname = "pcb2blender.solder_joint_add"
    bl_label = "Solder Joint"
    bl_options = {"REGISTER", "UNDO"}

    pad_type: EnumProperty(
        name="Pad Type",
        items=(("THT", "THT", ""), ("SMD", "SMD", "")),
        description="Type of pad",
    )
    pad_shape: EnumProperty(
        name="Pad Shape",
        items=(
            ("SQUARE", "Square", ""),
            ("RECTANGULAR", "Rectangular", ""),
            ("CIRCULAR", "Circular", ""),
            ("OVAL", "Oval / Oblong", ""),
        ),
        description="Shape of the pad",
    )
    pad_size: FloatVectorProperty(
        name="Pad Size",
        subtype="XYZ",
        size=2,
        default=(1.7, 1.7),
        description="Size of the pad in mm",
    )
    roundness: FloatProperty(
        name="Roundness", min=0.0, max=1.0, description="Roundness of the corners of the pad"
    )

    hole_shape: EnumProperty(
        name="Hole Shape",
        items=(("CIRCULAR", "Circular", ""), ("OVAL", "Oval", "")),
        description="Shape of the through hole",
    )
    hole_size: FloatVectorProperty(
        name="Hole Size",
        subtype="XYZ",
        size=2,
        default=(1.0, 1.0),
        description="Size of the through hole in mm",
    )

    pcb_thickness: FloatProperty(
        name="PCB Thickness",
        unit="LENGTH",
        min=0.0,
        soft_max=3.0,
        default=1.6,
        description="Thickness of the PCB in mm",
    )

    location: FloatVectorProperty(
        name="Location", subtype="XYZ", description="Location for the newly added object"
    )
    rotation: FloatVectorProperty(
        name="Rotation", subtype="EULER", description="Rotation for the newly added object"
    )

    reuse_material: BoolProperty(name="Reuse Material", default=False)

    def execute(self, context: bpy.types.Context) -> set[OperatorReturnItems]:
        assert context.collection and context.view_layer

        name = "Solder Joint"
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)

        context.collection.objects.link(obj)

        if self.pad_shape in {"SQUARE", "CIRCULAR"}:
            self.pad_size[1] = self.pad_size[0]
        if self.pad_shape in {"CIRCULAR", "OVAL"}:
            self.roundness = 1.0
        if self.hole_shape == "CIRCULAR":
            self.hole_size[1] = self.hole_size[0]
        hole_size = np.array(self.hole_size)
        pad_size = np.array(self.pad_size)
        if self.pad_type == "THT":
            pad_size += tht_pad_edge_expansion(pad_size, hole_size) * 2.0
        else:
            pad_size += smd_pad_edge_expansion(pad_size) * 2.0

        if self.pad_type == "THT":
            verts, faces = solder_joint_tht(pad_size, hole_size, self.roundness, self.pcb_thickness)
        elif self.pad_type == "SMD":
            verts, faces = solder_joint_smd(pad_size, self.roundness, self.pcb_thickness)
        else:
            assert_never(self.pad_type)

        verts *= MM_TO_M
        indices = faces.flatten()

        mesh.vertices.add(len(verts))
        mesh.vertices.foreach_set("co", verts.flatten())

        mesh.loops.add(len(indices))
        mesh.loops.foreach_set("vertex_index", indices)

        mesh.polygons.add(len(faces))
        mesh.polygons.foreach_set("loop_start", np.arange(0, len(indices), 4))
        mesh.polygons.foreach_set("loop_total", np.full(len(faces), 4))
        mesh.polygons.foreach_set("use_smooth", np.full(len(faces), True))

        mesh.update()
        mesh.validate()

        if not (self.reuse_material and (material := bpy.data.materials.get(name))):
            material = bpy.data.materials.new(name)
            if bpy.app.version < (5, 0, 0):
                material.use_nodes = True
            assert material.node_tree
            material.node_tree.nodes.clear()
            nodes: NodesDef = {
                "shader": ("ShaderNodeBsdfSolder", {}, {}),
                "output": (
                    "ShaderNodeOutputMaterial",
                    {"location": (240, 0)},
                    {"Surface": ("shader", 0)},
                ),
            }
            setup_node_tree(material.node_tree, nodes, False)
        mesh.materials.append(material)

        bpy.ops.object.select_all(action="DESELECT")
        context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.normals_make_consistent()
        bpy.ops.object.mode_set(mode="OBJECT")

        if self.pad_type == "SMD":
            smooth = obj.modifiers.new("Smooth", "SUBSURF")
            smooth.levels = 2
            smooth.render_levels = 2
        else:
            smooth = obj.modifiers.new("Smooth", "SUBSURF")
            smooth.levels = 2

        TEXTURE_NAME = "PCB2BLENDER_SOLDER_NOISE"
        if not (texture := bpy.data.textures.get(TEXTURE_NAME)):
            texture = bpy.data.textures.new(TEXTURE_NAME, "CLOUDS")
            texture.noise_scale = 2e-4
            texture.noise_depth = 1
        displace = obj.modifiers.new("Noise", "DISPLACE")
        displace.texture = texture
        displace.texture_coords = "GLOBAL"
        displace.strength = 3e-5 if self.pad_type == "SMD" else 2.5e-5

        return {"FINISHED"}

    def draw(self, context: bpy.types.Context):
        assert (layout := self.layout)
        layout.use_property_split = True

        layout.prop(self, "pad_type")
        layout.separator()

        layout.prop(self, "pad_shape")
        layout.prop(
            self,
            "pad_size",
            index=0 if self.pad_shape in {"SQUARE", "CIRCULAR"} else -1,
        )
        if self.pad_shape not in {"CIRCULAR", "OVAL"}:
            layout.prop(self, "roundness", slider=True)
        layout.separator()

        if self.pad_type == "THT":
            layout.prop(self, "hole_shape")
            layout.prop(self, "hole_size", index=0 if self.hole_shape == "CIRCULAR" else -1)
            layout.separator()

        layout.prop(self, "pcb_thickness")
        layout.separator()

        layout.prop(self, "location")
        layout.prop(self, "rotation")


def solder_joint_tht(
    pad_size: NDArray[np.float64],
    hole_size: NDArray[np.float64],
    roundness: float = 0.0,
    pcb_thickness: float = 1.6,
):
    vs = np.empty((0, 3), dtype=float)
    fs = np.empty((0, 4), dtype=int)

    is_slotted = not np.isclose(hole_size[0], hole_size[1])
    joint_height, component_side_height, pin_size = tht_joint_dimensions(
        pad_size, hole_size, roundness
    )
    segments_per_corner = 8 if roundness >= 1.0 or is_slotted else 2

    component_layers = meniscus_layers(
        pin_size * 0.65,
        pad_size,
        -(pcb_thickness + component_side_height),
        -(pcb_thickness - 0.03),
        5,
    )
    solder_layers = meniscus_layers(pad_size, pin_size, 0.05, joint_height, 7)
    layers = (
        *(
            (size, z, max(roundness, 0.4), index == 0)
            for index, (size, z) in enumerate(component_layers)
        ),
        (hole_size * 0.9, -0.30, 1.0, False),
        (pad_size, -0.10, max(roundness, 0.2), False),
        *(
            (size, z, max(roundness, 0.4), index == len(solder_layers) - 1)
            for index, (size, z) in enumerate(solder_layers)
        ),
    )
    for size, z, layer_roundness, fill in layers:
        vs, fs = add_octagon_layer(vs, fs, size, z, layer_roundness, fill, segments_per_corner)
    vs += np.array((0, 0, pcb_thickness * 0.5))

    return vs, fs


def solder_joint_smd(
    pad_size: NDArray[np.float64], roundness: float = 0.0, pcb_thickness: float = 1.6
):
    vs = np.empty((0, 3), dtype=float)
    fs = np.empty((0, 4), dtype=int)
    segments_per_corner = 8
    joint_height, _ = smd_joint_dimensions(pad_size, roundness)
    outline_roundness = max(roundness, 0.2)
    vs, fs = add_octagon_layer(
        vs, fs, pad_size, -0.04, outline_roundness, True, segments_per_corner
    )
    outline, ellipse = smd_wetting_outline(pad_size, outline_roundness, 32)
    vs[1:, :2] = outline

    angles = np.linspace(0.0, np.pi * 0.5 - np.pi / 64, 8)
    for index, angle in enumerate(angles):
        rise = np.sin(angle)
        scale = np.cos(angle)
        height = 0.02 + (joint_height - 0.02) * rise
        is_cap = index == len(angles) - 1
        cap_index = len(vs)
        vs, fs = add_octagon_layer(
            vs, fs, pad_size * scale, height, outline_roundness, is_cap, segments_per_corner
        )
        vs[-len(outline) :, :2] = (outline + (ellipse - outline) * rise**2) * scale
        if is_cap:
            vs[cap_index, 2] = joint_height
    vs += np.array((0, 0, pcb_thickness * 0.5))

    return vs, fs


def smd_wetting_outline(
    pad_size: NDArray[np.float64], roundness: float, count: int
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    angles = np.pi * 0.5 - np.arange(count) * (2.0 * np.pi / count)
    directions = np.column_stack((np.cos(angles), np.sin(angles)))
    half_size = pad_size * 0.5
    ellipse = directions * half_size
    rectangle = ellipse / np.max(np.abs(directions), axis=1, keepdims=True)
    radius = half_size.min() * np.clip(roundness, 0.0, 1.0)
    corner_center = half_size - radius
    in_corner = np.all(np.abs(rectangle) > corner_center, axis=1)

    ray_length_squared = np.maximum(np.sum(ellipse**2, axis=1), 1e-18)
    projection = np.sum(np.abs(ellipse) * corner_center, axis=1)
    discriminant = projection**2 - ray_length_squared * (np.sum(corner_center**2) - radius**2)
    corner_scale = (projection + np.sqrt(np.maximum(discriminant, 0))) / ray_length_squared
    outline = np.where(in_corner[:, None], ellipse * corner_scale[:, None], rectangle)
    return outline, ellipse


def rounded_rectangle_area(size: NDArray[np.float64], roundness: float) -> float:
    radius = size.min() * 0.5 * np.clip(roundness, 0.0, 1.0)
    return float(np.prod(size) - (4.0 - np.pi) * radius**2)


def equivalent_solder_radius(area: float) -> float:
    return float(np.sqrt(max(area, 0.0) / np.pi))


def tht_pad_edge_expansion(pad_size: NDArray[np.float64], hole_size: NDArray[np.float64]) -> float:
    annular_width = max(float(np.min((pad_size - hole_size) * 0.5)), 0.0)
    return float(
        np.clip(
            annular_width * 0.2,
            THT_PAD_EDGE_EXPANSION_MIN_MM,
            THT_PAD_EDGE_EXPANSION_MAX_MM,
        )
    )


def smd_pad_edge_expansion(pad_size: NDArray[np.float64]) -> float:
    return float(
        np.clip(
            pad_size.min() * 0.04,
            SMD_PAD_EDGE_EXPANSION_MIN_MM,
            SMD_PAD_EDGE_EXPANSION_MAX_MM,
        )
    )


def tht_joint_dimensions(
    pad_size: NDArray[np.float64], hole_size: NDArray[np.float64], roundness: float
) -> tuple[float, float, NDArray[np.float64]]:
    solder_area = max(
        rounded_rectangle_area(pad_size, roundness) - rounded_rectangle_area(hole_size, 1.0),
        0.0,
    )
    solder_radius = equivalent_solder_radius(solder_area)
    is_slotted = not np.isclose(hole_size[0], hole_size[1])
    if is_slotted:
        joint_height = np.clip(
            0.12 + solder_radius * 0.2,
            THT_SLOT_JOINT_HEIGHT_MIN_MM,
            THT_SLOT_JOINT_HEIGHT_MAX_MM,
        )
    else:
        joint_height = np.clip(
            0.3 + solder_radius * 0.8,
            THT_JOINT_HEIGHT_MIN_MM,
            THT_JOINT_HEIGHT_MAX_MM,
        )
    component_side_height = np.clip(
        0.07 + solder_radius * 0.15,
        THT_COMPONENT_SIDE_HEIGHT_MIN_MM,
        THT_COMPONENT_SIDE_HEIGHT_MAX_MM,
    )
    pin_size = hole_size * 0.72
    return float(joint_height), float(component_side_height), pin_size


def smd_joint_dimensions(
    pad_size: NDArray[np.float64], roundness: float
) -> tuple[float, NDArray[np.float64]]:
    pad_area = rounded_rectangle_area(pad_size, roundness)
    linear_size = np.sqrt(max(pad_area, 1e-9))
    joint_height = np.clip(
        0.09 + linear_size * 0.15,
        SMD_JOINT_HEIGHT_MIN_MM,
        SMD_JOINT_HEIGHT_MAX_MM,
    )
    top_scale = np.clip(0.72 + 0.05 * pad_size.min() / linear_size, 0.72, 0.8)
    return float(joint_height), pad_size * top_scale


def meniscus_layers(
    start_size: NDArray[np.float64],
    end_size: NDArray[np.float64],
    start_z: float,
    end_z: float,
    count: int,
) -> tuple[tuple[NDArray[np.float64], float], ...]:
    layers = []
    for amount in np.linspace(0.0, 1.0, count):
        smooth_amount = amount * amount * (3.0 - 2.0 * amount)
        size = start_size + (end_size - start_size) * smooth_amount
        layers.append((size, start_z + (end_z - start_z) * amount))
    return tuple(layers)


MAX_ROUNDNESS_FAC = 1 - 1 / np.tan(np.deg2rad(135.0 / 2))


def add_octagon_layer(
    verts: NDArray[np.float64],
    faces: NDArray[np.int64],
    size: NDArray[np.float64],
    z: float,
    roundness: float = 0.5,
    fill: bool = False,
    segments_per_corner: int = 2,
):
    half_size = size * 0.5
    if segments_per_corner == 2:
        min_half_size = half_size.min()
        r_offset = min_half_size * roundness * MAX_ROUNDNESS_FAC
        new_verts = np.array(
            (
                ((half_size[0] - r_offset), (half_size[1]), z),
                ((half_size[0]), (half_size[1] - r_offset), z),
                ((half_size[0]), -(half_size[1] - r_offset), z),
                ((half_size[0] - r_offset), -(half_size[1]), z),
                (-(half_size[0] - r_offset), -(half_size[1]), z),
                (-(half_size[0]), -(half_size[1] - r_offset), z),
                (-(half_size[0]), (half_size[1] - r_offset), z),
                (-(half_size[0] - r_offset), (half_size[1]), z),
            )
        )
    else:
        radius = half_size.min() * roundness
        corner_centers = (
            (half_size[0] - radius, half_size[1] - radius, 90.0),
            (half_size[0] - radius, -half_size[1] + radius, 0.0),
            (-half_size[0] + radius, -half_size[1] + radius, -90.0),
            (-half_size[0] + radius, half_size[1] - radius, -180.0),
        )
        new_verts = np.array(
            [
                (
                    center_x + radius * np.cos(angle),
                    center_y + radius * np.sin(angle),
                    z,
                )
                for center_x, center_y, start_angle in corner_centers
                for angle in np.deg2rad(
                    start_angle - np.arange(segments_per_corner) * 90.0 / segments_per_corner
                )
            ]
        )

    segment_count = len(new_verts)

    if len(verts) > 0:
        new_faces = np.array(
            [
                (
                    -segment_count + index,
                    -segment_count + (index + 1) % segment_count,
                    (index + 1) % segment_count,
                    index,
                )
                for index in range(segment_count)
            ]
        )
    else:
        new_faces = np.empty((0, 4), dtype=int)

    if fill:
        center_vertex = (0, 0, z)
        new_verts = np.append((center_vertex,), new_verts, axis=0)
        fill_faces = np.array(
            [
                (
                    index + 1,
                    (index + 1) % segment_count + 1,
                    (index + 2) % segment_count + 1,
                    0,
                )
                for index in range(0, segment_count, 2)
            ]
        )
        new_faces = new_faces + (new_faces >= 0)
        new_faces = np.append(new_faces, fill_faces, axis=0)

    faces = np.append(faces, new_faces + len(verts), axis=0)
    verts = np.append(verts, new_verts, axis=0)

    return verts, faces


classes = (PCB2BLENDER_OT_solder_joint_add,)


def menu_func_solder_joint_add(self: bpy.types.Menu, context: bpy.types.Context):
    assert self.layout
    self.layout.separator()
    self.layout.operator(PCB2BLENDER_OT_solder_joint_add.bl_idname, icon="PROP_CON")


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.VIEW3D_MT_mesh_add.append(menu_func_solder_joint_add)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func_solder_joint_add)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
