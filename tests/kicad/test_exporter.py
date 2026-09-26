import importlib.util
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from zipfile import ZipFile

import pytest

pcbnew = pytest.importorskip("pcbnew")
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def exporter():
    package = types.ModuleType("exporter_under_test")
    package.__path__ = [str(ROOT / "pcb2blender_exporter")]
    sys.modules[package.__name__] = package
    for name, path in (
        ("pcb3d", ROOT / "pcb2blender_importer" / "pcb3d.py"),
        ("export", ROOT / "pcb2blender_exporter" / "export.py"),
    ):
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    yield sys.modules[f"{package.__name__}.export"]
    for name in ("export", "pcb3d"):
        del sys.modules[f"{package.__name__}.{name}"]
    del sys.modules[package.__name__]


def vector(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


@pytest.fixture
def board():
    board = pcbnew.BOARD()
    for start, end in (
        ((0, 0), (20, 0)),
        ((20, 0), (20, 10)),
        ((20, 10), (0, 10)),
        ((0, 10), (0, 0)),
    ):
        edge = pcbnew.PCB_SHAPE(board)
        edge.SetShape(pcbnew.SHAPE_T_SEGMENT)
        edge.SetLayer(pcbnew.Edge_Cuts)
        edge.SetStart(vector(*start))
        edge.SetEnd(vector(*end))
        edge.SetWidth(pcbnew.FromMM(0.05))
        board.Add(edge)
    for text, position in (("PCB3D_TL_demo", (0, 0)), ("PCB3D_BR_demo", (20, 10))):
        drawing = pcbnew.PCB_TEXT(board)
        drawing.SetText(text)
        drawing.SetPosition(vector(*position))
        board.Add(drawing)
    footprint = pcbnew.FOOTPRINT(board)
    footprint.SetReference("J1")
    footprint.SetValue("Connector")
    footprint.SetAttributes(pcbnew.FP_THROUGH_HOLE)
    footprint.SetPosition(vector(5, 5))
    board.Add(footprint)
    for index in range(2):
        pad = pcbnew.PAD(footprint)
        pad.SetNumber(str(index + 1))
        pad.SetPosition(vector(5 + index * 5, 5))
        pad.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        pad.SetLayerSet(pcbnew.LSET.AllCuMask())
        pad.SetDrillSize(vector(0.6, 1.0))
        pad.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
        pad.Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
        pad.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_ROUNDRECT)
        pad.SetSize(pcbnew.F_Cu, vector(2, 2))
        pad.SetRoundRectRadiusRatio(pcbnew.F_Cu, 0.2)
        pad.SetShape(pcbnew.B_Cu, pcbnew.PAD_SHAPE_OVAL)
        pad.SetSize(pcbnew.B_Cu, vector(3, 4))
        pad.SetRoundRectRadiusRatio(pcbnew.B_Cu, 0.4)
        if index:
            pad.SetProperty(pcbnew.PAD_PROP_PRESSFIT)
        footprint.Add(pad)
    return board


@pytest.mark.parametrize("flipped", (False, True))
def test_per_side_geometry_and_pressfit(exporter, board, flipped):
    footprint = next(iter(board.Footprints()))
    if flipped:
        footprint.Flip(footprint.GetPosition(), False)
    pads = exporter.get_pads(board)
    for source, pad in zip(footprint.Pads(), pads.values()):
        assert pad.is_flipped == flipped
        for front, layer in ((True, pcbnew.F_Cu), (False, pcbnew.B_Cu)):
            geometry = pad.front if front else pad.back
            assert geometry.shape.value == source.GetShape(layer)
            assert geometry.size == pcbnew.ToMM(source.GetSize(layer))
            assert geometry.roundness == source.GetRoundRectRadiusRatio(layer)
        assert pad.front != pad.back
        assert pad.drill_shape == exporter.DrillShape.OVAL
        assert pad.drill_size == (0.6, 1.0)
        assert exporter.Pad.from_toml(pad.to_toml()) == pad
    assert sum(pad.fab_type == exporter.PadFabType.PRESSFIT for pad in pads.values()) == 1


@pytest.mark.parametrize("side", ("front", "back"))
def test_per_side_pad_metadata_is_required(exporter, board, side):
    pad = next(iter(exporter.get_pads(board).values()))
    text = "\n".join(
        line for line in pad.to_toml().splitlines() if not line.startswith(f"{side} =")
    )
    with pytest.raises(KeyError, match=side):
        exporter.Pad.from_toml(text)


