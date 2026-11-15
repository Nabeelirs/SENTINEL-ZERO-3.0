"""SYNTHETIC round trips plus separately marked actual local NISAR evidence."""
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio

# Reuse the existing explicitly synthetic six-raster generator without altering
# its tests, source metadata or the independently recorded real-data evidence.
from test_preparation import synthetic_pair  # noqa: F401
from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.science.preparation import prepare_pair, sha256_file, read_json
from sentinel_zero.science.change import (
    FILES, READY, analyze, load_change_product, main, statistics,
)


@pytest.fixture
def prepared(synthetic_pair):
    root, output, inv, gate = synthetic_pair
    prep = prepare_pair(root, output, "SYNTHETIC-preparation", *inv, gate)
    analysis_root = output.parent/"analysis"
    analysis_root.mkdir()
    return root, prep, analysis_root, sha256_file(prep/"manifest.json")


def run(prepared):
    root, prep, output, digest = prepared
    result = analyze(prep, root, output, "SYNTHETIC-change", preparation_sha256=digest)
    product = load_change_product(result, prep, root, preparation_sha256=digest,
                                  analysis_sha256=sha256_file(result/"analysis_manifest.json"))
    return result, product


def test_roundtrip_grid_nodata_stats_preview_and_no_source_modification(prepared):
    root, prep, output, digest = prepared
    before = {p.name: sha256_file(p) for p in root.iterdir()}
    result, product = run(prepared)
    assert product.provenance["decision"] == READY
    assert product.statistics["eligible_cells"] == 3
    assert np.isfinite(product.change_db).tolist() == product.prepared.common_valid.tolist()
    assert not product.change_db.flags.writeable
    for r, c in zip(*np.where(product.prepared.common_valid)):
        expected = 10*(math.log10(float(product.prepared.after_power[r, c])) - math.log10(float(product.prepared.before_power[r, c])))
        assert product.change_db[r, c] == pytest.approx(expected, rel=1e-7)
    with rasterio.open(result/FILES["change"]) as src:
        assert src.crs.to_epsg() == 32644 and list(src.transform)[:6] == [10, 0, 400000, 0, -10, 770000]
        assert src.xy(1, 2) == (400025, 769985) and src.dtypes == ("float32",)
        assert np.isnan(src.nodata) and src.units == ("dB",)
    assert product.statistics == statistics(product.change_db, product.prepared.common_valid, product.prepared.grid)
    assert sum(product.diagnostics["histogram"]["counts"]) == 3
    html = (result/FILES["preview"]).read_text(encoding="utf-8")
    assert "plotly.js" in html and "PROVISIONAL" in html and "Display clipping only" in html
    assert str(root) not in (result/"analysis_manifest.json").read_text()
    assert before == {p.name: sha256_file(p) for p in root.iterdir()}


@pytest.mark.parametrize("kind", ["raster", "statistics", "diagnostics", "preview", "analysis_hash", "preparation_hash"])
def test_corrupt_artifacts_or_receipts_fail(prepared, kind):
    root, prep, output, digest = prepared
    result, _ = run(prepared)
    analysis_digest = sha256_file(result/"analysis_manifest.json")
    if kind in ("raster", "statistics", "diagnostics", "preview"):
        name = FILES["change" if kind == "raster" else kind]
        with (result/name).open("ab") as stream: stream.write(b"SYNTHETIC corruption")
    elif kind == "analysis_hash": analysis_digest = "0"*64
    else: digest = "0"*64
    with pytest.raises(AcquisitionError):
        load_change_product(result, prep, root, preparation_sha256=digest, analysis_sha256=analysis_digest)


@pytest.mark.parametrize("mutation", ["order", "polarization", "granule", "grid", "support"])
def test_changed_preparation_cannot_be_reinterpreted(prepared, mutation):
    root, prep, output, digest = prepared
    m = read_json(prep/"manifest.json")
    if mutation == "order": m["source_inventories"].reverse()
    elif mutation == "polarization": m["source_inventories"][0]["artifacts"][0]["filename"] = "HVHV.tif"
    elif mutation == "granule": m["source_inventories"][0]["source"]["granule_id"] = "G3-TEST"
    elif mutation == "grid": m["grid"]["transform"][2] += 10
    else: m["statistics"]["common_eligible"] += 1
    (prep/"manifest.json").write_text(json.dumps(m))
    with pytest.raises(AcquisitionError):
        analyze(prep, root, output, "SYNTHETIC-invalid", preparation_sha256=sha256_file(prep/"manifest.json"))
    assert not list(output.iterdir())


