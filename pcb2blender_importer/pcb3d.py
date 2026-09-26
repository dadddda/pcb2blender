import re
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from types import GenericAlias
from typing import Any
from zipfile import Path as ZipPath, ZipFile

if "bpy" in locals():
    from error_helper import warning
else:
    warning = print


@dataclass
class TOMLSerializable:
    @classmethod
    def from_toml(cls, data: str):
        return cls.from_dict(load_toml(data))

    @classmethod
    def from_dict(cls, toml: dict[str, Any]):
        values = {}
        for f in fields(cls):
            value = toml[f.name]
            value_type = f.type
            if isinstance(value_type, type) and issubclass(value_type, Enum):
                values[f.name] = value_type[value]
            elif isinstance(value_type, type) and issubclass(value_type, TOMLSerializable):
                values[f.name] = value_type.from_dict(value)
            elif isinstance(value_type, (type, GenericAlias)):
                values[f.name] = value_type(value)  # pyright: ignore[reportCallIssue]
            else:
                values[f.name] = value
        return cls(**values)

    def to_toml(self):
        data = ""
        for f in fields(self):
            value = getattr(self, f.name)
            data += f"{f.name} = {self._toml_value(value)}\n"
        return data

    @classmethod
    def _toml_value(cls, value: Any) -> str:
        if isinstance(value, TOMLSerializable):
            entries = (
                f"{f.name} = {cls._toml_value(getattr(value, f.name))}" for f in fields(value)
            )
            return "{ " + ", ".join(entries) + " }"
        elif isinstance(value, (tuple, list)):
            return f"[ {', '.join(cls._toml_value(subvalue) for subvalue in value)} ]"
        elif isinstance(value, Enum):
            return f'"{value.name}"'
        elif isinstance(value, str):
            return f'"{value}"'
        elif isinstance(value, bool):
            return str(value).lower()
        else:
            return str(value)


class PadType(Enum):
    UNKNOWN = -1
    THT = 0
    SMD = 1
    CONN = 2
    NPTH = 3

    @classmethod
    def _missing_(cls, value: Any):
        warning(f"unknown pad type '{value}'")
        return cls.UNKNOWN


class PadShape(Enum):
    UNKNOWN = -1
    CIRCLE = 0
    RECT = 1
    OVAL = 2
    TRAPEZOID = 3
    ROUNDRECT = 4
    CHAMFERED_RECT = 5
    CUSTOM = 6

    @classmethod
    def _missing_(cls, value: Any):
        warning(f"unknown pad shape '{value}'")
        return cls.UNKNOWN


class DrillShape(Enum):
    UNKNOWN = -1
    CIRCULAR = 0
    OVAL = 1

    @classmethod
    def _missing_(cls, value: Any):
        warning(f"unknown drill shape '{value}'")
        return cls.UNKNOWN


class PadFabType(Enum):
    NONE = 0
    BGA = 1
    FIDUCIAL = (2, 3)
    TESTPOINT = 4
    HEATSINK = 5
    CASTELLATED = 6
    MECHANICAL = 7
    PRESSFIT = 8

    @classmethod
    def _missing_(cls, value: Any):
        warning(f"unknown pad fabrication attribute '{value}'")
        return cls.NONE


@dataclass
class PadGeometry(TOMLSerializable):
    shape: PadShape
    size: tuple[float, float]
    roundness: float

    @property
    def solder_roundness(self) -> float:
        if self.shape in {PadShape.CIRCLE, PadShape.OVAL}:
            return 1.0
        return self.roundness * 2.0 if self.shape == PadShape.ROUNDRECT else 0.0


@dataclass
class Pad(TOMLSerializable):
    position: tuple[float, float]
    is_flipped: bool
    has_model: bool
    is_tht_or_smd: bool
    has_paste: bool
    pad_type: PadType
    rotation: float
    drill_shape: DrillShape
    drill_size: tuple[float, float]
    front: PadGeometry
    back: PadGeometry
    fab_type: PadFabType = PadFabType.NONE


class KiCadColor(Enum):
    CUSTOM = 0
    GREEN = 1
    RED = 2
    BLUE = 3
    PURPLE = 4
    BLACK = 5
    WHITE = 6
    YELLOW = 7


