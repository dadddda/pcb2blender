import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

pytest.importorskip("requests")
ROOT = Path(__file__).resolve().parents[2]


def test_release_metadata_matches_built_archive(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "package_builder",
        ROOT / ".github" / "workflows" / "build_kicad_addon.py",
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    tag = "v2.20.0-k10.0-b5.1"
    monkeypatch.setattr(builder, "get_repo_url", lambda path: "https://example.com/repo")
    monkeypatch.setattr(builder, "get_repo_tags", lambda path: [tag])
    path = builder.build_kicad_addon(
        tag,
        ROOT / "pcb2blender_exporter",
        tmp_path,
        Path("images/icon.png"),
        [Path("images/blender_icon_32x32.png")],
    )
    assert path.name == "pcb2blender_exporter_v2-20-0_k10-0.zip"
    metadata = json.loads((tmp_path / "version.json").read_text())
    assert metadata["kicad_version"] == metadata["kicad_version_max"] == "10.0"
    assert (
        metadata["download_url"] == f"https://example.com/repo/releases/download/{tag}/{path.name}"
    )
    assert metadata["download_size"] == path.stat().st_size
    assert path.with_suffix(".zip.sha256").read_text() == metadata["download_sha256"]
    with ZipFile(path) as archive:
        shared = archive.read("plugins/pcb3d.py").decode("utf-8")
        assert "class PadGeometry" in shared
        compile(shared, "plugins/pcb3d.py", "exec")
        assert "download_sha256" not in json.loads(archive.read("metadata.json"))["versions"][0]
