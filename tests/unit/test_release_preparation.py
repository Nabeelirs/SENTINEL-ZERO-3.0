"""Synthetic software fixtures only; actual NISAR acceptance has separate evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import prepare_release as release
import run_demo as demo


@pytest.fixture
def miniature(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "app.py"
    source.write_text("# labelled synthetic release fixture\n")
    bundle = repo / "scientific_data.zip"
    bundle.write_bytes(b"synthetic archive identity fixture")
    monkeypatch.setattr(release, "ARCHIVE_SHA256", demo.digest(bundle))
    monkeypatch.setattr(release, "source_map", lambda *_: {"app.py":source,"scientific_data.zip":bundle})
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **kw: "synthetic-checkpoint\n")
    return repo, bundle, tmp_path/"candidate"


def test_curated_copy_receipt_and_no_overwrite(miniature):
    repo, bundle, target = miniature
    anchor = release.assemble(repo, bundle, target)
    record = release.verify(target, anchor)
    assert len(record["files"]) == 3
    assert (target/"app.py").read_bytes() == (repo/"app.py").read_bytes()
    assert "HOLD" in record["publication_status"]
    with pytest.raises(FileExistsError):
        release.assemble(repo, bundle, target)


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra", "manifest"])
def test_release_integrity_rejects_changes(miniature, mutation):
    repo, bundle, target = miniature
    anchor = release.assemble(repo, bundle, target)
    if mutation == "changed": (target/"app.py").write_bytes(b"changed")
    if mutation == "missing": (target/"app.py").unlink()
    if mutation == "extra": (target/"unlisted.txt").write_bytes(b"extra")
    if mutation == "manifest": (target/"release_manifest.json").write_bytes(b"{}")
    with pytest.raises(ValueError): release.verify(target, anchor)


def test_bad_bundle_blocks_before_any_copy(miniature):
    repo, bundle, target = miniature
    bundle.write_bytes(b"modified")
    with pytest.raises(ValueError): release.assemble(repo, bundle, target)
    assert not target.exists()


def test_missing_allowed_source_blocks(miniature):
    repo, bundle, target = miniature
    (repo/"app.py").unlink()
    with pytest.raises(ValueError): release.assemble(repo, bundle, target)
    assert not target.exists()


def test_bootstrap_bad_hash_does_not_extract(tmp_path):
    archive = tmp_path/"scientific.zip"
    archive.write_bytes(b"synthetic invalid content")
    with pytest.raises(ValueError): demo.unpack(archive, tmp_path/"cache")
    assert not (tmp_path/"cache").exists()


def test_bootstrap_rejects_path_escape_even_with_matching_archive_hash(tmp_path, monkeypatch):
    archive = tmp_path/"synthetic.zip"
    with zipfile.ZipFile(archive, "x") as z:
        z.writestr("../escaped.txt", b"synthetic fixture")
        for n in range(15): z.writestr(f"safe{n}.txt", b"test")
    monkeypatch.setattr(demo, "ARCHIVE_SHA256", demo.digest(archive))
    with pytest.raises(ValueError, match="Unsafe"): demo.unpack(archive, tmp_path/"cache")
    assert not (tmp_path/"escaped.txt").exists() and not (tmp_path/"cache").exists()
    assert not list(tmp_path.glob("s8-*"))


def test_operator_configuration_rejects_onedrive(tmp_path):
    with pytest.raises(ValueError, match="OneDrive"):
        demo.configure(tmp_path, tmp_path/"OneDrive"/"cache")


def test_hosting_configuration_is_local_package_plus_pinned_runtime():
    repo = TOOLS.parent
    source = (TOOLS/"hosted_app.py").read_text()
    assert "configure(Path(__file__).resolve().parent)" in source
    assert "presentation.explorer import main" in source
    assert 'requires-python = ">=3.11,<3.12"' in (repo/"pyproject.toml").read_text()
