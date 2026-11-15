"""Read-only real-metadata regression; mocked HEAD is NOT real data access."""

import json
from pathlib import Path

from sentinel_zero.ingestion.acquisition import main, run_preflight
import sentinel_zero.ingestion.acquisition as acquisition

ROOT = Path(__file__).resolve().parents[2]


def test_reconciles_real_catalog_without_scientific_acceptance():
    reviewed = json.loads((ROOT / "docs/evidence/sz003/kelani_provisional_reviewed.json").read_text())
    catalog = json.loads((ROOT / "docs/evidence/sz002/kelani_provisional_granules.json").read_text())
    capabilities = json.loads((ROOT / "docs/evidence/sz004/harmony_capabilities.json").read_text())
    calls = []
    def fake_head(asset):
        calls.append(asset.granule_id)
        return {"status": 401, "headers": {}}
    result = run_preflight(reviewed, catalog, capabilities, head_transport=fake_head)
    assert result["metadata_reconciled_with_sz003"]
    assert calls == ["G4348875889-ASF", "G4378876686-ASF"]
    assert result["full_hdf5_expected_bytes"] == 12_180_258_816
    assert result["full_hdf5_download_status"] == "BUDGET"
    assert result["pilot"]["output_total_bytes"] is None and not result["pilot"]["transfer_authorized_by_size_guard"]
    assert not result["data_inspected"] and not result["pair_accepted_for_analysis"]


def test_preflight_output_not_overwritten(tmp_path):
    output = tmp_path / "existing.json"
    output.write_text('{"SYNTHETIC":"PRESERVE"}')
    code = main(["--reviewed", "missing.json", "--catalog", "missing.json", "--capabilities", "missing.json", "--output", str(output)])
    assert code == 2 and json.loads(output.read_text()) == {"SYNTHETIC": "PRESERVE"}


def test_cli_blocked_gate_has_distinct_exit_and_record(monkeypatch, tmp_path):
    def offline_preflight(*inputs):
        return run_preflight(*inputs, head_transport=lambda asset: {"status": 401, "headers": {}})
    monkeypatch.setattr(acquisition, "run_preflight", offline_preflight)
    output = tmp_path / "offline-preflight.json"
    result = main(["--reviewed", str(ROOT / "docs/evidence/sz003/kelani_provisional_reviewed.json"),
                   "--catalog", str(ROOT / "docs/evidence/sz002/kelani_provisional_granules.json"),
                   "--capabilities", str(ROOT / "docs/evidence/sz004/harmony_capabilities.json"), "--output", str(output)])
    data = json.loads(output.read_text())
    assert result == 3 and data["authentication_status"] == "AUTHENTICATION_REQUIRED"
    assert data["pilot"]["scientific_bytes_downloaded"] == 0
    assert not data["pair_accepted_for_analysis"]
