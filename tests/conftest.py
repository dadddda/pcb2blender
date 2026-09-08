from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def empty_scene() -> None:
    import bpy

    bpy.ops.wm.read_homefile(use_empty=True)


@pytest.fixture
def write_material_map(tmp_path: Path) -> Callable[[str], Path]:
    def write(contents: str) -> Path:
        path = tmp_path / "materials.toml"
        path.write_text(contents, encoding="utf-8")
        return path

    return write