@pytest.mark.parametrize("mutation", ["source", "units", "grid", "limitations", "filename", "stats", "input_receipts", "preparation_reference"])
def test_manifest_semantics_checked_even_with_new_local_hash(prepared, mutation):
    root, prep, output, digest = prepared
    result, _ = run(prepared)
    m = read_json(result/"analysis_manifest.json")
    if mutation == "source": m["sources"].reverse()
    elif mutation == "units": m["parameters"]["units"] = "linear"
    elif mutation == "grid": m["grid"]["transform"][2] += 10
    elif mutation == "limitations": m["limitations"] = []
    elif mutation == "filename": m["outputs"]["change"]["filename"] = "../escape.tif"
    elif mutation == "input_receipts": m["input_receipts"][0]["artifacts"][0]["sha256"] = "0"*64
    elif mutation == "preparation_reference": m["preparation"]["run_id"] = "WRONG"
    else: m["statistics"]["mean"] = 123
    (result/"analysis_manifest.json").write_text(json.dumps(m))
    with pytest.raises(AcquisitionError):
        load_change_product(result, prep, root, preparation_sha256=digest,
                            analysis_sha256=sha256_file(result/"analysis_manifest.json"))


def test_no_overwrite_and_failed_write_is_not_published(prepared, monkeypatch):
    root, prep, output, digest = prepared
    result, _ = run(prepared)
    baseline = {p.name: sha256_file(p) for p in result.iterdir()}
    with pytest.raises(AcquisitionError): analyze(prep, root, output, result.name, preparation_sha256=digest)
    def fail(*args, **kwargs): raise OSError("SYNTHETIC preview failure")
    monkeypatch.setattr("sentinel_zero.science.change.write_preview", fail)
    with pytest.raises(OSError): analyze(prep, root, output, "SYNTHETIC-failed", preparation_sha256=digest)
    assert list(output.iterdir()) == [result]
    assert baseline == {p.name: sha256_file(p) for p in result.iterdir()}


def test_cli_help_analyze_verify_failures(prepared, capsys):
    root, prep, output, digest = prepared
    with pytest.raises(SystemExit) as help_exit: main(["--help"])
    assert help_exit.value.code == 0
    with pytest.raises(SystemExit): main(["analyze"])
    common = ["--source-root", str(root), "--preparation-dir", str(prep), "--preparation-sha256", digest]
    assert main(["analyze", *common, "--output-root", str(output), "--run-id", "SYNTHETIC-cli"]) == 0
    result = output/"SYNTHETIC-cli"
    assert main(["verify", *common, "--run-dir", str(result), "--analysis-sha256", sha256_file(result/"analysis_manifest.json")]) == 0
    assert main(["verify", *common, "--run-dir", str(result), "--analysis-sha256", "0"*64]) == 2
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.real_data
def test_real_nisar_change_independent_reference_two_runs_and_sources_unchanged(tmp_path):
    """Actual six NISAR TIFFs, independently scalar-checked; never substitute fixtures."""
    root_value, prep_value = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT"), os.environ.get("SENTINEL_ZERO_PREP_RUN")
    if not root_value or not prep_value:
        pytest.skip("Actual NISAR sources/preparation unavailable; set both runtime environment paths")
    root, prep = Path(root_value), Path(prep_value)
    digest = "90d4a84868538ac3a8562803f6f6bdfba31369822b248d921023d3791f9a969c"
    sources = read_json(prep/"manifest.json")["source_inventories"]
    receipts = {a["filename"]: a["sha256"] for i in sources for a in i["artifacts"]}
    products, runs = [], []
    for run_id in ("actual-nisar-a", "actual-nisar-b"):
        result = analyze(prep, root, tmp_path, run_id, preparation_sha256=digest)
        runs.append(result)
        products.append(load_change_product(result, prep, root, preparation_sha256=digest,
                                             analysis_sha256=sha256_file(result/"analysis_manifest.json")))
    a, b = products
    assert a.statistics["eligible_cells"] == 305137 and a.statistics["excluded_cells"] == 672
    assert np.array_equal(a.change_db, b.change_db, equal_nan=True) and a.statistics == b.statistics
    assert a.prepared.grid["epsg"] == 32644 and a.change_db.shape == (553, 553)
    indices = np.flatnonzero(a.prepared.common_valid)[::101]
    reference = np.array([10*(math.log10(float(a.prepared.after_power.flat[i])) - math.log10(float(a.prepared.before_power.flat[i]))) for i in indices])
    np.testing.assert_allclose(a.change_db.flat[indices], reference, rtol=1e-7, atol=2e-6)
    assert all(sha256_file(runs[0]/n) == sha256_file(runs[1]/n) for n in FILES.values())
    assert all(sha256_file(root/name) == digest for name, digest in receipts.items())
