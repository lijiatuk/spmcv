import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/build_union.py"
SPEC = importlib.util.spec_from_file_location("build_union", MODULE_PATH)
build_union = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build_union)


def test_export_target_creates_and_reuses_correct_symlink(tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"image")
    target = tmp_path / "out.jpg"

    build_union.ensure_export_target(target, source)
    build_union.ensure_export_target(target, source)

    assert target.is_symlink()
    assert target.resolve() == source.resolve()


def test_export_target_rejects_regular_copy_even_when_bytes_match(tmp_path):
    source = tmp_path / "source.jpg"
    source.write_bytes(b"image")
    target = tmp_path / "out.jpg"
    target.write_bytes(b"image")

    with pytest.raises(SystemExit, match="fresh output directory"):
        build_union.ensure_export_target(target, source)


def test_export_target_rejects_symlink_to_wrong_source(tmp_path):
    source = tmp_path / "source.jpg"
    other = tmp_path / "other.jpg"
    source.write_bytes(b"image")
    other.write_bytes(b"other")
    target = tmp_path / "out.jpg"
    target.symlink_to(other)

    with pytest.raises(SystemExit, match="stale dataset entry"):
        build_union.ensure_export_target(target, source)