class SurfaceFinish(Enum):
    HASL = 0
    ENIG = 1
    NONE = 2


@dataclass
class Stackup(TOMLSerializable):
    thickness_mm: float = 1.6
    mask_color: KiCadColor = KiCadColor.GREEN
    mask_color_custom: tuple[float, ...] = (0.0, 0.0, 0.0)
    silks_color: KiCadColor = KiCadColor.WHITE
    silks_color_custom: tuple[float, ...] = (0.0, 0.0, 0.0)
    surface_finish: SurfaceFinish = SurfaceFinish.HASL


@dataclass
class Bounds(TOMLSerializable):
    top_left: tuple[float, float]
    size: tuple[float, float]

    @property
    def bottom_right(self):
        return (self.top_left[0] + self.size[0], self.top_left[1] + self.size[1])

    @property
    def center(self):
        return self.top_left[0] + self.size[0] * 0.5, self.top_left[1] + self.size[1] * 0.5


class StackedBoard(tuple[float, float, float]):
    @classmethod
    def from_toml(cls, data: str):
        return StackedBoard(load_toml(data)["offset"])

    def to_toml(self):
        return f"offset = [ {self[0]}, {self[1]}, {self[2]} ]"


@dataclass
class Board:
    bounds: Bounds
    stacked_boards: dict[str, StackedBoard] = field(default_factory=dict)