@pytest.mark.parametrize("flipped", (False, True))
def test_smd_geometry_uses_footprint_side(exporter, board, flipped):
    footprint = next(iter(board.Footprints()))
    footprint.SetAttributes(pcbnew.FP_SMD)
    for pad in footprint.Pads():
        pad.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        pad.SetLayerSet(pcbnew.PAD.SMDMask())
    if flipped:
        footprint.Flip(footprint.GetPosition(), False)
    for pad in exporter.get_pads(board).values():
        assert pad.pad_type == exporter.PadType.SMD
        assert pad.is_flipped == flipped
        assert pad.has_paste
        assert (pad.back if flipped else pad.front).size == (2.0, 2.0)


def test_save_failure_is_explicit(exporter, board, monkeypatch, tmp_path):
    monkeypatch.setattr(exporter, "get_tempdir", lambda: tmp_path)
    monkeypatch.setattr(pcbnew, "SaveBoard", lambda *args, **kwargs: False)
    with pytest.raises(RuntimeError, match="stackup"):
        exporter.get_stackup(board)


def test_export_failure_is_explicit(exporter, monkeypatch, tmp_path):
    monkeypatch.setattr(exporter, "get_tempdir", lambda: tmp_path / "work")
    monkeypatch.setattr(pcbnew, "ExportVRML", lambda *args: False)
    with pytest.raises(RuntimeError, match="VRML"):
        exporter.export_pcb3d(tmp_path / "failed.pcb3d", {})
    assert not (tmp_path / "failed.pcb3d").exists()


def test_kicad10_archive_roundtrip(exporter, board, monkeypatch, tmp_path):
    models_root = Path(
        os.environ.get(
            "KICAD10_3DMODEL_DIR",
            str(Path(sys.executable).parent.parent / "share" / "kicad" / "3dmodels")
            if sys.platform == "win32"
            else "/usr/share/kicad/3dmodels",
        )
    )
    model_path = models_root / "Resistor_SMD.3dshapes" / "R_0603_1608Metric.step"
    assert model_path.is_file(), (
        f"Install KiCad 10 3D models or set KICAD10_3DMODEL_DIR: {model_path}"
    )
    model = pcbnew.FP_3DMODEL()
    model.m_Filename = str(model_path)
    next(iter(board.Footprints())).Models().push_back(model)
    board_path = tmp_path / "test.kicad_pcb"
    board.SetFileName(str(board_path))
    assert pcbnew.SaveBoard(str(board_path), board, aSkipSettings=True)
    cli = shutil.which("kicad-cli") or str(Path(sys.executable).with_name("kicad-cli.exe"))

    def export_vrml(path, scale, unspecified, dnp, export_models, relative, models, x, y):
        # The GUI-only SWIG entry point needs an editor; exercise the same exporter via the CLI.
        assert (scale, unspecified, dnp, export_models, relative, x, y) == (
            0.001,
            True,
            False,
            True,
            True,
            0.0,
            0.0,
        )
        subprocess.run(
            [
                cli,
                "pcb",
                "export",
                "vrml",
                str(board_path),
                "--output",
                str(path),
                "--units",
                "m",
                "--models-dir",
                str(models),
                "--models-relative",
                "--no-dnp",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True

    monkeypatch.setattr(pcbnew, "GetBoard", lambda: board)
    monkeypatch.setattr(pcbnew, "ExportVRML", export_vrml)
    monkeypatch.setattr(exporter, "get_tempdir", lambda: tmp_path / "work")
    boards, ignored = exporter.get_boarddefs(board)
    assert not ignored and boards["demo"].bounds.size == (20.0, 10.0)
    output = tmp_path / "test.pcb3d"
    exporter.export_pcb3d(output, boards)
    extract = tmp_path / "read"
    extract.mkdir()
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        for layer in exporter.PCB3D.INCLUDED_LAYERS:
            svg = archive.read(f"layers/{layer}.svg").decode("utf-8")
            assert 'viewBox="-1.025000 -1.025000 22.050000 12.050000"' in svg
        assert "components/R_0603_1608Metric.wrl" in archive.namelist()
        restored = exporter.PCB3D.from_file(archive, extract)
        assert restored.pads == exporter.get_pads(board)
        assert restored.stackup.thickness_mm == pytest.approx(1.6)
        assert len(list(exporter.PCB3D.REGEX_COMPONENT.finditer(restored.content))) == 1
    if destination := os.environ.get("PCB2BLENDER_TEST_PCB3D"):
        shutil.copyfile(output, destination)
