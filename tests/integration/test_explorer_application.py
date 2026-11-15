"""Actual local NISAR application tests; no synthetic substitute for real-data gates."""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from sentinel_zero.application.bundle import archive_bundle, create_bundle, verify_bundle_receipt
from sentinel_zero.application.data import (ANALYSIS_SHA256, RuntimePaths, candidates, configured_paths,
                                           fingerprint, load_explorer, pixel, summary_json)
from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.presentation.explorer import STAGES
from sentinel_zero.science.preparation import sha256_file

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def portable_workspace(tmp_path):
    # Preserve original long provider filenames without changing Windows policy.
    # Only this newly created, verified-contained temporary tree is cleaned up.
    parent = Path(os.environ["LOCALAPPDATA"])/"Temp" if os.name == "nt" else tmp_path
    with tempfile.TemporaryDirectory(prefix="sz7-", dir=parent) as name:
        root = Path(name)
        assert root.resolve().parent == parent.resolve() and root.name.startswith("sz7-")
        yield root


@pytest.fixture(scope="module")
def real_paths():
    value = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT")
    if not value:
        pytest.skip("Actual NISAR source files unavailable; set SENTINEL_ZERO_SOURCE_ROOT")
    source = Path(value)
    prep = Path(os.environ.get("SENTINEL_ZERO_PREP_RUN", str(source.parent/"sz005/kelani-20261006-20261018-v1")))
    analysis = source.parent/"sz006/kelani-20261006-20261018-v2"
    if not analysis.is_dir():
        pytest.skip("Actual published SZ-006 analysis is unavailable")
    return RuntimePaths(source, prep, analysis)


@pytest.mark.real_data
def test_actual_app_five_stages_pixels_thresholds_and_safe_exports(real_paths, monkeypatch):
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(real_paths.source.parent))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT", raising=False)
    at = AppTest.from_file(ROOT/"app.py", default_timeout=30).run()
    assert not at.exception and at.title[0].value == "SENTINEL ZERO 3.0"
    assert at.radio(key="stage").options == list(STAGES)
    assert [(m.label, m.value) for m in at.metric][:2] == [("Common eligible", "305,137"), ("Excluded", "672")]
    text = " ".join(x.value for x in at.caption)
    assert "G4348875889-ASF" in text and "G4378876686-ASF" in text and "9,712" in text
    at.radio(key="stage").set_value(STAGES[1]).run()
    assert not at.exception
    at.number_input(key="pixel_row").set_value(0)
    at.number_input(key="pixel_column").set_value(0).run()
    assert any("Excluded from analysis" in x.value for x in at.warning)
    assert at.metric[-1].value == "9,712"
    at.number_input(key="pixel_row").set_value(157)
    at.number_input(key="pixel_column").set_value(309).run()
    assert any(m.label == "Saved backscatter change" and m.value == "-26.151365 dB" for m in at.metric)
    at.slider[0].set_value(3.0).run()
    expected = 47016+31042
    assert at.metric[-1].value == f"{expected:,}"
    for stage in STAGES[2:]:
        at.radio(key="stage").set_value(stage).run()
        assert not at.exception
    # Redesign adds one human-readable PDF plus the preserved technical exports.
    downloads = at.get("download_button")
    assert len(downloads) == 3
    assert [b.proto.label for b in downloads] == ["Download Investigation Report (PDF)", "Export Technical Evidence (JSON)", "Export inspected-cell evidence (JSON)"]
    exported = json.loads(at.code[0].value)
    assert exported["statistics"]["eligible_cells"] == 305137 and exported["analysis_manifest_sha256"] == ANALYSIS_SHA256
    actual = load_explorer(real_paths)
    record = json.loads(summary_json(actual))
    assert record["analysis_manifest_sha256"] == ANALYSIS_SHA256
    assert record["statistics"]["eligible_cells"] == 305137
    assert all(v == "UNVERIFIED" for v in record["quality_flags"].values())
    assert at.session_state["selected_cell"] == (157, 309)
    assert at.session_state["investigation_threshold"] == 3.0


@pytest.mark.real_data
def test_portable_full_source_copy_relocation_archive_and_tamper(real_paths, portable_workspace, monkeypatch):
    tmp_path = portable_workspace
    bundle, digest = create_bundle(real_paths, tmp_path, "actual-offline")
    receipt = verify_bundle_receipt(bundle, digest)
    assert len(receipt["files"]) == 15
    moved = tmp_path/"new-machine-root"
    bundle.rename(moved)
    paths = RuntimePaths(moved/"sources", moved/"preparation", moved/"analysis", "Verified portable scientific mode", moved, digest)
    data = load_explorer(paths)
    assert data.product.statistics["eligible_cells"] == 305137
    assert pixel(data, 157, 309)["saved_change_db"] == pytest.approx(-26.151365280151367)
    archive = tmp_path/"actual-offline.zip"
    assert len(archive_bundle(moved, digest, archive)) == 64
    with zipfile.ZipFile(archive) as z:
        assert len(z.namelist()) == 16 and z.testzip() is None
        assert all(not name.startswith(("/", "..")) for name in z.namelist())
    with pytest.raises(AcquisitionError): archive_bundle(moved, digest, archive)
    monkeypatch.setenv("SENTINEL_ZERO_BUNDLE_ROOT", str(moved))
    monkeypatch.setenv("SENTINEL_ZERO_BUNDLE_SHA256", digest)
    at = AppTest.from_file(ROOT/"app.py", default_timeout=30).run()
    assert not at.exception and any("Verified portable" in c.value for c in at.caption)
    # Original source identities are unchanged in a different root, not reduced verification.
    source_file = next((moved/"sources").iterdir())
    first_key = fingerprint(paths)
    with source_file.open("r+b") as stream:
        first = stream.read(1); stream.seek(0); stream.write(bytes([first[0]^1]))
    assert fingerprint(paths) != first_key
    at.run()
    assert not at.exception and at.error and not at.metric
    assert any("Scientific display blocked" in x.value for x in at.error)
    with pytest.raises(AcquisitionError): load_explorer(paths)
    with pytest.raises(AcquisitionError): verify_bundle_receipt(moved, "0"*64)


