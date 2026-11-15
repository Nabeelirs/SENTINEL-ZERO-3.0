"""Offline integration: real parsing + pagination + selection + CLI + JSON I/O."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import fake_client, response
from sentinel_zero.cli import main
from sentinel_zero.config import load_config
from sentinel_zero.discovery.cmr import parse_collection, parse_granule

ROOT = Path(__file__).resolve().parents[2]


def test_installed_cli_help():
    completed = subprocess.run([sys.executable, "-m", "sentinel_zero.cli", "--help"], capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0 and "discover" in completed.stdout


def test_valid_config():
    config = load_config(ROOT / "config/kelani_provisional.json")
    assert config.query.collection_concept_id == "C2854338529-ASF"
    assert "not a verified basin boundary" in config.aoi_label


def test_invalid_arguments():
    with pytest.raises(SystemExit) as error:
        main(["discover"])
    assert error.value.code == 2


def test_successful_output_and_no_overwrite(fixture_data, tmp_path, capsys):
    first, second = deepcopy(fixture_data["granule"]), deepcopy(fixture_data["granule"])
    second["meta"]["concept-id"] = "G2-TEST"
    period = second["umm"]["TemporalExtent"]["RangeDateTime"]
    period.update(BeginningDateTime="2026-10-18T00:00:00Z", EndingDateTime="2026-10-18T00:01:00Z")
    second["umm"]["DataGranule"]["Identifiers"][0]["Identifier"] = "SYNTHETIC_NOT_A_REAL_ACQUISITION_2"
    client, transport, _ = fake_client([response([fixture_data["collection"]], collections=True), response([first], hits=2, cursor="next"), response([second], hits=2)])
    output = tmp_path / "audit.json"
    args = ["discover", "--collection", "C1-TEST", "--page-size", "1", "--output", str(output)]
    assert main(args, client=client) == 0
    parsed = json.loads(output.read_text(encoding="utf-8"))
    assert parsed["discovery"]["pagination_complete"] and parsed["recommended_pair"]["outcome"] == "PRELIMINARY_CANDIDATE"
    assert not parsed["scientific_acceptance"] and len(transport.calls) == 3
    old = output.read_bytes()
    assert main(args, client=client) == 4 and output.read_bytes() == old


def test_service_failure_exit_and_record(tmp_path):
    client, _, _ = fake_client([response([], status=403)])
    output = tmp_path / "failed.json"
    assert main(["discover", "--collection", "C1-TEST", "--output", str(output)], client=client) == 3
    assert json.loads(output.read_text())["discovery"]["failures"][0]["http_status"] == 403


def test_invalid_config(tmp_path):
    file = tmp_path / "config.json"
    file.write_text('{"schema_version":"1.0","collection_concept_id":"C1-TEST","token":"secret"}')
    assert main(["discover", "--config", str(file)]) == 2


def test_collection_pattern_cannot_be_granule_config(tmp_path):
    file = tmp_path / "config.json"
    file.write_text('{"schema_version":"1.0","short_name":"NISAR*"}')
    assert main(["discover", "--config", str(file)]) == 2


@pytest.mark.regression
def test_preserved_real_metadata_and_existing_pair():
    """Actual historical public CMR metadata; no network or scientific assertion."""
    evidence = ROOT / "docs/evidence/sz002"
    hashes = {"collections.json": "b9ccb2cd73209477dd7485d669db0e9ad8c1315490f8ea865ba7e2a8d3a7d2f5",
              "kelani_provisional_granules.json": "e05a0612330e181dda5c238bd91e1db03824a102e3d76c73fad77cade795c314",
              "kelani_validated_granules.json": "7244b7284e3aba75d8c2b1ca3cf28725152220a4b347c515fea08bd93f7d977f",
              "requests.json": "817c822087901efc204fbb6568238296a34c443c19c8a2d40689594ee6b648d5",
              "source_checks.json": "159be90939902361da52b44505e879fbaed4db095689eb44a606c593fec57993",
              "validation.json": "078c6ab41f960435fce18ffc9968ac2bd71e74c6b35be8d3ca96e8976a6269cb"}
    for name, expected in hashes.items():
        assert sha256((evidence / name).read_bytes()).hexdigest() == expected
    collections = {c.concept_id: c for c in map(parse_collection, json.loads((evidence / "collections.json").read_text())["feed"]["entry"])}
    data = json.loads((evidence / "kelani_provisional_granules.json").read_text())
    observations = [parse_granule(item, collections["C2854338529-ASF"]) for item in data["items"]]
    assert data["hits"] == 20 and len(observations) == 10
    from sentinel_zero.discovery.selection import assess_pair
    ids = {o.granule_concept_id: o for o in observations}
    pair = assess_pair(ids["G4348875889-ASF"], ids["G4378876686-ASF"])
    assert pair.outcome.value == "PRELIMINARY_CANDIDATE" and not pair.scientific_acceptance
    validated = json.loads((evidence / "kelani_validated_granules.json").read_text())
    assert len([parse_granule(item, collections["C3622241360-ASF"]) for item in validated["items"]]) == 2
