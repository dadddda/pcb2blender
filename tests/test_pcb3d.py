import importlib.util
import io
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest


@pytest.fixture(scope="module")
def pcb3d():
    path = Path(__file__).resolve().parents[1] / "pcb2blender_importer" / "pcb3d.py"
    spec = importlib.util.spec_from_file_location("pcb3d_format_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules[spec.name]


@pytest.fixture
def current_archive(pcb3d, tmp_path):
    wrl = tmp_path / "pcb.wrl"
    wrl.write_bytes(b"#VRML V2.0 utf8\n")
    layers = tmp_path / "layers"
    layers.mkdir()
    for layer in pcb3d.PCB3D.INCLUDED_LAYERS:
        (layers / f"{layer}.svg").write_text("<svg/>", encoding="utf-8")
    components = tmp_path / "components"
    components.mkdir()
    bounds = pcb3d.Bounds((0.0, 0.0), (20.0, 10.0))
    pad = pcb3d.Pad(
        position=(5.0, 5.0),
        is_flipped=False,
        has_model=True,
        is_tht_or_smd=True,
        has_paste=False,
        pad_type=pcb3d.PadType.THT,
        rotation=0.7,
        drill_shape=pcb3d.DrillShape.OVAL,
        drill_size=(0.6, 1.0),
        front=pcb3d.PadGeometry(pcb3d.PadShape.ROUNDRECT, (2.0, 2.5), 0.2),
        back=pcb3d.PadGeometry(pcb3d.PadShape.OVAL, (3.0, 4.0), 0.5),
        fab_type=pcb3d.PadFabType.PRESSFIT,
    )
    pcb = pcb3d.PCB3D(
        bounds,
        pcb3d.Stackup(),
        {
            "main": pcb3d.Board(bounds, {"daughter": pcb3d.StackedBoard((1.0, 2.0, 3.0))}),
            "daughter": pcb3d.Board(bounds),
        },
        {"Connector_J1_0_0": pad},
        wrl.read_text(encoding="utf-8"),
    )
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        pcb.write(archive, wrl, components, layers)
    with ZipFile(output) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    return pcb, members


def read_archive(pcb3d, members, extract_dir):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    with ZipFile(output) as archive:
        return pcb3d.PCB3D.from_file(archive, extract_dir)


def test_current_format_roundtrip(pcb3d, current_archive, tmp_path):
    pcb, members = current_archive
    assert members["format.toml"] == b"version = 1\n"
    pad_data = pcb3d.load_toml(members["pads/Connector_J1_0_0.toml"].decode("utf-8"))
    assert {"front", "back"} <= pad_data.keys()
    assert not {"shape", "size", "roundness"} & pad_data.keys()
    assert read_archive(pcb3d, members, tmp_path) == pcb


@pytest.mark.parametrize(
    "format_data",
    (
        None,
        b"version = 0",
        b"version = 2",
        b'version = "1"',
        b"version = true",
        b"version = 1.0",
        b"",
        b"version =",
        b"\xff",
    ),
)
def test_unsupported_format_is_rejected_before_extraction(pcb3d, tmp_path, format_data):
    members = {"pcb.wrl": b"#VRML V2.0 utf8\n"}
    if format_data is not None:
        members["format.toml"] = format_data
    with pytest.raises(ValueError, match="Re-export"):
        read_archive(pcb3d, members, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("side", ("front", "back"))
def test_missing_pad_surface_is_rejected(pcb3d, current_archive, tmp_path, side):
    _, members = current_archive
    name = "pads/Connector_J1_0_0.toml"
    members[name] = b"\n".join(
        line for line in members[name].splitlines() if not line.startswith(side.encode() + b" =")
    )
    with pytest.raises(KeyError, match=side):
        read_archive(pcb3d, members, tmp_path)


@pytest.mark.parametrize(
    "member",
    ("layers/bounds.toml", "layers/stackup.toml", "boards/main/bounds.toml"),
)
def test_required_metadata_cannot_fall_back(pcb3d, current_archive, tmp_path, member):
    _, members = current_archive
    del members[member]
    with pytest.raises((KeyError, FileNotFoundError)):
        read_archive(pcb3d, members, tmp_path)


@pytest.mark.parametrize("member", ("pads/old_pad", "boards/main/stacked_old_board"))
def test_binary_metadata_is_rejected_even_with_current_version(
    pcb3d,
    current_archive,
    tmp_path,
    member,
):
    _, members = current_archive
    members[member] = b"\x00" * 39
    with pytest.raises(ValueError, match="Re-export"):
        read_archive(pcb3d, members, tmp_path)


def test_empty_boards_and_pads_are_valid(pcb3d, current_archive, tmp_path):
    pcb, _ = current_archive
    pcb.boards = {}
    pcb.pads = {}
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        pcb.write(archive, tmp_path / "pcb.wrl", tmp_path / "components", tmp_path / "layers")
        assert "boards/" in archive.namelist()
        assert "pads/" in archive.namelist()
    with ZipFile(output) as archive:
        assert pcb3d.PCB3D.from_file(archive, tmp_path) == pcb


@pytest.mark.parametrize(
    "filename,pad_count",
    (
        ("breakout.pcb3d", 48),
        ("breakout_boarddef.pcb3d", 48),
        ("extender_custom_colors.pcb3d", 280),
    ),
)
def test_migrated_fixtures_use_current_format(pcb3d, tmp_path, filename, pad_count):
    path = Path(__file__).parent / "test_pcbs" / filename
    with ZipFile(path) as archive:
        pcb = pcb3d.PCB3D.from_file(archive, tmp_path)
        assert archive.read("format.toml") == b"version = 1\n"
    assert len(pcb.pads) == pad_count
    assert all(pad.front == pad.back for pad in pcb.pads.values())
    assert pcb.stackup.thickness_mm > 0