@dataclass
class PCB3D:
    layers_bounds: Bounds
    stackup: Stackup
    boards: dict[str, Board]
    pads: dict[str, Pad]
    content: str = ""

    @classmethod
    def from_file(cls, file: ZipFile, extract_dir: Path):
        reexport = "Re-export the board using the current pcb2blender exporter."
        if cls.FORMAT not in file.namelist():
            raise ValueError(f"Unsupported unversioned .pcb3d archive. {reexport}")
        try:
            version = load_toml(file.read(cls.FORMAT).decode("utf-8"))["version"]
        except (KeyError, ValueError) as error:
            raise ValueError(f"Invalid .pcb3d format metadata. {reexport}") from error
        if type(version) is not int or version != cls.FORMAT_VERSION:
            raise ValueError(f"Unsupported .pcb3d format version {version!r}. {reexport}")

        members = {path.name for path in ZipPath(file).iterdir()}
        if missing := cls.REQUIRED_MEMBERS.difference(members):
            raise ValueError(f"not a valid .pcb3d file: missing {str(missing)[1:-1]}")
        zip_path = ZipPath(file)

        layers_bounds = Bounds.from_toml(
            file.read(f"{cls.LAYERS}/{cls.LAYERS_BOUNDS}").decode("utf-8")
        )
        stackup = Stackup.from_toml(file.read(f"{cls.LAYERS}/{cls.LAYERS_STACKUP}").decode("utf-8"))
        boards = {}
        for board_dir in (zip_path / cls.BOARDS).iterdir():
            bounds = Bounds.from_toml((board_dir / cls.BOUNDS).read_text(encoding="utf-8"))
            stacked_boards = {}
            for path in board_dir.iterdir():
                if not path.name.startswith(cls.STACKED):
                    continue
                if path.suffix != ".toml":
                    raise ValueError(f"Unsupported stacked-board metadata: {path.name}. {reexport}")
                stacked_boards[path.stem[len(cls.STACKED) :]] = StackedBoard.from_toml(
                    path.read_text(encoding="utf-8")
                )
            boards[board_dir.name] = Board(bounds, stacked_boards)
        pads = {}
        for path in (zip_path / cls.PADS).iterdir():
            if path.suffix != ".toml":
                raise ValueError(f"Unsupported pad metadata: {path.name}. {reexport}")
            pads[path.stem] = Pad.from_toml(path.read_text(encoding="utf-8"))

        with file.open(cls.PCB) as pcb_file:
            pcb_file_content = pcb_file.read().decode("utf-8")
            with open(extract_dir / cls.PCB, "wb") as filtered_file:
                filtered = cls.REGEX_FILTER_COMPONENTS.sub("\\g<prefix>", pcb_file_content)
                filtered_file.write(filtered.encode("utf-8"))

        components = {
            name
            for name in file.namelist()
            if name.startswith(f"{cls.COMPONENTS}/") and name.endswith(".wrl")
        }

        file.extractall(extract_dir, components)

        layers = (f"{cls.LAYERS}/{layer}.svg" for layer in cls.INCLUDED_LAYERS)
        file.extractall(extract_dir, layers)

        return PCB3D(layers_bounds, stackup, boards, pads, pcb_file_content)

    def write(self, file: ZipFile, wrl_file: Path, components_dir: Path, layers_dir: Path):
        file.writestr(self.FORMAT, f"version = {self.FORMAT_VERSION}\n")
        file.writestr(f"{self.COMPONENTS}/", "")
        file.writestr(f"{self.LAYERS}/", "")
        file.writestr(f"{self.BOARDS}/", "")
        file.writestr(f"{self.PADS}/", "")

        file.write(wrl_file, self.PCB)
        for path in components_dir.glob("**/*.wrl"):
            file.write(path, f"{self.COMPONENTS}/{path.name}")

        for path in layers_dir.glob("**/*.svg"):
            file.write(path, f"{self.LAYERS}/{path.name}")
        file.writestr(f"{self.LAYERS}/{self.LAYERS_BOUNDS}", self.layers_bounds.to_toml())
        file.writestr(f"{self.LAYERS}/{self.LAYERS_STACKUP}", self.stackup.to_toml())

        for board_name, board in self.boards.items():
            subdir = f"{self.BOARDS}/{board_name}"
            file.writestr(f"{subdir}/{self.BOUNDS}", board.bounds.to_toml())

            for stacked_name, stacked in board.stacked_boards.items():
                file.writestr(f"{subdir}/{self.STACKED}{stacked_name}.toml", stacked.to_toml())

        for pad_name, pad in self.pads.items():
            file.writestr(f"{self.PADS}/{pad_name}.toml", pad.to_toml())

    FORMAT = "format.toml"
    FORMAT_VERSION = 1
    PCB = "pcb.wrl"
    COMPONENTS = "components"
    LAYERS = "layers"
    LAYERS_BOUNDS = "bounds.toml"
    LAYERS_STACKUP = "stackup.toml"
    BOARDS = "boards"
    BOUNDS = "bounds.toml"
    STACKED = "stacked_"
    PADS = "pads"

    REQUIRED_MEMBERS = {FORMAT, PCB, LAYERS, BOARDS, PADS}

    _INCLUDED_LAYERS = ["Cu", "Paste", "SilkS", "Mask"]
    INCLUDED_LAYERS_FRONT = [f"F_{layer}" for layer in _INCLUDED_LAYERS]
    INCLUDED_LAYERS_BACK = [f"B_{layer}" for layer in _INCLUDED_LAYERS]
    INCLUDED_LAYERS = list(sum(zip(INCLUDED_LAYERS_FRONT, INCLUDED_LAYERS_BACK), ()))

    REGEX_FILTER_COMPONENTS = re.compile(
        r"(?P<prefix>Transform\s*{\s*"
        r"(?:rotation (?P<r>[^\n]*)\n)?\s*"
        r"(?:translation (?P<t>[^\n]*)\n)?\s*"
        r"(?:scale (?P<s>[^\n]*)\n)?\s*"
        r"children\s*\[\s*)"
        r"(?P<instances>(?:Transform\s*{\s*"
        r"(?:rotation [^\n]*\n)?\s*(?:translation [^\n]*\n)?\s*(?:scale [^\n]*\n)?\s*"
        r"children\s*\[\s*Inline\s*{\s*url\s*\"[^\"]*\"\s*}\s*]\s*}\s*)+)"
    )

    REGEX_COMPONENT = re.compile(
        r"Transform\s*{\s*"
        r"(?:rotation (?P<r>[^\n]*)\n)?\s*"
        r"(?:translation (?P<t>[^\n]*)\n)?\s*"
        r"(?:scale (?P<s>[^\n]*)\n)?\s*"
        r"children\s*\[\s*Inline\s*{\s*url\s*\"(?P<url>[^\"]*)\"\s*}\s*]\s*}\s*"
    )


def load_toml(data: str):
    import tomllib

    return tomllib.loads(data)