@pytest.mark.parametrize("case", ["missing_root", "corrupt_manifest", "missing_source", "missing_preview", "changed_raster"])
@pytest.mark.real_data
def test_failure_modes_block_instead_of_showing_cached_science(real_paths, portable_workspace, monkeypatch, case):
    tmp_path = portable_workspace
    if case == "missing_root":
        monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(tmp_path/"absent"))
    else:
        # Reuse actual bytes as local test copies only, never modify historical inputs.
        runtime = tmp_path/"runtime"
        source = runtime/"sz004-pilot-20261112"
        prep = runtime/"sz005/kelani-20261006-20261018-v1"
        analysis = runtime/"sz006/kelani-20261006-20261018-v2"
        for src, dest in ((real_paths.source, source), (real_paths.preparation, prep), (real_paths.analysis, analysis)):
            dest.mkdir(parents=True)
            names = [a["filename"] for inv in json.loads((real_paths.preparation/"manifest.json").read_text())["source_inventories"] for a in inv["artifacts"]] if src == real_paths.source else [p.name for p in src.iterdir() if p.is_file()]
            for name in names:
                shutil.copyfile(src/name, dest/name)
        if case == "corrupt_manifest": (analysis/"analysis_manifest.json").write_text("{}");
        elif case == "missing_source": next(source.iterdir()).unlink()
        elif case == "missing_preview": (analysis/"comparison.html").unlink()
        else:
            with (analysis/"delta_backscatter_db.tif").open("ab") as stream: stream.write(b"CORRUPT TEST COPY")
        monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(runtime))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT", raising=False)
    at = AppTest.from_file(ROOT/"app.py", default_timeout=30).run()
    assert not at.exception and at.error and not at.metric
    assert any("operator" in x.value.lower() for x in at.markdown)


@pytest.mark.real_data
def test_unsupported_crs_or_wrong_identity_cannot_be_exposed(real_paths, monkeypatch):
    data = load_explorer(real_paths)
    invalid_grid = dict(data.product.prepared.grid, epsg=4326)
    wrong = replace(data.product, prepared=replace(data.product.prepared, grid=invalid_grid))
    monkeypatch.setattr("sentinel_zero.application.data.load_change_product", lambda *a, **k: wrong)
    with pytest.raises(AcquisitionError): load_explorer(real_paths)


@pytest.mark.real_data
def test_optional_chart_unavailable_navigation_and_reverify_still_work(real_paths, monkeypatch):
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(real_paths.source.parent))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT", raising=False)
    def missing_chart(*args, **kwargs):
        raise ImportError("SYNTHETIC missing optional chart backend")
    monkeypatch.setattr("sentinel_zero.presentation.explorer.distributions", missing_chart)
    at = AppTest.from_file(ROOT/"app.py", default_timeout=30).run()
    next(b for b in at.button if b.label.startswith("Inspect cells")).click().run()
    assert not at.exception and at.radio(key="stage").value == STAGES[1]
    assert any("Optional interactive" in x.value for x in at.info)
    assert not at.text_input and not at.get("file_uploader")
    next(b for b in at.button if b.label == "Reverify local evidence").click().run()
    assert not at.exception and at.metric[0].value == "305,137"


@pytest.mark.real_data
def test_bundle_no_overwrite_partial_failure_cleanup_and_path_guidance(real_paths, portable_workspace, monkeypatch):
    root = portable_workspace
    def interrupted_copy(*args, **kwargs):
        raise OSError("SYNTHETIC interrupted local copy")
    with monkeypatch.context() as m:
        m.setattr("sentinel_zero.application.bundle.shutil.copyfileobj", interrupted_copy)
        with pytest.raises(OSError): create_bundle(real_paths, root, "failed")
    assert not list(root.iterdir())
    bundle, digest = create_bundle(real_paths, root, "good")
    with pytest.raises(AcquisitionError): create_bundle(real_paths, root, "good")
    assert verify_bundle_receipt(bundle, digest)
    if os.name == "nt":
        long_parent = root/("long"*25)
        long_parent.mkdir()
        with pytest.raises(AcquisitionError, match="shorter"):
            create_bundle(real_paths, long_parent, "long")


@pytest.mark.real_data
def test_premium_build_largest_cell_empty_input_and_report_state(real_paths, monkeypatch, caplog):
    from sentinel_zero.presentation.theme import UI_BUILD
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(real_paths.source.parent))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT", raising=False)
    at = AppTest.from_file(ROOT/"app.py", default_timeout=30).run()
    assert any(UI_BUILD in m.value for m in at.markdown)
    assert len(at.get("image")) == 3
    at.radio(key="stage").set_value(STAGES[1]).run()
    at.number_input(key="pixel_row").set_value(None).run()
    assert not at.exception and any("Enter both" in item.value for item in at.info)
    next(b for b in at.button if b.label == "Inspect largest absolute-change cell").click().run()
    assert not at.exception
    assert at.number_input(key="pixel_row").value == 157 and at.number_input(key="pixel_column").value == 309
    assert any(m.value == "-26.151365 dB" for m in at.metric)
    assert not any("was created with a default value" in item.message for item in caplog.records)
    at.radio(key="stage").set_value(STAGES[4]).run()
    assert not at.exception and at.get("download_button")[0].proto.label == "Download Investigation Report (PDF)"
    assert at.session_state["selected_cell"] == (157, 309)
